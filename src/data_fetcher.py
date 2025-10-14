"""
Data fetcher module - handling OpenBB API calls
"""

from openbb import obb  # Our api caller
import pandas as pd  # Pandas is tool for working with data in tables


def get_stock_price(symbol):
    """
    Get current price for a stock symbol

    Args:
        symbol (str): Stock ticker symbol

    Returns:
        float: Current stock price, or None if error
    """
    try:
        data = obb.equity.price.historical(symbol, limit=1)
        df = data.to_df()
        current_price = df["close"].iloc[-1]
        return round(current_price, 2)
    except Exception as e:
        print(f"Error fetching price for {symbol}: {e}")
        return None


def get_stock_news(symbol, limit=3):
    """
    Get recent news headlines for a stock

    Args:
        symbol(str): Stock ticker symbol
        limit (int): Number of news articles to fetch

    Returns:
        list: List of dicts with 'title' and 'date', or empty list if error
    """
    try:
        news = obb.news.company(symbol, limit=limit)
        news_df = news.to_df()

        articles = []
        for _, row in news_df.iterrows():
            
            # Multiple possible date field names
            date_value = 'Unknown date'
            for date_field in ['date', 'published', 'updated', 'timestamp']:
                if date_field in row and row[date_field] is not None:
                    date_value = row[date_field]
                    break
            articles.append(
                {
                    'title' : row.get('title', 'No title'),
                    'date': date_value if date_value else 'Unknown date'
                }
            )
        return articles
    except Exception as e:
        print(f"⚠️  Error fetching news for {symbol} {str(e)}")
        return []


def get_multiple_prices(symbols):
    """
    Get prices for multiple stokcs

    Args:
        symbols (list): List of stock ticker symbols
    Returns:
        dict: {symbol:price} mapping
    """
    prices = {}
    for symbol in symbols:
        print(f"Fetching {symbol}...", end=" ")
        price = get_stock_price(symbol)
        if price:
            prices[symbol] = price
            print(f"${price}")
        else:
            print("Failed")
    return prices
