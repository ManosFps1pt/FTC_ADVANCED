# FTC Advanced Frontend Design

**Status:** product and interaction specification  
**Scope:** the web Driver Station, robot-development workbench, recording
workflow, and replay viewer.  
**Primary principle:** the interface follows the operator's intent, while robot
health and the stop action remain visible everywhere.

## 1. Product definition

FTC Advanced is a local, controlled-test workspace for operating an FTC robot,
configuring it, exercising one safe mechanism at a time, recording a run, and
understanding the result. It is not a page full of every available number. A
student arrives with an objective—drive, tune, diagnose, configure, or
review—and the product takes them to the smallest safe workflow that completes
that objective.

The normal path through the product is:

```text
intent -> confirm robot readiness -> perform focused task -> see live result
       -> stop/finish safely -> save, upload, or review the evidence
```

The existing system is already the foundation for this product:

- Robocol Driver Station control, OpMode lifecycle, physical and virtual
  gamepads, and live FTC telemetry;
- hardware-configuration read/edit/save/activate workflows;
- a separate structured TCP telemetry stream with catalog/schema, motors,
  loop time, gamepad frames, recording, and WebSocket fan-out;
- a versioned typed debugger manifest, parameters, commands, safety state,
  free-spin motor tool, watchdog, and command acknowledgement flow;
- direct scrcpy and Android-camera recording, camera device roles and status,
  video/telemetry session binding, upload/discard choices; and
- a live telemetry lab with traces, motor cards, gamepad replay, a timeline,
  and dynamically advertised TCP command forms.

The frontend must organize these existing capabilities into a single mental
model. It must not imply that an unimplemented robot-side capability exists.

## 2. Design principles

1. **Intent before screens.** Navigation names jobs people actually have;
   generic technology terms belong in supporting detail.
2. **Safety never changes location.** Connection, OpMode state, battery,
   telemetry freshness, recording state, and stop stay in the shell while the
   workbench changes.
3. **The robot is authoritative.** The UI shows requested, accepted, applied,
   and measured values as separate states. A pressed button or moved slider is
   not proof of physical motion.
4. **Focused workbench, not a mega-dashboard.** A tune or test task displays
   only the controls and evidence relevant to that job. Users can expand
   diagnostics when needed.
5. **Safe defaults and obvious recovery.** Motion starts disabled; dangerous
   actions state their preconditions; a bad connection or stale data makes the
   reason and recovery action clear.
6. **Record evidence, not just video.** A recording couples the OpMode,
   telemetry schema and samples, operator inputs, command/parameter changes,
   events, and camera media when available.
7. **Seasonal adaptability.** Robot-side manifests define mechanisms, tools,
   parameters, commands, telemetry, risk, and limits. The shell and generic
   controls work without hard-coding this season's turret, intake, or lift.

## 3. The persistent safety shell

Every route, including configuration, replay, and debugger pages, has one
fixed desktop header. On a smaller screen it becomes a fixed compact strip;
the emergency stop remains immediately reachable without opening a menu.

```text
┌ FTC Advanced | RC ● connected | Data ● 38 ms | 12.61 V | TeleOp RUNNING
│ Camera REC 00:34 | Recording ● | [ STOP ROBOT ] | alerts
└────────────────────────────────────────────────────────────────────────
```

### 3.1 Always-visible health contract

The shell displays, at minimum:

| Item | Required presentation | Source / behavior |
| --- | --- | --- |
| Robot Controller connection | connected, connecting, disconnected, or error; host and ping on detail | current Driver Station state |
| Data/debug connection | connected, listening, unavailable, or stale | structured TCP service and selected debugger state |
| Battery voltage | numeric value, color band, last-sample age | standard health telemetry from the RC |
| OpMode | selected/active name and `NOT_STARTED`, `INIT`, `RUNNING`, `STOPPED`, or fault | Driver Station lifecycle state |
| Telemetry freshness | live age, sample rate, gaps/drops when present | structured stream; never silently show old data as live |
| Camera and recording | preview/ready/recording/finalizing/error plus elapsed time | camera coordinator and recording session |
| Safety fault | highest-severity, actionable fault first | DS errors, debugger safety state, robot events, and recording/camera failures |
| Stop | persistent red `STOP ROBOT` action | the normal Driver Station stop path; does not depend on TCP |

The header must update live status in **under one second** end-to-end under a
healthy connection. The target is 4–10 UI updates per second from WebSocket
state; polling is fallback only. A displayed live value includes its age. At
750 ms without a new health sample it becomes **aging**; at 1,000 ms it becomes
**stale** and is visually distinct from a measured zero or an unavailable
sensor. A stale battery value must display `12.61 V · stale`, not look current.

