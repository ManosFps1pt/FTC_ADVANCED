"""Test the TeamCode Limelight bridge through a Robot Controller Wi-Fi connection.

The Limelight 3A must be connected to the Robot Controller, and the TeamCode app
containing LimelightBridge.java must be deployed first.

Examples:

    python limelight_control_hub_test.py
    python limelight_control_hub_test.py --rc-host 192.168.49.1 --watch
    python limelight_control_hub_test.py --rc-host 192.168.43.1 --endpoint results
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, build_opener


# A phone-based Robot Controller uses 192.168.49.1. A Control Hub uses
# 192.168.43.1, which can still be passed with --rc-host.
DEFAULT_RC_HOST = "192.168.49.1"
DEFAULT_RC_PORT = 8080
ENDPOINTS = ("health", "results")

# Do not send requests for the Control Hub's private Wi-Fi address through a
# laptop's configured HTTP proxy.
DIRECT_HTTP_OPENER = build_opener(ProxyHandler({}))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Test TeamCode's Limelight HTTP bridge.")
    parser.add_argument(
        "--rc-host",
        "--hub-host",
        dest="rc_host",
        default=DEFAULT_RC_HOST,
        help="Robot Controller IP/hostname (phone: 192.168.49.1; Control Hub: 192.168.43.1).",
    )
    parser.add_argument(
        "--rc-port",
        "--hub-port",
        dest="rc_port",
        type=int,
        default=DEFAULT_RC_PORT,
        help="Robot Controller web-server port.",
    )
    parser.add_argument("--endpoint", choices=ENDPOINTS, default="health", help="Bridge endpoint to request.")
    parser.add_argument("--watch", action="store_true", help="Request repeatedly until Ctrl+C is pressed.")
    parser.add_argument("--interval", type=float, default=1.0, help="Seconds between --watch requests.")
    return parser.parse_args()


def request_endpoint(rc_host: str, rc_port: int, endpoint: str) -> tuple[int, dict[str, Any]]:
    url = f"http://{rc_host}:{rc_port}/api/limelight/{endpoint}"
    try:
        with DIRECT_HTTP_OPENER.open(url, timeout=2.0) as response:
            return response.status, json.load(response)
    except HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = {"connected": False, "error": body or error.reason}
        return error.code, payload
    except (URLError, OSError, json.JSONDecodeError) as error:
        return 0, {"connected": False, "error": str(error)}


def main() -> int:
    args = parse_args()
    if not 1 <= args.rc_port <= 65535:
        raise SystemExit("--rc-port must be between 1 and 65535")
    if args.interval <= 0:
        raise SystemExit("--interval must be greater than zero")

    while True:
        status, payload = request_endpoint(args.rc_host, args.rc_port, args.endpoint)
        print(f"HTTP {status or 'OFFLINE'} {json.dumps(payload, indent=2, sort_keys=True)}")
        if not args.watch:
            return 0 if status == 200 else 1
        time.sleep(args.interval)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
