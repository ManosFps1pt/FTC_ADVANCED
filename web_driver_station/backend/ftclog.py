"""Crash-recoverable raw recorder for framed FTC robot-data protobuf streams.

An ``.ftclog`` stores the exact protobuf bytes accepted by the TCP listener,
plus the laptop monotonic receipt time.  It is intentionally an archival
format: replay views and analysis tables can be regenerated from it later.
"""

from __future__ import annotations

import os
import shutil
import struct
import threading
import time
import uuid
import zlib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path


class FtcLogError(RuntimeError):
    """Raised when an FTC log file is malformed or cannot be written."""


FORMAT_VERSION = 1
_MAGIC = b"FTCLOG\0\0"
_HEADER = struct.Struct("!8sHH16sQ")
_RECORD_PREFIX = struct.Struct("!IQI")
_CRC = struct.Struct("!I")
_MAX_FRAME_BYTES = 1_048_576


@dataclass(frozen=True, slots=True)
class FtcLogRecord:
    """One exact protobuf frame and when the laptop received it."""

    received_monotonic_ns: int
    protobuf_frame: bytes


class RawFtcLogRecorder:
    """Append raw frames to session-scoped files and atomically finalize them."""

    def __init__(self, recordings_root: Path) -> None:
        self._recordings_root = recordings_root
        self._writers: dict[str, tuple[Path, object]] = {}
        self._lock = threading.RLock()
        self._last_error: str | None = None

    @property
    def recordings_root(self) -> Path:
        return self._recordings_root

    def status(self) -> dict[str, object]:
        with self._lock:
            return {
                "active_log_count": len(self._writers),
                "active_sessions": sorted(self._writers),
                "last_error": self._last_error,
                "recordings_root": str(self._recordings_root),
            }

    def append(self, session_id: str, protobuf_frame: bytes, received_monotonic_ns: int) -> Path:
        """Append one raw protobuf frame and return its in-progress log path."""

        normalized_session_id = _normalize_session_id(session_id)
        if not protobuf_frame or len(protobuf_frame) > _MAX_FRAME_BYTES:
            raise FtcLogError("Invalid protobuf frame length for .ftclog")
        if received_monotonic_ns < 0:
            raise FtcLogError("Receipt timestamp must be non-negative")
        with self._lock:
            try:
                partial_path, handle = self._writers.get(normalized_session_id, (None, None))
                if partial_path is None or handle is None:
                    partial_path, handle = self._open_log(normalized_session_id)
                    self._writers[normalized_session_id] = (partial_path, handle)
                payload = _RECORD_PREFIX.pack(len(protobuf_frame), received_monotonic_ns, len(protobuf_frame)) + protobuf_frame
                checksum = zlib.crc32(payload) & 0xFFFFFFFF
                handle.write(payload)
                handle.write(_CRC.pack(checksum))
                handle.flush()
                self._last_error = None
                return partial_path
            except OSError as error:
                self._last_error = str(error)
                raise FtcLogError(f"Could not write .ftclog: {error}") from error

    def close_session(self, session_id: str) -> Path | None:
        """Finalize the active file for a session and return its final path."""

        normalized_session_id = _normalize_session_id(session_id)
        with self._lock:
            entry = self._writers.pop(normalized_session_id, None)
            if entry is None:
                return None
            partial_path, handle = entry
            try:
                handle.flush()
                os.fsync(handle.fileno())
                handle.close()
                final_path = partial_path.with_suffix("")
                os.replace(partial_path, final_path)
                self._last_error = None
                return final_path
            except OSError as error:
                self._last_error = str(error)
                raise FtcLogError(f"Could not finalize .ftclog: {error}") from error

    def close_all(self) -> None:
        with self._lock:
            session_ids = tuple(self._writers)
        for session_id in session_ids:
            self.close_session(session_id)

    def list_sessions(self) -> list[dict[str, object]]:
        if not self._recordings_root.is_dir():
            return []
        sessions: list[dict[str, object]] = []
        for session_path in self._recordings_root.iterdir():
            raw_path = session_path / "raw"
            if not session_path.is_dir() or not raw_path.is_dir() or not _is_session_id(session_path.name):
                continue
            files = sorted(path.name for path in raw_path.glob("stream-*.ftclog"))
            partial_files = sorted(path.name for path in raw_path.glob("stream-*.ftclog.partial"))
            if files or partial_files:
                sessions.append({"session_id": session_path.name, "logs": files, "partial_logs": partial_files})
        return sorted(sessions, key=lambda item: str(item["session_id"]), reverse=True)

    def log_path(self, session_id: str, log_name: str) -> Path:
        normalized_session_id = _normalize_session_id(session_id)
        if not log_name.startswith("stream-") or not log_name.endswith(".ftclog") or "/" in log_name or "\\" in log_name:
            raise FtcLogError("Invalid .ftclog file name")
        path = self._recordings_root / normalized_session_id / "raw" / log_name
        if not path.is_file():
            raise FtcLogError(".ftclog file was not found")
        return path

    def session_path(self, session_id: str) -> Path:
        """Return one finalized session directory after validating its UUID path."""

        normalized_session_id = _normalize_session_id(session_id)
        path = self._recordings_root / normalized_session_id
        if not path.is_dir():
            raise FtcLogError("Recording session was not found")
        return path

    def delete_session(self, session_id: str) -> None:
        """Delete one finalized UUID-named session directory."""

        normalized_session_id = _normalize_session_id(session_id)
        with self._lock:
            if normalized_session_id in self._writers:
                raise FtcLogError("Cannot delete an active recording session")
            path = self.session_path(normalized_session_id)
            shutil.rmtree(path)

    def _open_log(self, session_id: str) -> tuple[Path, object]:
        raw_path = self._recordings_root / session_id / "raw"
        raw_path.mkdir(parents=True, exist_ok=True)
        indices = [int(path.stem.split("-")[-1]) for path in raw_path.glob("stream-*.ftclog")]
        indices.extend(int(path.name.removesuffix(".ftclog.partial").split("-")[-1]) for path in raw_path.glob("stream-*.ftclog.partial"))
        index = max(indices, default=-1) + 1
        partial_path = raw_path / f"stream-{index:05d}.ftclog.partial"
        handle = partial_path.open("xb")
        handle.write(_HEADER.pack(_MAGIC, FORMAT_VERSION, _HEADER.size, uuid.UUID(session_id).bytes, time.perf_counter_ns()))
        handle.flush()
        return partial_path, handle


