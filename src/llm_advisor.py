import os
import requests
import feedparser
from datetime import datetime, timedelta
from openai import OpenAI
from fredapi import Fred
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


def get_macro_data():
    """
    Gather broad market context data

    Returns:
        dict: Market indices, news, and volatility data
    """
    from openbb import obb

    print("\n🌍 Fetching macro market context...")

    macro = {}

    try:
        # 1. General financial news (top stories)
        print("  • Fetching global financial news...")
        try:
            world_news = obb.news.world(limit=10)
            world_news_df = world_news.to_df()

            articles = []
            for _, row in world_news_df.iterrows():
                article = {
                    "title": row.get("title", "No title"),
                    "url": row.get("url") or row.get("link"),
                }

                # Scrape full content for top 5 macro articles
                if len(articles) < 5 and article["url"]:
                    print(f"    → Scraping: {article['title'][:50]}...")
                    article["content"] = get_article_content(article["url"])

                articles.append(article)

            macro["world_news"] = articles
        except Exception as e:
            print(f"    ⚠️  World news unavailable: {e}")
            macro["world_news"] = []

        # 2. Market indices performance
        print("  • Fetching market indices...")
        indices = {}
        for symbol, name in [
            ("SPY", "S&P 500"),
            ("QQQ", "Nasdaq"),
            ("DIA", "Dow Jones"),
        ]:
            try:
                data = obb.equity.price.historical(symbol, limit=5)
                df = data.to_df()

                current = df["close"].iloc[-1]
                week_ago = df["close"].iloc[0]
                change_pct = ((current - week_ago) / week_ago) * 100

                indices[symbol] = {
                    "name": name,
                    "current": round(current, 2),
                    "change_5d": round(change_pct, 2),
                }
            except:
                indices[symbol] = {"name": name, "current": "N/A", "change_5d": "N/A"}

        macro["indices"] = indices

        # 3. VIX (Volatility/Fear Index)
        print("  • Fetching VIX (fear index)...")
        try:
            vix_data = obb.equity.price.historical("VIX", limit=1)
            vix_df = vix_data.to_df()
            vix_level = round(vix_df["close"].iloc[-1], 2)

            # Interpret VIX level
            if vix_level < 15:
                vix_sentiment = "Low (Calm market)"
            elif vix_level < 25:
                vix_sentiment = "Normal"
            else:
                vix_sentiment = "High (Fear/Uncertainty)"

            macro["vix"] = {"level": vix_level, "sentiment": vix_sentiment}
        except:
            macro["vix"] = {"level": "N/A", "sentiment": "N/A"}

        # 4. Tech sector performance (since user is 100% tech)
        print("  • Fetching tech sector data...")
        try:
            tech_data = obb.equity.price.historical("XLK", limit=5)
            tech_df = tech_data.to_df()

            current = tech_df["close"].iloc[-1]
            week_ago = tech_df["close"].iloc[0]
            change_pct = ((current - week_ago) / week_ago) * 100

            macro["tech_sector"] = {
                "current": round(current, 2),
                "change_5d": round(change_pct, 2),
            }

            # Get tech sector news
            tech_news = obb.news.company("XLK", limit=3)
            tech_news_df = tech_news.to_df()

            tech_articles = []
            for _, row in tech_news_df.iterrows():
                tech_articles.append({"title": row.get("title", "No title")})

            macro["tech_sector"]["news"] = tech_articles

        except:
            macro["tech_sector"] = {"current": "N/A", "change_5d": "N/A", "news": []}

    except Exception as e:
        print(f"⚠️  Error fetching macro data: {e}")

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

        # Scrape full content for top 3 articles
        for i, article in enumerate(articles[:3]):
            url = article.get("url")
            if url and url != "No link available":
                print(f"    → Scraping article {i+1}/3...")
                content = get_article_content(url)
                article["full_content"] = content if content else "Content unavailable"
            else:
                article["full_content"] = "No URL available"

        news_data[symbol] = articles[:3]  # Only keep top 3 with content

    print("\n✅ Stock data gathered\n")

    return {"prices": prices, "news": news_data}


