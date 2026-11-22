"""Replay-only MCP entry point for a published Oracle recording archive."""

import json
import os
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from fastapi import FastAPI
from mcp.server.fastmcp import FastMCP

from web_driver_station.backend.ftclog import FtcLogError
from web_driver_station.backend.recording_metadata import RECORDING_METADATA_FILE
from web_driver_station.backend.review_api import review_routes
from web_driver_station.backend.saved_runs import saved_runs

from .backend_client import DriverStationApi
from .replay_tools import register_replay_tools


class PublishedRecordings:
    recording_upload_configured = False

    def __init__(self, root: Path) -> None:
        self.root = root.resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("The recording archive must be a directory")

    @property
    def recordings_root(self) -> Path:
        return self.root

    def recording_directory(self, session_id: str) -> Path:
        try:
            directory = self.root / str(UUID(session_id))
        except ValueError as error:
            raise FtcLogError("Invalid recording ID") from error
        if directory.is_symlink() or not directory.is_dir():
            raise FtcLogError("Recording was not found")
        return directory

    def review_exclusions(self) -> set[str]:
        excluded = set()
        for directory in self.root.iterdir():
            if directory.is_symlink() or not directory.is_dir() or directory.name.startswith("."):
                continue
            manifest = directory / "manifest.json"
            if manifest.is_symlink() or (directory / RECORDING_METADATA_FILE).is_symlink():
                excluded.add(directory.name)
                continue
            if manifest.is_file():
                try:
                    metadata = json.loads(manifest.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    excluded.add(directory.name)
                    continue
                if not isinstance(metadata, dict) or metadata.get("kind") == "debugger":
                    excluded.add(directory.name)
        return excluded

    def saved_recording_sessions(self) -> list[dict]:
        return saved_runs(self.root, self.review_exclusions())


def create_archive_server(root: Path, cache_root: Path, viewer_url: str, *, port: int = 8003) -> FastMCP:
    source = PublishedRecordings(root)
    cache_root = cache_root.resolve()
    if cache_root.is_relative_to(source.root):
        raise ValueError("The analysis cache must be outside the recording archive")
    viewer = urlsplit(viewer_url)
    if (viewer.scheme != "https" or not viewer.hostname or viewer.username is not None
            or viewer.query or viewer.fragment):
        raise ValueError("Use an HTTPS replay viewer URL without credentials, a query, or a fragment")

    reviews = FastAPI(openapi_url=None)
    reviews.include_router(review_routes(source, replay_base=viewer_url.rstrip("/") + "/",
                                        cache_root=cache_root, writable=False))

    def archive_api(*, timeout_s: float | None = None) -> DriverStationApi:
        # The shared review routes run inside this process and have no HTTP listener.
        return DriverStationApi("http://archive.local", timeout_s=timeout_s,
                                transport=httpx.ASGITransport(app=reviews))

    server = FastMCP(
        "FTC Advanced Oracle Replays",
        host="127.0.0.1",
        port=port,
        instructions=(
            "Analyze finalized recordings uploaded to Oracle. Resolve the recording ID with list_replays, "
            "check coverage and capture quality, and cite timestamped replay links. Times are seconds since "
            "the first telemetry snapshot. Gate commands indicate intent. A confirmed shot requires piece-passage evidence."
        ),
    )
    register_replay_tools(server, archive_api)
    return server


def main() -> None:
    server = create_archive_server(
        Path(os.getenv("FTC_RECORDING_DIR", "/srv/ftc-recordings")),
        Path(os.getenv("FTC_REPLAY_CACHE_DIR", "/var/cache/ftc-replay-mcp")),
        os.environ["FTC_REPLAY_VIEWER_URL"],
    )
    server.run(transport="streamable-http")


if __name__ == "__main__":
    main()
