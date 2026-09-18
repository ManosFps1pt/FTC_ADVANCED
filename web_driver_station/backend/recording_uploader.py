"""Secure, atomic upload of a finalized FTC recording session over SFTP.

The viewer only indexes folders in the final recordings root.  This uploader
first writes to ``.incoming/<session-id>`` and only then renames the complete
folder into view.  Re-running an interrupted upload resumes files whose remote
size already matches the local source.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import hmac
import json
import os
import posixpath
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ftc_local_config import LocalConfigError, local_config_path, section


class RecordingUploadError(RuntimeError):
    """Raised when a recording cannot be safely uploaded."""


UploadProgressCallback = Callable[[int, int, str | None], None]


@dataclass(frozen=True, slots=True)
class UploadSettings:
    host: str
    username: str
    private_key: Path
    remote_root: str = "/srv/ftc-recordings"
    port: int = 22
    known_hosts: Path | None = Path.home() / ".ssh" / "known_hosts"
    host_key_fingerprint: str | None = None

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

        try:
            secret_settings = section("recording_upload")
        except LocalConfigError as error:
            raise RecordingUploadError(str(error)) from error
        if secret_settings:
            return _settings_from_config(secret_settings, local_config_path())

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
            return _settings_from_config(config, config_path)
        except (OSError, TypeError, ValueError, json.JSONDecodeError, LocalConfigError) as error:
            raise RecordingUploadError(f"Invalid recording upload settings file {config_path}: {error}") from error


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


def _settings_from_config(config: dict[object, object], config_path: Path) -> UploadSettings:
    host = _required_config_string(config, "host")
    username = _required_config_string(config, "username")
    private_key_path = _config_path(config_path, _required_config_string(config, "private_key"))
    known_hosts_value = config.get("known_hosts")
    host_key_fingerprint = _optional_host_key_fingerprint(config.get("host_key_fingerprint"))
    remote_root = str(config.get("remote_root", "/srv/ftc-recordings"))
    port = int(config.get("port", 22))
    return UploadSettings(
        host=host,
        username=username,
        private_key=private_key_path,
        remote_root=remote_root,
        port=port,
        # A fingerprint deliberately wins when migrating an existing device
        # that still has a legacy known_hosts setting.
        known_hosts=None if host_key_fingerprint is not None else (_config_path(config_path, known_hosts_value) if isinstance(known_hosts_value, str) and known_hosts_value.strip() else _default_known_hosts()),
        host_key_fingerprint=host_key_fingerprint,
    )


def _config_path(config_path: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else config_path.parent / path


def _default_known_hosts() -> Path:
    return Path.home() / ".ssh" / "known_hosts"


def _environment_known_hosts() -> Path:
    value = os.getenv("FTC_RECORDING_UPLOAD_KNOWN_HOSTS")
    return Path(value).expanduser() if value else _default_known_hosts()


def _optional_host_key_fingerprint(value: object) -> str | None:
    """Validate an OpenSSH-style SHA-256 host-key fingerprint."""

    if value is None or value == "":
        return None
    if not isinstance(value, str) or not value.startswith("SHA256:"):
        raise ValueError("host_key_fingerprint must start with SHA256:")
    encoded = value.removeprefix("SHA256:")
    try:
        digest = base64.b64decode(encoded + "=", validate=True)
    except (ValueError, binascii.Error) as error:
        raise ValueError("host_key_fingerprint must contain base64 SHA-256 data") from error
    if len(digest) != 32:
        raise ValueError("host_key_fingerprint must be a SHA-256 fingerprint")
    return f"SHA256:{encoded}"


def _host_key_fingerprint(key: object) -> str:
    """Calculate the same SHA-256 fingerprint shown by ssh-keygen."""

    as_bytes = getattr(key, "asbytes", None)
    if not callable(as_bytes):
        raise RecordingUploadError("SSH server returned an unsupported host key")
    digest = hashlib.sha256(as_bytes()).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


class _PinnedHostKeyPolicy:
    """Accept only one preconfigured server key; never use trust-on-first-use."""

    def __init__(self, fingerprint: str) -> None:
        self._fingerprint = fingerprint

    def missing_host_key(self, _client, hostname: str, key: object) -> None:
        actual = _host_key_fingerprint(key)
        if hmac.compare_digest(actual, self._fingerprint):
            return
        raise RecordingUploadError(
            f"SSH host key for {hostname} did not match the configured host_key_fingerprint"
        )


class RecordingUploader:
    """Upload exactly one completed session without trusting unknown SSH hosts."""

    def __init__(self, settings: UploadSettings) -> None:
        self.settings = settings

    def upload(
        self,
        recording_directory: Path,
        progress: Callable[[str], None] | None = None,
        progress_callback: UploadProgressCallback | None = None,
    ) -> UploadResult:
        source, session_id, files = _validated_session(recording_directory)
        total_bytes = sum(path.stat().st_size for path in files)
        completed_bytes = 0
        _report_progress(progress_callback, total_bytes, completed_bytes, None)
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
                    _report_progress(progress_callback, total_bytes, total_bytes, None)
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
                        completed_bytes += size
                        _report_progress(progress_callback, total_bytes, completed_bytes, relative)
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
                    _report_progress(progress_callback, total_bytes, completed_bytes + written, relative)
                    next_progress = ((written // (5 * 1024 * 1024)) + 1) * 5 * 1024 * 1024
                    with local_path.open("rb") as local_handle, sftp.file(remote_path, mode) as remote_handle:
                        local_handle.seek(resume_at)
                        remote_handle.seek(resume_at)
                        while chunk := local_handle.read(1024 * 1024):
                            remote_handle.write(chunk)
                            written += len(chunk)
                            _report_progress(progress_callback, total_bytes, completed_bytes + written, relative)
                            if written >= next_progress or written == size:
                                _announce(progress, f"Uploading {relative}: {written / 1024 / 1024:.1f} / {size / 1024 / 1024:.1f} MiB")
                                next_progress += 5 * 1024 * 1024
                    if _remote_size(sftp, remote_path) != size:
                        raise RecordingUploadError(f"Remote size verification failed for {relative}")
                    uploaded_files += 1
                    uploaded_bytes += size
                    completed_bytes += size
                    _report_progress(progress_callback, total_bytes, completed_bytes, relative)
                _rename_into_library(client, stage_path, final_path)
                _announce(progress, "Upload complete; recording is now visible in the library")
                _report_progress(progress_callback, total_bytes, total_bytes, None)
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
        if not key_path.is_file():
            raise RecordingUploadError(f"SSH private key was not found: {key_path}")
        client = paramiko.SSHClient()
        if self.settings.host_key_fingerprint is not None:
            # The shared fingerprint is the trust anchor. Do not consult a
            # device-local host database, which could differ across laptops.
            client.set_missing_host_key_policy(_PinnedHostKeyPolicy(self.settings.host_key_fingerprint))
        else:
            known_hosts = self.settings.known_hosts.expanduser().resolve() if self.settings.known_hosts else None
            if known_hosts is None or not known_hosts.is_file():
                raise RecordingUploadError(
                    "Known-hosts file was not found. Configure recording_upload.host_key_fingerprint instead."
                )
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


def _report_progress(
    callback: UploadProgressCallback | None,
    total_bytes: int,
    completed_bytes: int,
    current_file: str | None,
) -> None:
    if callback is not None:
        callback(total_bytes, min(completed_bytes, total_bytes), current_file)


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
