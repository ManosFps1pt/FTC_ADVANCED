"""Minimal, reusable FTC Robocol connection session.

This module implements the connection layer described in
``FTC_DRIVER_STATION_PROTOCOL_RESEARCH.md``: peer discovery, the
Driver-Station heartbeat, OpMode lifecycle commands, normalized gamepad
packets, and guarded Robot Controller configuration-file transfer. It
deliberately does *not* implement physical-controller discovery or dangerous
maintenance operations such as hub-address and firmware changes.

It also includes a USB ADB adapter for the separate deployment and diagnostic
path. ADB USB cannot transport the UDP Robocol session; use Wi-Fi for Robocol
or provide a deliberately installed Android UDP-to-TCP relay.

Use it only on a controlled test robot.  It is not a replacement for the
official FTC Driver Station at an event.
"""

from __future__ import annotations

import argparse
import ctypes
import enum
import json
import logging
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Final, TextIO


LOG = logging.getLogger(__name__)

ROBOCOL_PORT: Final = 20884
ROBOCOL_VERSION: Final = 124
_NORMAL_HEADER = struct.Struct("!BHH")
_PEER_DISCOVERY = struct.Struct("!BHBBH B H B B B")
_GAMEPAD_PAYLOAD = struct.Struct("!BiqffffffIBBBffff")


class RobocolMessageType(enum.IntEnum):
    """Robocol packet type values used by this connection layer."""

    HEARTBEAT = 1
    GAMEPAD = 2
    PEER_DISCOVERY = 3
    COMMAND = 4
    TELEMETRY = 5


class PeerType(enum.IntEnum):
    """Peer-discovery result values relevant to a desktop Driver Station."""

    PEER = 1
    REJECTED_EXISTING_CONNECTION = 3


class RobotState(enum.IntEnum):
    """Robot states sent in heartbeat and telemetry payloads."""

    UNKNOWN = -1
    NOT_STARTED = 0
    INIT = 1
    RUNNING = 2
    STOPPED = 3
    EMERGENCY_STOP = 4


class GamepadButton(enum.IntFlag):
    """FTC SDK gamepad-v5 button-bit values.

    These values are deliberately independent of any operating-system
    controller API. A higher-level input provider converts its raw buttons
    into this representation before calling :meth:`ControlHubClient.set_gamepad_input`.
    """

    RIGHT_BUMPER = 0x00001
    LEFT_BUMPER = 0x00002
    BACK = 0x00004
    START = 0x00008
    GUIDE = 0x00010
    Y = 0x00020
    X = 0x00040
    B = 0x00080
    A = 0x00100
    DPAD_RIGHT = 0x00200
    DPAD_LEFT = 0x00400
    DPAD_DOWN = 0x00800
    DPAD_UP = 0x01000
    RIGHT_STICK_BUTTON = 0x02000
    LEFT_STICK_BUTTON = 0x04000
    TOUCHPAD = 0x08000
    TOUCHPAD_FINGER_2 = 0x10000
    TOUCHPAD_FINGER_1 = 0x20000


class ConnectionMode(enum.StrEnum):
    """Supported host-side connections.

    ``WIFI`` establishes the actual Robocol Driver-Station session.
    ``ADB_USB`` establishes only an Android Debug Bridge management session.
    """

    WIFI = "wifi"
    ADB_USB = "adb-usb"


class ControlHubError(RuntimeError):
    """Base exception raised by :class:`ControlHubClient`."""


class ConnectionTimeout(ControlHubError):
    """Raised when the RC does not accept peer discovery before the deadline."""


class PeerRejected(ControlHubError):
    """Raised when the RC already has another Driver Station peer."""


class ProtocolVersionMismatch(ControlHubError):
    """Raised when the RC advertises a Robocol version this client cannot use."""


class AdbError(ControlHubError):
    """Base error for the separate USB Android Debug Bridge adapter."""


class AdbNotFound(AdbError):
    """Raised when Android platform-tools' ``adb`` executable is unavailable."""


class AdbDeviceNotFound(AdbError):
    """Raised when no authorized USB Android device is available."""


class MultipleAdbDevices(AdbError):
    """Raised when a USB serial must be selected explicitly."""


class UsbRobocolRelayRequired(AdbError):
    """Raised if code tries to use standard ADB USB as a Robocol transport."""


class CommandTimeout(ControlHubError):
    """Raised when an RC command is not acknowledged before its deadline."""


@dataclass(frozen=True, slots=True)
class SdkMetadata:
    """SDK fields advertised in the historical peer-discovery packet."""

    release_month: int = 7
    release_year: int = 2026
    major: int = 11
    minor: int = 0


@dataclass(frozen=True, slots=True)
class ControlHubConfig:
    """Network and timing options for a single Robot Controller session.

    ``host`` is normally ``192.168.43.1`` for a REV Control Hub and normally
    ``192.168.49.1`` for a phone Robot Controller acting as Wi-Fi Direct group
    owner.  Pass the current gateway/RC address explicitly when in doubt.
    """

    host: str
    port: int = ROBOCOL_PORT
    local_port: int = ROBOCOL_PORT
    local_address: str | None = None
    discovery_interval_s: float = 1.0
    heartbeat_interval_s: float = 0.1
    receive_timeout_s: float = 0.3
    timezone_id: str | None = None
    sdk: SdkMetadata = SdkMetadata()


@dataclass(frozen=True, slots=True)
class AdbUsbConfig:
    """Settings for a physical USB ADB connection.

    ``serial`` is needed only when more than one USB Android device is
    authorized. Network-form serials such as ``192.168.43.1:5555`` are rejected
    because this adapter is deliberately for a wired ADB connection.
    """

    adb_path: str | None = None
    serial: str | None = None
    command_timeout_s: float = 5.0


@dataclass(frozen=True, slots=True)
class AdbDevice:
    """Read-only identity details for an authorized USB Android device."""

    serial: str
    manufacturer: str
    model: str
    android_version: str


@dataclass(frozen=True, slots=True)
class Packet:
    """A received Robocol packet after basic framing validation."""

    message_type: int
    sequence: int | None
    payload: bytes
    received_monotonic_ns: int


@dataclass(frozen=True, slots=True)
class Command:
    """A decoded reliable Robocol command packet."""

    name: str
    extra: bytes
    timestamp_ns: int
    acknowledged: bool
    sequence: int | None


