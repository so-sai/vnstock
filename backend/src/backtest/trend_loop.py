"""Vòng lặp ngày của backtest Trend Engine — hiện thực §4/§5/§6/§10 trên dữ liệu thật.

NĂM QUYẾT ĐỊNH CỦA CHỦ HỆ THỐNG (2026-10-09)
--------------------------------------------
  1. VÀO lệnh tại **Open T+1** — quyết định ở close T, khớp ở open phiên kế tiếp.
  2. THOÁT thường tại **Open T+1**; **khẩn cấp khớp Close T** (regime CRISIS, K3 toàn thị trường).
  3. RS theo §4.1 (đã định nghĩa tường minh), tính PIT từ `trend_features`.
  4. Lịch giao dịch lấy từ chính store (benchmark VNINDEX).
  5. Universe **động theo từng năm** (mã đủ điều kiện trong năm đó).

THIẾT KẾ ĐỂ CHẠY ĐƯỢC 5 NĂM
---------------------------
1247 phiên × ~900 mã = hơn một triệu điểm dữ liệu. Tra từng ô bằng pandas `.at` sẽ mất
hàng chục phút, nên module này **chuẩn bị mảng numpy một lần** cho toàn bộ universe rồi
vòng lặp chỉ còn truy cập chỉ số. Organ (nguồn chậm nhất: mỗi mã mỗi quý một lần truy vấn)
được tính trước theo quý và cache.

BA ĐẦU VÀO SPEC CÓ NHẮC NHƯNG STORE KHÔNG CÓ CHUỖI LỊCH SỬ — NÊU RÕ, KHÔNG TỰ CHẾ
--------------------------------------------------------------------------------
  · `nguồn_chặn ∈ {WORLD, MACRO}` (§4): không có chuỗi lịch sử ⇒ mặc định `"NONE"` (điều
    kiện được coi là thoả — tức KHÔNG loại ai vì lý do không đo được).
  · `POLARITY_MISMATCH` (§6): đến từ tầng causal C3, không có dữ liệu lịch sử ⇒ mặc định
    `False` (không chiết khấu ×0.5).
  · `beta` (§6, trần beta-weighted 25% NAV): không có ước lượng lịch sử ⇒ mặc định 1.0
    (khi đó trần beta-weighted suy biến thành trần 25% NAV).
Cả ba đều làm chiến lược **rộng rãi hơn** (không loại, không chiết khấu) — nên kết quả
backtest là **cận trên lạc quan** ở đúng ba chỗ đó. Phải nói ra, không được im lặng.
"""

from __future__ import annotations

