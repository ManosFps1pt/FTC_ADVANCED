# Robot Telemetry TCP Packet Format (v1)

**Status:** implementation contract

This document defines the first complete telemetry protocol between the FTC
Control Hub and the laptop application. It is deliberately independent from
the existing FTC Driver Station / Robocol control traffic.

The protocol is **observability-first**. Losing the laptop, its browser, or
its disk must never block an OpMode, alter motor output, or access FTC hardware
from a networking thread.

## 1. Responsibilities and boundaries

The robot sends facts:

- what physical or software source a signal came from;
- what each value means and which units it uses;
- timestamped values, state changes, and faults.

The laptop application owns presentation:

- which values appear in a table, line chart, bar chart, field view, or custom
  mechanism tool;
- dashboard layout and user preferences;
- recording, replay, downsampling, and derived analysis.

**No packet sent by the robot contains a chart type, widget type, colour,
layout, or other visualization instruction.**

The diagnostic TCP connection is not a general robot-command channel. Drive
commands, emergency stop, and any safety-critical control remain on the normal
FTC control path. A future diagnostic command protocol, if needed, must be a
separate, allowlisted, OpMode-thread-validated design.

## 2. Transport framing

TCP is a byte stream; one socket `read()` is not one message. Every protocol
frame therefore has this exact form:

```text
0                   3 4
+--------------------+------------------------------------+
| uint32_be byteCount | byteCount bytes of UTF-8 JSON text |
+--------------------+------------------------------------+
```

- `uint32_be` is an unsigned, four-byte, big-endian integer.
- `byteCount` is the number of UTF-8 bytes in the JSON document, not its Java
  character count.
- A receiver must use `readExactly()` until it has all four length bytes, then
  use `readExactly(byteCount)` for the body.
- Empty frames are invalid.
- Version 1 rejects frames larger than **1,048,576 bytes** (1 MiB).
- JSON is UTF-8 and one complete object per frame. Newlines have no protocol
  meaning.

Version 1 intentionally uses framed JSON because it is easy to inspect while
the telemetry API is still changing. A later protobuf version may use the same
session and signal model, but it must use a new protocol version.

## 3. Common envelope

Every frame is one JSON object with this envelope:

```json
{
  "protocol": "ftc-telemetry",
  "version": 1,
  "type": "sample",
  "sessionId": "01f7d1cc-4b3b-4bb5-8cfe-2baa01b92bf1",
  "connectionId": "8802cb80-90f1-4eb0-97db-6d7e7a31dd02",
  "sequence": "42",
  "robotTimeNs": "1845920312345",
  "data": {}
}
```

| Field | Required | Meaning |
|---|---:|---|
| `protocol` | yes | Always `"ftc-telemetry"`. |
| `version` | yes | Integer protocol version; v1 is `1`. |
| `type` | yes | Message type listed in this document. |
| `sessionId` | yes | UUID made when the OpMode data session opens. It survives a TCP reconnect. |
| `connectionId` | yes | UUID made for this one TCP connection. It changes after reconnect. |
| `sequence` | yes | Decimal-string unsigned 64-bit frame sequence, starting at `"0"` for each connection. |
| `robotTimeNs` | yes | Decimal-string monotonic Control Hub time in nanoseconds, taken when this message is produced. |
| `data` | yes | Object whose schema depends on `type`. |

### Integer and time representation

JavaScript cannot represent all 64-bit integers exactly. Therefore every
protocol sequence number and nanosecond timestamp is a **base-10 string**,
never a JSON number. Example: `"1845920312345"`.

Normal floating-point measurements are JSON numbers. A signal declared as
`int64` is also encoded as a decimal string. The laptop converts it to the
appropriate internal numeric representation after validation.

`robotTimeNs` must be monotonic time, not wall-clock time. For Android this is
`SystemClock.elapsedRealtimeNanos()`. The laptop records its own receipt time
separately; receipt time must not replace source time.

### Generic validation

