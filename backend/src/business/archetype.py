"""archetype.py — Business Archetype Classification Engine.

Định nghĩa "loài" doanh nghiệp qua DNA attributes.
Mỗi archetype có một cấu trúc kinh tế khác nhau → response khác nhau với cùng macro driver.

Usage:
    from src.business.archetype import ArchetypeEngine, BusinessArchetype
    engine = ArchetypeEngine()
    arch = engine.classify("FPT")
    print(arch.archetype, arch.revenue_model)
"""

import sqlite3
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

# Sentinel v2.2 (AGENTS.md Anchor)
_candidate = Path(sys.executable).resolve().parent
if Path(sys.executable).stem.lower().startswith("python"):
    _p = Path(__file__).resolve().parent.parent.parent
    for _par in [_p] + list(_p.parents):
        if (_par / "AGENTS.md").exists() and (_par / "backend").is_dir():
            _candidate = _par
            break
BACKEND_DIR = _candidate / "backend"
DATA_DIR = BACKEND_DIR / "data"
FINANCIAL_DB = DATA_DIR / "financial_facts.db"


# ═══════════════════════════════════════════════════════════════
# 1. ARCHETYPE TAXONOMY
# ═══════════════════════════════════════════════════════════════


class RevenueModel(Enum):
    RECURRING = "RECURRING"  # subscription, outsourcing, maintenance
    TRANSACTIONAL = "TRANSACTIONAL"  # retail, trading
    SPREAD_BASED = "SPREAD_BASED"  # bank NIM, commodity margin
    FEE_BASED = "FEE_BASED"  # bancassurance, brokerage
    PROJECT_BASED = "PROJECT_BASED"  # real estate handover, EPC
    REGULATED_TARIFF = "REGULATED_TARIFF"  # utility pricing by gov


class OperatingLeverage(Enum):
    LOW = "LOW"  # asset-light, variable cost dominant
    MEDIUM = "MEDIUM"  # balanced
    HIGH = "HIGH"  # fixed cost dominant (steel, airline)


class Cyclicality(Enum):
    NONE = "NONE"  # utility, consumer staple
    LOW = "LOW"  # compounder, platform
    MEDIUM = "MEDIUM"  # bank (credit cycle)
    HIGH = "HIGH"  # commodity, real estate


class PricingPower(Enum):
    NONE = "NONE"  # commodity, no differentiation
    LOW = "LOW"  # limited brand power
    MEDIUM = "MEDIUM"  # moderate switching cost
    HIGH = "HIGH"  # strong moat, essential service


class MoatType(Enum):
    SWITCHING_COST = "SWITCHING_COST"  # FPT: enterprise IT
    COST_ADVANTAGE = "COST_ADVANTAGE"  # DGC: apatite mine
    SCALE = "SCALE"  # HPG: steel scale
    BRAND = "BRAND"  # VCB: state bank brand
    REGULATORY = "REGULATORY"  # GAS: pipeline monopoly
    NETWORK = "NETWORK"  # platform
    ECOSYSTEM = "ECOSYSTEM"  # MBB: MB Group synergy
    LOCATION = "LOCATION"  # VHM: land bank


# ── Primary Archetypes ──────────────────────────────────────


@dataclass
class BusinessArchetype:
    """DNA của doanh nghiệp — cấu trúc bất biến qua chu kỳ.

    Đây là "loài", không phải "trạng thái sức khỏe".
    """

    archetype: str
    label: str
    description: str

    # DNA attributes
    revenue_model: RevenueModel
    operating_leverage: OperatingLeverage
    financial_leverage: OperatingLeverage  # same enum, different concept
    capex_intensity: str  # LOW / MEDIUM / HIGH
    cyclicality: Cyclicality
    pricing_power: PricingPower
    switching_cost: str  # LOW / MEDIUM / HIGH
    network_effect: str  # NONE / WEAK / STRONG
    regulation: str  # NONE / LOW / HIGH
    commodity_dependency: str  # NONE / LOW / MEDIUM / HIGH
    recurring_ratio: float  # 0.0 – 1.0
    reinvestment_profile: str  # LOW / MEDIUM / HIGH

    # Typical financial signatures
    typical_roic_range: tuple[float, float] = (0.0, 0.0)
    typical_pe_range: tuple[float, float] = (0.0, 0.0)
    typical_dividend_yield: str = ""

    # Primary macro sensitivities
    macro_sensitivities: list[str] = field(default_factory=list)
    primary_driver: str = ""


