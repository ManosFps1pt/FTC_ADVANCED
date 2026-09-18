"""Load FTC Advanced's single ignored, per-device configuration file.

The loader deliberately keeps secrets in memory only.  It never logs values,
and environment variables remain an explicit override for CI and automation.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


_ROOT = Path(__file__).resolve().parent
_DEFAULT_FILENAME = "ftc_advanced.local.json"


class LocalConfigError(RuntimeError):
    """The local configuration file exists but cannot be safely used."""


def local_config_path() -> Path:
    """Return the configured local-file path without reading its contents."""

    value = os.getenv("FTC_ADVANCED_SECRETS_FILE")
    if not value:
        return _ROOT / _DEFAULT_FILENAME
    path = Path(value).expanduser()
    return path if path.is_absolute() else (_ROOT / path)


def load_local_config() -> dict[str, object]:
    """Read the optional JSON config and return a plain object mapping."""

    path = local_config_path()
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise LocalConfigError(f"Could not read local configuration file {path}: {error}") from error
    if not isinstance(value, dict):
        raise LocalConfigError(f"Local configuration file {path} must contain one JSON object")
    return value


def section(name: str) -> dict[str, object]:
    """Return one named object from the local config, or an empty object."""

    value = load_local_config().get(name)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise LocalConfigError(f"Local configuration section {name!r} must be a JSON object")
    return dict(value)


def string(section_name: str, key: str) -> str | None:
    """Return a non-empty string setting without ever exposing it in errors."""

    value = section(section_name).get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise LocalConfigError(f"Local configuration setting {section_name}.{key} must be a non-empty string")
    return value.strip()


def integer_set(section_name: str, key: str) -> frozenset[int]:
    """Read a JSON integer list used for Discord IDs."""

    value = section(section_name).get(key)
    if value is None:
        return frozenset()
    if not isinstance(value, list) or any(type(item) is not int or item <= 0 for item in value):
        raise LocalConfigError(f"Local configuration setting {section_name}.{key} must be a list of positive integers")
    return frozenset(value)


def child_environment() -> dict[str, str]:
    """Build a child-process environment with secrets copied only when absent."""

    environment = os.environ.copy()
    mappings = (
        ("openai", "api_key", "OPENAI_API_KEY"),
        ("openai", "control_plane_api_key", "CONTROL_PLANE_API_KEY"),
        ("discord", "bot_token", "DISCORD_BOT_TOKEN"),
        ("discord", "api_base", "FTC_ADVANCED_API_BASE"),
    )
    for section_name, key, environment_name in mappings:
        if not environment.get(environment_name):
            value = string(section_name, key)
            if value:
                environment[environment_name] = value
    return environment


def _run_tunnel(command: list[str]) -> int:
    if not command:
        raise LocalConfigError("A tunnel command is required")
    environment = child_environment()
    if not environment.get("CONTROL_PLANE_API_KEY"):
        raise LocalConfigError(
            "Add openai.control_plane_api_key to ftc_advanced.local.json before starting the tunnel"
        )
    os.execvpe(command[0], command, environment)
    return 1  # pragma: no cover - execvpe only returns on failure


def main() -> int:
    parser = argparse.ArgumentParser(description="FTC Advanced local configuration launcher")
    subparsers = parser.add_subparsers(dest="command", required=True)
    tunnel = subparsers.add_parser("run-tunnel", help="start the tunnel with the local config injected")
    tunnel.add_argument("tunnel_command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    try:
        if args.command == "run-tunnel":
            return _run_tunnel(args.tunnel_command)
    except LocalConfigError as error:
        parser.error(str(error))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
