"""economic_engine.py — Value Creation Mechanism & Cash Flow Propagation.

Mô hình hóa chuỗi nhân quả từ macro driver đến chỉ số tài chính của từng
Business Archetype. Đây là lớp "cơ chế sinh lợi" — không phải chỉ số tài chính.

Chain:
  Macro Driver → Engine Component → Financial Metric → Health Outcome

Usage:
    from src.business.economic_engine import EconomicEngine
    engine = EconomicEngine()
    chain = engine.get_propagation_chain("FPT")
    # → [AI_CAPEX → IT_Outsource_Demand → Backlog → Revenue → Margin → EPS → ROIC]
"""
# WHY: Tách lớp "cơ chế sinh lợi" (Economic Engine) khỏi lớp "đo lường" (financial ratios)
# vì cùng một chỉ số tài chính có ý nghĩa hoàn toàn khác nhau giữa các archetype — NIM tốt
# cho ngân hàng nhưng vô nghĩa cho thép. Module này gắn macro driver vào từng mắt xích nhân
# quả của mỗi archetype để có thể trace "tại sao chỉ số này thay đổi" thay vì chỉ "chỉ số là gì".

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from src.business.archetype import ArchetypeEngine, BusinessArchetype, ARCHETYPE_REGISTRY


# ═══════════════════════════════════════════════════════════════
# 1. ENGINE COMPONENTS — Building blocks of value creation
# ═══════════════════════════════════════════════════════════════

@dataclass
class EngineComponent:
    """Một mắt xích trong chuỗi nhân quả của doanh nghiệp.

    WHY: is_leading phân biệt chỉ báo dẫn (backlog, presales, SSS) với chỉ báo trễ
    (revenue, margin) — dẫn dắt biến động xảy ra TRƯỚC khi ảnh hưởng lên EPS, giúp tầng
    dự báo dùng chúng làm sớm thay vì chỉ xác nhận sau khi xu hướng đã xảy ra.
    """
    name: str
    label: str
    description: str
    unit: str = ""  # %, VND, ratio, etc.
    is_leading: bool = False  # leading indicator vs lagging


@dataclass
class PropagationChain:
    """Chuỗi nhân quả hoàn chỉnh cho một archetype."""
    archetype: str
    label: str
    chain: List[EngineComponent]
    primary_driver: str
    macro_links: Dict[str, str]  # macro_driver → engine_component


# ── Component Registry ──────────────────────────────────────

