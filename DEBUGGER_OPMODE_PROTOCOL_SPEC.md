# FTC Advanced Debugger OpMode and TCP Protocol Specification

**Status:** proposed implementation specification  
**Revision:** 1  
**Date:** 2026-08-28  
**Audience:** FTC TeamCode, backend, frontend, and future MCP developers

## 1. Purpose

This document specifies a general-purpose FTC debugging workbench:

1. The laptop connects to the Robot Controller using the existing Driver
   Station/control path.
2. The laptop initializes and starts one physical FTC OpMode:
   `FTC Advanced / Debugger`.
3. The Debugger OpMode opens the structured TCP connection to the laptop and
   publishes a hierarchical menu of debugging tools.
4. The user selects a folder or leaf tool in the laptop UI.
5. The selection and all subsequent debugging commands travel over TCP.
6. The selected tool publishes its parameter schema and telemetry schema.
7. The laptop renders the tool's controls dynamically and records the session.

The Driver Station protocol remains responsible for lifecycle and liveness.
The TCP protocol becomes the rich, typed debugging protocol.

## 2. Important design correction: folders contain tools, not nested OpModes

The user-facing menu may look like this:

```text
Debugger
├── Motor Tests
│   ├── Free-spin motors
│   │   ├── Intake Left
│   │   └── Shooter
│   ├── Drivetrain motors
│   └── Mechanism motors
├── Velocity Control
│   └── Shooter velocity PID
└── Sensor Diagnostics
    └── Limelight
```

The leaf entries should be called **DebugTools** in the implementation.

They must not be independent FTC `OpMode` instances if the user expects to
start only one selector OpMode. The FTC lifecycle cannot start a second nested
OpMode solely through the structured TCP connection after the selector is
already running. Instead:

- `DebuggerOpMode` is the one physical FTC OpMode launched through the Driver
  Station protocol.
- Each leaf is a `DebugTool` with `init`, `start`, `loop`, and `stop` methods.
- `DebuggerOpMode` delegates those lifecycle calls to the selected tool.
- The Driver Station is used again only when the team explicitly wants a
  separate physical OpMode.

This preserves the desired workflow while avoiding a lifecycle contradiction.
The menu can still be rendered locally in a terminal and remotely in the web
IDE from the same registry.

## 3. System architecture

```text
                  Driver Station / Robocol
Laptop ─────────────────────────────────────────► Control Hub
  │                 init/start/stop/heartbeat        │
  │                                                  │
  │          length-prefixed protobuf over TCP       │
  └────────────── DebuggerOpMode ◄───────────────────┘
       manifest, selection, parameters, commands,
       telemetry, events, acknowledgements
```

### 3.1 Control plane

The existing Driver Station/control plane owns:

- Robot Controller connection and peer discovery;
- heartbeat and liveness;
- OpMode list discovery;
- `INIT`, `START`, and normal `STOP`;
- emergency/disconnect stop behavior;
- optional generic gamepad transport.

The laptop must continue sending the heartbeat independently of browser
activity. Browser JavaScript must not be the source of robot liveness.

### 3.2 Debug TCP plane

The structured TCP connection owns:

- debug manifest discovery;
- folder navigation metadata;
- tool selection;
- parameter schemas and updates;
- typed debug actions;
- tool state and command acknowledgements;
- structured telemetry and events;
- recording and replay metadata;
- optional MCP-facing diagnostics.

The TCP plane is not a replacement for the Driver Station stop path. A TCP
disconnect must also cause the active tool to remove actuator output through a
robot-side watchdog, but the Driver Station and physical power cutoff remain
independent safety layers.

## 4. Connection and frame format

The protocol reuses the existing protobuf `Envelope` and TCP framing from
`protocol/robot_data.proto`.

Every TCP message is:

```text
4 bytes: unsigned big-endian frame length N
N bytes: protobuf-serialized ftc.robotdata.v2.Envelope
```

Rules:

- Reject a zero-length frame.
- Reject a frame larger than 1 MiB.
- Use `readFully`/`readExactly`; one TCP read is not one message.
- Do not use newline-delimited JSON on the robot/laptop TCP connection.
- JSON may be used at the browser API boundary.
- `Envelope.protocol_version` remains `2` for this extension.
- `connection_sequence` is monotonic per sender and per TCP connection.
- `request_id` is the application-level identity for command deduplication.

