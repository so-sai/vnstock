"""company_health_engine.py — GIAI ĐOẠN 2: Company Health Engine

Tầng 2 của Kiến trúc 4 Lớp Bất biến.
Đọc Raw Facts từ financial_facts.db, tính toán bộ chỉ số sức khỏe,
phát hiện xu hướng suy giảm, lưu vào health_ratios table.
"""

import sqlite3
import json
import sys
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_candidate = Path(sys.executable).resolve().parent
if Path(sys.executable).stem.lower().startswith("python"):
    _p = Path(__file__).resolve().parent.parent.parent.parent
    for _par in [_p] + list(_p.parents):
        if (_par / "AGENTS.md").exists() and (_par / "backend").is_dir():
            _candidate = _par
            break
PROJECT_ROOT = _candidate
BACKEND_DIR = PROJECT_ROOT / "backend"
DATA_DIR = BACKEND_DIR / "data"
sys.path.insert(0, str(BACKEND_DIR))

from src.financial.financial_facts import FinancialFactsDB, FINANCIAL_DB_PATH

# =========================================================================
# RATIO REGISTRY
# =========================================================================

RATIO_META = {
    # --- Cash Flow Quality ---
    "CFO_TO_NET_INCOME": {
        "name": "Chất lượng dòng tiền",
        "category": "cash_flow",
        "formula": "CFO / Net Income",
        "better": ">1.0 (dòng tiền > lợi nhuận)",
        "thresholds": {"GOOD": 1.0, "WARNING": 0.5, "BAD": 0.0},
        "entity_types": ["STANDARD", "BANK"],
    },
    "FCF_TO_NET_INCOME": {
        "name": "Dòng tiền tự do trên LNST",
        "category": "cash_flow",
        "formula": "FCF / Net Income",
        "better": ">0.5",
        "thresholds": {"GOOD": 0.5, "WARNING": 0.0, "BAD": -0.5},
        "entity_types": ["STANDARD"],
    },
    "CAPEX_TO_CFO": {
        "name": "Chi đầu tư trên dòng tiền HĐKD",
        "category": "cash_flow",
        "formula": "CapEx / CFO",
        "better": "<0.5 (ít phải đầu tư để duy trì)",
        "inverted": True,
        "thresholds": {"GOOD": 0.3, "WARNING": 0.6, "BAD": 1.0},
        "entity_types": ["STANDARD"],
    },
    # --- Profitability ---
    "NET_MARGIN": {
        "name": "Biên lợi nhuận ròng",
        "category": "profitability",
        "formula": "Net Income / Revenue",
        "better": "càng cao càng tốt",
        "thresholds": {"GOOD": 0.15, "WARNING": 0.05, "BAD": 0.0},
        "entity_types": ["STANDARD"],
    },
    "GROSS_MARGIN": {
        "name": "Biên lợi nhuận gộp",
        "category": "profitability",
        "formula": "Gross Profit / Revenue",
        "better": "càng cao càng tốt",
        "thresholds": {"GOOD": 0.35, "WARNING": 0.15, "BAD": 0.0},
        "entity_types": ["STANDARD"],
    },
    "ROE": {
        "name": "ROE",
        "category": "profitability",
        "formula": "Net Income / Equity",
        "better": ">12% (vn stock)",
        "thresholds": {"GOOD": 0.12, "WARNING": 0.05, "BAD": 0.0},
        "entity_types": ["STANDARD", "BANK"],
    },
    "ROA": {
        "name": "ROA",
        "category": "profitability",
        "formula": "Net Income / Total Assets",
        "better": ">3%",
        "thresholds": {"GOOD": 0.03, "WARNING": 0.01, "BAD": 0.0},
        "entity_types": ["STANDARD", "BANK"],
    },
    # --- Liquidity ---
    "CURRENT_RATIO": {
        "name": "Hệ số thanh toán hiện hành",
        "category": "liquidity",
        "formula": "Current Assets / Current Liab",
        "better": "1.5-3.0",
        "thresholds": {"GOOD": 1.5, "WARNING": 1.0, "BAD": 0.5},
        "entity_types": ["STANDARD"],
    },
    "QUICK_RATIO": {
        "name": "Hệ số thanh toán nhanh",
        "category": "liquidity",
        "formula": "(Current Assets - Inventory) / Current Liab",
        "better": ">1.0",
        "thresholds": {"GOOD": 1.0, "WARNING": 0.5, "BAD": 0.0},
        "entity_types": ["STANDARD"],
    },
    "CASH_RATIO": {
        "name": "Hệ số tiền mặt",
        "category": "liquidity",
        "formula": "Cash / Current Liab",
        "better": ">0.3",
        "thresholds": {"GOOD": 0.3, "WARNING": 0.1, "BAD": 0.0},
        "entity_types": ["STANDARD"],
    },
    # --- Leverage ---
    "DEBT_TO_EQUITY": {
        "name": "Nợ vay trên VCSH",
        "category": "leverage",
        "formula": "Total Debt / Equity",
        "better": "<1.0 (tùy ngành)",
        "inverted": True,
        "thresholds": {"GOOD": 0.5, "WARNING": 1.0, "BAD": 2.0},
        "entity_types": ["STANDARD"],
    },
    "DEBT_TO_ASSETS": {
        "name": "Nợ vay trên tổng TS",
        "category": "leverage",
        "formula": "Total Debt / Total Assets",
        "better": "<0.5",
        "inverted": True,
        "thresholds": {"GOOD": 0.3, "WARNING": 0.5, "BAD": 0.7},
        "entity_types": ["STANDARD"],
    },
    "INTEREST_COVERAGE": {
        "name": "Hệ số chi trả lãi vay",
        "category": "leverage",
        "formula": "EBIT / Interest Expense",
        "better": ">3.0",
        "thresholds": {"GOOD": 3.0, "WARNING": 1.5, "BAD": 1.0},
        "entity_types": ["STANDARD"],
    },
    # --- Efficiency / Accounting Risk ---
    "RECEIVABLES_TO_REVENUE": {
        "name": "Phải thu trên doanh thu",
        "category": "risk",
        "formula": "Receivables / Revenue",
        "better": "xu hướng giảm",
        "inverted": True,
        "thresholds": {"GOOD": 0.3, "WARNING": 0.5, "BAD": 0.8},
        "entity_types": ["STANDARD"],
    },
    "INVENTORY_TO_REVENUE": {
        "name": "Tồn kho trên doanh thu",
        "category": "risk",
        "formula": "Inventory / Revenue",
        "better": "xu hướng giảm",
        "inverted": True,
        "thresholds": {"GOOD": 0.15, "WARNING": 0.3, "BAD": 0.5},
        "entity_types": ["STANDARD"],
    },
    "ASSET_TURNOVER": {
        "name": "Vòng quay tổng tài sản",
        "category": "efficiency",
        "formula": "Revenue / Total Assets",
        "better": "càng cao càng tốt",
        "thresholds": {"GOOD": 1.0, "WARNING": 0.5, "BAD": 0.2},
        "entity_types": ["STANDARD"],
    },
    # --- BANK-specific ---
    "NIM": {
        "name": "Tỷ lệ thu nhập lãi thuần",
        "category": "profitability",
        "formula": "NII / Customer Loans",
        "better": ">2.5%",
        "thresholds": {"GOOD": 0.025, "WARNING": 0.015, "BAD": 0.01},
        "entity_types": ["BANK"],
    },
    "PROVISION_TO_NET_PROFIT": {
        "name": "Chi phí dự phòng trên LNST",
        "category": "risk",
        "formula": "Provision Expense / Net Profit",
        "better": "<20%",
        "inverted": True,
        "thresholds": {"GOOD": 0.15, "WARNING": 0.25, "BAD": 0.40},
        "entity_types": ["BANK"],
    },
    "LDR": {
        "name": "Tỷ lệ cho vay trên tiền gửi",
        "category": "risk",
        "formula": "Customer Loans / Customer Deposits",
        "better": "0.7-0.9",
        "thresholds": {"GOOD": 0.8, "WARNING": 0.9, "BAD": 1.0},
        "entity_types": ["BANK"],
    },
    "CASA_RATIO": {
        "name": "Tỷ lệ CASA",
        "category": "efficiency",
        "formula": "CASA Ratio (raw)",
        "better": "càng cao càng tốt",
        "thresholds": {"GOOD": 0.35, "WARNING": 0.20, "BAD": 0.10},
        "entity_types": ["BANK"],
    },
    "NPL_RATIO": {
        "name": "Tỷ lệ nợ xấu",
        "category": "risk",
        "formula": "NPL Ratio (raw)",
        "better": "<2%",
        "inverted": True,
        "thresholds": {"GOOD": 0.015, "WARNING": 0.025, "BAD": 0.035},
        "entity_types": ["BANK"],
    },
    "CAPITAL_RATIO": {
        "name": "Tỷ lệ an toàn vốn (proxy)",
        "category": "risk",
        "formula": "Equity / Total Assets",
        "better": ">8%",
        "thresholds": {"GOOD": 0.10, "WARNING": 0.07, "BAD": 0.05},
        "entity_types": ["BANK"],
    },
    "COST_TO_INCOME": {
        "name": "Chi phí trên thu nhập",
        "category": "efficiency",
        "formula": "Operating Exp / TOI",
        "better": "<50%",
        "inverted": True,
        "thresholds": {"GOOD": 0.35, "WARNING": 0.45, "BAD": 0.55},
        "entity_types": ["BANK"],
    },
}


