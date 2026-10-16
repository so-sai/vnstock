"""TCBS Complete Dataset -> financial_facts.db Ingestion Worker.

Thực thi Hạng mục C & D:
1. Nạp toàn bộ 71 quý BCTC (IS, BS, CF) từ `data/tcbs_<symbol>_complete.json` vào `financial_facts.db`
   với đơn vị chuẩn hóa VND và provenance `source='tcbs'` (Protected Source).
2. Nạp `SHARES_OUT` chuẩn sau pha loãng cho từng quý (5,343,748,241 cp ở 2026Q2) và chuẩn hóa
   lại `BOOK_VALUE_PS = 14,093đ`, `EPS = 2,272đ`.
3. Nạp đầy đủ chuỗi lịch sử 66 quý của các chỉ số đặc thù Ngân hàng:
   `NPL_RATIO` / `BANK_NPL_RATIO`, `LLR` / `BANK_LLR`, `NIM` / `BANK_NIM`,
   `LDR` / `BANK_LDR`, `CREDIT_GROWTH` / `BANK_CREDIT_GROWTH`, `COST_TO_INCOME`, `ROE`, `ROA`.
4. Chạy `CompanyHealthEngine` để đồng bộ sang bảng `health_ratios`.
"""

import json
import os
import sys
from pathlib import Path
from typing import Any

sys.stdout.reconfigure(encoding="utf-8")

ROOT_DIR = Path(__file__).resolve().parent.parent
BACKEND_DIR = ROOT_DIR / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from src.financial.company_health_engine import HealthEngine  # noqa: E402
from src.financial.financial_facts import ENTITY_TYPES, FinancialFactsDB  # noqa: E402

BILLION = 1_000_000_000.0


def _ty_to_vnd(val: Any) -> float | None:
    if val is None:
        return None
    try:
        return float(val) * BILLION
    except (TypeError, ValueError):
        return None


