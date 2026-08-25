"""Length-delimited protobuf receiver for Control Hub telemetry (protocol v2)."""

from __future__ import annotations

import argparse
import asyncio
import inspect
import socket
import struct
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from google.protobuf.message import DecodeError

from .protocol import robot_data_pb2 as wire
from .protocol_codec import WireProtocolError, decode

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 5810
DEFAULT_DISCOVERY_PORT = 5811
DEFAULT_MAX_PACKET_BYTES = 1_048_576
_LENGTH_PREFIX = struct.Struct("!I")
_DISCOVERY_MAGIC = b"FTRD"
_DISCOVERY_VERSION = 2
_DISCOVERY_REQUEST = struct.Struct("!4sB16s")
_DISCOVERY_RESPONSE = struct.Struct("!4sB16sH")


class RobotDataProtocolError(Exception):
    """Raised when a peer violates framing or the protobuf contract."""


class _DiscoveryProtocol(asyncio.DatagramProtocol):
    def __init__(self, tcp_port: int) -> None:
        self._tcp_port = tcp_port
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        try:
            magic, version, nonce = _DISCOVERY_REQUEST.unpack(data)
            if magic != _DISCOVERY_MAGIC or version != _DISCOVERY_VERSION:
                return
        except struct.error:
            return
        if self.transport is not None:
            self.transport.sendto(_DISCOVERY_RESPONSE.pack(_DISCOVERY_MAGIC, _DISCOVERY_VERSION, nonce, self._tcp_port), addr)


@dataclass(frozen=True, slots=True)
class ReceivedRobotPacket:
    """One protobuf frame plus normalized records and receipt information."""

    envelope: wire.Envelope
    payloads: tuple[Mapping[str, Any], ...]
    peer: tuple[str, int] | None
    received_monotonic_ns: int

    @property
    def payload(self) -> Mapping[str, Any]:
        """The final normalized record; retained for status/debug views."""
        return self.payloads[-1]


PacketReply = wire.Envelope
PacketHandler = Callable[[ReceivedRobotPacket], Awaitable[PacketReply | None] | PacketReply | None]
ConnectionHandler = Callable[[bool, tuple[str, int] | None], Awaitable[None] | None]


class RobotDataTcpServer:
    """Own the laptop TCP listener and binary discovery responder."""

    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, *, discovery_port: int = DEFAULT_DISCOVERY_PORT,
                 max_packet_bytes: int = DEFAULT_MAX_PACKET_BYTES, packet_handler: PacketHandler | None = None,
                 connection_handler: ConnectionHandler | None = None) -> None:
        if not 0 <= port <= 65535 or not 0 < discovery_port <= 65535:
            raise ValueError("TCP port must be between 0 and 65535; discovery port must be between 1 and 65535")
        if max_packet_bytes <= 0:
            raise ValueError("max_packet_bytes must be positive")
        self.host, self.port, self.discovery_port, self.max_packet_bytes = host, port, discovery_port, max_packet_bytes
        self._packet_handler, self._connection_handler = packet_handler, connection_handler
        self._server: asyncio.Server | None = None
        self._discovery_transport: asyncio.DatagramTransport | None = None
        self._clients: set[asyncio.StreamWriter] = set()
        self._last_packet: ReceivedRobotPacket | None = None

    @property
    def last_packet(self) -> ReceivedRobotPacket | None: return self._last_packet
    @property
    def listening_addresses(self) -> tuple[tuple[Any, ...], ...]: return () if self._server is None else tuple(item.getsockname() for item in self._server.sockets or ())
    @property
    def connection_count(self) -> int: return len(self._clients)

    async def start(self) -> None:
        if self._server is not None: return
        self._server = await asyncio.start_server(self._handle_client, self.host, self.port)
        bound_port = int(self._server.sockets[0].getsockname()[1])
        transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(lambda: _DiscoveryProtocol(bound_port), local_addr=(self.host, self.discovery_port), family=socket.AF_INET, allow_broadcast=True)
        self._discovery_transport = transport

    async def serve_forever(self) -> None:
        if self._server is None: await self.start()
        assert self._server is not None
        async with self._server: await self._server.serve_forever()

    async def close(self) -> None:
        server, self._server = self._server, None
        discovery, self._discovery_transport = self._discovery_transport, None
        if discovery is not None: discovery.close()
        if server is not None: server.close()
        clients = tuple(self._clients)
        for writer in clients: writer.close()
        if server is not None: await server.wait_closed()
        if clients: await asyncio.gather(*(writer.wait_closed() for writer in clients), return_exceptions=True)
        self._clients.clear()

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._clients.add(writer)
        peer = _normalize_peer(writer.get_extra_info("peername"))
        channel_keys: dict[int, tuple[str, int]] = {}
        await self._notify_connection(True, peer)
        try:
            while True:
                length = _LENGTH_PREFIX.unpack(await reader.readexactly(_LENGTH_PREFIX.size))[0]
                if length == 0 or length > self.max_packet_bytes:
                    raise RobotDataProtocolError("invalid protobuf frame length")
                encoded = await reader.readexactly(length)
                try:
                    envelope = wire.Envelope.FromString(encoded)
                    payloads = decode(envelope, channel_keys)
                except (DecodeError, ValueError, WireProtocolError) as error:
                    raise RobotDataProtocolError(str(error)) from error
                packet = ReceivedRobotPacket(envelope, payloads, peer, time.perf_counter_ns())
                self._last_packet = packet
                if self._packet_handler is not None:
                    result = self._packet_handler(packet)
                    if inspect.isawaitable(result): result = await result
                    if result is not None: await self._write_packet(writer, result)
        except (asyncio.IncompleteReadError, ConnectionError, RobotDataProtocolError):
            pass
        finally:
            self._clients.discard(writer); await self._notify_connection(False, peer); writer.close()
            try: await writer.wait_closed()
            except ConnectionError: pass

    async def _notify_connection(self, connected: bool, peer: tuple[str, int] | None) -> None:
        if self._connection_handler is None: return
        result = self._connection_handler(connected, peer)
        if inspect.isawaitable(result): await result

    async def _write_packet(self, writer: asyncio.StreamWriter, envelope: wire.Envelope) -> None:
        encoded = envelope.SerializeToString()
        if not encoded or len(encoded) > self.max_packet_bytes: raise RobotDataProtocolError("invalid response frame length")
        writer.write(_LENGTH_PREFIX.pack(len(encoded)) + encoded); await writer.drain()


def _normalize_peer(value: object) -> tuple[str, int] | None:
    return (str(value[0]), int(value[1])) if isinstance(value, tuple) and len(value) >= 2 else None


async def _run_server(host: str, port: int, max_packet_bytes: int) -> None:
    server = RobotDataTcpServer(host, port, max_packet_bytes=max_packet_bytes)
    await server.start()
    print(f"Robot data protobuf server listening on {server.listening_addresses}; UDP discovery on {host}:{server.discovery_port}.")
    try: await server.serve_forever()
    finally: await server.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Receive length-delimited protobuf robot data")
    parser.add_argument("--host", default=DEFAULT_HOST); parser.add_argument("--port", type=int, default=DEFAULT_PORT); parser.add_argument("--max-packet-bytes", type=int, default=DEFAULT_MAX_PACKET_BYTES)
    args = parser.parse_args()
    try: asyncio.run(_run_server(args.host, args.port, args.max_packet_bytes))
    except KeyboardInterrupt: pass
    return 0


if __name__ == "__main__": raise SystemExit(main())
