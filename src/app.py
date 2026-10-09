"""
Screens and the interactive menu.

Each screen is a plain function that renders one view. The menu loop
clears the terminal, runs a screen, then waits for a keypress. The same
functions back the command-line shortcuts in main.py.
"""

import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from datetime import time as clock

from rich.console import Group
from rich.prompt import FloatPrompt, Prompt
from rich.table import Table
from rich.text import Text

from src import ui
from src.config import (
    API_KEYS,
    CURRENCIES,
    DATA_DIR,
    ENV_FILE,
    PROVIDERS,
    REPORTS_DIR,
    ai_key,
    ai_model,
    ai_provider,
    base_currency,
    get_setting,
    save_setting,
)
from src.portfolio_manager import Portfolio, is_valid_symbol
from src.ui import console


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _data():
    """Imported lazily so the menu appears instantly (pandas/yfinance load on first use)."""
    from src import data_fetcher

    return data_fetcher


def _analytics():
    from src import analytics

    return analytics


def _ask(prompt, default=""):
    return Prompt.ask(f"[accent]›[/] {prompt}", default=default, show_default=bool(default)).strip()


def _ask_amount(prompt, default=None, minimum=0.0, allow_zero=True):
    """Ask for a number. With no default, an answer is required (Rich re-asks on Enter)."""
    kwargs = {"default": default} if default is not None else {}
    while True:
        value = FloatPrompt.ask(f"[accent]›[/] {prompt}", **kwargs)
        if value > minimum or (allow_zero and value == minimum):
            return value
        ui.error(f"Enter a number {'≥' if allow_zero else '>'} {minimum:g}")


def _confirm(question, default=True):
    hint = "[Y/n]" if default else "[y/N]"
    key = ui.choose(["y", "n", ""], f"{question} [muted]{hint}[/]")
    return default if key == "" else key == "y"


def _split_symbols(raw):
    return [s for s in raw.replace(",", " ").upper().split() if s]


def _pick_search_result(text):
    """Search by name and let the user pick. Returns a symbol or None."""
    with console.status(f"[muted]Searching for “{text}”…[/]", spinner="dots"):
        results = _data().search(text)
    if not results:
        ui.error(f"Nothing found for “{text}”.")
        return None
    table = ui.data_table(("#", "right"), ("Symbol", "left"), ("Name", "left"), ("Exchange", "left"), ("Type", "left"))
    for i, r in enumerate(results, 1):
        table.add_row(Text(str(i), style="key"), Text(r["symbol"], style="bold"), r["name"], Text(r["exchange"], style="muted"), Text(r["type"], style="muted"))
    console.print(ui.section(table, f"Matches for “{text}”"))
    keys = [str(i) for i in range(1, len(results) + 1)] + [""]
    choice = ui.choose(keys, f"Pick 1–{len(results)} [muted](Enter to cancel)[/]")
    return results[int(choice) - 1]["symbol"] if choice else None


def resolve_symbol(text):
    """
    Turn user input into a ticker: "nvda" -> NVDA, "apple" -> search -> AAPL.
    Returns None if cancelled or nothing matched.
    """
    text = text.strip()
    if not text:
        return None
    candidate = text.upper()
    if " " not in text and is_valid_symbol(candidate):
        with console.status(f"[muted]Checking {candidate}…[/]", spinner="dots"):
            quote = _data().get_quote(candidate)
        if quote:
            return candidate
        ui.hint(f"No ticker {candidate} — searching by name instead.")
    return _pick_search_result(text)


def _ask_symbol(prompt="Ticker or company name"):
    return resolve_symbol(_ask(f"{prompt} [muted](Enter to cancel)[/]"))


def market_status():
    """● US market open / ○ closed (regular hours, ignores holidays)."""
    try:
        from zoneinfo import ZoneInfo

        now = datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        return None
    is_open = now.weekday() < 5 and clock(9, 30) <= now.time() < clock(16, 0)
    return Text("● US market open", style="up") if is_open else Text("○ US market closed", style="muted")


def _require_symbols(portfolio, what="this"):
    if portfolio.is_empty:
        console.print(ui.empty_state("Your portfolio is empty", f"Add holdings or watchlist symbols in Manage portfolio to use {what}."))
        if portfolio.cash:
            ui.hint(f"Cash: {ui.money(portfolio.cash)}")
        return False
    return True


def _warn_missing(snapshot):
    if snapshot["unpriced"]:
        ui.warn(f"Couldn't fetch prices for {', '.join(snapshot['unpriced'])} — totals exclude them.")
    if snapshot["unconverted"]:
        ui.warn(f"No FX rate for {', '.join(snapshot['unconverted'])} — totals exclude them.")


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


