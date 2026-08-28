"""Structured in-memory telemetry sessions for the laptop dashboard.

The TCP server owns framing.  This module owns the protocol meaning after a
complete JSON object has been decoded: catalogs, full snapshots, events, gaps,
and a bounded history suitable for the live browser.  Persisting raw frames to
disk is deliberately a later layer; no robot-control work depends on this
store.
"""

from __future__ import annotations

import math
import re
import threading
import time
import uuid
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


PROTOCOL_NAME = "ftc-telemetry"
PROTOCOL_VERSION = 1
_IDENTIFIER = re.compile(r"^[a-z][A-Za-z0-9]*(?:\.[a-z][A-Za-z0-9]*)*$")
_INT64 = re.compile(r"^-?(?:0|[1-9][0-9]*)$")
_UINT64 = re.compile(r"^(?:0|[1-9][0-9]*)$")
_VALUE_TYPES = {"float64", "int64", "boolean", "string", "enum", "vector2", "vector3", "pose2d"}
_ROLES = {"measured", "target", "command", "error", "status", "diagnostic"}
_SEVERITIES = {"debug", "info", "warning", "error", "critical"}
_GAMEPAD_AXES = ("leftStickX", "leftStickY", "rightStickX", "rightStickY")
_GAMEPAD_TRIGGERS = ("leftTrigger", "rightTrigger")
_GAMEPAD_BUTTONS = (
    "a", "b", "x", "y", "dpadUp", "dpadDown", "dpadLeft", "dpadRight",
    "leftBumper", "rightBumper", "leftStickButton", "rightStickButton", "back", "start", "guide",
)


class TelemetryProtocolError(ValueError):
    """Raised when a framed JSON object violates the telemetry contract."""


