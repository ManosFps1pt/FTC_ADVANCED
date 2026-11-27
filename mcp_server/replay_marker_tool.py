"""Write timestamped annotations from the authenticated Oracle analysis agent."""

import asyncio
import json
from collections.abc import Callable
from typing import Annotated
from urllib.parse import urlencode
from uuid import UUID

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.routes import build_resource_metadata_url
from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import Field

from recording_viewer.backend.replay_markers import ReplayMarkerInput, ReplayMarkerStore

from .backend_client import DriverStationApi


MARKER_SCOPE = "replays:mark"


def register_replay_marker_tool(
    server: FastMCP, archive_api: Callable[..., DriverStationApi], store: ReplayMarkerStore,
    viewer_url: str, read_scopes: list[str],
) -> None:
    required_scopes = [*read_scopes, MARKER_SCOPE]
    auth = server.settings.auth
    resource_metadata = build_resource_metadata_url(auth.resource_server_url) if auth and auth.resource_server_url else None

    @server.tool(
        annotations=ToolAnnotations(
            readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False,
        ),
        meta={"securitySchemes": [{"type": "oauth2", "scopes": required_scopes}]},
    )
    async def mark_replay_events(
        run: Annotated[str, Field(description="Finalized recording UUID from list_replays")],
        events: Annotated[list[ReplayMarkerInput], Field(min_length=1, max_length=25)],
    ) -> CallToolResult:
        """Save important observed events to the Oracle replay timeline when asked.

        Use seconds since the first telemetry snapshot. Set t_s for a point marker.
        Add end_s to mark a range from t_s through end_s. Choose the event kind for its
        timeline color and include a concise title and supporting evidence when available.
        A feed gate command alone does not establish that a shot occurred.
        Identical submissions are safe to retry.
        """
        token = get_access_token()
        if token is None or not set(required_scopes).issubset(token.scopes):
            message = "Reconnect the Oracle replay app with permission to save replay markers."
            parameters = [
                f'error="{"invalid_token" if token is None else "insufficient_scope"}"',
                f'error_description="{message}"',
                f'scope="{" ".join(required_scopes)}"',
            ]
            if resource_metadata is not None:
                parameters.append(f'resource_metadata="{resource_metadata}"')
            return CallToolResult(
                isError=True,
                content=[TextContent(type="text", text=message)],
                _meta={"mcp/www_authenticate": ["Bearer " + ", ".join(parameters)]},
            )
        session_id = str(UUID(run))
        quality = await archive_api(timeout_s=30).get(f"/api/reviews/{session_id}/report?section=quality")
        result = quality["result"]
        if not result["samples"]:
            raise ValueError("The recording has no telemetry timeline.")
        markers, added = await asyncio.to_thread(store.add, session_id, events, result["recorded_span_s"])
        payload = {
            "session_id": session_id,
            "added_count": added,
            "markers": [
                {**marker.model_dump(), "replay_path": f"{viewer_url.rstrip('/')}/?{urlencode({'recording': session_id, 't': marker.t_s})}"}
                for marker in markers
            ],
        }
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
            structuredContent=payload,
        )