The current envelope already contains `session_id`, `connection_id`, source
robot time, telemetry, schemas, events, heartbeats, gaps, and session ending.
The following messages extend its `oneof body` with debug messages using new
field numbers.

## 5. Protobuf message definitions

The following is the normative message shape. Field numbers must not be
reused. The implementation may split this into a separate `.proto` file, but
the generated messages must be embedded in the existing length-prefixed
`Envelope` stream.

```proto
// Add these alternatives to Envelope.body.
message Envelope {
  // Existing fields remain unchanged.
  oneof body {
    Hello hello = 10;
    HelloAck hello_ack = 11;
    Schema schema = 12;
    SampleBatch sample_batch = 13;
    Event event = 14;
    Heartbeat heartbeat = 15;
    Gap gap = 16;
    SessionEnd session_end = 17;
    GamepadSnapshot gamepad = 18;
    ProtocolError error = 19;

    DebugManifest debug_manifest = 20;
    DebugSelectRequest debug_select_request = 21;
    DebugSelectResponse debug_select_response = 22;
    DebugToolReady debug_tool_ready = 23;
    DebugToolState debug_tool_state = 24;
    DebugParameterSetRequest debug_parameter_set_request = 25;
    DebugParameterSetResponse debug_parameter_set_response = 26;
    DebugCommandRequest debug_command_request = 27;
    DebugCommandResponse debug_command_response = 28;
    DebugLeaseRequest debug_lease_request = 29;
    DebugLeaseResponse debug_lease_response = 30;
    DebugSafetyState debug_safety_state = 31;
  }
}
```

### 5.1 Debug manifest

The robot sends one `DebugManifest` after the TCP handshake and may resend it
when the registry revision changes. The manifest is a tree represented by
stable IDs and parent IDs.

```proto
message DebugManifest {
  uint32 revision = 1;
  string registry_id = 2;
  string registry_label = 3;
  repeated DebugNode nodes = 4;
  string active_node_id = 5;
}

message DebugNode {
  string id = 1;                    // stable, dot-separated identifier
  string parent_id = 2;             // empty only for the root
  string label = 3;
  string description = 4;
  NodeKind kind = 5;
  uint32 sort_order = 6;
  RiskClass risk_class = 7;
  string opmode_label = 8;          // populated for a TOOL leaf
  string tool_version = 9;
  bool selectable = 10;
  bool enabled = 11;
  string disabled_reason = 12;
  SafetyProfile safety = 13;
  McpAccess mcp_access = 14;
  repeated string telemetry_channel_ids = 15;
  repeated string parameter_ids = 16;
  repeated string command_ids = 17;
}

enum NodeKind {
  NODE_KIND_UNSPECIFIED = 0;
  FOLDER = 1;
  TOOL = 2;
}

enum RiskClass {
  RISK_UNSPECIFIED = 0;
  OBSERVE_ONLY = 1;
  FREE_SPIN = 2;
  DRIVETRAIN = 3;
  MECHANISM = 4;
}

enum McpAccess {
  MCP_ACCESS_UNSPECIFIED = 0;
  MCP_NONE = 1;
  MCP_READ_ONLY = 2;
  MCP_HUMAN_APPROVAL = 3;
  MCP_ALLOWED = 4;
}

message SafetyProfile {
  bool requires_floor = 1;
  bool requires_blocks = 2;
  bool requires_human_acknowledgement = 3;
  bool allows_actuator_output = 4;
  double max_output = 5;
  uint32 max_active_duration_ms = 6;
  uint32 command_timeout_ms = 7;
  bool one_actuator_at_a_time = 8;
  string warning = 9;
}
```

Rules:

- Folder nodes must not contain an `opmode_label`.
- Tool nodes must be leaves; they cannot have child nodes.
- A tool must have a stable `id` and a unique `opmode_label` for display and
  diagnostics, even though it is not a separate FTC OpMode.
- IDs use the existing dot-separated identifier convention, for example
  `motors.freeSpin.intakeLeft`.
- The laptop must treat the manifest as untrusted data and still enforce its
  own local policy.
- The robot remains the final authority for safety and capability checks.

### 5.2 Selection request and response

