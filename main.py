"""
ezstox - Simple portfolio tracker
Entry
"""

from src.portfolio_manager import Portfolio
from src.data_fetcher import get_multiple_prices, get_stock_news
from src.reporter import print_header, print_position, print_news


def main():
    """Main application logic"""

    print_header()

    portfolio = Portfolio()

    symbols = portfolio.get_symbols()

    print("Fetching current prices...")
    current_prices = get_multiple_prices(symbols)
    print()

    print("💼 HOLDINGS\n" + "-"*50)
    for symbol in symbols:
        if symbol in current_prices:
            position = portfolio.calculate_position(symbol, current_prices[symbol])

    totals = portfolio.get_total_value(current_prices)
    print_summary(totals)

    print("\n📰 NEWS UPDATES\n" + "-"50)
    for symbol in symbols:
        articles = get.stock_news(symbol, limit = 2)
        print_news(symbol, articles)

    if __name__ == "__main__"
        main()
