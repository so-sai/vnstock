"""backtest_trend_2021_2025.py — RUNNER backtest cho Trend Engine (spec v1.1).

Pre-registered spec: docs/SPEC_TREND_ENGINE_2021_2025.md (FROZEN, hash neo ở sidecar).

TRIẾT LÝ CỦA RUNNER NÀY: **FAIL-CLOSED, KHÔNG ĐƯỢC BỊA KẾT QUẢ**
--------------------------------------------------------------
Một skeleton trả về số 0 sẽ trông y như một backtest "chạy được nhưng chiến lược
kém": Sharpe 0.0, 0 lệnh, MaxDD 0%. Đó là dạng nguy hiểm nhất của báo cáo sai —
người đọc không thể phân biệt "chiến lược không có edge" với "engine chưa được viết".

Vì vậy `run_backtest()` **từ chối chạy** khi thiếu dependency, kèm danh sách cụ thể
những gì còn thiếu. Chỉ những phần đã cài đặt thật mới chạy:
  ✅ verify pre-registration lock     ✅ portfolio MTM hằng ngày theo close
  ✅ mô hình chi phí 0.25%/chiều      ✅ metrics (Sharpe/Calmar/MaxDD/WinRate/PF)
  ✅ lịch phiên giao dịch             ✅ benchmark VNINDEX từ store
  ✅ độ phủ dữ liệu cơ bản            ✅ xuất JSON summary + CSV trade log
  ❌ sinh tín hiệu (trend_engine)     ❌ sizing (trend_sizing)
  ❌ circuit breaker (K1–K6)          ❌ lọc organ/Health
"""

from __future__ import annotations

