"""
Shared test fixtures.

Every test runs against a deterministic fake market instead of Yahoo
Finance: yfinance.Ticker and yfinance.Search are replaced with fakes that
return realistic data shapes (including the pence-quoted London listings
and both news formats Yahoo has used). No test touches the network.
"""

import math
import random
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yfinance as yf  # noqa: E402

from src import data_fetcher  # noqa: E402

# symbol: (last price, daily drift, daily volatility, currency, kind)
MARKET = {
    "AAPL": (227.48, 0.0008, 0.016, "USD", "EQUITY"),
    "MSFT": (416.06, 0.0007, 0.015, "USD", "EQUITY"),
    "NVDA": (131.20, 0.0015, 0.030, "USD", "EQUITY"),
    "TSLA": (251.33, -0.0004, 0.035, "USD", "EQUITY"),
    "VUSA.L": (94.12, 0.0006, 0.010, "GBP", "ETF"),
    "LLOY.L": (5480.0, 0.0003, 0.014, "GBp", "EQUITY"),  # quoted in pence
    "AMD": (168.71, 0.0004, 0.028, "USD", "EQUITY"),
    "META": (589.95, 0.0012, 0.022, "USD", "EQUITY"),
    "SPY": (571.30, 0.0006, 0.010, "USD", "ETF"),
    "QQQ": (488.12, 0.0007, 0.013, "USD", "ETF"),
    "DIA": (421.66, 0.0004, 0.009, "USD", "ETF"),
    "XLK": (229.40, 0.0008, 0.015, "USD", "ETF"),
    "^VIX": (17.85, 0.0, 0.04, "USD", "INDEX"),
    "^IRX": (4.5, 0.0, 0.0, "USD", "INDEX"),
    "GBPUSD=X": (1.27, 0.0, 0.002, "USD", "CURRENCY"),
    "USDGBP=X": (0.787, 0.0, 0.002, "GBP", "CURRENCY"),
}
NAMES = {"AAPL": "Apple Inc.", "MSFT": "Microsoft Corporation", "NVDA": "NVIDIA Corporation",
         "AMD": "Advanced Micro Devices, Inc.", "META": "Meta Platforms, Inc.", "TSLA": "Tesla, Inc.",
         "VUSA.L": "Vanguard S&P 500 UCITS ETF", "LLOY.L": "Lloyds Banking Group plc"}
TODAY = date.today()


def make_closes(symbol, days=252):
    price, drift, vol, *_ = MARKET[symbol]
    rng = random.Random(symbol)
    values = [1.0]
    for _ in range(days - 1):
        values.append(values[-1] * (1 + drift + rng.gauss(0, vol)))
    scale = price / values[-1]
    index = pd.bdate_range(end=pd.Timestamp(TODAY), periods=days)
    return pd.Series([v * scale for v in values], index=index)


