"""Loopback-only API for the FTC web Driver Station dashboard.

Robocol timing stays in :class:`ftc_control_hub.ControlHubClient`'s background
thread. The web browser is only a local UI: it submits desired input states and
receives telemetry/status events over a WebSocket.
"""

from __future__ import annotations

import asyncio
import re
import threading
import xml.etree.ElementTree as ElementTree
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ftc_control_hub import (
    ControlHubClient,
    ControlHubConfig,
    ControlHubError,
    GamepadInput,
    Packet,
    RobotState,
    decode_telemetry,
)


class ConnectRequest(BaseModel):
    host: str = Field(min_length=1)
    local_address: str | None = None
    local_port: int = Field(default=20884, ge=1, le=65535)
    timezone_id: str | None = None
    timeout_s: float = Field(default=5.0, gt=0, le=30)


class GamepadState(BaseModel):
    left_stick_x: float = Field(default=0.0, ge=-1, le=1)
    left_stick_y: float = Field(default=0.0, ge=-1, le=1)
    right_stick_x: float = Field(default=0.0, ge=-1, le=1)
    right_stick_y: float = Field(default=0.0, ge=-1, le=1)
    left_trigger: float = Field(default=0.0, ge=0, le=1)
    right_trigger: float = Field(default=0.0, ge=0, le=1)
    buttons: int = Field(default=0, ge=0, le=0x3FFFF)


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
        self._started_opmode = False

    def set_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def status(self) -> dict[str, Any]:
        with self._lock:
            client = self._client
            return {
                "connected": bool(client and client.is_connected),
                "host": client.config.host if client else None,
                "robot_state": (client.robot_state.name if client else RobotState.UNKNOWN.name),
                "started_opmode": self._started_opmode,
                "telemetry": self._last_telemetry,
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
            self._client = client
        try:
            client.connect(timeout_s=request.timeout_s)
        except Exception:
            with self._lock:
                if self._client is client:
                    self._client = None
            client.close()
            raise
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
        self._publish_from_thread("status", self.status())
        return self.status()

    def start_opmode(self, request: OpModeRequest) -> dict[str, Any]:
        self._require_client().start_opmode(request.name, request.timeout_s)
        self._started_opmode = True
        self._publish_from_thread("status", self.status())
        return self.status()

    def stop_opmode(self) -> dict[str, Any]:
        client = self._require_client()
        client.clear_gamepad_input(1)
        client.clear_gamepad_input(2)
        client.stop_opmode()
        self._started_opmode = False
        self._publish_from_thread("status", self.status())
        return self.status()

    def set_gamepad(self, user: int, state: GamepadState) -> None:
        client = self._require_client()
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
            )
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
        self._last_telemetry = None

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


service = DriverStationService()
app = FastAPI(title="FTC Local Driver Station", docs_url=None, redoc_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup() -> None:
    service.set_event_loop(asyncio.get_running_loop())


@app.on_event("shutdown")
async def shutdown() -> None:
    service.disconnect(stop=True)


def _http_error(error: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(error))


def _validate_gamepad_user(user: int) -> None:
    if user not in (1, 2):
        raise HTTPException(status_code=422, detail="Gamepad user must be 1 or 2")


@app.get("/api/status")
def get_status() -> dict[str, Any]:
    return service.status()


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
        return service.init_opmode(request)
    except ControlHubError as error:
        raise _http_error(error) from error


@app.post("/api/opmodes/start")
def start_opmode(request: OpModeRequest) -> dict[str, Any]:
    try:
        return service.start_opmode(request)
    except ControlHubError as error:
        raise _http_error(error) from error


@app.post("/api/opmodes/stop")
def stop_opmode() -> dict[str, Any]:
    try:
        return service.stop_opmode()
    except ControlHubError as error:
        raise _http_error(error) from error


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


frontend_dist = Path(__file__).resolve().parents[1] / "frontend" / "dist"
if frontend_dist.is_dir():
    app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="dashboard")
