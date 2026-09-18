# FTC Advanced: Community Adoption and Open-Source Strategy

## Recommendation

**Open source FTC Advanced, but introduce it through one complete workflow: record a practice run, find the moment something went wrong, and show the evidence needed to improve the next run.** Keep the larger workbench for personal use and advanced experimentation. Make its additional capabilities optional for everyone else.

The app needs substantial work on distribution, onboarding, interoperability, and documentation. Its core does not need a wholesale rewrite. The most consequential architectural addition would be recording on the robot if competition-match analysis becomes part of the public promise. The existing durable recording path depends on telemetry arriving at the laptop.

The hypothesis that nobody has attempted software this broad is contradicted by the available evidence. Panels is already an integrated FTC dashboard with plugins and recording; AdvantageScope already combines logs, field views, gamepads, and synchronized video. There are also smaller FTC logging projects and team-built integrated environments. Breadth demonstrates ambition, but does not establish a reason to switch.[^4][^9][^21][^22]

The opportunity is more specific: **make useful diagnosis easier than assembling and interpreting several existing tools.** A successful product would let a student move from “the intake failed again” to a particular interval, relevant measurements, an understandable hypothesis, and a repeatable next test. Whether FTC Advanced can do that better remains a product hypothesis to validate with teams.

Both small programming teams and experienced teams deserve equal attention. They should receive different starting experiences over the same recording and analysis foundation. Beginners need guided examples and useful defaults; experienced users need low integration cost, trustworthy timestamps, exportable data, and freedom to keep their existing robot architecture.

## Evidence and limits

This assessment covers public material available on **9 September 2026** and the local repository at commit `7676849`, including its working-tree state. Public evidence includes maintainers’ documentation, repositories, FIRST publications, and dated community discussions. Repository findings are a static assessment of code and documentation, not a fresh installation test, a robot test, a performance benchmark, or a complete security audit. Local evidence is indexed at the end.

There is no representative dashboard market-share survey in the evidence reviewed. Quickstart inclusion demonstrates a distribution channel; forum posts demonstrate particular experiences; a repository demonstrates availability. None supplies a reliable percentage of FTC teams using a tool. GitHub stars and launch upvotes are deliberately not treated as active-team counts.

Public programming discussions also overrepresent people who seek help or build libraries. Teams satisfied with basic telemetry, teams working mostly in Blocks, and teams using private regional or Discord communities are less visible. No private Discord conversations or direct team interviews are included. The recommendations therefore combine documented facts with explicitly identified product judgments.

Historical complaints are used to identify failure patterns, not to assert that the same bugs remain unfixed. This distinction matters: current Panels documentation advertises webcam streaming even though 2025 discussion described it as planned; current Sloth documentation provides Panels integration despite older incompatibility reports.[^4][^16][^17]

## What FTC programmers use

### The baseline: the official tools and a small amount of instrumentation

The official FTC Datalogging tutorial demonstrates a modest workflow: add sample Java files, run an OpMode, retrieve a CSV, and graph the data in a spreadsheet. It addresses OnBot Java and Android Studio and explains a route to Blocks through myBlocks. The tutorial is dated December 2022, so it is evidence of an established teaching approach rather than a verified current installation recipe.[^27]

That is an important competitor to a sophisticated dashboard. A team may already get sufficient value from Driver Station telemetry, a short test OpMode, a phone recording, and a spreadsheet. Such a team will not necessarily describe the missing product as “an observability platform.” It may describe one concrete difficulty: a shooter slows between shots, an autonomous run ends in the wrong place, or a sensor stops updating.

**Implication:** provide a useful result before requiring a team to learn your complete system. A saved example session with explanatory annotations can teach the product without a robot connection. Support for existing CSV data can let a team try analysis before adding a new robot library.

### FTC Dashboard and the Road Runner workflow

FTC Dashboard documents live telemetry, configuration variables, camera preview, and limited OpMode/gamepad control. Its repository also lists telemetry CSV export. Its own documentation acknowledges limitations in browser gamepad latency and robustness.[^1][^3]

Its installation advantage is significant: teams add a Maven repository and dependency, deploy the robot code normally, connect to robot Wi-Fi, and open a browser address. Installing Node or building a frontend is a dashboard-development workflow, not an ordinary user requirement.[^2]

Road Runner’s tuning documentation directly incorporates FTC Dashboard into the tuning process. This gives Dashboard a reason to be installed before a student independently shops for dashboards.[^7] The strategic advantage is the surrounding tutorial and example ecosystem, not merely the interface.

**Product judgment:** displacing this workflow for prettier graphs is difficult. Adding a clear explanation of a failed run, while preserving its existing instrumentation, is a more credible proposition.

### Panels and the Pedro Pathing workflow

Panels is the closest direct competitor. Its current public site advertises live configuration, field and graph views, capture/replay, OpMode controls, gamepads, Limelight integration, webcam streaming, offline documentation, and plugins. It is already an integrated toolkit.[^4][^5]

Pedro Pathing’s current documentation says its quickstart includes Panels by default and supports either Panels or FTC Dashboard for visualization. It also identifies a specific limitation: FTC Dashboard does not support live tuning of Pedro’s complex constants in the documented workflow.[^6]

**Product judgment:** a new independent application has to overcome a bundled default. A reliable Pedro adapter and a useful shared workflow are more promising than asking every Pedro user to replace their dashboard.

### AdvantageScope and FTC logging libraries

AdvantageScope’s current repository lists FTC Dashboard live streaming and Road Runner, CSV, WPILOG, and RLOG log inputs, among others. It provides graphs, field visualization, video alignment, joystick views, and exports. AdvantageKit is not required to use it.[^9]

