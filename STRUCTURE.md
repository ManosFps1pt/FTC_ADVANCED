# FTC Advanced application structure

## Purpose

FTC Advanced is a workspace that joins independent robot-development systems into one coherent session. It is not a single dashboard, and it is not a replacement for every tool at every moment.

The product must always answer three questions before it tries to show any detail:

1. **What connection(s) are live right now?**
2. **What can this operator safely do through this application?**
3. **What is the most useful next place to work?**

The answer changes as the Robot Controller, the official Driver Station, a TCP-capable OpMode, the camera phone, and local services come and go. The application structure therefore follows *capabilities discovered from live state*, not a fixed set of permanently visible pages.

This document deliberately describes structure and behavior. It does not prescribe the visual design of the existing Paper file.

## The central model: one workspace, two primary robot links

The two links that determine the active work mode are independent:

```text
FTC Advanced laptop
    |
    |-- Robocol / Driver Station session ---- Robot Controller
    |       Grants control, OpMode lifecycle, configuration, and DS telemetry.
    |
    `-- Structured TCP data session --------- TCP-capable OpMode
            Grants high-rate samples, advertised tools, debug control,
            durable telemetry recording, and live replay.
```

Neither link implies the other:

- A team can drive through the official Driver Station while a TCP-capable OpMode sends data to FTC Advanced.
- FTC Advanced can control an OpMode that does not implement the structured TCP protocol.
- Both links can be live for an FTC Advanced-controlled, fully instrumented run.

The rest of the product sits beside these links. Camera capture, recordings, replay, MCP, and Oracle are important, but they must not make the application falsely claim that robot control or live data is available.

## Terms

| Term | Meaning |
| --- | --- |
| **Driver Station (DS) link** | FTC Advanced's own authenticated Robocol session to the Robot Controller. It is the only link that grants this app lifecycle, gamepad, and configuration authority. |
| **TCP link** | A structured, valid TCP protocol session initiated by a running OpMode. A listening port alone is not a live TCP capability. |
| **Fresh sample** | A valid TCP sample received recently enough to be rendered as live. Its source timestamp and laptop receipt timestamp must both be retained. |
| **Capability** | A specific permitted action or view, such as `runOpMode`, `viewLiveTelemetry`, or `tuneTurret`. Capabilities are more precise than pages. |
| **Mode** | The current topology of the two primary robot links. Modes are derived automatically; users do not select them as a preference. |
| **Surface** | A route, panel, command, or card exposed by the application. A surface appears only when its required capability is present. |
| **Session** | A bounded robot activity identified by lifecycle state and/or a structured TCP session UUID. A session may have telemetry, video, both, or neither. |

## System map

The following systems should remain independently owned. The home page and shared shell aggregate their state; they must not turn them into one giant feature.

| System | Authority / source of truth | What it contributes | What it must not imply |
| --- | --- | --- | --- |
| Web Driver Station clone | Robocol client and Robot Controller | RC connection, lifecycle state, OpMode catalog, gamepad forwarding, DS telemetry, configuration operations | That TCP telemetry, debugger tools, or recordings exist |
| Robot configuration | Robot Controller's loaded configuration plus safe configuration API | Read, edit, validate, save, and activate supported hardware configurations | Permission to change hardware while an OpMode is active |
| Structured TCP server | Laptop TCP listener plus valid protocol handshake | Samples, catalog, events, advertised commands, manifest, debug state, recording session ID | Driver Station control authority |
| Telemetry Lab | Valid, fresh TCP catalog and snapshots | High-rate graphs, current values, gamepad frames, tools advertised by the OpMode | That every OpMode supports the protocol |
| Debugger / mechanism tools | TCP debug manifest, selected tool, watchdog, and safety state | Robot-advertised test and tuning tools | A generic, always-present motor-control page |
| Android camera capture | Assigned ADB phone or direct scrcpy capture service | Preview, capture readiness, recording, finalization, and video artifacts | A live Robot Controller connection or paired telemetry session |
| Recording coordinator | Raw `.ftclog`, camera manifest, upload state | Durable session artifacts, pairing, upload/retry/discard state | That an unpaired video has telemetry, or vice versa |
| Replay viewer | Saved session library | Read-only replay, trace selection, video synchronization, historical analysis | That current robot state is live |
| MCP server | Local backend adapter | Read-only structured status, latest bounded telemetry, catalog, debugger state for an AI host | Direct robot control or a second owner of TCP state |
| Oracle instance | Hosted analysis/service boundary | Uploaded recording library, analysis, and future assistant workflows | Authority over local robot safety or an active control session |

### Current implementation boundaries

Structure must describe what is real now without hardcoding today's temporary limits into the long-term navigation.

- The web Driver Station already owns Robocol connection, OpMode lifecycle, gamepad forwarding, ordinary DS telemetry, RC exception reporting, and safe configuration operations.
- The laptop TCP service already listens independently of the Driver Station, validates the structured protocol, receives catalogs/snapshots/events, keeps bounded live replay history, and writes durable `.ftclog` streams.
- Only OpModes that include the structured TCP client can create a live Telemetry Lab session. An ordinary OpMode without that client is a valid Driver Station-only use case, not a broken telemetry state.
- Robot voltage is currently an instrumented TCP signal (`robot.voltage`) when an OpMode publishes it; it is not universal Driver Station state. The shell must show it only with its TCP source and age.
- The current debugger is manifest-driven. Its concrete live test is a bounded free-spin motor tool; drivetrain and mechanism entries can be locked, and a turret tuner must not be shown until a future OpMode actually advertises it.
- The Android phone camera service and direct scrcpy capture are independent from Robocol. A capture can begin before a TCP session is known, then bind to the first telemetry session; an unpaired video must be preserved and labeled, not discarded or mislabeled as paired.
- Raw telemetry, video, manifest, and upload finalization have distinct states. Replay is read-only and can work without any current robot connection.
- The implemented MCP server is a separate, read-only adapter over the local backend. It exposes bounded DS/TCP/telemetry/debug status, not robot control, configuration writes, video control, or replay mutation.
- The remote/Oracle-facing path is currently recording upload and hosted replay/analysis infrastructure. Its health can limit remote analysis or upload, but it never grants local robot authority.

## Product information architecture

Pages exist for a job, not because a subsystem happens to exist. The shared shell is stable; navigation and contents are capability-gated.

```text
Home
├── Control Station                 [requires DS link]
│   ├── Connect / controller setup
│   ├── OpMode selection and lifecycle
│   ├── Gamepads and Driver Station telemetry
│   └── Camera capture setup when control-led capture is appropriate
├── Configure Robot                 [requires DS link + safe stopped lifecycle]
├── Telemetry Lab                   [requires fresh structured TCP]
│   ├── Live values and traces
│   ├── Command catalog             [requires advertised typed commands]
│   └── Mechanism tools             [requires matching manifest + safety]
├── Capture & recordings            [requires local capture/recording state]
├── Replay library                  [requires any saved session]
├── Assistant / Oracle              [requires its service, never robot control by default]
└── System & integrations
    ├── Connection diagnostics
    ├── MCP status
    ├── Upload status
    └── Local service health
