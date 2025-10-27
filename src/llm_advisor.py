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

# STOCK_CONTENT , add as needed

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
        "sector": "semiconductors",
        "known_suppliers": ["ASML", "AMAT"],
        "known_customers": ["AAPL", "NVDA", "AMD"],
        "known_peers": ["NVDA", "AMD", "INTC"],
    },
    "AMD": {
        "sector": "semiconductors_ai",
        "known_suppliers": ["TSM", "ASML"],
        "known_customers": ["META", "MSFT", "cloud_providers"],
        "known_peers": ["NVDA", "INTC"],
    },
    "SSNLF": {
        "sector": "consumer_tech_semiconductors",
        "known_suppliers": ["ASML", "raw_materials"],
        "known_peers": ["AAPL", "TSM"],
        "known_products": ["smartphones", "semiconductors", "displays"],
    },
    "GLD": {
        "sector": "commodity_gold",
        "known_peers": ["IAU", "PHYS"],
        "known_customers": ["investors", "central_banks"],
    },
    "SLV": {
        "sector": "commodity_silver",
        "known_peers": ["SIVR", "PSLV"],
        "known_customers": ["investors", "industrial"],
    },
}


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
        str: Formatted prompt with ft inspiration
    """
    owned = portfolio.get_portfolio_symbols()
    watched = portfolio.get_watchlist_symbols()
    prices = stock_data["prices"]
    news = stock_data["news"]

    prompt = f"""You are an expert financial analyst providing institutional-quality investment research.

🎯 ANALYSIS FRAMEWORK (Financial Times Standard):

Your analysis must mach the rigor and specification of professional financial journalism:
- Cite SPECIFIC sources numbers from articles (percentages, dollar amounts, targets)
- Reference MULTIPLE sources to show consensus or conflicts
- Quanitfy risks and returns ("expect +X% if Y happens")
- Connect dots across sectors (suppliers, customers, peers)
- Be brutally honest about uncertainties

==================================================
ANALYSIS DATE: {datetime.now().strftime('%Y-%m-%d %H:%M')}
==================================================

=== PORTFOLIO OVERVIEW ===
"""
    # Portfolio holdings with context
    if owned:
        prompt += "\nCURRENT HOLDINGS:\n"
        for symbol in owned:
            holding = portfolio.holdings[symbol]
            current_price = prices.get(symbol, 0)

            total_cost = holding["shares"] * holding["cost_basis"]
            current_value = holding["shares"] * current_price
            gain_loss = current_value - total_cost
            gain_loss_pct = (gain_loss / total_cost * 100) if total_cost > 0 else 0

            # Sector context

            context = STOCK_CONTEXT.get(symbol, {})
            sector_info = (
                f"    (Sector: {context.get('sector', 'N?A')})" if context else ""
            )

            prompt += f"""
{symbol}{sector_info}:
    Position: {holding['shares']} shares @ ${holding['cost_basis']:.2f} avg cost
    Current: ${current_price:.2f}
    Value: ${current_value:.2f}
    P&L: ${gain_loss:.2f} ({gain_loss_pct:+.2f}%)