The video documentation describes separately loading footage and aligning it to logs, including manual synchronization when automatic alignment does not work. It also documents frame conversion, an FFmpeg dependency, and the absence of sound playback in that view.[^10] These are workflow tradeoffs to test against, not proof that FTC Advanced already provides a superior video experience.

The current ecosystem has several entry points:

| Tool or combination | Documented role | Adoption friction or limitation supported by the evidence | Implication for FTC Advanced |
|---|---|---|---|
| Official telemetry / Datalogging / CSV | Simple instrumentation and spreadsheet analysis | Manual selection, retrieval, and interpretation in the documented workflow | Import familiar data and guide interpretation before requiring migration.[^27] |
| FTC Dashboard + Road Runner | Live tuning and visualization inside an established autonomous workflow | Configuration semantics require care; browser driving has documented limitations | Preserve the workflow; make saved-run diagnosis additive.[^1][^7] |
| Panels + Pedro Pathing | Integrated dashboard, plugins, and quickstart distribution | Community reports show version/setup confusion; those incidents are historical, not a current defect rate | Offer explicit compatibility checks and an adapter with a tested example.[^6][^18] |
| AdvantageScope desktop | Broad analysis of logs and live data | Teams still need an appropriate data source and video alignment workflow | Compete on the complete diagnostic task; provide exports into its ecosystem.[^9][^10] |
| PsiKit | FTC-oriented port of AdvantageKit concepts | Its repository README and other current ecosystem descriptions disagree about non-live logging maturity | Treat capabilities as version-specific; verify an actual round trip before advertising compatibility.[^8][^13] |
| KoalaLog + AdvantageScope | FTC logging to WPILOG, with annotation support and log-retrieval tools | Users reported missing lifecycle instructions and requested macOS support in May 2026 | Clear lifecycle examples and reliable distribution matter as much as file format.[^14][^15] |
| Sloth | Faster code deployment during development | Its docs describe dependency variants and cases needing full installs | Support it deliberately if pilot teams use it; do not build a competing deployment system.[^16] |

PsiKit deserves particular caution. Its repository README labels log review/replay as forthcoming, while Pedro’s current comparison describes PsiKit as an option for file logging and AdvantageScope replay. The accessible sources do not establish which exact release reconciles these descriptions. This report does not repeat the old README as proof that current PsiKit cannot record files.[^8][^13]

AdvantageScope Lite is also relevant. Official documentation describes a Systemcore-oriented browser edition for v27.x and points to an unofficial existing-FTC distribution. The latter advertises a Gradle dependency, automatic FTC Dashboard connection, and existing Road Runner/PsiKit log access. Official Lite omits the video tab; the unofficial port’s generic feature listing should not be treated as proof that every desktop feature works there.[^11][^12]

### Other broad projects exist, but availability is not adoption

An August 2026 FTC discussion describes ARES Analytics as combining simulation, dashboards, path planning, log management, and other functions. Its author also says the suite was not ready for release and required building multiple repositories. The organization’s public repository listing corroborates the project’s existence; its detailed runtime claims remain self-reported.[^21]

TRACE proposes gradual adoption of telemetry, event recording, and replay. Its README explicitly distinguishes its desktop-tested components from FTC adapters that are sketches and robot use that has not been tested. It is evidence of parallel interest, not an established competitor with demonstrated deployment at scale.[^22]

Even FTC-specific MCP tooling already exists: ftcMCP advertises project inspection, knowledge lookup, and code validation. That differs from FTC Advanced’s access to live telemetry and bounded debugger actions, but it means “has AI/MCP integration” is too broad a uniqueness claim.[^23]

## What community reports reveal

The strongest repeated theme is the cost of obtaining a useful result: connecting correctly, installing compatible dependencies, producing the intended telemetry, and understanding what the data means. There is evidence of demand for richer tools, but much less evidence that teams want a large mandatory platform.

| Evidence | What actually happened | Product lesson | Confidence and boundary |
|---|---|---|---|
| Panels launch discussion, August 2025 | Users asked what it improved over FTC Dashboard; another participant challenged inaccurate claims about missing Dashboard features | Demonstrate a fair, concrete before/after workflow | High confidence in the discussion; no adoption-rate inference.[^17] |
| Panels/Pedro help thread, February 2026 | A team could not connect; replies focused on deployment, enablement, and dependency versions | Diagnose the failed setup stage instead of returning a generic connection error | A user-reported resolution, not a reproduced bug.[^18] |
| Programming-problems discussion, February 2026 | Participants described slow deployment, tried Sloth, and encountered integration confusion | Teams value iteration speed; avoid adding setup work to every session | Self-reported timings are not comparative benchmarks.[^19] |
| FTC Dashboard help, December 2023 | A student followed a video that introduced unnecessary SDK activity changes | Versioned written instructions and complete examples prevent tutorial drift | Historical example, not proof current Dashboard installation is difficult.[^20] |
| KoalaLog discussion, May 2026 | A user reported missing start/stop calls in the docs; the maintainer acknowledged and updated them; another asked about macOS | A small omission can block an otherwise useful tool; OS support must be explicit | Direct user and maintainer reports.[^15] |
| Shooter troubleshooting discussion, 2025–26 | Participants wanted to graph wheel speed to understand inconsistent shots and timing | Mechanism-focused diagnosis is a concrete job worth testing | Evidence of the problem, not proof of demand for this particular app.[^28] |
| AdvantageScope issue, February 2026 | An FTC user reported a port collision involving a PsiKit RLOG server and Limelight and requested an easier port setting | Connection diagnostics should identify endpoint/port conflicts | Single issue; its current resolution was not established.[^29] |

