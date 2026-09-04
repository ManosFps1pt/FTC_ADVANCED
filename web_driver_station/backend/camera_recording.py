"""Coordinate dashboard camera capture with robot-data recording sessions."""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Literal

from scrcpy_backend import CameraOptions, ScrcpyBackend, ScrcpyError

from .adb_camera import AdbCameraError, AdbCameraService


CaptureMode = Literal["scrcpy_direct", "adb_volume_up"]
VideoFinalizedCallback = Callable[[str, str | None], None]
_PREVIEW_RETRY_DELAYS_SECONDS = (1.0, 5.0, 15.0)


class CameraRecordingError(RuntimeError):
    """Raised when the selected dashboard camera source cannot be used."""


@dataclass(frozen=True, slots=True)
class DirectScrcpySettings:
    facing: str | None = "back"
    aspect_ratio: str | None = None
    fps: int = 60
    flip: bool = False

    def options(self, serial: str) -> CameraOptions:
        return CameraOptions(
            serial=serial,
            # The dashboard deliberately exposes only a simple lens selector,
            # not scrcpy's numeric camera-ID escape hatch.
            camera_id=None,
            facing=self.facing or None,
            aspect_ratio=self.aspect_ratio or None,
            fps=self.fps,
            flip=self.flip,
            audio=False,
        )


@dataclass(frozen=True, slots=True)
class CameraCaptureConfig:
    mode: CaptureMode = "scrcpy_direct"
    direct: DirectScrcpySettings = DirectScrcpySettings()


@dataclass(slots=True)
class _ActiveCapture:
    capture_id: str
    mode: CaptureMode
    staging_path: Path
    telemetry_session_id: str | None = None
    started_monotonic_ns: int | None = None


