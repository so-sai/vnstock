"""factor_exposure.py — Factor Exposure Matrix (Giai đoạn 2).

Ánh xạ 7 nhóm yếu tố vĩ mô vào từng doanh nghiệp với độ nhạy (sensitivity),
hướng tác động (direction), độ tin cậy (confidence) và độ suy giảm (decay).

Khác biệt cốt lõi so với LR tĩnh:
  - CREDIT_STRESS không còn là 0.25 cho mọi symbol
  - HPG: credit_exposure = 0.85 → LR mạnh
  - FPT: credit_exposure = 0.30 → LR nhẹ
  - GAS: credit_exposure = 0.05 → gần như miễn nhiễm

Usage:
    from src.business.factor_exposure import FactorExposureEngine
    engine = FactorExposureEngine()
    matrix = engine.compute("HPG")
    print(matrix.exposures["CREDIT"]["score"])
"""

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from src.business.archetype import ArchetypeEngine, ARCHETYPE_REGISTRY
from src.business.economic_engine import EconomicEngine, PROPAGATION_CHAINS
from src.business.competitive_position import CompetitiveEngine


# ═══════════════════════════════════════════════════════════════
# 1. FACTOR TAXONOMY — 7 macro factor groups
# ═══════════════════════════════════════════════════════════════

@dataclass
class FactorDef:
    """Định nghĩa một yếu tố vĩ mô."""
    id: str
    label: str
    group: str  # liquidity / credit / risk / energy / growth / sector / trust
    description: str
    measurement: str         # nguồn dữ liệu
    default_direction: int   # +1 = tăng factor → tốt cho nền kinh tế, -1 = ngược
    is_cyclical: bool = True


