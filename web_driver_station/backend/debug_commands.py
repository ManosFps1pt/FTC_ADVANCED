"""Small, dependency-light builders for outbound debug command envelopes."""

from __future__ import annotations

import uuid

from .protocol import robot_data_pb2 as wire


def command_request_id(client_request_id: str | None) -> str:
    """Preserve a browser correlation ID or create one for legacy callers."""

    return client_request_id or str(uuid.uuid4())


def build_debug_command_request(
    *,
    request_id: str,
    node_id: str,
    tool_instance_id: str,
    command_id: str,
    ttl_ms: int,
    arguments: list[wire.DebugCommandArgumentValue],
) -> wire.DebugCommandRequest:
    """Build the wire command without changing the browser's correlation ID."""

    return wire.DebugCommandRequest(
        request_id=request_id,
        node_id=node_id,
        tool_instance_id=tool_instance_id,
        command_id=command_id,
        ttl_ms=ttl_ms,
        arguments=arguments,
    )
