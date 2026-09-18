from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from .recording_uploader import (
    RecordingUploadError,
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


if __name__ == "__main__":
    unittest.main()
