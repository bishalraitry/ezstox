"""
ezstox MCP server

Exposes the existing ezstox portfolio/data logic as Model Context
Protocol (MCP) tools, so any MCP-aware client (Claude Desktop, the MCP
Inspector, another agent) can call into it over a standard protocol
instead of a bespoke integration.

What a "tool" is, in MCP terms
-------------------------------
An MCP tool is a named, schema-described function a server advertises
to a client: a name, a human-readable description, and a JSON Schema
for its input arguments. The client (an AI model, or the app driving
one) reads that schema, decides when a tool is relevant, and invokes
it with structured arguments. The server runs the real code and
returns a structured result. Nothing here is ezstox-specific about
that contract - it's the same shape whether the tool queries a stock
portfolio, a ticketing system, or an internal database. That's the
point: MCP standardizes the "how do I call this" plumbing so the AI
model doesn't need a custom integration per data source.

Server/client relationship
---------------------------
This file *is* an MCP server: it owns the real data (the Portfolio
class, the OpenBB-backed fetchers) and only ever reacts to requests.
It does not call out to an LLM itself. An MCP *client* (e.g. Claude
Desktop, or test_mcp_client.py in this repo) starts this process,
speaks JSON-RPC 2.0 over stdio to it, asks "what tools do you have"
(tools/list), and then "call this tool with these arguments"
(tools/call). The transport here is stdio: the client launches
`python mcp_server.py` as a subprocess and talks to it over its
stdin/stdout. That's why every tool below is careful not to let the
existing ezstox modules print to stdout - stdout is reserved
exclusively for MCP's JSON-RPC messages, and a stray print() would
corrupt the protocol stream.

Why this matters for institutional data
-----------------------------------------
Institutions (brokerages, research desks, internal data platforms)
have data trapped behind bespoke APIs, file formats, and internal
libraries. MCP gives them one consistent way to expose that data to
AI systems as callable tools with typed inputs/outputs, without
rewriting the underlying logic and without granting the model direct
access to internal systems. The AI model only ever sees the tool
contract; the server stays in control of what's fetched, how it's
authenticated, and what's returned.
"""

import contextlib
import sys

from mcp.server.fastmcp import FastMCP

from src.data_fetcher import get_stock_news as fetch_stock_news
from src.data_fetcher import get_stock_price as fetch_stock_price
from src.portfolio_manager import Portfolio

mcp = FastMCP("ezstox")


@contextlib.contextmanager
def _stdout_to_stderr():
    """
    Reroute stdout to stderr for the duration of a call into the
    existing ezstox modules.

    Portfolio and data_fetcher were written for a terminal app and
    call print() for status lines and warnings. Under the stdio MCP
    transport, stdout carries only JSON-RPC messages, so any of those
    incidental prints would corrupt the stream. We don't touch the
    original modules to fix this - we just isolate the integration
    point here.
    """
    with contextlib.redirect_stdout(sys.stderr):
        yield


@mcp.tool()
def get_portfolio() -> dict:
    """
    Get the current portfolio: each holding's shares, cost basis,
    live price, and gain/loss, plus the cash balance and total
    portfolio value. Reads from ezstox's data/portfolio.txt and
    data/cash.txt via the existing Portfolio class, and fetches live
    prices for held symbols via OpenBB.
    """
    with _stdout_to_stderr():
        portfolio = Portfolio(silent=True)
        symbols = portfolio.get_portfolio_symbols()

        current_prices = {}
        positions = []
        for symbol in symbols:
            price = fetch_stock_price(symbol)
            if price is not None:
                current_prices[symbol] = price
                positions.append(portfolio.calculate_position(symbol, price))

        result = {"holdings": positions, "cash": portfolio.cash}
        if current_prices:
            result["summary"] = portfolio.get_total_value(current_prices)
        return result


@mcp.tool()
def get_stock_price(symbol: str) -> dict:
    """
    Get the current price for a stock ticker symbol, e.g. "AAPL" or
    "NVDA". Reuses ezstox's existing OpenBB-backed price fetcher.
    """
    symbol = symbol.strip().upper()
    with _stdout_to_stderr():
        price = fetch_stock_price(symbol)

    if price is None:
        return {"symbol": symbol, "price": None, "error": f"Could not fetch a price for {symbol}"}
    return {"symbol": symbol, "price": price}


@mcp.tool()
def get_recent_news(symbol: str, limit: int = 3) -> list[dict]:
    """
    Get recent news headlines for a stock ticker symbol, e.g. "AAPL".
    Returns up to `limit` articles, each with a title, date, and URL
    where available. Reuses ezstox's existing news fetcher.
    """
    symbol = symbol.strip().upper()
    with _stdout_to_stderr():
        return fetch_stock_news(symbol, limit=limit)


@mcp.tool()
def get_watchlist() -> list[str]:
    """
    Get the current watchlist: the ticker symbols being tracked but
    not held, from ezstox's data/watchlist.txt.
    """
    with _stdout_to_stderr():
        portfolio = Portfolio(silent=True)
        return portfolio.get_watchlist_symbols()


if __name__ == "__main__":
    mcp.run()
