from __future__ import annotations

import asyncio
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from .ftclog import FtcLogError, RawFtcLogRecorder
from .main import RobotDataService
from .recording_uploader import RecordingUploadError


class _FakeUploader:
    def __init__(self) -> None:
        self.calls: list[Path] = []

    def upload(self, directory: Path, *, progress_callback=None):
        self.calls.append(directory)
        if progress_callback is not None:
            progress_callback(1, 1, "raw/stream-00000.ftclog")
        return SimpleNamespace(uploaded_files=1, skipped_files=0)

    def delete(self, session_id: str) -> None:
        return None


class _FlakyUploader(_FakeUploader):
    def __init__(self) -> None:
        super().__init__()
        self._failures_remaining = 1

    def upload(self, directory: Path, *, progress_callback=None):
        self.calls.append(directory)
        if self._failures_remaining:
            self._failures_remaining -= 1
            raise RecordingUploadError("SSH connection failed: Device disconnected")
        if progress_callback is not None:
            progress_callback(1, 1, "raw/stream-00000.ftclog")
        return SimpleNamespace(uploaded_files=1, skipped_files=0)


class RecordingDecisionTests(unittest.IsolatedAsyncioTestCase):
    async def test_finalized_recording_waits_for_upload_decision_and_can_be_discarded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            service = RobotDataService()
            service._raw_recorder = RawFtcLogRecorder(Path(temporary_directory))
            uploader = _FakeUploader()
            service._recording_uploader = uploader
            session_id = str(uuid.uuid4())
            service._raw_recorder.append(session_id, b"frame", 1)

            await service._finalize_recording(session_id)
            self.assertEqual("ready_to_upload", service._upload_states[session_id]["state"])
            self.assertEqual([], uploader.calls)
            self.assertTrue(service._raw_recorder.session_path(session_id).is_dir())

            result = await service.discard_recording(session_id)
            self.assertEqual("discarded", result["state"])
            self.assertEqual([], uploader.calls)
            with self.assertRaises(FtcLogError):
                service._raw_recorder.session_path(session_id)

    async def test_upload_waits_for_video_and_can_retry_after_a_video_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            service = RobotDataService()
            service._raw_recorder = RawFtcLogRecorder(Path(temporary_directory))
            uploader = _FakeUploader()
            service._recording_uploader = uploader
            session_id = str(uuid.uuid4())
            service._raw_recorder.append(session_id, b"frame", 1)
            video_ready = False
            service.set_capture_callbacks(
                video_ready_checker=lambda _: video_ready,
                session_bound=lambda _: None,
                capture_stop=lambda _: None,
            )
            await service._finalize_recording(session_id)
            self.assertEqual("waiting_for_video", service._upload_states[session_id]["state"])
            self.assertEqual([], uploader.calls)

            video_ready = True
            await service.video_finalized(session_id, None)
            self.assertEqual("ready_to_upload", service._upload_states[session_id]["state"])
            self.assertEqual([], uploader.calls)

            failed_session = str(uuid.uuid4())
            video_ready = False
            service._raw_recorder.append(failed_session, b"frame", 1)
            await service._finalize_recording(failed_session)
            await service.video_finalized(failed_session, "Video import failed")
            self.assertEqual("error", service._upload_states[failed_session]["state"])

            # A corrected video can be retried without replacing the raw log.
            service._video_errors.pop(failed_session)
            video_ready = True
            self.assertEqual("uploading", (await service.upload_recording(failed_session, keep_local=True))["state"])

    async def test_transient_upload_disconnect_retries_automatically(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            service = RobotDataService()
            service._raw_recorder = RawFtcLogRecorder(Path(temporary_directory))
            uploader = _FlakyUploader()
            service._recording_uploader = uploader
            session_id = str(uuid.uuid4())
            service._raw_recorder.append(session_id, b"frame", 1)

            with patch.object(service, "_AUTO_UPLOAD_RETRY_DELAYS_SECONDS", (0.001,)):
                await service._finalize_recording(session_id)
                await service.upload_recording(session_id, keep_local=True)
                for _ in range(40):
                    if service._upload_states[session_id]["state"] == "complete":
                        break
                    await asyncio.sleep(0.01)

            self.assertEqual("complete", service._upload_states[session_id]["state"])
            self.assertEqual(2, len(uploader.calls))

    async def test_upload_progress_tracks_total_bytes_and_current_file(self) -> None:
        service = RobotDataService()
        session_id = str(uuid.uuid4())
        service._upload_states[session_id] = {"state": "uploading", "detail": "Upload started"}

        service._record_upload_progress(session_id, 1_000, 375, "raw/stream-00000.ftclog")

        self.assertEqual(
            {
                "state": "uploading",
                "detail": "Uploading raw/stream-00000.ftclog",
                "totalBytes": 1_000,
                "uploadedBytes": 375,
                "currentFile": "raw/stream-00000.ftclog",
            },
            service._upload_states[session_id],
        )

    async def test_tcp_disconnect_finalizes_the_matched_camera_capture(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            service = RobotDataService()
            service._raw_recorder = RawFtcLogRecorder(Path(temporary_directory))
            service._recording_uploader = None
            session_id = str(uuid.uuid4())
            peer = ("127.0.0.1", 5810)
            stopped: list[str] = []
            service.set_capture_callbacks(
                video_ready_checker=lambda _: True,
                session_bound=lambda _: None,
                capture_stop=stopped.append,
            )
            service._raw_recorder.append(session_id, b"frame", 1)
            service._connection_sessions[peer] = session_id

            await service._on_connection(False, peer)

            self.assertEqual([session_id], stopped)


if __name__ == "__main__":
    unittest.main()
