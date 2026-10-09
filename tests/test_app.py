"""End-to-end screen tests: real screens, fake market, scripted keystrokes."""

import pytest

from src import app, llm_advisor
from src.portfolio_manager import Portfolio


@pytest.fixture
def portfolio(app_env, make_portfolio, monkeypatch):
    from src import config

    monkeypatch.setattr(config, "DATA_DIR", app_env.data)
    p = Portfolio(data_dir=app_env.data)
    p.holdings = {"NVDA": {"shares": 40, "cost_basis": 60}, "AAPL": {"shares": 10, "cost_basis": 150},
                  "VUSA.L": {"shares": 20, "cost_basis": 80}}
    p.watchlist = ["TSLA", "AMD"]
    p.cash = 1500
    p.save()
    return Portfolio(data_dir=app_env.data)


def test_dashboard_shows_values_in_base_currency_with_highlights(app_env, portfolio):
    app.dashboard(portfolio)
    out = app_env.output()
    assert "PORTFOLIO VALUE" in out and "VS S&P 500" in out
    assert "£94.12" in out  # London ETF priced in its own currency...
    assert "values in USD" in out  # ...but valued in the base currency
    assert "Highlights" in out and "NVDA is" in out


def test_insights_sections(app_env, portfolio):
    app.insights(portfolio)
    out = app_env.output()
    for heading in ("Key findings", "Performance vs S&P 500", "VOLATILITY", "BETA", "SHARPE RATIO",
                    "By holding", "By sector", "Correlation", "Trend & momentum", "Upcoming (30 days)"):
        assert heading in out, heading


def test_lookup_resolves_company_names(app_env, portfolio):
    app_env.feed("1", "n")  # pick the first search result, don't add to watchlist
    app.lookup(portfolio, symbol="apple")
    out = app_env.output()
    assert "Matches for “apple”" in out
    assert "Apple Inc." in out and "Key stats" in out and "Analyst target" in out


def test_manage_flow_updates_files(app_env, portfolio):
    app_env.feed(
        "1", "msft", "3", "",          # add MSFT: 3 shares at the default (live) price
        "3", "meta, amd",              # watch META (AMD already watched)
        "4", "TSLA",                   # stop watching TSLA
        "5", "2500",                   # cash
        "2", "AAPL", "y",              # remove AAPL
        "0",
    )
    app.manage(portfolio)
    reloaded = Portfolio(data_dir=app_env.data)
    assert set(reloaded.holdings) == {"NVDA", "VUSA.L", "MSFT"}
    assert reloaded.holdings["MSFT"]["shares"] == 3
    assert reloaded.watchlist == ["AMD", "META"]
    assert reloaded.cash == 2500


def test_ai_analysis_streams_saves_and_answers_followups(app_env, portfolio, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(llm_advisor, "get_article_content", lambda url: "Article text " * 20)
    monkeypatch.setattr(llm_advisor, "get_financial_news_rss", lambda limit=10: [])

    def fake_stream(messages, on_text=None):
        text = "## Verdict\n| Symbol | Action |\n|---|---|\n| NVDA | Trim |" if len(messages) == 2 else "Trim 10%."
        on_text and on_text(text)
        return text, {"input": 5000, "output": 400}

    monkeypatch.setattr(llm_advisor, "stream_chat", fake_stream)
    app_env.feed("How much should I trim?", "")
    app.ai_analysis(portfolio, confirm=False)

    out = app_env.output()
    assert "Verdict" in out and "NVDA" in out and "Sources" in out
    assert "10,000 input + 800 output tokens" not in out  # usage shown before follow-ups
    assert "5,000 input + 400 output tokens" in out
    report = next(app_env.reports.glob("*.md")).read_text()
    assert "## Follow-up: How much should I trim?" in report


def test_settings_switch_provider_and_currency(app_env, portfolio, monkeypatch):
    from src import config

    app_env.feed("1", "2", "4", "2", "0")  # provider -> DeepSeek, currency -> GBP, back
    app.settings()
    assert config.ai_provider() == "deepseek"
    assert config.base_currency() == "GBP"
    monkeypatch.delenv("AI_PROVIDER")
    monkeypatch.delenv("BASE_CURRENCY")


def test_watch_mode_renders_then_exits_on_ctrl_c(app_env, portfolio, monkeypatch):
    def interrupt(_):
        raise KeyboardInterrupt

    monkeypatch.setattr(app.time, "sleep", interrupt)
    with pytest.raises(KeyboardInterrupt):
        app.watch(portfolio)


def test_menu_quits_cleanly_on_end_of_input(app_env, portfolio):
    app_env.feed("1")  # open the dashboard, then input runs out
    app.run_menu()
    assert "PORTFOLIO VALUE" in app_env.output()


def test_first_run_onboarding(app_env, monkeypatch):
    from src import config

    monkeypatch.setattr(config, "DATA_DIR", app_env.data)
    app_env.feed("2", "5", "100", "0", "n", "q")  # GBP, set cash 100, back, skip AI, quit
    app.run_menu()
    assert config.base_currency() == "GBP"
    assert Portfolio(data_dir=app_env.data).cash == 100
    monkeypatch.delenv("BASE_CURRENCY")
