"""Read one durable FTC recording folder and serve a local replay UI."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any
import uuid

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from google.protobuf.message import DecodeError

from web_driver_station.backend.ftclog import FtcLogError, iter_records
from web_driver_station.backend.protocol import robot_data_pb2 as wire
from web_driver_station.backend.protocol_codec import WireProtocolError, decode


class RecordingLoadError(ValueError):
    """Raised when a selected folder does not contain a readable recording."""


class RecordingLibrary:
    """A safe, read-only index of completed recording session folders."""

    def __init__(self, root_directory: Path) -> None:
        self.root_directory = root_directory.expanduser().resolve()

    def list(self) -> list[dict[str, Any]]:
        if not self.root_directory.is_dir():
            return []
        recordings: list[dict[str, Any]] = []
        for session_path in self.root_directory.iterdir():
            if not session_path.is_dir() or session_path.name.startswith("."):
                continue
            try:
                session_id = str(uuid.UUID(session_path.name))
            except ValueError:
                continue
            raw_path = session_path / "raw"
            log_paths = sorted(raw_path.glob("stream-*.ftclog")) if raw_path.is_dir() else []
            # A session becomes public only after the uploader's final rename,
            # and a completed log must be present.  Partial files are never a
            # sufficient condition for listing it.
            if not log_paths:
                continue
            paths = [path for path in session_path.rglob("*") if path.is_file()]
            newest_ns = max((path.stat().st_mtime_ns for path in paths), default=session_path.stat().st_mtime_ns)
            recordings.append({
                "id": session_id,
                "logFileCount": len(log_paths),
                "videoFileCount": sum(path.suffix.lower() == ".mp4" for path in paths),
                "sizeBytes": sum(path.stat().st_size for path in paths),
                "modifiedAtNs": str(newest_ns),
            })
        return sorted(recordings, key=lambda item: int(item["modifiedAtNs"]), reverse=True)

    def recording_path(self, session_id: str) -> Path:
        try:
            normalized_id = str(uuid.UUID(session_id))
        except ValueError as error:
            raise RecordingLoadError("Recording was not found") from error
        path = (self.root_directory / normalized_id).resolve()
        if path.parent != self.root_directory or not path.is_dir():
            raise RecordingLoadError("Recording was not found")
        return path


class Recording:
    """An immutable browser-oriented model reconstructed from raw .ftclog files."""

    def __init__(self, recording_directory: Path) -> None:
        self.directory = _resolve_recording_directory(recording_directory)
        self.session_directory = self.directory.parent if self.directory.name == "raw" else self.directory
        self.catalog: dict[str, Any] | None = None
        self.session: dict[str, Any] | None = None
        self.snapshots: list[dict[str, Any]] = []
        self.gamepad_frames: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.debug_messages: list[dict[str, Any]] = []
        self.gaps: list[dict[str, Any]] = []
        self.incidents: list[dict[str, Any]] = []
        self.log_files: list[str] = []
        self.invalid_frames = 0
        self.video_clips: list[Path] = []
        self._load()

    def summary(self) -> dict[str, Any]:
        start_ns = self.snapshots[0]["robotTimeNs"] if self.snapshots else None
        end_ns = self.snapshots[-1]["robotTimeNs"] if self.snapshots else None
        return {
            "recordingDirectory": str(self.directory),
            "session": self.session,
            "catalog": self.catalog,
            "snapshotCount": len(self.snapshots),
            "gamepadFrameCount": len(self.gamepad_frames),
            "eventCount": len(self.events),
            "debugMessageCount": len(self.debug_messages),
            "gapCount": len(self.gaps),
            "incidentCount": len(self.incidents),
            "startRobotTimeNs": start_ns,
            "endRobotTimeNs": end_ns,
            "logFiles": self.log_files,
            "invalidFrames": self.invalid_frames,
            "videoClips": [
                {"index": index, "name": path.relative_to(self.session_directory).as_posix(), "url": f"/api/recording/video/{index}"}
                for index, path in enumerate(self.video_clips)
            ],
        }

    def browser_model(self) -> dict[str, Any]:
        return {
            **self.summary(),
            "snapshots": self.snapshots,
            "gamepadFrames": self.gamepad_frames,
            "events": self.events,
            "debugMessages": self.debug_messages,
            "gaps": self.gaps,
            "incidents": self.incidents,
        }

    def _load(self) -> None:
        channel_keys: dict[int, tuple[str, int]] = {}
        log_paths = sorted((*self.directory.glob("stream-*.ftclog"), *self.directory.glob("stream-*.ftclog.partial")))
        if not log_paths:
            raise RecordingLoadError(f"No .ftclog files found in {self.directory}")
        for log_path in log_paths:
            self.log_files.append(log_path.name)
            try:
                records = iter_records(log_path)
                for record in records:
                    self._ingest_record(record.protobuf_frame, record.received_monotonic_ns, channel_keys)
            except FtcLogError as error:
                raise RecordingLoadError(str(error)) from error
        self.snapshots.sort(key=lambda snapshot: int(snapshot["robotTimeNs"]))
        self.gamepad_frames.sort(key=lambda frame: int(frame["robotTimeNs"]))
        if self.session is None:
            raise RecordingLoadError("Recording contains no hello/session metadata")
        self.video_clips = sorted(
            path for path in self.session_directory.rglob("*.mp4")
            if path.is_file() and ".partial" not in path.name
        )
        self.incidents = _load_incidents(self.session_directory)
        _apply_incidents(self.snapshots, self.incidents)

    def video_path(self, index: int) -> Path:
        if not 0 <= index < len(self.video_clips):
            raise RecordingLoadError("Video clip was not found")
        return self.video_clips[index]

    def _ingest_record(self, protobuf_frame: bytes, received_monotonic_ns: int, channel_keys: dict[int, tuple[str, int]]) -> None:
        try:
            envelope = wire.Envelope.FromString(protobuf_frame)
            payloads = decode(envelope, channel_keys)
        except (DecodeError, ValueError, WireProtocolError):
            self.invalid_frames += 1
            return
        for payload in payloads:
            message_type = payload["type"]
            data = payload["data"]
            if message_type == "hello":
                self.session = {
                    "sessionId": payload["sessionId"],
                    "robotId": data["robotId"],
                    "robotName": data["robotName"],
                    "opModeName": data["opModeName"],
                }
            elif message_type == "catalog":
                self.catalog = data
            elif message_type == "sample":
                control_hub_highlighted = data.get("highlighted") is True
                self.snapshots.append({
                    "sampleSequence": data["sampleSequence"],
                    "schemaRevision": data["schemaRevision"],
                    "robotTimeNs": payload["robotTimeNs"],
                    "receivedMonotonicNs": str(received_monotonic_ns),
                    "controlHubHighlighted": control_hub_highlighted,
                    "highlighted": control_hub_highlighted,
                    "highlightSource": "control_hub" if control_hub_highlighted else None,
                    "values": data["values"],
                })
            elif message_type == "gamepad":
                self.gamepad_frames.append({
                    "robotTimeNs": payload["robotTimeNs"],
                    "gamepad1": data["gamepad1"],
                    "gamepad2": data["gamepad2"],
                })
            elif message_type == "event":
                self.events.append({"robotTimeNs": payload["robotTimeNs"], **data})
            elif message_type == "gap":
                self.gaps.append({"robotTimeNs": payload["robotTimeNs"], **data})
            elif message_type.startswith("debug_"):
                self.debug_messages.append({"type": message_type, "robotTimeNs": payload["robotTimeNs"], **data})


def _load_incidents(session_directory: Path) -> list[dict[str, Any]]:
    """Read only fully finalized local incident manifests from a session."""

    directory = session_directory / "incidents"
    if not directory.is_dir():
        return []
    incidents: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict) or value.get("version") != 1:
                continue
            if (
                not isinstance(value.get("id"), str)
                or _sequence(value.get("firstSampleSequence")) is None
                or not isinstance(value.get("segments"), list)
            ):
                continue
            incidents.append(value)
        except (OSError, ValueError, TypeError):
            continue
    return sorted(incidents, key=lambda item: _sequence(item.get("firstSampleSequence")) or -1)


def _apply_incidents(snapshots: list[dict[str, Any]], incidents: list[dict[str, Any]]) -> None:
    """Overlay saved effective-highlight segments without modifying raw packets."""

    for incident in incidents:
        for segment in incident.get("segments", []):
            if not isinstance(segment, dict):
                continue
            source = segment.get("source")
            first = _sequence(segment.get("firstSampleSequence"))
            last = _sequence(segment.get("lastSampleSequence"))
            if source not in {"control_hub", "telemetry_lab"} or first is None or last is None:
                continue
            for snapshot in snapshots:
                sequence = _sequence(snapshot.get("sampleSequence"))
                if sequence is not None and first <= sequence <= last:
                    snapshot["highlighted"] = True
                    snapshot["highlightSource"] = source


def _sequence(value: Any) -> int | None:
    if not isinstance(value, str) or not value.isdecimal():
        return None
    return int(value)


def _resolve_recording_directory(value: Path) -> Path:
    path = value.expanduser().resolve()
    if not path.is_dir():
        raise RecordingLoadError(f"Recording directory does not exist: {path}")
    raw_path = path / "raw"
    if raw_path.is_dir():
        return raw_path
    return path


def create_app(recording_directory: Path) -> FastAPI:
    """Keep the one-recording local development mode available."""

    recording = Recording(recording_directory)
    app = FastAPI(title="FTC Recording Viewer", docs_url=None, redoc_url=None)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5174", "http://localhost:5174"],
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    @app.get("/api/recording")
    def get_recording() -> dict[str, Any]:
        return recording.browser_model()

    @app.get("/api/recording/summary")
    def get_recording_summary() -> dict[str, Any]:
        return recording.summary()

    @app.get("/api/recording/video/{index}")
    def get_recording_video(index: int) -> FileResponse:
        """Stream one original recording clip; browser playback owns timing."""

        try:
            path = recording.video_path(index)
        except RecordingLoadError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return FileResponse(path, media_type="video/mp4", filename=path.name)

    _mount_frontend(app)
    return app


def create_library_app(recordings_root: Path) -> FastAPI:
    """Serve an index of completed recordings and load one on demand."""

    library = RecordingLibrary(recordings_root)
    app = FastAPI(title="FTC Recording Viewer", docs_url=None, redoc_url=None)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5174", "http://localhost:5174"],
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    def get_recording(session_id: str) -> Recording:
        try:
            return Recording(library.recording_path(session_id))
        except RecordingLoadError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    def library_model(session_id: str, *, summary_only: bool = False) -> dict[str, Any]:
        recording = get_recording(session_id)
        model = recording.summary() if summary_only else recording.browser_model()
        for clip in model["videoClips"]:
            clip["url"] = f"/api/recordings/{session_id}/video/{clip['index']}"
        return model

    @app.get("/api/recordings")
    def list_recordings() -> list[dict[str, Any]]:
        return library.list()

    @app.get("/api/recordings/{session_id}")
    def get_library_recording(session_id: str) -> dict[str, Any]:
        return library_model(session_id)

    @app.get("/api/recordings/{session_id}/summary")
    def get_library_recording_summary(session_id: str) -> dict[str, Any]:
        return library_model(session_id, summary_only=True)

    @app.get("/api/recordings/{session_id}/video/{index}")
    def get_library_recording_video(session_id: str, index: int) -> FileResponse:
        recording = get_recording(session_id)
        try:
            path = recording.video_path(index)
        except RecordingLoadError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return FileResponse(path, media_type="video/mp4", filename=path.name)

    _mount_frontend(app)
    return app


def create_configured_app() -> FastAPI:
    """Uvicorn factory for the deployed recording-library service."""

    recordings_root = Path(os.getenv("FTC_RECORDING_DIR", "/srv/ftc-recordings"))
    return create_library_app(recordings_root)


def _mount_frontend(app: FastAPI) -> None:
    frontend_dist = Path(__file__).resolve().parents[1] / "frontend" / "dist"
    if frontend_dist.is_dir():
        app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="recording-viewer")


def main() -> int:
    parser = argparse.ArgumentParser(description="View one durable FTC .ftclog recording")
    parser.add_argument("--recording-dir", type=Path, default=os.getenv("FTC_RECORDING_DIR"), help="Session folder or its raw/ folder")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8002)
    args = parser.parse_args()
    if args.recording_dir is None:
        parser.error("--recording-dir is required (or set FTC_RECORDING_DIR)")
    try:
        app = create_app(args.recording_dir)
    except RecordingLoadError as error:
        parser.error(str(error))
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
