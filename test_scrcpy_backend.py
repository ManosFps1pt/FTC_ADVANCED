"""Lifecycle tests use a real child process, without touching a phone."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import scrcpy_backend as sb

FAKE_RECORDER = r"""
import pathlib, signal, sys, time
output, mode = pathlib.Path(sys.argv[1]), sys.argv[2]
def finish(signum, frame):
    if mode == 'ignore':
        return
    if mode == 'record':
        with output.open('ab') as stream:
            stream.write(b'finalized')
        print('INFO: Recording complete to mp4 file: ' + str(output), flush=True)
    sys.exit(0)
signal.signal(signal.SIGINT, finish)
if hasattr(signal, 'SIGBREAK'):
    signal.signal(signal.SIGBREAK, finish)
print('INFO: Recording started to mp4 file: ' + str(output), flush=True)
if mode in ('record', 'disconnect'):
    output.write_bytes(b'frame-data' * 20000)
if mode == 'record':
    print('INFO: 30 fps', flush=True)
print('READY', flush=True)
if mode == 'disconnect':
    print('INFO: Recording complete to mp4 file: ' + str(output), flush=True)
    sys.exit(2)
if mode == 'empty':
    sys.exit(0)
while True:
    time.sleep(0.01)
"""


class BackendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.backend = sb.ScrcpyBackend(sys.executable, sys.executable)
        self.addCleanup(self.backend.stop)

    def launch_fake(self, mode: str) -> Path:
        output = self.root / "recording with spaces.mp4"
        self.backend._launch(["-u", "-c", FAKE_RECORDER, str(output), mode], output)
        deadline = time.monotonic() + 15
        while "READY" not in self.backend.status()["logs"]:
            if time.monotonic() >= deadline or self.backend._finished.is_set():
                self.fail(f"Fake recorder did not start: {self.backend.status()}")
            time.sleep(0.02)
        return output

    def test_stop_finalizes_owned_process_and_is_idempotent(self) -> None:
        output = self.launch_fake("record")
        before = self.backend.status()
        self.assertTrue(before["frame_activity_observed"])
        self.assertGreater(before["bytes"], 0)
        result = self.backend.stop()
        self.assertEqual(result["state"], "COMPLETED", result)
        self.assertEqual(result["returncode"], 0)
        self.assertTrue(result["scrcpy_finalized"])
        self.assertTrue(output.read_bytes().endswith(b"finalized"))
        self.assertEqual(self.backend.stop()["state"], "COMPLETED")

    def test_started_message_does_not_confirm_capture(self) -> None:
        self.launch_fake("started")
        state = self.backend.status()
        self.assertFalse(state["frame_activity_observed"])
        self.assertFalse(state["scrcpy_finalized"])
        self.assertEqual(state["bytes"], 0)
        self.assertEqual(self.backend.stop()["state"], "FAILED")

    def test_timeout_reports_failure_and_retains_partial_recording(self) -> None:
        output = self.launch_fake("ignore")
        output.write_bytes(b"partial")
        result = self.backend.stop(timeout=0.5)
        self.assertEqual(result["state"], "FAILED")
        self.assertIn("timed out", result["error"])
        self.assertEqual(output.read_bytes(), b"partial")
        self.assertFalse(result["scrcpy_finalized"])

    def test_disconnect_is_not_success_even_if_received_file_finalizes(self) -> None:
        output = self.root / "partial.mp4"
        self.backend._launch(["-u", "-c", FAKE_RECORDER, str(output), "disconnect"], output)
        result = self.backend.wait(15)
        self.assertEqual(result["state"], "DISCONNECTED")
        self.assertEqual(result["returncode"], 2)
        self.assertTrue(result["scrcpy_finalized"])
        self.assertTrue(output.exists())

    def test_empty_recording_is_failure_even_with_zero_exit(self) -> None:
        output = self.root / "empty.mp4"
        self.backend._launch(["-u", "-c", FAKE_RECORDER, str(output), "empty"], output)
        result = self.backend.wait(15)
        self.assertEqual(result["state"], "FAILED")
        self.assertEqual(result["returncode"], 0)

    def test_second_start_rejected_without_losing_first_process(self) -> None:
        self.launch_fake("record")
        pid = self.backend.status()["pid"]
        with self.assertRaises(sb.ScrcpyError):
            self.backend.start_native(["--help"])
        self.assertEqual(self.backend.status()["pid"], pid)
        self.assertEqual(self.backend.stop()["state"], "COMPLETED")

    def test_native_preserves_unknown_options_spaces_and_shell_metacharacters(self) -> None:
        args = ["--future-option=literal & value", "x; echo unwanted", "$HOME"]
        self.backend.start_native(["-c", "import json,sys; print(json.dumps(sys.argv[1:]))", *args])
        result = self.backend.wait(15)
        self.assertEqual(result["state"], "STOPPED")
        self.assertEqual(json.loads(result["logs"][0]), args)

    def test_wait_timeout_does_not_stop_process(self) -> None:
        self.launch_fake("record")
        with self.assertRaises(TimeoutError):
            self.backend.wait(0.01)
        self.assertTrue(self.backend.status()["running"])

    def test_existing_recording_is_never_overwritten(self) -> None:
        output = self.root / "keep.mp4"
        output.write_bytes(b"original")
        with patch.object(self.backend, "select_device", return_value="PHONE"), \
             patch.object(self.backend, "check_camera"):
            with self.assertRaisesRegex(sb.ScrcpyError, "already exists"):
                self.backend.start_camera(sb.CameraOptions(), output=output)
        self.assertEqual(output.read_bytes(), b"original")
        self.assertIsNone(self.backend._process)

    def test_invalid_options_do_not_contact_device_or_create_file(self) -> None:
        output = self.root / "invalid.mp4"
        with patch.object(self.backend, "adb") as adb:
            with self.assertRaisesRegex(sb.ScrcpyError, "cannot be combined"):
                self.backend.start_camera(sb.CameraOptions(size="1920x1080", max_size=1920), output=output)
        adb.assert_not_called()
        self.assertFalse(output.exists())

    def test_browser_preview_requests_a_small_slow_source_but_recording_keeps_selected_fps(self) -> None:
        preview_options = sb.CameraOptions(facing="back", fps=60)
        with patch.object(self.backend, "select_device", return_value="PHONE"), \
             patch.object(self.backend, "check_camera"), \
             patch.object(self.backend, "_launch", return_value={}) as launch:
            self.backend.start_camera(preview_options, preview=False, browser_preview=True)
        preview_args = launch.call_args.args[0]
        self.assertIn("--max-size=240", preview_args)
        self.assertIn("--camera-fps=10", preview_args)
        self.assertNotIn("--camera-fps=60", preview_args)

        output = self.root / "selected-fps.mp4"
        with patch.object(self.backend, "select_device", return_value="PHONE"), \
             patch.object(self.backend, "check_camera"), \
             patch.object(self.backend, "_launch", return_value={}) as launch:
            self.backend.start_camera(preview_options, output=output, preview=False)
        recording_args = launch.call_args.args[0]
        self.assertIn("--camera-fps=60", recording_args)
        self.assertNotIn("--max-size=240", recording_args)

    def test_camera_preflight_only_requires_supported_android(self) -> None:
        with patch.object(self.backend, "adb", return_value="36") as adb:
            self.backend.check_camera("PHONE")
        adb.assert_called_once_with("-s", "PHONE", "shell", "getprop", "ro.build.version.sdk")

    def test_fractional_duration_fails_before_starting(self) -> None:
        with patch.object(self.backend, "adb") as adb:
            with self.assertRaisesRegex(sb.ScrcpyError, "whole number"):
                self.backend.start_camera(sb.CameraOptions(duration=1.5), output=self.root / "clip.mp4")
        adb.assert_not_called()

    def test_installed_scrcpy_accepts_generated_camera_arguments(self) -> None:
        try:
            real = sb.ScrcpyBackend()
        except sb.ScrcpyError:
            self.skipTest("Optional installed-scrcpy argument compatibility check")
        for options in (
            sb.CameraOptions(duration=10),
            sb.CameraOptions(camera_id="0", size="1920x1080", fps=30, duration=10.0, audio=False),
            sb.CameraOptions(facing="back", max_size=1920, aspect_ratio="16:9", zoom=1.5, torch=True),
        ):
            for preview in (True, False):
                with self.subTest(options=options, preview=preview):
                    args = options.arguments("NOT_A_DEVICE", preview=preview, output=self.root / "parse-only.mp4")
                    # --version parses flags but exits before ADB or recording.
                    text = real._query([real.scrcpy_path, "--version", *args],
                                       real._environment(require_adb=False))
                    self.assertIn("scrcpy", text)
        self.assertFalse((self.root / "parse-only.mp4").exists())

    def test_camera_size_query_uses_camera_source(self) -> None:
        with patch.object(self.backend, "select_device", return_value="PHONE"), \
             patch.object(self.backend, "_query", return_value="sizes") as query:
            self.assertEqual(self.backend.inspect("sizes", "PHONE", "0"), "sizes")
        args = query.call_args.args[0]
        self.assertIn("--video-source=camera", args)
        self.assertIn("--camera-id=0", args)

    def test_old_android_is_rejected(self) -> None:
        with patch.object(self.backend, "adb", return_value="30"):
            with self.assertRaisesRegex(sb.ScrcpyError, "Android 12"):
                self.backend.check_camera("PHONE")

    def test_ambiguous_and_unauthorized_devices_are_rejected(self) -> None:
        devices = [{"serial": "A", "state": "device"}, {"serial": "B", "state": "device"},
                   {"serial": "C", "state": "unauthorized"}]
        with patch.dict(os.environ, {"ANDROID_SERIAL": ""}), \
             patch.object(self.backend, "devices", return_value=devices):
            with self.assertRaises(sb.ScrcpyError):
                self.backend.select_device()
            with self.assertRaises(sb.ScrcpyError):
                self.backend.select_device("C")
            self.assertEqual(self.backend.select_device("B"), "B")


if __name__ == "__main__":
    unittest.main()
