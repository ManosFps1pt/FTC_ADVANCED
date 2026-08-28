# TCP Robot Commands Specification

**Status:** proposed implementation specification  
**Revision:** 1  
**Date:** 2026-08-28  
**Scope:** typed, explicit commands sent from the laptop to a running FTC OpMode

## 1. Design position

This is the right next capability for the platform, provided that TCP commands
are treated as a typed remote procedure interface rather than as remote Java
reflection.

The laptop must never send an arbitrary method name, Java expression, script,
or serialized object for the robot to execute. The OpMode explicitly registers
the commands it is willing to expose. The laptop discovers those registered
commands, renders their argument forms, and sends requests using stable command
IDs.

For example, the robot may expose:

```text
alliance.set
  isRed: boolean, required
```

The implementation behind that command may be a normal Java method:

```java
setAlliance(boolean isRed)
```

However, the wire contract contains only `alliance.set` and the typed argument
`isRed`. It does not contain or invoke a method by name.

This distinction gives the platform predictable behavior, validation, audit
records, and a safe place to add permissions later.

## 2. Responsibility split

The two robot-control paths have separate jobs:

| Path | Responsibility |
|---|---|
| Driver Station / UDP gamepad path | Continuous human driving input and FTC lifecycle |
| Structured TCP path | Discrete, intentional, typed commands and command results |

TCP commands are not a replacement for gamepad input. A command such as
`alliance.set` is suitable for selecting configuration. A command such as
`drive.setPower` would require explicit actuator safety policy and should not
be treated as a high-rate substitute for the gamepad path.

The Driver Station remains authoritative for `INIT`, `START`, `STOP`, heartbeat,
and emergency stop. A TCP disconnect must not prevent the Driver Station from
stopping the OpMode.

## 3. End-to-end flow

```text
OpMode registers commands during init()
              |
              v
Robot sends Hello, then DebugManifest / DebugToolReady
              |
              v
Backend validates and stores the discovered command definitions
              |
              v
Browser sends JSON to the backend command endpoint
              |
              v
Backend validates local policy and encodes a protobuf request
              |
              v
Backend sends the length-prefixed request over the existing TCP socket
              |
              v
Robot TCP reader decodes and queues the request
              |
              v
OpMode loop validates and invokes the registered handler
              |
              v
Robot sends a protobuf response; backend forwards the result to the UI
```

The TCP server does not discover Java methods. The robot sends a command
catalog, and the server stores that catalog as untrusted capability metadata.
The robot remains the final authority and validates every request again.

## 4. Command registration on the robot

Commands should be registered explicitly in the OpMode or its selected debug
tool. Registration should happen before the TCP session begins publishing the
manifest.

A conceptual Java API is:

```java
private boolean allianceIsRed;

private void configureCommands(CommandRegistry commands) {
    commands.register(CommandSpec.builder("alliance.set", "Set Alliance")
            .description("Select the alliance used by autonomous and field-relative logic")
            .riskClass(RiskClass.OBSERVE_ONLY)
            .argument(CommandArgument.booleanValue("isRed", "Red alliance", true))
            .handler(arguments -> setAlliance(arguments.booleanValue("isRed")))
            .build());
}

private void setAlliance(boolean isRed) {
    allianceIsRed = isRed;
}
```

The exact builder names may change during implementation. The semantic rules
do not:

- every exposed command has a stable ID;
- every argument has a stable ID, type, and required/optional policy;
- the handler is registered by the OpMode, not looked up through reflection;
- command handlers are short, deterministic state transitions;
- a command has one unambiguous action;
- toggles such as `changeAlliance()` are not exposed;
- commands that need current state should be explicit, for example
  `alliance.set` or `alliance.reset`, not an unpredictable toggle;
- command registration is immutable after the manifest/tool-ready message;
- a command may return a small typed result, but it must not return arbitrary
  Java objects.

For a normal OpMode, command handlers may update OpMode-owned state. For an
actuator-producing command, the handler must pass through the same safety and
ownership rules used by the debugger tool layer.

## 5. Protobuf wire encoding

The command protocol reuses the existing `ftc.robotdata.v2.Envelope` and its
length-prefixed TCP framing:

```text
uint32_be frame_length
frame_length bytes: serialized ftc.robotdata.v2.Envelope
```

There is no newline, JSON, delimiter, or one-TCP-read-per-message assumption.
The envelope header is encoded exactly as it is for the existing TCP protocol:

```proto
protocol_version: 2
session_id: 16-byte UUID
connection_id: 16-byte UUID
connection_sequence: monotonically increasing per TCP connection
robot_elapsed_ns: robot monotonic timestamp
```

The current checked-in command messages are sufficient for commands without
arguments, but they do not yet carry typed arguments. Add fields without
reusing existing field numbers:

