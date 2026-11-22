from __future__ import annotations

import os
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from .ftclog import RawFtcLogRecorder
from .recording_metadata import RECORDING_METADATA_FILE
from .recording_uploader import (
    RecordingUploadError,
    RecordingUploader,
    UploadSettings,
    _PinnedHostKeyPolicy,
    _host_key_fingerprint,
)


class _FakeHostKey:
    def __init__(self, value: bytes) -> None:
        self.value = value

    def asbytes(self) -> bytes:
        return self.value


class UploadSettingsTests(unittest.TestCase):
    def test_loads_ignored_local_settings_file_when_environment_is_absent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            config = directory / "recording_upload.local.json"
            config.write_text(
                '{"host":"oracle.example","username":"ubuntu","private_key":"keys/upload.key","known_hosts":"known_hosts","remote_root":"/recordings","port":2222}',
                encoding="utf-8",
            )
            with patch.dict(os.environ, {
                "FTC_RECORDING_UPLOAD_CONFIG": str(config),
                "FTC_ADVANCED_SECRETS_FILE": str(directory / "missing-shared-secrets.json"),
            }):
                settings = UploadSettings.from_environment()

            self.assertIsNotNone(settings)
            assert settings is not None
            self.assertEqual("oracle.example", settings.host)
            self.assertEqual("ubuntu", settings.username)
            self.assertEqual(directory / "keys" / "upload.key", settings.private_key)
            self.assertEqual(directory / "known_hosts", settings.known_hosts)
            self.assertEqual("/recordings", settings.remote_root)
            self.assertEqual(2222, settings.port)

    def test_environment_settings_override_local_settings_file(self) -> None:
        with patch.dict(os.environ, {
            "FTC_RECORDING_UPLOAD_HOST": "env.example",
            "FTC_RECORDING_UPLOAD_USERNAME": "operator",
            "FTC_RECORDING_UPLOAD_PRIVATE_KEY": "C:/keys/env.key",
            "FTC_RECORDING_UPLOAD_ROOT": "/env-recordings",
            "FTC_RECORDING_UPLOAD_PORT": "2200",
        }):
            settings = UploadSettings.from_environment()

        self.assertIsNotNone(settings)
        assert settings is not None
        self.assertEqual("env.example", settings.host)
        self.assertEqual("operator", settings.username)
        self.assertEqual(Path("C:/keys/env.key"), settings.private_key)
        self.assertEqual("/env-recordings", settings.remote_root)
        self.assertEqual(2200, settings.port)

    def test_loads_the_shared_ignored_local_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            config = directory / "ftc_advanced.local.json"
            config.write_text(
                '{"recording_upload":{"host":"oracle.example","username":"ubuntu","private_key":"keys/upload.key","known_hosts":"known_hosts","remote_root":"/recordings","port":2222}}',
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"FTC_ADVANCED_SECRETS_FILE": str(config)}, clear=True):
                settings = UploadSettings.from_environment()

        self.assertIsNotNone(settings)
        assert settings is not None
        self.assertEqual("oracle.example", settings.host)
        self.assertEqual(directory / "keys" / "upload.key", settings.private_key)
        self.assertEqual(directory / "known_hosts", settings.known_hosts)
        self.assertEqual(2222, settings.port)

    def test_shared_config_accepts_a_pinned_host_key_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            host_key = _FakeHostKey(b"oracle-host-key")
            fingerprint = _host_key_fingerprint(host_key)
            config = directory / "ftc_advanced.local.json"
            config.write_text(
                '{"recording_upload":{"host":"oracle.example","username":"ubuntu","private_key":"keys/upload.key","host_key_fingerprint":"' + fingerprint + '"}}',
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"FTC_ADVANCED_SECRETS_FILE": str(config)}, clear=True):
                settings = UploadSettings.from_environment()

        self.assertIsNotNone(settings)
        assert settings is not None
        self.assertIsNone(settings.known_hosts)
        self.assertEqual(fingerprint, settings.host_key_fingerprint)

    def test_pinned_policy_rejects_a_different_server_key(self) -> None:
        expected = _FakeHostKey(b"expected-oracle-key")
        policy = _PinnedHostKeyPolicy(_host_key_fingerprint(expected))

        policy.missing_host_key(None, "oracle.example", expected)
        with self.assertRaisesRegex(RecordingUploadError, "did not match"):
            policy.missing_host_key(None, "oracle.example", _FakeHostKey(b"impostor-key"))


class RecordingMetadataUploadTests(unittest.TestCase):
    def test_retry_replaces_staged_metadata_and_verifies_published_details(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            session_id = str(uuid4())
            recorder = RawFtcLogRecorder(root / "local")
            recorder.append(session_id, b"telemetry", 1)
            log_path = recorder.close_session(session_id)
            source = log_path.parent.parent
            uploader = RecordingUploader(UploadSettings("oracle", "ubuntu", root / "unused", "/published"))
            uploader._connect = Mock()
            with self.assertRaisesRegex(RecordingUploadError, "Save the battery index"):
                uploader.upload(source)
            uploader._connect.assert_not_called()

            remote = root / "remote"
            staged = remote / "published" / ".incoming" / session_id
            published = remote / "published" / session_id
            metadata_path = source / RECORDING_METADATA_FILE
            previous = {"title": "Practice", "battery_index": 1, "robot_version": 1, "opmode_type": "auto"}
            expected = {**previous, "battery_index": 0}
            (staged / "raw").mkdir(parents=True)
            (staged / RECORDING_METADATA_FILE).write_text(json.dumps(previous), encoding="utf-8")
            (staged / "raw" / log_path.name).write_bytes(log_path.read_bytes()[:20])
            metadata_path.write_text(json.dumps(expected), encoding="utf-8")

            def remote_path(path):
                return remote / path.lstrip("/")

            sftp = SimpleNamespace(
                stat=lambda path: remote_path(path).stat(), mkdir=lambda path: remote_path(path).mkdir(),
                file=Mock(side_effect=lambda path, mode: remote_path(path).open("r+b" if mode == "r+" else mode)),
                close=lambda: None,
            )
            uploader._connect.return_value.open_sftp.return_value = sftp
            with patch("web_driver_station.backend.recording_uploader._rename_into_library",
                       side_effect=lambda _client, stage_path, final_path: remote_path(stage_path).rename(remote_path(final_path))):
                uploader.upload(source)
            self.assertEqual(expected, json.loads((published / RECORDING_METADATA_FILE).read_bytes()))
            self.assertEqual(log_path.read_bytes(), (published / "raw" / log_path.name).read_bytes())
            self.assertIn("r+", [call.args[1] for call in sftp.file.call_args_list])
            self.assertTrue(uploader.upload(source).already_present)
            metadata_path.write_text(json.dumps(previous), encoding="utf-8")
            with self.assertRaisesRegex(RecordingUploadError, "different recording details"):
                uploader.upload(source)
            self.assertEqual(expected, json.loads((published / RECORDING_METADATA_FILE).read_bytes()))


if __name__ == "__main__":
    unittest.main()
