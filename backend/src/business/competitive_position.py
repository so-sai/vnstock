"""competitive_position.py — Ecosystem Strength, Moat & Pricing Power.

Định nghĩa vị thế cạnh tranh của doanh nghiệp trong hệ sinh thái ngành.
Khác với Archetype (định nghĩa "loài"), Competitive Position định nghĩa
"sức mạnh tương đối" — cùng là BANK nhưng VCB có competitive position khác ACB.

Usage:
    from src.business.competitive_position import CompetitiveEngine
    engine = CompetitiveEngine()
    pos = engine.assess("VCB")
    print(pos.moat_score, pos.market_share_rank)
"""

import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from src.business.archetype import ArchetypeEngine, ARCHETYPE_REGISTRY, BusinessArchetype

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
# 1. COMPETITIVE POSITION SCHEMA
# ═══════════════════════════════════════════════════════════════

@dataclass
class MoatAssessment:
    """Đánh giá từng loại hào kinh tế."""
    type: str
    label: str
    score: float      # 0.0 – 1.0
    evidence: str     # ngắn gọn, định tính
    confidence: float # 0.0 – 1.0


@dataclass
class CompetitivePosition:
    """Vị thế cạnh tranh tổng thể của doanh nghiệp trong hệ sinh thái."""
    symbol: str
    archetype: str

    # ── Moat pillars ──
    switching_cost: MoatAssessment
    cost_advantage: MoatAssessment
    scale_advantage: MoatAssessment
    brand_moat: MoatAssessment
    regulatory_moat: MoatAssessment
    network_moat: MoatAssessment
    location_moat: MoatAssessment

    # ── Market position ──
    market_share_rank: int         # 1 = leader
    market_share_pct: float        # ước lượng thị phần
    customer_concentration: str    # LOW / MEDIUM / HIGH
    geographic_diversification: str # LOCAL / REGIONAL / NATIONAL / GLOBAL

    # ── Quality scores ──
    moat_score: float              # 0.0 – 1.0 tổng hợp
    pricing_power_score: float     # 0.0 – 1.0
    competitive_stability: str     # STABLE / IMPROVING / DECLINING / UNCLEAR

    # ── Raw data ──
    evidence_summary: str = ""


# ═══════════════════════════════════════════════════════════════
# 2. COMPETITIVE ENGINE
# ═══════════════════════════════════════════════════════════════