@dataclass(slots=True)
class _PendingCommand:
    """Internal retry state for one outbound reliable command."""

    command: Command
    acknowledged_event: threading.Event = field(default_factory=threading.Event)
    attempts: int = 0
    next_attempt_at: float = 0.0


@dataclass(frozen=True, slots=True)
class TelemetryPacket:
    """A decoded FTC Robocol telemetry packet.

    Entries remain strings or native floats as they are on the wire.  This
    avoids treating a formatted telemetry value such as ``"12.0 V"`` as a
    number when the Robot Controller deliberately sent it as text.
    """

    timestamp_ms: int
    sorted: bool
    robot_state: RobotState
    tag: str
    strings: tuple[tuple[str, str], ...]
    numbers: tuple[tuple[str, float], ...]
    sequence: int | None
    received_monotonic_ns: int


@dataclass(frozen=True, slots=True)
class GamepadInput:
    """A normalized FTC gamepad-v5 input packet for driver slot 1 or 2.

    This is the boundary between a physical controller/UI and Robocol.  It
    intentionally has no dependency on SDL, HID, pygame, or CustomTkinter.
    Axis values use the FTC convention of ``-1.0`` through ``1.0`` and trigger
    values use ``0.0`` through ``1.0``.  ``timestamp_ms`` is a local monotonic
    clock value that is refreshed whenever a new input state is supplied.
    """

    user: int = 1
    device_id: int = -2
    timestamp_ms: int = field(default_factory=lambda: time.monotonic_ns() // 1_000_000)
    left_stick_x: float = 0.0
    left_stick_y: float = 0.0
    right_stick_x: float = 0.0
    right_stick_y: float = 0.0
    left_trigger: float = 0.0
    right_trigger: float = 0.0
    buttons: GamepadButton = GamepadButton(0)
    legacy_type: int = 0
    gamepad_type: int = 0
    touchpad_finger_1_x: float = 0.0
    touchpad_finger_1_y: float = 0.0
    touchpad_finger_2_x: float = 0.0
    touchpad_finger_2_y: float = 0.0

    def __post_init__(self) -> None:
        if self.user not in (1, 2):
            raise ValueError("gamepad user must be driver slot 1 or 2")
        if not -(2**31) <= self.device_id < 2**31:
            raise ValueError("device_id must fit in a signed int32")
        if not -(2**63) <= self.timestamp_ms < 2**63:
            raise ValueError("timestamp_ms must fit in a signed int64")
        if not 0 <= int(self.buttons) <= 0xFFFFFFFF:
            raise ValueError("buttons must fit in an unsigned int32")
        if not 0 <= self.legacy_type <= 0xFF or not 0 <= self.gamepad_type <= 0xFF:
            raise ValueError("gamepad type values must fit in an unsigned byte")
        axes = (self.left_stick_x, self.left_stick_y, self.right_stick_x, self.right_stick_y)
        if any(not -1.0 <= axis <= 1.0 for axis in axes):
            raise ValueError("stick axes must be between -1.0 and 1.0")
        triggers = (self.left_trigger, self.right_trigger)
        if any(not 0.0 <= trigger <= 1.0 for trigger in triggers):
            raise ValueError("triggers must be between 0.0 and 1.0")
        touchpad_axes = (
            self.touchpad_finger_1_x,
            self.touchpad_finger_1_y,
            self.touchpad_finger_2_x,
            self.touchpad_finger_2_y,
        )
        if any(not -1.0 <= axis <= 1.0 for axis in touchpad_axes):
            raise ValueError("touchpad coordinates must be between -1.0 and 1.0")

    @property
    def is_at_rest(self) -> bool:
        """Whether this state cannot command motion or a pressed button."""
        return (
            self.left_stick_x == 0.0
            and self.left_stick_y == 0.0
            and self.right_stick_x == 0.0
            and self.right_stick_y == 0.0
            and self.left_trigger == 0.0
            and self.right_trigger == 0.0
            and not self.buttons
        )

    @classmethod
    def neutral(cls, user: int = 1) -> "GamepadInput":
        """Return a synthetic released state suitable for clearing an RC slot."""
        return cls(user=user, device_id=-2)


@dataclass(slots=True)
class _GamepadSlot:
    """Internal scheduling state for one assigned gamepad."""

    input: GamepadInput
    updated_at: float
    force_send: bool = True


class TelemetryTerminal:
    """Render the latest telemetry packet in a fixed terminal region.

    Each frame moves the cursor back over the prior frame, clears its lines,
    and redraws them.  It has the same no-scroll behavior as ``tqdm`` while
    keeping the terminal dependency-free.
    """

    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream or sys.stdout
        self._line_count = 0
        self._lock = threading.Lock()

    def render(self, telemetry: TelemetryPacket) -> None:
        lines = self._format_lines(telemetry)
        with self._lock:
            if self._line_count:
                self._stream.write(f"\x1b[{self._line_count}A")
            for line_number in range(max(self._line_count, len(lines))):
                line = lines[line_number] if line_number < len(lines) else ""
                self._stream.write(f"\r\x1b[2K{line}\n")
            self._stream.flush()
            self._line_count = len(lines)

    @staticmethod
    def _format_lines(telemetry: TelemetryPacket) -> list[str]:
        header = (
            f"Telemetry  state={telemetry.robot_state.name}  "
            f"tag={telemetry.tag}  sequence={telemetry.sequence}"
        )
        entries: list[tuple[str, str]] = [*telemetry.strings]
        entries.extend((key, f"{value:g}") for key, value in telemetry.numbers)
        if telemetry.sorted:
            entries.sort(key=lambda entry: entry[0])
        if not entries:
            return [header, "  (no telemetry entries)"]
        return [header, *(f"  {key}: {value}" for key, value in entries)]


PacketListener = Callable[[Packet], None]


class AdbUsbClient:
    """Read-only USB ADB session for deployment and diagnostic workflows.

    This class intentionally has no UDP ``send``/``receive`` API. Standard ADB
    forwarding is TCP/local-socket only, while Robocol is bidirectional UDP on
    port 20884. Keeping those responsibilities separate prevents an application
    from treating a healthy ADB cable as a live Driver-Station connection.
    """

    def __init__(self, config: AdbUsbConfig = AdbUsbConfig()) -> None:
        self.config = config
        self._adb_path = _find_adb(config.adb_path)
        self._device: AdbDevice | None = None

    @property
    def device(self) -> AdbDevice | None:
        """The authorized USB device selected by :meth:`connect`, if any."""
        return self._device

    def connect(self) -> AdbDevice:
        """Select one authorized USB Android device and read its identity."""
        if self.config.serial and ":" in self.config.serial:
            raise AdbDeviceNotFound(
                "ADB USB mode requires a physical USB serial, not a host:port network serial"
            )

        devices = self._list_authorized_usb_devices()
        if self.config.serial:
            matches = [device for device in devices if device == self.config.serial]
            if not matches:
                raise AdbDeviceNotFound(
                    f"USB ADB device {self.config.serial!r} was not found or is not authorized"
                )
            serial = matches[0]
        elif not devices:
            raise AdbDeviceNotFound(
                "No authorized USB Android device. Connect the phone with a data cable, "
                "enable USB debugging, and accept the RSA prompt."
            )
        elif len(devices) > 1:
            raise MultipleAdbDevices(
                "More than one USB Android device is connected; pass AdbUsbConfig(serial=...)."
            )
        else:
            serial = devices[0]

        self._device = AdbDevice(
            serial=serial,
            manufacturer=self._getprop(serial, "ro.product.manufacturer"),
            model=self._getprop(serial, "ro.product.model"),
            android_version=self._getprop(serial, "ro.build.version.release"),
        )
        return self._device

    def require_robocol_relay(self) -> None:
        """Explain why a direct USB ADB Robocol session cannot be opened."""
        raise UsbRobocolRelayRequired(
            "USB ADB is connected, but standard ADB cannot forward UDP Robocol. "
            "Use ControlHubClient over Wi-Fi, or install an Android-side UDP-to-TCP relay."
        )

    def shell(self, *command: str) -> str:
        """Run an ADB shell command on the selected device and return stdout.

        This is intentionally separate from :class:`ControlHubClient`; callers
        can use it for device diagnostics while the Wi-Fi client owns Robocol.
        """
        if self._device is None:
            raise AdbError("Call connect() before running an ADB shell command")
        return self._run("-s", self._device.serial, "shell", *command).stdout

    def _list_authorized_usb_devices(self) -> list[str]:
        result = self._run("devices", "-l")
        devices: list[str] = []
        for line in result.stdout.splitlines():
            if not line or line.startswith("List of devices attached"):
                continue
            fields = line.split()
            if len(fields) >= 2 and fields[1] == "device" and ":" not in fields[0]:
                devices.append(fields[0])
        return devices

    def _getprop(self, serial: str, property_name: str) -> str:
        return self._run("-s", serial, "shell", "getprop", property_name).stdout.strip()

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(
                [self._adb_path, *args],
                capture_output=True,
                check=False,
                text=True,
                timeout=self.config.command_timeout_s,
            )
        except subprocess.TimeoutExpired as error:
            raise AdbError(f"ADB command timed out: {' '.join(args)}") from error
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "unknown ADB error"
            raise AdbError(f"ADB command failed: {detail}")
        return result


def _find_adb(configured_path: str | None) -> str:
    """Locate the Android platform-tools executable without a global install."""
    if configured_path:
        candidate = Path(configured_path)
        if candidate.is_file():
            return str(candidate)
        found = shutil.which(configured_path)
        if found:
            return found
        raise AdbNotFound(f"ADB executable was not found: {configured_path}")

    if found := shutil.which("adb"):
        return found
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidate = Path(local_app_data) / "Android" / "Sdk" / "platform-tools" / "adb.exe"
        if candidate.is_file():
            return str(candidate)
    raise AdbNotFound(
        "Could not find adb. Install Android SDK Platform-Tools or pass AdbUsbConfig(adb_path=...)."
    )


class ControlHubClient:
    """Owns a UDP Robocol discovery/heartbeat session.

    The class is safe to import from a larger application.  Register packet
    listeners before :meth:`connect`; callbacks run on the client's background
    thread and should return quickly. Lifecycle commands are deliberately
    explicit: callers must request ``init_opmode()``, ``start_opmode()``, or
    ``stop_opmode()`` themselves.
    """

    _COMMAND_RETRY_INTERVAL_S: Final = 0.2
    _COMMAND_MAX_ATTEMPTS: Final = 10
    _GAMEPAD_INTERVAL_S: Final = 0.04
    _GAMEPAD_IDLE_GRACE_S: Final = 1.0
    _STOP_OPMODE_NAME: Final = "$Stop$Robot$"

    def __init__(self, config: ControlHubConfig) -> None:
        self.config = config
        self._socket: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._accepted_event = threading.Event()
        self._rejected_event = threading.Event()
        self._version_error: ProtocolVersionMismatch | None = None
        self._lock = threading.RLock()
        self._listeners: list[PacketListener] = []
        self._sequence = 0
        self._last_heartbeat_ns: int | None = None
        self._robot_state = RobotState.UNKNOWN
        self._pending_commands: dict[tuple[str, int], _PendingCommand] = {}
        self._gamepads: dict[int, _GamepadSlot] = {}
        self._opmode_list_event = threading.Event()
        self._opmodes: tuple[dict[str, object], ...] = ()
        self._init_notifications: dict[str, threading.Event] = {}
        self._run_notifications: dict[str, threading.Event] = {}
        # Configuration responses are not correlated to their request on the
        # wire, so serialize this small family of requests locally.
        self._configuration_request_lock = threading.RLock()
        self._configurations_event = threading.Event()
        self._active_configuration_event = threading.Event()
        self._configuration_xml_event = threading.Event()
        self._configurations: tuple[dict[str, object], ...] = ()
        self._configuration_references: dict[str, str] = {}
        self._active_configuration: dict[str, object] | None = None
        self._configuration_xml: str | None = None

    def __enter__(self) -> "ControlHubClient":
        self.connect()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @property
    def is_connected(self) -> bool:
        """Whether peer discovery has been accepted by the Robot Controller."""
        return self._accepted_event.is_set() and not self._stop_event.is_set()

    @property
    def robot_state(self) -> RobotState:
        """Most recent state observed in a heartbeat, or ``UNKNOWN``."""
        with self._lock:
            return self._robot_state

    @property
    def last_heartbeat_monotonic_ns(self) -> int | None:
        """Monotonic receipt time of the latest heartbeat from the RC."""
        with self._lock:
            return self._last_heartbeat_ns

    def add_packet_listener(self, listener: PacketListener) -> None:
        """Subscribe to all validated incoming packets."""
        with self._lock:
            self._listeners.append(listener)

    def set_gamepad_input(self, gamepad: GamepadInput) -> None:
        """Assign the normalized state for a driver slot.

        Active states are sent every 40 ms once peer discovery is accepted. A
        newly supplied neutral state is also sent for one second so the RC
        clears previously held input before idle traffic stops.
        """
        with self._lock:
            self._gamepads[gamepad.user] = _GamepadSlot(
                input=gamepad,
                updated_at=time.monotonic(),
            )

    def clear_gamepad_input(self, user: int) -> None:
        """Release all controls in ``user``'s RC gamepad slot."""
        self.set_gamepad_input(GamepadInput.neutral(user))

    def list_opmodes(self, timeout_s: float = 3.0) -> tuple[dict[str, object], ...]:
        """Request and return the RC's advertised OpMode metadata.

        The returned dictionaries preserve forward-compatible fields from the
        RC JSON.  In particular, callers should treat ``flavor`` as a string,
        because newer SDKs can add flavors.
        """
        self._require_connected()
        self._opmode_list_event.clear()
        self._send_reliable_command("CMD_REQUEST_OP_MODE_LIST", timeout_s=timeout_s)
        if not self._opmode_list_event.wait(timeout_s):
            raise CommandTimeout("RC did not send CMD_NOTIFY_OP_MODE_LIST")
        with self._lock:
            return self._opmodes

    def list_configurations(self, timeout_s: float = 3.0) -> tuple[dict[str, object], ...]:
        """Return the Robot Controller's configuration-file list.

        Each record intentionally retains unknown fields from the SDK JSON so
        callers can accommodate newer SDK versions without rewriting files.
        """
        self._require_connected()
        with self._configuration_request_lock:
            self._configurations_event.clear()
            self._send_reliable_command("CMD_REQUEST_CONFIGURATIONS", timeout_s=timeout_s)
            if not self._configurations_event.wait(timeout_s):
                raise CommandTimeout("RC did not send CMD_REQUEST_CONFIGURATIONS_RESP")
            with self._lock:
                return tuple(dict(item) for item in self._configurations)

    def get_active_configuration(self, timeout_s: float = 3.0) -> dict[str, object]:
        """Return the active configuration file reference reported by the RC."""
        self._require_connected()
        with self._configuration_request_lock:
            self._active_configuration_event.clear()
            self._send_reliable_command("CMD_REQUEST_ACTIVE_CONFIG", timeout_s=timeout_s)
            if not self._active_configuration_event.wait(timeout_s):
                raise CommandTimeout("RC did not send CMD_NOTIFY_ACTIVE_CONFIGURATION")
            with self._lock:
                if self._active_configuration is None:
                    raise CommandTimeout("RC sent an empty active configuration")
                return dict(self._active_configuration)

    def read_configuration_xml(self, name: str, timeout_s: float = 3.0) -> str:
        """Read one named configuration as canonical XML from the RC."""
        self._require_connected()
        with self._configuration_request_lock:
            with self._lock:
                reference = self._configuration_references.get(name)
            if reference is None:
                self.list_configurations(timeout_s)
                with self._lock:
                    reference = self._configuration_references.get(name)
            if reference is None:
                raise ControlHubError(f"Configuration {name!r} does not exist on the Robot Controller")

            with self._lock:
                self._configuration_xml = None
            self._configuration_xml_event.clear()
            self._send_reliable_command(
                "CMD_REQUEST_PARTICULAR_CONFIGURATION", reference.encode("utf-8"), timeout_s
            )
            if not self._configuration_xml_event.wait(timeout_s):
                raise CommandTimeout(f"RC did not send configuration XML for {name!r}")
            with self._lock:
                if self._configuration_xml is None:
                    raise CommandTimeout(f"RC sent an empty configuration XML for {name!r}")
                return self._configuration_xml

    def save_configuration_xml(self, name: str, xml: str, timeout_s: float = 5.0) -> dict[str, object]:
        """Save XML as a local configuration file and request that it becomes active.

        The RC protocol sends a JSON RobotConfigFile reference, a semicolon,
        then the XML document. The active-configuration notification is the
        only success signal available after the reliable command ACK.
        """
        self._require_connected()
        reference = json.dumps(
            {"name": name, "resourceId": 0, "location": "LOCAL_STORAGE", "isDirty": False},
            separators=(",", ":"),
        )
        extra = f"{reference};{xml}".encode("utf-8")
        if len(extra) > 0xFFFF:
            raise ValueError("Configuration XML is too large for one Robocol command")

        with self._configuration_request_lock:
            self._active_configuration_event.clear()
            self._send_reliable_command("CMD_SAVE_CONFIGURATION", extra, timeout_s)
            if not self._active_configuration_event.wait(timeout_s):
                raise CommandTimeout(
                    "RC acknowledged the save request but did not confirm the active configuration"
                )
            with self._lock:
                if self._active_configuration is None:
                    raise CommandTimeout("RC sent an empty active configuration after save")
                active_configuration = dict(self._active_configuration)
            if active_configuration.get("name") != name:
                raise ControlHubError(
                    f"RC reported {active_configuration.get('name')!r} as active after saving {name!r}"
                )
            return active_configuration

    def init_opmode(self, name: str, timeout_s: float = 3.0) -> None:
        """Ask the RC to initialize an OpMode and wait for its confirmation."""
        self._require_connected()
        notification = self._prepare_lifecycle_notification(self._init_notifications, name)
        self._send_reliable_command("CMD_INIT_OP_MODE", name.encode("utf-8"), timeout_s)
        if not notification.wait(timeout_s):
            raise CommandTimeout(f"RC did not confirm initialization of {name!r}")

    def start_opmode(self, name: str, timeout_s: float = 3.0) -> None:
        """Ask the RC to run an initialized OpMode and wait for confirmation."""
        self._require_connected()
        notification = self._prepare_lifecycle_notification(self._run_notifications, name)
        self._send_reliable_command("CMD_RUN_OP_MODE", name.encode("utf-8"), timeout_s)
        if not notification.wait(timeout_s):
            raise CommandTimeout(f"RC did not confirm start of {name!r}")

    def stop_opmode(self, timeout_s: float = 3.0) -> None:
        """Stop safely by initializing the FTC SDK's stop sentinel OpMode."""
        self.init_opmode(self._STOP_OPMODE_NAME, timeout_s)

    def connect(self, timeout_s: float = 5.0) -> None:
        """Bind UDP, complete discovery, and start the heartbeat service.

        Raises:
            ConnectionTimeout: no accepted discovery reply arrived in time.
            PeerRejected: the RC reports another active Driver Station.
            ProtocolVersionMismatch: the RC uses an unsupported Robocol version.
        """
        if self._thread and self._thread.is_alive():
            raise ControlHubError("client is already running")

        self._reset_session_state()
        local_address = self.config.local_address or self._route_local_address()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # The protocol's general receive timeout is 300 ms, but this
            # single-threaded service must poll more often to meet the 100 ms
            # heartbeat deadline.  A larger caller value remains a cap.
            sock.settimeout(min(self.config.receive_timeout_s, self.config.heartbeat_interval_s / 4))
            sock.bind((local_address, self.config.local_port))
            sock.connect((self.config.host, self.config.port))
        except OSError:
            sock.close()
            raise

        self._socket = sock
        self._thread = threading.Thread(
            target=self._run,
            name="ftc-robocol",
            daemon=True,
        )
        self._thread.start()

        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self._accepted_event.wait(timeout=0.05):
                return
            if self._rejected_event.is_set():
                self.close()
                raise PeerRejected("Robot Controller already has another Driver Station")
            if self._version_error is not None:
                error = self._version_error
                self.close()
                raise error

        self.close()
        raise ConnectionTimeout(
            f"No accepted peer-discovery reply from {self.config.host}:{self.config.port}"
        )

    def close(self) -> None:
        """Stop background I/O and release the UDP port."""
        self._stop_event.set()
        sock, self._socket = self._socket, None
        if sock is not None:
            sock.close()
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=self.config.receive_timeout_s + 0.5)
        self._thread = None

    def wait_for_heartbeat(self, timeout_s: float = 2.0) -> bool:
        """Wait until at least one RC heartbeat has been received."""
        start = self.last_heartbeat_monotonic_ns
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self.last_heartbeat_monotonic_ns != start:
                return True
            time.sleep(0.01)
        return False

    def _reset_session_state(self) -> None:
        self._stop_event.clear()
        self._accepted_event.clear()
        self._rejected_event.clear()
        self._version_error = None
        with self._lock:
            self._sequence = 0
            self._last_heartbeat_ns = None
            self._robot_state = RobotState.UNKNOWN
            self._pending_commands.clear()
            self._gamepads.clear()
            self._opmodes = ()
            self._init_notifications.clear()
            self._run_notifications.clear()
            self._configurations = ()
            self._configuration_references.clear()
            self._active_configuration = None
            self._configuration_xml = None
        self._opmode_list_event.clear()
        self._configurations_event.clear()
        self._active_configuration_event.clear()
        self._configuration_xml_event.clear()

    def _require_connected(self) -> None:
        if not self.is_connected:
            raise ControlHubError("Connect and receive peer acceptance before sending commands")

    def _prepare_lifecycle_notification(
        self, notifications: dict[str, threading.Event], name: str
    ) -> threading.Event:
        if not name or name == self._STOP_OPMODE_NAME and notifications is self._run_notifications:
            raise ValueError("A non-empty non-stop OpMode name is required")
        event = threading.Event()
        with self._lock:
            notifications[name] = event
        return event

    def _send_reliable_command(self, name: str, extra: bytes = b"", timeout_s: float = 3.0) -> None:
        if timeout_s <= 0:
            raise ValueError("timeout_s must be positive")
        try:
            name.encode("utf-8")
        except UnicodeEncodeError as error:
            raise ValueError("command name must be UTF-8 encodable") from error
        timestamp_ns = time.monotonic_ns()
        command = Command(name, extra, timestamp_ns, False, None)
        identity = (name, timestamp_ns)
        pending = _PendingCommand(command=command, next_attempt_at=time.monotonic())
        with self._lock:
            self._pending_commands[identity] = pending

        if not pending.acknowledged_event.wait(timeout_s):
            with self._lock:
                self._pending_commands.pop(identity, None)
            raise CommandTimeout(f"RC did not acknowledge {name}")

    def _send_due_commands(self, now: float) -> None:
        with self._lock:
            pending_commands = tuple(self._pending_commands.items())
        for identity, pending in pending_commands:
            if now < pending.next_attempt_at:
                continue
            if pending.attempts >= self._COMMAND_MAX_ATTEMPTS:
                with self._lock:
                    self._pending_commands.pop(identity, None)
                continue
            pending.attempts += 1
            pending.next_attempt_at = now + self._COMMAND_RETRY_INTERVAL_S
            sequence = self._next_sequence()
            self._send(self._encode_command(pending.command, sequence=sequence))

    def _send_due_gamepads(self, now: float) -> None:
        """Send active gamepads, plus a brief neutralization period at rest."""
        with self._lock:
            gamepads = tuple(self._gamepads.values())
        for slot in gamepads:
            input_age = now - slot.updated_at
            if not slot.force_send and slot.input.is_at_rest and input_age > self._GAMEPAD_IDLE_GRACE_S:
                continue
            self._send(self._encode_gamepad(slot.input))
            slot.force_send = False

    def _route_local_address(self) -> str:
        """Ask Windows/the OS which local IPv4 address reaches the RC."""
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect((self.config.host, self.config.port))
            return str(probe.getsockname()[0])
        finally:
            probe.close()

    def _run(self) -> None:
        next_discovery = 0.0
        next_heartbeat = 0.0
        next_gamepad = 0.0
        while not self._stop_event.is_set():
            now = time.monotonic()
            try:
                if not self._accepted_event.is_set() and now >= next_discovery:
                    self._send(self._encode_peer_discovery())
                    next_discovery = now + self.config.discovery_interval_s
                elif self._accepted_event.is_set() and now >= next_heartbeat:
                    self._send(self._encode_heartbeat())
                    next_heartbeat = now + self.config.heartbeat_interval_s
                if self._accepted_event.is_set():
                    self._send_due_commands(now)
                    if now >= next_gamepad:
                        self._send_due_gamepads(now)
                        next_gamepad = now + self._GAMEPAD_INTERVAL_S
                self._receive_one()
            except (OSError, ValueError) as error:
                if not self._stop_event.is_set():
                    LOG.warning("Robocol I/O stopped: %s", error)
                return

    def _send(self, data: bytes) -> None:
        sock = self._socket
        if sock is None:
            return
        sock.send(data)

    def _receive_one(self) -> None:
        sock = self._socket
        if sock is None:
            return
        try:
            data = sock.recv(65520)
        except socket.timeout:
            return
        if not data:
            return
        received_ns = time.monotonic_ns()
        if data[0] == RobocolMessageType.PEER_DISCOVERY:
            self._handle_peer_discovery(data)
            return
        packet = self._decode_normal_packet(data, received_ns)
        if packet is None:
            return
        if packet.message_type == RobocolMessageType.HEARTBEAT:
            self._handle_heartbeat(packet)
        elif packet.message_type == RobocolMessageType.COMMAND:
            self._handle_command(packet)
        self._notify_listeners(packet)

    def _handle_peer_discovery(self, data: bytes) -> None:
        if len(data) != 13:
            LOG.debug("Ignoring malformed peer-discovery datagram (%d bytes)", len(data))
            return
        _, payload_length, version, peer_type, _, _, _, _, _, _ = _PEER_DISCOVERY.unpack(data)
        if payload_length != 10:
            LOG.debug("Ignoring peer discovery with unexpected payload length %d", payload_length)
            return
        if version != ROBOCOL_VERSION:
            self._version_error = ProtocolVersionMismatch(
                f"RC Robocol version is {version}; this client requires {ROBOCOL_VERSION}"
            )
            return
        if peer_type == PeerType.REJECTED_EXISTING_CONNECTION:
            self._rejected_event.set()
        elif peer_type == PeerType.PEER:
            self._accepted_event.set()
        else:
            LOG.debug("Ignoring peer discovery with type %d", peer_type)

    def _handle_heartbeat(self, packet: Packet) -> None:
        # Creation timestamp (8 bytes), then signed robot-state byte.
        if len(packet.payload) < 9:
            return
        state_number = struct.unpack_from("!b", packet.payload, 8)[0]
        try:
            state = RobotState(state_number)
        except ValueError:
            LOG.debug("Unknown robot state %d", state_number)
            state = RobotState.UNKNOWN
        with self._lock:
            self._robot_state = state
            self._last_heartbeat_ns = packet.received_monotonic_ns

    def _handle_command(self, packet: Packet) -> None:
        command = self._decode_command(packet)
        if command is None:
            return
        identity = (command.name, command.timestamp_ns)
        if command.acknowledged:
            with self._lock:
                pending = self._pending_commands.pop(identity, None)
            if pending:
                pending.acknowledged_event.set()
            return

        # The RC expects command requests to be acknowledged immediately,
        # before their (possibly slow) notification handlers run.
        self._send(self._encode_command(command, acknowledged=True, sequence=packet.sequence))
        if command.name == "CMD_NOTIFY_OP_MODE_LIST":
            self._handle_opmode_list(command.extra)
        elif command.name == "CMD_REQUEST_CONFIGURATIONS_RESP":
            self._handle_configuration_list(command.extra)
        elif command.name == "CMD_NOTIFY_ACTIVE_CONFIGURATION":
            self._handle_active_configuration(command.extra)
        elif command.name == "CMD_REQUEST_PARTICULAR_CONFIGURATION_RESP":
            self._handle_configuration_xml(command.extra)
        elif command.name == "CMD_NOTIFY_INIT_OP_MODE":
            self._signal_lifecycle_notification(self._init_notifications, command.extra)
        elif command.name == "CMD_NOTIFY_RUN_OP_MODE":
            self._signal_lifecycle_notification(self._run_notifications, command.extra)

    def _handle_opmode_list(self, extra: bytes) -> None:
        try:
            parsed = json.loads(extra.decode("utf-8"))
            if not isinstance(parsed, list) or not all(isinstance(item, dict) for item in parsed):
                raise ValueError("OpMode list is not a JSON array of objects")
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
            LOG.warning("Ignoring malformed CMD_NOTIFY_OP_MODE_LIST: %s", error)
            return
        with self._lock:
            self._opmodes = tuple(parsed)
        self._opmode_list_event.set()

    def _handle_configuration_list(self, extra: bytes) -> None:
        try:
            parsed = json.loads(extra.decode("utf-8"))
            if not isinstance(parsed, list) or not all(
                isinstance(item, dict) and isinstance(item.get("name"), str) and item["name"]
                for item in parsed
            ):
                raise ValueError("configuration list has invalid records")
            references = {
                str(item["name"]): json.dumps(item, separators=(",", ":")) for item in parsed
            }
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
            LOG.warning("Ignoring malformed CMD_REQUEST_CONFIGURATIONS_RESP: %s", error)
            return
        with self._lock:
            self._configurations = tuple(parsed)
            self._configuration_references = references
        self._configurations_event.set()

    def _handle_active_configuration(self, extra: bytes) -> None:
        try:
            parsed = json.loads(extra.decode("utf-8"))
            if not isinstance(parsed, dict) or not isinstance(parsed.get("name"), str):
                raise ValueError("active configuration is not a named record")
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
            LOG.warning("Ignoring malformed CMD_NOTIFY_ACTIVE_CONFIGURATION: %s", error)
            return
        with self._lock:
            self._active_configuration = parsed
        self._active_configuration_event.set()

    def _handle_configuration_xml(self, extra: bytes) -> None:
        try:
            xml = extra.decode("utf-8")
            if not xml.strip():
                raise ValueError("empty XML document")
        except (UnicodeDecodeError, ValueError) as error:
            LOG.warning("Ignoring malformed CMD_REQUEST_PARTICULAR_CONFIGURATION_RESP: %s", error)
            return
        with self._lock:
            self._configuration_xml = xml
        self._configuration_xml_event.set()

    def _signal_lifecycle_notification(
        self, notifications: dict[str, threading.Event], extra: bytes
    ) -> None:
        try:
            name = extra.decode("utf-8")
        except UnicodeDecodeError:
            LOG.warning("Ignoring lifecycle notification with non-UTF-8 OpMode name")
            return
        with self._lock:
            event = notifications.get(name)
        if event:
            event.set()

    def _notify_listeners(self, packet: Packet) -> None:
        with self._lock:
            listeners = tuple(self._listeners)
        for listener in listeners:
            try:
                listener(packet)
            except Exception:  # A UI callback must not kill the safety heartbeat.
                LOG.exception("Robocol packet listener failed")

    def _next_sequence(self) -> int:
        with self._lock:
            sequence = self._sequence
            self._sequence = (sequence + 1) & 0xFFFF
            return sequence

    def _encode_peer_discovery(self) -> bytes:
        sdk = self.config.sdk
        return _PEER_DISCOVERY.pack(
            RobocolMessageType.PEER_DISCOVERY,
            10,
            ROBOCOL_VERSION,
            PeerType.PEER,
            self._next_sequence(),
            sdk.release_month,
            sdk.release_year,
            sdk.major,
            sdk.minor,
            0,
        )

    def _encode_heartbeat(self) -> bytes:
        timezone_name = (self.config.timezone_id or _local_timezone_name()).encode("utf-8")[:127]
        payload = struct.pack(
            "!qbqqqB",
            time.monotonic_ns(),
            RobotState.UNKNOWN,
            int(time.time() * 1000),
            0,
            0,
            len(timezone_name),
        ) + timezone_name
        return _NORMAL_HEADER.pack(
            RobocolMessageType.HEARTBEAT,
            len(payload),
            self._next_sequence(),
        ) + payload

    def _encode_gamepad(self, gamepad: GamepadInput) -> bytes:
        """Serialize a 60-byte FTC SDK gamepad-v5 payload."""
        payload = _GAMEPAD_PAYLOAD.pack(
            5,  # FTC SDK gamepad wire version
            gamepad.device_id,
            gamepad.timestamp_ms,
            gamepad.left_stick_x,
            gamepad.left_stick_y,
            gamepad.right_stick_x,
            gamepad.right_stick_y,
            gamepad.left_trigger,
            gamepad.right_trigger,
            int(gamepad.buttons),
            gamepad.user,
            gamepad.legacy_type,
            gamepad.gamepad_type,
            gamepad.touchpad_finger_1_x,
            gamepad.touchpad_finger_1_y,
            gamepad.touchpad_finger_2_x,
            gamepad.touchpad_finger_2_y,
        )
        return _NORMAL_HEADER.pack(
            RobocolMessageType.GAMEPAD,
            len(payload),
            self._next_sequence(),
        ) + payload

    @staticmethod
    def _encode_command(
        command: Command, *, acknowledged: bool | None = None, sequence: int | None = None
    ) -> bytes:
        acknowledged = command.acknowledged if acknowledged is None else acknowledged
        command_name = command.name.encode("utf-8")
        if len(command_name) > 0xFFFF:
            raise ValueError("command name exceeds the Robocol uint16 limit")
        payload = struct.pack(
            "!qBH", command.timestamp_ns, int(acknowledged), len(command_name)
        ) + command_name
        if not acknowledged:
            if len(command.extra) > 0xFFFF:
                raise ValueError("command extra exceeds the Robocol uint16 limit")
            payload += struct.pack("!H", len(command.extra)) + command.extra
        if sequence is None:
            raise ValueError("a Robocol command requires an explicit sequence number")
        return _NORMAL_HEADER.pack(RobocolMessageType.COMMAND, len(payload), sequence) + payload

    @staticmethod
    def _decode_command(packet: Packet) -> Command | None:
        payload = packet.payload
        if len(payload) < 11:
            LOG.debug("Ignoring truncated command packet (%d bytes)", len(payload))
            return None
        try:
            timestamp_ns, acknowledged, name_length = struct.unpack_from("!qBH", payload)
            offset = 11
            if len(payload) - offset < name_length:
                raise ValueError("truncated command name")
            name = payload[offset : offset + name_length].decode("utf-8")
            offset += name_length
            if acknowledged:
                if offset != len(payload):
                    raise ValueError("ACK command unexpectedly contains extra bytes")
                extra = b""
            else:
                if len(payload) - offset < 2:
                    raise ValueError("truncated command extra length")
                extra_length = struct.unpack_from("!H", payload, offset)[0]
                offset += 2
                if len(payload) - offset != extra_length:
                    raise ValueError("command extra length does not match packet")
                extra = payload[offset : offset + extra_length]
        except (UnicodeDecodeError, ValueError, struct.error) as error:
            LOG.debug("Ignoring malformed command packet: %s", error)
            return None
        return Command(name, extra, timestamp_ns, bool(acknowledged), packet.sequence)

    @staticmethod
    def _decode_normal_packet(data: bytes, received_ns: int) -> Packet | None:
        if len(data) < _NORMAL_HEADER.size:
            LOG.debug("Ignoring truncated Robocol packet (%d bytes)", len(data))
            return None
        message_type, payload_length, sequence = _NORMAL_HEADER.unpack_from(data)
        if len(data) != _NORMAL_HEADER.size + payload_length:
            LOG.debug(
                "Ignoring packet type %d: header says %d-byte payload, got %d bytes",
                message_type,
                payload_length,
                len(data) - _NORMAL_HEADER.size,
            )
            return None
        return Packet(message_type, sequence, data[_NORMAL_HEADER.size :], received_ns)


