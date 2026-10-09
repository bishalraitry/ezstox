"""
Portfolio analytics: the numbers behind the insights.

Everything here is computed from ~1 year of daily prices plus fundamentals,
with no AI involved, so it's fast, free, deterministic and testable:

- valuation of every holding in the base currency (FX-converted)
- returns over 1M / 3M / 6M / YTD / 1Y versus the S&P 500
- risk: volatility, beta, max drawdown, Sharpe ratio
- diversification: effective number of positions, correlated pairs, sectors
- technicals per symbol: 50/200-day averages, RSI, distance from 52w high
- upcoming earnings and ex-dividend dates
- ranked findings ("NVDA is 41% of your portfolio", "AAPL and MSFT move
  together") that the dashboard, Insights screen and AI prompt all share

Portfolio-level history uses your *current* weights applied to the last
year of prices: it answers "how has this mix behaved", not "what did my
account actually return" (ezstox doesn't record trade history).

`run()` fetches and assembles everything; the functions below it are pure.
"""

import math
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pandas as pd

from src import data_fetcher as data
from src.config import BENCHMARK, base_currency

TRADING_DAYS = 252
PERIODS = [("1M", 21), ("3M", 63), ("6M", 126), ("YTD", None), ("1Y", 252)]
RISK_FREE_SYMBOL = "^IRX"  # 13-week US T-bill yield, in percent

# Thresholds for findings
CONCENTRATION_RISK = 25.0  # single holding, % of portfolio
TOP3_WATCH = 60.0
SECTOR_WATCH = 40.0
HIGH_CORRELATION = 0.80
HIGH_BETA = 1.3
LOW_BETA = 0.7
BENCHMARK_GAP = 5.0  # percentage points
BIG_LOSS = -25.0
OFF_HIGH = -25.0
EVENT_DAYS = 14

LEVELS = {"risk": 0, "watch": 1, "info": 2, "good": 3}


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def run(portfolio, fundamentals="network", today=None):
    """
    Fetch everything and compute the full analysis.

    Args:
        fundamentals: "network" (fetch if not cached), "cache" (disk cache
            only - instant, used by the dashboard) or "none"
    """
    base = base_currency()
    symbols = portfolio.get_all_symbols()
    extra = [BENCHMARK, RISK_FREE_SYMBOL]

    with ThreadPoolExecutor(max_workers=2) as pool:
        f_hist = pool.submit(data.get_histories, symbols + extra)
        f_fund = None
        if fundamentals != "none" and symbols:
            f_fund = pool.submit(data.get_fundamentals_for, symbols, fundamentals == "cache")
        histories = f_hist.result()
        funds = {s: f or {} for s, f in (f_fund.result() if f_fund else {}).items()}

    quotes = {s: data.get_quote(s) for s in symbols}  # served from the history cache
    fx = data.get_fx_rates({q["currency"] for q in quotes.values() if q}, base)

    snapshot = build_snapshot(portfolio, quotes, fx, base)
    tech = {s: technicals(h["closes"]) for s, h in histories.items() if h and s in symbols}
    bench = histories.get(BENCHMARK)
    rf_hist = histories.get(RISK_FREE_SYMBOL)
    risk_free = float(rf_hist["closes"].iloc[-1]) / 100 if rf_hist else None
    metrics = portfolio_metrics(snapshot, histories, bench["closes"] if bench else None, risk_free)
    # Only meaningful once fundamentals are known (the dashboard may run on an empty cache)
    sectors = sector_allocation(snapshot, funds) if any(funds.values()) else []
    events = upcoming_events(symbols, funds, today=today)

    analysis = {
        "base": base,
        "symbols": symbols,
        "quotes": quotes,
        "fx": fx,
        "snapshot": snapshot,
        "tech": tech,
        "metrics": metrics,
        "benchmark_returns": period_returns(bench["closes"], today) if bench else {},
        "benchmark_volatility": annual_volatility(daily_returns(bench["closes"])) if bench else None,
        "benchmark_drawdown": max_drawdown(bench["closes"]) if bench else None,
        "risk_free": risk_free,
        "sectors": sectors,
        "events": events,
        "fundamentals": funds,
        "watch_only": [s for s in portfolio.watchlist if s not in portfolio.holdings],
    }
    analysis["findings"] = build_findings(analysis)
    return analysis


