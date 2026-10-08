"""
Screens and the interactive menu.

Each screen is a plain function that renders one view. The menu loop
clears the terminal, runs a screen, then waits for Enter. The same
functions back the command-line shortcuts in main.py.
"""

import sys
from concurrent.futures import ThreadPoolExecutor

from rich.console import Group
from rich.prompt import Confirm, FloatPrompt, Prompt
from rich.table import Table
from rich.text import Text

from src import ui
from src.config import API_KEYS, DATA_DIR, ENV_FILE, REPORTS_DIR, get_setting, openai_model, save_setting
from src.portfolio_manager import Portfolio, is_valid_symbol
from src.ui import console


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _data():
    """Imported lazily so the menu appears instantly (pandas/yfinance load on first use)."""
    from src import data_fetcher

    return data_fetcher


def _ask_symbol(prompt="Ticker", default=None):
    """Ask for a ticker; pressing Enter with no default returns None (cancel)."""
    while True:
        symbol = Prompt.ask(f"[accent]›[/] {prompt}", default=default or "", show_default=bool(default))
        symbol = symbol.strip().upper()
        if not symbol:
            return None
        if is_valid_symbol(symbol):
            return symbol
        ui.error(f"'{symbol}' doesn't look like a ticker (e.g. AAPL, BRK-B, VUSA.L)")


def _ask_amount(prompt, default=None, minimum=0.0, allow_zero=True):
    """Ask for a number. With no default, an answer is required (Rich re-asks on Enter)."""
    kwargs = {"default": default} if default is not None else {}
    while True:
        value = FloatPrompt.ask(f"[accent]›[/] {prompt}", **kwargs)
        if value > minimum or (allow_zero and value == minimum):
            return value
        ui.error(f"Enter a number {'≥' if allow_zero else '>'} {minimum:g}")


def _split_symbols(raw):
    return [s for s in raw.replace(",", " ").upper().split() if s]


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


def dashboard(portfolio):
    console.print(ui.header("Dashboard"))
    if portfolio.is_empty:
        console.print(
            ui.empty_state(
                "Your portfolio is empty",
                "Choose Manage portfolio from the menu to add holdings, watchlist symbols and cash.",
            )
        )
        if portfolio.cash:
            ui.hint(f"Cash: {ui.money(portfolio.cash)}")
        return

    with console.status("[muted]Fetching live prices…[/]", spinner="dots"):
        quotes = _data().get_quotes(portfolio.get_all_symbols())

    prices = {s: q["price"] for s, q in quotes.items() if q}
    totals = portfolio.get_total_value(prices)
    portfolio_value = totals["portfolio_value"]

    rows = []
    day_pnl = 0.0
    for symbol, holding in portfolio.holdings.items():
        quote = quotes.get(symbol)
        row = {"symbol": symbol, "shares": holding["shares"], "cost_basis": holding["cost_basis"], "price": None}
        if quote:
            position = portfolio.calculate_position(symbol, quote["price"])
            if quote["change"] is not None:
                day_pnl += holding["shares"] * quote["change"]
            row.update(
                price=quote["price"],
                day_pct=quote["change_pct"],
                value=position["current_value"],
                pnl=position["gain_loss"],
                pnl_pct=position["gain_loss_pct"],
                weight=position["current_value"] / portfolio_value * 100 if portfolio_value else 0,
                history=quote["history"],
            )
        rows.append(row)

    previous_value = totals["total_current"] - day_pnl
    day_pct = day_pnl / previous_value * 100 if previous_value else None
    cash_pct = portfolio.cash / portfolio_value * 100 if portfolio_value else 0
    any_priced = any(row["price"] is not None for row in rows)

    if any_priced:
        today = ui.tile("Today", ui.pnl(day_pnl), ui.change(day_pct))
        total_return = ui.tile(
            "Total return",
            ui.pnl(totals["total_gain_loss"]),
            Text.assemble(ui.change(totals["total_gain_loss_pct"]), (f" on {ui.money(totals['total_invested'])}", "muted")),
        )
    else:
        unpriced = "no live prices" if rows else "no holdings"
        today = ui.tile("Today", Text("—", style="muted"), unpriced)
        total_return = ui.tile("Total return", Text("—", style="muted"), unpriced)

    console.print(
        ui.tiles(
            ui.tile("Portfolio value", ui.money(portfolio_value), f"{len(portfolio.holdings)} holdings + cash"),
            today,
            total_return,
            ui.tile("Cash", ui.money(portfolio.cash), f"{cash_pct:.1f}% of portfolio"),
        )
    )

    if rows:
        console.print(ui.section(ui.holdings_table(rows), "Holdings"))

    watch = [s for s in portfolio.watchlist if s not in portfolio.holdings]
    if watch:
        watch_rows = []
        for symbol in watch:
            quote = quotes.get(symbol)
            watch_rows.append(
                {
                    "symbol": symbol,
                    "price": quote["price"] if quote else None,
                    "day_pct": quote["change_pct"] if quote else None,
                    "change_5d_pct": quote["change_5d_pct"] if quote else None,
                    "history": quote["history"] if quote else None,
                }
            )
        console.print(ui.section(ui.watchlist_table(watch_rows), "Watchlist"))

    missing = [s for s, q in quotes.items() if not q]
    if missing:
        ui.warn(f"Couldn't fetch prices for {', '.join(missing)} — totals exclude them.")


