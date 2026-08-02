"""test_sqlite_provider.py - TDD for the SQLite cache provider (offline fallback tier)."""

from src.providers import (
    SqliteCacheProvider,
    get_provider_manager,
    reset_provider_manager,
)


def test_sqlite_available_with_real_db():
    """The production financial_facts.db exists and is readable."""
    p = SqliteCacheProvider()
    assert p.is_available(), "financial_facts.db should exist in backend/data"
    meta = p.describe()
    assert meta["name"] == "sqlite"
    assert meta["available"] is True


def test_sqlite_reads_real_facts():
    """Reading known symbols returns normalized long-format facts."""
    p = SqliteCacheProvider()
    df = p.income_statement("FPT")
    assert df is not None and not df.empty
    assert set(["symbol", "period", "metric", "value"]).issubset(df.columns)


def test_sqlite_unknown_symbol_returns_none():
    p = SqliteCacheProvider()
    assert p.income_statement("__NOT_A_REAL_SYM__") is None
    assert p.balance_sheet("__NOT_A_REAL_SYM__") is None
    assert p.cashflow("__NOT_A_REAL_SYM__") is None


def test_sqlite_history_reads_ohlcv():
    p = SqliteCacheProvider()
    df = p.history("HPG", start="2026-07-01", end="2026-07-10")
    assert df is not None and not df.empty
    assert "close" in df.columns


def test_manager_factory_registers_defaults():
    reset_provider_manager()
    mgr = get_provider_manager()
    names = [p.name for p in mgr.providers]
    assert "sqlite" in names
    # vnstock may or may not be importable in CI; if present, it leads the chain
    assert "vnstock" not in names or names.index("sqlite") > names.index("vnstock")
