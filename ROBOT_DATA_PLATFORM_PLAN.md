# Robot Data Platform: Architecture and Implementation Plan

**Status:** proposed design  
**Project:** FTC Advanced Driver Station  
**Date:** 2026-08-23  
**Scope:** a second, independent data connection between the Control Hub and the laptop, plus recording, graphs, video synchronization, cloud archival, and replay

## Executive summary

The data system should be a separate, read-mostly path alongside Robocol. It must never become part of the robot's safety or control path. The recommended topology is:

```text
FTC OpMode loop                    Control Hub background runtime
---------------------------        ------------------------------------
read hardware                      reconnecting TCP client
compute controls                   protocol encoder / connection writer
publish immutable snapshot  ---->  bounded queue, never blocks OpMode
emit events                        sequence numbers and drop accounting
        |                                      |
        | Robocol control/telemetry             | independent TCP stream
        v                                      v
Driver Station logic             Laptop TelemetryReceiver / TCP listener
                                 |-- append-only recorder (full rate)
                                 |-- live fan-out (rate limited)
                                 |-- camera capture and clock alignment
                                 |-- FastAPI/WebSocket API
                                 v
                            React graphs and replay UI
                                 |
                                 v
                    local store-and-forward uploader
                                 |
                                 v
              Oracle Object Storage + optional Ubuntu catalog API
```

The most important principles are:

1. **Robot control always wins.** Publishing a sample must be bounded, non-blocking, and cheap. Network or disk failure must not change motor behavior or loop timing.
2. **Timestamp at the source.** Every sample and event receives a monotonic Control Hub timestamp when it is produced. Arrival time is also stored, but is not treated as the event time.
3. **Record once, transform later.** The laptop first writes the exact incoming framed stream to a recoverable append-only log. Charts, Parquet files, summaries, and replays are derived from it.
4. **Bound every queue.** A slow laptop, browser, disk, or cloud endpoint must have an explicit overflow policy and visible drop metrics.
5. **Live and replay use one model.** A replay source should expose the same normalized batches and events as the live source. UI tools should not need separate implementations.
6. **Video is a separate media stream.** Do not place video frames inside the telemetry TCP connection. Correlate video and data with clocks and markers.
7. **Upload from the laptop, not the Control Hub.** Recording continues offline and uploads resume later. The robot should not depend on Internet availability.

This repository currently uses FTC SDK **11.2.1**, Java 8, Android API 24 minimum, a Python/FastAPI backend, and a React/Vite frontend. The design below fits those constraints and extends the separation already present in `ftc_control_hub.py` and `web_driver_station/backend/main.py`.

---

## 1. Boundaries and non-goals

### The new channel is for observability

Good uses include:

- sensor values, motor targets and measured values;
- pose estimates and localization confidence;
- control-loop terms, errors, saturation, and timing;
- robot state transitions and operator annotations;
- structured fault events;
- synchronization marker events;
- experiment configuration and software/build identifiers.

The first version should **not** use this connection for drive commands, emergency stop, or any action whose loss could make the robot unsafe. Those remain on the established control path. A later request such as “flash the synchronization LED” can be supported only as a narrowly typed diagnostic request that is consumed and applied by the OpMode thread, with an explicit test-mode guard.

### “Independent from the main loop” has a precise meaning

It means the following work is independent:

- accepting, reading, and writing TCP;
- handshakes, heartbeats, and time-sync exchanges;
- serialization and batching;
- reconnect handling;
- laptop disk writes;
- browser fan-out, cloud upload, and file conversion.

It does **not** mean that an arbitrary background thread should read FTC hardware. Hardware values should normally be read once by the OpMode loop and copied into a data snapshot. This produces a coherent view of one control iteration and avoids thread-safety and bus-contention problems.

### Competition/legal boundary

The repository already identifies the custom web Driver Station as a controlled-test tool rather than an event-legal Driver Station. Treat this telemetry/video platform the same way until the current season's rules have been checked. The architecture must not assume that a laptop, extra wireless traffic, a custom application, or cloud access is permitted during an official match.

---

## 2. Connection topology

### Recommended role assignment

The **laptop acts as the TCP server** and the **Control Hub acts as the reconnecting client**. This assumes the laptop's robot-network adapter has a deliberately assigned static IPv4 address outside the Control Hub's DHCP allocation.

Reasons:

- the laptop backend can listen continuously even when no OpMode exists;
- opening an outbound connection from the Control Hub during `init()` keeps the Android lifecycle simple;
- the laptop naturally owns session receipt, storage, browser fan-out, and later cloud upload;
- the client connection and its worker can be created and destroyed with one OpMode;
- no discovery protocol is required once the static laptop address is configured.

Use a configurable, non-conflicting port, for example `5810`, bound only to the laptop's robot-network interface if practical. Do not silently reuse the Robocol port. The laptop IP, port, and protocol version belong in configuration, not scattered constants. The Windows firewall must permit this port on the robot network's profile.

Making the Control Hub the server remains technically possible and has essentially the same connected-state computational cost. Its main advantage would be the Control Hub's already predictable address. The chosen laptop-server topology is preferable here because a static laptop address is available and the desired connection lifetime is exactly one OpMode.

### Connection lifecycle

