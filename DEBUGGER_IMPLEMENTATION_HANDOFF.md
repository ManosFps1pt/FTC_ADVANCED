# What was built in this chat

Date: 2026-09-07

## Delivered scope

Implemented the first Debugger benchmark, **Friction & Free-Spin Characterization**, across the robot, TCP connection, laptop application, and existing Oracle viewer.

The implemented workflow is:

**Select registered mechanism → select benchmark → execute on robot → transfer buffered samples → analyze on laptop → review report → discard or upload → view saved report on Oracle.**

This is a software implementation with simulated validation. A real motor benchmark has not been run. The Android APK was built, but this chat did not install it on the Control Hub.

## 1. Robot execution

Added a hardware-independent benchmark execution interface and a friction procedure, with an FTC adapter that connects the procedure to `DcMotorEx` and battery voltage sensing.

The procedure implements:

- Rest detection before excitation.
- A gradual voltage ramp to detect sustained encoder movement.
- Four increasing steady operating points.
- Zero-output FLOAT coast-down.
- One to five repetitions, defaulting to three.
- Optional reverse runs when enabled in registration.
- Current cutoff, invalid-sensor handling, keepalive timeout, connection-loss shutdown, and operator abort.
- A bounded sample buffer, retained for transfer after execution.
- Saving/restoring motor run mode and zero-power behavior, with output remaining zero afterward.

Defaults are 50 Hz acquisition target, 0.25 V/s ramp, 3 V requested maximum, and the existing 0.35 power ceiling. Samples use actual robot timestamps. A capped ramp without encoder movement reports an inconclusive result rather than diagnosing a seized motor.

The initial mechanism still maps to **`debugMotor`**. No intake or shooter hardware names were invented. Encoder calibration is optional for unit conversion, but encoder feedback itself is required.

**The benchmark is disabled until `FREE_SPIN_CURRENT_LIMIT_AMPS` is set to an appropriate positive value in `Debugger.java`.** Its initial value is zero. Reverse is initially disabled, and unknown encoder calibration leaves measurements in ticks/s.

The existing manual motor-power tool remains available. The selected benchmark exposes its definition through the registry, and `SelectableOpMode` dispatches benchmark commands to the active tool. Switching is blocked while its dataset awaits acknowledgement or reported motion continues.

Main robot files, relative to `FtcRobotController/TeamCode/src/main/java/org/firstinspires/ftc/teamcode/`:

| File | Contribution |
| --- | --- |
| `Debugger.java` | Initial mechanism registration and configuration limits. |
| `SelectableOpMode.java` | Tool command dispatch, benchmark advertisement, busy checks, reconnect advertisement, and lightweight status during benchmarks. |
| `debug/Benchmark.java` | Execution contract for future benchmark implementations. |
| `debug/FrictionBenchmark.java` | Hardware-independent acquisition state machine. |
| `FrictionTool.java` | FTC hardware adapter, command handling, motor configuration, and dataset transfer. |
| `data/StructuredRobotDataClient.java` | Benchmark capability negotiation and a separate outgoing dataset queue. |

## 2. TCP protocol and result recording

Kept the existing length-prefixed **Protobuf over TCP** transport. Added benchmark messages rather than embedding JSON in TCP:

- Benchmark definitions on tool-ready messages.
- Run status with phase, repetition, and direction.
- Dataset header with run UUID, mechanism, procedure, inputs, and limits.
- Numbered chunks with individual sample timestamps and typed channel values.
- End marker with sample/chunk counts and execution outcome.
- Receipt acknowledgement.

The robot buffers samples during execution and transfers them afterward through a queue that live telemetry cannot evict. It retains the completed buffer until acknowledged and retries transfer after reconnect without rerunning the motor.

The laptop validates chunk identity, duplicate consistency, sequence numbers, timestamps, schema, and totals. Interrupted transfers remain incomplete. Fully received aborted runs remain inspectable. Small receipt records prevent a lost acknowledgement from recreating a result that was discarded or uploaded without keeping a laptop copy.

Each run gets its own UUID directory under the existing recordings root:

```text
<run-uuid>/
  manifest.json
  analysis.json
  raw/stream-*.ftclog
  storage.json          # when an uploaded laptop copy is retained
```

The raw logs preserve accepted Protobuf frame bytes. JSON stores metadata and analysis output. Benchmark recording is independent of the surrounding TCP session and does not wait for camera finalization or produce a second generic recording popup.

Changed `protocol/robot_data.proto`, regenerated Java-lite/Python bindings, and added `protocol/generate.ps1` for reproducible generation with protoc 3.25.5.

## 3. Laptop calculations

Added versioned Python analysis selected by benchmark ID. The analyzer produces:

| Measurement | What is calculated |
| --- | --- |
| Breakaway voltage | Estimated applied voltage at the marked onset of sustained movement; per repetition and median/range. |
| Current near breakaway | Mean controller current around movement onset. |
| Running kS/kV | Linear fit of absolute estimated voltage versus absolute speed, separately by direction, using valid steady windows. |
| Fit quality | R², residuals, residual RMS, and a warning for poorly supported fits. |
| Current versus speed | Mean current and variation at each accepted operating point. |
| Coast-down | Aligned speed traces, elapsed time from 80% to 20% of initial coast speed, and average deceleration. |
| Repeatability | Per-repetition measurements and within-run spreads. |