import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[3]
for _p in (PROJECT_ROOT / "backend", PROJECT_ROOT / "backend" / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from src.backtest.ablation_arms import ORGAN_MODE_DISTRESSED_ONLY, ArmSwitches
from src.engine import trend_circuit_breaker as cb
from src.engine import trend_engine as te
from src.engine import trend_sizing as ts
from src.engine.trend_organ_filter import DISTRESSED, OrganFilter, OrganVerdict

DB = PROJECT_ROOT / "backend" / "data" / "screener_cache.db"
BENCHMARK = "VNINDEX"
BASE_WINDOW = 20
RS_WEIGHTS = te.__dict__.get("RS_WEIGHTS") or {63: 0.4, 126: 0.2, 189: 0.2, 252: 0.2}
REGIME_CRISIS = "CRISIS"
RS_WARMUP_SESSIONS = 280  # > 252 (thành phần r1y) để RS có giá trị ngay phiên đầu cửa sổ


@dataclass
class MarketArrays:
    """Toàn bộ dữ liệu cần cho vòng lặp, đã căn theo trục `days` (NaN = thiếu).

    `days` bao gồm CẢ WARM-UP trước cửa sổ; `start_idx` là vị trí phiên đầu tiên THUỘC
    cửa sổ báo cáo. Vòng lặp chỉ chạy từ `start_idx`, nhưng chỉ số `i` dùng chung một trục
    nên các phép `shift(252)` phía sau vẫn có lịch sử để tính.

    ⚠️ Thiếu warm-up là lỗi im lặng: `close.shift(252)` toàn NaN trong 252 phiên đầu ⇒ RS
    rỗng ⇒ KHÔNG có ứng viên nào ⇒ năm đầu của cửa sổ bị bỏ trắng mà không báo gì.
    """

    days: list[str]
    idx: dict[str, int]
    start_idx: int
    close: pd.DataFrame  # index=date, columns=symbol (đã sắp theo `days`)
    open_: pd.DataFrame
    high: pd.DataFrame
    low: pd.DataFrame
    volume: pd.DataFrame

    def at(self, frame: pd.DataFrame, sym: str, i: int) -> float | None:
        try:
            v = frame.iat[i, frame.columns.get_loc(sym)]
        except KeyError, IndexError:
            return None
        return None if pd.isna(v) else float(v)


def _panel(field: str, start: str, end: str) -> pd.DataFrame:
    from src.database.data_validity import VALID_ROW_SQL

    con = sqlite3.connect(str(DB))
    q = f"SELECT date, symbol, {field} AS v FROM daily_ohlcv WHERE date BETWEEN ? AND ? AND is_stale = 0 AND {VALID_ROW_SQL}"
    df = pd.read_sql_query(q, con, params=[start, end])
    con.close()
    return df.pivot_table(index="date", columns="symbol", values="v", aggfunc="last").sort_index()


def load_regime(start: str, end: str) -> dict[str, str]:
    con = sqlite3.connect(str(DB))
    rows = con.execute("SELECT date, status FROM regime_history WHERE date BETWEEN ? AND ?", (start, end)).fetchall()
    con.close()
    return {d: s for d, s in rows}


@dataclass
class Universe:
    """Mã đủ điều kiện theo NĂM (§3, universe động)."""

    by_year: dict[str, set[str]]

    def at(self, date: str) -> set[str]:
        return self.by_year.get(date[:4], set())


def load_universe(start: str, end: str, min_sessions: int) -> Universe:
    con = sqlite3.connect(str(DB))
    rows = con.execute(
        "SELECT substr(date,1,4) AS y, symbol FROM daily_ohlcv "
        "WHERE date BETWEEN ? AND ? AND is_stale = 0 AND close > 0 "
        "GROUP BY y, symbol HAVING COUNT(*) >= ?",
        (start, end, min_sessions),
    ).fetchall()
    con.close()
    by_year: dict[str, set[str]] = {}
    for y, s in rows:
        by_year.setdefault(y, set()).add(s)
    return Universe(by_year)


def prepare(start: str, end: str, min_sessions: int) -> tuple[MarketArrays, Universe, dict[str, str], dict[str, float]]:
    """Nạp + chuẩn bị mọi thứ trước khi vào vòng lặp (không đọc DB trong vòng lặp)."""
    con = sqlite3.connect(str(DB))
    # ⚠️ Trục ngày warm-up phải lấy từ TOÀN store, KHÔNG lọc theo benchmark: VNINDEX
    # trong store chỉ bắt đầu 2021-01-04, nên lọc theo nó sẽ trả về RỖNG ⇒ warm_start =
    # start ⇒ không có warm-up ⇒ `shift(252)` toàn NaN suốt 252 phiên đầu ⇒ RS rỗng ⇒
    # ~20% cửa sổ (cả năm 2021) không sinh nổi một tín hiệu, và KHÔNG có gì báo lỗi.
    # Đây là lỗi đã xảy ra thật ở lần chạy đầu (2026-10-09) và làm kết quả không hợp lệ.
    warm = con.execute(
        "SELECT DISTINCT date FROM daily_ohlcv WHERE date < ? AND is_stale=0 ORDER BY date DESC LIMIT ?",
        (start, RS_WARMUP_SESSIONS),
    ).fetchall()
    warm_dates = sorted(r[0] for r in warm)
    warm_start = warm_dates[0] if warm_dates else start
    win_dates = [
        r[0]
        for r in con.execute(
            "SELECT DISTINCT date FROM daily_ohlcv WHERE symbol=? AND date BETWEEN ? AND ? AND is_stale=0 ORDER BY date",
            (BENCHMARK, start, end),
        ).fetchall()
    ]
    days = warm_dates + win_dates
    bench = {
        r[0]: float(r[1])
        for r in con.execute(
            "SELECT date, close FROM daily_ohlcv WHERE symbol=? AND date BETWEEN ? AND ? AND is_stale=0",
            (BENCHMARK, warm_start, end),
        ).fetchall()
    }
    con.close()
    start_idx = len(warm_dates)
    panels = {f: _panel(f, warm_start, end).reindex(days) for f in ("close", "open", "high", "low", "volume")}
    ma = MarketArrays(
        days=days,
        idx={d: i for i, d in enumerate(days)},
        start_idx=start_idx,
        close=panels["close"],
        open_=panels["open"],
        high=panels["high"],
        low=panels["low"],
        volume=panels["volume"],
    )
    return ma, load_universe(start, end, min_sessions), load_regime(warm_start, end), bench


def _rs_series(close: pd.DataFrame) -> pd.DataFrame:
    """RS theo §4.1 trên chính panel close (PIT: chỉ đọc quá khứ)."""
    raw: pd.DataFrame = close * 0.0
    for n, w in RS_WEIGHTS.items():
        raw = raw + (close / close.shift(n) - 1.0) * w
    from src.backtest.trend_features import load_scale_ambiguous

    amb = load_scale_ambiguous()
    if amb:
        raw = raw.drop(columns=[c for c in amb if c in raw.columns])
    return raw.rank(axis=1, pct=True) * 99.0


def _vnindex_drop_3d(bench: dict[str, float], days: list[str], i: int) -> float:
    if i < 3:
        return 0.0
    a, b = bench.get(days[i]), bench.get(days[i - 3])
    if not a or not b:
        return 0.0
    return (a / b - 1.0) * 100.0


def simulate(
    portfolio,
    *,
    start: str,
    end: str,
    min_sessions: int = 200,
    limit_days: int | None = None,
    verbose: bool = False,
    arm: ArmSwitches | None = None,
    universe_factory=None,
) -> dict:
    """Chạy vòng lặp ngày. Trả về dict thống kê tối thiểu để kiểm chứng.

    `arm` = hai công tắc của Ablation Study (`docs/SPEC_TREND_ENGINE_ABLATION_STUDY.md`).
    Mặc định `None` ⇒ `ArmSwitches(organ=True, regime=True)` = **đúng hành vi đã niêm phong**,
    nên đường chạy baseline không đổi một bit.

    `universe_factory(ma) -> object có .at(date) -> set[str]`: cho phép spec OOS 2026 dùng luật
    universe **cửa sổ trượt** (≥200 phiên sạch trong 250 phiên gần nhất) thay cho luật đếm theo
    năm dương lịch. `None` (mặc định) = giữ nguyên luật cũ ⇒ mọi đường chạy hiện có không đổi.
    """
    arm = arm or ArmSwitches()
    ma, universe, regime, bench = prepare(start, end, min_sessions)
    if universe_factory is not None:
        universe = universe_factory(ma)
    all_days = ma.days
    end_i = len(all_days) if limit_days is None else min(len(all_days), ma.start_idx + limit_days)
    rs = _rs_series(ma.close)
    # ── GUARD: RS phải CÓ giá trị ngay phiên đầu cửa sổ ──
    # Lỗi 2026-10-09: warm-up không được nạp ⇒ `shift(252)` toàn NaN ⇒ RS rỗng suốt 252
    # phiên đầu ⇒ cả năm 2021 không sinh nổi một tín hiệu, VÀ KHÔNG có gì báo lỗi. Kết quả
    # vẫn ra file trông hợp lệ. Nên phải kiểm ngay, và phải raise: một backtest thiếu 20%
    # tín hiệu mà không ai biết là tệ hơn không chạy.
    first_row = rs.iloc[ma.start_idx].dropna()
    if len(first_row) < 20:
        raise RuntimeError(
            f"RS KHÔNG có giá trị ở phiên đầu cửa sổ ({all_days[ma.start_idx]}): chỉ "
            f"{len(first_row)} mã. Nghi warm-up không được nạp (xem prepare()). "
            "Dừng thay vì chạy ra kết quả thiếu tín hiệu."
        )
    adv20 = ma.volume.shift(1).rolling(BASE_WINDOW).mean()
    high20 = ma.high.shift(1).rolling(BASE_WINDOW).max()
    ma20 = ma.close.shift(1).rolling(BASE_WINDOW).mean()
    organ = OrganFilter()

    pending_entries: list[str] = []
    pending_exits: list[str] = []
    cooldown_until: dict[str, int] = {}
    # Thoát một phần chỉ được kích MỘT LẦN cho mỗi vị thế. Nếu không: trailing −5% vẫn
    # đúng ở phiên kế tiếp ⇒ cắt 50% mỗi phiên ⇒ vị thế teo dần thành thoát toàn bộ, thứ
    # mà spec §5 không hề nói tới (spec nói "partial exit 50%", một lần).
    partial_done: set[str] = set()
    stats = {
        "days": 0,
        "entries": 0,
        "exits": 0,
        "full_exits": 0,
        "partial": 0,
        "emergency": 0,
        "candidates": 0,
        "days_with_positions": 0,
        "sum_positions": 0,
        "max_positions_held": 0,
        "candidates_archetype_none": 0,
        "candidates_distressed_rejected": 0,
    }

    for i in range(ma.start_idx, end_i):
        T = all_days[i]
        # ── 1. Khớp lệnh đã xếp hàng từ phiên trước, tại OPEN của T ──
        for sym in list(pending_exits):
            px = ma.at(ma.open_, sym, i)
            if px and px > 0:
                tr = portfolio.exit(sym, px, T, "exit_open_T+1")
                if tr:
                    stats["exits"] += 1
                cooldown_until[sym] = i + cb.REENTRY_COOLDOWN
            pending_exits.remove(sym)
        for sym in list(pending_entries):
            px = ma.at(ma.open_, sym, i)
            if not px or px <= 0 or sym in cooldown_until and i < cooldown_until[sym]:
                pending_entries.remove(sym)
                continue
            nav_now = portfolio.nav(_closes(ma, i))
            decision = ts.position_size(
                nav=nav_now,
                in_trend_pct=_trend_pct(portfolio, ma, i),
                open_positions=len(portfolio.positions),
                archetype=_archetype(organ, sym, T),
            )
            if decision.target_pct > 0 and portfolio.enter(sym, px, T, nav_now * decision.target_pct / 100.0):
                stats["entries"] += 1
            pending_entries.remove(sym)

        # ── 2. Đánh giá tại CLOSE của T ──
        # FAIL-CLOSED: thiếu dòng regime ⇒ RAISE, tuyệt đối không mặc định RANGING.
        # Lỗi thật 2026-10-14: 53/190 phiên của cửa sổ OOS 2026 (2026-04-20 → 2026-09-23)
        # không có dòng regime. Mặc định RANGING đã TẮT PHANH CRISIS trong im lặng ở đúng
        # vùng thị trường xấu ⇒ thổi phồng kết quả (C1 "+1,02" là số của một lần chạy
        # KHÔNG có phanh). Một phanh vắng mặt không được đọc thành "đường thoáng".
        if T not in regime:
            raise RuntimeError(
                f"THIẾU dòng regime tại {T} — dừng thay vì mặc định RANGING. "
                "Mặc định RANGING sẽ tắt phanh CRISIS mà không báo gì (fail-open). "
                "Nạp bù regime_history cho phiên này trước khi chạy lại."
            )
        regime_today = regime[T]
        vn3 = _vnindex_drop_3d(bench, all_days, i)
        # regime_on=False (V1, V3): TẮT veto CRISIS và trigger K3 toàn thị trường (§3).
        emergency = arm.regime and (regime_today == REGIME_CRISIS or vn3 <= -cb.VNINDEX_DROP_PCT)

        if emergency and portfolio.positions:
            px = _closes(ma, i)
            portfolio.close_all(T, px, f"emergency:{regime_today}/vn3={vn3:.1f}%")
            stats["emergency"] += 1
            for sym in list(portfolio.positions):
                cooldown_until[sym] = i + cb.REENTRY_COOLDOWN
            pending_entries.clear()
        elif portfolio.positions:
            for sym in list(portfolio.positions):
                pos = portfolio.positions[sym]
                c = ma.at(ma.close, sym, i)
                if not c or c <= 0:
                    continue
                pos.peak_price = max(pos.peak_price, c)
                pv = cb.PositionView(
                    symbol=sym,
                    peak_price=pos.peak_price,
                    current_price=c,
                    rs_now=_rs(rs, sym, i) or 0.0,
                    rs_prev=_rs(rs, sym, i - 1) or 0.0,
                )
                mv = cb.MarketView(
                    regime=regime_today,
                    vnindex_return_3d_pct=vn3,
                    atr_pct=_atr_pct(ma, sym, i),
                    atr_pct_ma20=_atr_pct_ma20(ma, sym, i),
                )
                trig = cb.evaluate(pv, mv)
                action = cb.resolve(trig)
                if action == cb.FULL_EXIT:
                    pending_exits.append(sym)
                    stats["full_exits"] += 1
                elif action == cb.PARTIAL_50 and sym not in partial_done:
                    half = pos.qty / 2.0
                    if half > 0:
                        portfolio.reduce(sym, c, T, "partial_50", half)
                        partial_done.add(sym)
                        stats["partial"] += 1

        # ── 3. Ứng viên vào lệnh (khớp tại Open T+1) ──
        room = ts.MAX_POSITIONS - len(portfolio.positions) - len(pending_entries)
        if room > 0 and not emergency:
            cands: list[tuple[float, str]] = []
            for sym in universe.at(T):
                if sym in portfolio.positions or sym in cooldown_until and i < cooldown_until[sym]:
                    continue
                r = _rs(rs, sym, i)
                if r is None or r < te.RS_MIN:
                    continue
                c, h20, m20 = (ma.at(f, sym, i) for f in (ma.close, high20, ma20))
                v, a20 = ma.at(ma.volume, sym, i), ma.at(adv20, sym, i)
                if None in (c, h20, m20, v, a20):
                    continue
                ov = organ.evaluate(sym, T)
                if not arm.organ:
                    # organ_on=False (V1, V2): bỏ CỔNG lọc nhưng GIỮ archetype làm đầu vào
                    # sizing (§3). Không sửa `trend_engine`: chỉ trao một verdict luôn đạt —
                    # khác biệt nằm tường minh ở đây, không nằm trong module đã niêm phong.
                    ov = OrganVerdict(passed=True, reasons=[], organs=ov.organs, archetype=ov.archetype)
                elif arm.organ_mode == ORGAN_MODE_DISTRESSED_ONLY:
                    # V5 (§2b spec v1.1): CHỈ chặn DISTRESSED, bỏ ba ngưỡng BS/Eff/Moat.
                    # `archetype is None` ĐI QUA — đúng nguyên văn luật "Health != DISTRESSED"
                    # đã khai báo trước; số lượt None đếm riêng để biết V5 có thực sự khác V2
                    # hay chỉ suy biến thành V2.
                    is_distressed = ov.archetype == DISTRESSED
                    if is_distressed:
                        stats["candidates_distressed_rejected"] += 1
                    ov = OrganVerdict(
                        passed=not is_distressed,
                        reasons=["Health = DISTRESSED"] if is_distressed else [],
                        organs=ov.organs,
                        archetype=ov.archetype,
                        archetype_confidence=ov.archetype_confidence,
                    )
                feat = te.TrendFeatures(
                    symbol=sym,
                    date=T,
                    close=c,
                    high_20=h20,
                    ma20=m20,
                    volume=v,
                    adv20=a20,
                    rs=r,
                    block_source="NONE",
                    organ=ov,
                )
                if te.entry_verdict(feat).passed:
                    cands.append((r, sym))
                    if arm.organ_mode == ORGAN_MODE_DISTRESSED_ONLY and ov.archetype is None:
                        stats["candidates_archetype_none"] += 1
            cands.sort(key=lambda x: -x[0])
            stats["candidates"] += len(cands)
            pending_entries.extend(s for _, s in cands[:room])

        # ── 4. MTM tại close T ──
        portfolio.mark(T, _closes(ma, i))
        stats["days"] += 1
        n_pos = len(portfolio.positions)
        stats["sum_positions"] += n_pos
        if n_pos:
            stats["days_with_positions"] += 1
        stats["max_positions_held"] = max(stats["max_positions_held"], n_pos)
        if verbose and i % 100 == 0:
            print(f"  {T}  nav={portfolio.nav_history[-1][1]:.2f}  vị thế={len(portfolio.positions)}")

    return stats


def _closes(ma: MarketArrays, i: int) -> dict[str, float]:
    row = ma.close.iloc[i].dropna()
    return {s: float(v) for s, v in row.items() if v > 0}


def _rs(rs: pd.DataFrame, sym: str, i: int) -> float | None:
    if i < 0:
        return None
    try:
        v = rs.iat[i, rs.columns.get_loc(sym)]
    except KeyError, IndexError:
        return None
    return None if pd.isna(v) else float(v)


def _trend_pct(portfolio, ma: MarketArrays, i: int) -> float:
    prices = _closes(ma, i)
    nav = portfolio.nav(prices)
    if nav <= 0:
        return 0.0
    mv = sum(p.qty * prices.get(p.symbol, p.entry_price) for p in portfolio.positions.values())
    return mv / nav * 100.0


def _archetype(organ: OrganFilter, sym: str, T: str) -> str | None:
    """Archetype PIT lấy từ chính verdict organ (đã cache theo (mã, quý) trong OrganFilter).

    Không bọc `try/except Exception`: `OrganFilter.evaluate` đã tự xử lý trạng thái thiếu
    dữ liệu (trả verdict không đạt). Bọc mù ở đây sẽ nuốt lỗi thật và trả `None` im lặng —
    đúng thứ làm archetype âm thầm thành None suốt lần chạy mà không ai biết.
    """
    return organ.evaluate(sym, T).archetype


def _atr_pct(ma: MarketArrays, sym: str, i: int) -> float:
    """ATR/close phiên T (True Range chuẩn, cần close phiên trước)."""
    if i < 1:
        return 0.0
    h, low, c = ma.at(ma.high, sym, i), ma.at(ma.low, sym, i), ma.at(ma.close, sym, i)
    pc = ma.at(ma.close, sym, i - 1)
    if None in (h, low, c, pc) or not c:
        return 0.0
    tr = max(h - low, abs(h - pc), abs(low - pc))
    return tr / c * 100.0


def _atr_pct_ma20(ma: MarketArrays, sym: str, i: int) -> float:
    vals = [_atr_pct(ma, sym, j) for j in range(max(0, i - BASE_WINDOW), i)]
    vals = [v for v in vals if v > 0]
    return sum(vals) / len(vals) if vals else 0.0
