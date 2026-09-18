from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import ftc_local_config


class LocalConfigTests(unittest.TestCase):
    def test_child_environment_reads_only_missing_values_from_the_local_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            config_path = Path(temporary_directory) / "secrets.json"
            config_path.write_text(json.dumps({
                "openai": {"api_key": "test-openai", "control_plane_api_key": "test-control-plane"},
                "discord": {"bot_token": "test-discord", "api_base": "http://127.0.0.1:8000"},
            }), encoding="utf-8")
            with patch.dict(os.environ, {
                "FTC_ADVANCED_SECRETS_FILE": str(config_path),
                "DISCORD_BOT_TOKEN": "environment-wins",
            }, clear=True):
                environment = ftc_local_config.child_environment()

        self.assertEqual("test-openai", environment["OPENAI_API_KEY"])
        self.assertEqual("test-control-plane", environment["CONTROL_PLANE_API_KEY"])
        self.assertEqual("environment-wins", environment["DISCORD_BOT_TOKEN"])
        self.assertEqual("http://127.0.0.1:8000", environment["FTC_ADVANCED_API_BASE"])

    def test_missing_local_file_is_optional(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            missing = Path(temporary_directory) / "missing.json"
            with patch.dict(os.environ, {"FTC_ADVANCED_SECRETS_FILE": str(missing)}, clear=True):
                self.assertEqual({}, ftc_local_config.load_local_config())


if __name__ == "__main__":
    unittest.main()
