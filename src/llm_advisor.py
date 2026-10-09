"""
LLM Advisor - AI portfolio review grounded in computed data.

Pipeline:
  1. Gather   - the full analytics run (valuation, risk, correlations,
                technicals, fundamentals, events, findings) plus headlines,
                market context and world news, all fetched concurrently.
  2. Read     - full article text for the most relevant headlines, in
                parallel (Jina Reader first, trafilatura as fallback).
  3. Write    - one structured "data pack" prompt, streamed back from any
                OpenAI-compatible API (OpenAI, DeepSeek, ...).
  4. Discuss  - follow-up questions continue the same conversation.

The model is told to treat the app's numbers as authoritative and to cite
news by tag; the source list with real URLs is appended by the app, not the
model, so links can't be hallucinated.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import requests

from src import analytics
from src.config import PROVIDERS, REPORTS_DIR, ai_key, ai_model, ai_provider, get_setting
from src.data_fetcher import get_news_for, get_quotes, get_stock_news

SCRAPE_WORKERS = 8
SCRAPE_TIMEOUT = 15
MAX_ARTICLE_CHARS = 3000
EQUITY_ARTICLES = 5
ETF_ARTICLES = 2
WORLD_NEWS_ARTICLES = 5

MARKET_SYMBOLS = [("QQQ", "Nasdaq 100"), ("DIA", "Dow Jones"), ("XLK", "Tech sector (XLK)"), ("^VIX", "VIX")]
REASONING_PREFIXES = ("o1", "o3", "o4", "gpt-5", "deepseek-reasoner")

UNAVAILABLE = (None, "Content unavailable", "No URL available")
BROWSER_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) ezstox"}

SYSTEM_PROMPT = """You are a seasoned portfolio analyst writing for a private investor.
You receive a data pack computed by their portfolio app: positions, risk analytics, technicals, fundamentals, upcoming events, rule-based findings, market context and news.

Rules:
- The data pack's numbers are authoritative. Use them; never invent prices, targets, ratios, dates or sources. If something is missing, say so in a few words rather than guessing.
- Cite news only with the tags given, e.g. [AAPL-2] or [WORLD-1], and only for the company the article is about.
- Interpret, don't just restate: explain what a number means for this investor and what to do about it.
- Be direct and specific. Prefer concrete levels, sizes and dates over generic advice.
- ETFs are judged on their underlying index or asset, macro trends and fees - not P/E or analyst targets.
- Write clean Markdown. End with a single italic line noting this is educational analysis, not personalised financial advice."""

TASK = """Write the review with exactly these sections:

## Verdict
Two or three sentences with the bottom line. Then a Markdown table with columns: Symbol | Action | Conviction | Why - one row per holding plus any watchlist names worth acting on. Action is one of Hold, Add, Trim, Sell, Buy, Watch. Conviction is 1-10. "Why" is one short line.

## Portfolio health
Risk, diversification and performance versus the S&P 500, using the analytics. Respond to every APP FINDING marked RISK or WATCH: agree, disagree or qualify it, with reasons.

## Holdings
One "### SYMBOL - Action" subsection per holding (80-150 words): the thesis from fundamentals, trend and cited news; the key risk; what would change your mind.

## Watchlist
At most three names worth acting on now: why now, and the entry level or trigger to wait for. Skip the section if none qualify.

## This week
Three to five numbered, concrete actions, including any earnings or ex-dividend dates to watch.