# ---------------------------------------------------------------------------
# Valuation
# ---------------------------------------------------------------------------


def build_snapshot(portfolio, quotes, fx, base):
    """
    Value every holding in its own currency and in the base currency.
    Cash is assumed to be held in the base currency.
    """
    positions = []
    for symbol, holding in portfolio.holdings.items():
        quote = quotes.get(symbol)
        shares, cost_basis = holding["shares"], holding["cost_basis"]
        position = {
            "symbol": symbol,
            "shares": shares,
            "cost_basis": cost_basis,
            "currency": quote["currency"] if quote else None,
            "price": quote["price"] if quote else None,
            "day_pct": quote["change_pct"] if quote else None,
            "history": quote["history"] if quote else None,
            "value": None,
        }
        rate = fx.get(quote["currency"]) if quote else None
        if quote and rate is not None:
            value = shares * quote["price"] * rate
            cost = shares * cost_basis * rate
            position.update(
                rate=rate,
                value=value,
                cost=cost,
                pnl=value - cost,
                pnl_pct=(value - cost) / cost * 100 if cost > 0 else 0.0,
                day_pnl=shares * (quote["change"] or 0) * rate,
            )
        positions.append(position)

    valued = [p for p in positions if p["value"] is not None]
    holdings_value = sum(p["value"] for p in valued)
    invested = sum(p["cost"] for p in valued)
    day_pnl = sum(p["day_pnl"] for p in valued)
    total = holdings_value + portfolio.cash
    for p in valued:
        p["weight"] = p["value"] / total * 100 if total else 0.0
    previous = holdings_value - day_pnl

    return {
        "base": base,
        "positions": positions,
        "holdings_value": holdings_value,
        "invested": invested,
        "pnl": holdings_value - invested,
        "pnl_pct": (holdings_value - invested) / invested * 100 if invested > 0 else None,
        "day_pnl": day_pnl,
        "day_pct": day_pnl / previous * 100 if valued and previous else None,
        "cash": portfolio.cash,
        "total_value": total,
        "cash_pct": portfolio.cash / total * 100 if total else 0.0,
        "priced": bool(valued),
        "unpriced": [p["symbol"] for p in positions if p["price"] is None],
        "unconverted": [p["symbol"] for p in positions if p["price"] is not None and p["value"] is None],
    }


# ---------------------------------------------------------------------------
# Single-series statistics
# ---------------------------------------------------------------------------


def period_returns(closes, today=None):
    """% return over standard periods; None where there isn't enough history."""
    out = {}
    if closes is None or len(closes) < 2:
        return {label: None for label, _ in PERIODS}
    last = float(closes.iloc[-1])
    today = today or closes.index[-1].date()
    for label, days in PERIODS:
        if label == "YTD":
            before = closes[closes.index < pd.Timestamp(date(today.year, 1, 1))]
            start = float(before.iloc[-1]) if len(before) else None
        elif label == "1Y":
            # A full year, allowing for holidays/short histories
            start = float(closes.iloc[0]) if len(closes) >= 200 else None
        else:
            start = float(closes.iloc[-days - 1]) if len(closes) > days else None
        out[label] = (last / start - 1) * 100 if start else None
    return out


def daily_returns(closes):
    return closes.pct_change(fill_method=None).dropna()


def annual_volatility(returns):
    """Annualised standard deviation of daily returns, in %."""
    if returns is None or len(returns) < 20:
        return None
    return float(returns.std() * math.sqrt(TRADING_DAYS) * 100)


def beta(returns, benchmark_returns):
    """Sensitivity to the benchmark: 1.2 means ~20% bigger moves than the market."""
    if returns is None or benchmark_returns is None:
        return None
    joined = pd.concat([returns, benchmark_returns], axis=1, join="inner").dropna()
    if len(joined) < 40:
        return None
    variance = joined.iloc[:, 1].var()
    return float(joined.iloc[:, 0].cov(joined.iloc[:, 1]) / variance) if variance else None


def max_drawdown(closes):
    """Worst peak-to-trough fall over the series, in % (negative)."""
    if closes is None or len(closes) < 2:
        return None
    return float((closes / closes.cummax() - 1).min() * 100)


