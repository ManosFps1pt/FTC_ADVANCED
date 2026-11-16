# Public-release candidate

The source migration targets `io.github.manosfps1pt` version `1.0.0-rc.1`.
This is a locally staged candidate, not a published or hardware-qualified release.
Desktop installation and dependency packaging are a separate project.

## Ownership and migration

ftclogger owns the robot-side protocol, telemetry, local recorder, and reusable
debugger. FTC_ADVANCED owns laptop ingestion, storage, analysis, and the browser.
BIOBUZZ is an acceptance consumer. Do not copy the library's Java sources into
TeamCode or apply the old companion patches.

| Consumer | Artifact | Tested dependency target |
| --- | --- | --- |
| SDK-only telemetry | `ftclogger` | FTC SDK 11.2.1 |
| SDK debugger | `ftclogger-debugger` | FTC SDK 11.2.1 |
| NextFTC integration | `ftclogger-nextftc` | NextFTC hardware 0.3.0 |
| BIOBUZZ | `ftclogger-nextftc` | FTC SDK 12.0.0, NextFTC hardware 0.3.0 |

These are build targets, not a claim of physical compatibility. Core has no
NextFTC dependency. Pedro/custom localizers use suppliers.

Build and stage all artifacts from the sibling ftclogger checkout with JDK 17:

```powershell
.\gradlew.bat testReleaseUnitTest lintRelease publishReleasePublicationToBuildRepository
```

Then build each consumer with an absolute repository path:

```powershell
.\gradlew.bat :TeamCode:assembleDebug "-PftcloggerRepository=C:/path/to/ftclogger/build/repository"
```

Run FTC_ADVANCED's command from `FtcRobotController`; run BIOBUZZ's from its root.
The normal hosted Maven coordinates will work only after the candidate is actually
published. Do not silently fall back to an older logger when resolution fails.

Replace old `org.firstinspires.ftc.teamcode.data/debug` imports with
`io.github.manosfps1pt.ftclogger` and `.debug`. Use `NextBindings.addMotor` for
NextFTC motors. Configure identity, transport, sampling, and storage before
`start()` in INIT. Reset runtime and call `markStarted()` at START. Capture with
`publishLoop()` after robot updates. Always close in `stop()` or `finally`.
A closed client cannot restart: construct a new client and subsystems per run.

OFF is the default on first installation. `DataTransportSetup` selects OFF,
PIT_TCP, or LOCAL_MATCH for the next session. Supplying a `LoggerConfig` overrides
the saved selection. Missing voltage/current stays unavailable; do not substitute
zero. Typed `SignalChannel` suppliers isolate measurement exceptions. Registration
errors fail before worker startup.

## Control contract

1. Start the debugger OpMode with the official Driver Station.
2. Select one robot session and a team-registered tool in the browser.
3. Acquire browser control.
4. Press both bumpers + A on a physical gamepad to arm that tool locally.

Browser renewal runs every 100 ms; its lease expires after 500 ms. Robot authority
also expires after 500 ms without renewal. Blur, disconnect, STOP, tool change, or
reconnect requires reacquisition/rearming. The backend rejects stale or v2 control
requests; the robot validates connection/session, lease, tool instance, request,
and registered limits. A TCP connection alone does not grant authority.

Manual controls use limits advertised by the robot. A feedforward tool must name
an allowed physical motor and limits during team registration. The old unrestricted
hub/port registration is disabled. Commanded, estimated, and measured signals are
distinct. RGB mappings and field coordinate conventions are explicit metadata.

These are software checks executed on OpMode loops. They cannot guarantee motor
shutdown while the robot loop/SDK/hardware is stalled. Measure that behavior in
the hardware acceptance tests below.

## Recording and recovery

Protocol v3 preserves acquisition time on every sample. v2 files remain readable;
their original batch timestamps cannot be reconstructed. `protocol/robot_data.proto`
and Python bindings are generated release artifacts from ftclogger's canonical
schema. Regenerate with `protocol/sync.py` and protoc 3.25.5; commit the schema hash
and generated Python output together. The protobuf namespace remains `v2` for
wire compatibility; the envelope version is 3.

