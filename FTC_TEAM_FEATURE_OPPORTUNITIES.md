# FTC Advanced: Feature Opportunities for an FTC Team

_Research and product-opportunity report, 4 September 2026_

## Executive recommendation

The strongest direction for FTC Advanced is not “more dashboard.” It is a **Robot Reliability Copilot**: a system that turns every practice run into evidence about whether the robot is becoming more dependable.

FTC teams rarely lose only because they lack one more graph. They lose because a battery sagged, a connector moved, a configuration changed, an autonomous routine became inconsistent, a driver could not reproduce a good cycle, or a failure happened once and nobody could reconstruct it. FTC Advanced already owns an unusually valuable combination of inputs: Driver Station state, structured high-rate telemetry, gamepad frames, guarded debug commands, camera footage, persistent recordings, and replay. That combination can answer a much more useful question than “what is the value right now?”:

> **What changed, what is becoming unreliable, and what should the team check before the next run?**

The first version need not contain machine learning. It can correlate known signals and produce an evidence-backed run debrief:

- exact code/build, robot configuration, OpMode, battery, camera, and controller identity;
- brownout-like voltage drops, current spikes, loop overruns, sample gaps, disconnects, watchdog stops, and exceptions;
- a one-tap incident marker that preserves the surrounding video, inputs, telemetry, and Robot Controller logs;
- comparison with the robot's own healthy baseline;
- a short checklist such as “battery B-04 sagged 1.6 V more than its median,” “left-front current rose while velocity fell,” or “the active configuration differs from the last successful autonomous run.”

This is a better fit than building a generic team-management or scouting suite from scratch. It is distinctive, it compounds the value of data the app already collects, and it attacks the reliability gap that separates a robot which works once from a robot a drive team can trust.

## What the current codebase already does

This review treats the current working tree as the product baseline. Design documents are useful context, but they are not counted as shipped behavior.

| Area | Present in the working implementation | Evidence |
| --- | --- | --- |
| Driver Station link | Connect/disconnect, robot status, ping, OpMode list, init/start/stop, physical and virtual gamepad state, switching and clearing gamepads | [`web_driver_station/backend/main.py`](web_driver_station/backend/main.py), [`web_driver_station/frontend/src/App.tsx`](web_driver_station/frontend/src/App.tsx) |
| Robot configuration | Reads saved configurations, parses hub/device topology, validates names and I2C metadata, saves and activates XML while the robot is stopped | [`web_driver_station/frontend/src/RobotConfiguration.tsx`](web_driver_station/frontend/src/RobotConfiguration.tsx), [`CONFIGURATION_XML.md`](CONFIGURATION_XML.md) |
| Structured robot data | Versioned protobuf envelopes, session/connection identity, device and channel schemas, typed snapshots, events, gamepads, gaps, heartbeats, and session end | [`protocol/robot_data.proto`](protocol/robot_data.proto), [`web_driver_station/backend/telemetry_store.py`](web_driver_station/backend/telemetry_store.py) |
| Live analysis | Catalog-driven traces, an instant recent-history timeline, per-motor position/velocity/current/power values, loop-time indication, gamepad visualization, and robot-advertised typed commands | [`web_driver_station/frontend/src/TelemetryDashboard.tsx`](web_driver_station/frontend/src/TelemetryDashboard.tsx) |
| Guarded debugging | Robot-advertised tool tree, typed parameters and commands, tool instances, risk classes, output bounds, TTL/watchdog behavior, hold-to-run motor control, and stop confirmation | [`web_driver_station/frontend/src/MotorLab.tsx`](web_driver_station/frontend/src/MotorLab.tsx), [`DEBUGGER_OPMODE_PROTOCOL_SPEC.md`](DEBUGGER_OPMODE_PROTOCOL_SPEC.md) |
| Recording and camera | Crash-recoverable `.ftclog` capture, direct scrcpy or native-phone video, finalization, local retention decisions, SFTP upload, and progress/error state | [`web_driver_station/backend/ftclog.py`](web_driver_station/backend/ftclog.py), [`web_driver_station/backend/camera_recording.py`](web_driver_station/backend/camera_recording.py), [`web_driver_station/backend/recording_uploader.py`](web_driver_station/backend/recording_uploader.py) |
| Replay | Recording library, signal traces, scrubber-controlled gamepad state, value inspection, and basic video selection/playback | [`recording_viewer/backend/main.py`](recording_viewer/backend/main.py), [`recording_viewer/frontend/src/App.tsx`](recording_viewer/frontend/src/App.tsx) |
| Assistant integration | Six bounded, read-only MCP tools for live Driver Station, TCP, telemetry, catalog, snapshot, and debugger status | [`mcp_server/server.py`](mcp_server/server.py), [`mcp_server/README.md`](mcp_server/README.md) |

### Important distinction: described is not implemented

The repository's product documents describe a richer future shell: a derived workspace mode, capability-aware Home page, attention queue, mechanism plugin workbench, complete recording library, synchronized replay, two-run comparison, annotations, Oracle analysis, and broader analysis SDK. Those are valuable intentions, but the current React application still exposes four principal routes—Driver Station, configuration, Telemetry Lab, and debugger—and the replay application remains separate and deliberately read-only. See [`STRUCTURE.md`](STRUCTURE.md), [`DESIGN.md`](DESIGN.md), [`APP_MENTAL_MODEL.md`](APP_MENTAL_MODEL.md), and [`ROBOT_DATA_PLATFORM_PLAN.md`](ROBOT_DATA_PLATFORM_PLAN.md).

