# ezstox

[![tests](https://github.com/bishalraitry/ezstox/actions/workflows/tests.yml/badge.svg)](https://github.com/bishalraitry/ezstox/actions/workflows/tests.yml)

A portfolio tracker, analytics engine and AI investment advisor that lives in your terminal.

ezstox computes the numbers a good analyst would look at first: risk, diversification, performance against the market, trend, valuation and upcoming events. It shows them to you, flags what matters, and hands the same data to an AI model for a review grounded in your actual portfolio.

## Quick start

You only need Python 3.9 or newer.

```bash
git clone https://github.com/bishalraitry/ezstox.git
cd ezstox
./ezstox
```

On the first run, `./ezstox` creates a private environment in `.venv/`, installs everything (about a minute), and walks you through setup: your base currency, your holdings, and optionally an AI key. After that it starts instantly. There's nothing to activate, and nothing to add to your shell config.

On Windows, run `python ezstox` instead. To run it from anywhere, use `ln -s "$(pwd)/ezstox" /usr/local/bin/ezstox`.

## What it does

| Screen | What you get |
|---|---|
| **Dashboard** | Value, today's move, unrealised P&L, performance vs the S&P 500, every holding with weight and a 1-month sparkline, watchlist with RSI and distance from high, and the top findings |
| **Insights** | Ranked findings, returns vs the S&P 500 (1M → 1Y), volatility, beta, max drawdown, Sharpe ratio, effective diversification, allocation by holding and sector, a correlation heatmap, trend and momentum signals, and an earnings/dividend calendar |
| **Lookup** | Any ticker *or company name* ("apple" finds AAPL): quote, valuation (P/E, PEG), quality (growth, margins, debt), analyst target and consensus, next earnings, trend, 52-week range and news |
| **News** | Latest headlines for all your stocks or one ticker, as clickable links |
| **AI analysis** | A streamed review with a verdict table first (action and conviction per stock), then portfolio health, per-holding theses, watchlist entries and this week's actions. You can ask follow-up questions afterwards. |
| **Live watch** | A full-screen dashboard that refreshes itself |
| **Manage portfolio** | Add, update or remove holdings, watchlist names and cash. Tickers are validated against live prices, and name search works here too. |
| **Reports** | Every AI analysis, including follow-ups, saved as Markdown |

Menus respond to a single keypress. Every screen also has a command-line shortcut:

```bash
./ezstox dashboard
./ezstox insights
./ezstox watch --interval 30
./ezstox news NVDA
./ezstox lookup "vanguard s&p 500"
./ezstox ai
./ezstox reports
```

## The analytics

Everything below is computed locally from about a year of daily prices plus fundamentals. No AI is involved, so it's fast, free and deterministic (`src/analytics.py`).

- **Risk:** annualised volatility, beta vs the S&P 500, max and current drawdown, and a Sharpe ratio using the live 13-week T-bill yield as the risk-free rate.
- **Diversification:**
  - effective number of positions (1 / Σw²)
  - pairwise correlations of daily returns
  - sector allocation
- **Performance:** your current mix vs the S&P 500 over 1M / 3M / 6M / YTD / 1Y.
- **Signals per stock:**
  - 50- and 200-day moving averages
  - RSI(14)
  - distance from the 1-year high
  - volatility
  - analyst target vs price
- **Calendar:** earnings and ex-dividend dates in the next 30 days.
- **Findings:** plain-English flags ranked risk → watch → info → good. Examples:
  - *"NVDA is 32% of your portfolio"*
  - *"AAPL and MSFT move together (correlation 0.84)"*
  - *"Lagging the S&P 500 by 6 pts over 1Y"*
  - *"TSLA is trading below its 200-day average"*

Portfolio-level figures apply your current holdings to the last 12 months of prices. They describe how this mix has behaved, not your account's actual return (ezstox doesn't record trades).

### Currencies

Every holding is priced in its own currency and converted to your base currency (Settings → Base currency) at the live FX rate. London listings quoted in pence (GBp) are normalised to pounds. Enter average costs in the stock's own currency, and cash in your base currency.

## AI analysis

The AI receives a structured **data pack**, not just headlines. It contains:
- your positions and totals
- the risk and performance analytics
- per-stock valuation, quality and trend data
- upcoming events
- the app's findings
- market context (indices, VIX, T-bill yield)
- full text of the most relevant news articles, which are read in parallel

The model is told the numbers are authoritative. It has to respond to every flagged risk and cite news by tag. The source list with real URLs is added by the app, so links can't be hallucinated.

It works with any OpenAI-compatible API. **OpenAI** and **DeepSeek** are built in; pick one in Settings and paste a key (stored in a git-ignored `.env`). The app asks for a key the first time you run an analysis. A run with `gpt-4o-mini` or `deepseek-chat` costs well under a cent; token usage is shown after each run.

Optional: a [Jina Reader](https://jina.ai/reader) key raises the article-reading rate limit. The free tier works without one.

## Your data

| File | Format |
|---|---|
| `data/portfolio.txt` | `SYMBOL,SHARES,AVG_COST`, one holding per line |
| `data/watchlist.txt` | one symbol per line |
| `data/cash.txt` | a single number |

All of these are git-ignored, along with `.env`, `reports/` and the `.cache/` of fundamentals. You can edit the text files by hand or from the app.

## How it's built

```
ezstox                 launcher: sets up .venv on first run, then starts main.py
main.py                entry point: interactive menu + command-line shortcuts
mcp_server.py          MCP server exposing portfolio data and insights as tools
src/
├── app.py             screens and menus
├── ui.py              Rich components: theme, tables, tiles, charts, live views, keypress input
├── analytics.py       valuation, risk, diversification, signals, events, findings
├── data_fetcher.py    prices, FX, news, fundamentals, search (yfinance); parallel + cached
├── llm_advisor.py     AI pipeline: gather → read articles → data pack → stream → follow-ups
├── portfolio_manager.py   holdings, watchlist and cash; reads/writes data/
└── config.py          paths, settings, AI provider presets
tests/                 48 tests against a deterministic fake market (no network)
```

- **Fast:**
  - The menu opens in about 0.2s, because heavy libraries load only when needed.
  - Prices, news and fundamentals are fetched in parallel. They're cached for 2 minutes in memory, and fundamentals for 12 hours on disk.
  - The AI pipeline gathers everything concurrently and reads all articles in one parallel pass.
- **Tested:** `pip install -r requirements-dev.txt && python -m pytest`. The suite covers the analytics maths, currency handling, both Yahoo news formats, the AI data pack, and full screen flows driven by scripted keystrokes. CI runs it on Python 3.9, 3.11 and 3.13.

## MCP server

`mcp_server.py` exposes ezstox as [Model Context Protocol](https://modelcontextprotocol.io) tools for any MCP client, such as Claude Desktop. It uses Anthropic's official `mcp` Python SDK and requires Python 3.10+.

| Tool | Returns |
|---|---|
| `get_portfolio` | holdings with live price and gain/loss, cash, totals |
| `get_portfolio_insights` | the full analytics: returns vs S&P 500, risk metrics, correlations, sectors, events, ranked findings |
| `get_stock_price` | current price for a ticker |
| `get_recent_news` | recent headlines with date and URL |
| `get_watchlist` | watchlist symbols |

```bash
.venv/bin/python test_mcp_client.py                                    # call every tool
npx @modelcontextprotocol/inspector .venv/bin/python mcp_server.py     # interactive (needs Node.js)
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

---

*ezstox is a personal tool for education and analysis, not financial advice.*
