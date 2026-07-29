# ezstox

Terminal-based portfolio tracker with AI investment advisor

## Features

- Track portfolio holdings with P&L calculations
- Monitor watchlist stocks
- Live stock prices via OpenBB
- Recent news articles for your stocks
- AI investment advisor powered by OpenAI GPT-4o-mini
- Automatic asset type detection (stocks, ETFs, commodities)
- Interactive menu system

## Setup

### 1. Install Dependencies

```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Set Up API Keys

Add these to your shell config (`~/.zshrc` or `~/.bashrc`):

```bash
export OPENAI_API_KEY='your-openai-key'
export FRED_API_KEY='your-fred-key'  # Optional - for VIX data
```

Then reload: `source ~/.zshrc`

### 3. Create Data Files

Create a `data/` directory with your portfolio information:

**data/portfolio.txt**
```
# Format: SYMBOL,SHARES,COST_BASIS
# Example:
AAPL,10,150.00
NVDA,5,200.00
```

**data/watchlist.txt**
```
# Format: One symbol per line
# Example:
TSLA
AMD
META
```

**data/cash.txt**
```
1000
```

### 4. Run

```bash
python main.py
```

This launches the interactive menu where you can:
1. View Portfolio & Watchlist
2. View News for Stocks
3. Get AI Investment Advice (Full Analysis)
4. Edit Portfolio
5. Edit Watchlist
6. Edit Cash Balance
7. Settings & Info

## Usage Notes

- **AI Analysis**: Costs ~$0.003-0.005 per run (~$0.21/month for 2x daily use)
- **Performance**: Portfolio view ~5 seconds, AI analysis ~60-90 seconds
- **Asset Types**: Automatically detects stocks, ETFs, and commodities
- **ETF Handling**: Analyzes ETFs based on underlying index/commodity trends instead of company metrics

## File Structure

```
ezstox/
├── main.py              # Entry point
├── menu.py              # Interactive menu
├── mcp_server.py         # MCP server exposing portfolio/data tools
├── test_mcp_client.py    # Test client for the MCP server
├── src/
│   ├── data_fetcher.py      # OpenBB API calls
│   ├── llm_advisor.py       # AI analysis
│   ├── portfolio_manager.py # Portfolio data handling
│   └── reporter.py          # Output formatting
└── data/                # Your portfolio data (not in git)
    ├── portfolio.txt
    ├── watchlist.txt
    └── cash.txt
```

## MCP Server

`mcp_server.py` exposes ezstox's existing portfolio and data-fetching
logic as [Model Context Protocol](https://modelcontextprotocol.io) tools,
using Anthropic's official `mcp` Python SDK. It wraps the existing
`Portfolio` class and `data_fetcher` functions directly — no business
logic was rewritten.

**Tools exposed:**

| Tool | Arguments | Returns |
|---|---|---|
| `get_portfolio` | none | holdings (shares, cost basis, live price, gain/loss), cash, total value |
| `get_stock_price` | `symbol` | current price for a ticker |
| `get_recent_news` | `symbol`, `limit` (default 3) | recent headlines with date/URL |
| `get_watchlist` | none | watchlist ticker symbols |

### Setup

```bash
pip install -r requirements.txt   # installs mcp[cli]==1.29.0 among others
```

The SDK version is pinned deliberately: `mcp` 2.0.0 is a ground-up rewrite
with a different API (no `mcp.server.fastmcp.FastMCP`), so an unpinned
install would break this server.

### Run it

```bash
python mcp_server.py
```

This starts the server on the stdio transport and blocks, waiting for an
MCP client to connect over stdin/stdout — that's expected, it's not meant
to be run standalone in a terminal for long. Point a client at it instead:

**Option A — test client (included):**

```bash
python test_mcp_client.py
```

Spawns `mcp_server.py`, lists the advertised tools, and calls all four
with real arguments, printing each result so you can confirm they return
real data.

**Option B — MCP Inspector (official tool):**

```bash
npx @modelcontextprotocol/inspector python mcp_server.py
```

Opens a browser UI to list tools, inspect their schemas, and call them
interactively. Requires Node.js.

**Option C — Claude Desktop:** add to its MCP server config
(`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "ezstox": {
      "command": "python",
      "args": ["/absolute/path/to/ezstox/mcp_server.py"]
    }
  }
}
```
