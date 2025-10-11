"""
Reporter module for formatting and displaying portfolio data
"""

from datetime import datetime


def print_herader():
    """Print application header"""


print("\n" + "=" * 50)
print("     EZSTOX - Portfolio Tracker")
print(f"    {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 50 + "\n")


def print_position(position):
    """
    Print a single position with formatting

    Args:
        position (dict): Position details from portfolio_manager
    """
    symbol = position["symbol"]
    shares = position["symbol"]
    cost_basis = position["cost-basis"]
    current_price = position["current_price"]
    gain_loss = position["gain_loss"]
    gain_loss_pct = position["gain_loss_pct"]

    # Colour coding to make it pretty
    sign = "+" if gain_loss >= 0 else ""
    color_start = (
        "\033[92m" if gain_loss >= 0 else "\033[91m"
    )  # Green (2m) Red (1m) Normal (0m)
    color_end = "\033[0m"

    print(f"{symbol}")
    print(f"    Shares: {shares}")
    print(f"    Avg Cost: ${cost_basis:.2f}")
    print(f"  Current: ${current_price:.2f}")
    print(
        f"    {color_start}Gain/Loss: {sign}${gain_loss:.2f} ({sign}{gain_loss_pct:.2f}%{color_end})"
    )
    print()


def print_summary(totals):
    """
    Print portfolio summary

    Args:
        totals (dict): Portfolio total from portfolio_manger
    """
    print("-" * 50)
    print("PORTFOLIO SUMMARY")
    print("-" * 50)
    print(f"Total Invested: ${totals['total-invested']}")
    print(f"Current Value: ${totals['total_current']:.2f}")

    gain_loss = totals["total_gain_loss"]
    gain_loss_pct = totals["total_gain_loss_pct"]
    sign = "+" if gain_loss >= 0 else ""
    color_start = "\033[92m" if gain_loss >= 0 else "\033[91m"
    color_end = "\033[0m"

    print(
        f"{color_start}Total Gain/Loss: {sign}${gain_loss:.2f}({sign}{gain_loss_pct:.2f}%){color_end}"
    )
    print(f"\nCash: ${totals['cash']:.2f}")
    print(f"Portfolio Value: ${totals['portfolio_value']:.2f}")
    print("=" * 50 + "\n")


def print_news(symbol, articles):
    """
    Print news headlines for stock

    Args:
        symbol (str): Stock ticker
        articles (list): List of news articles
    """
    if not articles:
        print(f"No recent news for {symbol}")
        return

    print(f"\n Recent news for {symbol}:")
    for article in articles:
        date_str = (
            article["date"].strftime("%Y-%m-%d")
            if hasattr(article["date"], "strftime")
            else str(article["date"])
        )
        print(f"    - {article['title']} ({date_str})")