FACTOR_REGISTRY: Dict[str, FactorDef] = {
    # ── Liquidity ────────────────────────
    "INTEREST_RATE": FactorDef("INTEREST_RATE", "Lãi suất điều hành", "liquidity",
                                "Lãi suất chính sách của SBV (refinancing rate)", "SBV policy rate", -1),
    "INTERBANK_ON": FactorDef("INTERBANK_ON", "Lãi suất liên ngân hàng ON", "liquidity",
                               "Thanh khoản ngắn hạn hệ thống ngân hàng", "Interbank ON", -1),
    "INTERBANK_3M": FactorDef("INTERBANK_3M", "Lãi suất liên ngân hàng 3M", "liquidity",
                               "Thanh khoản trung hạn hệ thống", "Interbank 3M", -1),
    "LIQUIDITY": FactorDef("LIQUIDITY", "Thanh khoản tổng thể", "liquidity",
                            "Chỉ số tổng hợp thanh khoản (P1)", "Transmission Engine", 1),

    # ── Credit ───────────────────────────
    "CREDIT_GROWTH": FactorDef("CREDIT_GROWTH", "Tăng trưởng tín dụng", "credit",
                                "Tăng trưởng dư nợ tín dụng toàn hệ thống (% YoY)", "SBV credit data", 1),
    "NPL_CYCLE": FactorDef("NPL_CYCLE", "Chu kỳ nợ xấu", "credit",
                            "Áp lực nợ xấu hệ thống ngân hàng", "NPL ratio trend", -1),
    "CASA_RATIO": FactorDef("CASA_RATIO", "Tỷ lệ CASA hệ thống", "credit",
                             "Tỷ lệ tiền gửi không kỳ hạn hệ thống", "Bank financials", 1),

    # ── Risk ─────────────────────────────
    "DXY": FactorDef("DXY", "Chỉ số USD Index", "risk",
                      "Sức mạnh đồng USD toàn cầu", "DXY index", -1),
    "VIX": FactorDef("VIX", "Chỉ số VIX (Fear Index)", "risk",
                      "Biến động thị trường chứng khoán Mỹ", "CBOE VIX", -1),
    "USD_VND": FactorDef("USD_VND", "Tỷ giá USD/VND", "risk",
                          "Áp lực tỷ giá lên VND", "SBV central rate", -1),

    # ── Energy ───────────────────────────
    "OIL_PRICE": FactorDef("OIL_PRICE", "Giá dầu Brent", "energy",
                            "Giá dầu thô Brent (USD/thùng)", "Brent crude futures", 1),
    "COAL_PRICE": FactorDef("COAL_PRICE", "Giá than", "energy",
                             "Giá than nhiệt/than luyện kim", "Global coal index", 1),

    # ── Growth ───────────────────────────
    "GDP_GROWTH": FactorDef("GDP_GROWTH", "Tăng trưởng GDP", "growth",
                             "Tăng trưởng tổng sản phẩm quốc nội (% YoY)", "GSO data", 1),
    "CONSTRUCTION": FactorDef("CONSTRUCTION", "Đầu tư xây dựng", "growth",
                               "Hoạt động xây dựng và hạ tầng", "Construction index", 1),
    "CONSUMER_SPENDING": FactorDef("CONSUMER_SPENDING", "Chi tiêu tiêu dùng", "growth",
                                    "Sức mua của người tiêu dùng", "Retail sales", 1),
    "CHINA_DEMAND": FactorDef("CHINA_DEMAND", "Cầu Trung Quốc", "growth",
                               "Nhu cầu nhập khẩu từ Trung Quốc", "China PMI", 1),

    # ── Sector-specific ──────────────────
    "STEEL_PRICE": FactorDef("STEEL_PRICE", "Giá thép trong nước", "sector",
                              "Giá thép xây dựng/thép cuộn cán nóng (HRC)", "Steel price index", 1),
    "AI_CAPEX": FactorDef("AI_CAPEX", "Đầu tư AI toàn cầu", "sector",
                           "Chi tiêu vốn cho AI và công nghệ", "Big Tech capex", 1),
    "IT_SPENDING": FactorDef("IT_SPENDING", "Chi tiêu CNTT", "sector",
                              "Tổng chi tiêu CNTT doanh nghiệp/chính phủ", "Gartner IDC", 1),
    "HOUSING_POLICY": FactorDef("HOUSING_POLICY", "Chính sách nhà ở", "sector",
                                 "Chính sách pháp lý BĐS và giải ngân đầu tư công", "Government policy", 1),
    "GAS_VOLUME": FactorDef("GAS_VOLUME", "Sản lượng khí", "sector",
                             "Sản lượng khí tiêu thụ nội địa", "Gas consumption", 1),
    "RETAIL_SALES": FactorDef("RETAIL_SALES", "Doanh thu bán lẻ", "sector",
                               "Tổng mức bán lẻ hàng hóa", "GSO retail", 1),

    # ── Trust / Institutional ────────────
    "REGULATORY_TARIFF": FactorDef("REGULATORY_TARIFF", "Điều tiết giá", "trust",
                                    "Khả năng điều chỉnh giá của cơ quan quản lý", "Government decision", 1),
    "GOV_IT_BUDGET": FactorDef("GOV_IT_BUDGET", "Ngân sách CNTT chính phủ", "trust",
                                "Chi tiêu chính phủ cho chuyển đổi số", "State budget", 1),
    "CORP_EARNINGS": FactorDef("CORP_EARNINGS", "Lợi nhuận doanh nghiệp", "trust",
                                "Tăng trưởng lợi nhuận khối doanh nghiệp niêm yết", "Market earnings", 1),
}


# ═══════════════════════════════════════════════════════════════
# 2. EXPOSURE MATRIX — Per-symbol sensitivity
# ═══════════════════════════════════════════════════════════════

@dataclass
class FactorExposure:
    """Mức độ nhạy cảm của một doanh nghiệp với một yếu tố vĩ mô."""
    factor_id: str
    factor_label: str
    factor_group: str

    exposure_score: float   # 0.0 (miễn nhiễm) → 1.0 (cực kỳ nhạy)
    direction: int          # +1 = factor tăng → tốt cho DN, -1 = factor tăng → xấu cho DN, 0 = không rõ
    confidence: float       # 0.0 → 1.0
    decay: float            # 0.0 (tác động vĩnh viễn) → 1.0 (tác động biến mất sau 1 kỳ)

    source: str             # archetype / engine / expert
    evidence: str = ""


@dataclass
class ExposureMatrix:
    """Ma trận nhạy cảm đầy đủ cho một doanh nghiệp."""
    symbol: str
    archetype: str
    moat_score: float
    exposures: Dict[str, FactorExposure]  # factor_id → FactorExposure

    def get_exposure(self, factor_id: str) -> Optional[FactorExposure]:
        return self.exposures.get(factor_id)

    def get_group_exposure(self, group: str) -> List[FactorExposure]:
        return [e for e in self.exposures.values() if e.factor_group == group]

    def summarized(self) -> Dict[str, float]:
        """Return {factor_id: exposure_score} for quick lookup."""
        return {fid: e.exposure_score for fid, e in self.exposures.items()}