1. The laptop backend starts its listener independently and leaves it ready between OpModes.
2. During OpMode `init()`, the Control Hub starts a background client that repeatedly attempts to connect with exponential backoff and jitter, capped around 3-5 seconds.
3. Both sides exchange `Hello` records containing protocol versions, robot identity, software build, capabilities, maximum frame size, and a random session nonce.
4. Authentication is checked if a pre-shared diagnostic key is configured.
5. The robot sends the channel schema and current session metadata.
6. Sample batches, events, heartbeats, time-sync messages, and status records are multiplexed over the framed stream.
7. A disconnect closes only that connection. The robot keeps controlling normally, counts dropped/unavailable samples, and reconnects while the OpMode session remains active.
8. A new recording session is created on OpMode initialization or on an explicit recording command, not merely for every transient TCP reconnect.

Initially allow one active robot connection per recording session. A duplicate connection with the same robot/session identity should replace or reject the old connection explicitly. Browser and analysis fan-out belongs inside the laptop backend, not on the Control Hub.

### Android integration level

For version 1, keep all new Java code in `TeamCode` and avoid editing the SDK's `FtcRobotControllerActivity`. A singleton `RobotDataRuntime` can survive for the process lifetime, while an OpMode obtains and releases a session handle:

```java
public final class ExampleOpMode extends OpMode {
    private RobotDataSession data;

    @Override public void init() {
        data = RobotDataRuntime.get().openSession(
            SessionInfo.builder("drive-test").build());
    }

    @Override public void loop() {
        long now = RobotClock.nowNanos();
        data.sample(now)
            .put(Channels.LEFT_TARGET, leftTarget)
            .put(Channels.LEFT_VELOCITY, leftVelocity)
            .put(Channels.BATTERY_VOLTAGE, voltage)
            .commit();                 // bounded and non-blocking
    }

    @Override public void stop() {
        data.event("opmode.stop").commit();
        data.close();
    }
}
```

This keeps FTC SDK updates manageable. The laptop listener can exist continuously, but the Android client and data session exist only from OpMode `init()` to `stop()`. If a later requirement calls for a process-wide robot connection, add a proper Android service or SDK lifecycle hook as a separate phase; do not leave an OpMode-owned thread untracked.

Verify the merged Android manifest includes `android.permission.INTERNET`; declare it explicitly in the TeamCode manifest if it is not inherited.

---

## 3. Wire protocol

### Do not use newline-delimited JSON as the durable protocol

JSON is excellent for debugging and the browser boundary, but it is inefficient for high-rate numeric data and makes type/schema evolution ambiguous. TCP also has no message boundaries: one `read()` may return half a message or several messages. A deliberate frame protocol is required.

### Recommended format: length-delimited Protocol Buffers

Use **protobuf-javalite** on Android and Python protobuf on the laptop. It gives compact numeric encoding, generated types on both sides, explicit compatibility rules, and unknown-field tolerance. Keep the outer framing simple:

```text
uint32_be frame_length
frame_length bytes: protobuf Envelope
```

Reject zero-length frames and frames over a negotiated maximum (for example 1 MiB). The reader must use `readExactly`, never assume one socket read equals one frame.

A conceptual schema is:

```proto
message Envelope {
  uint32 protocol_version = 1;
  bytes session_id = 2;          // 16-byte UUID
  uint64 connection_sequence = 3;
  uint64 robot_elapsed_ns = 4;   // when meaningful

  oneof body {
    Hello hello = 10;
    Schema schema = 11;
    SampleBatch samples = 12;
    Event event = 13;
    Heartbeat heartbeat = 14;
    SyncRequest sync_request = 15;
    SyncResponse sync_response = 16;
    Subscribe subscribe = 17;
    Gap gap = 18;
    Error error = 19;
  }
}
```

Each channel definition should include:

- stable numeric `channel_id` within a schema;
- stable string key such as `shooter.flywheel.velocity`;
- type: double, signed integer, boolean, string, bytes, or vector;
- unit such as `rad/s`, `tick`, `V`, or `m`;
- display label and optional description;
- semantic role: measured, target, error, command, status, or diagnostic;
- expected sampling policy/rate;
- optional suggested graph range and precision;
- subsystem/tool ownership;
- schema revision.

Samples use numeric IDs, not repeated channel names. A batch can carry several snapshots to amortize TCP and protobuf overhead. Metadata and rare events can remain descriptive.

### Compatibility rules

- Never reuse a protobuf field number or channel key for a different meaning.
- Adding optional fields is backward compatible.
- A breaking meaning/type change creates a new channel key or major protocol version.
- Record the exact schema in every session; replay must not require the current robot code.
- Reject incompatible major versions during `Hello`; negotiate minor/capability features.
- Set a hard string/bytes length and channel count limit.
- Include CRC32C per stored chunk, or a stronger content hash in the file manifest. TCP already detects transmission corruption; the extra hash protects storage and upload integrity.

### Sequences, gaps, and acknowledgements

Every connection envelope has a monotonically increasing sequence. Every sample also has a session-level sample sequence that does not reset on reconnect. If the producer queue overflows, emit a `Gap` record containing the first missing sequence, number dropped, and reason. Missing data must be visible, never silently interpolated.

Do not acknowledge every telemetry batch; TCP already provides reliable ordered delivery and per-batch application acknowledgements would add latency. Acknowledge only control-plane actions such as schema subscription or explicit diagnostic marker requests.

### Batching and socket behavior

Begin with 10-20 ms batches, or a size limit around 16-32 KiB, whichever comes first. This keeps chart latency low while reducing syscall and protobuf overhead. Test both `TCP_NODELAY` and normal Nagle behavior with the chosen batching; do not cargo-cult a setting. Heartbeats every second and a dead-peer timeout around 3-5 seconds are reasonable lab defaults.

