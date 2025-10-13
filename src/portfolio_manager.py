"""
Portfolio + Watchlist manager
"""

import os


class portfolio:
    """Manage portfolio holdings, watchlist, and cash"""

    def __init__(
        self,
        portfolio_file="data/portfolio.txt",
        watchlist_file="data/watchlist.txt",
        cash_file="data/cash,txt",
    ):
        self.portfolio_file = portfolio_file
        self.watchlist_file = watchlist_file
        self.cash_file = cash_file

        self.holdings = {}
        self.watchlist = []
        self.cash = 0

        self._ensure_files()
        self._load_portfolio()
        self._load_watchlist()
        self._load_cash()

    def _ensure_files(self):
        """Create data files if they don't exist"""
        os.makedirs("data", exist_ok=True)

        if not os.path.exists(self.portfolio_file):
            with open(self.portfolio_file, "w") as f:
                f.write("# Your holdings: SYMBOL,SHARES,COST_BASIS\n")
                f.write("# Example:\n META,1,750\n")

        if not os.path.exists(self.watchlist_file):
            with open(self.watchlist_file, "w") as f:
                f.write("# Stocks you're watching (One per line)\n")
                f.write("# Example: \n# CEG\n# NVDA")

        if not os.path.exists(self.cash_file):
            with open(self.cash_file, "w") as f:
                f.write("0")

    def _load_portfolio(self):
        """Load holdings from portfolio file"""
        if not os.path.exists(self.portfolio_file):
            return

        with open(self.portfolio_file, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue

                try:
                    parts = line.split(",")
                    symbol = parts[0].strip().upper()
                    shares = float(parts[1].strip())
                    cost_basis = float(parts[2].strip())

                    self.holdings[symbol] = {"shares": shares, "cost_basis": cost_basis}
                except Exception as e:
                    print(f"⚠️  Skipping invalid portfolio line: {line}")
        if self.holdings:
            print(f"✅ Portfolio {len(self.holdings)} holdings")

    def _load_watchlist(self):
        """Load watchlist from file"""
        if not os.path.exists(self.watchlist_file):
            return
        with open(self.watchlist_file, "r") as f:
            for line in f:
                line = line.strip().upper()
                if line and not line.startswith("#"):
                    self.watchlist.append(line)

        if self.watchlist:
            print(f"✅ Watchlist: {len(self.watchlist)} stocks")

    def _load_cash(self):
        """Load cash balance"""
        if not os.path.exists(self.cash_file):
            return

        try:
            with open(self.cash_file, "r") as f:
                self.cash = float(f.read().strip())
            print(f"✅ Cash: ${self.cash:,.2f}")
        except:
            self.cash = 0

    def get_portfolio_symbols(self):
        """Get symbols owned"""
        return list(self.holdings.keys())

    def get_watchlist_symbols(self):
        """Get symbols watched"""
        return self.watchlist

    def get_all_symbols(self):
        """Get all symbols (Portfolio + Watchlinst)"""
        return list(set((self.get_portfolio_symbols() + self.get_watchlist_symbols())))

    def calculate_position(self, symbol, current_price):
        """Calculate P&L"""
        if symbol not in self.holdings:
            return None

        holding = self.holdings[symbol]
        shares = holding["shares"]
        cost_basis = holding["cost_basis"]

        total_cost = shares * cost_basis
        current_value = shares * current_price
        gain_loss = current_value - total_cost
        gain_loss_pct = (gain_loss / total_cost) * 100 if total_cost > 0 else 0

        return {
            "symbol": symbol,
            "shares": shares,
            "cost_basis": cost_basis,
            "current_price": current_price,
            "total_cost": total_cost,
            "current_value": current_value,
            "gain_loss": gain_loss,
            "gain_loss_pct": gain_loss_pct,
        }

    def get_total_value(self, current_prices):
        """Calculate total portfolio value"""
        total_invested = 0
        total_current = 0

        for symbol, holding in self.holdings.items():
            if symbol in current_prices:
                shares = holding["shares"]
                cost_basis = holding["cost_basis"]
                current_price = current_prices[symbol]

                total_invested += shares * cost_basis
                total_current += shares * current_price

        total_gain_loss = total_current - total_invested
        total_gain_loss_pct = (
            (total_gain_loss / total_gain_loss) * 100 if total_invested > 0 else 0
        )

        return {
            "total_invested": total_invested,
            "total_current": total_current,
            "total_gain_loss": total_gain_loss,
            "total_gain_loss_pct": total_gain_loss_pct,
            "cash": self.cash,
            "portfolio_value": total_current + self.cash,
        }