These reports support a narrower interpretation than “teams dislike existing tools.” The tools often work well; the fragile part is the sequence between documentation, dependencies, robot configuration, telemetry wiring, and interpretation. FTC Advanced can improve that sequence, but it currently introduces several additional pieces of its own.

## Repository assessment

### Valuable foundations already implemented

The repository contains more than a UI concept. It has a Python robot-control client, a FastAPI application, a React/TypeScript interface, a structured protobuf telemetry client on the robot, persistent logs, a separate replay application, camera capture, incident manifests, and a friction-analysis path. It also contains tests around several of these boundaries. The presence of tests is evidence of engineering work, not a claim that they all passed during this assessment.

| Area | Evidence in the current repository | Assessment |
|---|---|---|
| Structured data | `StructuredRobotDataClient.java`, protobuf schema/bindings, TCP listener, telemetry store | Keep. Explicit signals, units, sessions, and timestamps are a strong analysis foundation. |
| Durable recording | `web_driver_station/backend/ftclog.py` | Keep. Versioned raw frames, CRC checks, and partial-file recovery are valuable; durability depends on data reaching the laptop. |
| Replay | `recording_viewer/backend/main.py` and frontend `App.tsx` | Keep and improve. The app already reads saved data independently of a live robot and displays traces, input state, pose, incidents, and video. |
| Incident capture | `web_driver_station/backend/incidents.py` and viewer integration | Build on it. Persisted incident intervals already exist; a universal automatic root-cause detector does not follow from that. |
| Mechanism tools | `SelectableOpMode.java`, `Debugger.java`, friction benchmark and analysis modules | Keep as an optional advanced capability. Start by generalizing one proven mechanism workflow. |
| Camera and upload | Camera modules and SFTP uploader | Keep behind optional setup. Useful integration, with substantial device and environment variability. |
| MCP | `mcp_server/server.py` | Keep optional. The implementation includes OpMode lifecycle and bounded motor/benchmark tools, beyond older read-only descriptions. |

Local source links and the exact scope of these observations appear in the repository evidence index below.

The existing no-hardware `TelemetryGamepadTest` is especially useful for onboarding: its runtime, gamepad, and mock-pose data do not require motors or sensors. It can become the first verified robot-connected example. An offline example recording would remove the robot requirement altogether.

### Adoption barriers visible in the repository

**1. Ordinary installation resembles setting up a development environment.** The Windows launcher checks for Python, Node, and pnpm, creates a virtual environment, installs Python dependencies, runs `pnpm install`, and rebuilds the frontend. It does those install/build steps on subsequent launches as well. It also contains a fallback to a developer-specific runtime location. A double-click launcher is helpful, but it does not make this a self-contained release.

**2. The default launch configuration is personal.** The launcher sets a particular remote upload host, account, private-key path, and known-hosts location. Those defaults should not ship as another team’s starting configuration. Upload should initially be disabled and should become available only after the team chooses its own destination.

**3. Robot integration is source-oriented.** The structured client and generated bindings live inside the included robot project. There is a usable direct client API, but no documented published SDK dependency that another team can add without adopting this repository’s robot project. Extracting and documenting that boundary is more valuable than adding another panel.

**4. A successful connection has several meanings.** Robocol control, structured TCP data, camera authorization, recording, and remote upload are distinct. The architecture documents already recognize this. Onboarding needs to make these differences obvious: a connected Robot Controller does not prove the active OpMode publishes structured telemetry.

**5. Public-facing documentation is incomplete and inconsistent.** The root README is only the project title. The dashboard README describes the TCP service as not feeding the frontend, while the current code integrates the data service and frontend telemetry. `STRUCTURE.md` describes MCP as read-only, while `mcp_server/server.py` exposes control tools. A public team cannot reliably distinguish old plans from present behavior.

**6. The replay experience is still specific to the current application.** The frontend explicitly limits numeric traces to three and renders a field using Pedro-style 0–144 inch coordinates. Video playback follows the recording’s elapsed time; the inspected viewer does not apply a measured camera offset or the full segment/frame-map calibration described in plans. Existing timestamp metadata is useful, but does not establish calibrated synchronization accuracy.

**7. The benchmark is characterization, not an automatic mechanical-health verdict.** The friction analyzer calculates summaries and fits, reports weak support, and explicitly explains that controller current is not calibrated friction torque. This is a good boundary. Comparisons across runs need battery, load, direction, geometry, and other context before they can support statements about deterioration.

**8. Current recording is not a disconnected match recorder.** The inspected Java client streams to a laptop, with a bounded queue; the durable `.ftclog` writer is on the laptop. A lost stream is not equivalent to an onboard recording that can be downloaded later. No equivalent durable onboard sink was found in the inspected client.

**9. Release reproducibility needs a public contract.** The frontend manifest uses `latest` ranges, although the launcher correctly requests a frozen lockfile. Keep the lockfile and identify supported runtime versions; ordinary launch should not re-resolve packages or rebuild the UI. Define which combinations of app, robot library, schema, and FTC SDK have actually been tested.

## Competition use and the control-system transition

### The current rules change the recording architecture

The current season page links **BIOBUZZ 2026–27 Competition Manual V0**, dated 31 July 2026. In that published version, **R704** requires programming laptops to disconnect during match play and restricts third-party wireless logging/streaming, explicitly naming FTC Dashboard and Panels. **R901** specifies an Android-based Driver Station device. These rules support the repository’s existing description of its laptop Driver Station as a testing tool.[^24]

Product consequence: market the initial release for **practice and offline review**. A future match-analysis workflow should record locally on the robot, retrieve files afterward, and align separately recorded video. A competition configuration must disable discovery and third-party streaming as well as actuation integrations; closing the browser alone does not establish that those services stopped. Validate that configuration against the final manual and current Q&A before claiming event compatibility.