TCP's head-of-line behavior is acceptable for this lossless telemetry/recording path. Video belongs elsewhere, and a future ultra-low-latency signal can use a different transport without changing the channel/schema API.

---

## 4. Control Hub runtime and public API

### Internal components

```text
RobotDataRuntime
├── ChannelRegistry          immutable definitions after session start
├── SessionManager           identity, metadata, lifecycle
├── SnapshotPool             optional allocation reduction
├── BoundedSampleQueue       OpMode producer -> network consumer
├── ProtocolEncoder          batches and envelopes
├── TcpConnector             connects/retries on a background thread
├── ConnectionWriter         the only socket writer
├── ConnectionReader         sync/subscription/diagnostic requests
└── RuntimeMetrics           queue depth, drops, bytes, reconnects, errors
```

The OpMode side should perform only validation, value copying, a non-blocking queue offer, and counters. Encoding happens on the writer thread. A queue capacity based on time is easier to reason about than an arbitrary large number—for example, enough for two seconds at the configured maximum publish rate.

When full, discard the **oldest unsent samples**, retain the newest state, and increment a gap counter. This favors live diagnosis after a stall. Events marked important should have a small separate bounded queue so a burst of numeric snapshots cannot evict `fault`, `state-change`, or synchronization markers. Even the event queue must be bounded.

### Suggested robot-side API

Channel creation should be typed, centralized, and easy to discover:

```java
public final class DriveChannels {
    public static final DoubleChannel LEFT_TARGET =
        Channels.dbl("drive.left.target", "rad/s")
            .label("Left target")
            .role(Role.TARGET)
            .build();

    public static final DoubleChannel LEFT_MEASURED =
        Channels.dbl("drive.left.measured", "rad/s")
            .label("Left measured")
            .role(Role.MEASURED)
            .build();
}
```

Publishing should be fluent but not magical:

```java
data.sample(RobotClock.nowNanos())
    .put(LEFT_TARGET, target)
    .put(LEFT_MEASURED, measured)
    .put(LOOP_DURATION, loopDurationNanos)
    .commit();

data.event("drive.encoder.reset")
    .attribute("side", "left")
    .attribute("previous_ticks", previousTicks)
    .commit();
```

Useful API rules:

- channel keys are namespaced by mechanism;
- units are required for physical numbers and use a documented vocabulary;
- the API never blocks waiting for a laptop;
- `commit()` returns a small result such as `QUEUED`, `DROPPED`, or `SESSION_CLOSED` for diagnostics, but normal robot logic must not branch its safety behavior on it;
- events and samples are immutable after commit;
- a session declares metadata: robot name, OpMode, Git commit, build version, field/test label, configuration hash, and optional notes;
- avoid reflection in the hot path;
- debug checks may reject duplicate channels and wrong value types; release behavior still records a structured internal error instead of crashing the OpMode;
- provide rate helpers (`everyLoop`, fixed-frequency, on-change, threshold/event) outside the network code.

### Sampling policies

Not every value should be sent on every loop:

- control-loop signals: every loop or a fixed 50-100 Hz;
- temperatures and battery: 1-5 Hz;
- discrete state: on change plus a periodic refresh;
- text/log messages: events, not high-rate samples;
- large arrays/images: separate specialized streams or sampled artifacts.

The channel declaration can state the intended rate, but the mechanism remains responsible for deciding when to read hardware. A `Sampler` utility may help with cadence without hiding reads in background threads.

### Shutdown and failure behavior

- OpMode stop closes the session and enqueues a terminal event, with a short bounded drain period on the background writer.
- An emergency robot stop never waits for that drain.
- Socket exceptions close the connection and return to reconnect mode while the session is active.
- The connector and writer threads have explicit ownership, names, uncaught-exception reporting, and idempotent `close()`.
- No thread spins while disconnected.
- Runtime metrics are accessible through both ordinary FTC telemetry and the new stream.
- If encoding repeatedly fails for a channel, quarantine/report that channel; do not terminate the network runtime or OpMode.

---

## 5. Laptop backend

### Integrate as a service, not another monolithic class

The current FastAPI backend can own a `TelemetryService`, but its responsibilities should be split:

```text
TelemetryService
├── RobotTcpServer          listener and accepted-socket configuration
├── ProtocolReader          frame validation and protobuf decoding
├── ClockSynchronizer       robot -> laptop monotonic mapping
├── SessionCoordinator      connection versus recording lifecycle
├── RawRecorder             exact append-only frames
├── ChunkWriter             derived Parquet/events/index artifacts
├── LiveBroker              independent bounded subscriptions
├── CameraManager           capture processes/threads and timestamps
├── UploadQueue             persistent store-and-forward jobs
└── SessionRepository       list/open/query/recover sessions
```

Use `asyncio.start_server()` for the laptop listener and strict framed readers. CPU-heavy conversion, OpenCV capture, video encoding, and synchronous filesystem/OCI work must run in dedicated workers, not on the FastAPI event loop.

### Fan-out must not couple consumers

Do not use one queue from which the disk writer and browser compete for messages. The raw recorder is the authoritative subscriber. Live graphs receive a separate coalescing/rate-limited subscription; analysis plugins receive their own policies.

Recommended behavior:

- **raw recorder:** full stream, large bounded buffer; if it falls behind, surface a critical recording fault and preserve explicit gaps;
- **derived chunk writer:** can be regenerated from raw data;
- **live UI:** coalesce numeric samples into 20-30 updates per second while preserving min/max spikes per pixel bucket;
- **slow browser:** drop old render updates and send the newest window;
- **analytics:** declared queue and overflow policy per plugin.

