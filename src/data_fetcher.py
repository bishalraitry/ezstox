"""
Market data: prices, history, FX, news, fundamentals and search via yfinance.

- One year of daily history per symbol powers quotes, sparklines and all
  the analytics, from a single request per symbol.
- Prices quoted in minor units (London's pence, "GBp") are normalised to
  the major unit (GBP), so they can be valued and converted correctly.
- Network results are cached in memory for a couple of minutes, and
  fundamentals (which change slowly) on disk for 12 hours, so moving
  between screens is instant and repeat runs are fast.
- Multi-symbol fetches run in parallel.

Nothing here prints: failures come back as None / empty results and the
UI decides how to show them (printing would also corrupt the MCP server's
stdio stream).
"""

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import yfinance as yf

from src.config import CACHE_DIR

# yfinance logs every failed lookup straight to the terminal; we report
# failures ourselves, so keep its logger quiet.
logging.getLogger("yfinance").setLevel(logging.CRITICAL)

CACHE_TTL_SECONDS = 120
DISK_TTL_SECONDS = 12 * 3600
MAX_WORKERS = 8

# Minor-unit currencies Yahoo uses, mapped to (major currency, divisor)
SUBUNITS = {"GBp": ("GBP", 100), "GBX": ("GBP", 100), "ZAc": ("ZAR", 100), "ILA": ("ILS", 100)}

_cache = {}
_cache_lock = threading.Lock()
_disk_lock = threading.Lock()


def _cached(key, fetch):
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < CACHE_TTL_SECONDS:
            return hit[1]
    value = fetch()
    if value is not None:
        with _cache_lock:
            _cache[key] = (now, value)
    return value


def clear_cache(kinds=None):
    """Forget in-memory results (all, or only the given kinds, e.g. {"history"})."""
    with _cache_lock:
        for key in list(_cache):
            if kinds is None or key[0] in kinds:
                del _cache[key]


def _parallel(fn, items):
    """Run fn over items concurrently, returning {item: result} in input order."""
    items = list(dict.fromkeys(items))
    if not items:
        return {}
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(items))) as pool:
        return dict(zip(items, pool.map(fn, items)))


def _clean(symbols):
    return [s.strip().upper() for s in symbols if s and s.strip()]


# ---------------------------------------------------------------------------
# Disk cache (fundamentals)
# ---------------------------------------------------------------------------


def _disk_path(name):
    return CACHE_DIR / f"{name}.json"


def _disk_get(name, key):
    try:
        entry = json.loads(_disk_path(name).read_text()).get(key)
    except (OSError, ValueError):
        return None
    if entry and time.time() - entry.get("t", 0) < DISK_TTL_SECONDS:
        return entry.get("data")
    return None


def _disk_put(name, key, data):
    with _disk_lock:
        path = _disk_path(name)
        try:
            store = json.loads(path.read_text())
        except (OSError, ValueError):
            store = {}
        store[key] = {"t": time.time(), "data": data}
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(store))
        tmp.replace(path)


# ---------------------------------------------------------------------------
# Prices
# ---------------------------------------------------------------------------


def get_history(symbol):
    """
    About a year of daily closes for a symbol, in the major currency unit.

    Returns:
        dict with "closes" (pandas Series indexed by date) and "currency",
        or None if the symbol couldn't be fetched.
    """
    symbol = symbol.strip().upper()

    def fetch():
        try:
            ticker = yf.Ticker(symbol)
            closes = ticker.history(period="1y", interval="1d")["Close"].dropna()
            if closes.empty:
                return None
            try:
                currency = ticker.get_history_metadata().get("currency")
            except Exception:
                currency = None
            if currency in SUBUNITS:
                currency, divisor = SUBUNITS[currency]
                closes = closes / divisor
            if getattr(closes.index, "tz", None) is not None:
                closes.index = closes.index.tz_localize(None)
            closes.index = closes.index.normalize()
            closes = closes[~closes.index.duplicated(keep="last")]
            return {"closes": closes.astype(float), "currency": currency or "USD"}
        except Exception:
            return None

    return _cached(("history", symbol), fetch)


def get_histories(symbols):
    return _parallel(get_history, _clean(symbols))