- UUIDs must be valid canonical UUID strings.
- `sequence` must increase by one within one `connectionId`. A receiver records
  a discontinuity as a transport issue; it does not invent missing samples.
- JSON numbers must be finite. `NaN`, `Infinity`, and `-Infinity` are invalid.
- Unknown optional fields must be preserved in the raw recording and ignored by
  a v1 reader.
- Unknown required message types or malformed required fields result in one
  `error` response followed by connection close.

## 4. Session lifecycle

The Control Hub is the reconnecting TCP client; the laptop is the listening TCP
server.

```text
Control Hub                                      Laptop
-----------                                      ------
connect ---------------------------------------> accept
hello -----------------------------------------> validate / create-or-resume session
                              <---------------- hello_ack
catalog ---------------------------------------> store active signal catalog
sample / event / heartbeat / gap -------------> record + live fan-out
                              <---------------- clock_sync_request (optional)
clock_sync_response --------------------------> update clock model
session_end -----------------------------------> finalize when appropriate
disconnect ------------------------------------> connection ends; session may resume
```

After every successful connection (including a reconnect), the robot sends in
this order:

1. `hello`
2. waits for an accepted `hello_ack`
3. `catalog` containing the complete currently active catalog
4. zero or more `sample`, `event`, `heartbeat`, and `gap` frames

The first `sample` must not precede the `catalog` for its `schemaRevision`.
The laptop may reject a major protocol mismatch, duplicate active session, or
invalid identity. It never acknowledges individual sample frames: TCP already
provides ordered delivery, and per-sample acknowledgements add latency.

## 5. Robot-to-laptop messages

### 5.1 `hello`

Sent once per TCP connection before any catalog or data frame.

```json
{
  "protocol": "ftc-telemetry",
  "version": 1,
  "type": "hello",
  "sessionId": "01f7d1cc-4b3b-4bb5-8cfe-2baa01b92bf1",
  "connectionId": "8802cb80-90f1-4eb0-97db-6d7e7a31dd02",
  "sequence": "0",
  "robotTimeNs": "1845920000000",
  "data": {
    "robotId": "ftc-12345",
    "robotName": "FTC Advanced",
    "opModeName": "TeleOp Main",
    "startedAtRobotTimeNs": "1845919000000",
    "software": {
      "appVersion": "1.0.0",
      "gitCommit": "a1b2c3d",
      "sdkVersion": "11.2.1"
    },
    "queueCapacity": 256,
    "capabilities": ["catalog", "full-snapshot", "events", "gaps", "clock-sync"]
  }
}
```

`robotId`, `robotName`, and `opModeName` are non-empty strings. `gitCommit` is
optional when there is no Git build. `queueCapacity` is the bounded number of
unsent telemetry frames available on the Control Hub.

### 5.2 `catalog`

The catalog is a complete description of the signals the current OpMode may
send. It is sent immediately after `hello_ack`, and again whenever the active
catalog changes. A catalog replaces the entire previous catalog for the
session; it is not a patch.

```json
{
  "protocol": "ftc-telemetry",
  "version": 1,
  "type": "catalog",
  "sessionId": "01f7d1cc-4b3b-4bb5-8cfe-2baa01b92bf1",
  "connectionId": "8802cb80-90f1-4eb0-97db-6d7e7a31dd02",
  "sequence": "1",
  "robotTimeNs": "1845920050000",
  "data": {
    "schemaRevision": 1,
    "devices": [
      {
        "id": "drivetrain.frontLeft",
        "label": "Front Left Drive",
        "subsystem": "drivetrain",
        "deviceType": "dcMotor"
      },
      {
        "id": "arm.motor",
        "label": "Arm Motor",
        "subsystem": "arm",
        "deviceType": "dcMotor"
      },
      {
        "id": "debug.armController",
        "label": "Arm Controller",
        "subsystem": "arm",
        "deviceType": "software"
      }
    ],
    "signals": [
      {
        "id": "drivetrain.frontLeft.currentA",
        "label": "Front Left Current",
        "deviceId": "drivetrain.frontLeft",
        "quantity": "current",
        "unit": "A",
        "valueType": "float64",
        "role": "measured",
        "sampleHintHz": 50
      },
      {
        "id": "arm.positionTicks",
        "label": "Arm Position",
        "deviceId": "arm.motor",
        "quantity": "position",
        "unit": "tick",
        "valueType": "int64",
        "role": "measured",
        "sampleHintHz": 50
      },
      {
        "id": "debug.arm.state",
        "label": "Arm State",
        "deviceId": "debug.armController",
        "quantity": "state",
        "unit": "none",
        "valueType": "enum",
        "role": "diagnostic"
      }
    ]
  }
}
```