### Preserve the analysis investment across hardware generations

FIRST’s December 2025 update schedules the new FTC control system for **2027–28**, with legacy/hybrid transition through at least **2030–31**. It also describes development of a desktop Driver Station application. These are announced plans, not a claim that all future software interfaces are finalized.[^25]

**Recommendation:** keep Robocol as one replaceable transport. Put session identity, signal metadata, replay, video alignment, comparisons, and reports behind interfaces that can accept other transports and log formats. Make room for WPILOG/NetworkTables interoperability as actual supported platforms and pilot demand justify it.

There is still a useful legacy-Control-Hub window. The transition is a reason to prioritize portable analysis and avoid spending the public roadmap on reproducing every legacy Driver Station screen.

## Positioning and differentiation

### The public promise

Suggested positioning:

> **FTC Advanced helps your team understand a failed practice run, using the video, robot data, and driver inputs from that moment.**

This promise is narrower than the architecture and broad enough to matter to both audiences. It also has a demonstrable outcome. A launch video can show an actual failure, navigate to its evidence, compare it with a successful run, and document the test that fixed it.

The durable advantage would be a collection of reliable workflows, sensible presets, trustworthy data handling, and examples from real teams. Open-source code makes individual features easy to copy; continued usability and community trust are earned through maintenance.

### Differentiators worth validating

| Proposed advantage | Beginner value | Experienced-team value | Proof required before marketing it |
|---|---|---|---|
| Incident-centered review | “Show me where to look” | Less time locating relevant evidence | A student finds the relevant interval without maintainer help |
| Guided mechanism tests | Understand what a test measures | Repeatable characterization with raw data | Results repeat under controlled conditions; limitations remain visible |
| Compare two runs | See the effect of one change | Compare error, timing, current, and state under matched conditions | Correct alignment, comparable metadata, and clear missing-data handling |
| Simple capture and video alignment | Fewer tools and manual steps | Known offset, drift, and uncertainty | Measured accuracy on supported camera paths |
| Share a self-contained session | Ask a mentor for help | Reproduce analysis on another machine | Works offline without the author’s server or installed toolchains |
| Existing-tool compatibility | Follow familiar tutorials | Keep existing libraries and architecture | Tested examples and successful import/export round trips |

These are proposed advantages, not claims of exclusive features. In particular, synchronized video, replay, plugins, AI, and a broad dashboard each already have public precedents.

### Features to keep out of the initial adoption path

Cloud hosting, SFTP, camera-phone setup, MCP, agent-driven actuation, hardware configuration editing, and custom mechanism UI development should not be prerequisites for a useful first session. Keep them available as optional capabilities where they help the author’s team.

Avoid adding scouting, outreach management, judging automation, a new path planner, and a universal plugin marketplace to the first public milestone. The existing feature-opportunities document contains many reasonable ideas, but publishing that whole opportunity space as a roadmap would create an unsustainable support promise.

## Serving both audiences

Equal attention should mean equal validation, not identical interfaces or support for every toolchain at once.

| Decision | Small team / newer programmer | Experienced programming team |
|---|---|---|
| First experience | Open a labeled example run with a short explanation | Import an existing run or connect a small instrumentation example |
| Initial data | A few explained signals with units and useful defaults | Searchable catalog, selectable signals, units, roles, and provenance |
| First success | Find a failure interval and explain one observation | Compare a failed and successful run without changing robot architecture |
| Robot integration | One complete minimal Java example with visible success indicators | Small SDK plus optional adapters; no forced base OpMode or framework |
| Analysis | Guided questions and mechanism presets | Raw export, configurable comparisons, visible uncertainty and data gaps |
| Optional depth | Add camera or another signal when needed | Custom tools, benchmark integrations, richer adapters, and MCP |
| Main rejection risk | “I cannot install it or understand why I need it” | “It duplicates my tools and forces a migration” |

Beginner does not always mean Java user. The current app’s full integration fits Java/Android Studio most naturally. Be explicit about that first support boundary. Give Blocks and OnBot teams access to the offline demonstration and supported imports; validate a maintained bridge before advertising full support for their robot workflows. FIRST’s myBlocks logging example shows that a bridge is possible, but does not establish compatibility with FTC Advanced’s protobuf client.[^27]

The public UI should organize actions around jobs such as **Open a run**, **Record a practice run**, **Investigate an incident**, and **Compare tests**. Advanced control and integration settings can be exposed when relevant. Existing capability-based navigation is a useful starting point, though a missing live capability should still have a discoverable explanation on the home screen.

## How much the app should change

**Overall judgment: a substantial productization pass, selective refactoring, and a narrower public scope.** A code-rewrite percentage would be misleading without a dependency-level implementation plan. The following changes are more useful to estimate independently.

| Area | Change required | Priority | Completion criterion |
|---|---|---|---|
| Public story and documentation | High | Before inviting outside teams | Root README demonstrates one use case, lists prerequisites and supported configurations, and distinguishes present features from plans |
| End-user distribution | High | First public beta | A downloaded package opens a sample recording without Python, Node, pnpm, or a frontend build |
| Personal configuration | Small but essential | Before publishing a release | No personal upload destination or credential paths are enabled by default |
| Onboarding and diagnostics | High | First public beta | The app distinguishes missing runtime, wrong network, absent robot integration, no data, and unavailable optional devices |
| Recording/replay core | Moderate | First public beta | A session can be recorded, reopened, inspected, and shared locally; failures preserve understandable partial artifacts |
| Robot SDK distribution | Moderate to high | Before broad integration claims | A team adds a versioned dependency and a minimal example to its existing project |
| Import/export | Moderate, format-dependent | During pilots | At least one common external format round-trips with timestamps, units, and gaps documented |
| Run comparison and guided diagnosis | Moderate to high | Main value milestone | Users explain a real problem faster or more accurately than with their prior workflow |
| Camera synchronization | Moderate to high | Before precision claims | Supported sources have a clear offset/alignment model and measured limitations |
| Onboard match logging | High; separate milestone | Required only for match-analysis promise | Full recording and later retrieval succeed without a laptop connection or third-party wireless streaming |
| Robocol control and configuration UI | Low change for personal use; high assurance for broad use | Optional advanced track | Clearly documented practice scope, tested stop behavior, and no accidental control authority |
| MCP and cloud | Mostly isolation and documentation now | Optional | Core use is complete with both disabled |