def dashboard_view(portfolio, a, highlights=4):
    snap, m = a["snapshot"], a["metrics"]
    parts = []

    if snap["priced"]:
        today = ui.tile("Today", ui.pnl(snap["day_pnl"]), ui.change(snap["day_pct"]))
        total_return = ui.tile(
            "Unrealised P&L",
            ui.pnl(snap["pnl"]),
            Text.assemble(ui.change(snap["pnl_pct"]), (f" on {ui.compact(snap['invested'], a['base'])}", "muted")),
        )
    else:
        reason = "no live prices" if portfolio.holdings else "no holdings"
        today = ui.tile("Today", Text("—", style="muted"), reason)
        total_return = ui.tile("Unrealised P&L", Text("—", style="muted"), reason)

    versus = None
    for period in ("1Y", "YTD", "6M", "3M"):
        mine, bench = m.get("returns", {}).get(period) if m else None, a["benchmark_returns"].get(period)
        if mine is not None and bench is not None:
            gap = mine - bench
            versus = ui.tile(
                f"vs S&P 500 ({period})",
                Text(f"{'+' if gap >= 0 else '−'}{abs(gap):.1f} pts", style=ui.tone(gap)),
                f"{mine:+.1f}% vs {bench:+.1f}%",
            )
            break

    tiles = [
        ui.tile("Portfolio value", ui.money(snap["total_value"]), f"{len(portfolio.holdings)} holdings + cash"),
        today,
        total_return,
    ]
    if versus:
        tiles.append(versus)
    tiles.append(ui.tile("Cash", ui.money(snap["cash"]), f"{snap['cash_pct']:.1f}% of portfolio"))
    parts.append(ui.tiles(*tiles))

    if snap["positions"]:
        parts.append(ui.section(ui.holdings_table(snap["positions"]), "Holdings", f"values in {a['base']}"))

    if a["watch_only"]:
        rows = [{"symbol": s, "quote": a["quotes"].get(s), "tech": a["tech"].get(s)} for s in a["watch_only"]]
        parts.append(ui.section(ui.watchlist_table(rows), "Watchlist"))

    if a["findings"] and highlights:
        parts.append(ui.section(ui.findings_list(a["findings"], limit=highlights, details=False), "Highlights",
                                f"{len(a['findings'])} findings · full analysis in Insights"))
    return Group(*parts)


def dashboard(portfolio):
    console.print(ui.header("Dashboard", status=market_status()))
    if not _require_symbols(portfolio, "the dashboard"):
        return
    with console.status("[muted]Fetching live prices…[/]", spinner="dots"):
        a = _analytics().run(portfolio, fundamentals="cache")
    console.print(dashboard_view(portfolio, a))
    _warn_missing(a["snapshot"])


def watch(portfolio, interval=60):
    """Full-screen dashboard that refreshes itself."""
    from rich.live import Live

    if not _require_symbols(portfolio, "live watch"):
        return
    analytics = _analytics()
    with console.status("[muted]Fetching live prices…[/]", spinner="dots"):
        a = analytics.run(portfolio, fundamentals="cache")
    updated = datetime.now()

    def render(seconds_left):
        footer = Text.assemble(
            ("● LIVE", "up"), ("  ·  refreshing in ", "muted"), (f"{seconds_left}s", "bold"),
            (f"  ·  updated {updated:%H:%M:%S}  ·  ", "muted"), ("Ctrl+C", "bold"), (" to exit", "muted"),
        )
        return Group(ui.header("Live watch", status=market_status()), dashboard_view(portfolio, a, highlights=3), footer)

    with Live(render(interval), console=console, screen=True, auto_refresh=False) as live:
        while True:
            for remaining in range(interval, 0, -1):
                live.update(render(remaining), refresh=True)
                time.sleep(1)
            live.update(render(0), refresh=True)
            _data().clear_cache({"history"})
            a = analytics.run(portfolio, fundamentals="cache")
            updated = datetime.now()


# ---------------------------------------------------------------------------
# Insights
# ---------------------------------------------------------------------------