```

The navigation should not show a disabled maze. If a live-only route has no required capability, remove it from the primary navigation and explain the reason on Home. Historical routes remain available when their artifacts exist: losing TCP must never make a completed replay disappear.

## The three main modes

There are exactly three primary work modes. They are derived from the usable state of the DS and TCP links, not from the route currently displayed.

```text
                         Fresh structured TCP
                         no                    yes

DS link live       Driver Station only     Both connections
DS link unavailable     --                 TCP connection only
```

The empty lower-left cell is a disconnected/setup state, not a fourth work mode. It is shown as a preflight or recovery state until at least one primary link is usable.

### Mode resolver

The resolver must use *usable* link state, not a raw socket flag.

```ts
type WorkspaceMode = "driver-station-only" | "tcp-only" | "both" | "setup";

const dsUsable = driverStation.transport === "connected"
  && driverStation.heartbeatFresh;

const tcpUsable = tcp.transport === "connected"
  && tcp.protocol === "accepted"
  && tcp.catalog !== null
  && tcp.lastValidSampleAgeMs <= 1_000;

const mode: WorkspaceMode = dsUsable && tcpUsable ? "both"
  : dsUsable ? "driver-station-only"
  : tcpUsable ? "tcp-only"
  : "setup";
```

The exact implementation can use different types, but the product rule matters: a TCP listener, an open socket without a valid protocol handshake, or a stale last sample must not unlock Telemetry Lab.

### 1. Driver Station only

**Definition:** FTC Advanced has a usable DS/Robocol session. There is no fresh, valid structured TCP session for the current activity.

This is the normal mode for an OpMode that does not implement the TCP protocol, a TCP server that has not been contacted, or ordinary Driver Station work.

**The operator can do:**

- connect or disconnect the Robot Controller;
- choose, initialize, start, and stop an OpMode;
- use physical or virtual gamepads;
- see ordinary Driver Station telemetry and RC errors;
- configure the robot when the lifecycle is safely stopped;
- prepare camera capture where the capture workflow is tied to the app-controlled lifecycle;
- use existing recordings and replay, if any exist.

**The operator cannot do through this mode:**

- open a live TCP graph, catalog, or command panel;
- operate an advertised debug/tuning tool;
- assume a recording will contain high-rate `.ftclog` data;
- see blank Telemetry Lab controls that appear to be malfunctioning.

**Required application behavior:**

- Keep **Control Station** and **Configure Robot** in primary navigation.
- Omit **Telemetry Lab** and live mechanism-tool routes from primary navigation.
- On Home, say exactly why: for example, “This OpMode has not opened a structured data session” or “Waiting up to 3 s for this OpMode's TCP handshake.”
- When an app-controlled OpMode becomes `INIT` or `RUNNING`, start an explicit TCP capability grace window. Do not label it unsupported on the first frame of lifecycle state.
- When the grace window expires without a valid handshake, resolve the active session as Driver Station only. Replace TCP cards with an optional developer-facing action such as **See how to add structured telemetry**; do not leave dead panes.

### 2. TCP connection only

**Definition:** FTC Advanced has a fresh, valid structured TCP session, but it has no usable DS/Robocol session.

The common interpretation is that teammates are controlling the robot through the official Driver Station while a TCP-capable OpMode is sending data to the laptop. It can also result from a DS-link failure. Those cases must be presented honestly.

**Important wording rule:** the application may know *“FTC Advanced cannot control the Robot Controller while TCP data is live.”* It cannot always prove *“the official Driver Station is controlling it.”* Unless the protocol provides an explicit contention signal, use text such as:

> Control through FTC Advanced is unavailable. Another Driver Station may be active; this workspace is observing the live TCP session.

If the DS stack reports a definitive exclusive-session/connection-conflict condition, the UI may upgrade the explanation to “Official Driver Station likely owns control.” A manual **Official DS in use** override is also valuable when a network failure is indistinguishable from contention.

**The operator can do:**

- observe live samples, events, robot-advertised telemetry, and gamepad frames;
- use read-only Telemetry Lab functions;
- review the live rolling replay and start from the latest fresh sample;
- use only TCP actions that are explicitly declared read-only by the active OpMode;
- observe recording, camera, upload, MCP, and Oracle status;
- open saved replays.

**The operator cannot do:**

- send gamepad data through FTC Advanced;
- select, initialize, start, or stop an OpMode through FTC Advanced;
- change hardware configuration through FTC Advanced;
- tune, test, or otherwise change robot output through a TCP mechanism tool;
- present a **STOP ROBOT** control that suggests this application has authority it does not have;
- silently start a capture that claims lifecycle synchronization if that synchronization depends on the app's DS lifecycle.

**Required application behavior:**

- If the user is on Control Station, Configure Robot, or another DS-authority route when the DS link becomes unavailable *and* TCP becomes fresh, automatically route them to **Telemetry Lab**.
- Show a persistent, calm observer-mode banner at the top of Telemetry Lab: “Observe only — Driver Station control is not owned by this app.”
- Hide Control Station and Configure Robot from primary navigation. They may remain in a secondary “Reconnect control” affordance, but must not look actionable.
- Keep the camera and recording status visible, but identify whether the current capture is **paired**, **unpaired**, **manual**, **recording**, or **finalizing**.
- Do not send teardown, neutral-gamepad, stop, or lifecycle commands merely because the app detected the mode. The app does not own control in this mode.

### 3. Both connections

**Definition:** FTC Advanced has a usable DS/Robocol session and a fresh, valid structured TCP session for the active activity.

This is the complete local development mode: the application can operate the robot and observe the instrumented OpMode at the same time.

**The operator can do:**

- run an OpMode from Control Station;
- use Driver Station controls while viewing high-rate telemetry in Telemetry Lab;
- start an explicitly configured run-and-record workflow;
- use robot-advertised tuning and diagnostic tools, subject to manifest, TTL, and safety gates;
- inspect camera status and pair video with the telemetry session;
- review prior recordings and send bounded read-only context to MCP/Oracle.

**Required application behavior:**

- Keep both Control Station and Telemetry Lab visible in navigation.
- Make the active relationship clear: **Control + observe** rather than two unrelated green badges.
- Offer a prominent, task-level route such as **Run an OpMode with recording**, which combines selection, capture preflight, lifecycle actions, and post-run replay handoff.
- Keep control and data authority separate in the UI. A failed TCP stream must not falsely mark the DS link disconnected; a DS failure must immediately remove lifecycle/gamepad authority even if live TCP values continue.
- When TCP disappears, resolve to Driver Station only and remove live Lab controls. When DS disappears while TCP remains fresh, resolve to TCP only and route to Telemetry Lab if the user is on a control-only route.

### Pairing is a safety qualifier, not a fourth mode

Topology alone is not enough to merge two live links. In Both mode, the backend must establish that the DS session and TCP session refer to the same robot and current run before it offers combined workflows such as **Run and record** or places TCP measurements beside DS lifecycle controls.

Use stable identity where the protocol provides it:

- Robot/Control Hub identity, not only an OpMode display name;
- structured TCP session UUID and connection/run epoch;
- active OpMode identity when it is authoritatively available from both sides;
- catalog/manifest revision for TCP tool instances.

If both links are live but do not pair, the topology remains Both while the association is `pending` or `mismatch`. The shell must say so, separate the evidence, block combined recording and robot-affecting tools, and never merge the two data streams by guesswork. This is a safety qualifier, not another user-facing work mode.

## Connection state is richer than mode

The three modes are intentionally simple. Each input must still expose enough detail to prevent misleading UI.

### Driver Station state

| State | Meaning | UI consequence |
| --- | --- | --- |
| `disconnected` | No app-owned Robocol session | No control/configuration capability |
| `connecting` | User initiated a connection; no authority yet | Show preflight progress, never controls |
| `connected-fresh` | Session and heartbeat are current | Grant DS capabilities allowed by lifecycle |
| `connected-stale` | Session exists but heartbeat freshness failed | Immediately withdraw control capability; retain diagnostic context |
| `unavailable-or-contended` | Connection cannot be established or was displaced while RC/network evidence remains | Show observer explanation; do not claim certainty without a definitive signal |
| `error` | Protocol, lifecycle, or RC failure | Expose reason and recovery; no control capability |

### TCP state

| State | Meaning | UI consequence |
| --- | --- | --- |
| `listener-starting` / `listening` | Laptop service is available but no robot session exists | Setup status only |
| `transport-connected` | A peer socket exists | Not enough to show Telemetry Lab yet |
| `handshake-pending` | Awaiting valid protocol hello/catalog | Show a concise waiting state |
| `live` | Valid handshake, catalog, and sample freshness are present | Grant Telemetry Lab and declared capabilities |
| `stale` | Last valid sample exceeds the live threshold | Freeze charts, label values stale, withdraw live actions |
| `unsupported-for-active-opmode` | An app-controlled OpMode passed its TCP grace period without a valid session | Hide live Lab surfaces; explain it is not a TCP-capable OpMode |
| `protocol-error` | Framing/contract validation failed | Keep a diagnostic record; do not treat raw packet data as live telemetry |

### Freshness contract

Connection badges are safety information, not decorative status.

- All state displayed as **live** must have a visible age or “updated just now” indicator derived from a monotonic receipt timestamp.
- The DS link and TCP link should update from backend events rather than a one-second browser poll.
- Target: a disconnect, transition to stale, or changed active mode reaches the shell in under one second under normal local conditions.
- Suggested thresholds: `live <= 1 s`, `stale > 1 s`, and `missing/disconnected` after the transport closes or a separately documented timeout. Use immediate withdrawal for known disconnects; use a short confirmation/debounce only when regaining availability to avoid mode flicker.
- Preserve the last value only as a clearly labeled snapshot: “last sample 3.4 s ago,” never as a current reading.
- Every metric must state its source where ambiguity matters: **RC/Robocol**, **TCP sample**, **camera service**, or **recording manifest**.

## Capability gates

Routes are a presentation of capabilities. The backend must remain the authority for whether an operation can run.

| Capability | Minimum condition | Additional guard |
| --- | --- | --- |
| `controlRobot` | DS link fresh | App owns the active DS session |
| `selectOrStartOpMode` | `controlRobot` | RC lifecycle allows it; selected name is advertised |
| `sendGamepad` | `controlRobot` | Session current; neutralize on loss of app-owned control |
| `stopAppOwnedOpMode` | `controlRobot` | Only when FTC Advanced actually owns the lifecycle session |
| `modifyConfiguration` | DS link fresh | Robot state stopped/not started; confirmation and XML/model validation |
| `viewDsTelemetry` | DS link fresh | Values have source and freshness state |
| `viewLiveTelemetry` | TCP state `live` | Valid catalog and fresh sample |
| `viewLiveReplay` | TCP state `live` | Bounded live history exists |
| `useAdvertisedTcpCommand` | TCP state `live` | Command appears in the current catalog/manifest; a mutating command additionally requires `controlRobot`, pairing, target-instance match, TTL, and safety approval. In absence of a declared read-only/mutating classification, deny it in TCP-only mode. |
| `useMechanismTool` | `controlRobot` + paired TCP debug manifest + ready state | Tool-specific risk, watchdog, acknowledgement, and exact tool-instance gates pass |
| `recordTelemetry` | Valid TCP session | Raw recorder healthy and session ID known |
| `recordCamera` | Camera assigned/healthy | Its synchronization preconditions are visible and satisfied |
| `reviewRecording` | Completed or recoverable local/remote artifact | Read-only; never requires a live robot link |
| `askMcpOrOracle` | Corresponding service healthy | Scoped data access; no implicit robot-control permission |

Capabilities must be recomputed at every relevant state change. Do not cache an enabled button from the previous mode.

## Routing and transition rules

### The core routing rule

Route automatically only when the current route has become impossible or unsafe. Do not force a user away from a valid page merely because a richer capability becomes available.

| Transition | Required behavior |
| --- | --- |
| Setup → Driver Station only | Keep Home or open Control Station only if the user explicitly initiated connection. Offer **Open Control Station**. |
| Setup → TCP only | Open Telemetry Lab automatically if the user was waiting on live data or a deep link targeted the Lab; otherwise make it the Home primary action. |
| Setup → Both | Keep the user's current safe route; Home offers the combined run-and-record flow. |
| Driver Station only → Both | Do not auto-jump. Add **Live TCP connected — open Telemetry Lab** and unlock combined actions. |
| Both → Driver Station only | If viewing a live Lab-only route, immediately route to Control Station or Home. Explain “This OpMode is no longer sending structured TCP data.” Remove Lab live cards and subscriptions. |
| Driver Station only → TCP only | If control is lost while TCP stays fresh, automatically route to Telemetry Lab. Show observer mode and remove DS controls. |
| Both → TCP only | Same automatic route to Telemetry Lab. Stop displaying app-owned lifecycle/gamepad actions immediately. |
| TCP only → Both | Stay on Telemetry Lab. Show a non-disruptive **Control Station available** action; never auto-start or seize control. |
| TCP only → Setup | Freeze the last live view as stale, then route to Home/recovery after the disconnected timeout. Keep replay links. |
| Any mode → setup because the local backend is down | Show a local-service recovery page. Never preserve stale controls as clickable. |

### Detecting use of the official Driver Station

The desired behavior is clear: when another Driver Station owns robot control and structured TCP data arrives here, this app should become an observer immediately.

The detection contract must distinguish fact from inference:

1. **Fact:** FTC Advanced's DS link is not usable.
2. **Fact:** a valid, fresh TCP session is arriving.
3. **Inference:** another Driver Station probably owns control.
4. **Definitive confirmation:** only available if the Robot Controller/Robocol protocol exposes an exclusive-session conflict, or the user marks an explicit override.

Recommended resolver input:

```text
ds.status                connected | stale | disconnected | conflict | error
ds.lastHeartbeatAt       monotonic timestamp
tcp.status               listening | handshake | live | stale | error
tcp.lastValidSampleAt    monotonic timestamp
tcp.sessionId            current structured session UUID
tcp.activeOpMode         optional, only if advertised by protocol
user.controlOwnerHint    auto | official-ds | ftc-advanced | unknown
```

When `ds.status` is `conflict` or `disconnected/stale` and `tcp.status` is `live`, resolve to TCP only. Phrase it as an observer transition unless the conflict is definitive. The current Robocol client can surface the concrete rejection `REJECTED_EXISTING_CONNECTION` (“Robot Controller already has another Driver Station”); that is a definitive reason to say that another Driver Station owns the control link. A user override must alter the copy and suppress unwanted reconnect attempts, but must never fabricate DS authority.

### Detecting an OpMode that does not support TCP

Only make the “Telemetry Lab vanishes” decision when FTC Advanced owns the DS lifecycle and can associate the expected TCP session with the selected OpMode.

1. User initializes or starts an OpMode through FTC Advanced.
2. The backend opens a short, visible handshake window for structured TCP.
3. A valid hello, catalog, and fresh snapshot inside that window unlocks TCP capabilities.
4. If the window expires, mark the current OpMode `unsupported-for-active-opmode`.
5. Remove Telemetry Lab from primary navigation, unmount live subscriptions, clear live tool availability, and replace the home status with an explanatory capability card.
6. Keep historical recordings, prior replay routes, camera artifacts, and system diagnostics available. They are not evidence of live TCP support.

The timeout must be configurable per OpMode category. A slow initialization should read “waiting for data” rather than “unsupported”; an explicit protocol declaration from the OpMode is preferable whenever possible.

### Session changes and stale-data handling

- A new TCP session UUID starts a new live context. Do not merge its graph history, commands, manifest, or recording state with the prior session.
- A TCP reconnect to the same session can append a numbered raw stream file, but the UI must show the interruption in the session timeline.
- A new OpMode or a new catalog invalidates selected traces and active mechanism-tool instances until they are re-advertised.
- When TCP drops, immediately disable TCP commands. Never dispatch a queued command after reconnect until its target session, manifest revision, tool instance, and TTL have been revalidated.
- When DS drops, neutralize app-forwarded gamepad output as the DS protocol requires, then withdraw all control UI. Never attempt a stop command through an unavailable or possibly unowned DS session.
- A camera or upload failure changes the recording card and creates an attention item; it does not downgrade an otherwise healthy robot-link mode.

## Shared shell

The shared shell should make the current topology legible on every page, but it must not become the giant dashboard the rest of the product avoids.

### Persistent session strip

Show a compact, source-labeled strip in this order:

```text
[ Workspace mode ] [ DS control ] [ TCP data ] [ Active OpMode ] [ Battery ] [ Recording ] [ Camera ] [ Last update ]
```

Rules:

- **Workspace mode** is textual: `Control only`, `Observe only`, `Control + observe`, or `Setup`.
- **DS control** says `App controls RC`, `Control unavailable`, or `Not connected`; never say simply “connected” when that hides ownership.
- **TCP data** says `Live`, `Waiting`, `Not supported by this OpMode`, `Stale 1.3 s`, or a precise protocol error.
- **Active OpMode** includes the source. If only TCP advertises it, label it `reported by TCP`; if DS owns it, label it `selected through this app`.
- **Battery** is shown only if a current source reports it. A stale voltage retains its age and source or disappears; it never looks live by default.
- **Recording** distinguishes `off`, `arming`, `recording`, `finalizing`, `ready to upload`, `uploading`, `complete`, and `needs attention`.
- **Camera** distinguishes `not configured`, `ready`, `preview`, `recording`, `finalizing`, and `error`.
- **Last update** represents the freshest critical source, not an arbitrary browser render time.

Use words, timestamp age, placement, and icons/patterns as well as color. A monochrome treatment still needs unambiguous online, stale, warning, and blocked states.

### Safety control

The shell must not render a universal `STOP ROBOT` button.

- In Driver Station only and Both, expose a prominent **Stop app-controlled OpMode** action only when this app owns the DS lifecycle and the backend confirms it is valid.
- In TCP only, replace it with **Observe only — stop from the active Driver Station**. Do not expose a robot-affecting TCP tool control in observer mode, even if a command happens to be advertised.
- In setup/stale states, show no control action that could imply ownership.

## What Home is for

Home is a **mode-aware session coordinator**, not a miniature version of every page. It connects the systems by explaining the current topology and offering the next safe job.

It should answer, in this order:

1. What is happening with the robot and the two primary links?
2. What can I safely do in this mode?
3. What needs attention before my next job?
4. Where can I continue work that already exists?

Home should not carry live trace charts, a full gamepad, raw packet dumps, every camera configuration field, the MCP tool catalog, or an entire replay viewer. Those belong on their dedicated surfaces.

## Detailed Home-page blueprint

### 1. Mode identity and primary next action

Place this directly beneath the shared session strip. It is the main Home decision block.

| Mode | Heading | Supporting explanation | Primary action | Secondary actions |
| --- | --- | --- | --- | --- |
| Driver Station only | `Control the robot` | `FTC Advanced owns the Driver Station link. This OpMode has no live structured data session.` | `Open Control Station` | `Configure robot`, `Prepare camera`, `How to add TCP telemetry` |
| TCP only | `Observe the live run` | `Structured data is live. Control is not owned by this app; another Driver Station may be active.` | `Open Telemetry Lab` | `Open live replay`, `Check recording`, `Reconnect control` |
| Both | `Run, record, and observe` | `FTC Advanced controls the active RC session and is receiving fresh structured telemetry.` | `Run an OpMode with recording` | `Open Control Station`, `Open Telemetry Lab`, `Tune a mechanism` if advertised |
| Setup | `Choose a connection` | `No usable robot link is active yet.` | `Connect Driver Station` | `Wait for TCP session`, `Open replay library`, `Check services` |

The action label must name the job, not the transport. `Run an OpMode with recording` is meaningful; `Open DS + TCP` is not.

### 2. Connection topology card

Show a small diagram rather than a collection of unrelated status chips:

```text
Robot Controller ── DS link ── FTC Advanced ── TCP listener ── Active OpMode
                         │                          │
                    control authority           fresh samples / tools
