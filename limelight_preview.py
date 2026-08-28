"""Discover a directly reachable Limelight 3A and display its MJPEG preview.

Install the only third-party dependency once:

    python -m pip install opencv-python

Examples:

    python limelight_preview.py
    python limelight_preview.py --host 172.29.0.1
    python limelight_preview.py --host limelight.local

Connect the Limelight 3A to this computer by USB before running the tool. The
camera presents a USB-Ethernet interface and normally serves REST results on
port 5807 and its MJPEG stream on port 5800. When connected through a Control
Hub, this computer must have a route to the camera's USB-Ethernet subnet.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

cv2: Any | None = None

# A development computer may have HTTP(S)_PROXY configured. urllib honors it by
# default, but a Limelight is a directly attached private-network device and
# must never be sent through that proxy.
DIRECT_HTTP_OPENER = build_opener(ProxyHandler({}))


DEFAULT_HOSTS = ("limelight.local", "172.28.0.1", "172.29.0.1", "172.29.1.1")
REST_PORT = 5807
STREAM_PORT = 5800


def normalise_host(host: str) -> str:
    """Allow either a hostname/IP or a URL-like --host argument."""
    return host.removeprefix("http://").removeprefix("https://").split("/")[0]


def results_url(host: str) -> str:
    return f"http://{host}:{REST_PORT}/results"


def stream_url(host: str) -> str:
    return f"http://{host}:{STREAM_PORT}"


def load_opencv() -> None:
    """Import OpenCV only after argparse has had a chance to handle --help."""
    global cv2
    try:
        import cv2 as imported_cv2
    except ImportError as error:
        raise RuntimeError(
            "OpenCV is required. Install it with: python -m pip install opencv-python"
        ) from error
    cv2 = imported_cv2


def result_object(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Return the result object across current and older Limelight REST shapes."""
    nested_results = payload.get("Results")
    if isinstance(nested_results, dict):
        return nested_results

    # Some LimelightOS releases return the result fields directly from
    # /results and use "v" rather than "tv" for the validity flag.
    if any(key in payload for key in ("v", "tv", "pID", "tx", "ty")):
        return payload
    return None


def probe_limelight(host: str, timeout_seconds: float) -> dict[str, Any] | None:
    """Return a Limelight REST payload, or None when this host is not reachable."""
    try:
        with DIRECT_HTTP_OPENER.open(
            results_url(host), timeout=timeout_seconds
        ) as response:
            payload = json.load(response)
    except (OSError, URLError, ValueError, json.JSONDecodeError):
        return None

    # A successful HTTP response alone is not enough: another device could use
    # this port. Require a recognizable Limelight result payload.
    if not isinstance(payload, dict) or result_object(payload) is None:
        return None
    return payload


def open_direct_stream(url: str) -> Any:
    """Open an MJPEG stream without forwarding a private camera IP to a proxy."""
    if cv2 is None:
        raise RuntimeError("OpenCV was not loaded")

    proxy_variable_names = (
        "ALL_PROXY",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "all_proxy",
        "http_proxy",
        "https_proxy",
    )
    previous_values = {
        name: os.environ.pop(name, None) for name in proxy_variable_names
    }
    try:
        return cv2.VideoCapture(url)
    finally:
        for name, value in previous_values.items():
            if value is not None:
                os.environ[name] = value


def discover_limelight(hosts: list[str], timeout_seconds: float) -> tuple[str, dict[str, Any]]:
    """Probe candidate hosts in order and return the first reachable Limelight."""
    for host in hosts:
        print(f"Checking {results_url(host)} ...", flush=True)
        payload = probe_limelight(host, timeout_seconds)
        if payload is not None:
            return host, payload

    checked = ", ".join(hosts)
    raise RuntimeError(
        "No Limelight REST endpoint responded. Checked: "
        f"{checked}. Connect the 3A by USB, wait for the Ethernet/DHCP connection, "
        "or pass its address with --host."
    )