# ---------------------------------------------------------------------------
# News
# ---------------------------------------------------------------------------


def news(portfolio, symbol=None, ask=True):
    console.print(ui.header("News"))
    symbols = portfolio.get_all_symbols()

    if symbol is None and ask:
        if symbols:
            ui.hint(f"Your stocks: {', '.join(symbols)}")
            choice = _ask_symbol("Ticker (Enter for all your stocks)", default="")
        else:
            choice = _ask_symbol("Ticker")
            if not choice:
                return
        symbol = choice or None

    targets = [symbol] if symbol else symbols
    if not targets:
        console.print(ui.empty_state("Nothing to show", "Add symbols in Manage portfolio, or enter a ticker."))
        return

    limit = 8 if symbol else 3
    label = symbol or f"{len(targets)} symbols"
    with console.status(f"[muted]Fetching headlines for {label}…[/]", spinner="dots"):
        results = _data().get_news_for(targets, limit=limit)

    for target, articles in results.items():
        console.print(ui.news_panel(target, articles, show_summary=bool(symbol)))
    ui.hint("Tip: headlines are clickable links in terminals that support it (iTerm2, VS Code, Windows Terminal…)")


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------


def lookup(portfolio, symbol=None, offer_add=True):
    console.print(ui.header("Lookup"))
    symbol = symbol or _ask_symbol("Ticker to look up")
    if not symbol:
        return

    data = _data()
    with console.status(f"[muted]Looking up {symbol}…[/]", spinner="dots"):
        with ThreadPoolExecutor(max_workers=3) as pool:
            f_quote = pool.submit(data.get_quote, symbol)
            f_fund = pool.submit(data.get_fundamentals, symbol)
            f_news = pool.submit(data.get_stock_news, symbol, 5)
            quote, fund, articles = f_quote.result(), f_fund.result(), f_news.result()

    if not quote and not fund:
        ui.error(f"Couldn't find any data for {symbol}. Check the ticker (London listings end in .L, e.g. VUSA.L).")
        return

    name = fund.get("long_name") or symbol
    kind = fund.get("quote_type", "")
    details = " · ".join(p for p in (kind if kind and kind != "EQUITY" else None, fund.get("sector"), fund.get("industry")) if p)

    title = Text.assemble((symbol, "accent"), "  ", (name, "bold"))
    if details:
        title.append(f"\n{details}", style="muted")

    price_line = Text()
    if quote:
        price_line.append(ui.money(quote["price"]), style="bold")
        if quote.get("currency") and quote["currency"] != "USD":
            price_line.append(f" {quote['currency']}", style="muted")
        price_line.append("   ")
        if quote["change"] is not None:
            price_line.append_text(ui.pnl(quote["change"]))
            price_line.append("  ")
        price_line.append_text(ui.change(quote["change_pct"]))
        price_line.append("  today", style="muted")
    else:
        price_line.append("Price unavailable", style="muted")

    chart = ui.sparkline(quote["history"], width=30) if quote else Text("")
    top = Table.grid(expand=True)
    top.add_column()
    top.add_column(justify="right")
    top.add_row(Group(title, Text(""), price_line), Group(Text("1 month", style="muted"), chart))
    console.print(ui.section(top, "Quote"))

    stats = Table.grid(expand=True, padding=(0, 3))
    for _ in range(4):
        stats.add_column()

    def stat(label, value):
        return Text.assemble((f"{label}\n", "muted"), (value, "bold"))

    target = fund.get("analyst_target_mean")
    upside = ""
    if target and quote:
        upside = f" ({(target - quote['price']) / quote['price'] * 100:+.1f}%)"
    dividend = fund.get("dividend_yield")
    recommendation = (fund.get("recommendation") or "").replace("_", " ").title()

    stats.add_row(
        stat("Market cap", ui.compact(fund.get("market_cap") or fund.get("total_assets"))),
        stat("P/E (ttm)", f"{fund['pe_ratio']:.2f}" if fund.get("pe_ratio") else "—"),
        stat("Forward P/E", f"{fund['forward_pe']:.2f}" if fund.get("forward_pe") else "—"),
        stat("Beta", f"{fund['beta']:.2f}" if fund.get("beta") else "—"),
    )
    stats.add_row(Text(""), Text(""), Text(""), Text(""))
    stats.add_row(
        stat("Analyst target", (ui.money(target) + upside) if target else "—"),
        stat("Consensus", recommendation or "—"),
        stat("Dividend yield", f"{dividend:.2f}%" if dividend else "—"),
        stat("5-day change", f"{quote['change_5d_pct']:+.2f}%" if quote and quote["change_5d_pct"] is not None else "—"),
    )
    range_line = Text.assemble(("52-week range   ", "muted"))
    range_line.append_text(
        ui.range_bar(fund.get("fifty_two_week_low"), fund.get("fifty_two_week_high"), quote["price"] if quote else None)
    )
    console.print(ui.section(Group(stats, Text(""), range_line), "Key stats"))
    console.print(ui.news_panel(f"{symbol} news", articles, show_summary=True))

    tracked = symbol in portfolio.holdings or symbol in portfolio.watchlist
    if offer_add and not tracked and Confirm.ask(f"[accent]›[/] Add {symbol} to your watchlist?", default=False):
        portfolio.add_to_watchlist(symbol)
        ui.success(f"Added {symbol} to your watchlist")


