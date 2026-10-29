"""
PRICE PROVENANCE GATE (V1.0) — CỔNG PHÁT HIỆN CHUỖI GIÁ ĐÃ ĐIỀU CHỈNH
PTCK_VNSTOCK Quantitative Research Suite

Status: ACTIVE (fail-closed gate — mọi runner audit cổ tức PHẢI đi qua)

[WHY] Báo cáo thực nghiệm FOC (Pha 1, commit 15e09c8) đã bị chứng minh chạy
trên chuỗi giá `kbs` ĐÃ back-adjust server-side, vi phạm Invariant #1
FAIL_CLOSED_ON_ADJUSTED_PRICE. Khi giá lịch sử đã loại cổ tức mà engine lại
cộng thêm cổ tức tiền mặt ⇒ cổ tức bị ĐẾM HAI LẦN, giá vào lệnh sai, lát cắt
ADV20 sai (volume cũng bị scalẻ theo). Cổng này chặn tái phát bằng BẰNG CHỨNG
VẬT LÝ không thể giả mạo của giá chứng khoán Việt Nam.

[ROOT CAUSE] API IIS của KB Securities (`kbs`) và gap-chart của Vietcap (`vci`)
đều trả lịch sử giá theo kiểu back-adjust multiplicative, neo về thời điểm
truy vấn: P_adj(t) = P_raw(t) × Π(1 − D_i / P_raw,i) với i chạy trên các ngày
GDKHQ SAU t. Hệ quả quan sát được:
  1. Bar trước ngày GDKHQ cuối cùng KHÔNG còn nằm trên lưới tick 100 VND
     (HOSE/UPCoM bắt buộc bội số 100) ⇒ giá trị phân số kiểu 27542.1496.
  2. Volume cũng bị scalẻ (turnover-preserving) ⇒ 299.320 cp thay vì số tròn.
  3. Bar sau ngày GDKHQ cuối cùng là giá thô ⇒ tròn bội 100 tuyệt đối.
  4. Vintage trộn lẫn: cùng một ngày trong DB và API khác ngày cho TỶ LỆ CỐ
     ĐỊNH (FOC 09/04/2021: DB 108.030 vs API 93.526 ⇒ hệ số 1,155×) vì lịch
     sử cũ không được pull lại sau khi có cổ tức mới.

[INVARIANT] assert_raw_price_series() phải được gọi TRƯỚC khi nạp bars vào
DividendAuditEngineV2. Mọi chuỗi không vượt cổng ⇒ AdjustedPriceDetectedError
(fail-closed). Cấm thay ngưỡng để "vượt" cổng: ngưỡng mặc định 1% là dung sai
mất mát làm tròn, KHÔNG phải công tắc tắt.

[WHY] V1.1 (NQ-04, 28/10/2026) — CHẾ ĐỘ `--reconstructed`: quét DB chỉ trả lời
được "vintage trong DB có sạch không" (FOC: KHÔNG, vì DB trộn VCI adjusted
1,15504× với kbs). Chuỗi sẽ thực sự đi vào engine là chuỗi PHỤC NGUYÊN từ MỘT
vintage KBS duy nhất (`raw_price_reconstruction`), nên cổng phải phán quyết
được trên chính chuỗi đó: 100% close bội lưới tick + gap identity qua biên độ
phiên GDKHQ. Chế độ này KHÔNG nới ngưỡng của chế độ DB — nó thêm một đường
kiểm tra CHẶT HƠN (max_off_tick_ratio = 0.0, tức 100% bars, đúng DoD NQ-04).
"""

from __future__ import annotations

import json
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# [WHY] Hai đường import (repo-root `backend.src.research.*` và `src.research.*`
# khi chạy từ backend/): runner audit cũ + pytest dùng cả hai; hard-code một
# đường làm gãy đường còn lại (đo thực tế 28/10/2026: ModuleNotFoundError).
try:
    from src.research.cash_event_registry import (
        CASH_EVENTS,
        DAILY_LIMIT_PCT,
        LIMIT_BAND_EVENTS,
        TICK_SIZES,
    )
    from src.research.raw_price_reconstruction import (
        CashEvent,
        FactorSolveError,
        reconstruct_raw_prices,
    )