def decode_telemetry(packet: Packet) -> TelemetryPacket | None:
    """Decode a validated Robocol type-5 telemetry packet.

    Malformed or future-incompatible telemetry is ignored rather than allowed
    to interrupt the heartbeat thread that owns the Robocol connection.
    """
    if packet.message_type != RobocolMessageType.TELEMETRY:
        return None

    payload = packet.payload
    offset = 0

    def take(size: int) -> bytes:
        nonlocal offset
        if len(payload) - offset < size:
            raise ValueError("truncated telemetry field")
        value = payload[offset : offset + size]
        offset += size
        return value

    def read_u8() -> int:
        return take(1)[0]

    def read_utf8_u8() -> str:
        return take(read_u8()).decode("utf-8")

    def read_utf8_u16() -> str:
        length = struct.unpack("!H", take(2))[0]
        return take(length).decode("utf-8")

    try:
        timestamp_ms = struct.unpack("!q", take(8))[0]
        sorted_entries = bool(read_u8())
        state_number = struct.unpack("!b", take(1))[0]
        try:
            robot_state = RobotState(state_number)
        except ValueError:
            robot_state = RobotState.UNKNOWN
        tag = read_utf8_u8()
        string_count = read_u8()
        strings = tuple((read_utf8_u16(), read_utf8_u16()) for _ in range(string_count))
        number_count = read_u8()
        numbers = tuple(
            (read_utf8_u16(), struct.unpack("!f", take(4))[0]) for _ in range(number_count)
        )
        if offset != len(payload):
            raise ValueError("unexpected bytes after telemetry payload")
    except (UnicodeDecodeError, ValueError, struct.error) as error:
        LOG.debug("Ignoring malformed telemetry packet: %s", error)
        return None

    return TelemetryPacket(
        timestamp_ms=timestamp_ms,
        sorted=sorted_entries,
        robot_state=robot_state,
        tag=tag,
        strings=strings,
        numbers=numbers,
        sequence=packet.sequence,
        received_monotonic_ns=packet.received_monotonic_ns,
    )


