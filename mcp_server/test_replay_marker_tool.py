from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from recording_viewer.backend.replay_markers import ReplayMarkerStore

from .replay_marker_tool import MARKER_SCOPE, register_replay_marker_tool


class ReplayMarkerToolTests(IsolatedAsyncioTestCase):
    async def test_scope_and_finalized_run_are_checked_before_saving(self):
        with TemporaryDirectory() as directory:
            run_id = str(uuid4())
            store = ReplayMarkerStore(Path(directory))
            archive_api = AsyncMock()
            archive_api.get.return_value = {"result": {"samples": 100, "recorded_span_s": 10}}
            server = FastMCP("Replay")
            server.settings.auth = AuthSettings(
                issuer_url="https://oracle.test",
                resource_server_url="https://oracle.test/mcp",
            )
            register_replay_marker_tool(server, lambda **_: archive_api, store, "https://example.test", ["replays:read"])
            tool, = await server.list_tools()
            self.assertFalse(tool.annotations.readOnlyHint)
            self.assertTrue(tool.annotations.idempotentHint)
            args = {"run": run_id, "events": [
                {"t_s": 3.2, "end_s": 3.45, "kind": "voltage_sag", "title": "Voltage sag"},
            ]}
            with patch("mcp_server.replay_marker_tool.get_access_token", return_value=SimpleNamespace(scopes=["replays:read"])):
                denied = await server.call_tool("mark_replay_events", args)
            self.assertTrue(denied.isError)
            challenge, = denied.meta["mcp/www_authenticate"]
            self.assertIn('error="insufficient_scope"', challenge)
            self.assertIn('scope="replays:read replays:mark"', challenge)
            self.assertIn('resource_metadata="https://oracle.test/.well-known/oauth-protected-resource/mcp"', challenge)
            archive_api.get.assert_not_called()
            self.assertEqual([], store.list_for_run(run_id))
            with patch("mcp_server.replay_marker_tool.get_access_token", return_value=SimpleNamespace(scopes=["replays:read", MARKER_SCOPE])):
                saved = await server.call_tool("mark_replay_events", args)
                retried = await server.call_tool("mark_replay_events", args)
            self.assertEqual(1, saved.structuredContent["added_count"])
            self.assertEqual(3.45, saved.structuredContent["markers"][0]["end_s"])
            self.assertEqual(0, retried.structuredContent["added_count"])
            archive_api.get.assert_awaited_with(f"/api/reviews/{run_id}/report?section=quality")
            marker, = store.list_for_run(run_id)
            self.assertEqual(3.2, marker.t_s)
            self.assertEqual(3.45, marker.end_s)
