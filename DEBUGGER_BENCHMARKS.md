# Debugger v1: Friction & Free-Spin Characterization

The Debugger now treats a benchmark as one repeatable experiment: select a registered mechanism and tool, run it, receive its buffered dataset, calculate a report on the laptop, then choose storage. The OpMode stays running between benchmarks. Oracle displays the saved laptop report using the same React component.

This version requires an encoder and a motor-controller current reading. It describes the current run. It does not diagnose bearing damage, compare historical results, convert current to friction torque, or recommend tuning changes.

## First hardware run

Configure `FtcRobotController/TeamCode/src/main/java/org/firstinspires/ftc/teamcode/Debugger.java`:

| Registration | Initial value | Meaning |
| --- | --- | --- |
| Hardware name | `debugMotor` | Existing hardware mapping; no intake/shooter names are assumed. |
| Mechanism ID | `motors.freeSpin` | Stable identity stored in each result. |
| `FREE_SPIN_CURRENT_LIMIT_AMPS` | **0: benchmark disabled** | Set the acceptable cutoff for the actual motor and mechanism. There is no universal default. |
| Power ceiling | 1.0 | Absolute duty-cycle ceiling, also applied during voltage excitation. |
| `FREE_SPIN_TICKS_PER_REVOLUTION` | 0 | Encoder counts per revolution of the shaft being characterized, including gearing. Unknown calibration keeps ticks/s. |
| `FREE_SPIN_REVERSE_ALLOWED` | false | Only enable for a mechanism that permits reverse operation. |

Build/install the Android app, start the laptop backend/frontend, then open **Debugger → Launch Debugger OpMode → Free-spin Motors → Friction & Free-Spin Characterization**. Select 1–5 repetitions and maximum requested voltage (0.1–12 V). Three repetitions and 12 V are the defaults. The motor runs in its configured forward direction; optional reverse performs a separate set after all forward repetitions.

Keep the Debugger page open while it executes. Abort stops output and retains acquired data. Closing the page expires its keepalive after two seconds. Losing TCP stops the procedure; reconnecting while the OpMode is still active retrieves its retained data. Stopping the OpMode before transfer loses any samples that never reached the laptop; the result remains incomplete.

After analysis choose **Discard**, **Upload only**, or **Upload and keep laptop copy**. You can first dismiss the dialog to review measurements and reopen it with **Choose storage option**. Upload failure retains the local bundle for retry. Upload-only deletion occurs after the existing SFTP uploader successfully publishes the complete remote directory.

## Procedure and acquisition

Execution runs on the OpMode loop with a 50 Hz acquisition target and actual monotonic robot timestamps. Each row records battery voltage, duty cycle, current, encoder position, reported velocity, phase, repetition, direction, operating point, and movement/steady-window markers. Estimated applied voltage is `battery_voltage × duty_cycle`; it is not a direct terminal-voltage measurement.

1. **Rest:** zero output. Require one second with reported speed below 5 ticks/s and encoder movement below three ticks. Abort if rest is not established within five seconds.
2. **Breakaway ramp:** request an additional 0.25 V/s, bounded by the requested voltage and power ceiling. Movement requires at least three ticks in the commanded direction and positive directional velocity for 150 ms. Mark the first qualifying sample retrospectively after sustained movement is confirmed. A capped sweep without movement ends as inconclusive: “Movement not observed.”
3. **Steady points:** request four equally spaced increasing voltages from detected breakaway toward the requested maximum. At each, wait up to five seconds for a one-second steady window: at least ten samples, directional mean speed at least 5 ticks/s, population standard deviation and first-to-last speed drift each at most 5% of mean. The power ceiling always applies; if it collapses requested points onto the same measured input, analysis may have insufficient distinct points.
4. **Coast:** command zero with FLOAT. Record for up to 15 seconds. The same one-second rest check is required before the next repetition/direction. An unfinished coast ends the execution as inconclusive.

Acquisition is bounded to 30,000 samples. Current-limit violations, invalid sensors, abort, keepalive expiry, buffer exhaustion, connection loss, and OpMode stop command zero output. The adapter saves motor run mode and zero-power behavior, uses `RUN_WITHOUT_ENCODER` plus FLOAT, then restores configuration with output zero. It continues reading the encoder in that mode. Switching tools is blocked until the dataset is acknowledged and reported motion has stopped.

## Metric definitions