```

The diagram adapts by mode:

- solid, labeled paths for usable links;
- a clear unavailable/broken path with an explanatory reason;
- a `listening` TCP endpoint when there is no robot client;
- source timestamps on each active path;
- no invented “official DS” node unless it is explicitly confirmed. Use an explanatory note instead.

Selecting a link opens its diagnostics page. The card itself stays concise: link status, age, peer/RC identity if safe to show, and next recovery action.

### 3. Available now

This is a short list of task cards generated from capabilities. It replaces a static grid of every subsystem.

Possible cards, in priority order:

- **Run an OpMode** — only with DS authority; includes selected/default OpMode and lifecycle readiness.
- **Run and record** — only in Both mode with a camera/recording preflight result; includes exactly what will be captured.
- **Observe live telemetry** — only with fresh TCP; includes session ID suffix, sample age, and catalog summary.
- **Tune or test a mechanism** — only in a paired Both session when the TCP manifest advertises a ready, safe tool; label it from the robot's manifest, for example `Tune turret PID`, not a generic “Lab.”
- **Configure robot hardware** — only with DS authority and a stopped lifecycle; includes loaded configuration name and whether saving is currently allowed.
- **Review last run** — whenever a recording exists; includes duration, telemetry/video pairing state, and upload state.
- **Recover a recording** — only if finalization/import/upload needs action; this outranks convenience cards.
- **Ask the assistant about this session** — only if MCP/Oracle is healthy and a bounded data source exists; clearly label whether it will inspect live status or a saved recording.

Each card must have:

- a one-sentence consequence, not a technical subsystem description;
- explicit availability (`Ready`, `Waiting for camera`, `Needs a TCP-capable OpMode`, etc.);
- no action that the current mode cannot execute;
- a transition to a dedicated page, never an overgrown inline panel on Home.

### 4. Current session context

When a live session exists, show one compact context card. Its content changes by source:

| Data available | Home presentation |
| --- | --- |
| DS only | Active/selected OpMode, DS lifecycle, controller assignment, RC error summary, and any source-labeled DS telemetry essential to safely start |
| TCP only | TCP session ID suffix, OpMode name only if protocol-advertised, catalog/device count, sample rate or sample age, recording/camera pairing state |
| Both | OpMode lifecycle plus TCP session ID, telemetry freshness, recording state, camera state, and a direct `Open live replay` handoff |
| Neither | Last successful connection target, listener status, prerequisites, and recent recovery failures |

This card is not a metrics wall. Show only data that changes the next decision. Link to the relevant page for full values.

### 5. Recording and replay continuity

Use a dedicated Home area because recordings connect live work to later analysis.

Show, at most, the latest active or most recent session:

- session name/ID suffix and start/end time;
- telemetry status: live, finalized, interrupted, missing, or error;
- video status: none, recording, finalizing, verified, unpaired, or error;
- pairing status: paired, waiting to bind, or intentionally standalone;
- upload status: local only, ready to upload, retrying, uploaded, or needs attention;
- primary recovery action, if any;
- `Open replay` only when a valid replay artifact exists.

An interrupted stream should not appear as a completed clean run. Surface the interruption and link to the evidence rather than hiding it in implementation details.

### 6. Attention queue

This is a small, ordered list of work that blocks or affects the next task. It is much more useful than displaying every healthy service.

Suggested severity order:

1. An app-owned OpMode is running, control or data became stale, or a safety watchdog blocked a tool.
2. Camera recording may not have finalized, telemetry recording could not write, or a session needs recovery.
3. An expected TCP handshake did not arrive for an app-controlled OpMode.
4. A configuration edit is unsaved or prohibited by current lifecycle state.
5. An upload failed but local artifacts are intact.
6. MCP/Oracle is offline while the rest of the robot workflow remains usable.

Every item must say whether it blocks control, blocks recording, limits analysis, or is merely informational. Do not make a cloud-analysis outage look like a robot safety fault.

### 7. Recent work

Show a compact chronological list rather than a full library:

- last recording(s), with telemetry/video/upload badges;
- last configuration activation or a saved-draft indicator;
- last successful mechanism test/tool selected;
- last analysis/report from Oracle, if available;
- recent connection transition such as `Switched to observe-only at 14:32:08`.

Each item opens its dedicated surface. Keep the full recording library and raw diagnostic history out of Home.

### 8. Services and integrations, deliberately quiet

MCP and Oracle belong in a small “Services” disclosure or lower-priority section, not the main action area.

For each service show:

- local/remote availability;
- last successful health check age;
- data scope, such as `read-only live status` or `recording analysis`;
- the next action only if it is actionable (`Start local MCP server`, `Retry upload`, `Open analysis`).

Never elevate “MCP is online” above “the TCP data stream is stale.” The former enriches a workflow; the latter changes what is safe and true about the robot.

## Mode-specific Home composition

### Driver Station only Home

```text
Persistent session strip
Mode identity: Control the robot
Primary action: Open Control Station
Connection topology: DS live / TCP waiting or unsupported
Available now: Run OpMode, Configure robot, Prepare camera
Current session: lifecycle + selected OpMode + controller assignment
Attention: TCP capability explanation, RC errors, camera preflight
Recent work: recordings/replays/configurations
```

Do not reserve empty slots for charts, advertised commands, catalog counts, or debugger tools. A clear “this OpMode does not provide structured data” explanation is more useful than a disabled Telemetry Lab teaser.

### TCP-only Home

```text
Persistent session strip: Observe only
Mode identity: Observe the live run
Primary action: Open Telemetry Lab
Connection topology: DS control unavailable / TCP live
Available now: Observe telemetry, Open live replay, Inspect recording
Current session: fresh sample age + advertised capabilities + session identity
Attention: control ownership explanation, recording/camera state, stream gaps
Recent work: current and previous sessions
```

The main visual message is that the app is contributing observation and capture while another station may be driving. There is no faux Control Station, no universal stop button, and no configuration editing route.

### Both-connections Home

```text
Persistent session strip: Control + observe
Mode identity: Run, record, and observe
Primary action: Run an OpMode with recording
Connection topology: DS live / TCP live
Available now: Control Station, Telemetry Lab, advertised mechanism tools
Current session: lifecycle + telemetry freshness + recording/video pairing
Attention: camera preflight, tool safety state, upload/recovery status
Recent work: latest replay and analysis
```

This is the only mode where a combined, guided execution flow makes sense. The flow should make each handoff visible:

```text
Choose OpMode
    → verify DS authority
    → verify TCP capability expectation
    → verify camera / recording plan
    → Init
    → receive structured hello and catalog
    → Start
    → record / observe
    → Stop
    → finalize telemetry + video
    → review or upload
