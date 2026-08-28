"""Secure, atomic upload of a finalized FTC recording session over SFTP.

The viewer only indexes folders in the final recordings root.  This uploader
first writes to ``.incoming/<session-id>`` and only then renames the complete
folder into view.  Re-running an interrupted upload resumes files whose remote
size already matches the local source.
"""

from __future__ import annotations

import argparse
import json
import os
import posixpath
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


class RecordingUploadError(RuntimeError):
    """Raised when a recording cannot be safely uploaded."""


@dataclass(frozen=True, slots=True)
class UploadSettings:
    host: str
    username: str
    private_key: Path
    remote_root: str = "/srv/ftc-recordings"
    port: int = 22
    known_hosts: Path = Path.home() / ".ssh" / "known_hosts"

    @classmethod
    def from_environment(cls) -> UploadSettings | None:
        """Load environment settings, or the ignored local settings file.

        The local file keeps a manually started Driver Station from silently
        losing recording uploads because it did not inherit the batch launcher's
        environment variables. Environment variables still take precedence.
        """

        host = os.getenv("FTC_RECORDING_UPLOAD_HOST")
        username = os.getenv("FTC_RECORDING_UPLOAD_USERNAME")
        private_key = os.getenv("FTC_RECORDING_UPLOAD_PRIVATE_KEY")
        if any((host, username, private_key)):
            if not all((host, username, private_key)):
                raise RecordingUploadError(
                    "Set FTC_RECORDING_UPLOAD_HOST, FTC_RECORDING_UPLOAD_USERNAME, "
                    "and FTC_RECORDING_UPLOAD_PRIVATE_KEY together"
                )
            return cls(
                host=host,
                username=username,
                private_key=Path(private_key).expanduser(),
                remote_root=os.getenv("FTC_RECORDING_UPLOAD_ROOT", "/srv/ftc-recordings"),
                port=int(os.getenv("FTC_RECORDING_UPLOAD_PORT", "22")),
                known_hosts=_environment_known_hosts(),
            )

        config_path = Path(os.getenv(
            "FTC_RECORDING_UPLOAD_CONFIG",
            str(Path(__file__).resolve().parents[1] / "recording_upload.local.json"),
        )).expanduser()
        if not config_path.is_file():
            return None
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
            if not isinstance(config, dict):
                raise TypeError("must be a JSON object")
            host = _required_config_string(config, "host")
            username = _required_config_string(config, "username")
            private_key_path = _config_path(config_path, _required_config_string(config, "private_key"))
            known_hosts_value = config.get("known_hosts")
            remote_root = str(config.get("remote_root", "/srv/ftc-recordings"))
            port = int(config.get("port", 22))
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise RecordingUploadError(f"Invalid recording upload settings file {config_path}: {error}") from error
        return cls(
            host=host,
            username=username,
            private_key=private_key_path,
            remote_root=remote_root,
            port=port,
            known_hosts=_config_path(config_path, known_hosts_value) if isinstance(known_hosts_value, str) and known_hosts_value.strip() else _default_known_hosts(),
        )


@dataclass(frozen=True, slots=True)
class UploadResult:
    session_id: str
    uploaded_files: int
    skipped_files: int
    uploaded_bytes: int
    already_present: bool = False


def _required_config_string(config: dict[object, object], name: str) -> str:
    value = config.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name!r} must be a non-empty string")
    return value