# ── Archetype Registry ──────────────────────────────────────

ARCHETYPE_REGISTRY: dict[str, BusinessArchetype] = {}


def _reg(a: BusinessArchetype):
    ARCHETYPE_REGISTRY[a.archetype] = a
    return a


# Compounder: asset-light, recurring, high switching cost
_reg(
    BusinessArchetype(
        archetype="COMPOUNDER",
        label="Công ty Tăng trưởng Chất lượng Cao",
        description="Asset-light, recurring revenue, high ROIC, low capex, pricing power từ switching cost",
        revenue_model=RevenueModel.RECURRING,
        operating_leverage=OperatingLeverage.LOW,
        financial_leverage=OperatingLeverage.LOW,
        capex_intensity="LOW",
        cyclicality=Cyclicality.LOW,
        pricing_power=PricingPower.HIGH,
        switching_cost="HIGH",
        network_effect="WEAK",
        regulation="LOW",
        commodity_dependency="NONE",
        recurring_ratio=0.70,
        reinvestment_profile="MEDIUM",
        typical_roic_range=(0.20, 0.45),
        typical_pe_range=(15, 30),
        typical_dividend_yield="1-2%",
        macro_sensitivities=["IT_SPENDING", "GOV_IT_BUDGET", "AI_CAPEX", "CORP_EARNINGS"],
        primary_driver="IT Outsource Backlog",
    )
)

# Cyclical Heavy: commodity, high operating leverage, inventory cycle
_reg(
    BusinessArchetype(
        archetype="CYCLICAL_HEAVY",
        label="Công ty Chu kỳ Nặng",
        description="Hàng hóa, đòn bẩy hoạt động cao, phụ thuộc giá đầu vào/đầu ra, capex lớn",
        revenue_model=RevenueModel.SPREAD_BASED,
        operating_leverage=OperatingLeverage.HIGH,
        financial_leverage=OperatingLeverage.MEDIUM,
        capex_intensity="HIGH",
        cyclicality=Cyclicality.HIGH,
        pricing_power=PricingPower.NONE,
        switching_cost="LOW",
        network_effect="NONE",
        regulation="LOW",
        commodity_dependency="HIGH",
        recurring_ratio=0.10,
        reinvestment_profile="HIGH",
        typical_roic_range=(0.05, 0.25),
        typical_pe_range=(5, 15),
        typical_dividend_yield="2-5%",
        macro_sensitivities=["STEEL_PRICE", "CONSTRUCTION", "CHINA_DEMAND", "COAL_PRICE", "FREIGHT"],
        primary_driver="Capacity Utilization → Spread",
    )
)

# Franchise Bank: cheap deposit franchise, low-risk lending
_reg(
    BusinessArchetype(
        archetype="FRANCHISE_BANK",
        label="Ngân hàng Thương hiệu",
        description="Chi phí vốn siêu thấp nhờ CASA cao, rủi ro thấp, NIM ổn định",
        revenue_model=RevenueModel.SPREAD_BASED,
        operating_leverage=OperatingLeverage.MEDIUM,
        financial_leverage=OperatingLeverage.HIGH,
        capex_intensity="MEDIUM",
        cyclicality=Cyclicality.MEDIUM,
        pricing_power=PricingPower.HIGH,
        switching_cost="MEDIUM",
        network_effect="NONE",
        regulation="HIGH",
        commodity_dependency="NONE",
        recurring_ratio=0.80,
        reinvestment_profile="MEDIUM",
        typical_roic_range=(0.15, 0.25),
        typical_pe_range=(8, 18),
        typical_dividend_yield="2-4%",
        macro_sensitivities=["INTEREST_RATE", "CREDIT_GROWTH", "NPL_CYCLE", "CASA_RATIO"],
        primary_driver="CASA → NIM → Pre-provision Income",
    )
)

