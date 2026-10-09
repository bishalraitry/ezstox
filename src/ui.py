"""
Terminal UI building blocks (Rich).

Everything visual lives here: theme, number formatting, tables, panels,
charts, findings, live views and input. Screens in app.py compose these
pieces, so the look stays consistent everywhere. Colours are chosen to
read well on both dark and light terminal themes.
"""

import os
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

from rich import box
from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.padding import Padding
from rich.panel import Panel
from rich.progress import BarColumn, Progress, ProgressColumn, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.segment import SegmentLines
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

from src.config import base_currency

THEME = Theme(
    {
        "accent": "bold #a78bfa",
        "accent.dim": "#7c6fd0",
        "muted": "grey58",
        "faint": "grey37",
        "up": "#3fb950",
        "down": "#f85149",
        "warn": "#e3b341",
        "info": "#79c0ff",
        "ok": "#3fb950",
        "err": "bold #f85149",
        "key": "bold #a78bfa",
        "markdown.h1.border": "#7c6fd0",
        "markdown.h2": "bold #a78bfa",
        "markdown.h3": "bold",
        "markdown.link": "#79c0ff",
        "markdown.item.bullet": "#a78bfa",
        "markdown.item.number": "#a78bfa",
        "markdown.table.border": "grey37",
        "markdown.table.header": "bold #a78bfa",
    }
)

console = Console(theme=THEME, highlight=False)

BORDER = "faint"
SPARK_BLOCKS = "▁▂▃▄▅▆▇█"
BAR_PARTIALS = " ▏▎▍▌▋▊▉"
CURRENCY_SYMBOLS = {
    "USD": "$", "GBP": "£", "EUR": "€", "JPY": "¥", "CAD": "C$", "AUD": "A$",
    "HKD": "HK$", "INR": "₹", "CHF": "CHF ", "SEK": "SEK ", "NOK": "NOK ", "DKK": "DKK ",
}
LEVEL_STYLES = {
    "risk": ("●", "down"),
    "watch": ("▲", "warn"),
    "info": ("•", "info"),
    "good": ("✓", "up"),
}


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def money(value, currency=None, signed=False):
    """$1,234.56 / £94.12 / −€12.00 - in the base currency unless told otherwise."""
    if value is None:
        return "—"
    code = currency or base_currency()
    symbol = CURRENCY_SYMBOLS.get(code, f"{code} ")
    decimals = 0 if code == "JPY" else 2
    sign = ("+" if value > 0 else "−" if value < 0 else "") if signed else ("−" if value < 0 else "")
    return f"{sign}{symbol}{abs(value):,.{decimals}f}"


def number(value):
    """Share counts: no pointless decimals (10, 1.5, 0.125)."""
    return f"{value:,.8f}".rstrip("0").rstrip(".") if value is not None else "—"


def compact(value, currency=None):
    """1.23T / 456.7B / 12.3M - for market caps and fund sizes."""
    if value is None:
        return "—"
    prefix = CURRENCY_SYMBOLS.get(currency, "") if currency else ""
    for threshold, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(value) >= threshold:
            return f"{prefix}{value / threshold:,.2f}{suffix}"
    return f"{prefix}{value:,.0f}"


def tone(value):
    if value is None:
        return "muted"
    return "up" if value > 0 else "down" if value < 0 else "muted"


def change(pct, arrow=True):
    """▲ 1.23% in green / ▼ 0.45% in red / — when unknown."""
    if pct is None:
        return Text("—", style="muted")
    if arrow:
        return Text(f"{'▲ ' if pct > 0 else '▼ ' if pct < 0 else '  '}{abs(pct):.2f}%", style=tone(pct))
    return Text(f"{'+' if pct > 0 else '−' if pct < 0 else ''}{abs(pct):.1f}%", style=tone(pct))


def pnl(amount, currency=None):
    return Text(money(amount, currency, signed=True), style=tone(amount))


