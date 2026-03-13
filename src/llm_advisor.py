"""
LLM Advisor Module - AI-powered portfolio analysis
Uses OpenAI GPT-4o to provide investment recommendations
Optimized with fundamentals data + improved article scraping
"""

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

import feedparser
import requests
import yfinance as yf
from fredapi import Fred
from newspaper import Article
from openai import OpenAI

from src.data_fetcher import get_multiple_prices, get_stock_news


def scrape_with_jina(url, max_chars=3000, timeout=20):
    """Try scraping with Jina AI"""
    try:
        jina_url = f"https://r.jina.ai/{url}"
        headers = {"X-Return-Format": "text"}
        response = requests.get(jina_url, headers=headers, timeout=timeout)

        if response.status_code == 200:
            content = response.text[:max_chars]
            if len(content) > 100:
                return content
        return None
    except Exception:
        return None


def scrape_with_newspaper(url, max_chars=3000):
    """Fallback scraper using newspaper3k"""
    try:
        article = Article(url)
        article.download()
        article.parse()

        if article.text and len(article.text) > 100:
            return article.text[:max_chars]
        return None
    except Exception:
        return None


def get_article_content(url, max_chars=3000, retries=2):
    """Hybrid scraper: tries Jina first, then newspaper3k"""
    if not url or url == "No link available":
        return None

    # Try Jina first (fast)
    for attempt in range(retries):
        content = scrape_with_jina(url, max_chars)
        if content:
            return content
        if attempt < retries - 1:
            time.sleep(1)  # Balanced delay for better quality

    # Fallback to newspaper3k (reliable)
    for attempt in range(retries):
        content = scrape_with_newspaper(url, max_chars)
        if content:
            return content
        if attempt < retries - 1:
            time.sleep(0.5)  # Balanced delay

    return None


def scrape_article_parallel(article, retries=2):
    """Helper function to scrape a single article (for parallel execution)"""
    url = article.get("url")
    if url and url != "No link available":
        content = get_article_content(url, retries=retries)
        article["full_content"] = content if content else "Content unavailable"
        return article, bool(content)
    else:
        article["full_content"] = "No URL available"
        return article, False


def scrape_articles_parallel(articles, max_workers=5, retries=2):
    """
    Scrape multiple articles in parallel using ThreadPoolExecutor

    Args:
        articles: List of article dicts
        max_workers: Max concurrent threads
        retries: Number of retries per article

    Returns:
        tuple: (updated_articles, scraped_count)
    """
    scraped_count = 0

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all scraping tasks
        future_to_article = {
            executor.submit(scrape_article_parallel, article, retries): article
            for article in articles
        }

        # Collect results as they complete
        for future in as_completed(future_to_article):
            try:
                article, success = future.result()
                if success:
                    scraped_count += 1
            except Exception as e:
                # If scraping fails, mark as unavailable
                article = future_to_article[future]
                article["full_content"] = "Content unavailable"

    return articles, scraped_count


def get_stock_fundamentals(symbol):
    """
    Get key financial metrics using yfinance directly

    Args:
        symbol (str): Stock ticker

    Returns:
        dict: Financial metrics or empty dict if unavailable
    """
    fundamentals = {}

    try:
        # Use yfinance directly for better reliability
        ticker = yf.Ticker(symbol)
        info = ticker.info

        # Asset type detection (EQUITY, ETF, MUTUALFUND, etc.)
        fundamentals["quote_type"] = info.get("quoteType", "EQUITY")
        fundamentals["long_name"] = info.get("longName", symbol)

        # Extract available metrics with proper field names
        fundamentals["pe_ratio"] = info.get("trailingPE")
        fundamentals["forward_pe"] = info.get("forwardPE")
        fundamentals["market_cap"] = info.get("marketCap")
        fundamentals["beta"] = info.get("beta")
        fundamentals["dividend_yield"] = info.get("dividendYield")

        # 52-week range
        fundamentals["fifty_two_week_high"] = info.get("fiftyTwoWeekHigh")
        fundamentals["fifty_two_week_low"] = info.get("fiftyTwoWeekLow")

        # Analyst targets (for equities)
        fundamentals["analyst_target_mean"] = info.get("targetMeanPrice")
        fundamentals["analyst_target_high"] = info.get("targetHighPrice")
        fundamentals["analyst_target_low"] = info.get("targetLowPrice")

        # Additional useful metrics
        fundamentals["price_to_book"] = info.get("priceToBook")
        fundamentals["revenue_growth"] = info.get("revenueGrowth")
        fundamentals["earnings_growth"] = info.get("earningsGrowth")

        # ETF-specific metrics
        if fundamentals["quote_type"] == "ETF":
            fundamentals["category"] = info.get("category")
            fundamentals["total_assets"] = info.get("totalAssets")

    except Exception as e:
        print(f"  Warning: Could not fetch fundamentals for {symbol}: {str(e)[:50]}")

    return fundamentals