The v1 `.ftclog` header is 36 bytes. Readers accept the exact historical Java
36-byte layout with the erroneous declared size 32, verify record CRCs, and do
not rewrite source files. Truncated tails remain recoverable. `.partial` files
must not be described as complete recordings.

Robot local storage defaults to 16 recordings, 256 MiB per recording. Laptop raw
telemetry defaults to 256 sessions / 4 GiB and refuses further writes at quota;
retrieve/delete old recordings explicitly. Benchmark results are persisted on
the robot before transfer and retained until the laptop acknowledges durable
storage. Interrupted transfers may be retried without rerunning the mechanism.
Video and other derived artifacts still need an aggregate disk-budget policy.
An unexpected laptop disconnect keeps the raw file as `.partial`; partial files
can be retrieved and replayed without relabeling the session complete. The shared
camera follows the selected session. A second robot's packets cannot rebind it.

## Verification record — 2026-10-28

| Check | Result |
| --- | --- |
| Library core/debugger/NextFTC unit tests | 36 passed |
| Library Android lint | Passed; dependency/tooling warnings remain |
| SDK-only staged-artifact examples | Built, including iterative and linear/local recording |
| Optional NextFTC staged-artifact examples | Built |
| FTC_ADVANCED TeamCode debug APK | Built against the same staged rc.1 artifacts |
| BIOBUZZ TeamCode debug APK and unit tests | Built; 18 tests passed on its current Gradle 9.6.0 toolchain |
| Backend regression suite | 76 passed |
| Python control/configuration/replay/MCP tests | 12 passed |
| Frontend production build | Passed with pinned dependencies |
| Mocked Chrome ownership/arming workflow | Passed: explicit selection, observation-only default, advertised limits, renewal, blur, and expiry |

Backend tests additionally cover stale heartbeats, lease expiry, per-sample times,
Java recorder interoperability, quota/partial-file behavior, slow browsers,
fragmented v3 frames, stale sequences, changed session identities, and camera
isolation. Browser tests intercept robot APIs; they are not hardware tests.
Remote CI definitions are checked in but have not been run on a pushed revision.
The repository-required Graphify refresh could not run because `graphify` is not
installed in the environment.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Logger OFF | Saved transport selection or explicit `LoggerConfig` |
| Discovering/degraded | Laptop listener, UDP 5811/TCP 5810 reachability, status diagnostics |
| Connected but observation only | v3 session selected, browser acquired, official DS START, fresh local arm |
| Controls vanish after changing tab | Expected blur revocation; reacquire and rearm |
| No watts/current | Sensor unavailable or voltage was not registered |
| Local recording failed | Storage quota, free disk, previous partial file, `getLastError()` |
| Build cannot resolve rc.1 | Stage all three artifacts and pass `ftcloggerRepository` |
| Windows Gradle daemon cannot open socket | Use a short `-Djdk.net.unixdomain.tmpdir` directory |

## Release gates still requiring evidence

Do not publish this as a finished release until the following are recorded against
the exact library, app, and consumer revisions:

- Real hardware: DS STOP, link loss, current cutoff, travel/duration limits,
  loop stalls, STOP during INIT, partial initialization, repeated OpModes.
- Control Hub profiling: OFF/ON loop p50/p95/p99, allocations, hardware reads,
  queue age, retained memory, and extended recording soak. The current 50 Hz
  default is provisional until those measurements exist.
- Fault injection: full disks/process termination, interrupted result transfers,
  delayed/duplicate acknowledgements, and recovery after Robot Controller restart.
- Close remaining software gaps: aggregate disk quota for non-raw artifacts,
  allocation reduction, and generic
  non-motor debugger telemetry. Review drop accounting on network write failure.
- Publish immutable library artifacts/schema, then run both consumers against
  those hosted coordinates. Local staging proves packaging, not hosted availability.

CI verifies Python regressions, frozen frontend installation/build, and schema
integrity. The library workflow tests/lints all modules and compiles SDK-only and
NextFTC consumers from the staged Maven repository. Cross-repository release
consumer verification takes explicit pushed revisions; uncommitted checkouts are
not represented by a remote CI run.
