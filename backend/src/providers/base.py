"""base.py — FinancialProvider abstract contract.

Every concrete provider (vnstock, cafef, tcbs, dnse, sbv, worldbank,
fred, fixture, sqlite) implements the SAME interface. The Governor and
downstream modules depend only on this contract, never on a concrete
library — inverting the dependency direction.

Return values are pandas DataFrames, mirroring what vnstock already
returns, so existing consumers keep working without re-plumbing.
"""

from abc import ABC, abstractmethod
from typing import Any

import pandas as pd


class FinancialProvider(ABC):
    """Contract every data provider must honour."""

    name: str = "base"

    # Offline/cache tiers opt out of circuit breaking: they are the
    # final fallback, so the breaker must never skip them.
    circuit_breakable: bool = True

    # Financial data family (e.g. "VCI", "KBS") used for canonical-key
    # mapping during cross-validation. Defaults to the provider name;
    # concrete network providers override it.
    source: str = "base"

    # ── Identity ────────────────────────────────────────────────────────
    @abstractmethod
    def is_available(self) -> bool:
        """Return True if this provider can be reached right now."""

    def describe(self) -> dict[str, Any]:
        """Provider metadata (health/audit)."""
        return {
            "name": self.name,
            "available": self.is_available(),
        }

    # ── Financial statements ───────────────────────────────────────────
    @abstractmethod
    def income_statement(self, symbol: str, **kwargs: Any) -> pd.DataFrame | None:
        """Income statement (IS) for a symbol."""

    @abstractmethod
    def balance_sheet(self, symbol: str, **kwargs: Any) -> pd.DataFrame | None:
        """Balance sheet (BS) for a symbol."""

    @abstractmethod
    def cashflow(self, symbol: str, **kwargs: Any) -> pd.DataFrame | None:
        """Cash flow statement (CF) for a symbol."""

    def financial_statements(self, symbol: str, **kwargs: Any) -> dict[str, pd.DataFrame | None]:
        """Convenience: fetch all three statements in one call."""
        return {
            "IS": self.income_statement(symbol, **kwargs),
            "BS": self.balance_sheet(symbol, **kwargs),
            "CF": self.cashflow(symbol, **kwargs),
        }

    # ── Market data ────────────────────────────────────────────────────
    @abstractmethod
    def history(
        self,
        symbol: str,
        start: str | None = None,
        end: str | None = None,
        **kwargs: Any,
    ) -> pd.DataFrame | None:
        """Daily/periodic OHLCV history for a symbol."""

    # ── Company info ───────────────────────────────────────────────────
    def company_info(self, symbol: str, **kwargs: Any) -> dict[str, Any] | None:
        """Company profile (sector, industry, listing). Optional to implement."""
        return None

    # ── Utilities ──────────────────────────────────────────────────────
    def symbols(self, **kwargs: Any) -> list[str] | None:
        """Full listed symbol universe. Optional to implement."""
        return None