def _performance_table(a):
    m, bench = a["metrics"], a["benchmark_returns"]
    periods = [label for label, _ in _analytics().PERIODS]
    table = ui.data_table(("", "left"), *[(p, "right") for p in periods])
    mine = m.get("returns", {})
    table.add_row(Text("Your portfolio", style="bold"), *[ui.change(mine.get(p), arrow=False) for p in periods])
    table.add_row(Text("S&P 500", style="muted"), *[ui.change(bench.get(p), arrow=False) for p in periods])
    gaps = []
    for p in periods:
        if mine.get(p) is None or bench.get(p) is None:
            gaps.append(Text("—", style="muted"))
        else:
            gap = mine[p] - bench[p]
            gaps.append(Text(f"{'+' if gap >= 0 else '−'}{abs(gap):.1f} pts", style=ui.tone(gap)))
    table.add_row(Text("Difference", style="bold"), *gaps)
    return table


def _risk_tiles(a):
    m = a["metrics"]
    bench_vol, bench_dd = a.get("benchmark_volatility"), a.get("benchmark_drawdown")
    vol = m.get("volatility")
    b = m.get("beta")
    if b is not None:
        b = round(b, 2) or 0.0  # avoid "-0.00"
    beta_note = "—" if b is None else "moves more than the market" if b > 1.05 else "moves less than the market" if b < 0.95 else "moves with the market"
    sharpe = m.get("sharpe")
    sharpe_note = "—" if sharpe is None else "excellent" if sharpe >= 1.5 else "good" if sharpe >= 1 else "fair" if sharpe >= 0.5 else "weak"
    return ui.tiles(
        ui.tile("Volatility (1y)", f"{vol:.1f}%" if vol is not None else "—", f"S&P 500 {bench_vol:.1f}%" if bench_vol else None),
        ui.tile("Beta", f"{b:.2f}" if b is not None else "—", beta_note),
        ui.tile(
            "Max drawdown (1y)",
            Text(f"{m['max_drawdown']:.1f}%", style="down") if m.get("max_drawdown") is not None else "—",
            f"S&P 500 {bench_dd:.1f}%" if bench_dd is not None else None,
        ),
        ui.tile("Sharpe ratio", f"{sharpe:.2f}" if sharpe is not None else "—", sharpe_note),
        ui.tile(
            "Diversification",
            f"{m['effective_positions']:.1f}" if m.get("effective_positions") else "—",
            f"effective positions of {len(m.get('weights', {}))}",
        ),
    )


def _signals_table(a):
    table = ui.data_table(
        ("Symbol", "left"), ("1M", "right"), ("3M", "right"), ("1Y", "right"), ("50d avg", "center"),
        ("200d avg", "center"), ("RSI", "right"), ("From high", "right"), ("Volatility", "right"), ("Earnings", "right"),
    )

    def trend(flag):
        if flag is None:
            return Text("—", style="muted")
        return Text("above", style="up") if flag else Text("below", style="down")

    holdings = [p["symbol"] for p in a["snapshot"]["positions"]]
    for symbol in holdings + a["watch_only"]:
        t = a["tech"].get(symbol)
        if not t:
            continue
        r = t["returns"]
        rsi = t.get("rsi")
        rsi_style = "down" if rsi and rsi >= 70 else "up" if rsi and rsi <= 30 else "muted"
        earnings = a["fundamentals"].get(symbol, {}).get("next_earnings")
        label = Text(symbol, style="bold" if symbol in holdings else "muted")
        table.add_row(
            label,
            ui.change(r.get("1M"), arrow=False),
            ui.change(r.get("3M"), arrow=False),
            ui.change(r.get("1Y"), arrow=False),
            trend(t.get("above_sma50")),
            trend(t.get("above_sma200")),
            Text(f"{rsi:.0f}" if rsi is not None else "—", style=rsi_style),
            ui.change(t.get("pct_from_high"), arrow=False),
            Text(f"{t['volatility']:.0f}%" if t.get("volatility") else "—", style="muted"),
            Text(datetime.fromisoformat(earnings).strftime("%d %b") if earnings else "—", style="muted"),
        )
    return table