Estimated applied voltage is **battery voltage × commanded duty cycle**. Breakaway voltage is kept separate from fitted running kS. Fits require at least three distinct steady operating points; accelerating or unstable windows are excluded. Missing data produces unavailable measurements rather than invented values.

Results use ticks/s without calibration, or RPM when counts per relevant shaft revolution are supplied. The analyzer does not convert current into friction torque, identify bearing damage, compare historical runs, or recommend tuning changes.

Main backend files:

- `web_driver_station/backend/debugger_analysis.py`
- `web_driver_station/backend/debugger_results.py`
- `web_driver_station/backend/debugger_api.py`
- Integration in `web_driver_station/backend/main.py` and `protocol_codec.py`.

## 4. Laptop interface

Added `DebuggerPage.tsx` and connected it to the app’s Debugger route. It provides:

- Mechanism navigation and tool cards.
- Benchmark description and inputs.
- Run progress, phase, repetition, direction, and Abort.
- Metric cards, repetition/operating-point tables, fit residuals, and supporting plots.
- Expandable acquisition timeline and measurement-quality details.
- Raw-data and report downloads.
- A storage dialog with **Discard**, **Upload only**, and **Upload and keep laptop copy**.
- Upload failure display and retry, with local files retained.

The report component and its TypeScript types live in `web_driver_station/frontend/src/debugger/Report.tsx`, with shared styling in `debugger.css`. Both frontends use this component.

The reusable foundation is present, but v1’s input form, schema validation, and report sections are specifically implemented for friction characterization. Adding another benchmark still requires its execution, schema/analyzer, and any additional report sections.

## 5. Oracle viewer and deployment

Added a **Debugger Results** area alongside Recordings in the existing viewer. It lists saved results, filters by mechanism/benchmark, opens the shared report, and provides downloads. Oracle serves the laptop’s saved calculations; it does not recalculate reports.

Reused the existing SFTP uploader, atomic publication, library root, service, and authentication. Ordinary recordings are excluded from the debugger view and debugger bundles are excluded from the ordinary recording list.

Deployed the viewer/backend changes to the existing Oracle instance:

- URL: <https://80.225.93.186/#/debugger>
- Application: `/opt/ftc-recording-viewer`
- Library: `/srv/ftc-recordings`
- Service: `ftc-recording-viewer`

Added `recording_viewer/deploy/update_debugger.py`, which stages selected files, checks imports/routes, backs up replaced files, publishes the update, and restarts/checks the service. The first pre-update backup is `/opt/ftc-recording-viewer/.deploy-backups/20260907T131133Z`; the final update backup is `20260907T133500Z` under the same parent.

Deployment checks confirmed the service was active, the result-list API worked, and all **30 existing ordinary recordings** remained listed. No simulated result was uploaded into the production library. Viewing a real uploaded benchmark remains part of physical acceptance.

During deployment, HTTPS exposed an existing certificate-reload issue: a renewed certificate was on disk, but Nginx still served an expired certificate. Added `reload_nginx_after_renewal.sh` as a Certbot deploy hook and reloaded Nginx. Public HTTPS then validated and returned **401 without credentials**, confirming authentication remained enabled. Existing login credentials were preserved.

## 6. Validation actually performed

| Check | Result |
| --- | --- |
| Python backend/viewer suite | **51 tests passed**, including existing regression tests and new debugger tests. |
| Pure Java fake motor/clock harness | **Six scenarios passed**: repetitions/reverse, missing movement, current cutoff, sensor failure, watchdog, and abort retention. |
| Android | `:TeamCode:assembleDebug` passed. |
| Laptop frontend | Production build passed. |
| Oracle frontend | Production build passed. |
| Synthetic analysis | Recovered known kS/kV, breakaway voltage, and coast-down values; checked unavailable and unstable-data cases. |
| TCP integration | Interrupted multi-chunk transfer resumed into one bundle, with per-sample timestamps and acknowledgement. |
| Storage tests | Covered all three choices, upload failure retention, retry, and duplicate requests/receipts. |
| Browser checks | Exercised mechanism/tool selection, running state, disabled controls, Abort visibility, report rendering, storage dialog, and simulated failed-upload recovery. |
| Oracle deployment | Active service, working local API, preserved ordinary recordings, valid HTTPS, and authentication enforcement. |

Browser and motor tests used clearly labeled simulated data. Abort behavior was tested in the engine and API; browser inspection verified the Abort control was present. These checks do not establish real measurement accuracy or real-hardware safety behavior.

## 7. Remaining work before calling hardware acceptance complete

1. Set the actual mechanism’s current cutoff in `Debugger.java`.
2. Confirm encoder availability, sign, and optional shaft calibration.
3. Install the built Android app on the Control Hub and restart the updated laptop application.
4. Run the real mechanism and check voltage/current/encoder readings, phase execution, coast-down, and shutdown behavior.
5. Upload that real result and inspect it in Oracle.

No bearing-fault diagnosis, historical comparison, automatic tuning recommendation, encoderless benchmark, or physical torque estimate was implemented. Those remain outside v1.

## Repository and documentation status

The implementation remains as working-tree changes; no commit or push was made in this chat. Existing uncommitted changes were preserved. The repository already contained unrelated changes, so the complete `git diff` should not be attributed to this benchmark work.

`DEBUGGER_BENCHMARKS.md` contains the detailed operating instructions, metric definitions, protocol-generation steps, APIs, deployment instructions, and research references. This file records what this chat actually delivered and its remaining verification limits.