def current_drawdown(closes):
    """How far the latest value is below the series peak, in % (negative or 0)."""
    if closes is None or len(closes) < 2:
        return None
    return float((closes.iloc[-1] / closes.max() - 1) * 100)


def rsi(closes, period=14):
    """Wilder's Relative Strength Index (0-100). >70 overbought, <30 oversold."""
    if closes is None or len(closes) <= period:
        return None
    delta = closes.diff().dropna()
    gains = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    losses = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    last_loss = float(losses.iloc[-1])
    if last_loss == 0:
        return 100.0
    return float(100 - 100 / (1 + float(gains.iloc[-1]) / last_loss))


def technicals(closes):
    """Trend and momentum signals for one symbol."""
    if closes is None or len(closes) < 2:
        return {}
    price = float(closes.iloc[-1])
    sma50 = float(closes.iloc[-50:].mean()) if len(closes) >= 50 else None
    sma200 = float(closes.iloc[-200:].mean()) if len(closes) >= 200 else None
    high, low = float(closes.max()), float(closes.min())
    returns = daily_returns(closes)
    return {
        "price": price,
        "sma50": sma50,
        "sma200": sma200,
        "above_sma50": price > sma50 if sma50 else None,
        "above_sma200": price > sma200 if sma200 else None,
        "rsi": rsi(closes),
        "high_1y": high,
        "low_1y": low,
        "pct_from_high": (price / high - 1) * 100,
        "pct_from_low": (price / low - 1) * 100,
        "volatility": annual_volatility(returns),
        "max_drawdown": max_drawdown(closes),
        "returns": period_returns(closes),
    }


# ---------------------------------------------------------------------------
# Portfolio-level statistics
# ---------------------------------------------------------------------------


def portfolio_metrics(snapshot, histories, benchmark_closes=None, risk_free=None):
    """Risk, return and diversification for the current mix of holdings."""
    weights = {
        p["symbol"]: p["value"]
        for p in snapshot["positions"]
        if p["value"] and histories.get(p["symbol"])
    }
    total = sum(weights.values())
    if not total:
        return {}
    weights = {s: v / total for s, v in weights.items()}

    frame = pd.DataFrame({s: histories[s]["closes"] for s in weights}).sort_index().ffill()
    returns = frame.pct_change(fill_method=None).iloc[1:].fillna(0.0)
    port_returns = (returns * pd.Series(weights)).sum(axis=1)
    curve = (1 + port_returns).cumprod()

    bench_returns = daily_returns(benchmark_closes) if benchmark_closes is not None else None
    vol = annual_volatility(port_returns)
    sharpe = None
    if vol:
        excess = port_returns.mean() * TRADING_DAYS - (risk_free or 0.0)
        sharpe = float(excess / (vol / 100))

    # Pairwise correlation of holdings (needs enough overlapping history)
    pairs = []
    symbols = list(weights)
    if len(symbols) >= 2:
        corr = frame.pct_change(fill_method=None).corr(min_periods=60)
        for i, a in enumerate(symbols):
            for b in symbols[i + 1 :]:
                value = corr.loc[a, b]
                if pd.notna(value):
                    pairs.append((a, b, float(value)))
    pairs.sort(key=lambda p: p[2], reverse=True)

    return {
        "weights": weights,
        "returns": period_returns(curve),
        "volatility": vol,
        "beta": beta(port_returns, bench_returns),
        "max_drawdown": max_drawdown(curve),
        "current_drawdown": current_drawdown(curve),
        "sharpe": sharpe,
        "effective_positions": 1 / sum(w * w for w in weights.values()),
        "correlations": pairs,
        "avg_correlation": sum(p[2] for p in pairs) / len(pairs) if pairs else None,
        "curve": curve,
    }


def sector_allocation(snapshot, fundamentals):
    """[(sector, value, % of portfolio)] including cash, largest first."""
    buckets = {}
    for p in snapshot["positions"]:
        if not p["value"]:
            continue
        fund = fundamentals.get(p["symbol"], {})
        kind = fund.get("quote_type")
        if kind == "ETF":
            name = f"ETF · {fund['category']}" if fund.get("category") else "ETFs & funds"
        elif kind and kind != "EQUITY":
            name = kind.title()
        else:
            name = fund.get("sector") or "Unclassified"
        buckets[name] = buckets.get(name, 0.0) + p["value"]
    if snapshot["cash"]:
        buckets["Cash"] = snapshot["cash"]
    total = snapshot["total_value"] or 1
    return sorted(((n, v, v / total * 100) for n, v in buckets.items()), key=lambda x: x[1], reverse=True)


