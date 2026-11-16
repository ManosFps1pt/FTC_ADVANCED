from __future__ import annotations

import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path

from web_driver_station.backend.ftclog import RawFtcLogRecorder
from web_driver_station.backend.incidents import IncidentRecorder
from web_driver_station.backend.protocol import robot_data_pb2 as wire
from web_driver_station.backend.recording_metadata import RECORDING_METADATA_FILE

from .main import Recording, RecordingLibrary


class RecordingReaderTests(unittest.TestCase):
    def test_reads_the_full_catalog_snapshots_and_gamepad_history(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            recordings_root = Path(temporary_directory)
            recorder = RawFtcLogRecorder(recordings_root)
            session_id, connection_id = uuid.uuid4().bytes, uuid.uuid4().bytes

            def envelope(**body: object) -> bytes:
                return wire.Envelope(
                    protocol_version=2,
                    session_id=session_id,
                    connection_id=connection_id,
                    connection_sequence=1,
                    robot_elapsed_ns=123_000_000,
                    **body,
                ).SerializeToString()

            session_text = str(uuid.UUID(bytes=session_id))
            recorder.append(session_text, envelope(hello=wire.Hello(robot_id="robot", robot_name="Replay Bot", op_mode_name="Test")), 1)
            recorder.append(session_text, envelope(schema=wire.Schema(revision=1, channels=[
                wire.Channel(channel_id=1, key="drive.speed", label="Speed", quantity="speed", unit="m/s", value_type=wire.FLOAT64, role=wire.MEASURED),
            ])), 2)
            recorder.append(session_text, envelope(sample_batch=wire.SampleBatch(snapshots=[
                wire.Snapshot(sample_sequence=7, schema_revision=1, values=[wire.ChannelValue(channel_id=1, float64_value=3.5)]),
            ])), 3)
            recorder.append(session_text, envelope(gamepad=wire.GamepadSnapshot(gamepad1=wire.Gamepad(a=True), gamepad2=wire.Gamepad(b=True))), 4)
            recorder.close_session(session_text)
            video_path = recordings_root / session_text / "video" / "field.mp4"
            video_path.parent.mkdir()
            video_path.write_bytes(b"placeholder-video")

            recording = Recording(recordings_root / session_text)

            self.assertEqual("Replay Bot", recording.session["robotName"])
            self.assertEqual("drive.speed", recording.catalog["signals"][0]["id"])
            self.assertEqual(3.5, recording.snapshots[0]["values"]["drive.speed"])
            self.assertTrue(recording.gamepad_frames[0]["gamepad1"]["a"])
            self.assertTrue(recording.gamepad_frames[0]["gamepad2"]["b"])
            self.assertTrue(video_path.samefile(recording.video_path(0)))
            self.assertEqual("video/field.mp4", recording.summary()["videoClips"][0]["name"])

    def test_library_lists_only_complete_uuid_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            complete_id = str(uuid.uuid4())
            incomplete_id = str(uuid.uuid4())
            recorder = RawFtcLogRecorder(root)
            hello = wire.Envelope(
                protocol_version=2, session_id=uuid.UUID(complete_id).bytes,
                connection_id=uuid.uuid4().bytes, connection_sequence=1,
                hello=wire.Hello(robot_id="robot", robot_name="Replay Bot", op_mode_name="Test"),
            )
            recorder.append(complete_id, hello.SerializeToString(), 1)
            recorder.close_session(complete_id)
            (root / complete_id / "video").mkdir()
            (root / complete_id / "video" / "field.mp4").write_bytes(b"video")
            (root / incomplete_id / "raw").mkdir(parents=True)
            (root / incomplete_id / "raw" / "stream-00000.ftclog.partial").write_bytes(b"partial")
            (root / ".incoming" / str(uuid.uuid4()) / "raw").mkdir(parents=True)

            recordings = RecordingLibrary(root).list()

            self.assertEqual([complete_id], [item["id"] for item in recordings])
            self.assertEqual(1, recordings[0]["videoFileCount"])
            self.assertIsNone(recordings[0]["metadata"])

            for name in ("field.sync.json", "field.synced.mp4", ".video-sync-test.partial.mp4"):
                artifact = root / complete_id / "video" / name
                artifact.write_bytes(b"generated")
                later = int(recordings[0]["modifiedAtNs"]) + 5_000_000_000
                os.utime(artifact, ns=(later, later))
            self.assertEqual(recordings, RecordingLibrary(root).list())

            metadata_path = root / complete_id / RECORDING_METADATA_FILE
            metadata = {"title": "Αυτόνομη δοκιμή", "description": "First run\n\nTurned early."}
            metadata_path.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(metadata, RecordingLibrary(root).list()[0]["metadata"])
            self.assertEqual(metadata, Recording(root / complete_id).browser_model()["metadata"])

            metadata_path.write_text('{"title": 7}', encoding="utf-8")
            self.assertIsNone(RecordingLibrary(root).list()[0]["metadata"])
            self.assertIsNone(Recording(root / complete_id).browser_model()["metadata"])

    def test_reads_incidents_and_telemetry_lab_command_confirmations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            recordings_root = Path(temporary_directory)
            recorder = RawFtcLogRecorder(recordings_root)
            session_id, connection_id = uuid.uuid4().bytes, uuid.uuid4().bytes
            session_text = str(uuid.UUID(bytes=session_id))

            def envelope(**body: object) -> bytes:
                return wire.Envelope(
                    protocol_version=2, session_id=session_id, connection_id=connection_id,
                    connection_sequence=1, robot_elapsed_ns=123_000_000, **body,
                ).SerializeToString()

            recorder.append(session_text, envelope(hello=wire.Hello(robot_id="robot", robot_name="Replay Bot", op_mode_name="Test")), 1)
            recorder.append(session_text, envelope(schema=wire.Schema(revision=1, channels=[
                wire.Channel(channel_id=1, key="drive.speed", label="Speed", quantity="speed", unit="m/s", value_type=wire.FLOAT64, role=wire.MEASURED),
            ])), 2)
            recorder.append(session_text, envelope(sample_batch=wire.SampleBatch(snapshots=[
                wire.Snapshot(sample_sequence=7, schema_revision=1, highlighted=False, values=[wire.ChannelValue(channel_id=1, float64_value=3.5)]),
            ])), 3)
            recorder.append(session_text, envelope(debug_command_response=wire.DebugCommandResponse(
                request_id="run-1", command_id="motor.stop", result=wire.DEBUG_COMMAND_COMPLETED,
                message="Output stopped", handled_at_robot_time_ns=123_000_000,
            )), 4)
            recorder.close_session(session_text)

            incidents = IncidentRecorder(recordings_root)
            incidents.observe_snapshot({
                "sessionId": session_text, "sampleSequence": "7", "robotTimeNs": "123000000",
                "highlighted": True, "highlightSource": "telemetry_lab",
            })
            incidents.close_session(session_text)

            recording = Recording(recordings_root / session_text)
            self.assertEqual(0, recording.invalid_frames)
            self.assertTrue(recording.snapshots[0]["highlighted"])
            self.assertEqual("telemetry_lab", recording.snapshots[0]["highlightSource"])
            self.assertEqual("debug_command_response", recording.debug_messages[0]["type"])
            self.assertEqual("motor.stop", recording.debug_messages[0]["commandId"])

    def test_library_api_serializes_recorded_pose2d(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            recordings_root = Path(temporary_directory)
            recorder = RawFtcLogRecorder(recordings_root)
            session_id, connection_id = uuid.uuid4().bytes, uuid.uuid4().bytes
            session_text = str(uuid.UUID(bytes=session_id))

            def envelope(**body: object) -> bytes:
                return wire.Envelope(
                    protocol_version=2, session_id=session_id, connection_id=connection_id,
                    connection_sequence=1, robot_elapsed_ns=123_000_000, **body,
                ).SerializeToString()

            recorder.append(session_text, envelope(hello=wire.Hello(robot_id="robot", robot_name="Replay Bot", op_mode_name="Localization")), 1)
            recorder.append(session_text, envelope(schema=wire.Schema(revision=1, channels=[
                wire.Channel(channel_id=1, key="localization.pose", label="Pose", quantity="pose", unit="in,rad", value_type=wire.POSE2D, role=wire.MEASURED),
            ])), 2)
            recorder.append(session_text, envelope(sample_batch=wire.SampleBatch(snapshots=[
                wire.Snapshot(sample_sequence=7, schema_revision=1, values=[
                    wire.ChannelValue(channel_id=1, pose2d_value=wire.Pose2d(x=24.5, y=111.25, heading_rad=1.57079632679)),
                ]),
            ])), 3)
            recorder.close_session(session_text)

            # FastAPI serializes this same browser model for the library API.
            # ``allow_nan=False`` also ensures a malformed pose cannot turn
            # into an invalid JSON response at replay time.
            response = json.loads(json.dumps(Recording(recordings_root / session_text).browser_model(), allow_nan=False))

            self.assertEqual(
                {"x": 24.5, "y": 111.25, "headingRad": 1.57079632679},
                response["snapshots"][0]["values"]["localization.pose"],
            )


    def test_v3_shooter_batch_keeps_10ms_timestamps_and_interrupted_segment(self) -> None:
        from web_driver_station.backend.recording_uploader import _validated_session
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            recorder = RawFtcLogRecorder(root)
            session, connection = uuid.uuid4().bytes, uuid.uuid4().bytes
            session_text = str(uuid.UUID(bytes=session))
            def frame(**body):
                return wire.Envelope(protocol_version=3, session_id=session, connection_id=connection,
                                     robot_elapsed_ns=1_010_000_000, **body).SerializeToString()
            recorder.append(session_text, frame(hello=wire.Hello(robot_id="test", robot_name="Shooter test", op_mode_name="Main TeleOp")), 1)
            recorder.append(session_text, frame(schema=wire.Schema(revision=1, channels=[
                wire.Channel(channel_id=1, key="shooter.measuredRpm", label="Shooter measured RPM",
                             quantity="velocity", unit="RPM", value_type=wire.FLOAT64, role=wire.MEASURED),
            ])), 2)
            recorder.append(session_text, frame(sample_batch=wire.SampleBatch(snapshots=[
                wire.Snapshot(sample_sequence=1, schema_revision=1, robot_time_ns=1_000_000_000,
                              values=[wire.ChannelValue(channel_id=1, float64_value=3000)]),
                wire.Snapshot(sample_sequence=2, schema_revision=1, robot_time_ns=1_010_000_000,
                              values=[wire.ChannelValue(channel_id=1, float64_value=2700)]),
            ])), 3)
            recorder.close_session(session_text, complete=False)
            _validated_session(root / session_text)
            self.assertTrue(RecordingLibrary(root).list()[0]["interrupted"])
            recording = Recording(root / session_text)
            self.assertEqual(0, recording.invalid_frames)
            self.assertTrue(recording.summary()["interrupted"])
            self.assertEqual(["1000000000", "1010000000"], [s["robotTimeNs"] for s in recording.snapshots])
            self.assertEqual([3000, 2700], [s["values"]["shooter.measuredRpm"] for s in recording.snapshots])


if __name__ == "__main__":
    unittest.main()
