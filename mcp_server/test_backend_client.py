from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from .backend_client import BackendError, DriverStationApi


class DriverStationApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_returns_json_object(self) -> None:
        response = type("Response", (), {"is_error": False, "json": lambda self: {"connected": True}})()
        client = AsyncMock()
        client.get.return_value = response

        with patch("mcp_server.backend_client.httpx.AsyncClient") as client_type:
            client_type.return_value.__aenter__.return_value = client
            result = await DriverStationApi("http://backend").get("/api/status")

        self.assertEqual({"connected": True}, result)
        client.get.assert_awaited_once_with("/api/status")

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
        client.get.return_value = response

        with patch("mcp_server.backend_client.httpx.AsyncClient") as client_type:
            client_type.return_value.__aenter__.return_value = client
            with self.assertRaisesRegex(BackendError, "robot backend unavailable"):
                await DriverStationApi("http://backend").get("/api/status")


if __name__ == "__main__":
    unittest.main()

