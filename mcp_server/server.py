"""Streamable HTTP MCP server for the bounded FTC Advanced debugger surface."""

from __future__ import annotations

import logging
import os
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

from .backend_client import BackendError, DriverStationApi


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
        "You can observe the live FTC Advanced Driver Station, launch an exact "
        "OpMode advertised by the connected Robot Controller, and control only "
        "its registered free-spin debugger tools. Use debugger discovery before "
        "selecting a tool or actuating a motor. Never use these tools for "
        "drivetrain or mechanism actuation. Motor power is limited to ±0.35 and "
        "automatically expires after at most 500 ms."
    ),
    host=_mcp_host,
    port=_mcp_port,
    transport_security=_transport_security(),
)

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)
DEBUGGER_CONTROL = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False
)
DEBUGGER_OPMODE_NAME = "FTC Advanced Debugger"
FREE_SPIN_RISK_CLASS = "debug_free_spin"
FREE_SPIN_POWER_LIMIT = 0.35


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


def _free_spin_node(status: dict[str, Any], node_id: str) -> dict[str, Any]:
    """Validate that an action can only target an advertised free-spin leaf."""

    manifest = status.get("manifest")
    if not isinstance(manifest, dict):
        raise ValueError("Debugger manifest is not available; launch and wait for the Debugger OpMode")
    nodes = manifest.get("nodes")
    if not isinstance(nodes, list):
        raise ValueError("Debugger manifest has no tools")
    node = next((item for item in nodes if isinstance(item, dict) and item.get("id") == node_id), None)
    if not isinstance(node, dict):
        raise ValueError(f"Debugger tool is not advertised: {node_id}")
    if node.get("kind") != "debug_tool" or node.get("riskClass") != FREE_SPIN_RISK_CLASS:
        raise ValueError("MCP controls only registered free-spin debugger tools")
    if not node.get("selectable") or not node.get("enabled"):
        raise ValueError(str(node.get("disabledReason") or "Debugger tool is not enabled"))
    return node


def _ready_free_spin_tool(status: dict[str, Any], node_id: str, tool_instance_id: str) -> dict[str, Any]:
    _free_spin_node(status, node_id)
    ready = status.get("tool_ready")
    if not isinstance(ready, dict) or ready.get("nodeId") != node_id:
        raise ValueError("Select this free-spin tool and wait for its runtime schema")
    if ready.get("toolInstanceId") != tool_instance_id:
        raise ValueError("The supplied tool instance is stale; rediscover the selected tool")
    return ready


@mcp.tool(title="Launch FTC Advanced Debugger", annotations=DEBUGGER_CONTROL)
async def launch_debugger_opmode(timeout_s: float = 5.0) -> dict[str, Any]:
    """Launch the fixed FTC Advanced Debugger OpMode through the Driver Station.

    This intentionally cannot launch arbitrary OpModes. The Driver Station
    verifies that this named OpMode is advertised by the connected robot.
    """

    return await launch_opmode(DEBUGGER_OPMODE_NAME, timeout_s)


@mcp.tool(title="Available OpModes", annotations=READ_ONLY)
async def list_available_opmodes() -> dict[str, Any]:
    """List the OpModes currently advertised by the connected Robot Controller."""

    opmodes = await _api().get("/api/opmodes")
    assert isinstance(opmodes, list)
    return {"opmodes": opmodes}