The laptop sends a selection request when the user clicks a folder or tool.
Folders are navigation-only. Selecting a folder never changes robot state.

```proto
message DebugSelectRequest {
  string request_id = 1;
  uint32 manifest_revision = 2;
  string node_id = 3;
  string client_session_id = 4;
}

message DebugSelectResponse {
  string request_id = 1;
  bool accepted = 2;
  string node_id = 3;
  DebugSessionState state = 4;
  string rejection_code = 5;
  string message = 6;
}

enum DebugSessionState {
  DEBUG_STATE_UNSPECIFIED = 0;
  DEBUG_IDLE = 1;
  DEBUG_SELECTED = 2;
  DEBUG_READY = 3;
  DEBUG_RUNNING = 4;
  DEBUG_STOPPING = 5;
  DEBUG_FAULTED = 6;
}
```

Selection rules:

- A request with an old `manifest_revision` is rejected with
  `STALE_MANIFEST`.
- A folder selection is accepted as navigation metadata but does not create
  a tool session.
- A disabled or unavailable tool is rejected with a reason suitable for the
  UI.
- Selecting a new tool first stops the old tool and sets all of its actuator
  outputs to zero.
- Only one tool may own an actuator at a time.
- The response is sent before or together with `DebugToolReady`; the UI must
  wait for `DebugToolReady` before rendering editable controls.

### 5.3 Selected tool initialization

After accepting a tool leaf, the robot initializes that tool and sends its
runtime schema.

```proto
message DebugToolReady {
  string node_id = 1;
  string tool_instance_id = 2;
  uint32 manifest_revision = 3;
  repeated ParameterDefinition parameters = 4;
  repeated CommandDefinition commands = 5;
  repeated string telemetry_channel_ids = 6;
  DebugSessionState state = 7;
  string message = 8;
}
```

`tool_instance_id` changes every time a tool is initialized. Requests must
include it so commands from a previous tool instance cannot affect the new
tool.

### 5.4 Parameter schema

Parameters are explicit registered values, not arbitrary reflected Java
fields. This supports Panels-style configurables while adding units, bounds,
permissions, persistence, and auditability.

```proto
message ParameterDefinition {
  string id = 1;
  string label = 2;
  string description = 3;
  ValueType value_type = 4;
  ParameterValue default_value = 5;
  ParameterValue current_value = 6;
  bool writable = 7;
  bool persistent = 8;
  bool requires_apply = 9;
  string unit = 10;
  double min_value = 11;
  double max_value = 12;
  double step = 13;
  repeated EnumOption enum_options = 14;
  ParameterUpdatePolicy update_policy = 15;
  McpAccess mcp_access = 16;
}

message EnumOption {
  string id = 1;
  string label = 2;
}

enum ParameterUpdatePolicy {
  PARAMETER_UPDATE_UNSPECIFIED = 0;
  UPDATE_ANYTIME = 1;
  UPDATE_LOOP_BOUNDARY = 2;
  UPDATE_WHEN_STOPPED = 3;
  UPDATE_ON_APPLY = 4;
}

message ParameterValue {
  oneof value {
    double float64_value = 1;
    sint64 int64_value = 2;
    bool boolean_value = 3;
    string string_value = 4;
    string enum_value = 5;
  }
}
```

Examples:

```text
shooter.kP             float64, range 0..1, step .001, temporary
shooter.kI             float64, range 0..1, step .001, temporary
shooter.kD             float64, range 0..1, step .001, temporary
shooter.targetVelocity float64, range 0..5000, unit ticks/s
motor.power            float64, range -0.35..0.35, unit normalized power
motor.direction        enum: FORWARD, REVERSE
```

The robot must validate type and bounds even if the frontend already did so.
An invalid parameter update must not partially apply.

### 5.5 Parameter update request and response

```proto
message DebugParameterSetRequest {
  string request_id = 1;
  string node_id = 2;
  string tool_instance_id = 3;
  string parameter_id = 4;
  ParameterValue value = 5;
  bool persist = 6;
  uint64 expires_at_robot_time_ns = 7;
}

message DebugParameterSetResponse {
  string request_id = 1;
  ParameterSetResult result = 2;
  string parameter_id = 3;
  ParameterValue effective_value = 4;
  string rejection_code = 5;
  string message = 6;
  uint64 applied_at_robot_time_ns = 7;
}

enum ParameterSetResult {
  PARAMETER_RESULT_UNSPECIFIED = 0;
  PARAMETER_ACCEPTED = 1;
  PARAMETER_APPLIED = 2;
  PARAMETER_QUEUED = 3;
  PARAMETER_REJECTED = 4;
  PARAMETER_EXPIRED = 5;
}
```