# ---------------------------------------------------------------------------
# AI analysis
# ---------------------------------------------------------------------------


def _ensure_openai_key():
    if get_setting("OPENAI_API_KEY"):
        return True
    console.print(
        ui.empty_state(
            "OpenAI API key needed",
            "AI analysis uses your OpenAI account (≈$0.003–0.005 per run with gpt-4o-mini).\n"
            "Get a key at platform.openai.com/api-keys — it's saved to .env in the project folder.",
        )
    )
    key = Prompt.ask("[accent]›[/] Paste your OpenAI API key (Enter to cancel)", password=True, default="", show_default=False).strip()
    if not key:
        return False
    save_setting("OPENAI_API_KEY", key)
    ui.success("Key saved")
    return True


def ai_analysis(portfolio, confirm=True):
    from src.llm_advisor import get_ai_advice, save_report

    console.print(ui.header("AI analysis"))
    if portfolio.is_empty:
        console.print(ui.empty_state("Nothing to analyse", "Add holdings or watchlist symbols in Manage portfolio first."))
        return
    if not _ensure_openai_key():
        return

    symbols = portfolio.get_all_symbols()
    console.print(
        ui.section(
            Text.assemble(
                ("Reviews ", "muted"), (f"{len(symbols)} symbols", "bold"), (f" ({', '.join(symbols)})", "muted"), "\n",
                ("Gathers prices, fundamentals, news and market context, reads the key articles,\n", "muted"),
                ("then asks ", "muted"), (openai_model(), "bold"), (" for a structured review. Usually 20–40 seconds.", "muted"),
            ),
            "What happens",
        )
    )
    if confirm and not Confirm.ask("[accent]›[/] Start analysis?", default=True):
        return

    try:
        with ui.StageProgress() as progress:
            result = get_ai_advice(portfolio, progress)
    except RuntimeError as e:
        ui.error(str(e))
        return

    path = save_report(result)
    created = result["created"]
    console.print()
    console.print(ui.report_panel(result["advice"], "AI analysis", f"{result['model']} · {created:%d %b %Y, %H:%M}"))
    console.print(ui.section(ui.sources_table(result["sources"]), "Sources", "✓ = full article read"))
    ui.success(f"Saved to [bold]{path.relative_to(REPORTS_DIR.parent)}[/]")


