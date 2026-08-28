# MCP Server Mental Model

This document is the starting point for adding an MCP server to FTC Advanced.
It explains what MCP is, where an MCP server lives, how a model reaches it,
how to create one, and how Ollama fits into the picture.

## The one-sentence model

MCP is a standard conversation protocol between an AI host and capability
servers. The server does not become the model, and the model does not open a
socket to the server directly. The host application owns the model and creates
an MCP client connection to each server.

```text
User
  |
  v
AI host / agent application
  |-- owns conversation, model, permissions, approvals
  |-- MCP client 1 ---------------- MCP server: FTC Advanced
  |-- MCP client 2 ---------------- MCP server: filesystem, Git, database, ...
  |
  +-- sends tool definitions and selected results to the model
  +-- executes approved tool calls through the correct MCP client
```

The official architecture calls these pieces **host**, **client**, and
**server**:

- The **host** is the application containing the model and user interface.
- An **MCP client** is a connection managed by the host. It normally has a
  one-to-one relationship with one server.
- The **MCP server** exposes focused capabilities. It does not see the whole
  conversation or the other servers connected to the host.

This separation is important for FTC Advanced: the future MCP server should
not independently compete with the web Driver Station for the robot TCP
connection. It should call the existing local backend API, which remains the
owner of robot connectivity, session state, command validation, and safety.

