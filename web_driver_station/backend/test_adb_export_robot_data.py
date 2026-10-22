from __future__ import annotations

import tempfile
import unittest
import uuid
from pathlib import Path

from .adb_export_robot_data import AdbRobotDataError, _validate_staging
from .ftclog import RawFtcLogRecorder


class AdbExportRobotDataTests(unittest.TestCase):
    def test_validation_accepts_recoverable_ftclog_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            recorder = RawFtcLogRecorder(root)
            session_id = str(uuid.uuid4())
            recorder.append(session_id, b"robot-frame", 123)
            recorder.close_all()

            sessions, frames = _validate_staging(root)

            self.assertEqual((session_id,), sessions)
            self.assertEqual(1, frames)

    def test_validation_rejects_a_session_without_raw_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            session_id = str(uuid.uuid4())
            (Path(directory) / session_id).mkdir()

            with self.assertRaises(AdbRobotDataError):
                _validate_staging(Path(directory))


if __name__ == "__main__":
    unittest.main()