def insights(portfolio):
    console.print(ui.header("Insights"))
    if not _require_symbols(portfolio, "insights"):
        return
    with console.status("[muted]Analysing a year of prices, risk and fundamentals…[/]", spinner="dots"):
        a = _analytics().run(portfolio, fundamentals="network")
    snap, m = a["snapshot"], a["metrics"]

    counts = {level: sum(1 for f in a["findings"] if f["level"] == level) for level in ("risk", "watch")}
    if a["findings"]:
        console.print(ui.section(ui.findings_list(a["findings"]), "Key findings", f"{counts['risk']} risk · {counts['watch']} watch"))

    if m:
        console.print(ui.section(_performance_table(a), "Performance vs S&P 500", "your current holdings over the last 12 months"))
        console.print(_risk_tiles(a))

        weights = [(p["symbol"], p["weight"]) for p in sorted(snap["positions"], key=lambda p: p.get("weight") or 0, reverse=True) if p.get("weight")]
        if snap["cash"]:
            weights.append(("Cash", snap["cash_pct"]))
        panels = [ui.section(ui.allocation(weights, width=18), "By holding")]
        if a["sectors"]:
            panels.append(ui.section(ui.allocation([(n, p) for n, _, p in a["sectors"]], width=18), "By sector"))
        console.print(ui.side_by_side(*panels))

        symbols = list(m["weights"])
        if 2 <= len(symbols) <= 8:
            console.print(ui.section(ui.correlation_matrix(symbols, m["correlations"]), "Correlation",
                                     "1y daily returns · red = moves together"))
        elif len(symbols) > 8:
            top = Group(*[Text(f"{x} / {y}  {r:.2f}") for x, y, r in m["correlations"][:6]])
            console.print(ui.section(top, "Most correlated pairs"))

    if a["tech"]:
        console.print(ui.section(_signals_table(a), "Trend & momentum", "watchlist names dimmed"))

    if a["events"]:
        events = ui.data_table(("Date", "left"), ("Symbol", "left"), ("Event", "left"), ("In", "right"))
        for e in a["events"]:
            events.add_row(f"{e['date']:%a %d %b}", Text(e["symbol"], style="bold"), e["kind"], Text(f"{e['days']} days", style="muted"))
        console.print(ui.section(events, "Upcoming (30 days)"))

    _warn_missing(snap)
    ui.hint("Portfolio figures apply your current holdings to the last 12 months of prices. "
            "Sharpe uses the 13-week T-bill as the risk-free rate.")


# ---------------------------------------------------------------------------
# News
# ---------------------------------------------------------------------------


def news(portfolio, symbol=None, ask=True):
    console.print(ui.header("News"))
    symbols = portfolio.get_all_symbols()

    if symbol is None and ask:
        if symbols:
            ui.hint(f"Your stocks: {', '.join(symbols)}")
            raw = _ask("Ticker or company [muted](Enter for all your stocks)[/]")
            if raw:
                symbol = resolve_symbol(raw)
                if not symbol:
                    return
        else:
            symbol = _ask_symbol()
            if not symbol:
                return

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
    ui.hint("Headlines are clickable links in terminals that support it (iTerm2, VS Code, Windows Terminal…)")


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------


def _stat(label, value, style="bold"):
    return Text.assemble((f"{label}\n", "muted"), (value, style))


def _fmt(value, fmt):
    return fmt.format(value) if value is not None else "—"