def validate_percentage_change(change_pct, timeframe="5 days"):
    """
    Validate if a percentage change is realistic

    Args:
        change_pct (float): Percentage change
        timeframe (str): Time period

    Returns:
        tuple: (is_valid, validated_value)
    """
    if abs(change_pct) > 10:
        print(
            f"  WARNING: Suspicious data: {change_pct:.2f}% change in {timeframe} (likely data error)"
        )
        return False, None

    return True, change_pct


def get_financial_news_rss(limit=10):
    """
    Get financial news from Google News RSS (free, no API key)

    Args:
        limit (int): Number of articles to fetch

    Returns:
        list: News articles with title, url, date
    """
    try:
        rss_url = "https://news.google.com/rss/search?q=stock+market+finance+economy+when:2d&hl=en-US&gl=US&ceid=US:en"

        feed = feedparser.parse(rss_url)
        articles = []

        for entry in feed.entries[:limit]:
            articles.append(
                {
                    "title": entry.title,
                    "url": entry.link,
                    "date": (
                        entry.published if hasattr(entry, "published") else "Recent"
                    ),
                }
            )

        return articles

    except Exception as e:
        print(f"  WARNING: Error fetching RSS news: {e}")
        return []


def get_vix_from_fred(fred_api_key=None):
    """
    Get VIX data from FRED API (free with API key)

    Args:
        fred_api_key (str): FRED API key

    Returns:
        dict: VIX level and sentiment
    """
    if not fred_api_key:
        fred_api_key = os.getenv("FRED_API_KEY")

    if not fred_api_key:
        print("  WARNING: FRED API key not found (VIX unavailable)")
        return {"level": "N/A", "sentiment": "N/A"}

    try:
        fred = Fred(api_key=fred_api_key)
        vix_series = fred.get_series(
            "VIXCLS", observation_start=datetime.now() - timedelta(days=7)
        )

        if len(vix_series) > 0:
            vix_level = round(vix_series.iloc[-1], 2)

            if vix_level < 15:
                vix_sentiment = "Low (Calm market)"
            elif vix_level < 25:
                vix_sentiment = "Normal"
            else:
                vix_sentiment = "High (Fear/Uncertainty)"

            return {"level": vix_level, "sentiment": vix_sentiment}
        else:
            return {"level": "N/A", "sentiment": "N/A"}

    except Exception as e:
        print(f"  WARNING: Error fetching VIX from FRED: {e}")
        return {"level": "N/A", "sentiment": "N/A"}


def fetch_index_data(symbol, name, start_date, end_date):
    """Helper function to fetch a single index (for parallel execution)"""
    from openbb import obb

    try:
        data = obb.equity.price.historical(
            symbol,
            start_date=start_date.strftime("%Y-%m-%d"),
            end_date=end_date.strftime("%Y-%m-%d"),
        )
        df = data.to_df()

        if len(df) >= 2:
            current = df["close"].iloc[-1]
            week_ago = df["close"].iloc[0]
            change_pct = ((current - week_ago) / week_ago) * 100

            is_valid, validated_change = validate_percentage_change(
                change_pct, "5 days"
            )

            if is_valid:
                return symbol, {
                    "name": name,
                    "current": round(current, 2),
                    "change_5d": round(validated_change, 2),
                }
            else:
                return symbol, {
                    "name": name,
                    "current": round(current, 2),
                    "change_5d": "Data Error",
                }
        else:
            return symbol, {"name": name, "current": "N/A", "change_5d": "N/A"}

    except Exception as e:
        print(f"    WARNING: {symbol} fetch failed: {e}")
        return symbol, {"name": name, "current": "N/A", "change_5d": "N/A"}