def get_quote(symbol):
    """
    Latest price plus context for one symbol.

    Returns:
        dict with price, prev_close, change, change_pct, change_5d_pct,
        history (last ~month of closes, for sparklines) and currency -
        or None if the symbol couldn't be fetched.
    """
    symbol = symbol.strip().upper()
    data = get_history(symbol)
    if not data:
        return None
    closes = data["closes"]
    price = float(closes.iloc[-1])
    prev_close = float(closes.iloc[-2]) if len(closes) > 1 else None
    week_ago = float(closes.iloc[-6]) if len(closes) > 5 else None
    return {
        "symbol": symbol,
        "price": round(price, 4) if price < 1 else round(price, 2),
        "prev_close": prev_close,
        "change": price - prev_close if prev_close else None,
        "change_pct": (price - prev_close) / prev_close * 100 if prev_close else None,
        "change_5d_pct": (price - week_ago) / week_ago * 100 if week_ago else None,
        "history": [float(c) for c in closes.iloc[-22:]],
        "currency": data["currency"],
    }


def get_quotes(symbols):
    """Quotes for many symbols, fetched in parallel. Failed symbols map to None."""
    return _parallel(get_quote, _clean(symbols))


def get_stock_price(symbol):
    """
    Get current price for a stock symbol.

    Returns:
        float: Current stock price, or None if it couldn't be fetched
    """
    quote = get_quote(symbol)
    return quote["price"] if quote else None


def get_multiple_prices(symbols):
    """
    Get prices for multiple stocks, in parallel.

    Returns:
        dict: {symbol: price} for every symbol that was fetched successfully
    """
    return {s: q["price"] for s, q in get_quotes(symbols).items() if q}


def get_fx_rates(currencies, base):
    """
    Conversion rates into the base currency, e.g. {"GBP": 1.27} for base USD.
    Currencies that couldn't be fetched are left out.
    """
    needed = sorted({c for c in currencies if c and c != base})
    pairs = {c: f"{c}{base}=X" for c in needed}
    histories = get_histories(pairs.values())
    rates = {base: 1.0}
    for currency, pair in pairs.items():
        data = histories.get(pair)
        if data:
            rates[currency] = float(data["closes"].iloc[-1])
    return rates


# ---------------------------------------------------------------------------
# News
# ---------------------------------------------------------------------------


def _parse_published(value):
    """Turn the formats Yahoo uses (ISO string or unix seconds) into a UTC datetime."""
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, tz=timezone.utc)
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (ValueError, OSError, OverflowError):
        return None


def _normalise_article(item):
    """Yahoo has shipped two news shapes over time; accept both."""
    content = item.get("content") or item
    url = (
        (content.get("canonicalUrl") or {}).get("url")
        or (content.get("clickThroughUrl") or {}).get("url")
        or content.get("link")
    )
    provider = content.get("provider") or {}
    published = _parse_published(
        content.get("pubDate") or content.get("displayTime") or content.get("providerPublishTime")
    )
    return {
        "title": (content.get("title") or "").strip() or "No title",
        "date": published.strftime("%Y-%m-%d") if published else "Unknown date",
        "published": published.isoformat() if published else None,
        "publisher": provider.get("displayName") or content.get("publisher") or "",
        "summary": (content.get("summary") or "").strip(),
        "url": url or "No link available",
    }


def get_stock_news(symbol, limit=3):
    """
    Get recent news headlines for a stock, newest first.

    Returns:
        list: dicts with title, date, published (ISO), publisher, summary
        and url - or an empty list if nothing could be fetched
    """
    symbol = symbol.strip().upper()

    def fetch():
        try:
            items = yf.Ticker(symbol).get_news(count=max(limit, 10))
        except Exception:
            return None
        articles = [_normalise_article(item) for item in items or []]
        articles.sort(key=lambda a: a["published"] or "", reverse=True)
        return articles

    return (_cached(("news", symbol), fetch) or [])[:limit]


def get_news_for(symbols, limit=3):
    """News for many symbols, fetched in parallel."""
    return _parallel(lambda s: get_stock_news(s, limit), _clean(symbols))


# ---------------------------------------------------------------------------
# Fundamentals
# ---------------------------------------------------------------------------


PRICE_FIELDS = (
    "fifty_two_week_high",
    "fifty_two_week_low",
    "analyst_target_mean",
    "analyst_target_high",
    "analyst_target_low",
)


def _unix_to_date(value):
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).date().isoformat() if value else None
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _next_earnings(ticker, info):
    """Next earnings date as ISO string, from the quote data or the calendar."""
    for key in ("earningsTimestampStart", "earningsTimestamp"):
        date = _unix_to_date(info.get(key))
        if date:
            return date
    try:
        dates = (ticker.get_calendar() or {}).get("Earnings Date") or []
        return dates[0].isoformat() if dates else None
    except Exception:
        return None


