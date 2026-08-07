"""capital_allocation.py — Giai đoạn 4: Capital Allocation Quality.

Đo lường năng lực phân bổ vốn của Ban điều hành.

3 Archetypes:
  VALUE_CREATOR   : ROIC > WACC + returns capital to shareholders
  VALUE_DESTROYER : ROIC < WACC + value-destructive allocation
  CAPITAL_HOARDER : High ROIC but hoards cash, no reinvestment or payout

Đầu vào từ financial_facts.db:
  EBIT, NET_INCOME, TOTAL_ASSETS, CASH_EQUIV, TOTAL_DEBT, TOTAL_EQUITY,
  INTEREST_EXPENSE, CFO, CAPEX, SHARES_OUT (for dilution), CURRENT_LIAB

Usage:
    from src.business.capital_allocation import CapitalAllocationEngine
    engine = CapitalAllocationEngine()
    result = engine.assess("HPG")
    print(result.archetype, result.quality_score)
"""

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from src.business.archetype import ArchetypeEngine

# ═══════════════════════════════════════════════════════════════
# 1. CAPITAL ALLOCATION TAXONOMY
# ═══════════════════════════════════════════════════════════════

ALLOCATION_ARCHETYPES = {
    "VALUE_CREATOR": {
        "label": "Kiến tạo Giá trị",
        "desc": "ROIC > WACC bền vững, tái đầu tư hiệu quả, trả cổ tức/mua lại CP",
        "score_range": (0.50, 1.00),
    },
    "EFFICIENT_ALLOCATOR": {
        "label": "Phân bổ Vốn Hiệu quả",
        "desc": "ROIC > WACC, cân bằng giữa tái đầu tư và trả cổ tức",
        "score_range": (0.25, 0.70),
    },
    "CAPITAL_HOARDER": {
        "label": "Tích trữ Vốn",
        "desc": "ROIC cao nhưng giữ tiền mặt lớn, không tái đầu tư, không trả cổ tức",
        "score_range": (0.00, 0.40),
    },
    "LEVERAGED_OPTIMIZER": {
        "label": "Tối ưu Đòn bẩy",
        "desc": "ROIC cao nhờ đòn bẩy tài chính, rủi ro thanh khoản tiềm ẩn",
        "score_range": (-0.10, 0.30),
    },
    "TRANSITIONAL": {
        "label": "Chuyển đổi",
        "desc": "Đang trong giai đoạn đầu tư lớn, ROIC chưa phản ánh",
        "score_range": (-0.20, 0.20),
    },
    "VALUE_DESTROYER": {
        "label": "Hủy hoại Giá trị",
        "desc": "ROIC < WACC kéo dài, M&A ngoài ngành, pha loãng cổ phiếu",
        "score_range": (-1.00, -0.10),
    },
}


# ═══════════════════════════════════════════════════════════════
# 2. SCORING ENGINE
# ═══════════════════════════════════════════════════════════════


@dataclass
class AllocationResult:
    symbol: str
    archetype: str
    archetype_label: str
    quality_score: float  # -1.0 to +1.0
    roic: float | None
    wacc: float | None
    roic_wacc_spread: float | None
    reinvestment_rate: float | None
    fcf_yield: float | None
    dilution_trend: float | None  # negative = dilution
    leverage: float | None  # D/E
    components: dict[str, float]  # raw component scores
    flags: list[str]


