"""
ezstox - portfolio tracker & AI advisor for the terminal.

Start it with ./ezstox (which sets everything up on first run). Running
`python main.py` directly also works once dependencies are installed.

    ./ezstox                  interactive menu
    ./ezstox dashboard        holdings, P&L and watchlist
    ./ezstox news [TICKER]    headlines for your stocks, or one ticker
    ./ezstox lookup TICKER    quote, key stats and news for any ticker
    ./ezstox ai               full AI analysis of your portfolio
"""

import argparse
import sys

try:
    from src import app
    from src.portfolio_manager import Portfolio
    from src.ui import console
except ImportError as e:
    sys.exit(f"Missing dependency ({e.name}). Start ezstox with ./ezstox and it will install everything for you.")

# Old flags from earlier versions keep working
LEGACY_FLAGS = {"--portfolio": "dashboard", "--ai-advice": "ai", "--menu": "menu"}


def parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="ezstox",
        description="Portfolio tracker & AI advisor for the terminal. Run with no arguments for the interactive menu.",
    )
    commands = parser.add_subparsers(dest="command", metavar="command")
    commands.add_parser("menu", help="interactive menu (default)")
    commands.add_parser("dashboard", help="holdings, P&L and watchlist")
    news = commands.add_parser("news", help="headlines for your stocks, or one ticker")
    news.add_argument("symbol", nargs="?", help="ticker, e.g. AAPL (default: all your stocks)")
    lookup = commands.add_parser("lookup", help="quote, key stats and news for any ticker")
    lookup.add_argument("symbol", help="ticker, e.g. NVDA")
    commands.add_parser("ai", help="full AI analysis of your portfolio")
    commands.add_parser("reports", help="browse saved AI analyses")
    return parser.parse_args([LEGACY_FLAGS.get(arg, arg) for arg in argv])


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    command = args.command or "menu"

    if command == "menu":
        app.run_menu()
        return

    portfolio = Portfolio()
    for warning in portfolio.warnings:
        console.print(f"[warn]![/] {warning}")

    if command == "dashboard":
        app.dashboard(portfolio)
    elif command == "news":
        app.news(portfolio, symbol=args.symbol.upper() if args.symbol else None, ask=False)
    elif command == "lookup":
        app.lookup(portfolio, symbol=args.symbol.upper(), offer_add=False)
    elif command == "ai":
        app.ai_analysis(portfolio, confirm=False)
    elif command == "reports":
        app.reports(portfolio)


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        console.print("\n[muted]Interrupted.[/]")
        sys.exit(130)