def relative_time(iso_timestamp):
    if not iso_timestamp:
        return ""
    try:
        published = datetime.fromisoformat(iso_timestamp)
    except ValueError:
        return ""
    seconds = (datetime.now(timezone.utc) - published).total_seconds()
    if seconds < 0:
        return "just now"
    if seconds < 3600:
        return f"{max(1, int(seconds // 60))}m ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h ago"
    if seconds < 7 * 86400:
        return f"{int(seconds // 86400)}d ago"
    return f"{published.day} {published:%b %Y}"


def domain(url):
    try:
        host = urlparse(url).netloc
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------


def sparkline(values, width=16):
    """A tiny unicode price chart, green if the period was up, red if down."""
    if not values or len(values) < 2:
        return Text("")
    if len(values) > width:
        step = (len(values) - 1) / (width - 1)
        values = [values[round(i * step)] for i in range(width)]
    low, high = min(values), max(values)
    span = (high - low) or 1
    chars = "".join(SPARK_BLOCKS[int((v - low) / span * (len(SPARK_BLOCKS) - 1))] for v in values)
    return Text(chars, style="up" if values[-1] >= values[0] else "down")


def range_bar(low, high, current, width=24, currency=None):
    """52-week range: $low ━━━━●━━━━ $high"""
    if low is None or high is None or current is None or high <= low:
        return Text("—", style="muted")
    position = round((min(max(current, low), high) - low) / (high - low) * (width - 1))
    bar = Text()
    bar.append(f"{money(low, currency)} ", style="muted")
    bar.append("━" * position, style="accent.dim")
    bar.append("●", style="accent")
    bar.append("━" * (width - 1 - position), style="faint")
    bar.append(f" {money(high, currency)}", style="muted")
    return bar


def bar(pct, width=24, style="accent.dim"):
    """Horizontal bar with eighth-block precision."""
    filled = max(0.0, min(pct, 100.0)) / 100 * width
    whole = int(filled)
    partial = BAR_PARTIALS[int((filled - whole) * 8)] if whole < width else ""
    return Text("█" * whole + partial.strip(), style=style)


def allocation(rows, width=22, limit=10):
    """rows: [(label, pct)] -> label · bar · pct, largest first."""
    grid = Table.grid(padding=(0, 1))
    grid.add_column(no_wrap=True)
    grid.add_column(no_wrap=True)
    grid.add_column(justify="right", no_wrap=True)
    for label, pct in rows[:limit]:
        style = "faint" if label == "Cash" else "accent.dim"
        grid.add_row(Text(label, style="bold" if label != "Cash" else "muted"), bar(pct, width, style), Text(f"{pct:.1f}%", style="muted"))
    if len(rows) > limit:
        rest = sum(p for _, p in rows[limit:])
        grid.add_row(Text(f"{len(rows) - limit} more", style="muted"), bar(rest, width, "faint"), Text(f"{rest:.1f}%", style="muted"))
    return grid


def correlation_matrix(symbols, pairs):
    """Heatmap of pairwise correlations (red = moves together)."""
    lookup = {}
    for a, b, value in pairs:
        lookup[(a, b)] = lookup[(b, a)] = value
    table = Table(box=box.SIMPLE_HEAD, header_style="muted", border_style=BORDER, pad_edge=False)
    table.add_column("")
    for s in symbols:
        table.add_column(s, justify="right")
    for a in symbols:
        cells = []
        for b in symbols:
            if a == b:
                cells.append(Text("·", style="faint"))
                continue
            value = lookup.get((a, b))
            if value is None:
                cells.append(Text("—", style="faint"))
            else:
                style = "down" if value >= 0.8 else "warn" if value >= 0.5 else "up" if value < 0.2 else "muted"
                cells.append(Text(f"{value:.2f}", style=style))
        table.add_row(Text(a, style="bold"), *cells)
    return table


# ---------------------------------------------------------------------------
# Messages & input
# ---------------------------------------------------------------------------


def success(message):
    console.print(f"[ok]✓[/] {message}")


def warn(message):
    console.print(f"[warn]![/] {message}")


def error(message):
    console.print(f"[err]✗[/] {message}")


def hint(message):
    console.print(f"[muted]{message}[/]")


