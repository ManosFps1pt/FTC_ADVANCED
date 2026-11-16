"""Loopback-only API for the FTC web Driver Station dashboard.

Robocol timing stays in :class:`ftc_control_hub.ControlHubClient`'s background
thread. The web browser is only a local UI: it submits desired input states and
receives telemetry/status events over a WebSocket.
"""

from __future__ import annotations

import asyncio
import os
import platform
import secrets
import re
import subprocess
import sys
import threading
import time
import uuid
from math import isfinite
import xml.etree.ElementTree as ElementTree
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable, Literal

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ftc_control_hub import (
    ControlHubClient,
    ControlHubConfig,
    ControlHubError,
    GamepadInput,
    OpModeException,
    Packet,
    RobotState,
    decode_telemetry,
)
from .robot_data_tcp_server import DEFAULT_HOST, DEFAULT_PORT, ReceivedRobotPacket, RobotDataTcpServer
from .telemetry_store import TelemetryProtocolError, TelemetryStore, TelemetryUpdate
from .protocol import robot_data_pb2 as wire
from .debug_commands import build_debug_command_request, command_request_id
from .ftclog import FtcLogError, RawFtcLogRecorder, is_closed_partial
from .debugger_results import DebuggerResults, atomic_json
from .debugger_api import install_debugger_controls
from .incidents import IncidentError, IncidentRecorder
from .broadcast import BrowserBroadcast
from .recording_metadata import RECORDING_METADATA_FILE, RecordingMetadata
from .recording_uploader import RecordingUploadError, RecordingUploader, UploadSettings
from .video_recorder import CameraRecordingConfig, VideoRecorder, VideoRecorderError
from .adb_camera import AdbCameraError, AdbCameraService
from .camera_recording import CameraRecordingCoordinator, CameraRecordingError


class ConnectRequest(BaseModel):
    host: str = Field(min_length=1)
    local_address: str | None = None
    local_port: int = Field(default=20884, ge=1, le=65535)
    timezone_id: str | None = None
    timeout_s: float = Field(default=5.0, gt=0, le=30)


class GamepadState(BaseModel):
    lease_id: str | None = None
    left_stick_x: float = Field(default=0.0, ge=-1, le=1)
    left_stick_y: float = Field(default=0.0, ge=-1, le=1)
    right_stick_x: float = Field(default=0.0, ge=-1, le=1)
    right_stick_y: float = Field(default=0.0, ge=-1, le=1)
    left_trigger: float = Field(default=0.0, ge=0, le=1)
    right_trigger: float = Field(default=0.0, ge=0, le=1)
    buttons: int = Field(default=0, ge=0, le=0x3FFFF)


class ControlLeaseRequest(BaseModel):
    lease_id: str = Field(min_length=16, max_length=100)


class OpModeRequest(BaseModel):
    name: str = Field(min_length=1)
    timeout_s: float = Field(default=3.0, gt=0, le=15)


class SwitchGamepadRequest(BaseModel):
    from_user: int = Field(ge=1, le=2)
    to_user: int = Field(ge=1, le=2)


