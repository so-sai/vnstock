"""base.py — FinancialProvider abstract contract.

Every concrete provider (vnstock, cafef, tcbs, dnse, sbv, worldbank,
fred, fixture, sqlite) implements the SAME interface. The Governor and
downstream modules depend only on this contract, never on a concrete
library — inverting the dependency direction.

Return values are pandas DataFrames, mirroring what vnstock already
returns, so existing consumers keep working without re-plumbing.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

import pandas as pd


class FinancialProvider(ABC):
    """Contract every data provider must honour."""

    name: str = "base"

    # ── Identity ────────────────────────────────────────────────────────
    @abstractmethod
    def is_available(self) -> bool:
        """Return True if this provider can be reached right now."""

    def describe(self) -> Dict[str, Any]:
        """Provider metadata (health/audit)."""
        return {
            "name": self.name,
            "available": self.is_available(),
        }

    # ── Financial statements ───────────────────────────────────────────
    @abstractmethod
    def income_statement(self, symbol: str, **kwargs: Any) -> Optional[pd.DataFrame]:
        """Income statement (IS) for a symbol."""

    @abstractmethod
    def balance_sheet(self, symbol: str, **kwargs: Any) -> Optional[pd.DataFrame]:
        """Balance sheet (BS) for a symbol."""

    @abstractmethod
    def cashflow(self, symbol: str, **kwargs: Any) -> Optional[pd.DataFrame]:
        """Cash flow statement (CF) for a symbol."""

    def financial_statements(self, symbol: str, **kwargs: Any) -> Dict[str, Optional[pd.DataFrame]]:
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
        start: Optional[str] = None,
        end: Optional[str] = None,
        **kwargs: Any,
    ) -> Optional[pd.DataFrame]:
        """Daily/periodic OHLCV history for a symbol."""

    # ── Company info ───────────────────────────────────────────────────
    def company_info(self, symbol: str, **kwargs: Any) -> Optional[Dict[str, Any]]:
        """Company profile (sector, industry, listing). Optional to implement."""
        return None

    # ── Utilities ──────────────────────────────────────────────────────
    def symbols(self, **kwargs: Any) -> Optional[List[str]]:
        """Full listed symbol universe. Optional to implement."""
        return None