def read_key():
    """
    One keypress, no Enter needed. Falls back to reading a line when input
    isn't an interactive terminal (pipes, tests).
    """
    if not sys.stdin.isatty():
        line = sys.stdin.readline()
        if not line:
            raise EOFError
        return line.strip()
    if os.name == "nt":
        import msvcrt

        key = msvcrt.getwch()
        if key == "\x03":
            raise KeyboardInterrupt
        return key
    import termios
    import tty

    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        key = sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
    if key == "\x04":
        raise EOFError
    return key


def choose(keys, prompt="Choose"):
    """Wait for one of `keys` (single keypress). Enter returns ""."""
    keys = [k.lower() for k in keys]
    console.print(f"[accent]›[/] {prompt} ", end="")
    while True:
        key = read_key().lower()
        if key in ("\r", "\n"):
            key = ""
        if key in keys:
            console.print(key or "")
            return key


def pause():
    console.print("\n[muted]Press any key to return to the menu…[/]", end="")
    try:
        read_key()
    except EOFError:
        pass
    console.print()


# ---------------------------------------------------------------------------
# Layout pieces
# ---------------------------------------------------------------------------


def header(subtitle=None, status=None):
    grid = Table.grid(expand=True)
    grid.add_column()
    grid.add_column(justify="right")
    left = Text.assemble(("◆ ", "accent"), ("ezstox", "bold"))
    left.append(f"  ·  {subtitle}" if subtitle else "  ·  Portfolio tracker & AI advisor", style="muted")
    now = datetime.now()
    right = Text()
    if status:
        right.append_text(status)
        right.append("  ·  ", style="faint")
    right.append(f"{now:%a} {now.day} {now:%b · %H:%M}", style="muted")
    grid.add_row(left, right)
    return Panel(grid, box=box.ROUNDED, border_style="accent.dim", padding=(0, 1))


def section(renderable, title, subtitle=None):
    return Panel(
        renderable,
        title=f"[bold]{title}[/]",
        title_align="left",
        subtitle=f"[muted]{subtitle}[/]" if subtitle else None,
        subtitle_align="right",
        box=box.ROUNDED,
        border_style=BORDER,
        padding=(0, 1),
    )


def tile(label, value, sub=None):
    body = Text()
    body.append(label.upper(), style="muted")
    body.append("\n")
    body.append_text(value if isinstance(value, Text) else Text(value, style="bold"))
    if sub is not None:
        body.append("\n")
        body.append_text(sub if isinstance(sub, Text) else Text(sub, style="muted"))
    return Panel(body, box=box.ROUNDED, border_style=BORDER, padding=(0, 1))


