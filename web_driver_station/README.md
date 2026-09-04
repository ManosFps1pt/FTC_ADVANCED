# Local web Driver Station dashboard

This is a local FastAPI and TypeScript replacement for the CustomTkinter test
window. It keeps Robocol, heartbeats, and gamepad transmission in Python; the
browser only sends desired gamepad state and renders telemetry.

Use it only on a controlled test robot. It is not an FTC event-legal Driver
Station and the browser must never be exposed beyond the local computer.

## Development

The easiest Windows launch is to double-click
`run_web_driver_station.bat` in the repository root. It prepares the local
environment, builds the dashboard, starts the local server, and opens the
browser. Python 3.11+ and Node.js LTS with pnpm are required the first time.

From the repository root, create a virtual environment and install the Python
dependencies:

```powershell
python -m venv .venv-web
.\.venv-web\Scripts\python.exe -m pip install -r .\web_driver_station\backend\requirements.txt
```

Install the frontend dependencies (Node.js LTS and pnpm are required):

```powershell
cd .\web_driver_station\frontend
pnpm install
pnpm run build
cd ..\..
```

Then run one local process and open `http://127.0.0.1:8000` in a browser:

```powershell
.\.venv-web\Scripts\python.exe -m uvicorn web_driver_station.backend.main:app --host 127.0.0.1 --port 8000
```

For frontend development, run the backend command above and `pnpm run dev` in
`frontend`; the Vite server proxies `/api` and `/ws` to the backend.

The dashboard lets the operator connect to a Robot Controller, list and start
an OpMode, switch between virtual gamepad slots, view telemetry, and stop the
OpMode. A physical USB or Bluetooth gamepad is read directly by the browser;
no extra Python or Node process is needed. When at least one physical gamepad
is connected, virtual controls are hidden. Hold **Start + A** to assign that
controller to Driver 1, or **Start + B** to assign it to Driver 2. The driver
status strip shows which slots are active. Closing the browser is not a
guaranteed stop action, so use the Stop button before closing it.

## Robot configuration

The **Configure Robot** panel follows the Driver Station flow rather than
asking the operator to edit XML: choose a saved configuration, open its scanned
portal and REV Hub, then configure Motors, Servos, Digital, PWM, Analog, and
I2C port groups using device-type dropdowns and hardware-map names. The app
holds a structured configuration model and generates XML only for **Save &
activate**.

The currently loaded RC configuration supplies the scan-owned portal serial
numbers, module addresses, cameras, and automatic IMU entries. **New from
loaded scan** uses that discovered topology as a safe baseline and retains
automatic devices; it intentionally does not invent hub addresses or serial
numbers. To make the first configuration on an RC with no saved configuration,
use the official Driver Station: **Configure Robot → New → Scan → Save**. The
dashboard can then load that scanned topology and create additional copies.
It preserves unknown XML elements and attributes that were loaded from the RC,
but does not expose a raw XML editor.

The backend rejects saves while an OpMode is active or the RC is not in
`STOPPED`/`NOT_STARTED`, validates the generated XML before it is sent,
requires a confirmation in the browser, and does not overwrite read-only SDK
templates. Saving a configuration can change the active hardware map, so test
this feature with the robot disabled or safely supported first.

## Independent robot-data TCP transport test

The first robot-data transport is a separate backend service and does not yet
feed the frontend. Start its laptop listener from the repository root:

```powershell
python -m web_driver_station.backend.robot_data_tcp_server --host 0.0.0.0 --port 5810
```

Keep the laptop's robot-network adapter on **DHCP**. On initialization,
**Telemetry Gamepad Test** broadcasts `where_is_data_server` over UDP port
`5811`; the listener replies with the laptop's current address and TCP port.
The OpMode then opens its framed TCP stream to that reply on port `5810`.
Permit **UDP 5811** and **TCP 5810** on the applicable Windows firewall
profile. Initialize and start **Telemetry Gamepad Test**; the terminal
continuously redraws the newest framed packet received from the Control Hub.
Stopping the OpMode closes that session, while the laptop keeps listening for
the next one.