# Reusable components
C = {
    # WHY: Registry dùng dict keyed by tên chuẩn (UPPER_SNAKE) để mọi chain tham chiếu
    # component tái sử dụng — tránh trùng lặp định nghĩa giữa các archetype và đảm bảo
    # một component chỉ có một nguồn sự thật cho label/description.
    # COMPOUNDER chain
    "IT_BACKLOG": EngineComponent("IT_BACKLOG", "IT Outsourcing Backlog",
                                   "Hợp đồng CNTT chưa thực hiện (leading indicator)"),
    "IT_HEADCOUNT": EngineComponent("IT_HEADCOUNT", "Headcount Billable",
                                     "Số nhân sự tính phí (thước đo năng lực cung ứng)"),
    "IT_REVENUE": EngineComponent("IT_REVENUE", "Revenue Outsourcing",
                                   "Doanh thu từ outsourcing & giải pháp CNTT"),
    "IT_MARGIN": EngineComponent("IT_MARGIN", "Operating Margin",
                                  "Biên lợi nhuận từ mảng CNTT (cao nhờ switching cost)"),
    "DOMESTIC_IT": EngineComponent("DOMESTIC_IT", "Domestic IT Revenue",
                                    "Doanh thu thị trường nội địa (chính phủ, ngân hàng)"),
    "EDUCATION_REV": EngineComponent("EDUCATION_REV", "Education Revenue",
                                      "Doanh thu giáo dục (phân khúc thấp hơn)"),

    # CYCLICAL_HEAVY chain
    "CAPACITY_UTIL": EngineComponent("CAPACITY_UTIL", "Capacity Utilization",
                                      "Tỷ lệ vận hành công suất (leading indicator)",
                                      unit="%", is_leading=True),
    "STEEL_SPREAD": EngineComponent("STEEL_SPREAD", "Steel Spread (HRC - Raw)",
                                     "Chênh lệch giá thép - nguyên liệu đầu vào",
                                     unit="VND/ton"),
    "INVENTORY_VALUE": EngineComponent("INVENTORY_VALUE", "Inventory Revaluation",
                                        "Chênh lệch giá trị tồn kho (LIFO/FIFO)"),
    "GROSS_MARGIN": EngineComponent("GROSS_MARGIN", "Gross Margin",
                                     "Biên lãi gộp (chịu ảnh hưởng spread + inventory reval)"),
    "EBITDA_MARGIN": EngineComponent("EBITDA_MARGIN", "EBITDA Margin",
                                      "Biên EBITDA (đòn bẩy hoạt động cao)"),

    # BANK chain
    "CASA_RATIO": EngineComponent("CASA_RATIO", "CASA Ratio",
                                   "Tỷ lệ tiền gửi không kỳ hạn (chi phí vốn thấp)",
                                   unit="%", is_leading=True),
    "FUNDING_COST": EngineComponent("FUNDING_COST", "Funding Cost",
                                     "Chi phí huy động vốn bình quân", unit="%"),
    "LOAN_BOOK": EngineComponent("LOAN_BOOK", "Loan Book Growth",
                                  "Tăng trưởng dư nợ tín dụng", unit="%"),
    "NIM": EngineComponent("NIM", "Net Interest Margin",
                            "Chênh lệch lãi suất đầu vào - đầu ra", unit="%"),
    "NPL": EngineComponent("NPL", "Non-Performing Loan Ratio",
                            "Tỷ lệ nợ xấu", unit="%"),
    "PPOP": EngineComponent("PPOP", "Pre-Provision Operating Profit",
                             "Lợi nhuận trước dự phòng (core earning power)"),
    "FEE_INCOME": EngineComponent("FEE_INCOME", "Fee Income (bancassurance)",
                                   "Thu nhập phí từ bảo hiểm, thanh toán,..."),

    # REAL_ESTATE chain
    "LAND_BANK": EngineComponent("LAND_BANK", "Land Bank (Ha)",
                                  "Quỹ đất sạch chưa phát triển"),
    "PRESALES": EngineComponent("PRESALES", "Presales Value",
                                 "Giá trị hợp đồng bán trước (leading)",
                                 is_leading=True),
    "HANDOVER_REV": EngineComponent("HANDOVER_REV", "Handover Revenue",
                                     "Doanh thu bàn giao (ghi nhận doanh thu)"),
    "RECEIVABLES": EngineComponent("RECEIVABLES", "Receivables (installment)",
                                    "Phải thu khách hàng trả góp"),

    # RETAIL chain
    "STORE_COUNT": EngineComponent("STORE_COUNT", "Store Count",
                                    "Số lượng cửa hàng"),
    "SSS": EngineComponent("SSS", "Same-Store-Sales Growth",
                            "Tăng trưởng doanh thu cùng cửa hàng (leading)",
                            unit="%", is_leading=True),
    "RETAIL_MARGIN": EngineComponent("RETAIL_MARGIN", "Retail Margin",
                                      "Biên lãi gộp bán lẻ"),
    "INVENTORY_TURN": EngineComponent("INVENTORY_TURN", "Inventory Turnover",
                                       "Vòng quay hàng tồn kho"),

    # REIT / Commercial RE chain
    "OCCUPANCY": EngineComponent("OCCUPANCY", "Occupancy Rate",
                                  "Tỷ lệ lấp đầy TTTM/văn phòng (leading)",
                                  unit="%", is_leading=True),
    "RENTAL_YIELD": EngineComponent("RENTAL_YIELD", "Rental Yield",
                                     "Suất sinh lời cho thuê trên m² (leading)",
                                     unit="%", is_leading=True),
    "LEASE_REVENUE": EngineComponent("LEASE_REVENUE", "Lease Revenue",
                                      "Doanh thu cho thuê định kỳ"),

    # UTILITY chain
    "GAS_VOLUME": EngineComponent("GAS_VOLUME", "Gas Volume Sold",
                                   "Sản lượng khí tiêu thụ", is_leading=True),
    "GAS_TARIFF": EngineComponent("GAS_TARIFF", "Regulated Tariff",
                                   "Giá khí do nhà nước điều tiết"),
    "BRENT_LINK": EngineComponent("BRENT_LINK", "Brent-linked Gas Price",
                                   "Giá khí điều chỉnh theo Brent (cơ chế pass-through)"),
    "UTILITY_EBITDA": EngineComponent("UTILITY_EBITDA", "EBITDA (Utility)",
                                       "EBITDA từ hạ tầng khí (ổn định, biên cao)"),
}


# ── Propagation Chains per Archetype ────────────────────────

PROPAGATION_CHAINS: Dict[str, PropagationChain] = {}

