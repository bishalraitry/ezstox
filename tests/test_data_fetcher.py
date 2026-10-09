import pytest
from conftest import MARKET

from src import data_fetcher


def test_quote_fields():
    quote = data_fetcher.get_quote("aapl")
    assert quote["symbol"] == "AAPL"
    assert quote["price"] == pytest.approx(MARKET["AAPL"][0])
    assert quote["currency"] == "USD"
    assert len(quote["history"]) == 22
    assert quote["change_pct"] == pytest.approx((quote["price"] / quote["prev_close"] - 1) * 100, rel=1e-3)


def test_unknown_symbol_returns_none_everywhere():
    assert data_fetcher.get_quote("NOPE") is None
    assert data_fetcher.get_stock_price("NOPE") is None
    assert data_fetcher.get_fundamentals("NOPE") == {}
    assert data_fetcher.get_multiple_prices(["NOPE", "AAPL"]).keys() == {"AAPL"}


def test_fx_rates_to_base():
    assert data_fetcher.get_fx_rates({"USD", "GBP"}, "USD") == {"USD": 1.0, "GBP": pytest.approx(1.27)}
    assert data_fetcher.get_fx_rates({"USD"}, "GBP") == {"GBP": 1.0, "USD": pytest.approx(0.787)}


def test_news_accepts_both_yahoo_formats_newest_first():
    articles = data_fetcher.get_stock_news("MSFT", limit=5)
    assert [a["title"] for a in articles] == ["MSFT new-format headline", "MSFT old-format headline"]
    assert [a["publisher"] for a in articles] == ["Reuters", "Bloomberg"]
    assert all(a["url"].startswith("https://example.com/MSFT") for a in articles)
    assert all(a["date"] != "Unknown date" for a in articles)


def test_fundamentals_are_cached_on_disk(monkeypatch):
    first = data_fetcher.get_fundamentals("AAPL")
    assert first["next_earnings"] is not None
    data_fetcher.clear_cache()
    # Even with the network "gone", the disk cache answers
    monkeypatch.setattr(data_fetcher.yf, "Ticker", lambda s: (_ for _ in ()).throw(RuntimeError("offline")))
    assert data_fetcher.get_fundamentals("AAPL") == first
    assert data_fetcher.get_fundamentals("AAPL", cached_only=True) == first
    assert data_fetcher.get_fundamentals("MSFT", cached_only=True) == {}


def test_search_by_company_name():
    results = data_fetcher.search("apple")
    assert results[0]["symbol"] == "AAPL" and results[0]["name"] == "Apple Inc."
    assert data_fetcher.search("   ") == []