### Session identity

Use a UUID generated when recording begins. A reconnect continues the same session only if the robot presents the same session UUID and the laptop has not finalized it. Store connection epochs within the session so reconnection boundaries are visible.

Suggested session states are `CREATED`, `RECORDING`, `FINALIZING`, `READY`, `UPLOAD_PENDING`, `UPLOADING`, `UPLOADED`, and `RECOVERY_NEEDED`. Persist them in a local SQLite catalog so a backend restart can resume work.

### Backend API surface

The browser should consume normalized APIs, not protobuf directly:

```text
GET  /api/data/status
GET  /api/data/sessions
GET  /api/data/sessions/{id}/manifest
GET  /api/data/sessions/{id}/channels
GET  /api/data/sessions/{id}/range?channels=...&start_ns=...&end_ns=...&points=...
GET  /api/data/sessions/{id}/events
GET  /api/data/sessions/{id}/video/{camera}/{segment}
POST /api/data/recording/start
POST /api/data/recording/stop
WS   /ws/data/live
WS   /ws/data/replay/{id}
```

The backend remains loopback-only by default, matching the current application's security model. Validate session IDs and paths; never form filesystem paths directly from URL text.

---

## 6. Clock synchronization: data, laptop, and camera

### Three clocks exist

1. **Robot monotonic clock:** `android.os.SystemClock.elapsedRealtimeNanos()` on the Control Hub. It is monotonic and includes sleep.
2. **Laptop monotonic clock:** `time.perf_counter_ns()` in Python.
3. **Camera/media clock:** frame presentation/capture timestamps, which may be driver timestamps or timestamps assigned when OpenCV returns a frame.

Wall-clock UTC is useful for filenames and human display, but it may jump and must not be used to align samples.

### Robot-to-laptop clock mapping

Run a continuous NTP-style four-timestamp exchange over the TCP connection:

```text
laptop sends request at L0
robot receives request at R1
robot sends response at R2
laptop receives response at L3
```

For each exchange, pair the clock midpoints:

```text
robot_mid  = (R1 + R2) / 2
laptop_mid = (L0 + L3) / 2
network_uncertainty ~= ((L3 - L0) - (R2 - R1)) / 2
```

Collect exchanges regularly, prefer the lowest-delay observations, reject outliers, and fit an affine mapping:

```text
laptop_time = scale * robot_time + offset
```

`scale` accounts for clock drift; it should be near 1. Store every raw exchange and each fitted mapping in the session. Use a piecewise mapping for long recordings if drift changes. On a reconnect, obtain a fresh fit and create a new clock epoch. Never infer source time only from packet arrival time.

This approach makes variable TCP delay largely irrelevant to graph/replay alignment because a telemetry sample carries its robot source timestamp.

### What timestamp does a cv2 frame have?

For a live USB camera, `cv2.VideoCapture.read()` does not guarantee a true exposure timestamp across all backends. `CAP_PROP_POS_MSEC` is primarily meaningful for media position and should not be trusted as a hardware timestamp for every webcam. The practical MVP is:

1. run one dedicated capture thread continuously so frames never wait for application polling;
2. request a camera buffer size of one, while recognizing that some backends ignore this property;
3. call `grab()`, assign `time.perf_counter_ns()` immediately when it returns, then call `retrieve()` to decode;
4. save the assigned timestamp for every retained frame in a sidecar table;
5. monitor frame intervals and flag duplicates, stalls, and drops;
6. fix resolution/FPS/exposure settings where supported and record the actual values returned by the backend.

This timestamp describes when the laptop obtained the frame, not necessarily the exposure. The LED procedure estimates the camera pipeline delay. If tighter accuracy is eventually required, use a backend that exposes driver/hardware timestamps (for example V4L2/GStreamer or PyAV/FFmpeg where supported) and retain the same frame-timestamp interface.

### LED flash synchronization procedure

The objective is to measure the difference between the recorded frame timestamp and the robot event's mapped physical time.

Recommended procedure:

1. Place a bright synchronization LED in a fixed camera region of interest (ROI). Avoid a region affected by other robot lights.
2. Disable or lock camera auto-exposure, auto white balance, and autofocus if the webcam permits. Automatic exposure can smear or delay the brightness response.
3. Trigger a distinctive coded pattern, not one arbitrary flash—for example short-short-long. Repeat it at session start, periodically, and near session end.
4. The OpMode thread changes the LED output and emits `sync.led.edge` in the same loop iteration, including commanded state, marker ID, and `RobotClock.nowNanos()`. A network thread must never touch the LED directly.
5. Convert each captured ROI to grayscale or HSV value, calculate a robust brightness statistic, and store this one-dimensional signal.
6. Detect edges using a rolling median baseline, median absolute deviation, and hysteresis. Validate the pulse durations/order against the coded pattern.
7. Map the robot event times into laptop time using the clock fit.
8. Match detected optical edges to robot edges and calculate:

   ```text
   camera_pipeline_delay = frame_tag_laptop_time - mapped_robot_LED_event_time
   ```

9. Use the median of repeated markers and report the spread (p50/p95 or median absolute deviation). Do not hide variability behind a single number.
10. For replay, estimate a frame's physical capture time as `frame_tag_time - calibrated_delay`. Interpolate the delay between marker groups if it changes over the session.