`persist = true` must require an explicit user action. Agent-originated
temporary tuning should default to `persist = false`.

### 5.6 Typed debug commands

Parameters change values. Commands invoke bounded actions such as selecting a
motor, beginning a test sequence, applying a temporary configuration, or
resetting an encoder.

```proto
message CommandDefinition {
  string id = 1;
  string label = 2;
  string description = 3;
  repeated ArgumentDefinition arguments = 4;
  bool requires_human_acknowledgement = 5;
  McpAccess mcp_access = 6;
}

message ArgumentDefinition {
  string id = 1;
  string label = 2;
  ValueType value_type = 3;
  bool required = 4;
  ParameterValue default_value = 5;
  double min_value = 6;
  double max_value = 7;
  string unit = 8;
}

message DebugCommandRequest {
  string request_id = 1;
  string node_id = 2;
  string tool_instance_id = 3;
  string command_id = 4;
  repeated NamedValue arguments = 5;
  uint32 ttl_ms = 6;
  string lease_id = 7;
}

message NamedValue {
  string name = 1;
  ParameterValue value = 2;
}

message DebugCommandResponse {
  string request_id = 1;
  CommandResult result = 2;
  string command_id = 3;
  repeated NamedValue outputs = 4;
  string rejection_code = 5;
  string message = 6;
  uint64 handled_at_robot_time_ns = 7;
}

enum CommandResult {
  COMMAND_RESULT_UNSPECIFIED = 0;
  COMMAND_ACCEPTED = 1;
  COMMAND_COMPLETED = 2;
  COMMAND_QUEUED = 3;
  COMMAND_REJECTED = 4;
  COMMAND_EXPIRED = 5;
  COMMAND_DUPLICATE = 6;
  COMMAND_FAILED = 7;
}
```

Commands must be idempotent where possible. The robot keeps a bounded cache of
recent `request_id` values and returns `COMMAND_DUPLICATE` with the original
result instead of invoking a command twice.

### 5.7 Tool state

```proto
message DebugToolState {
  string node_id = 1;
  string tool_instance_id = 2;
  DebugSessionState state = 3;
  bool output_enabled = 4;
  bool safety_acknowledged = 5;
  string status_message = 6;
  repeated ActiveOutput active_outputs = 7;
}

message ActiveOutput {
  string resource_id = 1;
  double requested_value = 2;
  double applied_value = 3;
  string unit = 4;
}
```

The tool state is authoritative for whether a command actually took effect.
An accepted request means the robot accepted it; it does not necessarily mean
the actuator is active.

### 5.8 Lease and safety state

A lease prevents multiple browser tabs or an MCP client from controlling the
same debug session.

```proto
message DebugLeaseRequest {
  string request_id = 1;
  LeaseAction action = 2;
  string requested_by = 3;
  string lease_id = 4;
}

enum LeaseAction {
  LEASE_ACTION_UNSPECIFIED = 0;
  ACQUIRE = 1;
  RENEW = 2;
  RELEASE = 3;
}

message DebugLeaseResponse {
  string request_id = 1;
  bool granted = 2;
  string lease_id = 3;
  uint32 expires_in_ms = 4;
  string holder = 5;
  string message = 6;
}

message DebugSafetyState {
  bool tcp_connected = 1;
  bool lease_valid = 2;
  bool physical_acknowledgement = 3;
  bool output_allowed = 4;
  bool watchdog_healthy = 5;
  string blocked_reason = 6;
}
```

The robot may reject every actuator command while the lease is absent or
expired. Lease renewal must be independent of high-rate telemetry.

## 6. Message direction and lifecycle

### 6.1 Initial connection

```text
Robot → Laptop: Hello(capabilities includes "debug-manifest-v1")
Laptop → Robot: HelloAck(accepted=true)
Robot → Laptop: DebugManifest
Robot → Laptop: Schema
Robot → Laptop: DebugSafetyState
```

