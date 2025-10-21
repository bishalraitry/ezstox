"""
LLM Advisor Module - AI-powered portfolio analysis
Uses OpenAI GPT-4o to provide investment recommendations
"""

import os
from datetime import datetime, timedelta

import feedparser
import requests
from fredapi import Fred
from openai import OpenAI

from src.data_fetcher import get_multiple_prices, get_stock_news


def get_article_content(url, max_chars=3000):
    """
    Fetch full article content using Jina AI Reader

    Args:
        url (str): Article URL
        max_chars (int): Maximum characters to return

    Returns:
        str: Clean article text or None if failed
    """
    if not url or url == "No link available":
        return None

    try:
        jina_url = f"https://r.jina.ai/{url}"
        headers = {"X-Return-Format": "text"}

        response = requests.get(jina_url, headers=headers, timeout=15)

        if response.status_code == 200:
            return response.text[:max_chars]
        else:
            return None

    except Exception as e:
        print(f"  ⚠️  Failed to fetch article: {str(e)[:50]}")
        return None


def validate_percentage_change(change_pct, timeframe="5 days"):
    """
    Validate if a percentage change is realistic

    Args:
        change_pct (float): Percentage change
        timeframe (str): Time period

    Returns:
        tuple: (is_valid, validated_value)
    """
    # Market can't realistically move >10% in 5 days under normal conditions
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
        # Google News RSS feed for finance/markets
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

            # Interpret VIX level
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

    # 1. Financial news from Google RSS (free, no key needed)
    print("  • Fetching global financial news (RSS)...")
    try:
        articles = get_financial_news_rss(limit=10)

        # Scrape full content for top 5 articles
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

    # 2. Market indices performance (with PROPER date ranges)
    print("  • Fetching market indices...")
    indices = {}

    end_date = datetime.now()
    start_date = end_date - timedelta(days=7)  # 7 days to account for weekends

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

                # Validate the data
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

    # 3. VIX (Volatility/Fear Index) from FRED
    print("  • Fetching VIX from FRED...")
    try:
        macro["vix"] = get_vix_from_fred(fred_api_key)
    except Exception as e:
        print(f"    ⚠️  VIX fetch failed: {e}")
        macro["vix"] = {"level": "N/A", "sentiment": "N/A"}

    # 4. Tech sector performance
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

        # Get tech sector news
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
    Gather all stock-specific data with full article content

    Args:
        portfolio: Portfolio object

    Returns:
        dict: Prices and news with full article content
    """
    print("📊 Gathering stock-specific data...")

    owned = portfolio.get_portfolio_symbols()
    watched = portfolio.get_watchlist_symbols()
    all_symbols = owned + watched

    # Get current prices
    print("\n💰 Fetching current prices...")
    prices = get_multiple_prices(all_symbols)

    # Get news with full article content
    print("\n📰 Fetching news + scraping articles...")
    news_data = {}

    for symbol in all_symbols:
        print(f"\n  {symbol}:")
        articles = get_stock_news(symbol, limit=5)

        # Scrape full content for top 3 articles only
        for i, article in enumerate(articles[:3], 1):
            url = article.get("url")
            if url and url != "No link available":
                print(f"    → Scraping article {i}/3...")
                content = get_article_content(url)
                article["full_content"] = content if content else "Content unavailable"
            else:
                article["full_content"] = "No URL available"

        news_data[symbol] = articles[:3]  # Only keep top 3

    print("\n✅ Stock data gathered\n")

    return {"prices": prices, "news": news_data}


def format_llm_prompt(portfolio, stock_data, macro_data):
    """
    Format comprehensive prompt for LLM with anti-hallucination instructions

    Args:
        portfolio: Portfolio object
        stock_data: Stock prices and news
        macro_data: Market context data

    Returns:
        str: Formatted prompt
    """
    owned = portfolio.get_portfolio_symbols()
    watched = portfolio.get_watchlist_symbols()
    prices = stock_data["prices"]
    news = stock_data["news"]

    prompt = f"""You are an expert financial advisor analyzing a portfolio. Provide actionable investment recommendations.

