"""
ezstox - Simple portfolio tracker
Entry
"""

import sys
from src.portfolio_manager import Portfolio
from src.data_fetcher import get_multiple_prices, get_stock_news
from src.reporter import print_header, print_position, print_summary, print_news


def main():
    """Main application logic"""

    print_header()

    # Load portfolio and watchlist
    portfolio = Portfolio()
    print()

    owned_symbols = portfolio.get_portfolio_symbols()
    watched_symbols = portfolio.get_watchlist_symbols()

    all_symbols = portfolio.get_all_symbols()

    if not all_symbols:
        print("⚠️ No holdings or watchlist found!")
        print("Edit data/portolio.txt and data/watchlist.txt\n")
        return

    print("Fetching current prices...")
    current_prices = get_multiple_prices(all_symbols)
    print()

    # What I own
    if owned_symbols:
        print("💼 HOLDINGS\n" + "-" * 50)
        for symbol in owned_symbols:
            if symbol in current_prices:
                position = portfolio.calculate_position(symbol, current_prices[symbol])
                print_position(position)

        totals = portfolio.get_total_value(current_prices)
        print_summary(totals)
    else:
        print("💼 Your PORTFOLIO\n" + "-" * 50)
        print("No holdings (100% Cash)")
        print(f"Cash: %{portfolio.cash:,.2f}\n")
    # Watchlist

    if watched_symbols:
        print("\n👀 WATCHLIST\n" + "-" * 50)
        for symbol in watched_symbols:
            if symbol in current_prices:
                price = current_prices[symbol]
                print(f"{symbol}: ${price:.2f}")
        print()

    # News
    print("\n📰 NEWS UPDATES\n" + "-" * 50)

    if owned_symbols:
        print("Your Holdings:")
        for symbol in owned_symbols:
            articles = get_stock_news(symbol, limit=2)
            print_news(symbol, articles)

    if watched_symbols:
        print("\nWatchlist:")
        for symbol in watched_symbols:
            articles = get_stock_news(symbol, limit=2)
            print_news(symbol, articles)


def main_with_ai():
    """Run portfolio tracker with AI advisor"""
    print("DEBUG: Entered main_with_ai()")
    
    try:
        from src.llm_advisor import get_ai_advice
        print("DEBUG: Successfully imported get_ai_advice")
    except Exception as e:
        print(f"DEBUG: Import failed: {e}")
        return
    
    # Load portfolio
    print("DEBUG: Loading portfolio...")
    portfolio = Portfolio()
    print("DEBUG: Portfolio loaded")
    
    # Get AI advice
    print("DEBUG: Calling get_ai_advice()...")
    advice = get_ai_advice(portfolio)
    
    # Display advice
    print(advice)
    print()


if __name__ == "__main__":
    if '--ai-advice' in sys.argv:
        print("DEBUG: AI advice flag detected!")
        main_with_ai()
    else:
        print("DEBUGL Running normal mode")
        main()
