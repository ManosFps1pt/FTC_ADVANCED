"""Read one durable FTC recording folder and serve a local replay UI."""

from __future__ import annotations

import argparse
import gzip
import json
import os
import uuid
from collections import OrderedDict
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from threading import Lock
from typing import Any, Callable

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from google.protobuf.message import DecodeError
from pydantic import TypeAdapter

from recording_viewer.backend.replay_markers import ReplayMarker, ReplayMarkerStore
from recording_viewer.backend.video_sync import (
    is_original_video,
    is_video_sync_artifact,
    original_video_at,
    original_videos,
    video_job_routes,
)
from web_driver_station.backend.ftclog import FtcLogError, iter_records, is_closed_partial
from web_driver_station.backend.debugger_results import DebuggerResults
from web_driver_station.backend.debugger_api import install_result_reads
from web_driver_station.backend.protocol import robot_data_pb2 as wire
from web_driver_station.backend.protocol_codec import WireProtocolError, decode
from web_driver_station.backend.recording_metadata import RECORDING_METADATA_FILE, read_recording_metadata, recording_details


REPLAY_CACHE_BYTES = 32 * 1024 * 1024
REPLAY_JSON = TypeAdapter(dict[str, Any])


class ReplayResponseCache:
    """Keep recently requested compressed replays within a fixed memory budget."""

    def __init__(self) -> None:
        self._entries: OrderedDict[tuple, tuple[bytes, str]] = OrderedDict()
        self._size = 0
        self._lock = Lock()

    def get(self, key: tuple) -> tuple[bytes, str] | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                self._entries.move_to_end(key)
            return entry

    def put(self, key: tuple, entry: tuple[bytes, str]) -> None:
        size = len(entry[0])
        if size > REPLAY_CACHE_BYTES:
            return
        with self._lock:
            if key in self._entries:
                self._entries.move_to_end(key)
                return
            while self._size + size > REPLAY_CACHE_BYTES:
                _, (previous_body, _) = self._entries.popitem(last=False)
                self._size -= len(previous_body)
            self._entries[key] = entry
            self._size += size


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
            if session_path.is_symlink() or not session_path.is_dir() or session_path.name.startswith("."):
                continue
            try:
                session_id = str(uuid.UUID(session_path.name))
            except ValueError:
                continue
            raw_path = session_path / "raw"
            try:
                if json.loads((session_path/"manifest.json").read_text(encoding="utf-8")).get("kind") == "debugger":
                    continue
            except (OSError, ValueError):
                pass
            log_paths = sorted(raw_path.glob("stream-*.ftclog")) if raw_path.is_dir() else []
            closed_partials = [path for path in raw_path.glob("stream-*.ftclog.partial") if is_closed_partial(path)]
            # The uploader publishes only closed streams. Preserve interruptions explicitly.
            if not log_paths and not closed_partials:
                continue
            paths = [path for path in session_path.rglob("*")
                     if not is_video_sync_artifact(path) and path.is_file()]
            newest_ns = max((path.stat().st_mtime_ns for path in paths), default=session_path.stat().st_mtime_ns)
            recordings.append({
                "id": session_id,
                "metadata": read_recording_metadata(session_path),
                "logFileCount": len(log_paths) + len(closed_partials),
                "interrupted": bool(closed_partials),
                "videoFileCount": sum(is_original_video(path) for path in paths),
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

    def __init__(self, recording_directory: Path, *, log_file: Path | None = None) -> None:
        self.directory = _resolve_recording_directory(recording_directory)
        self.session_directory = self.directory.parent if self.directory.name == "raw" else self.directory
        self.metadata = read_recording_metadata(self.session_directory)
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
        self._load(log_file)

    def summary(self) -> dict[str, Any]:
        start_ns = self.snapshots[0]["robotTimeNs"] if self.snapshots else None
        end_ns = self.snapshots[-1]["robotTimeNs"] if self.snapshots else None
        return {
            "recordingDirectory": str(self.directory),
            "metadata": self.metadata,
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
            "interrupted": any(name.endswith(".partial") for name in self.log_files),
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

    def _load(self, log_file: Path | None) -> None:
        channel_keys: dict[int, tuple[str, int]] = {}
        log_paths = [log_file] if log_file is not None else _log_paths(self.directory)
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
        self.video_clips = original_videos(self.session_directory)
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

    incidents: list[dict[str, Any]] = []
    for path in _incident_paths(session_directory):
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


def _log_paths(directory: Path) -> list[Path]:
    return sorted((*directory.glob("stream-*.ftclog"), *directory.glob("stream-*.ftclog.partial")))


def _incident_paths(session_directory: Path) -> list[Path]:
    return sorted((session_directory / "incidents").glob("*.json"))


def _replay_version(directory: Path) -> tuple:
    session = directory.parent if directory.name == "raw" else directory
    metadata = session / RECORDING_METADATA_FILE
    inputs = [*_log_paths(directory), metadata, *_incident_paths(session)]
    files = []
    for path in inputs:
        try:
            status = path.lstat() if path == metadata else path.stat()
        except OSError:
            continue
        files.append((path, status.st_mode, status.st_ino, status.st_size, status.st_mtime_ns, status.st_ctime_ns))
    # The replay contains clip names and order. Only the media route reads their contents.
    return directory, tuple(files), tuple(original_videos(session))


def _accepts_gzip(accept_encoding: str) -> bool:
    qualities = {}
    for item in accept_encoding.lower().split(","):
        coding, _, parameter = item.partition(";")
        coding = coding.strip()
        if coding not in {"gzip", "*"}:
            continue
        try:
            quality = float(parameter.strip().removeprefix("q=")) if parameter else 1.0
        except ValueError:
            continue
        qualities[coding] = max(qualities.get(coding, 0.0), quality if 0 < quality <= 1 else 0.0)
    return qualities.get("gzip", qualities.get("*", 0.0)) > 0


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


def create_library_app(recordings_root: Path, *, excluded: Callable[[], set[str]] | None = None) -> FastAPI:
    """Serve an index of completed recordings and load one on demand."""

    library = RecordingLibrary(recordings_root)
    replay_cache = ReplayResponseCache()
    markers = ReplayMarkerStore(recordings_root)
    app = FastAPI(title="FTC Recording Viewer", docs_url=None, redoc_url=None)
    install_result_reads(app, DebuggerResults(recordings_root))
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5174", "http://localhost:5174"],
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    def get_recording_path(session_id: str) -> Path:
        try:
            path = library.recording_path(session_id)
            if excluded is not None:
                from web_driver_station.backend.saved_runs import saved_streams

                if path.name in excluded() or not saved_streams(path):
                    raise RecordingLoadError("Recording is not finalized")
            return path
        except RecordingLoadError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    def load_recording(path: Path) -> Recording:
        try:
            return Recording(path)
        except RecordingLoadError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    def get_recording(session_id: str) -> Recording:
        return load_recording(get_recording_path(session_id))

    def get_recording_directory(path: Path) -> Path:
        try:
            return _resolve_recording_directory(path)
        except RecordingLoadError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    def add_video_urls(model: dict[str, Any], session_id: str, root_path: str) -> None:
        for clip in model["videoClips"]:
            clip["url"] = f"{root_path}/api/recordings/{session_id}/video/{clip['index']}"

    @app.get("/api/recordings")
    def list_recordings() -> list[dict[str, Any]]:
        if excluded is None:
            return library.list()
        from web_driver_station.backend.saved_runs import saved_runs

        return [{
            "id": item["session_id"],
            "metadata": {"title": item["title"], "description": item["description"], **recording_details(item)},
            "modifiedAtNs": str(int(datetime.fromisoformat(item["captured_at"]).timestamp() * 1e9)),
            "logFileCount": len(item["logs"]) + len(item["partial_logs"]),
            "interrupted": item["interrupted"],
            "sizeBytes": item["size_bytes"],
            "videoFileCount": len(original_videos(recordings_root / item["session_id"])),
        } for item in saved_runs(recordings_root, excluded())]

    @app.get("/api/recordings/{session_id}", response_model=dict[str, Any])
    def get_library_recording(session_id: str, request: Request) -> Response:
        path = get_recording_path(session_id)
        directory = get_recording_directory(path)
        version = _replay_version(directory)
        root_path = request.scope.get("root_path", "")
        key = (session_id, root_path, version)
        entry = replay_cache.get(key)
        body = None
        if entry is None:
            model = load_recording(path).browser_model()
            add_video_urls(model, session_id, root_path)
            # Serialize once in the route's worker thread so the same response can
            # be compressed and reused without another pass over the replay.
            body = REPLAY_JSON.dump_json(model)
            del model
            compressed = gzip.compress(body, compresslevel=3, mtime=0)
            entry = compressed, sha256(compressed).hexdigest()
            if version == _replay_version(directory):
                replay_cache.put(key, entry)
        compressed, digest = entry
        etag = f'"{digest}"'
        headers = {"Cache-Control": "private, no-cache", "ETag": f"W/{etag}", "Vary": "Accept-Encoding"}
        matching_tags = {tag.strip().removeprefix("W/") for tag in request.headers.get("if-none-match", "").split(",")}
        if etag in matching_tags or "*" in matching_tags:
            return Response(status_code=304, headers=headers)
        if _accepts_gzip(request.headers.get("accept-encoding", "")):
            headers["Content-Encoding"] = "gzip"
            return Response(compressed, media_type="application/json", headers=headers)
        return Response(body if body is not None else gzip.decompress(compressed),
                        media_type="application/json", headers=headers)

    @app.get("/api/recordings/{session_id}/summary")
    def get_library_recording_summary(session_id: str, request: Request) -> dict[str, Any]:
        model = get_recording(session_id).summary()
        add_video_urls(model, session_id, request.scope.get("root_path", ""))
        return model

    @app.get("/api/recordings/{session_id}/markers")
    def get_library_recording_markers(session_id: str, response: Response) -> list[ReplayMarker]:
        path = get_recording_path(session_id)
        response.headers["Cache-Control"] = "private, no-store"
        return markers.list_for_run(path.name)

    @app.get("/api/recordings/{session_id}/video/{index}")
    def get_library_recording_video(session_id: str, index: int) -> FileResponse:
        session = get_recording_path(session_id)
        directory = get_recording_directory(session)
        if not _log_paths(directory):
            raise HTTPException(status_code=404, detail=f"No .ftclog files found in {directory}")
        path = original_video_at(session, index)
        return FileResponse(path, media_type="video/mp4", filename=path.name)

    app.include_router(video_job_routes(get_recording_path, get_recording),
                       prefix="/api/recordings")
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
