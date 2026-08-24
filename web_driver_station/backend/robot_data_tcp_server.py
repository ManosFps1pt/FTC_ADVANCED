"""Independent TCP receiver for structured robot data.

The robot data connection deliberately does not share the Robocol client or
its timing thread.  A Control Hub opens this connection when an OpMode is
initialized and closes it when that OpMode stops.  The laptop can keep this
server listening between OpModes.

Protocol version 1 is intentionally small so the transport can be exercised
before storage and frontend work is added:

    4-byte unsigned big-endian payload length
    UTF-8 JSON object of exactly that length

TCP is a byte stream, so every packet is read with ``readexactly``.  The JSON
payload is an initial test envelope; it can later be replaced by protobuf
without changing server ownership or connection lifecycle.
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import socket
import struct
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any


DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 5810
DEFAULT_DISCOVERY_PORT = 5811
DEFAULT_MAX_PACKET_BYTES = 1_048_576
_LENGTH_PREFIX = struct.Struct("!I")
_DISCOVERY_PROTOCOL_VERSION = 1
_DISCOVERY_REQUEST_KIND = "where_is_data_server"
_DISCOVERY_RESPONSE_KIND = "data_server"


class RobotDataProtocolError(Exception):
    """Raised when a connected peer sends an invalid framed packet."""


class _DiscoveryProtocol(asyncio.DatagramProtocol):
    """Reply to a robot's UDP request with the laptop address for the TCP stream."""

    def __init__(self, tcp_port: int) -> None:
        self._tcp_port = tcp_port
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        try:
            request = json.loads(data.decode("utf-8"))
            if not isinstance(request, dict):
                return
            if request.get("protocol_version") != _DISCOVERY_PROTOCOL_VERSION:
                return
            if request.get("kind") != _DISCOVERY_REQUEST_KIND:
                return
            request_id = request.get("request_id")
            if not isinstance(request_id, str) or not request_id:
                return
            host = _local_address_for_peer(addr[0])
            response = json.dumps(
                {
                    "protocol_version": _DISCOVERY_PROTOCOL_VERSION,
                    "kind": _DISCOVERY_RESPONSE_KIND,
                    "request_id": request_id,
                    "host": host,
                    "port": self._tcp_port,
                },
                separators=(",", ":"),
            ).encode("utf-8")
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return

        if self.transport is not None:
            self.transport.sendto(response, addr)


def _local_address_for_peer(peer_host: str) -> str:
    """Select the local interface address the robot can actually route back to."""

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.connect((peer_host, 9))
        return str(probe.getsockname()[0])


@dataclass(frozen=True, slots=True)
class ReceivedRobotPacket:
    """A decoded packet together with laptop-side receipt information."""

    payload: Mapping[str, Any]
    peer: tuple[str, int] | None
    received_monotonic_ns: int


PacketReply = Mapping[str, Any]
PacketHandler = Callable[[ReceivedRobotPacket], Awaitable[PacketReply | None] | PacketReply | None]
ConnectionHandler = Callable[[bool, tuple[str, int] | None], Awaitable[None] | None]


