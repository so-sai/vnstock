"""company_health_engine.py — GIAI ĐOẠN 2: Company Health Engine

Tầng 2 của Kiến trúc 4 Lớp Bất biến.
Đọc Raw Facts từ financial_facts.db, tính toán bộ chỉ số sức khỏe,
phát hiện xu hướng suy giảm, lưu vào health_ratios table.
"""

# WHY (thiết kế tổng thể):
# WHY: Registry RATIO_META là nguồn DUY NHẤT cho formula/threshold/entity_type →
#   mọi consumer (compute/interpret/UI/health-v2) đọc chung, tránh duplicate
#   công thức và threshold lệch nhau.
# WHY: Tách entity_type STANDARD/BANK: ngân hàng có cấu trúc BCTC riêng
#   (NIM/LDR/NPL/CASA thay cho Inventory/Gross Margin) → dùng chung threshold
#   sẽ cho kết luận sai.
# WHY: Ghi kết quả vào bảng health_ratios riêng (không đụng financial_facts):
#   ratio là dữ liệu DERIVED tái tính được, giữ tầng 1 thuần raw facts.

import json
import sqlite3
import sys
from pathlib import Path

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

from src.financial.financial_facts import FinancialFactsDB

# =========================================================================
# RATIO REGISTRY
# =========================================================================