# ---------------------------------------------------------------------------
# Manage portfolio
# ---------------------------------------------------------------------------


def _portfolio_overview(portfolio):
    holdings = ui.data_table(("Symbol", "left"), ("Shares", "right"), ("Avg cost", "right"), ("Cost basis", "right"))
    for symbol, h in portfolio.holdings.items():
        holdings.add_row(Text(symbol, style="bold"), ui.number(h["shares"]), ui.money(h["cost_basis"]), ui.money(h["shares"] * h["cost_basis"]))
    if not portfolio.holdings:
        holdings.add_row(Text("No holdings yet", style="muted"), "", "", "")

    watch = Text(", ".join(portfolio.watchlist) if portfolio.watchlist else "Empty", style="bold" if portfolio.watchlist else "muted")
    cash = Text(ui.money(portfolio.cash), style="bold")
    return Group(
        ui.section(holdings, "Holdings"),
        ui.tiles(ui.tile("Watchlist", watch), ui.tile("Cash", cash)),
    )


def _add_holding(portfolio):
    symbol = _ask_symbol("Ticker")
    if not symbol:
        return None
    existing = portfolio.holdings.get(symbol)
    if existing:
        ui.hint(f"You hold {ui.number(existing['shares'])} {symbol} at {ui.money(existing['cost_basis'])} — new values will replace these.")

    with console.status(f"[muted]Checking {symbol}…[/]", spinner="dots"):
        price = _data().get_stock_price(symbol)
    if price is None:
        ui.warn(f"Couldn't find a live price for {symbol}.")
        if not Confirm.ask("[accent]›[/] Add it anyway?", default=False):
            return None
    else:
        ui.hint(f"{symbol} is trading at {ui.money(price)}")

    shares = _ask_amount("Shares", default=existing["shares"] if existing else None, allow_zero=False)
    cost = _ask_amount(
        "Average cost per share",
        default=existing["cost_basis"] if existing else price,
    )
    portfolio.set_holding(symbol, shares, cost)
    return True, f"{'Updated' if existing else 'Added'} {symbol}: {ui.number(shares)} shares at {ui.money(cost)}"


def _remove_holding(portfolio):
    if not portfolio.holdings:
        return False, "No holdings to remove"
    symbol = _ask_symbol("Remove which holding? (Enter to cancel)", default="")
    if not symbol:
        return None
    if symbol not in portfolio.holdings:
        return False, f"You don't hold {symbol}"
    if not Confirm.ask(f"[accent]›[/] Remove {symbol}?", default=False):
        return None
    portfolio.remove_holding(symbol)
    return True, f"Removed {symbol}"


