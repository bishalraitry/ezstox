"""
Minimal MCP test client for the ezstox MCP server.

This plays the role of an MCP *client*: it launches mcp_server.py as a
subprocess, speaks MCP over stdio to it (the same transport a real
host like Claude Desktop would use), lists the tools the server
advertises, and calls each one with real arguments. Run it to confirm
all four tools genuinely return real data end-to-end:

    python test_mcp_client.py

For interactive poking instead of a scripted run, use the official
MCP Inspector:

    npx @modelcontextprotocol/inspector python mcp_server.py
"""

import asyncio
import json

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER_PARAMS = StdioServerParameters(command="python3", args=["mcp_server.py"])


def _print_result(result):
    if result.isError:
        print("  ERROR:", result.content[0].text if result.content else "unknown error")
        return

    if result.structuredContent is not None:
        print(" ", json.dumps(result.structuredContent, indent=2))
    else:
        for block in result.content:
            if block.type == "text":
                print(" ", block.text)


async def main():
    async with stdio_client(SERVER_PARAMS) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            print("Tools advertised by the server:")
            for tool in tools.tools:
                print(f"  - {tool.name}: {tool.description.strip().splitlines()[0]}")
            print()

            print("=== get_portfolio() ===")
            _print_result(await session.call_tool("get_portfolio", {}))
            print()

            print("=== get_watchlist() ===")
            _print_result(await session.call_tool("get_watchlist", {}))
            print()

            print("=== get_stock_price(symbol='AAPL') ===")
            _print_result(await session.call_tool("get_stock_price", {"symbol": "AAPL"}))
            print()

            print("=== get_recent_news(symbol='AAPL', limit=2) ===")
            _print_result(
                await session.call_tool("get_recent_news", {"symbol": "AAPL", "limit": 2})
            )


if __name__ == "__main__":
    asyncio.run(main())