| Result | Implementation |
| --- | --- |
| Breakaway estimated voltage | Absolute estimated voltage of the marked movement-onset sample, separately per repetition; median and min/max across this run. This is an encoder-resolution-dependent estimate. |
| Current near breakaway | Mean controller current within ±75 ms of onset, using available samples; reported separately from any torque interpretation. |
| Running kS and kV | Independently revalidate each marked steady window. Fit ordinary least squares to one mean observation per window: `abs(V_est) = kS + kV × abs(speed)`. Fit each direction separately; require at least three distinct mean voltages. |
| Fit quality | R², residual for each steady point, residual RMS. Negative kS, nonpositive kV, or R² below 0.9 flags the fit for inspection; this flag concerns the model fit, not mechanism health. |
| Current/speed | Mean speed, speed SD, current mean and population SD per valid steady window. |
| Coast 80%→20% | Initial speed is the first coast sample. Interpolate the first downward crossings of 80% and 20% of that speed using actual sample times; subtract their times. Average deceleration magnitude is `0.6 × initial_speed / elapsed_time`. Missing crossings produce an unavailable value. |
| Repeatability | Per-repetition onset/coast metrics, operating-point table, aligned coast traces, and median/min/max spreads. |

When counts per relevant shaft revolution are supplied, speed becomes RPM through `ticks/s × 60/counts_per_revolution`; kV becomes V/RPM and deceleration RPM/s. Otherwise units remain ticks/s, V/(ticks/s), and ticks/s². Acquired encoder values remain unchanged in the raw data.

Measured breakaway voltage is distinct from fitted running kS. kV includes back-EMF and speed-dependent losses. Coast-down also depends on inertia; current alone does not establish friction torque. Acceleration samples are excluded from steady fits, and aborted datasets are analyzed only where valid phase data exists.