def lookup(portfolio, symbol=None, offer_add=True):
    console.print(ui.header("Lookup"))
    if symbol:
        symbol = resolve_symbol(symbol)
    else:
        symbol = _ask_symbol("Ticker or company name to look up")
    if not symbol:
        return

    data, analytics = _data(), _analytics()
    with console.status(f"[muted]Looking up {symbol}…[/]", spinner="dots"):
        with ThreadPoolExecutor(max_workers=3) as pool:
            f_hist = pool.submit(data.get_history, symbol)
            f_fund = pool.submit(data.get_fundamentals, symbol)
            f_news = pool.submit(data.get_stock_news, symbol, 5)
            history, fund, articles = f_hist.result(), f_fund.result(), f_news.result()
        quote = data.get_quote(symbol) if history else None

    if not quote and not fund:
        ui.error(f"Couldn't find any data for {symbol}.")
        return

    tech = analytics.technicals(history["closes"]) if history else {}
    currency = quote["currency"] if quote else fund.get("currency")
    kind = fund.get("quote_type", "")
    is_equity = kind in ("", "EQUITY")

    # --- Quote ---
    name = fund.get("long_name") or symbol
    details = " · ".join(p for p in (None if is_equity else kind, fund.get("sector"), fund.get("industry"), fund.get("category")) if p)
    title = Text.assemble((symbol, "accent"), "  ", (name, "bold"))
    if details:
        title.append(f"\n{details}", style="muted")
    price_line = Text()
    if quote:
        price_line.append(ui.money(quote["price"], currency), style="bold")
        price_line.append("   ")
        if quote["change"] is not None:
            price_line.append_text(ui.pnl(quote["change"], currency))
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

    # --- Key stats ---
    stats = Table.grid(expand=True, padding=(0, 3))
    for _ in range(4):
        stats.add_column()
    price = quote["price"] if quote else None
    if is_equity:
        target = fund.get("analyst_target_mean")
        upside = f" ({(target / price - 1) * 100:+.1f}%)" if target and price else ""
        consensus = (fund.get("recommendation") or "").replace("_", " ").title() or "—"
        if fund.get("analyst_count"):
            consensus += f" ({fund['analyst_count']})"
        earnings = fund.get("next_earnings")
        rows = [
            [_stat("Market cap", ui.compact(fund.get("market_cap"), currency)), _stat("P/E (ttm)", _fmt(fund.get("pe_ratio"), "{:.2f}")),
             _stat("Forward P/E", _fmt(fund.get("forward_pe"), "{:.2f}")), _stat("PEG", _fmt(fund.get("peg_ratio"), "{:.2f}"))],
            [_stat("Revenue growth", _fmt(fund.get("revenue_growth") and fund["revenue_growth"] * 100, "{:+.1f}%")),
             _stat("Profit margin", _fmt(fund.get("profit_margin") and fund["profit_margin"] * 100, "{:.1f}%")),
             _stat("Debt / equity", _fmt(fund.get("debt_to_equity"), "{:.0f}")),
             _stat("Dividend yield", _fmt(fund.get("dividend_yield"), "{:.2f}%"))],
            [_stat("Analyst target", (ui.money(target, currency) + upside) if target else "—"), _stat("Consensus", consensus),
             _stat("Next earnings", datetime.fromisoformat(earnings).strftime("%d %b %Y") if earnings else "—"),
             _stat("Beta", _fmt(fund.get("beta"), "{:.2f}"))],
        ]
    else:
        rows = [[
            _stat("Fund size", ui.compact(fund.get("total_assets"), currency)),
            _stat("Expense ratio", _fmt(fund.get("expense_ratio"), "{:.2f}%")),
            _stat("Yield", _fmt(fund.get("dividend_yield"), "{:.2f}%")),
            _stat("Category", fund.get("category") or "—"),
        ]]
    for i, row in enumerate(rows):
        if i:
            stats.add_row(*[Text("")] * 4)
        stats.add_row(*row)

    perf = Table.grid(expand=True, padding=(0, 3))
    for _ in range(4):
        perf.add_column()
    if tech:
        r = tech["returns"]
        rsi = tech.get("rsi")
        trend = "—" if tech.get("above_sma200") is None else "above 200d avg" if tech["above_sma200"] else "below 200d avg"
        perf.add_row(
            Text.assemble(("1M  ", "muted"), ui.change(r.get("1M"), arrow=False), ("   3M  ", "muted"), ui.change(r.get("3M"), arrow=False)),
            Text.assemble(("YTD  ", "muted"), ui.change(r.get("YTD"), arrow=False), ("   1Y  ", "muted"), ui.change(r.get("1Y"), arrow=False)),
            Text.assemble(("Trend  ", "muted"), (trend, "up" if tech.get("above_sma200") else "down" if tech.get("above_sma200") is False else "muted")),
            Text.assemble(("RSI  ", "muted"), (f"{rsi:.0f}" if rsi is not None else "—", "bold"),
                          ("   Vol  ", "muted"), (f"{tech['volatility']:.0f}%" if tech.get("volatility") else "—", "bold")),
        )
    low = fund.get("fifty_two_week_low") or tech.get("low_1y")
    high = fund.get("fifty_two_week_high") or tech.get("high_1y")
    range_line = Text.assemble(("52-week range   ", "muted"))
    range_line.append_text(ui.range_bar(low, high, price, currency=currency))
    console.print(ui.section(Group(stats, Text(""), perf, Text(""), range_line), "Key stats"))
    console.print(ui.news_panel(f"{symbol} news", articles, show_summary=True))

    tracked = symbol in portfolio.holdings or symbol in portfolio.watchlist
    if offer_add and not tracked and _confirm(f"Add {symbol} to your watchlist?", default=False):
        portfolio.add_to_watchlist(symbol)
        ui.success(f"Added {symbol} to your watchlist")


# ---------------------------------------------------------------------------
# AI analysis
# ---------------------------------------------------------------------------


def _ensure_ai_key():
    if ai_key():
        return True
    console.print(
        ui.empty_state(
            "Connect an AI provider",
            "AI analysis works with any OpenAI-compatible API. Pick one - you'll need an API key from that provider.\n"
            "Keys are saved to .env in the project folder.",
        )
    )
    names = list(PROVIDERS)
    console.print(ui.menu([(str(i), PROVIDERS[n]["label"], PROVIDERS[n]["key_url"]) for i, n in enumerate(names, 1)] + [("0", "Cancel", "")]))
    choice = ui.choose([str(i) for i in range(len(names) + 1)])
    if choice == "0":
        return False
    provider = names[int(choice) - 1]
    key = Prompt.ask(f"[accent]›[/] Paste your {PROVIDERS[provider]['label']} API key (Enter to cancel)", password=True, default="", show_default=False).strip()
    if not key:
        return False
    save_setting(PROVIDERS[provider]["key_env"], key)
    save_setting("AI_PROVIDER", provider)
    ui.success("Key saved")
    return True


