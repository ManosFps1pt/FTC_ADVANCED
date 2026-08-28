from __future__ import annotations

import unittest
import uuid

from .debug_commands import build_debug_command_request, command_request_id


class DebugCommandRequestTests(unittest.TestCase):
    def test_browser_request_id_is_preserved_in_the_robot_command(self) -> None:
        request_id = command_request_id("browser-command-42")
        request = build_debug_command_request(
            request_id=request_id,
            node_id="telemetry.gamepad",
            tool_instance_id="tool-1",
            command_id="alliance.set",
            ttl_ms=1_000,
            arguments=[],
        )

        self.assertEqual("browser-command-42", request_id)
        self.assertEqual("browser-command-42", request.request_id)

    def test_command_request_id_is_generated_for_existing_api_callers(self) -> None:
        generated_id = command_request_id(None)

        self.assertEqual(4, uuid.UUID(generated_id).version)


if __name__ == "__main__":
    unittest.main()