That distinction matters when prioritizing ideas. A feature that appears in a design document should be treated as architectural follow-through, not as an existing capability.

## Competition-use boundary

FTC Advanced should label features by where they are intended to be used.

- **Practice / pit (P):** laptop-connected development, maintenance, calibration, inspection preparation, and post-match repair work.
- **Stands / strategy (S):** scouting or event information on devices that do not communicate with the robot or operator console.
- **Post-run / remote (R):** replay, reports, mentor review, and engineering documentation.
- **Match candidate (M):** potentially useful during a match only if delivered through the season's permitted Robot Controller/Driver Station software and hardware. This label is not a legality guarantee.

The archived 2025–26 competition manual required a specified Android Driver Station device, official Robot Controller/Driver Station communication, and prohibited other wireless communication to or from the operator console during a match. FTC Advanced's laptop web Driver Station and secondary TCP link should therefore be treated as **practice tooling unless the current season's rules and event officials explicitly establish otherwise**. Re-check the current manual before every event; do not infer legality from technical feasibility. Sources: [2025–26 Competition Manual, especially sections 12.7 and 12.9](https://ftc-resources.firstinspires.org/ftc/archive/2026/game/cm-html/DECODE_Competition_Manual_TU32.htm) and the [official inspection checklist](https://ftc-resources.firstinspires.org/ftc/event/inspection-check).

## Ranked opportunity map

Scores use a 1–5 scale. “Fit” means leverage of this repository's existing capabilities. Effort is intentionally coarse because this is an opportunity report, not an implementation estimate.

| Rank | Opportunity | Impact | Fit | Distinctiveness | Effort | Why it belongs near the top |
| ---: | --- | :---: | :---: | :---: | :---: | --- |
| 1 | Robot Reliability Copilot | 5 | 5 | 5 | L | Converts the app's telemetry, video, events, configuration, and logs into preventive checks and actionable diagnoses rather than isolated graphs. |
| 2 | Autonomous Repeatability Lab | 5 | 5 | 4 | M | Measures success over many runs, exposes drift and condition sensitivity, and gives teams evidence that an auto is competition-ready. |
| 3 | Run identity and change provenance | 5 | 5 | 4 | M | Makes every graph trustworthy by recording exactly which code, constants, hardware configuration, battery, and robot state produced it. |
| 4 | One-tap incident capture and evidence bundle | 5 | 5 | 4 | M | Preserves the few seconds around a failure and correlates RC/DS logs with video, inputs, gaps, and telemetry before context is lost. |
| 5 | Battery Passport and load-sag tracker | 5 | 4 | 4 | M | Replaces tape labels and memory with per-battery evidence, helping avoid weak batteries and voltage-sensitive autonomous behavior. |
| 6 | Driver Practice Coach | 5 | 5 | 5 | L | Uses synchronized inputs, robot state, pose, and video to identify slow cycles, hesitation, saturation, and inconsistent execution. |
| 7 | Baseline-versus-candidate replay | 4 | 5 | 4 | M | Turns recordings into an engineering comparison tool and makes regressions visible after software or mechanical changes. |
| 8 | Guided pit preflight and self-inspection | 5 | 4 | 3 | M | Combines official inspection items with checks the app can verify, reducing preventable event-day failures. |
| 9 | Collaborative replay annotations and debrief | 4 | 5 | 4 | S–M | Lets drivers, programmers, and builders create one shared explanation of what happened at an exact time in a run. |
| 10 | Vision and AprilTag calibration wizard | 4 | 4 | 4 | M | Replaces a multi-tool, error-prone workflow with guided capture, calibration-quality feedback, and versioned intrinsics. |
| 11 | Alliance compatibility and autonomous collision planner | 4 | 3 | 5 | M | Helps teams choose compatible start positions/routines and communicate a concrete pre-match plan instead of relying on memory. |
| 12 | Experiment notebook and portfolio evidence pipeline | 4 | 4 | 5 | M | Automatically turns hypotheses, changes, runs, plots, and conclusions into credible engineering-process evidence. |
| 13 | 3D maintenance and failure history | 3 | 4 | 5 | L | Uses the existing robot model to make recurring physical failures spatial and visible to the whole pit crew. |
| 14 | Role-aware Home and attention queue | 4 | 5 | 3 | M | Executes the existing design direction and prevents drivers, programmers, and pit crew from hunting through unrelated panels. |

### Why the first four reinforce each other

Run provenance gives data meaning. Incident capture preserves evidence. Repeatability testing creates a healthy baseline. The Reliability Copilot compares new evidence with that baseline and explains what deserves attention. Building these as one data model avoids four disconnected features.

## Full feature catalog

Every idea below is deliberately framed as a team problem and experience, not merely a technology. “Layer” identifies the main work: **B** backend, **U** UI/UX, or **B+U** combined.

### A. Reliability, inspection, and pit readiness

| # | Feature and problem solved | Proposed experience | Layer / leverage | Benefit | Context | Important limitation |
| ---: | --- | --- | --- | --- | --- | --- |
| 1 | **Robot Reliability Copilot.** Failures are visible in separate graphs but the team does not know what matters. | After each run, show a short evidence-backed health summary, detected anomalies, likely checks, confidence, and links to the exact moments. | B+U; recordings, events, gaps, current, voltage, video, MCP | Faster diagnosis and fewer repeat failures. | P/R | Begin with deterministic rules; never present correlation as a proven root cause. |
| 2 | **One-tap incident marker.** A driver feels something wrong but the team later searches an entire recording. | A large practice-only “Mark incident” action captures a pre/post window, optional voice note, current state, video frame, and relevant logs. | B+U; session clock, video, telemetry, gamepads | Preserves context while the observation is fresh. | P/R | Clock alignment and log retrieval must be reliable; the marker itself must not distract a match driver. |
| 3 | **Run identity and build provenance.** Teams compare runs produced by unknown code or constants. | Attach Git commit/dirty flag, APK/build fingerprint, protocol version, active configuration hash, OpMode, tuning profile, and hardware fingerprint to every session. | B; hello/session metadata and recorder | Reproducible testing and defensible comparisons. | P/R | Avoid storing secrets or student identity; not every build source will be Git. |
| 4 | **Configuration drift alarm.** A renamed device, swapped port, or forgotten configuration causes an exception or silent behavior change. | Compare the active hardware map and discovered devices with the last approved baseline; explain exact additions, removals, renames, and address changes before Init. | B+U; configuration XML, device catalog, DS connection | Catches setup mistakes before motion. | P | The app cannot electrically verify every declared device and must distinguish “not detected” from “not supported.” |
| 5 | **Battery Passport.** Batteries are selected by tape label and open-circuit voltage, which misses load performance. | Scan or choose a battery ID; track charge/use history, minimum voltage, sag under known loads, estimated internal resistance, and retirement warning. | B+U; battery voltage, motor current, sessions | More consistent autonomous behavior and fewer brownouts. | P | Measurements are comparative, not a certified battery safety test; QR/NFC identity requires team discipline. |
| 6 | **Guided self-inspection.** Official checklists are long and some items are machine-verifiable while others require a person. | Present the current-season checklist with automatic evidence for versions, naming, battery, connectivity, and configuration plus photo/manual sign-offs for physical items. | B+U; status, configuration, Android/ADB inspection | Faster event inspection preparation and fewer omissions. | P | Must link to and version the official checklist; never claim official inspection approval. |
| 7 | **Brownout/ESD/disconnect classifier.** A disconnect has several plausible causes and teams often guess “Wi-Fi.” | Correlate voltage/current, USB events, ping, TCP gaps, RC logs, impact markers, and recovery timing; show evidence for each plausible category. | B+U; logs, telemetry, DS/TCP state | Better corrective action and evidence for technical volunteers. | P/R | Cannot prove radio interference without appropriate RF evidence. FIRST lists low battery, wiring, ESD, impacts, brownout, and interference as different possibilities. |
| 8 | **Control-system version matrix.** Mismatched apps, firmware, OS, or stale hubs are discovered at the event. | Show RC/DS SDK, Control/Expansion Hub firmware, Driver Hub OS, configuration compatibility, “known/unknown,” and a versioned readiness snapshot. | B+U; DS/ADB/manage-page data | Makes upgrades deliberate and inspection preparation repeatable. | P | Do not auto-update during a critical period; current recommended versions are season-dependent. |
| 9 | **Pit turnaround board.** Repairs expand until nobody knows whether the robot will reach queue. | Start a repair card from an incident, assign owner/checks, show next match/queue deadline, require a minimal functional test, and surface blockers. | B+U; incidents, event schedule, test tools | Coordinates a stressed pit crew around the next deadline. | P/S | A full task manager would be scope creep; keep this robot-incident-specific. |
| 10 | **Spare-part and connector history.** Repeat failures occur because the same suspect component is moved around the robot. | Give motors, servos, cables, hubs, cameras, and batteries asset IDs; record installation/removal, port, reason, and failure symptoms. | B+U; device catalog and configuration | Identifies bad actors and supports preventive replacement. | P/R | Manual asset identity is work; start only with high-value parts. |

Official motivation: FIRST encourages self-inspection and exposes version, battery, naming, password, Wi-Fi, and app checks in the [FTC Self Inspect screens](https://ftc-docs.firstinspires.org/en/latest/hardware_and_software_configuration/self_inspect/new-self-inspect.html). FIRST also documents [event disconnect causes](https://ftc-docs.firstinspires.org/en/latest/control_system_troubleshooting/troubleshooting_wireless_at_events/troubleshooting-wireless-at-events.html), [ESD risks including the Control Hub USB 2.0 issue](https://ftc-docs.firstinspires.org/en/latest/hardware_and_software_configuration/configuring/managing_esd/managing-esd.html), and [wiring/strain-relief practices](https://ftc-docs.firstinspires.org/en/latest/robot_building/wiring_guide/wiring-guide.html).

### B. Autonomous, localization, and regression testing

| # | Feature and problem solved | Proposed experience | Layer / leverage | Benefit | Context | Important limitation |
| ---: | --- | --- | --- | --- | --- | --- |
| 11 | **Autonomous Repeatability Lab.** One successful run is mistaken for readiness. | Define a test matrix, run the auto repeatedly, mark success criteria, and show success rate, endpoint spread, path deviation, duration distribution, and failure clusters. | B+U; OpMode lifecycle, pose channels, recording | Makes reliability measurable rather than anecdotal. | P/R | Success detection needs season/team-defined criteria; do not hide small sample sizes. |
| 12 | **Baseline-versus-candidate replay.** Mechanical or code changes are judged by memory. | Overlay two compatible sessions: pose/path, targets, errors, currents, input, events, and synchronized video; explain incompatible schemas. | B+U; replay and schema metadata | Quickly reveals regressions and improvements. | R | Clock, coordinate frame, and signal semantics must match before overlay. |
| 13 | **Ghost robot field view.** Numeric traces hide where an autonomous diverged. | Animate candidate and baseline robot footprints together on a calibrated field, with uncertainty and collision envelopes. | U+B; Pose2D, replay, existing 3D assets | Makes path and clearance mistakes obvious to non-programmers. | P/R | Requires correct robot dimensions, field coordinates, and localization confidence. |
| 14 | **Starting-condition robustness sweeps.** Autos are tested from one perfect pose and fail after human placement variation. | Prompt controlled offsets in X/Y/heading, battery band, game-element state, and lighting; visualize the region in which the routine succeeds. | B+U; test plans and recording metadata | Produces an honest operating envelope. | P | Physical setup remains manual unless a field measurement system is added. |
| 15 | **Sensor dropout/fault-injection practice.** Recovery paths are rarely tested until a real failure. | In a dedicated test OpMode, simulate stale vision, frozen encoder, noisy distance sensor, low voltage, delayed loop, or rejected command and verify safe behavior. | B+U; typed debug protocol and events | Builds graceful degradation and programmer confidence. | P | Must be isolated from normal OpModes, visibly armed, bounded, and impossible to activate accidentally. |
| 16 | **Odometry drift profiler.** Teams know the endpoint is wrong but not whether error comes from scale, heading, slip, or geometry. | Run guided straight, strafe, turn, square, and return tests; fit error patterns and recommend which parameter class to investigate. | B+U; pose, encoders, IMU, guided tools | Shortens localization diagnosis. | P | Recommendations depend on drivetrain/localizer model and should not blindly write constants. |
| 17 | **Vision/AprilTag calibration wizard.** Capturing images, moving files, calculating intrinsics, and matching resolutions is fragmented. | Guide image capture, verify coverage/sharpness, run calibration, report reprojection error, store intrinsics by camera serial and resolution, and export FTC-ready values. | B+U; camera/ADB pipeline and configuration | Improves pose accuracy and makes calibration repeatable. | P | Calibration is specific to camera/lens/resolution; physical camera movement can invalidate it. |
| 18 | **Field-origin and camera-extrinsics assistant.** Good intrinsics still yield bad global pose when mounting offsets or axes are wrong. | Show a known tag/field pose, let the team validate robot/camera frames, estimate mount transform from samples, and visualize residual error. | B+U; AprilTag pose and field view | Prevents sign, axis, and offset mistakes. | P | Needs multiple well-measured observations and cannot compensate for a flexible camera mount. |
| 19 | **Autonomous coverage and branch explorer.** Some sensor/game branches are never exercised. | Instrument state-machine transitions and produce a coverage map of visited states, branch outcomes, timeouts, and untested paths across runs. | B+U; event stream and session comparison | Reveals brittle, dead, or untested autonomous logic. | P/R | Requires explicit state/branch instrumentation; code coverage on Android alone would not express physical outcomes. |

FIRST notes that accurate AprilTag pose depends on calibration for the exact camera and resolution; see [AprilTag camera calibration](https://ftc-docs.firstinspires.org/en/latest/apriltag/vision_portal/apriltag_camera_calibration/apriltag-camera-calibration.html), [camera calibration methods](https://ftc-docs.firstinspires.org/programming_resources/vision/camera_calibration/camera-calibration.html), and [AprilTag localization](https://ftc-docs.firstinspires.org/en/latest/apriltag/vision_portal/apriltag_localization/apriltag-localization.html). Road Runner's documentation also emphasizes an ordered, repeatable tuning procedure and retuning after significant robot changes: [Road Runner tuning guide](https://rr.brott.dev/docs/v0-5/quickstart/tuning/).

### C. Driver practice and coaching

| # | Feature and problem solved | Proposed experience | Layer / leverage | Benefit | Context | Important limitation |
| ---: | --- | --- | --- | --- | --- | --- |
| 20 | **Automatic cycle segmentation.** Practice produces a final score but little explanation of where time went. | Detect or let teams define intake, transit, alignment, score, recovery, and endgame events; show cycle distributions and the video for the slowest segment. | B+U; events, pose, mechanisms, video | Focuses practice on the costly part of the cycle. | P/R | Generic inference will be unreliable without season/team-specific events. |
| 21 | **Driver Practice Coach.** Drivers receive broad advice rather than evidence. | Summarize hesitation, stick saturation, overcorrection, unnecessary reversals, mechanism waiting, path consistency, and improvement trend with linked clips. | B+U; gamepads, pose, targets, video | Makes coaching concrete and repeatable. | P/R | Metrics must support coaching, not rank or shame students; context matters. |
| 22 | **Ghost-run practice.** Drivers cannot feel where they lost time against their best clean run. | During replay, compare position and cycle phase with a selected personal best; in practice, optionally provide a non-distracting ahead/behind indicator. | B+U; comparisons and live telemetry | Creates a clear, motivating target. | P/R | Live feedback can distract; default to post-run review. |
| 23 | **Structured drill library.** Teams “drive around” without deliberate practice. | Define drills such as alignment repetitions, intake recovery, endgame under time, defense escape, and controller handoff; track attempts and consistency. | B+U; OpMode control and session metrics | Converts limited robot time into targeted training. | P | Scoring and drill definitions must be editable each season. |
| 24 | **Controller ergonomics and dead-zone lab.** Drift, trigger mismatch, swapped modes, or uncomfortable mappings surface during a match. | Visualize raw inputs, drift/noise, response curves, button reach sequences, disconnects, and mapping; produce a preflight result per controller. | B+U; existing gamepad capture and overlay | Catches hardware/input issues and supports better mappings. | P | Official match gamepad and console rules still apply; calibration must not create an illegal modification. |
| 25 | **Pressure-mode simulator.** Quiet practice does not reproduce match timing, communication, or recovery stress. | Run a practice match clock with queue countdown, randomized field/setup faults, crowd noise option, penalties to recognize, and post-run communication review. | U+B; recording and drills | Trains routines and team communication, not just joystick skill. | P | Sounds must never mimic or distract at official matches; practice-only and clearly branded. |
| 26 | **Drive-team voice timeline.** Important callouts are lost when reviewing only video and gamepads. | Record an optional practice microphone track, transcribe it, and pin callouts to robot actions, delays, and errors. | B+U; synchronized video clock | Improves coach/driver communication and role clarity. | P/R | Requires consent, privacy controls, deletion, and awareness of venue recording policies. |
| 27 | **Consistency and fatigue view.** A best run hides degradation over a long practice block. | Plot cycle variability, reaction proxies, mistakes, disconnects, and mechanism faults across the session order; recommend a break rather than overfitting conclusions. | B+U; session library | Helps schedule effective practice and detect heat/wear effects. | P/R | These are performance proxies, not medical or psychological judgments. |

Community discussions repeatedly emphasize limited robot access, insufficient driver practice, and the value of repeated full runs. These are observations, not official findings; examples include [FTC driver-training pain points](https://www.reddit.com/r/FTC/comments/1mpgpec/what_problems_have_you_had_with_ftc_driver/) and [a community discussion of practice and reliability](https://www.reddit.com/r/FTC/comments/lu206o/does_anyone_have_the_problem_when_their_robot/). The second link may change or become unavailable because it is community-hosted.

### D. Event workflow, scouting, and alliance strategy

| # | Feature and problem solved | Proposed experience | Layer / leverage | Benefit | Context | Important limitation |
| ---: | --- | --- | --- | --- | --- | --- |
| 28 | **FTC Events schedule and queue clock.** The pit loses repair/practice time because match timing lives elsewhere. | Sync the selected event, cache schedule/results, show the team's next match, configurable queue lead time, likely turnaround, and stale-data age. | B+U; new official API adapter | Better pit prioritization and fewer rushed departures. | P/S | Event schedules slip; never present an estimate as an official field call. Offline caching is essential. |
| 29 | **Post-match debrief card.** Observations disappear before scouts, drivers, and pit crew compare notes. | Immediately after a match, ask three fast questions: what worked, what failed, what must change; attach them to the recording and official result later. | U+B; recording library and event data | Creates a shared, searchable operational history. | P/S/R | Data entry must take seconds or teams will skip it. |
| 30 | **Alliance compatibility matrix.** Raw team averages do not reveal whether two robots can coexist. | Compare scoring zones, preferred starts, autonomous paths, cycle roles, endgame needs, reliability, and communication preferences. | B+U; external scouting import and team-defined data | Supports better match plans and pick lists. | S/R | Scouting data is uncertain and game-specific; show sample count and confidence. |
| 31 | **Autonomous collision planner.** Partners describe paths verbally and discover conflicts on-field. | Drag both robot footprints/paths onto a field, add timing uncertainty, flag spatial/temporal conflicts, and export a one-screen alliance plan. | U+B; field view and path schemas | Faster, clearer pre-match coordination. | P/S | Partner path data may be approximate; the tool cannot guarantee collision avoidance. |
| 32 | **Strategy card generator.** Plans are scattered across chat, paper, and memory. | Produce a compact offline card with start positions, auto choices, role split, hand signals/callouts, endgame trigger, fallback, and partner contacts. | U; event/scouting data | Gives the drive team one shared plan. | S/M | During a match it must comply with current rules and avoid introducing prohibited electronics or communications. Paper export is safest. |
| 33 | **Scouting confidence and contradiction tracker.** Teams average inconsistent observations into false precision. | Store who/when/how each fact was observed, flag conflicting pit claims versus match evidence, and display confidence intervals/sample counts. | B+U; external data model | More honest strategy decisions. | S/R | Requires enough observations and careful UX; confidence is not certainty. |
| 34 | **Offline QR scouting interchange.** Venue internet is unreliable and team devices cannot synchronize. | Import/export signed or checksummed batches by QR/file, deduplicate observations, and preserve provenance. | B+U; optional scouting adapter | Resilient data sharing without venue Wi-Fi. | S | QR payload size and merge conflicts limit complexity; do not create robot/operator-console communication. |
| 35 | **Opponent tendency and traffic heatmap.** Averages hide where robots spend time and create congestion. | Aggregate manually tagged or video-derived field occupancy by match phase and show common routes, blocking zones, and uncertainty. | B+U; scouting/video analysis | Improves route and defense planning. | S/R | Computer vision from stands is noisy; manual correction and confidence are required. |

The official [FTC Events API](https://ftc-events.firstinspires.org/api-docs/index.html) exposes event listings, schedules, results, rankings, score details, teams, and awards, and supports cache freshness through HTTP modification headers. It is a strong integration point, but it does not replace team-observed scouting or provide in-progress match results. Existing scouting products already cover substantial ground: for example, [Cipher Scout](https://scout.vcs-robotics.com/) advertises offline-first match scouting, comparison, QR transfer, and configurable analytics. FTC Advanced should integrate or import before rebuilding that entire category.

### E. Engineering process and judging evidence

| # | Feature and problem solved | Proposed experience | Layer / leverage | Benefit | Context | Important limitation |
| ---: | --- | --- | --- | --- | --- | --- |
| 36 | **Experiment notebook.** Teams tune repeatedly but fail to record the hypothesis, change, and conclusion. | Before a run, capture question, changed variable, expected outcome, and success metric; after it, attach plots/clips and a conclusion. | B+U; sessions and comparisons | Better engineering decisions and less circular tuning. | P/R | Must remain faster than a paper note; automatic evidence should not fabricate reasoning. |
| 37 | **Portfolio evidence inbox.** Strong technical work is forgotten when the portfolio deadline arrives. | Let students promote a run, incident, decision, plot, photo, or comparison into a tagged evidence inbox mapped to current award criteria. | B+U; artifacts and annotations | Preserves authentic evidence throughout the season. | R | Criteria change; students must author the final narrative and protect PII. |
| 38 | **Decision/trade-off matrix builder.** Design choices are often remembered as conclusions without alternatives or tests. | Compare alternatives with team-chosen criteria, link each score to a test or observation, and show what evidence changed the decision. | U+B; experiment records | Encourages defensible engineering reasoning. | P/R | Numerical scores can create false rigor; explanations and evidence must remain primary. |
| 39 | **Requirement-to-test traceability.** Teams say a subsystem is “done” without defining what done means. | Define measurable robot requirements, link them to drills/tests, show last passing run and regression status, and distinguish untested from failed. | B+U; test plans and recordings | Makes readiness visible across subteams. | P/R | Keep the model lightweight; not every creative task needs formal verification. |
| 40 | **Automated technical appendix exporter.** Rebuilding plots and tables for documentation wastes time. | Export selected charts, run metadata, configuration diffs, and captions as print-ready images/Markdown/CSV with source-session links. | B+U; replay and reports | Speeds documentation without hiding the underlying data. | R | The portfolio has current page/file limits; export must not imply all evidence belongs in the final submission. |
| 41 | **Pit-interview story mode.** Students hunt through tools when demonstrating the engineering journey. | Create an offline, read-only sequence of problem → evidence → iteration → result, with large controls and cached media. | U; evidence inbox and replay | Helps students explain work coherently to judges and visitors. | P/R | Must remain student-led and work without internet; do not expose secrets or unsafe controls. |
| 42 | **Contribution and learning map.** Team leaders struggle to see whether knowledge is concentrated in one student. | Let members record skills learned, reviews performed, and systems they can explain; highlight bus-factor risks and cross-training opportunities. | B+U; team metadata | Supports succession and inclusive participation. | R | Avoid surveillance-style productivity scoring and minimize student personal data. |

The 2025–26 manual limited portfolios and described the Think Award as evidence of engineering process, lessons learned, trade-off/cost-benefit analysis, or mathematical analysis. The exact criteria can change, so integrations must be season-versioned. See [2025–26 Competition Manual, section 6](https://ftc-resources.firstinspires.org/ftc/archive/2026/game/cm-html/DECODE_Competition_Manual_TU32.htm) and the [official event/judging resources](https://ftc-resources.firstinspires.org/ftc/archive/2026/event).

### F. UI/UX, accessibility, and safe attention management

| # | Feature and problem solved | Proposed experience | Layer / leverage | Benefit | Context | Important limitation |
| ---: | --- | --- | --- | --- | --- | --- |
| 43 | **Role-aware Home.** The driver, pit lead, programmer, and scout need different next actions. | Choose or infer a temporary role and reorder the same authoritative state into “ready to run,” “needs repair,” “needs analysis,” or “next match” views. | U+B; planned workspace-state model | Reduces hunting and cognitive load. | P/S/R | Roles should change presentation, not bypass permissions or safety gates. |
| 44 | **Attention queue with consequence labels.** Warnings compete visually even when only one blocks the next action. | Sort issues by safety/control, recording loss, analysis limitation, or information; state the exact consequence and recovery action. | B+U; statuses and planned Home | Faster decisions under time pressure. | P | Requires one authoritative derived state; duplicate client-side interpretations will conflict. |
| 45 | **“Why unavailable?” capability explanations.** Disabled controls look broken. | Every unavailable action explains the missing authority, stale source, lifecycle state, hardware prerequisite, or safety condition and offers the next safe step. | U+B; capability gates and debug safety | Teaches students and reduces dangerous trial-and-error. | P | Explanations must come from backend facts, not guessed UI state. |
| 46 | **Novice/expert progressive disclosure.** New students are overwhelmed while experienced students need density. | Keep one state model but offer guided terminology, diagrams, and procedures in novice mode; shortcuts, compact tables, and raw detail in expert mode. | U | Makes the tool useful for teaching without slowing experts. | P/R | Do not hide warnings or safety consequences in either mode. |
| 47 | **Bilingual pit and checklist mode.** Mixed-language teams lose time translating faults and procedures. | Localize fixed UI, units, checklists, and recovery steps while preserving exact protocol/log terms alongside translations. | U; structured status codes | Improves accessibility and mentor/student collaboration. | P/R | Machine-translated safety text must be reviewed; raw technical identifiers must remain searchable. |
| 48 | **Glanceable pit kiosk.** The full workstation is too dense when several people need status from across the pit. | A read-only large-text view shows next match, robot readiness, battery, active repair, last test, recording upload, and blockers. | U+B; attention queue and event data | Creates shared situational awareness. | P/S | Must be read-only, offline-capable, and careful about displaying sensitive credentials or student data. |

### G. More speculative, out-of-the-box opportunities

| # | Feature and problem solved | Proposed experience | Layer / leverage | Benefit | Context | Important limitation |
| ---: | --- | --- | --- | --- | --- | --- |
| 49 | **3D maintenance and failure map.** Text logs do not show that several failures occur at one moving joint or cable route. | Pin assets, repairs, photos, wear observations, and incident frequency to the existing robot model; color by recency/severity. | B+U; `3d model/` assets, incidents, parts | Makes mechanical patterns understandable across subteams. | P/R | The model must track the real robot revision; spatial annotations are manual. |
| 50 | **Phone-microphone/vibration signature lab.** Bearings, gears, chains, and fans often sound wrong before telemetry clearly fails. | During a safe repeatable mechanism test, record audio/phone motion, compare spectra with a healthy baseline, and link anomalies to video/current. | B+U; camera phone and recordings | Cheap early-warning signal for mechanical wear. | P | Placement/noise dominate results; this is comparative screening, not a diagnosis. |
| 51 | **Automatic field-camera cycle detector.** Manual event tagging limits practice analytics. | Let teams label a few examples, then propose intake/score/contact/out-of-bounds moments for confirmation and calculate cycle timing. | B+U; synchronized video | Reduces analysis labor and enables richer coaching. | P/R | Season-specific vision, occlusion, and camera angle make fully automatic results unreliable. |
| 52 | **Remote mentor review packet.** A mentor cannot reproduce the robot setup and receives an unexplained video. | Share a bounded, read-only bundle containing selected clips, plots, build/config identity, questions, and threaded timestamp comments. | B+U; uploaded replay and annotations | Makes asynchronous help much more effective. | R | Requires access control, expiry, privacy review, and no remote robot control. |
| 53 | **Robot “flight certification.”** Teams lack a shared definition of ready after a major change. | Run a configurable suite—connection, controller, sensor sanity, motor direction, mechanism limits, autonomous repeatability, battery load—and issue an expiring internal readiness badge. | B+U; debug tools, requirements, sessions | Gives the whole team a visible release gate. | P | It is an internal test result, never official inspection or a guarantee of safety. |
| 54 | **Repair effectiveness tracker.** A fix is declared successful after one run and the same symptom returns. | Link a repair to the incident signature, require chosen verification runs, and automatically reopen it if the symptom recurs within a defined window. | B+U; incidents, asset history, test results | Closes the loop between pit work and evidence. | P/R | Similar symptoms can have different causes; a recurrence is a prompt, not proof. |
| 55 | **Robot failure rehearsal generator.** Drive teams practice perfect operation but not recovery. | Select legal, software-simulated practice scenarios—lost intake, delayed mechanism, degraded speed, failed sensor fallback—and score recognition and response. | B+U; fault injection and drills | Improves composure and fallback strategy. | P | Never inject uncontrolled physical faults; scenarios must be safe, bounded, and practice-only. |

## Build, integrate, or avoid

### Build as FTC Advanced's core

1. **Reliability data model and run provenance.** This is the foundation that makes every later conclusion credible.
2. **Incident capture, comparison, and annotations.** These exploit existing synchronized data and create immediate team value.
3. **Autonomous Repeatability Lab.** It fits the current OpMode/telemetry/recording architecture and addresses a universal FTC need.
4. **Battery Passport and configuration drift.** Both connect physical pit discipline to measured robot behavior.
5. **Driver Practice Coach.** This is a distinctive use of gamepad + pose + mechanism + video data that ordinary dashboards do not combine.
6. **Experiment/portfolio evidence.** Build the robot-evidence pipeline; keep final authorship with students.

### Integrate before rebuilding

- **Official event information:** use the FTC Events API with caching, stale-state labels, and offline snapshots.
- **Scouting:** import from CSV/JSON/QR or support adapters. Mature tools already offer offline collection, comparison, and synchronization; FTC Advanced's unique contribution should be alliance-path compatibility and linking the team's own reliability evidence.
- **Road Runner/Pedro/other localization stacks:** consume their logs/signals and export compatible tuning artifacts rather than forcing a new path framework.
- **FTC Dashboard or Panels:** coexist or provide migration/adapters for familiar telemetry/configuration conventions. [FTC Dashboard](https://acmerobotics.github.io/ftc-dashboard/) already provides telemetry plots, field graphics, live variables, camera streaming, and limited OpMode/gamepad control.
- **Team task/calendar systems:** link a robot incident or repair to an external task tool if desired; do not turn the robot workstation into a general project-management suite.

### Avoid or strictly constrain

- A second generic all-robot metrics wall; the repository's focused-workbench philosophy is stronger.
- Cloud-only features needed in the pit or stands; venue connectivity is not dependable.
- Unexplained “AI says the motor will fail” scores. Always show signals, comparison window, rule/model version, uncertainty, and the next human check.
- Autonomous AI control or arbitrary code execution through MCP. The current read-only boundary is appropriate until an independently secured, safety-reviewed use case exists.
- Assuming the laptop web Driver Station or TCP telemetry link is legal for an official match. Treat it as practice tooling unless current rules and officials say otherwise.
- Automatically changing firmware, constants, or robot configuration because an anomaly was detected. Recommend and verify; require deliberate team action for changes.
- Rebuilding a full scouting, chat, inventory, portfolio editor, or task-management product when a focused adapter would preserve development time for robot-specific strengths.

## Suggested opportunity sequence

### Quick wins: make recordings useful to humans

- Add human-readable session names/tags and explicit run outcome.
- Add timestamp annotations and one-tap incident markers.
- Export a compact run summary with selected plots and clips.
- Surface configuration hash, OpMode, protocol version, and available build identity.
- Implement the role-aware attention queue already described in the design documents.

### Foundation: make evidence comparable and trustworthy

- Define a versioned session-manifest/provenance model.
- Bring replay into the shared information architecture and align video/robot/log clocks.
- Add baseline/candidate comparison with schema/frame compatibility checks.
- Ingest Robot Controller and Driver Station match logs into the session timeline. FIRST documents the log locations and the importance of correct timestamps in [Using Log Files to Troubleshoot Problems](https://ftc-docs.firstinspires.org/en/latest/control_system_troubleshooting/using_log_files/using-log-files.html).
- Add battery, part, and configuration identities without requiring every low-value component to be cataloged.

### High-value applications

- Build Autonomous Repeatability Lab on the comparison foundation.
- Add deterministic Reliability Copilot checks for voltage, current/velocity mismatch, loop overruns, gaps, disconnects, watchdogs, and configuration drift.
- Build driver cycle segmentation, practice drills, and evidence-linked coaching.
- Add guided self-inspection and internal flight certification.
- Connect experiment notes and verified results to the portfolio evidence inbox.

### Ambitious differentiators

- 3D maintenance/failure history.
- Audio/vibration health baselines.
- Semi-automatic video event detection.
- Fault-injection and failure-rehearsal sessions.
- Remote mentor review with bounded, expiring access.
- Alliance autonomous collision planning driven by shareable path envelopes rather than proprietary robot data.

## BIOBUZZ hooks after kickoff

As of this report, FIRST lists the 2026–27 BIOBUZZ kickoff/game reveal for **12 September 2026**. The available V0 manual and season pages should not be treated as a complete source of game mechanics. Sources: [official BIOBUZZ game-and-season page](https://www.firstinspires.org/programs/ftc/game-and-season) and [2026–27 game materials](https://ftc-resources.firstinspires.org/ftc/archive/2027/game).

After the reveal, add a season pack rather than hardcoding game assumptions into the platform:

1. **Scoring/event schema:** game phases, scoring actions, penalties, ranking/tie-break fields, and official score-detail mapping.
2. **Cycle vocabulary:** team-defined intake, transport, scoring, defense, recovery, and endgame events used by practice analytics.
3. **Field model:** official dimensions, obstacles, AprilTag metadata, legal start regions, and robot-clearance overlays.
4. **Autonomous test matrix:** starts, selectable branches, environmental variables, success criteria, and partner-conflict envelopes derived from the revealed game.
5. **Inspection/rules pack:** versioned checklist links, software/console constraints, and change notices; never silently copy old-season rules forward.
6. **Scouting adapter:** season-specific form definitions and FTC Events score-detail import while keeping the storage/merge/confidence engine generic.
7. **Driver drills:** game-specific repeated skills and fallback scenarios generated only after the team defines its strategy.
8. **Portfolio evidence tags:** current award criteria and limits, with source/version dates visible.

This separation keeps FTC Advanced useful every year: the platform owns sessions, safety, evidence, comparison, and workflows; a small season pack supplies game-specific semantics.

## Final product judgment

FTC Advanced's defensible advantage is the **closed learning loop around the team's own robot**:

```text
prepare → run → capture → explain → repair/tune → verify → trust
```

The app should become the place where a team proves that a change helped, reconstructs why a run failed, and decides whether the robot is ready. Scouting, schedules, judging, and team coordination are useful extensions when they feed that loop. They should not displace it.

If only one major feature is chosen, build the Reliability Copilot as an explainable layer over trustworthy run provenance and replay comparison. It would solve a real FTC problem, use nearly every strong capability already present in the codebase, and give the product an identity beyond “another telemetry dashboard.”

## Source notes

- Official FIRST materials are authoritative for rules and inspection, but season documents change. Store source URLs, version/date, and a “recheck before event” notice wherever the product turns a rule into UI.
- Community posts are used only to illustrate reported team experiences; they are not evidence of rules or universal behavior.
- Product pages are used for build-versus-integrate comparison, not as independent proof that a workflow is necessary.
- The working repository is the authority for claims about what FTC Advanced currently implements.
