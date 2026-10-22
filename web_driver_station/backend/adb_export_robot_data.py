"""Pull LOCAL_MATCH recordings from a Control Hub over ADB.

This is intentionally a post-match import tool. It never starts a TCP server,
does not touch the Driver Station link, and never deletes a Control Hub file.

Run from the repository root after connecting the Control Hub by USB or ADB:

    python -m web_driver_station.backend.adb_export_robot_data --serial SERIAL
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

from .adb_camera import AdbCameraError, _find_adb, _parse_adb_devices
from .ftclog import FtcLogError, iter_records


REMOTE_RECORDINGS_DIR = "/sdcard/FIRST/data/ftc-advanced-recordings"


class AdbRobotDataError(RuntimeError):
    """A Control Hub recording could not be safely imported."""


@dataclass(frozen=True, slots=True)
class ImportResult:
    serial: str
    imported_sessions: tuple[str, ...]
    already_present_sessions: tuple[str, ...]
    verified_frames: int


def export_recordings(
    recordings_dir: Path,
    *,
    serial: str | None = None,
    adb_path: str | None = None,
) -> ImportResult:
    """Copy and verify every completed or recoverable LOCAL_MATCH recording."""

    adb = adb_path or _find_adb()
    selected_serial = _select_device(adb, serial)
    _run(adb, selected_serial, ["shell", "test", "-d", REMOTE_RECORDINGS_DIR])

    destination = recordings_dir.expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    staging = destination / f".adb-import-{uuid.uuid4()}"
    staging.mkdir()
    try:
        _run(adb, selected_serial, ["pull", REMOTE_RECORDINGS_DIR + "/.", str(staging)], timeout=120)
        sessions, frames = _validate_staging(staging)
        imported: list[str] = []
        present: list[str] = []
        for session in sessions:
            source = staging / session
            target = destination / session
            if target.exists():
                present.append(session)
                continue
            shutil.move(str(source), str(target))
            imported.append(session)
        return ImportResult(selected_serial, tuple(imported), tuple(present), frames)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _select_device(adb: str, requested_serial: str | None) -> str:
    result = subprocess.run([adb, "devices", "-l"], capture_output=True, text=True, timeout=10, check=False)
    if result.returncode != 0:
        raise AdbRobotDataError(_command_error("adb devices", result))
    devices = _parse_adb_devices(result.stdout)
    if requested_serial:
        state = devices.get(requested_serial, {}).get("state")
        if state != "device":
            raise AdbRobotDataError(f"ADB device {requested_serial!r} is not ready (state: {state or 'not found'})")
        return requested_serial
    ready = sorted(key for key, value in devices.items() if value.get("state") == "device")
    if len(ready) != 1:
        choices = ", ".join(ready) if ready else "none"
        raise AdbRobotDataError(f"Expected exactly one ready ADB device; found {choices}. Pass --serial explicitly.")
    return ready[0]


def _run(adb: str, serial: str, arguments: list[str], *, timeout: float = 30) -> subprocess.CompletedProcess[str]:
    result = subprocess.run([adb, "-s", serial, *arguments], capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode != 0:
        command = "adb " + " ".join(arguments[:2])
        raise AdbRobotDataError(_command_error(command, result))
    return result


def _validate_staging(staging: Path) -> tuple[tuple[str, ...], int]:
    sessions: list[str] = []
    frames = 0
    for session_path in sorted(path for path in staging.iterdir() if path.is_dir()):
        try:
            session_id = str(uuid.UUID(session_path.name))
        except ValueError:
            continue
        raw = session_path / "raw"
        if not raw.is_dir():
            raise AdbRobotDataError(f"{session_id} has no raw recording directory")
        logs = sorted((*raw.glob("*.ftclog"), *raw.glob("*.ftclog.partial")))
        if not logs:
            raise AdbRobotDataError(f"{session_id} contains no .ftclog data")
        try:
            for log in logs:
                frames += sum(1 for _ in iter_records(log))
        except FtcLogError as error:
            raise AdbRobotDataError(f"{session_id} failed integrity validation: {error}") from error
        sessions.append(session_id)
    if not sessions:
        raise AdbRobotDataError(f"No recordings found at {REMOTE_RECORDINGS_DIR}")
    return tuple(sessions), frames


def _command_error(command: str, result: subprocess.CompletedProcess[str]) -> str:
    detail = (result.stderr or result.stdout).strip()
    return f"{command} failed" + (f": {detail}" if detail else "")


def main() -> int:
    parser = argparse.ArgumentParser(description="Import Control Hub LOCAL_MATCH data with ADB")
    parser.add_argument("--serial", help="ADB serial; required when more than one device is connected")
    parser.add_argument("--adb", help="Path to adb executable; defaults to Android Platform-Tools")
    parser.add_argument(
        "--recordings-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "control_hub_recordings",
        help="Laptop directory for verified Control Hub session folders",
    )
    args = parser.parse_args()
    try:
        result = export_recordings(args.recordings_dir, serial=args.serial, adb_path=args.adb)
    except (AdbCameraError, AdbRobotDataError, OSError, subprocess.TimeoutExpired) as error:
        print(f"Import failed: {error}")
        return 1
    print(
        f"Imported {len(result.imported_sessions)} session(s), "
        f"skipped {len(result.already_present_sessions)} existing session(s), "
        f"and verified {result.verified_frames} frame(s) from {result.serial}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
