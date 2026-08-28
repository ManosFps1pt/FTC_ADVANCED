"""List and exercise the local MCP endpoint from a developer terminal."""

from __future__ import annotations

import asyncio

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def run() -> None:
    async with streamable_http_client("http://127.0.0.1:8001/mcp") as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print("Available tools:")
            for tool in tools.tools:
                print(f"- {tool.name}")

            print("\nDriver Station status result:")
            result = await session.call_tool("get_driver_station_status", {})
            for item in result.content:
                if getattr(item, "text", None):
                    print(item.text)


if __name__ == "__main__":
    asyncio.run(run())