`schemaRevision` is a positive integer and increases when the catalog changes
within a session. A new OpMode session normally starts at revision `1`.

#### Device definition

| Field | Required | Meaning |
|---|---:|---|
| `id` | yes | Stable, dotted identifier unique within the catalog. |
| `label` | yes | Human-readable source name. |
| `subsystem` | yes | Logical owner such as `drivetrain`, `arm`, `intake`, or `power`. |
| `deviceType` | yes | Physical/software class, e.g. `dcMotor`, `servo`, `imu`, `voltageSensor`, `camera`, `software`, or `system`. |

#### Signal definition

| Field | Required | Meaning |
|---|---:|---|
| `id` | yes | Stable, dotted signal identifier unique within the catalog. |
| `label` | yes | Human-readable signal name. |
| `deviceId` | no | Device that produces the value. Omit only for robot-wide values such as `system.loopDurationMs`. |
| `quantity` | yes | What the number/state represents: `current`, `power`, `position`, `velocity`, `heading`, `temperature`, `state`, `error`, etc. |
| `unit` | yes | Physical unit such as `A`, `V`, `tick`, `rad`, `rad/s`, `m`, `m/s`, `percent`, `ms`, or `none`. |
| `valueType` | yes | Encoding listed below. |
| `role` | yes | Semantic role: `measured`, `target`, `command`, `error`, `status`, or `diagnostic`. |
| `sampleHintHz` | no | Intended maximum source update rate. It is metadata, not a delivery guarantee. |
| `description` | no | Short explanation for a person inspecting a recording. |

The catalog describes data semantics, not presentation. In particular, it must
not contain `display`, `chartType`, `widget`, `colour`, `layout`, or similar
fields.

#### Identifier rules

`id` and `deviceId` use dot-separated camelCase-like segments:

```text
^[a-z][A-Za-z0-9]*(\.[a-z][A-Za-z0-9]*)*$
```

Examples: `drivetrain.frontLeft.currentA`, `debug.arm.state`, and
`system.loopDurationMs`.

Signal IDs are data-contract identifiers, not display text. Their meaning and
unit must never change during a session. A changed type or meaning requires a
new signal ID or a new catalog revision.

#### Value types and JSON encoding

| `valueType` | JSON value |
|---|---|
| `float64` | Finite JSON number, e.g. `3.25`. |
| `int64` | Base-10 string, e.g. `"1762"`. |
| `boolean` | `true` or `false`. |
| `string` | JSON string. |
| `enum` | JSON string selected by robot code, e.g. `"MOVING_TO_SCORE"`. |
| `vector2` | Object `{ "x": number, "y": number }`; both values use the signal unit. |
| `vector3` | Object `{ "x": number, "y": number, "z": number }`; all values use the signal unit. |
| `pose2d` | Object `{ "x": number, "y": number, "headingRad": number }`; `unit` must be `m-rad` or `in-rad`. |

No arbitrary nested JSON object type is allowed in v1. A new structured value
shape must be added to this document before robot and server implementations
rely on it.

### 5.3 `sample`

A sample is one coherent, point-in-time **full robot state snapshot**. It is
the primary live and recorded-data message.