class CapitalAllocationEngine:
    """Đánh giá năng lực phân bổ vốn của Ban điều hành."""

    COST_OF_EQUITY = 0.12  # Vietnam market standard

    def __init__(self):
        self._arch_engine = ArchetypeEngine()
        self._db_path = Path(__file__).resolve().parent.parent.parent / "data" / "financial_facts.db"

    def assess(self, symbol: str) -> AllocationResult | None:
        sym = symbol.upper().strip()

        # 1. Get archetype for context
        self._arch_engine.classify(sym)

        # 2. Load financial data (latest 3 periods)
        facts = self._load_facts(sym)
        if not facts:
            return None

        # 3. Compute components
        roic = self._compute_roic(facts)
        cost_of_debt = self._compute_cost_of_debt(facts)
        wacc = self._compute_wacc(facts, cost_of_debt)
        spread = roic - wacc if (roic is not None and wacc is not None) else None

        reinvest = self._compute_reinvestment_rate(facts)
        fcf_yield_val = self._compute_fcf_yield(facts)
        dilution = self._compute_dilution(facts)
        tot_debt = self._current_value(facts, "TOTAL_DEBT") or self._current_value(facts, "LONG_TERM_DEBT") or 0
        tot_equity = self._current_value(facts, "TOTAL_EQUITY") or 1
        leverage = tot_debt / max(tot_equity, 1e-6)

        # 4. Score each dimension [-1, +1]
        s_profitability = self._score_profitability(roic, wacc, spread)
        s_reinvest = self._score_reinvestment_efficiency(reinvest, spread)
        s_capital_return = self._score_capital_return(fcf_yield_val, dilution)
        s_balance = self._score_balance_sheet(leverage, facts)

        # Weighted overall score
        quality = s_profitability * 0.40 + s_reinvest * 0.25 + s_capital_return * 0.20 + s_balance * 0.15

        # 5. Classify archetype
        archetype = self._classify(quality, spread, reinvest, fcf_yield_val)
        arch_info = ALLOCATION_ARCHETYPES.get(archetype, {})

        # 6. Generate flags
        flags = self._generate_flags(spread, reinvest, dilution, leverage, fcf_yield_val)

        return AllocationResult(
            symbol=sym,
            archetype=archetype,
            archetype_label=arch_info.get("label", archetype),
            quality_score=round(quality, 3),
            roic=round(roic, 4) if roic is not None else None,
            wacc=round(wacc, 4) if wacc is not None else None,
            roic_wacc_spread=round(spread, 4) if spread is not None else None,
            reinvestment_rate=round(reinvest, 4) if reinvest is not None else None,
            fcf_yield=round(fcf_yield_val, 4) if fcf_yield_val is not None else None,
            dilution_trend=round(dilution, 4) if dilution is not None else None,
            leverage=round(leverage, 4) if leverage is not None else None,
            components={
                "profitability": round(s_profitability, 3),
                "reinvestment": round(s_reinvest, 3),
                "capital_return": round(s_capital_return, 3),
                "balance_sheet": round(s_balance, 3),
            },
            flags=flags,
        )

    def assess_many(self, symbols: list[str]) -> dict[str, AllocationResult | None]:
        return {s: self.assess(s) for s in symbols}

    # ── Financial computations ─────────────────────────────

    def _compute_roic(self, facts: dict) -> float | None:
        """ROIC = EBIT * (1-tax_rate) / (Total Assets - Cash - Current Liabilities)

        For banks (no EBIT/CASH_EQUIV), fallback to ROE as capital allocation proxy.
        """
        ebit = self._latest_value(facts, "EBIT")
        ni = self._latest_value(facts, "NET_INCOME") or self._latest_value(facts, "NET_PROFIT")
        # Bank fallback: use RORWA-like metric
        if ebit is None:
            equity = self._latest_avg(facts, "TOTAL_EQUITY", 3)
            if ni is not None and equity and equity > 0:
                return ni / equity  # ROE as ROIC proxy for banks
            return None
        ebt_approx = ebit
        tax_rate = 1.0 - (ni / ebt_approx) if (ni is not None and ebt_approx and ebt_approx != 0) else 0.20
        cash = self._latest_avg(facts, "CASH_EQUIV", 3) or self._latest_avg(facts, "CASH_AND_BALANCES", 3) or 0
        cliab = self._latest_avg(facts, "CURRENT_LIAB", 3) or 0
        assets = self._latest_avg(facts, "TOTAL_ASSETS", 3)
        if assets is None or assets <= 0:
            return None
        invested = assets - cash - cliab
        if invested <= 0:
            return ni / assets if (ni is not None and assets > 0) else None
        return ebit * (1 - max(0, min(1, tax_rate))) / invested

    def _compute_cost_of_debt(self, facts: dict) -> float | None:
        interest = self._latest_avg(facts, "INTEREST_EXPENSE", 3)
        debt = self._latest_avg(facts, "TOTAL_DEBT", 3)
        if interest is None or debt is None or debt <= 0:
            return 0.08  # default
        return min(0.15, interest / debt)

    def _compute_wacc(self, facts: dict, cost_of_debt: float | None) -> float | None:
        debt = self._latest_avg(facts, "TOTAL_DEBT", 3) or 0
        equity = self._latest_avg(facts, "TOTAL_EQUITY", 3) or 1
        total = debt + equity
        if total <= 0:
            return self.COST_OF_EQUITY
        w_d = debt / total
        w_e = equity / total
        cod = cost_of_debt or 0.08
        return w_d * cod * (1 - 0.20) + w_e * self.COST_OF_EQUITY

    def _compute_reinvestment_rate(self, facts: dict) -> float | None:
        capex = self._latest_avg(facts, "CAPEX", 3) or 0
        cfo = self._latest_avg(facts, "CFO", 3)
        if cfo is None or cfo <= 0:
            return None
        return min(2.0, capex / cfo)

    def _compute_fcf_yield(self, facts: dict) -> float | None:
        cfo = self._latest_avg(facts, "CFO", 3)
        capex = self._latest_avg(facts, "CAPEX", 3) or 0
        equity = self._latest_avg(facts, "TOTAL_EQUITY", 3)
        if cfo is None or equity is None or equity <= 0:
            return None
        fcf = cfo - capex
        return fcf / equity

    def _compute_dilution(self, facts: dict) -> float | None:
        """Negative = dilution, Positive = buyback."""
        shares = self._load_field_series(facts, "SHARES_OUT")
        if len(shares) < 2:
            return None
        newest = shares[-1][1]
        oldest = shares[0][1]
        if oldest is None or oldest <= 0 or newest is None:
            return None
        change = (newest - oldest) / oldest if oldest else 0
        return -change  # positive = buyback (good), negative = dilution (bad)

    # ── Scoring functions ──────────────────────────────────

    def _score_profitability(self, roic: float | None, wacc: float | None, spread: float | None) -> float:
        if spread is None:
            return 0.0
        # spread > 5% → +1.0, spread > 0% → +0.5, spread = 0 → 0, spread < 0 → -0.5 to -1.0
        if spread >= 0.05:
            return 1.0
        if spread >= 0.02:
            return 0.5
        if spread >= 0.00:
            return 0.1
        if spread >= -0.03:
            return -0.3
        return -0.8

    def _score_reinvestment_efficiency(self, reinvest: float | None, spread: float | None) -> float:
        if reinvest is None:
            return 0.0
        # Low reinvestment + positive spread → efficient (don't need much)
        # High reinvestment + negative spread → destroying value
        if reinvest < 0.2:
            return 0.3 if (spread is not None and spread > 0) else -0.2
        if reinvest < 0.5:
            return 0.5 if (spread is not None and spread > 0) else -0.1
        if reinvest < 1.0:
            return 0.8 if (spread is not None and spread > 0.02) else -0.3
        return -0.5  # excessive reinvestment without return

    def _score_capital_return(self, fcf_yield: float | None, dilution: float | None) -> float:
        score = 0.0
        if fcf_yield is not None:
            if fcf_yield > 0.10:
                score += 0.5
            elif fcf_yield > 0.05:
                score += 0.3
            elif fcf_yield > 0.00:
                score += 0.1
            else:
                score -= 0.3
        if dilution is not None:
            if dilution > 0.02:
                score += 0.4  # buyback
            elif dilution > 0.00:
                score += 0.2
            elif dilution > -0.02:
                score -= 0.1
            else:
                score -= 0.4  # dilution
        return max(-1.0, min(1.0, score))

    def _score_balance_sheet(self, leverage: float | None, facts: dict) -> float:
        if leverage is None:
            return 0.0
        ic = self._latest_value(facts, "INTEREST_EXPENSE")
        ebit = self._latest_value(facts, "EBIT")
        icr = ebit / ic if (ic is not None and ebit is not None and ic != 0) else None
        score = 0.0
        if leverage < 0.3:
            score += 0.4
        elif leverage < 0.8:
            score += 0.3
        elif leverage < 1.5:
            score += 0.0
        elif leverage < 2.5:
            score -= 0.3
        else:
            score -= 0.6
        if icr is not None:
            if icr > 5:
                score += 0.3
            elif icr > 2:
                score += 0.1
            else:
                score -= 0.3
        return max(-1.0, min(1.0, score))

    def _classify(self, quality: float, spread: float | None, reinvest: float | None, fcf_yield: float | None) -> str:
        if spread is not None and spread > 0.03 and quality > 0.4:
            if fcf_yield is not None and fcf_yield > 0.05:
                return "VALUE_CREATOR"
            return "EFFICIENT_ALLOCATOR"
        if spread is not None and spread > 0.03 and quality > 0.1:
            if reinvest is not None and reinvest < 0.2:
                return "CAPITAL_HOARDER"
            return "EFFICIENT_ALLOCATOR"
        if spread is not None and spread > 0:
            return "LEVERAGED_OPTIMIZER"
        if quality > -0.2:
            return "TRANSITIONAL"
        return "VALUE_DESTROYER"

    def _generate_flags(
        self,
        spread: float | None,
        reinvest: float | None,
        dilution: float | None,
        leverage: float | None,
        fcf_yield: float | None,
    ) -> list[str]:
        flags = []
        if spread is not None:
            if spread < -0.03:
                flags.append(f"ROIC thấp hơn WACC {spread:.1%} — giá trị bị hủy")
            elif spread > 0.05:
                flags.append(f"ROIC vượt WACC {spread:.1%} — giá trị được tạo")
        if reinvest is not None and reinvest > 1.0:
            flags.append(f"CAPEX/CFO={reinvest:.1f}x — tái đầu tư rất cao")
        if dilution is not None and dilution < -0.03:
            flags.append(f"Pha loãng CP {dilution:.1%} — huy động vốn từ cổ đông")
        if dilution is not None and dilution > 0.03:
            flags.append(f"Mua lại CP {dilution:.1%} — trả tiền cho cổ đông")
        if leverage is not None:
            if leverage > 2.0:
                flags.append(f"D/E={leverage:.1f}x — đòn bẩy cao")
            elif leverage < 0.1:
                flags.append(f"D/E={leverage:.1f}x — không dùng đòn bẩy")
        if fcf_yield is not None and fcf_yield > 0.15:
            flags.append(f"FCF/Equity={fcf_yield:.1%} — dòng tiền dồi dào")
        return flags

    # ── Data helpers ───────────────────────────────────────

    def _load_facts(self, symbol: str) -> dict | None:
        try:
            conn = sqlite3.connect(str(self._db_path))
            rows = conn.execute(
                "SELECT period, metric, value FROM financial_facts WHERE symbol=? ORDER BY period", (symbol,)
            ).fetchall()
            conn.close()
            if not rows:
                return None
            facts: dict = {}
            for period, metric, value in rows:
                if value is None:
                    continue
                if metric not in facts:
                    facts[metric] = []
                facts[metric].append((period, value))
            return facts
        except Exception:  # noqa: BLE001 - cố ý bắt rộng để fallback/phòng thủ an toàn
            return None

    @staticmethod
    def _current_value(facts: dict, metric: str) -> float | None:
        series = facts.get(metric)
        return series[-1][1] if series else None

    @staticmethod
    def _latest_value(facts: dict, metric: str) -> float | None:
        return CapitalAllocationEngine._current_value(facts, metric)

    @staticmethod
    def _latest_avg(facts: dict, metric: str, n: int = 3) -> float | None:
        series = facts.get(metric)
        if not series:
            return None
        vals = [v for _, v in series[-n:]]
        return sum(vals) / len(vals) if vals else None

    @staticmethod
    def _load_field_series(facts: dict, metric: str) -> list[tuple[str, float]]:
        return facts.get(metric, [])

    def close(self):
        self._arch_engine.close()