except ImportError:  # pragma: no cover - chỉ xảy ra khi import bằng `backend.`
    from backend.src.research.cash_event_registry import (
        CASH_EVENTS,
        DAILY_LIMIT_PCT,
        LIMIT_BAND_EVENTS,
        TICK_SIZES,
    )
    from backend.src.research.raw_price_reconstruction import (
        CashEvent,
        FactorSolveError,
        reconstruct_raw_prices,
    )

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Lưới tick tối thiểu của HOSE/UPCoM (VND). Giá thô LUÔN là bội số của 100.
TICK_SIZE_VND = 100.0

# Dung sai mặc định: ≤1% bar lệch lưới ⇒ có thể là làm tròn/đàm phán lẻ còn
# sót. Trên 1% ⇒ chuỗi điều chỉnh (fail-closed).
DEFAULT_MAX_OFF_TICK_RATIO = 0.01

# Dung sai kiểm tra khoảng rơi ngày GDKHQ (tỷ lệ tuyệt đối).
DEFAULT_GAP_TOLERANCE = 0.02

# Vị từ chuẩn — chỉ đọc bar hợp lệ, KHÔNG đụng is_stale (panel lịch sử).
VALID_ROW_SQL = (
    "(close IS NOT NULL AND close > 0 "
    "AND low IS NOT NULL AND low <= close "
    "AND high IS NOT NULL AND close <= high "
    "AND volume IS NOT NULL AND volume >= 0)"
)


class AdjustedPriceDetectedError(RuntimeError):
    """Chuỗi giá bị nghi là ĐÃ ĐIỀU CHỈNH — chặn trước khi audit (fail-closed)."""


@dataclass(frozen=True)
class TickComplianceReport:
    """Kết quả kiểm tra lưới tick của một chuỗi giá."""

    symbol: str
    n_bars: int
    n_off_tick: int
    off_tick_ratio: float
    is_tick_clean: bool
    first_date: object | None = None  # date | None
    last_date: object | None = None


@dataclass(frozen=True)
class ExDateGapVerdict:
    """Phán quyết đối chiếu khoảng rơi giá ngày GDKHQ với cổ tức công bố."""

    expected_drop_ratio: float
    observed_drop_ratio: float
    abs_error: float
    is_consistent: bool


def tick_compliance(
    symbol: str,
    closes: list[float] | None,
    tick_size: float = TICK_SIZE_VND,
    max_off_tick_ratio: float = DEFAULT_MAX_OFF_TICK_RATIO,
    first_date=None,
    last_date=None,
) -> TickComplianceReport:
    """Đo tỷ lệ đóng cửa KHÔNG phải bội số của lưới tick.

    [INVARIANT] Chuỗi rỗng/None ⇒ ValueError (fail-closed: không có bằng chứng
    thì không qua cổng, tuyệt đối không trả "sạch" cho dữ liệu thiếu).
    """
    if not closes:
        raise ValueError(f"FAIL_CLOSED: chuỗi giá của {symbol} rỗng — không đủ bằng chứng để phán quyết.")

    n_off = sum(1 for c in closes if abs((c / tick_size) - round(c / tick_size)) > 1e-6)
    ratio = n_off / len(closes)
    return TickComplianceReport(
        symbol=symbol,
        n_bars=len(closes),
        n_off_tick=n_off,
        off_tick_ratio=ratio,
        is_tick_clean=ratio <= max_off_tick_ratio,
        first_date=first_date,
        last_date=last_date,
    )


def assert_raw_price_series(report: TickComplianceReport) -> TickComplianceReport:
    """Chặn đứng chuỗi giá đã điều chỉnh. Ném AdjustedPriceDetectedError nếu vi phạm."""
    if not report.is_tick_clean:
        raise AdjustedPriceDetectedError(
            f"ADJUSTED_PRICE_DETECTED: {report.symbol} có {report.n_off_tick}/{report.n_bars} bar "
            f"({report.off_tick_ratio * 100:.1f}%) đóng cửa KHÔNG phải bội số 100 VND. "
            "Chuỗi này nhiều khả năng đã back-adjust server-side (cổ tức đã bị loại khỏi giá). "
            "CẤP BẮT BUỘC: lấy lại giá thô hoặc dựng lại từ corporate actions có provenance "
            "trước khi chạy bất kỳ audit cổ tức nào (Invariant #1)."
        )
    return report