# =========================================================================
# HEALTH ENGINE
# =========================================================================

class HealthEngine:
    """Tính toán chỉ số sức khỏe tài chính từ Raw Facts."""

    def __init__(self, facts_db: FinancialFactsDB = None):
        self.facts_db = facts_db or FinancialFactsDB()
        self.db_path = self.facts_db.db_path

    def connect(self):
        return sqlite3.connect(str(self.db_path))

    def init_schema(self):
        conn = self.connect()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS health_ratios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                period TEXT NOT NULL,
                fiscal_year INTEGER,
                fiscal_quarter INTEGER,
                entity_type TEXT NOT NULL,
                ratio_name TEXT NOT NULL,
                ratio_value REAL,
                category TEXT,
                interpretation TEXT,
                metadata TEXT,
                UNIQUE(symbol, period, ratio_name)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_health_symbol ON health_ratios(symbol, period)
        """)
        conn.commit()
        conn.close()
        print(f"  Schema OK: health_ratios table")

    def _safe_div(self, a, b):
        if b is None or b == 0:
            return None
        try:
            return a / b
        except (ZeroDivisionError, TypeError):
            return None

    def _interpret(self, ratio_name: str, value: float) -> str:
        meta = RATIO_META.get(ratio_name)
        if not meta or value is None:
            return "NEUTRAL"
        th = meta["thresholds"]
        inverted = meta.get("inverted", False)
        if inverted:
            if value <= th["GOOD"]:
                return "GOOD"
            elif value <= th["WARNING"]:
                return "WARNING"
            elif value <= th["BAD"]:
                return "WARNING"
            else:
                return "BAD"
        else:
            if value >= th["GOOD"]:
                return "GOOD"
            elif value >= th["WARNING"]:
                return "WARNING"
            elif value >= th["BAD"]:
                return "WARNING"
            else:
                return "BAD"

    def compute_standard_ratios(self, period_metrics: dict) -> Dict[str, float]:
        r = {}
        # Cash Flow Quality
        ni = period_metrics.get("NET_INCOME")
        cfo = period_metrics.get("CFO")
        fcf = period_metrics.get("FCF")
        capex = period_metrics.get("CAPEX")
        rev = period_metrics.get("REVENUE")
        gp = period_metrics.get("GROSS_PROFIT")
        assets = period_metrics.get("TOTAL_ASSETS")
        equity = period_metrics.get("TOTAL_EQUITY")
        debt = period_metrics.get("TOTAL_DEBT")
        if debt is None:
            # WHY: CaféF Bank API không tách nợ vay ngắn/dài hạn cho doanh nghiệp,
            # chỉ trả "Tổng nợ" (TOTAL_LIABILITIES). Dùng tổng nợ phải trả làm
            # proxy cho đòn bẩy khi không có TOTAL_DEBT → giải phóng DEBT_TO_EQUITY.
            debt = period_metrics.get("TOTAL_LIABILITIES")
        interest = period_metrics.get("INTEREST_EXPENSE")
        ca = period_metrics.get("CURRENT_ASSETS")
        cl = period_metrics.get("CURRENT_LIAB")
        inv = period_metrics.get("INVENTORY")
        cash = period_metrics.get("CASH_EQUIV")
        rec = period_metrics.get("RECEIVABLES")

        r["CFO_TO_NET_INCOME"] = self._safe_div(cfo, ni)
        r["FCF_TO_NET_INCOME"] = self._safe_div(fcf, ni)
        r["CAPEX_TO_CFO"] = self._safe_div(capex, cfo)
        r["NET_MARGIN"] = self._safe_div(ni, rev)
        r["GROSS_MARGIN"] = self._safe_div(gp, rev)
        r["ROE"] = self._safe_div(ni, equity)
        r["ROA"] = self._safe_div(ni, assets)
        r["CURRENT_RATIO"] = self._safe_div(ca, cl)
        if ca is not None and inv is not None and cl is not None:
            r["QUICK_RATIO"] = self._safe_div(ca - inv, cl)
        r["CASH_RATIO"] = self._safe_div(cash, cl)
        r["DEBT_TO_EQUITY"] = self._safe_div(debt, equity)
        r["DEBT_TO_ASSETS"] = self._safe_div(debt, assets)
        if interest and interest != 0:
            ebit = (period_metrics.get("EBITDA") or
                    (ni + interest if ni else None))
            if ebit is not None:
                r["INTEREST_COVERAGE"] = self._safe_div(ebit, interest)
        r["RECEIVABLES_TO_REVENUE"] = self._safe_div(rec, rev)
        r["INVENTORY_TO_REVENUE"] = self._safe_div(inv, rev)
        r["ASSET_TURNOVER"] = self._safe_div(rev, assets)
        return {k: v for k, v in r.items() if v is not None}

    def compute_bank_ratios(self, period_metrics: dict) -> Dict[str, float]:
        r = {}
        nii = period_metrics.get("NII")
        np_ = period_metrics.get("NET_PROFIT")
        prov = period_metrics.get("PROVISION_EXPENSE")
        loans = period_metrics.get("CUSTOMER_LOANS")
        deposits = period_metrics.get("CUSTOMER_DEPOSITS")
        assets = period_metrics.get("TOTAL_ASSETS")
        equity = period_metrics.get("TOTAL_EQUITY")
        cfo = period_metrics.get("CFO")
        npl = period_metrics.get("NPL_RATIO")
        casa = period_metrics.get("CASA_RATIO")
        toi = period_metrics.get("TOI")
        opex = period_metrics.get("OPERATING_EXPENSE")

        r["CFO_TO_NET_INCOME"] = self._safe_div(cfo, np_)
        r["ROE"] = self._safe_div(np_, equity)
        r["ROA"] = self._safe_div(np_, assets)
        r["NIM"] = self._safe_div(nii, loans)
        r["PROVISION_TO_NET_PROFIT"] = self._safe_div(prov, np_)
        r["LDR"] = self._safe_div(loans, deposits)
        r["CAPITAL_RATIO"] = self._safe_div(equity, assets)
        r["COST_TO_INCOME"] = self._safe_div(opex, toi)
        if npl is not None:
            r["NPL_RATIO"] = npl
        if casa is not None:
            r["CASA_RATIO"] = casa
        return {k: v for k, v in r.items() if v is not None}

    def compute_period_ratios(self, symbol: str, period: str,
                               period_metrics: dict, entity_type: str) -> Dict:
        if entity_type == "BANK":
            ratios = self.compute_bank_ratios(period_metrics)
        else:
            ratios = self.compute_standard_ratios(period_metrics)

        result = {}
        for rname, rval in ratios.items():
            meta = RATIO_META.get(rname)
            result[rname] = {
                "value": rval,
                "category": meta["category"] if meta else "other",
                "interpretation": self._interpret(rname, rval),
            }
        return result

    def compute_health(self, symbol: str) -> Dict:
        entity_type = self.facts_db.get_entity_type(symbol)
        facts = self.facts_db.get_facts(symbol)
        if not facts:
            return {"status": "NO_DATA", "symbol": symbol}

        conn = self.connect()
        results = []
        warnings = []

        for period in sorted(facts.keys(), reverse=True):
            pm = facts[period]
            fy = pm.get("_fiscal_year", int(period[:4]))
            fq = pm.get("_fiscal_quarter", int(period[5:6]))

            computed = self.compute_period_ratios(symbol, period, pm, entity_type)
            period_count = 0

            for rname, rinfo in computed.items():
                meta = RATIO_META.get(rname, {})
                metadata = {"thresholds": meta.get("thresholds", {})}
                conn.execute("""
                    INSERT OR REPLACE INTO health_ratios
                        (symbol, period, fiscal_year, fiscal_quarter,
                         entity_type, ratio_name, ratio_value, category,
                         interpretation, metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    symbol.upper(), period, fy, fq, entity_type.upper(),
                    rname, rinfo["value"], rinfo["category"],
                    rinfo["interpretation"], json.dumps(metadata),
                ))
                period_count += 1

            results.append({"period": period, "ratios": period_count})
            print(f"    {period}: {period_count} ratios computed")

        conn.commit()
        conn.close()

        return {
            "status": "DONE",
            "symbol": symbol.upper(),
            "entity_type": entity_type,
            "periods": len(results),
            "total_ratios": sum(r["ratios"] for r in results),
            "details": results,
        }

    def get_health_summary(self, symbol: str) -> Optional[Dict]:
        conn = self.connect()
        cur = conn.cursor()
        cur.execute("""
            SELECT period, ratio_name, ratio_value, category, interpretation
            FROM health_ratios
            WHERE symbol = ?
            ORDER BY period DESC, category, ratio_name
        """, (symbol.upper(),))
        rows = cur.fetchall()
        conn.close()
        if not rows:
            return None
        result = {"symbol": symbol.upper(), "ratios": []}
        for r in rows:
            result["ratios"].append({
                "period": r[0], "ratio": r[1], "value": r[2],
                "category": r[3], "interpretation": r[4],
            })
        return result

    def get_latest_health(self, symbol: str) -> Optional[Dict]:
        conn = self.connect()
        cur = conn.cursor()
        cur.execute("""
            SELECT period, ratio_name, ratio_value, category, interpretation
            FROM health_ratios
            WHERE symbol = ? AND period = (
                SELECT MAX(period) FROM health_ratios WHERE symbol = ?
            )
            ORDER BY category, ratio_name
        """, (symbol.upper(), symbol.upper()))
        rows = cur.fetchall()
        conn.close()
        if not rows:
            return None
        period = rows[0][0]
        result = {"symbol": symbol.upper(), "period": period, "ratios": {}}
        for r in rows:
            cat = r[3]
            if cat not in result["ratios"]:
                result["ratios"][cat] = []
            result["ratios"][cat].append({
                "ratio": r[1], "value": r[2], "interpretation": r[4],
            })
        return result

    def compare_symbols(self, symbols: List[str]) -> Dict:
        results = {}
        for sym in symbols:
            h = self.get_latest_health(sym)
            if h:
                results[sym] = h
        return results