class ConfigurationSaveRequest(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    xml: str = Field(min_length=1, max_length=60_000)
    timeout_s: float = Field(default=5.0, gt=0, le=15)


class VideoRecordingRequest(BaseModel):
    """Laptop webcam settings for one replay recording session."""

    device_index: int = Field(default=0, ge=0, le=32)
    width: int = Field(default=1280, ge=16, le=7680)
    height: int = Field(default=720, ge=16, le=4320)
    fps: float = Field(default=30.0, ge=1, le=120)
    segment_seconds: float = Field(default=60.0, ge=1, le=3600)
    camera_id: str = Field(default="cam0", min_length=1, max_length=32)


class DebugSelectHttpRequest(BaseModel):
    lease_id: str | None = None
    node_id: str = Field(min_length=1, max_length=160)
    manifest_revision: int = Field(default=1, ge=1)


class DebugTargetRequest(BaseModel):
    session_id: str


class DebugParameterHttpRequest(BaseModel):
    lease_id: str | None = None
    node_id: str = Field(min_length=1, max_length=160)
    tool_instance_id: str = Field(min_length=1, max_length=80)
    parameter_id: str = Field(min_length=1, max_length=160)
    value: float = Field(allow_inf_nan=False)
    ttl_ms: int = Field(default=500, ge=1, le=10_000)
    request_id: str | None = Field(default=None, min_length=1, max_length=80)


class DebugCommandHttpRequest(BaseModel):
    lease_id: str | None = None
    node_id: str = Field(min_length=1, max_length=160)
    tool_instance_id: str = Field(min_length=1, max_length=80)
    command_id: str = Field(min_length=1, max_length=160)
    arguments: dict[str, Any] = Field(default_factory=dict)
    ttl_ms: int = Field(default=1_000, ge=1, le=10_000)
    # Browser callers create this before dispatching so a TCP response that
    # arrives before the HTTP response can still be matched to its control.
    # It remains optional for compatibility with existing API callers.
    request_id: str | None = Field(default=None, min_length=1, max_length=80)


class TelemetryCaptureRequest(BaseModel):
    enabled: bool


class RecordingUploadRequest(RecordingMetadata):
    keep_local: bool = False


class AdbDeviceRoleRequest(BaseModel):
    role: Literal["auto", "robot_controller", "camera", "ignored"]


class DirectScrcpySettingsRequest(BaseModel):
    facing: Literal["front", "back", "external"] | None = "back"
    aspect_ratio: str | None = Field(default=None, max_length=64)
    fps: int = Field(default=30, ge=1, le=120)
    max_size: int | None = Field(default=1280, ge=16, le=7680)
    video_bit_rate: str = Field(default="4M", pattern=r"^[1-9]\d*[KMG]$")
    flip: bool = False


class CameraCaptureConfigRequest(BaseModel):
    mode: Literal["scrcpy_direct", "adb_volume_up"] = "scrcpy_direct"
    direct: DirectScrcpySettingsRequest = Field(default_factory=DirectScrcpySettingsRequest)


# Mirror the SDK's filename safety checks while also excluding the semicolon
# used as the Robocol save-command delimiter. Dots and parentheses are common
# in team configuration names, so do not needlessly reject them.
_CONFIGURATION_NAME = re.compile(r'[^?:"*|/\\<>\x00-\x1F;]{1,60}\Z')


class DriverStationService:
    """Own one Robocol session and safely expose it to a local web UI."""

    def __init__(self) -> None:
        self._client: ControlHubClient | None = None
        self._lock = threading.RLock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._sockets: set[WebSocket] = set()
        self._last_telemetry: dict[str, Any] | None = None
        self._driver_station_error: str | None = None
        self._last_opmode_exception_timestamp_ns: int | None = None
        self._started_opmode = False
        # Lifecycle commands have a definitive acknowledgement, whereas the
        # next heartbeat can arrive late or report the OpMode that just ended.
        # Keep the confirmed command state authoritative for the UI until the
        # session is closed.
        self._confirmed_robot_state: RobotState | None = None

    def set_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def status(self) -> dict[str, Any]:
        with self._lock:
            client = self._client
            robot_state = self._confirmed_robot_state or (client.robot_state if client else RobotState.UNKNOWN)
            return {
                "connected": bool(client and client.is_connected),
                "host": client.config.host if client else None,
                "robot_state": robot_state.name,
                "started_opmode": robot_state is RobotState.RUNNING,
                "telemetry": self._last_telemetry,
                "driver_station_error": self._driver_station_error,
            }

    def connect(self, request: ConnectRequest) -> dict[str, Any]:
        with self._lock:
            if self._client and self._client.is_connected:
                raise ControlHubError("Already connected; disconnect before choosing another Robot Controller")
            self._close_locked(stop=False)
            client = ControlHubClient(
                ControlHubConfig(
                    host=request.host,
                    local_address=request.local_address,
                    local_port=request.local_port,
                    timezone_id=request.timezone_id,
                )
            )
            client.add_packet_listener(self._on_packet)
            client.add_opmode_exception_listener(self._on_opmode_exception)
            self._client = client
            self._confirmed_robot_state = None
        try:
            client.connect(timeout_s=request.timeout_s)
        except Exception:
            with self._lock:
                if self._client is client:
                    self._client = None
            client.close()
            raise
        # A newly connected dashboard has not selected a user OpMode yet.
        # The RC often reports its internal "$Stop$Robot$" sentinel as
        # RUNNING; reflecting that directly makes the UI present a Stop button
        # and forces users to stop the sentinel before choosing their OpMode.
        # Start this local session at the neutral lifecycle state instead.
        with self._lock:
            if self._client is client:
                self._confirmed_robot_state = RobotState.NOT_STARTED
        self._publish_from_thread("status", self.status())
        return self.status()

    def disconnect(self, *, stop: bool = True) -> dict[str, Any]:
        with self._lock:
            self._close_locked(stop=stop)
        self._publish_from_thread("status", self.status())
        return self.status()

    def list_opmodes(self) -> tuple[dict[str, object], ...]:
        return self._require_client().list_opmodes()

    def init_opmode(self, request: OpModeRequest) -> dict[str, Any]:
        self._require_client().init_opmode(request.name, request.timeout_s)
        self._started_opmode = False
        self._confirmed_robot_state = RobotState.INIT
        self._publish_from_thread("status", self.status())
        return self.status()

    def start_opmode(self, request: OpModeRequest) -> dict[str, Any]:
        self._require_client().start_opmode(request.name, request.timeout_s)
        self._started_opmode = True
        self._confirmed_robot_state = RobotState.RUNNING
        self._publish_from_thread("status", self.status())
        return self.status()

    def launch_opmode(self, request: OpModeRequest) -> dict[str, Any]:
        """Stop the current OpMode, then initialize and start the requested one."""

        available = self.list_opmodes()
        if request.name not in {str(item.get("name", "")) for item in available}:
            raise ControlHubError(f"OpMode {request.name!r} is not advertised by the Robot Controller")
        robot_state = self.status()["robot_state"]
        if robot_state not in (RobotState.NOT_STARTED.name, RobotState.STOPPED.name):
            self.stop_opmode()
        self.init_opmode(request)
        return self.start_opmode(request)

    def stop_opmode(self) -> dict[str, Any]:
        client = self._require_client()
        client.release_control()
        client.clear_gamepad_input(1)
        client.clear_gamepad_input(2)
        client.stop_opmode()
        # Keep the exception shown by the FTC SDK's stacktrace command until
        # the operator explicitly stops the OpMode.
        with self._lock:
            self._driver_station_error = None
            self._last_opmode_exception_timestamp_ns = None
        self._started_opmode = False
        self._confirmed_robot_state = RobotState.STOPPED
        self._publish_from_thread("status", self.status())
        return self.status()

    def set_gamepad(self, user: int, state: GamepadState) -> None:
        client = self._require_client()
        if self.status()["robot_state"] not in ("INIT", "RUNNING"):
            raise ControlHubError("Initialize an OpMode before sending gamepad input")
        client.set_gamepad_input(
            GamepadInput(
                user=user,
                left_stick_x=state.left_stick_x,
                left_stick_y=state.left_stick_y,
                right_stick_x=state.right_stick_x,
                right_stick_y=state.right_stick_y,
                left_trigger=state.left_trigger,
                right_trigger=state.right_trigger,
                buttons=state.buttons,
            ), lease_id=state.lease_id
        )

    def clear_gamepad(self, user: int) -> None:
        self._require_client().clear_gamepad_input(user)

    def switch_gamepad(self, request: SwitchGamepadRequest) -> None:
        if request.from_user == request.to_user:
            return
        client = self._require_client()
        client.clear_gamepad_input(request.from_user)
        client.set_gamepad_input(GamepadInput.neutral(request.to_user))

    def list_configurations(self) -> tuple[dict[str, object], ...]:
        return self._require_client().list_configurations()

    def active_configuration(self) -> dict[str, object]:
        return self._require_client().get_active_configuration()

    def ping(self) -> dict[str, object]:
        """Measure the local computer's ICMP round trip to the connected RC.

        This deliberately measures network reachability rather than creating
        extra Robocol commands that could interfere with the Driver Station
        session. A failed ICMP request is reported as unavailable, not as a
        connection failure, because some networks block ICMP.
        """
        client = self._require_client()
        command = ["ping", "-n", "1", "-w", "1000", client.config.host] if platform.system() == "Windows" else [
            "ping", "-c", "1", "-W", "1", client.config.host
        ]
        started = time.perf_counter()
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=1.5, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return {"latency_ms": None}
        if result.returncode != 0:
            return {"latency_ms": None}
        # The wall-clock result includes process startup, but remains a useful
        # cross-platform fallback when a localized ping output has no time= token.
        return {"latency_ms": round((time.perf_counter() - started) * 1000, 1)}

    def read_configuration_xml(self, name: str) -> dict[str, str]:
        return {"name": name, "xml": self._require_client().read_configuration_xml(name)}

    def save_configuration(self, request: ConfigurationSaveRequest) -> dict[str, object]:
        name = request.name
        if name != name.strip() or name in {".", ".."} or not _CONFIGURATION_NAME.fullmatch(name):
            raise ControlHubError(
                "Configuration names cannot start or end with whitespace and cannot contain "
                "path characters, semicolons, or control characters"
            )
        encoded_xml = request.xml.encode("utf-8")
        if len(encoded_xml) > 60_000:
            raise ControlHubError("Configuration XML must be 60,000 UTF-8 bytes or smaller")
        try:
            root = ElementTree.fromstring(request.xml)
        except ElementTree.ParseError as error:
            raise ControlHubError(f"Configuration XML is invalid: {error}") from error
        if root.tag != "Robot":
            raise ControlHubError("Configuration XML must have a <Robot> root element")

        client = self._require_client()
        # SDK-bundled templates and the synthetic "no configuration" entry are
        # read-only. Require an explicit new name rather than turning a save
        # into a surprising local shadow of one of those entries.
        existing = {str(item.get("name", "")).casefold(): item for item in client.list_configurations()}
        previous = existing.get(name.casefold())
        if previous and str(previous.get("location", "")).upper() in {"RESOURCE", "NONE"}:
            raise ControlHubError("This is a read-only configuration/template. Save it under a new name instead")
        # Changing a hardware map while an OpMode owns the devices can leave the
        # RC in an unsafe, partially reconfigured state. The local lifecycle
        # flag plus the RC heartbeat must both report a stopped robot.
        if self._started_opmode or client.robot_state not in (RobotState.NOT_STARTED, RobotState.STOPPED):
            raise ControlHubError("Stop the robot and wait for a STOPPED or NOT_STARTED state before saving a configuration")
        active = client.save_configuration_xml(name, request.xml, request.timeout_s)
        self._publish_from_thread("status", self.status())
        return active

    async def add_socket(self, socket: WebSocket) -> None:
        await socket.accept()
        self._sockets.add(socket)
        await socket.send_json({"kind": "status", "data": self.status()})

    def remove_socket(self, socket: WebSocket) -> None:
        self._sockets.discard(socket)

    def _require_client(self) -> ControlHubClient:
        with self._lock:
            if self._client is None or not self._client.is_connected:
                raise ControlHubError("Connect to a Robot Controller first")
            return self._client

    def _close_locked(self, *, stop: bool) -> None:
        client, self._client = self._client, None
        if client is None:
            return
        if stop and client.is_connected:
            try:
                client.clear_gamepad_input(1)
                client.clear_gamepad_input(2)
                client.stop_opmode(timeout_s=2.0)
            except ControlHubError:
                pass
        client.close()
        self._started_opmode = False
        self._confirmed_robot_state = None
        self._last_telemetry = None

    def _on_opmode_exception(self, exception: OpModeException) -> None:
        """Print the same stacktrace the official DS receives over Robocol."""

        with self._lock:
            if self._last_opmode_exception_timestamp_ns == exception.timestamp_ns:
                return
            self._last_opmode_exception_timestamp_ns = exception.timestamp_ns
            self._driver_station_error = exception.stacktrace

        print(
            "\n=== Robot Controller: OpMode threw an uncaught exception ===\n"
            f"{exception.stacktrace.rstrip()}\n"
            "=== End Robot Controller OpMode exception ===\n",
            file=sys.stderr,
            flush=True,
        )
        self._publish_from_thread("status", self.status())

    def _on_packet(self, packet: Packet) -> None:
        telemetry = decode_telemetry(packet)
        if telemetry:
            self._last_telemetry = {
                "timestamp_ms": telemetry.timestamp_ms,
                "state": telemetry.robot_state.name,
                "tag": telemetry.tag,
                "strings": list(telemetry.strings),
                "numbers": list(telemetry.numbers),
            }
            self._publish_from_thread("telemetry", self._last_telemetry)
        elif packet.message_type == 1:
            self._publish_from_thread("status", self.status())

    def _publish_from_thread(self, kind: str, data: dict[str, Any]) -> None:
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._broadcast(kind, data), self._loop)

    async def _broadcast(self, kind: str, data: dict[str, Any]) -> None:
        stale: list[WebSocket] = []
        for socket in tuple(self._sockets):
            try:
                await socket.send_json({"kind": kind, "data": data})
            except RuntimeError:
                stale.append(socket)
        for socket in stale:
            self._sockets.discard(socket)