This is an explicit data requirement: every compatible OpMode must publish the
standard health packet (voltage, robot time, OpMode state, and stream health)
at least once per second. The backend keeps the last value and timestamp but
does not fabricate freshness. If a non-debug OpMode cannot publish voltage,
the shell says `Battery unavailable for this OpMode`.

### 3.2 Stop and fault behavior

- `STOP ROBOT` is enabled whenever a Driver Station stop request can be sent;
  it remains visible during modal dialogs and on all workbench pages.
- Pressing it sends the Driver Station lifecycle stop immediately, neutralizes
  locally forwarded gamepad state, and requests camera finalization. It does
  not wait for a debug-command response.
- A tool-level `Stop` stops only that tool and reports robot confirmation;
  it never replaces the global robot stop.
- An OpMode exception, data loss, watchdog fault, rejected command, camera
  failure, or low-battery threshold creates a persistent alert. Alerts explain
  what happened, when, which source reported it, and the next safe action.
- Toasts are for confirmations; safety faults remain in the alert area until
  cleared, superseded, or resolved.

### 3.3 Persistent workspace rail

The desktop shell also includes a fixed left rail. It remains in the same
position and keeps the same width, order, and visual language on every route;
the active workbench changes only in the main content area. The rail is not a
page-specific dashboard and must not be recreated separately by Telemetry Lab,
Debugger, Configure Robot, or replay views.

The rail contains four evenly sized, evenly spaced modules:

1. **DS connection** — Robot Controller address and connection detail;
2. **Camera stream** — the selected camera preview or an explicit unavailable
   state;
3. **Limelight stream** — the Limelight preview or an explicit unavailable
   state; and
4. **ADB status** — Control Hub, Android camera, and device-role status.

The modules keep stable slots even when a source is disconnected. Empty states
explain what is unavailable and how to recover; they do not collapse the slot
or shift the other modules. The rail may show compact status treatments, but
global stop, battery, OpMode, telemetry freshness, and recording state remain
owned by the top safety shell. On narrow screens the rail becomes a fixed
compact strip or a horizontally scrollable shell region without changing the
order of the four modules.

## 4. Information architecture

The primary navigation is action-oriented. Each item opens a workbench while
the safety shell remains intact.

```text
Home / Ready to work
├── Operate
│   ├── Choose and run OpMode
│   ├── Drive station and gamepads
│   └── Camera + recording controls
├── Tune & Test
│   ├── Debugger tool tree (manifest supplied)
│   ├── Mechanism-specific tools: turret, shooter, lift, intake, drive, ...
│   └── Safe generic tool forms and diagnostics
├── Analyze
│   ├── Live telemetry
│   ├── Current-run instant replay
│   └── Saved recording library and full replay
├── Configure
│   ├── Hardware configurations
│   └── Camera / device roles and capture settings
└── System
    ├── Connection details and logs
    ├── Recording upload queue
    └── Preferences / thresholds
```

`Home` is a quiet readiness and recent-work view, not another dashboard. It
answers “Can I safely start?” and offers intent cards such as **Run an OpMode**,
**Tune a mechanism**, **Test a motor**, **Review the last run**, and
**Configure hardware**. Cards are enabled, disabled, or annotated from actual
preconditions. For example, “Tune turret” opens the Turret tool only when it
is advertised by the active Debugger manifest; otherwise it says what must be
started first.

The current routes—Driver Station, Configure Robot, Telemetry Lab, and
Debugger—are retained during migration, but become the corresponding
workbenches above rather than separate product identities.

## 5. Intent flows

### 5.1 “I want to run the main OpMode and record how fast the robot is.”

1. From **Operate**, choose the main OpMode. The readiness panel confirms RC
   connection, voltage/freshness, gamepad assignment, data-stream availability,
   camera selection, and free storage. Missing optional camera capability can
   be acknowledged; unavailable telemetry is called out because speed cannot
   later be measured.
2. Choose recording mode: `Telemetry only`, `Telemetry + camera`, or `No
   recording`. Show the selected camera, lens, frame rate, preview/readiness,
   and expected storage use. Do not silently start a camera the user did not
   select.
3. `Initialize` starts the normal OpMode lifecycle and binds the intended
   camera recording to the telemetry session. `Start` is prominent only after
   successful initialization. Gamepad inputs begin neutral.