```json
{
  "protocol": "ftc-telemetry",
  "version": 1,
  "type": "sample",
  "sessionId": "01f7d1cc-4b3b-4bb5-8cfe-2baa01b92bf1",
  "connectionId": "8802cb80-90f1-4eb0-97db-6d7e7a31dd02",
  "sequence": "42",
  "robotTimeNs": "1845920312345",
  "data": {
    "sampleSequence": "17",
    "schemaRevision": 1,
    "values": {
      "drivetrain.frontLeft.currentA": 3.4,
      "drivetrain.frontLeft.power": 0.52,
      "drivetrain.frontRight.currentA": 3.15,
      "drivetrain.backLeft.currentA": 3.31,
      "drivetrain.backRight.currentA": 3.08,
      "arm.positionTicks": "1762",
      "arm.targetTicks": "1800",
      "debug.arm.state": "MOVING_TO_SCORE",
      "debug.arm.errorTicks": "38",
      "debug.arm.limitSwitchPressed": false,
      "power.batteryVoltage": 12.46
    }
  }
}
```

| Field | Required | Meaning |
|---|---:|---|
| `sampleSequence` | yes | Decimal-string unsigned 64-bit sequence that increases for the whole session and does not reset on reconnect. |
| `schemaRevision` | yes | Catalog revision that defines every key in `values`. |
| `values` | yes | Map from catalog signal ID to correctly encoded value. |

Rules:

1. Every v1 sample is **full**: it contains one entry for every active catalog
   signal. The value is the newest known value as of `robotTimeNs`.
2. A signal that is not currently available is sent as JSON `null`. It remains
   in the snapshot; omission is not used to mean unavailable.
3. `null` is not a measurement. The receiver must render it as unavailable and
   must not interpolate across it silently.
4. No `values` key may be absent from the active catalog, and no catalog signal
   may appear with an incompatible JSON shape.
5. FTC hardware reads occur in the OpMode loop. The loop copies values into an
   immutable snapshot and performs a bounded, non-blocking enqueue. The socket
   worker serializes and sends that copy later.

Full snapshots keep implementation and replay unambiguous. If profiling later
shows that the JSON overhead is too large, a future protocol version can add
delta/batched encoding while retaining the same catalog semantics.

### 5.4 `event`

Events describe important occurrences rather than continuous state: state
transitions, command changes, faults, encoder resets, and annotations. They
make logical bugs searchable in replay.

```json
{
  "protocol": "ftc-telemetry",
  "version": 1,
  "type": "event",
  "sessionId": "01f7d1cc-4b3b-4bb5-8cfe-2baa01b92bf1",
  "connectionId": "8802cb80-90f1-4eb0-97db-6d7e7a31dd02",
  "sequence": "43",
  "robotTimeNs": "1845920313000",
  "data": {
    "name": "arm.stateChanged",
    "severity": "info",
    "attributes": {
      "from": "IDLE",
      "to": "MOVING_TO_SCORE",
      "reason": "operatorRequestedHighScore"
    }
  }
}
```

- `name` follows the same identifier rule as signal IDs.
- `severity` is one of `debug`, `info`, `warning`, `error`, or `critical`.
- `attributes` is a flat map with string keys and JSON primitive values only
  (`string`, finite number, boolean, or `null`). It is intentionally not a
  second arbitrary object protocol.

### 5.5 `gap`

A `gap` makes loss visible when the Control Hub's bounded queue discards
unsent snapshots or a source intentionally skips a range. The robot emits it
as soon as practical after the loss.

```json
{
  "protocol": "ftc-telemetry",
  "version": 1,
  "type": "gap",
  "sessionId": "01f7d1cc-4b3b-4bb5-8cfe-2baa01b92bf1",
  "connectionId": "8802cb80-90f1-4eb0-97db-6d7e7a31dd02",
  "sequence": "44",
  "robotTimeNs": "1845920400000",
  "data": {
    "firstMissingSampleSequence": "18",
    "lastMissingSampleSequence": "31",
    "reason": "outboundQueueFull"
  }
}
```

