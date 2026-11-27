from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
import uuid
from contextlib import closing
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

from web_driver_station.backend.ftclog import RawFtcLogRecorder
from web_driver_station.backend.incidents import IncidentRecorder
from web_driver_station.backend.protocol import robot_data_pb2 as wire
from web_driver_station.backend.recording_metadata import RECORDING_METADATA_FILE

from .main import Recording, RecordingLibrary, create_library_app
from .replay_markers import ReplayMarkerInput, ReplayMarkerStore


class RecordingReaderTests(unittest.TestCase):
    def test_range_markers_preserve_existing_point_markers_and_reject_invalid_ranges(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session_id = str(uuid.uuid4())
            (root / session_id).mkdir()
            store = ReplayMarkerStore(root)
            store.path.parent.mkdir()
            legacy_json = '{"t_s":1.5,"kind":"other","title":"Point","note":""}'
            legacy_id = sha256(legacy_json.encode()).hexdigest()[:24]
            point = ReplayMarkerInput.model_validate_json(legacy_json)
            with closing(sqlite3.connect(store.path)) as connection, connection:
                connection.execute("""
                    CREATE TABLE markers (
                        session_id TEXT NOT NULL, id TEXT NOT NULL, t_s REAL NOT NULL,
                        kind TEXT NOT NULL, title TEXT NOT NULL, note TEXT NOT NULL,
                        PRIMARY KEY (session_id, id)
                    )
                """)
                connection.execute("INSERT INTO markers VALUES (?, ?, ?, ?, ?, ?)",
                                   (session_id, legacy_id, point.t_s, point.kind, point.title, point.note))
            existing, = store.list_for_run(session_id)
            self.assertEqual(legacy_id, existing.id)
            self.assertIsNone(existing.end_s)

            with self.assertRaises(ValidationError):
                ReplayMarkerInput(t_s=3, end_s=3, kind="other", title="Empty range")
            interval = ReplayMarkerInput(t_s=3, end_s=3.15, kind="intake_load", title="Intake stall")
            with self.assertRaisesRegex(ValueError, "within the recording"):
                store.add(session_id, [interval], 3.1)
            records, added = store.add(session_id, [point, interval], 4)
            self.assertEqual(1, added)
            self.assertEqual(legacy_id, records[0].id)
            self.assertEqual(0, store.add(session_id, [interval], 4)[1])
            with TestClient(create_library_app(root)) as client:
                markers = client.get(f"/api/recordings/{session_id}/markers").raise_for_status().json()
            self.assertEqual([None, 3.15], [marker["end_s"] for marker in markers])

    def test_replay_markers_are_ordered_retry_safe_and_separate_from_the_recording(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session_id = str(uuid.uuid4())
            (root / session_id).mkdir()
            url = f"/api/recordings/{session_id}/markers"
            store = ReplayMarkerStore(root)
            later = ReplayMarkerInput(t_s=9.5, kind="shooter_dip", title="RPM dip")
            earlier = ReplayMarkerInput(t_s=3.2, kind="voltage_sag", title="Bus voltage sag", note="12.1 V to 10.8 V")
            with TestClient(create_library_app(root)) as client:
                self.assertEqual([], client.get(url).raise_for_status().json())
                self.assertFalse(store.path.exists())
                with self.assertRaisesRegex(ValueError, "within the recording"):
                    store.add(session_id, [earlier, later], 9.4)
                self.assertEqual([], client.get(url).json())
                _, created = store.add(session_id, [later, earlier, earlier], 10)
                self.assertEqual(2, created)
                _, created = store.add(session_id, [earlier, later], 10)
                self.assertEqual(0, created)
                found = client.get(url).raise_for_status().json()
                self.assertEqual([3.2, 9.5], [marker["t_s"] for marker in found])
                self.assertNotEqual(found[0]["color"], found[1]["color"])
                self.assertEqual("12.1 V to 10.8 V", found[0]["note"])
                self.assertEqual([], list((root / session_id).iterdir()))
                self.assertEqual(404, client.get(f"/api/recordings/{uuid.uuid4()}/markers").status_code)

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

    def test_replay_response_preserves_telemetry_and_video_ranges(self) -> None:
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

            url = f"/api/recordings/{session_text}"
            session_directory = recordings_root / session_text
            with TestClient(create_library_app(recordings_root)) as client:
                response = client.get(url, headers={"Accept-Encoding": "gzip"}).raise_for_status()
                self.assertEqual("gzip", response.headers["Content-Encoding"])
                self.assertIn("Accept-Encoding", [field.strip() for field in response.headers["Vary"].split(",")])
                self.assertEqual(
                    {"x": 24.5, "y": 111.25, "headingRad": 1.57079632679},
                    response.json()["snapshots"][0]["values"]["localization.pose"],
                )
                with patch.object(Recording, "_load", side_effect=AssertionError("Replay was already prepared")):
                    identity = client.get(url, headers={"Accept-Encoding": "gzip;q=0, *;q=1"}).raise_for_status()
                    self.assertNotIn("Content-Encoding", identity.headers)
                    self.assertEqual(response.json(), identity.json())
                    unchanged = client.get(url, headers={"If-None-Match": response.headers["ETag"]})
                    self.assertEqual(304, unchanged.status_code)
                    self.assertEqual(b"", unchanged.content)

                (session_directory / RECORDING_METADATA_FILE).write_text('{"title":"Updated title"}', encoding="utf-8")
                updated = client.get(url, headers={"If-None-Match": response.headers["ETag"]}).raise_for_status()
                self.assertEqual("Updated title", updated.json()["metadata"]["title"])
                self.assertNotEqual(response.headers["ETag"], updated.headers["ETag"])

                clip = session_directory / "field.mp4"
                clip.write_bytes(b"0123456789")
                with_video = client.get(url, headers={"If-None-Match": updated.headers["ETag"]}).raise_for_status()
                video_url = with_video.json()["videoClips"][0]["url"]
                with patch.object(Recording, "_load", side_effect=AssertionError("Media requests should skip telemetry")):
                    for start, end in ((0, 3), (7, 9)):
                        partial = client.get(video_url, headers={"Range": f"bytes={start}-{end}"})
                        self.assertEqual(206, partial.status_code)
                        self.assertEqual(f"bytes {start}-{end}/10", partial.headers["Content-Range"])
                        self.assertEqual(b"0123456789"[start:end + 1], partial.content)


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