Cross-correlation between the robot's binary LED-state timeline and the camera ROI-brightness timeline is useful for finding the coarse lag. Edge/pattern matching then provides the precise marker associations. The coded pattern prevents a bright field object from being mistaken for the synchronization LED.

Important limitations:

- the recorded robot timestamp is the software command time, not the exact time photons leave the LED;
- the OpMode period, LED driver latency, exposure duration, rolling shutter, and USB buffering all contribute uncertainty;
- a camera pipeline that buffers a variable number of frames cannot be corrected perfectly with one constant offset;
- if sub-frame or millisecond-level ground truth becomes necessary, use a photodiode or common hardware trigger visible to both acquisition systems.

The system should display its measured synchronization uncertainty. “Aligned within 8 ms p95” is meaningful; “synchronized” without an error estimate is not.

---

## 7. Efficient local storage

### Treat a session as a directory of immutable chunks

Suggested layout:

```text
data/sessions/2026/08/23/<session-uuid>/
├── manifest.json
├── raw/
│   ├── stream-00000.ftclog
│   └── stream-00001.ftclog
├── telemetry/
│   ├── schema-0001-part-00000.parquet
│   └── schema-0001-part-00001.parquet
├── events/
│   └── events-00000.parquet
├── clocks/
│   ├── robot-laptop.parquet
│   └── camera-calibration.parquet
├── video/cam0/
│   ├── segment-00000.mp4
│   ├── segment-00000.frames.parquet
│   └── segment-00001.mp4
├── artifacts/
│   └── marker-<id>.jpg
├── checksums.sha256
└── upload-state.json
```

During writing, use a `.partial` suffix and atomically rename after the chunk is closed and checked. The final `manifest.json` is the session commit record; write it last.

### Raw telemetry log

The `.ftclog` format should contain a tiny file header followed by the exact length-prefixed envelopes plus the laptop receive timestamp. Rotate it by time or size (for example 60 seconds or 64-128 MiB). It is the crash-recovery and protocol-audit source.

Benefits:

- minimal work on the ingest path;
- exact reconstruction of gaps, schemas, and future fields;
- easy recovery up to the last complete length-prefixed record;
- derived formats can evolve without changing robot code.

### Parquet for analysis and range queries

Convert numeric snapshots into Parquet using PyArrow in batches. For a stable schema, use one row per robot snapshot and typed columns per channel; start a new file when the schema revision changes. This is more compact and graph-friendly than millions of JSON objects. Store events in a separate typed table. Use Zstandard compression and row groups sized to roughly a few seconds so seeking does not require reading the whole run.

If channels are extremely dynamic, a long-form fallback table (`time`, `channel_id`, typed value) is simpler but usually larger. Prefer stable declared schemas for the high-rate path.

### Video storage

Do not save every frame as JPEG/PNG. Millions of small files are inefficient to create, enumerate, upload, and replay. Encode video into 30-60 second H.264 MP4 segments at a deliberate bitrate, and store a Parquet sidecar mapping each encoded frame index/PTS to its original laptop monotonic timestamp and capture status.

Segmenting limits loss if the process crashes and enables resumable upload and range-based replay. OpenCV `VideoWriter` is acceptable for the first vertical slice if the codec is dependable on the target laptop. A dedicated FFmpeg process is the stronger production choice because codec settings, fragmented MP4, timestamps, and error reporting are more controllable.

Create still images only for marker thumbnails, faults, or explicitly requested snapshots. Each still should reference the source segment and frame index so it is not a second source of truth.

### Retention and sizing

Before implementation, measure a realistic channel set. As a planning example, 50 doubles at 100 Hz contain about 40 kB/s of raw numeric payload before metadata; protobuf batches and compression should remain modest. Video dominates: a 4 Mbit/s stream is about 30 MB/minute. Make bitrate, resolution, frame rate, local retention, and minimum free space explicit settings.

Stop starting new recordings below a safe disk threshold, but do not delete old sessions automatically unless a visible retention policy has been enabled. Never let cleanup target an unresolved or broad path.

---

## 8. Live graphs and frontend model

### Data path

```text
full-rate recorder
      |
      +--> in-memory recent window --> time-bucket reducer --> WebSocket batch
                                                        --> React chart adapter
```

Do not cause a React state update for every 100 Hz sample/channel. The backend should send batches around 20-30 times per second. The browser keeps typed-array ring buffers and redraws on `requestAnimationFrame` or at a fixed visual rate.

For downsampling, preserve the minimum and maximum in each time bucket so short current spikes and control oscillations remain visible. For historical ranges, the backend accepts a desired point count based on chart pixel width and performs min/max or LTTB-style reduction. Never fetch a 30-minute, 100 Hz run in full just to draw a 1,200-pixel chart.

### Chart capabilities

The shared chart primitive should support:

- source-time x-axis and optional wall-clock labels;
- multiple compatible-unit series;
- measured-versus-target pairing;
- cursor with exact values and event markers;
- pause without stopping recording;
- rolling windows (5 s, 30 s, 2 min) and manual zoom;
- dropped-sample/gap shading;
- connection and OpMode boundaries;
- synchronization uncertainty display when video is linked;
- export of the visible range.

A canvas-based library such as **uPlot** is a good first candidate for dense time series, but isolate it behind an app-owned `TimeSeriesChart` component. Library choice should not leak into mechanism tools. Evaluate it with a real 10-20 series workload before committing; accessibility and annotations may justify a different library.

### Extension API