See the [MCP architecture specification](https://modelcontextprotocol.io/specification/2025-06-18/architecture).

## What a server exposes

MCP has three primary server primitives.

| Primitive | Normally controlled by | Meaning | FTC Advanced example |
|---|---|---|---|
| Tool | Model, subject to host/user approval | An executable function with a typed input schema | `query_telemetry`, `set_alliance`, `analyze_video` |
| Resource | Application/client | Readable contextual data, similar to a file or document | A telemetry session, catalog, or analysis report |
| Prompt | User | A reusable workflow or prompt template | “Explain this drivetrain run” |

Start with read-only tools. Add resources when the model needs to inspect
large or reusable data. Add prompts after the workflows become repeatable.
Control tools should be explicit, narrow, and approved by the host.

See the [server primitives overview](https://modelcontextprotocol.io/specification/2025-06-18/server/).

## What happens during a request

MCP uses JSON-RPC 2.0 messages over a transport. A typical session looks like
this:

1. The host launches or connects to the MCP server.
2. Client and server exchange `initialize` messages and negotiate capabilities.
3. The client asks for the server's tools, resources, or prompts.
4. The host gives the model the available tool definitions, including names,
   descriptions, and JSON schemas for arguments.
5. The model may request a tool call, for example:

   ```json
   {
     "name": "query_telemetry",
     "arguments": {
       "session_id": "...",
       "signal_id": "drive.left.velocity",
       "start_seconds": 10,
       "end_seconds": 20
     }
   }
   ```

6. The host applies its approval policy and asks the MCP client to call the
   server's tool.
7. The server validates the arguments, performs the operation, and returns
   structured content or an error.
8. The host gives the result back to the model, which produces the answer.

The model chooses a tool; the host executes it; the server performs the
operation. Keeping those responsibilities separate prevents a tool description
from becoming an implicit permission grant.

## Transport choices

MCP currently defines two standard transports.

### stdio: the best first choice for this project

The host launches the server as a child process. JSON-RPC messages travel over
stdin/stdout.

```text
MCP host process --stdin/stdout--> mcp_server process
```

Advantages:

- Very little infrastructure.
- Natural for a local developer tool.
- The host can start and stop the server with the session.
- No listening port or network authentication is needed for the first version.

Critical rule: stdout is the protocol channel. A stdio server must never print
logs, banners, stack traces, or debug text to stdout. Send logs to stderr using
Python `logging` or `console.error()` in TypeScript.

### Streamable HTTP: for a separately hosted service

The server runs independently behind one MCP HTTP endpoint. This is useful
when several clients, machines, or users need to connect to the same service.
It introduces authentication, origin validation, session handling, deployment,
and network security.

For a local HTTP server, bind to `127.0.0.1` unless there is a deliberate reason
to expose it more broadly. Validate `Origin` and authenticate clients before
adding robot-control tools.

See the [MCP transport specification](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports).

## Recommended architecture for FTC Advanced

```text
MCP host / local agent
        |
        | stdio, initially
        v
FTC Advanced MCP server
        |
        | localhost HTTP API
        v
FTC Advanced Web Driver Station backend
        |
        | existing structured TCP connection
        v
Robot Controller / OpMode
```

The first server should be an adapter, not a second telemetry implementation.
The server should call the existing backend endpoints and data services for:

- active robot-data status;
- telemetry catalog and snapshots;
- recorded sessions;
- video analysis later;
- explicitly approved robot commands.

Suggested future layout:

```text
mcp_server/
  pyproject.toml
  src/ftc_advanced_mcp/
    __main__.py          # stdio entry point
    server.py             # MCP registration
    driver_station_api.py # localhost backend client
    tools/
      telemetry.py
      video.py
      robot_commands.py
    resources/
      sessions.py
    prompts/
      analysis.py
  tests/
```

The server should not import the web backend's internal globals. Treat the
backend as an API boundary so the MCP process can be tested and restarted
independently.

## Creating the smallest Python server

The official Python quickstart uses Python 3.10+, the Python MCP SDK, and `uv`.
For a new server:

```powershell
uv init mcp_server
cd mcp_server
uv venv
.venv\Scripts\activate
uv add "mcp[cli]"
```

A minimal read-only server looks like this. The exact SDK surface should be
checked against the installed SDK version when implementation starts.

```python
import logging

from mcp.server import MCPServer

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
mcp = MCPServer("ftc-advanced")


@mcp.tool()
async def get_driver_station_status() -> str:
    """Return the current local Driver Station connection status."""
    # Call the existing localhost backend here.
    logger.info("Reading Driver Station status")
    return "Driver Station status goes here"


if __name__ == "__main__":
    mcp.run(transport="stdio")
```

The function signature and documentation become part of the tool definition.
The model sees the tool's name, description, and typed arguments; it does not
see the Python implementation.

The official tutorial also provides TypeScript, Java, Kotlin, C#, Ruby, Rust,
and Go examples. Python is the natural first choice here because the existing
web Driver Station backend is Python.

See the [official server-building guide](https://modelcontextprotocol.io/docs/develop/build-server).

## Host configuration

Host configuration is application-specific, but the core idea is always the
same: give the host an absolute executable path and its arguments.

Example shape for a stdio host on Windows:

```json
{
  "mcpServers": {
    "ftc-advanced": {
      "command": "uv",
      "args": [
        "--directory",
        "C:/absolute/path/to/FTC_ADVANCED/mcp_server",
        "run",
        "src/ftc_advanced_mcp/__main__.py"
      ],
      "env": {
        "FTC_ADVANCED_API_BASE": "http://127.0.0.1:8000"
      }
    }
  }
}
```

Use the host's documented configuration format. `mcpServers` is a common
configuration shape, not a universal requirement of the MCP wire protocol.
Absolute paths matter because GUI hosts often start servers with a different
working directory than a terminal.

## Can Ollama models use MCP servers?

Yes, but there is an important distinction:

- An Ollama model can produce tool calls if the model supports tool use and
  the application supplies tool definitions.
- Ollama's model runner is not, by itself, a general MCP host that discovers
  arbitrary MCP servers from `ollama run`.
- An application must act as the MCP host/client: connect to the MCP server,
  convert the discovered MCP tools into Ollama tool definitions, send them to
  Ollama, execute any returned tool call through MCP, then send the MCP result
  back to Ollama as a tool result.

```text
Your agent application
  |-- MCP client --> FTC Advanced MCP server
  |-- Ollama API --> local Ollama model
  |
  +-- translates MCP tool schemas to Ollama tools
  +-- translates Ollama tool_calls back into MCP tools/call
```

The loop is conceptually:

```python
tools = await mcp_client.list_tools()
ollama_tools = convert_mcp_tools_to_ollama_tools(tools)

response = ollama.chat(model="qwen3", messages=messages, tools=ollama_tools)

for tool_call in response.message.tool_calls:
    result = await mcp_client.call_tool(
        tool_call.function.name,
        tool_call.function.arguments,
    )
    messages.append(tool_result_message(tool_call, result))

final_response = ollama.chat(model="qwen3", messages=messages, tools=ollama_tools)
```

The model never needs to understand MCP wire messages. The adapter application
does. The model only needs reliable tool-calling behavior. Ollama documents
tool calling through its API and notes that supported models include examples
such as Qwen 3, Devstral, Qwen2.5, Llama 3.1, and Llama 4; support and quality
vary by model and version.

For this project, the cleanest arrangement is either:

1. an MCP-capable host that uses Ollama as its model backend; or
2. a small custom Python agent that owns one MCP client and one Ollama client.

Do not let the model call the robot directly. Put the robot command behind the
MCP server, the existing Driver Station backend, and an explicit approval and
safety policy.

See Ollama's [tool support](https://ollama.com/blog/tool-support) and
[streaming/tool-calling documentation](https://ollama.com/blog/streaming-tool).

## Environment model

Keep these environments separate:

| Environment | Responsibility |
|---|---|
| FTC Robot Controller | Runs OpModes and the robot-side TCP client |
| Web Driver Station backend | Owns robot TCP, telemetry storage, command routing, and safety |
| MCP server environment | Provides model-facing tools and calls the local backend |
| Model runtime | Ollama or another LLM provider; produces text and possible tool calls |
| MCP host | Owns conversation, MCP clients, permissions, approvals, and tool loop |

Recommended Python setup:

```text
mcp_server/
  pyproject.toml       # dependencies and executable metadata
  uv.lock              # reproducible dependency resolution
  .venv/               # local, never committed
  src/...
```

Configuration should come from environment variables, not hard-coded secrets:

```text
FTC_ADVANCED_API_BASE=http://127.0.0.1:8000
FTC_ADVANCED_MCP_LOG_LEVEL=INFO
```

Use separate development and production settings. The first version should
bind locally, use no public credentials, and expose read-only tools only.

## Safety rules for this project

MCP makes arbitrary code and data access easy, so treat every tool as an API
surface:

- Start with read-only status, telemetry, session, and catalog tools.
- Give every tool a narrow purpose and a strict input schema.
- Never use a free-form “execute arbitrary command” tool.
- Keep robot-control tools separate from analysis tools.
- Require explicit approval for robot-control tools.
- Reuse the backend's allowlist, TTL, target-instance checks, and OpMode safety
  state.
- Return structured errors rather than hiding failures in natural language.
- Log tool name, request ID, duration, and result status, but never secrets.
- Test malformed arguments, missing sessions, disconnected robots, stale command
  targets, duplicate requests, and backend timeouts.
- For stdio, write logs only to stderr.

The MCP protocol provides the message contract; it does not automatically make
a tool safe. Safety belongs to the host, backend, server implementation, and
user approval flow together.

## First implementation milestone

The first useful milestone is intentionally small:

1. Create a Python MCP server using stdio.
2. Add `get_driver_station_status`.
3. Add `list_telemetry_sessions`.
4. Add `get_telemetry_catalog`.
5. Add `read_telemetry_window` with bounded time and sample limits.
6. Connect it to an MCP host and inspect tool discovery.
7. Add tests with a fake backend before adding video analysis or robot control.

Once that works, the video playback/analyzer and scientific analysis can be
implemented as deterministic Python services and exposed through MCP without
making the model responsible for the numerical work.

## Mental checklist

When adding a capability, ask:

```text
Is this context, an executable action, or a reusable workflow?
        |                 |                    |
     resource           tool                prompt

Who controls it?
        |
   host/user approval, not the tool description alone

Where is the authoritative state?
        |
   existing FTC Advanced backend, not a duplicated MCP-side cache
```

The MCP server should remain thin, composable, observable, and replaceable.
The backend should remain authoritative for robot state. The model should
interpret results and request actions, never bypassing those boundaries.

