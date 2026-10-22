from __future__ import annotations

import asyncio
import struct
import tempfile
import unittest
import uuid
from pathlib import Path

from .ftclog import iter_records
from .protocol import robot_data_pb2 as wire
from .record_robot_data import LaptopRobotRecorder
from .robot_data_tcp_server import RobotDataTcpServer


class LaptopRobotRecorderTests(unittest.IsolatedAsyncioTestCase):
    async def test_it_acknowledges_and_persists_a_control_hub_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            recorder = LaptopRobotRecorder(Path(directory))
            server = RobotDataTcpServer(
                "127.0.0.1", 0, discovery_port=5814,
                packet_handler=recorder.on_packet,
                raw_packet_handler=recorder.on_raw_packet,
                connection_handler=recorder.on_connection,
            )
            await server.start()
            try:
                port = server.listening_addresses[0][1]
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                session_id, connection_id = uuid.uuid4().bytes, uuid.uuid4().bytes
                hello = wire.Envelope(
                    protocol_version=2, session_id=session_id, connection_id=connection_id,
                    connection_sequence=0, hello=wire.Hello(robot_id="test", robot_name="Test", op_mode_name="Test"),
                )
                encoded = hello.SerializeToString()
                writer.write(struct.pack("!I", len(encoded)) + encoded)
                await writer.drain()
                reply_length = struct.unpack("!I", await reader.readexactly(4))[0]
                reply = wire.Envelope.FromString(await reader.readexactly(reply_length))
                self.assertTrue(reply.hello_ack.accepted)
                writer.close()
                await writer.wait_closed()
                await asyncio.sleep(0.05)
            finally:
                await server.close()
                await recorder.close()

            session = str(uuid.UUID(bytes=session_id))
            path = Path(directory) / session / "raw" / "stream-00000.ftclog"
            self.assertEqual([encoded], [item.protobuf_frame for item in iter_records(path)])


if __name__ == "__main__":
    unittest.main()
