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
from fredapi import Fred
from newspaper import Article
from openai import OpenAI

from src.data_fetcher import get_multiple_prices, get_stock_news

# =============================================================================
# STOCK CONTEXT - Add your stocks here as you expand
# =============================================================================

STOCK_CONTEXT = {
    "META": {
        "sector": "social_media_cloud",
        "known_suppliers": ["NVDA", "AMD"],
        "known_peers": ["GOOGL", "MSFT", "AMZN"],
        "known_customers": ["advertisers"],
    },
    "CEG": {
        "sector": "utility_nuclear",
        "known_peers": ["VST", "NEE", "DUK"],
        "known_customers": ["datacenters", "industrial"],
    },
    "AAPL": {
        "sector": "consumer_tech",
        "known_suppliers": ["TSM", "FOXCONN"],
        "known_peers": ["MSFT", "GOOGL"],
        "known_products": ["iPhone", "Mac", "Services"],
    },
    "TSLA": {
        "sector": "automotive_ev",
        "known_peers": ["RIVN", "LCID", "F", "GM"],
        "known_suppliers": ["battery_manufacturers"],
    },
    "NVDA": {
        "sector": "semiconductors_ai",
        "known_suppliers": ["TSM", "ASML"],
        "known_customers": ["META", "MSFT", "GOOGL", "AMZN"],
        "known_peers": ["AMD", "INTC"],
    },
    "TSM": {
        "sector": "semiconductors_foundry",
        "known_customers": ["AAPL", "NVDA", "AMD"],
        "known_peers": ["INTC", "SMSN"],
    },
    "IGLN.L": {
        "sector": "commodity_gold",
        "asset_type": "physical_gold_etc",
        "description": "iShares Physical Gold ETC",
        "note": "NOT a stock - tracks physical gold price, NOT clean energy",
    },
    "VUSA.L": {
        "sector": "index_fund_sp500",
        "asset_type": "etf",
        "description": "Vanguard S&P 500 UCITS ETF",
        "note": "Tracks S&P 500 index",
    },
    "GLD": {
        "sector": "commodity_gold",
        "asset_type": "gold_etf",
        "description": "SPDR Gold Shares",
        "known_peers": ["IAU", "IGLN.L"],
    },
    "SLV": {
        "sector": "commodity_silver",
        "asset_type": "silver_etf",
        "description": "iShares Silver Trust",
    },
}

