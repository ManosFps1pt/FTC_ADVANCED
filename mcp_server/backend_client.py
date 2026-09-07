"""Small HTTP client for the existing local Driver Station API."""

from __future__ import annotations

import os
from typing import Any

import httpx


class BackendError(RuntimeError):
    """Raised when the Driver Station backend cannot answer an API request."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class DriverStationApi:
    """Call the bounded Driver Station API exposed to the MCP server.

    The MCP process deliberately does not import ``main.py`` or access service
    globals. This keeps the two processes independently restartable and makes
    the MCP layer usable with a different backend implementation later.
    """

    def __init__(self, base_url: str | None = None, *, timeout_s: float | None = None) -> None:
        configured_url = base_url or os.getenv("FTC_ADVANCED_API_BASE", "http://127.0.0.1:8000")
        self.base_url = configured_url.rstrip("/")
        self.timeout_s = timeout_s or float(os.getenv("FTC_ADVANCED_API_TIMEOUT_S", "3"))

    async def get(self, path: str) -> dict[str, Any] | list[Any]:
        return await self._request("GET", path)

    async def post(self, path: str, body: dict[str, Any]) -> dict[str, Any] | list[Any]:
        return await self._request("POST", path, json=body)

    async def _request(
        self, method: str, path: str, *, json: dict[str, Any] | None = None
    ) -> dict[str, Any] | list[Any]:
        try:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout_s) as client:
                response = await client.request(method, path, json=json)
        except httpx.HTTPError as error:
            raise BackendError(
                f"FTC Advanced backend is unavailable at {self.base_url}: {error}"
            ) from error

        if response.is_error:
            detail: object = response.text
            try:
                body = response.json()
                if isinstance(body, dict) and body.get("detail"):
                    detail = body["detail"]
            except ValueError:
                pass
            raise BackendError(
                f"Backend {method} {path} failed with HTTP {response.status_code}: {detail}",
                status_code=response.status_code,
            )

        try:
            payload = response.json()
        except ValueError as error:
            raise BackendError(f"Backend {method} {path} returned invalid JSON") from error
        if not isinstance(payload, (dict, list)):
            raise BackendError(f"Backend {method} {path} returned an unexpected JSON value")
        return payload
