from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from .recording_uploader import UploadSettings


class UploadSettingsTests(unittest.TestCase):
    def test_loads_ignored_local_settings_file_when_environment_is_absent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            config = directory / "recording_upload.local.json"
            config.write_text(
                '{"host":"oracle.example","username":"ubuntu","private_key":"keys/upload.key","known_hosts":"known_hosts","remote_root":"/recordings","port":2222}',
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"FTC_RECORDING_UPLOAD_CONFIG": str(config)}):
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


if __name__ == "__main__":
    unittest.main()
