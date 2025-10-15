"""
Data fetcher module - handling OpenBB API calls
"""
from datetime import datetime
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


def _parse_date(date_value):
    """Try to parse various date formats into readable string

    Args:
        date_value: Date in various possible formats

    Returns:
        str: Formatted date string or 'Unknown date'
    """
    if date_value is None or pd.isna(date_value):
        return "Unknown date"

    # If already datetime object
    if hasattr(date_value, "strftime"):
        return date_value.strftime("%Y-%m-%d")

    # If string, parse

    if isinstance(date_value, str):
        date_formats = [
            "%Y-%m-%d",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%m/%d/%Y",
            "%d/%m/%Y",
            "%B %d, %Y",
        ]

        for fmt in date_formats:
            try:
                parsed_date = datetime.strptime(date_value.split('.')[0].split('+')[0].strip(), fmt)
                return parsed_date.strftime('%Y-%m-%d')
            except:
                continue
        return 'Unknown date'


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
            date_value = "Unknown date"
            for date_field in ["date", "published", "updated", "timestamp"]:
                if date_field in row and row[date_field] is not None:
                    date_value = row[date_field]
                    break

            url = None
            for url_field in ['url', 'link', 'article_url', 'news_url']:
                if url_field in row and row[url_field] is not None:
                    url = row[url_field]
                    break            


            articles.append(
                {
                    "title": row.get("title", "No title"),
                    "date": _parse_date(date_value),
                    'url': url if url else 'No link available'
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
