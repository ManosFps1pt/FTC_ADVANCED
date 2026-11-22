# FTC_ADVANCED

## Run the Driver Station and replay library

From PowerShell or Command Prompt on Windows, run:

```powershell
.\run_ftc_stack.bat
```

From Git Bash on Windows or Bash on macOS/Linux, run:

```bash
bash run_ftc_stack.sh
```

On first setup, add `--setup` with Python 3.11+, Node.js LTS, and pnpm installed.
This installs the local dependencies and builds the dashboard and replay
viewer. Subsequent launches work offline. The launcher opens
<http://127.0.0.1:8000>. Press Ctrl+C in its terminal to stop it.

The **Recordings** panel lists finalized runs and opens local replay or event
review. Save the battery index, robot version, and OpMode type when a recording
ends. The capture stays available for replay if you fill in the details later.
Use **Upload pending** to queue recordings with saved details after practice. If the
internet connection drops, leave the app running or restart it later to resume.
The uploader keeps laptop copies.

For ChatGPT and Discord, run once with `--setup --online`, then use
`--online` on subsequent launches. The read-only MCP server uses port
8001. Configure the tunnel and Discord credentials using the
[MCP setup guide](mcp_server/README.md) and [Discord setup guide](discord_bot/README.md).
The Driver Station continues running if the bot or tunnel exits.