def exdate_gap_check(
    prev_close_raw: float,
    ex_date_close_raw: float,
    cash_per_share: float,
    tolerance: float = DEFAULT_GAP_TOLERANCE,
) -> ExDateGapVerdict:
    """Đối chiếu khoảng rơi giá qua ngày GDKHQ với cổ tức tiền mặt công bố.

    [WHY] Bằng chứng độc lập thứ hai với lưới tick: trên giá THÔ, cổ phiếu rơi
    approximately đúng tỷ lệ D/P ngay khi hết quyền (bỏ qua biến động thị trường
    trong biên độ tolerance). Giá đã điều chỉnh thì khoảng rơi này KHÔNG còn.
    [INVARIANT] prev_close_raw <= 0 hoặc cash_per_share < 0 ⇒ ValueError.
    """
    if prev_close_raw <= 0:
        raise ValueError("FAIL_CLOSED: giá đóng cửa trước GDKHQ phải > 0.")
    if cash_per_share < 0:
        raise ValueError("FAIL_CLOSED: cổ tức tiền mặt không được âm.")

    expected = cash_per_share / prev_close_raw
    observed = 1.0 - (ex_date_close_raw / prev_close_raw)
    err = abs(observed - expected)
    return ExDateGapVerdict(
        expected_drop_ratio=expected,
        observed_drop_ratio=observed,
        abs_error=err,
        is_consistent=err <= tolerance,
    )


@dataclass(frozen=True)
class ReconstructedVerdict:
    """Phán quyết trên chuỗi ĐÃ PHỤC NGUYÊN (chế độ `--reconstructed`)."""

    symbol: str
    n_bars: int
    n_off_tick: int | None
    max_abs_delta: float | None
    is_certified: bool
    reasons: tuple[str, ...]
    segments: tuple[tuple[object, object, float], ...] = ()
    gap_deltas: tuple[float, ...] = ()
    # Nhãn tầng nghiệm thu từng event: "in-range" (band OHLC) hoặc "limit-band".
    gap_checks: tuple[str, ...] = ()


