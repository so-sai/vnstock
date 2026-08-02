"""providers/__init__.py — Data Provider abstraction layer.

WHY:
  Decouples the Core Engine (Governor, Financial, Valuation, CSI) from
  concrete data acquisition libraries. The Governor should only ever ask:

      get_income_statement("VCB")

  and the ProviderManager decides which source answers (VNStock → CafeF
  → TCBS → Fixture/SQLite). This inverts the dependency: core logic no
  longer imports `vnstock` directly, so a single provider dying never
  breaks the decision engine.

Layered data flow (target architecture):

  Internet
      │
      ▼
  Providers   (vnstock / cafef / tcbs / sbv / worldbank / fred / fixture)
      │
      ▼
  ETL / Validation
      │
      ▼
  financial_facts.db   ← Governor ONLY reads this
      │
      ▼
  Decision
"""

from src.providers.base import FinancialProvider
from src.providers.manager import ProviderManager, get_provider_manager, reset_provider_manager
from src.providers.sqlite_provider import SqliteCacheProvider
from src.providers.vnstock_provider import VnstockProvider

__all__ = [
    "FinancialProvider",
    "ProviderManager",
    "SqliteCacheProvider",
    "VnstockProvider",
    "get_provider_manager",
    "reset_provider_manager",
]
