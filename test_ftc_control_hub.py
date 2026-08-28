"""Focused protocol tests for the desktop Driver Station client."""

from __future__ import annotations

import unittest

from ftc_control_hub import Command, ControlHubClient, ControlHubConfig, Packet, RobocolMessageType, decode_opmode_exception


class OpModeExceptionProtocolTests(unittest.TestCase):
    def test_decodes_sdk_stacktrace_command(self) -> None:
        command = Command(
            name="CMD_SHOW_STACKTRACE",
            extra=(
                "java.lang.IllegalArgumentException: bad Limelight value\n"
                "\tat org.example.TeleOp.runOpMode(TeleOp.java:42)\n"
            ).encode("utf-8"),
            timestamp_ns=123_456,
            acknowledged=False,
            sequence=17,
        )

        exception = decode_opmode_exception(command)

        self.assertIsNotNone(exception)
        assert exception is not None
        self.assertEqual(exception.timestamp_ns, 123_456)
        self.assertEqual(exception.sequence, 17)
        self.assertIn("TeleOp.java:42", exception.stacktrace)

    def test_notifies_listener_after_acknowledging_stacktrace(self) -> None:
        client = ControlHubClient(ControlHubConfig(host="192.168.49.1"))
        received = []
        client.add_opmode_exception_listener(received.append)
        command = Command(
            name="CMD_SHOW_STACKTRACE",
            extra=b"java.lang.RuntimeException: test failure\n",
            timestamp_ns=999,
            acknowledged=False,
            sequence=4,
        )

        wire = client._encode_command(command, sequence=4)
        packet = Packet(
            message_type=RobocolMessageType.COMMAND,
            sequence=4,
            payload=wire[5:],
            received_monotonic_ns=1,
        )
        client._handle_command(packet)

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].stacktrace, "java.lang.RuntimeException: test failure\n")


if __name__ == "__main__":
    unittest.main()