# =============================================================================


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
    Get key financial metrics from OpenBB

    Args:
        symbol (str): Stock ticker

    Returns:
        dict: Financial metrics or empty dict if unavailable
    """
    from openbb import obb

    fundamentals = {}

    try:
        # Get key metrics from Yahoo Finance
        quote = obb.equity.price.quote(symbol, provider="yfinance")
        quote_df = quote.to_df()

        if len(quote_df) > 0:
            q = quote_df.iloc[0]

            # Extract available metrics
            fundamentals["pe_ratio"] = q.get("pe_ttm")
            fundamentals["forward_pe"] = q.get("forward_pe")
            fundamentals["market_cap"] = q.get("market_cap")
            fundamentals["beta"] = q.get("beta")
            fundamentals["dividend_yield"] = q.get("dividend_yield")
            fundamentals["fifty_two_week_high"] = q.get("fifty_two_week_high")
            fundamentals["fifty_two_week_low"] = q.get("fifty_two_week_low")
            fundamentals["analyst_target_mean"] = q.get("price_target_average")
            fundamentals["analyst_target_high"] = q.get("price_target_high")
            fundamentals["analyst_target_low"] = q.get("price_target_low")

    except Exception as e:
        print(f"  ⚠️  Could not fetch fundamentals for {symbol}: {str(e)[:50]}")

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
            f"  ⚠️  Suspicious data: {change_pct:.2f}% change in {timeframe} (likely data error)"
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
        print(f"  ⚠️  Error fetching RSS news: {e}")
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
        print("  ⚠️  FRED API key not found (VIX unavailable)")
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
        print(f"  ⚠️  Error fetching VIX from FRED: {e}")
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

    print("\n🌍 Fetching macro market context...")

    macro = {}

    # 1. Financial news from Google RSS
    print("  • Fetching global financial news (RSS)...")
    try:
        articles = get_financial_news_rss(limit=10)

        print("    → Scraping top articles...")
        for article in articles[:5]:
            url = article.get("url")
            if url:
                content = get_article_content(url)
                article["content"] = content if content else "Content unavailable"

        macro["world_news"] = articles
        print(f"    ✅ Got {len(articles)} financial news articles")
    except Exception as e:
        print(f"    ⚠️  Error fetching news: {e}")
        macro["world_news"] = []

    # 2. Market indices
    print("  • Fetching market indices...")
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
            print(f"    ⚠️  {symbol} fetch failed: {e}")
            indices[symbol] = {"name": name, "current": "N/A", "change_5d": "N/A"}

    macro["indices"] = indices

    # 3. VIX
    print("  • Fetching VIX from FRED...")
    try:
        macro["vix"] = get_vix_from_fred(fred_api_key)
    except Exception as e:
        print(f"    ⚠️  VIX fetch failed: {e}")
        macro["vix"] = {"level": "N/A", "sentiment": "N/A"}

    # 4. Tech sector
    print("  • Fetching tech sector data...")
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
        print(f"    ⚠️  Tech sector fetch failed: {e}")
        macro["tech_sector"] = {"current": "N/A", "change_5d": "N/A", "news": []}

    print("✅ Macro context gathered\n")
    return macro


def gather_stock_data(portfolio):
    """
    Gather stock data: prices, news (5 articles), and fundamentals

    Args:
        portfolio: Portfolio object

    Returns:
        dict: Prices, news, and fundamentals
    """
    print("📊 Gathering stock-specific data...")

    owned = portfolio.get_portfolio_symbols()
    watched = portfolio.get_watchlist_symbols()
    all_symbols = owned + watched

    # Get current prices
    print("\n💰 Fetching current prices...")
    prices = get_multiple_prices(all_symbols)

    # Get fundamentals + news
    print("\n📊 Fetching fundamentals + news...")
    news_data = {}
    fundamentals_data = {}

    for symbol in all_symbols:
        print(f"\n  {symbol}:")

        # Get fundamentals
        print(f"    → Fetching fundamentals...")
        fundamentals_data[symbol] = get_stock_fundamentals(symbol)

        # Get 8 news articles
        articles = get_stock_news(symbol, limit=8)

        # Scrape top 5
        scraped_count = 0
        for i, article in enumerate(articles[:5], 1):
            url = article.get("url")
            if url and url != "No link available":
                print(f"    → Scraping article {i}/5...")
                content = get_article_content(url, retries=2)
                article["full_content"] = content if content else "Content unavailable"
                if content:
                    scraped_count += 1
                time.sleep(1.5)  # Rate limiting
            else:
                article["full_content"] = "No URL available"

        news_data[symbol] = articles[:5]
        print(f"    ✅ {scraped_count}/5 articles scraped successfully")

    print("\n✅ Stock data gathered\n")

    print(f"DEBUG - {symbol} fundamentals: {fundamentals_data[symbol]}")

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

🎯 CRITICAL RULES:

1. DATA SUFFICIENCY:
   - Stock with <3 of 5 articles: Mark "LIMITED DATA - Lower conviction"
   - Stock with 0-1 articles + no fundamentals: "INSUFFICIENT DATA - CANNOT ANALYZE"
   - NEVER make recommendations without evidence

2. PRIORITIZE FUNDAMENTALS:
   - Analyst targets > Article speculation
   - P/E ratios > Vague sector trends
   - Actual earnings > Generic news

3. CITE SOURCES:
   - "Fundamentals show P/E of X"
   - "Article [1] states Y"
   - "Analyst target: $Z"

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

            context = STOCK_CONTEXT.get(symbol, {})
            sector = context.get("sector", "Unknown")

            prompt += f"\n{symbol} ({sector}): {holding['shares']} sh @ ${holding['cost_basis']:.2f} → ${current_price:.2f} | P&L: ${gain_loss:.2f} ({gain_loss_pct:+.1f}%)\n"

            # Fundamentals
            fund = fundamentals.get(symbol, {})
            if fund.get("pe_ratio"):
                prompt += f"  P/E: {fund['pe_ratio']:.1f}"
            if fund.get("analyst_target_mean"):
                upside = (
                    (
                        (fund["analyst_target_mean"] - current_price)
                        / current_price
                        * 100
                    )
                    if current_price > 0
                    else 0
                )
                prompt += (
                    f" | Target: ${fund['analyst_target_mean']:.0f} ({upside:+.0f}%)"
                )
            if fund.get("fifty_two_week_high"):
                prompt += f" | 52w: ${fund['fifty_two_week_low']:.0f}-${fund['fifty_two_week_high']:.0f}"
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
            context = STOCK_CONTEXT.get(symbol, {})
            sector = context.get("sector", "Unknown")
            prompt += f"{symbol} ({sector}): ${price}"

            fund = fundamentals.get(symbol, {})
            if fund.get("pe_ratio"):
                prompt += f" | P/E: {fund['pe_ratio']:.1f}"
            if fund.get("analyst_target_mean"):
                upside = (
                    ((fund["analyst_target_mean"] - price) / price * 100)
                    if price > 0
                    else 0
                )
                prompt += (
                    f" | Target: ${fund['analyst_target_mean']:.0f} ({upside:+.0f}%)"
                )
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
        available_count = sum(
            1
            for a in stock_news
            if a.get("full_content")
            not in [None, "Content unavailable", "No URL available"]
        )
        quality = (
            "✅" if available_count >= 3 else "⚠️" if available_count >= 1 else "❌"
        )

        prompt += f"\n{symbol} {quality} ({available_count}/5 articles):\n"

        if available_count > 0:
            for i, article in enumerate(stock_news[:3], 1):
                prompt += f"[{i}] {article['title']}\n"
                content = article.get("full_content", "")
                if content and content not in [
                    "Content unavailable",
                    "No URL available",
                ]:
                    prompt += f"    {content[:400]}...\n"
        else:
            prompt += "  ❌ NO DATA - Cannot analyze\n"

    # Instructions
    prompt += """