"""

    total_portfolio_value = portfolio.cash + sum(
        portfolio.holdings[s]["shares"] * prices.get(s, 0) for s in owned
    )
    cash_pct = (
        (portfolio.cash / total_portfolio_value * 100)
        if total_portfolio_value > 0
        else 100
    )

    prompt += f"\nCASH: %{portfolio.cash:,.2f} ({cash_pct:,.1f}% of portfolio)\n"
    prompt += f"TOTAL PORTFOLIO VALUE: ${total_portfolio_value:,.2f}\n"

    # Watchlist with context
    if watched:
        prompt += "\nWATCHLIST (Considering): \n"
        for symbol in watched:
            price = prices.get(symbol, "N/A")
            context = STOCK_CONTEXT.get(symbol, {})
            sector_info = f" - {context.get('sector', 'N/A')}" if context else ""
            prompt += f" {symbol}: ${price}{sector_info}\n"

    # Market context with interpretation
    prompt += "\n\n=== MARKET CONTEXT ===\n"
    prompt += "Current macro environment affecting portfolio:\n\n"

    # Indices with validation
    prompt += "Market Performance (Last 5 Trading days):\n"
    indices_data = macro_data.get("indices", {})
    for symbol, data in indices_data.items():
        change = data.get("change_5d", "N/A")
        if change == "Data Error":
            prompt += f"    {data['name']}: [Data Error -Ignore]\n"
        elif change != "N/A":
            prompt += f"    {data['name']}: {change:+.2f}%\n"
        else:
            prompt += f"    {data['name']}: Data Unavailable\n"

    # VIX with interprertation
    vix = macro_data.get("vix", {})
    vix_level = macro_data.get("vix", "N/A")
    if vix_level != "N/A":
        prompt += (
            f"\nVolatility Index (VIX): {vix_level} - {vix.get('sentiment', 'N/A')}\n"
        )
        prompt += "    Context = VIX 12-20=Calm | 20-30=Elevated | >30=Fear\n"
    else:
        prompt += (
            "\nVolatility Index (VIX): Unavailable (use caution without fear gauge)\n"
        )

    # Tech sector (relevant for this portfolio)
    tech = macro_data.get("tech_sector", {})
    tech_change = tech.get("change_5d", "N/A")
    if tech_change not in ["N/A", "Data Error"]:
        prompt += f"\nTechnology Sector (XLK): {tech_change:+.2f}%\n"
        prompt += (
            "    Note: Your portfolio is tech-heavy - this is your sector benchmark\n"
        )

    # Top financial headlines
    world_news = macro_data.get("world_news", [])
    if world_news:
        prompt += "\n\nGLOBAL FINANCIAL NEWS (Last 48 hours):\n"
        prompt += "Key themes affecting markets:\n"
        for i, article in enumerate(world_news[:5], 1):
            prompt += "Key themes affecting markets:\n"
            if article.get("content"):
                prompt += f"    Key Points: {article['content'][:500]}...\n"
    else:
        prompt += "\n\nGlobal News: Limited data available\n"

    # Stock-specific news with sector context
    prompt += "\n\n=== STOCK-SPECIFIC INTELLIGENCE ===\n"

    if owned:
        prompt += "\n📊 YOUR HOLDINGS (Analyze deeply):\n"
        for symbol in owned:
            context = STOCK_CONTEXT.get(symbol, {})

            prompt += f"\n{symbol}"
            if context:
                prompt += f"    |   Sector: {context.get('sector', 'N/A')}\n"
                if context.get("known_suppliers"):
                    prompt += (
                        f"    Suppliers: {', '.join(context['known_suppliers'])}\n"
                    )
                if context.get("known_customers"):
                    prompt += f"    Customers {', '.join(context['known_customers'])}\n"
                if context.get("known_peers"):
                    prompt += f"    Peers: {', '.join(context['known_peers'])}\n"
            else:
                prompt += "\n"

            prompt += " News Analysis:\n"
            stock_news = news.get(symbol, [])
            if stock_news:
                for i, article in enumerate(stock_news, 1):
                    prompt += f"    [{i}] {article['title']}\n"
                    if article.get("full_content"):
                        prompt += f"    Details: {article['full_content'][:600]}...\n"

            else:
                prompt += " No recent news available\n"

        if watched:
            prompt += "\n\n🎯 WATCHLIST (Evaluuate for purchase): \n"
            for symbol in watched:
                context = STOCK_CONTEXT.get(symbol, {})

                prompt += f"\n{symbol}"
                if context:
                    prompt += f" | Sector: {context.get('sector', 'N/A')}\n"
            else:
                prompt += "    No recent news available \n"

    # FT-Style Analysis Instructions
    prompt += """
=== YOUR ANALYSIS TASK ===

ALL EXAMPLES GIVEN ARE EXAMPLES ONLY, THEY ARE NOT REAL DO NOT USE THE EXAMPLES, ONLY USE THE EXAMPLES AS A GUIDANCE ON THE RESPONSE I REQUIRE.

Provide institutional-quality research managing Finanial Times Standards

📋 REQUIRED FORMAT (for each recommendation):

1. INVESTMENT THESIS (2-3 sentences)
    - Core case with SPECIFIC numbers from articles
    - Example: "CEG operates 7 nuclear plants (per Article [1])."
    AI datacenter demand growing 40% anually (Morgan Stanley via macro news).
    Trading at P/E 21 vs sector average 15."

2.  SUPPORTING EVIDENCE
    - Cite at least 2 specific articles by number:[1], [2], etc.
    - Extract KEY FACTS: percentages, dollar amounts, timelines
    - Cross-check: Do articles agree or contradict each other?

