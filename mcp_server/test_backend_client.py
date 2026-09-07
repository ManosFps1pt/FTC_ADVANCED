from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from .backend_client import BackendError, DriverStationApi


class DriverStationApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_returns_json_object(self) -> None:
        response = type("Response", (), {"is_error": False, "json": lambda self: {"connected": True}})()
        client = AsyncMock()
        client.request.return_value = response

        with patch("mcp_server.backend_client.httpx.AsyncClient") as client_type:
            client_type.return_value.__aenter__.return_value = client
            result = await DriverStationApi("http://backend").get("/api/status")

        self.assertEqual({"connected": True}, result)
        client.request.assert_awaited_once_with("GET", "/api/status", json=None)

    async def test_post_sends_a_json_body(self) -> None:
        response = type("Response", (), {"is_error": False, "json": lambda self: {"runId": "run"}})()
        client = AsyncMock()
        client.request.return_value = response

        with patch("mcp_server.backend_client.httpx.AsyncClient") as client_type:
            client_type.return_value.__aenter__.return_value = client
            result = await DriverStationApi("http://backend").post("/api/debug/runs", {"repetitions": 3})

        self.assertEqual({"runId": "run"}, result)
        client.request.assert_awaited_once_with("POST", "/api/debug/runs", json={"repetitions": 3})

    async def test_http_error_includes_backend_detail(self) -> None:
        response = type(
            "Response",
            (),
            {
                "is_error": True,
                "status_code": 503,
                "text": "unavailable",
                "json": lambda self: {"detail": "robot backend unavailable"},
            },
        )()
        client = AsyncMock()
        client.request.return_value = response

        with patch("mcp_server.backend_client.httpx.AsyncClient") as client_type:
            client_type.return_value.__aenter__.return_value = client
            with self.assertRaisesRegex(BackendError, "robot backend unavailable"):
                await DriverStationApi("http://backend").get("/api/status")

    async def test_http_error_preserves_status_code(self) -> None:
        response = type(
            "Response", (), {"is_error": True, "status_code": 405, "text": "method", "json": lambda self: {}}
        )()
        client = AsyncMock()
        client.request.return_value = response

        with patch("mcp_server.backend_client.httpx.AsyncClient") as client_type:
            client_type.return_value.__aenter__.return_value = client
            with self.assertRaises(BackendError) as caught:
                await DriverStationApi("http://backend").post("/api/opmodes/launch", {})

        self.assertEqual(405, caught.exception.status_code)


if __name__ == "__main__":
    unittest.main()