=== YOUR ANALYSIS ===

Format (max 1,500 words total):

I. MARKET OVERVIEW (100 words)
II. HOLDINGS: For each - Hold/Add/Trim? Why? (150 words each)
III. WATCHLIST: Top 2-3 picks only - Buy/Pass? Why? (150 words each)
IV. ACTIONS: Top 3 specific moves this week

REQUIREMENTS:
- Cite fundamentals + articles by number
- If insufficient data, say so
- Quantify: "+X% if Y" or "Stop -Z%"
- Conviction: X/10 with reason
"""

    return prompt


def call_openai(prompt, api_key=None):
    """Send prompt to OpenAI GPT-4o-mini"""
    if api_key is None:
        api_key = os.getenv("OPENAI_API_KEY")

    if not api_key:
        return "⚠️  ERROR: OpenAI API key not found!"

    try:
        client = OpenAI(api_key=api_key)

        print("🤖 Sending to OpenAI GPT-4o-mini...\n")

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
        return f"⚠️  Error calling OpenAI API: {str(e)}"


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
    print("🤖 AI FINANCIAL ADVISOR")
    print("=" * 60)

    # Gather all data
    stock_data = gather_stock_data(portfolio)
    macro_data = get_macro_data(fred_key)

    # Format prompt
    print("📝 Formatting analysis prompt...\n")
    prompt = format_llm_prompt(portfolio, stock_data, macro_data)

    # Get AI response
    advice = call_openai(prompt, openai_key)

    print("=" * 60)
    print("✅ ANALYSIS COMPLETE")
    print("=" * 60)
    print()

    # Print the advice
    if advice and len(advice) > 0:
        print(advice)
        print()
    else:
        print("⚠️ No advice generated")

    articles = get_stock_news("CEG", limit=8)
    for i, article in enumerate(articles):
        print(f"\n--- Article {i+1} ---")
        print(f"Title: {article['title']}")
        print(f"URL: {article['url']}")
        print(f"Date: {article['date']}")

    return advice
