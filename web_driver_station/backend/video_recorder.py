"""Segmented laptop-camera recordings with replayable capture timestamps.

The media file is intentionally kept separate from the robot telemetry stream.
Each retained frame receives a laptop monotonic timestamp at ``grab()`` and is
written alongside the MP4 segment in a compact JSON-lines sidecar.  The sidecar
is the source of truth for later LED synchronization and replay alignment.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


class VideoRecorderError(RuntimeError):
    """Raised when a camera recording cannot be started or finalized."""


@dataclass(frozen=True, slots=True)
class CameraRecordingConfig:
    """Requested settings for one locally attached webcam."""

    device_index: int = 0
    width: int = 1280
    height: int = 720
    fps: float = 30.0
    segment_seconds: float = 60.0
    camera_id: str = "cam0"

    def validate(self) -> None:
        if self.device_index < 0:
            raise VideoRecorderError("Camera device index must be zero or greater")
        if not 16 <= self.width <= 7680 or not 16 <= self.height <= 4320:
            raise VideoRecorderError("Camera dimensions must be between 16 pixels and 7680×4320")
        if not 1 <= self.fps <= 120:
            raise VideoRecorderError("Camera FPS must be between 1 and 120")
        if not 1 <= self.segment_seconds <= 3600:
            raise VideoRecorderError("Video segment length must be between 1 and 3600 seconds")
        if not self.camera_id or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for character in self.camera_id):
            raise VideoRecorderError("Camera ID may contain only letters, numbers, underscores, and hyphens")


class VideoRecorder:
    """Record a USB webcam on one dedicated worker thread.

    ``VideoCapture`` is never accessed by an asyncio or FastAPI worker.  A
    recorder start creates a durable session directory immediately; completed
    MP4 and frame-map pairs are atomically renamed from ``.partial`` files.
    """

    def __init__(self, recordings_root: Path) -> None:
        self._recordings_root = recordings_root
        self._lock = threading.RLock()
        self._stop_requested = threading.Event()
        self._thread: threading.Thread | None = None
        self._session_id: str | None = None
        self._session_path: Path | None = None
        self._config: CameraRecordingConfig | None = None
        self._status: dict[str, Any] = self._empty_status()

    @staticmethod
    def _empty_status() -> dict[str, Any]:
        return {
            "recording": False,
            "session_id": None,
            "camera_id": None,
            "frames_written": 0,
            "segments_completed": 0,
            "actual_width": None,
            "actual_height": None,
            "actual_fps": None,
            "error": None,
        }

    @property
    def recordings_root(self) -> Path:
        return self._recordings_root

    def status(self) -> dict[str, Any]:
        with self._lock:
            result = dict(self._status)
            if self._session_path is not None:
                result["session_path"] = str(self._session_path)
            return result

    def start(self, config: CameraRecordingConfig) -> dict[str, Any]:
        config.validate()
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise VideoRecorderError("A camera recording is already active")
            self._recordings_root.mkdir(parents=True, exist_ok=True)
            session_id = str(uuid.uuid4())
            session_path = self._recordings_root / session_id
            session_path.mkdir()
            self._stop_requested.clear()
            self._session_id, self._session_path, self._config = session_id, session_path, config
            self._status = {
                **self._empty_status(),
                "recording": True,
                "session_id": session_id,
                "camera_id": config.camera_id,
                "requested": asdict(config),
                "started_monotonic_ns": time.perf_counter_ns(),
            }
            self._write_manifest_locked(state="RECORDING")
            self._thread = threading.Thread(target=self._record, name=f"video-recorder-{config.camera_id}", daemon=True)
            self._thread.start()
            return self.status()

    def stop(self, *, timeout_s: float = 10.0) -> dict[str, Any]:
        with self._lock:
            thread = self._thread
            if thread is None:
                return self.status()
            self._stop_requested.set()
        thread.join(timeout_s)
        if thread.is_alive():
            raise VideoRecorderError("Camera recorder did not stop within the requested timeout")
        return self.status()

    def list_sessions(self) -> list[dict[str, Any]]:
        if not self._recordings_root.is_dir():
            return []
        sessions: list[dict[str, Any]] = []
        for path in self._recordings_root.iterdir():
            manifest_path = path / "manifest.json"
            if not path.is_dir() or not manifest_path.is_file():
                continue
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            sessions.append({
                "session_id": path.name,
                "state": manifest.get("state", "UNKNOWN"),
                "camera_id": manifest.get("camera_id"),
                "frames_written": manifest.get("frames_written", 0),
                "segments": manifest.get("segments", []),
                "ended_monotonic_ns": manifest.get("ended_monotonic_ns"),
            })
        return sorted(sessions, key=lambda session: str(session["session_id"]), reverse=True)

    def session_directory(self, session_id: str) -> Path:
        if not _safe_session_id(session_id):
            raise VideoRecorderError("Invalid video session ID")
        path = self._recordings_root / session_id
        if not path.is_dir():
            raise VideoRecorderError("Video session was not found")
        return path

    def _record(self) -> None:
        capture = None
        writer = None
        frame_map = None
        segment_index = 0
        segment_started_ns = 0
        segment_frames = 0
        try:
            cv2 = _load_opencv()
            config = self._require_config()
            capture = cv2.VideoCapture(config.device_index)
            if not capture.isOpened():
                raise VideoRecorderError(f"Could not open camera device {config.device_index}")
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, config.width)
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, config.height)
            capture.set(cv2.CAP_PROP_FPS, config.fps)
            capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)

            while not self._stop_requested.is_set():
                if not capture.grab():
                    raise VideoRecorderError("Camera stopped returning frames")
                timestamp_ns = time.perf_counter_ns()
                ok, frame = capture.retrieve()
                if not ok or frame is None:
                    continue
                frame_height, frame_width = frame.shape[:2]
                if writer is None or timestamp_ns - segment_started_ns >= int(config.segment_seconds * 1_000_000_000):
                    if writer is not None:
                        self._close_segment(writer, frame_map, segment_index, segment_frames)
                        writer, frame_map = None, None
                        segment_index += 1
                        segment_frames = 0
                    writer, frame_map = self._open_segment(cv2, segment_index, frame_width, frame_height)
                    segment_started_ns = timestamp_ns
                writer.write(frame)
                frame_map.write(json.dumps({"frame_index": segment_frames, "capture_monotonic_ns": timestamp_ns}, separators=(",", ":")) + "\n")
                segment_frames += 1
                with self._lock:
                    self._status["frames_written"] += 1
                    self._status["actual_width"] = frame_width
                    self._status["actual_height"] = frame_height
                    self._status["actual_fps"] = float(capture.get(cv2.CAP_PROP_FPS) or config.fps)
        except Exception as error:
            with self._lock:
                self._status["error"] = str(error)
        finally:
            if writer is not None:
                try:
                    self._close_segment(writer, frame_map, segment_index, segment_frames)
                except Exception as error:
                    with self._lock:
                        self._status["error"] = str(error)
            if capture is not None:
                capture.release()
            with self._lock:
                self._status["recording"] = False
                self._status["ended_monotonic_ns"] = time.perf_counter_ns()
                self._write_manifest_locked(state="READY" if self._status["error"] is None else "FAILED")
                self._thread = None

    def _open_segment(self, cv2: Any, index: int, width: int, height: int) -> tuple[Any, Any]:
        session_path = self._require_session_path()
        video_path = session_path / f"segment-{index:05d}.partial.mp4"
        map_path = session_path / f"segment-{index:05d}.frames.partial.jsonl"
        writer = cv2.VideoWriter(str(video_path), cv2.VideoWriter_fourcc(*"mp4v"), self._require_config().fps, (width, height))
        if not writer.isOpened():
            writer.release()
            raise VideoRecorderError("Could not open MP4 writer (the local OpenCV codec is unavailable)")
        return writer, map_path.open("w", encoding="utf-8", buffering=64 * 1024)

    def _close_segment(self, writer: Any, frame_map: Any, index: int, frame_count: int) -> None:
        writer.release()
        frame_map.flush()
        os.fsync(frame_map.fileno())
        frame_map.close()
        session_path = self._require_session_path()
        video_partial = session_path / f"segment-{index:05d}.partial.mp4"
        map_partial = session_path / f"segment-{index:05d}.frames.partial.jsonl"
        video_final = session_path / f"segment-{index:05d}.mp4"
        map_final = session_path / f"segment-{index:05d}.frames.jsonl"
        if not video_partial.is_file():
            raise VideoRecorderError("MP4 writer did not produce a segment")
        os.replace(video_partial, video_final)
        os.replace(map_partial, map_final)
        with self._lock:
            self._status["segments_completed"] += 1
            segments = self._status.setdefault("segments", [])
            segments.append({"index": index, "video": video_final.name, "frame_map": map_final.name, "frame_count": frame_count, "codec": "mp4v"})
            self._write_manifest_locked(state="RECORDING")

    def _write_manifest_locked(self, *, state: str) -> None:
        session_path = self._require_session_path()
        manifest = {
            "format": "ftc-video-session-v1",
            "state": state,
            "session_id": self._session_id,
            "camera_id": self._config.camera_id if self._config else None,
            "requested": asdict(self._config) if self._config else None,
            "frames_written": self._status["frames_written"],
            "segments": self._status.get("segments", []),
            "actual_width": self._status.get("actual_width"),
            "actual_height": self._status.get("actual_height"),
            "actual_fps": self._status.get("actual_fps"),
            "started_monotonic_ns": self._status.get("started_monotonic_ns"),
            "ended_monotonic_ns": self._status.get("ended_monotonic_ns"),
            "error": self._status.get("error"),
        }
        partial_path = session_path / "manifest.partial.json"
        partial_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(partial_path, session_path / "manifest.json")

    def _require_session_path(self) -> Path:
        if self._session_path is None:
            raise VideoRecorderError("No video session is active")
        return self._session_path

    def _require_config(self) -> CameraRecordingConfig:
        if self._config is None:
            raise VideoRecorderError("No camera configuration is active")
        return self._config


def _load_opencv() -> Any:
    try:
        import cv2
    except ImportError as error:
        raise VideoRecorderError("OpenCV is required for video recording. Install opencv-python.") from error
    return cv2


def _safe_session_id(value: str) -> bool:
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False