```

If any step changes capability, the flow must state what continues. For example: “The OpMode is running under DS control; structured telemetry did not connect, so this run will not have live Lab data.”

## Page-level rules

### Control Station

- It is the authority page for lifecycle, gamepads, standard DS telemetry, and configuration entry points.
- In Both mode, it links naturally to the Lab but does not try to embed all high-rate telemetry.
- In TCP-only mode, it is removed from active navigation and unmounted as an interactive control surface.
- It must show whether the active OpMode is expected to create a structured TCP session, is waiting for one, or does not support it.

### Telemetry Lab

- It exists only for fresh, valid structured TCP data.
- It owns live traces, catalog-driven values, gamepad frames, robot-advertised commands, and live replay.
- It must clear catalog-specific UI on a new session or catalog revision.
- In TCP-only mode it has an observer banner; in Both mode it offers a clear route back to Control Station.
- When TCP becomes stale, freeze data with its last timestamp and remove all live commands before navigating away or showing recovery.

### Mechanism tools and debugger

- They are discovered from the current robot manifest, not hardcoded navigation categories.
- They require app-owned DS control and a paired live TCP session; observer mode is read-only even when a manifest is present.
- A tool appears only if the current TCP session advertises it and its safety state is ready.
- Each tool has a stop/cleanup contract, watchdog/freshness behavior, risk class, and exact target session/tool instance.
- A tool loss should return the user to Telemetry Lab with an explanation, not leave an orphaned control form.

### Capture and recordings

- Capture can be independently configured, but Home must explain whether it is actually paired to the current telemetry session.
- Camera issues should be actionable without blocking unrelated observation when recording is optional.
- If the user chose a run-and-record workflow where camera capture is mandatory, block the final lifecycle action with an explicit override/decision rather than silently producing an incomplete recording.

### Replay and Oracle

- Replay is read-only and survives every live-mode transition.
- Oracle analysis consumes saved/replay context by default. It does not assume a live Robot Controller and it never gains control authority through its presence.
- A replay can be sent to analysis only after artifact readiness is clear: complete, partial/recoverable, or missing components.

### MCP

- MCP is an integration boundary, not a user-facing live mode.
- It consumes the backend's authoritative, bounded state; it must not open a competing TCP connection or maintain a divergent robot cache.
- Initial MCP capability remains read-only. Any future control tool must independently pass the same backend capability gates and explicit host/user approvals.

## Recommended backend contract: one derived workspace state

The frontend should not race several WebSocket streams and invent its own mode. The backend already owns the Driver Station and TCP services, so it should publish a derived session-topology payload whenever either input changes.

Conceptual shape:

```ts
type WorkspaceState = {
  revision: number;
  computedAtMonotonicMs: number;
  mode: "driver-station-only" | "tcp-only" | "both" | "setup";
  modeReason: string;
  driverStation: {
    transport: "disconnected" | "connecting" | "connected" | "stale" | "conflict" | "error";
    controlAuthority: "app-owned" | "unavailable" | "unknown";
    lastHeartbeatAgeMs: number | null;
    robotState: string | null;
    activeOpMode: string | null;
    error: string | null;
  };
  tcp: {
    listener: "starting" | "listening" | "error";
    transport: "disconnected" | "connected";
    protocol: "waiting" | "accepted" | "unsupported" | "stale" | "error";
    sessionId: string | null;
    lastSampleAgeMs: number | null;
    catalogRevision: number | null;
    activeOpMode: string | null;
    error: string | null;
  };
  association: "not-applicable" | "pending" | "paired" | "mismatch";
  capabilities: Record<string, { available: boolean; reason?: string }>;
  camera: { state: string; pairedSessionId: string | null; detail?: string };
  recording: { state: string; sessionId: string | null; detail?: string };
  services: {
    mcp: { state: string; lastHealthAgeMs: number | null };
    oracle: { state: string; lastHealthAgeMs: number | null };
  };
};
```

Implementation requirements:

- Publish the state over one authoritative workspace WebSocket as well as a read endpoint for initial load.
- Increment `revision` atomically whenever it is derived; never combine a new DS status with a stale TCP status in the same revision.
- Include monotonic ages so browser clock changes cannot make a stream look fresh.
- Keep detailed raw status endpoints for diagnostics, but render Home and navigation from `WorkspaceState`.
- Treat the frontend as a renderer of capabilities. Every mutation endpoint repeats validation in the backend.

## Acceptance scenarios

### Official Driver Station practice

1. Teammates use the official Driver Station to start a TCP-capable OpMode.
2. FTC Advanced receives a valid TCP hello, catalog, and samples, while its DS link is unavailable or contended.
3. Within one second of the resolved state change, FTC Advanced enters TCP-only mode.
4. If the user was in Control Station or Configure Robot, the app routes to Telemetry Lab.
5. The shell says `Observe only`; no lifecycle/gamepad/configuration action remains reachable.
6. Live telemetry, replay, recording status, and explicitly read-only TCP surfaces remain available; every robot-affecting TCP action stays unavailable.

### FTC Advanced controls an OpMode with no TCP support

1. The user connects through FTC Advanced, selects an ordinary non-TCP OpMode, and initializes/starts it.
2. The TCP grace window expires without a valid handshake.
3. The app resolves to Driver Station only.
4. Telemetry Lab and TCP-specific tool routes vanish from primary navigation; there is no empty dashboard.
5. Home explains that the current OpMode does not provide structured TCP data and offers control, configuration, camera, and historical replay work instead.

### Complete instrumented run

1. FTC Advanced owns the DS link and receives fresh TCP data from the selected OpMode.
2. Home resolves to Both and offers `Run an OpMode with recording`.
3. Camera/recording preflight states are explicit before Init.
4. During the run, control remains on Control Station and high-rate data stays in Telemetry Lab.
5. On stop or TCP session end, the app finalizes artifacts, reports pairing/upload state, and offers the replay without pretending a partially finalized artifact is ready.

### TCP data goes stale mid-run

1. The DS link remains usable, but valid TCP samples stop.
2. The shell marks TCP stale in under one second and immediately disables TCP commands/tool outputs.
3. The workspace resolves to Driver Station only after the documented threshold.
4. If the current Lab route cannot operate without fresh data, it routes to Control Station or Home with an explanation; the last graph is clearly historical/stale.

### DS control disappears while TCP continues

1. FTC Advanced loses its DS session but TCP samples remain fresh.
2. All app-control actions disappear immediately; app-sent inputs are neutralized according to the DS safety protocol.
3. The workspace resolves to TCP-only and routes from Control Station to Telemetry Lab.
4. It does not call Stop, reconnect aggressively, or claim that official DS control is proven unless that signal exists.

## Non-negotiable rules

- Do not use route names as a proxy for authority; use backend-derived capabilities.
- Do not show a value as live without source and freshness semantics.
- Do not show a Stop control unless the application can actually and safely execute the applicable stop.
- Do not leave TCP-only pages in the UI for an OpMode known not to support TCP.
- Do not erase replay or recordings because a live connection disappeared.
- Do not let MCP, Oracle, camera, or upload health change the primary robot-link mode.
- Do not allow a newly reconnected stream to revive an old tool instance, queued command, or stale UI selection.
- Do not treat “official Driver Station is active” as a fact unless the system has evidence beyond the absence of FTC Advanced's own DS link.

## Design checkpoint

Before implementing a page or feature, answer:

```text
Which capability does this need?
Which source makes that capability true?
What happens when that source disappears?
Where does the user go next?
Can the UI honestly explain the loss without guessing?
```

If those answers are clear, the application can scale to many independent subsystems without turning Home into an overwhelming dashboard.