# Asset Bank: growth-focused, higher risk, ecosystem synergy
_reg(
    BusinessArchetype(
        archetype="ASSET_BANK",
        label="Ngân hàng Bán lẻ & Đa năng",
        description="Tăng trưởng tín dụng cao, bancassurance, hệ sinh thái, NIM cao hơn nhưng rủi ro cao hơn franchise bank",
        revenue_model=RevenueModel.SPREAD_BASED,
        operating_leverage=OperatingLeverage.MEDIUM,
        financial_leverage=OperatingLeverage.HIGH,
        capex_intensity="MEDIUM",
        cyclicality=Cyclicality.MEDIUM,
        pricing_power=PricingPower.MEDIUM,
        switching_cost="LOW",
        network_effect="WEAK",
        regulation="HIGH",
        commodity_dependency="NONE",
        recurring_ratio=0.65,
        reinvestment_profile="HIGH",
        typical_roic_range=(0.12, 0.22),
        typical_pe_range=(6, 14),
        typical_dividend_yield="1-3%",
        macro_sensitivities=["INTEREST_RATE", "CREDIT_GROWTH", "CONSUMER_SPENDING", "RETAIL_LOANS"],
        primary_driver="Credit Growth → NIM → Fee Income",
    )
)

# Real Estate Developer: land bank, leverage, cash conversion cycle
_reg(
    BusinessArchetype(
        archetype="REAL_ESTATE_DEVELOPER",
        label="Công ty Phát triển Bất động sản",
        description="Quỹ đất lớn, đòn bẩy tài chính, chu kỳ presales → handover, phụ thuộc chính sách",
        revenue_model=RevenueModel.PROJECT_BASED,
        operating_leverage=OperatingLeverage.MEDIUM,
        financial_leverage=OperatingLeverage.HIGH,
        capex_intensity="HIGH",
        cyclicality=Cyclicality.HIGH,
        pricing_power=PricingPower.MEDIUM,
        switching_cost="MEDIUM",
        network_effect="NONE",
        regulation="HIGH",
        commodity_dependency="NONE",
        recurring_ratio=0.05,
        reinvestment_profile="HIGH",
        typical_roic_range=(0.06, 0.18),
        typical_pe_range=(5, 12),
        typical_dividend_yield="1-3%",
        macro_sensitivities=["INTEREST_RATE", "HOUSING_POLICY", "CREDIT_GROWTH", "CONSTRUCTION", "LAND_PRICE"],
        primary_driver="Land Bank → Presales → Handover Revenue",
    )
)

# Retail Platform: store density, same-store-sales, supply chain
_reg(
    BusinessArchetype(
        archetype="RETAIL_PLATFORM",
        label="Nền tảng Bán lẻ",
        description="Mật độ cửa hàng, doanh thu cùng cửa hàng, chuỗi cung ứng, thị phần bán lẻ",
        revenue_model=RevenueModel.TRANSACTIONAL,
        operating_leverage=OperatingLeverage.MEDIUM,
        financial_leverage=OperatingLeverage.LOW,
        capex_intensity="MEDIUM",
        cyclicality=Cyclicality.LOW,
        pricing_power=PricingPower.MEDIUM,
        switching_cost="LOW",
        network_effect="WEAK",
        regulation="LOW",
        commodity_dependency="NONE",
        recurring_ratio=0.30,
        reinvestment_profile="MEDIUM",
        typical_roic_range=(0.12, 0.25),
        typical_pe_range=(10, 25),
        typical_dividend_yield="1-2%",
        macro_sensitivities=["CONSUMER_SPENDING", "RETAIL_SALES", "INFLATION", "SUPPLY_CHAIN"],
        primary_driver="Store Density → Same-Store-Sales → Margin",
    )
)

