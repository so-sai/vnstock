"""item_mapping.py — Canonical financial item key mapping across providers.

VCI and KBS expose the same economics under different `item_id` labels
(e.g. VCI `net_sales` == KBS `revenue`). This module is the alias
dictionary that lets the `@cross_validate` flow normalize both sources
to a single canonical key space so statements can be compared metric
by metric.

Verified against live data (PNJ):
  * VCI income_statement: `net_sales`, `cost_of_sales`, `gross_profit`,
    `net_profit_loss_after_tax`, `eps_basic_vnd`, ...
  * KBS income_statement: `revenue`, `cost_of_goods_sold`, `gross_profit`,
    `net_profit`, `earnings_per_share_vnd`, ...
  * VCI balance_sheet: `total_assets`, `total_resource`, ...
  * KBS balance_sheet: EMPTY for PNJ → BS is NOT cross-validated.
  * VCI cashflow: `net_cash_inflows_outflows_from_operating_activities`
  * KBS cashflow: `operating_cash_flow`, `investing_cash_flow`, ...

TCBS is intentionally absent: the current adapter returns no data for
it and vnstock's tcbs explorer does not emit `item_id` in this schema.
"""

from typing import Dict, List

VCI = "VCI"
KBS = "KBS"
TCBS = "TCBS"

# ── Canonical key → per-source item_id aliases ─────────────────────────
# Canonical keys mirror the identifiers used in financial_facts.db.
ITEM_ALIASES: Dict[str, Dict[str, List[str]]] = {
    # Income statement
    "NET_REVENUE": {VCI: ["net_sales"], KBS: ["revenue"]},
    "COGS": {VCI: ["cost_of_sales"], KBS: ["cost_of_goods_sold"]},
    "GROSS_PROFIT": {VCI: ["gross_profit"], KBS: ["gross_profit"]},
    "OPERATING_PROFIT": {
        VCI: ["operating_profit_loss"],
        KBS: ["operating_profit"],
    },
    "PROFIT_BEFORE_TAX": {
        VCI: ["net_accounting_profit_loss_before_tax"],
        KBS: ["profit_before_tax"],
    },
    "NET_PROFIT": {
        VCI: ["net_profit_loss_after_tax"],
        KBS: ["net_profit"],
    },
    "SELLING_EXPENSES": {
        VCI: ["selling_expenses"],
        KBS: ["selling_expenses"],
    },
    "ADMIN_EXPENSES": {
        VCI: ["general_and_admin_expenses"],
        KBS: ["admin_expenses"],
    },
    "FINANCE_EXPENSES": {
        VCI: ["financial_expenses"],
        KBS: ["finance_expenses"],
    },
    "INTEREST_EXPENSES": {
        VCI: ["interest_expenses"],
        KBS: ["of_which_interest_expense"],
    },
    "EPS": {
        VCI: ["eps_basic_vnd"],
        KBS: ["earnings_per_share_vnd", "eps"],
    },
    # Balance sheet
    "TOTAL_ASSETS": {
        VCI: ["total_assets"],
        KBS: ["total_assets"],
    },
    "TOTAL_LIABILITIES": {
        VCI: ["liabilities"],
        KBS: ["total_liabilities"],
    },
    "EQUITY": {
        VCI: ["owners_equity"],
        KBS: ["equity"],
    },
    # Cash flow
    "CFO": {
        VCI: ["net_cash_inflows_outflows_from_operating_activities"],
        KBS: ["operating_cash_flow"],
    },
    "CFI": {
        VCI: ["net_cash_inflows_outflows_from_investing_activities"],
        KBS: ["investing_cash_flow"],
    },
    "CFF": {
        VCI: ["net_cash_inflows_outflows_from_financing_activities"],
        KBS: ["financing_cash_flow"],
    },
    "NET_CHANGE_IN_CASH": {
        VCI: ["net_increase_in_cash_and_cash_equivalents"],
        KBS: ["net_cash_flows_during_the_period", "net_change_in_cash"],
    },
    "CASH_END": {
        VCI: ["cash_and_cash_equivalents_at_the_end_of_period"],
        KBS: ["cash_and_cash_equivalents_at_end_of_the_period", "ending_cash"],
    },
}

# ── Reverse index: (source, item_id) → canonical key ───────────────────
_REVERSE: Dict[str, Dict[str, str]] = {}
for _canonical, _source_map in ITEM_ALIASES.items():
    for _source, _ids in _source_map.items():
        _by_source = _REVERSE.setdefault(_source, {})
        for _item_id in _ids:
            _by_source[_item_id] = _canonical


def canonical_key(source: str, item_id: str) -> str:
    """Map a provider's item_id to the canonical key (identity fallback)."""
    return _REVERSE.get(source, {}).get(item_id, item_id)


def canonical_keys(source: str) -> Dict[str, str]:
    """All item_id → canonical mappings known for a source."""
    return dict(_REVERSE.get(source, {}))


def canonical_metrics() -> List[str]:
    """Sorted list of every canonical metric name."""
    return sorted(ITEM_ALIASES.keys())
