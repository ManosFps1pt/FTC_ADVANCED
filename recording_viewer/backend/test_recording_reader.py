from __future__ import annotations

import tempfile
import unittest
import uuid
from pathlib import Path

from web_driver_station.backend.ftclog import RawFtcLogRecorder
from web_driver_station.backend.protocol import robot_data_pb2 as wire

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
            (root / complete_id / "raw").mkdir(parents=True)
            (root / complete_id / "raw" / "stream-00000.ftclog").write_bytes(b"log")
            (root / complete_id / "video").mkdir()
            (root / complete_id / "video" / "field.mp4").write_bytes(b"video")
            (root / incomplete_id / "raw").mkdir(parents=True)
            (root / incomplete_id / "raw" / "stream-00000.ftclog.partial").write_bytes(b"partial")
            (root / ".incoming" / str(uuid.uuid4()) / "raw").mkdir(parents=True)

            recordings = RecordingLibrary(root).list()

            self.assertEqual([complete_id], [item["id"] for item in recordings])
            self.assertEqual(1, recordings[0]["videoFileCount"])


if __name__ == "__main__":
    unittest.main()