### Packaging recommendation

Start with a **Windows release bundle containing a prebuilt frontend and a bundled Python runtime/backend**, opening the normal browser. That reuses the current stack. Select the packaging mechanism after a small clean-machine prototype rather than committing immediately to a desktop-shell rewrite.

Keep source installation as the contributor path. The ordinary-user launcher should select a free port, start the backend, verify readiness, and open the browser. Updates should be separate from ordinary startup and preserve existing recordings and a rollback path.

Split optional camera/upload dependencies from the minimum replay/telemetry installation where feasible. Bundle a few small example sessions, including one with missing data, and essential troubleshooting instructions. An initial download may need the internet; reopening the app and reviewing local data should not.

A Docker image is useful for self-hosted replay, but it adds unfamiliar machinery to the robot-network/camera workflow. It should not be the beginner installation recommendation. A fully browser-only viewer could eventually make demos and shared-session review easier, but it would require porting parsing and file handling; it is an option to evaluate, not a prerequisite for releasing the existing work.

Windows-first is a support sequencing choice, not a claim that all FTC teams use Windows. Recruit macOS/Linux testers and make their status explicit. Add verified builds as demand and maintainer capacity permit; browser frontend portability alone does not prove that native camera, ADB, or packaging dependencies work everywhere.

### Integration recommendation

Expose a small robot library that can be composed into an ordinary `OpMode` or `LinearOpMode`. Keep the mechanism-oriented `SelectableOpMode` optional. Provide a compatibility table covering the combinations actually exercised, rather than claiming support for every FTC framework.

Start adapters with pilot demand. A plausible order is generic CSV import/export, one Road Runner or Pedro telemetry adapter, then WPILOG interoperability. CSV needs explicit rules for time columns, units, missing values, and flattening structured types. A richer format should preserve information CSV cannot represent. Do not promise all three in the first beta.

Retain `.ftclog` as the lossless internal archive if it continues to serve the implementation well. Document its schema/version policy and provide a small independent reader plus sample files. Being open source does not automatically make a custom format convenient for another team’s analysis tool.

## Open-source decision

### Why opening the project is a good fit

The repository already contains an **MIT license** for the project’s own code. The decision is therefore largely about public availability, release quality, contribution expectations, and support. Repository visibility was not established by the local inspection.

Open sourcing fits the stated objective: build something useful for the author’s team and let others benefit. It gives teams access to the implementation, allows adaptation when their robot differs, and provides continuity if the original maintainer becomes unavailable. It can also attract improvements that a single team cannot test, such as another operating system or controller combination.

Those are potential benefits, not guaranteed outcomes. Publishing source does not recruit maintainers automatically. Most adoption work will initially remain with the author: examples, compatible releases, issue triage, and explanation of what the product is for.

### License and release boundaries

**Recommendation: keep MIT for original project code unless there is a specific reason to require downstream modifications to remain open.** MIT permits reuse, modification, distribution, and commercial use subject to retaining its notice and license terms. It does not require downstream projects to publish their changes.[^26]

The included FTC SDK has its own license and notices. They must remain separately attributed; placing an MIT file at the repository root does not replace third-party terms. Before distributing bundled runtimes, camera utilities, codecs, or other binaries, inventory their actual licenses and ship the required notices. Protocol interoperability with another tool is a different decision from copying that tool’s source.

For the first public release, publish the reusable application, robot library, format documentation, and examples. Keep personal deployment settings, credentials, private recordings, and team-specific experiments outside the release package. They can remain local or in an appropriate private location.

A local private-key file is present and is specifically ignored. The tracked-path/history-name check did not find a `.key`/`.pem`/`.env` entry in local history, but that narrow check does not establish the absence of secrets elsewhere. The launch script demonstrably references personal infrastructure, and `server.txt` is tracked. A content-aware check of the release tree and relevant history is a concrete pre-publication task; no credential values need to appear in documentation or an issue.

### Sustainable maintenance

Publish an explicit supported core: for example, local capture/replay on one tested OS, a specific FTC SDK range, and one working robot integration. Label experimental capabilities clearly. Add a short contribution guide, a reproducible bug-report template, compatibility notes, release notes, and a security contact route.

Prioritize contributions that preserve the core workflow: a tested adapter, a diagnostic improvement, a fixture for a malformed recording, or a verified platform build. Avoid accepting features solely because someone offers code; every feature adds a maintenance obligation.

Use a predictable support boundary. A student tool can honestly say that responses depend on school and competition schedules. A release that works without its creator’s server is more sustainable than a free service whose uptime and storage bills depend on one person.

Retain the freedom to build ambitious features for personal use. The public supported surface can be smaller than the complete repository. That is compatible with treating outside users seriously and with keeping the project enjoyable.

## A practical release and validation plan

This is a proposed sequence, not a delivery estimate. Clean-machine packaging, actual robot behavior, and pilot feedback may change its duration. The first milestone should be independently useful even if later work stops.