Align this data layer with the application's existing rule that mechanisms own tools. A tool declares channels and optional graph presets:

```ts
registerMechanism({
  id: "shooter",
  tools: [{
    id: "tune",
    component: ShooterTuneTool,
    telemetry: {
      required: ["shooter.target", "shooter.velocity"],
      optional: ["shooter.error", "battery.voltage"],
      graphs: [{
        title: "Flywheel response",
        series: ["shooter.target", "shooter.velocity"],
        unit: "rad/s",
      }],
    },
  }],
});
```

Expose hooks such as `useLiveChannels(keys)`, `useSessionRange(query)`, `useEvents(filter)`, and `useReplayClock()`. Tools should receive typed normalized data, not WebSocket messages or file paths.

---

## 9. Oracle storage and upload architecture

### Recommended split

Use the Ubuntu Oracle instance for a small catalog/authentication API if desired, but store large immutable session chunks in **OCI Object Storage**, not only on the VM boot disk.

```text
Laptop UploadQueue
   | HTTPS, retry, content hash, idempotency
   v
Ubuntu catalog API (optional) ----> session/index database
   |
   +------------------------------> OCI Object Storage

or

Laptop OCI SDK/CLI ---------------> OCI Object Storage directly
```

Object names can be:

```text
team/<team-id>/robot/<robot-id>/year=2026/month=08/day=23/session=<uuid>/<relative-path>
```

### Store-and-forward behavior

The laptop may lose Internet access while connected to the Control Hub Wi-Fi, especially with one wireless adapter. Therefore:

- recording never waits for upload;
- every finalized chunk creates a persistent upload job in SQLite;
- jobs retry with exponential backoff and jitter;
- upload by immutable object name plus SHA-256 makes retries idempotent;
- use multipart/resumable upload for large video objects;
- upload data chunks first, video next, and `manifest.json` last;
- the remote session is considered complete only when the committed manifest and all listed checksums exist;
- retain local data until remote verification succeeds and the retention policy permits deletion.

If the laptop has Ethernet or a second adapter for Internet, uploads may run during recording at a capped bandwidth and low process priority. Otherwise, upload after leaving the robot network.

### Authentication choices

Preferred production choice: OCI SDK credentials or an Ubuntu ingestion API token stored in the laptop's OS credential store, with HTTPS and a least-privilege policy restricted to the session prefix.

OCI pre-authenticated requests (PARs) are convenient for a prototype and support scoped object access and multipart uploads, but the URL is a bearer secret. Do not commit it, print it in normal logs, embed it in the React bundle, or put it on the robot. Give it an expiry and narrow prefix. A thin ingestion service offers better revocation, audit, validation, quotas, and per-team identity.

### Server-side catalog

The remote catalog needs only session metadata, object keys, checksums, sizes, state, and permissions. Start with SQLite for a single team/instance, or PostgreSQL if concurrent users and richer queries justify it. Telemetry bulk data remains in Parquet/Object Storage; do not insert every 100 Hz scalar into a transactional SQL database.

Add object lifecycle rules for old video only after the team chooses a retention policy. Protect videos as potentially sensitive recordings.

---

## 10. Replay application

### Reuse the main app shell

Replay should be a new system tool inside the existing web application, not a completely separate frontend. It can reuse navigation, graph components, mechanism tools, and event rendering.

Core abstraction:

```ts
interface DataSource {
  listChannels(): Promise<ChannelDefinition[]>;
  queryRange(query: RangeQuery): Promise<SeriesBatch>;
  subscribe?(query: LiveQuery, onBatch: (batch: SeriesBatch) => void): Unsubscribe;
  listEvents(range: TimeRange): Promise<RobotEvent[]>;
}

interface ReplayClock {
  state: "paused" | "playing";
  timeNs: bigint;
  speed: 0.25 | 0.5 | 1 | 2 | 4;
  play(): void;
  pause(): void;
  seek(timeNs: bigint): void;
}
```

`LiveDataSource` and `RecordedDataSource` produce the same channel definitions and series batches. A mechanism's PID graph can therefore display a live run or a replay without being rewritten.

### Replay behavior

- seek on the canonical laptop monotonic session timeline;
- map telemetry source times through the stored robot clock model;
- map video frames through their sidecar timestamps and camera-delay calibration;
- expose a scrubber with OpMode, connection, fault, and sync-marker tracks;
- query only the visible graph range/resolution;
- serve local MP4 segments with HTTP range support;
- prefetch the adjacent segment;
- keep video and replay clock within a small tolerance, correcting gently for small drift and seeking for large drift;
- support data-only sessions and video-only gaps gracefully;
- show raw versus corrected video timing for debugging synchronization;
- allow annotations/bookmarks that do not mutate the immutable recording.

The replay clock should be the application master. Browser video time is adjusted to it using the timestamp sidecar rather than assuming `frame_index / advertised_fps` is exact.

### Analysis extensions

Once the foundation is stable, analysis plugins can consume recorded channels to produce derived artifacts:

- PID step-response metrics and settling time;
- motor current/velocity anomaly detection;
- localization path and confidence overlays;
- loop-time histograms and missed-deadline events;
- automatic clips around faults or button markers;
- comparison of two sessions with aligned trigger events.

Derived data should record its algorithm/version and source-session hash so results are reproducible.

---

## 11. Security and operational safety

