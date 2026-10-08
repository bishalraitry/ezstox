"""
LLM Advisor Module - AI-powered portfolio analysis

Pipeline:
  1. Gather   - quotes, fundamentals and headlines for every symbol, plus
                market context (indices, VIX, tech sector, world news),
                all fetched concurrently.
  2. Scrape   - full article text for the most relevant headlines, in
                parallel (Jina Reader first, trafilatura as fallback).
  3. Analyse  - one structured prompt to OpenAI.
  4. Sources  - a numbered source list with real URLs is appended by us,
                not the model, so links can't be hallucinated.

Progress is reported through an optional `progress` object (see
NullProgress) so the UI can show live status without this module printing.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import requests

from src.config import REPORTS_DIR, get_setting, openai_model
from src.data_fetcher import get_fundamentals_for, get_news_for, get_quotes, get_stock_news

SCRAPE_WORKERS = 8
SCRAPE_TIMEOUT = 15
MAX_ARTICLE_CHARS = 3000
EQUITY_ARTICLES = 5
ETF_ARTICLES = 2
WORLD_NEWS_ARTICLES = 5

INDICES = [("SPY", "S&P 500"), ("QQQ", "Nasdaq"), ("DIA", "Dow Jones")]
TECH_SECTOR = "XLK"
VIX = "^VIX"

UNAVAILABLE = (None, "Content unavailable", "No URL available")
BROWSER_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) ezstox"}


class NullProgress:
    """Progress sink that ignores everything (used when no UI is attached)."""

    def stage(self, key, description, total=None):
        pass

    def advance(self, key, amount=1):
        pass

    def done(self, key, note=""):
        pass


# ---------------------------------------------------------------------------
# Article scraping
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


# ---------------------------------------------------------------------------
# Market context
# ---------------------------------------------------------------------------


def validate_percentage_change(change_pct):
    """A >10% move in an index over 5 days is almost certainly bad data."""
    if change_pct is None or abs(change_pct) > 10:
        return None
    return change_pct


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


def build_macro(quotes, world_news, tech_news):
    def index_entry(symbol, name):
        quote = quotes.get(symbol)
        if not quote:
            return {"name": name, "current": "N/A", "change_5d": "N/A"}
        change = validate_percentage_change(quote["change_5d_pct"])
        return {
            "name": name,
            "current": quote["price"],
            "change_5d": round(change, 2) if change is not None else "Data Error",
        }

    vix_quote = quotes.get(VIX)
    if vix_quote:
        level = vix_quote["price"]
        sentiment = (
            "Low (Calm market)" if level < 15 else "Normal" if level < 25 else "High (Fear/Uncertainty)"
        )
        vix = {"level": level, "sentiment": sentiment}
    else:
        vix = {"level": "N/A", "sentiment": "N/A"}

    tech = index_entry(TECH_SECTOR, "Tech sector (XLK)")
    tech["news"] = [{"title": a["title"]} for a in tech_news]

    return {
        "indices": {symbol: index_entry(symbol, name) for symbol, name in INDICES},
        "vix": vix,
        "tech_sector": tech,
        "world_news": world_news,
    }


# ---------------------------------------------------------------------------
# Data gathering
# ---------------------------------------------------------------------------


def gather_data(portfolio, progress):
    """Fetch everything the analysis needs, as concurrently as possible."""
    symbols = portfolio.get_all_symbols()
    context_symbols = [s for s, _ in INDICES] + [TECH_SECTOR, VIX]

    progress.stage("gather", "Prices, fundamentals & headlines")
    with ThreadPoolExecutor(max_workers=5) as pool:
        f_quotes = pool.submit(get_quotes, symbols + context_symbols)
        f_fundamentals = pool.submit(get_fundamentals_for, symbols)
        f_news = pool.submit(get_news_for, symbols, 8)
        f_world = pool.submit(get_financial_news_rss, 10)
        f_tech = pool.submit(get_stock_news, TECH_SECTOR, 3)
        quotes = f_quotes.result()
        fundamentals = {s: f or {} for s, f in f_fundamentals.result().items()}
        all_news = f_news.result()
        world_news = f_world.result()
        tech_news = f_tech.result()

    prices = {s: quotes[s]["price"] for s in symbols if quotes.get(s)}
    missing = [s for s in symbols if s not in prices]
    note = f"{len(prices)}/{len(symbols)} priced" + (f" · missing {', '.join(missing)}" if missing else "")
    progress.done("gather", note)

    # Decide which articles are worth reading in full (ETFs need fewer -
    # they're analysed through their underlying index/commodity instead).
    news = {}
    to_scrape = []
    for symbol in symbols:
        is_etf = fundamentals[symbol].get("quote_type") == "ETF"
        picked = [dict(a) for a in all_news.get(symbol, [])[: ETF_ARTICLES if is_etf else EQUITY_ARTICLES]]
        news[symbol] = picked
        to_scrape.extend(picked)
    world_news = [dict(a) for a in world_news]
    to_scrape.extend(world_news[:WORLD_NEWS_ARTICLES])

    progress.stage("scrape", "Reading full articles", total=len(to_scrape))
    scraped = scrape_articles(to_scrape, progress)
    progress.done("scrape", f"{scraped}/{len(to_scrape)} articles read")

    stock_data = {"prices": prices, "news": news, "fundamentals": fundamentals}
    return stock_data, build_macro(quotes, world_news, tech_news)


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------


def _has_content(article):
    return article.get("full_content") not in UNAVAILABLE


def format_llm_prompt(portfolio, stock_data, macro_data):
    """
    Format FT-style prompt with fundamentals and data quality tracking
    """
    owned = portfolio.get_portfolio_symbols()
    watched = [s for s in portfolio.get_watchlist_symbols() if s not in owned]
    prices = stock_data["prices"]
    news = stock_data["news"]
    fundamentals = stock_data.get("fundamentals", {})

    prompt = f"""You are an expert financial analyst providing institutional-quality investment research.