The gradual voltage excitation and voltage/motion logging are informed by [WPILib identification routines](https://docs.wpilib.org/en/stable/docs/software/advanced-controls/system-identification/creating-routine.html). The steady voltage model follows the zero-acceleration case of [DC motor feedforward](https://frcdocs.wpi.edu/en/stable/docs/software/advanced-controls/introduction/introduction-to-feedforward.html). Interpretation of motor losses and coast-down is informed by [maxon’s motor model](https://support.maxongroup.com/hc/en-us/articles/360013761160-Motor-data-and-simulation) and [MathWorks’ motor characterization model](https://www.mathworks.com/company/technical-articles/creating-a-high-fidelity-model-of-an-electric-motor-for-control-system-design-and-verification.html). The numeric thresholds above are this procedure’s versioned engineering defaults, not universal values from those sources.

## Reusable interfaces

- `SelectableOpMode.Tool`: advertise benchmark definitions, receive commands, initialize hardware, update on the loop, abort, and handle receipt acknowledgement. Commands are dispatched to the selected tool after instance/TTL checks.
- `debug/Benchmark<S>`: one bounded execution with validated constructor inputs, loop ticks, keepalive, abort, phase/outcome, and completed dataset. `FrictionBenchmark` is hardware-independent; `FrictionTool` connects it to FTC hardware and TCP.
- `debugger_analysis.py`: versioned analyzer functions take saved decoded samples plus header/settings and return JSON-safe structured reports. `ANALYZERS` selects the implementation by benchmark ID. Add future schemas/analyzers explicitly rather than guessing their semantics.
- `frontend/src/debugger/Report.tsx`: shared report types, metric/table rendering, plots, and downloads. The Oracle frontend imports this component directly. Both applications consume the same stored `analysis.json`.

Register future mechanisms with their stable ID, display name, explicit hardware mapping and limits; attach tools to their mechanism folder. Future benchmarks add a definition/version, expected typed channels, execution implementation, analyzer, and report sections as needed. v1’s run inputs and schema validation deliberately describe the friction procedure.

## TCP protocol and durable bundles

Transport remains the existing four-byte big-endian length prefix and Protobuf `Envelope`. No JSON is embedded inside TCP. New envelope fields 30–34 are additive; existing field numbers are preserved. Both peers advertise `debug-runs-v1` (`Hello.capabilities`, `HelloAck.capabilities`).

| Message | Purpose |
| --- | --- |
| `DebugToolReady.benchmarks` | ID/version, typed inputs/channels, compatible analyzer IDs, direction availability and maximum voltage. |
| `DebugCommandRequest` | `benchmark.run`, `benchmark.abort`, `benchmark.keepalive`; run UUID is the request ID, with selected node and tool instance. |
| `DebugRunStatus` | Lightweight phase/repetition/direction/progress at 5 Hz. |
| `DebugRunHeader` | Run/mechanism identity, definition, inputs, calibration, robot start time and registered limits. |
| `DebugRunChunk` | Numbered chunks of at most 128 samples, each with its own robot timestamp and sequence number. |
| `DebugRunEnd` | Exact chunk/sample totals, execution outcome and explanation. |
| `DebugRunAck` | Durable receipt of a run and its sample count. |

Samples stay on the robot during execution. The completed dataset uses a separate bounded outgoing queue whose frames cannot be evicted by live telemetry. The robot retains data until ACK, restarting transfer after reconnect or an ACK timeout. The same run ID is never re-actuated by a retry. The laptop deduplicates chunks by run ID/index and rejects conflicting duplicates or invalid sequences/timestamps. An incomplete transfer gets no ACK or completed analysis.

Each UUID folder under the existing recordings root contains:

```text
<run-uuid>/
  manifest.json             # kind=debugger; procedure, inputs, units, outcome, acquisition
  analysis.json             # versioned metrics, tables, plot data and limitations
  raw/stream-00000.ftclog   # exact accepted Protobuf frame bytes plus receipt timestamps
  storage.json             # present when an uploaded laptop copy is retained
```

Raw frames are not reconstructed from JSON. Interrupted raw files are preserved on recovery; analysis uses validated chunks and acquisition timestamps rather than arrival times. Incomplete manifests remain visible after laptop restart. Small UUID/count receipts outside result folders prevent lost ACKs from recreating discarded data. Report publication and acknowledgement happen after raw finalization and atomic JSON writes. Benchmark completion does not finalize the surrounding TCP session or wait for camera recording.

## APIs

Laptop: `POST /api/debug/runs`, `POST /api/debug/runs/{id}/keepalive`, `POST /api/debug/runs/{id}/abort`, `GET /api/debug/runs`, `POST /api/debug/runs/{id}/upload` with `keep_local`, and `DELETE /api/debug/runs/{id}`. Existing `/api/debug/select` and `/api/debug/launch` remain in use. `/ws/debug` carries run progress alongside existing debugger messages.

Both laptop and hosted library expose read-only `GET /api/debug/results`, `/{id}`, `/{id}/report` and `/{id}/raw` under that prefix. Raw download is a ZIP of manifest and exact logs; report download is `analysis.json`. The Oracle **Debugger Results** tab filters by mechanism and benchmark, while ordinary recordings stay in **Recordings**. Oracle serves saved calculations without running an analyzer.

## Generate, validate, deploy

Generate bindings with protoc **3.25.5** (version output `libprotoc 25.5`), available from the official protobuf Maven artifact. From the repository root:

```powershell
.\protocol\generate.ps1 -Protoc 'C:\path\to\protoc.exe'
.\.venv-web\Scripts\python.exe -B -m unittest web_driver_station.backend.test_debugger_results
Push-Location web_driver_station/frontend
npm run build
Pop-Location
Push-Location recording_viewer/frontend
npm run build
Pop-Location
Push-Location FtcRobotController
.\gradlew.bat :TeamCode:assembleDebug --no-daemon
Pop-Location
```

The pure Java harness is `TeamCode/src/test/java/org/firstinspires/ftc/teamcode/debug/FrictionBenchmarkHarness.java`; compile it together with `Benchmark.java` and `FrictionBenchmark.java` using `javac -d <output>`, then run `org.firstinspires.ftc.teamcode.debug.FrictionBenchmarkHarness`. It requires no Android device. On this Windows host, Java’s Unix-domain loopback path needed a process-local fallback for Gradle: `JAVA_TOOL_OPTIONS=-Djdk.net.unixdomain.tmpdir=Z:/ftc-unavailable-unix-sockets`, with JBR 21. This is an environment workaround, not an Android code requirement.

Deploy the built viewer using the existing ignored SFTP configuration:

```powershell
.\.venv-web\Scripts\python.exe -B recording_viewer/deploy/update_debugger.py
```

The update stages an allowlist of application files under `/opt/ftc-recording-viewer/.deploy-stage/<timestamp>`, checks backend imports/routes, backs up replaced files under `.deploy-backups/<timestamp>`, publishes files, restarts `ftc-recording-viewer`, and checks the result API. It preserves the recordings root and existing Basic Auth credentials. Old hashed frontend assets remain available to open browser tabs. To roll back, copy backed-up files onto their corresponding application paths and restart the service.

The deployment also installs the Certbot deploy hook `reload_nginx_after_renewal.sh`. A renewed certificate was present on disk but Nginx still served its expired predecessor; the hook validates Nginx configuration and reloads it after future renewals. Do not rerun the original HTTPS bootstrap merely to renew a certificate: that bootstrap generates a new login password.

## Delivery validation

Software checks on 2026-09-07: 51 Python backend/viewer tests passed, six fake motor/clock scenarios passed, Android `assembleDebug` passed, and both frontend production builds passed. Browser checks exercised selection, disabled controls during execution, phase/repetition/Abort display, shared report rendering, the three storage choices, and failed-upload retention. Synthetic coefficients and coast time were recovered; TCP interruption/retry produced one independent bundle without a camera dependency or generic recording popup.

Oracle deployed successfully; all 30 existing ordinary recordings remained listed. Physical acceptance is still required after configuring the actual current cutoff: verify encoder sign/calibration, battery/current readings, bounded output, all repetitions, FLOAT coast behavior, abort/connection-loss shutdown, and upload/viewing of a real benchmark. No real motor was actuated during software validation.