4. While driving, the workbench prioritizes driver inputs, OpMode state,
   selected speed/pose/drive metrics, camera state, recording elapsed time, and
   a compact “live evidence” strip. It does not bury the stop action.
5. At `Stop`, recording finalizes. The review card reports telemetry samples,
   video status, gaps/drops, final duration, and session ID. The user can open
   instant replay, upload (optionally keep local copy), retry a failed upload,
   or discard with an explicit irreversible confirmation.
6. In **Analyze**, speed is shown only if the active schema supplied a valid
   speed/velocity signal. The UI names the signal and unit, plots it over time,
   and pairs it with video and gamepads. It never invents “robot speed” from a
   motor encoder unless the robot supplied that as a defined derived channel.

### 5.2 “I want to tune my turret.”

1. From **Tune & Test**, choose `Turret` then the robot-advertised task, such
   as `Tune PID`, `Home`, `Position test`, or `Diagnostics`.
2. The tool begins with an intent-specific preflight: debugger TCP online,
   correct OpMode/tool instance, voltage and freshness, tool safety state,
   physical clear-space acknowledgement where the robot requires it, and any
   current limit/home-switch prerequisites.
3. The tool shows the live target, measured position/velocity, error, motor
   output, relevant limit-switch state, and graph. It presents parameters with
   unit, bounds, step, default/current value, update policy, and persistence
   behavior supplied by the manifest.
4. Parameter changes clearly show `Requested`, then `Accepted`, `Applied`,
   and the measured response. Temporary changes are the default. `Save
   constants` is a distinct, confirmed action, available only when the robot
   advertises persistence and is in an allowed lifecycle state.
5. Any motion uses a specific enable/deadman or run action, obeys robot limits,
   reports watchdog state and applied output, and stops on pointer release,
   timeout, tool exit, TCP loss, OpMode stop, or global stop.
6. The test creates annotations/events in the recording so the analyst can see
   exactly when gains, targets, and commands changed.

Turret is an example, not a frontend special case. A tool definition supplies
its labels, controls, telemetry channels, risk class, preflight content, and
custom visualization; generic panels render what is declared.

### 5.3 “I want to test one motor.”

The existing free-spin tool becomes a **Motor Test** task under Tune & Test.
It retains the manifest tree and locked drivetrain/mechanism entries, but
frames the process as: select a registered motor → inspect risk/limit → confirm
clear space → hold to run → inspect requested/applied power, current, velocity,
and watchdog → release/stop. Drivetrain testing requires the extra floor
acknowledgement defined robot-side; mechanism entries without actuation remain
read-only with their disabled reason.

### 5.4 “I need to diagnose why the robot did something.”

From **Analyze**, select the current run or a saved recording. The default is
a synchronized, read-only timeline with events and explicit recording gaps.
Users can choose up to a reasonable number of comparable numeric traces,
inspect motor cards, move a playhead, view corresponding gamepad state and
video, and filter events such as OpMode lifecycle, command result, parameter
change, safety block, camera issue, or user annotation. A replay session must
never expose live robot controls.

### 5.5 “I need to set up or change hardware.”

From **Configure**, show configuration files and the active configuration,
then retain the existing scan-derived portal/hub/device workflow. The frontend
must preserve scanned serial numbers and hub addresses, support supported
motor/servo/digital/PWM/analog/I2C devices and webcams, validate duplicate
hardware-map names and I2C-address collisions, and create XML only when
saved. `Save & activate` is disabled during an active OpMode and requires a
clear confirmation because it changes the robot hardware map.

## 6. Workbench requirements

### 6.1 Operate

- RC address/connect/disconnect with errors that preserve the last useful
  detail; OpMode list, selection, initialize/start/stop lifecycle; and status
  updates without page refresh.
- Physical-controller discovery, assignment to Driver 1/2, reassignment
  shortcuts, neutralization on loss/reassignment, and virtual gamepad fallback
  with hold-to-press controls and `Release all`.
- A compact live FTC telemetry panel and a clear exception panel containing the
  Robot Controller stack trace and stop guidance.
- Camera/device role selection, direct scrcpy preview, direct-camera settings
  (lens, aspect ratio, FPS, flip), native Android-camera mode, device battery
  and storage when available, camera stop, and verified phone-copy deletion.
- Recording state and upload progress that remain visible during operation.

### 6.2 Tune & Test

- Manifest-driven navigator: folders are navigation only; enabled tools can be
  selected; locked tools remain visible with a robot-provided reason.
