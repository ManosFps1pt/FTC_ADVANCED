# FTC Advanced MCP server

This MCP integration exposes bounded live status plus a deliberately narrow
Debugger control surface. It does not expose recording replay, video,
configuration writes, arbitrary OpMode lifecycle commands, drivetrain control,
or mechanism control.

## Tools

- `get_driver_station_status` — Robot Controller connection, OpMode state, and errors.
- `get_robot_data_status` — structured telemetry TCP listener and recording status.
- `get_telemetry_status` — active telemetry session summary only.
- `get_latest_telemetry_snapshot` — one latest sample plus at most ten recent events.
- `get_telemetry_catalog` — signal names, units, devices, and roles.
- `get_debugger_status` — debugger manifest, selected tool, and safety state.
- `list_available_opmodes` / `launch_opmode` — inspect and launch an exact
  Robot Controller-advertised user OpMode. Internal FTC stop sentinels cannot
  be launched.
- `stop_opmode` — stop the active user OpMode through the Driver Station.
- `launch_debugger_opmode` — launches only `FTC Advanced Debugger`.
- `discover_debugger_functionality` — live manifest, selected-tool schema,
  safety state, and advertised benchmark inputs.
- `select_free_spin_tool` — selects an enabled manifest-advertised free-spin tool.
- `set_free_spin_motor_power` / `stop_free_spin_motor` — bounded free-spin
  control. Power is capped at ±0.35 and expires after at most 500 ms.
- `run_friction_benchmark` — starts the selected friction benchmark.
- `list_friction_benchmark_runs` / `get_friction_benchmark_result` — inspect
  execution status and the completed analyzed report.

The control tools validate the live manifest and permit only enabled
`debug_free_spin` tool leaves. The backend and robot remain authoritative for
tool-instance checks, schema validation, output limits, watchdog expiry, and
benchmark safety. MCP annotations are hints for clients, not a replacement for
network authentication or backend authorization.

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

After connecting, ask:

```text
Use the FTC Advanced tools to discover the Debugger. Launch only the FTC
Advanced Debugger OpMode, select only an enabled free-spin tool, and report
its safety state before using any actuator control. For a friction benchmark,
return the run ID and retrieve the result once it is complete.
```

If ChatGPT reports a `421 Misdirected Request`, the tunnel's public hostname
must be configured in the MCP SDK transport-security allowlist. The SDK
defaults to localhost protection, which is intentional.

## Scope boundary

The MCP server calls the backend HTTP API and never imports backend globals.
The backend remains authoritative for Robot Controller connectivity, telemetry
storage, and safety. Replay exploration and control tools should be added only
after this status-only path is stable.