3. SECTOR CONTEXT & CROSS-REFERENCES
    - Is this stock-specific or industry-wide trend?
    - Check peers: Are they mentioned in macro news?
    - Supplier/customer signals: Any related company news?
    - Example: "META datacenter announcemnet + hitachi transformer
    shortage (macro news [3] = CEG has pricing power "

4. BULL CASE (Assuem stock RISES)
    - 3 strongest reasons with evidence
    - Expected return : "X% because..."
    - Timeframe: X months or weeks
    - Catalysts: What truly drives this, backed by evidence and logic?

5. BEAR CASE (Assume stock FALLS)
    - 3 strongest reasons with evidence
    - Expected return : "X% because..."
    - What would trigger a scenario like this, using evidence and logic
    - Be brutally honest about what could go wrong

6.  VALUATION ASSESSMENT
    - Current vs Historical (if mentioned in articles)
    - vs peers (if comparison data available)
    - Expensive / Far / Cheap ? Support with evidence

7. FINAL RECOMMENDATION
    - Action: BUY / SELL / HOLD / WAIT
    - Conviction : X/10 (explain why not 10/10)
    - Positon size: "X% of cash" or "Add Y shares"
    - Entry strategy: "Buy now" vs "Wait for dip to $X"
    - Exit condiitions: "Take profit at $X" or "Stop loss at $X"
    - Timeline: Near-term (0-3mo) / Medium (3-12mo) / Long (1-3yr)

8. RISK FACTORS
    - Quantify: "If x happens, expect -Y%"
    - Macro risks affecting this position
    - Company-specific risks

🚨 CRITICAL NON-NEGOTIABLE RULES:

    -   SPECIFITY: Every claim needs a number or source
        ❌ "Gold is rallying"
        ✅ "Gold is up 19% since September per macro news [4], Goldman targets $4900 "
    
    -   HONESTY: If data is limited, say so
        ❌ Making up analyst targets
        ✅ "Insufficient valuation data in articles - recommend caution"
     
    -   BALANCE: Bull and bear cases should have equal depth
        ❌ 500 words bull, 50 words bear
        ✅ Balanced analysis showing non biased perspectives
    
    -   CONNECTIONS: Link related news
        ❌ Analyzing each stock in isolation
        ✅  "META datacenter news + NVDA chip demand + CEG power = sector trend"

    -   ACTIONABILITY: Be specific about what to do
        ❌  "Consider buying"
        ✅  "Buy 5-10 shares (~$1,800 - 3,600)"       if market dips 3% this week"

=== OUTPUT STRUCUTRE ===

I. MARKET OVERVIEW
    - Macro sentimnet and key themes
    - How current conditions affect THIS portfolio specifically

II. HOLDINGS ANALYSIS
    For each position you own:
    - Hold / Add / Trim / Sell?
    - Complete analysis per format above

III. WATCHLIST OPPORTUNITIES
    For each watchlist stock:
    - Buy / Pass / Wait for dip?
    - Complete analysis per format above

IV. PORTFOLIO CONSTRUCTION
    - Cash allocation assessment (currently {cash_pct:.1f}%)
    - Diversification analysis
    - Overall risk assessment

V. IMMEDIATE ACTION ITEMS
    - Top 3 specific actions with priority (3 minimum)
    - Timeline: This week / This month / This quarter

Remember : You are competing with professional analysts. Match their rigor using the tools that you have
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

        print("🤖 Sending to OpenAI 5-mini...\n")

        response = client.chat.completions.create(
            model="gpt-5-mini-2025-08-07",
            messages=[
                {
                    "role": "system",
                    "content": "You are an expert financial advisor. Provide clear, specific, evidence-based investment advice. Always cite your sources. Never make up data. If information is missing, acknowledge it.",
                },
                {"role": "user", "content": prompt},
            ],
            max_completion_tokens=2000,
        )
        advice = response.choices[0].message.content
        print(
            f"DEBUG: Got response, length: {len(advice) if advice else 0}"
        )  # ADD THIS
        return advice

    except Exception as e:
        return f"⚠️  Error calling OpenAI API: {str(e)}"


def call_openai(prompt, api_key=None):
    """Send prompt to OpenAI and get response"""
    if api_key is None:
        api_key = os.getenv("OPENAI_API_KEY")

    if not api_key:
        return "⚠️  ERROR: OpenAI API key not found!"

    try:
        client = OpenAI(api_key=api_key)

        print("🤖 Sending to OpenAI GPT-5-mini...\n")

        response = client.chat.completions.create(
            model="gpt-5-mini-2025-08-07",
            messages=[
                {
                    "role": "system",
                    "content": "You are an expert financial advisor. Provide clear, specific, evidence-based investment advice.",
                },
                {"role": "user", "content": prompt},
            ],
            max_completion_tokens=16000,
        )

        # DEBUG: Print full response object
        print(f"DEBUG: Response object: {response}")
        print(f"DEBUG: Choices: {response.choices}")
        print(f"DEBUG: Message: {response.choices[0].message}")

        advice = response.choices[0].message.content
        print(f"DEBUG: Content length: {len(advice) if advice else 0}")
        return advice

    except Exception as e:
        print(f"DEBUG: Exception type: {type(e)}")
        print(f"DEBUG: Exception details: {str(e)}")
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

    # Print the advice
    if advice and len(advice) > 0:
        print(advice)
        print()
    else:
        print("⚠️ No advice generated")

    return advice