`reason` is one of `outboundQueueFull`, `encoderFailure`, `sourceDisabled`, or
another documented string. The recorder stores the gap as a first-class record;
charts and replay must visibly break at it rather than fabricate a smooth line.

### 5.6 `heartbeat`

Sent every second while connected, even when no sample is due.

```json
{
  "protocol": "ftc-telemetry",
  "version": 1,
  "type": "heartbeat",
  "sessionId": "01f7d1cc-4b3b-4bb5-8cfe-2baa01b92bf1",
  "connectionId": "8802cb80-90f1-4eb0-97db-6d7e7a31dd02",
  "sequence": "45",
  "robotTimeNs": "1845921000000",
  "data": {
    "queuedFrames": 3,
    "droppedSamples": "14",
    "lastSampleSequence": "31"
  }
}
```

This measures the telemetry system itself. It is not robot-state telemetry.

### 5.7 `session_end`

Sent from `OpMode.stop()` as a best-effort terminal marker. The robot must not
wait for it to be delivered before stopping motors or ending the OpMode.

```json
{
  "protocol": "ftc-telemetry",
  "version": 1,
  "type": "session_end",
  "sessionId": "01f7d1cc-4b3b-4bb5-8cfe-2baa01b92bf1",
  "connectionId": "8802cb80-90f1-4eb0-97db-6d7e7a31dd02",
  "sequence": "46",
  "robotTimeNs": "1845922000000",
  "data": {
    "reason": "opModeStopped",
    "lastSampleSequence": "31"
  }
}
```

## 6. Laptop-to-robot messages

The robot-side connection reader runs on the background networking thread. It
never reads or writes FTC hardware.

### 6.1 `hello_ack`

The laptop sends this after a valid `hello`.

```json
{
  "protocol": "ftc-telemetry",
  "version": 1,
  "type": "hello_ack",
  "sessionId": "01f7d1cc-4b3b-4bb5-8cfe-2baa01b92bf1",
  "connectionId": "8802cb80-90f1-4eb0-97db-6d7e7a31dd02",
  "sequence": "0",
  "robotTimeNs": "0",
  "data": {
    "accepted": true,
    "serverTimeNs": "553401220000",
    "sessionDisposition": "new"
  }
}
```

`sequence` is independently counted by the laptop for frames it sends.
`robotTimeNs` is `"0"` because the laptop does not know a robot timestamp for
this response. `sessionDisposition` is `new` or `resumed`.

If `accepted` is `false`, `data` also contains a short `reason`. The robot
closes the connection and retries with normal backoff.

### 6.2 `clock_sync_request` and `clock_sync_response`

Clock synchronization is optional in the first visualizer but required before
video/data alignment. The laptop periodically sends:

```json
{
  "protocol": "ftc-telemetry",
  "version": 1,
  "type": "clock_sync_request",
  "sessionId": "01f7d1cc-4b3b-4bb5-8cfe-2baa01b92bf1",
  "connectionId": "8802cb80-90f1-4eb0-97db-6d7e7a31dd02",
  "sequence": "1",
  "robotTimeNs": "0",
  "data": {
    "requestId": "9a0754e6-e088-4d95-a3c0-3b2f1e8dd172",
    "serverSendTimeNs": "553401230000"
  }
}
```

The Control Hub immediately responds from its networking thread:

```json
{
  "protocol": "ftc-telemetry",
  "version": 1,
  "type": "clock_sync_response",
  "sessionId": "01f7d1cc-4b3b-4bb5-8cfe-2baa01b92bf1",
  "connectionId": "8802cb80-90f1-4eb0-97db-6d7e7a31dd02",
  "sequence": "47",
  "robotTimeNs": "1845921010123",
  "data": {
    "requestId": "9a0754e6-e088-4d95-a3c0-3b2f1e8dd172",
    "serverSendTimeNs": "553401230000",
    "robotReceiveTimeNs": "1845921010000",
    "robotSendTimeNs": "1845921010123"
  }
}
```

The laptop supplies its receipt time for the response and calculates the clock
mapping. Neither side substitutes wall-clock time for these values.

