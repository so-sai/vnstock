"""company_health.py — Giai đoạn 3: Contextualized Company Health.

Thay thế ngưỡng tài chính cố định (static limits) bằng ngưỡng động
(Archetype-Aware Dynamic Thresholds) dựa trên DNA Business Archetype.

Cách hoạt động:
  1. Đọc archetype của doanh nghiệp (Giai đoạn 1)
  2. Lấy các thuộc tính DNA (operating_leverage, revenue_model, v.v.)
  3. Áp dụng luật điều chỉnh ngưỡng cho từng chỉ số tài chính
  4. So sánh giá trị thực tế với ngưỡng đã điều chỉnh → contextual score

Ví dụ:
  - D/E = 1.5x: FPT (COMPOUNDER, financial_leverage=LOW) → BAD
                 HPG (CYCLICAL_HEAVY, financial_leverage=HIGH) → GOOD
  - CFO/NI = 0.6x: MWG (RETAIL, revenue_model=TRANSACTIONAL) → WARNING
                    VHM (REAL_ESTATE, revenue_model=PROJECT_BASED) → GOOD

Usage:
    from src.financial.company_health import ContextualHealthEngine
    engine = ContextualHealthEngine()
    result = engine.assess("HPG")
    print(result.overall_score, result.flags)
"""

# WHY: Module này thay thế ngưỡng tài chính TĨNH bằng ngưỡng ĐỘNG theo DNA archetype.
# Một bộ ngưỡng cố định không phân biệt được "xấu vì cấu trúc vốn" với "bình thường
# theo mô hình kinh doanh": D/E=1.5x với FPT (low-leverage) là BAD nhưng với HPG
# (high-leverage cyclical) là GOOD. Điều chỉnh ngưỡng theo archetype giúp điểm health
# phản ánh đúng bản chất từng ngành thay vì so sánh chung giữa các mô hình khác nhau.

import sqlite3
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from src.business.archetype import (
    ARCHETYPE_REGISTRY,
    ArchetypeEngine,
    BusinessArchetype,
    Cyclicality,
    OperatingLeverage,
    PricingPower,
    RevenueModel,
)

# ═══════════════════════════════════════════════════════════════
# 1. STATIC THRESHOLD DEFINITIONS (base values)
# ═══════════════════════════════════════════════════════════════


@dataclass
class Threshold:
    good: float
    warning: float
    bad: float
    inverted: bool = False  # True = lower is better (e.g., D/E)

    def evaluate(self, value: float) -> str:
        if value is None:
            return "NEUTRAL"
        # WHY: inverted=True dùng cho chỉ số "thấp hơn thì tốt hơn" (D/E, CAPEX/CFO,...).
        # Đảo hướng so sánh để cùng cấu trúc (good → warning → bad) áp dụng cho cả 2 chiều,
        # tránh viết riêng 2 bộ logic đánh giá.
        if self.inverted:
            if value <= self.good:
                return "GOOD"
            elif value <= self.warning:
                return "WARNING"
            elif value <= self.bad:
                return "WARNING"
            else:
                return "BAD"
        else:
            if value >= self.good:
                return "GOOD"
            elif value >= self.warning:
                return "WARNING"
            elif value >= self.bad:
                return "WARNING"
            else:
                return "BAD"

    def score(self, value: float) -> float:
        """Convert value to [0, 1] score using linear interpolation."""
        if value is None:
            return 0.5
        # WHY: Nội suy tuyến tính giữa good↔bad để chuẩn hoá mọi chỉ số về cùng thang [0,1],
        # cho phép tính overall_score là trung bình cộng giữa các metric có đơn vị khác nhau
        # mà không bị lệch trọng số do đơn vị. missing value → 0.5 (NEUTRAL, không phạt).
        if self.inverted:
            if value <= self.good:
                return 1.0
            elif value >= self.bad:
                return 0.0
            else:
                return 1.0 - (value - self.good) / (self.bad - self.good)
        else:
            if value >= self.good:
                return 1.0
            elif value <= self.bad:
                return 0.0
            else:
                return (value - self.bad) / (self.good - self.bad)


