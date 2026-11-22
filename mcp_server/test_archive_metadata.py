import json
import os
import tempfile
import unittest
from importlib.util import find_spec
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

@unittest.skipUnless(all(find_spec(package) for package in ("fastapi", "cv2", "numpy", "google")),
                     "The archive and viewer dependencies are required")
class ArchiveRecordingMetadataTests(unittest.IsolatedAsyncioTestCase):
    async def test_archive_lists_both_recording_kinds_without_decoding_capture(self):
        from fastapi.testclient import TestClient

        from recording_viewer.backend.main import create_library_app
        from web_driver_station.backend.recording_metadata import RECORDING_METADATA_FILE

        from .archive import create_archive_server

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "archive"
            replay_id, legacy_id, benchmark_id, active_id = (str(UUID(int=value)) for value in range(1, 5))
            replay_details = {"battery_index": 0, "robot_version": 1, "opmode_type": "teleop"}
            benchmark_details = {"battery_index": 8, "robot_version": 2, "opmode_type": "debugging"}
            for run_id, timestamp in ((replay_id, 1_700_000_000), (legacy_id, 1_600_000_000)):
                log = root / run_id / "raw" / "stream-00000.ftclog"
                log.parent.mkdir(parents=True)
                log.write_bytes(b"metadata lookup must never parse this")
                os.utime(log, (timestamp, timestamp))
            (root / replay_id / RECORDING_METADATA_FILE).write_text(
                json.dumps({"title": "Drive practice", **replay_details}), encoding="utf-8",
            )
            for run_id, state in ((benchmark_id, "ready"), (active_id, "receiving")):
                path = root / run_id
                path.mkdir()
                (path / "manifest.json").write_text(json.dumps({
                    "kind": "debugger", "state": state, "runId": run_id, "createdAt": "2025-01-01T00:00:00Z",
                    "mechanismLabel": "Shooter", "benchmark": {"label": "Friction"},
                }), encoding="utf-8")
                (path / RECORDING_METADATA_FILE).write_text(json.dumps(benchmark_details), encoding="utf-8")

            server = create_archive_server(root, root.parent / "cache", "https://replay.example/")

            async def list_metadata(**arguments):
                result = await server.call_tool("list_recording_metadata", arguments)
                if isinstance(result, tuple):
                    return result[1]
                if isinstance(result, dict):
                    return result
                return json.loads(result[0].text)

            with patch("web_driver_station.backend.saved_runs.iter_records", side_effect=AssertionError("Decoded a log")):
                first = await list_metadata(limit=1)
                self.assertEqual(3, first["total"])
                self.assertEqual(1, first["next_offset"])
                benchmark, = first["runs"]
                self.assertEqual(benchmark_id, benchmark["run_id"])
                self.assertEqual("Shooter · Friction", benchmark["title"])
                self.assertEqual(benchmark_details, {key: benchmark[key] for key in benchmark_details})
                second = await list_metadata(kind="replay", offset=1, limit=1)
                legacy, = second["runs"]
                self.assertEqual(legacy_id, legacy["run_id"])
                self.assertTrue(all(legacy[key] is None for key in replay_details))
                replay, = (await list_metadata(run_id=replay_id))["runs"]
                self.assertEqual(replay_details, {key: replay[key] for key in replay_details})

            with TestClient(create_library_app(root)) as viewer:
                items = viewer.get("/api/recordings").raise_for_status().json()
                self.assertEqual(replay_details, {key: items[0]["metadata"][key] for key in replay_details})
                report = viewer.get(f"/api/debug/results/{benchmark_id}").raise_for_status().json()
                self.assertEqual(benchmark_details, report["manifest"]["metadata"])