class FakeTicker:
    def __init__(self, symbol):
        self.symbol = symbol.upper()

    def history(self, period="1y", interval="1d"):
        if self.symbol not in MARKET:
            return pd.DataFrame({"Close": []})
        return pd.DataFrame({"Close": make_closes(self.symbol)})

    def get_history_metadata(self):
        return {"currency": MARKET[self.symbol][3]}

    def get_news(self, count=10, tab="news"):
        if self.symbol not in MARKET:
            return []
        now = datetime.now(timezone.utc)
        new_style = {
            "content": {
                "title": f"{self.symbol} new-format headline",
                "pubDate": (now - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "provider": {"displayName": "Reuters"},
                "canonicalUrl": {"url": f"https://example.com/{self.symbol}/1"},
                "summary": "Summary text.",
            }
        }
        old_style = {
            "title": f"{self.symbol} old-format headline",
            "link": f"https://example.com/{self.symbol}/2",
            "publisher": "Bloomberg",
            "providerPublishTime": int((now - timedelta(days=1)).timestamp()),
        }
        return [new_style, old_style][:count]

    def get_calendar(self):
        return {"Earnings Date": [TODAY + timedelta(days=9)]}

    @property
    def info(self):
        if self.symbol not in MARKET:
            return {}
        price, _, _, currency, kind = MARKET[self.symbol]
        return {
            "quoteType": kind,
            "longName": NAMES.get(self.symbol, self.symbol),
            "sector": "Technology" if kind == "EQUITY" else None,
            "currency": currency,
            "trailingPE": 30.0 if kind == "EQUITY" else None,
            "fiftyTwoWeekHigh": price * 1.1,
            "fiftyTwoWeekLow": price * 0.7,
            "targetMeanPrice": price * 0.95 if self.symbol == "NVDA" else price * 1.1,
            "exDividendDate": int(datetime.combine(TODAY + timedelta(days=4), datetime.min.time()).timestamp()) if self.symbol == "AAPL" else None,
            "category": "Large Blend" if kind == "ETF" else None,
        }


class FakeSearch:
    def __init__(self, query, **kwargs):
        q = query.lower()
        self.quotes = [
            {"symbol": s, "longname": n, "exchDisp": "NASDAQ", "quoteType": "EQUITY", "typeDisp": "Equity"}
            for s, n in NAMES.items()
            if q in n.lower()
        ]


@pytest.fixture(autouse=True)
def fake_market(monkeypatch, tmp_path):
    monkeypatch.setattr(yf, "Ticker", FakeTicker)
    monkeypatch.setattr(yf, "Search", FakeSearch)
    monkeypatch.setattr(data_fetcher, "CACHE_DIR", tmp_path / "cache")
    for var in ("BASE_CURRENCY", "AI_PROVIDER", "OPENAI_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_MODEL"):
        monkeypatch.delenv(var, raising=False)
    data_fetcher.clear_cache()
    yield
    data_fetcher.clear_cache()


@pytest.fixture
def make_portfolio(tmp_path):
    """Build a Portfolio in a temp data dir: make_portfolio({"AAPL": (10, 150)}, ["MSFT"], cash=1000)."""
    from src.portfolio_manager import Portfolio

    def build(holdings=None, watchlist=None, cash=0.0):
        data_dir = tmp_path / "data"
        portfolio = Portfolio(data_dir=data_dir)
        for symbol, (shares, cost) in (holdings or {}).items():
            portfolio.holdings[symbol] = {"shares": shares, "cost_basis": cost}
        portfolio.watchlist = list(watchlist or [])
        portfolio.cash = cash
        portfolio.save()
        return Portfolio(data_dir=data_dir)

    return build


def series(values, start="2025-01-01"):
    return pd.Series(values, index=pd.bdate_range(start=start, periods=len(values)), dtype=float)


@pytest.fixture
def app_env(monkeypatch, tmp_path):
    """
    Run app screens against temp folders with a recording console.
    Returns an object with .feed(*lines) to script keyboard input and
    .output() to read what was rendered.
    """
    import io

    from rich.console import Console

    from src import app, config, llm_advisor, ui

    data_dir, reports_dir, env_file = tmp_path / "data", tmp_path / "reports", tmp_path / ".env"
    for module in (config, app):
        monkeypatch.setattr(module, "DATA_DIR", data_dir, raising=False)
    for module in (config, app, llm_advisor):
        monkeypatch.setattr(module, "REPORTS_DIR", reports_dir, raising=False)
    monkeypatch.setattr(config, "ENV_FILE", env_file)
    monkeypatch.setattr(app, "ENV_FILE", env_file)

    console = Console(theme=ui.THEME, record=True, width=120, force_terminal=False, color_system=None)
    monkeypatch.setattr(ui, "console", console)
    monkeypatch.setattr(app, "console", console)

    class Env:
        data = data_dir
        reports = reports_dir

        def feed(self, *lines):
            monkeypatch.setattr(sys, "stdin", io.StringIO("".join(f"{line}\n" for line in lines)))

        def output(self):
            return console.export_text(clear=False)

    return Env()


__all__ = ["series", "make_closes", "MARKET", "math"]