def _require_object(value: object, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TelemetryProtocolError(f"{field_name} must be an object")
    return value


def _require_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise TelemetryProtocolError(f"{field_name} must be a non-empty string")
    return value


def _require_identifier(value: object, field_name: str) -> str:
    identifier = _require_string(value, field_name)
    if not _IDENTIFIER.fullmatch(identifier):
        raise TelemetryProtocolError(f"{field_name} must be a dot-separated identifier")
    return identifier


def _require_uint64(value: object, field_name: str) -> str:
    rendered = _require_string(value, field_name)
    if not _UINT64.fullmatch(rendered):
        raise TelemetryProtocolError(f"{field_name} must be an unsigned decimal string")
    return rendered


def _require_uuid(value: object, field_name: str) -> str:
    rendered = _require_string(value, field_name)
    try:
        return str(uuid.UUID(rendered))
    except (AttributeError, ValueError) as error:
        raise TelemetryProtocolError(f"{field_name} must be a UUID") from error


def _finite_number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise TelemetryProtocolError(f"{field_name} must be a finite number")
    return float(value)


def _validate_value(value: object, value_type: str, signal_id: str) -> Any:
    """Validate a value while preserving the JSON-friendly representation."""

    if value is None:
        return None
    if value_type == "float64":
        _finite_number(value, signal_id)
        return value
    if value_type == "int64":
        if not isinstance(value, str) or not _INT64.fullmatch(value):
            raise TelemetryProtocolError(f"{signal_id} must be an int64 decimal string or null")
        return value
    if value_type == "boolean":
        if not isinstance(value, bool):
            raise TelemetryProtocolError(f"{signal_id} must be a boolean or null")
        return value
    if value_type in {"string", "enum"}:
        if not isinstance(value, str):
            raise TelemetryProtocolError(f"{signal_id} must be a string or null")
        return value
    if value_type in {"vector2", "vector3", "pose2d"}:
        object_value = _require_object(value, signal_id)
        fields = ("x", "y") if value_type == "vector2" else ("x", "y", "z")
        if value_type == "pose2d":
            fields = ("x", "y", "headingRad")
        if set(object_value) != set(fields):
            raise TelemetryProtocolError(f"{signal_id} must contain exactly {', '.join(fields)}")
        for field_name in fields:
            _finite_number(object_value[field_name], f"{signal_id}.{field_name}")
        return dict(object_value)
    raise TelemetryProtocolError(f"Unsupported value type {value_type}")


def _validate_gamepad(value: object, field_name: str) -> dict[str, Any]:
    """Validate the compact, display-oriented gamepad envelope."""

    state = _require_object(value, field_name)
    expected = set(_GAMEPAD_AXES) | set(_GAMEPAD_TRIGGERS) | set(_GAMEPAD_BUTTONS)
    if set(state) != expected:
        raise TelemetryProtocolError(f"{field_name} must contain the complete standard gamepad state")
    normalized: dict[str, Any] = {}
    for axis in _GAMEPAD_AXES:
        numeric = _finite_number(state[axis], f"{field_name}.{axis}")
        if not -1 <= numeric <= 1:
            raise TelemetryProtocolError(f"{field_name}.{axis} must be between -1 and 1")
        normalized[axis] = numeric
    for trigger in _GAMEPAD_TRIGGERS:
        numeric = _finite_number(state[trigger], f"{field_name}.{trigger}")
        if not 0 <= numeric <= 1:
            raise TelemetryProtocolError(f"{field_name}.{trigger} must be between 0 and 1")
        normalized[trigger] = numeric
    for button in _GAMEPAD_BUTTONS:
        if not isinstance(state[button], bool):
            raise TelemetryProtocolError(f"{field_name}.{button} must be a boolean")
        normalized[button] = state[button]
    return normalized


@dataclass(frozen=True, slots=True)
class DeviceDefinition:
    id: str
    label: str
    subsystem: str
    device_type: str

    def to_wire(self) -> dict[str, str]:
        return {
            "id": self.id,
            "label": self.label,
            "subsystem": self.subsystem,
            "deviceType": self.device_type,
        }


@dataclass(frozen=True, slots=True)
class SignalDefinition:
    id: str
    label: str
    device_id: str | None
    quantity: str
    unit: str
    value_type: str
    role: str
    sample_hint_hz: float | None = None
    description: str | None = None

    def to_wire(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            "label": self.label,
            "quantity": self.quantity,
            "unit": self.unit,
            "valueType": self.value_type,
            "role": self.role,
        }
        if self.device_id is not None:
            result["deviceId"] = self.device_id
        if self.sample_hint_hz is not None:
            result["sampleHintHz"] = self.sample_hint_hz
        if self.description is not None:
            result["description"] = self.description
        return result


@dataclass(frozen=True, slots=True)
class TelemetryCatalog:
    revision: int
    devices: dict[str, DeviceDefinition]
    signals: dict[str, SignalDefinition]

    def to_wire(self) -> dict[str, Any]:
        return {
            "schemaRevision": self.revision,
            "devices": [device.to_wire() for device in self.devices.values()],
            "signals": [signal.to_wire() for signal in self.signals.values()],
        }


@dataclass(frozen=True, slots=True)
class TelemetrySnapshot:
    session_id: str
    sample_sequence: str
    schema_revision: int
    robot_time_ns: str
    received_monotonic_ns: int
    received_at_ms: int
    values: dict[str, Any]

    def to_wire(self) -> dict[str, Any]:
        return {
            "sessionId": self.session_id,
            "sampleSequence": self.sample_sequence,
            "schemaRevision": self.schema_revision,
            "robotTimeNs": self.robot_time_ns,
            "receivedMonotonicNs": str(self.received_monotonic_ns),
            "receivedAtMs": self.received_at_ms,
            "values": self.values,
        }


@dataclass(frozen=True, slots=True)
class GamepadSnapshot:
    """One timestamped pair of FTC gamepad states for replay and overlay use."""

    robot_time_ns: str
    gamepad1: dict[str, Any]
    gamepad2: dict[str, Any]

    def to_wire(self) -> dict[str, Any]:
        return {
            "robotTimeNs": self.robot_time_ns,
            "gamepad1": self.gamepad1,
            "gamepad2": self.gamepad2,
        }


@dataclass(frozen=True, slots=True)
class TelemetryEvent:
    name: str
    severity: str
    robot_time_ns: str
    attributes: dict[str, str | float | bool | None]

    def to_wire(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "severity": self.severity,
            "robotTimeNs": self.robot_time_ns,
            "attributes": self.attributes,
        }


@dataclass(slots=True)
class TelemetrySession:
    id: str
    robot_id: str = ""
    robot_name: str = ""
    opmode_name: str = ""
    catalog: TelemetryCatalog | None = None
    snapshots: deque[TelemetrySnapshot] = field(default_factory=deque)
    gamepad_frames: deque[GamepadSnapshot] = field(default_factory=deque)
    events: deque[TelemetryEvent] = field(default_factory=deque)
    gaps: deque[dict[str, str] | dict[str, Any]] = field(default_factory=deque)
    latest_snapshot: TelemetrySnapshot | None = None
    active_connection_id: str | None = None
    ended: bool = False
    last_seen_at_ms: int = 0
    dropped_samples: str = "0"

    def summary(self) -> dict[str, Any]:
        return {
            "sessionId": self.id,
            "robotId": self.robot_id or None,
            "robotName": self.robot_name or None,
            "opModeName": self.opmode_name or None,
            "active": not self.ended and self.active_connection_id is not None,
            "ended": self.ended,
            "schemaRevision": self.catalog.revision if self.catalog else None,
            "signalCount": len(self.catalog.signals) if self.catalog else 0,
            "snapshotCount": len(self.snapshots),
            "gamepadFrameCount": len(self.gamepad_frames),
            "eventCount": len(self.events),
            "gapCount": len(self.gaps),
            "droppedSamples": self.dropped_samples,
            "lastSeenAtMs": self.last_seen_at_ms or None,
        }


@dataclass(frozen=True, slots=True)
class TelemetryUpdate:
    """A normalized update for websocket fan-out after one ingest operation."""

    kind: str
    data: dict[str, Any]


class TelemetryStore:
    """Keep bounded structured telemetry history for one or more OpMode sessions."""

    # Loop-time instrumentation can publish hundreds of snapshots per second. Retain
    # enough samples for the dashboard's 10-second time window at that rate.
    def __init__(self, *, max_snapshots_per_session: int = 12_000, max_events_per_session: int = 500) -> None:
        if max_snapshots_per_session <= 0 or max_events_per_session <= 0:
            raise ValueError("history limits must be positive")
        self._max_snapshots = max_snapshots_per_session
        self._max_events = max_events_per_session
        self._sessions: dict[str, TelemetrySession] = {}
        self._latest_session_id: str | None = None
        self._last_error: str | None = None
        self._lock = threading.RLock()

    def ingest(
        self,
        envelope: Mapping[str, Any],
        *,
        received_monotonic_ns: int,
        received_at_ms: int | None = None,
    ) -> list[TelemetryUpdate]:
        """Validate and add one structured protocol envelope.

        Legacy transport-test envelopes are intentionally ignored by this store;
        the existing raw-packet status UI can still show them until robot code
        has been migrated to the catalog/snapshot protocol.
        """

        if envelope.get("protocol") != PROTOCOL_NAME:
            return []
        received_at_ms = received_at_ms if received_at_ms is not None else int(time.time() * 1000)
        with self._lock:
            try:
                common = self._validate_envelope(envelope)
                message_type = common["type"]
                session = self._sessions.get(common["sessionId"])
                if message_type == "hello":
                    update = self._ingest_hello(common, received_at_ms)
                else:
                    if session is None:
                        raise TelemetryProtocolError("hello must be received before this message")
                    session.active_connection_id = common["connectionId"]
                    session.last_seen_at_ms = received_at_ms
                    if message_type == "catalog":
                        update = self._ingest_catalog(session, common)
                    elif message_type == "sample":
                        update = self._ingest_sample(session, common, received_monotonic_ns, received_at_ms)
                    elif message_type == "gamepad":
                        update = self._ingest_gamepad(session, common)
                    elif message_type == "event":
                        update = self._ingest_event(session, common)
                    elif message_type == "gap":
                        update = self._ingest_gap(session, common)
                    elif message_type == "heartbeat":
                        update = self._ingest_heartbeat(session, common)
                    elif message_type == "session_end":
                        update = self._ingest_session_end(session, common)
                    elif message_type.startswith("debug_"):
                        # Debugger control/state messages are handled by the
                        # DebugSessionService. They still update the session's
                        # liveness but are not telemetry samples.
                        update = []
                    else:
                        raise TelemetryProtocolError(f"Unsupported robot message type {message_type}")
                self._last_error = None
                return update
            except TelemetryProtocolError as error:
                self._last_error = str(error)
                raise

    def status(self) -> dict[str, Any]:
        with self._lock:
            session = self._current_session()
            return {
                "activeSession": session.summary() if session else None,
                "sessionCount": len(self._sessions),
                "lastError": self._last_error,
            }

    def latest_state(self, *, event_limit: int = 10) -> dict[str, Any]:
        """Return only the active session's latest state, never its history.

        The dashboard intentionally has a history-oriented ``live_state``
        method. Model-facing integrations need a bounded response so a status
        request cannot accidentally place thousands of samples in context.
        """

        if event_limit <= 0:
            raise ValueError("event_limit must be positive")
        with self._lock:
            session = self._current_session()
            if session is None:
                return {
                    "status": self.status(),
                    "session": None,
                    "catalog": None,
                    "snapshot": None,
                    "recentEvents": [],
                }
            return {
                "status": self.status(),
                "session": session.summary(),
                "catalog": session.catalog.to_wire() if session.catalog else None,
                "snapshot": session.latest_snapshot.to_wire() if session.latest_snapshot else None,
                "recentEvents": [event.to_wire() for event in list(session.events)[-event_limit:]],
            }

    def has_session(self, session_id: object) -> bool:
        """Return whether a valid-looking session is already known for resume."""

        return isinstance(session_id, str) and session_id in self._sessions

    def disconnect_connection(self, connection_id: object) -> list[TelemetryUpdate]:
        """Mark a TCP connection inactive when its transport closes.

        ``session_end`` is useful when the robot can send it, but an OpMode may
        stop or Wi-Fi may disappear before it does. Transport closure is still
        authoritative for the live dashboard's ``active`` flag.
        """

        if not isinstance(connection_id, str):
            return []
        with self._lock:
            updates: list[TelemetryUpdate] = []
            for session in self._sessions.values():
                if session.active_connection_id == connection_id:
                    session.active_connection_id = None
                    updates.append(TelemetryUpdate("telemetry_session", session.summary()))
            return updates

    def live_state(self, *, history_limit: int = 12_000) -> dict[str, Any]:
        """Return one browser-friendly bootstrap message for the active session."""

        if history_limit <= 0:
            raise ValueError("history_limit must be positive")
        with self._lock:
            session = self._current_session()
            if session is None:
                return {"status": self.status(), "session": None, "catalog": None, "snapshots": [], "gamepadFrames": [], "events": []}
            snapshots = list(session.snapshots)[-history_limit:]
            events = list(session.events)[-history_limit:]
            return {
                "status": self.status(),
                "session": session.summary(),
                "catalog": session.catalog.to_wire() if session.catalog else None,
                "snapshots": [snapshot.to_wire() for snapshot in snapshots],
                "gamepadFrames": [frame.to_wire() for frame in list(session.gamepad_frames)[-history_limit:]],
                "events": [event.to_wire() for event in events],
            }

    def _validate_envelope(self, envelope: Mapping[str, Any]) -> dict[str, Any]:
        if envelope.get("version") != PROTOCOL_VERSION:
            raise TelemetryProtocolError("Unsupported telemetry protocol version")
        message_type = _require_string(envelope.get("type"), "type")
        session_id = _require_uuid(envelope.get("sessionId"), "sessionId")
        connection_id = _require_uuid(envelope.get("connectionId"), "connectionId")
        sequence = _require_uint64(envelope.get("sequence"), "sequence")
        robot_time_ns = _require_uint64(envelope.get("robotTimeNs"), "robotTimeNs")
        data = _require_object(envelope.get("data"), "data")
        return {
            "type": message_type,
            "sessionId": session_id,
            "connectionId": connection_id,
            "sequence": sequence,
            "robotTimeNs": robot_time_ns,
            "data": data,
        }

    def _ingest_hello(self, common: Mapping[str, Any], received_at_ms: int) -> list[TelemetryUpdate]:
        data = common["data"]
        assert isinstance(data, Mapping)
        robot_id = _require_string(data.get("robotId"), "hello.data.robotId")
        robot_name = _require_string(data.get("robotName"), "hello.data.robotName")
        opmode_name = _require_string(data.get("opModeName"), "hello.data.opModeName")
        session_id = common["sessionId"]
        assert isinstance(session_id, str)
        session = self._sessions.get(session_id)
        if session is None:
            session = TelemetrySession(
                id=session_id,
                snapshots=deque(maxlen=self._max_snapshots),
                gamepad_frames=deque(maxlen=self._max_snapshots),
                events=deque(maxlen=self._max_events),
                gaps=deque(maxlen=self._max_events),
            )
            self._sessions[session_id] = session
        session.robot_id = robot_id
        session.robot_name = robot_name
        session.opmode_name = opmode_name
        session.active_connection_id = common["connectionId"]
        session.ended = False
        session.last_seen_at_ms = received_at_ms
        self._latest_session_id = session_id
        return [TelemetryUpdate("telemetry_session", session.summary())]

    def _ingest_catalog(self, session: TelemetrySession, common: Mapping[str, Any]) -> list[TelemetryUpdate]:
        data = common["data"]
        assert isinstance(data, Mapping)
        revision = data.get("schemaRevision")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision <= 0:
            raise TelemetryProtocolError("catalog.data.schemaRevision must be a positive integer")
        devices_raw = data.get("devices")
        signals_raw = data.get("signals")
        if not isinstance(devices_raw, list) or not isinstance(signals_raw, list):
            raise TelemetryProtocolError("catalog.data.devices and catalog.data.signals must be arrays")
        devices: dict[str, DeviceDefinition] = {}
        for raw in devices_raw:
            item = _require_object(raw, "catalog device")
            device = DeviceDefinition(
                id=_require_identifier(item.get("id"), "catalog device.id"),
                label=_require_string(item.get("label"), "catalog device.label"),
                subsystem=_require_identifier(item.get("subsystem"), "catalog device.subsystem"),
                device_type=_require_string(item.get("deviceType"), "catalog device.deviceType"),
            )
            if device.id in devices:
                raise TelemetryProtocolError(f"Duplicate device id {device.id}")
            devices[device.id] = device
        signals: dict[str, SignalDefinition] = {}
        for raw in signals_raw:
            item = _require_object(raw, "catalog signal")
            device_id = item.get("deviceId")
            if device_id is not None:
                device_id = _require_identifier(device_id, "catalog signal.deviceId")
                if device_id not in devices:
                    raise TelemetryProtocolError(f"Signal references unknown device {device_id}")
            value_type = _require_string(item.get("valueType"), "catalog signal.valueType")
            if value_type not in _VALUE_TYPES:
                raise TelemetryProtocolError(f"Unsupported signal valueType {value_type}")
            role = _require_string(item.get("role"), "catalog signal.role")
            if role not in _ROLES:
                raise TelemetryProtocolError(f"Unsupported signal role {role}")
            hint = item.get("sampleHintHz")
            if hint is not None:
                hint = _finite_number(hint, "catalog signal.sampleHintHz")
                if hint <= 0:
                    raise TelemetryProtocolError("catalog signal.sampleHintHz must be positive")
            description = item.get("description")
            if description is not None and not isinstance(description, str):
                raise TelemetryProtocolError("catalog signal.description must be a string")
            signal = SignalDefinition(
                id=_require_identifier(item.get("id"), "catalog signal.id"),
                label=_require_string(item.get("label"), "catalog signal.label"),
                device_id=device_id,
                quantity=_require_identifier(item.get("quantity"), "catalog signal.quantity"),
                unit=_require_string(item.get("unit"), "catalog signal.unit"),
                value_type=value_type,
                role=role,
                sample_hint_hz=hint,
                description=description,
            )
            if signal.id in signals:
                raise TelemetryProtocolError(f"Duplicate signal id {signal.id}")
            signals[signal.id] = signal
        session.catalog = TelemetryCatalog(revision=revision, devices=devices, signals=signals)
        return [TelemetryUpdate("telemetry_catalog", session.catalog.to_wire()), TelemetryUpdate("telemetry_session", session.summary())]

    def _ingest_sample(
        self,
        session: TelemetrySession,
        common: Mapping[str, Any],
        received_monotonic_ns: int,
        received_at_ms: int,
    ) -> list[TelemetryUpdate]:
        if session.catalog is None:
            raise TelemetryProtocolError("catalog must be received before samples")
        data = common["data"]
        assert isinstance(data, Mapping)
        sample_sequence = _require_uint64(data.get("sampleSequence"), "sample.data.sampleSequence")
        revision = data.get("schemaRevision")
        if revision != session.catalog.revision:
            raise TelemetryProtocolError("sample schemaRevision does not match the active catalog")
        values = _require_object(data.get("values"), "sample.data.values")
        expected_ids = set(session.catalog.signals)
        received_ids = set(values)
        if received_ids != expected_ids:
            missing = sorted(expected_ids - received_ids)
            unknown = sorted(received_ids - expected_ids)
            details: list[str] = []
            if missing:
                details.append(f"missing {', '.join(missing)}")
            if unknown:
                details.append(f"unknown {', '.join(unknown)}")
            raise TelemetryProtocolError(f"sample values must be a full catalog snapshot ({'; '.join(details)})")
        normalized = {
            signal_id: _validate_value(values[signal_id], definition.value_type, signal_id)
            for signal_id, definition in session.catalog.signals.items()
        }
        snapshot = TelemetrySnapshot(
            session_id=session.id,
            sample_sequence=sample_sequence,
            schema_revision=revision,
            robot_time_ns=common["robotTimeNs"],
            received_monotonic_ns=received_monotonic_ns,
            received_at_ms=received_at_ms,
            values=normalized,
        )
        session.snapshots.append(snapshot)
        session.latest_snapshot = snapshot
        return [TelemetryUpdate("telemetry_snapshot", snapshot.to_wire()), TelemetryUpdate("telemetry_session", session.summary())]

    def _ingest_gamepad(self, session: TelemetrySession, common: Mapping[str, Any]) -> list[TelemetryUpdate]:
        data = common["data"]
        assert isinstance(data, Mapping)
        frame = GamepadSnapshot(
            robot_time_ns=common["robotTimeNs"],
            gamepad1=_validate_gamepad(data.get("gamepad1"), "gamepad.data.gamepad1"),
            gamepad2=_validate_gamepad(data.get("gamepad2"), "gamepad.data.gamepad2"),
        )
        session.gamepad_frames.append(frame)
        return [TelemetryUpdate("telemetry_gamepad", frame.to_wire()), TelemetryUpdate("telemetry_session", session.summary())]

    def _ingest_event(self, session: TelemetrySession, common: Mapping[str, Any]) -> list[TelemetryUpdate]:
        data = common["data"]
        assert isinstance(data, Mapping)
        severity = _require_string(data.get("severity"), "event.data.severity")
        if severity not in _SEVERITIES:
            raise TelemetryProtocolError(f"Unsupported event severity {severity}")
        raw_attributes = _require_object(data.get("attributes", {}), "event.data.attributes")
        attributes: dict[str, str | float | bool | None] = {}
        for key, value in raw_attributes.items():
            if not isinstance(key, str) or not key:
                raise TelemetryProtocolError("event attribute keys must be non-empty strings")
            if isinstance(value, bool) or value is None or isinstance(value, str):
                attributes[key] = value
            elif isinstance(value, (int, float)) and math.isfinite(value):
                attributes[key] = float(value)
            else:
                raise TelemetryProtocolError("event attributes must be JSON primitives")
        event = TelemetryEvent(
            name=_require_identifier(data.get("name"), "event.data.name"),
            severity=severity,
            robot_time_ns=common["robotTimeNs"],
            attributes=attributes,
        )
        session.events.append(event)
        return [TelemetryUpdate("telemetry_event", event.to_wire()), TelemetryUpdate("telemetry_session", session.summary())]

    def _ingest_gap(self, session: TelemetrySession, common: Mapping[str, Any]) -> list[TelemetryUpdate]:
        data = common["data"]
        assert isinstance(data, Mapping)
        gap = {
            "firstMissingSampleSequence": _require_uint64(data.get("firstMissingSampleSequence"), "gap firstMissingSampleSequence"),
            "lastMissingSampleSequence": _require_uint64(data.get("lastMissingSampleSequence"), "gap lastMissingSampleSequence"),
            "reason": _require_string(data.get("reason"), "gap reason"),
            "robotTimeNs": common["robotTimeNs"],
        }
        session.gaps.append(gap)
        return [TelemetryUpdate("telemetry_gap", gap), TelemetryUpdate("telemetry_session", session.summary())]

    def _ingest_heartbeat(self, session: TelemetrySession, common: Mapping[str, Any]) -> list[TelemetryUpdate]:
        data = common["data"]
        assert isinstance(data, Mapping)
        dropped = _require_uint64(data.get("droppedSamples"), "heartbeat.data.droppedSamples")
        session.dropped_samples = dropped
        return [TelemetryUpdate("telemetry_session", session.summary())]

    def _ingest_session_end(self, session: TelemetrySession, common: Mapping[str, Any]) -> list[TelemetryUpdate]:
        data = common["data"]
        assert isinstance(data, Mapping)
        _require_string(data.get("reason"), "session_end.data.reason")
        session.ended = True
        session.active_connection_id = None
        return [TelemetryUpdate("telemetry_session", session.summary())]

    def _current_session(self) -> TelemetrySession | None:
        if self._latest_session_id is None:
            return None
        return self._sessions.get(self._latest_session_id)