# Commercial Real Estate / REIT: mall & office leasing, occupancy-led revenue
_reg(
    BusinessArchetype(
        archetype="REIT_COMMERCIAL",
        label="Công ty Cho thuê BĐS Thương mại",
        description="Sở hữu & cho thuê TTTM, văn phòng; doanh thu cho thuê định kỳ, phụ thuộc tỷ lệ lấp đầy & suất cho thuê",
        revenue_model=RevenueModel.RECURRING,
        operating_leverage=OperatingLeverage.MEDIUM,
        financial_leverage=OperatingLeverage.HIGH,
        capex_intensity="HIGH",
        cyclicality=Cyclicality.MEDIUM,
        pricing_power=PricingPower.MEDIUM,
        switching_cost="MEDIUM",
        network_effect="MEDIUM",
        regulation="MEDIUM",
        commodity_dependency="NONE",
        recurring_ratio=0.85,
        reinvestment_profile="HIGH",
        typical_roic_range=(0.08, 0.18),
        typical_pe_range=(8, 18),
        typical_dividend_yield="2-5%",
        macro_sensitivities=["INTEREST_RATE", "CONSUMER_SPENDING", "RETAIL_SALES", "INFLATION"],
        primary_driver="Occupancy → Rental Yield → Lease Revenue",
    )
)

# Regulated Utility: monopoly/license, regulated tariff, stable cashflow
_reg(
    BusinessArchetype(
        archetype="REGULATED_UTILITY",
        label="Công ty Hạ tầng Dịch vụ",
        description="Độc quyền hạ tầng (khí, điện, nước), giá điều tiết, dòng tiền ổn định, tăng trưởng thấp",
        revenue_model=RevenueModel.REGULATED_TARIFF,
        operating_leverage=OperatingLeverage.HIGH,
        financial_leverage=OperatingLeverage.HIGH,
        capex_intensity="HIGH",
        cyclicality=Cyclicality.NONE,
        pricing_power=PricingPower.HIGH,
        switching_cost="HIGH",
        network_effect="NONE",
        regulation="HIGH",
        commodity_dependency="MEDIUM",
        recurring_ratio=0.95,
        reinvestment_profile="HIGH",
        typical_roic_range=(0.08, 0.15),
        typical_pe_range=(8, 16),
        typical_dividend_yield="4-7%",
        macro_sensitivities=["OIL_PRICE", "GAS_VOLUME", "REGULATORY_TARIFF", "INDUSTRIAL_PRODUCTION"],
        primary_driver="Gas Volume → Regulated Tariff → EBITDA",
    )
)

# Export Manufacturer: FX sensitive, global demand, thin margin
_reg(
    BusinessArchetype(
        archetype="EXPORT_MANUFACTURER",
        label="Sản xuất Xuất khẩu",
        description="Nhạy tỷ giá, phụ thuộc cầu toàn cầu, biên mỏng, đòn bẩy hoạt động cao",
        revenue_model=RevenueModel.TRANSACTIONAL,
        operating_leverage=OperatingLeverage.HIGH,
        financial_leverage=OperatingLeverage.MEDIUM,
        capex_intensity="HIGH",
        cyclicality=Cyclicality.HIGH,
        pricing_power=PricingPower.LOW,
        switching_cost="LOW",
        network_effect="NONE",
        regulation="LOW",
        commodity_dependency="MEDIUM",
        recurring_ratio=0.15,
        reinvestment_profile="MEDIUM",
        typical_roic_range=(0.08, 0.20),
        typical_pe_range=(6, 14),
        typical_dividend_yield="2-4%",
        macro_sensitivities=["USD_VND", "GLOBAL_DEMAND", "FREIGHT", "TARIFF", "CHINA_DEMAND"],
        primary_driver="Export Volume → FX → Margin",
    )
)


# ═══════════════════════════════════════════════════════════════
# 2. CLASSIFIER
# ═══════════════════════════════════════════════════════════════