# Baseline assessment for 10 target symbols (human-validated expert judgment)
BASELINE_POSITIONS: Dict[str, dict] = {
    "FPT": {
        "switching_cost": (0.95, "Chi phí chuyển đổi nhà cung cấp CNTT rất cao (hợp đồng 3-5 năm, tích hợp sâu vào vận hành KH)"),
        "cost_advantage": (0.60, "Quy mô nhân sự >40,000 cho phép đấu thầu cạnh tranh với Accenture, nhưng chi phí lao động tăng"),
        "scale_advantage": (0.75, "Mạng lưới 60+ văn phòng tại 30+ quốc gia, quy mô top 3 Đông Nam Á"),
        "brand_moat": (0.80, "Thương hiệu CNTT số 1 Việt Nam, uy tín với khách hàng Nhật Bản, châu Âu"),
        "regulatory_moat": (0.30, "Không có rào cản pháp lý đặc biệt"),
        "network_moat": (0.40, "Hệ sinh thái Made-by-FPT có network effect hạn chế"),
        "location_moat": (0.50, "Phân bố nhân sự toàn cầu, giảm phụ thuộc vào một thị trường"),
        "market_share_rank": 1,
        "market_share_pct": 0.35,
        "customer_concentration": "LOW",
        "geographic_diversification": "GLOBAL",
        "pricing_power_score": 0.80,
        "competitive_stability": "IMPROVING",
    },
    "DGC": {
        "switching_cost": (0.50, "Khách hàng mua hóa chất theo giá spot, switching cost thấp"),
        "cost_advantage": (0.90, "Độc quyền mỏ apatit Lào Cai — nguồn phosphorus duy nhất cả nước"),
        "scale_advantage": (0.70, "Nhà sản xuất phosphorus lớn nhất Việt Nam, top 3 thế giới"),
        "brand_moat": (0.40, "Thương hiệu trong ngành hóa chất, không phải consumer brand"),
        "regulatory_moat": (0.75, "Giấy phép khai thác mỏ apatit có hạn chế"),
        "network_moat": (0.20, "Không có network effect"),
        "location_moat": (0.85, "Mỏ apatit — tài nguyên vị trí địa lý độc quyền"),
        "market_share_rank": 1,
        "market_share_pct": 0.70,
        "customer_concentration": "MEDIUM",
        "geographic_diversification": "NATIONAL",
        "pricing_power_score": 0.75,
        "competitive_stability": "STABLE",
    },
    "HPG": {
        "switching_cost": (0.30, "Thép là hàng hóa, khách hàng chọn theo giá (switching cost thấp)"),
        "cost_advantage": (0.85, "Lợi thế chi phí nhờ tích hợp dọc (từ quặng → thép cuộn), giảm nhập khẩu phôi"),
        "scale_advantage": (0.90, "Công suất 8+ triệu tấn/năm, top 1 Việt Nam, cảng nước sâu Dung Quất"),
        "brand_moat": (0.40, "Thương hiệu Hòa Phát uy tín trong xây dựng dân dụng"),
        "regulatory_moat": (0.30, "Không bảo hộ đặc biệt, tự do cạnh tranh với hàng nhập"),
        "network_moat": (0.10, "Không có network effect"),
        "location_moat": (0.80, "Khu liên hợp Dung Quất với cảng nước sâu — lợi thế logistics khó sao chép"),
        "market_share_rank": 1,
        "market_share_pct": 0.28,
        "customer_concentration": "LOW",
        "geographic_diversification": "NATIONAL",
        "pricing_power_score": 0.30,
        "competitive_stability": "STABLE",
    },
    "VCB": {
        "switching_cost": (0.60, "Khách hàng doanh nghiệp có chi phí chuyển đổi tài khoản (payroll, credit)"),
        "cost_advantage": (0.95, "Chi phí vốn thấp nhất hệ thống nhờ CASA 35%+ và thương hiệu quốc doanh"),
        "scale_advantage": (0.80, "Mạng lưới rộng, top bank Việt Nam về quy mô"),
        "brand_moat": (0.95, "Thương hiệu ngân hàng TMCP Nhà nước, uy tín số 1"),
        "regulatory_moat": (0.70, "Ngân hàng có vốn Nhà nước, được hưởng lợi từ chính sách"),
        "network_moat": (0.50, "Mạng lưới chi nhánh rộng — switching cost ở bán lẻ"),
        "location_moat": (0.30, "Không có lợi thế vị trí đặc biệt"),
        "market_share_rank": 3,
        "market_share_pct": 0.15,
        "customer_concentration": "LOW",
        "geographic_diversification": "NATIONAL",
        "pricing_power_score": 0.90,
        "competitive_stability": "STABLE",
    },
    "ACB": {
        "switching_cost": (0.55, "Khách hàng bán lẻ, switching cost trung bình"),
        "cost_advantage": (0.70, "Chi phí vốn cạnh tranh (CASA ~30%) nhưng không bằng VCB"),
        "scale_advantage": (0.60, "Top 5 ngân hàng tư nhân, mạng lưới vừa"),
        "brand_moat": (0.75, "Thương hiệu mạnh trong bán lẻ và doanh nghiệp vừa"),
        "regulatory_moat": (0.40, "Ngân hàng tư nhân, chịu áp lực cạnh tranh"),
        "network_moat": (0.40, "Chi nhánh rộng nhưng không vượt trội"),
        "location_moat": (0.20, "Không có lợi thế vị trí"),
        "market_share_rank": 5,
        "market_share_pct": 0.08,
        "customer_concentration": "LOW",
        "geographic_diversification": "NATIONAL",
        "pricing_power_score": 0.70,
        "competitive_stability": "STABLE",
    },
    "MBB": {
        "switching_cost": (0.50, "Bán lẻ, switching cost trung bình-thấp"),
        "cost_advantage": (0.65, "CASA ~25%, chi phí vốn khá"),
        "scale_advantage": (0.65, "Mạng lưới rộng nhờ hệ sinh thái MB Group"),
        "brand_moat": (0.60, "Thương hiệu quân đội uy tín, đặc biệt trong khối doanh nghiệp nhà nước"),
        "regulatory_moat": (0.35, "Ngân hàng tư nhân"),
        "network_moat": (0.65, "Hệ sinh thái MB Group (bảo hiểm, chứng khoán, quản lý quỹ) — cross-sell mạnh"),
        "location_moat": (0.20, "Không có lợi thế vị trí"),
        "market_share_rank": 4,
        "market_share_pct": 0.10,
        "customer_concentration": "LOW",
        "geographic_diversification": "NATIONAL",
        "pricing_power_score": 0.60,
        "competitive_stability": "IMPROVING",
    },
    "HDB": {
        "switching_cost": (0.40, "Chủ yếu bán lẻ qua HD SAISON, switching cost thấp"),
        "cost_advantage": (0.55, "CASA ~20%, chi phí vốn cao hơn VCB và ACB"),
        "scale_advantage": (0.50, "Mạng lưới vừa, tập trung bán lẻ"),
        "brand_moat": (0.45, "Thương hiệu đang phát triển, chưa mạnh"),
        "regulatory_moat": (0.30, "Ngân hàng tư nhân"),
        "network_moat": (0.55, "Hợp tác phân phối qua Vietjet + chuỗi bán lẻ (Thế giới Di động)"),
        "location_moat": (0.20, "Không có lợi thế vị trí"),
        "market_share_rank": 7,
        "market_share_pct": 0.05,
        "customer_concentration": "MEDIUM",
        "geographic_diversification": "NATIONAL",
        "pricing_power_score": 0.50,
        "competitive_stability": "IMPROVING",
    },
    "VHM": {
        "switching_cost": (0.30, "Bất động sản, switching cost thấp (sản phẩm so sánh được)"),
        "cost_advantage": (0.85, "Chi phí phát triển thấp nhờ quỹ đất giá rẻ từ Vingroup"),
        "scale_advantage": (0.90, "Top 1 bất động sản Việt Nam, quỹ đất đại đô thị khổng lồ"),
        "brand_moat": (0.80, "Thương hiệu Vingroup + Vinhomes ≈ chuẩn mực BĐS Việt Nam"),
        "regulatory_moat": (0.50, "Quan hệ chính quyền tốt, phê duyệt dự án nhanh hơn"),
        "network_moat": (0.60, "Hệ sinh thái Vingroup (trường học, bệnh viện, bán lẻ) tăng giá trị dự án"),
        "location_moat": (0.95, "Quỹ đất đại đô thị tại vị trí vàng (Vinhomes Ocean Park, Smart City)"),
        "market_share_rank": 1,
        "market_share_pct": 0.35,
        "customer_concentration": "LOW",
        "geographic_diversification": "NATIONAL",
        "pricing_power_score": 0.65,
        "competitive_stability": "STABLE",
    },
    "MWG": {
        "switching_cost": (0.40, "Bán lẻ điện thoại/điện máy, switching cost thấp"),
        "cost_advantage": (0.70, "Quy mô mua hàng lớn → chiết khấu cao từ nhà cung cấp"),
        "scale_advantage": (0.85, "1000+ cửa hàng, chuỗi cung ứng mạnh nhất ngành bán lẻ Việt Nam"),
        "brand_moat": (0.75, "Thế giới Di động + Điện máy Xanh = thương hiệu bán lẻ số 1"),
        "regulatory_moat": (0.20, "Không có rào cản pháp lý"),
        "network_moat": (0.30, "Mạng lưới cửa hàng tạo barrier với đối thủ"),
        "location_moat": (0.40, "Vị trí cửa hàng đắc địa, khó tìm mặt bằng tương đương"),
        "market_share_rank": 1,
        "market_share_pct": 0.40,
        "customer_concentration": "LOW",
        "geographic_diversification": "NATIONAL",
        "pricing_power_score": 0.50,
        "competitive_stability": "STABLE",
    },
    "GAS": {
        "switching_cost": (0.90, "Nhà máy điện / phân bón không thể chuyển đổi nhà cung cấp khí (hạ tầng gắn liền)"),
        "cost_advantage": (0.95, "Độc quyền hạ tầng khí tự nhiên — không đối thủ cạnh tranh trực tiếp"),
        "scale_advantage": (0.95, "Độc quyền hệ thống pipeline + nhà máy xử lý khí"),
        "brand_moat": (0.60, "Thương hiệu Nhà nước, uy tín cao"),
        "regulatory_moat": (0.95, "Độc quyền tự nhiên — giấy phép hạ tầng khí do chính phủ cấp"),
        "network_moat": (0.85, "Hệ thống pipeline là barrier cực lớn: không ai xây pipeline song song"),
        "location_moat": (0.90, "Đường ống dẫn khí từ mỏ đến nhà máy — vị trí hạ tầng phi thương mại"),
        "market_share_rank": 1,
        "market_share_pct": 0.95,
        "customer_concentration": "MEDIUM",
        "geographic_diversification": "NATIONAL",
        "pricing_power_score": 0.90,
        "competitive_stability": "STABLE",
    },
}


