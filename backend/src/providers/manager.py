"""manager.py — ProviderManager with fallback chain.

Resolves every data request against an ordered list of providers:

    VNStock → CafeF → TCBS → DNSE → Fixture/SQLite

If the first provider fails (network, throttling, empty payload), the
manager transparently tries the next one. The Governor never sees which
source answered — it only receives a normalized DataFrame.

Also records per-request source attribution for audit / Silent
Throttling telemetry (mirrors backfill_engine counters).
"""

import logging
import threading
import time
from collections import deque
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

import pandas as pd

from src.providers.base import FinancialProvider
from src.providers.cross_validate import FLAG_DISCREPANCY
from src.providers.cross_validate import cross_validate as xvalidate

logger = logging.getLogger(__name__)


class CircuitBreaker:
    """Per-provider failure circuit breaker (CLOSED → OPEN → HALF-OPEN).

    Tracks outcomes in a rolling time window. When the error rate over
    the window exceeds `error_rate_threshold` (and at least
    `min_samples` calls were observed) the breaker trips to OPEN and
    `allow_request()` starts returning False, so the ProviderManager
    skips that source and falls through to the offline cache tier.

    After `cooldown_seconds` the breaker moves to HALF-OPEN and lets a
    single probe through; a successful probe resets it to CLOSED, a
    failed probe re-opens it. The breaker is per-provider and needs no
    process restart to take effect.
    """

    def __init__(
        self,
        window_seconds: int = 300,
        error_rate_threshold: float = 0.5,
        min_samples: int = 5,
        cooldown_seconds: int = 60,
    ) -> None:
        self.window_seconds = window_seconds
        self.error_rate_threshold = error_rate_threshold
        self.min_samples = min_samples
        self.cooldown_seconds = cooldown_seconds
        self._lock = threading.Lock()
        self._state = "CLOSED"
        self._events: Deque[Tuple[float, bool]] = deque()
        self._state_since = time.time()

    # ── State ──────────────────────────────────────────────────────────
    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    def reset(self) -> None:
        with self._lock:
            self._state = "CLOSED"
            self._events.clear()
            self._state_since = time.time()

    # ── Request gating ─────────────────────────────────────────────────
    def allow_request(self) -> bool:
        """True if a request may be sent to the guarded provider."""
        with self._lock:
            if self._state == "OPEN":
                if time.time() - self._state_since >= self.cooldown_seconds:
                    self._state = "HALF-OPEN"
                    self._state_since = time.time()
                    return True
                return False
            return True

    # ── Outcome recording ──────────────────────────────────────────────
    def record_success(self) -> None:
        with self._lock:
            self._prune(time.time())
            self._events.append((time.time(), True))
            if self._state == "HALF-OPEN":
                self._state = "CLOSED"
                self._events.clear()
                self._state_since = time.time()

    def record_failure(self) -> None:
        with self._lock:
            now = time.time()
            self._prune(now)
            self._events.append((now, False))
            if self._state == "HALF-OPEN":
                self._state = "OPEN"
                self._state_since = now
                return
            if len(self._events) >= self.min_samples and self._error_rate() > self.error_rate_threshold:
                self._state = "OPEN"
                self._state_since = now

    # ── Internals ──────────────────────────────────────────────────────
    def _prune(self, now: float) -> None:
        while self._events and now - self._events[0][0] > self.window_seconds:
            self._events.popleft()

    def _error_rate(self) -> float:
        if not self._events:
            return 0.0
        failures = sum(1 for _, ok in self._events if not ok)
        return failures / len(self._events)


# ── Default manager (module-level singleton) ─────────────────────────
_default_manager: Optional["ProviderManager"] = None
_manager_lock = threading.Lock()


def get_provider_manager(providers: Optional[List[FinancialProvider]] = None) -> "ProviderManager":
    """Return the shared ProviderManager, registering `providers` on first call."""
    global _default_manager
    with _manager_lock:
        if _default_manager is None:
            _default_manager = ProviderManager()
            _default_manager._load_default_providers()
        if providers:
            for p in providers:
                _default_manager.register(p)
        return _default_manager


def reset_provider_manager() -> None:
    """Reset the singleton (used by tests)."""
    global _default_manager
    with _manager_lock:
        _default_manager = None