# Baseline mapping for 10 Core + 10 Satellite symbols (human-validated)
BASELINE_MAP: dict[str, str] = {
    # ── Core Universe (10 mã) ──
    "FPT": "COMPOUNDER",
    "DGC": "COMPOUNDER",  # chemical compounder — cost advantage
    "HPG": "CYCLICAL_HEAVY",
    "VCB": "FRANCHISE_BANK",
    "ACB": "FRANCHISE_BANK",
    "MBB": "ASSET_BANK",
    "HDB": "ASSET_BANK",
    "VHM": "REAL_ESTATE_DEVELOPER",
    "BCM": "REAL_ESTATE_DEVELOPER",  # Becamex IDC — KCN/industrial park developer
    "VRE": "REIT_COMMERCIAL",  # Vincom Retail — ~95.5% revenue từ cho thuê TTTM, KHÔNG phải developer (override ICB BĐS)
    "MWG": "RETAIL_PLATFORM",
    "GAS": "REGULATED_UTILITY",
    # ── Satellite Universe (10 mã chờ, thêm 2026-07-30) ──
    "TCB": "FRANCHISE_BANK",  # Techcombank — CASA ~40%, premier franchise
    "BID": "FRANCHISE_BANK",  # BIDV — Big-4 state bank, deposit franchise + scale
    "CTG": "FRANCHISE_BANK",  # VietinBank — Big-4 state bank, corporate franchise
    "STB": "ASSET_BANK",  # Sacombank — retail, restructuring story
    "VIB": "ASSET_BANK",  # Vietnam International Bank — retail, auto lending
    "REE": "COMPOUNDER",  # Refrigeration Electrical — 40+ yr moat, diversified
    "GMD": "CYCLICAL_HEAVY",  # Gemadept — port operator, volume-cyclical
    "VGI": "REGULATED_UTILITY",  # Viettel Global — telecom infra, state-backed
    "SSI": "RETAIL_PLATFORM",  # SSI Securities — brokerage platform
    "BSR": "CYCLICAL_HEAVY",  # Binh Son Refining — oil refinery, crack-spread cyclical
    "QNS": "EXPORT_MANUFACTURER",  # Quang Ngai Sugar — commodity processor
    "TLG": "COMPOUNDER",  # Thien Long Group — stationery leader, strong brand
}