class CompetitiveEngine:
    """Đánh giá vị thế cạnh tranh của doanh nghiệp."""

    def __init__(self):
        self._arch_engine = ArchetypeEngine()

    def assess(self, symbol: str) -> CompetitivePosition:
        sym = symbol.upper().strip()
        arch = self._arch_engine.classify(sym)
        row = BASELINE_POSITIONS.get(sym, self._fallback_position(sym, arch))

        moats = {
            "switching_cost": MoatAssessment("SWITCHING_COST", "Chi phí chuyển đổi",
                                               row["switching_cost"][0], row["switching_cost"][1], 0.7),
            "cost_advantage": MoatAssessment("COST_ADVANTAGE", "Lợi thế chi phí",
                                              row["cost_advantage"][0], row["cost_advantage"][1], 0.7),
            "scale_advantage": MoatAssessment("SCALE", "Quy mô",
                                               row["scale_advantage"][0], row["scale_advantage"][1], 0.7),
            "brand_moat": MoatAssessment("BRAND", "Thương hiệu",
                                          row["brand_moat"][0], row["brand_moat"][1], 0.6),
            "regulatory_moat": MoatAssessment("REGULATORY", "Rào cản pháp lý",
                                               row["regulatory_moat"][0], row["regulatory_moat"][1], 0.6),
            "network_moat": MoatAssessment("NETWORK", "Hệ sinh thái",
                                            row["network_moat"][0], row["network_moat"][1], 0.5),
            "location_moat": MoatAssessment("LOCATION", "Vị trí địa lý",
                                             row["location_moat"][0], row["location_moat"][1], 0.7),
        }

        # Composite moat score (weighted)
        moat_weights = {
            "switching_cost": 0.20, "cost_advantage": 0.15, "scale_advantage": 0.12,
            "brand_moat": 0.12, "regulatory_moat": 0.12, "network_moat": 0.14,
            "location_moat": 0.15,
        }
        moat_score = sum(
            moats[k].score * moat_weights.get(k, 0.1) * moats[k].confidence
            for k in moat_weights
        )

        evidence_parts = [
            f"Chi phí chuyển đổi: {moats['switching_cost'].evidence}"
        ]
        if moats["cost_advantage"].score > 0.7:
            evidence_parts.append(f"Lợi thế chi phí: {moats['cost_advantage'].evidence}")
        if moats["regulatory_moat"].score > 0.7:
            evidence_parts.append(f"Hàng rào pháp lý: {moats['regulatory_moat'].evidence}")

        return CompetitivePosition(
            symbol=sym,
            archetype=arch.archetype,
            **moats,
            market_share_rank=row["market_share_rank"],
            market_share_pct=row["market_share_pct"],
            customer_concentration=row["customer_concentration"],
            geographic_diversification=row["geographic_diversification"],
            moat_score=round(moat_score, 3),
            pricing_power_score=row["pricing_power_score"],
            competitive_stability=row["competitive_stability"],
            evidence_summary=" | ".join(evidence_parts),
        )

    def assess_many(self, symbols: List[str]) -> Dict[str, CompetitivePosition]:
        return {s: self.assess(s) for s in symbols}

    def _fallback_position(self, symbol: str, arch: BusinessArchetype) -> dict:
        """Fallback khi symbol chưa có baseline mapping (từ data)."""
        return {
            "switching_cost": (0.50, "Ước lượng: switching cost trung bình"),
            "cost_advantage": (0.50, "Không đủ dữ liệu"),
            "scale_advantage": (0.50, "Không đủ dữ liệu"),
            "brand_moat": (0.50, "Không đủ dữ liệu"),
            "regulatory_moat": (0.30, "Không đủ dữ liệu"),
            "network_moat": (0.30, "Không đủ dữ liệu"),
            "location_moat": (0.30, "Không đủ dữ liệu"),
            "market_share_rank": 99,
            "market_share_pct": 0.01,
            "customer_concentration": "MEDIUM",
            "geographic_diversification": "NATIONAL",
            "pricing_power_score": 0.50,
            "competitive_stability": "UNCLEAR",
        }

    def close(self):
        self._arch_engine.close()