def _add_watchlist(portfolio):
    raw = Prompt.ask("[accent]›[/] Tickers to watch (space or comma separated)", default="", show_default=False)
    symbols = [s for s in _split_symbols(raw) if s not in portfolio.watchlist]
    invalid = [s for s in symbols if not is_valid_symbol(s)]
    for s in invalid:
        ui.error(f"Skipped '{s}' — not a valid ticker")
    symbols = [s for s in symbols if s not in invalid]
    if not symbols:
        return None

    with console.status("[muted]Checking tickers…[/]", spinner="dots"):
        quotes = _data().get_quotes(symbols)
    unknown = [s for s, q in quotes.items() if not q]
    if unknown and not Confirm.ask(f"[accent]›[/] No price found for {', '.join(unknown)}. Add anyway?", default=False):
        symbols = [s for s in symbols if s not in unknown]

    for s in symbols:
        portfolio.add_to_watchlist(s)
    return (True, f"Watching {', '.join(symbols)}") if symbols else None


def _remove_watchlist(portfolio):
    if not portfolio.watchlist:
        return False, "Watchlist is already empty"
    ui.hint(f"Watching: {', '.join(portfolio.watchlist)}")
    raw = Prompt.ask("[accent]›[/] Tickers to remove", default="", show_default=False)
    removed = [s for s in _split_symbols(raw) if portfolio.remove_from_watchlist(s)]
    return (True, f"Removed {', '.join(removed)}") if removed else (False, "None of those are on your watchlist")


def _set_cash(portfolio):
    amount = _ask_amount("Cash balance", default=portfolio.cash)
    portfolio.set_cash(amount)
    return True, f"Cash set to {ui.money(amount)}"


MANAGE_ACTIONS = [
    ("1", "Add or update holding", _add_holding),
    ("2", "Remove holding", _remove_holding),
    ("3", "Add to watchlist", _add_watchlist),
    ("4", "Remove from watchlist", _remove_watchlist),
    ("5", "Set cash balance", _set_cash),
]


def manage(portfolio):
    message = None
    while True:
        console.clear()
        console.print(ui.header("Manage portfolio"))
        console.print(_portfolio_overview(portfolio))
        console.print(ui.menu([(k, label, "") for k, label, _ in MANAGE_ACTIONS] + [("0", "Back", "")]))
        if message:
            ok, text = message
            (ui.success if ok else ui.warn)(text)
            message = None
        ui.hint(f"Saved automatically to {DATA_DIR.relative_to(DATA_DIR.parent)}/ — you can hand-edit those files too.")

        choice = Prompt.ask("[accent]›[/] Choose", choices=[k for k, _, _ in MANAGE_ACTIONS] + ["0"], show_choices=False)
        if choice == "0":
            return
        action = next(fn for k, _, fn in MANAGE_ACTIONS if k == choice)
        try:
            message = action(portfolio)
        except KeyboardInterrupt:
            message = None


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


def reports(portfolio=None):
    from src.llm_advisor import list_reports

    console.print(ui.header("Reports"))
    paths = list_reports()
    if not paths:
        console.print(ui.empty_state("No reports yet", "Every AI analysis is saved here automatically."))
        return

    shown = paths[:15]
    table = ui.data_table(("#", "right"), ("Date", "left"), ("Time", "left"), ("File", "left"))
    for i, path in enumerate(shown, 1):
        stamp = path.stem
        date, _, time = stamp.partition("_")
        table.add_row(Text(str(i), style="key"), date, f"{time[:2]}:{time[2:4]}" if len(time) >= 4 else time, Text(path.name, style="muted"))
    console.print(ui.section(table, "Saved AI analyses", f"{len(paths)} total"))

    choice = Prompt.ask("[accent]›[/] Open which report? (Enter to go back)", default="", show_default=False).strip()
    if not choice.isdigit() or not 1 <= int(choice) <= len(shown):
        return
    path = shown[int(choice) - 1]
    console.clear()
    console.print(ui.header("Reports"))
    console.print(ui.report_panel(path.read_text(), path.name))


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def _masked(value):
    return f"{value[:3]}…{value[-4:]}" if len(value) > 10 else "set"