| Stage | Deliverable | Exit test |
|---|---|---|
| 1. Make a reviewable public alpha | Clear README, current feature descriptions, optional personal integrations, license inventory, example recording | An unfamiliar student can explain the use case and open the example using the documented path |
| 2. Package one complete practice workflow | Prebuilt app, minimal robot SDK/example, visible connection stages, record/open/share loop | A clean Windows machine completes the workflow without a developer toolchain |
| 3. Pilot with both audiences | Five small/newer programming teams and five experienced teams | Each team attempts a real debugging task; assistance and failure points are recorded separately |
| 4. Improve the proven value | Fix installation blockers; improve incident review; add the highest-demand adapter or comparison | Teams return at a later practice and use it without the author guiding them |
| 5. Broaden deliberately | Verified additional OS builds, richer formats, additional mechanism tools | Each supported path has a maintainer or repeatable verification process |
| 6. Consider match analysis | Onboard recording, later retrieval, event configuration, independent footage alignment | End-to-end tests and current rules review support the precise advertised use |

### Suggested pilot targets

The numbers below are **decision thresholds proposed for a small pilot**, not market benchmarks or statistically reliable forecasts:

- At least **8 of 10 teams**, including **4 of 5 in each audience**, complete a first recording/review with no more than the written guide and brief help.
- At least **6 of 10**, including **3 of 5 in each audience**, choose to use it again at a separate practice within two weeks.
- At least **4 teams** document one specific debugging or tuning decision that the app helped them make, with an example artifact.
- Aim for a **five-minute offline demonstration** and a **fifteen-minute first robot-connected result** once the required robot project and hardware are ready. Record setup delays separately instead of hiding them.
- Track maintainer assistance per team and per session. Treat repeated manual installation or server intervention as a product defect, even if the eventual demo succeeds.

A useful active-team definition is “a team that recorded or analyzed a real robot session on at least two separate practice days.” This is more meaningful than downloads, stars, or membership in a support server.

Compare each team against its own previous workflow. Start with “show the last problem you had and how you investigated it,” then ask the team to attempt a comparable task using FTC Advanced. Observe whether it finds the correct evidence, misunderstands a graph, or needs intervention. A polished demonstration by the author is not an installation or usability test.

### Questions that can change the roadmap

1. What was the last failure that took more than one practice to understand?
2. Which tools and data did the team actually use, and what was missing?
3. Where does the new installation first fail on the team’s real laptop?
4. Can a student identify the relevant signals without someone explaining the robot code?
5. Does the team need synchronized video, or do incident markers and a few plots solve its immediate problem?
6. Would the team use this alongside its existing dashboard, and what would cause it to stop?
7. Is the need primarily practice analysis or review of official matches?
8. Can a second student repeat the workflow after the first student is absent?

If teams inspect the demo but do not install, work on value clarity and setup. If they install but do not return, investigate relevance and analysis quality before adding features. If only experienced teams retain it, simplify the beginner path or state a narrower audience honestly. If both groups mainly use exported data elsewhere, consider making FTC Advanced an excellent recorder and diagnostic layer rather than insisting that its dashboard be the destination.

## Community introduction

Start with a small pilot through existing relationships and relevant public FTC programming communities. Public Reddit and Chief Delphi threads show real discussion around dashboards and logging, but the evidence does not establish the reach of any particular channel. Make contact with library maintainers only after there is a working integration to demonstrate.

Prepare three assets: a short failure-to-diagnosis demonstration, a small downloadable example session, and a complete installation guide for the supported release. Ask for help validating a specific workflow rather than asking whether people like a large feature list.

Publish fair comparisons. Acknowledge that Dashboard is easy to add, Panels integrates with Pedro, and AdvantageScope already offers sophisticated visualization. Show the steps FTC Advanced removes or the explanation it makes easier. If those improvements are not yet demonstrable, describe them as the pilot’s objective.

Seek tutorial or quickstart integration after compatibility has been proven. The Road Runner and Pedro documentation illustrate why being part of the path a student already follows can be more valuable than an independent product announcement.[^6][^7]

The project does not need to become the majority dashboard to succeed. A smaller group of teams repeatedly using it to solve real problems would justify continued development, provide useful feedback, and preserve its original purpose. Widespread popularity remains uncertain; a focused open-source release gives that possibility a credible test without requiring the author to build and support everything at once.

## Repository evidence index

These links point to the local files inspected. The observations concern the assessed working tree; later edits can supersede them.