def _config_path(config_path: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else config_path.parent / path


def _default_known_hosts() -> Path:
    return Path.home() / ".ssh" / "known_hosts"


def _environment_known_hosts() -> Path:
    value = os.getenv("FTC_RECORDING_UPLOAD_KNOWN_HOSTS")
    return Path(value).expanduser() if value else _default_known_hosts()


class RecordingUploader:
    """Upload exactly one completed session without trusting unknown SSH hosts."""

    def __init__(self, settings: UploadSettings) -> None:
        self.settings = settings

    def upload(
        self,
        recording_directory: Path,
        progress: Callable[[str], None] | None = None,
    ) -> UploadResult:
        source, session_id, files = _validated_session(recording_directory)
        _announce(progress, f"Connecting to {self.settings.host} as {self.settings.username}")
        client = self._connect()
        try:
            sftp = client.open_sftp()
            try:
                remote_root = _normal_remote_root(self.settings.remote_root)
                final_path = posixpath.join(remote_root, session_id)
                stage_path = posixpath.join(remote_root, ".incoming", session_id)
                if _remote_exists(sftp, final_path):
                    _announce(progress, "Recording is already present on the server")
                    return UploadResult(session_id, 0, len(files), 0, already_present=True)
                _mkdirs(sftp, stage_path)
                uploaded_files = skipped_files = uploaded_bytes = 0
                for local_path in files:
                    relative = local_path.relative_to(source).as_posix()
                    remote_path = posixpath.join(stage_path, relative)
                    _mkdirs(sftp, posixpath.dirname(remote_path))
                    size = local_path.stat().st_size
                    if _remote_size(sftp, remote_path) == size:
                        skipped_files += 1
                        _announce(progress, f"Already uploaded: {relative}")
                        continue
                    existing_size = _remote_size(sftp, remote_path)
                    resume_at = existing_size if existing_size is not None and 0 <= existing_size < size else 0
                    # ``ab`` relies on server-side append semantics that vary
                    # between SFTP servers.  Seek the remote handle explicitly
                    # instead, so a resumed file ends at the exact source size.
                    mode = "r+" if resume_at else "wb"
                    _announce(progress, f"{'Resuming' if resume_at else 'Uploading'}: {relative}")
                    written = resume_at
                    next_progress = ((written // (5 * 1024 * 1024)) + 1) * 5 * 1024 * 1024
                    with local_path.open("rb") as local_handle, sftp.file(remote_path, mode) as remote_handle:
                        local_handle.seek(resume_at)
                        remote_handle.seek(resume_at)
                        while chunk := local_handle.read(1024 * 1024):
                            remote_handle.write(chunk)
                            written += len(chunk)
                            if written >= next_progress or written == size:
                                _announce(progress, f"Uploading {relative}: {written / 1024 / 1024:.1f} / {size / 1024 / 1024:.1f} MiB")
                                next_progress += 5 * 1024 * 1024
                    if _remote_size(sftp, remote_path) != size:
                        raise RecordingUploadError(f"Remote size verification failed for {relative}")
                    uploaded_files += 1
                    uploaded_bytes += size
                _rename_into_library(client, stage_path, final_path)
                _announce(progress, "Upload complete; recording is now visible in the library")
                return UploadResult(session_id, uploaded_files, skipped_files, uploaded_bytes)
            finally:
                sftp.close()
        finally:
            client.close()

    def delete(self, session_id: str) -> None:
        """Delete a finalized or staged remote session, if it exists."""

        try:
            normalized_session_id = str(uuid.UUID(session_id))
        except ValueError as error:
            raise RecordingUploadError("Recording session ID must be a UUID") from error
        client = self._connect()
        try:
            sftp = client.open_sftp()
            try:
                remote_root = _normal_remote_root(self.settings.remote_root)
                _remove_remote_tree(sftp, posixpath.join(remote_root, normalized_session_id))
                _remove_remote_tree(sftp, posixpath.join(remote_root, ".incoming", normalized_session_id))
            finally:
                sftp.close()
        finally:
            client.close()

    def _connect(self):
        try:
            import paramiko
        except ImportError as error:
            raise RecordingUploadError("paramiko is required; install web_driver_station/backend/requirements.txt") from error
        key_path = self.settings.private_key.expanduser().resolve()
        known_hosts = self.settings.known_hosts.expanduser().resolve()
        if not key_path.is_file():
            raise RecordingUploadError(f"SSH private key was not found: {key_path}")
        if not known_hosts.is_file():
            raise RecordingUploadError(
                f"Known-hosts file was not found: {known_hosts}. Connect once with OpenSSH using StrictHostKeyChecking=yes first."
            )
        client = paramiko.SSHClient()
        client.load_system_host_keys()
        client.load_host_keys(str(known_hosts))
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        try:
            client.connect(
                hostname=self.settings.host,
                port=self.settings.port,
                username=self.settings.username,
                key_filename=str(key_path),
                look_for_keys=False,
                allow_agent=False,
                timeout=15,
                banner_timeout=15,
                auth_timeout=15,
            )
        except Exception as error:
            client.close()
            raise RecordingUploadError(f"SSH connection failed: {error}") from error
        return client


def _validated_session(recording_directory: Path) -> tuple[Path, str, list[Path]]:
    source = recording_directory.expanduser().resolve()
    try:
        session_id = str(uuid.UUID(source.name))
    except ValueError as error:
        raise RecordingUploadError("Recording directory name must be its UUID session ID") from error
    raw_directory = source / "raw"
    log_files = list(raw_directory.glob("stream-*.ftclog")) if raw_directory.is_dir() else []
    partial_files = list(source.rglob("*.partial")) if source.is_dir() else []
    if not log_files:
        raise RecordingUploadError("Recording has no finalized .ftclog file")
    if partial_files:
        raise RecordingUploadError("Recording still contains partial files; wait until recording finalizes")
    files: list[Path] = []
    for path in source.rglob("*"):
        if path.is_symlink():
            raise RecordingUploadError(f"Recording cannot contain symlinks: {path}")
        if path.is_file():
            files.append(path)
    if not files:
        raise RecordingUploadError("Recording has no files to upload")
    return source, session_id, sorted(files)


def _normal_remote_root(value: str) -> str:
    if not value.startswith("/") or ".." in value.split("/"):
        raise RecordingUploadError("Remote recordings root must be an absolute, normalized POSIX path")
    return value.rstrip("/") or "/"


def _remote_exists(sftp, path: str) -> bool:
    try:
        sftp.stat(path)
        return True
    except OSError:
        return False


def _remote_size(sftp, path: str) -> int | None:
    try:
        return sftp.stat(path).st_size
    except OSError:
        return None


def _mkdirs(sftp, path: str) -> None:
    current = "/"
    for part in path.strip("/").split("/"):
        current = posixpath.join(current, part)
        try:
            attributes = sftp.stat(current)
        except OSError:
            sftp.mkdir(current)
            continue
        if not stat.S_ISDIR(attributes.st_mode):
            raise RecordingUploadError(f"Remote path is not a directory: {current}")


def _remove_remote_tree(sftp, path: str) -> None:
    """Remove one exact remote file/directory tree through SFTP."""

    try:
        attributes = sftp.stat(path)
    except OSError:
        return
    if stat.S_ISDIR(attributes.st_mode):
        for entry in sftp.listdir_attr(path):
            _remove_remote_tree(sftp, posixpath.join(path, entry.filename))
        sftp.rmdir(path)
    else:
        sftp.remove(path)


def _rename_into_library(client, stage_path: str, final_path: str) -> None:
    # Values are constructed from a configured absolute root plus a UUID; quote
    # regardless so a configuration error cannot become a remote shell command.
    import shlex

    command = f"test ! -e {shlex.quote(final_path)} && mv {shlex.quote(stage_path)} {shlex.quote(final_path)}"
    _stdin, stdout, stderr = client.exec_command(command)
    exit_status = stdout.channel.recv_exit_status()
    if exit_status != 0:
        detail = stderr.read().decode("utf-8", errors="replace").strip()
        raise RecordingUploadError(f"Could not finalize remote recording: {detail or 'destination already exists'}")


def _announce(progress: Callable[[str], None] | None, message: str) -> None:
    if progress is not None:
        progress(message)


def main() -> int:
    parser = argparse.ArgumentParser(description="Atomically upload one finalized FTC recording session")
    parser.add_argument("recording_directory", type=Path, help="Session folder containing raw/*.ftclog and optional video/")
    parser.add_argument("--host", default=os.getenv("FTC_RECORDING_UPLOAD_HOST"))
    parser.add_argument("--username", default=os.getenv("FTC_RECORDING_UPLOAD_USERNAME"))
    parser.add_argument("--private-key", type=Path, default=os.getenv("FTC_RECORDING_UPLOAD_PRIVATE_KEY"))
    parser.add_argument("--known-hosts", type=Path, default=os.getenv("FTC_RECORDING_UPLOAD_KNOWN_HOSTS", Path.home() / ".ssh" / "known_hosts"))
    parser.add_argument("--remote-root", default=os.getenv("FTC_RECORDING_UPLOAD_ROOT", "/srv/ftc-recordings"))
    parser.add_argument("--port", type=int, default=int(os.getenv("FTC_RECORDING_UPLOAD_PORT", "22")))
    args = parser.parse_args()
    if not args.host or not args.username or args.private_key is None:
        parser.error("--host, --username, and --private-key are required (or set the FTC_RECORDING_UPLOAD_* variables)")
    settings = UploadSettings(args.host, args.username, args.private_key, args.remote_root, args.port, args.known_hosts)
    try:
        result = RecordingUploader(settings).upload(args.recording_directory, print)
    except RecordingUploadError as error:
        parser.error(str(error))
    print(f"Session {result.session_id}: {result.uploaded_files} uploaded, {result.skipped_files} already present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