def settings(portfolio=None):
    while True:
        console.clear()
        console.print(ui.header("Settings"))

        keys = ui.data_table(("Service", "left"), ("Status", "left"), ("Used for", "left"))
        for env, label, purpose, required in API_KEYS:
            value = get_setting(env)
            if value:
                status = Text(f"✓ {_masked(value)}", style="ok")
            else:
                status = Text("✗ not set" if required else "– not set", style="err" if required else "muted")
            keys.add_row(Text(label, style="bold"), status, Text(purpose, style="muted"))
        console.print(ui.section(keys, "API keys", f"stored in {ENV_FILE.name}"))

        info = ui.data_table(("Setting", "left"), ("Value", "left"))
        info.add_row("AI model", Text(openai_model(), style="bold"))
        info.add_row("Holdings", str(DATA_DIR / "portfolio.txt"))
        info.add_row("Watchlist", str(DATA_DIR / "watchlist.txt"))
        info.add_row("Cash", str(DATA_DIR / "cash.txt"))
        info.add_row("Reports", str(REPORTS_DIR))
        info.add_row("Python", sys.version.split()[0])
        console.print(ui.section(info, "Configuration"))

        console.print(ui.menu([("1", "Set OpenAI API key", ""), ("2", "Set Jina API key", "optional"), ("3", "Change AI model", ""), ("0", "Back", "")]))
        choice = Prompt.ask("[accent]›[/] Choose", choices=["1", "2", "3", "0"], show_choices=False)
        if choice == "0":
            return
        if choice in ("1", "2"):
            env = "OPENAI_API_KEY" if choice == "1" else "JINA_API_KEY"
            value = Prompt.ask("[accent]›[/] Paste key (Enter to keep current)", password=True, default="", show_default=False).strip()
            if value:
                save_setting(env, value)
        elif choice == "3":
            model = Prompt.ask("[accent]›[/] OpenAI model", default=openai_model()).strip()
            if model:
                save_setting("OPENAI_MODEL", model)


# ---------------------------------------------------------------------------
# Main menu
# ---------------------------------------------------------------------------

MENU = [
    ("1", "Dashboard", "Holdings, P&L and watchlist at a glance", dashboard),
    ("2", "News", "Latest headlines for your stocks", news),
    ("3", "Lookup", "Price, key stats and news for any ticker", lookup),
    ("4", "AI analysis", "Full AI review of your portfolio", ai_analysis),
    ("5", "Manage portfolio", "Edit holdings, watchlist and cash", manage),
    ("6", "Reports", "Browse saved AI analyses", reports),
    ("7", "Settings", "API keys, model and data files", settings),
]

# Screens that manage their own screen loop and don't need "press Enter"
SELF_CONTAINED = {manage, settings}


def _status_line(portfolio):
    line = Text("  ")
    line.append(f"{len(portfolio.holdings)} holdings", style="bold")
    line.append("  ·  ", style="faint")
    line.append(f"{len(portfolio.watchlist)} watching", style="bold")
    line.append("  ·  ", style="faint")
    line.append(f"{ui.money(portfolio.cash)} cash", style="bold")
    line.append("  ·  ", style="faint")
    if get_setting("OPENAI_API_KEY"):
        line.append("AI ready", style="ok")
    else:
        line.append("AI needs an OpenAI key (Settings)", style="warn")
    return line


def run_menu():
    while True:
        portfolio = Portfolio()
        console.clear()
        console.print(ui.header())
        console.print(_status_line(portfolio))
        for warning in portfolio.warnings:
            ui.warn(warning)
        console.print(ui.menu([(k, label, desc) for k, label, desc, _ in MENU] + [("0", "Quit", "")]))

        try:
            choice = Prompt.ask("[accent]›[/] Choose", choices=[k for k, *_ in MENU] + ["0", "q"], show_choices=False)
        except (KeyboardInterrupt, EOFError):
            choice = "0"
        if choice in ("0", "q"):
            console.print("\n[muted]See you next time.[/]\n")
            return

        screen = next(fn for k, _, _, fn in MENU if k == choice)
        console.clear()
        try:
            screen(portfolio)
        except KeyboardInterrupt:
            console.print()
            ui.hint("Cancelled.")
        except EOFError:
            return
        if screen not in SELF_CONTAINED:
            ui.pause()