CRITICAL RULES:

1. DATA SUFFICIENCY & ACCURACY:
   - EQUITIES with <3 of 5 articles: Mark "LIMITED DATA - Lower conviction"
   - EQUITIES with 0-1 articles + no fundamentals: "INSUFFICIENT DATA - CANNOT ANALYZE"
   - ETFs: Do NOT require 5 articles - analyze using macro data and underlying index/commodity trends
   - ONLY cite articles that match the stock symbol (e.g., only use [AAPL-#] articles for AAPL analysis)
   - NEVER cite articles about a different company
   - NEVER make recommendations without evidence
   - NEVER invent specific analyst targets or price numbers not in the data

2. PRIORITIZE FUNDAMENTALS:
   - Use EXACT P/E ratios from fundamentals data (do not round or estimate)
   - If fundamentals show "None" or missing data, state "P/E: N/A" explicitly
   - Analyst targets: ONLY cite if explicitly provided in fundamentals
   - If no analyst target is provided, say "No analyst target available"

3. CITE SOURCES ACCURATELY:
   - Use exact numbers from fundamentals data
   - Reference specific article claims: "Article [SYMBOL-#] reports..."
   - Never cite brokerages (JPMorgan, Goldman, etc.) unless explicitly mentioned in articles
   - If uncertain about a data point, acknowledge the limitation

4. CONCISE OUTPUT:
   - Max 1,500 words total
   - Each stock: 150 words max
   - Direct, actionable, no fluff

==================================================
ANALYSIS DATE: {datetime.now().strftime('%Y-%m-%d %H:%M')}
==================================================

=== PORTFOLIO ===

HOLDINGS:
"""

    for symbol in owned:
        holding = portfolio.holdings[symbol]
        current_price = prices.get(symbol)
        fund = fundamentals.get(symbol, {})
        quote_type = fund.get("quote_type", "EQUITY")
        type_label = f"[{quote_type}]" if quote_type != "EQUITY" else ""

        if current_price is None:
            prompt += f"\n{symbol} {type_label}: {holding['shares']:g} sh @ ${holding['cost_basis']:.2f} -> price unavailable\n"
        else:
            total_cost = holding["shares"] * holding["cost_basis"]
            gain_loss = holding["shares"] * current_price - total_cost
            gain_loss_pct = (gain_loss / total_cost * 100) if total_cost > 0 else 0
            prompt += (
                f"\n{symbol} {type_label}: {holding['shares']:g} sh @ ${holding['cost_basis']:.2f} "
                f"-> ${current_price:.2f} | P&L: ${gain_loss:.2f} ({gain_loss_pct:+.1f}%)\n"
            )
        prompt += f"  {fund.get('long_name', symbol)}\n"
        prompt += _metrics_line(fund, quote_type, current_price or 0, target_label="Analyst Target") + "\n"

    total_value = portfolio.cash + sum(
        portfolio.holdings[s]["shares"] * prices.get(s, 0) for s in owned
    )
    cash_pct = (portfolio.cash / total_value * 100) if total_value > 0 else 100
    prompt += f"\nCASH: ${portfolio.cash:,.0f} ({cash_pct:.1f}%)\nTOTAL: ${total_value:,.0f}\n"

    if watched:
        prompt += "\nWATCHLIST:\n"
        for symbol in watched:
            price = prices.get(symbol)
            fund = fundamentals.get(symbol, {})
            quote_type = fund.get("quote_type", "EQUITY")
            type_label = f"[{quote_type}]" if quote_type != "EQUITY" else ""
            price_text = f"${price:.2f}" if price is not None else "price unavailable"
            prompt += f"{symbol} {type_label}: {price_text}\n"
            prompt += f"  {fund.get('long_name', symbol)}\n"
            prompt += _metrics_line(fund, quote_type, price or 0, target_label="Target") + "\n"

    # Market context
    prompt += "\n=== MARKET CONTEXT ===\n"
    for data in macro_data.get("indices", {}).values():
        if isinstance(data.get("change_5d"), (int, float)):
            prompt += f"{data['name']}: {data['change_5d']:+.1f}% (5d)\n"
    tech = macro_data.get("tech_sector", {})
    if isinstance(tech.get("change_5d"), (int, float)):
        prompt += f"{tech['name']}: {tech['change_5d']:+.1f}% (5d)\n"
    vix = macro_data.get("vix", {})
    if vix.get("level") != "N/A":
        prompt += f"VIX: {vix['level']} ({vix['sentiment']})\n"

    world_news = macro_data.get("world_news", [])
    if world_news:
        prompt += "\nWORLD FINANCIAL NEWS (last 2 days):\n"
        for i, article in enumerate(world_news, 1):
            prompt += f"[WORLD-{i}] {article['title']}\n"
            if _has_content(article):
                prompt += f"    {article['full_content'][:300]}...\n"

    # Stock news with data quality
    prompt += "\n=== STOCK INTELLIGENCE ===\n"
    for symbol in owned + watched:
        stock_news = news.get(symbol, [])
        fund = fundamentals.get(symbol, {})
        quote_type = fund.get("quote_type", "EQUITY")
        available_count = sum(1 for a in stock_news if _has_content(a))

        if quote_type == "EQUITY":
            quality = (
                "[GOOD]" if available_count >= 3 else "[LIMITED]" if available_count >= 1 else "[INSUFFICIENT]"
            )
            expected = EQUITY_ARTICLES
        else:
            quality = "[ETF - Use Macro Data]"
            expected = ETF_ARTICLES

        prompt += f"\n{symbol} {quality} ({available_count}/{expected} articles):\n"

        if available_count > 0:
            for i, article in enumerate(stock_news, 1):
                prompt += f"[{symbol}-{i}] {article['title']}\n"
                if _has_content(article):
                    prompt += f"    {article['full_content'][:400]}...\n"

        if quote_type == "ETF":
            long_name = fund.get("long_name", "")
            if "S&P 500" in long_name or "VUSA" in symbol:
                prompt += "  [ANALYSIS GUIDE] Track S&P 500 index (see Market Context above)\n"
            elif "Gold" in long_name or "GLD" in symbol or "IGLN" in symbol:
                prompt += "  [ANALYSIS GUIDE] Track gold prices - inflation hedge, safe haven demand\n"
            elif "Silver" in long_name or "SLV" in symbol:
                prompt += "  [ANALYSIS GUIDE] Track silver prices - industrial demand + precious metal\n"
        elif available_count == 0:
            prompt += "  [INSUFFICIENT DATA] - Cannot analyze\n"

    prompt += """

=== YOUR ANALYSIS ===

Format as Markdown (max 2,000 words total):

## I. Market Overview (100-150 words)
## II. Holdings - for each stock: Hold/Add/Trim? Why? (150-200 words each, one ### heading per stock)
## III. Watchlist - top 2-3 picks only: Buy/Pass? Why? (150-200 words each, one ### heading per stock)
## IV. Actions - top 3-5 specific moves this week, as a numbered list

FORMATTING REQUIREMENTS:
- Start each stock analysis with key metrics as a short bullet list:
  - Current Price: $X.XX
  - P/E: X.XX (for equities only, omit for ETFs)
  - Analyst Target: $X.XX (for equities only, omit for ETFs or if N/A)
- Then provide a detailed analysis paragraph
- End with: **Conviction: X/10** with clear reasoning
- Do NOT add a sources or references section - one is appended automatically

CONTENT REQUIREMENTS:
- Cite articles using the format [SYMBOL-#] (e.g., [CEG-1], [AAPL-2]) or [WORLD-#] for world news
- ONLY reference articles that match the stock symbol you're analyzing
- Use EXACT P/E ratios from the data above - do not round or estimate
- ONLY cite analyst targets if explicitly shown as "Target: $X" above
- Do NOT invent brokerage names (JPMorgan, Goldman, etc.) unless mentioned in articles
- If data shows "N/A", explicitly state it in your analysis
- Quantify upside/downside conservatively based on fundamentals
- Provide specific reasoning with evidence from articles or fundamentals

CRITICAL - ASSET TYPE HANDLING:
- [EQUITY]: Analyze using P/E ratios, analyst targets, earnings growth, competitive positioning
  * Requires 3+ articles for full conviction
  * Include P/E ratio and analyst target in your metrics summary

- [ETF]: Analyze based on underlying index/sector performance, NOT individual company metrics
  * S&P 500 ETFs (VUSA): Analyze using S&P 500 performance from Market Context
  * Gold ETFs (GLD, IGLN.L): Analyze based on gold as inflation hedge, safe haven demand, macro trends
  * Silver ETFs (SLV): Analyze based on industrial demand + precious metal trends
  * CRITICAL: DO NOT mention "insufficient data" for ETFs - use macro context instead
  * CRITICAL: DO NOT discuss P/E ratios for ETFs - they don't have P/E ratios
  * CRITICAL: DO NOT discuss analyst price targets for ETFs - they track indices/commodities
  * Focus on: underlying asset performance, macro trends, diversification benefits

- [COMMODITY/CURRENCY]: Analyze based on macro trends, supply/demand, inflation hedging

- For ETFs marked "[ETF - Use Macro Data]", you have sufficient data to provide analysis using market indices, VIX, and macro trends
"""
    return prompt


def _metrics_line(fund, quote_type, price, target_label):
    """One line of key metrics, tailored to the asset type."""
    parts = []
    low, high = fund.get("fifty_two_week_low"), fund.get("fifty_two_week_high")
    if quote_type == "EQUITY":
        pe = fund.get("pe_ratio")
        parts.append(f"P/E: {pe:.2f}" if pe is not None else "P/E: N/A")
        target = fund.get("analyst_target_mean")
        if target:
            upside = ((target - price) / price * 100) if price > 0 else 0
            parts.append(f"{target_label}: ${target:.2f} ({upside:+.1f}%)")
        else:
            parts.append(f"{target_label}: N/A")
        if low is not None and high is not None:
            parts.append(f"52w: ${low:.2f}-${high:.2f}")
    else:
        if low is not None and high is not None:
            parts.append(f"52w Range: ${low:.2f}-${high:.2f}")
        if quote_type == "ETF" and fund.get("category"):
            parts.append(f"Category: {fund['category']}")
    return "  " + " | ".join(parts) if parts else ""


# ---------------------------------------------------------------------------
# Model call, sources, reports
# ---------------------------------------------------------------------------


def call_openai(prompt, model):
    """Send prompt to OpenAI. Raises RuntimeError with a readable message on failure."""
    api_key = get_setting("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("No OpenAI API key set - add one in Settings.")

    from openai import OpenAI

    try:
        response = OpenAI(api_key=api_key).chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": "You are an expert financial advisor. Provide clear, specific, evidence-based investment advice with detailed analysis. Always cite your sources using exact article references. Never make up data. Format responses as clean Markdown with clear structure and comprehensive reasoning.",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.6,
            max_tokens=2500,
        )
    except Exception as e:
        raise RuntimeError(f"OpenAI request failed: {e}") from e
    return (response.choices[0].message.content or "").strip()


def build_sources(portfolio, stock_data, macro_data):
    """Every article the model could cite, with its citation tag and URL."""
    sources = []
    for symbol in portfolio.get_all_symbols():
        for i, article in enumerate(stock_data["news"].get(symbol, []), 1):
            sources.append({"tag": f"{symbol}-{i}", "read": _has_content(article), **article})
    for i, article in enumerate(macro_data.get("world_news", []), 1):
        sources.append({"tag": f"WORLD-{i}", "read": _has_content(article), **article})
    return sources


def get_ai_advice(portfolio, progress=None):
    """
    Main function: Get complete AI investment advice

    Returns:
        dict: advice (Markdown), sources, model, stock_data, macro, created
    Raises:
        RuntimeError: if there's nothing to analyse or the model call fails
    """
    progress = progress or NullProgress()
    if portfolio.is_empty:
        raise RuntimeError("Your portfolio and watchlist are empty - add some symbols first.")

    stock_data, macro_data = gather_data(portfolio, progress)

    model = openai_model()
    progress.stage("ai", f"Analysing with {model}")
    advice = call_openai(format_llm_prompt(portfolio, stock_data, macro_data), model)
    if not advice:
        raise RuntimeError("The model returned an empty response - try again.")
    progress.done("ai")

    return {
        "advice": advice,
        "sources": build_sources(portfolio, stock_data, macro_data),
        "model": model,
        "stock_data": stock_data,
        "macro": macro_data,
        "created": datetime.now(),
    }


def save_report(result):
    """Save the report (with sources) as Markdown under reports/. Returns the path."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    created = result["created"]
    path = REPORTS_DIR / f"{created:%Y-%m-%d_%H%M%S}.md"
    lines = [
        f"# ezstox AI analysis - {created:%d %b %Y, %H:%M}",
        "",
        f"_Model: {result['model']}_",
        "",
        result["advice"],
        "",
        "## Sources",
        "",
    ]
    for source in result["sources"]:
        url = source.get("url")
        title = source.get("title", "No title")
        link = f"[{title}]({url})" if url and url != "No link available" else title
        lines.append(f"- **[{source['tag']}]** {link}")
    path.write_text("\n".join(lines) + "\n")
    return path


def list_reports():
    """Saved reports, newest first."""
    if not REPORTS_DIR.exists():
        return []
    return sorted(REPORTS_DIR.glob("*.md"), reverse=True)