- A selected-tool workbench with title, instance ID, lifecycle/session state,
  risk class, output/safety/watchdog state, parameter controls, typed command
  forms, progress, events, telemetry cards, and tool-specific custom content.
- Generic commands validate required fields, types, bounds, enums, and user
  acknowledgement before send. Status is correlated by request ID and remains
  visible until terminal response or timeout.
- Only robot-advertised commands are sent. The browser never opens raw TCP to
  the robot and never executes arbitrary method names or scripts.
- The existing Motor Lab's hold-to-run behavior, 500 ms parameter TTL, bounded
  ±0.35 power, stop response confirmation, and watchdog visibility are the
  baseline for actuator-oriented tools—not an optional visual convention.

### 6.3 Analyze

- A live session view with catalog-driven signal labels, units, unavailable
  values, motor telemetry, selected traces, loop-time monitor, gamepad frames,
  an instant-replay timeline, and dynamic TCP function controls when live.
- The motor telemetry strip must support up to eight motors on one desktop row
  without introducing a second page or a bulky carousel. Each motor slot shows
  velocity, commanded power, and current with stable labels and units. The
  current value uses a continuous semantic scale: low draw is green, rising
  draw transitions through orange, and high draw reaches red. The scale is
  expressed with text and numeric values as well as color so it is not
  color-only.
- A total-current-draw monitor sits adjacent to the motor strip and uses the
  same green → orange → red scale, with the threshold source and unit visible.
  Recovered layout space is used for a compact performance graph inspired by
  instrumentation tools: bounded height, thin traces, readable axes, and no
  decorative chrome that competes with live values.
- Gamepad telemetry uses a compact Xbox-layout broadcast overlay beneath the
  trace selector while the replay graphs take the full width of the former
  input panel. The controller silhouette is functional rather than decorative:
  stick positions, trigger travel, pressed face buttons, and connection state
  are shown in place, with a compact second-driver status. Keep the surface
  intentionally secondary and within the existing narrow column; the trace
  workbench has priority.
- A recording library containing session time, OpMode, robot/build/configuration
  metadata where provided, duration, camera availability, sample count,
  telemetry/video gaps, upload state, and retention state.
- Replay controls: play/pause, live-follow, time scrub, zoom/window choice,
  video synchronization, selected traces, derived metrics when defined, and
  event/command annotations. Queries should downsample for display while the
  raw recording remains intact.
- Comparison of two recordings is a later feature, but the information model
  must support a baseline/candidate pair and identify schema incompatibility
  rather than comparing mismatched signals.

### 6.4 Configure and System

- The configuration editor described in section 5.5.
- Connection inspector: RC host/ping/errors; TCP listener, peer, handshake,
  protocol/schema revision, queue/dropped-sample status; and discovery
  troubleshooting. These are diagnostics, not primary navigation.
- Recording queue: retained, uploading, uploaded, failed, and discarded state;
  progress; retry; upload-and-keep-local; local deletion only after a confirmed
  durable copy; and precise deletion scope.
- Local preferences: display units, low-battery warning threshold, reduced
  motion/transient effects, and any team-approved camera defaults. Robot
  safety limits do not live in browser preferences.

## 7. Data, state, and visualization rules

### 7.1 State language

Use consistent state labels and colors throughout the product:

- gray: not connected, unknown, unavailable, or inactive;
- blue: connecting, initializing, queued, or informational;
- green: connected, ready, confirmed, or healthy;
- amber: aging, warning, pending confirmation, or constrained;
- red: stopped, fault, rejected, stale, or unsafe.

Color is never the only signal: text, icon shape, and accessible status labels
are always present. Numerical values retain units and sensible precision;
unavailable is `—`, never `0`.

### 7.2 Live-stream behavior

- WebSocket is the normal live transport. HTTP endpoints populate initial and
  recovery state; they must not overwrite a newer ordered live telemetry frame.
- Render at a bounded cadence and batch high-rate samples so charting cannot
  degrade driver input or the safety shell.
- Preserve robot monotonic timestamps, arrival time, sequence number, and
  declared schema. Surface dropped samples and `Gap` records; never interpolate
  across a known gap.
- Charts show the signal label, unit, trace color, exact cursor value/time,
  and time basis. They must tolerate missing data and changing schemas.
- Browser history is bounded. Long-term analysis reads recording data rather
  than retaining an unbounded live array.

### 7.3 Recording lifecycle

```text
armed -> session bound -> recording -> finalizing -> ready to review
      -> uploading -> uploaded / retryable failure / retained locally / discarded
```