| Evidence | Local source |
|---|---|
| Root introduction and existing project license | [README](<C:/Users/Lefteris Dragasakis/Documents/GitHub/FTC_ADVANCED/README.md>), [LICENSE](<C:/Users/Lefteris Dragasakis/Documents/GitHub/FTC_ADVANCED/LICENSE>) |
| Installation steps, repeated dependency/build operations, personal upload settings | [Windows launcher](<C:/Users/Lefteris Dragasakis/Documents/GitHub/FTC_ADVANCED/run_web_driver_station.bat>), [dashboard README](<C:/Users/Lefteris Dragasakis/Documents/GitHub/FTC_ADVANCED/web_driver_station/README.md>) |
| Frontend and backend dependencies | [frontend package manifest](<C:/Users/Lefteris Dragasakis/Documents/GitHub/FTC_ADVANCED/web_driver_station/frontend/package.json>), [backend requirements](<C:/Users/Lefteris Dragasakis/Documents/GitHub/FTC_ADVANCED/web_driver_station/backend/requirements.txt>) |
| Capability model and older scope descriptions | [STRUCTURE](<C:/Users/Lefteris Dragasakis/Documents/GitHub/FTC_ADVANCED/STRUCTURE.md>), [app mental model](<C:/Users/Lefteris Dragasakis/Documents/GitHub/FTC_ADVANCED/APP_MENTAL_MODEL.md>) |
| Current data-service integration and APIs | [backend main](<C:/Users/Lefteris Dragasakis/Documents/GitHub/FTC_ADVANCED/web_driver_station/backend/main.py>), [TCP listener](<C:/Users/Lefteris Dragasakis/Documents/GitHub/FTC_ADVANCED/web_driver_station/backend/robot_data_tcp_server.py>) |
| Durable raw log format and incident manifests | [ftclog](<C:/Users/Lefteris Dragasakis/Documents/GitHub/FTC_ADVANCED/web_driver_station/backend/ftclog.py>), [incidents](<C:/Users/Lefteris Dragasakis/Documents/GitHub/FTC_ADVANCED/web_driver_station/backend/incidents.py>) |
| Replay parsing, trace limit, field coordinates, and video playback | [viewer backend](<C:/Users/Lefteris Dragasakis/Documents/GitHub/FTC_ADVANCED/recording_viewer/backend/main.py>), [viewer frontend](<C:/Users/Lefteris Dragasakis/Documents/GitHub/FTC_ADVANCED/recording_viewer/frontend/src/App.tsx>), [viewer README](<C:/Users/Lefteris Dragasakis/Documents/GitHub/FTC_ADVANCED/recording_viewer/README.md>) |
| Robot client, bounded queue, and discovery | [StructuredRobotDataClient](<C:/Users/Lefteris Dragasakis/Documents/GitHub/FTC_ADVANCED/FtcRobotController/TeamCode/src/main/java/org/firstinspires/ftc/teamcode/data/StructuredRobotDataClient.java>) |
| Minimal no-hardware example | [TelemetryGamepadTest](<C:/Users/Lefteris Dragasakis/Documents/GitHub/FTC_ADVANCED/FtcRobotController/TeamCode/src/main/java/org/firstinspires/ftc/teamcode/TelemetryGamepadTest.java>) |
| Optional mechanism framework and current hardware configuration | [SelectableOpMode](<C:/Users/Lefteris Dragasakis/Documents/GitHub/FTC_ADVANCED/FtcRobotController/TeamCode/src/main/java/org/firstinspires/ftc/teamcode/SelectableOpMode.java>), [Debugger](<C:/Users/Lefteris Dragasakis/Documents/GitHub/FTC_ADVANCED/FtcRobotController/TeamCode/src/main/java/org/firstinspires/ftc/teamcode/Debugger.java>) |
| Characterization output and explicit measurement limitations | [debugger analysis](<C:/Users/Lefteris Dragasakis/Documents/GitHub/FTC_ADVANCED/web_driver_station/backend/debugger_analysis.py>) |
| Actual MCP tool surface | [MCP server](<C:/Users/Lefteris Dragasakis/Documents/GitHub/FTC_ADVANCED/mcp_server/server.py>), [MCP README](<C:/Users/Lefteris Dragasakis/Documents/GitHub/FTC_ADVANCED/mcp_server/README.md>) |
| SDK dependencies and third-party license | [TeamCode Gradle](<C:/Users/Lefteris Dragasakis/Documents/GitHub/FTC_ADVANCED/FtcRobotController/TeamCode/build.gradle>), [FTC SDK license](<C:/Users/Lefteris Dragasakis/Documents/GitHub/FTC_ADVANCED/FtcRobotController/LICENSE>) |
| Existing opportunity backlog and earlier graph snapshot | [feature opportunities](<C:/Users/Lefteris Dragasakis/Documents/GitHub/FTC_ADVANCED/FTC_TEAM_FEATURE_OPPORTUNITIES.md>), [graph report](<C:/Users/Lefteris Dragasakis/Documents/GitHub/FTC_ADVANCED/graphify-out/GRAPH_REPORT.md>) |

## Sources

All web sources were accessed on 9 September 2026. “Undated” means the page did not expose a reliable publication date; it does not imply an old or current release. Reddit and forum dates identify discussions, whose later replies can differ in date. Source descriptions below state the claims used rather than treating author claims as independent benchmarks.