import csv
import json
import math
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[3]
for _p in (PROJECT_ROOT, PROJECT_ROOT / "backend", PROJECT_ROOT / "backend" / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from src.backtest.preregistration import (
    verify_preregistration_lock,
)

DB = PROJECT_ROOT / "backend" / "data" / "screener_cache.db"
FACTS_DB = PROJECT_ROOT / "backend" / "data" / "financial_facts.db"
OUT_DIR = PROJECT_ROOT / "backend" / "reports" / "backtest"
VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")

# ══════════════════════════════════════════════════════════════════════
# HẰNG SỐ KHÓA THEO SPEC — muốn đổi ⇒ phải là variant B (file spec riêng)
# ══════════════════════════════════════════════════════════════════════
WINDOW_START = "2021-01-01"
WINDOW_END = "2025-12-31"
MIN_SESSIONS_PER_YEAR = 200
MIN_ADV20 = 500_000
TRADING_DAYS_PER_YEAR = 252


# Spec §11: phí 0.15% + trượt giá 0.10% MỖI CHIỀU ⇒ 0.25%/chiều = 0.50% round-trip.
# Chạy THÊM một kịch bản robustness theo thực tế Việt Nam (+0.10% thuế TNCN khi BÁN).
# Không chọn một: chọn một là data-snooping — xem §8 spec và quyết định 2026-10-09.
@dataclass(frozen=True)
class CostModel:
    buy_pct: float
    sell_pct: float
    label: str

    @property
    def round_trip_pct(self) -> float:
        return self.buy_pct + self.sell_pct


COST_SCENARIOS: dict[str, CostModel] = {
    "primary": CostModel(0.25, 0.25, "spec §11 — 0.15% phí + 0.10% trượt giá mỗi chiều"),
    "robustness": CostModel(0.25, 0.35, "+0.10% thuế TNCN khi bán (thực tế VN; spec thiếu)"),
}
DEFAULT_SCENARIO = "primary"

MAX_POSITIONS = 4
BASE_SIZE_PCT = 5.0
MAX_TREND_ALLOC_PCT = 20.0
DISCOUNT_POLARITY_MISMATCH = 0.5
DISCOUNT_LEVERAGED_GROWTH = 0.8

BENCHMARK_SYMBOL = "VNINDEX"


# ══════════════════════════════════════════════════════════════════════
# Nạp dữ liệu (thật)
# ══════════════════════════════════════════════════════════════════════
def load_trading_days(start: str = WINDOW_START, end: str = WINDOW_END, db: Path = DB) -> list[str]:
    """Phiên giao dịch trong cửa sổ, lấy từ chính store (nguồn: benchmark VNINDEX)."""
    conn = sqlite3.connect(str(db))
    try:
        rows = conn.execute(
            "SELECT DISTINCT date FROM daily_ohlcv WHERE symbol = ? AND date BETWEEN ? AND ? "
            "AND COALESCE(is_stale, 0) = 0 ORDER BY date",
            (BENCHMARK_SYMBOL, start, end),
        ).fetchall()
    finally:
        conn.close()
    return [r[0] for r in rows]


def load_benchmark(start: str = WINDOW_START, end: str = WINDOW_END, db: Path = DB) -> dict[str, float]:
    """Chuỗi VNINDEX (price return) — benchmark đã chốt."""
    conn = sqlite3.connect(str(db))
    try:
        rows = conn.execute(
            "SELECT date, close FROM daily_ohlcv WHERE symbol = ? AND date BETWEEN ? AND ? "
            "AND COALESCE(is_stale, 0) = 0 ORDER BY date",
            (BENCHMARK_SYMBOL, start, end),
        ).fetchall()
    finally:
        conn.close()
    return {d: float(c) for d, c in rows}


def load_price_eligible(db: Path = DB, start: str = WINDOW_START, end: str = WINDOW_END) -> set[str]:
    """Mã đạt tiêu chí GIÁ của universe (§3): ≥200 phiên sạch ở ít nhất 1 năm trong cửa sổ.

    (Cổng ADV20 được kiểm theo từng ngày trong vòng lặp, vì nó trượt theo thời gian.)
    """
    conn = sqlite3.connect(str(db))
    try:
        rows = conn.execute(
            "SELECT symbol FROM daily_ohlcv WHERE date BETWEEN ? AND ? AND COALESCE(is_stale, 0) = 0 "
            "GROUP BY symbol, substr(date, 1, 4) HAVING COUNT(*) >= ?",
            (start, end, MIN_SESSIONS_PER_YEAR),
        ).fetchall()
    finally:
        conn.close()
    return {r[0] for r in rows}


def load_fundamental_coverage(facts_db: Path = FACTS_DB) -> dict[str, set[str]]:
    """{fiscal_year: {symbol có dữ liệu tài chính}} — để đo mẫu số universe thật (~740)."""
    out: dict[str, set[str]] = {}
    try:
        conn = sqlite3.connect(str(facts_db))
    except sqlite3.Error:
        return out
    try:
        for y, s in conn.execute(
            "SELECT fiscal_year, symbol FROM financial_facts WHERE fiscal_year BETWEEN 2021 AND 2025 "
            "GROUP BY fiscal_year, symbol"
        ):
            out.setdefault(str(y), set()).add(s)
    finally:
        conn.close()
    return out


# ══════════════════════════════════════════════════════════════════════
# Portfolio — MTM hằng ngày theo close (đã chốt)
# ══════════════════════════════════════════════════════════════════════
@dataclass
class Position:
    symbol: str
    qty: float
    entry_price: float
    entry_date: str
    peak_price: float = 0.0

    def __post_init__(self):
        if not self.peak_price:
            self.peak_price = self.entry_price


@dataclass
class Trade:
    symbol: str
    date_in: str
    date_out: str
    price_in: float
    price_out: float
    qty: float
    pnl_pct: float
    hold_days: int
    exit_reason: str


@dataclass
class Portfolio:
    """Tiền mặt + vị thế, MTM mỗi ngày theo giá close (spec §11 đã chốt MTM)."""

    initial_nav: float = 100.0
    cost: CostModel = field(default_factory=lambda: COST_SCENARIOS[DEFAULT_SCENARIO])
    cash: float = field(init=False)
    positions: dict[str, Position] = field(default_factory=dict)
    trades: list[Trade] = field(default_factory=list)
    nav_history: list[tuple[str, float]] = field(default_factory=list)

    def __post_init__(self):
        self.cash = self.initial_nav

    def apply_cost(self, notional: float, side: str) -> float:
        """Phí + trượt giá theo kịch bản chi phí (bất đối xứng: thuế chỉ ở chiều BÁN)."""
        rate = self.cost.buy_pct if side == "buy" else self.cost.sell_pct
        fee = abs(notional) * rate / 100.0
        self.cash -= fee
        return fee

    def nav(self, prices: dict[str, float]) -> float:
        mv = sum(p.qty * prices.get(p.symbol, p.entry_price) for p in self.positions.values())
        return self.cash + mv

    def enter(self, symbol: str, price: float, date: str, target_notional: float) -> bool:
        if symbol in self.positions or len(self.positions) >= MAX_POSITIONS or price <= 0:
            return False
        notional = min(target_notional, self.cash)
        if notional <= 0:
            return False
        self.apply_cost(notional, "buy")
        notional = min(notional, self.cash)
        if notional <= 0:
            return False
        qty = notional / price
        self.cash -= notional
        self.positions[symbol] = Position(symbol, qty, price, date)
        return True

    def exit(self, symbol: str, price: float, date: str, reason: str) -> Trade | None:
        pos = self.positions.pop(symbol, None)
        if pos is None:
            return None
        notional = pos.qty * price
        self.apply_cost(notional, "sell")
        self.cash += notional
        tr = Trade(
            symbol=symbol,
            date_in=pos.entry_date,
            date_out=date,
            price_in=pos.entry_price,
            price_out=price,
            qty=pos.qty,
            pnl_pct=(price / pos.entry_price - 1.0) * 100.0,
            hold_days=0,
            exit_reason=reason,
        )
        self.trades.append(tr)
        return tr

    def mark(self, date: str, prices: dict[str, float]):
        self.nav_history.append((date, self.nav(prices)))

    def close_all(self, date: str, prices: dict[str, float], reason: str):
        for sym in list(self.positions):
            px = prices.get(sym)
            if px:
                self.exit(sym, px, date, reason)


# ══════════════════════════════════════════════════════════════════════
# Metrics (hàm thuần — test được không cần DB)
# ══════════════════════════════════════════════════════════════════════
def daily_returns(navs: list[float]) -> list[float]:
    return [navs[i] / navs[i - 1] - 1.0 for i in range(1, len(navs)) if navs[i - 1]]


def sharpe(navs: list[float], rf: float = 0.0) -> float:
    r = daily_returns(navs)
    if len(r) < 2:
        return 0.0
    mu = sum(r) / len(r)
    var = sum((x - mu) ** 2 for x in r) / (len(r) - 1)
    sd = math.sqrt(var)
    if sd == 0:
        return 0.0
    return (mu - rf / TRADING_DAYS_PER_YEAR) / sd * math.sqrt(TRADING_DAYS_PER_YEAR)


def max_drawdown(navs: list[float]) -> float:
    """Max drawdown (số dương, đơn vị %)."""
    peak = navs[0] if navs else 0.0
    mdd = 0.0
    for v in navs:
        peak = max(peak, v)
        if peak > 0:
            mdd = max(mdd, (peak - v) / peak * 100.0)
    return mdd


def cagr(navs: list[float], n_days: int) -> float:
    if not navs or n_days <= 0 or navs[0] <= 0:
        return 0.0
    years = n_days / TRADING_DAYS_PER_YEAR
    if years <= 0:
        return 0.0
    return ((navs[-1] / navs[0]) ** (1 / years) - 1.0) * 100.0


def calmar(navs: list[float], n_days: int) -> float:
    mdd = max_drawdown(navs)
    if mdd <= 0:
        return 0.0
    return cagr(navs, n_days) / mdd


def win_rate(trades: list[Trade]) -> float:
    if not trades:
        return 0.0
    return sum(1 for t in trades if t.pnl_pct > 0) / len(trades) * 100.0


def profit_factor(trades: list[Trade]) -> float:
    gross_win = sum(t.pnl_pct for t in trades if t.pnl_pct > 0)
    gross_loss = -sum(t.pnl_pct for t in trades if t.pnl_pct < 0)
    if gross_loss == 0:
        return float("inf") if gross_win > 0 else 0.0
    return gross_win / gross_loss


# ══════════════════════════════════════════════════════════════════════
# Preflight — kiểm dependency & PHÁT HIỆN trước khi chạy
# ══════════════════════════════════════════════════════════════════════
REQUIRED_MODULES = {
    "src.engine.trend_engine": "sinh tín hiệu vào/ra (§4, §5)",
    "src.engine.trend_sizing": "định cỡ vị thế + discount multiplier (§6)",
    "src.engine.trend_circuit_breaker": "kill switch K1–K6 (§10)",
    "src.engine.trend_organ_filter": "lọc Health/organ BS/Eff/Moat (§3, §4)",
}


def preflight() -> dict:
    """Kiểm mọi thứ cần có. KHÔNG sửa state, KHÔNG chạy backtest."""
    lock = verify_preregistration_lock(raise_on_fail=False)

    missing = []
    for mod, why in REQUIRED_MODULES.items():
        try:
            __import__(mod)
        except ImportError:
            missing.append(f"{mod} — {why}")

    days = load_trading_days()
    bench = load_benchmark()
    price_ok = load_price_eligible()
    facts = load_fundamental_coverage()
    facts_union = set().union(*facts.values()) if facts else set()
    organ_ok = price_ok & facts_union

    # Mẫu số THEO NĂM (đúng như spec §3.1) — union cả cửa sổ là số khác (lớn hơn) và
    # KHÔNG được dùng làm mẫu số báo cáo.
    per_year = {}
    for y in ("2021", "2022", "2023", "2024", "2025"):
        p = load_price_eligible(start=f"{y}-01-01", end=f"{y}-12-31")
        f = facts.get(y, set())
        per_year[y] = {"price": len(p), "facts": len(f), "intersection": len(p & f)}

    notes = []
    notes.append(
        "COST MODEL: chạy CẢ HAI kịch bản (không chọn một — chọn một là data-snooping): "
        + " · ".join(f"{k} = {v.round_trip_pct:.2f}% round-trip ({v.label})" for k, v in COST_SCENARIOS.items())
        + ". primary theo spec §11 (0.50%); robustness thêm thuế TNCN ở chiều bán (0.60%)."
    )
    if len(bench) != len(days):
        notes.append(f"benchmark {len(bench)} phiên ≠ lịch {len(days)} phiên — cần đối chiếu.")

    return {
        "preregistration": lock,
        "missing_modules": missing,
        "trading_days": len(days),
        "window": [days[0], days[-1]] if days else None,
        "benchmark_sessions": len(bench),
        "price_eligible": len(price_ok),
        "fundamental_covered": len(facts_union),
        "effective_universe": len(organ_ok),
        "universe_per_year": per_year,
        "notes": notes,
        "ready": not missing and lock["ok"] and bool(days),
    }


# ══════════════════════════════════════════════════════════════════════
# Runner
# ══════════════════════════════════════════════════════════════════════
def run_backtest(force: bool = False) -> dict:
    """Chạy backtest. TỪ CHỐI chạy nếu thiếu dependency hoặc spec bị sửa."""
    pf = preflight()

    problems = []
    if not pf["preregistration"]["ok"]:
        problems.append("pre-registration: spec đã bị sửa (xem problems)")
    if pf["missing_modules"]:
        problems.append("thiếu module: " + "; ".join(pf["missing_modules"]))
    if not pf["trading_days"]:
        problems.append("không có phiên giao dịch nào trong cửa sổ")

    if problems and not force:
        raise RuntimeError(
            "BACKTEST KHÔNG ĐƯỢC CHẠY — chưa đủ điều kiện:\n  - "
            + "\n  - ".join(problems)
            + "\n\nRunner cố tình KHÔNG trả về kết quả rỗng: một báo cáo Sharpe=0 / 0 lệnh "
            "sẽ bị đọc nhầm thành 'chiến lược kém' trong khi thực tế engine chưa tồn tại."
        )
    if force:
        raise RuntimeError(
            "force=True không được hỗ trợ: chạy backtest trên engine chưa cài đặt sẽ tạo ra "
            "kết quả VÔ NGHĨA nhưng trông hợp lệ. Cài đủ module rồi chạy lại."
        )

    # ── Vòng lặp ngày (chỉ tới đây khi mọi dependency đã có) ──
    days = load_trading_days()
    pf_obj = Portfolio()
    for _d in days:
        # 1. circuit breaker (§10) · 2. regime CRISIS ⇒ full exit (§7 #8)
        # 3. sinh tín hiệu (§4) · 4. sizing (§6) · 5. thoát (§5) · 6. MTM
        raise NotImplementedError("vòng lặp ngày chưa cài đặt — xem preflight()")
    # pragma: no cover
    return export_results(pf_obj, days, pf)


def export_results(portfolio: Portfolio, days: list[str], preflight_info: dict, scenario: str = DEFAULT_SCENARIO) -> dict:
    """Ghi JSON summary + CSV trade log CHO MỘT kịch bản chi phí."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    navs = [v for _, v in portfolio.nav_history]
    summary = {
        "generated_at": datetime.now(VN_TZ).isoformat(),
        "spec": preflight_info["preregistration"]["spec"],
        "spec_sha256": preflight_info["preregistration"]["sha256"],
        "cost_scenario": scenario,
        "cost_buy_pct": portfolio.cost.buy_pct,
        "cost_sell_pct": portfolio.cost.sell_pct,
        "cost_round_trip_pct": portfolio.cost.round_trip_pct,
        "window": [days[0], days[-1]] if days else None,
        "sessions": len(days),
        "effective_universe": preflight_info["effective_universe"],
        "initial_nav": portfolio.initial_nav,
        "final_nav": navs[-1] if navs else None,
        "total_return_pct": ((navs[-1] / navs[0] - 1) * 100) if navs else None,
        "sharpe": sharpe(navs) if navs else None,
        "calmar": calmar(navs, len(navs)) if navs else None,
        "max_drawdown_pct": max_drawdown(navs) if navs else None,
        "win_rate_pct": win_rate(portfolio.trades),
        "profit_factor": profit_factor(portfolio.trades),
        "total_trades": len(portfolio.trades),
    }
    (OUT_DIR / f"backtest_summary_{scenario}.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    with (OUT_DIR / f"backtest_trades_{scenario}.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["symbol", "date_in", "date_out", "price_in", "price_out", "qty", "pnl_pct", "hold_days", "exit_reason"])
        for t in portfolio.trades:
            w.writerow(
                [
                    t.symbol,
                    t.date_in,
                    t.date_out,
                    f"{t.price_in:.2f}",
                    f"{t.price_out:.2f}",
                    f"{t.qty:.0f}",
                    f"{t.pnl_pct:.4f}",
                    t.hold_days,
                    t.exit_reason,
                ]
            )
    return summary


def interpret_scenarios(summaries: dict[str, dict]) -> str:
    """Đọc kết quả 2 kịch bản theo luật ĐÃ CHỐT TRƯỚC khi chạy.

    strong pass | marginal pass | fail — luật này không được đổi sau khi thấy số.
    """
    thr = 0.5  # tiêu chí Sharpe §8
    sh = {k: v.get("sharpe") for k, v in summaries.items()}
    if not sh or any(v is None for v in sh.values()):
        return "INCOMPLETE — thiếu kết quả ở ít nhất một kịch bản"
    ok = {k: (v is not None and v >= thr) for k, v in sh.items()}
    if all(ok.values()):
        return "STRONG PASS — chiến lược không nhạy với giả định chi phí"
    if ok.get("primary") and not ok.get("robustness"):
        return "MARGINAL PASS — chỉ pass ở kịch bản spec; phải review giả định chi phí trước khi live"
    return "FAIL — không implement, giữ nguyên kiến trúc veto"


def export_comparison(summaries: dict[str, dict]) -> dict:
    """Ghi file tổng hợp CẢ HAI kịch bản + delta + kết luận (báo cáo trung thực cả hai)."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    prim, rob = summaries.get("primary", {}), summaries.get("robustness", {})
    delta = None
    if prim.get("sharpe") is not None and rob.get("sharpe") is not None:
        delta = prim["sharpe"] - rob["sharpe"]
    payload = {
        "generated_at": datetime.now(VN_TZ).isoformat(),
        "note": "Chạy CẢ HAI kịch bản chi phí; không chọn một. Kết luận theo luật đã chốt trước khi chạy.",
        "primary": prim,
        "robustness": rob,
        "delta_sharpe": delta,
        "verdict": interpret_scenarios(summaries),
    }
    (OUT_DIR / "backtest_summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main() -> int:
    pf = preflight()
    print("=" * 72)
    print("  BACKTEST TREND ENGINE — PREFLIGHT (spec v1.1 pre-registered)")
    print("=" * 72)
    lock = pf["preregistration"]
    print(f"  pre-registration : {'✅ KHỚP' if lock['ok'] else '❌ BỊ SỬA'}")
    print(f"  spec sha256      : {lock['sha256']}")
    print(f"  cửa sổ           : {pf['window']}  ({pf['trading_days']} phiên)")
    print(f"  benchmark        : {pf['benchmark_sessions']} phiên VNINDEX")
    print(f"  universe (giá)   : {pf['price_eligible']} mã")
    print(f"  phủ cơ bản (facts): {pf['fundamental_covered']} mã")
    print(f"  universe HIỆU DỤNG: {pf['effective_universe']} mã   ← mẫu số để báo cáo")
    print("  mẫu số theo năm (spec §3.1):")
    for y, d in pf["universe_per_year"].items():
        print(f"     {y}: giá {d['price']:>4} · cơ bản {d['facts']:>4} · giao điểm {d['intersection']:>4}")
    if pf["missing_modules"]:
        print("\n  ❌ THIẾU MODULE (chưa thể chạy):")
        for m in pf["missing_modules"]:
            print(f"     - {m}")
    for n in pf["notes"]:
        print(f"\n  ⚠️  {n}")
    print(f"\n  READY: {pf['ready']}")
    print("=" * 72)
    return 0 if pf["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
