# FTC Advanced MCP server

This MCP integration exposes live telemetry, local recording inventory, OpMode
lifecycle tools, and read access to Debugger state and benchmark results.

## Tools

The replay tools work on finalized laptop recordings after the robot disconnects.
Run the stack with `--online` or start the MCP server with
`FTC_ADVANCED_MCP_REPLAY_ONLY=1` to expose saved-run analysis and read-only
connection checks to ChatGPT.

- `list_replays`, `get_run_report`, `get_recording_quality` — find runs and check coverage and capture quality.
- `list_recording_metadata` — find battery index, robot version, and OpMode type for finalized replays and completed Debugger benchmarks. Filter by `run_id` or `kind` (`replay` or `debugger`). Older recordings can return `null` for missing details.
- `list_recorded_signals`, `get_signal_window` — inspect channel units, full-window statistics, and bounded signal samples.
- `find_shooter_dips`, `get_shooter_transitions`, `get_shooter_tracking`, `compare_shooter_runs` — inspect speed, recovery, and saturation at common RPM targets.
- `find_voltage_sags`, `get_motor_current_report`, `find_intake_loads`, `check_encoder_health` — review electrical and feedback evidence.
- `get_feed_gate_windows`, `get_replay_plot` — inspect commanded gate intervals and get diagnostic PNGs.

Start an investigation with `list_replays`, then reuse the selected UUID for
every tool call. Timestamps are seconds from the first telemetry sample.
Replay paths refer to the laptop's local Driver Station. Mechanism-specific
calculations use the BIOBUZZ Main TeleOp signal IDs and analysis settings.

### Driver Station tools

- `get_driver_station_status` — Robot Controller connection, OpMode state, and errors.
- `get_robot_data_status` — structured telemetry TCP listener, active recordings,
  and whether recording uploads are configured.
- `get_telemetry_status` — active telemetry session summary only.
- `get_latest_telemetry_snapshot` — one latest sample, at most ten recent events,
  and telemetry capture status.
- `get_telemetry_catalog` — signal names, units, devices, and roles.
- `list_recording_sessions` — local recording IDs and counts of finalized and
  partial raw logs. Results follow backend order. Pass `nextOffset` as `offset`
  for the next page. The default page size is 20 and the maximum is 50.
- `get_debugger_status` — debugger manifest, selected tool, and safety state.
- `list_available_opmodes` / `launch_opmode` — inspect and launch an exact
  Robot Controller-advertised user OpMode. Internal FTC stop sentinels cannot
  be launched.
- `stop_opmode` — stop the active user OpMode through the Driver Station.
- `launch_debugger_opmode` — launches only `FTC Advanced Debugger`.
- `discover_debugger_functionality` — robot sessions and protocol versions,
  selected session, lease ownership, live manifest, selected-tool schema,
  safety state, and advertised benchmark inputs.
- `select_free_spin_tool` — selects an enabled manifest-advertised free-spin tool.
- `set_free_spin_motor_power` / `stop_free_spin_motor` — bounded free-spin
  control. Power is capped at ±0.35 and expires after at most 500 ms.
- `run_friction_benchmark` — starts the selected friction benchmark.
- `list_friction_benchmark_runs` / `get_friction_benchmark_result` — inspect
  execution status and the completed analyzed report.

The free-spin tools validate the live manifest and accept enabled
`debug_free_spin` tool leaves. On the v3 backend, motor power and benchmark
start require a fresh selected session, a control lease, and an armed tool.
The current MCP tools do not supply the lease. Use the Web Driver Station
for these operations. The backend and robot enforce tool-instance checks,
schema validation, output limits, watchdog expiry, and benchmark safety.

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
6. In ChatGPT **Plugins**, select **Add custom MCP server**, choose **Tunnel**, and select or enter the tunnel ID.

The tunnel client must remain running while ChatGPT uses the app. The tunnel
documentation says it uses outbound HTTPS and does not require inbound public
access to the laptop.

With the `--online` launcher running, select the replay plugin in a new chat
and ask:

```text
Use list_replays to find my newest Main TeleOp recording.
Use its session ID with get_run_report and find_shooter_dips.
Give me the worst dip and its elapsed replay timestamp.
```

The replay profile also advertises `get_driver_station_status`,
`get_robot_data_status`, and `get_telemetry_status`. These report live
connection and stream state. If the plugin shows a tool list from an earlier
server version, refresh it in ChatGPT and select it in a new chat.

To verify the plugin points to the tunnel run by this laptop, compare its
tunnel ID with the running client's `control_plane_tunnel_id` field:

```powershell
(Invoke-RestMethod 'http://127.0.0.1:18080/api/status').control_plane_tunnel_id
```

If ChatGPT reports a `421 Misdirected Request`, the tunnel's public hostname
must be configured in the MCP SDK transport-security allowlist. The SDK
defaults to localhost protection, which is intentional.

## Scope boundary

The MCP server calls the backend HTTP API and never imports backend globals.
The backend remains authoritative for Robot Controller connectivity, telemetry
storage, and safety. The Recording Viewer serves saved telemetry replay and
video through its own API.

## Oracle replay MCP

`python -m mcp_server.archive` serves the saved-run tools from published
recordings on Oracle. It binds to `127.0.0.1:8003/mcp` and uses the same
measurements as the laptop. The recording directory is read-only and derived
reports and plots go to a separate cache. The HTTPS viewer continues to serve
the timestamped replay links.

1. Put this source tree in `/opt/ftc-replay-mcp` on Oracle. Create a Python
   3.11+ virtual environment at `.venv` and install `mcp_server/requirements-archive.txt`.
   Create a service account named `ftc-replay-mcp` with read and directory
   traversal access to the published archive, including future uploads.
2. Install the [official OpenAI tunnel client](https://github.com/openai/tunnel-client/releases/latest)
   for the instance architecture at `/usr/local/bin/tunnel-client` and verify
   the release checksum. Install the two `ftc-replay-*.service` units from
   `recording_viewer/deploy` in `/etc/systemd/system` and copy the tunnel YAML
   to `/etc/ftc-replay-mcp/tunnel.yaml`.
3. Set `FTC_REPLAY_VIEWER_URL=https://80.225.93.186/` in
   `/etc/ftc-replay-mcp/viewer.env`, or use the deployed viewer URL. In the
   root-owned, mode `0600` `/etc/ftc-replay-mcp/tunnel.env`, set the new
   `CONTROL_PLANE_TUNNEL_ID` and its restricted `CONTROL_PLANE_API_KEY`.
   Set `CONTROL_PLANE_ORGANIZATION_ID` if the tunnel requires it.
4. Run `sudo systemctl daemon-reload` and
   `sudo systemctl enable --now ftc-replay-mcp ftc-replay-tunnel`. Check both
   unit logs and `curl --fail http://127.0.0.1:18081/readyz` on Oracle. These
   units use loopback ports only. Nginx continues to serve the replay viewer.
5. In the authorized ChatGPT workspace, create the **FTC Oracle Replays**
   custom MCP plugin with the Oracle tunnel ID. Call `list_replays` with
   `limit=1`, then use that run ID for `get_run_report`. Verify the hosted
   replay link opens while the laptop is offline.
