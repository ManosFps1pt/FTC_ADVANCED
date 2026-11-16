from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from time import monotonic, sleep
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from . import video_sync


class VideoSyncTests(unittest.TestCase):
    def test_overlap_keeps_snapshot_clock_for_both_offset_signs(self):
        self.assertEqual((0, 8), video_sync.overlap(8, 14, 2))
        self.assertEqual((0, 4), video_sync.overlap(8, 6, 2))
        self.assertEqual((2, 8), video_sync.overlap(10, 6, -2))
        with self.assertRaisesRegex(ValueError, "No video overlaps"):
            video_sync.overlap(8, 6, 7)

    def test_sync_status_persists_and_exports_replace_only_on_success(self):
        with TemporaryDirectory() as directory:
            session = Path(directory).resolve()
            clip = session / "field.mp4"
            clip.write_bytes(b"original MP4")
            raw = session / "stream.ftclog"
            raw.write_bytes(b"raw snapshots")
            recording = SimpleNamespace(snapshots=[{"robotTimeNs": "100"}, {"robotTimeNs": "8000000100"}])
            url = "/session/video/0"

            def client():
                app = FastAPI()
                app.include_router(video_sync.video_job_routes(lambda _: session, lambda _: recording))
                return TestClient(app)

            def finished(api):
                deadline = monotonic() + 3
                while monotonic() < deadline:
                    status = api.get(url + "/status").json()
                    if not status["running"]:
                        return status
                    sleep(.01)
                self.fail("Video job did not finish")

            def media(command, timeout):
                if command[0] == "ffprobe":
                    return "6.0"
                self.assertEqual("2.000000000", command[command.index("-ss") + 1])
                self.assertEqual("4.000000000", command[command.index("-t") + 1])
                self.assertIn("0:a:0?", command)
                Path(command[-1]).write_bytes(next(exports))

            exports = iter((b"first export", b"second export"))
            with client() as api:
                self.assertEqual(409, api.post(url + "/export", json={}).status_code)
                self.assertEqual(404, api.get("/session/video/-1/status").status_code)
                active, release = Event(), Event()

                def calibrate(path, _):
                    active.set()
                    release.wait(2)
                    video_sync._save_sync(path, {"video_delay_seconds": 2, "matched_edges": 8})

                with patch.object(video_sync, "_calibrate", side_effect=calibrate) as sync:
                    self.assertEqual(202, api.post(url + "/sync", json={}).status_code)
                    self.assertTrue(active.wait(2))
                    self.assertEqual(202, api.post(url + "/sync", json={}).status_code)
                    release.set()
                    self.assertEqual(2, finished(api)["sync"]["video_delay_seconds"])
                    sync.assert_called_once()

            with client() as api:
                self.assertEqual(2, api.get(url + "/status").json()["sync"]["video_delay_seconds"])
                with patch.object(video_sync, "_calibrate", side_effect=ValueError("Several delays match the flashes")):
                    api.post(url + "/sync", json={})
                    status = finished(api)
                    self.assertIn("Several delays", status["error"])
                    self.assertEqual(2, status["sync"]["video_delay_seconds"])

                with patch.object(video_sync, "_run_media_tool", side_effect=media):
                    for expected in (b"first export", b"second export"):
                        self.assertEqual(202, api.post(url + "/export", json={}).status_code)
                        status = finished(api)
                        self.assertIsNone(status["error"])
                        self.assertEqual({"telemetry_start_seconds": 0, "telemetry_end_seconds": 4}, status["export"])
                        self.assertEqual(expected, api.get(url + "/download").content)
                with patch.object(video_sync, "_run_media_tool", side_effect=ValueError("Encoder failed")):
                    api.post(url + "/export", json={})
                    self.assertEqual("Encoder failed", finished(api)["error"])
                    self.assertEqual(b"second export", api.get(url + "/download").content)

                stored = video_sync._read_sync(clip)
                video_sync._save_sync(clip, {**stored, "video_delay_seconds": -1})
                self.assertIsNone(api.get(url + "/status").json()["export"])
                self.assertEqual(404, api.get(url + "/download").status_code)
                self.assertEqual(404, api.get("/session/video/1/status").status_code)

            self.assertEqual([clip], video_sync.original_videos(session))
            self.assertEqual(["field.synced.mp4"], [path.name for path in session.glob("*.synced.mp4")])
            self.assertEqual(b"second export", (session / "field.synced.mp4").read_bytes())
            self.assertEqual(b"original MP4", clip.read_bytes())
            self.assertEqual(b"raw snapshots", raw.read_bytes())
