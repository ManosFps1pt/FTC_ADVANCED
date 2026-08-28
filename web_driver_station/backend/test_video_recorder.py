from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from .video_recorder import CameraRecordingConfig, VideoRecorder


class _Frame:
    shape = (48, 64, 3)


class _Capture:
    def isOpened(self) -> bool:
        return True

    def set(self, _property: int, _value: float) -> bool:
        return True

    def grab(self) -> bool:
        time.sleep(0.002)
        return True

    def retrieve(self) -> tuple[bool, _Frame]:
        return True, _Frame()

    def get(self, _property: int) -> float:
        return 30.0

    def release(self) -> None:
        pass


class _Writer:
    def __init__(self, path: str) -> None:
        self._path = Path(path)

    def isOpened(self) -> bool:
        return True

    def write(self, _frame: _Frame) -> None:
        pass

    def release(self) -> None:
        self._path.write_bytes(b"not-a-real-mp4-but-a-finalized-test-artifact")


class _FakeCv2:
    CAP_PROP_FRAME_WIDTH = 3
    CAP_PROP_FRAME_HEIGHT = 4
    CAP_PROP_FPS = 5
    CAP_PROP_BUFFERSIZE = 38

    def VideoCapture(self, _index: int) -> _Capture:
        return _Capture()

    @staticmethod
    def VideoWriter_fourcc(*_codec: str) -> int:
        return 0

    @staticmethod
    def VideoWriter(path: str, _fourcc: int, _fps: float, _size: tuple[int, int]) -> _Writer:
        return _Writer(path)


class VideoRecorderTests(unittest.TestCase):
    def test_recording_finalizes_segment_and_frame_time_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            recorder = VideoRecorder(Path(temporary_directory))
            with patch("web_driver_station.backend.video_recorder._load_opencv", return_value=_FakeCv2()):
                started = recorder.start(CameraRecordingConfig(width=64, height=48, fps=30, segment_seconds=60))
                deadline = time.monotonic() + 1
                while recorder.status()["frames_written"] < 3 and time.monotonic() < deadline:
                    time.sleep(0.01)
                stopped = recorder.stop()

            self.assertFalse(stopped["recording"])
            self.assertIsNone(stopped["error"])
            self.assertGreaterEqual(stopped["frames_written"], 3)
            self.assertEqual(1, stopped["segments_completed"])
            session_path = recorder.session_directory(started["session_id"])
            self.assertTrue((session_path / "segment-00000.mp4").is_file())
            frame_times = [json.loads(line) for line in (session_path / "segment-00000.frames.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(stopped["frames_written"], len(frame_times))
            self.assertTrue(all("capture_monotonic_ns" in item for item in frame_times))
            manifest = json.loads((session_path / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual("READY", manifest["state"])
            self.assertEqual("mp4v", manifest["segments"][0]["codec"])

    def test_invalid_camera_id_is_rejected(self) -> None:
        with self.assertRaisesRegex(Exception, "Camera ID"):
            CameraRecordingConfig(camera_id="../../unsafe").validate()


if __name__ == "__main__":
    unittest.main()