# ═══════════════════════════════════════════════════════════════
# 3. EXPOSURE ENGINE
# ═══════════════════════════════════════════════════════════════

# Expert-calibrated base exposures per archetype
# Maps: archetype → {factor_id: (score, direction, confidence, decay)}
ARCHETYPE_EXPOSURE_BASE: Dict[str, Dict[str, Tuple[float, int, float, float]]] = {
    "COMPOUNDER": {
        "INTEREST_RATE": (0.10, -1, 0.6, 0.3),
        "LIQUIDITY": (0.15, 1, 0.5, 0.4),
        "CREDIT_GROWTH": (0.10, 1, 0.4, 0.5),
        "NPL_CYCLE": (0.05, -1, 0.3, 0.6),
        "DXY": (0.15, -1, 0.5, 0.4),
        "USD_VND": (0.15, -1, 0.6, 0.3),
        "GDP_GROWTH": (0.50, 1, 0.7, 0.2),
        "AI_CAPEX": (0.80, 1, 0.8, 0.1),
        "IT_SPENDING": (0.85, 1, 0.8, 0.1),
        "GOV_IT_BUDGET": (0.70, 1, 0.7, 0.2),
        "CORP_EARNINGS": (0.60, 1, 0.6, 0.3),
        "CONSUMER_SPENDING": (0.20, 1, 0.4, 0.5),
    },
    "CYCLICAL_HEAVY": {
        "INTEREST_RATE": (0.45, -1, 0.6, 0.3),
        "LIQUIDITY": (0.25, 1, 0.5, 0.4),
        "CREDIT_GROWTH": (0.55, 1, 0.6, 0.3),
        "NPL_CYCLE": (0.10, -1, 0.3, 0.6),
        "DXY": (0.30, -1, 0.5, 0.4),
        "USD_VND": (0.25, 1, 0.5, 0.4),
        "GDP_GROWTH": (0.60, 1, 0.7, 0.2),
        "CONSTRUCTION": (0.85, 1, 0.8, 0.1),
        "STEEL_PRICE": (0.95, 1, 0.9, 0.1),
        "CHINA_DEMAND": (0.75, 1, 0.7, 0.2),
        "COAL_PRICE": (0.60, 1, 0.6, 0.3),
        "OIL_PRICE": (0.30, 1, 0.5, 0.4),
    },
    "FRANCHISE_BANK": {
        "INTEREST_RATE": (0.30, 1, 0.7, 0.2),
        "LIQUIDITY": (0.20, 1, 0.6, 0.3),
        "CREDIT_GROWTH": (0.70, 1, 0.8, 0.1),
        "NPL_CYCLE": (0.50, -1, 0.7, 0.2),
        "CASA_RATIO": (0.75, 1, 0.7, 0.1),
        "INTERBANK_ON": (0.25, -1, 0.5, 0.4),
        "INTERBANK_3M": (0.30, -1, 0.5, 0.4),
        "GDP_GROWTH": (0.50, 1, 0.6, 0.3),
        "CONSTRUCTION": (0.35, 1, 0.4, 0.5),
        "HOUSING_POLICY": (0.30, 1, 0.4, 0.5),
    },
    "ASSET_BANK": {
        "INTEREST_RATE": (0.25, 1, 0.6, 0.3),
        "LIQUIDITY": (0.25, 1, 0.5, 0.4),
        "CREDIT_GROWTH": (0.85, 1, 0.8, 0.1),
        "NPL_CYCLE": (0.60, -1, 0.7, 0.2),
        "CASA_RATIO": (0.50, 1, 0.6, 0.2),
        "INTERBANK_ON": (0.30, -1, 0.5, 0.4),
        "GDP_GROWTH": (0.55, 1, 0.6, 0.3),
        "CONSUMER_SPENDING": (0.60, 1, 0.6, 0.3),
        "HOUSING_POLICY": (0.40, 1, 0.5, 0.4),
    },
    "REAL_ESTATE_DEVELOPER": {
        "INTEREST_RATE": (0.75, -1, 0.8, 0.1),
        "LIQUIDITY": (0.40, 1, 0.5, 0.4),
        "CREDIT_GROWTH": (0.85, 1, 0.8, 0.1),
        "NPL_CYCLE": (0.30, -1, 0.5, 0.4),
        "CONSTRUCTION": (0.80, 1, 0.7, 0.2),
        "HOUSING_POLICY": (0.90, 1, 0.8, 0.1),
        "GDP_GROWTH": (0.60, 1, 0.6, 0.3),
        "DXY": (0.20, -1, 0.3, 0.6),
        "USD_VND": (0.15, -1, 0.3, 0.6),
    },
    "RETAIL_PLATFORM": {
        "INTEREST_RATE": (0.30, -1, 0.5, 0.4),
        "LIQUIDITY": (0.15, 1, 0.5, 0.4),
        "CREDIT_GROWTH": (0.40, 1, 0.5, 0.4),
        "CONSUMER_SPENDING": (0.85, 1, 0.8, 0.1),
        "RETAIL_SALES": (0.80, 1, 0.8, 0.1),
        "GDP_GROWTH": (0.60, 1, 0.7, 0.2),
        "USD_VND": (0.20, -1, 0.4, 0.5),
        "INFLATION": (0.40, -1, 0.5, 0.4),
    },
    "REIT_COMMERCIAL": {
        "INTEREST_RATE": (0.50, -1, 0.7, 0.2),
        "LIQUIDITY": (0.30, 1, 0.5, 0.4),
        "CREDIT_GROWTH": (0.35, 1, 0.5, 0.4),
        "CONSUMER_SPENDING": (0.65, 1, 0.7, 0.2),
        "RETAIL_SALES": (0.55, 1, 0.7, 0.2),
        "GDP_GROWTH": (0.50, 1, 0.6, 0.3),
        "INFLATION": (0.40, -1, 0.5, 0.4),
        "USD_VND": (0.20, -1, 0.4, 0.5),
    },
    "REGULATED_UTILITY": {
        "INTEREST_RATE": (0.40, -1, 0.6, 0.3),
        "LIQUIDITY": (0.20, 1, 0.5, 0.4),
        "OIL_PRICE": (0.50, 1, 0.7, 0.2),
        "GAS_VOLUME": (0.80, 1, 0.8, 0.1),
        "REGULATORY_TARIFF": (0.75, 1, 0.7, 0.1),
        "GDP_GROWTH": (0.30, 1, 0.5, 0.4),
        "USD_VND": (0.10, 1, 0.3, 0.6),
        "DXY": (0.10, 1, 0.3, 0.6),
    },
    "EXPORT_MANUFACTURER": {
        "INTEREST_RATE": (0.35, -1, 0.5, 0.4),
        "DXY": (0.60, 1, 0.6, 0.3),
        "USD_VND": (0.70, 1, 0.7, 0.2),
        "GDP_GROWTH": (0.50, 1, 0.6, 0.3),
        "CHINA_DEMAND": (0.50, 1, 0.6, 0.3),
        "CONSUMER_SPENDING": (0.40, 1, 0.5, 0.4),
        "OIL_PRICE": (0.30, -1, 0.5, 0.4),
    },
}