class RobotDataTcpServer:
    """Own the laptop listener and expose the latest decoded robot packet.

    The class has no FastAPI dependency.  It can be run independently for
    transport testing now and owned by the web backend as a separate service
    later.
    """

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        *,
        discovery_port: int = DEFAULT_DISCOVERY_PORT,
        max_packet_bytes: int = DEFAULT_MAX_PACKET_BYTES,
        packet_handler: PacketHandler | None = None,
        connection_handler: ConnectionHandler | None = None,
    ) -> None:
        if not 0 < port <= 65_535:
            raise ValueError("port must be between 1 and 65535")
        if not 0 < discovery_port <= 65_535:
            raise ValueError("discovery_port must be between 1 and 65535")
        if max_packet_bytes <= 0:
            raise ValueError("max_packet_bytes must be positive")

        self.host = host
        self.port = port
        self.discovery_port = discovery_port
        self.max_packet_bytes = max_packet_bytes
        self._packet_handler = packet_handler
        self._connection_handler = connection_handler
        self._server: asyncio.Server | None = None
        self._discovery_transport: asyncio.DatagramTransport | None = None
        self._clients: set[asyncio.StreamWriter] = set()
        self._last_packet: ReceivedRobotPacket | None = None

    @property
    def last_packet(self) -> ReceivedRobotPacket | None:
        """Return the newest complete packet received by this process."""

        return self._last_packet

    @property
    def listening_addresses(self) -> tuple[tuple[Any, ...], ...]:
        if self._server is None:
            return ()
        return tuple(socket.getsockname() for socket in self._server.sockets or ())

    @property
    def connection_count(self) -> int:
        return len(self._clients)

    async def start(self) -> None:
        """Start listening; calling this twice is harmless."""

        if self._server is not None:
            return
        self._server = await asyncio.start_server(
            self._handle_client,
            host=self.host,
            port=self.port,
        )
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(
            lambda: _DiscoveryProtocol(self.port),
            local_addr=(self.host, self.discovery_port),
            family=socket.AF_INET,
            allow_broadcast=True,
        )
        self._discovery_transport = transport

    async def serve_forever(self) -> None:
        if self._server is None:
            await self.start()
        assert self._server is not None
        async with self._server:
            await self._server.serve_forever()

    async def close(self) -> None:
        """Close the listener and all current robot connections."""

        server, self._server = self._server, None
        discovery_transport, self._discovery_transport = self._discovery_transport, None
        if discovery_transport is not None:
            discovery_transport.close()
        if server is not None:
            server.close()

        clients = tuple(self._clients)
        for writer in clients:
            writer.close()

        if server is not None:
            await server.wait_closed()
        if clients:
            await asyncio.gather(
                *(writer.wait_closed() for writer in clients),
                return_exceptions=True,
            )
        self._clients.clear()

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        self._clients.add(writer)
        peer = _normalize_peer(writer.get_extra_info("peername"))
        await self._notify_connection(True, peer)
        try:
            while True:
                prefix = await reader.readexactly(_LENGTH_PREFIX.size)
                payload_length = _LENGTH_PREFIX.unpack(prefix)[0]
                if payload_length == 0:
                    raise RobotDataProtocolError("zero-length packets are not allowed")
                if payload_length > self.max_packet_bytes:
                    raise RobotDataProtocolError(
                        f"packet length {payload_length} exceeds "
                        f"the {self.max_packet_bytes}-byte limit"
                    )

                encoded = await reader.readexactly(payload_length)
                packet = self._decode_packet(encoded, peer)
                self._last_packet = packet
                if self._packet_handler is not None:
                    result = self._packet_handler(packet)
                    if inspect.isawaitable(result):
                        result = await result
                    if result is not None:
                        await self._write_packet(writer, result)
        except asyncio.IncompleteReadError:
            # EOF between OpModes and Wi-Fi loss are normal lifecycle events.
            pass
        except (ConnectionError, RobotDataProtocolError, UnicodeDecodeError, json.JSONDecodeError):
            # This test backend drops malformed/broken connections.  A later
            # integration can route the exception details into status events.
            pass
        finally:
            self._clients.discard(writer)
            await self._notify_connection(False, peer)
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass

    async def _notify_connection(self, connected: bool, peer: tuple[str, int] | None) -> None:
        if self._connection_handler is None:
            return
        result = self._connection_handler(connected, peer)
        if inspect.isawaitable(result):
            await result

    @staticmethod
    async def _write_packet(writer: asyncio.StreamWriter, payload: Mapping[str, Any]) -> None:
        """Send one framed JSON response on the same TCP connection."""

        encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        if not encoded or len(encoded) > DEFAULT_MAX_PACKET_BYTES:
            raise RobotDataProtocolError("response packet has an invalid length")
        writer.write(_LENGTH_PREFIX.pack(len(encoded)))
        writer.write(encoded)
        await writer.drain()

    @staticmethod
    def _decode_packet(
        encoded: bytes,
        peer: tuple[str, int] | None,
    ) -> ReceivedRobotPacket:
        payload = json.loads(encoded.decode("utf-8"))
        if not isinstance(payload, dict):
            raise RobotDataProtocolError("packet payload must be a JSON object")
        return ReceivedRobotPacket(
            payload=payload,
            peer=peer,
            received_monotonic_ns=time.perf_counter_ns(),
        )


def _normalize_peer(value: object) -> tuple[str, int] | None:
    if isinstance(value, tuple) and len(value) >= 2:
        return str(value[0]), int(value[1])
    return None


class _LatestPacketPrinter:
    """Redraw one terminal line so a 50 Hz test does not flood stdout."""

    def __init__(self) -> None:
        self._previous_width = 0

    def __call__(self, packet: ReceivedRobotPacket) -> None:
        rendered = json.dumps(
            {
                "peer": packet.peer,
                "received_monotonic_ns": packet.received_monotonic_ns,
                "packet": packet.payload,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
        padded = rendered.ljust(self._previous_width)
        print(f"\rLast packet: {padded}", end="", flush=True)
        self._previous_width = len(rendered)

    def finish(self) -> None:
        if self._previous_width:
            print()


async def _run_server(host: str, port: int, max_packet_bytes: int) -> None:
    printer = _LatestPacketPrinter()
    server = RobotDataTcpServer(
        host,
        port,
        max_packet_bytes=max_packet_bytes,
        packet_handler=printer,
    )
    await server.start()
    addresses = ", ".join(str(address) for address in server.listening_addresses)
    print(
        f"Robot data TCP server listening on {addresses}; "
        f"UDP discovery on {host}:{server.discovery_port}. Press Ctrl+C to stop."
    )
    try:
        await server.serve_forever()
    finally:
        await server.close()
        printer.finish()


def main() -> int:
    parser = argparse.ArgumentParser(description="Receive framed robot data over TCP")
    parser.add_argument("--host", default=DEFAULT_HOST, help="local address to bind")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--max-packet-bytes",
        type=int,
        default=DEFAULT_MAX_PACKET_BYTES,
    )
    args = parser.parse_args()

    try:
        asyncio.run(_run_server(args.host, args.port, args.max_packet_bytes))
    except KeyboardInterrupt:
        print("Robot data TCP server stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