# Base thresholds (neutral archetype)
# WHY: Đây là các giá trị NỀN cho archetype "trung tính" (multiplier=1, offset=0), được
# hiệu chỉnh tương đối (nhân/cộng) trong ADJUSTMENT_MATRIX. Chọn kiểu relative thay vì
# hardcode từng ngưỡng theo từng archetype để giữ 1 nguồn chân lý duy nhất, dễ thêm DN mới.
BASE_THRESHOLDS: dict[str, Threshold] = {
    "DEBT_TO_EQUITY": Threshold(0.50, 1.50, 3.00, inverted=True),
    "DEBT_TO_ASSETS": Threshold(0.30, 0.50, 0.70, inverted=True),
    "INTEREST_COVERAGE": Threshold(5.00, 2.00, 1.00),
    "CFO_TO_NET_INCOME": Threshold(1.00, 0.50, 0.00),
    "NET_MARGIN": Threshold(0.10, 0.04, 0.00),
    "GROSS_MARGIN": Threshold(0.30, 0.12, 0.00),
    "ROE": Threshold(0.15, 0.08, 0.00),
    "ROA": Threshold(0.05, 0.02, 0.00),
    "CURRENT_RATIO": Threshold(1.50, 1.00, 0.50),
    "QUICK_RATIO": Threshold(1.00, 0.50, 0.20),
    "ASSET_TURNOVER": Threshold(1.00, 0.50, 0.20),
    "RECEIVABLES_TO_REVENUE": Threshold(0.20, 0.40, 0.70, inverted=True),
    "INVENTORY_TO_REVENUE": Threshold(0.12, 0.25, 0.50, inverted=True),
    "FCF_TO_NET_INCOME": Threshold(0.50, 0.00, -0.50),
    "CAPEX_TO_CFO": Threshold(0.30, 0.60, 1.00, inverted=True),
}


# ═══════════════════════════════════════════════════════════════
# 2. ARCHETYPE-DRIVEN ADJUSTMENT RULES
# ═══════════════════════════════════════════════════════════════


@dataclass
class AdjRule:
    """Một luật điều chỉnh ngưỡng dựa trên DNA attribute."""

    multiplier: float = 1.0
    offset: float = 0.0


# Mỗi metric có một dict: attribute_value → AdjRule
# WHY: Chọn dict keyed by attribute value (enum/string) thay vì if/else dài để lookup theo
# từng DNA attribute là O(1) và quy tắc của mỗi metric gom về 1 chỗ, dễ đọc/dễ thêm rule.
# multiplier dùng để scale ngưỡng theo cường độ (margin, leverage...), offset để dịch
# ngưỡng tuyệt đối cho khác biệt cấu trúc báo cáo (vd accrual-heavy của ngân hàng).
ADJUSTMENT_MATRIX = {
    "DEBT_TO_EQUITY": {
        OperatingLeverage.LOW: AdjRule(multiplier=0.5),  # COMPOUNDER: stricter
        OperatingLeverage.HIGH: AdjRule(multiplier=1.5),  # CYCLICAL: looser
        Cyclicality.HIGH: AdjRule(multiplier=1.5),  # cyclical peaks
        "capex_high": AdjRule(multiplier=1.3),  # high capex needs debt
    },
    "INTEREST_COVERAGE": {
        OperatingLeverage.LOW: AdjRule(multiplier=1.5),  # need more buffer
        OperatingLeverage.HIGH: AdjRule(multiplier=0.7),  # less buffer acceptable
        Cyclicality.HIGH: AdjRule(multiplier=1.3),  # earnings dip
    },
    "CFO_TO_NET_INCOME": {
        RevenueModel.PROJECT_BASED: AdjRule(offset=-0.3),  # RE: project phasing
        RevenueModel.RECURRING: AdjRule(offset=+0.2),  # SaaS/IT: high conversion
        RevenueModel.SPREAD_BASED: AdjRule(offset=-0.1),  # bank: accrual heavy
    },
    "NET_MARGIN": {
        PricingPower.HIGH: AdjRule(multiplier=1.8),  # moat → higher margin
        PricingPower.NONE: AdjRule(multiplier=0.4),  # commodity → thin
        "commodity_high": AdjRule(multiplier=0.5),  # commodity dependency
    },
    "GROSS_MARGIN": {
        PricingPower.HIGH: AdjRule(multiplier=1.5),
        PricingPower.NONE: AdjRule(multiplier=0.5),
        "commodity_high": AdjRule(multiplier=0.5),
    },
    "ROE": {
        OperatingLeverage.HIGH: AdjRule(offset=-0.03),  # high leverage inflates ROE
        Cyclicality.HIGH: AdjRule(offset=-0.03),  # peak earnings bias
    },
    "CURRENT_RATIO": {
        OperatingLeverage.LOW: AdjRule(multiplier=1.3),  # asset-light: higher cash
        RevenueModel.TRANSACTIONAL: AdjRule(multiplier=0.5),  # retail: fast turnover
    },
    "ASSET_TURNOVER": {
        "capex_high": AdjRule(multiplier=0.5),  # heavy assets → low turnover
        RevenueModel.PROJECT_BASED: AdjRule(multiplier=0.3),  # RE: slow
    },
    "RECEIVABLES_TO_REVENUE": {
        RevenueModel.PROJECT_BASED: AdjRule(multiplier=1.5),  # RE: longer receivable
        RevenueModel.TRANSACTIONAL: AdjRule(multiplier=0.5),  # retail: cash
    },
    "CAPEX_TO_CFO": {
        "capex_high": AdjRule(multiplier=1.5),  # naturally high capex
        OperatingLeverage.LOW: AdjRule(multiplier=0.5),  # asset-light: low capex
    },
}


