import math
from datetime import date, timedelta

import pytest
from conftest import MARKET, series

from src import analytics, data_fetcher


# --- Pure statistics ---------------------------------------------------------


def test_period_returns_match_compound_growth():
    closes = series([100 * 1.001**i for i in range(300)])
    returns = analytics.period_returns(closes)
    assert returns["1M"] == pytest.approx((1.001**21 - 1) * 100)
    assert returns["3M"] == pytest.approx((1.001**63 - 1) * 100)
    assert returns["1Y"] == pytest.approx((1.001**299 - 1) * 100)


def test_period_returns_without_enough_history_are_none():
    returns = analytics.period_returns(series([100, 101, 102]))
    assert returns["1M"] is None and returns["1Y"] is None


def test_drawdowns():
    closes = series([100, 120, 60, 90])
    assert analytics.max_drawdown(closes) == pytest.approx(-50.0)
    assert analytics.current_drawdown(closes) == pytest.approx(-25.0)


def test_beta_of_a_levered_copy_is_the_leverage():
    bench = series([0.01 * math.sin(i) for i in range(100)])
    assert analytics.beta(bench * 2, bench) == pytest.approx(2.0)


def test_volatility_is_annualised():
    returns = series([0.01, -0.01] * 50)
    expected = returns.std() * math.sqrt(252) * 100
    assert analytics.annual_volatility(returns) == pytest.approx(expected)


def test_rsi_extremes():
    assert analytics.rsi(series(range(1, 40))) == 100.0
    assert analytics.rsi(series(range(40, 1, -1))) < 1


def test_technicals_moving_averages():
    closes = series([float(i) for i in range(1, 251)])
    t = analytics.technicals(closes)
    assert t["sma50"] == pytest.approx(sum(range(201, 251)) / 50)
    assert t["above_sma50"] is True and t["above_sma200"] is True
    assert t["pct_from_high"] == pytest.approx(0.0)


# --- Valuation & FX ------------------------------------------------------------


def test_snapshot_converts_foreign_holdings_to_base_currency(make_portfolio):
    portfolio = make_portfolio({"AAPL": (10, 150), "VUSA.L": (20, 80)}, cash=1000)
    quotes = data_fetcher.get_quotes(["AAPL", "VUSA.L"])
    fx = data_fetcher.get_fx_rates({"USD", "GBP"}, "USD")
    snap = analytics.build_snapshot(portfolio, quotes, fx, "USD")

    by_symbol = {p["symbol"]: p for p in snap["positions"]}
    assert by_symbol["AAPL"]["value"] == pytest.approx(10 * MARKET["AAPL"][0])
    assert by_symbol["VUSA.L"]["value"] == pytest.approx(20 * MARKET["VUSA.L"][0] * 1.27)
    assert snap["total_value"] == pytest.approx(snap["holdings_value"] + 1000)
    assert sum(p["weight"] for p in snap["positions"]) + snap["cash_pct"] == pytest.approx(100)


def test_pence_quotes_are_normalised_to_pounds():
    quote = data_fetcher.get_quote("LLOY.L")
    assert quote["currency"] == "GBP"
    assert quote["price"] == pytest.approx(MARKET["LLOY.L"][0] / 100)
    # Fundamentals' price fields get the same treatment, so they're comparable
    fund = data_fetcher.get_fundamentals("LLOY.L")
    assert fund["fifty_two_week_high"] == pytest.approx(MARKET["LLOY.L"][0] * 1.1 / 100)


def test_unconvertible_currency_is_reported_not_mixed_in(make_portfolio):
    portfolio = make_portfolio({"AAPL": (1, 100), "VUSA.L": (1, 80)})
    quotes = data_fetcher.get_quotes(["AAPL", "VUSA.L"])
    snap = analytics.build_snapshot(portfolio, quotes, {"USD": 1.0}, "USD")
    assert snap["unconverted"] == ["VUSA.L"]
    assert snap["holdings_value"] == pytest.approx(MARKET["AAPL"][0])


# --- Full analysis ---------------------------------------------------------------


def test_run_produces_metrics_events_and_findings(make_portfolio):
    portfolio = make_portfolio({"NVDA": (100, 50), "AAPL": (5, 150), "MSFT": (2, 300)}, ["TSLA"], cash=500)
    a = analytics.run(portfolio)

    m = a["metrics"]
    assert m["volatility"] > 0
    assert m["beta"] is not None
    assert 1 <= m["effective_positions"] <= 3
    assert set(a["benchmark_returns"]) == {"1M", "3M", "6M", "YTD", "1Y"}
    assert a["risk_free"] == pytest.approx(0.045)

    titles = [f["title"] for f in a["findings"]]
    assert any(t.startswith("NVDA is") and "of your portfolio" in t for t in titles)
    assert any("NVDA trades above its average analyst target" in t for t in titles)
    assert "NVDA, AAPL, MSFT report earnings in 9 days" in " ".join(titles)  # grouped by day
    assert any("AAPL goes ex-dividend in 4 days" in t for t in titles)

    levels = [analytics.LEVELS[f["level"]] for f in a["findings"]]
    assert levels == sorted(levels), "findings must be ordered by severity"

    sectors = dict((name, pct) for name, _, pct in a["sectors"])
    assert sum(sectors.values()) == pytest.approx(100)
    assert "Cash" in sectors


def test_events_window():
    today = date(2026, 1, 1)
    funds = {"A": {"next_earnings": (today + timedelta(days=5)).isoformat()},
             "B": {"next_earnings": (today + timedelta(days=60)).isoformat()},
             "C": {"next_earnings": (today - timedelta(days=1)).isoformat()}}
    events = analytics.upcoming_events(["A", "B", "C"], funds, days=30, today=today)
    assert [e["symbol"] for e in events] == ["A"]


def test_empty_portfolio_has_no_metrics(make_portfolio):
    a = analytics.run(make_portfolio(cash=100))
    assert a["metrics"] == {}
    assert a["findings"] == []


def test_unknown_sectors_never_trigger_a_concentration_warning(make_portfolio):
    # The dashboard runs on cached fundamentals only; with an empty cache every
    # stock is unclassified, which must not look like sector concentration.
    a = analytics.run(make_portfolio({"AAPL": (10, 150), "MSFT": (5, 300)}), fundamentals="cache")
    assert a["sectors"] == []
    assert not any(" is in " in f["title"] for f in a["findings"])