@mcp.tool(title="Launch advertised OpMode", annotations=DEBUGGER_CONTROL)
async def launch_opmode(name: str, timeout_s: float = 5.0) -> dict[str, Any]:
    """Stop any running user OpMode, then launch an exact advertised OpMode.

    Use ``list_available_opmodes`` first. Internal FTC stop sentinels cannot
    be launched through MCP, and the Driver Station verifies the requested
    name again immediately before changing the robot lifecycle.
    """

    if not 0 < timeout_s <= 15:
        raise ValueError("timeout_s must be between 0 and 15 seconds")
    if not name or name.startswith("$"):
        raise ValueError("An explicitly advertised user OpMode name is required")
    opmodes = await _api().get("/api/opmodes")
    assert isinstance(opmodes, list)
    if name not in {item.get("name") for item in opmodes if isinstance(item, dict)}:
        raise ValueError(f"OpMode is not advertised by the connected Robot Controller: {name}")
    request = {"name": name, "timeout_s": timeout_s}
    try:
        result = await _api().post("/api/opmodes/launch", request)
    except BackendError as error:
        # The initial Driver Station debugger endpoint already delegates to
        # ``launch_opmode`` and accepts any advertised OpMode name. Keep this
        # compatibility path only while a running backend has not yet been
        # restarted with the dedicated generic route above.
        if error.status_code not in (404, 405):
            raise
        result = await _api().post("/api/debug/launch", request)
    assert isinstance(result, dict)
    return result


@mcp.tool(title="Stop active OpMode", annotations=DEBUGGER_CONTROL)
async def stop_opmode() -> dict[str, Any]:
    """Immediately stop the active user OpMode through the Driver Station."""

    result = await _api().post("/api/opmodes/stop", {})
    assert isinstance(result, dict)
    return result


@mcp.tool(title="Discover Debugger functionality", annotations=READ_ONLY)
async def discover_debugger_functionality() -> dict[str, Any]:
    """List the live debugger tree, selected-tool schema, safety state, and benchmark inputs."""

    status = await _api().get("/api/debug/status")
    assert isinstance(status, dict)
    return {
        "connected": status.get("connected"),
        "manifest": status.get("manifest"),
        "selectedTool": status.get("tool_ready"),
        "toolState": status.get("tool_state"),
        "safety": status.get("safety"),
        "lastEvent": status.get("last_event"),
        "mcpControlScope": "registered free-spin tools only; drivetrain and mechanism tools are blocked",
    }


@mcp.tool(title="Select free-spin debugger tool", annotations=DEBUGGER_CONTROL)
async def select_free_spin_tool(node_id: str) -> dict[str, Any]:
    """Select an enabled, registered free-spin tool and request its live command schema."""

    status = await _api().get("/api/debug/status")
    assert isinstance(status, dict)
    manifest = status.get("manifest")
    _free_spin_node(status, node_id)
    assert isinstance(manifest, dict)
    result = await _api().post("/api/debug/select", {
        "node_id": node_id,
        "manifest_revision": manifest.get("revision", 1),
    })
    assert isinstance(result, dict)
    return result


@mcp.tool(title="Set free-spin motor power", annotations=DEBUGGER_CONTROL)
async def set_free_spin_motor_power(
    node_id: str, tool_instance_id: str, power: float, duration_ms: int = 500
) -> dict[str, Any]:
    """Set a selected free-spin motor's bounded power for at most 500 ms.

    The robot independently rejects values beyond its registered output limit
    and its watchdog returns the motor to zero when this command expires.
    """

    if not -FREE_SPIN_POWER_LIMIT <= power <= FREE_SPIN_POWER_LIMIT:
        raise ValueError("power must be between -0.35 and 0.35")
    if not 1 <= duration_ms <= 500:
        raise ValueError("duration_ms must be between 1 and 500")
    status = await _api().get("/api/debug/status")
    assert isinstance(status, dict)
    ready = _ready_free_spin_tool(status, node_id, tool_instance_id)
    parameter = next((item for item in ready.get("parameters", []) if item.get("id") == "motor.power"), None)
    if not isinstance(parameter, dict) or not parameter.get("writable"):
        raise ValueError("The selected free-spin tool does not advertise writable motor.power")
    result = await _api().post("/api/debug/parameters", {
        "node_id": node_id,
        "tool_instance_id": tool_instance_id,
        "parameter_id": "motor.power",
        "value": power,
        "ttl_ms": duration_ms,
    })
    assert isinstance(result, dict)
    return {**result, "requestedPower": power, "expiresAfterMs": duration_ms}