# ═══════════════════════════════════════════════════════════════
# 3. CONTEXTUAL HEALTH ENGINE
# ═══════════════════════════════════════════════════════════════


@dataclass
class MetricAssessment:
    name: str
    label: str
    raw_value: float
    adjusted_threshold: Threshold
    base_threshold: Threshold
    interpretation: str  # GOOD / WARNING / BAD / NEUTRAL
    score: float  # 0.0 - 1.0
    archetype_adjustment: str  # description of what was adjusted

    def __post_init__(self):
        self._label = self.label


@dataclass
class ContextualHealthResult:
    symbol: str
    archetype: str
    archetype_label: str
    entity_type: str
    period: str
    metrics: dict[str, MetricAssessment]
    overall_score: float  # weighted average of metric scores
    overall_label: str  # HEALTHY / MODERATE / WEAK / CRITICAL
    flags: list[str]  # warnings specific to this archetype

    def __post_init__(self, *args, **kwargs):
        self._symbol = self.symbol

    def print_report(self):
        print(f"\n  {'=' * 65}")
        print("  CONTEXTUALIZED HEALTH — Giai đoạn 3")
        print(f"  {'=' * 65}")
        print(f"  {self.symbol:<6} | Archetype: {self.archetype_label}")
        print(f"  {'=' * 65}")
        print(f"  Overall: {self.overall_score:.3f} ({self.overall_label})")
        if self.flags:
            for f in self.flags:
                print(f"  ⚠ {f}")
        print(f"  {'─' * 65}")
        print(f"  {'Metric':<28} {'Value':>8} {'Score':>6} {'Adj.Threshold':>14} {'Base':>10}")
        print(f"  {'─' * 65}")
        for m in sorted(self.metrics.values(), key=lambda x: x.score):
            ico = {"GOOD": "🟢", "WARNING": "🟡", "BAD": "🔴", "NEUTRAL": "⚪"}
            val_str = f"{m.raw_value:.3f}" if m.raw_value is not None else "N/A"
            gt = m.adjusted_threshold.good
            bt = m.adjusted_threshold.bad
            th_str = f"{bt:.2f}-{gt:.2f}" if not m.adjusted_threshold.inverted else f"{gt:.2f}-{bt:.2f}"
            base_str = f"{m.base_threshold.good:.1f}/{m.base_threshold.bad:.1f}"
            print(f"  {ico.get(m.interpretation, '⚪')} {m.name:<26} {val_str:>8} {m.score:>6.2f} {th_str:>14} {base_str:>10}")
        print(f"  {'─' * 65}")