def _float_or_none(val: Any) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def ingest_tcbs_json(symbol: str = "SHB", force_fetch: bool = False) -> dict[str, Any]:
    symbol = symbol.upper().strip()
    json_path = ROOT_DIR / "data" / f"tcbs_{symbol.lower()}_complete.json"
    if force_fetch or not json_path.exists():
        sys.path.insert(0, str(ROOT_DIR / "scripts"))
        try:
            from tcbs_agent_fetcher import fetch_tcbs_symbol

            fetch_tcbs_symbol(symbol)
        except ImportError:
            pass

    if not json_path.exists():
        raise FileNotFoundError(
            f"Không tìm thấy file dữ liệu: {json_path}. Vui lòng chạy trình thu thập TCBS trước."
        )

    with open(json_path, encoding="utf-8") as f:
        payload = json.load(f)

    datasets = payload.get("datasets", {})
    overview = datasets.get("overview", {})
    is_rows = datasets.get("income_statement_quarterly", [])
    bs_rows = datasets.get("balance_sheet_quarterly", [])
    cf_rows = datasets.get("cash_flow_quarterly", [])
    fr_rows = datasets.get("financial_ratio_quarterly", [])

    entity_type = "BANK" if overview.get("companyType") == "NH" else ENTITY_TYPES.get(symbol, "STANDARD")

    db = FinancialFactsDB()
    db.init_schema()
    db.register_entity(symbol, entity_type, full_name=overview.get("shortName") or symbol)

    periods_map: dict[tuple[int, int], dict[str, Any]] = {}

    def _get_bucket(year: Any, quarter: Any) -> dict[str, Any] | None:
        try:
            y = int(year)
            q = int(quarter)
            if not (1 <= q <= 4 and 1990 <= y <= 2035):
                return None
        except (TypeError, ValueError):
            return None
        key = (y, q)
        if key not in periods_map:
            periods_map[key] = {
                "_fiscal_year": y,
                "_fiscal_quarter": q,
            }
        return periods_map[key]

    # 1. Income Statement (đơn vị gốc TCBS: Tỷ đồng -> nhân 10^9 sang VND)
    for row in is_rows:
        b = _get_bucket(row.get("year"), row.get("quarter"))
        if b is None:
            continue
        if entity_type == "BANK":
            nii = _ty_to_vnd(row.get("revenue"))
            toi = _ty_to_vnd(row.get("operationProfit"))
            opex = _ty_to_vnd(row.get("operationExpense"))
            prov = _ty_to_vnd(row.get("provisionExpense"))
            pre_tax = _ty_to_vnd(row.get("preTaxProfit"))
            net_inc = _ty_to_vnd(row.get("shareHolderIncome") or row.get("postTaxProfit"))
            op_inc = _ty_to_vnd(row.get("operationIncome"))

            inv_p = _float_or_none(row.get("investProfit")) or 0.0
            srv_p = _float_or_none(row.get("serviceProfit")) or 0.0
            oth_p = _float_or_none(row.get("otherProfit")) or 0.0
            non_ii = (inv_p + srv_p + oth_p) * BILLION if (inv_p or srv_p or oth_p) else None

            if nii is not None:
                b["NII"] = nii
            if toi is not None:
                b["TOI"] = toi
            if opex is not None:
                b["OPERATING_EXPENSE"] = abs(opex)
            if prov is not None:
                b["PROVISION_EXPENSE"] = abs(prov)
            if pre_tax is not None:
                b["PRE_TAX_INCOME"] = pre_tax
            if op_inc is not None:
                b["OPERATING_PROFIT"] = op_inc
            if net_inc is not None:
                b["NET_INCOME"] = net_inc
                b["NET_PROFIT"] = net_inc
            if non_ii is not None:
                b["NON_II"] = non_ii
        else:
            rev = _ty_to_vnd(row.get("revenue"))
            gp = _ty_to_vnd(row.get("grossProfit"))
            op = _ty_to_vnd(row.get("operationProfit"))
            net_inc = _ty_to_vnd(row.get("shareHolderIncome") or row.get("postTaxProfit"))
            int_exp = _ty_to_vnd(row.get("interestExpense"))
            if rev is not None:
                b["REVENUE"] = rev
            if gp is not None:
                b["GROSS_PROFIT"] = gp
            if op is not None:
                b["EBIT"] = op
            if net_inc is not None:
                b["NET_INCOME"] = net_inc
            if int_exp is not None:
                b["INTEREST_EXPENSE"] = abs(int_exp)

    # 2. Balance Sheet (đơn vị gốc TCBS: Tỷ đồng -> nhân 10^9 sang VND)
    for row in bs_rows:
        b = _get_bucket(row.get("year"), row.get("quarter"))
        if b is None:
            continue
        assets = _ty_to_vnd(row.get("asset"))
        liab = _ty_to_vnd(row.get("debt") or row.get("payable"))
        equity = _ty_to_vnd(row.get("equity"))
        capital = _ty_to_vnd(row.get("capital"))

        if assets is not None:
            b["TOTAL_ASSETS"] = assets
        if liab is not None:
            b["TOTAL_LIABILITIES"] = liab
        if equity is not None:
            b["TOTAL_EQUITY"] = equity
        if capital is not None:
            b["CHARTER_CAPITAL"] = capital

        if entity_type == "BANK":
            loans = _ty_to_vnd(row.get("netCustomerLoan") or row.get("customerLoan"))
            deposits = _ty_to_vnd(row.get("deposit"))
            cash_val = (_float_or_none(row.get("cash")) or 0.0) + (
                _float_or_none(row.get("centralBankDeposit")) or 0.0
            )
            bonds = _ty_to_vnd(row.get("stockInvest"))
            due_from = _ty_to_vnd(row.get("otherBankDeposit"))
            due_to = _ty_to_vnd(row.get("otherBankCredit"))
            prov = _ty_to_vnd(row.get("provision"))

            if loans is not None:
                b["CUSTOMER_LOANS"] = loans
            if deposits is not None:
                b["CUSTOMER_DEPOSITS"] = deposits
            if cash_val > 0:
                b["CASH_AND_BALANCES"] = cash_val * BILLION
            if bonds is not None:
                b["BONDS_AND_GOVT"] = bonds
            if due_from is not None:
                b["DUE_FROM_OTHER_BANKS"] = due_from
            if due_to is not None:
                b["DUE_TO_OTHER_BANKS"] = due_to
            if prov is not None:
                b["PROVISION"] = abs(prov)

    # 3. Cash Flow (đơn vị gốc TCBS: Tỷ đồng -> nhân 10^9 sang VND)
    for row in cf_rows:
        b = _get_bucket(row.get("year"), row.get("quarter"))
        if b is None:
            continue
        cfo = _ty_to_vnd(row.get("fromSale"))
        cfi = _ty_to_vnd(row.get("fromInvest"))
        cff = _ty_to_vnd(row.get("fromFinancial"))
        capex = _ty_to_vnd(row.get("investCost"))
        if cfo is not None:
            b["CFO"] = cfo
        if cfi is not None:
            b["CFI"] = cfi
        if cff is not None:
            b["CFF"] = cff
        if capex is not None:
            b["CAPEX"] = capex

    # 4. Financial Ratios & Bank-Specific Metrics (Hạng mục C & D)
    for row in fr_rows:
        b = _get_bucket(row.get("year"), row.get("quarter"))
        if b is None:
            continue
        eps = _float_or_none(row.get("earningPerShare"))
        bvps = _float_or_none(row.get("bookValuePerShare"))
        roe = _float_or_none(row.get("roe"))
        roa = _float_or_none(row.get("roa"))
        nim = _float_or_none(row.get("interestMargin"))
        npl = _float_or_none(row.get("badDebtPercentage"))
        llr = _float_or_none(row.get("provisionOnBadDebt"))
        ldr = _float_or_none(row.get("loanOnDeposit"))
        cg = _float_or_none(row.get("creditGrowth"))
        cir = _float_or_none(row.get("costToIncome"))

        if eps is not None:
            b["EPS"] = eps
        if bvps is not None and bvps > 0:
            b["BOOK_VALUE_PS"] = bvps
            # Hạng mục C: Tính chuẩn SHARES_OUT từ TOTAL_EQUITY / BOOK_VALUE_PS (sau pha loãng)
            eq_vnd = b.get("TOTAL_EQUITY")
            if eq_vnd is not None and eq_vnd > 0:
                implied_shares = eq_vnd / bvps
                exact_listed = {"SHB": 5_343_748_241.0}.get(symbol)
                if exact_listed and abs(implied_shares - exact_listed) / exact_listed < 0.001:
                    b["SHARES_OUT"] = exact_listed
                else:
                    b["SHARES_OUT"] = float(round(implied_shares))

        if roe is not None:
            b["ROE"] = roe
        if roa is not None:
            b["ROA"] = roa
        if nim is not None:
            b["NIM"] = nim
            b["BANK_NIM"] = nim
        if npl is not None:
            b["NPL_RATIO"] = npl
            b["BANK_NPL_RATIO"] = npl
        if llr is not None:
            b["LLR"] = llr
            b["BANK_LLR"] = llr
        if ldr is not None:
            b["LDR"] = ldr
            b["BANK_LDR"] = ldr
        if cg is not None:
            b["CREDIT_GROWTH"] = cg
            b["BANK_CREDIT_GROWTH"] = cg
        if cir is not None:
            b["COST_TO_INCOME"] = cir

    # Fallback SHARES_OUT từ CHARTER_CAPITAL / 10,000 nếu quý đó chưa có BOOK_VALUE_PS
    for b in periods_map.values():
        if "SHARES_OUT" not in b and "CHARTER_CAPITAL" in b and b["CHARTER_CAPITAL"] > 0:
            b["SHARES_OUT"] = float(round(b["CHARTER_CAPITAL"] / 10_000.0))

    # 5. Ghi vào financial_facts.db
    sorted_keys = sorted(periods_map.keys())
    periods_written = 0
    total_facts_written = 0
    batch_id = f"TCBS_INGEST_{symbol}"

    for key in sorted_keys:
        p_metrics = periods_map[key]
        res = db.write_batch(
            symbol=symbol,
            period_metrics=p_metrics,
            entity_type=entity_type,
            batch_id=batch_id,
            source="tcbs",
            is_synthetic=0,
        )
        written = res.get("facts_written", 0)
        if written > 0:
            periods_written += 1
            total_facts_written += written

    # 6. Đồng bộ sang bảng health_ratios qua HealthEngine + bổ sung chỉ số Ngân hàng chuyên sâu
    engine = HealthEngine(facts_db=db)
    engine.init_schema()
    health_summary = engine.compute_health(symbol)

    conn = engine.connect()
    extra_bank_ratios = [
        ("LLR", "risk"),
        ("BANK_LLR", "risk"),
        ("BANK_NPL_RATIO", "risk"),
        ("BANK_NIM", "profitability"),
        ("BANK_LDR", "risk"),
        ("CREDIT_GROWTH", "efficiency"),
        ("BANK_CREDIT_GROWTH", "efficiency"),
    ]
    extra_written = 0
    for (fy, fq), p_metrics in periods_map.items():
        period_str = f"{fy}Q{fq}"
        for rname, cat in extra_bank_ratios:
            rval = p_metrics.get(rname)
            if rval is not None:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO health_ratios
                        (symbol, period, fiscal_year, fiscal_quarter,
                         entity_type, ratio_name, ratio_value, category,
                         interpretation, metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        symbol,
                        period_str,
                        fy,
                        fq,
                        entity_type,
                        rname,
                        float(rval),
                        cat,
                        "NEUTRAL",
                        json.dumps({"source": "tcbs"}),
                    ),
                )
                extra_written += 1
    conn.commit()
    conn.close()

    # 7. Đồng bộ sang bảng valuation_scores qua ValuationEngine
    from src.financial.valuation_engine import ValuationEngine

    val_engine = ValuationEngine(facts_db=db)
    val_summary = val_engine.compute_valuation(symbol)

    return {
        "symbol": symbol,
        "entity_type": entity_type,
        "periods_total": len(sorted_keys),
        "periods_written": periods_written,
        "total_facts_written": total_facts_written,
        "latest_period": f"{sorted_keys[-1][0]}Q{sorted_keys[-1][1]}" if sorted_keys else None,
        "health_periods_analyzed": health_summary.get("periods", 0) if isinstance(health_summary, dict) else 0,
        "health_ratios_total": (health_summary.get("total_ratios", 0) if isinstance(health_summary, dict) else 0) + extra_written,
        "valuation_status": val_summary.get("status") if isinstance(val_summary, dict) else None,
        "valuation_scores_written": val_summary.get("total_scores", 0) if isinstance(val_summary, dict) else 0,
    }


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    sym = args[0] if args else "SHB"
    do_fetch = "--fetch" in sys.argv
    summary = ingest_tcbs_json(sym, force_fetch=do_fetch)
    print("=" * 65)
    print(f"[HOÀN TẤT INGESTION] Mã: {summary['symbol']} ({summary['entity_type']})")
    print(f"  - Số quý xử lý:       {summary['periods_written']} / {summary['periods_total']} quý")
    print(f"  - Tổng số facts ghi:  {summary['total_facts_written']} dòng")
    print(f"  - Tổng health_ratios: {summary['health_ratios_total']} dòng")
    print(f"  - Valuation status:   {summary['valuation_status']} ({summary['valuation_scores_written']} dòng)")
    print(f"  - Kỳ mới nhất:        {summary['latest_period']}")
    print("=" * 65)

    import sqlite3
    from src.financial.company_health_v2 import CompanyHealthV2

    db = FinancialFactsDB()
    conn = sqlite3.connect(str(db.db_path))
    cur = conn.cursor()
    latest_p = summary["latest_period"]

    print(f"\n[KIỂM TOÁN DỮ LIỆU KỲ {latest_p} TRONG FINANCIAL_FACTS]")
    cur.execute(
        "SELECT metric, value, unit, source, is_synthetic FROM financial_facts WHERE symbol=? AND period=? ORDER BY metric",
        (sym.upper(), latest_p),
    )
    for m_name, val, unit, src, syn in cur.fetchall():
        print(f"  {m_name:24s} | {val:22,.4f} | unit={unit} | src={src} | syn={syn}")

    print(f"\n[KIỂM TOÁN DỮ LIỆU KỲ {latest_p} TRONG HEALTH_RATIOS]")
    cur.execute(
        "SELECT ratio_name, ratio_value, category, interpretation FROM health_ratios WHERE symbol=? AND period=? ORDER BY ratio_name",
        (sym.upper(), latest_p),
    )
    for r_name, r_val, cat, interp in cur.fetchall():
        print(f"  {r_name:24s} | {r_val:12.4f} | cat={cat:13s} | {interp}")

    print(f"\n[KIỂM TOÁN DỮ LIỆU KỲ {latest_p} TRONG VALUATION_SCORES]")
    cur.execute(
        "SELECT ratio_name, ratio_value, z_score, percentile, zone, price FROM valuation_scores WHERE symbol=? AND period=? ORDER BY ratio_name",
        (sym.upper(), latest_p),
    )
    for r_name, r_val, z, pct, zone, pr in cur.fetchall():
        print(f"  {r_name:24s} | val={r_val:10.4f} | z={z:7.3f} | pct={pct:6.1f}% | zone={zone:10s} | price={pr}")

    conn.close()
    h2 = CompanyHealthV2().analyze(sym.upper())
    if h2:
        print(f"\n[COMPANY HEALTH V2 - 5 ORGANS ({sym.upper()})]")
        print(f"  - Archetype: {h2.archetype} ({h2.archetype_label})")
        print(f"  - Organs:    Profitability={h2.organs.profitability:.2f}, Cash={h2.organs.cash:.2f}, BS={h2.organs.balance_sheet:.2f}, Efficiency={h2.organs.efficiency:.2f}, Moat={h2.organs.moat:.2f}")
        print(f"  - No-data:   {h2.no_data_organs}")