def _usage_line(result):
    from src.llm_advisor import total_usage

    usage = total_usage(result)
    if not usage["input"] and not usage["output"]:
        return ""
    return f"{usage['input']:,} input + {usage['output']:,} output tokens"


def _followups(result):
    from src.llm_advisor import ask_followup, save_report

    ui.hint("Ask anything about the review — e.g. “What would you sell first if the market drops 10%?”")
    while True:
        question = _ask("Follow-up question [muted](Enter to finish)[/]")
        if not question:
            return
        try:
            with ui.StreamView("Answer") as view:
                answer = ask_followup(result, question, view.update)
        except RuntimeError as e:
            ui.error(str(e))
            return
        console.print(ui.report_panel(answer, question if len(question) <= 70 else question[:67] + "…"))
        save_report(result)


def ai_analysis(portfolio, confirm=True, followups=True):
    from src.llm_advisor import prepare, save_report, write_report

    console.print(ui.header("AI analysis"))
    if not _require_symbols(portfolio, "AI analysis"):
        return
    if not _ensure_ai_key():
        return

    provider, model = PROVIDERS[ai_provider()]["label"], ai_model()
    symbols = portfolio.get_all_symbols()
    console.print(
        ui.section(
            Text.assemble(
                ("Reviews ", "muted"), (f"{len(symbols)} symbols", "bold"), (f" ({', '.join(symbols)})", "muted"), "\n",
                ("Computes risk, performance vs the S&P 500, correlations, trends and events, reads the key\n", "muted"),
                ("news articles, then asks ", "muted"), (f"{provider} {model}", "bold"),
                (" for a review grounded in that data. Usually 30–60 seconds.", "muted"),
            ),
            "What happens",
        )
    )
    if confirm and not _confirm("Start analysis?"):
        return

    try:
        with ui.StageProgress() as progress:
            context = prepare(portfolio, progress)
        with ui.StreamView(f"Writing with {model}") as view:
            result = write_report(context, view.update)
    except RuntimeError as e:
        ui.error(str(e))
        return

    path = save_report(result)
    console.print(ui.report_panel(result["advice"], "AI analysis", f"{provider} · {model} · {result['created']:%d %b %Y, %H:%M}"))
    console.print(ui.section(ui.sources_table(result["sources"]), "Sources", "✓ = full article read"))
    usage = _usage_line(result)
    ui.success(f"Saved to [bold]{path.relative_to(REPORTS_DIR.parent)}[/]" + (f"  [muted]· {usage}[/]" if usage else ""))
    if followups:
        _followups(result)


# ---------------------------------------------------------------------------
# Manage portfolio
# ---------------------------------------------------------------------------


def _portfolio_overview(portfolio):
    holdings = ui.data_table(("Symbol", "left"), ("Shares", "right"), ("Avg cost", "right"))
    for symbol, h in portfolio.holdings.items():
        holdings.add_row(Text(symbol, style="bold"), ui.number(h["shares"]), f"{h['cost_basis']:,.2f}")
    if not portfolio.holdings:
        holdings.add_row(Text("No holdings yet", style="muted"), "", "")

    watch = Text(", ".join(portfolio.watchlist) if portfolio.watchlist else "Empty", style="bold" if portfolio.watchlist else "muted")
    return Group(
        ui.section(holdings, "Holdings", "costs in each stock's own currency"),
        ui.tiles(ui.tile("Watchlist", watch), ui.tile("Cash", ui.money(portfolio.cash), f"in {base_currency()}")),
    )


def _add_holding(portfolio):
    symbol = _ask_symbol()
    if not symbol:
        return None
    existing = portfolio.holdings.get(symbol)
    if existing:
        ui.hint(f"You hold {ui.number(existing['shares'])} {symbol} at {existing['cost_basis']:,.2f} — new values will replace these.")

    quote = _data().get_quote(symbol)
    if quote is None:
        ui.warn(f"Couldn't find a live price for {symbol}.")
        if not _confirm("Add it anyway?", default=False):
            return None
        currency = "its trading currency"
    else:
        currency = quote["currency"]
        ui.hint(f"{symbol} is trading at {ui.money(quote['price'], currency)} ({currency})")

    shares = _ask_amount("Shares", default=existing["shares"] if existing else None, allow_zero=False)
    cost = _ask_amount(
        f"Average cost per share in {currency}",
        default=existing["cost_basis"] if existing else (quote["price"] if quote else None),
    )
    portfolio.set_holding(symbol, shares, cost)
    return True, f"{'Updated' if existing else 'Added'} {symbol}: {ui.number(shares)} shares at {cost:,.2f}"


