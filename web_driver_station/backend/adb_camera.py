"""Native Android camera recording controlled through serial-bound ADB commands.

The phone's OEM camera application owns capture and encoding.  This module only
discovers ADB devices, resolves safe RC/camera roles, injects the calibrated
record key, and imports the finalized media without requiring a phone-side app.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal


FTC_RC_PACKAGE = "com.qualcomm.ftcrobotcontroller"
ROLE_VALUES = {"auto", "robot_controller", "camera", "ignored"}
CAMERA_COMPONENT_CANDIDATES = (
    "com.sec.android.app.camera",
    "com.android.camera2",
    "com.android.camera",
    "com.google.android.GoogleCamera",
    "com.oplus.camera",
    "com.oppo.camera",
    "com.coloros.camera",
)


class AdbCameraError(RuntimeError):
    """Raised when discovery, capture control, or import cannot proceed safely."""


@dataclass(slots=True)
class AdbDevice:
    serial: str
    adb_state: str
    product: str | None = None
    model: str | None = None
    device: str | None = None
    manufacturer: str | None = None
    android_version: str | None = None
    boot_completed: bool = False
    camera_capable: bool = False
    camera_package: str | None = None
    camera_component: str | None = None
    rc_installed: bool = False
    rc_active: bool = False
    is_control_hub: bool = False
    preferred_role: str = "auto"
    effective_role: str = "unassigned"
    suggested_role: str | None = None
    role_reason: str = "No role has been assigned"
    record_key: str = "KEYCODE_VOLUME_UP"

    def as_dict(self) -> dict[str, Any]:
        return {
            "serial": self.serial,
            "serial_suffix": self.serial[-6:],
            "adb_state": self.adb_state,
            "product": self.product,
            "model": self.model or self.device or self.serial,
            "device": self.device,
            "manufacturer": self.manufacturer,
            "android_version": self.android_version,
            "boot_completed": self.boot_completed,
            "camera_capable": self.camera_capable,
            "camera_package": self.camera_package,
            "camera_component": self.camera_component,
            "rc_installed": self.rc_installed,
            "rc_active": self.rc_active,
            "is_control_hub": self.is_control_hub,
            "preferred_role": self.preferred_role,
            "effective_role": self.effective_role,
            "suggested_role": self.suggested_role,
            "role_reason": self.role_reason,
            "record_key": self.record_key,
        }


@dataclass(slots=True)
class MediaItem:
    media_id: str
    path: str
    display_name: str
    size: int
    duration_ms: int
    width: int | None
    height: int | None
    date_added: int
    date_modified: int
    mime_type: str


@dataclass(slots=True)
class _Recording:
    session_id: str
    serial: str
    device_model: str
    camera_package: str
    camera_component: str
    record_key: str
    session_path: Path
    baseline_media_ids: set[str]
    started_wall_time: float
    started_monotonic_ns: int
    remote_item: MediaItem | None = None
    local_path: Path | None = None
    phone_sha256: str | None = None
    laptop_sha256: str | None = None
    ended_monotonic_ns: int | None = None
    warnings: list[str] = field(default_factory=list)


CommandRunner = Callable[[list[str], float], subprocess.CompletedProcess[str]]
FinalizedCallback = Callable[[str, str | None], None]


class AdbCameraService:
    """Discover Android devices and control one assigned native camera."""

    def __init__(
        self,
        recordings_root: Path,
        *,
        settings_path: Path | None = None,
        adb_path: str | None = None,
        discovery_interval_s: float = 3.0,
        command_runner: CommandRunner | None = None,
        on_finalized: FinalizedCallback | None = None,
    ) -> None:
        self._recordings_root = recordings_root.expanduser().resolve()
        self._settings_path = (settings_path or Path(__file__).resolve().parents[1] / "adb_device_roles.local.json").resolve()
        initial_adb_error: str | None = None
        if adb_path:
            self._adb_path = adb_path
        else:
            try:
                self._adb_path = _find_adb()
            except AdbCameraError as error:
                # Keep the rest of the Driver Station usable when Platform
                # Tools is absent; discovery reports the actionable error.
                self._adb_path = "adb"
                initial_adb_error = str(error)
        self._discovery_interval_s = discovery_interval_s
        self._command_runner = command_runner or self._default_command_runner
        self._on_finalized = on_finalized
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._discovery_thread: threading.Thread | None = None
        self._transfer_thread: threading.Thread | None = None
        self._identity_cache: dict[str, dict[str, Any]] = {}
        self._devices: dict[str, AdbDevice] = {}
        self._preferences = self._load_preferences()
        self._adb_error: str | None = initial_adb_error
        self._recording: _Recording | None = None
        self._completed_recordings: dict[str, _Recording] = {}
        self._camera_status: dict[str, Any] = self._empty_camera_status()

    @staticmethod
    def _empty_camera_status() -> dict[str, Any]:
        return {
            "state": "IDLE",
            "recording": False,
            "confirmed": False,
            "session_id": None,
            "device_serial": None,
            "device_model": None,
            "detail": "Assign a camera to enable native recording",
            "started_monotonic_ns": None,
            "ended_monotonic_ns": None,
            "duration_ms": None,
            "width": None,
            "height": None,
            "bytes": None,
            "local_path": None,
            "phone_path": None,
            "phone_copy_pending": False,
            "phone_copy_deleted": False,
            "transfer_progress": None,
            "battery_percent": None,
            "free_storage_bytes": None,
            "error": None,
        }

    def start(self) -> None:
        """Start background discovery; safe to call more than once."""
        with self._lock:
            if self._discovery_thread and self._discovery_thread.is_alive():
                return
            self._stop_event.clear()
            self._discovery_thread = threading.Thread(target=self._discovery_loop, name="adb-device-discovery", daemon=True)
            self._discovery_thread.start()

    def set_finalized_callback(self, callback: FinalizedCallback | None) -> None:
        """Set dashboard bookkeeping hook without affecting camera safety."""
        with self._lock:
            self._on_finalized = callback

    def relocate_verified_recording(self, session_id: str, session_path: Path) -> None:
        """Update the verified-copy reference after dashboard UUID attachment.

        The camera importer initially owns a staging UUID because telemetry has
        not arrived yet.  The unified coordinator later moves that whole tree
        under the telemetry UUID; retain the exact verified-file reference so
        the existing explicit phone-copy cleanup remains safe.
        """
        destination = session_path.resolve()
        with self._lock:
            recording = self._completed_recordings.get(session_id)
            if recording is None:
                return
            original = recording.session_path
            if recording.local_path is not None:
                try:
                    recording.local_path = destination / recording.local_path.relative_to(original)
                except ValueError:
                    return
            recording.session_path = destination
            if self._camera_status.get("session_id") == session_id:
                self._camera_status["local_path"] = str(recording.local_path) if recording.local_path else None
        self._write_manifest(recording, "LAPTOP_VERIFIED")

    def close(self) -> None:
        """Stop discovery and make a best effort to stop an active recording."""
        with self._lock:
            should_stop = bool(self._recording and self._camera_status.get("recording"))
        if should_stop:
            try:
                self.stop_recording()
            except AdbCameraError:
                pass
        self._stop_event.set()
        if self._discovery_thread:
            self._discovery_thread.join(timeout=5)
        if self._transfer_thread:
            self._transfer_thread.join(timeout=20)

    def refresh_devices(self) -> dict[str, Any]:
        """Perform one discovery pass and return the public status."""
        try:
            result = self._run(["devices", "-l"], timeout=5)
            discovered = _parse_adb_devices(result.stdout)
            next_devices: dict[str, AdbDevice] = {}
            for serial, fields in discovered.items():
                preference = self._preferences.get(serial, {})
                device = AdbDevice(
                    serial=serial,
                    adb_state=fields.get("state", "unknown"),
                    product=fields.get("product"),
                    model=fields.get("model"),
                    device=fields.get("device"),
                    preferred_role=str(preference.get("role", "auto")),
                    record_key=str(preference.get("record_key", "KEYCODE_VOLUME_UP")),
                )
                if device.adb_state == "device":
                    identity = self._identity_cache.get(serial)
                    if identity is None:
                        identity = self._probe_identity(serial, device)
                        self._identity_cache[serial] = identity
                    for key, value in identity.items():
                        setattr(device, key, value)
                    device.boot_completed = self._getprop(serial, "sys.boot_completed") == "1"
                    device.rc_active = self._probe_rc_active(serial)
                next_devices[serial] = device
            self._resolve_roles(next_devices)
            with self._lock:
                self._devices = next_devices
                self._adb_error = None
                if self._recording is None and self._camera_status["state"] in {"IDLE", "READY", "NO_CAMERA"}:
                    selected = self._selected_camera_from(next_devices)
                    if selected:
                        self._camera_status.update({
                            "state": "READY",
                            "device_serial": selected.serial,
                            "device_model": selected.model,
                            "detail": f"{selected.model} is assigned as the native camera",
                            "error": None,
                        })
                    else:
                        self._camera_status.update({
                            "state": "NO_CAMERA",
                            "device_serial": None,
                            "device_model": None,
                            "detail": "No connected Android device is assigned as camera",
                        })
        except AdbCameraError as error:
            with self._lock:
                self._adb_error = str(error)
        return self.status()

    def status(self) -> dict[str, Any]:
        with self._lock:
            devices = [device.as_dict() for device in sorted(self._devices.values(), key=lambda item: (item.effective_role, item.model or "", item.serial))]
            selected = next((device for device in devices if device["effective_role"] == "camera"), None)
            return {
                "adb_available": self._adb_error is None,
                "adb_error": self._adb_error,
                "devices": devices,
                "selected_camera_serial": selected["serial"] if selected else None,
                "camera": dict(self._camera_status),
            }

    def assign_role(
        self,
        serial: str,
        role: Literal["auto", "robot_controller", "camera", "ignored"],
    ) -> dict[str, Any]:
        if role not in ROLE_VALUES:
            raise AdbCameraError(f"Unsupported ADB device role: {role}")
        self.refresh_devices()
        with self._lock:
            if self._recording is not None:
                raise AdbCameraError("Device roles cannot change while a camera recording is active or importing")
            device = self._devices.get(serial)
            if device is None:
                raise AdbCameraError(f"ADB device {serial!r} is not connected")
            if role == "camera" and (device.rc_active or device.is_control_hub):
                raise AdbCameraError("This device is reserved as Robot Controller and cannot be assigned as camera")
            if role == "camera" and not device.camera_capable:
                raise AdbCameraError("This Android device does not report camera hardware")
            if role == "robot_controller":
                active_other = next(
                    (item for item in self._devices.values() if item.serial != serial and item.rc_active),
                    None,
                )
                if active_other is not None:
                    raise AdbCameraError(
                        f"{active_other.model or active_other.serial} already has the FTC Robot Controller active"
                    )
                for other_serial, other_preference in self._preferences.items():
                    if other_serial != serial and other_preference.get("role") == "robot_controller":
                        other_preference["role"] = "auto"
            if role == "camera":
                for other_serial, preference in self._preferences.items():
                    if other_serial != serial and preference.get("role") == "camera":
                        preference["role"] = "auto"
            preference = self._preferences.setdefault(serial, {})
            preference.update({
                "role": role,
                "friendly_name": device.model or device.device or serial,
                "record_key": preference.get("record_key", "KEYCODE_VOLUME_UP"),
            })
            self._save_preferences_locked()
        return self.refresh_devices()

    def start_recording(self) -> dict[str, Any]:
        """Open the assigned native camera and inject its record key.

        No assigned camera is treated as an optional camera configuration and
        returns ``NO_CAMERA``.  A configured but unavailable/unsafe camera is an
        error so Init cannot silently proceed without the expected recording.
        """
        self.refresh_devices()
        with self._lock:
            if self._recording is not None:
                raise AdbCameraError("A native camera recording is already active or importing")
            configured_serials = [serial for serial, value in self._preferences.items() if value.get("role") == "camera"]
            selected = self._selected_camera_from(self._devices)
            if selected is None:
                if configured_serials:
                    raise AdbCameraError("The assigned camera is not connected, authorized, or is currently reserved as RC")
                self._camera_status.update({"state": "NO_CAMERA", "detail": "Init continued without an assigned camera"})
                return self.status()
            if selected.rc_active or selected.is_control_hub:
                raise AdbCameraError("The selected camera is currently reserved as Robot Controller")
            if not selected.boot_completed:
                raise AdbCameraError("The selected camera is still booting")
            if not selected.camera_component or not selected.camera_package:
                raise AdbCameraError("Could not resolve the selected device's native camera application")

        serial = selected.serial
        battery_percent = self._battery_percent(serial)
        free_storage_bytes = self._free_storage_bytes(serial)
        baseline = self._query_media(serial)
        session_id = str(uuid.uuid4())
        session_path = self._recordings_root / session_id
        camera_id = _safe_camera_id(selected.model or serial, serial)
        (session_path / "video" / camera_id).mkdir(parents=True, exist_ok=False)
        recording = _Recording(
            session_id=session_id,
            serial=serial,
            device_model=selected.model or serial,
            camera_package=selected.camera_package,
            camera_component=selected.camera_component,
            record_key=selected.record_key,
            session_path=session_path,
            baseline_media_ids={item.media_id for item in baseline},
            started_wall_time=time.time(),
            started_monotonic_ns=time.perf_counter_ns(),
        )
        self._write_manifest(recording, "STARTING")

        try:
            self._shell(serial, "input", "keyevent", "KEYCODE_WAKEUP", timeout=5)
            self._shell(serial, "wm", "dismiss-keyguard", timeout=5)
            time.sleep(0.25)
            # If the operator staged Pro Video (fixed exposure/ISO, high FPS,
            # lens choice), leave that exact foreground activity untouched.
            # Only launch the resolved OEM video activity when Camera is not
            # already visible.
            focus = self._shell(serial, "dumpsys", "window", timeout=8, check=False)
            focus_line = next((line for line in focus.splitlines() if "mCurrentFocus=" in line), "")
            if selected.camera_package not in focus_line:
                self._shell(serial, "am", "start", "-n", selected.camera_component, timeout=8)
                time.sleep(0.9)
            focus = self._shell(serial, "dumpsys", "window", timeout=8, check=False)
            focus_line = next((line for line in focus.splitlines() if "mCurrentFocus=" in line), "")
            if focus_line and selected.camera_package not in focus_line:
                raise AdbCameraError("Unlock the phone and leave its native camera visible before pressing Init")
            self._shell(serial, "input", "keyevent", selected.record_key, timeout=5)
        except Exception:
            shutil.rmtree(session_path, ignore_errors=True)
            raise

        with self._lock:
            self._recording = recording
            self._camera_status = {
                **self._empty_camera_status(),
                "state": "RECORDING_UNVERIFIED",
                "recording": True,
                "confirmed": False,
                "session_id": session_id,
                "device_serial": serial,
                "device_model": recording.device_model,
                "detail": "Record command sent to the native camera",
                "started_monotonic_ns": recording.started_monotonic_ns,
                "battery_percent": battery_percent,
                "free_storage_bytes": free_storage_bytes,
            }
        self._write_manifest(recording, "RECORDING_UNVERIFIED")
        return self.status()

    def stop_recording(self) -> dict[str, Any]:
        """Inject Stop immediately and import/finalize in a background thread."""
        with self._lock:
            recording = self._recording
            if recording is None:
                return self.status()
            if self._transfer_thread and self._transfer_thread.is_alive():
                return self.status()
            self._camera_status.update({
                "state": "STOPPING",
                "recording": False,
                "detail": "Sending stop to the native camera",
                "error": None,
            })
        try:
            self._shell(recording.serial, "input", "keyevent", recording.record_key, timeout=5)
        except AdbCameraError as error:
            with self._lock:
                self._camera_status.update({
                    "state": "ERROR",
                    "recording": True,
                    "detail": "Camera may still be recording; reconnect the device and retry Stop",
                    "error": str(error),
                })
            return self.status()

        recording.ended_monotonic_ns = time.perf_counter_ns()
        with self._lock:
            self._camera_status.update({
                "state": "FINALIZING",
                "ended_monotonic_ns": recording.ended_monotonic_ns,
                "detail": "Waiting for the phone to finalize the MP4",
            })
            self._transfer_thread = threading.Thread(
                target=self._finalize_and_import,
                args=(recording,),
                name=f"adb-camera-import-{recording.session_id}",
                daemon=True,
            )
            self._transfer_thread.start()
        self._write_manifest(recording, "FINALIZING")
        return self.status()

    def delete_phone_copy(self, session_id: str | None = None) -> dict[str, Any]:
        """Delete exactly the verified phone item after caller confirmation."""
        with self._lock:
            target_id = session_id or str(self._camera_status.get("session_id") or "")
            recording = self._completed_recordings.get(target_id)
            if recording is None or recording.remote_item is None or recording.local_path is None:
                raise AdbCameraError("There is no verified phone copy waiting for deletion")
            if not self._camera_status.get("phone_copy_pending"):
                raise AdbCameraError("The phone copy is not pending deletion")
            item = recording.remote_item
            expected_hash = recording.phone_sha256
            if not expected_hash or not recording.local_path.is_file():
                raise AdbCameraError("The verified laptop copy is unavailable")

        self.refresh_devices()
        with self._lock:
            device = self._devices.get(recording.serial)
            if device is None or device.adb_state != "device":
                raise AdbCameraError("Reconnect the same camera phone before deleting its copy")
        remote_hash = self._remote_sha256(recording.serial, item.path)
        local_hash = _sha256(recording.local_path)
        if remote_hash != expected_hash or local_hash != expected_hash:
            raise AdbCameraError("Deletion refused because the phone or laptop file no longer matches the verified recording")
        current = {entry.media_id: entry for entry in self._query_media(recording.serial)}.get(item.media_id)
        if current is None or current.path != item.path or current.size != item.size:
            raise AdbCameraError("Deletion refused because the phone media identity changed")

        self._shell(recording.serial, "rm", "-f", "--", item.path, timeout=15)
        exists = self._shell(recording.serial, "test", "-e", item.path, timeout=5, check=False)
        if exists == "0":
            raise AdbCameraError("The phone reported that the recording still exists after deletion")
        # Remove a stale MediaStore row where supported.  The exact filesystem
        # item is already gone, so failure here is non-destructive and recoverable.
        self._shell(
            recording.serial,
            "content",
            "delete",
            "--uri",
            "content://media/external/video/media",
            "--where",
            f"_id={item.media_id}",
            timeout=8,
            check=False,
        )
        with self._lock:
            self._camera_status.update({
                "state": "VERIFIED",
                "detail": "Laptop copy verified; phone copy deleted",
                "phone_copy_pending": False,
                "phone_copy_deleted": True,
            })
        self._write_manifest(recording, "VERIFIED", phone_copy_deleted=True)
        return self.status()

    def _discovery_loop(self) -> None:
        while not self._stop_event.is_set():
            self.refresh_devices()
            self._stop_event.wait(self._discovery_interval_s)

    def _probe_identity(self, serial: str, parsed: AdbDevice) -> dict[str, Any]:
        manufacturer = self._getprop(serial, "ro.product.manufacturer") or None
        model = self._getprop(serial, "ro.product.model") or parsed.model
        android_version = self._getprop(serial, "ro.build.version.release") or None
        boot_completed = self._getprop(serial, "sys.boot_completed") == "1"
        features = self._shell(serial, "pm", "list", "features", timeout=8, check=False)
        camera_capable = "android.hardware.camera" in features
        rc_installed = bool(self._shell(serial, "pm", "path", FTC_RC_PACKAGE, timeout=8, check=False).strip())
        camera_package, camera_component = self._resolve_native_camera(serial, manufacturer)
        combined = f"{manufacturer or ''} {model or ''}".casefold()
        is_control_hub = "rev robotics" in combined or "control hub" in combined
        return {
            "manufacturer": manufacturer,
            "model": model,
            "android_version": android_version,
            "boot_completed": boot_completed,
            "camera_capable": camera_capable,
            "camera_package": camera_package,
            "camera_component": camera_component,
            "rc_installed": rc_installed,
            "is_control_hub": is_control_hub,
        }

    def _probe_rc_active(self, serial: str) -> bool:
        services = self._shell(serial, "dumpsys", "activity", "services", FTC_RC_PACKAGE, timeout=10, check=False)
        if "com.qualcomm.ftccommon.FtcRobotControllerService" in services:
            return True
        activities = self._shell(serial, "dumpsys", "activity", "activities", timeout=10, check=False)
        return any(
            FTC_RC_PACKAGE in line
            and ("mResumedActivity" in line or "topResumedActivity" in line)
            for line in activities.splitlines()
        )

    def _resolve_native_camera(self, serial: str, manufacturer: str | None) -> tuple[str | None, str | None]:
        candidates = list(CAMERA_COMPONENT_CANDIDATES)
        if manufacturer and manufacturer.casefold() == "samsung":
            candidates.remove("com.sec.android.app.camera")
            candidates.insert(0, "com.sec.android.app.camera")
        for package in candidates:
            if not self._shell(serial, "pm", "path", package, timeout=6, check=False).strip():
                continue
            # Prefer the OEM activity registered for video mode.  Resolving
            # only the package launcher can open photo mode, where the same
            # volume key would take a still instead of toggling recording.
            resolved = self._shell(
                serial,
                "cmd",
                "package",
                "resolve-activity",
                "--brief",
                "-a",
                "android.media.action.VIDEO_CAMERA",
                "-p",
                package,
                timeout=6,
                check=False,
            )
            component = _last_component(resolved)
            if component:
                return package, component
            resolved = self._shell(serial, "cmd", "package", "resolve-activity", "--brief", package, timeout=6, check=False)
            component = _last_component(resolved)
            if component:
                return package, component
        queried = self._shell(
            serial,
            "cmd",
            "package",
            "query-activities",
            "--brief",
            "-a",
            "android.media.action.VIDEO_CAMERA",
            timeout=8,
            check=False,
        )
        for line in queried.splitlines():
            component = line.strip()
            if "/" in component and "ResolverActivity" not in component:
                return component.split("/", 1)[0], component
        return None, None

    def _resolve_roles(self, devices: dict[str, AdbDevice]) -> None:
        for device in devices.values():
            if device.adb_state != "device":
                device.effective_role = "unavailable"
                device.role_reason = f"ADB state is {device.adb_state}"
            elif device.rc_active:
                device.effective_role = "robot_controller"
                device.role_reason = "FTC Robot Controller service/activity is active"
            elif device.is_control_hub:
                device.effective_role = "robot_controller"
                device.role_reason = "REV Control Hub hardware"
            elif device.preferred_role == "robot_controller":
                device.effective_role = "robot_controller"
                device.role_reason = "Remembered operator assignment"
            elif device.preferred_role == "camera":
                device.effective_role = "camera"
                device.role_reason = "Remembered operator assignment"
            elif device.preferred_role == "ignored":
                device.effective_role = "ignored"
                device.role_reason = "Ignored by operator"
            else:
                device.effective_role = "unassigned"
                device.role_reason = "No role has been assigned"

        strong_rcs = [device for device in devices.values() if device.effective_role == "robot_controller"]
        candidates = [
            device for device in devices.values()
            if device.effective_role == "unassigned" and device.camera_capable and device.adb_state == "device"
        ]
        if len(strong_rcs) == 1 and len(candidates) == 1:
            candidates[0].suggested_role = "camera"
            candidates[0].role_reason = "Only camera-capable device not reserved as Robot Controller"

    @staticmethod
    def _selected_camera_from(devices: dict[str, AdbDevice]) -> AdbDevice | None:
        cameras = [device for device in devices.values() if device.effective_role == "camera" and device.adb_state == "device"]
        return cameras[0] if len(cameras) == 1 else None

    def _query_media(self, serial: str) -> list[MediaItem]:
        projection = "_id:_data:_display_name:duration:_size:width:height:date_added:date_modified:mime_type"
        output = self._shell(
            serial,
            "content",
            "query",
            "--uri",
            "content://media/external/video/media",
            "--projection",
            projection,
            timeout=15,
        )
        return _parse_media_rows(output)

    def _finalize_and_import(self, recording: _Recording) -> None:
        try:
            item = self._wait_for_new_media(recording)
            recording.remote_item = item
            local_directory = next((recording.session_path / "video").iterdir())
            partial_path = local_directory / "recording.partial.mp4"
            final_path = local_directory / "recording.mp4"
            with self._lock:
                self._camera_status.update({
                    "state": "IMPORTING",
                    "detail": f"Copying {item.display_name} from {recording.device_model}",
                    "phone_path": item.path,
                    "duration_ms": item.duration_ms,
                    "width": item.width,
                    "height": item.height,
                    "bytes": item.size,
                    "transfer_progress": 0,
                })
            self._write_manifest(recording, "IMPORTING")
            self._pull(recording.serial, item.path, partial_path, item.size)
            if not partial_path.is_file() or partial_path.stat().st_size != item.size:
                raise AdbCameraError("The copied video size does not match the finalized phone file")
            recording.phone_sha256 = self._remote_sha256(recording.serial, item.path)
            recording.laptop_sha256 = _sha256(partial_path)
            if recording.phone_sha256 != recording.laptop_sha256:
                raise AdbCameraError("Phone and laptop SHA-256 hashes do not match")
            os.replace(partial_path, final_path)
            recording.local_path = final_path
            with self._lock:
                self._completed_recordings[recording.session_id] = recording
                if self._recording is recording:
                    self._recording = None
                self._camera_status.update({
                    "state": "LAPTOP_VERIFIED",
                    "recording": False,
                    "confirmed": True,
                    "detail": "Video copied and verified; phone cleanup requires confirmation",
                    "local_path": str(final_path),
                    "phone_copy_pending": True,
                    "phone_copy_deleted": False,
                    "transfer_progress": 100,
                    "error": None,
                })
            self._write_manifest(recording, "LAPTOP_VERIFIED")
            self._notify_finalized(recording.session_id, None)
        except Exception as error:
            with self._lock:
                if self._recording is recording:
                    self._recording = None
                self._camera_status.update({
                    "state": "ERROR",
                    "recording": False,
                    "detail": "Video finalization or import failed; the phone original was not deleted",
                    "error": str(error),
                })
            recording.warnings.append(str(error))
            self._write_manifest(recording, "ERROR")
            self._notify_finalized(recording.session_id, str(error))

    def _notify_finalized(self, session_id: str, error: str | None) -> None:
        callback = self._on_finalized
        if callback is None:
            return
        try:
            callback(session_id, error)
        except Exception:
            # The verified source/import result must not be reclassified when
            # dashboard bookkeeping or upload notification fails.
            pass

    def _wait_for_new_media(self, recording: _Recording, timeout_s: float = 30.0) -> MediaItem:
        deadline = time.monotonic() + timeout_s
        candidate: MediaItem | None = None
        stable_size: int | None = None
        stable_observations = 0
        while time.monotonic() < deadline:
            items = self._query_media(recording.serial)
            candidates = [
                item for item in items
                if item.media_id not in recording.baseline_media_ids
                and item.size > 0
                and item.mime_type.startswith("video/")
                and item.date_added >= int(recording.started_wall_time) - 5
            ]
            if candidates:
                newest = max(candidates, key=lambda item: (item.date_added, item.date_modified, int(item.media_id or 0)))
                if candidate and newest.media_id == candidate.media_id and newest.size == stable_size and newest.duration_ms > 0:
                    stable_observations += 1
                else:
                    stable_observations = 0
                candidate, stable_size = newest, newest.size
                if stable_observations >= 1:
                    return newest
            time.sleep(1)
        raise AdbCameraError("Timed out waiting for the native camera to finalize a new video")

    def _pull(self, serial: str, remote_path: str, local_path: Path, expected_size: int) -> None:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        result_holder: dict[str, Any] = {}

        def transfer() -> None:
            try:
                result_holder["result"] = self._run(["-s", serial, "pull", remote_path, str(local_path)], timeout=3600)
            except Exception as error:
                result_holder["error"] = error

        thread = threading.Thread(target=transfer, name="adb-pull-worker", daemon=True)
        thread.start()
        while thread.is_alive():
            size = local_path.stat().st_size if local_path.exists() else 0
            progress = min(99, int(size * 100 / expected_size)) if expected_size else None
            with self._lock:
                self._camera_status["transfer_progress"] = progress
            thread.join(timeout=0.25)
        if "error" in result_holder:
            raise AdbCameraError(str(result_holder["error"]))

    def _battery_percent(self, serial: str) -> int | None:
        output = self._shell(serial, "dumpsys", "battery", timeout=8, check=False)
        match = re.search(r"^\s*level:\s*(\d+)\s*$", output, re.MULTILINE)
        return int(match.group(1)) if match else None

    def _free_storage_bytes(self, serial: str) -> int | None:
        output = self._shell(serial, "df", "-k", "/sdcard", timeout=8, check=False)
        lines = [line.split() for line in output.splitlines() if line.strip()]
        if len(lines) < 2 or len(lines[-1]) < 4:
            return None
        try:
            return int(lines[-1][3]) * 1024
        except ValueError:
            return None

    def _remote_sha256(self, serial: str, path: str) -> str:
        output = self._shell(serial, "sha256sum", path, timeout=120)
        match = re.match(r"([0-9a-fA-F]{64})\s", output.strip())
        if not match:
            raise AdbCameraError("Could not calculate the phone video SHA-256")
        return match.group(1).lower()

    def _getprop(self, serial: str, name: str) -> str:
        return self._shell(serial, "getprop", name, timeout=6, check=False).strip()

    def _shell(self, serial: str, *args: str, timeout: float, check: bool = True) -> str:
        result = self._run(["-s", serial, "shell", *args], timeout=timeout, check=check)
        if not check:
            return str(result.returncode) if args[:2] == ("test", "-e") else result.stdout
        return result.stdout

    def _run(self, args: list[str], *, timeout: float, check: bool = True) -> subprocess.CompletedProcess[str]:
        try:
            result = self._command_runner([self._adb_path, *args], timeout)
        except subprocess.TimeoutExpired as error:
            raise AdbCameraError(f"ADB command timed out: {' '.join(args)}") from error
        except OSError as error:
            raise AdbCameraError(f"Could not run ADB: {error}") from error
        if check and result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "unknown ADB error"
            raise AdbCameraError(f"ADB command failed: {detail}")
        return result

    @staticmethod
    def _default_command_runner(command: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
        return subprocess.run(command, capture_output=True, check=False, text=True, timeout=timeout)

    def _load_preferences(self) -> dict[str, dict[str, Any]]:
        if not self._settings_path.is_file():
            return {}
        try:
            payload = json.loads(self._settings_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        devices = payload.get("devices") if isinstance(payload, dict) else None
        return {str(serial): dict(value) for serial, value in devices.items() if isinstance(value, dict)} if isinstance(devices, dict) else {}

    def _save_preferences_locked(self) -> None:
        self._settings_path.parent.mkdir(parents=True, exist_ok=True)
        partial = self._settings_path.with_suffix(self._settings_path.suffix + ".partial")
        partial.write_text(json.dumps({"version": 1, "devices": self._preferences}, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(partial, self._settings_path)

    def _write_manifest(self, recording: _Recording, state: str, *, phone_copy_deleted: bool = False) -> None:
        item = recording.remote_item
        manifest = {
            "format": "ftc-adb-native-camera-v1",
            "state": state,
            "session_id": recording.session_id,
            "device": {
                "serial": recording.serial,
                "model": recording.device_model,
                "camera_package": recording.camera_package,
                "camera_component": recording.camera_component,
                "record_key": recording.record_key,
            },
            "timing": {
                "started_wall_time": recording.started_wall_time,
                "started_monotonic_ns": recording.started_monotonic_ns,
                "ended_monotonic_ns": recording.ended_monotonic_ns,
            },
            "media": None if item is None else {
                "media_id": item.media_id,
                "phone_path": item.path,
                "display_name": item.display_name,
                "duration_ms": item.duration_ms,
                "size": item.size,
                "width": item.width,
                "height": item.height,
                "mime_type": item.mime_type,
                "phone_sha256": recording.phone_sha256,
                "laptop_sha256": recording.laptop_sha256,
                "local_path": str(recording.local_path) if recording.local_path else None,
                "phone_copy_deleted": phone_copy_deleted,
            },
            "warnings": list(recording.warnings),
        }
        recording.session_path.mkdir(parents=True, exist_ok=True)
        partial = recording.session_path / "camera-manifest.partial.json"
        partial.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(partial, recording.session_path / "camera-manifest.json")


def _find_adb() -> str:
    found = shutil.which("adb")
    if found:
        return found
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidate = Path(local_app_data) / "Android" / "Sdk" / "platform-tools" / "adb.exe"
        if candidate.is_file():
            return str(candidate)
    raise AdbCameraError("ADB was not found. Install Android SDK Platform-Tools and restart the backend.")


def _parse_adb_devices(output: str) -> dict[str, dict[str, str]]:
    devices: dict[str, dict[str, str]] = {}
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("List of devices attached") or stripped.startswith("*"):
            continue
        fields = stripped.split()
        if len(fields) < 2:
            continue
        entry = {"state": fields[1]}
        for field in fields[2:]:
            if ":" in field:
                key, value = field.split(":", 1)
                entry[key] = value
        devices[fields[0]] = entry
    return devices


def _last_component(output: str) -> str | None:
    for line in reversed(output.splitlines()):
        candidate = line.strip()
        if re.fullmatch(r"[A-Za-z0-9_.]+/[A-Za-z0-9_.$]+", candidate):
            return candidate
    return None


def _parse_media_rows(output: str) -> list[MediaItem]:
    items: list[MediaItem] = []
    for line in output.splitlines():
        match = re.match(r"Row:\s*\d+\s+(.*)", line.strip())
        if not match:
            continue
        values: dict[str, str] = {}
        for part in re.split(r",\s+(?=[A-Za-z_][A-Za-z0-9_]*=)", match.group(1)):
            if "=" in part:
                key, value = part.split("=", 1)
                values[key] = value
        path = values.get("_data", "")
        media_id = values.get("_id", "")
        if not path or not media_id:
            continue
        items.append(MediaItem(
            media_id=media_id,
            path=path,
            display_name=values.get("_display_name") or Path(path).name,
            size=_integer(values.get("_size")),
            duration_ms=_integer(values.get("duration")),
            width=_optional_integer(values.get("width")),
            height=_optional_integer(values.get("height")),
            date_added=_integer(values.get("date_added")),
            date_modified=_integer(values.get("date_modified")),
            mime_type=values.get("mime_type", "video/mp4"),
        ))
    return items


def _integer(value: str | None) -> int:
    try:
        return int(value or 0)
    except ValueError:
        return 0


def _optional_integer(value: str | None) -> int | None:
    number = _integer(value)
    return number or None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_camera_id(model: str, serial: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_-]+", "-", model).strip("-").lower() or "android-camera"
    return f"{normalized}-{serial[-6:].lower()}"