def get_macro_data(fred_api_key=None):
    """
    Gather broad market context data
    OPTIMIZED: Uses parallel processing for indices and article scraping

    Args:
        fred_api_key (str): FRED API key for VIX data

    Returns:
        dict: Market indices, news, and volatility data
    """
    from openbb import obb

    print("\n[MACRO DATA] Fetching market context...")

    macro = {}

    # 1. Financial news from Google RSS with parallel scraping
    print("  - Fetching global financial news (RSS)...")
    try:
        articles = get_financial_news_rss(limit=10)

        print("    - Scraping top articles (parallel)...")
        articles_to_scrape = articles[:5]

        # Parallel scrape with moderate workers for quality
        articles_to_scrape, scraped_count = scrape_articles_parallel(
            articles_to_scrape, max_workers=3, retries=2
        )

        # Update original articles list
        for i in range(len(articles_to_scrape)):
            articles[i] = articles_to_scrape[i]

        macro["world_news"] = articles
        print(f"    [OK] Retrieved {len(articles)} articles ({scraped_count} scraped)")
    except Exception as e:
        print(f"    WARNING: Error fetching news: {e}")
        macro["world_news"] = []

    # 2. Market indices (parallel fetching)
    print("  - Fetching market indices (parallel)...")
    indices = {}

    end_date = datetime.now()
    start_date = end_date - timedelta(days=7)

    index_list = [("SPY", "S&P 500"), ("QQQ", "Nasdaq"), ("DIA", "Dow Jones")]

    with ThreadPoolExecutor(max_workers=3) as executor:
        future_to_index = {
            executor.submit(fetch_index_data, symbol, name, start_date, end_date): symbol
            for symbol, name in index_list
        }

        for future in as_completed(future_to_index):
            try:
                symbol, data = future.result()
                indices[symbol] = data
                print(f"    {symbol}... OK")
            except Exception as e:
                symbol = future_to_index[future]
                print(f"    {symbol}... ERROR")
                indices[symbol] = {"name": symbol, "current": "N/A", "change_5d": "N/A"}

    macro["indices"] = indices

    # 3. VIX (parallel with tech sector)
    print("  - Fetching VIX from FRED...")
    try:
        macro["vix"] = get_vix_from_fred(fred_api_key)
    except Exception as e:
        print(f"    WARNING: VIX fetch failed: {e}")
        macro["vix"] = {"level": "N/A", "sentiment": "N/A"}

    # 4. Tech sector
    print("  - Fetching tech sector data...")
    try:
        tech_data = obb.equity.price.historical(
            "XLK",
            start_date=start_date.strftime("%Y-%m-%d"),
            end_date=end_date.strftime("%Y-%m-%d"),
        )
        tech_df = tech_data.to_df()

        if len(tech_df) >= 2:
            current = tech_df["close"].iloc[-1]
            week_ago = tech_df["close"].iloc[0]
            change_pct = ((current - week_ago) / week_ago) * 100

            is_valid, validated_change = validate_percentage_change(
                change_pct, "5 days"
            )

            macro["tech_sector"] = {
                "current": round(current, 2),
                "change_5d": round(validated_change, 2) if is_valid else "Data Error",
            }
        else:
            macro["tech_sector"] = {"current": "N/A", "change_5d": "N/A"}

        tech_news = obb.news.company("XLK", limit=3)
        tech_news_df = tech_news.to_df()

        tech_articles = []
        for _, row in tech_news_df.iterrows():
            tech_articles.append({"title": row.get("title", "No title")})

        macro["tech_sector"]["news"] = tech_articles

    except Exception as e:
        print(f"    WARNING: Tech sector fetch failed: {e}")
        macro["tech_sector"] = {"current": "N/A", "change_5d": "N/A", "news": []}

    print("[OK] Macro context gathered\n")
    return macro


