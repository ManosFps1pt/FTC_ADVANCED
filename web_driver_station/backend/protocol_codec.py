"""Translate protobuf v2 telemetry into the backend's normalized dashboard model."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from .protocol import robot_data_pb2 as wire

WIRE_PROTOCOL_VERSION = 2


class WireProtocolError(ValueError):
    """A protobuf envelope violates the robot/laptop wire contract."""


def _uuid(value: bytes, name: str) -> str:
    if len(value) != 16:
        raise WireProtocolError(f"{name} must be a 16-byte UUID")
    return str(uuid.UUID(bytes=value))


def _common(envelope: wire.Envelope, message_type: str, data: Mapping[str, Any]) -> dict[str, Any]:
    if envelope.protocol_version != WIRE_PROTOCOL_VERSION:
        raise WireProtocolError("unsupported telemetry protocol version")
    return {
        "protocol": "ftc-telemetry",
        # The telemetry store's dictionary model predates protobuf. This is an
        # internal normalized representation, not a JSON wire version.
        "version": 1,
        "type": message_type,
        "sessionId": _uuid(envelope.session_id, "session_id"),
        "connectionId": _uuid(envelope.connection_id, "connection_id"),
        "sequence": str(envelope.connection_sequence),
        "robotTimeNs": str(envelope.robot_elapsed_ns),
        "data": dict(data),
    }


def decode(envelope: wire.Envelope, channel_keys: dict[int, tuple[str, int]]) -> tuple[dict[str, Any], ...]:
    """Decode one protobuf frame into one or more normalized telemetry records."""

    body = envelope.WhichOneof("body")
    if body is None:
        raise WireProtocolError("envelope body is required")
    if body == "hello":
        value = envelope.hello
        return (_common(envelope, "hello", {
            "robotId": value.robot_id, "robotName": value.robot_name, "opModeName": value.op_mode_name,
            "startedAtRobotTimeNs": str(value.started_at_robot_time_ns), "queueCapacity": value.queue_capacity,
            "capabilities": list(value.capabilities),
        }),)
    if body == "schema":
        value = envelope.schema
        devices = {device.device_id: device.key for device in value.devices}
        channel_keys.clear()
        channel_keys.update({channel.channel_id: (channel.key, channel.value_type) for channel in value.channels})
        return (_common(envelope, "catalog", {
            "schemaRevision": value.revision,
            "devices": [{"id": item.key, "label": item.label, "subsystem": item.subsystem, "deviceType": item.device_type} for item in value.devices],
            "signals": [{
                "id": item.key, "label": item.label,
                **({"deviceId": devices[item.device_id]} if item.device_id else {}),
                "quantity": item.quantity, "unit": item.unit,
                "valueType": wire.ValueType.Name(item.value_type).lower(),
                "role": wire.ChannelRole.Name(item.role).lower(),
                **({"sampleHintHz": item.sample_hint_hz} if item.sample_hint_hz else {}),
                **({"description": item.description} if item.description else {}),
            } for item in value.channels],
        }),)
    if body == "sample_batch":
        if not channel_keys:
            raise WireProtocolError("sample batch received before schema")
        records: list[dict[str, Any]] = []
        for snapshot in envelope.sample_batch.snapshots:
            values: dict[str, Any] = {}
            for item in snapshot.values:
                channel = channel_keys.get(item.channel_id)
                if channel is None:
                    raise WireProtocolError("sample references an unknown or duplicate channel")
                key, value_type = channel
                if key in values:
                    raise WireProtocolError("sample references an unknown or duplicate channel")
                value = item.WhichOneof("value")
                if value is None:
                    raise WireProtocolError("sample channel value is required")
                raw = None if value == "unavailable" else getattr(item, value)
                # The normalized dashboard model represents int64 as a string
                # because its browser API must retain every 64-bit value.
                values[key] = str(raw) if raw is not None and value_type == wire.INT64 else raw
            records.append(_common(envelope, "sample", {
                "sampleSequence": str(snapshot.sample_sequence), "schemaRevision": snapshot.schema_revision, "values": values,
            }))
        if not records:
            raise WireProtocolError("sample batch must not be empty")
        return tuple(records)
    if body == "gamepad":
        return (_common(envelope, "gamepad", {"gamepad1": _gamepad(envelope.gamepad.gamepad1), "gamepad2": _gamepad(envelope.gamepad.gamepad2)}),)
    if body == "heartbeat":
        value = envelope.heartbeat
        return (_common(envelope, "heartbeat", {"queuedFrames": value.queued_frames, "droppedSamples": str(value.dropped_samples), "lastSampleSequence": str(value.last_sample_sequence)}),)
    if body == "gap":
        value = envelope.gap
        return (_common(envelope, "gap", {"firstMissingSampleSequence": str(value.first_missing_sample_sequence), "lastMissingSampleSequence": str(value.last_missing_sample_sequence), "reason": value.reason}),)
    if body == "event":
        attributes: dict[str, Any] = {}
        for attribute in envelope.event.attributes:
            value_name = attribute.WhichOneof("value")
            if not attribute.key or value_name is None:
                raise WireProtocolError("event attribute key and value are required")
            attributes[attribute.key] = None if value_name == "null_value" else getattr(attribute, value_name)
        return (_common(envelope, "event", {
            "name": envelope.event.name,
            "severity": wire.Severity.Name(envelope.event.severity).lower(),
            "attributes": attributes,
        }),)
    if body == "session_end":
        value = envelope.session_end
        return (_common(envelope, "session_end", {"reason": value.reason, "lastSampleSequence": str(value.last_sample_sequence)}),)
    if body == "debug_manifest":
        value = envelope.debug_manifest
        return (_common(envelope, "debug_manifest", {
            "revision": value.revision,
            "registryId": value.registry_id,
            "registryLabel": value.registry_label,
            "activeNodeId": value.active_node_id,
            "nodes": [{
                "id": node.id,
                "parentId": node.parent_id,
                "label": node.label,
                "description": node.description,
                "kind": wire.DebugNodeKind.Name(node.kind).lower(),
                "sortOrder": node.sort_order,
                "riskClass": wire.DebugRiskClass.Name(node.risk_class).lower(),
                "opModeLabel": node.op_mode_label,
                "selectable": node.selectable,
                "enabled": node.enabled,
                "disabledReason": node.disabled_reason,
                "maxOutput": node.max_output,
                "maxActiveDurationMs": node.max_active_duration_ms,
                "parameterIds": list(node.parameter_ids),
                "commandIds": list(node.command_ids),
            } for node in value.nodes],
        }),)
    if body == "debug_select_response":
        value = envelope.debug_select_response
        return (_common(envelope, "debug_select_response", {
            "requestId": value.request_id, "accepted": value.accepted, "nodeId": value.node_id,
            "state": wire.DebugSessionState.Name(value.state).lower(),
            "rejectionCode": value.rejection_code, "message": value.message,
        }),)
    if body == "debug_tool_ready":
        value = envelope.debug_tool_ready
        return (_common(envelope, "debug_tool_ready", {
            "nodeId": value.node_id, "toolInstanceId": value.tool_instance_id,
            "manifestRevision": value.manifest_revision,
            "parameters": [{
                "id": item.id, "label": item.label, "description": item.description,
                "valueType": wire.ValueType.Name(item.value_type).lower(),
                "current": item.current_float64, "min": item.min_float64, "max": item.max_float64,
                "step": item.step_float64, "unit": item.unit, "writable": item.writable,
                "persistent": item.persistent,
                "updatePolicy": wire.DebugParameterUpdatePolicy.Name(item.update_policy).lower(),
            } for item in value.parameters],
            "commands": [{
                "id": item.id, "label": item.label, "description": item.description,
                "requiresHumanAcknowledgement": item.requires_human_acknowledgement,
                "arguments": [{
                    "id": argument.id, "label": argument.label, "description": argument.description,
                    "valueType": wire.ValueType.Name(argument.value_type).lower(),
                    "required": argument.required, "unit": argument.unit,
                    "min": argument.min_float64, "max": argument.max_float64,
                    "enumOptions": [{"id": option.id, "label": option.label} for option in argument.enum_options],
                } for argument in item.arguments],
            } for item in value.commands],
            "telemetryChannelIds": list(value.telemetry_channel_ids),
            "state": wire.DebugSessionState.Name(value.state).lower(), "message": value.message,
        }),)
    if body == "debug_parameter_set_response":
        value = envelope.debug_parameter_set_response
        return (_common(envelope, "debug_parameter_set_response", {
            "requestId": value.request_id,
            "result": wire.DebugParameterSetResult.Name(value.result).lower(),
            "parameterId": value.parameter_id, "effectiveFloat64": value.effective_float64,
            "rejectionCode": value.rejection_code, "message": value.message,
            "appliedAtRobotTimeNs": str(value.applied_at_robot_time_ns),
        }),)
    if body == "debug_command_response":
        value = envelope.debug_command_response
        return (_common(envelope, "debug_command_response", {
            "requestId": value.request_id, "result": wire.DebugCommandResult.Name(value.result).lower(),
            "commandId": value.command_id, "rejectionCode": value.rejection_code,
            "message": value.message, "handledAtRobotTimeNs": str(value.handled_at_robot_time_ns),
            "outputs": [{
                "id": output.id,
                "valueType": output.WhichOneof("value"),
                "value": None if output.WhichOneof("value") is None else getattr(output, output.WhichOneof("value")),
            } for output in value.outputs],
        }),)
    if body == "debug_tool_state":
        value = envelope.debug_tool_state
        return (_common(envelope, "debug_tool_state", {
            "nodeId": value.node_id, "toolInstanceId": value.tool_instance_id,
            "state": wire.DebugSessionState.Name(value.state).lower(),
            "outputEnabled": value.output_enabled, "requestedOutput": value.requested_output,
            "appliedOutput": value.applied_output, "outputUnit": value.output_unit,
            "statusMessage": value.status_message,
        }),)
    if body == "debug_safety_state":
        value = envelope.debug_safety_state
        return (_common(envelope, "debug_safety_state", {
            "tcpConnected": value.tcp_connected, "watchdogHealthy": value.watchdog_healthy,
            "outputAllowed": value.output_allowed, "blockedReason": value.blocked_reason,
        }),)
    raise WireProtocolError(f"robot cannot send {body}")


def _gamepad(value: wire.Gamepad) -> dict[str, Any]:
    return {
        "leftStickX": value.left_stick_x, "leftStickY": value.left_stick_y, "rightStickX": value.right_stick_x, "rightStickY": value.right_stick_y,
        "leftTrigger": value.left_trigger, "rightTrigger": value.right_trigger,
        "a": value.a, "b": value.b, "x": value.x, "y": value.y,
        "dpadUp": value.dpad_up, "dpadDown": value.dpad_down, "dpadLeft": value.dpad_left, "dpadRight": value.dpad_right,
        "leftBumper": value.left_bumper, "rightBumper": value.right_bumper,
        "leftStickButton": value.left_stick_button, "rightStickButton": value.right_stick_button,
        "back": value.back, "start": value.start, "guide": value.guide,
    }