class ArchetypeEngine:
    """Phân loại doanh nghiệp vào Business Archetype.

    Two-tier:
      1. Baseline map cho 10 mã quen thuộc
      2. Rule-based classifier cho mã mới (dựa trên financial_facts.db)
    """

    def __init__(self):
        self._conn: sqlite3.Connection | None = None

    def _get_conn(self):
        if self._conn is None:
            self._conn = sqlite3.connect(str(FINANCIAL_DB))
        return self._conn

    def classify(self, symbol: str) -> BusinessArchetype:
        """Return BusinessArchetype cho một symbol."""
        sym = symbol.upper().strip()

        # 1. Baseline map
        if sym in BASELINE_MAP:
            return ARCHETYPE_REGISTRY[BASELINE_MAP[sym]]

        # 2. Rule-based fallback (for new symbols)
        return self._classify_by_ratios(sym)

    def classify_many(self, symbols: list[str]) -> dict[str, BusinessArchetype]:
        return {s: self.classify(s) for s in symbols}

    def _is_bank_symbol(self, symbol: str, entity_type: str) -> bool:
        """Hard Constraint: mã ngân hàng tuyệt đối không gán mô hình phi tài chính.

        Kiểm tra 2 nguồn:
          1. entity_type == "BANK" trong financial_facts.db (health_ratios)
          2. ICB sector == "Ngân hàng" trong screener_cache.db (symbol_industry)

        WHY: Nhiều ngân hàng (BID, CTG, VPB, TPB) chưa có health_ratios rows
        → entity_type rỗng → rơi vào nhánh STANDARD → gán RETAIL_PLATFORM sai.
        ICB mapping là nguồn sự thật thứ 2 để chặn cứng.
        """
        if entity_type == "BANK":
            return True
        try:
            import sqlite3 as _sqlite

            conn = _sqlite.connect(str(DATA_DIR / "screener_cache.db"))
            try:
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT icb_name2 FROM symbol_industry
                    WHERE symbol = ? LIMIT 1
                """,
                    (symbol,),
                )
                row = cur.fetchone()
                return bool(row and str(row[0]).strip() == "Ngân hàng")
            finally:
                conn.close()
        except Exception:  # noqa: BLE001 - cố ý bắt rộng để fallback/phòng thủ an toàn
            return False

    def _icb_sector(self, symbol: str) -> str | None:
        """Resolve ICB sector (icb_name2) từ screener_cache.db — nguồn sự thật
        thứ 2, NHẤT QUÁN với _is_bank_symbol và _symbol_sector().
        """
        try:
            conn = sqlite3.connect(str(DATA_DIR / "screener_cache.db"))
            try:
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT icb_name2 FROM symbol_industry
                    WHERE symbol = ? LIMIT 1
                """,
                    (symbol,),
                )
                row = cur.fetchone()
                return str(row[0]).strip() if row and row[0] else None
            finally:
                conn.close()
        except Exception:  # noqa: BLE001 - cố ý bắt rộng để fallback/phòng thủ an toàn
            return None

    def _classify_by_ratios(self, symbol: str) -> BusinessArchetype:
        """Fallback classifier — đọc financial_facts.db, suy luận archetype."""
        conn = self._get_conn()
        cur = conn.cursor()

        # Lấy entity_type
        cur.execute(
            """
            SELECT DISTINCT entity_type FROM health_ratios
            WHERE symbol = ? LIMIT 1
        """,
            (symbol,),
        )
        row = cur.fetchone()
        entity_type = str(row[0]).strip().upper() if row else "STANDARD"

        # Hard Constraint 2: thuộc ICB sector "Bất động sản" → REAL_ESTATE_DEVELOPER.
        # WHY: KCN (SIP, IDC, KBC) chưa có health_ratios rows → ratios rỗng →
        # rơi vào nhánh STANDARD → gán RETAIL_PLATFORM sai (chain SAME_STORE_SALES
        # thay vì PRESALES). ICB mapping là nguồn sự thật, vì sector BĐS đồng
        # nghĩa với mô hình phát triển dự án (PRESALES), bất kể ratio cụ thể.
        if self._icb_sector(symbol) == "Bất động sản":
            return ARCHETYPE_REGISTRY["REAL_ESTATE_DEVELOPER"]

        if self._is_bank_symbol(symbol, entity_type):
            # Hard Constraint: thuộc ngành Ngân hàng → bắt buộc archetype ngân hàng.
            # Phân biệt FRANCHISE vs ASSET bằng CASA ratio.
            cur.execute(
                """
                SELECT ratio_value FROM health_ratios
                WHERE symbol = ? AND ratio_name = 'CASA_RATIO'
                ORDER BY period DESC LIMIT 1
            """,
                (symbol,),
            )
            casa = cur.fetchone()
            if casa and casa[0] and float(casa[0]) > 0.35:
                return ARCHETYPE_REGISTRY["FRANCHISE_BANK"]
            return ARCHETYPE_REGISTRY["ASSET_BANK"]

        # Standard: xét capex_intensity qua asset turnover
        cur.execute(
            """
            SELECT ratio_value FROM health_ratios
            WHERE symbol = ? AND ratio_name = 'RECEIVABLES_TO_REVENUE'
            ORDER BY period DESC LIMIT 1
        """,
            (symbol,),
        )
        turnover = cur.fetchone()
        rev = turnover[0] if turnover else 0.5

        cur.execute(
            """
            SELECT ratio_value FROM health_ratios
            WHERE symbol = ? AND ratio_name = 'DEBT_TO_EQUITY'
            ORDER BY period DESC LIMIT 1
        """,
            (symbol,),
        )
        debt = cur.fetchone()
        de = float(debt[0]) if debt and debt[0] else 0.5

        # High debt + low turnover → CYCLICAL_HEAVY or REAL_ESTATE
        if de > 1.5 and rev < 0.3:
            return ARCHETYPE_REGISTRY["REAL_ESTATE_DEVELOPER"]
        if de > 1.0 and rev > 0.5:
            return ARCHETYPE_REGISTRY["CYCLICAL_HEAVY"]

        # Recurring test
        cur.execute(
            """
            SELECT ratio_value FROM health_ratios
            WHERE symbol = ? AND ratio_name = 'CFO_TO_NET_INCOME'
            ORDER BY period DESC LIMIT 1
        """,
            (symbol,),
        )
        cfo = cur.fetchone()
        cfo_val = float(cfo[0]) if cfo and cfo[0] else 0.5

        if cfo_val > 0.8:
            return ARCHETYPE_REGISTRY["COMPOUNDER"]
        if cfo_val > 0.4:
            return ARCHETYPE_REGISTRY["RETAIL_PLATFORM"]

        return ARCHETYPE_REGISTRY["COMPOUNDER"]

    def close(self):
        if self._conn:
            self._conn.close()

    def __del__(self):
        self.close()