- Bind the laptop web API to `127.0.0.1` by default.
- Treat the telemetry TCP stream as read-only except for handshake, subscription, time sync, and explicitly allowlisted diagnostic requests.
- A diagnostic request is queued to and validated by the OpMode; the socket reader never accesses hardware.
- Use a pre-shared HMAC challenge if accidental/unauthorized robot-network clients are a concern. Do not design a custom encryption scheme; use TLS if confidentiality is actually required.
- Limit frame sizes, string lengths, channel counts, sample rates, video settings, and per-session disk usage.
- Sanitize metadata before using it in display text or object names.
- Keep secrets out of Git, session files, browser JavaScript, and protobuf captures.
- Log authentication failures and protocol mismatches without logging secret material.
- Include a visible state for `connected but not recording`, `recording locally`, `recording with gaps`, and `upload pending`; a green TCP badge alone is insufficient.

---

## 12. Implementation roadmap

### Phase 0 — write the contracts and measure the budget

Deliverables:

- one-page invariants/non-goals document;
- `.proto` definitions and compatibility rules;
- channel naming/unit conventions;
- benchmark OpMode with a realistic channel set;
- measured loop cost, payload rate, and laptop disk rate;
- decision on port, authentication, and session lifecycle.

Exit criteria:

- the expected worst-case telemetry is comfortably below the network/disk budget;
- the publish call has a stated p95/p99 time budget;
- disconnect and queue-full behavior are unambiguous.

### Phase 1 — smallest end-to-end vertical slice

Control Hub:

- `RobotClock`, typed channel registry, session, bounded queue;
- reconnecting TCP client, `Hello`, schema, sample batch, heartbeat;
- a no-hardware test OpMode publishing runtime and synthetic sine waves.

Laptop:

- asyncio TCP listener and strict frame reader;
- console inspection and raw `.ftclog` recorder;
- status endpoint and live WebSocket batch.

Frontend:

- connection/recording indicators;
- one rolling graph and visible gap counter.

Exit criteria:

- pull Wi-Fi for 30 seconds and both sides recover without restarting the OpMode;
- corrupt/oversized frames are rejected safely;
- the OpMode never blocks when no laptop is connected;
- raw log can be decoded after forced backend termination.

### Phase 2 — durable sessions and extensible API

- finalize the fluent Java publish API and channel conventions;
- implement events, schema revisions, sequences, gap records, and connection epochs;
- add SQLite session catalog and state machine;
- rotate raw chunks and generate Parquet/events files;
- implement range-query APIs and recover `.partial` sessions;
- add loop/runtime metrics and recording fault UI.

Exit criteria:

- replayed values equal the raw input for a golden recording;
- a schema change remains readable with old software;
- disk-full and slow-disk simulations fail visibly without affecting robot control.

### Phase 3 — graph framework and replay

- app-owned `TimeSeriesChart` and graph presets;
- live browser ring buffers, rate limiting, and min/max downsampling;
- `DataSource`/`ReplayClock` abstractions;
- session browser, seek, speed, event track, and data-only replay;
- CSV export for selected channels/ranges, not entire raw sessions by default.

Exit criteria:

- a 30-minute session opens quickly and zooms without loading every sample;
- the same mechanism graph works with live and recorded sources;
- gaps and reconnects are visually explicit.

### Phase 4 — camera capture and synchronization

- dedicated OpenCV capture service with per-frame timestamps and diagnostics;
- segmented H.264 writing and frame sidecars;
- robot synchronization marker event/LED API applied in the OpMode loop;
- ROI selection tool, brightness trace, coded-flash detector, clock fit, and uncertainty report;
- synchronized video/data replay.

Exit criteria:

- markers are detected reliably under expected lighting;
- repeated tests report a measured residual alignment error and p95 bound;
- variable camera buffering is detected rather than silently “corrected”;
- video process failure does not stop telemetry recording.

### Phase 5 — Oracle archival

- persistent upload queue and checksums;
- Object Storage bucket/prefix policy;
- resumable/multipart video uploads;
- optional Ubuntu catalog/auth API;
- remote verification, manifest-last commit, retention controls;
- remote session browsing/download into the same replay path.

Exit criteria:

- interrupted upload resumes without duplicate/corrupt objects;
- a remote session can be downloaded/replayed and passes checksum validation;
- local deletion cannot occur before verified upload and policy approval.

### Phase 6 — analysis plugin SDK

- documented Python analysis interface over normalized session data;
- derived-artifact provenance/versioning;
- first real analyses: loop timing, PID response, and event-centered video clips;
- comparison view for two sessions.

---

## 13. Proposed repository structure

```text
FtcRobotController/TeamCode/src/main/java/org/firstinspires/ftc/teamcode/
└── data/
    ├── api/                 typed channels, sample/event builders
    ├── protocol/            generated protobuf + framing
    ├── runtime/             session, queues, threads, metrics
    ├── sync/                robot clock and marker requests
    └── testopmodes/         synthetic/no-hardware diagnostics

protocol/
├── robot_data.proto
├── README.md               compatibility and generation instructions
└── test-vectors/           byte-level golden frames

web_driver_station/backend/
└── data/
    ├── service.py
    ├── robot_data_tcp_server.py
    ├── protocol.py
    ├── clock_sync.py
    ├── recording.py
    ├── parquet.py
    ├── sessions.py
    ├── camera.py
    ├── led_sync.py
    ├── upload.py
    └── api.py

web_driver_station/frontend/src/
└── data/
    ├── types.ts
    ├── LiveDataSource.ts
    ├── RecordedDataSource.ts
    ├── ReplayClock.ts
    ├── TimeSeriesChart.tsx
    ├── SessionBrowser.tsx
    └── ReplayWorkbench.tsx
```