# WHY: "inverted": True cho ratio mà THẤP hơn là TỐT hơn (CAPEX_TO_CFO,
# DEBT_TO_EQUITY, NPL_RATIO...) — _interpret() đảo chiều so sánh, giữ threshold
# GOOD < WARNING < BAD theo thứ tự tăng dần cho cả 2 loại.
RATIO_META = {
    # --- Cash Flow Quality ---
    "CFO_TO_NET_INCOME": {
        "name": "Chất lượng dòng tiền",
        "category": "cash_flow",
        "formula": "CFO / Net Income",
        "better": ">1.0 (dòng tiền > lợi nhuận)",
        "thresholds": {"GOOD": 1.0, "WARNING": 0.5, "BAD": 0.0},
        "entity_types": ["STANDARD", "BANK", "SECURITIES", "INSURANCE"],
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
        "entity_types": ["STANDARD", "BANK", "SECURITIES", "INSURANCE"],
    },
    "ROA": {
        "name": "ROA",
        "category": "profitability",
        "formula": "Net Income / Total Assets",
        "better": ">3%",
        "thresholds": {"GOOD": 0.03, "WARNING": 0.01, "BAD": 0.0},
        "entity_types": ["STANDARD", "BANK", "SECURITIES", "INSURANCE"],
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
    # --- SECURITIES-specific (CTCK) ---
    # WHY: Khung Thông tư 334/2016/TT-BTC. Tài sản CTCK chia 4 nhóm sinh lời:
    # FVTPL (tự doanh ngắn hạn), HTM (giữ đến đáo hạn), AFS (sẵn sàng để bán),
    # MARGIN_LOANS (cho vay ký quỹ). Trần pháp lý cho vay margin 2.0x equity.
    "MARGIN_TO_EQUITY": {
        "name": "Đòn bẩy Margin trên VCSH",
        "category": "leverage",
        "formula": "Margin Loans / Total Equity",
        "better": "<2.0 (trần pháp lý UBCKNN)",
        "inverted": True,
        "thresholds": {"GOOD": 1.0, "WARNING": 1.5, "BAD": 2.0},
        "entity_types": ["SECURITIES"],
    },
    "MARGIN_TO_ASSETS": {
        "name": "Dư nợ Margin trên tổng tài sản",
        "category": "risk",
        "formula": "Margin Loans / Total Assets",
        "better": "càng thấp càng tốt",
        "inverted": True,
        "thresholds": {"GOOD": 0.25, "WARNING": 0.35, "BAD": 0.45},
        "entity_types": ["SECURITIES"],
    },
    "FVTPL_TO_ASSETS": {
        "name": "Tỷ trọng tự doanh FVTPL",
        "category": "risk",
        "formula": "FVTPL / Total Assets",
        "better": "càng thấp càng tốt",
        "inverted": True,
        "thresholds": {"GOOD": 0.20, "WARNING": 0.30, "BAD": 0.40},
        "entity_types": ["SECURITIES"],
    },
    "HTM_TO_ASSETS": {
        "name": "Tỷ trọng tài sản thu nhập cố định",
        "category": "efficiency",
        "formula": "HTM / Total Assets",
        "better": "càng cao càng tốt (an toàn)",
        "thresholds": {"GOOD": 0.30, "WARNING": 0.15, "BAD": 0.05},
        "entity_types": ["SECURITIES"],
    },
    "AFS_TO_ASSETS": {
        "name": "Tỷ trọng tài sản sẵn sàng để bán",
        "category": "risk",
        "formula": "AFS / Total Assets",
        "better": "càng thấp càng tốt",
        "inverted": True,
        "thresholds": {"GOOD": 0.15, "WARNING": 0.25, "BAD": 0.35},
        "entity_types": ["SECURITIES"],
    },
    # --- INSURANCE-specific (DNBH) ---
    # WHY: Khung Thông tư 135/2012/TT-BTC. Lợi nhuận kỹ thuật bảo hiểm đến từ
    # phí thuần trừ bồi thường và chi phí — Combined Ratio < 100% mới có lãi nghiệp vụ.
    "LOSS_RATIO": {
        "name": "Tỷ lệ bồi thường thuần",
        "category": "profitability",
        "formula": "Net Claims / Net Premium",
        "better": "<70%",
        "inverted": True,
        "thresholds": {"GOOD": 0.60, "WARNING": 0.70, "BAD": 0.80},
        "entity_types": ["INSURANCE"],
    },
    "EXPENSE_RATIO": {
        "name": "Tỷ lệ chi phí khai thác",
        "category": "profitability",
        "formula": "Operating Expense / Net Premium",
        "better": "<30%",
        "inverted": True,
        "thresholds": {"GOOD": 0.20, "WARNING": 0.30, "BAD": 0.40},
        "entity_types": ["INSURANCE"],
    },
    "COMBINED_RATIO": {
        "name": "Tỷ lệ hợp nhất kỹ thuật",
        "category": "profitability",
        "formula": "Loss Ratio + Expense Ratio",
        "better": "<100% (lãi nghiệp vụ bảo hiểm)",
        "inverted": True,
        "thresholds": {"GOOD": 0.90, "WARNING": 1.00, "BAD": 1.10},
        "entity_types": ["INSURANCE"],
    },
    "TECH_RESERVE_TO_ASSETS": {
        "name": "Dự phòng nghiệp vụ trên tổng tài sản",
        "category": "risk",
        "formula": "Technical Reserves / Total Assets",
        "better": "đủ dự phòng cho nghĩa vụ",
        "thresholds": {"GOOD": 0.30, "WARNING": 0.15, "BAD": 0.05},
        "entity_types": ["INSURANCE"],
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
        print("  Schema OK: health_ratios table")

    def _safe_div(self, a, b):
        # WHY: trả None (không raise) khi chia 0/thiếu dữ liệu → ratio đó bị
        # loại khỏi kết quả (filter None cuối hàm), tránh ratio vô nghĩa
        # như INF hay số bịa từ mẫu số = 0.
        if b is None or b == 0:
            return None
        try:
            return a / b
        except ZeroDivisionError, TypeError:
            return None

    def _interpret(self, ratio_name: str, value: float) -> str:
        meta = RATIO_META.get(ratio_name)
        if not meta or value is None:
            return "NEUTRAL"
        th = meta["thresholds"]
        inverted = meta.get("inverted", False)
        # WHY: ratio inverted dùng <= (thấp hơn ngưỡng = tốt hơn); ratio
        # thường dùng >=. GOOD là mức kỳ vọng, WARNING là ranh giới cần theo
        # dõi, ngoài BAD là đỏ.
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

    def compute_standard_ratios(self, period_metrics: dict) -> dict[str, float]:
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
        # WHY: equity ≤ 0 (âm vốn chủ sở hữu) → ROE/DEBT_TO_EQUITY là số âm vô nghĩa,
        # đánh lừa đánh giá "sinh lời/vốn" khi công ty thực chất mất vốn. Không sinh.
        if equity is not None and equity > 0:
            r["ROE"] = self._safe_div(ni, equity)
            r["DEBT_TO_EQUITY"] = self._safe_div(debt, equity)
        r["ROA"] = self._safe_div(ni, assets)
        r["CURRENT_RATIO"] = self._safe_div(ca, cl)
        if ca is not None and inv is not None and cl is not None:
            r["QUICK_RATIO"] = self._safe_div(ca - inv, cl)
        r["CASH_RATIO"] = self._safe_div(cash, cl)
        r["DEBT_TO_ASSETS"] = self._safe_div(debt, assets)
        if interest and interest != 0:
            ebit = period_metrics.get("EBITDA") or (ni + interest if ni else None)
            if ebit is not None:
                r["INTEREST_COVERAGE"] = self._safe_div(ebit, interest)
        r["RECEIVABLES_TO_REVENUE"] = self._safe_div(rec, rev)
        r["INVENTORY_TO_REVENUE"] = self._safe_div(inv, rev)
        r["ASSET_TURNOVER"] = self._safe_div(rev, assets)
        return {k: v for k, v in r.items() if v is not None}

    def compute_bank_ratios(self, period_metrics: dict) -> dict[str, float]:
        r = {}
        nii = period_metrics.get("NII")
        # NET_PROFIT vs NET_INCOME: VCI maps net_profit_loss_after_tax → NET_INCOME,
        # CafeF maps netProfit → NET_PROFIT. Accept either for bank ratios.
        np_ = period_metrics.get("NET_PROFIT")
        if np_ is None:
            np_ = period_metrics.get("NET_INCOME")
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
        # WHY: equity ≤ 0 (âm vốn chủ sở hữu) → ROE/CAPITAL_RATIO âm vô nghĩa, không sinh.
        if equity is not None and equity > 0:
            r["ROE"] = self._safe_div(np_, equity)
            r["CAPITAL_RATIO"] = self._safe_div(equity, assets)
        r["ROA"] = self._safe_div(np_, assets)
        r["NIM"] = self._safe_div(nii, loans)
        r["PROVISION_TO_NET_PROFIT"] = self._safe_div(prov, np_)
        r["LDR"] = self._safe_div(loans, deposits)
        r["COST_TO_INCOME"] = self._safe_div(opex, toi)
        if npl is not None:
            r["NPL_RATIO"] = npl
        if casa is not None:
            r["CASA_RATIO"] = casa
        return {k: v for k, v in r.items() if v is not None}

    def compute_securities_ratios(self, period_metrics: dict) -> dict[str, float]:
        """Tính ratios cho Công ty Chứng khoán (SECURITIES) — ADDITIVE.

        WHY: CTCK vẫn có Doanh thu/Gross Profit/CFO/D/E chuẩn (doanh thu môi giới,
        lãi tự doanh, dòng tiền hoạt động) nên KẾ THỪA toàn bộ standard_ratios
        (không mất GROSS_MARGIN/D/E hiện có), rồi BỔ SUNG các ratio đặc thù
        (Thông tư 334/2016/TT-BTC): đòn bẩy margin, cơ cấu tài sản FVTPL/HTM/AFS.
        Các ratio đặc thù chỉ xuất hiện khi nguồn cung cấp metric tương ứng —
        thiếu dữ liệu → không bịa (filter None cuối hàm).
        """
        r = self.compute_standard_ratios(period_metrics)
        margin = period_metrics.get("MARGIN_LOANS")
        fvtpl = period_metrics.get("FVTPL")
        htm = period_metrics.get("HTM")
        afs = period_metrics.get("AFS")
        assets = period_metrics.get("TOTAL_ASSETS")
        equity = period_metrics.get("TOTAL_EQUITY")

        r["MARGIN_TO_EQUITY"] = self._safe_div(margin, equity) if equity and equity > 0 else None
        r["MARGIN_TO_ASSETS"] = self._safe_div(margin, assets)
        r["FVTPL_TO_ASSETS"] = self._safe_div(fvtpl, assets)
        r["HTM_TO_ASSETS"] = self._safe_div(htm, assets)
        r["AFS_TO_ASSETS"] = self._safe_div(afs, assets)
        return {k: v for k, v in r.items() if v is not None}

    def compute_insurance_ratios(self, period_metrics: dict) -> dict[str, float]:
        """Tính ratios cho Doanh nghiệp Bảo hiểm (INSURANCE).

        WHY: DNBH không có Doanh thu thuần/Gross Profit theo chuẩn sản xuất —
        doanh thu chính là Phí bảo hiểm thuần (NET_PREMIUM). Chỉ số cốt lõi theo
        Thông tư 135/2012/TT-BTC: Loss/Expense/Combined Ratio (lãi kỹ thuật) +
        dự phòng nghiệp vụ. ROE/ROA/CFO_TO_NET_INCOME dùng chung để so được với
        các ngành khác.
        """
        r = {}
        np_ = period_metrics.get("NET_PROFIT")
        if np_ is None:
            np_ = period_metrics.get("NET_INCOME")
        premium = period_metrics.get("NET_PREMIUM")
        claims = period_metrics.get("NET_CLAIMS")
        opex = period_metrics.get("OPERATING_EXPENSE")
        reserves = period_metrics.get("TECHNICAL_RESERVES")
        assets = period_metrics.get("TOTAL_ASSETS")
        equity = period_metrics.get("TOTAL_EQUITY")
        cfo = period_metrics.get("CFO")

        r["CFO_TO_NET_INCOME"] = self._safe_div(cfo, np_)
        # WHY: equity ≤ 0 (âm vốn chủ sở hữu) → ROE âm vô nghĩa, không sinh.
        if equity is not None and equity > 0:
            r["ROE"] = self._safe_div(np_, equity)
        r["ROA"] = self._safe_div(np_, assets)
        if premium is not None and premium > 0:
            loss_ratio = self._safe_div(claims, premium)
            expense_ratio = self._safe_div(opex, premium)
            if loss_ratio is not None:
                r["LOSS_RATIO"] = loss_ratio
            if expense_ratio is not None:
                r["EXPENSE_RATIO"] = expense_ratio
            if loss_ratio is not None and expense_ratio is not None:
                r["COMBINED_RATIO"] = round(loss_ratio + expense_ratio, 4)
        r["TECH_RESERVE_TO_ASSETS"] = self._safe_div(reserves, assets)
        return {k: v for k, v in r.items() if v is not None}

    def compute_period_ratios(self, symbol: str, period: str, period_metrics: dict, entity_type: str) -> dict:
        # WHY: chọn bộ công thức theo entity_type — 4 khung kế toán VAS khác biệt:
        # BANK (NIM/LDR/NPL/CASA), SECURITIES (margin/FVTPL/HTM/AFS + standard),
        # INSURANCE (Loss/Combined/Reserve), STANDARD (Inventory/Gross Margin/D/E).
        et = entity_type.upper() if entity_type else "STANDARD"
        if et == "BANK":
            ratios = self.compute_bank_ratios(period_metrics)
        elif et == "SECURITIES":
            ratios = self.compute_securities_ratios(period_metrics)
        elif et == "INSURANCE":
            ratios = self.compute_insurance_ratios(period_metrics)
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

    def compute_health(self, symbol: str) -> dict:
        entity_type = self.facts_db.get_entity_type(symbol)
        facts = self.facts_db.get_facts(symbol)
        if not facts:
            return {"status": "NO_DATA", "symbol": symbol}

        conn = self.connect()
        results = []

        # WHY: duyệt kỳ MỚI NHẤT trước — log dễ đọc, và các consumer
        # (get_latest_health) lấy MAX(period) không phụ thuộc thứ tự này.
        for period in sorted(facts.keys(), reverse=True):
            pm = facts[period]
            fy = pm.get("_fiscal_year", int(period[:4]))
            fq = pm.get("_fiscal_quarter", int(period[5:6]))

            computed = self.compute_period_ratios(symbol, period, pm, entity_type)
            period_count = 0

            for rname, rinfo in computed.items():
                meta = RATIO_META.get(rname, {})
                metadata = {"thresholds": meta.get("thresholds", {})}
                # WHY: INSERT OR REPLACE + UNIQUE(symbol, period, ratio_name)
                # → compute lại idempotent, không đúp dòng khi chạy nhiều lần.
                conn.execute(
                    """
                    INSERT OR REPLACE INTO health_ratios
                        (symbol, period, fiscal_year, fiscal_quarter,
                         entity_type, ratio_name, ratio_value, category,
                         interpretation, metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        symbol.upper(),
                        period,
                        fy,
                        fq,
                        entity_type.upper(),
                        rname,
                        rinfo["value"],
                        rinfo["category"],
                        rinfo["interpretation"],
                        json.dumps(metadata),
                    ),
                )
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

    def get_health_summary(self, symbol: str) -> dict | None:
        conn = self.connect()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT period, ratio_name, ratio_value, category, interpretation
            FROM health_ratios
            WHERE symbol = ?
            ORDER BY period DESC, category, ratio_name
        """,
            (symbol.upper(),),
        )
        rows = cur.fetchall()
        conn.close()
        if not rows:
            return None
        result = {"symbol": symbol.upper(), "ratios": []}
        for r in rows:
            result["ratios"].append(
                {
                    "period": r[0],
                    "ratio": r[1],
                    "value": r[2],
                    "category": r[3],
                    "interpretation": r[4],
                }
            )
        return result

    def get_latest_health(self, symbol: str) -> dict | None:
        conn = self.connect()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT period, ratio_name, ratio_value, category, interpretation
            FROM health_ratios
            WHERE symbol = ? AND period = (
                SELECT MAX(period) FROM health_ratios WHERE symbol = ?
            )
            ORDER BY category, ratio_name
        """,
            (symbol.upper(), symbol.upper()),
        )
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
            result["ratios"][cat].append(
                {
                    "ratio": r[1],
                    "value": r[2],
                    "interpretation": r[4],
                }
            )
        return result

    def compare_symbols(self, symbols: list[str]) -> dict:
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
    parser.add_argument("action", choices=["init", "compute", "show", "compare"], help="Hành động")
    parser.add_argument("--symbols", nargs="+", default=["FPT", "ACB", "HDB", "MBB", "VCB"], help="Danh sách symbol")
    parser.add_argument("--periods", type=int, default=8, help="Số kỳ gần nhất")
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
            print(f"  [{result['status']}] {result['symbol']}: {result['periods']} periods, {result['total_ratios']} ratios")

    elif args.action == "show":
        for sym in args.symbols:
            h = engine.get_latest_health(sym)
            if not h:
                print(f"\n  {sym}: NO DATA")
                continue
            print(f"\n  {'=' * 50}")
            print(f"  {sym} ({h['period']})")
            print(f"  {'=' * 50}")
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
        print(f"\n  {'=' * 70}")
        print(f"  COMPARE: {', '.join(args.symbols)}")
        print(f"  {'=' * 70}")
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
        print(f"  {'-' * len(header)}")
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