def _remove_holding(portfolio):
    if not portfolio.holdings:
        return False, "No holdings to remove"
    ui.hint(f"Holdings: {', '.join(portfolio.holdings)}")
    symbol = _ask("Remove which holding? [muted](Enter to cancel)[/]").upper()
    if not symbol:
        return None
    if symbol not in portfolio.holdings:
        return False, f"You don't hold {symbol}"
    if not _confirm(f"Remove {symbol}?", default=False):
        return None
    portfolio.remove_holding(symbol)
    return True, f"Removed {symbol}"


def _add_watchlist(portfolio):
    raw = _ask("Tickers or company names to watch [muted](comma separated)[/]")
    entries = [e.strip() for e in raw.split(",") if e.strip()]
    if len(entries) == 1 and " " not in entries[0]:
        entries = _split_symbols(entries[0])  # allow "TSLA AMD"
    added = []
    for entry in entries:
        symbol = resolve_symbol(entry)
        if symbol and portfolio.add_to_watchlist(symbol):
            added.append(symbol)
    return (True, f"Watching {', '.join(added)}") if added else None


def _remove_watchlist(portfolio):
    if not portfolio.watchlist:
        return False, "Watchlist is already empty"
    ui.hint(f"Watching: {', '.join(portfolio.watchlist)}")
    raw = _ask("Tickers to remove")
    removed = [s for s in _split_symbols(raw) if portfolio.remove_from_watchlist(s)]
    return (True, f"Removed {', '.join(removed)}") if removed else (False, "None of those are on your watchlist")


def _set_cash(portfolio):
    amount = _ask_amount(f"Cash balance in {base_currency()}", default=portfolio.cash)
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

        choice = ui.choose([k for k, _, _ in MANAGE_ACTIONS] + ["0", "q"])
        if choice in ("0", "q"):
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

    shown = paths[:9]
    table = ui.data_table(("#", "right"), ("Date", "left"), ("Time", "left"), ("File", "left"))
    for i, path in enumerate(shown, 1):
        date_part, _, time_part = path.stem.partition("_")
        table.add_row(Text(str(i), style="key"), date_part, f"{time_part[:2]}:{time_part[2:4]}" if len(time_part) >= 4 else time_part, Text(path.name, style="muted"))
    console.print(ui.section(table, "Saved AI analyses", f"{len(paths)} total"))

    choice = ui.choose([str(i) for i in range(1, len(shown) + 1)] + [""], "Open which report? [muted](Enter to go back)[/]")
    if not choice:
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


def _choose_from(options, title):
    console.print(ui.menu([(str(i), option, "") for i, option in enumerate(options, 1)] + [("0", "Cancel", "")]))
    choice = ui.choose([str(i) for i in range(len(options) + 1)], title)
    return None if choice == "0" else options[int(choice) - 1]


