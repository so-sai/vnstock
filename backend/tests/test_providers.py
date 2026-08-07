"""test_providers.py - TDD for the FinancialProvider abstraction & ProviderManager."""

import time

import pandas as pd
from src.providers.base import FinancialProvider
from src.providers.manager import CircuitBreaker, ProviderManager


class DummyProvider(FinancialProvider):
    """Deterministic fake provider for fallback-chain tests."""

    def __init__(self, name, available=True, fail_history=False, fail_statements=False):
        self.name = name
        self._available = available
        self._fail_history = fail_history
        self._fail_statements = fail_statements

    def is_available(self):
        return self._available

    def income_statement(self, symbol, **kwargs):
        if self._fail_statements:
            raise RuntimeError("boom")
        return pd.DataFrame({"symbol": [symbol], "metric": ["REVENUE"], "value": [100.0]})

    def balance_sheet(self, symbol, **kwargs):
        if self._fail_statements:
            raise RuntimeError("boom")
        return pd.DataFrame({"symbol": [symbol], "metric": ["ASSETS"], "value": [500.0]})

    def cashflow(self, symbol, **kwargs):
        if self._fail_statements:
            raise RuntimeError("boom")
        return pd.DataFrame({"symbol": [symbol], "metric": ["CFO"], "value": [20.0]})

    def history(self, symbol, start=None, end=None, **kwargs):
        if self._fail_history:
            raise RuntimeError("boom")
        return pd.DataFrame({"symbol": [symbol], "date": ["2026-01-01"], "close": [10.0]})


def test_abc_is_abstract():
    """FinancialProvider cannot be instantiated directly."""
    try:
        FinancialProvider()  # type: ignore[abstract]
        raised = False
    except TypeError:
        raised = True
    assert raised


def test_provider_registers_unique():
    mgr = ProviderManager()
    mgr.register(DummyProvider("a"))
    mgr.register(DummyProvider("a"))
    assert len(mgr.providers) == 1


def test_fallback_to_second_provider():
    """First provider down → manager transparently uses the second."""
    dead = DummyProvider("dead", available=False, fail_history=True, fail_statements=True)
    alive = DummyProvider("alive")
    mgr = ProviderManager([dead, alive])

    df = mgr.income_statement("VCB")
    assert df is not None and not df.empty
    assert mgr.source_attribution().get("alive") == 1
    assert mgr.failures().get("dead") == 1


def test_empty_dataframe_treated_as_failure():
    """Empty result skips to next provider (mirrors silent-throttle)."""
    class EmptyProvider(FinancialProvider):
        name = "empty"

        def is_available(self):
            return True

        def income_statement(self, symbol, **kwargs):
            return pd.DataFrame()

        def balance_sheet(self, symbol, **kwargs):
            return pd.DataFrame()

        def cashflow(self, symbol, **kwargs):
            return pd.DataFrame()

        def history(self, symbol, start=None, end=None, **kwargs):
            return pd.DataFrame()

    alive = DummyProvider("alive")
    mgr = ProviderManager([EmptyProvider(), alive])

    df = mgr.cashflow("FPT")
    assert df is not None and not df.empty
    assert mgr.source_attribution().get("alive") == 1


def test_all_down_returns_none():
    mgr = ProviderManager([
        DummyProvider("x", fail_history=True, fail_statements=True),
        DummyProvider("y", fail_history=True, fail_statements=True),
    ])
    assert mgr.income_statement("ANY") is None
    assert mgr.history("ANY") is None


def test_active_source_prefers_available():
    mgr = ProviderManager([
        DummyProvider("a", available=False),
        DummyProvider("b", available=True),
    ])
    assert mgr.active_source() == "b"


def test_financial_statements_bundle():
    mgr = ProviderManager([DummyProvider("a")])
    bundle = mgr.financial_statements("HPG")
    assert set(bundle.keys()) == {"IS", "BS", "CF"}
    assert all(df is not None and not df.empty for df in bundle.values())


def test_provider_describe_shape():
    p = DummyProvider("audited")
    meta = p.describe()
    assert meta["name"] == "audited"
    assert "available" in meta


# ── Circuit Breaker ───────────────────────────────────────────────────

class AlwaysFailingProvider(FinancialProvider):
    """Deterministic provider that raises on every call."""

    name = "flaky"

    def __init__(self):
        self.calls = 0

    def is_available(self):
        return True

    def income_statement(self, symbol, **kwargs):
        self.calls += 1
        raise RuntimeError("network down")

    def balance_sheet(self, symbol, **kwargs):
        self.calls += 1
        raise RuntimeError("network down")

    def cashflow(self, symbol, **kwargs):
        self.calls += 1
        raise RuntimeError("network down")

    def history(self, symbol, start=None, end=None, **kwargs):
        self.calls += 1
        raise RuntimeError("network down")