class FactorExposureEngine:
    """Tính ma trận nhạy cảm yếu tố cho từng doanh nghiệp.

    Công thức:
      adjusted_exposure = base_exposure * (1 - moat_buffering * 0.3)

    Trong đó:
      - base_exposure: từ ARCHETYPE_EXPOSURE_BASE (chuyên gia hiệu chỉnh)
      - moat_buffering: doanh nghiệp có moat càng mạnh càng ít nhạy với
        các cú sốc bên ngoài (hệ số 0.3 = 30% moat score được khấu trừ)
    """

    def __init__(self):
        self._arch_engine = ArchetypeEngine()
        self._econ_engine = EconomicEngine()
        self._comp_engine = CompetitiveEngine()

    def compute(self, symbol: str) -> ExposureMatrix:
        sym = symbol.upper().strip()
        arch = self._arch_engine.classify(sym)
        comp = self._comp_engine.assess(sym)

        moat_buffering = comp.moat_score  # 0.0-1.0
        base_exposures = ARCHETYPE_EXPOSURE_BASE.get(arch.archetype, {})

        exposures = {}
        for fid, (base_score, direction, confidence, decay) in base_exposures.items():
            factor = FACTOR_REGISTRY.get(fid)
            if not factor:
                continue
            # Moat buffering: moat mạnh → giảm exposure
            adjusted = round(base_score * max(0.1, 1.0 - moat_buffering * 0.3), 3)
            exposures[fid] = FactorExposure(
                factor_id=fid,
                factor_label=factor.label,
                factor_group=factor.group,
                exposure_score=adjusted,
                direction=direction,
                confidence=round(confidence, 2),
                decay=round(decay, 2),
                source="engine",
                evidence=f"Base={base_score}, Moat buffer={(moat_buffering*0.3):.2f} → adjusted={adjusted}",
            )

        return ExposureMatrix(
            symbol=sym,
            archetype=arch.archetype,
            moat_score=moat_buffering,
            exposures=exposures,
        )

    def compute_many(self, symbols: List[str]) -> Dict[str, ExposureMatrix]:
        return {s: self.compute(s) for s in symbols}

    def query(self, symbol: str, factor_id: str) -> Optional[FactorExposure]:
        """Query exposure của một symbol với một factor cụ thể."""
        matrix = self.compute(symbol)
        return matrix.get_exposure(factor_id.upper())

    def top_exposures(self, symbol: str, n: int = 5) -> List[FactorExposure]:
        """Top N exposures mạnh nhất (theo score)."""
        matrix = self.compute(symbol)
        sorted_exp = sorted(
            matrix.exposures.values(),
            key=lambda e: e.exposure_score * e.confidence,
            reverse=True,
        )
        return sorted_exp[:n]

    def close(self):
        self._arch_engine.close()
        self._comp_engine.close()


