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
from typing import Any, Callable, Dict, List, Optional

import pandas as pd

from src.providers.base import FinancialProvider

logger = logging.getLogger(__name__)

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

    def __init__(self, providers: Optional[List[FinancialProvider]] = None) -> None:
        self._providers: List[FinancialProvider] = providers or []
        self._lock = threading.Lock()
        self._source_attribution: Dict[str, int] = {}
        self._failures: Dict[str, int] = {}

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

    @property
    def providers(self) -> List[FinancialProvider]:
        return list(self._providers)

    def active_source(self) -> Optional[str]:
        """Name of the first currently-available provider."""
        for p in self._providers:
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

    # ── Generic dispatch ───────────────────────────────────────────────
    def _try_call(self, method: str, *args: Any, **kwargs: Any):
        """Invoke `method` on each provider until one succeeds."""
        for provider in self._providers:
            try:
                fn: Callable[..., Any] = getattr(provider, method)
                result = fn(*args, **kwargs)
                if result is not None and not (isinstance(result, pd.DataFrame) and result.empty):
                    with self._lock:
                        self._source_attribution[provider.name] = (
                            self._source_attribution.get(provider.name, 0) + 1
                        )
                    return result, provider.name
            except Exception as e:  # noqa: BLE001 - provider boundary
                logger.debug("Provider %s failed %s: %s", provider.name, method, e)
            with self._lock:
                self._failures[provider.name] = self._failures.get(provider.name, 0) + 1
        return None, None

    # ── Domain methods (mirror FinancialProvider) ──────────────────────
    def income_statement(self, symbol: str, **kwargs: Any) -> Optional[pd.DataFrame]:
        result, _ = self._try_call("income_statement", symbol, **kwargs)
        return result

    def balance_sheet(self, symbol: str, **kwargs: Any) -> Optional[pd.DataFrame]:
        result, _ = self._try_call("balance_sheet", symbol, **kwargs)
        return result

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

    def symbols(self, **kwargs: Any) -> Optional[List[str]]:
        result, _ = self._try_call("symbols", **kwargs)
        return result