def test_circuit_breaker_trips_after_threshold():
    cb = CircuitBreaker(window_seconds=300, error_rate_threshold=0.5, min_samples=3, cooldown_seconds=60)
    assert cb.state == "CLOSED"
    for _ in range(3):
        cb.record_failure()
    assert cb.state == "OPEN"
    assert cb.allow_request() is False


def test_circuit_breaker_stays_closed_with_few_failures():
    cb = CircuitBreaker(window_seconds=300, error_rate_threshold=0.5, min_samples=5, cooldown_seconds=60)
    cb.record_failure()
    cb.record_success()
    assert cb.state == "CLOSED"
    assert cb.allow_request() is True


def test_circuit_breaker_open_then_half_open_probe():
    cb = CircuitBreaker(window_seconds=300, error_rate_threshold=0.5, min_samples=3, cooldown_seconds=0)
    for _ in range(3):
        cb.record_failure()
    assert cb.state == "OPEN"
    time.sleep(0.01)
    assert cb.allow_request() is True  # cooldown elapsed → HALF-OPEN probe
    assert cb.state == "HALF-OPEN"
    cb.record_success()
    assert cb.state == "CLOSED"


def test_circuit_breaker_half_open_failure_reopens():
    cb = CircuitBreaker(window_seconds=300, error_rate_threshold=0.5, min_samples=3, cooldown_seconds=0)
    for _ in range(3):
        cb.record_failure()
    assert cb.state == "OPEN"
    time.sleep(0.01)
    cb.allow_request()
    cb.record_failure()
    assert cb.state == "OPEN"


def test_manager_skips_open_provider_and_uses_fallback():
    flaky = AlwaysFailingProvider()
    alive = DummyProvider("alive")
    mgr = ProviderManager(
        [flaky, alive],
        breaker_factory=lambda: CircuitBreaker(window_seconds=300, error_rate_threshold=0.5, min_samples=3),
    )

    # Drive the breaker OPEN with three failures.
    for _ in range(3):
        mgr.income_statement("VCB")
    assert mgr.breaker_states()["flaky"] == "OPEN"

    # Further calls must skip flaky entirely (no new calls) → fall back to alive.
    calls_before = flaky.calls
    df = mgr.income_statement("VCB")
    assert df is not None and not df.empty
    assert flaky.calls == calls_before  # never invoked again
    assert mgr.source_attribution().get("alive", 0) > 0
    assert mgr.tripped_sources().get("flaky", 0) > 0


def test_manager_sqlite_not_circuit_breakable():
    """Offline tier must never get a breaker (it is the guaranteed fallback)."""
    mgr = ProviderManager()
    mgr.register(DummyProvider("network"))
    from src.providers.sqlite_provider import SqliteCacheProvider

    mgr.register(SqliteCacheProvider(db_path=None))
    assert mgr.breaker_states() == {"network": "CLOSED"}
    assert "sqlite" not in mgr.breaker_states()


def test_active_source_skips_open_provider():
    flaky = AlwaysFailingProvider()
    alive = DummyProvider("alive")
    mgr = ProviderManager(
        [flaky, alive],
        breaker_factory=lambda: CircuitBreaker(window_seconds=300, error_rate_threshold=0.5, min_samples=3),
    )
    for _ in range(3):
        mgr.income_statement("VCB")
    assert mgr.breaker_states()["flaky"] == "OPEN"
    assert mgr.active_source() == "alive"


def test_empty_result_counts_as_breaker_failure():
    class EmptyProvider(FinancialProvider):
        name = "empty"

        def is_available(self):
            return True

        def income_statement(self, symbol, **kwargs):
            return pd.DataFrame()

        def balance_sheet(self, symbol, **kwargs):
            return pd.DataFrame()

        def cashflow(self, symbol, **kwargs):
            return pd.DataFrame()

        def history(self, symbol, start=None, end=None, **kwargs):
            return pd.DataFrame()

    mgr = ProviderManager(
        [EmptyProvider()],
        breaker_factory=lambda: CircuitBreaker(window_seconds=300, error_rate_threshold=0.5, min_samples=3),
    )
    for _ in range(3):
        mgr.cashflow("FPT")
    assert mgr.breaker_states()["empty"] == "OPEN"