# ═══════════════════════════════════════════════════════════════
# 4. GOVERNOR INTEGRATION — Lấy LR adjustment từ exposure
# ═══════════════════════════════════════════════════════════════

def compute_lr_adjustment(
    matrix: ExposureMatrix,
    macro_state: str,
    transmission_phase: str,
) -> float:
    """Tính hệ số điều chỉnh LR từ exposure matrix + macro context.

    Trả về: multiplier ∈ [0.3, 1.5]
      < 1.0: macro xấu hơn so với baseline cho symbol này
      > 1.0: macro tốt hơn so với baseline

    Dùng để modulate Governor's base LR per symbol.
    """
    # Map macro context → các factor bị ảnh hưởng và hướng
    macro_factor_map = {
        "CREDIT_STRESS": {"CREDIT_GROWTH": -1, "NPL_CYCLE": 1, "INTEREST_RATE": 1},
        "LIQUIDITY_EXPANSION": {"LIQUIDITY": 1, "INTERBANK_ON": -1},
        "AI_BOOM": {"AI_CAPEX": 1, "IT_SPENDING": 1, "CORP_EARNINGS": 1},
        "INFLATION_SHOCK": {"INTEREST_RATE": 1, "DXY": 1, "USD_VND": 1},
        "RECOVERY": {"GDP_GROWTH": 1, "CONSUMER_SPENDING": 1, "CONSTRUCTION": 1},
        "STABLE": {},
        "RISK_OFF": {"DXY": 1, "VIX": 1, "USD_VND": 1},
        "PRE_CREDIT_EXPANSION": {"CREDIT_GROWTH": 1, "INTEREST_RATE": -1},
    }

    transmission_factor_map = {
        "LIQUIDITY_TRAP": {"LIQUIDITY": 1, "CREDIT_GROWTH": -1},
        "CREDIT_CRUNCH": {"CREDIT_GROWTH": -1, "NPL_CYCLE": 1},
        "HEALTHY_TRANSMISSION": {"LIQUIDITY": 1, "CREDIT_GROWTH": 1},
        "OVERHEATING": {"CREDIT_GROWTH": 1, "INTEREST_RATE": 1},
        "RISK_OFF_FLIGHT": {"DXY": 1, "USD_VND": 1, "VIX": 1},
        "FRAGILE_STABILITY": {},
    }

    # Compute impact score
    impact = 0.0
    n_factors = 0

    affected = macro_factor_map.get(macro_state, {})
    for fid, macro_dir in affected.items():
        exp = matrix.get_exposure(fid)
        if exp and exp.exposure_score > 0:
            # Nếu macro tác động cùng chiều với direction của DN → positive
            # Nếu macro tác động ngược chiều → negative
            alignment = macro_dir * exp.direction
            impact += exp.exposure_score * exp.confidence * alignment
            n_factors += 1

    affected_t = transmission_factor_map.get(transmission_phase, {})
    for fid, macro_dir in affected_t.items():
        exp = matrix.get_exposure(fid)
        if exp and exp.exposure_score > 0:
            alignment = macro_dir * exp.direction
            impact += exp.exposure_score * exp.confidence * alignment * 0.5  # transmission weight = half of macro
            n_factors += 1

    if n_factors == 0:
        return 1.0

    avg_impact = impact / n_factors
    # Convert impact [-1, 1] to multiplier [0.3, 1.5]
    multiplier = 1.0 + avg_impact * 0.5
    multiplier = max(0.3, min(1.5, multiplier))

    return round(multiplier, 3)


