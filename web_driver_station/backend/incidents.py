"""Durable, source-linked incident ranges for one recorded telemetry session."""

from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class IncidentError(RuntimeError):
    """An incident cannot be persisted safely."""


@dataclass(slots=True)
class _OpenIncident:
    id: str
    session_id: str
    first_sample_sequence: str
    first_robot_time_ns: str
    last_sample_sequence: str
    last_robot_time_ns: str
    segments: list[dict[str, str]] = field(default_factory=list)


class IncidentRecorder:
    """Store effective highlighted ranges beside, but never inside, raw packets."""

    def __init__(self, recordings_root: Path) -> None:
        self._root = recordings_root
        self._open: dict[str, _OpenIncident] = {}
        self._lock = threading.RLock()

    def observe_snapshot(self, snapshot: dict[str, Any]) -> None:
        """Advance one session's incident state after its raw packet was archived."""

        session_id = _text(snapshot.get("sessionId"), "session ID")
        sequence = _text(snapshot.get("sampleSequence"), "sample sequence")
        robot_time_ns = _text(snapshot.get("robotTimeNs"), "robot timestamp")
        highlighted = snapshot.get("highlighted") is True
        source = snapshot.get("highlightSource")
        if highlighted and source not in {"control_hub", "telemetry_lab"}:
            raise IncidentError("Highlighted snapshots require a known source")

        with self._lock:
            current = self._open.get(session_id)
            if not highlighted:
                if current is not None:
                    self._finalize_locked(current)
                return

            assert isinstance(source, str)
            if current is None:
                current = _OpenIncident(
                    id=str(uuid.uuid4()),
                    session_id=session_id,
                    first_sample_sequence=sequence,
                    first_robot_time_ns=robot_time_ns,
                    last_sample_sequence=sequence,
                    last_robot_time_ns=robot_time_ns,
                    segments=[_segment(sequence, robot_time_ns, source)],
                )
                self._open[session_id] = current
            else:
                last_segment = current.segments[-1]
                if last_segment["source"] != source:
                    current.segments.append(_segment(sequence, robot_time_ns, source))

            current.last_sample_sequence = sequence
            current.last_robot_time_ns = robot_time_ns
            current.segments[-1]["lastSampleSequence"] = sequence
            current.segments[-1]["endRobotTimeNs"] = robot_time_ns
            self._write_locked(current, suffix=".partial")

    def close_session(self, session_id: str) -> None:
        """Atomically publish any open incident once its telemetry session ends."""

        with self._lock:
            current = self._open.get(session_id)
            if current is not None:
                self._finalize_locked(current)

    def _finalize_locked(self, incident: _OpenIncident) -> None:
        self._write_locked(incident, suffix="")
        partial = self._path(incident, suffix=".partial")
        if partial.exists():
            partial.unlink()
        self._open.pop(incident.session_id, None)

    def _write_locked(self, incident: _OpenIncident, *, suffix: str) -> None:
        target = self._path(incident, suffix=suffix)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "id": incident.id,
            "sessionId": incident.session_id,
            "firstSampleSequence": incident.first_sample_sequence,
            "lastSampleSequence": incident.last_sample_sequence,
            "startRobotTimeNs": incident.first_robot_time_ns,
            "endRobotTimeNs": incident.last_robot_time_ns,
            "sources": sorted({segment["source"] for segment in incident.segments}),
            "segments": incident.segments,
            "artifacts": {"raw": "raw", "video": "video"},
        }
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("x", encoding="utf-8") as handle:
                json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        except OSError as error:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise IncidentError(f"Could not persist incident: {error}") from error

    def _path(self, incident: _OpenIncident, *, suffix: str) -> Path:
        return self._root / incident.session_id / "incidents" / f"{incident.id}.json{suffix}"


def _segment(sequence: str, robot_time_ns: str, source: str) -> dict[str, str]:
    return {
        "source": source,
        "firstSampleSequence": sequence,
        "lastSampleSequence": sequence,
        "startRobotTimeNs": robot_time_ns,
        "endRobotTimeNs": robot_time_ns,
    }


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise IncidentError(f"Incident {label} is required")
    return value