The robot may send telemetry and heartbeats immediately after the handshake.
The laptop must not send selection or commands until the `HelloAck` has been
accepted and the manifest has been validated.

### 6.2 Folder navigation

Folder clicks are local UI navigation. The frontend may optionally send a
`DebugSelectRequest` for a folder so the backend can log the operator's
navigation, but the robot must not initialize a tool for a folder.

### 6.3 Tool selection

```text
Laptop → Robot: DebugSelectRequest(node_id = leaf)
Robot → Laptop: DebugSelectResponse(accepted=true, state=DEBUG_SELECTED)
Robot → Laptop: DebugToolReady(parameters, commands, telemetry IDs)
Robot → Laptop: DebugToolState(state=DEBUG_READY)
```

The selected tool must start with all actuator outputs disabled. A separate
command or safety acknowledgement is required before motion.

### 6.4 Parameter update

```text
Laptop → Robot: DebugParameterSetRequest
Robot: validate request
Robot: enqueue update for the requested policy boundary
Robot → Laptop: DebugParameterSetResponse
Robot → Laptop: Event(parameter.changed)
Robot → Laptop: telemetry samples containing the effective value
```

Hardware reads and actuator writes happen only in the OpMode loop. The TCP
reader places requests into a bounded queue; it does not access `hardwareMap`
or call motor methods.

### 6.5 Command execution

```text
Laptop → Robot: DebugCommandRequest
Robot → Laptop: DebugCommandResponse(QUEUED or ACCEPTED)
Robot: execute at a safe OpMode-loop boundary
Robot → Laptop: DebugCommandResponse(COMPLETED or FAILED)
```

Long-running tests must return an initial response and then publish progress
through `DebugToolState` and `Event` messages. The laptop must remain
responsive while a test is running.

### 6.6 Stop behavior

There are three stop paths:

1. **Tool stop:** TCP command disables the selected tool's outputs.
2. **Debugger OpMode stop:** Driver Station initializes the FTC stop OpMode;
   the tool's `stop()` method is called.
3. **Emergency/disconnect stop:** Driver Station or robot watchdog forces
   outputs to zero.

The web UI must always show the Driver Station stop action even when a TCP
tool is active.

## 7. DebuggerOpMode Java contract

The TeamCode implementation should expose interfaces similar to these:

```java
public interface DebugTool {
    void init(DebugToolContext context) throws Exception;
    void start() throws Exception;
    void loop() throws Exception;
    void stop();
}

public interface DebugToolFactory {
    DebugTool create();
}

public final class DebugRegistry {
    public DebugRegistry folder(String id, String label, Consumer<FolderBuilder> children);
    public DebugRegistry tool(DebugToolDefinition definition, DebugToolFactory factory);
    public DebugManifestSnapshot manifest();
}
```

`DebugToolContext` provides:

- `HardwareMap` access during tool initialization;
- telemetry registration;
- parameter registration;
- command registration;
- event publishing;
- bounded command queue access;
- robot clock;
- current safety state;
- tool instance ID.

The registry should be declared once and used for:

- the local terminal selector;
- the TCP `DebugManifest`;
- backend/frontend tool discovery;
- MCP discovery.

### 7.1 Do not expose raw configurable statics

The existing Panels-style pattern of static configurable values is useful for
experimentation, but the wire contract should use explicit registrations:

```java
parameters.number(
    "shooter.kP",
    "Shooter kP",
    0.095,
    limits(0.0, 1.0, 0.001),
    unit("coefficient"),
    temporary()
);
```

This makes the value's type, bounds, persistence, update policy, and access
policy visible to every client.

## 8. Motor-testing policy

The motor classification is robot-specific and must be declared by TeamCode.
It must not be inferred only from the hardware configuration XML.

```java
motor("intake.left", "intakeLeft")
    .label("Intake Left")
    .risk(FREE_SPIN)
    .maxPower(0.35)
    .maxDurationMs(5_000)
    .mcpAccess(MCP_ALLOWED);

motor("drive.frontLeft", "frontLeft")
    .label("Front Left Drive")
    .risk(DRIVETRAIN)
    .requiresFloor(true)
    .maxPower(0.15)
    .maxDurationMs(2_000)
    .mcpAccess(MCP_NONE);

motor("lift.left", "liftLeft")
    .label("Lift Left")
    .risk(MECHANISM)
    .actuatorTesting(false)
    .mcpAccess(MCP_NONE)
    .disabledReason("Mechanism can move unexpectedly or be damaged.");
```