def settings(portfolio=None):
    while True:
        console.clear()
        console.print(ui.header("Settings"))

        provider = ai_provider()
        ai = ui.data_table(("Setting", "left"), ("Value", "left"))
        ai.add_row("AI provider", Text(PROVIDERS[provider]["label"], style="bold"))
        ai.add_row("AI model", Text(ai_model(provider), style="bold"))
        ai.add_row("Base currency", Text(base_currency(), style="bold"))
        console.print(ui.section(ai, "Preferences"))

        keys = ui.data_table(("Service", "left"), ("Status", "left"), ("Used for", "left"))
        for env, label, purpose in API_KEYS:
            value = get_setting(env)
            status = Text(f"✓ {_masked(value)}", style="ok") if value else Text("– not set", style="muted")
            keys.add_row(Text(label, style="bold"), status, Text(purpose, style="muted"))
        console.print(ui.section(keys, "API keys", f"stored in {ENV_FILE.name}"))

        info = ui.data_table(("Data", "left"), ("Location", "left"))
        info.add_row("Holdings / watchlist / cash", str(DATA_DIR))
        info.add_row("AI reports", str(REPORTS_DIR))
        info.add_row("Python", sys.version.split()[0])
        console.print(ui.section(info, "Files"))

        console.print(ui.menu([
            ("1", "AI provider", "OpenAI, DeepSeek"),
            ("2", "AI model", ""),
            ("3", "API keys", ""),
            ("4", "Base currency", "currency totals are shown in"),
            ("0", "Back", ""),
        ]))
        choice = ui.choose(["1", "2", "3", "4", "0", "q"])
        if choice in ("0", "q"):
            return
        if choice == "1":
            labels = [p["label"] for p in PROVIDERS.values()]
            picked = _choose_from(labels, "Provider")
            if picked:
                save_setting("AI_PROVIDER", next(n for n, p in PROVIDERS.items() if p["label"] == picked))
        elif choice == "2":
            preset = PROVIDERS[provider]
            picked = _choose_from(preset["models"] + ["Other…"], "Model")
            if picked == "Other…":
                picked = _ask("Model name", default=ai_model(provider))
            if picked:
                save_setting(preset["model_env"], picked)
        elif choice == "3":
            picked = _choose_from([label for _, label, _ in API_KEYS], "Key")
            if picked:
                env = next(e for e, label, _ in API_KEYS if label == picked)
                value = Prompt.ask(f"[accent]›[/] Paste {picked} key (Enter to keep current)", password=True, default="", show_default=False).strip()
                if value:
                    save_setting(env, value)
        elif choice == "4":
            picked = _choose_from(CURRENCIES, "Currency")
            if picked:
                save_setting("BASE_CURRENCY", picked)


# ---------------------------------------------------------------------------
# First run
# ---------------------------------------------------------------------------


def needs_onboarding():
    return not any((DATA_DIR / name).exists() for name in ("portfolio.txt", "watchlist.txt", "cash.txt"))


def onboarding(portfolio):
    console.clear()
    console.print(ui.header("Welcome"))
    console.print(
        ui.empty_state(
            "Welcome to ezstox",
            "Let's get you set up - it takes about a minute:\n"
            "  1. pick the currency you think in\n"
            "  2. add your holdings, watchlist and cash\n"
            "  3. optionally connect an AI provider for full portfolio reviews",
        )
    )
    picked = _choose_from(CURRENCIES, "Your base currency")
    if picked:
        save_setting("BASE_CURRENCY", picked)
    portfolio.save()  # create the data files so this only runs once
    manage(portfolio)
    console.clear()
    console.print(ui.header("Welcome"))
    if not ai_key() and _confirm("Set up AI analysis now?", default=False):
        _ensure_ai_key()


# ---------------------------------------------------------------------------
# Main menu
# ---------------------------------------------------------------------------

MENU = [
    ("1", "Dashboard", "Holdings, P&L, watchlist and highlights", dashboard),
    ("2", "Insights", "Risk, performance vs S&P 500, diversification, signals", insights),
    ("3", "News", "Latest headlines for your stocks", news),
    ("4", "Lookup", "Quote, fundamentals and news for any ticker or company", lookup),
    ("5", "AI analysis", "Full AI review grounded in all of the above", ai_analysis),
    ("6", "Live watch", "Auto-refreshing full-screen dashboard", watch),
    ("7", "Manage portfolio", "Edit holdings, watchlist and cash", manage),
    ("8", "Reports", "Browse saved AI analyses", reports),
    ("9", "Settings", "AI provider, keys, currency", settings),
]

# Screens that manage their own screen loop and don't need "press a key"
SELF_CONTAINED = {manage, settings, watch}


def _status_line(portfolio):
    line = Text("  ")
    line.append(f"{len(portfolio.holdings)} holdings", style="bold")
    line.append("  ·  ", style="faint")
    line.append(f"{len(portfolio.watchlist)} watching", style="bold")
    line.append("  ·  ", style="faint")
    line.append(f"{ui.money(portfolio.cash)} cash", style="bold")
    line.append("  ·  ", style="faint")
    if ai_key():
        line.append(f"AI ready ({PROVIDERS[ai_provider()]['label']})", style="ok")
    else:
        line.append("AI needs an API key (Settings)", style="warn")
    return line


def run_menu():
    if needs_onboarding():
        try:
            onboarding(Portfolio())
        except (KeyboardInterrupt, EOFError):
            pass

    while True:
        portfolio = Portfolio()
        console.clear()
        console.print(ui.header(status=market_status()))
        console.print(_status_line(portfolio))
        for warning in portfolio.warnings:
            ui.warn(warning)
        console.print(ui.menu([(k, label, desc) for k, label, desc, _ in MENU] + [("0", "Quit", "")]))

        try:
            choice = ui.choose([k for k, *_ in MENU] + ["0", "q"])
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