def tiles(*items):
    """A row of equal-width stat tiles (fewer per row on narrow terminals)."""
    per_row = len(items) if console.width >= 18 * len(items) + 10 else max(2, (console.width - 4) // 26)
    grid = Table.grid(expand=True, padding=(0, 1))
    for _ in range(per_row):
        grid.add_column(ratio=1)
    for i in range(0, len(items), per_row):
        row = list(items[i : i + per_row])
        grid.add_row(*row, *[""] * (per_row - len(row)))
    return grid


def side_by_side(*renderables):
    grid = Table.grid(expand=True, padding=(0, 1))
    for _ in renderables:
        grid.add_column(ratio=1)
    grid.add_row(*renderables)
    return grid


def data_table(*columns):
    """Consistent table style. Columns: (header, justify) tuples."""
    table = Table(box=box.SIMPLE_HEAD, header_style="muted", border_style=BORDER, expand=True, pad_edge=False)
    for heading, justify in columns:
        table.add_column(heading, justify=justify, no_wrap=True)
    return table


def menu(items):
    grid = Table.grid(padding=(0, 2))
    grid.add_column(justify="right")
    grid.add_column()
    grid.add_column()
    for key, label, description in items:
        grid.add_row(Text(key, style="key"), Text(label, style="bold"), Text(description, style="muted"))
    return Panel(grid, box=box.ROUNDED, border_style=BORDER, padding=(1, 2))


def empty_state(title, message):
    body = Text.assemble((title, "bold"), "\n", (message, "muted"))
    return Panel(body, box=box.ROUNDED, border_style=BORDER, padding=(1, 2))


def findings_list(findings, limit=None, details=True):
    grid = Table.grid(padding=(0, 1))
    grid.add_column(no_wrap=True)
    grid.add_column()
    for f in findings[:limit]:
        icon, style = LEVEL_STYLES[f["level"]]
        text = Text(f["title"], style="bold")
        if details and f["detail"]:
            text.append(f"\n{f['detail']}", style="muted")
        grid.add_row(Text(icon, style=style), text)
    return grid


# ---------------------------------------------------------------------------
# Domain renderables
# ---------------------------------------------------------------------------


def holdings_table(positions):
    """Positions from analytics.build_snapshot (price in native currency, value in base)."""
    wide = console.width >= 112
    columns = [
        ("Symbol", "left"), ("Shares", "right"), ("Avg cost", "right"), ("Price", "right"), ("Today", "right"),
        ("Value", "right"), ("P&L", "right"), ("Return", "right"), ("Weight", "right"),
    ]
    if wide:
        columns.append(("1M trend", "left"))
    table = data_table(*columns)

    for p in positions:
        native = p.get("currency")
        if p["price"] is None:
            cells = [Text(p["symbol"], style="bold"), number(p["shares"]), money(p["cost_basis"], native),
                     Text("unavailable", style="muted"), *[Text("—", style="muted")] * 5]
        elif p["value"] is None:
            cells = [Text(p["symbol"], style="bold"), number(p["shares"]), money(p["cost_basis"], native),
                     money(p["price"], native), change(p["day_pct"]), Text("no FX rate", style="muted"), *[Text("—", style="muted")] * 3]
        else:
            cells = [
                Text(p["symbol"], style="bold"),
                number(p["shares"]),
                money(p["cost_basis"], native),
                money(p["price"], native),
                change(p["day_pct"]),
                money(p["value"]),
                pnl(p["pnl"]),
                change(p["pnl_pct"], arrow=False),
                Text(f"{p['weight']:.1f}%", style="muted"),
            ]
        if wide:
            cells.append(sparkline(p.get("history")))
        table.add_row(*cells)
    return table


def watchlist_table(rows):
    """rows: dicts with symbol, quote (or None), tech (dict)"""
    wide = console.width >= 90
    columns = [("Symbol", "left"), ("Price", "right"), ("Today", "right"), ("5 days", "right"), ("From 1y high", "right")]
    if wide:
        columns += [("RSI", "right"), ("1M trend", "left")]
    table = data_table(*columns)
    for row in rows:
        q, t = row["quote"], row.get("tech") or {}
        if not q:
            cells = [Text(row["symbol"], style="bold"), Text("unavailable", style="muted"), "—", "—", "—"]
            if wide:
                cells += ["—", ""]
        else:
            cells = [
                Text(row["symbol"], style="bold"),
                money(q["price"], q["currency"]),
                change(q["change_pct"]),
                change(q["change_5d_pct"]),
                change(t.get("pct_from_high"), arrow=False),
            ]
            if wide:
                rsi = t.get("rsi")
                style = "down" if rsi and rsi >= 70 else "up" if rsi and rsi <= 30 else "muted"
                cells += [Text(f"{rsi:.0f}" if rsi is not None else "—", style=style), sparkline(q.get("history"))]
        table.add_row(*cells)
    return table


def article_line(article, show_summary=False):
    url = article.get("url")
    has_link = bool(url) and url != "No link available"
    title = Text()
    title.append("• ", style="accent")
    title.append(article.get("title", "No title"), style=f"bold link {url}" if has_link else "bold")

    meta = [part for part in (article.get("publisher"), relative_time(article.get("published"))) if part]
    if not article.get("published") and article.get("date") not in (None, "Unknown date"):
        meta.append(article["date"])
    if has_link:
        meta.append(domain(url))
    lines = [title, Text("  " + "  ·  ".join(meta), style="muted")]
    if show_summary and article.get("summary"):
        summary = article["summary"]
        lines.append(Padding(Text(summary[:220] + "…" if len(summary) > 220 else summary, style="faint"), (0, 0, 0, 2)))
    return Group(*lines)


def news_panel(symbol, articles, show_summary=False):
    if not articles:
        body = Text("No recent news found.", style="muted")
    else:
        items = []
        for i, article in enumerate(articles):
            if i:
                items.append(Text(""))
            items.append(article_line(article, show_summary))
        body = Group(*items)
    return section(body, symbol, subtitle=f"{len(articles)} articles" if articles else None)


def report_panel(markdown_text, title, subtitle=None):
    return Panel(
        Markdown(markdown_text, hyperlinks=True),
        title=f"[bold]{title}[/]",
        title_align="left",
        subtitle=f"[muted]{subtitle}[/]" if subtitle else None,
        subtitle_align="right",
        box=box.ROUNDED,
        border_style="accent.dim",
        padding=(1, 2),
    )


def sources_table(sources):
    table = data_table(("Ref", "left"), ("Article", "left"), ("Source", "left"), ("Read", "center"))
    table.columns[1].no_wrap = False
    table.columns[1].ratio = 1
    for source in sources:
        url = source.get("url")
        has_link = bool(url) and url != "No link available"
        table.add_row(
            Text(source["tag"], style="accent"),
            Text(source.get("title", "No title"), style=f"link {url}" if has_link else ""),
            Text(source.get("publisher") or (domain(url) if has_link else ""), style="muted"),
            Text("✓", style="ok") if source.get("read") else Text("–", style="faint"),
        )
    return table


# ---------------------------------------------------------------------------
# Live views
# ---------------------------------------------------------------------------


class _CountColumn(ProgressColumn):
    """3/12 for counted stages, nothing for open-ended ones."""

    def render(self, task):
        if not task.fields.get("counted"):
            return Text("")
        return Text(f"{int(task.completed)}/{int(task.total)}", style="muted")


class StageProgress:
    """
    Live multi-stage progress display, driven by a pipeline via
    stage() / advance() / done(). Safe to call from worker threads.
    """

    def __init__(self):
        self._progress = Progress(
            SpinnerColumn(style="accent", finished_text="[ok]✓[/]"),
            TextColumn("{task.description}"),
            BarColumn(bar_width=24, style="faint", complete_style="accent.dim", finished_style="ok"),
            _CountColumn(),
            TextColumn("[muted]{task.fields[note]}[/]"),
            TimeElapsedColumn(),
            console=console,
        )
        self._tasks = {}

    def __enter__(self):
        self._progress.start()
        return self

    def __exit__(self, *exc):
        self._progress.stop()

    def stage(self, key, description, total=None):
        self._tasks[key] = self._progress.add_task(description, total=total, note="", counted=total is not None)

    def advance(self, key, amount=1):
        if key in self._tasks:
            self._progress.advance(self._tasks[key], amount)

    def done(self, key, note=""):
        task_id = self._tasks.get(key)
        if task_id is None:
            return
        task = self._progress.tasks[task_id]
        total = task.total if task.total else 1
        self._progress.update(task_id, total=total, completed=total, note=note)


class StreamView:
    """
    Shows a response as it's written: the newest lines of the rendered
    Markdown in a fixed-height panel, then disappears so the finished
    report can be printed in full.
    """

    def __init__(self, title):
        self.title = title
        self.text = ""
        self.started = time.monotonic()
        self._live = Live(self, console=console, refresh_per_second=10, transient=True)

    def __enter__(self):
        self._live.start()
        return self

    def __exit__(self, *exc):
        self._live.stop()

    def update(self, text):
        self.text = text

    def __rich_console__(self, rich_console, options):
        height = max(6, rich_console.size.height - 6)
        if not self.text:
            body = Spinner("dots", text=Text(" Thinking…", style="muted"), style="accent")
        else:
            lines = rich_console.render_lines(
                Markdown(self.text), options.update(width=max(20, options.max_width - 4), height=None), pad=False
            )
            body = SegmentLines(lines[-height:], new_lines=True)
        elapsed = int(time.monotonic() - self.started)
        yield Panel(
            body,
            title=f"[bold]{self.title}[/]",
            title_align="left",
            subtitle=f"[muted]{len(self.text.split())} words · {elapsed}s[/]",
            subtitle_align="right",
            box=box.ROUNDED,
            border_style="accent.dim",
            padding=(0, 1),
        )
