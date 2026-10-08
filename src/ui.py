"""
Terminal UI building blocks (Rich).

Everything visual lives here: theme, number formatting, tables, panels,
sparklines and progress. Screens in app.py compose these pieces, so the
look stays consistent everywhere. Colours are chosen to read well on both
dark and light terminal themes.
"""

from datetime import datetime, timezone
from urllib.parse import urlparse

from rich import box
from rich.console import Console, Group
from rich.markdown import Markdown
from rich.padding import Padding
from rich.panel import Panel
from rich.progress import BarColumn, Progress, ProgressColumn, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

THEME = Theme(
    {
        "accent": "bold #a78bfa",
        "accent.dim": "#7c6fd0",
        "muted": "grey58",
        "faint": "grey37",
        "up": "#3fb950",
        "down": "#f85149",
        "warn": "#e3b341",
        "ok": "#3fb950",
        "err": "bold #f85149",
        "key": "bold #a78bfa",
        "markdown.h1.border": "#7c6fd0",
        "markdown.h2": "bold #a78bfa",
        "markdown.h3": "bold",
        "markdown.link": "#79c0ff",
        "markdown.item.bullet": "#a78bfa",
        "markdown.item.number": "#a78bfa",
    }
)

console = Console(theme=THEME, highlight=False)

BORDER = "faint"
SPARK_BLOCKS = "▁▂▃▄▅▆▇█"


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def money(value, signed=False):
    if value is None:
        return "—"
    sign = ("+" if value > 0 else "−" if value < 0 else "") if signed else ("−" if value < 0 else "")
    return f"{sign}${abs(value):,.2f}"


def number(value):
    """Share counts: no pointless decimals (10, 1.5, 0.125)."""
    return f"{value:,.8f}".rstrip("0").rstrip(".") if value is not None else "—"


def compact(value):
    """1.23T / 456.7B / 12.3M - for market caps and fund sizes."""
    if value is None:
        return "—"
    for threshold, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(value) >= threshold:
            return f"{value / threshold:,.2f}{suffix}"
    return f"{value:,.0f}"


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
    return Text(f"{'+' if pct > 0 else '−' if pct < 0 else ''}{abs(pct):.2f}%", style=tone(pct))


def pnl(amount):
    return Text(money(amount, signed=True), style=tone(amount))


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


def range_bar(low, high, current, width=24):
    """52-week range: $low ━━━━●━━━━ $high"""
    if low is None or high is None or current is None or high <= low:
        return Text("—", style="muted")
    position = round((min(max(current, low), high) - low) / (high - low) * (width - 1))
    bar = Text()
    bar.append(f"{money(low)} ", style="muted")
    bar.append("━" * position, style="accent.dim")
    bar.append("●", style="accent")
    bar.append("━" * (width - 1 - position), style="faint")
    bar.append(f" {money(high)}", style="muted")
    return bar


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


def success(message):
    console.print(f"[ok]✓[/] {message}")


def warn(message):
    console.print(f"[warn]![/] {message}")


def error(message):
    console.print(f"[err]✗[/] {message}")


def hint(message):
    console.print(f"[muted]{message}[/]")


def pause():
    try:
        console.input("\n[muted]Press [bold]Enter[/bold] to return to the menu…[/] ")
    except EOFError:
        pass


# ---------------------------------------------------------------------------
# Layout pieces
# ---------------------------------------------------------------------------


def header(subtitle=None):
    grid = Table.grid(expand=True)
    grid.add_column()
    grid.add_column(justify="right")
    left = Text.assemble(("◆ ", "accent"), ("ezstox", "bold"))
    left.append(f"  ·  {subtitle}" if subtitle else "  ·  Portfolio tracker & AI advisor", style="muted")
    now = datetime.now()
    right = Text(f"{now:%a} {now.day} {now:%b · %H:%M}", style="muted")
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
    """A row of equal-width stat tiles (2 per row on narrow terminals)."""
    per_row = len(items) if console.width >= 90 else 2
    grid = Table.grid(expand=True, padding=(0, 1))
    for _ in range(per_row):
        grid.add_column(ratio=1)
    for i in range(0, len(items), per_row):
        row = list(items[i : i + per_row])
        grid.add_row(*row, *[""] * (per_row - len(row)))
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


# ---------------------------------------------------------------------------
# Domain renderables
# ---------------------------------------------------------------------------


def holdings_table(rows):
    """
    rows: dicts with symbol, shares, cost_basis, price, day_pct, value,
    pnl, pnl_pct, weight, history (price/value fields None if unpriced)
    """
    wide = console.width >= 110
    columns = [
        ("Symbol", "left"),
        ("Shares", "right"),
        ("Avg cost", "right"),
        ("Price", "right"),
        ("Today", "right"),
        ("Value", "right"),
        ("P&L", "right"),
        ("Return", "right"),
        ("Weight", "right"),
    ]
    if wide:
        columns.append(("1M trend", "left"))
    table = data_table(*columns)

    for row in rows:
        if row["price"] is None:
            cells = [
                Text(row["symbol"], style="bold"),
                number(row["shares"]),
                money(row["cost_basis"]),
                Text("unavailable", style="muted"),
                *[Text("—", style="muted")] * 5,
            ]
        else:
            cells = [
                Text(row["symbol"], style="bold"),
                number(row["shares"]),
                money(row["cost_basis"]),
                money(row["price"]),
                change(row["day_pct"]),
                money(row["value"]),
                pnl(row["pnl"]),
                change(row["pnl_pct"], arrow=False),
                Text(f"{row['weight']:.1f}%", style="muted"),
            ]
        if wide:
            cells.append(sparkline(row.get("history")))
        table.add_row(*cells)
    return table


def watchlist_table(rows):
    """rows: dicts with symbol, price, day_pct, change_5d_pct, history"""
    wide = console.width >= 80
    columns = [("Symbol", "left"), ("Price", "right"), ("Today", "right"), ("5 days", "right")]
    if wide:
        columns.append(("1M trend", "left"))
    table = data_table(*columns)
    for row in rows:
        if row["price"] is None:
            cells = [Text(row["symbol"], style="bold"), Text("unavailable", style="muted"), "—", "—"]
        else:
            cells = [
                Text(row["symbol"], style="bold"),
                money(row["price"]),
                change(row["day_pct"]),
                change(row["change_5d_pct"]),
            ]
        if wide:
            cells.append(sparkline(row.get("history")))
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
# Progress
# ---------------------------------------------------------------------------


class _CountColumn(ProgressColumn):
    """3/12 for counted stages, nothing for open-ended ones."""

    def render(self, task):
        if not task.fields.get("counted"):
            return Text("")
        return Text(f"{int(task.completed)}/{int(task.total)}", style="muted")


class StageProgress:
    """
    Live multi-stage progress display, driven by the advisor pipeline via
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