def _reg_chain(a: str, label: str, chain: List[str], primary: str, macro_links: Dict[str, str]):
    # WHY: Helper đăng ký chain bằng danh sách tên thay vì object — vừa ngắn gọn khi khai
    # báo 8 archetype, vừa tự bỏ qua component chưa có trong registry thay vì crash lúc import.
    PROPAGATION_CHAINS[a] = PropagationChain(
        archetype=a,
        label=label,
        chain=[C[c] for c in chain if c in C],
        primary_driver=primary,
        macro_links=macro_links,
    )

# WHY: Mỗi archetype chọn macro_links theo kênh truyền dẫn thực tế — ví dụ COMPOUNDER
# nhận AI_CAPEX/GOV_IT_BUDGET trực tiếp vào backlog vì ngành IT Việt Nam phụ thuộc
# ngân sách CNTT toàn cầu, không phải lãi suất; việc chọn đúng driver quyết định chất
# lượng trace chứ không phải số lượng link.
_reg_chain("COMPOUNDER", "Công ty Tăng trưởng Chất lượng Cao", [
    "IT_BACKLOG", "IT_HEADCOUNT", "IT_REVENUE", "IT_MARGIN", "DOMESTIC_IT", "EDUCATION_REV",
], "IT Outsourcing Backlog", {
    "AI_CAPEX": "IT_BACKLOG",
    "GOV_IT_BUDGET": "IT_BACKLOG",
    "CORP_EARNINGS": "IT_REVENUE",
    "GDP_GROWTH": "DOMESTIC_IT",
})

_reg_chain("CYCLICAL_HEAVY", "Công ty Chu kỳ Nặng", [
    "CAPACITY_UTIL", "STEEL_SPREAD", "INVENTORY_VALUE", "GROSS_MARGIN", "EBITDA_MARGIN",
], "Capacity Utilization → Spread", {
    "STEEL_PRICE": "STEEL_SPREAD",
    "IRON_ORE_PRICE": "STEEL_SPREAD",
    "COAL_PRICE": "STEEL_SPREAD",
    "CONSTRUCTION": "CAPACITY_UTIL",
    "CHINA_DEMAND": "STEEL_SPREAD",
})

_reg_chain("FRANCHISE_BANK", "Ngân hàng Thương hiệu", [
    "CASA_RATIO", "FUNDING_COST", "LOAN_BOOK", "NIM", "NPL", "PPOP", "FEE_INCOME",
], "CASA → NIM → Pre-provision Income", {
    "INTEREST_RATE": "NIM",
    "CREDIT_GROWTH": "LOAN_BOOK",
    "NPL_CYCLE": "NPL",
    "CASA_RATIO": "FUNDING_COST",
})

_reg_chain("ASSET_BANK", "Ngân hàng Bán lẻ & Đa năng", [
    "CASA_RATIO", "FUNDING_COST", "LOAN_BOOK", "NIM", "NPL", "PPOP", "FEE_INCOME",
], "Credit Growth → NIM → Fee Income", {
    "INTEREST_RATE": "NIM",
    "CREDIT_GROWTH": "LOAN_BOOK",
    "CONSUMER_SPENDING": "LOAN_BOOK",
    "NPL_CYCLE": "NPL",
})

_reg_chain("REAL_ESTATE_DEVELOPER", "Công ty Phát triển Bất động sản", [
    "LAND_BANK", "PRESALES", "HANDOVER_REV", "RECEIVABLES",
], "Land Bank → Presales → Handover Revenue", {
    "LAND_PRICE": "LAND_BANK",
    "INTEREST_RATE": "PRESALES",
    "HOUSING_POLICY": "PRESALES",
    "CONSTRUCTION": "HANDOVER_REV",
})

_reg_chain("RETAIL_PLATFORM", "Nền tảng Bán lẻ", [
    "STORE_COUNT", "SSS", "RETAIL_MARGIN", "INVENTORY_TURN",
], "Store Density → Same-Store-Sales → Margin", {
    "CONSUMER_SPENDING": "SSS",
    "RETAIL_SALES": "SSS",
    "INFLATION": "RETAIL_MARGIN",
})

_reg_chain("REIT_COMMERCIAL", "Công ty Cho thuê BĐS Thương mại", [
    "OCCUPANCY", "RENTAL_YIELD", "LEASE_REVENUE",
], "Occupancy → Rental Yield → Lease Revenue", {
    "INTEREST_RATE": "RENTAL_YIELD",
    "CONSUMER_SPENDING": "OCCUPANCY",
})

_reg_chain("REGULATED_UTILITY", "Công ty Hạ tầng Dịch vụ", [
    "GAS_VOLUME", "GAS_TARIFF", "BRENT_LINK", "UTILITY_EBITDA",
], "Gas Volume → Tariff → EBITDA", {
    "OIL_PRICE": "BRENT_LINK",
    "GAS_VOLUME": "GAS_VOLUME",
    "REGULATORY_TARIFF": "GAS_TARIFF",
    "INDUSTRIAL_PRODUCTION": "GAS_VOLUME",
})