def upcoming_events(symbols, fundamentals, days=30, today=None):
    """Earnings and ex-dividend dates in the next `days` days, soonest first."""
    today = today or date.today()
    events = []
    for symbol in symbols:
        fund = fundamentals.get(symbol, {})
        for kind, field in (("Earnings", "next_earnings"), ("Ex-dividend", "ex_dividend_date")):
            value = fund.get(field)
            if not value:
                continue
            try:
                when = date.fromisoformat(value)
            except ValueError:
                continue
            away = (when - today).days
            if 0 <= away <= days:
                events.append({"symbol": symbol, "kind": kind, "date": when, "days": away})
    return sorted(events, key=lambda e: e["days"])


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------


def _finding(level, title, detail="", symbol=None):
    return {"level": level, "title": title, "detail": detail, "symbol": symbol}


def _pct(value, signed=False):
    return f"{value:+.1f}%" if signed else f"{abs(value):.1f}%"


def build_findings(a):
    """Plain-English observations, most important first."""
    found = []
    snap, metrics, tech, funds = a["snapshot"], a["metrics"], a["tech"], a["fundamentals"]
    valued = sorted((p for p in snap["positions"] if p["value"]), key=lambda p: p["weight"], reverse=True)

    # --- Concentration -----------------------------------------------------
    if valued:
        top = valued[0]
        top3 = sum(p["weight"] for p in valued[:3])
        if top["weight"] >= CONCENTRATION_RISK and len(valued) > 1:
            found.append(_finding(
                "risk", f"{top['symbol']} is {_pct(top['weight'])} of your portfolio",
                "One position this large can dominate your results - consider whether that's intentional.",
                top["symbol"]))
        elif len(valued) > 3 and top3 >= TOP3_WATCH:
            names = ", ".join(p["symbol"] for p in valued[:3])
            found.append(_finding("watch", f"Top 3 holdings are {_pct(top3)} of the portfolio", f"{names} drive most of your outcome."))
        eff = metrics.get("effective_positions")
        if eff and len(valued) >= 4 and eff < len(valued) / 2:
            found.append(_finding(
                "watch", f"{len(valued)} holdings, but effectively only {eff:.1f} positions",
                "Uneven weights mean a few names carry the portfolio."))

    for a_sym, b_sym, rho in metrics.get("correlations", [])[:3]:
        if rho >= HIGH_CORRELATION:
            found.append(_finding(
                "watch", f"{a_sym} and {b_sym} move together (correlation {rho:.2f})",
                "Holding both adds less diversification than it looks."))

    for name, _, pct in a["sectors"]:
        if name not in ("Cash", "Unclassified") and not name.startswith("ETF") and pct >= SECTOR_WATCH:
            found.append(_finding("watch", f"{_pct(pct)} of the portfolio is in {name}", "A sector-specific shock would hit most of your holdings at once."))
            break

    # --- Risk & performance --------------------------------------------------
    b = metrics.get("beta")
    if b is not None:
        b = round(b, 2) or 0.0  # avoid "-0.00"
    if b is not None and b >= HIGH_BETA:
        found.append(_finding("watch", f"Portfolio beta is {b:.2f}", f"Historically it has moved ~{(b - 1) * 100:.0f}% more than the S&P 500, in both directions."))
    elif b is not None and b <= LOW_BETA:
        found.append(_finding("info", f"Portfolio beta is {b:.2f} (defensive)", "It has moved noticeably less than the market."))

    port_ret, bench_ret = metrics.get("returns", {}), a["benchmark_returns"]
    for period in ("1Y", "6M", "3M"):
        p_val, b_val = port_ret.get(period), bench_ret.get(period)
        if p_val is None or b_val is None:
            continue
        gap = p_val - b_val
        if gap <= -BENCHMARK_GAP:
            found.append(_finding("watch", f"Lagging the S&P 500 by {abs(gap):.1f} pts over {period}",
                                  f"Your current mix returned {_pct(p_val, True)} vs {_pct(b_val, True)} for the index."))
        elif gap >= BENCHMARK_GAP:
            found.append(_finding("good", f"Beating the S&P 500 by {gap:.1f} pts over {period}",
                                  f"Your current mix returned {_pct(p_val, True)} vs {_pct(b_val, True)} for the index."))
        break

    dd = metrics.get("current_drawdown")
    if dd is not None and dd <= -10:
        found.append(_finding("watch", f"Portfolio is {_pct(dd)} below its 1-year peak", "Based on your current holdings' prices over the last year."))

    # --- Per holding ---------------------------------------------------------
    for p in valued:
        s, t, f = p["symbol"], tech.get(p["symbol"], {}), funds.get(p["symbol"], {})
        if p["pnl_pct"] <= BIG_LOSS:
            found.append(_finding("risk", f"{s} is down {_pct(p['pnl_pct'])} from your cost",
                                  "Worth revisiting the thesis: would you buy it today at this price?", s))
        if t.get("pct_from_high") is not None and t["pct_from_high"] <= OFF_HIGH:
            found.append(_finding("watch", f"{s} is {_pct(t['pct_from_high'])} below its 1-year high", "", s))
        if t.get("above_sma200") is False:
            found.append(_finding("watch", f"{s} is trading below its 200-day average",
                                  "A common sign of a longer-term downtrend.", s))
        if t.get("rsi") is not None and t["rsi"] >= 75:
            found.append(_finding("info", f"{s} looks overbought (RSI {t['rsi']:.0f})", "Strong recent run - pullbacks are more common from here.", s))
        elif t.get("rsi") is not None and t["rsi"] <= 25:
            found.append(_finding("info", f"{s} looks oversold (RSI {t['rsi']:.0f})", "Heavy recent selling.", s))
        target = f.get("analyst_target_mean")
        if target and p["price"] and p["price"] > target * 1.02 and f.get("quote_type") == "EQUITY":
            found.append(_finding("watch", f"{s} trades above its average analyst target",
                                  f"Price {p['price']:,.2f} vs consensus target {target:,.2f}.", s))
        if p["day_pct"] is not None and abs(p["day_pct"]) >= 5:
            found.append(_finding("info", f"{s} moved {_pct(p['day_pct'], True)} today", "", s))

    # --- Calendar --------------------------------------------------------------
    # Holdings only, grouped by day; the Insights calendar lists everything
    held = {p["symbol"] for p in snap["positions"]}
    grouped = {}
    for e in a["events"]:
        if e["days"] <= EVENT_DAYS and e["symbol"] in held:
            grouped.setdefault((e["kind"], e["date"], e["days"]), []).append(e["symbol"])
    for (kind, when_date, days), symbols in grouped.items():
        when = "today" if days == 0 else "tomorrow" if days == 1 else f"in {days} days"
        many = len(symbols) > 1
        if kind == "Earnings":
            verb = "report earnings" if many else "reports earnings"
        else:
            verb = "go ex-dividend" if many else "goes ex-dividend"
        found.append(_finding("info", f"{', '.join(symbols)} {verb} {when} ({when_date:%d %b})", "", None if many else symbols[0]))

    # --- Cash ----------------------------------------------------------------
    if valued and snap["total_value"]:
        if snap["cash_pct"] < 2:
            found.append(_finding("info", f"Only {_pct(snap['cash_pct'])} in cash", "Little dry powder for opportunities or emergencies."))
        elif snap["cash_pct"] > 40:
            found.append(_finding("info", f"{_pct(snap['cash_pct'])} of the portfolio is cash", "A large cash position lags the market in rising years."))

    # --- Watchlist opportunities ----------------------------------------------
    for s in a["watch_only"]:
        t = tech.get(s, {})
        if t.get("rsi") is not None and t["rsi"] <= 30:
            found.append(_finding("info", f"Watchlist: {s} looks oversold (RSI {t['rsi']:.0f})", "", s))
        elif t.get("pct_from_high") is not None and t["pct_from_high"] <= -30:
            found.append(_finding("info", f"Watchlist: {s} is {_pct(t['pct_from_high'])} off its 1-year high", "", s))

    if valued and not any(f["level"] in ("risk", "watch") for f in found):
        found.append(_finding("good", "No major risk flags", "Concentration, trend and correlation checks all look healthy."))

    return sorted(found, key=lambda f: LEVELS[f["level"]])
