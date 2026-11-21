"""Inventory for finalized local recordings shared by the UI and review tools."""

from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from google.protobuf.message import DecodeError

from .ftclog import FtcLogError, is_closed_partial, iter_records
from .protocol import robot_data_pb2 as wire
from .recording_metadata import read_recording_metadata


def saved_streams(directory: Path) -> list[Path]:
    raw = directory / "raw"
    if raw.is_symlink():
        return []
    streams = sorted(raw.glob("stream-*.ftclog*"))
    streams = [p for p in streams if p.name.endswith((".ftclog", ".ftclog.partial"))]
    if any(p.is_symlink() or (p.suffix == ".partial" and not is_closed_partial(p)) for p in streams):
        return []
    return streams


def describe_run(directory: Path, streams: list[Path]) -> dict:
    opmode = robot = None
    try:
        for record in iter_records(streams[0]):
            frame = wire.Envelope.FromString(record.protobuf_frame)
            if frame.HasField("hello"):
                opmode, robot = frame.hello.op_mode_name, frame.hello.robot_name
                break
            if frame.HasField("sample_batch"):
                break
    except (FtcLogError, DecodeError):
        pass
    captured_ns = max(path.stat().st_mtime_ns for path in streams)
    captured_at = datetime.fromtimestamp(captured_ns / 1e9, tz=timezone.utc)
    metadata = read_recording_metadata(directory)
    title = f"{opmode or 'Recording'} · {captured_at.astimezone():%d %b %H:%M:%S}"
    return {
        "session_id": directory.name,
        "title": metadata["title"] if metadata else title[:120],
        "description": metadata["description"] if metadata else "",
        "opmode": opmode,
        "robot": robot,
        "captured_at": captured_at.isoformat(),
        "interrupted": any(p.suffix == ".partial" for p in streams),
        "logs": [p.name for p in streams if p.suffix == ".ftclog"],
        "partial_logs": [p.name for p in streams if p.suffix == ".partial"],
        "size_bytes": sum(p.stat().st_size for p in directory.rglob("*") if not p.is_symlink() and p.is_file()),
    }


def saved_runs(root: Path, excluded: set[str]) -> list[dict]:
    runs = []
    if not root.is_dir():
        return runs
    for directory in root.iterdir():
        if directory.name in excluded or directory.is_symlink() or not directory.is_dir():
            continue
        try:
            if str(UUID(directory.name)) != directory.name:
                continue
            streams = saved_streams(directory)
            if streams:
                runs.append(describe_run(directory, streams))
        except (ValueError, OSError):
            # The operator can remove a recording while a reader enumerates it.
            continue
    return sorted(runs, key=lambda run: run["captured_at"], reverse=True)