# ═══════════════════════════════════════════════════════════════
# 3. REPORTING
# ═══════════════════════════════════════════════════════════════


def print_archetype_report(results: dict[str, BusinessArchetype]):
    print(f"\n  {'=' * 80}")
    print("  BUSINESS ARCHETYPE — DNA MAP")
    print(f"  {'=' * 80}")
    print(f"  {'Mã':<6} {'Archetype':<22} {'Revenue Model':<20} {'Cyclicality':<12} {'Pricing Power':<14} {'Moat'}")
    print(f"  {'-' * 80}")
    for sym, a in sorted(results.items()):
        moats = [a.switching_cost, a.network_effect, a.regulation]
        primary_moat = next((m for m in moats if m != "NONE"), a.switching_cost)
        print(
            f"  {sym:<6} {a.label:<22} {a.revenue_model.value:<20} {a.cyclicality.value:<12} "
            f"{a.pricing_power.value:<14} {primary_moat}"
        )

    print(f"\n  {'=' * 80}")
    print("  CHI TIẾT DNA")
    print(f"  {'=' * 80}")
    for sym, a in sorted(results.items()):
        print(f"\n  {'─' * 50}")
        print(f"  {sym} — {a.label}")
        print(f"  {'─' * 50}")
        print(f"  Archetype:        {a.archetype}")
        print(f"  Revenue Model:    {a.revenue_model.value}")
        print(f"  Operating Lev:    {a.operating_leverage.value}")
        print(f"  Financial Lev:    {a.financial_leverage.value}")
        print(f"  Capex Intensity:  {a.capex_intensity}")
        print(f"  Cyclicality:      {a.cyclicality.value}")
        print(f"  Pricing Power:    {a.pricing_power.value}")
        print(f"  Switching Cost:   {a.switching_cost}")
        print(f"  Network Effect:   {a.network_effect}")
        print(f"  Regulation:       {a.regulation}")
        print(f"  Commodity Dep:    {a.commodity_dependency}")
        print(f"  Recurring Ratio:  {a.recurring_ratio:.0%}")
        print(f"  Primary Driver:   {a.primary_driver}")
        print(f"  Macro Sensitivity: {', '.join(a.macro_sensitivities)}")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Business Archetype Engine — DNA classification")
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=[
            "FPT",
            "ACB",
            "HDB",
            "MBB",
            "VCB",
            "HPG",
            "VHM",
            "DGC",
            "MWG",
            "GAS",
        ],
        help="Danh sách mã",
    )
    args = parser.parse_args()

    engine = ArchetypeEngine()
    results = engine.classify_many(args.symbols)
    print_archetype_report(results)


if __name__ == "__main__":
    main()
