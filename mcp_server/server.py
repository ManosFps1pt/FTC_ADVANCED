"""Read-only Streamable HTTP MCP server for FTC Advanced.

The tools intentionally return bounded status data. Replay history, raw
``.ftclog`` files, video, and robot-control commands are not exposed in this
first milestone.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

from .backend_client import DriverStationApi


logging.basicConfig(level=os.getenv("FTC_ADVANCED_MCP_LOG_LEVEL", "INFO"), format="%(levelname)s %(message)s")
logger = logging.getLogger("ftc-advanced-mcp")


def _csv_environment(name: str) -> list[str]:
    return [item.strip() for item in os.getenv(name, "").split(",") if item.strip()]


def _transport_security() -> TransportSecuritySettings | None:
    """Allow explicitly configured tunnel hosts, never arbitrary hosts."""

    allowed_hosts = _csv_environment("FTC_ADVANCED_MCP_ALLOWED_HOSTS")
    allowed_origins = _csv_environment("FTC_ADVANCED_MCP_ALLOWED_ORIGINS")
    if not allowed_hosts and not allowed_origins:
        return None
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
    )


_mcp_host = os.getenv("FTC_ADVANCED_MCP_HOST", "127.0.0.1")
_mcp_port = int(os.getenv("FTC_ADVANCED_MCP_PORT", "8001"))

mcp = FastMCP(
    "FTC Advanced",
    instructions=(
        "You have read-only access to the live FTC Advanced Driver Station. "
        "Use status tools before making claims about the robot. No tool in "
        "this server starts, stops, configures, or controls the robot."
    ),
    host=_mcp_host,
    port=_mcp_port,
    transport_security=_transport_security(),
)

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)


def _api() -> DriverStationApi:
    return DriverStationApi()


@mcp.tool(title="Driver Station status", annotations=READ_ONLY)
async def get_driver_station_status() -> dict[str, Any]:
    """Return concise connection and OpMode status from the Driver Station."""

    status = await _api().get("/api/status")
    assert isinstance(status, dict)
    return {
        "connected": bool(status.get("connected")),
        "robotController": status.get("host"),
        "robotState": status.get("robot_state"),
        "opModeRunning": bool(status.get("started_opmode")),
        "hasTelemetry": status.get("telemetry") is not None,
        "driverStationError": status.get("driver_station_error"),
    }


@mcp.tool(title="Robot data TCP status", annotations=READ_ONLY)
async def get_robot_data_status() -> dict[str, Any]:
    """Return concise status for the robot's structured telemetry TCP stream."""

    status = await _api().get("/api/data/status")
    assert isinstance(status, dict)
    telemetry = status.get("telemetry")
    recording = status.get("recording")
    return {
        "listening": bool(status.get("listening")),
        "connected": bool(status.get("connected")),
        "connectionCount": status.get("connection_count", 0),
        "peers": status.get("peers", []),
        "telemetry": telemetry if isinstance(telemetry, dict) else None,
        "recording": {
            "activeLogCount": recording.get("active_log_count", 0),
            "activeSessions": recording.get("active_sessions", []),
            "lastError": recording.get("last_error"),
        } if isinstance(recording, dict) else None,
    }


@mcp.tool(title="Telemetry status", annotations=READ_ONLY)
async def get_telemetry_status() -> dict[str, Any]:
    """Return the current telemetry session summary without samples or history."""

    status = await _api().get("/api/data/telemetry/status")
    assert isinstance(status, dict)
    return status


@mcp.tool(title="Latest telemetry snapshot", annotations=READ_ONLY)
async def get_latest_telemetry_snapshot() -> dict[str, Any]:
    """Return the latest telemetry values and at most ten recent events.

    This never returns replay history. Use the catalog tool to resolve signal
    names, units, and device associations.
    """

    state = await _api().get("/api/data/telemetry/latest")
    assert isinstance(state, dict)
    return {
        "session": state.get("session"),
        "snapshot": state.get("snapshot"),
        "recentEvents": state.get("recentEvents", []),
    }


@mcp.tool(title="Telemetry catalog", annotations=READ_ONLY)
async def get_telemetry_catalog() -> dict[str, Any]:
    """Return the active telemetry signal catalog, including units and roles."""

    state = await _api().get("/api/data/telemetry/latest")
    assert isinstance(state, dict)
    catalog = state.get("catalog")
    return {"session": state.get("session"), "catalog": catalog}


@mcp.tool(title="Debugger status", annotations=READ_ONLY)
async def get_debugger_status() -> dict[str, Any]:
    """Return the current debugger manifest, selected tool, and safety state."""

    status = await _api().get("/api/debug/status")
    assert isinstance(status, dict)
    return status


def main() -> None:
    """Start the Streamable HTTP endpoint used by a remote MCP client."""

    logger.info("Starting FTC Advanced MCP server on %s:%s/mcp", _mcp_host, _mcp_port)
    mcp.run(transport="streamable-http")
