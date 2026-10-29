"""TẦNG 3 — KIỂM ĐỊNH CHÉO HAI VENDOR (KBS vintage vs DB VCI).

[WHY] Tầng 1 (lưới tick) chỉ chứng minh "chuỗi phục nguyên nằm trên lưới giá",
KHÔNG chứng minh khoản cổ tức khai báo là đúng. Tầng 2 (band OHLC ngày GDKHQ)
mạnh hơn nhưng chỉ dùng được khi phiên ex giao dịch HAI PHÍA giá tham chiếu;
đo thật trên PAT: 4/9 event rơi vào phiên gap (cả dải một phía) ⇒ phải nghiệm
thu bằng tầng trần/sàn ±15% (yếu hơn band 5-10×). Trước module này, các ca gap
ấy chỉ có "niềm tin vào VCI IQ", tức là niềm tin vào CHÍNH nguồn đang bị kiểm
toán — lập luận vòng tròn.

[ROOT CAUSE] Hai vendor (KBS IIS, VCI) đều xuất chuỗi back-adjusted multiplicative
theo cùng một công thức: P_adj(t) = P_raw(t) × Π f_i. Nếu CẢ HAI áp cùng khoản
cổ tức tại cùng ex_date thì tỷ số R(t) = KBS(t)/DB(t) là HẰNG SỐ qua ex_date.
Nếu chỉ một bên áp (bên kia chưa pull lại lịch sử), R nhảy đúng một bước bằng
f = 1 − cash/P_pre. Vậy BƯỚC NHẢY CỦA TỶ SỐ là phép đo cash hoàn toàn độc lập
với giá khớp và độc lập với band OHLC.

[INVARIANT]
(1) Không bao giờ suy cash từ im lặng: mọi kết luận phải kèm số đo `step_pct`
    và `implied_cash` để người đọc kiểm lại được.
(2) `agree` chỉ khi đo được bước nhảy ≈ 0 (|step| < 0,3%); thiếu bar một phía
    ⇒ `insufficient-data` + `implied_cash=None`, TUYỆT ĐỐI không mặc định agree
    (Missing > Fake).
(3) Bước nhảy đo ra cash khác khai báo quá 3% ⇒ `conflict` ⇒ raise
    `ProvenanceConflictError` (fail-closed). Đây là chốt chặn chống "vá quá tay":
    nếu ai đó sửa cash trong registry cho vừa band OHLC, tầng này lộ ngay.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

try:
    from src.database.data_validity import VALID_ROW_SQL
except ImportError:  # pragma: no cover - chỉ xảy ra khi import bằng `backend.`
    from backend.src.database.data_validity import VALID_ROW_SQL

# [WHY] Ngưỡng "tỷ số phẳng": đo thật 8/8 event cũ của PAT và 9/10 của DSN cho
# |step| ≤ 0,009% (sai số làm tròn 4 chữ số thập phân của KBS). Event thật sự
# lệch đo được 4,342-4,361%, cách ngưỡng 14 lần ⇒ 0,3% là khoảng trống an toàn
# giữa nhiễu làm tròn và một bước điều chỉnh thật. Nới ngưỡng này ⇒ một nửa cổ
# tức bị bỏ qua mà vẫn được nhãn "agree".
AGREE_STEP_PCT_MAX = 0.3

# [WHY] Dung sai đối chiếu `implied_cash` với cash khai báo: PAT 2026-09-15 cho
# implied 2.926 vs khai 2.900 (lệch 0,9%) do giá tham chiếu lấy từ bar đóng cửa
# gần nhất chứ không phải giá ATO của phiên ex. 3% đủ chứa sai số lấy mẫu đó
# nhưng không đủ chứa một khoản cổ tức khác (ca conflict trong test lệch 5×).
IMPLIED_CASH_TOLERANCE = 0.03

# [WHY] Cửa sổ lấy theo SỐ BAR gần ex nhất (không phải số ngày): DB VCI có lỗ
# hổng phủ thật — đo 29/10/2026, DSN thiếu 39 bar trong 2026-04..2026-07 (tháng
# 2026-05 chỉ còn 2/20 bar), nên cửa sổ 10 ngày lịch quanh ex 2026-05-18 chỉ có
# 1 bar mỗi phía ⇒ mọi event rơi vào vùng lỗ hổng sẽ thành "insufficient-data"
# oan. Lấy 10 bar gần nhất mỗi phía vẫn giữ được trung bình khử nhiễu tick.
MAX_BARS_PER_SIDE = 10

# [WHY] Hai đầu cửa sổ bị chặn bởi event KỀ (trước/sau) để không bao giờ trộn
# hai segment factor khác nhau — trộn sẽ tạo bước nhảy giả. Chặn cứng 120 ngày
# chỉ để event đầu/cuối chuỗi không kéo bar từ segment quá xa (PAT event đầu
# cách bar đầu 48 ngày, DSN event đầu cách 87 ngày ⇒ 120 ngày là đủ).
MAX_SPAN_DAYS = 120

# [WHY] Tối thiểu 3 bar mỗi phía: với 2 bar, một bar lỗi dữ liệu là đủ đổi dấu
# bước nhảy. Dưới ngưỡng này trả `insufficient-data` thay vì đoán.
MIN_BARS = 3


class ProvenanceConflictError(RuntimeError):
    """Bước nhảy tỷ số hai vendor không khớp cổ tức đã khai báo (fail-closed)."""


@dataclass(frozen=True)
class EventCrossCheck:
    """Kết quả kiểm chéo MỘT event. `implied_cash=None` khi không đo được."""

    ex_date: date
    cash: float
    status: str  # agree | vendor-lag | conflict | insufficient-data
    step_pct: float | None = None
    implied_cash: float | None = None
    n_before: int = 0
    n_after: int = 0
    lagging_vendor: str = ""
    note: str = ""


def _as_pairs(events) -> list[tuple[date, float]]:
    """Chuẩn hoá event: nhận cả `CashEvent` (`.ex_date`/`.cash`) lẫn tuple."""
    out: list[tuple[date, float]] = []
    for ev in events:
        if hasattr(ev, "ex_date") and hasattr(ev, "cash"):
            out.append((ev.ex_date, float(ev.cash)))
        else:
            ex, cash = ev
            out.append((ex, float(cash)))
    return out


def _median(values: list[float]) -> float:
    """Trung vị — bộ ước lượng robust cho tỷ số hai vendor.

    [WHY] Đo thật DSN quanh 2026-05-18: cửa sổ SAU ex có 4/10 bar mà close DB
    lệch KBS tới ±1,2% (hệ quả ingest một phần trong lỗ hổng phủ 2026-04..07).
    Trung bình log bị các bar đó kéo bước nhảy từ 4,34% xuống 4,14% ⇒ implied
    cash 1.530 thay vì 1.600, đủ để gắn nhãn `conflict` SAI cho một event đúng.
    Tỷ số là HẰNG SỐ trong một segment factor, nên nhiễu ingest là outlier một
    phía — trung vị khử được mà không nới ngưỡng.
    """
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    return ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2.0


def cross_vendor_steps(
    symbol: str,
    events,
    kbs_closes: dict[date, float],
    db_closes: dict[date, float],
    *,
    max_bars: int = MAX_BARS_PER_SIDE,
    min_bars: int = MIN_BARS,
) -> list[EventCrossCheck]:
    """Đo bước nhảy tỷ số KBS/DB tại từng ex_date và phân loại.

    [WHY] Tỷ số chỉ tính trên bar CÓ MẶT Ở CẢ HAI nguồn (giao): so bar lệch ngày
    sẽ trộn "hai vendor lệch lịch nghỉ" vào bước nhảy, tạo conflict giả.
    """
    # [WHY] Lọc bar giá ≤ 0 trước khi lấy log: log(0) là ValueError, còn bỏ qua
    # im lặng thì một bar rác sẽ kéo trung bình lệch mà không ai biết.
    common = sorted(d for d in (set(kbs_closes) & set(db_closes)) if kbs_closes[d] > 0 and db_closes[d] > 0)
    if not common:
        raise ValueError(f"FAIL_CLOSED: {symbol} không có bar chung giữa KBS và DB — không thể kiểm chéo")

    ratios = {d: kbs_closes[d] / db_closes[d] for d in common}
    checks: list[EventCrossCheck] = []
    pairs = sorted(_as_pairs(events), key=lambda p: p[0])

    for i, (ex, cash) in enumerate(pairs):
        # [WHY] Chặn hai đầu bởi event kề: tỷ số chỉ phẳng TRONG một segment
        # factor, nên cửa sổ vắt qua event khác sẽ báo "bước nhảy" của event đó.
        lower = pairs[i - 1][0] if i > 0 else ex - timedelta(days=MAX_SPAN_DAYS)
        upper = pairs[i + 1][0] if i + 1 < len(pairs) else ex + timedelta(days=MAX_SPAN_DAYS)
        before_dates = [d for d in common if lower < d < ex][-max_bars:]
        after_dates = [d for d in common if ex <= d < upper][:max_bars]
        if len(before_dates) < min_bars or len(after_dates) < min_bars:
            checks.append(
                EventCrossCheck(
                    ex_date=ex,
                    cash=cash,
                    status="insufficient-data",
                    n_before=len(before_dates),
                    n_after=len(after_dates),
                    note=(
                        f"chỉ {len(before_dates)} bar trước / {len(after_dates)} bar sau ex "
                        f"(cần ≥{min_bars} mỗi phía) — KHÔNG kết luận"
                    ),
                )
            )
            continue

        q = _median([ratios[d] for d in after_dates]) / _median([ratios[d] for d in before_dates])
        step_pct = (q - 1.0) * 100.0

        if abs(step_pct) < AGREE_STEP_PCT_MAX:
            checks.append(
                EventCrossCheck(
                    ex_date=ex,
                    cash=cash,
                    status="agree",
                    step_pct=step_pct,
                    n_before=len(before_dates),
                    n_after=len(after_dates),
                    note="tỷ số phẳng qua ex — hai vendor áp cùng khoản điều chỉnh",
                )
            )
            continue

        last_before = before_dates[-1]
        if q > 1.0:
            # DB chưa áp kỳ này (ratio nhảy lên): giá tham chiếu thô là close DB.
            lagging = "DB-VCI"
            p_ref = db_closes[last_before]
            implied = p_ref * (1.0 - 1.0 / q)
        else:
            # KBS chưa áp (ratio nhảy xuống): giá tham chiếu thô là close KBS.
            lagging = "KBS"
            p_ref = kbs_closes[last_before]
            implied = p_ref * (1.0 - q)

        if implied <= 0:
            checks.append(
                EventCrossCheck(
                    ex_date=ex,
                    cash=cash,
                    status="conflict",
                    step_pct=step_pct,
                    implied_cash=implied,
                    n_before=len(before_dates),
                    n_after=len(after_dates),
                    lagging_vendor=lagging,
                    note="bước nhảy ngược dấu khoản cổ tức ⇒ dữ liệu một vendor sai",
                )
            )
            continue

        rel = abs(implied - cash) / cash if cash else float("inf")
        status = "vendor-lag" if rel <= IMPLIED_CASH_TOLERANCE else "conflict"
        checks.append(
            EventCrossCheck(
                ex_date=ex,
                cash=cash,
                status=status,
                step_pct=step_pct,
                implied_cash=implied,
                n_before=len(before_dates),
                n_after=len(after_dates),
                lagging_vendor=lagging,
                note=(
                    f"{lagging} chưa điều chỉnh kỳ này; bước nhảy quy ra "
                    f"{implied:,.0f} đ/cp vs khai báo {cash:,.0f} (lệch {rel * 100:.2f}%)"
                ),
            )
        )

    return checks


def assert_no_conflict(checks: list[EventCrossCheck]) -> None:
    """Raise nếu có event `conflict`. `insufficient-data` KHÔNG raise ở đây.

    [WHY] Hai loại lỗi khác nhau về cách xử lý: `conflict` = hai nguồn nói khác
    nhau ⇒ cấm certify ngay; `insufficient-data` = chưa đo được ⇒ cổng phải báo
    FAIL vì thiếu bằng chứng, nhưng crash ở đây sẽ biến "chưa biết" thành lỗi
    hạ tầng, che mất việc phải đi backfill bar.
    """
    bad = [c for c in checks if c.status == "conflict"]
    if bad:
        detail = "; ".join(f"{c.ex_date} (khai {c.cash:,.0f} vs implied {c.implied_cash:,.0f})" for c in bad)
        raise ProvenanceConflictError(f"FAIL_CLOSED: {len(bad)} event cổ tức có bước nhảy tỷ số vendor không khớp: {detail}")


def format_cross_vendor_table(symbol: str, checks: list[EventCrossCheck]) -> list[str]:
    """Dòng báo cáo cho cổng — in cả số đo, không chỉ nhãn."""
    lines = [f"  kiểm chéo vendor KBS/VCI ({symbol}) — tầng 3, độc lập với band OHLC:"]
    for c in checks:
        step = "—" if c.step_pct is None else f"{c.step_pct:+.3f}%"
        implied = "—" if c.implied_cash is None else f"{c.implied_cash:,.0f}"
        lines.append(f"    {c.ex_date}: cash={c.cash:>10,.0f} | bước tỷ số={step:>9} | implied={implied:>10} | {c.status}")
    return lines


def load_db_closes(symbol: str, db_path: Path | str) -> dict[date, float]:
    """Nạp close DB VCI cho kiểm chéo.

    [WHY] Chế độ PANEL LỊCH SỬ (AGENTS luật B): chỉ `VALID_ROW_SQL`, KHÔNG lọc
    `is_stale` — `is_stale` là khái niệm live/PIT, dùng ở đây sẽ loại bỏ bar lịch
    sử hợp lệ và làm cửa sổ kiểm chéo rỗng. Vẫn fail-closed nếu không có bar.
    """
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = con.execute(
            f"SELECT date, close FROM daily_ohlcv WHERE symbol = ? AND {VALID_ROW_SQL} ORDER BY date ASC",
            (symbol,),
        ).fetchall()
    finally:
        con.close()
    if not rows:
        raise ValueError(f"FAIL_CLOSED: DB không có bar hợp lệ cho {symbol} — không thể kiểm chéo")
    return {date.fromisoformat(d): float(c) for d, c in rows}
