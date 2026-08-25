# Robot Telemetry Packet Format (v2)

**Status:** shipping contract

The Control Hub sends diagnostics to the laptop on a separate, observability-only connection. It never carries drive, safety, or lifecycle commands. Losing this connection must not delay an OpMode or alter robot output.

## Wire format

The protocol is Protocol Buffers, defined by the checked-in canonical schema:

- [`protocol/robot_data.proto`](protocol/robot_data.proto)

Each TCP frame is exactly:

```text
uint32_be frame_length
frame_length bytes: serialized ftc.robotdata.v2.Envelope
```

Frames must be non-empty and no larger than 1 MiB. Receivers must read the prefix and body with `readExactly`; TCP reads have no message boundaries.

Protocol version **2** is intentionally incompatible with the old framed JSON debug transport. An `Envelope` contains 16-byte binary UUIDs, uint64 sequence and monotonic-time fields, plus one protobuf `body`. Unknown protobuf fields are preserved by compatible readers and must be ignored unless required for the message being processed.

## Connection lifecycle

After every TCP connection, including reconnects, the Control Hub sends:

1. `Hello` and waits for `HelloAck`.
2. `Schema`, which assigns stable numeric `channel_id` values.
3. `SampleBatch`, `Event`, `Heartbeat`, `Gap`, `GamepadSnapshot`, and finally best-effort `SessionEnd` messages as appropriate.

The server returns `HelloAck` only for a valid compatible `Hello`. It does not acknowledge samples; TCP already provides ordered reliable delivery.

## Encoding rules

- `session_id` survives reconnects; `connection_id` changes per socket.
- `connection_sequence` increases per connection. `sample_sequence` increases for the entire session.
- `robot_elapsed_ns` is `SystemClock.elapsedRealtimeNanos()`, never wall time.
- Schema metadata remains descriptive, but samples use only numeric `channel_id`s. This avoids repeating long string keys at high rates.
- A `SampleBatch` contains up to 32 snapshots and is emitted after at most 20 ms, keeping latency low while amortizing TCP and protobuf overhead.
- `ChannelValue.unavailable = true` represents an unavailable measurement. It is distinct from numeric zero and must be rendered as unavailable.
- Values must agree with the declared `ValueType`; non-finite float inputs are encoded as unavailable.
- When the bounded RC queue drops samples, the RC increments its drop count and emits a `Gap` as soon as practical. Readers must not interpolate gaps.

## Discovery

UDP port 5811 uses a compact binary request/response, not JSON:

```text
request:  "FTRD" | uint8 protocol_version (2) | 16-byte nonce
response: "FTRD" | uint8 protocol_version (2) | echoed nonce | uint16_be tcp_port
```

The TCP peer address from the response is the server address. The nonce prevents accepting unrelated broadcasts. The data stream listens on TCP port 5810 by default; neither port overlaps FTC Robocol.

## Compatibility and generation

Never reuse protobuf field numbers or a channel key for a different meaning. Adding optional fields is compatible; changing semantics or value type requires a new channel key or a new major protocol version. Generated Java-lite sources and Python bindings are checked in. Regenerate them from `robot_data.proto` with Protobuf 25.5 whenever the schema changes, then run the protocol tests.

The browser API remains normalized JSON over WebSocket. It is a local presentation boundary, not the robot/laptop telemetry transport.
