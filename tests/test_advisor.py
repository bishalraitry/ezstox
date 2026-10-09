import pytest

from src import config, llm_advisor


@pytest.fixture
def offline_reading(monkeypatch):
    monkeypatch.setattr(llm_advisor, "get_article_content", lambda url: None if url.endswith("/2") else "Full article text " * 20)
    monkeypatch.setattr(llm_advisor, "get_financial_news_rss", lambda limit=10: [
        {"title": "Stocks climb", "url": "https://example.com/world/1"}])


def test_prepare_builds_a_data_pack(make_portfolio, offline_reading):
    portfolio = make_portfolio({"NVDA": (100, 50), "VUSA.L": (20, 80)}, ["TSLA"], cash=1000)
    context = llm_advisor.prepare(portfolio)
    prompt = context["prompt"]

    for section in ("PORTFOLIO SUMMARY", "RISK & PERFORMANCE", "ALLOCATION", "HOLDINGS", "WATCHLIST",
                    "MARKET CONTEXT", "APP FINDINGS", "NEWS", "TASK"):
        assert f"=== {section}" in prompt, section
    assert "[NVDA-1]" in prompt and "[WORLD-1]" in prompt
    assert "VIX:" in prompt and "S&P 500:" in prompt
    assert "[RISK] NVDA is" in prompt  # concentration finding reaches the model
    assert "## Verdict" in prompt

    tags = [s["tag"] for s in context["sources"]]
    assert "NVDA-1" in tags and "WORLD-1" in tags
    read = {s["tag"]: s["read"] for s in context["sources"]}
    assert read["NVDA-1"] is True and read["NVDA-2"] is False


def test_empty_portfolio_is_rejected(make_portfolio):
    with pytest.raises(RuntimeError, match="empty"):
        llm_advisor.prepare(make_portfolio())


def test_missing_key_gives_a_readable_error(monkeypatch):
    with pytest.raises(RuntimeError, match="No OpenAI API key"):
        llm_advisor.stream_chat([{"role": "user", "content": "hi"}])


def test_request_options_per_model_family():
    assert llm_advisor._request_options("openai", "gpt-4o-mini") == {"max_completion_tokens": 4000, "temperature": 0.4}
    assert llm_advisor._request_options("openai", "gpt-5-mini") == {"max_completion_tokens": 16000}
    assert llm_advisor._request_options("deepseek", "deepseek-chat") == {"max_tokens": 4000, "temperature": 0.4}
    assert llm_advisor._request_options("deepseek", "deepseek-reasoner") == {"max_tokens": 16000}


def test_provider_falls_back_to_whichever_key_is_set(monkeypatch):
    assert config.ai_provider() == "openai"
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    assert config.ai_provider() == "deepseek"
    assert config.ai_model() == "deepseek-chat"
    monkeypatch.setenv("AI_PROVIDER", "openai")
    assert config.ai_provider() == "openai"


def test_report_followups_and_saving(make_portfolio, offline_reading, monkeypatch, tmp_path):
    monkeypatch.setattr(llm_advisor, "REPORTS_DIR", tmp_path / "reports")
    calls = []

    def fake_stream(messages, on_text=None):
        calls.append(messages)
        text = "## Verdict\nHold everything." if len(messages) == 2 else "Because of [NVDA-1]."
        if on_text:
            on_text(text)
        return text, {"input": 1000, "output": 100}

    monkeypatch.setattr(llm_advisor, "stream_chat", fake_stream)
    context = llm_advisor.prepare(make_portfolio({"NVDA": (10, 100)}))
    result = llm_advisor.write_report(context)
    answer = llm_advisor.ask_followup(result, "Why?")

    assert answer == "Because of [NVDA-1]."
    assert [m["role"] for m in calls[1]] == ["system", "user", "assistant", "user"]
    assert llm_advisor.total_usage(result) == {"input": 2000, "output": 200}

    path = llm_advisor.save_report(result)
    text = path.read_text()
    assert "## Follow-up: Why?" in text and "## Sources" in text and "## App findings" in text
    assert llm_advisor.save_report(result) == path  # re-saving updates the same file
