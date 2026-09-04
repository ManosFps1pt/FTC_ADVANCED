"""Standalone scrcpy camera backend and complete native CLI access (Python 3.10+).

Install on Windows: winget install --exact --id Genymobile.scrcpy
Install the test website: python -m pip install -r requirements-scrcpy.txt
No permanent Android app is required.

Run this file without arguments for the Flask test website. Use "native-help" for ALL options
and keyboard shortcuts supported by the installed scrcpy, and "native -- ..."
to pass those options unchanged. Camera capture requires Android 12 or newer.

Import ScrcpyBackend and CameraOptions for start_camera(), status(), wait(),
and stop(). This module does not start a camera at import time.
"""
from __future__ import annotations

import argparse
from collections import deque
from dataclasses import dataclass, replace
import json
import logging
import math
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from typing import Sequence

LOG = logging.getLogger("scrcpy_backend")
ROOT = Path(__file__).resolve().parent

# The dashboard needs a deliberately small camera-control surface.  Keep the
# low-level options below available to direct callers and the native CLI, but
# do not publish or accept them through the dashboard-facing web endpoint.
DASHBOARD_CAMERA_FIELDS = frozenset({
    "facing", "aspect_ratio", "fps", "flip",
})
BROWSER_PREVIEW_MAX_SIZE = 240
BROWSER_PREVIEW_MAX_FPS = 10

# A private hidden console lets stop() work from a web server/IDE with no console,
# without sending Ctrl+C to the parent or unrelated ADB/robot processes.
_WINDOWS_INTERRUPT = """
import ctypes, sys, time
k = ctypes.WinDLL('kernel32', use_last_error=True)
k.FreeConsole()
if not k.AttachConsole(int(sys.argv[1])):
    raise ctypes.WinError(ctypes.get_last_error())
handler_type = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_ulong)
handler = handler_type(lambda event: 1)
if not k.SetConsoleCtrlHandler(handler, True):
    raise ctypes.WinError(ctypes.get_last_error())
if not k.GenerateConsoleCtrlEvent(1, 0):
    raise ctypes.WinError(ctypes.get_last_error())
time.sleep(0.2)
k.FreeConsole()
"""


class ScrcpyError(RuntimeError):
    """An actionable discovery, preflight or process error."""


def find_executable(name: str, explicit: str | None = None) -> str:
    """Find tools even in an existing shell whose PATH predates a WinGet install."""
    requested = explicit or os.environ.get("SCRCPY" if name == "scrcpy" else "ADB")
    if requested:
        found = shutil.which(requested)
        if found:
            return str(Path(found).resolve())
        raise ScrcpyError(f"Cannot find {name} executable: {requested}")
    found = shutil.which(name)
    if found:
        return str(Path(found).resolve())
    local = os.environ.get("LOCALAPPDATA")
    if local and os.name == "nt":
        base = Path(local)
        candidates = [base / "Microsoft/WinGet/Links" / f"{name}.exe"]
        if name == "adb":
            candidates.insert(0, base / "Android/Sdk/platform-tools/adb.exe")
        candidates.extend(sorted(
            (base / "Microsoft/WinGet/Packages").glob(
                f"Genymobile.scrcpy_*/scrcpy-win*/{name}.exe"
            ), key=lambda p: p.stat().st_mtime, reverse=True,
        ))
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate.resolve())
    hint = ("winget install --exact --id Genymobile.scrcpy" if name == "scrcpy"
            else "Install Android SDK Platform-Tools, or set ADB to adb.exe.")
    raise ScrcpyError(f"{name} was not found. {hint}")