Keep the whole review under 1,400 words."""


class NullProgress:
    """Progress sink that ignores everything (used when no UI is attached)."""

    def stage(self, key, description, total=None):
        pass

    def advance(self, key, amount=1):
        pass

    def done(self, key, note=""):
        pass


# ---------------------------------------------------------------------------
# Article reading
# ---------------------------------------------------------------------------


def scrape_with_jina(url):
    """Jina Reader returns clean article text for almost any URL."""
    headers = {"X-Return-Format": "text"}
    jina_key = get_setting("JINA_API_KEY")
    if jina_key:
        headers["Authorization"] = f"Bearer {jina_key}"
    try:
        response = requests.get(f"https://r.jina.ai/{url}", headers=headers, timeout=SCRAPE_TIMEOUT)
        if response.status_code == 200 and len(response.text) > 100:
            return response.text[:MAX_ARTICLE_CHARS]
    except requests.RequestException:
        pass
    return None


def scrape_with_trafilatura(url):
    """Fallback: fetch the page ourselves and extract the main text locally."""
    try:
        import trafilatura

        response = requests.get(url, headers=BROWSER_HEADERS, timeout=SCRAPE_TIMEOUT)
        if response.status_code != 200:
            return None
        text = trafilatura.extract(response.text, url=url)
        if text and len(text) > 100:
            return text[:MAX_ARTICLE_CHARS]
    except Exception:
        pass
    return None


def get_article_content(url):
    if not url or url == "No link available":
        return None
    return scrape_with_jina(url) or scrape_with_trafilatura(url)


def scrape_articles(articles, progress, key="scrape"):
    """Fill in article["full_content"] for every article, in parallel."""

    def scrape(article):
        url = article.get("url")
        if not url or url == "No link available":
            article["full_content"] = "No URL available"
        else:
            article["full_content"] = get_article_content(url) or "Content unavailable"
        progress.advance(key)
        return article["full_content"] not in UNAVAILABLE

    if not articles:
        return 0
    with ThreadPoolExecutor(max_workers=SCRAPE_WORKERS) as pool:
        return sum(pool.map(scrape, articles))


def _has_content(article):
    return article.get("full_content") not in UNAVAILABLE


def get_financial_news_rss(limit=10):
    """Get financial news from Google News RSS (free, no API key)"""
    try:
        import feedparser

        feed = feedparser.parse(
            "https://news.google.com/rss/search?q=stock+market+finance+economy+when:2d"
            "&hl=en-US&gl=US&ceid=US:en"
        )
        return [
            {"title": entry.title, "url": entry.link, "date": getattr(entry, "published", "Recent")}
            for entry in feed.entries[:limit]
        ]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Gathering
# ---------------------------------------------------------------------------


def prepare(portfolio, progress=None):
    """
    Gather and read everything the review needs.

    Returns:
        dict context: analysis, news, world_news, market, sources, prompt
    Raises:
        RuntimeError: if there's nothing to analyse
    """
    progress = progress or NullProgress()
    if portfolio.is_empty:
        raise RuntimeError("Your portfolio and watchlist are empty - add some symbols first.")
    symbols = portfolio.get_all_symbols()

    progress.stage("gather", "Prices, analytics, fundamentals & news")
    with ThreadPoolExecutor(max_workers=4) as pool:
        f_analysis = pool.submit(analytics.run, portfolio, "network")
        f_news = pool.submit(get_news_for, symbols, 8)
        f_market = pool.submit(get_quotes, [s for s, _ in MARKET_SYMBOLS])
        f_world = pool.submit(get_financial_news_rss, 10)
        analysis, all_news, market, world = f_analysis.result(), f_news.result(), f_market.result(), f_world.result()
    priced = sum(1 for s in symbols if analysis["quotes"].get(s))
    progress.done("gather", f"{priced}/{len(symbols)} priced · {len(analysis['findings'])} findings")

    # Read the most relevant articles in full (ETFs need fewer - they're
    # analysed through their underlying index or asset instead).
    news, to_read = {}, []
    for symbol in symbols:
        is_etf = analysis["fundamentals"].get(symbol, {}).get("quote_type") == "ETF"
        picked = [dict(a) for a in all_news.get(symbol, [])[: ETF_ARTICLES if is_etf else EQUITY_ARTICLES]]
        news[symbol] = picked
        to_read.extend(picked)
    world = [dict(a) for a in world]
    to_read.extend(world[:WORLD_NEWS_ARTICLES])

    progress.stage("scrape", "Reading full articles", total=len(to_read))
    read = scrape_articles(to_read, progress)
    progress.done("scrape", f"{read}/{len(to_read)} articles read")

    context = {"analysis": analysis, "news": news, "world_news": world, "market": market, "portfolio": portfolio}
    context["prompt"] = format_prompt(context)
    context["sources"] = build_sources(context)
    return context


# ---------------------------------------------------------------------------
# Prompt (the "data pack")
# ---------------------------------------------------------------------------


def _n(value, fmt="{:,.2f}", none="n/a"):
    return fmt.format(value) if value is not None else none


def _pct(value, signed=True):
    if value is None:
        return "n/a"
    return f"{value:+.1f}%" if signed else f"{value:.1f}%"


def _ratio_pct(value):
    """Yahoo gives growth/margins as fractions (0.12 = 12%)."""
    return f"{value * 100:+.1f}%" if value is not None else "n/a"


def _returns_line(returns):
    return " | ".join(f"{label} {_pct(returns.get(label))}" for label, _ in analytics.PERIODS)


def _symbol_block(symbol, a, position=None):
    fund = a["fundamentals"].get(symbol, {})
    tech = a["tech"].get(symbol, {})
    quote = a["quotes"].get(symbol)
    kind = fund.get("quote_type", "EQUITY")
    header = f"{symbol} - {fund.get('long_name', symbol)} [{kind}]"
    if fund.get("sector") or fund.get("category"):
        header += f" · {fund.get('sector') or fund.get('category')}"
    lines = [header]

    if position and position.get("value") is not None:
        lines.append(
            f"  Position: {position['shares']:g} @ {position['cost_basis']:,.2f} -> {position['price']:,.2f} {position['currency']}"
            f" | value {a['base']} {position['value']:,.0f} | P&L {a['base']} {position['pnl']:+,.0f} ({_pct(position['pnl_pct'])})"
            f" | weight {position['weight']:.1f}%"
        )
    elif quote:
        lines.append(f"  Price: {quote['price']:,.2f} {quote['currency']} | today {_pct(quote['change_pct'])}")
    else:
        lines.append("  Price: unavailable")

    if tech:
        trend = [
            f"vs 50d avg {'above' if tech['above_sma50'] else 'below'}" if tech.get("above_sma50") is not None else None,
            f"vs 200d avg {'above' if tech['above_sma200'] else 'below'}" if tech.get("above_sma200") is not None else None,
            f"RSI {tech['rsi']:.0f}" if tech.get("rsi") is not None else None,
            f"{_pct(tech['pct_from_high'])} from 1y high",
            f"1y vol {_n(tech.get('volatility'), '{:.0f}%')}",
            f"max drawdown {_pct(tech.get('max_drawdown'))}",
        ]
        lines.append("  Returns: " + _returns_line(tech.get("returns", {})))
        lines.append("  Trend: " + " | ".join(t for t in trend if t))

    if fund and kind == "EQUITY":
        target, price = fund.get("analyst_target_mean"), (quote or {}).get("price")
        upside = f" ({_pct((target / price - 1) * 100)})" if target and price else ""
        analysts = f", {fund['analyst_count']} analysts" if fund.get("analyst_count") else ""
        lines.append(
            f"  Valuation: P/E {_n(fund.get('pe_ratio'))} | fwd P/E {_n(fund.get('forward_pe'))} | PEG {_n(fund.get('peg_ratio'))}"
            f" | P/B {_n(fund.get('price_to_book'))} | mkt cap {_n(fund.get('market_cap'), '{:,.0f}')}"
        )
        lines.append(
            f"  Quality: revenue growth {_ratio_pct(fund.get('revenue_growth'))} | earnings growth {_ratio_pct(fund.get('earnings_growth'))}"
            f" | profit margin {_ratio_pct(fund.get('profit_margin'))} | debt/equity {_n(fund.get('debt_to_equity'))}"
            f" | short interest {_ratio_pct(fund.get('short_percent_float'))}"
        )
        lines.append(
            f"  Street: target {_n(target)}{upside}{analysts} | consensus {fund.get('recommendation') or 'n/a'}"
            f" | dividend yield {_n(fund.get('dividend_yield'), '{:.2f}%')} | next earnings {fund.get('next_earnings') or 'n/a'}"
        )
    elif fund:
        lines.append(
            f"  Fund: assets {_n(fund.get('total_assets'), '{:,.0f}')} | expense ratio {_n(fund.get('expense_ratio'), '{:.2f}%')}"
            f" | yield {_n(fund.get('dividend_yield'), '{:.2f}%')}"
        )
    return "\n".join(lines)


def format_prompt(context):
    a = context["analysis"]
    snap, m = a["snapshot"], a["metrics"]
    base = a["base"]
    out = [f"DATE: {datetime.now():%Y-%m-%d %H:%M} | BASE CURRENCY: {base}", ""]

    out.append("=== PORTFOLIO SUMMARY ===")
    if snap["priced"]:
        out.append(
            f"Total {base} {snap['total_value']:,.0f} = holdings {snap['holdings_value']:,.0f} + cash {snap['cash']:,.0f} ({snap['cash_pct']:.1f}%)"
        )
        out.append(f"Unrealised P&L {base} {snap['pnl']:+,.0f} ({_pct(snap['pnl_pct'])}) | today {base} {snap['day_pnl']:+,.0f} ({_pct(snap['day_pct'])})")
    else:
        out.append(f"No holdings could be priced. Cash {base} {snap['cash']:,.0f}.")
    for label, symbols in (("Unpriced", snap["unpriced"]), ("No FX rate", snap["unconverted"])):
        if symbols:
            out.append(f"{label}: {', '.join(symbols)} (excluded from totals)")

    if m:
        out.append("")
        out.append("=== RISK & PERFORMANCE (current holdings, applied to the last 12 months of prices) ===")
        out.append("Portfolio: " + _returns_line(m["returns"]))
        out.append("S&P 500:   " + _returns_line(a["benchmark_returns"]))
        rf = f" (risk-free {a['risk_free'] * 100:.2f}%)" if a.get("risk_free") is not None else ""
        out.append(
            f"Volatility {_n(m['volatility'], '{:.1f}%')} | beta {_n(m['beta'])} | max drawdown {_pct(m['max_drawdown'])}"
            f" | now {_pct(m['current_drawdown'])} from peak | Sharpe {_n(m['sharpe'])}{rf}"
        )
        corr = ", ".join(f"{x}/{y} {r:.2f}" for x, y, r in m["correlations"][:5])
        out.append(
            f"Effective positions {m['effective_positions']:.1f} | avg pairwise correlation {_n(m['avg_correlation'])}"
            + (f" | most correlated: {corr}" if corr else "")
        )

    if a["sectors"]:
        out.append("")
        out.append("=== ALLOCATION ===")
        out.append("By sector: " + ", ".join(f"{name} {pct:.1f}%" for name, _, pct in a["sectors"]))

    positions = {p["symbol"]: p for p in snap["positions"]}
    if positions:
        out.append("")
        out.append("=== HOLDINGS ===")
        for symbol in positions:
            out.append(_symbol_block(symbol, a, positions[symbol]))
    if a["watch_only"]:
        out.append("")
        out.append("=== WATCHLIST ===")
        for symbol in a["watch_only"]:
            out.append(_symbol_block(symbol, a))

    if a["events"]:
        out.append("")
        out.append("=== UPCOMING EVENTS (30 days) ===")
        for e in a["events"]:
            out.append(f"{e['date']:%a %d %b}: {e['symbol']} {e['kind'].lower()} (in {e['days']} days)")

    out.append("")
    out.append("=== MARKET CONTEXT ===")
    bench = a["benchmark_returns"]
    out.append(f"S&P 500: 1M {_pct(bench.get('1M'))} | YTD {_pct(bench.get('YTD'))}")
    for symbol, label in MARKET_SYMBOLS:
        q = context["market"].get(symbol)
        if not q:
            continue
        if symbol == "^VIX":
            regime = "calm" if q["price"] < 15 else "normal" if q["price"] < 25 else "elevated fear"
            out.append(f"VIX: {q['price']:.1f} ({regime})")
        else:
            out.append(f"{label}: 5d {_pct(q['change_5d_pct'])}")
    if a.get("risk_free") is not None:
        out.append(f"13-week T-bill yield: {a['risk_free'] * 100:.2f}%")

    if a["findings"]:
        out.append("")
        out.append("=== APP FINDINGS (rule-based, from the analytics above) ===")
        for f in a["findings"]:
            out.append(f"[{f['level'].upper()}] {f['title']}" + (f" - {f['detail']}" if f["detail"] else ""))

    out.append("")
    out.append("=== NEWS ===")
    for symbol, articles in context["news"].items():
        read = sum(1 for x in articles if _has_content(x))
        out.append(f"{symbol} ({read}/{len(articles)} read in full):")
        for i, article in enumerate(articles, 1):
            meta = ", ".join(p for p in (article.get("publisher"), article.get("date")) if p and p != "Unknown date")
            out.append(f"[{symbol}-{i}] {article['title']}" + (f" ({meta})" if meta else ""))
            if _has_content(article):
                out.append(f"    {article['full_content'][:450].strip()}...")
            elif article.get("summary"):
                out.append(f"    {article['summary'][:300]}")
    if context["world_news"]:
        out.append("World financial news:")
        for i, article in enumerate(context["world_news"], 1):
            out.append(f"[WORLD-{i}] {article['title']}")
            if _has_content(article):
                out.append(f"    {article['full_content'][:300].strip()}...")

    out.append("")
    out.append("=== TASK ===")
    out.append(TASK)
    return "\n".join(out)


def build_sources(context):
    """Every article the model could cite, with its citation tag and URL."""
    sources = []
    for symbol, articles in context["news"].items():
        for i, article in enumerate(articles, 1):
            sources.append({"tag": f"{symbol}-{i}", "read": _has_content(article), **article})
    for i, article in enumerate(context["world_news"], 1):
        sources.append({"tag": f"WORLD-{i}", "read": _has_content(article), **article})
    return sources


# ---------------------------------------------------------------------------
# Model calls
# ---------------------------------------------------------------------------


def _request_options(provider, model):
    reasoning = model.lower().startswith(REASONING_PREFIXES)
    limit_key = "max_completion_tokens" if provider == "openai" else "max_tokens"
    options = {limit_key: 16000 if reasoning else 4000}
    if not reasoning:
        options["temperature"] = 0.4
    return options


def stream_chat(messages, on_text=None):
    """
    Stream a chat completion from the configured provider.

    Args:
        on_text: called with the full text so far as it arrives
    Returns:
        (text, usage) - usage is {"input": n, "output": n} or None
    Raises:
        RuntimeError with a readable message on any failure
    """
    import openai

    provider = ai_provider()
    preset = PROVIDERS[provider]
    model = ai_model(provider)
    key = ai_key(provider)
    if not key:
        raise RuntimeError(f"No {preset['label']} API key set - add one in Settings.")

    client = openai.OpenAI(api_key=key, base_url=preset["base_url"], timeout=300)
    parts, usage = [], None
    try:
        stream = client.chat.completions.create(
            model=model,
            messages=messages,
            stream=True,
            stream_options={"include_usage": True},
            **_request_options(provider, model),
        )
        for chunk in stream:
            if getattr(chunk, "usage", None):
                usage = {"input": chunk.usage.prompt_tokens, "output": chunk.usage.completion_tokens}
            if chunk.choices:
                text = getattr(chunk.choices[0].delta, "content", None)
                if text:
                    parts.append(text)
                    if on_text:
                        on_text("".join(parts))
    except openai.AuthenticationError as e:
        raise RuntimeError(f"{preset['label']} rejected the API key - update it in Settings.") from e
    except openai.NotFoundError as e:
        raise RuntimeError(f"Model '{model}' isn't available on {preset['label']} - pick another in Settings.") from e
    except openai.RateLimitError as e:
        raise RuntimeError(f"{preset['label']} rate limit or quota reached - check your account balance.") from e
    except openai.APIConnectionError as e:
        raise RuntimeError(f"Couldn't reach {preset['label']} - check your internet connection.") from e
    except openai.APIError as e:
        raise RuntimeError(f"{preset['label']} request failed: {e}") from e

    text = "".join(parts).strip()
    if not text:
        raise RuntimeError("The model returned an empty response - try again.")
    return text, usage


def write_report(context, on_text=None):
    """Stream the review. Returns a result dict usable for follow-ups and saving."""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": context["prompt"]},
    ]
    advice, usage = stream_chat(messages, on_text)
    messages.append({"role": "assistant", "content": advice})
    provider = ai_provider()
    return {
        "advice": advice,
        "sources": context["sources"],
        "findings": context["analysis"]["findings"],
        "provider": PROVIDERS[provider]["label"],
        "model": ai_model(provider),
        "usage": [usage] if usage else [],
        "messages": messages,
        "followups": [],
        "created": datetime.now(),
        "path": None,
    }


def ask_followup(result, question, on_text=None):
    """Continue the conversation with the full data pack and review in context."""
    messages = result["messages"] + [{"role": "user", "content": question}]
    answer, usage = stream_chat(messages, on_text)
    result["messages"] = messages + [{"role": "assistant", "content": answer}]
    result["followups"].append((question, answer))
    if usage:
        result["usage"].append(usage)
    return answer


def total_usage(result):
    return {
        "input": sum(u["input"] for u in result["usage"]),
        "output": sum(u["output"] for u in result["usage"]),
    }


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


def save_report(result):
    """Save (or re-save, after follow-ups) the review as Markdown. Returns the path."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    created = result["created"]
    path = result.get("path") or REPORTS_DIR / f"{created:%Y-%m-%d_%H%M%S}.md"
    lines = [
        f"# ezstox AI analysis - {created:%d %b %Y, %H:%M}",
        "",
        f"_{result['provider']} · {result['model']}_",
        "",
        result["advice"],
    ]
    for question, answer in result["followups"]:
        lines += ["", f"## Follow-up: {question}", "", answer]
    if result.get("findings"):
        lines += ["", "## App findings", ""]
        lines += [f"- **{f['level'].upper()}** {f['title']}" + (f" - {f['detail']}" if f["detail"] else "") for f in result["findings"]]
    lines += ["", "## Sources", ""]
    for source in result["sources"]:
        url = source.get("url")
        title = source.get("title", "No title")
        link = f"[{title}]({url})" if url and url != "No link available" else title
        lines.append(f"- **[{source['tag']}]** {link}")
    path.write_text("\n".join(lines) + "\n")
    result["path"] = path
    return path


def list_reports():
    """Saved reports, newest first."""
    if not REPORTS_DIR.exists():
        return []
    return sorted(REPORTS_DIR.glob("*.md"), reverse=True)
