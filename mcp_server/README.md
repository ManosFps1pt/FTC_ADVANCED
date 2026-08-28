# FTC Advanced MCP server

This is the first, deliberately read-only MCP integration. It exposes bounded
live status from the existing Web Driver Station backend and does not expose
recording replay, video, configuration writes, OpMode lifecycle commands, or
robot-control commands.

## Tools

- `get_driver_station_status` — Robot Controller connection, OpMode state, and errors.
- `get_robot_data_status` — structured telemetry TCP listener and recording status.
- `get_telemetry_status` — active telemetry session summary only.
- `get_latest_telemetry_snapshot` — one latest sample plus at most ten recent events.
- `get_telemetry_catalog` — signal names, units, devices, and roles.
- `get_debugger_status` — debugger manifest, selected tool, and safety state.

All tools are annotated as read-only. The annotations are hints for clients,
not a replacement for network authentication or backend authorization.

## Local installation

From the repository root in PowerShell:

```powershell
python -m venv mcp_server\.venv
mcp_server\.venv\Scripts\python.exe -m pip install -r mcp_server\requirements.txt
```

Start the existing backend first, then start MCP:

```powershell
mcp_server\.venv\Scripts\python.exe -m mcp_server
```

The local endpoint is:

```text
http://127.0.0.1:8001/mcp
```

Override the backend or MCP listener with environment variables when needed:

```powershell
$env:FTC_ADVANCED_API_BASE = "http://127.0.0.1:8000"
$env:FTC_ADVANCED_MCP_HOST = "127.0.0.1"
$env:FTC_ADVANCED_MCP_PORT = "8001"
```

When using a tunnel, configure its exact hostname before starting MCP. For
example, for `https://ftc.example.com`:

```powershell
$env:FTC_ADVANCED_MCP_ALLOWED_HOSTS = "ftc.example.com"
$env:FTC_ADVANCED_MCP_ALLOWED_ORIGINS = "https://ftc.example.com"
```

Multiple values may be comma-separated. Leave these variables empty for the
local-only server.

## Connecting ChatGPT

ChatGPT cannot normally reach a laptop-only `127.0.0.1` endpoint. The
recommended development path is OpenAI's Secure MCP Tunnel: it keeps this
server private and lets the tunnel client forward requests from ChatGPT to
`http://127.0.0.1:8001/mcp`. See the [official Secure MCP Tunnel
guide](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels).

The setup is:

1. Create an OpenAI-hosted tunnel and copy its `tunnel_id` from Platform tunnel settings.
2. Download `tunnel-client` from the link provided there.
3. Set `CONTROL_PLANE_API_KEY` in the tunnel-client process. Do not commit it.
4. Initialize an HTTP profile whose MCP server URL is `http://127.0.0.1:8001/mcp`.
5. Run `tunnel-client doctor --profile <profile> --explain`, then `tunnel-client run --profile <profile>`.
6. In ChatGPT, create a developer-mode app, choose **Tunnel**, and select or enter the tunnel ID.

The tunnel client must remain running while ChatGPT uses the app. The tunnel
documentation says it uses outbound HTTPS and does not require inbound public
access to the laptop.

Enable only the six read-only tools. After connecting, ask:

```text
Use the FTC Advanced tools to report the Driver Station state,
telemetry session state, and latest telemetry sample. If the backend is
offline, say so instead of inferring robot state.
```

If ChatGPT reports a `421 Misdirected Request`, the tunnel's public hostname
must be configured in the MCP SDK transport-security allowlist. The SDK
defaults to localhost protection, which is intentional.

## Scope boundary

The MCP server calls the backend HTTP API and never imports backend globals.
The backend remains authoritative for Robot Controller connectivity, telemetry
storage, and safety. Replay exploration and control tools should be added only
after this status-only path is stable.