def display_preview(host: str, output_directory: Path) -> None:
    """Show the latest preview frame until the user presses Q or Escape."""
    if cv2 is None:
        raise RuntimeError("OpenCV was not loaded")

    url = stream_url(host)
    print(f"Opening stream: {url}")
    capture = open_direct_stream(url)
    capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    if not capture.isOpened():
        capture.release()
        raise RuntimeError(
            f"Found a Limelight at {host}, but OpenCV could not open {url}. "
            "Check that the stream is enabled and that this computer can reach port 5800."
        )

    title = f"Limelight preview — {host}"
    cv2.namedWindow(title, cv2.WINDOW_NORMAL)
    last_status_check = 0.0
    stream_is_healthy = True

    print("Preview is open. Press Q or Escape to quit; press S to save a JPEG snapshot.")
    try:
        while True:
            ok, frame = capture.read()
            if not ok or frame is None:
                if stream_is_healthy:
                    print("Lost the camera stream; waiting for it to recover...", file=sys.stderr)
                    stream_is_healthy = False
                time.sleep(0.05)
                continue

            stream_is_healthy = True
            cv2.putText(
                frame,
                "Q/Esc: quit   S: save snapshot",
                (12, 28),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.65,
                (255, 255, 255),
                2,
                cv2.LINE_AA,
            )
            cv2.putText(
                frame,
                "Q/Esc: quit   S: save snapshot",
                (12, 28),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.65,
                (0, 0, 0),
                1,
                cv2.LINE_AA,
            )
            cv2.imshow(title, frame)

            key = cv2.waitKey(1) & 0xFF
            if key in (27, ord("q"), ord("Q")):
                return
            if key in (ord("s"), ord("S")):
                output_directory.mkdir(parents=True, exist_ok=True)
                filename = datetime.now().strftime("limelight_%Y%m%d_%H%M%S_%f.jpg")
                destination = output_directory / filename
                if cv2.imwrite(str(destination), frame):
                    print(f"Saved snapshot: {destination.resolve()}")
                else:
                    print(f"Could not save snapshot: {destination}", file=sys.stderr)

            # Keep detecting a physical disconnect even if OpenCV returns a
            # buffered frame for a short time after it occurs.
            now = time.monotonic()
            if now - last_status_check >= 2.0:
                last_status_check = now
                if probe_limelight(host, timeout_seconds=0.2) is None:
                    print("Warning: Limelight REST endpoint is not responding.", file=sys.stderr)
    finally:
        capture.release()
        cv2.destroyAllWindows()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Discover a reachable Limelight 3A and open its preview stream."
    )
    parser.add_argument(
        "--host",
        action="append",
        metavar="HOST",
        help=(
            "Limelight hostname or IP to probe. May be supplied more than once. "
            "When omitted, probes limelight.local, 172.28.0.1, 172.29.0.1, "
            "and 172.29.1.1."
        ),
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=0.6,
        help="REST detection timeout per candidate in seconds (default: 0.6).",
    )
    parser.add_argument(
        "--snapshot-dir",
        type=Path,
        default=Path("limelight_captures"),
        help="Directory used when S saves a snapshot (default: limelight_captures).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.timeout <= 0:
        raise SystemExit("--timeout must be greater than zero")

    requested_hosts = args.host or list(DEFAULT_HOSTS)
    hosts = list(dict.fromkeys(normalise_host(host) for host in requested_hosts))

    try:
        load_opencv()
        host, payload = discover_limelight(hosts, args.timeout)
        results = result_object(payload)
        assert results is not None  # guaranteed by discover_limelight()
        print(
            f"Detected Limelight at {host} "
            f"(valid target: {bool(results.get('tv', results.get('v', False)))}, "
            f"pipeline: {results.get('pID', 'unknown')})."
        )
        display_preview(host, args.snapshot_dir)
    except RuntimeError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
