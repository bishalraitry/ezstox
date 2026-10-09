import pytest

from src.portfolio_manager import Portfolio, is_valid_symbol


def test_fresh_portfolio_is_empty_and_creates_no_fake_holdings(tmp_path):
    portfolio = Portfolio(data_dir=tmp_path)
    assert portfolio.holdings == {} and portfolio.watchlist == [] and portfolio.cash == 0
    assert portfolio.is_empty


def test_round_trip_through_files(tmp_path):
    portfolio = Portfolio(data_dir=tmp_path)
    portfolio.set_holding("aapl", 10, 150.5)
    portfolio.set_holding("VUSA.L", 2.125, 80)
    portfolio.add_to_watchlist("tsla")
    portfolio.set_cash(1234.56)

    reloaded = Portfolio(data_dir=tmp_path)
    assert reloaded.holdings == {"AAPL": {"shares": 10, "cost_basis": 150.5}, "VUSA.L": {"shares": 2.125, "cost_basis": 80}}
    assert reloaded.watchlist == ["TSLA"]
    assert reloaded.cash == pytest.approx(1234.56)
    assert "AAPL,10,150.5" in (tmp_path / "portfolio.txt").read_text()


def test_invalid_lines_are_skipped_with_a_warning(tmp_path):
    (tmp_path / "portfolio.txt").write_text("# comment\nAAPL,10,150\nGARBAGE\nMSFT,x,1\n")
    portfolio = Portfolio(data_dir=tmp_path)
    assert list(portfolio.holdings) == ["AAPL"]
    assert len(portfolio.warnings) == 2


def test_watchlist_dedupes_and_remove(tmp_path):
    portfolio = Portfolio(data_dir=tmp_path)
    assert portfolio.add_to_watchlist("AMD")
    assert not portfolio.add_to_watchlist("amd")
    assert portfolio.remove_from_watchlist("AMD")
    assert not portfolio.remove_from_watchlist("AMD")


def test_total_return_percentage_is_gain_over_invested(tmp_path):
    # Regression: this used to divide the gain by itself (always 100%)
    portfolio = Portfolio(data_dir=tmp_path)
    portfolio.holdings = {"AAPL": {"shares": 10, "cost_basis": 100}}
    totals = portfolio.get_total_value({"AAPL": 110})
    assert totals["total_gain_loss"] == pytest.approx(100)
    assert totals["total_gain_loss_pct"] == pytest.approx(10)
    assert portfolio.get_total_value({"AAPL": 100})["total_gain_loss_pct"] == 0


@pytest.mark.parametrize("symbol,valid", [("AAPL", True), ("brk-b", True), ("VUSA.L", True), ("^VIX", True),
                                          ("GC=F", True), ("!!", False), ("apple inc", False), ("", False)])
def test_symbol_validation(symbol, valid):
    assert is_valid_symbol(symbol) is valid