def gather_stock_data(portfolio):
    """
    Gather stock data: prices, news (5 articles), and fundamentals
    OPTIMIZED: Uses parallel processing for fundamentals and article scraping

    Args:
        portfolio: Portfolio object

    Returns:
        dict: Prices, news, and fundamentals
    """
    print("[STOCK DATA] Gathering stock-specific data...")

    owned = portfolio.get_portfolio_symbols()
    watched = portfolio.get_watchlist_symbols()
    # Remove duplicates (stocks in both owned and watched)
    all_symbols = list(dict.fromkeys(owned + watched))

    # Get current prices
    print("\n[PRICES] Fetching current prices...")
    prices = get_multiple_prices(all_symbols)

    # Get fundamentals in parallel
    print("\n[FUNDAMENTALS] Fetching fundamentals (parallel)...")
    fundamentals_data = {}

    with ThreadPoolExecutor(max_workers=5) as executor:
        future_to_symbol = {
            executor.submit(get_stock_fundamentals, symbol): symbol
            for symbol in all_symbols
        }

        for future in as_completed(future_to_symbol):
            symbol = future_to_symbol[future]
            try:
                fundamentals_data[symbol] = future.result()
                print(f"  {symbol}... OK")
            except Exception as e:
                print(f"  {symbol}... ERROR: {str(e)[:30]}")
                fundamentals_data[symbol] = {}

    # Get news with parallel scraping
    print("\n[NEWS] Fetching news + scraping articles (parallel)...")
    news_data = {}

    for symbol in all_symbols:
        print(f"  {symbol}...", end=" ", flush=True)

        quote_type = fundamentals_data[symbol].get("quote_type", "EQUITY")

        # ETFs need less news - they track indices/commodities
        if quote_type == "ETF":
            # Get only 2 articles for ETFs (lighter scraping)
            articles = get_stock_news(symbol, limit=2)
            articles_to_scrape = articles[:2]

            # Parallel scrape with conservative workers for quality
            articles_to_scrape, scraped_count = scrape_articles_parallel(
                articles_to_scrape, max_workers=2, retries=2
            )

            news_data[symbol] = articles_to_scrape
            print(f"{scraped_count}/2 articles [ETF]")
        else:
            # Full scraping for equities
            articles = get_stock_news(symbol, limit=8)
            articles_to_scrape = articles[:5]

            # Parallel scrape with moderate workers to balance speed and quality
            articles_to_scrape, scraped_count = scrape_articles_parallel(
                articles_to_scrape, max_workers=3, retries=2
            )

            news_data[symbol] = articles_to_scrape
            print(f"{scraped_count}/5 articles")

        # Small delay between stocks to avoid overwhelming Jina API
        time.sleep(0.5)

    print("\n[OK] Stock data gathered\n")

    return {"prices": prices, "news": news_data, "fundamentals": fundamentals_data}