def format_llm_prompt(portfolio, stock_data, macro_data):
    """
    Format comprehensive prompt for LLM

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

CRITICAL INSTRUCTIONS:
- Focus on BUY/SELL/HOLD decisions with specific reasoning
- Consider BOTH individual stock news AND broader market conditions
- If macro conditions are unfavorable, recommend caution even if individual stocks look good
- Cite specific news articles that support your recommendations
- Be balanced: highlight both opportunities and risks
- Provide confidence levels (High/Medium/Low) for each recommendation

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

    prompt += f"\nCASH: ${portfolio.cash:,.2f} ({(portfolio.cash / (portfolio.cash + sum(portfolio.holdings[s]['shares'] * prices.get(s, 0) for s in owned)) * 100):.1f}% of portfolio)\n"

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
        prompt += f"  - {data['name']}: {data['change_5d']}%\n"

    # VIX
    vix = macro_data.get("vix", {})
    prompt += f"\nVolatility Index (VIX): {vix.get('level', 'N/A')} - {vix.get('sentiment', 'N/A')}\n"
    prompt += "(Normal VIX: 12-20 | Elevated: 20-30 | High Fear: >30)\n"

    # Tech sector
    tech = macro_data.get("tech_sector", {})
    prompt += f"\nTechnology Sector (XLK): {tech.get('change_5d', 'N/A')}% (5-day)\n"

    # Top financial headlines
    prompt += "\n\nTop Financial News (Last 48 Hours):\n"
    for i, article in enumerate(macro_data.get("world_news", [])[:5], 1):
        prompt += f"\n{i}. {article['title']}\n"
        if article.get("content"):
            prompt += f"   Content: {article['content'][:800]}...\n"

    # Stock-specific news
    prompt += "\n\n=== STOCK-SPECIFIC NEWS ===\n"

    if owned:
        prompt += "\nYOUR HOLDINGS:\n"
        for symbol in owned:
            prompt += f"\n{symbol} (YOU OWN THIS):\n"
            for i, article in enumerate(news.get(symbol, []), 1):
                prompt += f"  {i}. {article['title']}\n"
                if article.get("full_content"):
                    prompt += f"     Content: {article['full_content'][:800]}...\n"

    if watched:
        prompt += "\nWATCHLIST:\n"
        for symbol in watched:
            prompt += f"\n{symbol} (CONSIDERING):\n"
            for i, article in enumerate(news.get(symbol, []), 1):
                prompt += f"  {i}. {article['title']}\n"
                if article.get("full_content"):
                    prompt += f"     Content: {article['full_content'][:800]}...\n"

    # Questions
    prompt += """

=== YOUR ANALYSIS ===

Provide a comprehensive analysis with these sections:

1. MARKET OVERVIEW
   - Overall market sentiment and key risks/opportunities
   - How current conditions affect this portfolio

2. BUY RECOMMENDATIONS
   - Which watchlist stocks to buy (if any)
   - Specific reasoning based on news and market conditions
   - Suggested position sizes
   - Confidence level for each

3. SELL RECOMMENDATIONS
   - Should any current holdings be sold?
   - Specific reasoning

4. HOLD POSITIONS
   - Which holdings to keep and why

5. PORTFOLIO HEALTH
   - Cash allocation assessment
   - Diversification concerns
   - Risk factors

6. IMMEDIATE ACTION ITEMS
   - Top 3 specific actions to take this week

Be direct and actionable. Focus on WHAT to do and WHY, not general market commentary.
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

Or pass it directly: get_ai_advice(portfolio, api_key="your-key")
"""

    try:
        client = OpenAI(api_key=api_key)

        print("🤖 Sending to OpenAI GPT-4o...\n")

        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "system",
                    "content": "You are an expert financial advisor providing clear, actionable investment advice. Be specific, cite sources, and consider both individual stock fundamentals and broader market conditions.",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,
            max_tokens=2000,
        )

        return response.choices[0].message.content

    except Exception as e:
        return f"⚠️  Error calling OpenAI API: {str(e)}"


def get_ai_advice(portfolio, api_key=None):
    """
    Main function: Get complete AI investment advice

    Args:
        portfolio: Portfolio object
        api_key (str): Optional OpenAI API key

    Returns:
        str: Formatted AI advice
    """
    print("\n" + "=" * 60)
    print("🤖 AI FINANCIAL ADVISOR")
    print("=" * 60)

    # Gather all data
    stock_data = gather_stock_data(portfolio)
    macro_data = get_macro_data()

    # Format prompt
    print("📝 Formatting analysis prompt...\n")
    prompt = format_llm_prompt(portfolio, stock_data, macro_data)

    # Get AI response
    advice = call_openai(prompt, api_key)

    print("=" * 60)
    print("✅ ANALYSIS COMPLETE")
    print("=" * 60)
    print()

    return advice