def _local_timezone_name() -> str:
    """Return an IANA timezone name, including the common Windows mappings.

    ``timezone_id`` in :class:`ControlHubConfig` is the authoritative escape
    hatch for an unmapped zone.  Java accepts IANA identifiers, whereas Windows
    normally reports a different naming scheme.
    """
    windows_to_iana = {
        "GTB Standard Time": "Europe/Athens",
        "GMT Standard Time": "Europe/London",
        "W. Europe Standard Time": "Europe/Berlin",
        "Romance Standard Time": "Europe/Paris",
        "Eastern Standard Time": "America/New_York",
        "Central Standard Time": "America/Chicago",
        "Mountain Standard Time": "America/Denver",
        "Pacific Standard Time": "America/Los_Angeles",
        "UTC": "UTC",
    }
    if hasattr(ctypes, "windll"):
        class _DynamicTimeZoneInformation(ctypes.Structure):
            _fields_ = [
                ("bias", ctypes.c_long),
                ("standard_name", ctypes.c_wchar * 32),
                ("standard_date", ctypes.c_byte * 16),
                ("standard_bias", ctypes.c_long),
                ("daylight_name", ctypes.c_wchar * 32),
                ("daylight_date", ctypes.c_byte * 16),
                ("daylight_bias", ctypes.c_long),
                ("key_name", ctypes.c_wchar * 128),
                ("dynamic_daylight_disabled", ctypes.c_bool),
            ]

        info = _DynamicTimeZoneInformation()
        if ctypes.windll.kernel32.GetDynamicTimeZoneInformation(ctypes.byref(info)) != 0xFFFFFFFF:
            if mapped := windows_to_iana.get(info.key_name):
                return mapped
    return "UTC"