🚨 CRITICAL ANTI-HALLUCINATION RULES:
1. ONLY use information provided below - DO NOT make up data or statistics
2. ALWAYS cite specific article titles when making claims
3. If data shows "N/A" or "Data Error", acknowledge this limitation
4. BAD: "NVDA benefits from AI boom" (generic)
   GOOD: "NVDA mentioned in article 'TSM collaboration boosts production' which discusses 20% capacity increase"
5. If you cannot find supporting evidence in the articles, say "Insufficient data to recommend"
6. Be specific about WHY you're recommending something, referencing actual news content

TONE: Balanced - highlight both opportunities AND risks. Consider broader market conditions.

==================================================
ANALYSIS DATE: {datetime.now().strftime('%Y-%m-%d %H:%M')}
==================================================

=== CURRENT PORTFOLIO ===
"""

    # Portfolio holdings
    if owned:
        prompt += "\nHOLDINGS:\n"
        for symbol in owned:
            holding = portfolio.holdings[symbol]
            current_price = prices.get(symbol, 0)

            total_cost = holding["shares"] * holding["cost_basis"]
            current_value = holding["shares"] * current_price
            gain_loss = current_value - total_cost
            gain_loss_pct = (gain_loss / total_cost * 100) if total_cost > 0 else 0

            prompt += f"""
{symbol}:
  - Shares: {holding['shares']}
  - Average Cost: ${holding['cost_basis']:.2f}
  - Current Price: ${current_price:.2f}
  - Position Value: ${current_value:.2f}
  - Gain/Loss: ${gain_loss:.2f} ({gain_loss_pct:+.2f}%)
"""

    total_portfolio_value = portfolio.cash + sum(
        portfolio.holdings[s]["shares"] * prices.get(s, 0) for s in owned
    )
    cash_pct = (
        (portfolio.cash / total_portfolio_value * 100)
        if total_portfolio_value > 0
        else 100
    )

    prompt += f"\nCASH: ${portfolio.cash:,.2f} ({cash_pct:.1f}% of portfolio)\n"

    # Watchlist
    if watched:
        prompt += "\nWATCHLIST (stocks being considered):\n"
        for symbol in watched:
            price = prices.get(symbol, "N/A")
            prompt += f"  - {symbol}: ${price}\n"

    # Market context
    prompt += "\n\n=== MARKET CONTEXT ===\n"
    prompt += "⚠️ IMPORTANT: Consider these broader market factors\n\n"

    # Indices
    prompt += "Market Performance (Last 5 Days):\n"
    for symbol, data in macro_data.get("indices", {}).items():
        change = data.get("change_5d", "N/A")
        if change == "Data Error":
            prompt += f"  - {data['name']}: Data Error (ignore this metric)\n"
        elif change != "N/A":
            prompt += f"  - {data['name']}: {change:+.2f}%\n"
        else:
            prompt += f"  - {data['name']}: Data unavailable\n"

    # VIX
    vix = macro_data.get("vix", {})
    vix_level = vix.get("level", "N/A")
    if vix_level != "N/A":
        prompt += (
            f"\nVolatility Index (VIX): {vix_level} - {vix.get('sentiment', 'N/A')}\n"
        )
        prompt += (
            "(Reference: VIX 12-20 = Normal | 20-30 = Elevated | >30 = High Fear)\n"
        )
    else:
        prompt += f"\nVolatility Index (VIX): Data unavailable\n"

    # Tech sector
    tech = macro_data.get("tech_sector", {})
    tech_change = tech.get("change_5d", "N/A")
    if tech_change != "N/A" and tech_change != "Data Error":
        prompt += f"\nTechnology Sector (XLK): {tech_change:+.2f}% (5-day)\n"
    else:
        prompt += f"\nTechnology Sector (XLK): Data unavailable\n"

    # Top financial headlines with content
    world_news = macro_data.get("world_news", [])
    if world_news:
        prompt += "\n\nTop Financial News (Last 48 Hours):\n"
        for i, article in enumerate(world_news[:5], 1):
            prompt += f"\n{i}. {article['title']}\n"
            if article.get("content"):
                prompt += f"   Summary: {article['content'][:600]}...\n"
    else:
        prompt += "\n\nGlobal financial news: Unavailable\n"

    # Stock-specific news
    prompt += "\n\n=== STOCK-SPECIFIC NEWS ===\n"

    if owned:
        prompt += "\nYOUR HOLDINGS:\n"
        for symbol in owned:
            prompt += f"\n{symbol} (YOU OWN THIS):\n"
            stock_news = news.get(symbol, [])
            if stock_news:
                for i, article in enumerate(stock_news, 1):
                    prompt += f"  Article {i}: {article['title']}\n"
                    if article.get("full_content"):
                        prompt += f"     Content: {article['full_content'][:700]}...\n"
            else:
                prompt += "  No recent news available\n"

    if watched:
        prompt += "\nWATCHLIST:\n"
        for symbol in watched:
            prompt += f"\n{symbol} (CONSIDERING):\n"
            stock_news = news.get(symbol, [])
            if stock_news:
                for i, article in enumerate(stock_news, 1):
                    prompt += f"  Article {i}: {article['title']}\n"
                    if article.get("full_content"):
                        prompt += f"     Content: {article['full_content'][:700]}...\n"
            else:
                prompt += "  No recent news available\n"

    # Analysis instructions
    prompt += """