class CameraRecordingCoordinator:
    """Own one dashboard capture source and attach it to a telemetry UUID.

    Camera capture begins at Init, before the robot-data protocol reveals its
    session UUID. The recorder therefore writes to a private staging folder.
    The first raw telemetry packet binds the running capture, and finalization
    moves the complete video tree into that telemetry session atomically enough
    for the session uploader (the source file has already been closed).
    """

    def __init__(
        self,
        recordings_root: Path,
        adb_camera: AdbCameraService,
        *,
        settings_path: Path | None = None,
        on_video_finalized: VideoFinalizedCallback | None = None,
    ) -> None:
        self._recordings_root = recordings_root.resolve()
        self._staging_root = self._recordings_root / ".camera-staging"
        self._settings_path = (settings_path or self._recordings_root.parent / "camera_capture.local.json").resolve()
        self._adb_camera = adb_camera
        self._on_video_finalized = on_video_finalized
        self._lock = threading.RLock()
        self._config = self._load_config()
        self._scrcpy: ScrcpyBackend | None = None
        self._active: _ActiveCapture | None = None
        self._last_error: str | None = None
        self._preview_requested = False
        self._preview_retry_pending = False
        self._preview_retry_attempt = 0
        self._preview_started_monotonic: float | None = None
        self._waiting_for_camera = False

    def status(self) -> dict[str, Any]:
        with self._lock:
            direct_status = self._scrcpy.status() if self._scrcpy is not None else None
            self._schedule_preview_recovery_locked(direct_status)
            direct_error = (direct_status or {}).get("error")
            active = self._active
            return {
                "config": self.config_dict(),
                "recording": active is not None,
                "capture_id": active.capture_id if active else None,
                "telemetry_session_id": active.telemetry_session_id if active else None,
                "started_monotonic_ns": active.started_monotonic_ns if active else None,
                "preview": {
                    "enabled": bool(direct_status and direct_status.get("browser_preview", {}).get("enabled")),
                    "running": bool(direct_status and direct_status.get("running")),
                    "detail": (direct_status or {}).get("browser_preview", {}).get("error") or direct_error,
                },
                "direct": direct_status,
                "error": self._last_error or direct_error,
            }

    def config_dict(self) -> dict[str, Any]:
        return {"mode": self._config.mode, "direct": asdict(self._config.direct)}

    def update_config(self, *, mode: CaptureMode, direct: dict[str, Any]) -> dict[str, Any]:
        if mode not in ("scrcpy_direct", "adb_volume_up"):
            raise CameraRecordingError("Unsupported capture mode")
        values = {
            "facing": _optional_facing(direct.get("facing")),
            "aspect_ratio": _optional_string(direct.get("aspect_ratio")),
            "fps": _fps(direct.get("fps", 60)),
            "flip": _bool(direct.get("flip", False), "flip"),
        }
        next_config = CameraCaptureConfig(mode=mode, direct=DirectScrcpySettings(**values))
        with self._lock:
            if self._active is not None:
                raise CameraRecordingError("Capture mode cannot change while a recording is active")
            self._stop_preview_locked()
            self._config = next_config
            self._save_config_locked()
            self._last_error = None
        if mode == "scrcpy_direct":
            self.ensure_preview()
        return self.status()

    def start(self) -> None:
        """Start a direct preview at dashboard startup when its mode is selected."""
        if self._config.mode == "scrcpy_direct":
            self.ensure_preview()

    def close(self) -> None:
        try:
            self.stop_recording()
        except CameraRecordingError:
            pass
        with self._lock:
            self._stop_preview_locked()

    def ensure_preview(self) -> dict[str, Any]:
        with self._lock:
            if self._config.mode != "scrcpy_direct" or self._active is not None:
                return self.status()
            if self._scrcpy is not None and self._scrcpy.status()["running"]:
                return self.status()
            # Keep trying when dashboard startup beats ADB device discovery.
            # The operator's saved camera assignment is durable, while the
            # USB device itself often takes a few seconds to become visible.
            self._preview_requested = True
            try:
                serial = self._selected_camera_serial()
                self._waiting_for_camera = False
                backend = self._backend_locked()
                backend.start_camera(self._config.direct.options(serial), preview=False, browser_preview=True)
                self._preview_started_monotonic = time.monotonic()
                waiter = getattr(backend, "wait", None)
                if callable(waiter):
                    try:
                        startup = waiter(timeout=2)
                    except TimeoutError:
                        startup = None
                    if isinstance(startup, dict) and startup.get("state") == "FAILED":
                        detail = str(startup.get("error") or "scrcpy camera preview failed")
                        logs = startup.get("logs", ())
                        evidence = "\n".join(str(line) for line in logs) if isinstance(logs, list) else ""
                        if (self._config.direct.aspect_ratio
                                and "could not select camera size" in f"{detail}\n{evidence}".lower()):
                            self._use_camera_default_aspect_ratio_locked()
                            return self.ensure_preview()
                        raise CameraRecordingError(detail)
                # A successful preview must not hide an unpaired recording:
                # that staging path needs an operator-visible recovery hint.
                if not (self._last_error or "").startswith("Video was preserved in staging"):
                    self._last_error = None
            except (CameraRecordingError, ScrcpyError, AdbCameraError, OSError, ValueError) as error:
                self._last_error = str(error)
                if str(error) == "Assign an authorized Android device as the camera first":
                    self._waiting_for_camera = True
                    self._schedule_camera_availability_retry_locked()
            return self.status()

    def stop_preview(self) -> dict[str, Any]:
        with self._lock:
            if self._active is not None:
                raise CameraRecordingError("Cannot stop preview while a recording is active")
            self._stop_preview_locked()
            return self.status()

    def preview_frame(self) -> bytes | None:
        with self._lock:
            return self._scrcpy.preview_frame() if self._scrcpy is not None else None

    def start_recording(self) -> dict[str, Any]:
        with self._lock:
            if self._active is not None:
                raise CameraRecordingError("A camera recording is already active or finalizing")
            self._last_error = None
            if self._config.mode == "adb_volume_up":
                try:
                    result = self._adb_camera.start_recording()
                except AdbCameraError as error:
                    raise CameraRecordingError(str(error)) from error
                camera = result["camera"]
                capture_id = camera.get("session_id")
                if not capture_id:
                    # No selected ADB camera remains an optional configuration.
                    return self.status()
                self._active = _ActiveCapture(
                    capture_id=str(capture_id), mode="adb_volume_up",
                    staging_path=self._staging_root / str(capture_id),
                    started_monotonic_ns=camera.get("started_monotonic_ns"),
                )
                return self.status()

            self._stop_preview_locked()
            capture_id = str(uuid.uuid4())
            staging_path = self._staging_root / capture_id
            output = staging_path / "video" / "scrcpy" / "recording.mp4"
            try:
                serial = self._selected_camera_serial()
                backend = self._backend_locked()
                backend.start_camera(self._config.direct.options(serial), output=output, preview=False)
            except (CameraRecordingError, ScrcpyError, AdbCameraError, OSError, ValueError) as error:
                self._last_error = str(error)
                self._restart_preview_after_failure_locked()
                raise CameraRecordingError(str(error)) from error
            self._active = _ActiveCapture(
                capture_id=capture_id, mode="scrcpy_direct", staging_path=staging_path,
                started_monotonic_ns=time.perf_counter_ns(),
            )
            return self.status()

    def bind_telemetry_session(self, session_id: str) -> None:
        """Bind the active Init-started video to the first raw telemetry UUID."""
        with self._lock:
            if self._active is None or self._active.telemetry_session_id is not None:
                return
            self._active.telemetry_session_id = session_id

    def stop_recording(self) -> dict[str, Any]:
        with self._lock:
            active = self._active
            if active is None:
                return self.status()
            if active.mode == "adb_volume_up":
                try:
                    self._adb_camera.stop_recording()
                except AdbCameraError as error:
                    self._last_error = str(error)
                    raise CameraRecordingError(str(error)) from error
                return self.status()
            backend = self._backend_locked()
        # scrcpy's stop may wait for its MP4 finalization; do not hold the
        # coordinator lock while the child process exits.
        try:
            result = backend.stop()
        except (ScrcpyError, OSError) as error:
            with self._lock:
                self._complete_capture_locked(active.capture_id, str(error))
                self._restart_preview_after_failure_locked()
            raise CameraRecordingError(str(error)) from error
        with self._lock:
            self._complete_capture_locked(active.capture_id, None if result.get("state") in {"COMPLETED", "STOPPED"} else str(result.get("error") or "scrcpy recording failed"))
            self._restart_preview_after_failure_locked()
            return self.status()

    def stop_recording_for_session(self, session_id: str) -> dict[str, Any]:
        """Finalize only the capture bound to a just-ended telemetry session."""
        with self._lock:
            active = self._active
            if active is None or active.telemetry_session_id != session_id:
                return self.status()
        return self.stop_recording()

    def notify_adb_finalized(self, capture_id: str, error: str | None = None) -> None:
        """Called by the ADB import worker after its verified local copy exists."""
        with self._lock:
            self._complete_capture_locked(capture_id, error)

    def video_ready_for(self, session_id: str) -> bool:
        """Whether the telemetry session has no video or all of its video is final."""
        with self._lock:
            return self._active is None or self._active.telemetry_session_id != session_id

    def _complete_capture_locked(self, capture_id: str, error: str | None) -> None:
        active = self._active
        if active is None or active.capture_id != capture_id:
            return
        self._active = None
        if active.telemetry_session_id is None:
            self._last_error = error or "Video was preserved in staging because no telemetry session was received"
            return
        if error is None:
            try:
                self._attach_staging_locked(active)
            except OSError as attach_error:
                error = f"Could not attach video to telemetry session: {attach_error}"
        if error:
            self._last_error = error
        callback = self._on_video_finalized
        if callback is not None:
            callback(active.telemetry_session_id, error)

    def _attach_staging_locked(self, active: _ActiveCapture) -> None:
        target = self._recordings_root / str(active.telemetry_session_id)
        target.mkdir(parents=True, exist_ok=True)
        if active.staging_path.is_dir():
            for source in active.staging_path.iterdir():
                destination = target / source.name
                if destination.exists():
                    raise OSError(f"Target already contains {source.name}")
                os.replace(source, destination)
            active.staging_path.rmdir()
        if active.mode == "adb_volume_up":
            self._adb_camera.relocate_verified_recording(active.capture_id, target)

    def _selected_camera_serial(self) -> str:
        # Force a fresh role/device read so dashboard startup can recover after
        # the phone is connected before opening the page.
        status = self._adb_camera.refresh_devices()
        serial = status.get("selected_camera_serial")
        if not isinstance(serial, str) or not serial:
            raise CameraRecordingError("Assign an authorized Android device as the camera first")
        return serial

    def _backend_locked(self) -> ScrcpyBackend:
        if self._scrcpy is None:
            self._scrcpy = ScrcpyBackend()
        return self._scrcpy

    def _stop_preview_locked(self) -> None:
        if self._scrcpy is not None and self._scrcpy.status()["running"]:
            self._scrcpy.stop()
        self._preview_requested = False
        self._preview_retry_pending = False
        self._preview_retry_attempt = 0
        self._preview_started_monotonic = None
        self._waiting_for_camera = False

    def _restart_preview_after_failure_locked(self) -> None:
        if self._config.mode == "scrcpy_direct":
            # Start after releasing the direct recorder; failure is reflected in
            # status and never invalidates the finalized recording.
            self._preview_requested = False
            threading.Thread(target=self.ensure_preview, name="scrcpy-dashboard-preview", daemon=True).start()

    def _schedule_preview_recovery_locked(self, direct_status: dict[str, Any] | None) -> None:
        """Restart an unexpectedly-ended browser preview a few times.

        Android camera providers can briefly revoke a camera stream without
        dropping ADB. A dashboard refresh must not be required to get the
        preview back. The cap prevents a faulty phone/cable from spawning an
        endless series of scrcpy processes.
        """
        if (self._config.mode != "scrcpy_direct" or self._active is not None
                or not self._preview_requested or not direct_status):
            return
        if direct_status.get("running"):
            # A preview that remains healthy for a short period starts a fresh
            # recovery budget, rather than carrying over an old hiccup.
            started = self._preview_started_monotonic
            if started is not None and time.monotonic() - started >= 20:
                self._preview_retry_attempt = 0
            return
        if direct_status.get("state") not in {"DISCONNECTED", "FAILED"}:
            return
        if self._preview_retry_pending:
            return
        if self._preview_retry_attempt >= len(_PREVIEW_RETRY_DELAYS_SECONDS):
            self._last_error = (
                "Camera preview ended repeatedly. Check the camera phone is unlocked "
                "and no other app is using its camera, then press Start preview."
            )
            return
        delay = _PREVIEW_RETRY_DELAYS_SECONDS[self._preview_retry_attempt]
        self._preview_retry_attempt += 1
        self._preview_retry_pending = True
        self._last_error = f"Camera preview disconnected; retrying in {delay:g} second(s)."
        threading.Thread(
            target=self._retry_preview_after_delay, args=(delay,),
            name="scrcpy-dashboard-preview-retry", daemon=True,
        ).start()

    def _schedule_camera_availability_retry_locked(self) -> None:
        """Poll ADB until the saved camera assignment becomes usable.

        This is intentionally not capped: unlike a failed scrcpy process, a
        missing ADB device at server startup is an expected transient state.
        The retry stops immediately if preview is stopped or recording begins.
        """
        if self._preview_retry_pending:
            return
        self._preview_retry_pending = True
        self._last_error = "Waiting for the assigned Android camera to become available."
        threading.Thread(
            target=self._retry_preview_after_delay, args=(2.0,),
            name="scrcpy-dashboard-camera-wait", daemon=True,
        ).start()

    def _retry_preview_after_delay(self, delay: float) -> None:
        time.sleep(delay)
        with self._lock:
            self._preview_retry_pending = False
            if (self._config.mode != "scrcpy_direct" or self._active is not None
                    or not self._preview_requested):
                return
        self.ensure_preview()

    def _use_camera_default_aspect_ratio_locked(self) -> None:
        """Recover when a phone rejects the operator's requested aspect ratio."""

        direct = self._config.direct
        self._config = CameraCaptureConfig(
            mode="scrcpy_direct",
            direct=DirectScrcpySettings(
                facing=direct.facing,
                aspect_ratio=None,
                fps=direct.fps,
                flip=direct.flip,
            ),
        )
        self._save_config_locked()

    def _load_config(self) -> CameraCaptureConfig:
        if not self._settings_path.is_file():
            return CameraCaptureConfig()
        try:
            raw = json.loads(self._settings_path.read_text(encoding="utf-8"))
            mode = raw.get("mode", "scrcpy_direct")
            direct = raw.get("direct", {})
            if not isinstance(direct, dict):
                raise ValueError("direct settings must be an object")
            if mode not in ("scrcpy_direct", "adb_volume_up"):
                raise ValueError("invalid mode")
            return CameraCaptureConfig(
                mode=mode,
                direct=DirectScrcpySettings(
                    facing=_optional_facing(direct.get("facing", "back")),
                    aspect_ratio=_optional_string(direct.get("aspect_ratio")),
                    fps=_fps(direct.get("fps", 60)),
                    flip=_bool(direct.get("flip", False), "flip"),
                ),
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return CameraCaptureConfig()

    def _save_config_locked(self) -> None:
        self._settings_path.parent.mkdir(parents=True, exist_ok=True)
        partial = self._settings_path.with_suffix(self._settings_path.suffix + ".partial")
        partial.write_text(json.dumps(self.config_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(partial, self._settings_path)


def _optional_string(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or len(value) > 64:
        raise CameraRecordingError("Camera settings must be short strings")
    return value


def _optional_facing(value: Any) -> str | None:
    value = _optional_string(value)
    if value not in (None, "front", "back", "external"):
        raise CameraRecordingError("Facing must be front, back, or external")
    return value


def _fps(value: Any) -> int:
    if type(value) is not int or not 1 <= value <= 120:
        raise CameraRecordingError("Framerate must be a whole number from 1 to 120")
    return value


def _bool(value: Any, name: str) -> bool:
    if type(value) is not bool:
        raise CameraRecordingError(f"{name} must be a boolean")
    return value