def _positive(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be a finite number greater than zero")
    return number


@dataclass(frozen=True)
class CameraOptions:
    serial: str | None = None
    camera_id: str | None = None
    facing: str | None = None
    size: str | None = None
    max_size: int | None = None
    aspect_ratio: str | None = None
    fps: int | None = None
    flip: bool = False
    high_speed: bool = False
    zoom: float | None = None
    torch: bool = False
    video_codec: str = "h264"
    video_bit_rate: str = "16M"
    video_encoder: str | None = None
    video_codec_options: str | None = None
    audio: bool = True
    audio_source: str = "mic"
    audio_codec: str = "aac"
    audio_bit_rate: str = "128K"
    audio_encoder: str | None = None
    audio_codec_options: str | None = None
    require_audio: bool = False
    orientation: str | None = None
    duration: int | None = None
    fullscreen: bool = False
    always_on_top: bool = False

    def arguments(self, serial: str, *, preview: bool, output: Path | None) -> list[str]:
        if self.camera_id is not None and self.facing is not None:
            raise ScrcpyError("Choose camera_id OR facing, not both.")
        if self.size is not None:
            if not re.fullmatch(r"[1-9]\d*x[1-9]\d*", self.size):
                raise ScrcpyError("Camera size must be WIDTHxHEIGHT, for example 1920x1080.")
            if self.max_size is not None or self.aspect_ratio is not None:
                raise ScrcpyError("Explicit size cannot be combined with max_size or aspect_ratio.")
        if self.facing not in (None, "front", "back", "external"):
            raise ScrcpyError("Camera facing must be front, back or external.")
        for name in ("fps", "max_size", "zoom", "duration"):
            value = getattr(self, name)
            if value is not None and (not math.isfinite(value) or value <= 0):
                raise ScrcpyError(f"{name} must be finite and greater than zero.")
        for name in ("fps", "max_size", "duration"):
            value = getattr(self, name)
            if value is not None and int(value) != value:
                raise ScrcpyError(f"{name} must be a whole number.")
        if self.high_speed and self.fps is None:
            raise ScrcpyError("High-speed capture requires an explicit fps from the sizes listing.")
        if self.flip and self.orientation is not None:
            raise ScrcpyError("flip cannot be combined with an explicit orientation.")
        if not self.audio and self.require_audio:
            raise ScrcpyError("require_audio cannot be combined with audio=False.")
        if not preview and output is None:
            raise ScrcpyError("Headless capture requires an output recording.")
        if not preview and (self.fullscreen or self.always_on_top):
            raise ScrcpyError("Window options require preview=True.")
        args = [
            f"--serial={serial}", "--video-source=camera",
            f"--video-codec={self.video_codec}", f"--video-bit-rate={self.video_bit_rate}",
            "--no-audio-playback", "--no-clipboard-autosync", "--no-terminal-title",
            "--no-downsize-on-error",
        ]
        values = {
            "camera-id": self.camera_id,
            "camera-facing": self.facing if self.camera_id is None else None,
            "camera-size": self.size, "max-size": self.max_size,
            "camera-ar": self.aspect_ratio, "camera-fps": self.fps,
            "camera-zoom": self.zoom, "video-encoder": self.video_encoder,
            "video-codec-options": self.video_codec_options,
            "orientation": "flip0" if self.flip else self.orientation,
            "time-limit": int(self.duration) if self.duration is not None else None,
        }
        if self.audio:
            values.update({
                "audio-source": self.audio_source, "audio-codec": self.audio_codec,
                "audio-bit-rate": self.audio_bit_rate, "audio-encoder": self.audio_encoder,
                "audio-codec-options": self.audio_codec_options,
            })
        else:
            args.append("--no-audio")
        if self.camera_id is None and self.facing is None:
            args.append("--camera-facing=back")
        args.extend(f"--{key}={value}" for key, value in values.items() if value is not None)
        for enabled, flag in (
            (self.high_speed, "--camera-high-speed"), (self.torch, "--camera-torch"),
            (self.require_audio, "--require-audio"), (self.fullscreen, "--fullscreen"),
            (self.always_on_top, "--always-on-top"),
        ):
            if enabled:
                args.append(flag)
        if preview:
            args.append("--print-fps")
        else:
            args.extend(["--no-window", "--no-playback", "--no-control"])
        if output is not None:
            args.append(f"--record={output}")
        return args


class ScrcpyBackend:
    """Own one scrcpy process; use separate instances for simultaneous cameras.

    RUNNING means the process is alive, not that frames have been confirmed.
    Preview FPS logs establish frame activity. Headless file growth is weaker
    evidence, exposed separately. Timestamps are laptop observations, not camera
    exposure timestamps or automatic telemetry synchronization.
    """

    def __init__(self, scrcpy_path: str | None = None, adb_path: str | None = None) -> None:
        self.scrcpy_path = find_executable("scrcpy", scrcpy_path)
        self._adb_path = adb_path
        self._lock = threading.RLock()
        self._finished = threading.Event()
        self._finished.set()
        self._process: subprocess.Popen[str] | None = None
        self._reader: threading.Thread | None = None
        self._logs: deque[str] = deque(maxlen=100)
        self._output: Path | None = None
        self._command: list[str] = []
        self._state = "IDLE"
        self._error: str | None = None
        self._returncode: int | None = None
        self._forced = False
        self._finalized = False
        self._started_ns: int | None = None
        self._ended_ns: int | None = None
        self._last_frame_ns: int | None = None
        self._last_growth_ns: int | None = None
        self._bytes = 0
        self._fps: float | None = None
        self._preview = None

    def _environment(self, *, require_adb: bool = True) -> dict[str, str]:
        env = os.environ.copy()
        if require_adb:
            env["ADB"] = find_executable("adb", self._adb_path)
        return env

    @staticmethod
    def _query(command: list[str], env: dict[str, str], timeout: float = 30) -> str:
        try:
            result = subprocess.run(
                command, env=env, stdin=subprocess.DEVNULL, capture_output=True,
                text=True, encoding="utf-8", errors="replace", timeout=timeout,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except subprocess.TimeoutExpired as exc:
            # Do not include arguments: an ADB pairing command contains a code.
            raise ScrcpyError(f"{Path(command[0]).name} timed out after {timeout} seconds.") from exc
        except OSError as exc:
            raise ScrcpyError(f"Command failed: {command[0]}: {exc}") from exc
        text = (result.stdout + result.stderr).strip()
        if result.returncode:
            raise ScrcpyError(text or f"Command exited with code {result.returncode}")
        return text

    def adb(self, *args: str, timeout: float = 30) -> str:
        env = self._environment()
        return self._query([env["ADB"], *args], env, timeout)

    def devices(self) -> list[dict[str, str]]:
        devices = []
        for line in self.adb("devices", "-l").splitlines():
            fields = line.split()
            if len(fields) < 2 or fields[0] in ("List", "*"):
                continue
            if fields[1] not in ("device", "offline", "unauthorized", "recovery", "sideload", "no"):
                continue
            item = {"serial": fields[0], "state": fields[1]}
            item.update(dict(field.split(":", 1) for field in fields[2:] if ":" in field))
            devices.append(item)
        return devices

    def select_device(self, serial: str | None = None) -> str:
        serial = serial or os.environ.get("ANDROID_SERIAL") or None
        devices = self.devices()
        if serial is not None:
            match = next((d for d in devices if d["serial"] == serial), None)
            if match is None or match["state"] != "device":
                raise ScrcpyError(f"Device {serial!r} is not connected and authorized. Check 'devices'.")
            return serial
        ready = [d for d in devices if d["state"] == "device"]
        if len(ready) != 1:
            raise ScrcpyError("Select exactly one authorized device with --serial; see 'devices'.")
        return ready[0]["serial"]

    def check_camera(self, serial: str) -> None:
        sdk = self.adb("-s", serial, "shell", "getprop", "ro.build.version.sdk")
        if not sdk.isdigit() or int(sdk) < 31:
            raise ScrcpyError(f"Direct camera capture requires Android 12+ (API 31); device reports {sdk!r}.")

    def inspect(self, feature: str, serial: str | None = None,
                camera_id: str | None = None) -> str:
        flags = {"cameras": "--list-cameras", "sizes": "--list-camera-sizes",
                 "encoders": "--list-encoders", "displays": "--list-displays", "apps": "--list-apps"}
        if feature not in flags:
            raise ScrcpyError(f"Unknown inspection: {feature}")
        selected = self.select_device(serial)
        args = [self.scrcpy_path, f"--serial={selected}", flags[feature]]
        if feature in ("cameras", "sizes"):
            args.append("--video-source=camera")
        if camera_id is not None:
            if feature not in ("cameras", "sizes"):
                raise ScrcpyError("camera_id is only applicable to cameras and sizes.")
            args.append(f"--camera-id={camera_id}")
        return self._query(args, self._environment(), timeout=60)

    def native_help(self) -> str:
        return self._query([self.scrcpy_path, "--help"], self._environment(require_adb=False))

    def doctor(self) -> dict[str, object]:
        info: dict[str, object] = {"scrcpy": self.scrcpy_path}
        info["version"] = self._query([self.scrcpy_path, "--version"], self._environment(require_adb=False))
        info["adb"] = find_executable("adb", self._adb_path)
        info["devices"] = self.devices()
        return info

    def start_camera(self, options: CameraOptions, *, output: str | Path | None = None,
                     preview: bool = True, browser_preview: bool = False) -> dict[str, object]:
        with self._lock:
            if not self._finished.is_set():
                raise ScrcpyError("A scrcpy session is already running or finalizing.")
            destination = Path(output).expanduser().resolve() if output is not None else None
            preview_options = options
            if browser_preview and destination is None:
                # The browser relay uses CPU decoding. Request a small, slow
                # source stream before it reaches Python so it stays current
                # instead of accumulating several seconds of old H.264 frames.
                # Do not apply this to recordings: their selected quality wins.
                preview_options = replace(
                    options,
                    max_size=BROWSER_PREVIEW_MAX_SIZE if options.size is None else None,
                    fps=min(options.fps or BROWSER_PREVIEW_MAX_FPS, BROWSER_PREVIEW_MAX_FPS),
                )
            # Validate before making ADB calls or creating files.
            preview_options.arguments(options.serial or "", preview=preview or browser_preview, output=destination)
            if destination is not None and destination.suffix.lower() not in (".mp4", ".mkv"):
                raise ScrcpyError("Camera recordings must use an .mp4 or .mkv extension.")
            serial = self.select_device(options.serial)
            self.check_camera(serial)
            args = preview_options.arguments(serial, preview=preview or browser_preview, output=destination)
            if browser_preview and not preview:
                args = [arg for arg in args if arg != "--print-fps"]
                args.extend(["--no-window", "--no-playback", "--no-control"])
            if destination is not None:
                destination.parent.mkdir(parents=True, exist_ok=True)
                try:
                    # Reserve exclusively: do not silently truncate an existing recording.
                    with destination.open("xb"):
                        pass
                except FileExistsError as exc:
                    raise ScrcpyError(f"Recording already exists: {destination}") from exc
            return self._launch(args, destination, browser_preview=browser_preview)

    def start_native(self, args: Sequence[str]) -> dict[str, object]:
        """Exact advanced passthrough, with scrcpy's own validation and defaults.

        Native mode deliberately does not infer camera/file configuration, apply
        camera role checks, or claim recording verification. It exposes ALL
        upstream features, including OTG, virtual displays and audio-only capture.
        """
        if not args:
            raise ScrcpyError("Provide native scrcpy arguments after --; see native-help.")
        with self._lock:
            return self._launch(list(args), None, native=True)

    def _launch(self, args: list[str], output: Path | None,
                native: bool = False, browser_preview: bool = False) -> dict[str, object]:
        if not self._finished.is_set():
            raise ScrcpyError("A scrcpy session is already running or finalizing.")
        env = self._environment(require_adb=not (native and any(
            flag in args for flag in ("--otg", "--help", "-h", "--version", "-v")
        )))
        if browser_preview and output is None and "--no-window" in args:
            # scrcpy disables video when neither a renderer nor a recorder exists.
            # SDL's offscreen renderer keeps the stream alive without any file/window.
            args = [arg for arg in args if arg not in ("--no-window", "--no-playback")]
            env["SDL_VIDEODRIVER"] = "dummy"
            env["SDL_RENDER_DRIVER"] = "software"
        if self._preview is not None:
            self._preview.close()
        self._preview = None
        if browser_preview:
            if not re.search(r"\bscrcpy 4\.1(?:\s|$)", self._query([self.scrcpy_path, "--version"], env)):
                raise ScrcpyError("Browser preview currently requires scrcpy 4.1; disable it for other versions.")
            try:
                from scrcpy_preview import BrowserPreview
                self._preview = BrowserPreview()
            except ImportError as exc:
                raise ScrcpyError("Install browser preview dependencies: python -m pip install -r requirements-scrcpy.txt") from exc
            args = [*args, *self._preview.arguments()]
        self._logs.clear()
        self._output, self._command = output, [self.scrcpy_path, *args]
        self._state, self._error, self._returncode = "STARTING", None, None
        self._forced, self._finalized = False, False
        self._started_ns, self._ended_ns = time.perf_counter_ns(), None
        self._last_frame_ns, self._last_growth_ns, self._fps, self._bytes = None, None, None, 0
        kwargs: dict[str, object] = {}
        if os.name == "nt":
            startup = subprocess.STARTUPINFO()
            startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startup.wShowWindow = 0  # hide the helper console; scrcpy owns its preview window
            kwargs.update(creationflags=subprocess.CREATE_NEW_CONSOLE, startupinfo=startup)
        else:
            kwargs["start_new_session"] = True
        self._finished.clear()
        try:
            self._process = subprocess.Popen(
                self._command, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                bufsize=1, **kwargs,
            )
        except OSError as exc:
            if self._preview is not None:
                self._preview.close()
            self._state, self._error = "FAILED", str(exc)
            self._ended_ns = time.perf_counter_ns()
            self._finished.set()
            raise ScrcpyError(f"Could not start scrcpy: {exc}") from exc
        self._state = "RUNNING"
        self._reader = threading.Thread(target=self._read_output, name="scrcpy-output", daemon=True)
        self._reader.start()
        return self.status()

    def _read_output(self) -> None:
        process = self._process
        assert process is not None and process.stdout is not None
        try:
            for line in process.stdout:
                line = line.strip()
                if not line:
                    continue
                with self._lock:
                    self._logs.append(line)
                    match = re.search(r"\b(\d+(?:\.\d+)?)\s+fps\b", line, re.IGNORECASE)
                    if match:
                        self._fps = float(match[1])
                        if self._fps > 0:
                            self._last_frame_ns = time.perf_counter_ns()
                    if "Recording complete to " in line:
                        self._finalized = True
                    if "ERROR:" in line:
                        self._error = line
                LOG.info("%s", line)
        finally:
            process.stdout.close()
            returncode = process.wait()
            if self._preview is not None:
                self._preview.close()
            with self._lock:
                self._returncode = returncode
                self._ended_ns = time.perf_counter_ns()
                self._refresh_bytes()
                if self._forced:
                    self._state = "FAILED"
                elif returncode == 2:
                    self._state = "DISCONNECTED"
                    self._error = "Device disconnected; any received recording is retained."
                elif returncode != 0:
                    self._state = "FAILED"
                    self._error = self._error or f"scrcpy exited with code {returncode}."
                elif self._output is not None:
                    if self._finalized and self._bytes > 0:
                        self._state = "COMPLETED"
                    else:
                        self._state = "FAILED"
                        self._error = self._error or "scrcpy did not confirm a non-empty finalized recording."
                else:
                    self._state = "STOPPED"
                self._finished.set()

    def _refresh_bytes(self) -> None:
        if self._output is None:
            return
        try:
            size = self._output.stat().st_size
        except OSError:
            return
        if size > self._bytes:
            self._last_growth_ns = time.perf_counter_ns()
        self._bytes = size

    def status(self) -> dict[str, object]:
        with self._lock:
            self._refresh_bytes()
            running = not self._finished.is_set()
            age = None if self._last_frame_ns is None else (
                time.perf_counter_ns() - self._last_frame_ns
            ) / 1_000_000_000
            return {
                "state": self._state, "running": running,
                "pid": self._process.pid if running and self._process else None,
                "returncode": self._returncode, "command": list(self._command),
                "output": str(self._output) if self._output else None, "bytes": self._bytes,
                "scrcpy_finalized": self._finalized,
                "frame_activity_observed": self._last_frame_ns is not None,
                "last_reported_fps": self._fps,
                "seconds_since_frame_report": age,
                "frame_reports_stale": running and age is not None and age > 5,
                "last_output_growth_observed_ns": self._last_growth_ns,
                "started_monotonic_ns": self._started_ns, "ended_monotonic_ns": self._ended_ns,
                "error": self._error, "logs": list(self._logs),
                "browser_preview": self._preview.status() if self._preview else {"enabled": False},
            }

    def preview_frame(self) -> bytes | None:
        return self._preview.frame() if self._preview else None

    def wait(self, timeout: float | None = None) -> dict[str, object]:
        if not self._finished.wait(timeout):
            raise TimeoutError("scrcpy is still running.")
        return self.status()

    def stop(self, timeout: float = 15) -> dict[str, object]:
        if not math.isfinite(timeout) or timeout <= 0:
            raise ScrcpyError("Stop timeout must be finite and greater than zero.")
        with self._lock:
            process = self._process
            if self._finished.is_set() or process is None:
                return self.status()
            self._state = "STOPPING"
        try:
            if process.poll() is None:
                if os.name == "nt":
                    self._query(
                        [sys.executable, "-c", _WINDOWS_INTERRUPT, str(process.pid)],
                        os.environ.copy(), timeout=min(timeout, 5),
                    )
                else:
                    process.send_signal(signal.SIGINT)
        except (OSError, ScrcpyError) as exc:
            if process.poll() is None:
                with self._lock:
                    self._error = f"Graceful stop failed: {exc}"
        if not self._finished.wait(timeout):
            with self._lock:
                self._forced = True
                self._error = "Graceful stop timed out; scrcpy was terminated. Recording may be incomplete."
            try:
                process.kill()
            except ProcessLookupError:
                pass
            if not self._finished.wait(5):
                raise ScrcpyError("scrcpy did not finish after forced termination.")
        return self.status()

    def __enter__(self) -> ScrcpyBackend:
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()


def parse_native_options(help_text: str) -> list[dict[str, object]]:
    """Read the installed CLI's option headings, keeping upstream descriptions."""
    options: list[dict[str, object]] = []
    current = None
    for line in help_text.splitlines():
        if line.startswith("Shortcuts:"):
            break
        if re.match(r"^    -\S", line):
            names = re.findall(r"--[a-z0-9-]+|(?<![\w-])-[A-Za-z0-9](?=[,\s]|$)", line)
            if not names:
                continue
            current = {
                "flag": next((name for name in names if name.startswith("--")), names[0]),
                "heading": line.strip(), "description": "",
                "takes_value": "=" in line, "optional_value": "[=" in line,
            }
            options.append(current)
        elif current is not None and line.startswith("        "):
            current["description"] += line.strip() + " "
    return options


def create_web_app(backend: ScrcpyBackend | None = None, recordings_dir: Path | None = None):
    """A local, single-session testing surface; no main-app integration."""
    import secrets
    from dataclasses import fields, replace
    from datetime import datetime

    try:
        from flask import Flask, abort, jsonify, render_template, request, send_file, Response
        from werkzeug.exceptions import HTTPException
    except ImportError as exc:
        raise ScrcpyError("Install Flask: python -m pip install -r requirements-scrcpy.txt") from exc

    app = Flask(__name__, template_folder=str(ROOT))
    app.config.update(MAX_CONTENT_LENGTH=65536, TRUSTED_HOSTS=["127.0.0.1", "localhost", "[::1]"])
    backend = backend or ScrcpyBackend()
    directory = (recordings_dir or ROOT / "web_driver_station/recordings/scrcpy-test").resolve()
    token = secrets.token_urlsafe(32)
    actions = threading.Lock()
    active_output: Path | None = None
    app.extensions["scrcpy_backend"] = backend
    app.extensions["scrcpy_token"] = token
    native_help: str | None = None
    session_kind = None
    # Recordings default to a smooth 60 FPS. Browser-only previews are reduced
    # separately before they reach the Python decoder.
    camera_options = CameraOptions(fps=60)
    desktop_preview = False
    browser_preview_enabled = True
    startup_error = None
    last_recording = None
    shutdown = threading.Event()
    worker = None

    def begin_preview():
        nonlocal session_kind, active_output, startup_error
        backend.start_camera(replace(camera_options, duration=None), output=None,
                             preview=desktop_preview,
                             browser_preview=browser_preview_enabled or not desktop_preview)
        session_kind, active_output, startup_error = "preview", None, None

    def resume_preview():
        nonlocal last_recording, startup_error, session_kind
        last_recording = backend.status()
        try:
            begin_preview()
        except (ScrcpyError, OSError, ValueError) as exc:
            session_kind = None
            startup_error = f"Recording ended; could not restart preview: {exc}"

    def background_loop():
        nonlocal startup_error
        with actions:
            if not shutdown.is_set() and not backend.status()["running"]:
                try:
                    begin_preview()
                except (ScrcpyError, OSError, ValueError) as exc:
                    startup_error = f"Automatic preview unavailable: {exc}. Select a phone and click Start preview."
        while not shutdown.wait(.5):
            with actions:
                if session_kind == "record" and backend.status()["state"] == "COMPLETED":
                    resume_preview()

    def start_automatic_preview():
        nonlocal worker
        if worker is None:
            worker = threading.Thread(target=background_loop, name="scrcpy-web-camera", daemon=True)
            worker.start()

    def close_camera():
        shutdown.set()
        with actions:
            backend.stop()
        if worker is not None:
            worker.join(timeout=2)

    app.extensions["start_automatic_preview"] = start_automatic_preview
    app.extensions["close_camera"] = close_camera

    @app.before_request
    def local_request():
        # No CORS, shell execution, public bind, or Flask debugger is enabled.
        if request.method == "POST":
            if not secrets.compare_digest(request.headers.get("X-Scrcpy-Token", ""), token):
                abort(403, "Reload the test page before sending commands.")
            if not request.is_json:
                abort(415, "Commands require JSON.")

    @app.after_request
    def response_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Cache-Control"] = "no-store"
        return response

    def error_response(error):
        return jsonify(error=str(error)), error.code if isinstance(error, HTTPException) else 400

    for error_type in (ScrcpyError, ValueError, TypeError, OSError, HTTPException):
        app.register_error_handler(error_type, error_response)

    def body() -> dict:
        data = request.get_json()
        if not isinstance(data, dict):
            abort(400, "Expected a JSON object.")
        return data

    def output_path(name: str) -> Path:
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _().-]{0,175}\.(mp4|mkv|m4a|mka|opus|aac|flac|wav)", name, re.I):
            abort(400, "Use a recording filename such as test.mp4, without folders.")
        path = directory / name
        if path.is_reserved() or path.resolve().parent != directory:
            abort(400, "Invalid recording filename.")
        return path

    def idle():
        if backend.status()["running"]:
            abort(409, "Stop the current scrcpy session first.")

    @app.get("/")
    def index():
        return render_template("scrcpy_test.html", token=token)

    @app.get("/api/features")
    def features():
        camera_fields = [
            {"name": f.name, "default": getattr(camera_options, f.name), "type": f.type}
            for f in fields(CameraOptions) if f.name in DASHBOARD_CAMERA_FIELDS
        ]
        # Keep empty legacy keys so the standalone test page remains loadable
        # while its advanced native controls are intentionally unavailable.
        return jsonify(camera=camera_fields, native=[], help="",
                       recordings_directory=str(directory))

    @app.get("/api/preview/frame")
    def preview_frame():
        frame = backend.preview_frame()
        if frame is None:
            return Response(status=204)
        return Response(frame, mimetype="image/jpeg")

    @app.get("/api/devices")
    def devices():
        return jsonify(devices=backend.devices())

    @app.get("/api/status")
    def status():
        state = backend.status()
        state["web_output"] = active_output.name if active_output else None
        state["session_kind"] = session_kind
        state["startup_error"] = startup_error
        state["last_recording"] = last_recording
        return jsonify(state)

    @app.post("/api/inspect")
    def inspect_device():
        data = body()
        with actions:
            feature = data.get("feature")
            if feature == "doctor":
                return jsonify(text=json.dumps(backend.doctor(), indent=2))
            return jsonify(text=backend.inspect(feature, data.get("serial") or None,
                                                data.get("camera_id") or None))

    @app.post("/api/camera")
    def start_camera():
        nonlocal active_output, session_kind, camera_options, desktop_preview, browser_preview_enabled, startup_error
        data = body()
        mode = data.get("mode")
        if mode not in ("preview", "record"):
            abort(400, "Mode must be preview or record.")
        values = data.get("options", {})
        if not isinstance(values, dict):
            abort(400, "Camera options must be an object.")
        schema = {
            f.name: f.type for f in fields(CameraOptions)
            if f.name in DASHBOARD_CAMERA_FIELDS or f.name == "serial"
        }
        for key, value in values.items():
            if key not in schema:
                abort(400, f"Unknown camera option: {key}")
            expected = schema[key]
            valid = ((value is None and "None" in expected)
                     or (expected == "bool" and type(value) is bool)
                     or (expected.startswith("int") and type(value) is int)
                     or (expected.startswith("float") and type(value) in (int, float))
                     or (expected.startswith("str") and isinstance(value, str)))
            if not valid:
                abort(400, f"Invalid value for {key}.")
        preview = data.get("preview", True)
        if type(preview) is not bool:
            abort(400, "Preview must be a boolean.")
        browser_preview = data.get("browser_preview", False)
        if type(browser_preview) is not bool:
            abort(400, "Browser preview must be a boolean.")
        filename = data.get("filename") or datetime.now().strftime("capture-%Y%m%d-%H%M%S-%f.mp4")
        output = output_path(filename) if mode == "record" else None
        options = CameraOptions(**values)
        if mode == "preview":
            options = replace(options, duration=None)
        effective_preview = preview if browser_preview else mode == "preview" or preview
        options.arguments(options.serial or "", preview=effective_preview or browser_preview, output=output)
        if output is not None and output.suffix.lower() not in (".mp4", ".mkv"):
            abort(400, "Camera recordings must use an .mp4 or .mkv extension.")
        with actions:
            running = backend.status()["running"]
            if running and session_kind != "preview":
                abort(409, "Stop the current recording or native session first.")
            if output is not None and output.exists():
                abort(409, "That recording already exists. Choose a new filename.")
            if running:
                backend.stop()
            kwargs = {"browser_preview": True} if browser_preview else {}
            result = backend.start_camera(options, output=output, preview=effective_preview, **kwargs)
            camera_options = options
            desktop_preview, browser_preview_enabled = preview, browser_preview
            session_kind = mode
            startup_error = None
            active_output = output
            return jsonify(result)

    @app.post("/api/native")
    def start_native():
        nonlocal active_output, session_kind, startup_error
        args = body().get("args")
        if not isinstance(args, list) or not args or not all(isinstance(a, str) and a and "\x00" not in a for a in args):
            abort(400, "Choose native options or provide one argument per line.")
        # Keep recording files in the test library, including native audio-only recordings.
        rewritten, output, i = [], None, 0
        while i < len(args):
            arg = args[i]
            name = None
            if arg in ("-r", "--record"):
                i += 1
                if i == len(args):
                    abort(400, "The record option needs a filename.")
                name = args[i]
            elif arg.startswith("--record="):
                name = arg.partition("=")[2]
            elif arg.startswith("-r") and not arg.startswith("--"):
                name = arg[2:].lstrip("=")
            if name is not None:
                if output is not None:
                    abort(400, "Specify a recording filename only once.")
                output = output_path(name)
                rewritten.append(f"--record={output}")
            else:
                rewritten.append(arg)
            i += 1
        with actions:
            idle()
            if output is not None:
                output.parent.mkdir(parents=True, exist_ok=True)
                if output.exists():
                    abort(409, "That recording already exists. Choose a new filename.")
                with output.open("xb"):
                    pass
            result = backend.start_native(rewritten)
            session_kind, startup_error = "native", None
            active_output = output
            return jsonify(result)

    @app.post("/api/stop")
    def stop():
        nonlocal session_kind, startup_error, last_recording
        with actions:
            result = backend.stop()
            if session_kind == "record":
                last_recording = result
            session_kind, startup_error = None, None
            return jsonify(result)

    @app.post("/api/preview/stop")
    def stop_preview():
        """Stop only a live preview, leaving recording sessions untouched."""
        nonlocal session_kind, startup_error
        with actions:
            if session_kind != "preview":
                abort(409, "No camera preview is active.")
            result = backend.stop()
            session_kind, startup_error = None, None
            return jsonify(result)

    @app.post("/api/recording/stop")
    def stop_recording():
        with actions:
            if session_kind != "record":
                abort(409, "No camera recording is active.")
            result = backend.stop()
            resume_preview()
            return jsonify(result)

    @app.post("/api/wireless")
    def wireless():
        data = body()
        action, address = data.get("action"), data.get("address", "")
        if action not in ("pair", "connect", "disconnect"):
            abort(400, "Choose pair, connect or disconnect.")
        if not isinstance(address, str) or not re.fullmatch(r"(?:[\w.-]+|\[[0-9a-fA-F:]+\]):\d{1,5}", address):
            abort(400, "Use HOST:PORT from the phone's wireless debugging settings.")
        args = [action, address]
        if action == "pair":
            code = data.get("code", "")
            if not isinstance(code, str) or not re.fullmatch(r"\d{6}", code):
                abort(400, "Enter the six-digit pairing code.")
            args.append(code)
        with actions:
            text = backend.adb(*args, timeout=60)
        if re.search(r"failed|cannot|unable|error", text, re.I):
            abort(400, text)
        return jsonify(text=text)

    @app.get("/api/recordings")
    def recordings():
        running = backend.status()["running"]
        files = []
        if directory.exists():
            for path in sorted(directory.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
                if not path.is_file() or path.resolve().parent != directory:
                    continue
                files.append({"name": path.name, "bytes": path.stat().st_size,
                              "active": running and path == active_output})
        return jsonify(files=files)

    @app.get("/api/recordings/<name>")
    def recording(name):
        path = output_path(name)
        if backend.status()["running"] and path == active_output:
            abort(409, "Stop recording before playing or downloading the file.")
        if not path.is_file():
            abort(404, "Recording not found.")
        return send_file(path, as_attachment=request.args.get("download") == "1", conditional=True)

    return app


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Direct Android camera capture through scrcpy; no permanent phone app.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples (run from the repository root):
  python scrcpy_backend.py                         # Local testing website
  python scrcpy_backend.py serve --port 8766
  python scrcpy_backend.py doctor
  python scrcpy_backend.py devices
  python scrcpy_backend.py cameras --serial PHONE_SERIAL
  python scrcpy_backend.py sizes --serial PHONE_SERIAL --camera-id 0
  python scrcpy_backend.py preview --serial PHONE_SERIAL --size 1920x1080 --fps 30
  python scrcpy_backend.py record --serial PHONE_SERIAL --output clip.mp4 --duration 10
  python scrcpy_backend.py record --output clip.mkv --preview --no-audio
  python scrcpy_backend.py native-help
  python scrcpy_backend.py native -- --serial=PHONE_SERIAL --video-source=display
  python scrcpy_backend.py native -- --tcpip --video-source=camera --record=wireless.mp4

Ctrl+C stops the owned process gracefully. native-help lists ALL upstream
features and keyboard shortcuts. Native mode uses scrcpy's own defaults and
file-overwrite behavior; use record for managed camera capture.
""",
    )
    parser.add_argument("--scrcpy", help="scrcpy executable (or set SCRCPY)")
    parser.add_argument("--adb", help="ADB executable (or set ADB)")
    sub = parser.add_subparsers(dest="command")
    serve = sub.add_parser("serve", help="Start the local Flask test website (default)")
    serve.add_argument("--port", type=int, default=8765)
    for name in ("doctor", "devices", "native-help"):
        sub.add_parser(name)
    for name in ("cameras", "sizes", "encoders", "displays", "apps"):
        p = sub.add_parser(name, help=f"List device {name}")
        p.add_argument("--serial")
        if name in ("cameras", "sizes"):
            p.add_argument("--camera-id")
    for name in ("connect", "disconnect"):
        p = sub.add_parser(name, help=f"ADB wireless {name}; address must include port")
        p.add_argument("address", help="HOST:PORT")
    pair = sub.add_parser("pair", help="Pair Android wireless debugging; prompts privately for code")
    pair.add_argument("address", help="Pairing HOST:PORT from Android settings")
    native = sub.add_parser("native", help="Pass every option through: native -- <scrcpy options>")
    native.add_argument("args", nargs=argparse.REMAINDER)
    for name in ("preview", "record"):
        p = sub.add_parser(name, help=f"{name.title()} the camera directly (Android 12+)")
        p.add_argument("--serial")
        selector = p.add_mutually_exclusive_group()
        selector.add_argument("--camera-id")
        selector.add_argument("--facing", choices=("front", "back", "external"))
        p.add_argument("--size", help="WIDTHxHEIGHT; otherwise let scrcpy select a supported size")
        p.add_argument("--max-size", type=int)
        p.add_argument("--aspect-ratio", help="16:9, 4:3, sensor, etc.; not with --size")
        p.add_argument("--fps", type=int)
        p.add_argument("--high-speed", action="store_true")
        p.add_argument("--zoom", type=_positive)
        p.add_argument("--torch", action="store_true")
        p.add_argument("--video-codec", default="h264", choices=("h264", "h265", "av1", "vp8", "vp9"))
        p.add_argument("--video-bit-rate", default="16M")
        p.add_argument("--video-encoder")
        p.add_argument("--video-codec-options")
        p.add_argument("--no-audio", action="store_true", help="Disable microphone recording")
        p.add_argument("--audio-source", default="mic")
        p.add_argument("--audio-codec", default="aac", choices=("aac", "opus", "flac", "raw"))
        p.add_argument("--audio-bit-rate", default="128K")
        p.add_argument("--audio-encoder")
        p.add_argument("--audio-codec-options")
        p.add_argument("--require-audio", action="store_true")
        p.add_argument("--orientation", choices=("0", "90", "180", "270", "flip0", "flip90", "flip180", "flip270"))
        p.add_argument("--duration", type=int, help="Whole seconds; omit to run until Ctrl+C")
        p.add_argument("--fullscreen", action="store_true")
        p.add_argument("--always-on-top", action="store_true")
        if name == "record":
            p.add_argument("--output", type=Path, required=True, help="New .mp4 or .mkv path; never overwritten")
            p.add_argument("--preview", action="store_true", help="Show a live preview (default: headless)")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    ns = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    try:
        backend = ScrcpyBackend(ns.scrcpy, ns.adb)
        if ns.command in (None, "serve"):
            port = getattr(ns, "port", 8765)
            if not 1 <= port <= 65535:
                raise ScrcpyError("Port must be between 1 and 65535.")
            app = create_web_app(backend)
            print(f"scrcpy test page: http://127.0.0.1:{port}", flush=True)
            try:
                app.extensions["start_automatic_preview"]()
                app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False, threaded=True)
            finally:
                app.extensions["close_camera"]()
        elif ns.command == "native-help":
            print(backend.native_help())
        elif ns.command == "doctor":
            print(json.dumps(backend.doctor(), indent=2))
        elif ns.command == "devices":
            print(json.dumps(backend.devices(), indent=2))
        elif ns.command in ("cameras", "sizes", "encoders", "displays", "apps"):
            print(backend.inspect(ns.command, ns.serial, getattr(ns, "camera_id", None)))
        elif ns.command in ("connect", "disconnect", "pair"):
            if not re.fullmatch(r"(?:[A-Za-z0-9_.-]+|\[[0-9a-fA-F:]+\]):[0-9]+", ns.address):
                raise ScrcpyError("Use HOST:PORT from the phone's wireless debugging settings.")
            if ns.command == "pair":
                import getpass
                code = getpass.getpass("Wireless debugging pairing code: ")
                print(backend.adb("pair", ns.address, code, timeout=60))
            else:
                print(backend.adb(ns.command, ns.address))
        else:
            with backend:
                if ns.command == "native":
                    args = ns.args[1:] if ns.args[:1] == ["--"] else ns.args
                    backend.start_native(args)
                else:
                    values = {key: getattr(ns, key) for key in CameraOptions.__dataclass_fields__
                              if hasattr(ns, key)}
                    values["audio"] = not ns.no_audio
                    backend.start_camera(
                        CameraOptions(**values), output=getattr(ns, "output", None),
                        preview=ns.command == "preview" or ns.preview,
                    )
                try:
                    # Polling also observes headless output growth; logs stream to stderr.
                    while True:
                        try:
                            result = backend.wait(0.5)
                            break
                        except TimeoutError:
                            backend.status()
                except KeyboardInterrupt:
                    print("Stopping scrcpy and finalizing the recording...", file=sys.stderr)
                    result = backend.stop()
                print(json.dumps(result, indent=2))
                return 0 if result["state"] in ("COMPLETED", "STOPPED") else int(result["returncode"] or 1)
    except KeyboardInterrupt:
        return 130
    except (ScrcpyError, OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