class ContextualHealthEngine:
    """Đánh giá sức khỏe tài chính theo ngữ cảnh archetype."""

    def __init__(self):
        self._arch_engine = ArchetypeEngine()
        self._db_path = Path(__file__).resolve().parent.parent.parent / "data" / "financial_facts.db"

    def assess(self, symbol: str) -> ContextualHealthResult | None:
        sym = symbol.upper().strip()

        # 1. Get archetype
        arch_result = self._arch_engine.classify(sym)
        if not arch_result:
            return None
        arch_name = arch_result.archetype
        arch_def = ARCHETYPE_REGISTRY.get(arch_name)
        if not arch_def:
            return None

        # 2. Get financial data
        ratios = self._load_ratios(sym)
        if not ratios:
            return None

        # 3. Compute contextual thresholds per metric
        metrics: dict[str, MetricAssessment] = {}
        overall = 0.0
        n_scored = 0
        flags = []

        for metric_name, base_th in BASE_THRESHOLDS.items():
            raw_val = ratios.get(metric_name)
            if raw_val is None:
                continue

            # Compute adjusted threshold
            adj_th = self._adjust_threshold(metric_name, base_th, arch_def)

            # Evaluate
            interp = adj_th.evaluate(raw_val)
            score = adj_th.score(raw_val)

            # Generate flags for BAD metrics in context
            if interp == "BAD":
                if metric_name in ("DEBT_TO_EQUITY", "DEBT_TO_ASSETS"):
                    if arch_def.financial_leverage == OperatingLeverage.LOW:
                        flags.append(f"D/E={raw_val:.2f} cao bất thường với doanh nghiệp low-leverage")
                    else:
                        flags.append(f"D/E={raw_val:.2f} — cần theo dõi")
                elif metric_name == "CFO_TO_NET_INCOME":
                    if arch_def.revenue_model == RevenueModel.PROJECT_BASED:
                        pass  # RE can have low CFO during build
                    else:
                        flags.append(f"CFO/NI={raw_val:.2f} — dòng tiền thấp hơn lợi nhuận")
                elif metric_name == "INTEREST_COVERAGE":
                    flags.append(f"ICR={raw_val:.2f} — rủi ro thanh toán lãi vay")
                elif metric_name == "NET_MARGIN":
                    if arch_def.pricing_power == PricingPower.HIGH:
                        flags.append(f"Biên={raw_val:.1%} quá thấp so với định vị pricing power cao")

            # Build description of adjustment
            adj_desc = self._describe_adjustment(metric_name, arch_def)

            metrics[metric_name] = MetricAssessment(
                name=metric_name,
                label=BASE_THRESHOLDS[metric_name].__class__.__name__,
                raw_value=raw_val,
                adjusted_threshold=adj_th,
                base_threshold=base_th,
                interpretation=interp,
                score=round(score, 3),
                archetype_adjustment=adj_desc,
            )
            overall += score
            n_scored += 1

        if n_scored == 0:
            return None

        overall_score = overall / n_scored
        # WHY: Ngưỡng 0.70/0.45/0.25 chọn theo phân phối thực tế của overall score trung bình
        # các metric (mỗi score trong [0,1]): 0.70≈tốt đồng đều, 0.45≈trung bình ngành,
        # dưới 0.25≈suy yếu nghiêm trọng. Nhãn HEALTHY/MODERATE/WEAK/CRITICAL để báo cáo
        # trực quan, giữ thứ tự tương ứng với mức rủi ro tăng dần.
        if overall_score >= 0.70:
            overall_label = "HEALTHY"
        elif overall_score >= 0.45:
            overall_label = "MODERATE"
        elif overall_score >= 0.25:
            overall_label = "WEAK"
        else:
            overall_label = "CRITICAL"

        return ContextualHealthResult(
            symbol=sym,
            archetype=arch_name,
            archetype_label=arch_def.label,
            entity_type="",
            period="latest",
            metrics=metrics,
            overall_score=round(overall_score, 3),
            overall_label=overall_label,
            flags=flags,
        )

    def assess_many(self, symbols: list[str]) -> dict[str, ContextualHealthResult | None]:
        return {s: self.assess(s) for s in symbols}

    # ── Threshold Adjustment Logic ──────────────────────────

    def _adjust_threshold(self, metric: str, base: Threshold, arch: BusinessArchetype) -> Threshold:
        """Compute archetype-aware threshold from base + DNA rules."""
        rules = ADJUSTMENT_MATRIX.get(metric, {})
        mult = 1.0
        offset = 0.0

        # WHY: Nhân các multiplier với nhau (chứ không cộng) vì mỗi rule scale theo cấp số
        # nhân của "mức độ chịu đựng" (2 rule cùng multiplier 1.5 → 2.25x, đúng bản chất
        # rủi ro chồng nhau); offset thì cộng dồn vì là dịch chuyển tuyệt đối trên cùng thang.
        for attr_val, rule in rules.items():
            matched = False
            # Match enum values
            if isinstance(attr_val, Enum):
                # Check common DNA attributes
                for dna_attr in [
                    arch.revenue_model,
                    arch.operating_leverage,
                    arch.financial_leverage,
                    arch.cyclicality,
                    arch.pricing_power,
                ]:
                    if attr_val == dna_attr:
                        matched = True
                        break
            elif attr_val == "capex_high" and arch.capex_intensity == "HIGH":
                matched = True
            elif attr_val == "commodity_high" and arch.commodity_dependency in ("HIGH", "MEDIUM"):
                matched = True

            if matched:
                mult *= rule.multiplier
                offset += rule.offset

        return Threshold(
            good=base.good * mult + offset,
            warning=base.warning * mult + offset,
            bad=base.bad * mult + offset,
            inverted=base.inverted,
        )

    def _describe_adjustment(self, metric: str, arch: BusinessArchetype) -> str:
        """Human-readable description of the adjustment applied."""
        rules = ADJUSTMENT_MATRIX.get(metric, {})
        parts = []
        for attr_val, rule in rules.items():
            matched = False
            if isinstance(attr_val, Enum):
                for dna_attr in [
                    arch.revenue_model,
                    arch.operating_leverage,
                    arch.financial_leverage,
                    arch.cyclicality,
                    arch.pricing_power,
                ]:
                    if attr_val == dna_attr:
                        matched = True
                        dna_name = type(dna_attr).__name__.replace("_", " ")
                        break
            elif attr_val == "capex_high" and arch.capex_intensity == "HIGH":
                matched = True
                dna_name = "capex_intensity=HIGH"
            elif attr_val == "commodity_high" and arch.commodity_dependency in ("HIGH", "MEDIUM"):
                matched = True
                dna_name = "commodity_dependency"
            if matched:
                adj = []
                if rule.multiplier != 1.0:
                    adj.append(f"x{rule.multiplier:.1f}")
                if rule.offset != 0.0:
                    adj.append(f"{rule.offset:+.1f}")
                if adj:
                    parts.append(f"{dna_name} ({','.join(adj)})")
        return "; ".join(parts) if parts else "base (no adjustment)"

    # ── Data Loading ────────────────────────────────────────

    def _load_ratios(self, symbol: str) -> dict[str, float] | None:
        """Load latest ratios for a symbol from health_ratios table."""
        db = str(self._db_path)
        try:
            conn = sqlite3.connect(db)
            # Get latest period
            row = conn.execute("SELECT MAX(period) FROM health_ratios WHERE symbol=?", (symbol,)).fetchone()
            if not row or not row[0]:
                conn.close()
                return None
            period = row[0]

            # Load all ratios for that period
            rows = conn.execute(
                "SELECT ratio_name, ratio_value FROM health_ratios WHERE symbol=? AND period=?", (symbol, period)
            ).fetchall()
            conn.close()

            return {r[0]: r[1] for r in rows if r[1] is not None}
        except sqlite3.Error, TypeError, ValueError, KeyError, IndexError:
            return None

    def close(self):
        self._arch_engine.close()