def get_fundamentals(symbol, cached_only=False):
    """
    Key financial metrics for a symbol (cached on disk for 12 hours).

    Args:
        cached_only: return only what's already cached, never hit the network

    Returns:
        dict: metrics (missing ones are None), or an empty dict if unavailable
    """
    symbol = symbol.strip().upper()

    def fetch():
        cached = _disk_get("fundamentals", symbol)
        if cached is not None:
            return cached
        try:
            ticker = yf.Ticker(symbol)
            info = ticker.info or {}
        except Exception:
            return None
        if not info:
            return None
        quote_type = info.get("quoteType", "EQUITY")
        fundamentals = {
            "quote_type": quote_type,
            "long_name": info.get("longName") or info.get("shortName") or symbol,
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "pe_ratio": info.get("trailingPE"),
            "forward_pe": info.get("forwardPE"),
            "peg_ratio": info.get("trailingPegRatio") or info.get("pegRatio"),
            "market_cap": info.get("marketCap"),
            "beta": info.get("beta"),
            "dividend_yield": info.get("dividendYield"),
            "fifty_two_week_high": info.get("fiftyTwoWeekHigh"),
            "fifty_two_week_low": info.get("fiftyTwoWeekLow"),
            "analyst_target_mean": info.get("targetMeanPrice"),
            "analyst_target_high": info.get("targetHighPrice"),
            "analyst_target_low": info.get("targetLowPrice"),
            "analyst_count": info.get("numberOfAnalystOpinions"),
            "recommendation": info.get("recommendationKey"),
            "price_to_book": info.get("priceToBook"),
            "profit_margin": info.get("profitMargins"),
            "revenue_growth": info.get("revenueGrowth"),
            "earnings_growth": info.get("earningsGrowth"),
            "debt_to_equity": info.get("debtToEquity"),
            "free_cash_flow": info.get("freeCashflow"),
            "short_percent_float": info.get("shortPercentOfFloat"),
            "currency": info.get("currency"),
            "next_earnings": _next_earnings(ticker, info) if quote_type == "EQUITY" else None,
            "ex_dividend_date": _unix_to_date(info.get("exDividendDate")),
        }
        if quote_type == "ETF":
            fundamentals["category"] = info.get("category")
            fundamentals["total_assets"] = info.get("totalAssets")
            fundamentals["expense_ratio"] = info.get("netExpenseRatio") or info.get("annualReportExpenseRatio")
        if fundamentals["currency"] in SUBUNITS:
            # Pence-quoted: bring price-like fields into pounds, matching get_history
            fundamentals["currency"], divisor = SUBUNITS[fundamentals["currency"]]
            for field in PRICE_FIELDS:
                if fundamentals.get(field) is not None:
                    fundamentals[field] = fundamentals[field] / divisor
        _disk_put("fundamentals", symbol, fundamentals)
        return fundamentals

    if cached_only:
        return _disk_get("fundamentals", symbol) or {}
    return _cached(("fundamentals", symbol), fetch) or {}


def get_fundamentals_for(symbols, cached_only=False):
    return _parallel(lambda s: get_fundamentals(s, cached_only), _clean(symbols))


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

SEARCH_TYPES = {"EQUITY", "ETF", "MUTUALFUND", "INDEX", "CURRENCY", "CRYPTOCURRENCY", "FUTURE"}


def search(query, limit=6):
    """
    Find tickers by company name or partial symbol ("apple", "vanguard s&p").

    Returns:
        list of dicts with symbol, name, exchange and type
    """
    query = query.strip()
    if not query:
        return []

    def fetch():
        try:
            quotes = yf.Search(query, max_results=limit, news_count=0, lists_count=0,
                               recommended=0, raise_errors=False).quotes
        except Exception:
            return None
        results = []
        for q in quotes or []:
            if q.get("quoteType", "").upper() not in SEARCH_TYPES:
                continue
            results.append({
                "symbol": q.get("symbol", "").upper(),
                "name": q.get("longname") or q.get("shortname") or "",
                "exchange": q.get("exchDisp") or q.get("exchange") or "",
                "type": q.get("typeDisp") or q.get("quoteType") or "",
            })
        return results

    return _cached(("search", query.lower()), fetch) or []