```proto
message DebugCommand {
  string id = 1;
  string label = 2;
  string description = 3;
  bool requires_human_acknowledgement = 4;
  repeated DebugCommandArgumentDefinition arguments = 5;
}

message DebugCommandArgumentDefinition {
  string id = 1;
  string label = 2;
  string description = 3;
  ValueType value_type = 4;
  bool required = 5;
  string unit = 6;
  double min_float64 = 7;
  double max_float64 = 8;
  repeated DebugEnumOption enum_options = 9;
}

message DebugEnumOption {
  string id = 1;
  string label = 2;
}

message DebugCommandArgumentValue {
  string id = 1;
  oneof value {
    double float64_value = 2;
    sint64 int64_value = 3;
    bool boolean_value = 4;
    string string_value = 5;
    string enum_value = 6;
  }
}

message DebugCommandRequest {
  string request_id = 1;
  string node_id = 2;
  string tool_instance_id = 3;
  string command_id = 4;
  uint32 ttl_ms = 5;
  repeated DebugCommandArgumentValue arguments = 6;
}

message DebugCommandResponse {
  string request_id = 1;
  DebugCommandResult result = 2;
  string command_id = 3;
  string rejection_code = 4;
  string message = 5;
  uint64 handled_at_robot_time_ns = 6;
  repeated DebugCommandArgumentValue outputs = 7;
}

enum DebugCommandResult {
  DEBUG_COMMAND_RESULT_UNSPECIFIED = 0;
  DEBUG_COMMAND_ACCEPTED = 1;
  DEBUG_COMMAND_COMPLETED = 2;
  DEBUG_COMMAND_REJECTED = 3;
  DEBUG_COMMAND_EXPIRED = 4;
  DEBUG_COMMAND_DUPLICATE = 5;
  DEBUG_COMMAND_FAILED = 6;
}
```

Existing field numbers must remain unchanged. The generated Java-lite and
Python bindings must be regenerated from `protocol/robot_data.proto` after
this additive schema change.

### 5.1 Example command definition

The robot advertises `alliance.set` inside `DebugToolReady.commands`:

```text
commands {
  id: "alliance.set"
  label: "Set Alliance"
  description: "Select the alliance used by robot logic"
  arguments {
    id: "isRed"
    label: "Red alliance"
    value_type: BOOLEAN
    required: true
  }
  requires_human_acknowledgement: false
}
```

The exact protobuf binary is generated by Protobuf serialization. The
human-readable form above is a decoded representation for diagnostics, not a
second wire format.

### 5.2 Example command request

The backend sends this decoded protobuf message:

```text
protocol_version: 2
session_id: <16 bytes>
connection_id: <16 bytes>
connection_sequence: 17
robot_elapsed_ns: 0

debug_command_request {
  request_id: "6b1e3f0f-8c4d-4f23-9c43-9e6d2e7d3f5a"
  node_id: "opmode.autonomous"
  tool_instance_id: "tool-instance-42"
  command_id: "alliance.set"
  ttl_ms: 1000
  arguments {
    id: "isRed"
    boolean_value: true
  }
}
```

On the actual TCP connection this message is preceded by its four-byte
big-endian serialized length and contains protobuf wire bytes, not the text
shown above.

## 6. Browser and backend behavior

The browser should use a JSON API because it is a local application boundary;
it should never open a second raw TCP socket to the robot.

Example browser request:

```http
POST /api/debug/commands
Content-Type: application/json
```

```json
{
  "nodeId": "opmode.autonomous",
  "toolInstanceId": "tool-instance-42",
  "commandId": "alliance.set",
  "arguments": {
    "isRed": true
  },
  "ttlMs": 1000
}
```

The backend command service should:

1. require an active, accepted robot TCP session;
2. require the command to exist in the latest robot-advertised definition;
3. require the `nodeId` and `toolInstanceId` to match the current selection;
4. validate argument names, required arguments, types, enum values, and local
   bounds;
5. apply local authorization, lease, and human-acknowledgement policy;
6. generate a fresh UUID `request_id` rather than trusting a browser-supplied
   request ID;
7. encode the request as `DebugCommandRequest` inside an `Envelope`;
8. send it through the existing single-owner `RobotDataTcpServer.send()` path;
9. return an immediate transport-accepted response to the browser containing
   the request ID;
10. forward the later robot response over the existing WebSocket/status path.

The immediate HTTP response means “the request was queued for the robot TCP
connection.” It does not mean that the OpMode handler ran successfully.

The robot response is authoritative for completion, rejection, expiration, or
failure.

## 7. Robot-side receive and execution model

The TCP reader must never call an OpMode command handler and must never read or
write hardware. Its only job is to decode a complete frame and enqueue the
validated protobuf message for the OpMode loop.

At each OpMode loop boundary, the OpMode processes a bounded number of queued
messages:

