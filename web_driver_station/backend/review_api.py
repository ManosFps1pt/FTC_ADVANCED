"""HTTP views used by the local recordings panel, Discord, and MCP."""

import asyncio
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from recording_viewer.backend.run_analysis import RunAnalysis, event_page, fingerprint, replay_path

from .debugger_results import atomic_json
from .ftclog import FtcLogError
from .recording_metadata import RECORDING_METADATA_FILE, RecordingMetadata
from .recording_uploader import RecordingUploadError
from .saved_runs import describe_run, saved_streams

Section = Literal["overview", "quality", "shooter", "electrical", "intake", "encoders"]
EventKind = Literal["shooter_dips", "shooter_transitions", "intake_loads", "voltage_sags", "gate_windows"]
PlotKind = Literal["overview", "shooter", "electrical"]
Seconds = Annotated[float, Query(ge=0, allow_inf_nan=False)]


def review_routes(service) -> APIRouter:
    router = APIRouter(prefix="/api/reviews")
    analyzer = RunAnalysis()

    def headers(run: dict) -> dict:
        return {**run, "replay_path": replay_path(run["session_id"])}

    def source(session_id: str):
        if session_id == "latest":
            runs = service.saved_recording_sessions()
            if not runs:
                raise HTTPException(404, "No finalized local recordings")
            session_id = runs[0]["session_id"]
        try:
            directory = service.recording_directory(session_id)
        except FtcLogError as error:
            raise HTTPException(404, "Recording was not found") from error
        if (directory.name in service.review_exclusions()
                or not (streams := saved_streams(directory))):
            raise HTTPException(404, "Recording is active, unavailable, or not a telemetry replay")
        return directory, streams

    @router.get("")
    def list_reviews(offset: Annotated[int, Query(ge=0)] = 0,
                     limit: Annotated[int, Query(ge=1, le=256)] = 50,
                     search: Annotated[str, Query(max_length=120)] = "") -> dict:
        runs = service.saved_recording_sessions()
        if term := search.strip().casefold():
            runs = [run for run in runs if any(term in str(run[key] or "").casefold()
                                              for key in ("title", "opmode", "robot", "session_id"))]
        return {
            "runs": [headers(run) for run in runs[offset:offset + limit]],
            "total": len(runs),
            "next_offset": offset + limit if offset + limit < len(runs) else None,
            "upload_configured": service.recording_upload_configured,
        }

    @router.post("/upload-pending")
    async def upload_pending() -> dict:
        try:
            return service.queue_uploads(await asyncio.to_thread(service.saved_recording_sessions))
        except (OSError, RecordingUploadError) as error:
            raise HTTPException(400, str(error)) from error

    @router.patch("/{session_id}")
    async def update_notes(session_id: str, metadata: RecordingMetadata) -> dict:
        directory, _ = source(session_id)
        try:
            service.check_recording_edit(directory.name, editing_notes=True)
            atomic_json(directory / RECORDING_METADATA_FILE, metadata.model_dump())
            return metadata.model_dump()
        except (OSError, RecordingUploadError) as error:
            raise HTTPException(409, str(error)) from error

    @router.delete("/{session_id}")
    async def delete_local(session_id: str) -> dict:
        directory, _ = source(session_id)
        try:
            await service.delete_saved_recording(directory.name)
            return {"deleted": directory.name}
        except (FtcLogError, OSError, RecordingUploadError) as error:
            raise HTTPException(409, str(error)) from error

    @router.get("/{session_id}/report")
    def report(session_id: str, section: Section = "overview") -> dict:
        directory, streams = source(session_id)
        try:
            report = analyzer.report(directory, fingerprint(streams))
            summary = report["summary"]
            if section == "overview":
                result = {
                    "quality": {key: value for key, value in summary["quality"].items()
                                if key != "channel_coverage_fraction"},
                    "lowest_voltage": summary["electrical"]["lowest_voltage"],
                    "flatlined_encoders": [key for key, motor in summary["electrical"]["per_motor"].items()
                                           if motor["powered_velocity_all_zero"]],
                    "event_counts": {key: len(rows) for key, rows in report["tables"].items()},
                }
            elif section == "encoders":
                result = {key: {name: value for name, value in motor.items() if name.startswith("powered_")}
                          for key, motor in summary["electrical"]["per_motor"].items()}
            else:
                result = summary[section]
            return {
                "run": headers(describe_run(directory, streams)),
                "analysis_version": summary["analysis_version"],
                "settings": summary["settings"],
                "capture": {key: summary["source"][key] for key in ("session_end", "max_heartbeat_drops", "invalid_frames")},
                "section": section,
                "result": result,
            }
        except (FtcLogError, OSError, ValueError) as error:
            raise HTTPException(422, str(error)) from error

    @router.get("/{session_id}/events/{kind}")
    def events(session_id: str, kind: EventKind,
               offset: Annotated[int, Query(ge=0)] = 0,
               limit: Annotated[int, Query(ge=1, le=25)] = 10) -> dict:
        directory, streams = source(session_id)
        try:
            result = analyzer.report(directory, fingerprint(streams))
            return {
                "session_id": directory.name,
                **event_page(result, directory.name, kind, offset, limit),
            }
        except (FtcLogError, OSError, ValueError) as error:
            raise HTTPException(422, str(error)) from error

    @router.get("/{session_id}/signals")
    def signals(session_id: str) -> dict:
        directory, streams = source(session_id)
        try:
            return {"session_id": directory.name, "signals": analyzer.signals(directory, fingerprint(streams))}
        except (FtcLogError, OSError, ValueError) as error:
            raise HTTPException(422, str(error)) from error

    @router.get("/{session_id}/window")
    def window(session_id: str, start: Seconds, end: Seconds,
               signals: Annotated[list[str], Query(min_length=1, max_length=8)],
               points: Annotated[int, Query(ge=2, le=200)] = 80) -> dict:
        directory, streams = source(session_id)
        try:
            return {
                "session_id": directory.name,
                "replay_path": replay_path(directory.name, start),
                **analyzer.window(directory, fingerprint(streams), signals, start, end, points),
            }
        except (FtcLogError, OSError, ValueError) as error:
            raise HTTPException(422, str(error)) from error

    @router.get("/{session_id}/plot/{kind}")
    def plot(session_id: str, kind: PlotKind) -> FileResponse:
        directory, streams = source(session_id)
        try:
            path = analyzer.plot(directory, fingerprint(streams), kind)
            return FileResponse(path, media_type="image/png")
        except (FtcLogError, OSError, ValueError) as error:
            raise HTTPException(422, str(error)) from error

    return router