class ProviderManager:
    """Ordered fallback chain over FinancialProvider implementations."""

    def __init__(
        self,
        providers: Optional[List[FinancialProvider]] = None,
        breaker_factory: Optional[Callable[[], CircuitBreaker]] = None,
    ) -> None:
        self._providers: List[FinancialProvider] = providers or []
        self._lock = threading.Lock()
        self._source_attribution: Dict[str, int] = {}
        self._failures: Dict[str, int] = {}
        self._tripped: Dict[str, int] = {}
        self._breakers: Dict[str, CircuitBreaker] = {}
        self._breaker_factory = breaker_factory or CircuitBreaker
        for provider in self._providers:
            if getattr(provider, "circuit_breakable", True):
                self._breakers[provider.name] = self._breaker_factory()
        self._cross_validation: Dict[tuple, Dict[str, Any]] = {}
        self._discrepancies: Dict[tuple, Dict[str, Any]] = {}
        self._last_primary_source: Optional[str] = None
        self._secondary_provider = self._make_secondary_provider
        self._forensic_cache: Any = None

    def _make_secondary_provider(self, source: str = "KBS") -> FinancialProvider:
        """Build a cross-validation secondary provider (KBS by default)."""
        from src.providers.vnstock_provider import VnstockProvider

        return VnstockProvider(source=source)

    # ── Provider registry ──────────────────────────────────────────────
    def _load_default_providers(self) -> None:
        """Register the default provider chain (Vnstock → SQLite cache).

        Order matters: network providers lead, the offline cache is the
        final fallback so the Governor always has a source.
        """
        try:
            from src.providers.vnstock_provider import VnstockProvider

            self.register(VnstockProvider())
        except Exception as e:  # noqa: BLE001
            logger.warning("VnstockProvider unavailable: %s", e)
        try:
            from src.providers.sqlite_provider import SqliteCacheProvider

            self.register(SqliteCacheProvider())
        except Exception as e:  # noqa: BLE001
            logger.debug("SqliteCacheProvider unavailable: %s", e)

    def register(self, provider: FinancialProvider) -> None:
        if provider.name not in [p.name for p in self._providers]:
            self._providers.append(provider)
            if getattr(provider, "circuit_breakable", True):
                self._breakers[provider.name] = self._breaker_factory()

    @property
    def providers(self) -> List[FinancialProvider]:
        return list(self._providers)

    def active_source(self) -> Optional[str]:
        """Name of the first currently-available (and un-tripped) provider."""
        for p in self._providers:
            breaker = self._breakers.get(p.name)
            if breaker is not None and breaker.state == "OPEN":
                continue
            try:
                if p.is_available():
                    return p.name
            except Exception:
                continue
        return None

    def source_attribution(self) -> Dict[str, int]:
        with self._lock:
            return dict(self._source_attribution)

    def failures(self) -> Dict[str, int]:
        with self._lock:
            return dict(self._failures)

    def breaker_states(self) -> Dict[str, str]:
        """Current circuit state per breakable provider."""
        return {name: cb.state for name, cb in self._breakers.items()}

    def tripped_sources(self) -> Dict[str, int]:
        """Count of requests skipped because a provider was OPEN."""
        with self._lock:
            return dict(self._tripped)

    def reset_breakers(self) -> None:
        """Manually close all circuits (ops recovery)."""
        for cb in self._breakers.values():
            cb.reset()

    # ── Generic dispatch ───────────────────────────────────────────────
    def _try_call(self, method: str, *args: Any, **kwargs: Any):
        """Invoke `method` on each provider until one succeeds.

        A provider whose circuit is OPEN is skipped entirely so the
        manager transparently falls through to the next source (the
        offline cache tier when every network source is tripped).
        """
        for provider in self._providers:
            breaker = self._breakers.get(provider.name)
            if breaker is not None and not breaker.allow_request():
                with self._lock:
                    self._tripped[provider.name] = self._tripped.get(provider.name, 0) + 1
                logger.debug("Circuit OPEN for %s — skipped %s", provider.name, method)
                continue
            try:
                fn: Callable[..., Any] = getattr(provider, method)
                result = fn(*args, **kwargs)
                success = result is not None and not (isinstance(result, pd.DataFrame) and result.empty)
                if breaker is not None:
                    if success:
                        breaker.record_success()
                    else:
                        breaker.record_failure()
                if success:
                    with self._lock:
                        self._source_attribution[provider.name] = self._source_attribution.get(provider.name, 0) + 1
                        self._last_primary_source = getattr(provider, "source", provider.name)
                    return result, provider.name
            except Exception as e:  # noqa: BLE001 - provider boundary
                logger.debug("Provider %s failed %s: %s", provider.name, method, e)
                if breaker is not None:
                    breaker.record_failure()
            with self._lock:
                self._failures[provider.name] = self._failures.get(provider.name, 0) + 1
        return None, None

    # ── Cross-validation audit ──────────────────────────────────────────
    def cross_validation_report(self) -> Dict[str, Dict[str, Any]]:
        """Full audit trail of every cross-validation run (symbol, method)."""
        with self._lock:
            return {f"{sym}:{m}": dict(rep) for (sym, m), rep in sorted(self._cross_validation.items())}

    def discrepancies(self) -> Dict[str, Dict[str, Any]]:
        """Only the flagged FLAG_DISCREPANCY records (quarantine view)."""
        with self._lock:
            return {f"{sym}:{m}": dict(rep) for (sym, m), rep in sorted(self._discrepancies.items())}

    def cross_validate(
        self,
        symbol: str,
        method: str = "income_statement",
        secondary_source: str = "KBS",
        threshold: float = 0.05,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Run an on-demand cross-validation for a single symbol/method.

        Returns the comparison report (VERIFIED / FLAG_DISCREPANCY /
        NO_SECONDARY) without touching the provider chain's return value.
        """
        from src.providers.cross_validate import compare_statements

        result, primary_source = self._try_call(method, symbol, **kwargs)
        if result is None:
            return {"status": "NO_PRIMARY", "max_error": 0.0, "metrics": {}}
        secondary = self._secondary_provider(source=secondary_source)
        sec_result = getattr(secondary, method)(symbol, **kwargs)
        report = compare_statements(
            result,
            sec_result,
            source=primary_source or "VCI",
            secondary_source=secondary_source,
            method=method,
            threshold=threshold,
        )
        with self._lock:
            self._cross_validation[(symbol, method)] = report
            if report["status"] == FLAG_DISCREPANCY:
                self._discrepancies[(symbol, method)] = report
        return report

    # ── Domain methods (mirror FinancialProvider) ──────────────────────
    @xvalidate
    def income_statement(self, symbol: str, **kwargs: Any) -> Optional[pd.DataFrame]:
        result, _ = self._try_call("income_statement", symbol, **kwargs)
        return result

    @xvalidate
    def balance_sheet(self, symbol: str, **kwargs: Any) -> Optional[pd.DataFrame]:
        result, _ = self._try_call("balance_sheet", symbol, **kwargs)
        return result

    @xvalidate
    def cashflow(self, symbol: str, **kwargs: Any) -> Optional[pd.DataFrame]:
        result, _ = self._try_call("cashflow", symbol, **kwargs)
        return result

    def financial_statements(self, symbol: str, **kwargs: Any) -> Dict[str, Optional[pd.DataFrame]]:
        return {
            "IS": self.income_statement(symbol, **kwargs),
            "BS": self.balance_sheet(symbol, **kwargs),
            "CF": self.cashflow(symbol, **kwargs),
        }

    def history(
        self,
        symbol: str,
        start: Optional[str] = None,
        end: Optional[str] = None,
        **kwargs: Any,
    ) -> Optional[pd.DataFrame]:
        result, _ = self._try_call("history", symbol, start, end, **kwargs)
        return result

    def company_info(self, symbol: str, **kwargs: Any) -> Optional[Dict[str, Any]]:
        result, _ = self._try_call("company_info", symbol, **kwargs)
        return result

    # ── Forensic screening cache (O(1) read, never recomputes on request) ─
    def forensic_risk(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Latest cached forensic score for a symbol (Beneish/Sloan/ARI).

        Reads the materialized `forensic_scores` table via PRIMARY KEY lookup
        — O(1), zero recompute at request time. The table is refreshed by a
        post-backfill hook (:meth:`ForensicScoreCache.refresh`), never inside
        a REST/SSE request path. Returns None when the cache is empty.
        """
        try:
            if self._forensic_cache is None:
                from src.financial.forensic_engine import ForensicScoreCache

                self._forensic_cache = ForensicScoreCache()
            return self._forensic_cache.get(symbol)
        except Exception:  # noqa: BLE001 - provider boundary
            return None

    def symbols(self, **kwargs: Any) -> Optional[List[str]]:
        result, _ = self._try_call("symbols", **kwargs)
        return result
