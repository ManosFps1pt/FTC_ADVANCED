"""Standalone laptop recorder for the Control Hub structured TCP stream.

Run from the repository root:

    python -m web_driver_station.backend.record_robot_data

It uses UDP discovery on port 5811 and receives the framed Protobuf TCP stream
on port 5810 by default. It does not use ADB; ADB remains independent video
capture infrastructure.
"""

from __future__ import annotations

import argparse
import asyncio
import time
import uuid
from pathlib import Path

from .ftclog import RawFtcLogRecorder
from .protocol import robot_data_pb2 as wire
from .robot_data_tcp_server import (
    DEFAULT_DISCOVERY_PORT,
    DEFAULT_HOST,
    DEFAULT_MAX_PACKET_BYTES,
    DEFAULT_PORT,
    ReceivedRobotPacket,
    RobotDataTcpServer,
)


class LaptopRobotRecorder:
    """Persist every accepted robot frame as a session-scoped ``.ftclog``."""

    def __init__(self, recordings_dir: Path) -> None:
        self.recorder = RawFtcLogRecorder(recordings_dir)
        self._peer_sessions: dict[tuple[str, int], str] = {}

    async def on_raw_packet(
        self,
        envelope: wire.Envelope,
        encoded: bytes,
        peer: tuple[str, int] | None,
        received_monotonic_ns: int,
    ) -> None:
        if envelope.protocol_version != 2 or len(envelope.session_id) != 16 or envelope.WhichOneof("body") is None:
            return
        session_id = str(uuid.UUID(bytes=envelope.session_id))
        await asyncio.to_thread(self.recorder.append, session_id, encoded, received_monotonic_ns)
        if peer is not None:
            self._peer_sessions[peer] = session_id

    async def on_packet(self, packet: ReceivedRobotPacket) -> wire.Envelope | None:
        if packet.envelope.WhichOneof("body") == "hello":
            return wire.Envelope(
                protocol_version=2,
                session_id=packet.envelope.session_id,
                connection_id=packet.envelope.connection_id,
                connection_sequence=0,
                robot_elapsed_ns=0,
                hello_ack=wire.HelloAck(accepted=True, server_time_ns=time.perf_counter_ns()),
            )
        if any(payload.get("type") == "session_end" for payload in packet.payloads):
            await self._finalize_peer(packet.peer)
        return None

    async def on_connection(self, connected: bool, peer: tuple[str, int] | None) -> None:
        if not connected:
            await self._finalize_peer(peer)

    async def close(self) -> None:
        await asyncio.to_thread(self.recorder.close_all)

    async def _finalize_peer(self, peer: tuple[str, int] | None) -> None:
        if peer is None:
            return
        session_id = self._peer_sessions.pop(peer, None)
        if session_id is not None:
            await asyncio.to_thread(self.recorder.close_session, session_id)


async def run(args: argparse.Namespace) -> None:
    recordings_dir = args.recordings_dir.resolve()
    recorder = LaptopRobotRecorder(recordings_dir)
    server = RobotDataTcpServer(
        args.host,
        args.port,
        discovery_port=args.discovery_port,
        max_packet_bytes=args.max_packet_bytes,
        packet_handler=recorder.on_packet,
        raw_packet_handler=recorder.on_raw_packet,
        connection_handler=recorder.on_connection,
    )
    await server.start()
    print(
        f"Recording Control Hub data in {recordings_dir}. "
        f"TCP: {server.listening_addresses}; UDP discovery: {args.host}:{args.discovery_port}."
    )
    try:
        await server.serve_forever()
    finally:
        await server.close()
        await recorder.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Persist FTC Control Hub TCP telemetry on this laptop")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--discovery-port", type=int, default=DEFAULT_DISCOVERY_PORT)
    parser.add_argument("--max-packet-bytes", type=int, default=DEFAULT_MAX_PACKET_BYTES)
    parser.add_argument(
        "--recordings-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "recordings",
        help="Directory that will receive session UUID folders",
    )
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