# ═══════════════════════════════════════════════════════════════
# 5. REPORTING
# ═══════════════════════════════════════════════════════════════

def print_exposure_report(results: Dict[str, ExposureMatrix], top_n: int = 5):
    print(f"\n  {'='*80}")
    print(f"  FACTOR EXPOSURE MATRIX — Giai đoạn 2")
    print(f"  {'='*80}")

    for sym, matrix in sorted(results.items()):
        print(f"\n  📍 {sym} ({matrix.archetype}) | Moat={matrix.moat_score:.3f}")
        print(f"  {'─'*60}")
        sorted_exp = sorted(
            matrix.exposures.values(),
            key=lambda e: e.exposure_score * e.confidence,
            reverse=True,
        )[:top_n]
        print(f"  {'Factor':<24} {'Nhóm':<12} {'Score':>6} {'Hướng':>5} {'Tin cậy':>7} {'Suy giảm':>7}")
        print(f"  {'─'*60}")
        for e in sorted_exp:
            dir_str = "+" if e.direction > 0 else ("-" if e.direction < 0 else "=")
            print(f"  {e.factor_label:<24} {e.factor_group:<12} {e.exposure_score:>6.3f} "
                  f"{dir_str:>5} {e.confidence:>6.2f}  {e.decay:>6.2f}")

    # Group summary
    print(f"\n  {'='*80}")
    print(f"  TỔNG HỢP NHÓM YẾU TỐ")
    print(f"  {'='*80}")
    for sym, matrix in sorted(results.items()):
        groups: Dict[str, List[FactorExposure]] = {}
        for e in matrix.exposures.values():
            groups.setdefault(e.factor_group, []).append(e)
        avg_by_group = {
            g: sum(x.exposure_score for x in lst) / len(lst)
            for g, lst in groups.items()
        }
        print(f"  {sym:<6} | "
              + " | ".join(f"{g}:{avg_by_group[g]:.2f}" for g in sorted(avg_by_group)))


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Factor Exposure Matrix — Giai đoạn 2")
    parser.add_argument("--symbols", nargs="+", default=[
        "FPT", "ACB", "HDB", "MBB", "VCB",
        "HPG", "VHM", "DGC", "MWG", "GAS",
    ], help="Danh sách mã")
    parser.add_argument("--top", type=int, default=5, help="Số factor hiển thị")
    parser.add_argument("--query", type=str, default=None,
                        help="Factor ID để query (VD: INTEREST_RATE)")
    parser.add_argument("--lr-adjust", action="store_true",
                        help="Tính LR adjustment cho macro state hiện tại")
    args = parser.parse_args()

    engine = FactorExposureEngine()

    if args.query:
        for sym in args.symbols:
            exp = engine.query(sym, args.query)
            if exp:
                print(f"\n  {sym}: {exp.factor_label} = {exp.exposure_score:.3f}")
            else:
                print(f"\n  {sym}: Không có exposure cho {args.query}")
        engine.close()
        return

    if args.lr_adjust:
        # Simulate current macro context
        try:
            from src.core.macro.macro_state_classifier import MacroStateClassifier
            ms = MacroStateClassifier()
            state = ms.classify()
            macro = state.macro_state
        except Exception:
            macro = "CREDIT_STRESS"
        try:
            from src.core.macro.economic_transmission_engine import EconomicTransmissionEngine
            te = EconomicTransmissionEngine()
            ts = te.compute()
            trans = ts.transmission_phase
        except Exception:
            trans = "LIQUIDITY_TRAP"
        print(f"\n  {'='*80}")
        print(f"  LR ADJUSTMENT — Macro={macro} | Transmission={trans}")
        print(f"  {'='*80}")
        print(f"  {'Symbol':<6} {'Archetype':<20} {'Base LR':>8} {'Multiplier':>10} {'Adjusted LR':>12}")
        print(f"  {'─'*60}")
        for sym in args.symbols:
            matrix = engine.compute(sym)
            mult = compute_lr_adjustment(matrix, macro, trans)
            print(f"  {sym:<6} {matrix.archetype:<20} {1.0:>8.2f} {mult:>10.3f} {1.0*mult:>11.3f}")
        engine.close()
        return

    results = engine.compute_many(args.symbols)
    print_exposure_report(results, args.top)
    engine.close()


if __name__ == "__main__":
    main()
