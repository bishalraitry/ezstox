# ezstox

A portfolio tracker and AI investment advisor that lives in your terminal.

- **Dashboard**: holdings, live P&L, today's move, allocation weights and 1-month trend sparklines
- **News**: latest headlines for all your stocks, or any single ticker, as clickable links
- **Lookup**: quote, key stats, analyst target, 52-week range and news for any ticker
- **AI analysis**: an OpenAI-powered review of your whole portfolio, grounded in fundamentals, full news articles and market context, with a source list of real URLs
- **Manage portfolio**: add, update and remove holdings, watchlist symbols and cash from inside the app
- **Reports**: every AI analysis is saved as Markdown and can be reopened later
- **MCP server**: exposes your portfolio and market data as tools to any MCP client (e.g. Claude Desktop)

## Quick start

You only need Python 3.9 or newer.

```bash
git clone https://github.com/bishalraitry/ezstox.git
cd ezstox
./ezstox
```

That's it. On the first run, `./ezstox` creates a private environment in `.venv/`, installs everything (about a minute), and starts the app. After that it starts instantly. There's nothing to activate and nothing to add to your shell config. It reinstalls dependencies on its own whenever `requirements.txt` changes.

On Windows, run `python ezstox` instead of `./ezstox`.

**Run it from anywhere:** symlink the launcher onto your PATH:

```bash
ln -s "$(pwd)/ezstox" /usr/local/bin/ezstox
ezstox
```

## Using it

`./ezstox` opens the interactive menu. You can also jump straight to a screen:

```bash
./ezstox dashboard        # holdings, P&L and watchlist
./ezstox news             # headlines for all your stocks
./ezstox news NVDA        # headlines for one ticker
./ezstox lookup MSFT      # quote, key stats and news for any ticker
./ezstox ai               # full AI analysis
./ezstox reports          # reopen a saved analysis
```

### Your portfolio

Add holdings, watchlist symbols and cash from **Manage portfolio** in the menu. Your data is stored as plain text in `data/` (git-ignored, so it stays private), and you can edit those files by hand too:

| File | Format |
|---|---|
| `data/portfolio.txt` | `SYMBOL,SHARES,AVG_COST`, one holding per line, e.g. `AAPL,10,150` |
| `data/watchlist.txt` | one symbol per line |
| `data/cash.txt` | a single number |

London-listed tickers end in `.L` (e.g. `VUSA.L`).

### AI analysis

AI analysis needs an OpenAI API key ([get one here](https://platform.openai.com/api-keys)). The app asks for it the first time you run an analysis, or you can set it in **Settings**. It's stored in `.env` in the project folder (git-ignored). A run with the default `gpt-4o-mini` model costs about $0.003–0.005, and you can change the model in Settings.

Optional: a [Jina Reader](https://jina.ai/reader) key raises the article-scraping rate limit. The free tier works without one.

## How it works

```
ezstox                 launcher: sets up .venv on first run, then starts main.py
main.py                entry point: interactive menu + command-line shortcuts
mcp_server.py          MCP server exposing portfolio and market data as tools
test_mcp_client.py     test client that calls every MCP tool
src/
├── app.py             screens and menu
├── ui.py              Rich components: theme, tables, tiles, sparklines, progress
├── data_fetcher.py    prices, news and fundamentals (yfinance), cached and parallel
├── portfolio_manager.py   holdings, watchlist and cash; reads/writes data/
├── llm_advisor.py     AI pipeline: gather → read articles → analyse → sources
└── config.py          paths, .env settings
```

- **Fast startup:** heavy libraries load only when a screen needs them, so the menu appears instantly.
- **Parallel fetching:** prices, news and fundamentals for all your symbols are fetched concurrently and cached for 2 minutes, so moving between screens doesn't refetch anything.
- **AI pipeline:** stock data and market context (S&P 500, Nasdaq, Dow, VIX, tech sector, world news) are gathered together. The key articles are then read in parallel (Jina Reader, falling back to local extraction with trafilatura) before a single structured prompt goes to OpenAI. The source list with real URLs is built by the app, not the model, so links can't be hallucinated.

## MCP server

`mcp_server.py` exposes ezstox as [Model Context Protocol](https://modelcontextprotocol.io) tools, using Anthropic's official `mcp` Python SDK (requires Python 3.10+):

| Tool | Arguments | Returns |
|---|---|---|
| `get_portfolio` | none | holdings (shares, cost basis, live price, gain/loss), cash, total value |
| `get_stock_price` | `symbol` | current price for a ticker |
| `get_recent_news` | `symbol`, `limit` (default 3) | recent headlines with date and URL |
| `get_watchlist` | none | watchlist ticker symbols |

Test it (after running `./ezstox` once to set up `.venv`):

```bash
.venv/bin/python test_mcp_client.py
# or interactively, with the official MCP Inspector (needs Node.js):
npx @modelcontextprotocol/inspector .venv/bin/python mcp_server.py
```

To use it from Claude Desktop, add this to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "ezstox": {
      "command": "/absolute/path/to/ezstox/.venv/bin/python",
      "args": ["/absolute/path/to/ezstox/mcp_server.py"]
    }
  }
}
```