def format_llm_prompt(portfolio, stock_data, macro_data):
    """
    Format FT-style prompt with fundamentals and data quality tracking
    """
    owned = portfolio.get_portfolio_symbols()
    watched = portfolio.get_watchlist_symbols()
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

    # Holdings with fundamentals
    if owned:
        for symbol in owned:
            holding = portfolio.holdings[symbol]
            current_price = prices.get(symbol, 0)
            total_cost = holding["shares"] * holding["cost_basis"]
            current_value = holding["shares"] * current_price
            gain_loss = current_value - total_cost
            gain_loss_pct = (gain_loss / total_cost * 100) if total_cost > 0 else 0

            # Get fundamentals and asset type
            fund = fundamentals.get(symbol, {})
            quote_type = fund.get("quote_type", "EQUITY")
            long_name = fund.get("long_name", symbol)

            # Show asset type in header
            type_label = f"[{quote_type}]" if quote_type != "EQUITY" else ""

            prompt += f"\n{symbol} {type_label}: {holding['shares']} sh @ ${holding['cost_basis']:.2f} -> ${current_price:.2f} | P&L: ${gain_loss:.2f} ({gain_loss_pct:+.1f}%)\n"
            prompt += f"  {long_name}\n"

            # Conditional metrics based on asset type
            if quote_type == "EQUITY":
                # P/E ratio with explicit None handling
                pe_val = fund.get("pe_ratio")
                if pe_val is not None:
                    prompt += f"  P/E: {pe_val:.2f}"
                else:
                    prompt += f"  P/E: N/A"

                # Analyst target with explicit None handling
                target_val = fund.get("analyst_target_mean")
                if target_val is not None and target_val > 0:
                    upside = (
                        ((target_val - current_price) / current_price * 100)
                        if current_price > 0
                        else 0
                    )
                    prompt += f" | Analyst Target: ${target_val:.2f} ({upside:+.1f}%)"
                else:
                    prompt += f" | Analyst Target: N/A"

                # 52-week range
                high_val = fund.get("fifty_two_week_high")
                low_val = fund.get("fifty_two_week_low")
                if high_val is not None and low_val is not None:
                    prompt += f" | 52w: ${low_val:.2f}-${high_val:.2f}"

            elif quote_type == "ETF":
                # ETF-specific metrics
                high_val = fund.get("fifty_two_week_high")
                low_val = fund.get("fifty_two_week_low")
                if high_val is not None and low_val is not None:
                    prompt += f"  52w Range: ${low_val:.2f}-${high_val:.2f}"

                category = fund.get("category")
                if category:
                    prompt += f" | Category: {category}"

            else:
                # Commodities, currencies, etc - just show 52-week range
                high_val = fund.get("fifty_two_week_high")
                low_val = fund.get("fifty_two_week_low")
                if high_val is not None and low_val is not None:
                    prompt += f"  52w Range: ${low_val:.2f}-${high_val:.2f}"

            prompt += "\n"

    total_value = portfolio.cash + sum(
        portfolio.holdings[s]["shares"] * prices.get(s, 0) for s in owned
    )
    cash_pct = (portfolio.cash / total_value * 100) if total_value > 0 else 100

    prompt += f"\nCASH: ${portfolio.cash:,.0f} ({cash_pct:.1f}%)\nTOTAL: ${total_value:,.0f}\n"

    # Watchlist
    if watched:
        prompt += "\nWATCHLIST:\n"
        for symbol in watched:
            price = prices.get(symbol, 0)
            fund = fundamentals.get(symbol, {})
            quote_type = fund.get("quote_type", "EQUITY")
            long_name = fund.get("long_name", symbol)

            type_label = f"[{quote_type}]" if quote_type != "EQUITY" else ""
            prompt += f"{symbol} {type_label}: ${price}\n"
            prompt += f"  {long_name}\n"

            # Conditional metrics based on asset type
            if quote_type == "EQUITY":
                # P/E ratio
                pe_val = fund.get("pe_ratio")
                if pe_val is not None:
                    prompt += f"  P/E: {pe_val:.2f}"
                else:
                    prompt += f"  P/E: N/A"

                # Analyst target
                target_val = fund.get("analyst_target_mean")
                if target_val is not None and target_val > 0:
                    upside = ((target_val - price) / price * 100) if price > 0 else 0
                    prompt += f" | Target: ${target_val:.2f} ({upside:+.1f}%)"
                else:
                    prompt += f" | Target: N/A"

            elif quote_type == "ETF":
                # ETF metrics
                category = fund.get("category")
                if category:
                    prompt += f"  Category: {category}"

            prompt += "\n"

    # Macro
    prompt += "\n=== MARKET CONTEXT ===\n"
    for symbol, data in macro_data.get("indices", {}).items():
        change = data.get("change_5d", "N/A")
        if change not in ["N/A", "Data Error"]:
            prompt += f"{data['name']}: {change:+.1f}% (5d)\n"

    vix = macro_data.get("vix", {})
    if vix.get("level") != "N/A":
        prompt += f"VIX: {vix['level']} ({vix['sentiment']})\n"

    # Stock news with data quality
    prompt += "\n=== STOCK INTELLIGENCE ===\n"

    for symbol in owned + watched:
        stock_news = news.get(symbol, [])
        fund = fundamentals.get(symbol, {})
        quote_type = fund.get("quote_type", "EQUITY")

        available_count = sum(
            1
            for a in stock_news
            if a.get("full_content")
            not in [None, "Content unavailable", "No URL available"]
        )

        # Different quality thresholds for different asset types
        if quote_type == "EQUITY":
            quality = (
                "[GOOD]" if available_count >= 3 else "[LIMITED]" if available_count >= 1 else "[INSUFFICIENT]"
            )
            expected = 5
        else:
            # ETFs need less data - use macro trends instead
            quality = "[ETF - Use Macro Data]"
            expected = 2

        prompt += f"\n{symbol} {quality} ({available_count}/{expected} articles):\n"

        if available_count > 0:
            for i, article in enumerate(stock_news, 1):
                prompt += f"[{symbol}-{i}] {article['title']}\n"
                content = article.get("full_content", "")
                if content and content not in [
                    "Content unavailable",
                    "No URL available",
                ]:
                    prompt += f"    {content[:400]}...\n"

        # Special handling for ETFs - guide AI on what to analyze
        if quote_type == "ETF":
            long_name = fund.get("long_name", "")
            if "S&P 500" in long_name or "VUSA" in symbol:
                prompt += "  [ANALYSIS GUIDE] Track S&P 500 index (see Market Context above)\n"
            elif "Gold" in long_name or "GLD" in symbol or "IGLN" in symbol:
                prompt += "  [ANALYSIS GUIDE] Track gold prices - inflation hedge, safe haven demand\n"
            elif "Silver" in long_name or "SLV" in symbol:
                prompt += "  [ANALYSIS GUIDE] Track silver prices - industrial demand + precious metal\n"
        elif quote_type == "EQUITY" and available_count == 0:
            prompt += "  [INSUFFICIENT DATA] - Cannot analyze\n"

    # Instructions
    prompt += """

=== YOUR ANALYSIS ===

Format (max 2,000 words total):

I. MARKET OVERVIEW (100-150 words)
II. HOLDINGS: For each stock - Hold/Add/Trim? Why? (150-200 words each)
III. WATCHLIST: Top 2-3 picks only - Buy/Pass? Why? (150-200 words each)
IV. ACTIONS: Top 3-5 specific moves this week

FORMATTING REQUIREMENTS:
- Start each stock analysis with key metrics on separate lines:
  Current Price: $X.XX
  P/E: X.XX (for equities only, omit for ETFs)
  Analyst Target: $X.XX (for equities only, omit for ETFs or if N/A)
- Then provide detailed analysis paragraph
- End with: Conviction: X/10 with clear reasoning
- Use proper spacing and line breaks for readability

CONTENT REQUIREMENTS:
- Cite articles using the format [SYMBOL-#] (e.g., [CEG-1], [AAPL-2])
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


def call_openai(prompt, api_key=None):
    """Send prompt to OpenAI GPT-4o-mini"""
    if api_key is None:
        api_key = os.getenv("OPENAI_API_KEY")

    if not api_key:
        return "ERROR: OpenAI API key not found!"

    try:
        client = OpenAI(api_key=api_key)

        print("[AI] Sending to OpenAI GPT-4o-mini...\n")

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": "You are an expert financial advisor. Provide clear, specific, evidence-based investment advice with detailed analysis. Always cite your sources using exact article references. Never make up data. Format responses with clear structure and comprehensive reasoning.",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.6,
            max_tokens=2500,
        )

        return response.choices[0].message.content

    except Exception as e:
        return f"ERROR: Failed to call OpenAI API: {str(e)}"


def get_ai_advice(portfolio, openai_key=None, fred_key=None):
    """
    Main function: Get complete AI investment advice

    Args:
        portfolio: Portfolio object
        openai_key (str): Optional OpenAI API key
        fred_key (str): Optional FRED API key

    Returns:
        str: Formatted AI advice
    """
    print("\n" + "=" * 60)
    print("                  AI Financial Advisor")
    print("=" * 60)

    # Gather all data
    stock_data = gather_stock_data(portfolio)
    macro_data = get_macro_data(fred_key)

    # Format prompt
    print("[ANALYSIS] Formatting prompt...\n")
    prompt = format_llm_prompt(portfolio, stock_data, macro_data)

    # Get AI response
    advice = call_openai(prompt, openai_key)

    print("=" * 60)
    print("                  Analysis Complete")
    print("=" * 60)
    print()

    # Return the advice (caller will print it)
    if not advice or len(advice) == 0:
        print("WARNING: No advice generated")
        return None

    # Debug: Show article quality per stock
    print("\n" + "=" * 60)
    print("               Article Scraping Summary")
    print("=" * 60)

    owned = portfolio.get_portfolio_symbols()
    watched = portfolio.get_watchlist_symbols()
    # Remove duplicates (stocks in both owned and watched)
    all_symbols = list(dict.fromkeys(owned + watched))

    for symbol in all_symbols:
        stock_news = stock_data['news'].get(symbol, [])
        fund = stock_data['fundamentals'].get(symbol, {})
        quote_type = fund.get("quote_type", "EQUITY")

        scraped_count = sum(
            1 for a in stock_news
            if a.get("full_content") not in [None, "Content unavailable", "No URL available"]
        )

        type_label = f" [{quote_type}]" if quote_type != "EQUITY" else ""
        print(f"\n{symbol}{type_label}: {scraped_count}/{len(stock_news)} articles scraped")

        for i, article in enumerate(stock_news, 1):
            has_content = article.get("full_content") not in [None, "Content unavailable", "No URL available"]
            status = "[OK]" if has_content else "[--]"
            print(f"  [{symbol}-{i}] {status} {article.get('title', 'No title')[:60]}...")

    return advice