Keep generated protobuf files reproducible through a documented Gradle/Python task. Commit either generated sources or a hermetic generation toolchain; do not require every student to guess compatible compiler versions.

---

## 14. Test plan and acceptance metrics

### Protocol tests

- split every golden frame at every possible byte boundary;
- concatenate multiple frames in one read;
- truncated, zero, oversized, unknown-field, and incompatible-version cases;
- high sequence numbers and wrap/overflow policy;
- schema evolution and old-recording replay;
- randomized/fuzz input with bounded resource use.

### Control Hub tests

- no laptop, slow laptop, disconnect during batch, rapid reconnect;
- producer faster than consumer and correct `Gap` record;
- important-event queue saturation;
- repeated OpMode init/start/stop without leaked threads or ports;
- background encoder exception;
- loop benchmark with garbage-collection monitoring.

Suggested initial performance targets, to be revised after measurement:

- publish/enqueue p99 under 200 microseconds for a normal snapshot;
- zero blocking socket/disk calls on the OpMode thread;
- no telemetry-induced missed robot loop deadline in a 30-minute stress run;
- bounded memory under a disconnected laptop;
- explicit accounting for 100% of missing session sequences.

### Laptop/storage tests

- forced process kill at every chunk stage and successful recovery;
- full disk, permission error, slow disk, malformed manifest;
- 30-60 minute soak with telemetry and video;
- checksum mismatch and interrupted multipart upload;
- two network interfaces and loss of Internet while robot Wi-Fi stays connected;
- replay query performance at full-session and zoomed ranges.

### Time/video tests

- synthetic clock offset/drift/outliers for the affine fitter;
- repeated coded flashes at several exposure/FPS settings;
- LED distractors and changing ambient light;
- camera unplug/replug, stalled frames, duplicate frames;
- start/end marker comparison to reveal drift;
- report median, p95, and maximum residual alignment error.

### End-to-end failure drills

1. Start recording before the laptop connects.
2. Disconnect and reconnect Wi-Fi during robot operation.
3. Close the browser while recording continues.
4. Kill and restart FastAPI; recover the partial session.
5. Fill a test volume to its reserve threshold.
6. Interrupt cloud upload and reboot the laptop.
7. Replay the recovered session and verify gaps, clock epochs, video segments, and checksums.

---

## 15. Decisions to make before coding

These are real design choices, not blockers to the architecture:

1. Maximum expected channel count and per-channel rates.
2. Whether recording starts automatically with OpMode init or explicitly from the UI.
3. Whether a recording spans multiple OpModes.
4. Which physical LED/output will be safe and visible for synchronization.
5. Laptop OS, webcam model/backend, desired resolution/FPS, and acceptable alignment error.
6. Local retention size and whether video is opt-in.
7. Whether the Oracle target means an Ubuntu VM disk, OCI Object Storage, or both.
8. Whether live Internet is available while connected to the robot network.
9. Authentication level for a lab prototype versus shared environments.
10. Whether remote replay is download-first or streamed from object storage.

The recommended defaults are: explicit recording sessions tied to one OpMode; telemetry available while the session is open; video opt-in; 100 Hz maximum for control signals; continuously listening laptop TCP server at a configured static IP; reconnecting Control Hub client; protobuf; raw-log-first recording; Parquet derived chunks; segmented H.264; local-first upload; Object Storage for durable remote blobs; and a thin catalog only when remote browsing is needed.

---

## 16. Recommended first milestone

The first milestone should deliberately exclude Oracle and video. Build one trustworthy path:

```text
synthetic Java OpMode
  -> non-blocking typed snapshot
  -> length-delimited protobuf over reconnecting TCP
  -> Python raw recorder
  -> one live React graph
  -> replay the saved graph
```

Then disconnect Wi-Fi, overload the queue, kill the backend, and prove that robot timing is unaffected and missing data is explicit. Once this vertical slice is reliable, camera synchronization and cloud archival become independent extensions rather than extra failure modes inside an unproven core.

---

## References

- Existing project mental model: [`APP_MENTAL_MODEL.md`](APP_MENTAL_MODEL.md)
- Existing laptop/Robocol background-thread separation: [`ftc_control_hub.py`](ftc_control_hub.py) and [`web_driver_station/backend/main.py`](web_driver_station/backend/main.py)
- [Android `SystemClock` documentation](https://developer.android.com/reference/android/os/SystemClock) — monotonic elapsed-time clock behavior
- [Android `java.net` documentation](https://developer.android.com/reference/java/net/package-summary) — TCP `Socket` and `ServerSocket`
- [Protocol Buffers language guide](https://protobuf.dev/programming-guides/proto3/) — schema and compatibility model
- [OpenCV `VideoCapture` properties](https://docs.opencv.org/4.x/d4/d15/group__videoio__flags__base.html) — backend-dependent capture properties
- [Apache Parquet documentation](https://parquet.apache.org/docs/) — columnar storage format
- [OCI Object Storage](https://docs.oracle.com/en-us/iaas/Content/Object/home.htm)
- [OCI pre-authenticated requests](https://docs.oracle.com/en-us/iaas/Content/Object/Tasks/usingpreauthenticatedrequests.htm)
- [OCI multipart uploads with pre-authenticated requests](https://docs.oracle.com/en-us/iaas/Content/Object/Tasks/usingpreauthenticatedrequests_topic-Working_with_PreAuthenticated_Requests.htm)