```text
TCP reader thread
    |
    | DebugCommandRequest
    v
bounded incoming queue
    |
    | next OpMode loop
    v
validate envelope and request identity
    |
validate selected node/tool instance
    |
find registered command ID
    |
validate arguments against command definition
    |
check TTL, lease, safety, and acknowledgement state
    |
invoke registered handler on OpMode thread
    |
enqueue DebugCommandResponse for TCP writer
```

The execution sequence is normative:

1. Reject malformed or unsupported protobuf messages.
2. Reject a missing, blank, or duplicate argument ID.
3. Reject an unknown `command_id`; never fall back to a similarly named
   function.
4. Reject a stale `tool_instance_id` so a previous tool cannot affect a newly
   selected tool.
5. Reject a stale or disabled node.
6. Reject a request whose TTL has expired. TTL is measured against the robot's
   monotonic clock, not wall time.
7. Validate all arguments before invoking the handler. No partial application
   is allowed.
8. Check command-specific safety policy immediately before execution.
9. Invoke the registered handler on the OpMode thread only.
10. Send exactly one terminal response for the request.

For `alliance.set`, the handler receives one typed boolean and updates the
OpMode's alliance state. It does not toggle the current value and does not
infer intent from the previous state.

## 8. Results, retries, and idempotency

The existing `request_id` is the idempotency key. The robot should keep a
bounded cache of recent request IDs and terminal responses.

If the same request ID arrives again:

- do not invoke the handler again;
- return the cached response;
- report a duplicate or replayed request according to the final enum choice.

Commands should be designed to be idempotent where practical:

```text
alliance.set(isRed=true)       predictable and idempotent
encoder.reset()                can be idempotent if defined carefully
alliance.toggle()              prohibited
drive.increasePower()          prohibited unless an explicit value is supplied
```

Recommended result meanings are:

```text
COMMAND_ACCEPTED   request passed validation and was queued
COMMAND_COMPLETED  handler ran successfully
COMMAND_REJECTED   validation, policy, or safety denied execution
COMMAND_EXPIRED    TTL elapsed before execution
COMMAND_FAILED     handler ran but reported an operational failure
```

If a command may take multiple OpMode loops, `COMMAND_ACCEPTED` should be sent
first and a terminal `COMMAND_COMPLETED`, `COMMAND_FAILED`, or
`COMMAND_EXPIRED` response should follow. A short setter such as
`alliance.set` can normally return one completed response after the loop-boundary
handler runs.

## 9. Safety and authorization

Every command definition should carry or inherit a risk classification:

```text
OBSERVE_ONLY   no actuator output
CONFIGURATION  changes robot state or configuration
MECHANISM      may move a mechanism
DRIVETRAIN     may move the drivetrain
```

`alliance.set` is a configuration command and does not require a physical-floor
acknowledgement. A command that can energize a motor should require the relevant
lease, safety conditions, and human acknowledgement before the backend sends
it. The robot must repeat those checks; backend policy is not a substitute for
robot-side policy.

The following must never be accepted as a command payload:

- arbitrary Java method names;
- class names or reflection paths;
- Java, Python, shell, or JavaScript source;
- raw hardware-map names without a registered command definition;
- unbounded numeric values;
- a request that silently changes the selected tool or target node.

## 10. Connection loss and lifecycle

TCP commands are best-effort discrete commands, not a lifecycle authority.

- If the connection is lost before execution, the queued request may expire or
  be rejected; it must not block the OpMode loop.
- If the connection is lost after execution, the robot must not undo a
  completed state change unless the command explicitly defines an undo action.
- Actuator-producing tools must stop outputs using their watchdog and the
  normal OpMode stop path.
- A reconnect creates a new `connection_id`, but the session and command/tool
  identity rules still prevent stale commands from affecting a new tool
  instance.

## 11. Recording and observability

The raw recorder should preserve both directions of the exchange:

- the command definition that was advertised;
- the exact `DebugCommandRequest` protobuf payload;
- receipt time and peer identity;
- the exact `DebugCommandResponse` protobuf payload;
- the normalized browser/API request;
- the selected node, tool instance, and operator identity when available.

The dashboard should show at least:

```text
command ID | arguments | request ID | queued | completed/rejected | reason | robot time
```

This makes a command auditable even when the browser reconnects or the robot
TCP connection drops.

## 12. Recommended implementation order

1. Extend `protocol/robot_data.proto` with typed command definitions,
   argument values, and command outputs.
2. Regenerate Java-lite and Python protobuf bindings.
3. Add an explicit command registry/handler API to the Debugger OpMode.
4. Add robot-side argument validation, bounded queue execution, idempotency,
   and terminal responses.
5. Extend the backend `/api/debug/commands` request model and protobuf encoder.
6. Add WebSocket command-status updates and raw recording.
7. Add a dynamic command form in the frontend from `DebugToolReady.commands`.
8. Implement `alliance.set(isRed)` as the first non-actuator integration test.
9. Add safety-gated actuator commands only after the complete request/result
   path is reliable.
