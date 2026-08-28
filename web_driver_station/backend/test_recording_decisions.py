from __future__ import annotations

import asyncio
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace

from .ftclog import FtcLogError, RawFtcLogRecorder
from .main import RobotDataService


class _FakeUploader:
    def __init__(self) -> None:
        self.calls: list[Path] = []

    def upload(self, directory: Path):
        self.calls.append(directory)
        return SimpleNamespace(uploaded_files=1, skipped_files=0)

    def delete(self, session_id: str) -> None:
        return None


class RecordingDecisionTests(unittest.IsolatedAsyncioTestCase):
    async def test_finalized_recording_waits_for_upload_decision(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            service = RobotDataService()
            service._raw_recorder = RawFtcLogRecorder(Path(temporary_directory))
            uploader = _FakeUploader()
            service._recording_uploader = uploader
            session_id = str(uuid.uuid4())
            service._raw_recorder.append(session_id, b"frame", 1)

            await service._finalize_recording(session_id)
            self.assertEqual("pending_confirmation", service._upload_states[session_id]["state"])
            self.assertEqual([], uploader.calls)

            self.assertEqual("uploading", (await service.upload_recording(session_id))["state"])
            for _ in range(20):
                if service._upload_states[session_id]["state"] == "complete":
                    break
                await asyncio.sleep(0.01)
            self.assertEqual("complete", service._upload_states[session_id]["state"])
            self.assertEqual(1, len(uploader.calls))
            with self.assertRaises(FtcLogError):
                service._raw_recorder.session_path(session_id)

    async def test_pending_recording_can_be_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            service = RobotDataService()
            service._raw_recorder = RawFtcLogRecorder(Path(temporary_directory))
            service._recording_uploader = _FakeUploader()
            session_id = str(uuid.uuid4())
            service._raw_recorder.append(session_id, b"frame", 1)
            await service._finalize_recording(session_id)
            final_path = service._raw_recorder.session_path(session_id)

            result = await service.delete_recording(session_id)
            self.assertEqual("deleted", result["state"])
            self.assertFalse(final_path.exists())


if __name__ == "__main__":
    unittest.main()
