# FTC Advanced Discord bot

This is a **read-only** Discord bot for sharing the current Driver Station
status, debugger state, and completed recordings. It deliberately does not
launch OpModes, change motor output, control gamepads, or change recording
state.

## Setup

1. Create a Discord application and bot in the [Discord Developer Portal](https://discord.com/developers/applications).
   Enable the `applications.commands` scope when installing it in the team server.
2. Copy the repository-root `ftc_advanced.local.example.json` to
   `ftc_advanced.local.json`, then fill in the `discord` section. This one
   ignored file is shared by the Discord bot, the OpenAI tunnel launcher, and
   recording uploads. Use numeric JSON arrays for `guild_ids` and
   `viewer_role_ids`.
3. Create a virtual environment and install dependencies:

   ```powershell
   python -m venv discord_bot\.venv
   discord_bot\.venv\Scripts\python.exe -m pip install -r discord_bot\requirements.txt
   ```

4. Start the bot:

   ```powershell
   discord_bot\.venv\Scripts\python.exe -m discord_bot.bot
   ```

The bot must be able to reach `FTC_ADVANCED_API_BASE`. For the first release,
run it on the Driver Station laptop and keep that address as `127.0.0.1`.
Do not expose the Driver Station backend as a public Oracle endpoint: it
contains live-control routes. If the bot later lives on Oracle, add a separate
authenticated, read-only relay instead.

`run_mcp_server.bat` creates the MCP and bot virtual environments as needed,
then opens separate windows for the MCP server, secure tunnel client, and
Discord bot. It reads the tunnel's `control_plane_api_key` from the same
ignored local JSON file and never prints it.

## Commands

- `/robot status` — Driver Station connection and OpMode state.
- `/robot telemetry` — active telemetry session summary.
- `/robot opmodes` — currently advertised Robot Controller OpModes.
- `/debugger status` — debugger and safety status.
- `/recording latest` — newest finalized local recording by capture time.
- `/recording list` — saved runs with OpMode, session ID, and upload state.
- `/recording analyze` — report shooter dips, voltage sags, or intake loads with elapsed timestamps and a PNG. Choose `latest` or paste the session ID.

Use `FTC_RECORDING_VIEWER_URL` in the bot environment to add direct timestamp
links for uploaded sessions. Set it to the hosted replay library's base URL.
Local captures are available to the bot after the laptop regains internet.

Results are posted in the channel where the command is used. The bot does not
need an OpenAI API key.

## Troubleshooting server access

If startup reports that an application cannot sync commands to a guild, check
that the server ID in `discord.guild_ids` is correct and that the same Discord
application is installed in that server with the `bot` and
`applications.commands` scopes. The log identifies the application and server
IDs. A `DISCORD_BOT_TOKEN` environment variable overrides the token in the local
JSON file; remove a stale override or update it to use the intended application.
The bot continues syncing other configured servers when one denies access.