### 8.1 Free-spin test requirements

- Only one motor may be active by default.
- Power is clamped robot-side.
- Power changes are slew-rate limited.
- The control is deadman-style: stale commands mean zero output.
- The command has a TTL and maximum active duration.
- Pointer release, browser close, TCP loss, lease expiry, OpMode stop, or
  watchdog failure disables output.
- The frontend displays applied power, not only requested power.
- Every change is recorded with request ID and robot timestamp.

### 8.2 Drivetrain test requirements

- The tool displays a floor-placement warning.
- The user must explicitly acknowledge the warning.
- Default power and duration are lower than free-spin testing.
- The first version should not expose drivetrain actuation to MCP.

### 8.3 Mechanism test requirements

- Mechanism entries may be visible for documentation and future support.
- Actuator commands must not be advertised in `command_ids`.
- The robot rejects manually forged requests with `MECHANISM_LOCKED`.
- Read-only telemetry and health diagnostics remain allowed.

## 9. Backend responsibilities

The Python backend should add a `DebugSessionService` next to the existing
Driver Station and `RobotDataService` services.

It should:

- own the TCP command writer and reader;
- correlate requests and responses by `request_id`;
- enforce one active lease;
- validate that the selected tool and instance are current;
- expose manifest and tool state to the frontend over WebSocket;
- persist command requests, responses, parameter changes, and safety events;
- forward telemetry to the existing telemetry store;
- expose replay queries without requiring the robot to be connected;
- refuse actuator commands when the Driver Station state is not appropriate;
- clear pending actuator commands during disconnect and stop.

The backend must not allow a browser or MCP client to open a second raw TCP
connection directly to the robot. All clients go through the one session
owner.

Suggested browser endpoints:

```text
GET  /api/debug/manifest
GET  /api/debug/status
POST /api/debug/lease
POST /api/debug/select
POST /api/debug/parameters/{id}
POST /api/debug/commands/{id}
POST /api/debug/stop
WS   /ws/debug
```

The backend should also support replay equivalents:

```text
GET /api/data/sessions/{id}/manifest
GET /api/data/sessions/{id}/channels
GET /api/data/sessions/{id}/range
GET /api/data/sessions/{id}/events
```

Replay tools must be read-only and must never be connected to the actuator
command writer.

## 10. Frontend responsibilities

The web IDE should have these layers:

```text
Safety shell
├── Driver Station connection state
├── TCP/debug connection state
├── active tool and lease
├── battery and stop controls
└── safety warnings

Debug navigator
└── manifest tree: folders and tools

Tool workbench
├── generated parameter controls
├── generated command controls
├── telemetry cards and graphs
├── events and acknowledgements
└── tool-specific custom UI
```

The frontend may generate basic forms from `ParameterDefinition`, but a tool
may provide a custom panel for graphs, calibration procedures, or motor
visualization. The tool-specific UI must consume typed backend models rather
than raw protobuf frames.

The UI must distinguish:

```text
Requested  →  Accepted  →  Applied  →  Measured
```

For example, a requested motor power of `0.35` is not evidence that the motor
received `0.35`; the robot's applied output and telemetry are authoritative.

## 11. MCP integration boundary

The MCP server should connect to the laptop backend, not directly to the
Control Hub. It can expose:

```text
list_debug_tools()
get_debug_manifest()
get_tool_schema(tool_id)
get_live_values(channel_ids)
list_recordings()
query_recording(session_id, channels, start_ns, end_ns, max_points)
compare_recordings(baseline_id, candidate_id, channels)
compute_pid_metrics(session_id, target, measured, output)
```

Actuator tools should be capability-filtered:

```text
FREE_SPIN       observe: yes, actuation: allowlisted
DRIVETRAIN      observe: yes, actuation: human approval only
MECHANISM       observe: yes, actuation: unavailable
```

The MCP layer must not infer permission from a tool description. The backend
must enforce the same `McpAccess` policy and the robot must enforce it again
through its registered tool policy.

## 12. Recording and replay requirements

The raw TCP recorder should preserve every debug envelope, including:

