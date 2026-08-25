from __future__ import annotations

import asyncio
import struct
import unittest
import uuid

from .protocol import robot_data_pb2 as wire
from .robot_data_tcp_server import RobotDataTcpServer


class RobotDataProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.packets = []

        async def receive(packet):
            self.packets.append(packet)
            return None

        self.server = RobotDataTcpServer("127.0.0.1", 0, discovery_port=5812, packet_handler=receive)
        await self.server.start()
        self.port = self.server.listening_addresses[0][1]
        self.session_id, self.connection_id = uuid.uuid4().bytes, uuid.uuid4().bytes

    async def asyncTearDown(self) -> None:
        await self.server.close()

    async def _send(self, writer: asyncio.StreamWriter, envelope: wire.Envelope) -> None:
        encoded = envelope.SerializeToString()
        writer.write(struct.pack("!I", len(encoded)) + encoded)
        await writer.drain()

    def _envelope(self, **body) -> wire.Envelope:
        return wire.Envelope(protocol_version=2, session_id=self.session_id, connection_id=self.connection_id,
                             connection_sequence=len(self.packets), robot_elapsed_ns=123, **body)

    async def test_schema_and_batched_numeric_samples_are_normalized(self) -> None:
        _, writer = await asyncio.open_connection("127.0.0.1", self.port)
        await self._send(writer, self._envelope(hello=wire.Hello(robot_id="robot", robot_name="Robot", op_mode_name="Test")))
        await self._send(writer, self._envelope(schema=wire.Schema(
            revision=1,
            devices=[wire.Device(device_id=1, key="drive.left", label="Left", subsystem="drive", device_type="dcMotor")],
            channels=[wire.Channel(channel_id=1, key="drive.left.currentA", label="Current", device_id=1, quantity="current", unit="A", value_type=wire.FLOAT64, role=wire.MEASURED)],
        )))
        await self._send(writer, self._envelope(sample_batch=wire.SampleBatch(snapshots=[
            wire.Snapshot(sample_sequence=7, schema_revision=1, values=[wire.ChannelValue(channel_id=1, float64_value=3.25)]),
            wire.Snapshot(sample_sequence=8, schema_revision=1, values=[wire.ChannelValue(channel_id=1, unavailable=True)]),
        ])))
        await asyncio.sleep(0.05)
        writer.close(); await writer.wait_closed()

        self.assertEqual(3, len(self.packets))
        self.assertEqual("catalog", self.packets[1].payloads[0]["type"])
        samples = self.packets[2].payloads
        self.assertEqual(["7", "8"], [item["data"]["sampleSequence"] for item in samples])
        self.assertEqual(3.25, samples[0]["data"]["values"]["drive.left.currentA"])
        self.assertIsNone(samples[1]["data"]["values"]["drive.left.currentA"])

    async def test_int64_is_preserved_as_a_decimal_string_for_the_browser_model(self) -> None:
        _, writer = await asyncio.open_connection("127.0.0.1", self.port)
        await self._send(writer, self._envelope(hello=wire.Hello(robot_id="robot", robot_name="Robot", op_mode_name="Test")))
        await self._send(writer, self._envelope(schema=wire.Schema(revision=1, channels=[
            wire.Channel(channel_id=1, key="arm.ticks", label="Ticks", quantity="position", unit="tick", value_type=wire.INT64, role=wire.MEASURED),
        ])))
        await self._send(writer, self._envelope(sample_batch=wire.SampleBatch(snapshots=[
            wire.Snapshot(sample_sequence=1, schema_revision=1, values=[wire.ChannelValue(channel_id=1, int64_value=9_007_199_254_740_993)]),
        ])))
        await asyncio.sleep(0.05)
        writer.close(); await writer.wait_closed()
        self.assertEqual("9007199254740993", self.packets[-1].payload["data"]["values"]["arm.ticks"])


if __name__ == "__main__":
    unittest.main()