def iter_records(path: Path) -> Iterator[FtcLogRecord]:
    """Yield valid records, stopping safely at an incomplete crash tail."""

    try:
        with path.open("rb") as handle:
            header = handle.read(_HEADER.size)
            if len(header) != _HEADER.size:
                raise FtcLogError("Truncated .ftclog header")
            magic, version, header_size, _session_id, _started_ns = _HEADER.unpack(header)
            if magic != _MAGIC or version != FORMAT_VERSION or header_size != _HEADER.size:
                raise FtcLogError("Unsupported .ftclog header")
            while True:
                prefix = handle.read(_RECORD_PREFIX.size)
                if not prefix:
                    return
                if len(prefix) != _RECORD_PREFIX.size:
                    return
                record_length, received_monotonic_ns, frame_length = _RECORD_PREFIX.unpack(prefix)
                if frame_length == 0 or frame_length > _MAX_FRAME_BYTES or record_length != frame_length:
                    raise FtcLogError("Invalid .ftclog record length")
                frame = handle.read(frame_length)
                checksum = handle.read(_CRC.size)
                if len(frame) != frame_length or len(checksum) != _CRC.size:
                    return
                if zlib.crc32(prefix + frame) & 0xFFFFFFFF != _CRC.unpack(checksum)[0]:
                    raise FtcLogError(".ftclog record checksum mismatch")
                yield FtcLogRecord(received_monotonic_ns=received_monotonic_ns, protobuf_frame=frame)
    except OSError as error:
        raise FtcLogError(f"Could not read .ftclog: {error}") from error


def _normalize_session_id(value: str) -> str:
    if not isinstance(value, str):
        raise FtcLogError("Session ID must be a UUID")
    try:
        return str(uuid.UUID(value))
    except ValueError as error:
        raise FtcLogError("Session ID must be a UUID") from error


def _is_session_id(value: str) -> bool:
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False
