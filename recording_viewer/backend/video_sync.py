"""Calibrate and export camera clips against the recorded snapshot clock."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import logging
import math
import os
from pathlib import Path
import subprocess
from tempfile import NamedTemporaryFile
from threading import Lock
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse


Operation = Literal["sync", "export"]
logger = logging.getLogger(__name__)


def is_original_video(path: Path) -> bool:
    name = path.name.lower()
    return name.endswith(".mp4") and not name.endswith(".synced.mp4") and ".partial" not in name and not path.is_symlink()


def is_video_sync_artifact(path: Path) -> bool:
    name = path.name.lower()
    return name.startswith(".video-sync-") or name.endswith((".sync.json", ".synced.mp4", ".sync.partial"))


def original_videos(session: Path) -> list[Path]:
    return sorted(path for path in session.rglob("*") if is_original_video(path) and path.is_file())


def _sidecar(clip: Path) -> Path:
    return clip.with_suffix(".sync.json")


def _export_path(clip: Path) -> Path:
    return clip.with_suffix(".synced.mp4")


def _read_sync(clip: Path) -> dict | None:
    path = _sidecar(clip)
    if path.is_symlink():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        delay = data["video_delay_seconds"]
        if isinstance(delay, (int, float)) and math.isfinite(delay):
            return data
    except (OSError, ValueError, TypeError, KeyError):
        pass
    return None


def _save_sync(clip: Path, data: dict) -> None:
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=clip.parent,
                            prefix=".video-sync-", suffix=".sync.partial", delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(data, stream, indent=2, allow_nan=False)
        except Exception:
            stream.close()
            temporary.unlink(missing_ok=True)
            raise
    try:
        temporary.replace(_sidecar(clip))
    finally:
        temporary.unlink(missing_ok=True)


def _published_export(clip: Path, sync: dict | None) -> dict | None:
    output = _export_path(clip)
    if sync is None or output.is_symlink():
        return None
    try:
        modification = output.stat().st_mtime_ns
    except OSError:
        return None
    exports = sync.get("exports")
    for export in exports if isinstance(exports, list) else []:
        if (isinstance(export, dict) and export.get("file_mtime_ns") == modification
                and export.get("delay_seconds") == sync["video_delay_seconds"]
                and all(isinstance(export.get(key), (int, float)) and math.isfinite(export[key])
                        for key in ("telemetry_start_seconds", "telemetry_end_seconds"))):
            return export
    return None


def _calibrate(clip: Path, recording) -> None:
    from recording_viewer.backend.estimate_video_delay import estimate_delay, find_flash_edges, read_video

    signal, colors, edges = find_flash_edges(
        {"catalog": recording.catalog, "snapshots": recording.snapshots}, None, None,
    )
    times, scores, clock = read_video(clip, [0, 0, 1, 1], colors)
    result = estimate_delay(times, scores, edges, 4)
    previous = _read_sync(clip)
    existing_export = _published_export(clip, previous)
    _save_sync(clip, {
        **result, "signal": signal, "colors": colors, "video_clock": clock,
        "exports": [existing_export] if existing_export else [],
    })


def overlap(telemetry_duration: float, video_duration: float, delay: float) -> tuple[float, float]:
    """Return the interval on the telemetry clock for the available footage."""
    start = max(0.0, -delay)
    end = min(telemetry_duration, video_duration - delay)
    if end <= start:
        raise ValueError("No video overlaps the recorded telemetry")
    return start, end


def _run_media_tool(command: list[str], timeout: int) -> str:
    try:
        return subprocess.run(command, check=True, capture_output=True, text=True, timeout=timeout).stdout.strip()
    except FileNotFoundError as error:
        raise ValueError(f"{command[0]} is required to export video") from error
    except subprocess.TimeoutExpired as error:
        raise ValueError("Video export timed out") from error
    except subprocess.CalledProcessError as error:
        logger.warning("%s failed during video export: %s", command[0], error.stderr)
        raise ValueError("Could not trim the video") from error


def _export(clip: Path, recording) -> None:
    sync = _read_sync(clip)
    if sync is None:
        raise ValueError("Sync the video before exporting")
    snapshots = recording.snapshots
    if len(snapshots) < 2:
        raise ValueError("Recording has no telemetry interval")
    duration = (int(snapshots[-1]["robotTimeNs"]) - int(snapshots[0]["robotTimeNs"])) / 1e9
    video_duration = float(_run_media_tool([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(clip),
    ], timeout=60))
    if not math.isfinite(video_duration) or video_duration <= 0:
        raise ValueError("Could not determine the video duration")
    delay = sync["video_delay_seconds"]
    start, end = overlap(duration, video_duration, delay)
    with NamedTemporaryFile(dir=clip.parent, prefix=".video-sync-", suffix=".partial.mp4", delete=False) as stream:
        temporary = Path(stream.name)
    try:
        _run_media_tool([
            "ffmpeg", "-nostdin", "-v", "error", "-y", "-threads", "1", "-filter_threads", "1",
            "-ss", f"{start + delay:.9f}", "-i", str(clip), "-t", f"{end - start:.9f}",
            "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "libx264", "-preset", "veryfast",
            "-tune", "zerolatency", "-threads", "2", "-pix_fmt", "yuv420p", "-c:a", "aac",
            "-movflags", "+faststart", str(temporary),
        ], timeout=1800)
        if not temporary.stat().st_size:
            raise ValueError("The trimmed video is empty")
        previous = _published_export(clip, sync)
        # Publish metadata for both file identities before replacing the MP4.
        # Readers can still find the prior copy if replacement fails.
        new_export = {
            "file_mtime_ns": temporary.stat().st_mtime_ns, "delay_seconds": delay,
            "telemetry_start_seconds": start, "telemetry_end_seconds": end,
        }
        _save_sync(clip, {**sync, "exports": [item for item in (new_export, previous) if item is not None]})
        temporary.replace(_export_path(clip))
    finally:
        temporary.unlink(missing_ok=True)


class VideoJobs:
    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="video-sync")
        self._lock = Lock()
        self._active: tuple[Path, Operation] | None = None
        self._errors: dict[Path, str] = {}

    def start(self, clip: Path, load_recording, operation: Operation) -> None:
        with self._lock:
            if self._active == (clip, operation):
                return
            if self._active is not None:
                raise HTTPException(409, "Another video job is running. Try again when it finishes.")
            if operation == "export" and _read_sync(clip) is None:
                raise HTTPException(409, "Sync the video before exporting")
            self._active = (clip, operation)
            self._errors.pop(clip, None)
            self._executor.submit(self._run, clip, load_recording, operation)

    def status(self, clip: Path) -> dict:
        with self._lock:
            running = self._active[1] if self._active and self._active[0] == clip else None
            sync = _read_sync(clip)
            export = _published_export(clip, sync)
            return {
                "running": running,
                "error": self._errors.get(clip),
                "sync": {key: value for key, value in sync.items() if key != "exports"} if sync else None,
                "export": {
                    "telemetry_start_seconds": export["telemetry_start_seconds"],
                    "telemetry_end_seconds": export["telemetry_end_seconds"],
                } if export else None,
            }

    def _run(self, clip: Path, load_recording, operation: Operation) -> None:
        error = None
        try:
            action = _calibrate if operation == "sync" else _export
            action(clip, load_recording())
        except ValueError as failure:
            error = str(failure)
        except Exception:
            logger.exception("Video %s failed for %s", operation, clip)
            error = "Video processing failed. Check the server log for details."
        finally:
            with self._lock:
                if error:
                    self._errors[clip] = error
                self._active = None


def video_job_routes(recording_path, load_recording) -> APIRouter:
    router = APIRouter()
    jobs = VideoJobs()

    def get_clip(session_id: str, index: int):
        session = recording_path(session_id).resolve()
        clips = original_videos(session)
        if not 0 <= index < len(clips) or not clips[index].resolve().is_relative_to(session):
            raise HTTPException(404, "Video clip was not found")
        return clips[index]

    @router.get("/{session_id}/video/{index}/status")
    def status(session_id: str, index: int):
        return jobs.status(get_clip(session_id, index))

    @router.post("/{session_id}/video/{index}/{operation}", status_code=202)
    def start(session_id: str, index: int, operation: Operation, _body: dict):
        clip = get_clip(session_id, index)
        jobs.start(clip, lambda: load_recording(session_id), operation)
        return jobs.status(clip)

    @router.get("/{session_id}/video/{index}/download")
    def download(session_id: str, index: int):
        clip = get_clip(session_id, index)
        if _published_export(clip, _read_sync(clip)) is None:
            raise HTTPException(404, "No trimmed export is available for the saved offset")
        output = _export_path(clip)
        return FileResponse(output, media_type="video/mp4", filename=output.name,
                            headers={"Cache-Control": "no-store"})

    return router
