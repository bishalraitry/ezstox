"""
Portfolio + Watchlist manager

Holdings, watchlist and cash live in plain text files under data/, so
they stay easy to read and hand-edit. The app can also edit them for you
(see set_holding, add_to_watchlist, set_cash...) and writes changes back
atomically, so a crash mid-save can't corrupt your data.
"""

import os
import re
import tempfile

from src import config

SYMBOL_PATTERN = re.compile(r"^\^?[A-Z0-9][A-Z0-9.\-=]{0,14}$")


def is_valid_symbol(symbol):
    """Ticker symbols: letters/digits plus . - = ^ (e.g. BRK-B, IGLN.L, ^VIX, GC=F)."""
    return bool(SYMBOL_PATTERN.match(symbol.strip().upper()))


def _format_number(value):
    """Write numbers without float noise or needless trailing zeros (1.5, 10, 0.125)."""
    return f"{value:.8f}".rstrip("0").rstrip(".")


def _atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, path)


class Portfolio:
    """Manage portfolio holdings, watchlist, and cash"""

    def __init__(self, data_dir=None):
        data_dir = data_dir or config.DATA_DIR
        self.portfolio_file = data_dir / "portfolio.txt"
        self.watchlist_file = data_dir / "watchlist.txt"
        self.cash_file = data_dir / "cash.txt"

        self.holdings = {}
        self.watchlist = []
        self.cash = 0.0
        self.warnings = []

        self._load_portfolio()
        self._load_watchlist()
        self._load_cash()

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    @staticmethod
    def _data_lines(path):
        if not path.exists():
            return []
        lines = (line.strip() for line in path.read_text().splitlines())
        return [line for line in lines if line and not line.startswith("#")]

    def _load_portfolio(self):
        """Load holdings from portfolio file (SYMBOL,SHARES,COST_BASIS per line)"""
        for line in self._data_lines(self.portfolio_file):
            try:
                symbol, shares, cost_basis = (part.strip() for part in line.split(","))
                self.holdings[symbol.upper()] = {
                    "shares": float(shares),
                    "cost_basis": float(cost_basis),
                }
            except ValueError:
                self.warnings.append(f"Skipped invalid line in portfolio.txt: {line!r}")

    def _load_watchlist(self):
        """Load watchlist (one symbol per line)"""
        for line in self._data_lines(self.watchlist_file):
            symbol = line.upper()
            if symbol not in self.watchlist:
                self.watchlist.append(symbol)

    def _load_cash(self):
        """Load cash balance"""
        lines = self._data_lines(self.cash_file)
        if not lines:
            return
        try:
            self.cash = float(lines[0].replace(",", ""))
        except ValueError:
            self.warnings.append(f"Couldn't read cash.txt ({lines[0]!r}) - using 0")

    # ------------------------------------------------------------------
    # Saving
    # ------------------------------------------------------------------

    def save(self):
        """Write holdings, watchlist and cash back to their files"""
        holdings = "".join(
            f"{symbol},{_format_number(h['shares'])},{_format_number(h['cost_basis'])}\n"
            for symbol, h in self.holdings.items()
        )
        _atomic_write(
            self.portfolio_file,
            "# Holdings: SYMBOL,SHARES,COST_BASIS (one per line)\n" + holdings,
        )
        _atomic_write(
            self.watchlist_file,
            "# Watchlist: one symbol per line\n" + "".join(f"{s}\n" for s in self.watchlist),
        )
        _atomic_write(self.cash_file, f"{_format_number(self.cash)}\n")

    # ------------------------------------------------------------------
    # Editing
    # ------------------------------------------------------------------

    def set_holding(self, symbol, shares, cost_basis):
        """Add a holding, or replace an existing one"""
        self.holdings[symbol.strip().upper()] = {
            "shares": float(shares),
            "cost_basis": float(cost_basis),
        }
        self.save()

    def remove_holding(self, symbol):
        removed = self.holdings.pop(symbol.strip().upper(), None) is not None
        if removed:
            self.save()
        return removed

    def add_to_watchlist(self, symbol):
        symbol = symbol.strip().upper()
        if symbol in self.watchlist:
            return False
        self.watchlist.append(symbol)
        self.save()
        return True

    def remove_from_watchlist(self, symbol):
        symbol = symbol.strip().upper()
        if symbol not in self.watchlist:
            return False
        self.watchlist.remove(symbol)
        self.save()
        return True

    def set_cash(self, amount):
        self.cash = float(amount)
        self.save()

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    @property
    def is_empty(self):
        return not self.holdings and not self.watchlist

    def get_portfolio_symbols(self):
        """Get symbols owned"""
        return list(self.holdings.keys())

    def get_watchlist_symbols(self):
        """Get symbols watched"""
        return list(self.watchlist)

    def get_all_symbols(self):
        """Get all symbols (holdings first, then watchlist), without duplicates"""
        return list(dict.fromkeys(self.get_portfolio_symbols() + self.get_watchlist_symbols()))

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
        """Calculate total portfolio value (holdings without a price are left out)"""
        total_invested = 0
        total_current = 0

        for symbol, holding in self.holdings.items():
            if symbol in current_prices:
                total_invested += holding["shares"] * holding["cost_basis"]
                total_current += holding["shares"] * current_prices[symbol]

        total_gain_loss = total_current - total_invested
        total_gain_loss_pct = (
            (total_gain_loss / total_invested) * 100 if total_invested > 0 else 0
        )

        return {
            "total_invested": total_invested,
            "total_current": total_current,
            "total_gain_loss": total_gain_loss,
            "total_gain_loss_pct": total_gain_loss_pct,
            "cash": self.cash,
            "portfolio_value": total_current + self.cash,
        }