# =========================================================================
# CLI ENTRY POINT
# =========================================================================

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Company Health Engine — PTCK_VN Phase 2")
    parser.add_argument("action", choices=["init", "compute", "show", "compare"],
                        help="Hành động")
    parser.add_argument("--symbols", nargs="+", default=["FPT", "ACB", "HDB", "MBB", "VCB"],
                        help="Danh sách symbol")
    parser.add_argument("--periods", type=int, default=8,
                        help="Số kỳ gần nhất")
    args = parser.parse_args()

    engine = HealthEngine()

    if args.action == "init":
        print("=== Khởi tạo health_ratios table ===")
        engine.init_schema()

    elif args.action == "compute":
        print(f"=== Compute Health: {', '.join(args.symbols)} ===")
        engine.init_schema()
        for sym in args.symbols:
            print(f"\n  [{sym}] Computing...")
            result = engine.compute_health(sym)
            print(f"  [{result['status']}] {result['symbol']}: "
                  f"{result['periods']} periods, {result['total_ratios']} ratios")

    elif args.action == "show":
        for sym in args.symbols:
            h = engine.get_latest_health(sym)
            if not h:
                print(f"\n  {sym}: NO DATA")
                continue
            print(f"\n  {'='*50}")
            print(f"  {sym} ({h['period']})")
            print(f"  {'='*50}")
            for cat, ratios in h["ratios"].items():
                print(f"\n  [{cat.upper()}]")
                for r in ratios:
                    ico = {"GOOD": "🟢", "WARNING": "🟡", "BAD": "🔴", "NEUTRAL": "⚪"}
                    val = r["value"]
                    if val is not None and abs(val) < 10:
                        vs = f"{val:.2%}"
                    elif val is not None:
                        vs = f"{val:.2f}x"
                    else:
                        vs = "N/A"
                    print(f"    {ico.get(r['interpretation'], '⚪')} {r['ratio']:25s} = {vs:>10s}  ({r['interpretation']})")

    elif args.action == "compare":
        print(f"\n  {'='*70}")
        print(f"  COMPARE: {', '.join(args.symbols)}")
        print(f"  {'='*70}")
        all_ratios = set()
        data = {}
        for sym in args.symbols:
            h = engine.get_latest_health(sym)
            if h:
                data[sym] = {}
                for cat, ratios in h["ratios"].items():
                    for r in ratios:
                        all_ratios.add(r["ratio"])
                        data[sym][r["ratio"]] = r

        all_ratios = sorted(all_ratios)
        header = f"  {'Ratio':30s}" + "".join(f" {s:>10s}" for s in args.symbols)
        print(f"\n  {header}")
        print(f"  {'-'*len(header)}")
        for rname in all_ratios:
            row = f"  {rname:30s}"
            for sym in args.symbols:
                if sym in data and rname in data[sym]:
                    val = data[sym][rname]["value"]
                    ico = {"GOOD": "🟢", "WARNING": "🟡", "BAD": "🔴", "NEUTRAL": "⚪"}
                    if val is not None and abs(val) < 10:
                        vs = f"{val:.2%}"
                    elif val is not None:
                        vs = f"{val:.2f}x"
                    else:
                        vs = "N/A"
                    row += f" {ico.get(data[sym][rname]['interpretation'], '⚪')}{vs:>9s}"
                else:
                    row += f" {'---':>11s}"
            print(row)


if __name__ == "__main__":
    main()
