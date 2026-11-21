"""Read-only MCP adapters for saved FTC recording measurements."""

import asyncio
from typing import Annotated, Literal
from urllib.parse import urlencode
from uuid import UUID

from mcp.server.fastmcp import FastMCP, Image
from mcp.types import ToolAnnotations
from pydantic import Field

from .backend_client import DriverStationApi

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
Limit = Annotated[int, Field(ge=1, le=25)]
Offset = Annotated[int, Field(ge=0)]
Seconds = Annotated[float, Field(ge=0, allow_inf_nan=False)]


def _path(run: str, suffix: str) -> str:
    try:
        session_id = "latest" if run == "latest" else str(UUID(run))
    except ValueError as error:
        raise ValueError("Use 'latest' or a session ID from list_replays") from error
    return f"/api/reviews/{session_id}/{suffix}"


async def _report(run: str, section: str) -> dict:
    result = await DriverStationApi(timeout_s=30).get(_path(run, f"report?section={section}"))
    assert isinstance(result, dict)
    return result


async def _events(run: str, kind: str, limit: int, offset: int) -> dict:
    result = await DriverStationApi(timeout_s=30).get(_path(run, f"events/{kind}?limit={limit}&offset={offset}"))
    assert isinstance(result, dict)
    return result


def register_replay_tools(mcp: FastMCP) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def list_replays(search: Annotated[str, Field(max_length=120)] = "", limit: Limit = 10,
                           offset: Offset = 0) -> dict:
        """Find finalized local runs, newest first. Resolve and reuse the session ID for multi-step analysis."""
        query = urlencode({"search": search, "limit": limit, "offset": offset})
        result = await DriverStationApi().get(f"/api/reviews?{query}")
        assert isinstance(result, dict)
        return result

    @mcp.tool(annotations=READ_ONLY)
    async def get_run_report(run: str = "latest") -> dict:
        """Summarize a saved run: sample cadence, missing samples, encoder flags, minimum voltage and event counts."""
        return await _report(run, "overview")

    @mcp.tool(annotations=READ_ONLY)
    async def get_recording_quality(run: str = "latest") -> dict:
        """Check clean closure, acquisition intervals, missing sequences, drops and per-channel coverage."""
        return await _report(run, "quality")

    @mcp.tool(annotations=READ_ONLY)
    async def list_recorded_signals(run: str = "latest") -> dict:
        """List recorded numeric signal IDs, units and counts of usable samples."""
        result = await DriverStationApi(timeout_s=30).get(_path(run, "signals"))
        assert isinstance(result, dict)
        return result

    @mcp.tool(annotations=READ_ONLY)
    async def get_signal_window(start_s: Seconds, end_s: Seconds,
                                signals: Annotated[list[str], Field(min_length=1, max_length=8)],
                                run: str = "latest", points: Annotated[int, Field(ge=2, le=200)] = 80) -> dict:
        """Inspect up to eight numeric signals over a maximum 120 seconds from the first sample.

        Statistics use all observed window samples. The returned series is downsampled to points.
        """
        query = urlencode({"start": start_s, "end": end_s, "signals": signals, "points": points}, doseq=True)
        result = await DriverStationApi(timeout_s=30).get(_path(run, f"window?{query}"))
        assert isinstance(result, dict)
        return result

    @mcp.tool(annotations=READ_ONLY)
    async def find_shooter_dips(run: str = "latest", limit: Limit = 10, offset: Offset = 0) -> dict:
        """Rank stable-target RPM drops by depth, with onset, trough, gate command, voltage, duty and recovery."""
        return await _events(run, "shooter_dips", limit, offset)

    @mcp.tool(annotations=READ_ONLY)
    async def get_shooter_transitions(run: str = "latest", limit: Limit = 10, offset: Offset = 0) -> dict:
        """List shooter cold starts, coasting starts and target changes with observed readiness and overshoot."""
        return await _events(run, "shooter_transitions", limit, offset)

    @mcp.tool(annotations=READ_ONLY)
    async def get_shooter_tracking(run: str = "latest") -> dict:
        """Analyze steady shooter targets, measured RPM error, full-duty time and voltage by target."""
        return await _report(run, "shooter")

    @mcp.tool(annotations=READ_ONLY)
    async def find_voltage_sags(run: str = "latest", limit: Limit = 10, offset: Offset = 0) -> dict:
        """Rank low-voltage episodes by minimum bus voltage and show timing, duration, and observed motor load."""
        return await _events(run, "voltage_sags", limit, offset)

    @mcp.tool(annotations=READ_ONLY)
    async def get_motor_current_report(run: str = "latest") -> dict:
        """Review per-motor current, peak times, integrated load and activity combinations in a saved run."""
        return await _report(run, "electrical")

    @mcp.tool(annotations=READ_ONLY)
    async def find_intake_loads(run: str = "latest", limit: Limit = 10, offset: Offset = 0) -> dict:
        """Find sustained forward intake intervals with high current and slow measured encoder movement."""
        return await _events(run, "intake_loads", limit, offset)

    @mcp.tool(annotations=READ_ONLY)
    async def check_encoder_health(run: str = "latest") -> dict:
        """Check how often powered motors reported nonzero velocity. Missing samples or low exposure limit conclusions."""
        return await _report(run, "encoders")

    @mcp.tool(annotations=READ_ONLY)
    async def get_feed_gate_windows(run: str = "latest", limit: Limit = 10, offset: Offset = 0) -> dict:
        """List open gate windows from the BIOBUZZ command proxy. These windows do not establish piece passage."""
        return await _events(run, "gate_windows", limit, offset)

    @mcp.tool(annotations=READ_ONLY)
    async def compare_shooter_runs(baseline: str, candidate: str) -> dict:
        """Compare steady shooter metrics at shared RPM targets. Consider voltage and test conditions when interpreting deltas."""
        first, second = await asyncio.gather(_report(baseline, "shooter"), _report(candidate, "shooter"))
        a, b = (report["result"]["by_target"] for report in (first, second))
        targets = []
        for target in sorted(a.keys() & b.keys(), key=float):
            metrics = {}
            for key in ("measured_in_band_fraction", "saturated_fraction"):
                before, after = a[target][key], b[target][key]
                metrics[f"delta_{key}"] = after - before if before is not None and after is not None else None
            targets.append({"target_rpm": float(target), "baseline": a[target], "candidate": b[target], **metrics})
        return {"baseline": first["run"], "candidate": second["run"], "targets": targets}

    @mcp.tool(annotations=READ_ONLY)
    async def get_replay_plot(kind: Literal["overview", "shooter", "electrical"] = "overview",
                              run: str = "latest") -> Image:
        """Return a PNG overview, shooter-deceleration plot or electrical event plot for a saved run."""
        image = await DriverStationApi(timeout_s=45).get_png(_path(run, f"plot/{kind}"))
        return Image(data=image, format="png")