# ═══════════════════════════════════════════════════════════════
# 3. REPORTING
# ═══════════════════════════════════════════════════════════════

MOAT_ICON = {
    "SWITCHING_COST": "🔗", "COST_ADVANTAGE": "💰", "SCALE": "📐",
    "BRAND": "🏷️", "REGULATORY": "⚖️", "NETWORK": "🕸️", "LOCATION": "📍",
}


def print_competitive_report(results: Dict[str, CompetitivePosition]):
    print(f"\n  {'='*80}")
    print(f"  COMPETITIVE POSITION — MOAT & MARKET POWER")
    print(f"  {'='*80}")
    print(f"  {'Mã':<6} {'Archetype':<18} {'Moat Score':>10} {'Pricing Power':>13} "
          f"{'Rank':>5} {'Stability':<12} {'Primary Moat'}")
    print(f"  {'-'*80}")
    for sym, p in sorted(results.items()):
        primary = max(
            [(k, getattr(p, k)) for k in
             ["switching_cost", "cost_advantage", "scale_advantage",
              "brand_moat", "regulatory_moat", "network_moat", "location_moat"]],
            key=lambda x: x[1].score * x[1].confidence
        )
        icon = MOAT_ICON.get(primary[1].type, "")
        print(f"  {sym:<6} {p.archetype:<18} {p.moat_score:>10.3f} {p.pricing_power_score:>12.2f} "
              f"{p.market_share_rank:>4}  {p.competitive_stability:<12} {icon}{primary[1].label}")

    print(f"\n  {'='*80}")
    print(f"  CHI TIẾT MOAT")
    print(f"  {'='*80}")
    for sym, p in sorted(results.items()):
        print(f"\n  {'─'*60}")
        print(f"  {sym} — Moat Score: {p.moat_score:.3f}")
        print(f"  {'─'*60}")
        for m in ["switching_cost", "cost_advantage", "scale_advantage",
                   "brand_moat", "regulatory_moat", "network_moat", "location_moat"]:
            m_obj: MoatAssessment = getattr(p, m)
            bar = "▓" * int(m_obj.score * 20) + "░" * (20 - int(m_obj.score * 20))
            icon = MOAT_ICON.get(m_obj.type, " ")
            print(f"  {icon} {m_obj.label:<18} {m_obj.score:>4.2f} {bar}  {m_obj.evidence[:55]}...")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Competitive Position — Moat & Ecosystem Strength")
    parser.add_argument("--symbols", nargs="+", default=[
        "FPT", "ACB", "HDB", "MBB", "VCB",
        "HPG", "VHM", "DGC", "MWG", "GAS",
    ], help="Danh sách mã")
    args = parser.parse_args()

    engine = CompetitiveEngine()
    results = engine.assess_many(args.symbols)
    print_competitive_report(results)


if __name__ == "__main__":
    main()
