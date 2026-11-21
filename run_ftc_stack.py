"""Shared launcher for the Driver Station, MCP server, and OpenAI tunnel."""

import argparse
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time
import webbrowser

from ftc_local_config import LocalConfigError, string

HOST = "127.0.0.1"
WEB_PORT = 8000
MCP_PORT = 8001
WINDOWS = os.name == "nt"
services = []
optional_services = {}


def listening(port):
    try:
        with socket.create_connection((HOST, port), timeout=0.25):
            return True
    except OSError:
        return False


def check_services():
    for name, process in services:
        if (code := process.poll()) is not None:
            raise RuntimeError(f"{name} exited with status {code}.")


def start(name, command, *, env=None, port=None):
    print(f"Starting {name}...")
    options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if WINDOWS else {"start_new_session": True}
    services.append((name, subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL, **options)))
    if port is not None:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            check_services()
            if listening(port):
                return
            time.sleep(0.2)
        raise RuntimeError(f"{name} did not listen on {HOST}:{port} within 30 seconds.")


def restart_optional():
    options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if WINDOWS else {"start_new_session": True}
    for name, service in optional_services.items():
        process = service.get("process")
        upstream = None
        if service.get("requires_mcp"):
            upstream = optional_services["Replay MCP"].get("process")
            if upstream is None or upstream.poll() is not None:
                continue
            if process is not None and process.poll() is None and service.get("upstream") is not upstream:
                # OAuth discovery can stay failed after the HTTP listener goes away.
                try:
                    process.terminate()
                except OSError as error:
                    print(f"Could not restart {name}: {error}")
                service["retry_at"] = 0
                continue
        if process is not None and process.poll() is None:
            continue
        if time.monotonic() < service.get("retry_at", 0):
            continue
        if upstream is not None and not listening(MCP_PORT):
            continue
        service["retry_at"] = time.monotonic() + 15
        try:
            print(f"Starting {name}...")
            service["process"] = subprocess.Popen(
                service["command"], env=service["env"], stdin=subprocess.DEVNULL, **options,
            )
            service["upstream"] = upstream
        except OSError as error:
            print(f"{name} unavailable: {error}. Retrying in 15 seconds.")


def stop_services():
    processes = services + [(name, service["process"]) for name, service in optional_services.items()
                           if "process" in service]
    for _, process in reversed(processes):
        try:
            if WINDOWS:
                if process.poll() is None:
                    process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                os.killpg(process.pid, signal.SIGTERM)
        except OSError:
            pass

    for _, process in reversed(processes):
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            if WINDOWS:
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
                )
                if process.poll() is None:
                    process.kill()
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            process.wait()


def interrupt(*_):
    raise KeyboardInterrupt


def optional_setting(section, key):
    try:
        return string(section, key)
    except LocalConfigError as error:
        print(f"Online configuration unavailable: {error}")
        return None


def setup(web_python, mcp_python, discord_python, online):
    pnpm = shutil.which("pnpm")
    corepack = shutil.which("corepack")
    if pnpm:
        frontend_command = [pnpm]
    elif corepack:
        frontend_command = [corepack, "pnpm"]
    else:
        raise RuntimeError("Install Node.js and pnpm before running --setup.")

    environments = [(web_python, "web_driver_station/backend/requirements.txt")]
    if online:
        environments.extend([
            (mcp_python, "mcp_server/requirements.txt"),
            (discord_python, "discord_bot/requirements.txt"),
        ])
    for python, requirements in environments:
        if not python.is_file():
            subprocess.run([sys.executable, "-m", "venv", python.parent.parent], check=True)
        subprocess.run([python, "-m", "pip", "install", "-r", requirements], check=True)

    for frontend in (Path("web_driver_station/frontend"), Path("recording_viewer/frontend")):
        subprocess.run([*frontend_command, "install", "--frozen-lockfile"], cwd=frontend, check=True)
        subprocess.run([*frontend_command, "run", "build"], cwd=frontend, check=True)