### 6.3 `error`

Either peer may send one error frame before closing an unrecoverable connection.

```json
{
  "protocol": "ftc-telemetry",
  "version": 1,
  "type": "error",
  "sessionId": "01f7d1cc-4b3b-4bb5-8cfe-2baa01b92bf1",
  "connectionId": "8802cb80-90f1-4eb0-97db-6d7e7a31dd02",
  "sequence": "2",
  "robotTimeNs": "0",
  "data": {
    "code": "unsupportedProtocolVersion",
    "message": "Server accepts only protocol version 1"
  }
}
```

`code` is a stable machine-readable string; `message` is a concise human
description. Do not include stack traces, secrets, or huge payloads.

## 7. How the server chooses visualizations

The server resolves data by catalog metadata, not by string-name guesses and
not by robot-supplied chart instructions. For example, a live bar-chart
configuration can be stored entirely on the laptop as:

```json
{
  "widgetType": "liveBarChart",
  "title": "Drive Motor Current",
  "query": {
    "subsystem": "drivetrain",
    "deviceType": "dcMotor",
    "quantity": "current",
    "unit": "A"
  }
}
```

It matches the catalog's device and signal metadata, then reads the most recent
value for every matched signal from the latest full snapshot.

For logical debugging, the default first view is a table/timeline, not a chart:

```text
time       debug.arm.state      targetTicks  positionTicks  errorTicks
12.450 s   MOVING_TO_SCORE      1800         1762           38
```

Custom mechanism tools may explicitly select the signals they require. Example:
a field-replay tool can use a signal declared as `pose2d`; a PID tool can put
its `target`, `measured`, and `error` signals on the same server-defined graph.

## 8. Implementation requirements

### Control Hub

- Generate `sessionId` when a data session opens and keep it until the OpMode
  session closes.
- Generate a fresh `connectionId` after every successful TCP connection.
- Use a bounded producer queue. `publishSnapshot()` copies data and returns
  immediately; JSON encoding, network I/O, reconnecting, and clock-sync replies
  happen on the worker thread.
- On a full queue, drop the **oldest unsent sample**, retain the newest state,
  increment `droppedSamples`, and later emit a `gap` frame.
- Read hardware only in the OpMode loop. Do not let the network worker touch
  motors, servos, IMU, or other FTC hardware.
- Send one complete `catalog` before the first sample and after a reconnect.

### Laptop backend

- Validate framing and envelope fields before accepting data.
- Append the raw framed bytes to the session recorder before live UI fan-out.
- Store the active catalog with every session and schema revision.
- Record frame sequence discontinuities and explicit `gap` records.
- Treat the raw recorder, browser connections, charts, and future upload jobs
  as independent consumers with independent bounded queues.
- Do not allow a slow browser to delay recording or robot control.

### Browser/frontend

- Receive normalized data from the laptop over WebSocket; it does not connect
  directly to the Control Hub telemetry TCP port.
- Build navigation from catalog subsystem/device metadata.
- Show current values even before advanced chart tooling exists.
- Preserve unavailable (`null`) values and gaps in tables and charts.
- Keep chart layout/configuration separate from recorded robot packets.

## 9. Worked minimal session

```text
1. Control Hub connects.
2. Control Hub sends hello(session A, connection X).
3. Laptop replies hello_ack(accepted, new).
4. Control Hub sends catalog(revision 1: four drive motors, arm, debug signals).
5. Control Hub sends full sample #0 at source timestamp T0.
6. Control Hub sends event arm.stateChanged at T1.
7. Control Hub sends full sample #1 at T2.
8. Wi-Fi stalls; bounded queue drops samples #2 through #12.
9. Connection resumes; Control Hub sends hello(session A, connection Y), ack,
   catalog(revision 1), gap(#2-#12), and current full sample #13.
10. OpMode stops; Control Hub best-effort sends session_end.
```

The replay UI can now show exactly what is known, where it is missing, and why
the robot made its recorded decisions—without ever needing to pause the robot.
