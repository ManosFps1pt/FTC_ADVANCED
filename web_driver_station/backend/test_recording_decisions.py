from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pydantic import ValidationError

from .ftclog import FtcLogError, RawFtcLogRecorder
from .main import RecordingUploadRequest, RobotDataService
from .recording_metadata import RECORDING_METADATA_FILE, CompleteRecordingMetadata
from .recording_uploader import RecordingUploadError, _validated_session

DETAILS = {"battery_index": 0, "robot_version": 1, "opmode_type": "teleop"}
NOTES = CompleteRecordingMetadata(title="Drive tuning", description="First run\n\nLowered the gain.", **DETAILS)


class _FakeUploader:
    def __init__(self) -> None:
        self.calls: list[Path] = []

    def upload(self, directory: Path, *, progress_callback=None):
        self.calls.append(directory)
        source, _, files = _validated_session(directory)
        metadata_path = source / RECORDING_METADATA_FILE
        if metadata_path not in files:
            raise RecordingUploadError("Recording details are missing from the upload")
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
        if self._failures_remaining:
            self.calls.append(directory)
            self._failures_remaining -= 1
            raise RecordingUploadError("SSH connection failed: Device disconnected")
        return super().upload(directory, progress_callback=progress_callback)


class RecordingUploadRequestTests(unittest.TestCase):
    def test_upload_request_requires_notes_and_valid_recording_details(self) -> None:
        prototype = {"title": "Drive", "description": "Details", **DETAILS}
        for key, value in (("title", "  "), ("description", "\n"), ("battery_index", -1), ("battery_index", 9),
                           ("battery_index", True), ("battery_index", 2.5), ("battery_index", "0"),
                           ("robot_version", 0), ("robot_version", 1.5), ("robot_version", True),
                           ("opmode_type", "practice")):
            with self.subTest(key=key, value=value), self.assertRaises(ValidationError):
                RecordingUploadRequest.model_validate({**prototype, key: value})
        for key in DETAILS:
            with self.subTest(missing=key), self.assertRaises(ValidationError):
                RecordingUploadRequest.model_validate({name: value for name, value in prototype.items() if name != key})

        request = RecordingUploadRequest(
            title="  Drive tuning ", description="\nFirst run\n\nLowered the gain. ", keep_local=True, **DETAILS,
        )
        self.assertEqual(NOTES.title, request.title)
        self.assertEqual(NOTES.description, request.description)
        self.assertEqual(0, request.battery_index)
        self.assertEqual(8, RecordingUploadRequest.model_validate({**prototype, "battery_index": 8}).battery_index)


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
            result = await service.upload_recording(failed_session, metadata=NOTES, keep_local=True)
            self.assertEqual("uploading", result["state"])
            await self._wait_for_upload_state(service, failed_session, "complete")

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
                await service.upload_recording(session_id, metadata=NOTES, keep_local=True)
                await self._wait_for_upload_state(service, session_id, "complete")

            self.assertEqual(2, len(uploader.calls))
            session_path = service._raw_recorder.session_path(session_id)
            self.assertEqual(NOTES.model_dump(), json.loads((session_path / RECORDING_METADATA_FILE).read_bytes()))

    async def test_failed_upload_offers_same_metadata_on_manual_retry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = RobotDataService()
            service._raw_recorder = RawFtcLogRecorder(Path(directory))
            service._recording_uploader = _FlakyUploader()
            session_id = str(uuid.uuid4())
            service._raw_recorder.append(session_id, b"frame", 1)
            await service._finalize_recording(session_id)

            with patch.object(service, "_AUTO_UPLOAD_RETRY_DELAYS_SECONDS", ()):
                await service.upload_recording(session_id, metadata=NOTES, keep_local=True)
                await self._wait_for_upload_state(service, session_id, "error")
            self.assertEqual(NOTES.model_dump(), service.status()["recording"]["uploads"][session_id]["metadata"])

            changed = CompleteRecordingMetadata(title="Changed", description="Different notes", **{**DETAILS, "battery_index": 8})
            await service.upload_recording(session_id, metadata=changed, keep_local=True)
            await self._wait_for_upload_state(service, session_id, "complete")
            session_path = service._raw_recorder.session_path(session_id)
            self.assertEqual(NOTES.model_dump(), json.loads((session_path / RECORDING_METADATA_FILE).read_bytes()))

    async def _wait_for_upload_state(self, service: RobotDataService, session_id: str, expected: str) -> None:
        for _ in range(100):
            if service._upload_states[session_id]["state"] == expected:
                return
            await asyncio.sleep(0.01)
        self.fail(f"Expected {expected}, got {service._upload_states[session_id]}")

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


    async def test_interrupted_stream_can_upload_without_hiding_the_interruption(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = RobotDataService()
            service._raw_recorder = RawFtcLogRecorder(Path(directory))
            service._recording_uploader = _FakeUploader()
            session = str(uuid.uuid4())
            peer = ("127.0.0.1", 12345)
            partial = service._raw_recorder.append(session, b"captured frame", 1)
            service._connection_sessions[peer] = session
            await service._on_connection(False, peer)
            self.assertTrue(partial.is_file())
            self.assertEqual("ready_to_upload", service._upload_states[session]["state"])
            self.assertIn("interrupted", service._upload_states[session]["detail"])
            _, _, files = _validated_session(partial.parent.parent)
            self.assertIn(partial.resolve(), files)
            await service.upload_recording(session, metadata=NOTES, keep_local=True)
            await self._wait_for_upload_state(service, session, "complete")
            self.assertTrue(partial.is_file())

    async def test_active_reconnected_stream_still_blocks_upload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            recorder = RawFtcLogRecorder(Path(directory))
            session = str(uuid.uuid4())
            recorder.append(session, b"first", 1)
            recorder.close_session(session, complete=False)
            recorder.append(session, b"reconnected", 2)
            with self.assertRaisesRegex(RecordingUploadError, "active or unclosed"):
                _validated_session(recorder.session_path(session))
            recorder.close_session(session)
            _, _, files = _validated_session(recorder.session_path(session))
            self.assertEqual(3, len(files))


if __name__ == "__main__":
    unittest.main()