=== YOUR ANALYSIS TASK ===

Provide analysis in these sections:

1. MARKET OVERVIEW
   - Overall market sentiment based on the data provided
   - Key risks or opportunities in current conditions
   - How macro conditions affect this specific portfolio

2. BUY RECOMMENDATIONS
   - Which watchlist stocks to buy (if any)
   - CITE specific articles/news that support each recommendation
   - Suggested position sizes (percentage of cash or dollar amount)
   - Confidence level: High/Medium/Low (based on quality of available data)

3. SELL RECOMMENDATIONS
   - Should any holdings be sold? Why?
   - CITE specific negative news if recommending sell

4. HOLD POSITIONS
   - Which holdings to keep and specific reasons why
   - CITE supporting articles

5. PORTFOLIO HEALTH
   - Cash allocation analysis
   - Diversification assessment
   - Risk factors specific to this portfolio

6. IMMEDIATE ACTION ITEMS
   - Top 2-3 specific actions to take this week
   - Prioritize based on urgency and opportunity

REQUIREMENTS:
- Every recommendation MUST reference a specific article by title
- If data is limited/unavailable, acknowledge this
- Do NOT give generic investment advice
- Focus on WHAT to do, WHY (with citations), and WHEN
"""

    return prompt


def call_openai(prompt, api_key=None):
    """
    Send prompt to OpenAI GPT-4o and get response

    Args:
        prompt (str): Formatted prompt
        api_key (str): OpenAI API key (or set OPENAI_API_KEY env var)

    Returns:
        str: AI advisor response
    """
    if api_key is None:
        api_key = os.getenv("OPENAI_API_KEY")

    if not api_key:
        return """
⚠️  ERROR: OpenAI API key not found!

Please set your API key:
  Windows: setx OPENAI_API_KEY "your-key-here"
  Mac/Linux: export OPENAI_API_KEY="your-key-here"

Or pass it directly: get_ai_advice(portfolio, openai_key="your-key")
"""

    try:
        client = OpenAI(api_key=api_key)

        print("🤖 Sending to OpenAI GPT-4o...\n")

        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "system",
                    "content": "You are an expert financial advisor. Provide clear, specific, evidence-based investment advice. Always cite your sources. Never make up data. If information is missing, acknowledge it.",
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
        fred_key (str): Optional FRED API key for VIX data

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

    return advice