The lifecycle control is a single state-aware button: **Init** when stopped,
**Start** after initialization, and **Stop** while running. The connection
badge also shows a sampled ICMP network ping to the RC every two seconds; a
dash means ICMP is unavailable or blocked even though the Robocol link may
still be healthy.

## Dashboard camera capture

The Camera capture panel uses the Android phone assigned the **camera** ADB
role. **Direct scrcpy** is the default: it starts a small 240-pixel, 10-FPS
idle preview when the dashboard starts, records at the configured FPS
(60 by default) from OpMode Init through Stop, and resumes the preview after
the MP4 finalizes. Its only camera controls are lens, aspect ratio, recording
FPS, and flip. The preview can be stopped without changing the saved mode.

**ADB Volume Up** remains available for phones whose OEM Camera app is the
preferred recorder. It has no live preview, so it never competes with the
native camera. Configure the phone's native camera settings before Init.

Both modes require an authorized Android 12+ camera phone and the direct mode
also requires `scrcpy` on `PATH` (or configured through `SCRCPY`). Errors are
shown in the Camera capture panel without preventing the rest of the dashboard
from running.

## Durable `.ftclog` telemetry recordings

Every valid protobuf frame accepted from the independent robot-data TCP stream
is now appended to `web_driver_station/recordings/<session-uuid>/raw/` as an
`.ftclog`. This is independent of the browser's bounded live history: stopping
an OpMode or losing its TCP connection finalizes the active file instead of
erasing its snapshots. A reconnect for the same session starts a new numbered
stream file, preserving the interruption explicitly.

Each log contains a versioned header, then the exact protobuf frame bytes,
laptop monotonic receipt timestamp, frame length, and CRC32 per record. An
unexpected crash leaves a `.ftclog.partial` file whose complete records can be
read safely up to its incomplete tail. Set `ROBOT_DATA_RECORDINGS_DIR` to put
recordings somewhere other than the default directory.

Camera capture starts before the telemetry protocol supplies its UUID, so it
uses a private staging folder briefly. The first raw telemetry packet binds the
video to `recordings/<telemetry-session-uuid>/video/`; the raw log, video,
manifest, and frame maps are then uploaded together automatically once both
are finalized. Completed uploads keep the local session until the dashboard
operator explicitly deletes it. Failed uploads keep the local artifacts and
offer Retry. If no telemetry session arrives, the staged video is preserved and
the camera panel reports the recovery path instead of uploading an unpaired
clip.

Use `GET /api/data/recordings` to list saved sessions, and download a finalized
file with `GET /api/data/recordings/{session-id}/stream-00000.ftclog`.

## Laptop video recording (first replay layer)

The backend can record a locally attached webcam independently of the robot
control and telemetry streams. It writes 60-second MP4 segments and a JSONL
sidecar for every segment; each sidecar row maps an encoded frame index to the
timestamp assigned immediately after OpenCV's `grab()` returns. This is the
timestamp source used later to synchronize video with robot snapshots.

Start the backend, then start a recording (camera index `0` is the default
laptop webcam):

```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/video/start `
  -ContentType application/json `
  -Body '{"device_index":0,"width":1280,"height":720,"fps":30,"segment_seconds":60,"camera_id":"cam0"}'
```

Stop it with:

```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/video/stop
```

Recordings are written under `web_driver_station/recordings/<session-uuid>/`
by default, or under `ROBOT_DATA_RECORDINGS_DIR` if set so telemetry and
camera artifacts retain one shared session root. Completed segments are
available at `/api/video/sessions/{session-id}/segments/segment-00000.mp4`;
the session list and recorder state are available at `/api/video/sessions` and
`/api/video/status`. Camera auto-exposure and USB buffering mean the frame
timestamp is not yet an exposure timestamp; the upcoming LED-marker layer will
measure the corresponding camera delay rather than pretending it is zero.
