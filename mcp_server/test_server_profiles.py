import unittest

from .server import create_replay_server, mcp


class ServerProfileTests(unittest.IsolatedAsyncioTestCase):
    async def test_replay_profile_exposes_status_without_robot_control(self):
        status_tools = {"get_driver_station_status", "get_robot_data_status", "get_telemetry_status"}
        full = {tool.name for tool in await mcp.list_tools()}
        replay = {tool.name: tool for tool in await create_replay_server().list_tools()}
        self.assertTrue(status_tools <= full)
        self.assertIn("list_recording_metadata", full)
        self.assertTrue(status_tools | {"list_replays", "list_recording_metadata", "find_shooter_dips"} <= replay.keys())
        self.assertTrue(all(tool.annotations.readOnlyHint for tool in replay.values()))
        self.assertNotIn("launch_opmode", replay)
        self.assertNotIn("stop_opmode", replay)
