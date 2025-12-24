"""
LLM Advisor Module - AI-powered portfolio analysis
Uses OpenAI GPT-4o to provide investment recommendations
Optimized with fundamentals data + improved article scraping
"""

import os
import time
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
            time.sleep(2)

    # Fallback to newspaper3k (reliable)
    for attempt in range(retries):
        content = scrape_with_newspaper(url, max_chars)
        if content:
            return content
        if attempt < retries - 1:
            time.sleep(1)

    return None


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


def get_macro_data(fred_api_key=None):
    """
    Gather broad market context data

    Args:
        fred_api_key (str): FRED API key for VIX data

    Returns:
        dict: Market indices, news, and volatility data
    """
    from openbb import obb

    print("\n[MACRO DATA] Fetching market context...")

    macro = {}

    # 1. Financial news from Google RSS
    print("  - Fetching global financial news (RSS)...")
    try:
        articles = get_financial_news_rss(limit=10)

        print("    - Scraping top articles...")
        for article in articles[:5]:
            url = article.get("url")
            if url:
                content = get_article_content(url)
                article["content"] = content if content else "Content unavailable"
                time.sleep(1.5)

        macro["world_news"] = articles
        print(f"    [OK] Retrieved {len(articles)} financial news articles")
    except Exception as e:
        print(f"    WARNING: Error fetching news: {e}")
        macro["world_news"] = []

    # 2. Market indices
    print("  - Fetching market indices...")
    indices = {}

    end_date = datetime.now()
    start_date = end_date - timedelta(days=7)

    for symbol, name in [("SPY", "S&P 500"), ("QQQ", "Nasdaq"), ("DIA", "Dow Jones")]:
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
                    indices[symbol] = {
                        "name": name,
                        "current": round(current, 2),
                        "change_5d": round(validated_change, 2),
                    }
                else:
                    indices[symbol] = {
                        "name": name,
                        "current": round(current, 2),
                        "change_5d": "Data Error",
                    }
            else:
                indices[symbol] = {"name": name, "current": "N/A", "change_5d": "N/A"}

        except Exception as e:
            print(f"    WARNING: {symbol} fetch failed: {e}")
            indices[symbol] = {"name": name, "current": "N/A", "change_5d": "N/A"}

    macro["indices"] = indices

    # 3. VIX
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

    # Get fundamentals + news
    print("\n[FUNDAMENTALS] Fetching fundamentals + news...")
    news_data = {}
    fundamentals_data = {}

    for symbol in all_symbols:
        print(f"  {symbol}...", end=" ", flush=True)

        # Get fundamentals
        fundamentals_data[symbol] = get_stock_fundamentals(symbol)
        quote_type = fundamentals_data[symbol].get("quote_type", "EQUITY")

        # ETFs need less news - they track indices/commodities
        if quote_type == "ETF":
            # Get only 2 articles for ETFs (lighter scraping)
            articles = get_stock_news(symbol, limit=2)
            scraped_count = 0
            for i, article in enumerate(articles[:2], 1):
                url = article.get("url")
                if url and url != "No link available":
                    content = get_article_content(url, retries=1)  # Fewer retries
                    article["full_content"] = content if content else "Content unavailable"
                    if content:
                        scraped_count += 1
                    time.sleep(1.5)
                else:
                    article["full_content"] = "No URL available"
            news_data[symbol] = articles[:2]
            print(f"{scraped_count}/2 articles [ETF]")
        else:
            # Full scraping for equities
            articles = get_stock_news(symbol, limit=8)
            scraped_count = 0
            for i, article in enumerate(articles[:5], 1):
                url = article.get("url")
                if url and url != "No link available":
                    content = get_article_content(url, retries=2)
                    article["full_content"] = content if content else "Content unavailable"
                    if content:
                        scraped_count += 1
                    time.sleep(1.5)
                else:
                    article["full_content"] = "No URL available"
            news_data[symbol] = articles[:5]
            print(f"{scraped_count}/5 articles")

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

Format (max 1,500 words total):

I. MARKET OVERVIEW (100 words)
II. HOLDINGS: For each - Hold/Add/Trim? Why? (150 words each)
III. WATCHLIST: Top 2-3 picks only - Buy/Pass? Why? (150 words each)
IV. ACTIONS: Top 3 specific moves this week

REQUIREMENTS:
- Cite articles using the format [SYMBOL-#] (e.g., [CEG-1], [AAPL-2])
- ONLY reference articles that match the stock symbol you're analyzing
- Use EXACT P/E ratios from the data above - do not round or estimate
- ONLY cite analyst targets if explicitly shown as "Target: $X" above
- Do NOT invent brokerage names (JPMorgan, Goldman, etc.) unless mentioned in articles
- If data shows "N/A", explicitly state it in your analysis
- Quantify upside/downside conservatively based on fundamentals
- Conviction: X/10 with clear reasoning tied to data quality

ASSET TYPE HANDLING:
- [EQUITY]: Analyze using P/E ratios, analyst targets, earnings growth, competitive positioning
  * Requires 3+ articles for full conviction
- [ETF]: Analyze based on underlying index/sector performance, NOT individual company metrics
  * S&P 500 ETFs (VUSA): Analyze using S&P 500 performance from Market Context
  * Gold ETFs (GLD, IGLN.L): Analyze based on gold as inflation hedge, safe haven demand, macro trends
  * Silver ETFs (SLV): Analyze based on industrial demand + precious metal trends
  * DO NOT say "insufficient data" for ETFs - use macro context instead
  * DO NOT discuss P/E ratios or analyst targets for ETFs
- [COMMODITY/CURRENCY]: Analyze based on macro trends, supply/demand, inflation hedging
- For ETFs marked "[ETF - Use Macro Data]", you have sufficient data to provide analysis
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
                    "content": "You are an expert financial advisor. Provide clear, specific, evidence-based investment advice. Always cite your sources. Never make up data.",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,
            max_tokens=2000,
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