@mcp.tool(title="Stop free-spin motor", annotations=DEBUGGER_CONTROL)
async def stop_free_spin_motor(node_id: str, tool_instance_id: str) -> dict[str, Any]:
    """Immediately request zero output from a selected free-spin motor tool."""

    status = await _api().get("/api/debug/status")
    assert isinstance(status, dict)
    ready = _ready_free_spin_tool(status, node_id, tool_instance_id)
    if not any(item.get("id") == "motor.stop" for item in ready.get("commands", [])):
        raise ValueError("The selected free-spin tool does not advertise motor.stop")
    result = await _api().post("/api/debug/commands", {
        "node_id": node_id,
        "tool_instance_id": tool_instance_id,
        "command_id": "motor.stop",
        "arguments": {},
        "ttl_ms": 1_000,
    })
    assert isinstance(result, dict)
    return result


@mcp.tool(title="Run friction benchmark", annotations=DEBUGGER_CONTROL)
async def run_friction_benchmark(
    node_id: str,
    tool_instance_id: str,
    repetitions: int = 3,
    max_voltage: float = 12.0,
    reverse: bool = False,
) -> dict[str, Any]:
    """Start the selected tool's advertised friction/free-spin benchmark.

    The returned runId identifies the live status and the immutable result
    report available after robot-side acquisition and laptop analysis finish.
    """

    status = await _api().get("/api/debug/status")
    assert isinstance(status, dict)
    ready = _ready_free_spin_tool(status, node_id, tool_instance_id)
    if not any(item.get("id") == "motor.friction.v1" for item in ready.get("benchmarks", [])):
        raise ValueError("The selected free-spin tool does not advertise the friction benchmark")
    result = await _api().post("/api/debug/runs", {
        "node_id": node_id,
        "tool_instance_id": tool_instance_id,
        "benchmark_id": "motor.friction.v1",
        "repetitions": repetitions,
        "max_voltage": max_voltage,
        "reverse": reverse,
    })
    assert isinstance(result, dict)
    return result


@mcp.tool(title="Friction benchmark runs", annotations=READ_ONLY)
async def list_friction_benchmark_runs() -> dict[str, Any]:
    """Return live and completed friction benchmark runs, including their states."""

    runs = await _api().get("/api/debug/runs")
    assert isinstance(runs, list)
    return {"runs": runs}


@mcp.tool(title="Friction benchmark result", annotations=READ_ONLY)
async def get_friction_benchmark_result(run_id: str) -> dict[str, Any]:
    """Return a completed friction benchmark's bounded, analyzed output.

    It includes the measured operating points, fitted coefficients, repeat
    metrics, warnings, and run outcome, while omitting the potentially large
    per-sample timeline. Raw data remains available from the Driver Station's
    existing result-download endpoint.
    """

    result = await _api().get(f"/api/debug/results/{run_id}")
    assert isinstance(result, dict)
    analysis = result.get("analysis")
    if not isinstance(analysis, dict):
        return {"manifest": result.get("manifest"), "analysis": None}
    return {
        "manifest": result.get("manifest"),
        "analysis": {
            "analyzerId": analysis.get("analyzerId"),
            "version": analysis.get("version"),
            "speedUnit": analysis.get("speedUnit"),
            "kvUnit": analysis.get("kvUnit"),
            "summary": analysis.get("summary"),
            "repetitions": analysis.get("repetitions"),
            "operatingPoints": analysis.get("operatingPoints"),
            "fits": analysis.get("fits"),
            "warnings": analysis.get("warnings"),
            "notes": analysis.get("notes"),
            "timelineSampleCount": len(analysis.get("timeline", [])),
        },
    }


def main() -> None:
    """Start the Streamable HTTP endpoint used by a remote MCP client."""

    logger.info("Starting FTC Advanced MCP server on %s:%s/mcp", _mcp_host, _mcp_port)
    mcp.run(transport="streamable-http")
