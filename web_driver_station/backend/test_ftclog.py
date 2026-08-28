from __future__ import annotations

import tempfile
import unittest
import uuid
from pathlib import Path

from .ftclog import RawFtcLogRecorder, iter_records


class FtcLogTests(unittest.TestCase):
    def test_exact_frames_are_durable_and_finalized_on_session_close(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            recorder = RawFtcLogRecorder(Path(temporary_directory))
            session_id = str(uuid.uuid4())

            partial_path = recorder.append(session_id, b"first-protobuf-frame", 101)
            recorder.append(session_id, b"second-protobuf-frame", 202)

            self.assertTrue(partial_path.name.endswith(".ftclog.partial"))
            self.assertEqual(
                [(101, b"first-protobuf-frame"), (202, b"second-protobuf-frame")],
                [(record.received_monotonic_ns, record.protobuf_frame) for record in iter_records(partial_path)],
            )
            final_path = recorder.close_session(session_id)

            self.assertIsNotNone(final_path)
            assert final_path is not None
            self.assertTrue(final_path.is_file())
            self.assertFalse(partial_path.exists())
            self.assertEqual(["stream-00000.ftclog"], recorder.list_sessions()[0]["logs"])
            self.assertEqual(final_path, recorder.log_path(session_id, final_path.name))

    def test_reader_ignores_incomplete_crash_tail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            recorder = RawFtcLogRecorder(Path(temporary_directory))
            session_id = str(uuid.uuid4())
            partial_path = recorder.append(session_id, b"complete", 5)
            with partial_path.open("ab") as handle:
                handle.write(b"incomplete tail")

            self.assertEqual([b"complete"], [record.protobuf_frame for record in iter_records(partial_path)])
            recorder.close_all()


if __name__ == "__main__":
    unittest.main()