def main():
    parser = argparse.ArgumentParser(
        description="Start the local Driver Station and replay library."
    )
    parser.add_argument("--setup", action="store_true", help="Install dependencies and build the dashboard first.")
    parser.add_argument("--online", action="store_true", help="Also start replay MCP, the OpenAI tunnel, and Discord.")
    args = parser.parse_args()

    os.chdir(Path(__file__).resolve().parent)
    venv_python = Path("Scripts/python.exe") if WINDOWS else Path("bin/python")
    web_python = Path(".venv-web").resolve() / venv_python
    mcp_python = Path("mcp_server/.venv").resolve() / venv_python
    discord_python = Path("discord_bot/.venv").resolve() / venv_python
    tunnel_dir = Path("mcp_server/tunnel-client")
    default_client = tunnel_dir / ("tunnel-client.exe" if WINDOWS else "tunnel-client")
    tunnel_client = Path(os.getenv("FTC_ADVANCED_TUNNEL_CLIENT") or default_client).resolve()
    default_profile = tunnel_dir / "profiles/ftc-local.yaml"
    tunnel_profile = Path(os.getenv("FTC_ADVANCED_TUNNEL_PROFILE") or default_profile).resolve()

    signal.signal(signal.SIGTERM, interrupt)
    if WINDOWS:
        signal.signal(signal.SIGBREAK, interrupt)

    if args.setup:
        if sys.version_info < (3, 11):
            raise RuntimeError("Install Python 3.11+ or set FTC_ADVANCED_PYTHON.")
        setup(web_python, mcp_python, discord_python, args.online)
    if not (web_python.is_file() and Path("web_driver_station/frontend/dist/index.html").is_file()
            and Path("recording_viewer/frontend/dist/index.html").is_file()):
        raise RuntimeError("The Python environment or web builds are missing. Rerun with --setup.")
    if listening(WEB_PORT):
        raise RuntimeError(f"{HOST}:{WEB_PORT} is already in use. Stop that service before starting the stack.")

    web_url = f"http://{HOST}:{WEB_PORT}"
    start(
        "Driver Station",
        [web_python, "-m", "uvicorn", "web_driver_station.backend.main:app", "--host", HOST, "--port", str(WEB_PORT)],
        port=WEB_PORT,
    )
    print(f"Driver Station dashboard: {web_url}")
    try:
        opened = webbrowser.open(web_url, new=2)
    except OSError:
        opened = False
    if not opened:
        print(f"Could not open the browser automatically. Open {web_url} in your browser.")

    if args.online:
        online_env = {**os.environ, "FTC_ADVANCED_API_BASE": web_url}
        if mcp_python.is_file() and not listening(MCP_PORT):
            optional_services["Replay MCP"] = {
                "command": [mcp_python, "-m", "mcp_server"],
                "env": {**online_env, "FTC_ADVANCED_MCP_HOST": HOST, "FTC_ADVANCED_MCP_PORT": str(MCP_PORT),
                        "FTC_ADVANCED_MCP_REPLAY_ONLY": "1"},
            }
            key = os.getenv("CONTROL_PLANE_API_KEY") or optional_setting("openai", "control_plane_api_key")
            if key and tunnel_client.is_file() and os.access(tunnel_client, os.X_OK) and tunnel_profile.is_file():
                optional_services["OpenAI tunnel"] = {
                    "command": [tunnel_client, "run", "--profile-file", tunnel_profile],
                    "env": {**online_env, "CONTROL_PLANE_API_KEY": key},
                    "requires_mcp": True,
                }
            else:
                print("OpenAI tunnel unavailable. Set up the client, profile, and tunnel API key.")
        else:
            print("Replay MCP unavailable. Run --setup --online or free up port 8001.")
        if discord_python.is_file() and (os.getenv("DISCORD_BOT_TOKEN") or optional_setting("discord", "bot_token")):
            optional_services["Discord bot"] = {
                "command": [discord_python, "-m", "discord_bot.bot"],
                "env": online_env,
            }
        else:
            print("Discord bot unavailable. Run --setup --online and configure its bot token.")
    print("Press Ctrl+C to stop the stack.")
    while True:
        check_services()
        restart_optional()
        time.sleep(0.25)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopping FTC Advanced...")
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"FTC Advanced: {error}", file=sys.stderr)
        sys.exit(1)
    finally:
        stop_services()