def main() -> int:
    """Run either a Wi-Fi Robocol or USB ADB lab connectivity smoke test."""
    parser = argparse.ArgumentParser(description="Connect to an FTC Robot Controller via Robocol")
    parser.add_argument(
        "host",
        nargs="?",
        help="Robot Controller IP (usually 192.168.43.1 for a Control Hub or 192.168.49.1 for a phone RC)",
    )
    parser.add_argument(
        "--mode",
        choices=[mode.value for mode in ConnectionMode],
        default=ConnectionMode.WIFI.value,
        help="wifi runs Robocol; adb-usb verifies the separate wired ADB connection",
    )
    parser.add_argument("--local-address", help="Wi-Fi IPv4 address to bind (normally auto-detected)")
    parser.add_argument("--local-port", type=int, default=ROBOCOL_PORT)
    parser.add_argument("--timezone", help="IANA timezone sent in heartbeats, for example Europe/Athens")
    parser.add_argument("--timeout", type=float, default=5.0, help="discovery timeout in seconds")
    parser.add_argument("--adb", help="path to adb or its executable name")
    parser.add_argument("--adb-serial", help="physical USB ADB serial when multiple Android devices are connected")
    parser.add_argument("--list-opmodes", action="store_true", help="print OpModes advertised by the RC")
    parser.add_argument("--opmode", help="exact OpMode name used with --init and/or --start")
    parser.add_argument("--init", action="store_true", help="initialize --opmode and wait for RC confirmation")
    parser.add_argument("--start", action="store_true", help="start --opmode and wait for RC confirmation")
    parser.add_argument("--stop", action="store_true", help="stop via the FTC $Stop$Robot$ sentinel")
    parser.add_argument(
        "--no-telemetry",
        action="store_true",
        help="do not render incoming telemetry in the terminal",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    if (args.init or args.start) and not args.opmode:
        parser.error("--opmode is required with --init or --start")
    if args.stop and (args.init or args.start):
        parser.error("--stop cannot be combined with --init or --start")

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(message)s")
    if args.mode == ConnectionMode.ADB_USB.value:
        try:
            device = AdbUsbClient(AdbUsbConfig(adb_path=args.adb, serial=args.adb_serial)).connect()
            print(
                f"USB ADB ready: {device.manufacturer} {device.model} "
                f"(Android {device.android_version}, serial {device.serial})."
            )
            print("ADB is ready for deployment/diagnostics; Robocol control still requires Wi-Fi.")
            return 0
        except ControlHubError as error:
            print(f"USB ADB connection failed: {error}")
            return 1

    if not args.host:
        parser.error("host is required in --mode wifi")
    config = ControlHubConfig(
        host=args.host,
        local_address=args.local_address,
        local_port=args.local_port,
        timezone_id=args.timezone,
    )
    client = ControlHubClient(config)
    telemetry_terminal = TelemetryTerminal()

    def render_telemetry(packet: Packet) -> None:
        if telemetry := decode_telemetry(packet):
            telemetry_terminal.render(telemetry)

    if not args.no_telemetry:
        client.add_packet_listener(render_telemetry)
    try:
        client.connect(timeout_s=args.timeout)
        print(
            f"Connected to {config.host}:{config.port}; telemetry updates redraw in place. "
            "Ctrl+C to disconnect."
        )
        opmodes: tuple[dict[str, object], ...] | None = None
        if args.list_opmodes or args.init or args.start:
            opmodes = client.list_opmodes(timeout_s=args.timeout)
        if args.list_opmodes:
            assert opmodes is not None
            for opmode in opmodes:
                print(
                    f"{opmode.get('name', '<unnamed>')} "
                    f"[{opmode.get('flavor', 'UNKNOWN')}] "
                    f"group={opmode.get('group', '')}"
                )
            if not (args.init or args.start or args.stop):
                return 0
        if args.init or args.start:
            assert opmodes is not None
            available_names = {str(opmode.get("name", "")) for opmode in opmodes}
            if args.opmode not in available_names:
                raise ControlHubError(
                    f"OpMode {args.opmode!r} is not advertised by the RC; "
                    "run with --list-opmodes to inspect the available names"
                )
        if args.stop:
            client.stop_opmode(timeout_s=args.timeout)
            print("RC confirmed stop.")
            return 0
        if args.init:
            client.init_opmode(args.opmode, timeout_s=args.timeout)
            print(f"RC confirmed initialization of {args.opmode!r}.")
        if args.start:
            client.start_opmode(args.opmode, timeout_s=args.timeout)
            print(f"RC confirmed start of {args.opmode!r}.")
        while True:
            time.sleep(1)
            if args.no_telemetry:
                print(f"robot state: {client.robot_state.name}")
    except (ControlHubError, OSError) as error:
        print(f"Connection failed: {error}")
        return 1
    except KeyboardInterrupt:
        print("Disconnecting.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