[^1]: ACME Robotics. [FTC Dashboard — Features](https://acmerobotics.github.io/ftc-dashboard/features.html). Undated, current documentation. Telemetry, configuration semantics, camera support, and browser-gamepad limitations.

[^2]: ACME Robotics. [FTC Dashboard — Getting Started](https://acmerobotics.github.io/ftc-dashboard/gettingstarted.html). Undated, current documentation. Ordinary installation versus dashboard-development prerequisites.

[^3]: ACME Robotics. [ftc-dashboard repository](https://github.com/acmerobotics/ftc-dashboard). Current README. Feature list, CSV export, and installation model.

[^4]: Lazar / ByteForce 19234. [Panels](https://panels.bylazar.com/). Undated; site displays v1.0.12. Current advertised features, including webcam streaming and offline documentation.

[^5]: FTControl. [ftcontrol-panels repository](https://github.com/ftcontrol/ftcontrol-panels). Current README. Integrated dashboard scope and plugin architecture.

[^6]: Pedro Pathing. [Choosing a Dashboard](https://pedropathing.com/docs/pathing/dashboard). Undated, current documentation. Panels quickstart default and Pedro-specific tuning compatibility.

[^7]: ACME Robotics / Road Runner. [Tuning](https://rr.brott.dev/docs/v1-0/tuning/). Undated, Road Runner 1.0 documentation. Dashboard use within the official tuning workflow.

[^8]: Pedro Pathing. [PedroPathing vs Roadrunner](https://pedropathing.com/docs/pathing/pedro-v-roadrunner). Undated, current comparison. Logging and visualizer ecosystem descriptions only; vendor-side comparative performance assertions are not adopted here.

[^9]: Mechanical Advantage, Team 6328. [AdvantageScope repository](https://github.com/Mechanical-Advantage/AdvantageScope). Current README. Supported data sources, analysis features, and separation from AdvantageKit.

[^10]: Mechanical Advantage. [AdvantageScope — Video](https://docs.advantagescope.org/tab-reference/video/). Undated, current documentation. Alignment workflow, frame conversion, FFmpeg, and sound limitation.

[^11]: Mechanical Advantage. [AdvantageScope Lite](https://docs.advantagescope.org/more-features/advantagescope-lite/). Undated, forward-looking v27.x documentation. Systemcore scope, omitted features, and unofficial-port boundary.

[^12]: j5155. [AdvantageScope Lite for FTC](https://github.com/j5155/AdvantageScope-Lite-FTC). Current README. Unofficial Control Hub integration and advertised installation/data-source support; not independently runtime-tested.

[^13]: PsiLynx. [PsiKit repository](https://github.com/PsiLynx/PsiKit). Current accessible README, undated. FTC port and explicitly noted uncertainty about README freshness versus other ecosystem documentation.

[^14]: Koala-Log. [Koala-Log repository](https://github.com/Koala-Log/Koala-Log). Current README. WPILOG output, annotations, and ADB retrieval tooling.

[^15]: Kully and community participants. [Introducing KoalaLog — An FTC Logger Producing AdvantageScope-Compatible .wpilog Files](https://www.chiefdelphi.com/t/introducing-koalalog-an-ftc-logger-producing-advantagescope-compatible-wpilog-files-looking-for-feedback-and-testers/503204). Chief Delphi, 15 June 2025–26 May 2026. Maintainer announcement, reported use, documentation correction, and macOS request.

[^16]: Dairy Foundation. [Sloth repository](https://github.com/Dairy-Foundation/Sloth). Current README, undated. Hot-reload role, full-install caveats, and documented Dashboard/Panels variants; advertised speed is not used as a benchmark.

[^17]: MonsieurLazar and r/FTC participants. [Panels 1.0 Release](https://www.reddit.com/r/FTC/comments/1n0sb5i/panels_10_release/). 26–28 August 2025. Demand for meaningful comparisons and correction of competitor-feature claims.

[^18]: r/FTC participants. [Help with Dashboard](https://www.reddit.com/r/FTC/comments/1r95p22/help_with_dashboard/). 19–21 February 2026. Reported Panels/Pedro installation and connection confusion; proposed resolution is not an independently reproduced fix.

[^19]: r/FTC participants. [What problems do you have programming wise?](https://www.reddit.com/r/FTC/comments/1qvfs4a/what_problems_do_you_have_programming_wise/). February 2026. Self-reported iteration delays and integration friction.

[^20]: r/FTC participants. [Need help with FTC Dashboard](https://www.reddit.com/r/FTC/comments/18g8fwc/). 12 December 2023. Historical tutorial/setup confusion; not a current defect claim.

[^21]: r/FTC participants, including an ARES project author. [Control Hub emulator discussion](https://www.reddit.com/r/FTC/comments/1vt16d3/i_made_a_rev_control_hub_emulator_with_a_lot_of/). 19–20 August 2026. Self-reported ARES scope and unreleased/multiple-repository setup. [ARES 23247 public repositories](https://github.com/ARES-23247) corroborate project existence, not full functionality or third-party adoption.

[^22]: The Allsparks, FTC 36117. [TRACE repository](https://github.com/The-Allsparks/TRACE). Current README, undated. Proposed gradual-adoption design and explicit implementation/hardware-testing limitations.

[^23]: jackulau. [ftcMCP repository](https://github.com/jackulau/ftcMCP). Current README, undated. FTC-specific project inspection, knowledge, and code-validation tooling; no adoption estimate.

[^24]: FIRST. [BIOBUZZ Competition Manual](https://ftc-resources.firstinspires.org/ftc/game/manual), V0, 31 July 2026; section 12.7, R704, printed p. 84, and section 12.9, R901, printed p. 87. Version identified through [Current Game and Season Materials](https://ftc-resources.firstinspires.org/ftc/game). Live links can change to newer versions; this assessment applies to the retrieved V0 wording.

[^25]: FIRST Staff. [Control System Update — FIRST Tech Challenge Edition](https://community.firstinspires.org/control-system-update-first-tech-challenge-edition). 8 December 2025. Announced 2027–28 introduction, legacy/hybrid transition, and Driver Station development direction.

[^26]: Open Source Initiative. [The MIT License](https://opensource.org/license/mit). Undated, canonical license text. Permissions, notice requirement, and permissive licensing implications.

[^27]: Westside Robotics / FIRST Tech Challenge Wiki. [Datalogging](https://github.com/FIRST-Tech-Challenge/FtcRobotController/wiki/Datalogging). Last displayed edit 26 December 2022. Java/OnBot/myBlocks teaching approach, CSV retrieval, and spreadsheet analysis; historical tutorial rather than verified current setup.

[^28]: r/FTC participants. [not consistent outake shooter](https://www.reddit.com/r/FTC/comments/1p1ngn8/not_consistent_outake_shooter/). Thread initiated November 2025, with later season replies. Concrete need to graph shooter speed and relate it to timing/mechanical changes; technical suggestions are not treated as verified diagnoses.

[^29]: rb4774. [Ability to change rlog port in UI preferences, AdvantageScope issue #476](https://github.com/Mechanical-Advantage/AdvantageScope/issues/476). Opened 23 February 2026. Self-reported FTC PsiKit/Limelight port conflict; current fix status not established.