- manifest revision;
- selection requests and responses;
- tool instance IDs;
- parameter changes;
- command requests and responses;
- lease events;
- safety-state changes;
- telemetry samples and events.

The recording manifest should include:

```text
robot identity
TeamCode/build identifier
Git commit if available
hardware configuration hash
debug registry revision
selected tool and tool version
parameter values at start
parameter changes
battery voltage
recording gaps
stop reason
```

Replay must reproduce the observable debug timeline without allowing any
replayed command to reach the robot.

## 13. Error codes

Use stable machine-readable codes and human-readable messages.

```text
UNSUPPORTED_PROTOCOL
STALE_MANIFEST
UNKNOWN_NODE
NODE_NOT_SELECTABLE
TOOL_DISABLED
TOOL_INSTANCE_MISMATCH
INVALID_PARAMETER
PARAMETER_OUT_OF_RANGE
PARAMETER_READ_ONLY
PERSISTENCE_REQUIRES_CONFIRMATION
UNKNOWN_COMMAND
COMMAND_EXPIRED
DUPLICATE_REQUEST
LEASE_REQUIRED
LEASE_EXPIRED
DRIVER_STATION_NOT_READY
ROBOT_NOT_STOPPED
MECHANISM_LOCKED
FLOOR_ACK_REQUIRED
OUTPUT_LIMIT_EXCEEDED
WATCHDOG_NOT_HEALTHY
TCP_SESSION_NOT_ACTIVE
TOOL_FAULT
HARDWARE_NOT_FOUND
```

## 14. Implementation phases

### Phase 1: protocol and registry

- Extract the hierarchical menu from the existing `Debugger.java` pattern into
  a reusable `DebugRegistry`.
- Add `DebugNode`, `DebugTool`, and explicit parameter definitions in TeamCode.
- Extend the protobuf envelope with manifest, selection, tool-ready, and state
  messages.
- Add golden encode/decode tests in Python and Java.

### Phase 2: selector OpMode

- Implement `DebuggerOpMode` as the one physical FTC OpMode.
- Connect the existing structured TCP client during `init()`.
- Publish the manifest after the handshake.
- Add a TCP reader thread that only queues decoded requests.
- Apply selections and parameter updates from the OpMode loop.

### Phase 3: laptop workbench

- Add backend debug-session ownership and request correlation.
- Add `/ws/debug`.
- Render folders and tools from the manifest.
- Render parameter controls from `ParameterDefinition`.
- Add safety-state and acknowledgement UI.

### Phase 4: first useful tool

- Implement free-spin motor testing.
- Publish motor power, encoder, velocity, current, voltage, temperature, and
  watchdog state.
- Enforce the free-spin policy on the robot.
- Record every test as a telemetry session.

### Phase 5: replay and MCP

- Add range-query and derived-metric APIs.
- Implement read-only MCP access to manifests, recordings, graphs, and events.
- Add PID metrics and comparison tools.
- Add human-approved actuation only for the free-spin tool.

## 15. Acceptance criteria

The first implementation is complete when all of the following are true:

1. The Driver Station can launch `FTC Advanced / Debugger`.
2. The laptop receives a versioned hierarchical manifest over TCP.
3. Folder selection never changes robot hardware state.
4. Selecting a leaf produces a runtime parameter and command schema.
5. The frontend renders a tool without hard-coded knowledge of every parameter.
6. A stale, duplicated, expired, malformed, or out-of-range request is safely
   rejected.
7. TCP loss disables tool actuator output within the configured watchdog time.
8. Driver Station stop always stops the selected tool.
9. Mechanism tools expose no actuator command to MCP and reject forged commands.
10. Every command and parameter change is visible in the recording.
11. A completed recording can be queried without reconnecting to the robot.
12. Replay cannot invoke a robot-side command.

## 16. Summary

The final system is a single physical Debugger OpMode with a remotely
discoverable, hierarchical tool registry. UDP launches and supervises it;
TCP provides the rich debugging interface; the backend owns arbitration and
recording; the frontend renders the laboratory workbench; and MCP becomes a
controlled analysis and experimentation client.

This gives the team the flexibility of Panels-style live configurables while
adding a typed protocol, explicit safety policy, replayability, and a stable
foundation for agent-assisted debugging.