class RobotDataService:
    """Expose raw packets and structured telemetry to the web dashboard."""

    # A Robot Controller stop and the final TCP disconnect can coincide with a
    # short-lived Wi-Fi/SSH interruption. Keep finalized data locally and retry
    # only those transport failures before surfacing a terminal upload error.
    _AUTO_UPLOAD_RETRY_DELAYS_SECONDS = (2.0, 10.0)

    def __init__(self) -> None:
        host = os.getenv("ROBOT_DATA_TCP_HOST", DEFAULT_HOST)
        port = int(os.getenv("ROBOT_DATA_TCP_PORT", str(DEFAULT_PORT)))
        self._server = RobotDataTcpServer(
            host,
            port,
            packet_handler=self._on_packet,
            raw_packet_handler=self._on_raw_packet,
            connection_handler=self._on_connection,
        )
        self._sockets: set[WebSocket] = set()
        self._telemetry_sockets: set[WebSocket] = set()
        self._debug_sockets: set[WebSocket] = set()
        self._peers: set[tuple[str, int]] = set()
        self._connection_ids: dict[tuple[str, int], str] = {}
        self._connection_sessions: dict[tuple[str, int], str] = {}
        self._latest_packet: dict[str, Any] | None = None
        self._telemetry = TelemetryStore()
        self._capture_override_enabled = False
        self._capture_override_session_id: str | None = None
        self._debug_manifest: dict[str, Any] | None = None
        self._debug_ready: dict[str, Any] | None = None
        self._debug_tool_state: dict[str, Any] | None = None
        self._debug_safety: dict[str, Any] | None = None
        self._debug_last_event: dict[str, Any] | None = None
        self._active_peer: tuple[str, int] | None = None
        self._active_session_id: bytes | None = None
        self._active_connection_id: bytes | None = None
        self._peer_states: dict[tuple[str, int] | None, dict[str, Any]] = {}
        self._selected_session: str | None = None
        self._browser_broadcast = BrowserBroadcast()
        self._debug_lease = None
        self._debug_lease_deadline = 0.0
        self._blocked_robot_lease = ""
        self._debug_sequence = 1
        recordings_root = Path(
            os.getenv("ROBOT_DATA_RECORDINGS_DIR", str(Path(__file__).resolve().parents[1] / "recordings"))
        ).resolve()
        self._raw_recorder = RawFtcLogRecorder(recordings_root)
        self._debug_results = DebuggerResults(recordings_root)
        self._benchmark_sessions: set[str] = set()
        self._incident_recorder = IncidentRecorder(recordings_root)
        self._recording_uploader: RecordingUploader | None = None
        self._upload_states: dict[str, dict[str, Any]] = {}
        self._raw_finalized: set[str] = set()
        self._video_errors: dict[str, str] = {}
        self._video_ready_checker: Callable[[str], bool] = lambda _session_id: True
        self._session_bound_callback: Callable[[str], None] | None = None
        self._capture_stop_callback: Callable[[str], None] | None = None
        self._capture_start_callback: Callable[[str], None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        try:
            upload_settings = UploadSettings.from_environment()
            if upload_settings is not None:
                self._recording_uploader = RecordingUploader(upload_settings)
        except RecordingUploadError as error:
            self._upload_states["configuration"] = {"state": "error", "detail": str(error)}

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        await self._server.start()

    def set_capture_callbacks(
        self,
        *,
        video_ready_checker: Callable[[str], bool],
        session_bound: Callable[[str], None],
        capture_stop: Callable[[str], None],
        capture_start: Callable[[str], None] | None = None,
    ) -> None:
        self._video_ready_checker = video_ready_checker
        self._session_bound_callback = session_bound
        self._capture_stop_callback = capture_stop
        self._capture_start_callback = capture_start

    def video_finalized_from_thread(self, session_id: str, error: str | None) -> None:
        """Accept camera-worker completion without touching asyncio from its thread."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        asyncio.run_coroutine_threadsafe(self.video_finalized(session_id, error), loop)

    async def video_finalized(self, session_id: str, error: str | None) -> None:
        if error:
            self._video_errors[session_id] = error
        await self._prepare_upload_decision(session_id)

    async def close(self) -> None:
        await self._browser_broadcast.close()
        await self._server.close()
        if self._capture_override_session_id is not None:
            await asyncio.to_thread(self._incident_recorder.close_session, self._capture_override_session_id)
        await asyncio.to_thread(self._raw_recorder.close_all)
        self._peers.clear()
        self._connection_ids.clear()
        self._connection_sessions.clear()

    def status(self) -> dict[str, Any]:
        return {
            "listening": bool(self._server.listening_addresses),
            "connected": any(time.perf_counter_ns()-state["received_ns"] < 2_000_000_000 for state in self._peer_states.values()),
            "connection_count": len(self._peers),
            "peers": [{"host": host, "port": port} for host, port in sorted(self._peers)],
            "latest_packet": self._latest_packet,
            "telemetry": self._telemetry.status(),
            "recording": {
                **self._raw_recorder.status(),
                "upload_configured": self._recording_uploader is not None,
                "uploads": self._upload_states,
            },
            "debug": self.debug_status(),
        }

    async def add_socket(self, socket: WebSocket) -> None:
        await socket.accept()
        self._sockets.add(socket)
        await socket.send_json({"kind": "robot_data_status", "data": self.status()})

    def remove_socket(self, socket: WebSocket) -> None:
        self._sockets.discard(socket)

    async def add_telemetry_socket(self, socket: WebSocket) -> None:
        """Attach a browser to normalized telemetry, including a short history."""

        await socket.accept()
        self._telemetry_sockets.add(socket)
        await socket.send_json({"kind": "telemetry_state", "data": self._telemetry.live_state()})

    def remove_telemetry_socket(self, socket: WebSocket) -> None:
        self._telemetry_sockets.discard(socket)

    async def add_debug_socket(self, socket: WebSocket) -> None:
        await socket.accept()
        self._debug_sockets.add(socket)
        await socket.send_json({"kind": "debug_state", "data": self.debug_status()})

    def remove_debug_socket(self, socket: WebSocket) -> None:
        self._debug_sockets.discard(socket)

    def debug_status(self) -> dict[str, Any]:
        return {
            "connected": self._debug_fresh(),
            "selected_session": self._selected_session,
            "sessions": [{"session_id": state["session_id"], "robot_id": state["robot_id"],
                          "opmode": state["opmode"], "protocol": state["protocol"]}
                         for state in self._peer_states.values()],
            "manifest": self._debug_manifest,
            "tool_ready": self._debug_ready,
            "tool_state": self._debug_tool_state,
            "safety": {key: value for key, value in (self._debug_safety or {}).items() if key != "controlLeaseId"},
            "control_owned": bool(self._debug_lease and time.monotonic()<self._debug_lease_deadline),
            "last_event": self._debug_last_event,
        }

    def _debug_fresh(self) -> bool:
        state = self._peer_states.get(self._active_peer)
        return bool(state and time.perf_counter_ns() - state["received_ns"] < 2_000_000_000)

    def select_debug_target(self, session_id: str) -> None:
        matches = [(peer, state) for peer, state in self._peer_states.items() if state["session_id"] == session_id]
        if len(matches) != 1:
            raise ValueError("Choose exactly one connected robot session")
        peer, state = matches[0]
        if self._debug_lease and time.monotonic()<self._debug_lease_deadline:
            raise ValueError("Release control before switching robot sessions")
        self._debug_lease = None
        self._selected_session = session_id
        if self._telemetry.has_session(session_id):
            self._telemetry.select_session(session_id)
        self._active_peer = peer
        self._active_session_id = state["session_bytes"]
        self._active_connection_id = state["connection_bytes"]
        self._debug_sequence = state.get("outgoing_sequence", 1)
        self._debug_manifest = state.get("debug_manifest")
        self._debug_ready = state.get("debug_tool_ready")
        self._debug_tool_state = state.get("debug_tool_state")
        self._debug_safety = state.get("debug_safety_state")
        self._debug_last_event = None

    def acquire_debug_control(self) -> str:
        if not self._debug_fresh() or self._peer_states[self._active_peer]["protocol"] != 3:
            raise RuntimeError("Select a fresh protocol v3 session first")
        if self._debug_lease and time.monotonic()<self._debug_lease_deadline:
            raise RuntimeError("Another browser owns this session")
        self._debug_lease = secrets.token_urlsafe(24)
        self._blocked_robot_lease = (self._debug_safety or {}).get("controlLeaseId", "")
        self._debug_lease_deadline = time.monotonic()+0.5
        return self._debug_lease

    def require_debug_control(self, token: str | None) -> None:
        if not self._debug_fresh() or time.monotonic()>=self._debug_lease_deadline:
            self._debug_lease = None
        if not token or token != self._debug_lease:
            raise RuntimeError("Debugger control lease expired; explicitly reacquire control")

    async def renew_debug_control(self, token: str) -> None:
        self.require_debug_control(token)
        self._debug_lease_deadline = time.monotonic()+0.5
        safety = self._debug_safety or {}
        if safety.get("armed") and safety.get("controlLeaseId") != self._blocked_robot_lease and self._debug_ready:
            envelope=self._debug_envelope(lease_id=token, debug_command_request=wire.DebugCommandRequest(
                request_id=str(uuid.uuid4()), node_id=self._debug_ready["nodeId"],
                tool_instance_id=self._debug_ready["toolInstanceId"], command_id="control.keepalive", ttl_ms=500))
            await self._server.send(envelope, self._active_peer)

    async def arm_debug_control(self, token: str) -> None:
        self.require_debug_control(token)
        ready = self._debug_ready or {}
        if not any(c.get("id") == "control.arm" for c in ready.get("commands", [])):
            raise RuntimeError("Install the updated BIOBUZZ APK to arm from the laptop")
        envelope = self._debug_envelope(lease_id=token, debug_command_request=wire.DebugCommandRequest(
            request_id=str(uuid.uuid4()), node_id=ready["nodeId"],
            tool_instance_id=ready["toolInstanceId"], command_id="control.arm", ttl_ms=500))
        await self._server.send(envelope, self._active_peer)

    async def send_debug_select(self, request: DebugSelectHttpRequest) -> dict[str, str]:
        request_id = str(uuid.uuid4())
        envelope = self._debug_envelope(
            lease_id=request.lease_id,
            debug_select_request=wire.DebugSelectRequest(
                request_id=request_id,
                manifest_revision=request.manifest_revision,
                node_id=request.node_id,
            )
        )
        await self._server.send(envelope, self._active_peer)
        return {"request_id": request_id, "node_id": request.node_id}

    async def send_debug_parameter(self, request: DebugParameterHttpRequest) -> dict[str, str]:
        ready = self._debug_ready or {}
        if (ready.get("nodeId") != request.node_id or ready.get("toolInstanceId") != request.tool_instance_id):
            raise RuntimeError("debug parameter targets a different tool instance")
        parameter = next((item for item in ready.get("parameters", []) if item.get("id") == request.parameter_id), None)
        if parameter is None or not parameter.get("writable"):
            raise ValueError(f"parameter is not writable: {request.parameter_id}")
        if not isfinite(request.value) or not parameter["min"] <= request.value <= parameter["max"]:
            raise ValueError(f"value is outside the registered bounds for {request.parameter_id}")

        request_id = command_request_id(request.request_id)
        envelope = self._debug_envelope(
            lease_id=request.lease_id,
            debug_parameter_set_request=wire.DebugParameterSetRequest(
                request_id=request_id,
                node_id=request.node_id,
                tool_instance_id=request.tool_instance_id,
                parameter_id=request.parameter_id,
                float64_value=request.value,
                ttl_ms=request.ttl_ms,
            )
        )
        await self._server.send(envelope, self._active_peer)
        return {"request_id": request_id, "parameter_id": request.parameter_id}

    async def send_debug_command(self, request: DebugCommandHttpRequest) -> dict[str, str]:
        if self._debug_ready is None:
            raise RuntimeError("robot has not advertised a debug command catalog")
        if (self._debug_ready.get("nodeId") != request.node_id
                or self._debug_ready.get("toolInstanceId") != request.tool_instance_id):
            raise RuntimeError("debug command targets a different tool instance")
        command = next(
            (item for item in self._debug_ready.get("commands", []) if item.get("id") == request.command_id),
            None,
        )
        if command is None:
            raise ValueError(f"command is not advertised: {request.command_id}")

        definitions = {item["id"]: item for item in command.get("arguments", [])}
        unknown = sorted(set(request.arguments) - set(definitions))
        missing = sorted(
            item_id for item_id, item in definitions.items()
            if item.get("required", False) and item_id not in request.arguments
        )
        if unknown:
            raise ValueError(f"unknown command argument(s): {', '.join(unknown)}")
        if missing:
            raise ValueError(f"missing command argument(s): {', '.join(missing)}")

        encoded_arguments = [
            self._encode_debug_argument(argument_id, definitions[argument_id], value)
            for argument_id, value in request.arguments.items()
        ]
        request_id = command_request_id(request.request_id)
        envelope = self._debug_envelope(
            lease_id=request.lease_id,
            debug_command_request=build_debug_command_request(
                request_id=request_id,
                node_id=request.node_id,
                tool_instance_id=request.tool_instance_id,
                command_id=request.command_id,
                ttl_ms=request.ttl_ms,
                arguments=encoded_arguments,
            )
        )
        await self._server.send(envelope, self._active_peer)
        return {"request_id": request_id, "command_id": request.command_id}

    @staticmethod
    def _encode_debug_argument(argument_id: str, definition: dict[str, Any], value: Any) -> wire.DebugCommandArgumentValue:
        value_type = str(definition.get("valueType", "")).lower()
        result = wire.DebugCommandArgumentValue(id=argument_id)
        if value_type == "boolean":
            if not isinstance(value, bool):
                raise ValueError(f"argument {argument_id} must be boolean")
            result.boolean_value = value
        elif value_type == "float64":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"argument {argument_id} must be numeric")
            result.float64_value = float(value)
        elif value_type == "int64":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"argument {argument_id} must be an integer")
            result.int64_value = value
        elif value_type == "string":
            if not isinstance(value, str):
                raise ValueError(f"argument {argument_id} must be a string")
            result.string_value = value
        elif value_type == "enum":
            if not isinstance(value, str):
                raise ValueError(f"argument {argument_id} must be an enum ID")
            allowed = {item.get("id") for item in definition.get("enumOptions", [])}
            if value not in allowed:
                raise ValueError(f"argument {argument_id} has an invalid enum value")
            result.enum_value = value
        else:
            raise ValueError(f"argument {argument_id} has unsupported type: {value_type or 'unspecified'}")
        return result

    def _debug_envelope(self, lease_id: str | None = None, **body: Any) -> wire.Envelope:
        if self._active_session_id is None or self._active_connection_id is None:
            raise RuntimeError("robot debug TCP connection is not active")
        state = self._peer_states.get(self._active_peer)
        if not state or state["protocol"] != 3 or not self._debug_fresh():
            raise RuntimeError("Select a fresh protocol v3 robot session; v2 is observation-only")
        stop = body.get("debug_command_request")
        harmless = ("debug_select_request" in body or "debug_run_ack" in body
                    or (stop is not None and stop.command_id in ("stop", "motor.stop", "benchmark.abort")))
        safety = self._debug_safety or {}
        arming = stop is not None and stop.command_id == "control.arm"
        if not harmless:
            self.require_debug_control(lease_id)
        if "debug_select_request" in body and self._debug_lease and time.monotonic()<self._debug_lease_deadline:
            self.require_debug_control(lease_id)
        if not harmless and not arming and not safety.get("armed"):
            raise RuntimeError("Click Arm selected motor before running the test")
        if not harmless and not arming and safety.get("controlLeaseId", "") == self._blocked_robot_lease:
            raise RuntimeError("Arm the tool again after acquiring this browser lease")
        envelope = wire.Envelope(
            protocol_version=3,
            control_lease_id=safety.get("controlLeaseId", ""),
            session_id=self._active_session_id,
            connection_id=self._active_connection_id,
            connection_sequence=self._debug_sequence,
            robot_elapsed_ns=0,
            **body,
        )
        self._debug_sequence += 1
        state["outgoing_sequence"] = self._debug_sequence
        return envelope

    def telemetry_state(self) -> dict[str, Any]:
        state = self._telemetry.live_state()
        state["capture"] = self.capture_status()
        return state

    def telemetry_status(self) -> dict[str, Any]:
        """Return the bounded telemetry session summary."""

        return self._telemetry.status()

    def telemetry_latest_state(self) -> dict[str, Any]:
        """Return bounded model-friendly telemetry without replay history."""

        state = self._telemetry.latest_state()
        state["capture"] = self.capture_status()
        return state

    def capture_status(self) -> dict[str, Any]:
        return {
            "enabled": self._capture_override_enabled,
            "sessionId": self._capture_override_session_id,
        }

    async def set_capture_override(self, enabled: bool) -> dict[str, Any]:
        owners = [state for state in self._peer_states.values()
                  if state["session_id"] == self._capture_override_session_id
                  and time.perf_counter_ns() - state["received_ns"] < 2_000_000_000]
        if len(owners) != 1:
            raise RuntimeError("Telemetry capture requires an active robot-data session")
        self._capture_override_enabled = enabled
        await self._broadcast_telemetry(TelemetryUpdate("telemetry_capture", self.capture_status()))
        return self.capture_status()

    async def select_capture_session(self, session_id: str) -> None:
        """Bind the shared camera only to the chosen session, never the last packet."""
        if session_id == self._capture_override_session_id:
            return
        previous = self._capture_override_session_id
        self._capture_override_enabled = False
        self._capture_override_session_id = None
        if previous is not None:
            await asyncio.to_thread(self._incident_recorder.close_session, previous)
            await self._stop_capture_for_session(previous)
        if session_id in self._benchmark_sessions:
            return
        self._capture_override_session_id = session_id
        try:
            if self._capture_start_callback is not None:
                await asyncio.to_thread(self._capture_start_callback, session_id)
            if self._session_bound_callback is not None:
                await asyncio.to_thread(self._session_bound_callback, session_id)
        except (CameraRecordingError, OSError):
            # Raw telemetry remains usable when the optional camera is unavailable.
            pass

    def recording_sessions(self) -> list[dict[str, object]]:
        debugger_ids = {r["runId"] for r in self._debug_results.list()}
        return [r for r in self._raw_recorder.list_sessions() if r["session_id"] not in debugger_ids]

    def recording_path(self, session_id: str, log_name: str) -> Path:
        return self._raw_recorder.log_path(session_id, log_name)

    @property
    def recordings_root(self) -> Path:
        """One root shared by telemetry, selected camera video, and uploads."""
        return self._raw_recorder.recordings_root

    async def _on_raw_packet(
        self,
        envelope: wire.Envelope,
        protobuf_frame: bytes,
        peer: tuple[str, int] | None,
        received_monotonic_ns: int,
    ) -> None:
        """Archive a parsed protobuf envelope before dashboard normalization."""

        if len(envelope.session_id) != 16:
            return
        session_id = str(uuid.UUID(bytes=envelope.session_id))
        if envelope.HasField("hello") and "debug-runs-v1" in envelope.hello.capabilities:
            self._benchmark_sessions.add(session_id)
        if session_id in self._benchmark_sessions:
            return  # Each benchmark is its own recording; no duplicate session/video popup.
        try:
            await asyncio.to_thread(self._raw_recorder.append, session_id, protobuf_frame, received_monotonic_ns)
            if peer is not None:
                self._connection_sessions[peer] = session_id
        except FtcLogError:
            # The receiver will still validate and expose its live state; the
            # raw recorder status retains the disk failure for the operator.
            pass

    async def _on_packet(self, packet: ReceivedRobotPacket) -> wire.Envelope | None:
        self._latest_packet = {
            "payload": dict(packet.payload),
            "peer": {"host": packet.peer[0], "port": packet.peer[1]} if packet.peer else None,
            "received_monotonic_ns": packet.received_monotonic_ns,
            "received_at_ms": int(time.time() * 1000),
        }
        peer_state = self._peer_states.get(packet.peer)
        if packet.envelope.HasField("hello"):
            peer_state = {"session_id": str(uuid.UUID(bytes=packet.envelope.session_id)),
                          "session_bytes": packet.envelope.session_id, "connection_bytes": packet.envelope.connection_id,
                          "protocol": packet.envelope.protocol_version, "robot_id": packet.envelope.hello.robot_id,
                          "opmode": packet.envelope.hello.op_mode_name}
            self._peer_states[packet.peer] = peer_state
        if peer_state is not None:
            peer_state["received_ns"] = packet.received_monotonic_ns
        dataset_ack = None
        if packet.envelope.WhichOneof("body") in ("debug_run_header", "debug_run_chunk", "debug_run_end"):
            try:
                dataset_ack = await asyncio.to_thread(self._debug_results.ingest, packet.envelope,
                                                     packet.protobuf_frame, packet.received_monotonic_ns)
            except (ValueError, OSError, FtcLogError) as error:
                run_id = getattr(packet.envelope, packet.envelope.WhichOneof("body")).run_id
                pending = self._debug_results.pending.get(run_id)
                if pending is not None: pending.update(state="incomplete", message=str(error))
        is_hello = packet.envelope.WhichOneof("body") == "hello"
        resumed = is_hello and self._telemetry.has_session(packet.payloads[0].get("sessionId"))
        session_id = packet.payloads[0].get("sessionId")
        updates: list[TelemetryUpdate] = []
        structured_accepted = True
        for payload in packet.payloads:
            effective_payload = self._effective_snapshot_highlight(payload)
            try:
                updates.extend(self._telemetry.ingest(
                    effective_payload,
                    received_monotonic_ns=packet.received_monotonic_ns,
                    received_at_ms=self._latest_packet["received_at_ms"],
                ))
            except TelemetryProtocolError:
                # Keep the decoded packet visible for diagnosis, but reject its
                # handshake so the RC reconnects instead of streaming bad data.
                structured_accepted = False
                break
            if packet.peer is not None:
                connection_id = payload.get("connectionId")
                if isinstance(connection_id, str):
                    self._connection_ids[packet.peer] = connection_id
        for update in updates:
            if update.kind == "telemetry_snapshot":
                try:
                    await asyncio.to_thread(self._incident_recorder.observe_snapshot, dict(update.data))
                except IncidentError:
                    # The raw stream remains authoritative and uploadable if the
                    # optional evidence index cannot be written.
                    pass
        for update in updates:
            current = self._telemetry.status().get("activeSession")
            if current and current["sessionId"] == session_id:
                await self._broadcast_telemetry(update)
        for payload in packet.payloads:
            if str(payload.get("type", "")).startswith("debug_"):
                if payload.get("type") in ("debug_run_header", "debug_run_chunk", "debug_run_end"):
                    continue
                data = dict(payload.get("data", {}))
                if peer_state is not None:
                    peer_state[str(payload.get("type"))] = data
                if packet.peer != self._active_peer or self._selected_session is None:
                    continue
                if payload.get("type") == "debug_run_status":
                    pending = self._debug_results.pending.get(data.get("runId"))
                    if pending is not None:
                        pending.update(data)
                if payload.get("type") == "debug_command_response" and data.get("commandId") == "benchmark.run":
                    pending = self._debug_results.pending.get(data.get("requestId"))
                    if pending is not None:
                        result = data.get("result")
                        pending.update(state="running" if result in ("debug_command_accepted", "debug_command_duplicate") else "rejected",
                                       message=data.get("message", ""))
                message = {"type": payload.get("type"), "data": data, "robotTimeNs": payload.get("robotTimeNs")}
                self._debug_last_event = message
                if payload.get("type") == "debug_manifest": self._debug_manifest = data
                elif payload.get("type") == "debug_tool_ready": self._debug_ready = data
                elif payload.get("type") == "debug_tool_state": self._debug_tool_state = data
                elif payload.get("type") == "debug_safety_state": self._debug_safety = data
                await self._broadcast_debug(message)
        # Debug manifest and readiness updates are part of the connection
        # status consumed by the workspace router. Publish only after they
        # have been applied so a newly connected Debugger OpMode routes to its
        # debugger page without waiting for a later telemetry packet.
        await self._broadcast_status()
        if structured_accepted and is_hello:
            if isinstance(session_id, str) and (
                self._selected_session == session_id or
                (self._selected_session is None and self._capture_override_session_id is None)
            ):
                await self.select_capture_session(session_id)
            return self._hello_ack(packet.envelope, resumed=resumed)
        if packet.envelope.HasField("heartbeat") and peer_state is not None:
            sequence = peer_state.get("outgoing_sequence", 1)
            peer_state["outgoing_sequence"] = sequence + 1
            if packet.peer == self._active_peer:
                self._debug_sequence = sequence + 1
            return wire.Envelope(protocol_version=packet.envelope.protocol_version,
                                 session_id=packet.envelope.session_id, connection_id=packet.envelope.connection_id,
                                 connection_sequence=sequence, heartbeat=wire.Heartbeat())
        if dataset_ack is not None:
            if peer_state is None:
                return None
            sequence = peer_state.get("outgoing_sequence", 1)
            peer_state["outgoing_sequence"] = sequence + 1
            if packet.peer == self._active_peer:
                self._debug_sequence = sequence + 1
            return wire.Envelope(protocol_version=packet.envelope.protocol_version,
                                 session_id=packet.envelope.session_id, connection_id=packet.envelope.connection_id,
                                 connection_sequence=sequence, debug_run_ack=dataset_ack)
        if any(payload.get("type") == "session_end" for payload in packet.payloads) and isinstance(session_id, str):
            await self._finalize_recording(session_id)
            await self._stop_capture_for_session(session_id)
        return None

    def _effective_snapshot_highlight(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Apply the laptop override without changing the archived RC protobuf."""

        if payload.get("type") != "sample":
            return dict(payload)
        data = dict(payload.get("data", {}))
        control_hub_highlighted = data.get("highlighted") is True
        overrides_this_session = (
            self._capture_override_enabled
            and payload.get("sessionId") == self._capture_override_session_id
        )
        highlighted = True if overrides_this_session else control_hub_highlighted
        data["controlHubHighlighted"] = control_hub_highlighted
        data["highlighted"] = highlighted
        data["highlightSource"] = "telemetry_lab" if overrides_this_session else (
            "control_hub" if highlighted else None
        )
        result = dict(payload)
        result["data"] = data
        return result

    @staticmethod
    def _hello_ack(request: wire.Envelope, *, resumed: bool) -> wire.Envelope:
        """Accept a structured session before it sends its catalog and samples."""

        return wire.Envelope(
            protocol_version=request.protocol_version,
            session_id=request.session_id,
            connection_id=request.connection_id,
            connection_sequence=0,
            robot_elapsed_ns=0,
            hello_ack=wire.HelloAck(
                accepted=True,
                server_time_ns=time.perf_counter_ns(),
                session_resumed=resumed,
                capabilities=["debug-runs-v1"],
            ),
        )

    async def _on_connection(self, connected: bool, peer: tuple[str, int] | None) -> None:
        if peer is not None:
            if connected:
                self._peers.add(peer)
            else:
                self._peers.discard(peer)
                departed = self._peer_states.pop(peer, None)
                if departed is not None:
                    self._benchmark_sessions.discard(departed["session_id"])
                if self._active_peer == peer:
                    self._selected_session = None
                    self._debug_results.disconnected()
                    self._active_peer = None
                    self._active_session_id = None
                    self._active_connection_id = None
                    self._debug_lease = None
                    self._debug_manifest = None
                    self._debug_ready = None
                    self._debug_tool_state = None
                    self._debug_safety = None
                    self._debug_last_event = None
                connection_id = self._connection_ids.pop(peer, None)
                session_id = self._connection_sessions.pop(peer, None)
                if session_id is not None:
                    await self._finalize_recording(session_id, complete=False)
                    await self._stop_capture_for_session(session_id)
                for update in self._telemetry.disconnect_connection(connection_id):
                    await self._broadcast_telemetry(update)
        await self._broadcast_status()

    async def _stop_capture_for_session(self, session_id: str) -> None:
        callback = self._capture_stop_callback
        if callback is None:
            return
        try:
            # Direct scrcpy may take a moment to close its MP4. Keep that work
            # off the robot-data server event loop while preserving the
            # wait-for-video upload ordering.
            await asyncio.to_thread(callback, session_id)
        except Exception:
            # Raw telemetry remains durable if a disconnected camera cannot be
            # stopped a second time.
            pass

    async def _finalize_recording(self, session_id: str, *, complete: bool = True) -> None:
        """Finalize telemetry, then wait for the operator's upload decision."""

        await asyncio.to_thread(self._incident_recorder.close_session, session_id)
        if session_id == self._capture_override_session_id:
            self._capture_override_enabled = False
            self._capture_override_session_id = None
        try:
            final_path = await asyncio.to_thread(self._raw_recorder.close_session, session_id, complete=complete)
        except FtcLogError:
            return
        if final_path is None or (final_path.name.endswith(".partial") and not is_closed_partial(final_path)):
            return
        self._raw_finalized.add(session_id)
        await self._prepare_upload_decision(session_id)

    async def _prepare_upload_decision(self, session_id: str) -> None:
        """Expose a finalized recording for an explicit upload or discard choice."""
        if session_id not in self._raw_finalized:
            return
        current = self._upload_states.get(session_id, {})
        if current.get("state") in {
            "uploading", "retrying", "complete", "deleting", "deleted", "discarded", "local_deleted",
        } or "metadata" in current:
            return
        if session_id in self._video_errors:
            self._upload_states[session_id] = {"state": "error", "detail": self._video_errors[session_id]}
            await self._broadcast_status()
            return
        if not self._video_ready_checker(session_id):
            self._upload_states[session_id] = {
                "state": "waiting_for_video",
                "detail": "Telemetry finalized; waiting for video finalization.",
            }
            await self._broadcast_status()
            return
        if self._recording_uploader is None:
            self._upload_states[session_id] = {
                "state": "error",
                "detail": "Recording upload is not configured on this laptop.",
            }
            await self._broadcast_status()
            return
        if current.get("state") == "ready_to_upload":
            return
        interrupted = any(self._raw_recorder.session_path(session_id).glob("raw/*.ftclog.partial"))
        self._upload_states[session_id] = {
            "state": "ready_to_upload",
            "detail": ("Recording was interrupted; captured data is preserved and can be uploaded. "
                       if interrupted else "Recording is ready. ")
                      + "Choose whether to upload it or discard it.",
        }
        await self._broadcast_status()

    async def upload_recording(
        self, session_id: str, *, metadata: RecordingMetadata, keep_local: bool = False
    ) -> dict[str, Any]:
        if self._recording_uploader is None:
            raise RecordingUploadError("Recording upload is not configured on this laptop")
        current = self._upload_states.get(session_id)
        if current is None or current.get("state") not in {"ready_to_upload", "error"}:
            raise RecordingUploadError("This recording is not ready to upload")
        if not self._video_ready_checker(session_id):
            raise RecordingUploadError("Video is still finalizing")
        recording_directory = self._raw_recorder.session_path(session_id)
        # A retry must transfer the same bytes as the original upload. The SFTP
        # uploader resumes staged files by size and may have already published the session.
        notes = current.get("metadata") or {"title": metadata.title, "description": metadata.description}
        self._upload_states[session_id] = {"state": "uploading", "detail": "Upload started"}
        await self._broadcast_status()
        loop = asyncio.get_running_loop()

        def progress(total_bytes: int, uploaded_bytes: int, current_file: str | None) -> None:
            loop.call_soon_threadsafe(
                self._record_upload_progress,
                session_id,
                total_bytes,
                uploaded_bytes,
                current_file,
            )

        async def upload() -> None:
            try:
                await asyncio.to_thread(atomic_json, recording_directory / RECORDING_METADATA_FILE, notes)
            except OSError as error:
                self._upload_states[session_id] = {
                    "state": "error", "detail": f"Could not save recording details: {error}", "metadata": notes,
                }
                await self._broadcast_status()
                return

            attempt = 0
            while True:
                try:
                    result = await asyncio.to_thread(
                        self._recording_uploader.upload,
                        recording_directory,
                        progress_callback=progress,
                    )
                    if not keep_local:
                        try:
                            await asyncio.to_thread(self._raw_recorder.delete_session, session_id)
                        except FtcLogError as error:
                            self._upload_states[session_id] = {
                                "state": "error",
                                "detail": f"Uploaded to server, but local cleanup failed: {error}",
                                "uploadedFiles": result.uploaded_files,
                                "skippedFiles": result.skipped_files,
                                "metadata": notes,
                            }
                        else:
                            self._upload_states[session_id] = {
                                "state": "complete",
                                "uploadedFiles": result.uploaded_files,
                                "skippedFiles": result.skipped_files,
                                "localCopyKept": False,
                            }
                    else:
                        self._upload_states[session_id] = {
                            "state": "complete",
                            "uploadedFiles": result.uploaded_files,
                            "skippedFiles": result.skipped_files,
                            "localCopyKept": True,
                        }
                    await self._broadcast_status()
                    return
                except (RecordingUploadError, OSError) as error:
                    if (attempt >= len(self._AUTO_UPLOAD_RETRY_DELAYS_SECONDS)
                            or not _is_transient_upload_error(error)):
                        # The finalized local session remains untouched, so a
                        # terminal failure can still be retried manually.
                        self._upload_states[session_id] = {"state": "error", "detail": str(error), "metadata": notes}
                        await self._broadcast_status()
                        return
                    delay = self._AUTO_UPLOAD_RETRY_DELAYS_SECONDS[attempt]
                    attempt += 1
                    self._upload_states[session_id] = {
                        "state": "retrying",
                        "detail": f"{error}; retrying in {delay:g} seconds ({attempt}/{len(self._AUTO_UPLOAD_RETRY_DELAYS_SECONDS)})",
                    }
                    await self._broadcast_status()
                    await asyncio.sleep(delay)
                    self._upload_states[session_id] = {"state": "uploading", "detail": f"Retry {attempt} started"}
                    await self._broadcast_status()

        asyncio.create_task(upload(), name=f"upload-recording-{session_id}")
        return {"session_id": session_id, "state": "uploading"}

    def _record_upload_progress(
        self,
        session_id: str,
        total_bytes: int,
        uploaded_bytes: int,
        current_file: str | None,
    ) -> None:
        """Relay byte-level SFTP progress from its worker thread to the UI."""

        state = self._upload_states.get(session_id)
        if state is None or state.get("state") != "uploading":
            return
        state.update({
            "totalBytes": total_bytes,
            "uploadedBytes": uploaded_bytes,
            "currentFile": current_file,
            "detail": "Uploading recording" if current_file is None else f"Uploading {current_file}",
        })
        asyncio.create_task(self._broadcast_status())

    async def delete_recording(self, session_id: str) -> dict[str, Any]:
        current = self._upload_states.get(session_id)
        if current is None or current.get("state") not in {"waiting_for_video", "error", "complete"}:
            raise FtcLogError("This recording cannot be deleted in its current state")
        self._upload_states[session_id] = {"state": "deleting"}
        try:
            if self._recording_uploader is not None:
                await asyncio.to_thread(self._recording_uploader.delete, session_id)
            await asyncio.to_thread(self._raw_recorder.delete_session, session_id)
        except Exception:
            self._upload_states[session_id] = current
            raise
        self._upload_states[session_id] = {"state": "deleted", "detail": "Recording deleted locally"}
        await self._broadcast_status()
        return {"session_id": session_id, "state": "deleted"}

    async def discard_recording(self, session_id: str) -> dict[str, Any]:
        """Delete a finalized local recording before it has been uploaded."""

        current = self._upload_states.get(session_id)
        if current is None or current.get("state") not in {"ready_to_upload", "error"}:
            raise FtcLogError("This recording cannot be discarded in its current state")
        self._upload_states[session_id] = {"state": "deleting"}
        try:
            await asyncio.to_thread(self._raw_recorder.delete_session, session_id)
        except Exception:
            self._upload_states[session_id] = current
            raise
        self._upload_states[session_id] = {"state": "discarded", "detail": "Recording discarded from this laptop"}
        await self._broadcast_status()
        return {"session_id": session_id, "state": "discarded"}

    async def delete_local_recording(self, session_id: str) -> dict[str, Any]:
        current = self._upload_states.get(session_id)
        if current is None or current.get("state") != "complete":
            raise FtcLogError("Only a completed upload can have its local copy deleted")
        await asyncio.to_thread(self._raw_recorder.delete_session, session_id)
        self._upload_states[session_id] = {"state": "local_deleted", "detail": "Uploaded session removed from this laptop"}
        await self._broadcast_status()
        return {"session_id": session_id, "state": "local_deleted"}

    async def _broadcast_status(self) -> None:
        self._browser_broadcast.publish(self._sockets, {"kind": "robot_data_status", "data": self.status()})

    async def _broadcast_telemetry(self, update: TelemetryUpdate) -> None:
        self._browser_broadcast.publish(self._telemetry_sockets, {"kind": update.kind, "data": update.data})

    async def _broadcast_debug(self, message: dict[str, Any]) -> None:
        self._browser_broadcast.publish(self._debug_sockets, {"kind": "debug", "data": message})


service = DriverStationService()
robot_data_service = RobotDataService()
# A capture must join the raw `.ftclog` tree before upload.  Keep the legacy
# standalone webcam service in this same root too, rather than letting a
# separate video environment variable split a telemetry session in two.
_video_recordings_root = robot_data_service.recordings_root.resolve()
video_recorder = VideoRecorder(_video_recordings_root)
_camera_staging_root = _video_recordings_root / ".camera-staging"
adb_camera_service = AdbCameraService(_camera_staging_root)
camera_coordinator = CameraRecordingCoordinator(
    _video_recordings_root,
    adb_camera_service,
    on_video_finalized=robot_data_service.video_finalized_from_thread,
)
adb_camera_service.set_finalized_callback(camera_coordinator.notify_adb_finalized)
robot_data_service.set_capture_callbacks(
    video_ready_checker=camera_coordinator.video_ready_for,
    session_bound=camera_coordinator.bind_telemetry_session,
    capture_stop=camera_coordinator.stop_recording_for_session,
    capture_start=camera_coordinator.ensure_recording,
)
app = FastAPI(title="FTC Local Driver Station", docs_url=None, redoc_url=None)
install_debugger_controls(app, robot_data_service)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup() -> None:
    service.set_event_loop(asyncio.get_running_loop())
    await robot_data_service.start()
    adb_camera_service.start()


@app.on_event("shutdown")
async def shutdown() -> None:
    try:
        video_recorder.stop()
    except VideoRecorderError:
        pass
    await robot_data_service.close()
    service.disconnect(stop=True)
    camera_coordinator.close()
    adb_camera_service.close()


def _http_error(error: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(error))


def _validate_gamepad_user(user: int) -> None:
    if user not in (1, 2):
        raise HTTPException(status_code=422, detail="Gamepad user must be 1 or 2")


@app.get("/api/status")
def get_status() -> dict[str, Any]:
    return service.status()


def _is_transient_upload_error(error: RecordingUploadError | OSError) -> bool:
    """Return whether a retained recording should get an automatic retry."""

    detail = str(error).lower()
    return any(marker in detail for marker in (
        "device disconnected",
        "connection reset",
        "connection refused",
        "connection timed out",
        "timed out",
        "eof during negotiation",
        "no existing session",
    ))


@app.get("/api/data/status")
def get_robot_data_status() -> dict[str, Any]:
    return robot_data_service.status()


@app.get("/api/debug/status")
def get_debug_status() -> dict[str, Any]:
    return robot_data_service.debug_status()


@app.post("/api/debug/target")
async def select_debug_target(request: DebugTargetRequest):
    try:
        robot_data_service.select_debug_target(request.session_id)
        await robot_data_service.select_capture_session(request.session_id)
        await robot_data_service._broadcast_telemetry(TelemetryUpdate("telemetry_state", robot_data_service.telemetry_state()))
        return robot_data_service.debug_status()
    except ValueError as error:
        raise HTTPException(409, str(error)) from error


@app.post("/api/debug/control/acquire")
def acquire_debug_control():
    try:
        return {"lease_id": robot_data_service.acquire_debug_control()}
    except RuntimeError as error:
        raise HTTPException(409, str(error)) from error


@app.post("/api/debug/control/renew")
async def renew_debug_control(request: ControlLeaseRequest):
    try:
        await robot_data_service.renew_debug_control(request.lease_id)
        return {"ok": True}
    except (RuntimeError, OSError) as error:
        raise HTTPException(409, str(error)) from error


@app.post("/api/debug/control/arm")
async def arm_debug_control(request: ControlLeaseRequest):
    try:
        await robot_data_service.arm_debug_control(request.lease_id)
        return {"ok": True}
    except (RuntimeError, OSError) as error:
        raise HTTPException(409, str(error)) from error


@app.post("/api/debug/control/release")
def release_debug_control(request: ControlLeaseRequest):
    if robot_data_service._debug_lease == request.lease_id:
        robot_data_service._debug_lease = None
    return {"ok": True}


@app.post("/api/debug/select")
async def select_debug_tool(request: DebugSelectHttpRequest) -> dict[str, str]:
    try:
        return await robot_data_service.send_debug_select(request)
    except Exception as error:
        raise _http_error(error) from error


@app.post("/api/debug/parameters")
async def set_debug_parameter(request: DebugParameterHttpRequest) -> dict[str, str]:
    try:
        return await robot_data_service.send_debug_parameter(request)
    except Exception as error:
        raise _http_error(error) from error


@app.post("/api/debug/commands")
async def send_debug_command(request: DebugCommandHttpRequest) -> dict[str, str]:
    try:
        return await robot_data_service.send_debug_command(request)
    except Exception as error:
        raise _http_error(error) from error


@app.get("/api/data/telemetry")
def get_live_telemetry_state() -> dict[str, Any]:
    return robot_data_service.telemetry_state()


@app.get("/api/data/telemetry/status")
def get_telemetry_status() -> dict[str, Any]:
    return robot_data_service.telemetry_status()


@app.get("/api/data/telemetry/latest")
def get_latest_telemetry_state() -> dict[str, Any]:
    """Return the active session, catalog, latest snapshot, and recent events."""

    return robot_data_service.telemetry_latest_state()


@app.post("/api/data/telemetry/capture")
async def set_telemetry_capture(request: TelemetryCaptureRequest) -> dict[str, Any]:
    try:
        return await robot_data_service.set_capture_override(request.enabled)
    except Exception as error:
        raise _http_error(error) from error


@app.get("/api/data/recordings")
def list_robot_data_recordings() -> list[dict[str, object]]:
    """List durable raw stream sessions; the live replay history is separate."""

    return robot_data_service.recording_sessions()


@app.get("/api/data/recordings/{session_id}/{log_name}")
def download_robot_data_recording(session_id: str, log_name: str) -> FileResponse:
    """Download one finalized raw .ftclog without exposing arbitrary paths."""

    try:
        log_path = robot_data_service.recording_path(session_id, log_name)
    except FtcLogError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return FileResponse(log_path, media_type="application/octet-stream", filename=log_name)


@app.post("/api/data/recordings/{session_id}/upload")
async def upload_robot_data_recording(session_id: str, request: RecordingUploadRequest) -> dict[str, Any]:
    try:
        return await robot_data_service.upload_recording(
            session_id, metadata=request, keep_local=request.keep_local
        )
    except (RecordingUploadError, FtcLogError) as error:
        raise _http_error(error) from error


@app.delete("/api/data/recordings/{session_id}")
async def delete_robot_data_recording(session_id: str) -> dict[str, Any]:
    try:
        return await robot_data_service.delete_recording(session_id)
    except FtcLogError as error:
        raise _http_error(error) from error


@app.delete("/api/data/recordings/{session_id}/discard")
async def discard_robot_data_recording(session_id: str) -> dict[str, Any]:
    try:
        return await robot_data_service.discard_recording(session_id)
    except FtcLogError as error:
        raise _http_error(error) from error


@app.delete("/api/data/recordings/{session_id}/local")
async def delete_local_robot_data_recording(session_id: str) -> dict[str, Any]:
    try:
        return await robot_data_service.delete_local_recording(session_id)
    except FtcLogError as error:
        raise _http_error(error) from error


@app.get("/api/video/status")
def get_video_status() -> dict[str, Any]:
    """Return state for the single local camera recorder."""

    return video_recorder.status()


@app.get("/api/adb-camera/status")
def get_adb_camera_status() -> dict[str, Any]:
    return adb_camera_service.status()


@app.post("/api/adb-camera/refresh")
def refresh_adb_devices() -> dict[str, Any]:
    return adb_camera_service.refresh_devices()


@app.put("/api/adb-camera/devices/{serial}/role")
def assign_adb_device_role(serial: str, request: AdbDeviceRoleRequest) -> dict[str, Any]:
    try:
        return adb_camera_service.assign_role(serial, request.role)
    except AdbCameraError as error:
        raise _http_error(error) from error


@app.post("/api/adb-camera/stop")
def stop_adb_camera_recording() -> dict[str, Any]:
    try:
        return adb_camera_service.stop_recording()
    except AdbCameraError as error:
        raise _http_error(error) from error


@app.post("/api/adb-camera/delete-phone-copy")
def delete_adb_camera_phone_copy() -> dict[str, Any]:
    try:
        return adb_camera_service.delete_phone_copy()
    except AdbCameraError as error:
        raise _http_error(error) from error


@app.get("/api/camera/status")
def camera_status() -> dict[str, Any]:
    """Unified dashboard capture status; legacy ADB routes remain available."""
    return {"capture": camera_coordinator.status(), "adb": adb_camera_service.status()}


@app.put("/api/camera/config")
def update_camera_config(request: CameraCaptureConfigRequest) -> dict[str, Any]:
    try:
        return {"capture": camera_coordinator.update_config(
            mode=request.mode, direct=request.direct.model_dump(),
        ), "adb": adb_camera_service.status()}
    except CameraRecordingError as error:
        raise _http_error(error) from error


@app.post("/api/camera/preview/stop")
def stop_camera_preview() -> dict[str, Any]:
    try:
        return {"capture": camera_coordinator.stop_preview()}
    except CameraRecordingError as error:
        raise _http_error(error) from error


@app.get("/api/camera/preview/frame")
def camera_preview_frame() -> Response:
    frame = camera_coordinator.preview_frame()
    if frame is None:
        return Response(status_code=204)
    return Response(frame, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.post("/api/video/start")
def start_video_recording(request: VideoRecordingRequest) -> dict[str, Any]:
    """Start a segmented recording from a laptop-attached webcam."""

    try:
        return video_recorder.start(CameraRecordingConfig(**request.model_dump()))
    except VideoRecorderError as error:
        raise _http_error(error) from error


@app.post("/api/video/stop")
def stop_video_recording() -> dict[str, Any]:
    try:
        return video_recorder.stop()
    except VideoRecorderError as error:
        raise _http_error(error) from error


@app.get("/api/video/sessions")
def list_video_sessions() -> list[dict[str, Any]]:
    return video_recorder.list_sessions()


@app.get("/api/video/sessions/{session_id}/segments/{segment_name}")
def get_video_segment(session_id: str, segment_name: str) -> FileResponse:
    """Serve a finalized MP4 segment without exposing arbitrary local paths."""

    if not re.fullmatch(r"segment-\d{5}\.mp4", segment_name):
        raise HTTPException(status_code=404, detail="Video segment was not found")
    try:
        video_path = video_recorder.session_directory(session_id) / segment_name
    except VideoRecorderError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    if not video_path.is_file():
        raise HTTPException(status_code=404, detail="Video segment was not found")
    return FileResponse(video_path, media_type="video/mp4", filename=segment_name)


@app.post("/api/connect")
def connect(request: ConnectRequest) -> dict[str, Any]:
    try:
        return service.connect(request)
    except (ControlHubError, OSError) as error:
        raise _http_error(error) from error


@app.post("/api/disconnect")
def disconnect() -> dict[str, Any]:
    return service.disconnect(stop=True)


@app.get("/api/opmodes")
def list_opmodes() -> tuple[dict[str, object], ...]:
    try:
        return service.list_opmodes()
    except ControlHubError as error:
        raise _http_error(error) from error


@app.get("/api/configurations")
def list_configurations() -> tuple[dict[str, object], ...]:
    try:
        return service.list_configurations()
    except (ControlHubError, ValueError) as error:
        raise _http_error(error) from error


@app.get("/api/configurations/active")
def active_configuration() -> dict[str, object]:
    try:
        return service.active_configuration()
    except (ControlHubError, ValueError) as error:
        raise _http_error(error) from error


@app.get("/api/ping")
def ping() -> dict[str, object]:
    try:
        return service.ping()
    except ControlHubError as error:
        raise _http_error(error) from error


@app.get("/api/configurations/{name}/xml")
def read_configuration_xml(name: str) -> dict[str, str]:
    try:
        return service.read_configuration_xml(name)
    except (ControlHubError, ValueError) as error:
        raise _http_error(error) from error


@app.put("/api/configurations")
def save_configuration(request: ConfigurationSaveRequest) -> dict[str, object]:
    try:
        return service.save_configuration(request)
    except (ControlHubError, ValueError) as error:
        raise _http_error(error) from error


@app.post("/api/opmodes/init")
def init_opmode(request: OpModeRequest) -> dict[str, Any]:
    try:
        result = service.init_opmode(request)
        try:
            # Capture begins at the same lifecycle point as the OpMode. If a
            # camera is not assigned, telemetry and robot Init still succeed.
            camera_coordinator.ensure_recording()
        except CameraRecordingError:
            pass
        return result
    except ControlHubError as error:
        raise _http_error(error) from error


@app.post("/api/opmodes/start")
def start_opmode(request: OpModeRequest) -> dict[str, Any]:
    try:
        return service.start_opmode(request)
    except ControlHubError as error:
        raise _http_error(error) from error


@app.post("/api/opmodes/launch")
def launch_opmode(request: OpModeRequest) -> dict[str, Any]:
    """Stop, initialize, and start one Robot Controller-advertised OpMode."""

    try:
        return service.launch_opmode(request)
    except ControlHubError as error:
        raise _http_error(error) from error


@app.post("/api/debug/launch")
def launch_debugger(request: OpModeRequest) -> dict[str, Any]:
    if request.name != "FTC Advanced Debugger":
        raise HTTPException(400, "Select FTC Advanced Debugger")
    try:
        return service.launch_opmode(request)
    except ControlHubError as error:
        raise _http_error(error) from error


@app.post("/api/opmodes/stop")
def stop_opmode() -> dict[str, Any]:
    try:
        return service.stop_opmode()
    except ControlHubError as error:
        raise _http_error(error) from error


@app.post("/api/control/acquire")
def acquire_control() -> dict[str, str]:
    try:
        return {"lease_id": service._require_client().acquire_control()}
    except ControlHubError as error:
        raise _http_error(error) from error


@app.post("/api/control/renew", status_code=204)
def renew_control(request: ControlLeaseRequest) -> None:
    try:
        service._require_client().renew_control(request.lease_id)
    except ControlHubError as error:
        raise _http_error(error) from error


@app.post("/api/control/release", status_code=204)
def release_control() -> None:
    if service._client is not None:
        service._client.release_control()


@app.put("/api/gamepads/{user}", status_code=204)
def set_gamepad(user: int, state: GamepadState) -> None:
    _validate_gamepad_user(user)
    try:
        service.set_gamepad(user, state)
    except ControlHubError as error:
        raise _http_error(error) from error


@app.post("/api/gamepads/{user}/clear", status_code=204)
def clear_gamepad(user: int) -> None:
    _validate_gamepad_user(user)
    try:
        service.clear_gamepad(user)
    except ControlHubError as error:
        raise _http_error(error) from error


@app.post("/api/gamepads/switch", status_code=204)
def switch_gamepad(request: SwitchGamepadRequest) -> None:
    try:
        service.switch_gamepad(request)
    except ControlHubError as error:
        raise _http_error(error) from error


@app.websocket("/ws")
async def websocket(socket: WebSocket) -> None:
    await service.add_socket(socket)
    try:
        while True:
            await socket.receive_text()
    except WebSocketDisconnect:
        service.remove_socket(socket)


@app.websocket("/ws/data")
async def robot_data_websocket(socket: WebSocket) -> None:
    await robot_data_service.add_socket(socket)
    try:
        while True:
            await socket.receive_text()
    except WebSocketDisconnect:
        robot_data_service.remove_socket(socket)


@app.websocket("/ws/telemetry")
async def telemetry_websocket(socket: WebSocket) -> None:
    await robot_data_service.add_telemetry_socket(socket)
    try:
        while True:
            await socket.receive_text()
    except WebSocketDisconnect:
        robot_data_service.remove_telemetry_socket(socket)


@app.websocket("/ws/debug")
async def debug_websocket(socket: WebSocket) -> None:
    await robot_data_service.add_debug_socket(socket)
    try:
        while True:
            await socket.receive_text()
    except WebSocketDisconnect:
        robot_data_service.remove_debug_socket(socket)


frontend_dist = Path(__file__).resolve().parents[1] / "frontend" / "dist"
if frontend_dist.is_dir():
    app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="dashboard")