# ═══════════════════════════════════════════════════════════════
# 3. REPORTING
# ═══════════════════════════════════════════════════════════════


def print_allocation_report(results: dict[str, AllocationResult | None]):
    print(f"\n  {'=' * 65}")
    print("  CAPITAL ALLOCATION QUALITY — Giai đoạn 4")
    print(f"  {'=' * 65}")

    for sym, res in sorted(results.items()):
        if not res:
            print(f"\n  {sym}: ⚠ No data")
            continue

        # Determine icon
        if res.archetype == "VALUE_CREATOR":
            icon = "🟢"
        elif res.archetype == "EFFICIENT_ALLOCATOR":
            icon = "🟢"
        elif res.archetype == "CAPITAL_HOARDER":
            icon = "🟡"
        elif res.archetype == "LEVERAGED_OPTIMIZER":
            icon = "🟡"
        elif res.archetype == "TRANSITIONAL":
            icon = "🔵"
        else:
            icon = "🔴"

        print(f"\n  {icon} {res.symbol} | {res.archetype_label}")
        print(f"  {'─' * 60}")
        print(f"  Quality Score:      {res.quality_score:>+7.3f}")
        print(f"  ROIC:               {res.roic:>7.1%}" if res.roic is not None else "  ROIC:               N/A")
        print(f"  WACC:               {res.wacc:>7.1%}" if res.wacc is not None else "  WACC:               N/A")
        print(
            f"  ROIC−WACC:          {res.roic_wacc_spread:>+7.1%}"
            if res.roic_wacc_spread is not None
            else "  ROIC−WACC:          N/A"
        )
        print(
            f"  Reinvest (Capex/CFO): {res.reinvestment_rate:>7.2f}x"
            if res.reinvestment_rate is not None
            else "  Reinvest:           N/A"
        )
        print(f"  FCF/Equity:         {res.fcf_yield:>+7.1%}" if res.fcf_yield is not None else "  FCF/Equity:         N/A")
        print(
            f"  Dilution (shares):  {res.dilution_trend:>+7.1%}"
            if res.dilution_trend is not None
            else "  Dilution:           N/A"
        )
        print(f"  D/E:                {res.leverage:>7.2f}x" if res.leverage is not None else "  D/E:                N/A")
        print(f"  Components: {', '.join(f'{k}={v:+0.2f}' for k, v in sorted(res.components.items()))}")
        if res.flags:
            for f in res.flags:
                print(f"  ⚠ {f}")

    # Summary
    print(f"\n  {'=' * 65}")
    print("  SO SÁNH TỔNG THỂ")
    print(f"  {'=' * 65}")
    print(f"  {'Symbol':<6} {'Archetype':<24} {'Score':>7} {'ROIC−WACC':>10}")
    print(f"  {'─' * 50}")
    for sym, res in sorted(results.items()):
        if res:
            spread = f"{res.roic_wacc_spread:>+7.1%}" if res.roic_wacc_spread is not None else "N/A"
            print(f"  {sym:<6} {res.archetype_label:<24} {res.quality_score:>+7.3f} {spread:>10}")
    print(f"  {'─' * 50}")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Capital Allocation Quality — Giai đoạn 4")
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

    engine = CapitalAllocationEngine()
    results = engine.assess_many(args.symbols)
    print_allocation_report(results)
    engine.close()


if __name__ == "__main__":
    main()