An unexpected disconnect changes the state to an explicit partial/finalizing
condition; it does not silently mark the recording complete. The review UI
shows which artifacts were actually saved. Upload is a transfer decision, not
a proxy for whether the robot ran.

## 8. Safety and permission requirements

- The FTC Driver Station is authoritative for lifecycle and emergency stop.
  The structured TCP channel is for typed, discrete debug commands and
  observability; its loss must never prevent the normal stop path.
- Backend and robot independently validate selected node/tool instance, command
  schema, values, TTL, lifecycle, lease/acknowledgement where supported, risk
  policy, and physical safety state. Client-side validation improves feedback
  but is not a safety boundary.
- All controls that may move hardware declare risk and consequences before use.
  The interface does not hide a dangerous tool merely because it is disabled.
- Navigation away, browser blur/close, tool replacement, command expiry,
  socket loss, watchdog failure, OpMode stop, and global stop must leave
  actuator-producing tools in their robot-defined safe state.
- Commands and parameter changes are auditable in the recording with request
  ID, result, robot time, operator/source when available, and effective value.
- This custom web station is for controlled testing unless current FTC rules
  have been verified for the intended event context.

## 9. Component and extensibility model

The app owns the shell, navigation, live-status store, safety notices,
recording lifecycle, command status, and reusable visual primitives. A robot
mechanism owns its declared tools and specialized user interface.

Reusable primitives include freshness-aware live value, status pill, health
card, command-result timeline, bounded numeric field, enum/boolean field,
hold-to-run button, acknowledgement dialog, preflight checklist, trace chart,
event log, recording state card, and tool lifecycle panel.

A tool registration should be able to declare conceptually:

```text
id, mechanism, label, description, risk, enablement/preflight,
parameters, commands, telemetry channels, cleanup behavior,
recording annotations, and optional custom workbench renderer
```

This allows a future `Turret / Tune PID` workbench to be richer than a generic
form while still inheriting state freshness, safety, stop behavior, recording,
and accessibility from the app.

## 10. Accessibility and responsive behavior

- Every action is keyboard reachable; hold-to-run has an equivalent safe
  keyboard interaction only when it can preserve deadman semantics.
- Controls have visible labels, error explanations, units, and sufficient
  contrast. Live announcements are rate-limited so screen readers are not
  flooded by telemetry.
- The desktop layout prioritizes a laptop at the field/pit table. At narrow
  widths, workbench panels stack; charts remain horizontally scrollable or
  reduce density; the safety strip and stop action stay fixed.
- Motion/flash used for live state respects reduced-motion preferences. Do not
  convey a safety event only through animation or sound.

## 11. Acceptance criteria

The official frontend design is satisfied when:

1. Every route shows the persistent safety shell and global stop action.
2. RC status, data status, battery, OpMode state, telemetry age, camera, and
   recording status visibly update within one second or explicitly become stale.
3. A user can complete the run-and-record flow and immediately inspect an
   honest speed/telemetry timeline, video status, input frames, and gaps.
4. A user can find a manifest-advertised tool such as turret tuning, complete
   its preflight, make a temporary bounded change, see requested/accepted/
   applied/measured state, stop it, and retain the result in a recording.
5. The free-spin motor test preserves deadman, robot-side bounds, watchdog,
   confirmation, and global-stop behavior.
6. Configuration changes remain scan-derived, validate locally, cannot save
   during an active OpMode, and require confirmation to activate.
7. Disconnection, stale telemetry, a rejected command, or recording failure is
   unmistakable and offers a safe recovery path.
8. The interface can render a newly registered mechanism/tool with generic
   components and no app-wide rewrite.
9. Replay is read-only and cannot issue a robot command.

## 12. Implementation order

1. Build the shared live-status store and persistent safety shell first,
   including the standard health telemetry contract and stale-value treatment.
2. Reframe current routes under the intent-oriented navigation without removing
   existing Driver Station, configuration, telemetry, debugger, or camera
   functionality.
3. Add the readiness/home flow and a unified run-and-record lifecycle.
4. Generalize the debugger into manifest-driven Tune & Test workbenches;
   preserve the existing Motor Lab as the first concrete implementation.
5. Promote the current telemetry timeline into live/replay Analyze views and
   add recording-library metadata and event annotations.
6. Add mechanism-specific workbenches (starting with the team’s real turret)
   only after the robot exports their typed tool definitions and telemetry.

This ordering makes the always-visible under-one-second health requirement the
first implementation concern, not a cosmetic status bar added after controls
and graphs are built.