# ═══════════════════════════════════════════════════════════════
# 4. CLI ENTRY POINT
# ═══════════════════════════════════════════════════════════════


def print_contextual_report(results: dict[str, ContextualHealthResult | None]):
    print(f"\n  {'=' * 65}")
    print("  CONTEXTUALIZED COMPANY HEALTH — Giai đoạn 3 (Archetype-Aware)")
    print(f"  {'=' * 65}")
    for sym, result in sorted(results.items()):
        if not result:
            print(f"\n  {sym}: ⚠ No data")
            continue
        result.print_report()

    # Summary comparison
    print(f"\n  {'=' * 65}")
    print("  SO SÁNH TỔNG THỂ")
    print(f"  {'=' * 65}")
    print(f"  {'Symbol':<6} {'Archetype':<25} {'Score':>6} {'Label':<10}")
    print(f"  {'─' * 50}")
    for sym, result in sorted(results.items()):
        if result:
            print(f"  {sym:<6} {result.archetype_label:<25} {result.overall_score:>6.2f} {result.overall_label:<10}")
    print(f"  {'─' * 50}")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Contextualized Company Health — Giai đoạn 3")
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

    engine = ContextualHealthEngine()
    results = engine.assess_many(args.symbols)
    print_contextual_report(results)
    engine.close()


if __name__ == "__main__":
    main()
