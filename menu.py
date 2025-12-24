"""
Terminal menu interface for ezstox
Clean and simple menu system
"""

import os
import sys


def clear_screen():
    """Clear the terminal screen"""
    os.system("clear" if os.name == "posix" else "cls")


def print_banner():
    """Print simple banner"""
    print("\n" + "=" * 60)
    print("                      ezstox")
    print("              Your AI Portfolio Assistant")
    print("=" * 60 + "\n")


def print_menu():
    """Display main menu options"""
    print("What would you like to do?\n")
    print("  1. View Portfolio & Watchlist")
    print("  2. View News for Stocks")
    print("  3. Get AI Investment Advice (Full Analysis)")
    print("  4. Edit Portfolio")
    print("  5. Edit Watchlist")
    print("  6. Edit Cash Balance")
    print("  7. Settings & Info")
    print("  0. Exit")
    print()


def get_user_choice():
    """Get and validate user input"""
    while True:
        choice = input("Enter your choice (0-7): ").strip()
        if choice in ["0", "1", "2", "3", "4", "5", "6", "7"]:
            return choice
        print("Invalid choice. Please enter a number between 0-7.\n")


def pause():
    """Wait for user to press enter"""
    input("\nPress ENTER to continue...")


def confirm_action(message="Are you sure?"):
    """Ask user to confirm an action"""
    response = input(f"{message} (y/n): ").strip().lower()
    return response in ["y", "yes"]


def show_portfolio_info():
    """Display info about portfolio files"""
    print("\nPortfolio Files Location:")
    print("  - Holdings: data/portfolio.txt")
    print("  - Watchlist: data/watchlist.txt")
    print("  - Cash: data/cash.txt")
    print("\nFormat Info:")
    print("  - Portfolio: SYMBOL,SHARES,COST_BASIS")
    print("  - Watchlist: One symbol per line")
    print("  - Cash: Single number only")


def edit_file(filepath, file_description):
    """
    Simple file editor - opens in default text editor

    Args:
        filepath (str): Path to file to edit
        file_description (str): What the file contains
    """
    print(f"\nEditing {file_description}...")
    print(f"File: {filepath}\n")

    # Check if file exists
    if not os.path.exists(filepath):
        print(f"File doesn't exist yet. Creating it...")
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "w") as f:
            if "portfolio" in filepath:
                f.write("# Format: SYMBOL,SHARES,COST_BASIS\n")
                f.write("# Example: META,1.5,700.00\n\n")
            elif "watchlist" in filepath:
                f.write("# Format: One symbol per line\n")
                f.write("# Example:\n# AAPL\n# NVDA\n\n")
            elif "cash" in filepath:
                f.write("0")

    # Show current contents
    print("Current contents:")
    print("-" * 60)
    with open(filepath, "r") as f:
        contents = f.read()
        if contents.strip():
            print(contents)
        else:
            print("(empty file)")
    print("-" * 60)

    # Ask what to do
    print("\nWhat would you like to do?")
    print("  1. Open in text editor (nano/vim/notepad)")
    print("  2. Add a line manually")
    print("  3. View only (no changes)")
    print("  0. Cancel")

    choice = input("\nChoice: ").strip()

    if choice == "1":
        # Open in default editor
        editor = os.environ.get("EDITOR", "nano" if os.name == "posix" else "notepad")
        print(f"\nOpening with {editor}...")
        os.system(f"{editor} {filepath}")
        print("\nChanges saved!")

    elif choice == "2":
        # Manual add
        print("\nEnter line to add (or 'cancel' to abort):")

        if "portfolio" in filepath:
            print("Format: SYMBOL,SHARES,COST_BASIS")
            print("Example: AAPL,10,150.00")
        elif "watchlist" in filepath:
            print("Format: SYMBOL")
            print("Example: NVDA")
        elif "cash" in filepath:
            print("Format: Just the number")
            print("Example: 500")

        new_line = input("\n> ").strip()

        if new_line.lower() != "cancel" and new_line:
            with open(filepath, "a") as f:
                f.write(f"\n{new_line}")
            print("\nAdded successfully!")
        else:
            print("\nCancelled")

    elif choice == "3":
        print("\nView only - no changes made")

    else:
        print("\nCancelled")


def show_settings():
    """Display settings and info"""
    clear_screen()
    print_banner()
    print("SETTINGS & INFO\n")
    print("=" * 60)

    # Check API keys
    print("\nAPI Keys Status:")
    openai_key = os.getenv("OPENAI_API_KEY")
    fred_key = os.getenv("FRED_API_KEY")

    print(f"  - OpenAI: {'Configured' if openai_key else 'Not found'}")
    print(f"  - FRED (VIX data): {'Configured' if fred_key else 'Not found'}")

    if not openai_key or not fred_key:
        print("\nTo add API keys:")
        print("  macOS/Linux: Add to ~/.zshrc or ~/.bashrc")
        print("  export OPENAI_API_KEY='your-key'")
        print("  export FRED_API_KEY='your-key'")

    print("\n" + "=" * 60)
    show_portfolio_info()
    print("\n" + "=" * 60)

    print("\nCost per AI Analysis (GPT-4o-mini):")
    print("  - ~$0.003-0.005 per analysis")
    print("  - 2 analyses/day = ~$0.21/month")

    print("\nPerformance:")
    print("  - Portfolio view: ~5 seconds")
    print("  - AI analysis: ~60-90 seconds")

    print("\n" + "=" * 60)
    pause()


def run_menu():
    """Main menu loop"""
    while True:
        clear_screen()
        print_banner()
        print_menu()

        choice = get_user_choice()

        if choice == "0":
            print("\nThanks for using ezstox! See you later.\n")
            sys.exit(0)

        elif choice == "1":
            print("\nLoading portfolio view...\n")
            # Import here to avoid circular imports
            from main import main_portfolio_only

            main_portfolio_only()
            pause()

        elif choice == "2":
            print("\nFetching news articles...\n")
            from main import main_news_only

            main_news_only()
            pause()

        elif choice == "3":
            print("\nStarting AI analysis...\n")
            print("This will take 60-90 seconds...")
            print("   - Fetching stock data & news")
            print("   - Scraping article content")
            print("   - Getting market context")
            print("   - Running AI analysis\n")

            if confirm_action("Ready to start?"):
                from main import main_with_ai

                main_with_ai()
            else:
                print("\nCancelled")
            pause()

        elif choice == "4":
            clear_screen()
            print_banner()
            edit_file("data/portfolio.txt", "Portfolio Holdings")
            pause()

        elif choice == "5":
            clear_screen()
            print_banner()
            edit_file("data/watchlist.txt", "Watchlist")
            pause()

        elif choice == "6":
            clear_screen()
            print_banner()
            edit_file("data/cash.txt", "Cash Balance")
            pause()

        elif choice == "7":
            show_settings()


if __name__ == "__main__":
    try:
        run_menu()
    except KeyboardInterrupt:
        print("\n\nCaught Ctrl+C. Goodbye!\n")
        sys.exit(0)

