"""Read-only slash-command bot for FTC Advanced.

The bot intentionally talks to the existing Driver Station HTTP API instead of
asking an LLM to interpret or execute Discord messages.  It has no endpoints
for robot control in this first release.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

import aiohttp
import discord
from discord import app_commands

from ftc_local_config import LocalConfigError, integer_set, string


LOG = logging.getLogger("ftc-advanced-discord")
MAX_DISCORD_FIELD = 1_000

EPHEMERAL = False

def _environment_id_set(name: str) -> frozenset[int]:
    raw = os.getenv(name, "")
    try:
        return frozenset(int(part.strip()) for part in raw.split(",") if part.strip())
    except ValueError as error:
        raise RuntimeError(f"{name} must be a comma-separated list of Discord role IDs") from error


@dataclass(frozen=True)
class Settings:
    """Configuration from environment overrides or the ignored local JSON file."""

    token: str
    api_base: str
    guild_ids: frozenset[int]
    viewer_role_ids: frozenset[int]

    @classmethod
    def from_environment(cls) -> "Settings":
        try:
            token = os.getenv("DISCORD_BOT_TOKEN", "").strip() or (string("discord", "bot_token") or "")
            configured_api_base = string("discord", "api_base")
            configured_guild_ids = integer_set("discord", "guild_ids")
            configured_viewer_role_ids = integer_set("discord", "viewer_role_ids")
        except LocalConfigError as error:
            raise RuntimeError(str(error)) from error
        if not token:
            raise RuntimeError("Set discord.bot_token in ftc_advanced.local.json")
        api_base = (os.getenv("FTC_ADVANCED_API_BASE", "").strip() or configured_api_base or "http://127.0.0.1:8000").rstrip("/")
        if not api_base.startswith(("http://", "https://")):
            raise RuntimeError("FTC_ADVANCED_API_BASE must be an http(s) URL")
        guild_ids = _environment_id_set("FTC_DISCORD_GUILD_IDS") or configured_guild_ids
        # Preserve the first test-server configuration while teams migrate to
        # the multi-guild setting.
        if not guild_ids:
            guild_ids = _environment_id_set("FTC_DISCORD_GUILD_ID")
        viewer_role_ids = _environment_id_set("FTC_DISCORD_VIEWER_ROLE_IDS") or configured_viewer_role_ids
        return cls(
            token=token,
            api_base=f"{api_base}/",
            guild_ids=guild_ids,
            viewer_role_ids=viewer_role_ids,
        )


class BackendError(RuntimeError):
    """A concise backend failure suitable for a Discord response."""


class DriverStationApi:
    def __init__(self, base_url: str) -> None:
        self._base_url = base_url
        self._session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        timeout = aiohttp.ClientTimeout(total=8)
        self._session = aiohttp.ClientSession(timeout=timeout)

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def get(self, path: str) -> Any:
        if self._session is None:
            raise RuntimeError("Driver Station API client was not started")
        url = urljoin(self._base_url, path.lstrip("/"))
        try:
            async with self._session.get(url) as response:
                if response.status >= 400:
                    detail = await response.text()
                    raise BackendError(f"Driver Station returned {response.status}: {_shorten(detail, 200)}")
                return await response.json(content_type=None)
        except asyncio.TimeoutError as error:
            raise BackendError("Driver Station did not respond within 8 seconds") from error
        except aiohttp.ClientError as error:
            raise BackendError("Cannot reach the Driver Station backend") from error


def _shorten(value: object, limit: int = MAX_DISCORD_FIELD) -> str:
    text = str(value).strip() or "None"
    return text if len(text) <= limit else f"{text[:limit - 1]}…"


def _json(value: object, limit: int = MAX_DISCORD_FIELD) -> str:
    return _shorten(json.dumps(value, ensure_ascii=False, indent=2, default=str), limit)


def _has_viewer_access(interaction: discord.Interaction, allowed_roles: frozenset[int]) -> bool:
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        return False
    if not allowed_roles:
        return True
    return any(role.id in allowed_roles for role in interaction.user.roles)


class FtcDiscordBot(discord.Client):
    def __init__(self, settings: Settings) -> None:
        intents = discord.Intents.none()
        # Guilds is a normal, non-privileged intent. It is sufficient for
        # guild slash commands; message-content and member intents stay off.
        intents.guilds = True
        super().__init__(intents=intents)
        self.settings = settings
        self.api = DriverStationApi(settings.api_base)
        self.tree = app_commands.CommandTree(self)
        self.robot = app_commands.Group(name="robot", description="Read current robot information")
        self.debugger = app_commands.Group(name="debugger", description="Read debugger information")
        self.recording = app_commands.Group(name="recording", description="Review completed recordings")
        self._register_commands()

    async def setup_hook(self) -> None:
        await self.api.start()
        self.tree.add_command(self.robot)
        self.tree.add_command(self.debugger)
        self.tree.add_command(self.recording)
        if self.settings.guild_ids:
            for guild_id in sorted(self.settings.guild_ids):
                guild = discord.Object(id=guild_id)
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
                LOG.info("Synced slash commands to guild %s", guild_id)
        else:
            await self.tree.sync()
            LOG.info("Synced global slash commands")

    async def close(self) -> None:
        await self.api.close()
        await super().close()

    async def _authorize(self, interaction: discord.Interaction) -> bool:
        if _has_viewer_access(interaction, self.settings.viewer_role_ids):
            return True
        await interaction.response.send_message(
            "This bot only accepts configured server members with the viewer role.", ephemeral=EPHEMERAL,
        )
        return False

    async def _respond_backend_error(self, interaction: discord.Interaction, error: BackendError) -> None:
        LOG.warning("Backend request failed: %s", error)
        await interaction.followup.send(f"⚠️ {_shorten(error, 500)}", ephemeral=EPHEMERAL)

    def _register_commands(self) -> None:
        @self.robot.command(name="status", description="Show Driver Station connection and OpMode state")
        async def robot_status(interaction: discord.Interaction) -> None:
            if not await self._authorize(interaction):
                return
            await interaction.response.defer(ephemeral=EPHEMERAL, thinking=True)
            try:
                status = await self.api.get("/api/status")
            except BackendError as error:
                await self._respond_backend_error(interaction, error)
                return
            embed = discord.Embed(title="FTC Advanced · Driver Station", colour=discord.Colour.blurple())
            embed.add_field(name="Connected", value="Yes" if status.get("connected") else "No")
            embed.add_field(name="Robot state", value=_shorten(status.get("robot_state"), 120))
            embed.add_field(name="OpMode running", value="Yes" if status.get("started_opmode") else "No")
            embed.add_field(name="Robot Controller", value=_shorten(status.get("host"), 250), inline=False)
            if status.get("driverStationError"):
                embed.add_field(name="Latest error", value=_shorten(status["driverStationError"]), inline=False)
            await interaction.followup.send(embed=embed, ephemeral=EPHEMERAL)

        @self.robot.command(name="telemetry", description="Show active telemetry session summary")
        async def robot_telemetry(interaction: discord.Interaction) -> None:
            if not await self._authorize(interaction):
                return
            await interaction.response.defer(ephemeral=EPHEMERAL, thinking=True)
            try:
                telemetry = await self.api.get("/api/data/telemetry/status")
            except BackendError as error:
                await self._respond_backend_error(interaction, error)
                return
            await interaction.followup.send(f"```json\n{_json(telemetry)}\n```", ephemeral=EPHEMERAL)

        @self.robot.command(name="opmodes", description="List OpModes advertised by the Robot Controller")
        async def robot_opmodes(interaction: discord.Interaction) -> None:
            if not await self._authorize(interaction):
                return
            await interaction.response.defer(ephemeral=EPHEMERAL, thinking=True)
            try:
                opmodes = await self.api.get("/api/opmodes")
            except BackendError as error:
                await self._respond_backend_error(interaction, error)
                return
            names = [str(item.get("name", "Unnamed")) for item in opmodes if isinstance(item, dict)]
            message = "\n".join(f"• {name}" for name in names) or "No OpModes are currently advertised."
            await interaction.followup.send(_shorten(message, 1_900), ephemeral=EPHEMERAL)

        @self.debugger.command(name="status", description="Show debugger manifest and safety state")
        async def debugger_status(interaction: discord.Interaction) -> None:
            if not await self._authorize(interaction):
                return
            await interaction.response.defer(ephemeral=EPHEMERAL, thinking=True)
            try:
                status = await self.api.get("/api/debug/status")
            except BackendError as error:
                await self._respond_backend_error(interaction, error)
                return
            safe_status = {
                "connected": status.get("connected"),
                "safety": status.get("safety"),
                "selectedTool": status.get("tool_ready"),
                "toolState": status.get("tool_state"),
            }
            await interaction.followup.send(f"```json\n{_json(safe_status)}\n```", ephemeral=EPHEMERAL)

        @self.recording.command(name="latest", description="Show the latest completed recording")
        async def recording_latest(interaction: discord.Interaction) -> None:
            if not await self._authorize(interaction):
                return
            await interaction.response.defer(ephemeral=EPHEMERAL, thinking=True)
            try:
                recordings = await self.api.get("/api/data/recordings")
            except BackendError as error:
                await self._respond_backend_error(interaction, error)
                return
            if not isinstance(recordings, list) or not recordings:
                await interaction.followup.send("No completed recordings are available.", ephemeral=EPHEMERAL)
                return
            await interaction.followup.send(f"```json\n{_json(recordings[0])}\n```", ephemeral=EPHEMERAL)

        @self.recording.command(name="list", description="List recent completed recordings")
        @app_commands.describe(limit="Number of recordings to show (1–10)")
        async def recording_list(interaction: discord.Interaction, limit: app_commands.Range[int, 1, 10] = 5) -> None:
            if not await self._authorize(interaction):
                return
            await interaction.response.defer(ephemeral=EPHEMERAL, thinking=True)
            try:
                recordings = await self.api.get("/api/data/recordings")
            except BackendError as error:
                await self._respond_backend_error(interaction, error)
                return
            if not isinstance(recordings, list) or not recordings:
                await interaction.followup.send("No completed recordings are available.", ephemeral=EPHEMERAL)
                return
            summary = []
            for index, recording in enumerate(recordings[:limit], start=1):
                if not isinstance(recording, dict):
                    continue
                summary.append(f"{index}. `{recording.get('session_id', recording.get('id', 'unknown'))}` — {_shorten(recording.get('state', 'unknown'), 80)}")
            await interaction.followup.send("\n".join(summary) or "No readable recordings are available.", ephemeral=EPHEMERAL)


def main() -> None:
    logging.basicConfig(level=os.getenv("FTC_DISCORD_LOG_LEVEL", "INFO"), format="%(levelname)s %(name)s: %(message)s")
    settings = Settings.from_environment()
    FtcDiscordBot(settings).run(settings.token, log_handler=None)


if __name__ == "__main__":
    main()
