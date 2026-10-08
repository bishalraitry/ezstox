"""
Market data: prices, news and fundamentals via yfinance.

All network calls are cached in memory for a couple of minutes, so moving
between screens (dashboard -> news -> lookup) doesn't refetch anything,
and multi-symbol fetches run in parallel instead of one after another.
Nothing here prints: failures come back as None / empty results and the
UI decides how to show them (printing would also corrupt the MCP server's
stdio stream).
"""

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import yfinance as yf

# yfinance logs every failed lookup straight to the terminal; we report
# failures ourselves, so keep its logger quiet.
logging.getLogger("yfinance").setLevel(logging.CRITICAL)

CACHE_TTL_SECONDS = 120
MAX_WORKERS = 8

_cache = {}
_cache_lock = threading.Lock()


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


def clear_cache():
    with _cache_lock:
        _cache.clear()


def _parallel(fn, items):
    """Run fn over items concurrently, returning {item: result} in input order."""
    items = list(dict.fromkeys(items))
    if not items:
        return {}
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(items))) as pool:
        return dict(zip(items, pool.map(fn, items)))


# ---------------------------------------------------------------------------
# Prices
# ---------------------------------------------------------------------------


def get_quote(symbol):
    """
    Latest price plus context for one symbol.

    Returns:
        dict with price, prev_close, change, change_pct, change_5d_pct,
        history (last ~month of closes, for sparklines) and currency -
        or None if the symbol couldn't be fetched.
    """
    symbol = symbol.strip().upper()

    def fetch():
        try:
            ticker = yf.Ticker(symbol)
            closes = ticker.history(period="1mo", interval="1d")["Close"].dropna()
            if closes.empty:
                return None
            price = float(closes.iloc[-1])
            prev_close = float(closes.iloc[-2]) if len(closes) > 1 else None
            week_ago = float(closes.iloc[-6]) if len(closes) > 5 else None
            try:
                currency = ticker.get_history_metadata().get("currency")
            except Exception:
                currency = None
            return {
                "symbol": symbol,
                "price": round(price, 2),
                "prev_close": prev_close,
                "change": price - prev_close if prev_close else None,
                "change_pct": (price - prev_close) / prev_close * 100 if prev_close else None,
                "change_5d_pct": (price - week_ago) / week_ago * 100 if week_ago else None,
                "history": [float(c) for c in closes],
                "currency": currency,
            }
        except Exception:
            return None

    return _cached(("quote", symbol), fetch)


def get_quotes(symbols):
    """Quotes for many symbols, fetched in parallel. Failed symbols map to None."""
    return _parallel(get_quote, [s.strip().upper() for s in symbols])


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
    return _parallel(lambda s: get_stock_news(s, limit), [s.strip().upper() for s in symbols])


# ---------------------------------------------------------------------------
# Fundamentals
# ---------------------------------------------------------------------------


def get_fundamentals(symbol):
    """
    Key financial metrics for a symbol.

    Returns:
        dict: metrics (missing ones are None), or an empty dict if unavailable
    """
    symbol = symbol.strip().upper()

    def fetch():
        try:
            info = yf.Ticker(symbol).info or {}
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
            "market_cap": info.get("marketCap"),
            "beta": info.get("beta"),
            "dividend_yield": info.get("dividendYield"),
            "fifty_two_week_high": info.get("fiftyTwoWeekHigh"),
            "fifty_two_week_low": info.get("fiftyTwoWeekLow"),
            "analyst_target_mean": info.get("targetMeanPrice"),
            "analyst_target_high": info.get("targetHighPrice"),
            "analyst_target_low": info.get("targetLowPrice"),
            "recommendation": info.get("recommendationKey"),
            "price_to_book": info.get("priceToBook"),
            "revenue_growth": info.get("revenueGrowth"),
            "earnings_growth": info.get("earningsGrowth"),
        }
        if quote_type == "ETF":
            fundamentals["category"] = info.get("category")
            fundamentals["total_assets"] = info.get("totalAssets")
        return fundamentals

    return _cached(("fundamentals", symbol), fetch) or {}


def get_fundamentals_for(symbols):
    return _parallel(get_fundamentals, [s.strip().upper() for s in symbols])