def load_vintage_ohlcv(symbol: str, bars_dir: Path | str) -> tuple[list, list[float], list[float], list[float]]:
    """Nạp OHLC từ MỘT vintage KBS (`kbs_<sym>_2021_2026.json`) — fail-closed.

    [WHY] Phục nguyên chỉ đúng khi toàn chuỗi đến từ MỘT lần pull API: giá trị
    4 chữ số thập phân của KBS là đủ để solve factor grid; trộn vintage (DB VCI
    1,15504×) sẽ tạo ra "grid giả" và factor sai. Hàm này cố tình KHÔNG đọc
    `daily_ohlcv` để không bao giờ trộn nguồn.
    [INVARIANT] Thiếu file ⇒ FileNotFoundError; bar hỏng/phi lý (low > high,
    giá ≤ 0), trùng ngày, hoặc không tăng dần ⇒ ValueError. Không im lặng bỏ
    bar (Missing > Fake).
    [WHY] KHÔNG kiểm `low ≤ close ≤ high` toàn chuỗi: đo thực tế vintage FOC
    2021–2026 có 7/1452 bar vi phạm (KBS trả o=h=l theo mức adjust cũ, close
    chính xác 4dp, lệch ≤0.2%) tại các bar KHÔNG dùng làm bằng chứng; chặn cả
    chuỗi sẽ chặn oan 1452 bars vì 7 bar không liên quan. Bar GDKHQ (bằng chứng
    duy nhất dùng low/high) được kiểm chặt riêng trong `reconstruct_raw_prices`.
    """
    path = Path(bars_dir) / f"kbs_{symbol.lower()}_2021_2026.json"
    if not path.exists():
        raise FileNotFoundError(f"không có vintage OHLC cho {symbol}: {path}")
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not rows:
        raise ValueError(f"vintage {path.name} rỗng — không có bằng chứng để phục nguyên")

    dates: list = []
    closes: list[float] = []
    lows: list[float] = []
    highs: list[float] = []
    for r in rows:
        try:
            d = datetime.strptime(r["t"][:10], "%Y-%m-%d").date()
            o, h, lo, c = float(r["o"]), float(r["h"]), float(r["l"]), float(r["c"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"bar hỏng trong {path.name}: {r!r} ({exc})") from exc
        if not all(x > 0 and x == x and x != float("inf") for x in (o, h, lo, c)):
            raise ValueError(f"{d}: giá không hợp lệ (o={o}, h={h}, l={lo}, c={c}) trong {path.name}")
        if lo > h:
            raise ValueError(f"{d}: bar bất khả (low={lo} > high={h}) trong {path.name}")
        dates.append(d)
        closes.append(c)
        lows.append(lo)
        highs.append(h)

    if len(set(dates)) != len(dates):
        raise ValueError(f"{path.name}: có ngày trùng — vintage bị ghép nhiều lần pull")
    if any(b <= a for a, b in zip(dates, dates[1:])):
        raise ValueError(f"{path.name}: chuỗi ngày không tăng dần")
    return dates, closes, lows, highs


def verify_reconstructed_prices(
    symbol: str,
    dates: list,
    closes: list[float],
    lows: list[float],
    highs: list[float],
    events: tuple[CashEvent, ...],
    tick_size: float = TICK_SIZE_VND,
) -> ReconstructedVerdict:
    """Phục nguyên rồi phán quyết: 100% bars bội lưới tick + gap qua biên phiên.

    [WHY] DoD NQ-04 đòi HAI điều kiện đồng thời: (i) factor từng segment đưa
    TOÀN BỘ bars về bội 100 VND — nên `max_off_tick_ratio=0.0`, không dùng dung
    sai 1% của chế độ DB; (ii) khoảng rơi qua GDKHQ khớp cổ tức trong biên độ
    phiên ex (Δ_market được phép, nhưng phải nằm trong [low_ex, high_ex]).
    [INVARIANT] Không có event provenance ⇒ ValueError (không chứng nhận mù).
    Segment không giải được ⇒ verdict KHÔNG certified, không raise ra ngoài
    (cổng phải báo cáo được lý do thay vì crash giữa script).
    """
    if not events:
        raise ValueError(f"FAIL_CLOSED: {symbol} không có event cổ tức đã xác minh — không thể chứng nhận gap identity")
    try:
        report = reconstruct_raw_prices(
            symbol,
            dates,
            closes,
            events=events,
            tick=tick_size,
            lows=lows,
            highs=highs,
            limit_pct=DAILY_LIMIT_PCT.get(symbol),
            limit_band_events=tuple(LIMIT_BAND_EVENTS.get(symbol, {})),
        )
    except FactorSolveError as exc:
        return ReconstructedVerdict(
            symbol=symbol,
            n_bars=len(dates),
            n_off_tick=None,
            max_abs_delta=None,
            is_certified=False,
            reasons=(f"FACTOR_SOLVE_ERROR: {exc}",),
        )

    tick = tick_compliance(
        f"{symbol}:reconstructed",
        list(report.raw_closes),
        tick_size=tick_size,
        max_off_tick_ratio=0.0,  # [WHY] DoD: 100% bars, không dung sai
        first_date=dates[0] if dates else None,
        last_date=dates[-1] if dates else None,
    )
    reasons = list(report.anomalies)
    if not tick.is_tick_clean:
        reasons.append(f"{tick.n_off_tick}/{tick.n_bars} bars lệch lưới {tick_size:g} VND (DoD đòi 100% bars bội lưới)")
    max_abs = max((abs(d) for d in report.gap_deltas), default=None)
    return ReconstructedVerdict(
        symbol=symbol,
        n_bars=report.n_bars,
        n_off_tick=tick.n_off_tick,
        max_abs_delta=max_abs,
        is_certified=not reasons,
        reasons=tuple(reasons),
        segments=report.segments,
        gap_deltas=report.gap_deltas,
        gap_checks=report.gap_checks,
    )


def scan_symbol(
    db_path: Path | str,
    symbol: str,
    start: str | None = None,
    end: str | None = None,
) -> dict:
    """Quét compliance theo NĂM cho một symbol trong screener_cache.db.

    Trả về {"overall": TickComplianceReport, "by_year": {year: TickComplianceReport},
            "first_date": date, "last_date": date}. Không ghi — chỉ đọc (audit).
    """
    conn = sqlite3.connect(str(db_path))
    try:
        query = f"SELECT date, close FROM daily_ohlcv WHERE symbol = ? AND {VALID_ROW_SQL} "
        params: list[object] = [symbol]
        if start:
            query += "AND date >= ? "
            params.append(start)
        if end:
            query += "AND date <= ? "
            params.append(end)
        query += "ORDER BY date ASC"
        rows = conn.execute(query, params).fetchall()
    finally:
        conn.close()

    closes = [float(r[1]) for r in rows]
    dates = [datetime.strptime(r[0], "%Y-%m-%d").date() for r in rows]
    overall = tick_compliance(
        symbol,
        closes,
        first_date=dates[0] if dates else None,
        last_date=dates[-1] if dates else None,
    )

    by_year: dict[int, TickComplianceReport] = {}
    year_buckets: dict[int, list[float]] = {}
    for d, c in zip(dates, closes):
        year_buckets.setdefault(d.year, []).append(c)
    for y in sorted(year_buckets):
        by_year[y] = tick_compliance(f"{symbol}:{y}", year_buckets[y])

    return {
        "overall": overall,
        "by_year": by_year,
        "first_date": dates[0] if dates else None,
        "last_date": dates[-1] if dates else None,
    }


def _run_reconstructed(symbols: list[str], bars_dir: Path) -> int:
    """Chạy cổng trên chuỗi PHỤC NGUYÊN từ vintage KBS (NQ-04 DoD)."""
    print("=" * 80)
    print("PRICE PROVENANCE GATE — CHẾ ĐỘ RECONSTRUCTED (100% BARS BỘI TICK)")
    print(f"Vintage OHLC: {bars_dir}  (một nguồn duy nhất — không trộn DB)")
    print("Event provenance: backend/src/research/cash_event_registry.py")
    print("=" * 80)

    exit_code = 0
    for sym in symbols:
        events = CASH_EVENTS.get(sym)
        tick_size = TICK_SIZES.get(sym)
        if not events or tick_size is None:
            print(f"\n[{sym}] FAIL_CLOSED: không có registry cổ tức/lưới tick đã xác minh cho {sym}.")
            exit_code = max(exit_code, 2)
            continue
        try:
            dates, closes, lows, highs = load_vintage_ohlcv(sym, bars_dir)
        except (FileNotFoundError, ValueError) as exc:
            print(f"\n[{sym}] FAIL_CLOSED: {exc}")
            exit_code = max(exit_code, 2)
            continue

        try:
            verdict = verify_reconstructed_prices(sym, dates, closes, lows, highs, events, tick_size=tick_size)
        except ValueError as exc:
            print(f"\n[{sym}] FAIL_CLOSED: {exc}")
            exit_code = max(exit_code, 2)
            continue

        print(
            f"\n[{sym}] {dates[0]} → {dates[-1]} | {verdict.n_bars} bars | "
            f"lưới tick {tick_size:g} VND | {len(events)} cổ tức tiền mặt"
        )
        for s, e, f in verdict.segments:
            print(f"  segment {s} .. {e}: factor={f:.8f}")
        if verdict.gap_deltas:
            print("  gap identity (Δ_market = drop − cash; nhãn = tầng nghiệm thu):")
            for ev, delta, tier in zip(events, verdict.gap_deltas, verdict.gap_checks):
                mark = "" if tier == "in-range" else "  ⇐ TRẦN/SÀN (yếu hơn)"
                print(f"    {ev.ex_date}: cash={ev.cash:>10,.1f} | Δ_market={delta:>+9,.1f} VND | {tier}{mark}")
            print(f"  max |Δ_market| = {verdict.max_abs_delta:,.1f} VND (biên phiên GDKHQ)")

        n_limit = sum(1 for t in verdict.gap_checks if t == "limit-band")
        if verdict.is_certified and n_limit:
            # [WHY] Minh bạch mức độ bằng chứng: tầng trần/sàn không phân biệt
            # được cổ tức sai ≤ limit% với biến động thị trường, nên danh sách
            # event + số lượng phải hiện ra, không được gộp vào "PASS" chung.
            print(
                f"  LƯU Ý BẰNG CHỨNG: {n_limit}/{len(verdict.gap_checks)} event nghiệm thu bằng "
                f"tầng trần/sàn ±{100 * DAILY_LIMIT_PCT.get(sym, 0.0):.0f}% (yếu hơn band OHLC); "
                "các event còn lại đạt band OHLC."
            )

        if verdict.is_certified:
            print(
                f"  ==> PHÁN QUYẾT: PASS — {verdict.n_off_tick}/{verdict.n_bars} bar lệch lưới; "
                f"100% bars bội {tick_size:g} VND, gap identity qua biên phiên GDKHQ."
            )
        else:
            print(f"  ==> PHÁN QUYẾT: FAIL — chuỗi {sym} KHÔNG đủ bằng chứng phục nguyên:")
            for reason in verdict.reasons:
                print(f"    ! {reason}")
            exit_code = max(exit_code, 1)

    return exit_code


def main(argv: list[str] | None = None) -> int:
    """CLI (chạy từ thư mục `backend/`, PYTHONPATH sạch):
        python -m src.research.price_provenance_gate --symbols VEA,FOC
        python -m src.research.price_provenance_gate --symbols FOC --reconstructed

    Thêm `--reconstructed` (NQ-04): phán quyết trên chuỗi phục nguyên từ vintage
    KBS trong `--bars-dir` (mặc định backend/data) thay vì quét DB.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    symbols: list[str] = ["VEA", "FOC"]
    db_path = Path(__file__).resolve().parent.parent.parent / "data" / "screener_cache.db"
    bars_dir = Path(__file__).resolve().parent.parent.parent / "data"
    reconstructed = False

    i = 0
    while i < len(args):
        if args[i] == "--symbols" and i + 1 < len(args):
            symbols = [s.strip().upper() for s in args[i + 1].split(",") if s.strip()]
            i += 2
        elif args[i] == "--db" and i + 1 < len(args):
            db_path = Path(args[i + 1])
            i += 2
        elif args[i] == "--bars-dir" and i + 1 < len(args):
            bars_dir = Path(args[i + 1])
            i += 2
        elif args[i] == "--reconstructed":
            reconstructed = True
            i += 1
        else:
            print(f"Tham số không hợp lệ: {args[i]}")
            return 2

    if reconstructed:
        return _run_reconstructed(symbols, bars_dir)

    if not db_path.exists():
        print(f"FAIL_CLOSED: không tìm thấy CSDL {db_path}")
        return 2

    print("=" * 80)
    print("PRICE PROVENANCE GATE — KIỂM TRA LƯỚI TICK 100 VND (NGUỒN: screener_cache.db)")
    print(f"CSDL: {db_path}")
    print("=" * 80)

    exit_code = 0
    for sym in symbols:
        rep = scan_symbol(db_path, sym)
        ov = rep["overall"]
        print(f"\n[{sym}] {rep['first_date']} → {rep['last_date']} | {ov.n_bars} phiên sạch (VALID_ROW_SQL)")
        for y, yr in rep["by_year"].items():
            status = "RAW?" if yr.is_tick_clean else "ADJUSTED(!)"
            print(f"  {y}: {yr.n_bars:3d} bar | lệch lưới: {yr.n_off_tick:3d} ({yr.off_tick_ratio * 100:5.1f}%) | {status}")
        if ov.is_tick_clean:
            print(f"  ==> PHÁN QUYẾT: PASS — chuỗi {sym} nằm trên lưới tick (không phát hiện điều chỉnh).")
        else:
            print(
                f"  ==> PHÁN QUYẾT: FAIL — chuỗi {sym} NGHI LÀ ĐÃ ĐIỀU CHỈNH "
                f"({ov.off_tick_ratio * 100:.1f}% bar lệch lưới). KHÔNG được dùng cho audit cổ tức."
            )
            exit_code = 1

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
