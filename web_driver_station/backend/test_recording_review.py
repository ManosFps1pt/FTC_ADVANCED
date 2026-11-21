from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from uuid import UUID, uuid4
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from recording_viewer.backend.main import create_library_app

from .ftclog import RawFtcLogRecorder
from .main import RobotDataService
from .protocol import robot_data_pb2 as wire
from .recording_uploader import RecordingUploadError, UploadResult
from .review_api import review_routes


def record_shooter_run(recorder: RawFtcLogRecorder, session_id: str) -> Path:
    session = UUID(session_id).bytes
    connection = uuid4().bytes

    def frame(**payload):
        return wire.Envelope(
            protocol_version=3, session_id=session, connection_id=connection,
            robot_elapsed_ns=1_000_000_000, **payload,
        ).SerializeToString()

    recorder.append(session_id, frame(hello=wire.Hello(
        robot_id="review-test", robot_name="Test Robot", op_mode_name="Main TeleOp",
    )), 1)
    channels = (
        ("shooter.targetRpm", "RPM"), ("shooter.commandedRpm", "RPM"),
        ("shooter.measuredRpm", "RPM"), ("fos.dutyCycle", "normalized"),
    )
    recorder.append(session_id, frame(schema=wire.Schema(revision=1, channels=[
        wire.Channel(channel_id=index, key=key, label=key, unit=unit, value_type=wire.FLOAT64, role=wire.MEASURED)
        for index, (key, unit) in enumerate(channels, 1)
    ])), 2)
    for sample in range(40):
        rpm = 2400.0 if 18 <= sample < 22 else 3000.0
        recorder.append(session_id, frame(sample_batch=wire.SampleBatch(snapshots=[
            wire.Snapshot(
                sample_sequence=sample, schema_revision=1, robot_time_ns=1_000_000_000 + sample * 50_000_000,
                values=[wire.ChannelValue(channel_id=index, float64_value=value)
                        for index, value in enumerate((3000, 3000, rpm, .279), 1)],
            ),
        ])), 3 + sample)
    return recorder.close_session(session_id)


class ReplayReviewTests(unittest.TestCase):
    def test_recency_events_windows_and_mounted_replay_use_same_recording(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            service = RobotDataService()
            service._raw_recorder = RawFtcLogRecorder(root)
            latest, older = str(UUID(int=1)), str(UUID(int=2))
            record_shooter_run(service._raw_recorder, latest)
            path = record_shooter_run(service._raw_recorder, older)
            os.utime(path, (1_600_000_000, 1_600_000_000))
            active = str(uuid4())
            service._raw_recorder.append(active, b"active", 1)
            app = FastAPI()
            app.include_router(review_routes(service))
            app.mount("/replays", create_library_app(root, excluded=service.review_exclusions))
            try:
                with TestClient(app) as client:
                    runs = client.get("/api/reviews").raise_for_status().json()["runs"]
                    self.assertEqual([latest, older], [run["session_id"] for run in runs])
                    self.assertEqual(404, client.get(f"/api/reviews/{active}/report").status_code)
                    result = client.get("/api/reviews/latest/events/shooter_dips").raise_for_status().json()
                    event, = result["events"]
                    self.assertAlmostEqual(.9, event["t_s"])
                    self.assertEqual(600, event["drop_rpm"])
                    self.assertTrue(event["replay_path"].endswith("&t=0.900"))
                    self.assertEqual([], result["missing_signals"])
                    sags = client.get("/api/reviews/latest/events/voltage_sags").raise_for_status().json()
                    self.assertEqual(["robot.voltage"], sags["missing_signals"])
                    window = client.get("/api/reviews/latest/window", params={
                        "start": .5, "end": 1.2, "signals": "shooter.measuredRpm", "points": 2,
                    }).raise_for_status().json()
                    self.assertEqual(2400, window["signals"]["shooter.measuredRpm"]["statistics"]["min"])
                    self.assertEqual(2, len(window["seconds"]))
                    replay = client.get(f"/replays/api/recordings/{latest}").raise_for_status().json()
                    self.assertEqual(40, len(replay["snapshots"]))
                    client.patch(f"/api/reviews/{latest}", json={"title": "Lab attempt"}).raise_for_status()
                    renamed = client.get("/api/reviews?search=Lab").raise_for_status().json()["runs"]
                    self.assertEqual("Lab attempt", renamed[0]["title"])
                    client.delete(f"/api/reviews/{latest}").raise_for_status()
                    self.assertEqual(404, client.get(f"/replays/api/recordings/{latest}").status_code)
            finally:
                service._raw_recorder.close_all()


class RecordingQueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_offline_job_survives_restart_and_keeps_local_copy(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            session_id = str(uuid4())
            recorder = RawFtcLogRecorder(root)
            path = record_shooter_run(recorder, session_id)
            service = RobotDataService()
            service._raw_recorder = recorder
            failed = asyncio.Event()
            loop = asyncio.get_running_loop()

            class OfflineUploader:
                def upload(self, *args, **kwargs):
                    loop.call_soon_threadsafe(failed.set)
                    raise RecordingUploadError("[WinError 10051] A socket operation was attempted to an unreachable network")

            service._recording_uploader = OfflineUploader()
            service._AUTO_UPLOAD_RETRY_DELAYS_SECONDS = ()
            try:
                self.assertEqual({"queued": 1}, service.queue_uploads(service.saved_recording_sessions()))
                self.assertEqual({"queued": 0}, service.queue_uploads(service.saved_recording_sessions()))
                await asyncio.wait_for(failed.wait(), 2)
            finally:
                await service.close()
            self.assertTrue(path.is_file())

            restarted = RobotDataService()
            restarted._raw_recorder = RawFtcLogRecorder(root)
            restarted._server.start = AsyncMock()
            calls = []

            class OnlineUploader:
                def upload(self, directory, **kwargs):
                    calls.append(directory.name)
                    return UploadResult(directory.name, 1, 0, path.stat().st_size)

            restarted._recording_uploader = OnlineUploader()
            try:
                await restarted.start()
                await asyncio.wait_for(restarted._queue_task, 2)
                self.assertEqual([session_id], calls)
                self.assertTrue(path.is_file())
                self.assertEqual("uploaded", restarted.saved_recording_sessions()[0]["upload"]["state"])
                self.assertEqual({"queued": 0}, restarted.queue_uploads(restarted.saved_recording_sessions()))
            finally:
                await restarted.close()