_reg_chain("EXPORT_MANUFACTURER", "Sản xuất Xuất khẩu", [
    "CAPACITY_UTIL", "GROSS_MARGIN", "EBITDA_MARGIN",
], "Export Volume → FX → Margin", {
    "USD_VND": "GROSS_MARGIN",
    "GLOBAL_DEMAND": "CAPACITY_UTIL",
    "FREIGHT": "GROSS_MARGIN",
    "TARIFF": "CAPACITY_UTIL",
})


# ═══════════════════════════════════════════════════════════════
# 2. ECONOMIC ENGINE
# ═══════════════════════════════════════════════════════════════

class EconomicEngine:
    """Tra cứu chuỗi nhân quả cho từng doanh nghiệp dựa trên archetype.

    WHY: Engine lưu ArchetypeEngine làm thành phần nội bộ (composition) để một lần khởi
    tạo là có thể classify nhiều symbol — mỗi lần classify lại tạo ArchetypeEngine mới sẽ
    tốn I/O và phá vỡ cache của archetype detector.
    """

    def __init__(self):
        self._arch_engine = ArchetypeEngine()

    def get_chain(self, symbol: str) -> Optional[PropagationChain]:
        """Return propagation chain cho một symbol."""
        arch = self._arch_engine.classify(symbol)
        return PROPAGATION_CHAINS.get(arch.archetype)

    def get_chain_by_archetype(self, archetype: str) -> Optional[PropagationChain]:
        return PROPAGATION_CHAINS.get(archetype)

    def trace_macro_impact(self, symbol: str, macro_driver: str) -> List[str]:
        """Trace một macro driver qua chain: driver → component → ... → end."""
        chain = self.get_chain(symbol)
        if not chain:
            return []
        direct_link = chain.macro_links.get(macro_driver)
        if not direct_link:
            return [f"Không có link trực tiếp: {macro_driver}"]

        # WHY: Cắt từ component trúng driver trở về cuối chain — tất cả các mắt xích sau
        # đó đều chịu ảnh hưởng dây chuyền, nên trả cả phần đuôi thay vì chỉ 1 mắt xích
        # sẽ cho người dùng thấy đường đi đầy đủ đến kết quả tài chính cuối.
        # Tìm vị trí của component trong chain → return từ đó đến cuối
        names = [c.name for c in chain.chain]
        if direct_link in names:
            idx = names.index(direct_link)
            return [c.label for c in chain.chain[idx:]]
        return [f"Component {direct_link} not found in chain"]

    def list_allowed_macro_drivers(self, symbol: str) -> List[str]:
        """Return danh sách macro drivers có ảnh hưởng đến symbol."""
        chain = self.get_chain(symbol)
        if not chain:
            return []
        return list(chain.macro_links.keys())

    def close(self):
        self._arch_engine.close()


# ═══════════════════════════════════════════════════════════════
# 3. REPORTING
# ═══════════════════════════════════════════════════════════════

def print_engine_report(results: Dict[str, list]):
    print(f"\n  {'='*80}")
    print(f"  ECONOMIC ENGINE — CHAIN OF CAUSATION")
    print(f"  {'='*80}")
    for sym, chain_list in sorted(results.items()):
        print(f"\n  📍 {sym}")
        print(f"  {'─'*60}")
        for i, step in enumerate(chain_list):
            arrow = " ↓" if i < len(chain_list) - 1 else ""
            print(f"    {i+1}. {step}{arrow}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Economic Engine — value creation mechanism")
    parser.add_argument("--symbols", nargs="+", default=[
        "FPT", "ACB", "HDB", "MBB", "VCB",
        "HPG", "VHM", "DGC", "MWG", "GAS",
    ], help="Danh sách mã")
    args = parser.parse_args()

    engine = EconomicEngine()
    print(f"\n  Khởi tạo Economic Engine — {len(args.symbols)} symbols")
    for sym in args.symbols:
        chain = engine.get_chain(sym)
        if chain:
            step_names = [c.label for c in chain.chain]
            print(f"\n  📍 {sym} ({chain.label})")
            for i, s in enumerate(step_names):
                arrow = " ↓" if i < len(step_names) - 1 else ""
                print(f"    {i+1}. {s}{arrow}")
            print(f"  Macro links: {', '.join(chain.macro_links.keys())}")
    engine.close()


if __name__ == "__main__":
    main()
