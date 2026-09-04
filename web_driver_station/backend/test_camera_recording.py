from __future__ import annotations

import tempfile
import unittest
import uuid
from pathlib import Path

from .camera_recording import CameraRecordingCoordinator


class _FakeAdbCamera:
    def __init__(self) -> None:
        self.native_starts = 0

    def refresh_devices(self) -> dict[str, object]:
        return {"selected_camera_serial": "CAMERA123"}

    def status(self) -> dict[str, object]:
        return {"camera": {"state": "READY"}}

    def start_recording(self) -> dict[str, object]:
        self.native_starts += 1
        return {"camera": {"session_id": "adb-capture", "started_monotonic_ns": 1}}

    def stop_recording(self) -> dict[str, object]:
        return self.status()


class _DelayedCameraAdb(_FakeAdbCamera):
    def __init__(self) -> None:
        super().__init__()
        self.available = False

    def refresh_devices(self) -> dict[str, object]:
        return {"selected_camera_serial": "CAMERA123" if self.available else None}


class _FakeScrcpy:
    def __init__(self) -> None:
        self.running = False
        self.calls: list[tuple[object, Path | None, bool, bool]] = []

    def status(self) -> dict[str, object]:
        return {
            "running": self.running,
            "browser_preview": {"enabled": self.running, "error": None},
        }

    def start_camera(self, options, *, output=None, preview=True, browser_preview=False):
        destination = Path(output) if output else None
        self.calls.append((options, destination, preview, browser_preview))
        self.running = True
        if destination is not None:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b"finalized-video")
        return self.status()

    def stop(self) -> dict[str, object]:
        self.running = False
        return {"state": "COMPLETED"}


class _AspectFallbackScrcpy(_FakeScrcpy):
    def wait(self, timeout: float | None = None) -> dict[str, object]:
        options = self.calls[-1][0]
        if options.aspect_ratio:
            self.running = False
            return {"state": "FAILED", "error": "Could not select camera size"}
        raise TimeoutError("preview is still running")


class _DisconnectedPreviewScrcpy(_FakeScrcpy):
    def status(self) -> dict[str, object]:
        status = super().status()
        if not self.running and self.calls:
            status.update({"state": "DISCONNECTED", "error": "Camera disconnected"})
        return status


class CameraRecordingCoordinatorTests(unittest.TestCase):
    def make_coordinator(self, root: Path):
        finalized: list[tuple[str, str | None]] = []
        adb = _FakeAdbCamera()
        coordinator = CameraRecordingCoordinator(
            root / "recordings", adb, settings_path=root / "camera.json",
            on_video_finalized=lambda session_id, error: finalized.append((session_id, error)),
        )
        fake = _FakeScrcpy()
        coordinator._scrcpy = fake
        return coordinator, fake, adb, finalized

    def test_direct_preview_transitions_to_selected_fps_recording_and_attaches_to_telemetry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            coordinator, fake, _adb, finalized = self.make_coordinator(root)

            coordinator.ensure_preview()
            preview = fake.calls[-1]
            self.assertFalse(preview[2])
            self.assertTrue(preview[3])
            self.assertEqual(60, preview[0].fps)

            coordinator.start_recording()
            recording = fake.calls[-1]
            self.assertFalse(recording[2])
            self.assertFalse(recording[3])
            self.assertEqual(60, recording[0].fps)
            session_id = str(uuid.uuid4())
            coordinator.bind_telemetry_session(session_id)
            coordinator.stop_recording()

            self.assertEqual([(session_id, None)], finalized)
            self.assertTrue((root / "recordings" / session_id / "video" / "scrcpy" / "recording.mp4").is_file())

    def test_unpaired_video_stays_in_recoverable_staging(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            coordinator, fake, _adb, finalized = self.make_coordinator(root)
            coordinator.start_recording()
            capture_id = coordinator.status()["capture_id"]
            coordinator.stop_recording()

            self.assertEqual([], finalized)
            self.assertTrue((root / "recordings" / ".camera-staging" / str(capture_id) / "video" / "scrcpy" / "recording.mp4").is_file())
            self.assertIn("no telemetry session", coordinator.status()["error"])

    def test_adb_volume_up_mode_stops_direct_preview_before_native_camera_starts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            coordinator, fake, adb, _finalized = self.make_coordinator(root)
            coordinator.ensure_preview()
            self.assertTrue(fake.running)

            coordinator.update_config(mode="adb_volume_up", direct=coordinator.config_dict()["direct"])
            self.assertFalse(fake.running)
            coordinator.start_recording()

            self.assertEqual(1, adb.native_starts)
            self.assertFalse(fake.running)

    def test_preview_falls_back_to_camera_default_when_requested_aspect_is_unsupported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            coordinator, _fake, _adb, _finalized = self.make_coordinator(root)
            fallback = _AspectFallbackScrcpy()
            coordinator._scrcpy = fallback
            coordinator.update_config(
                mode="scrcpy_direct",
                direct={"facing": "back", "aspect_ratio": "16:9", "fps": 60, "flip": False},
            )

            self.assertIsNone(coordinator.config_dict()["direct"]["aspect_ratio"])
            self.assertEqual(2, len(fallback.calls))
            self.assertEqual("16:9", fallback.calls[0][0].aspect_ratio)
            self.assertIsNone(fallback.calls[1][0].aspect_ratio)

    def test_disconnected_preview_is_restarted_automatically(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            coordinator, _fake, _adb, _finalized = self.make_coordinator(root)
            disconnected = _DisconnectedPreviewScrcpy()
            coordinator._scrcpy = disconnected
            coordinator.ensure_preview()
            self.assertEqual(1, len(disconnected.calls))

            disconnected.running = False
            with coordinator._lock:
                coordinator._schedule_preview_recovery_locked(disconnected.status())
            # Run the recovery action directly so this test does not wait for
            # the production backoff delay.
            coordinator._retry_preview_after_delay(0)

            self.assertEqual(2, len(disconnected.calls))
            self.assertTrue(disconnected.running)

    def test_preview_retries_until_the_assigned_camera_appears_after_startup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            adb = _DelayedCameraAdb()
            coordinator = CameraRecordingCoordinator(root / "recordings", adb, settings_path=root / "camera.json")
            fake = _FakeScrcpy()
            coordinator._scrcpy = fake

            coordinator.ensure_preview()
            self.assertEqual([], fake.calls)
            self.assertEqual("Waiting for the assigned Android camera to become available.", coordinator.status()["error"])

            adb.available = True
            # Run the scheduled recovery directly instead of waiting two seconds.
            coordinator._retry_preview_after_delay(0)

            self.assertEqual(1, len(fake.calls))
            self.assertTrue(fake.running)
            self.assertIsNone(coordinator.status()["error"])


if __name__ == "__main__":
    unittest.main()
