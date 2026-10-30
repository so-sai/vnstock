"""Cổng chất lượng + tiện ích cho backfill chuỗi vĩ mô lịch sử (`macro_history`).

[WHY] NQ-10/NQ-HĐKT (30/10/2026) ra lệnh kéo full history `XAGUSD` (N ≥ 1.400
phiên) TRƯỚC khi dựng bất kỳ tham số GSR/cointegration/beta nào, vì 318 quan sát
hiện có là mẫu cụt. Nhưng "tải được nhiều dòng" không đồng nghĩa "chuỗi hợp lệ":
vendor có thể trả 0.0 làm sentinel, trả bar trùng, hoặc — nguy hiểm nhất — trả
chuỗi mà các ngày cũ KHÔNG còn khớp giá trị đã lưu (đổi quy ước điều chỉnh hợp
đồng tương lai). Module này là cổng nằm GIỮA download và `save_data_upsert`.

[ROOT CAUSE] `cmd_backfill_macro` cũ: (a) chỉ có `--days` cho toàn bộ 24 ticker
nên không chọn được 1 chuỗi; (b) không có phép kiểm nào trên dữ liệu tải về;
(c) mặc định `period="252d"` — đủ cho cập nhật hàng ngày nhưng không đủ cho bất
kỳ kiểm định thống kê nào, và vì không có ngưỡng hàng tối thiểu nên không ai
phát hiện ra điều đó.

[INVARIANT]
(1) Không có "vá quá tay": chặn `value == 0.0` (sentinel missing) nhưng KHÔNG
    chặn `value < 0`, vì WTI 2020-04-20 đóng cửa −37,63 USD là dữ liệu thật.
(2) Vùng chồng lấn giữa bản tải mới và dữ liệu đã lưu phải khớp trong ngưỡng
    khai báo; lệch quá ngưỡng ⇒ DỪNG (đây là bẫy scale-switch của E19 áp dụng
    trước khi ghi, thay vì phát hiện sau).
(3) Mọi ngưỡng đều là tham số có tên + `[WHY]` đo được, không magic number.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

import pandas as pd

# SSOT: bảng mã nằm ở `daily_updater` (dùng cho cả cập nhật hằng ngày) — import
# để không tồn tại bản sao thứ hai. Đo 30/10/2026: 24 ticker, import 0,45s.
from src.daily_updater import MACRO_TICKERS as TICKERS

# [WHY] 0,5% — ngưỡng phát hiện "bản tải mới đổi quy ước giá". Sai số làm tròn
# 6 chữ số thập phân ở đây là ~1e-7 (vô hại); còn đổi thang/roll hợp đồng tương
# lai đo thật ở ca DSN E19 là 4,34% — cách ngưỡng gần 9 lần. Chọn 0,5% để vẫn
# bắt được cả thay đổi nhỏ 1% (không đợi tới 4%).
DEFAULT_OVERLAP_TOL_PCT = 0.5

# [WHY] 1.000 phiên — dưới mức này, ADF/Hurst/OU đều cho khoảng tin cậy rộng đến
# mức không thể phân biệt random walk với mean-reversion (NQ-10 nêu rõ N > 1.000).
# Chặn ở bước GHI để lần sau không ai dựng tham số trên chuỗi 318 dòng.
MIN_ROWS_FOR_STATS = 1_000


class MacroSeriesInvalid(ValueError):
    """Chuỗi macro vi phạm bất biến dữ liệu ⇒ cấm ghi vào `macro_history`."""


def resolve_tickers(spec: str | None) -> dict[str, str]:
    """Chọn tập con ticker cần tải. `None`/rỗng = toàn bộ `MACRO_TICKERS`.

    [WHY] Tên biến không tồn tại phải FAIL kèm danh sách tên sai. Im lặng bỏ qua
    sẽ khiến người vận hành tin rằng "đã kéo xong XAGUSD" trong khi thực ra đã
    gõ nhầm `SILVER` và không có dòng nào được thêm.
    """
    if not spec or not spec.strip():
        return dict(TICKERS)
    wanted = [part.strip() for part in spec.split(",") if part.strip()]
    unknown = [name for name in wanted if name not in TICKERS]
    if unknown:
        raise MacroSeriesInvalid(f"Biến macro không tồn tại: {', '.join(unknown)}. Hợp lệ: {', '.join(sorted(TICKERS))}")
    return {name: TICKERS[name] for name in wanted}


def melt_close_frame(close: pd.DataFrame, inv_map: Mapping[str, str]) -> pd.DataFrame:
    """Chuyển frame Close (cột = ticker, index = ngày) → tidy (date, variable, value).

    [WHY] `dropna()` trước khi round là bắt buộc: ngày nghỉ lệch lịch giữa các
    thị trường tạo NaN ở một số cột. Giữ NaN rồi để nơi khác đổi thành 0 sẽ tạo
    "giá bạc = 0 USD/oz" — đúng loại bar giả đã gặp ở PAT 2026-06.
    """
    frame = close.rename(columns=dict(inv_map)).reset_index()
    date_col = frame.columns[0]
    frame = frame.rename(columns={date_col: "date"})
    tidy = frame.melt(id_vars=["date"], var_name="variable", value_name="value")
    tidy["date"] = pd.to_datetime(tidy["date"]).dt.strftime("%Y-%m-%d")
    tidy = tidy.dropna(subset=["value"])
    tidy["value"] = tidy["value"].astype(float).round(6)
    return tidy.sort_values(["variable", "date"], ignore_index=True)


def validate_series(
    df: pd.DataFrame,
    *,
    min_rows: int = 0,
    required: Iterable[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Kiểm chuỗi trước khi ghi; trả về thống kê đo được theo từng biến.

    [WHY] Trả về `{variable: {n, first, last, min, max}}` chứ không chỉ True/False:
    báo cáo phải nêu số đo thật để lần chạy sau so được trước/sau, và để người
    đọc kiểm lại được thay vì tin vào một chữ "OK".
    """
    if df is None or len(df) == 0:
        raise MacroSeriesInvalid("Không có dòng nào để ghi vào macro_history.")
    missing_cols = {"date", "variable", "value"} - set(df.columns)
    if missing_cols:
        raise MacroSeriesInvalid(f"Thiếu cột: {', '.join(sorted(missing_cols))}")

    nan_rows = int(df["value"].isna().sum())
    if nan_rows:
        raise MacroSeriesInvalid(f"Có {nan_rows} dòng value = NaN (phải bị loại trước khi ghi).")

    zero_rows = df.index[df["value"] == 0].tolist()
    if zero_rows:
        sample = ", ".join(f"{df.at[i, 'variable']}@{df.at[i, 'date']}" for i in zero_rows[:5])
        raise MacroSeriesInvalid(
            f"Có {len(zero_rows)} dòng value = 0 — sentinel 'không có dữ liệu', "
            f"không phải giá ({sample}). Không ghi để tránh Missing → Fake."
        )

    conflicts = df.groupby(["variable", "date"])["value"].nunique().loc[lambda s: s > 1]
    if len(conflicts):
        sample = ", ".join(f"{var}@{date}" for var, date in conflicts.index[:5])
        raise MacroSeriesInvalid(f"{len(conflicts)} cặp (variable, date) trùng nhưng giá trị khác nhau: {sample}")
    deduped = df.drop_duplicates(subset=["variable", "date"], keep="last")

    stats: dict[str, dict[str, Any]] = {}
    for variable, group in deduped.groupby("variable"):
        stats[str(variable)] = {
            "n": int(len(group)),
            "first": str(group["date"].min()),
            "last": str(group["date"].max()),
            "min": float(group["value"].min()),
            "max": float(group["value"].max()),
        }

    if required is not None:
        absent = [name for name in required if name not in stats]
        if absent:
            raise MacroSeriesInvalid(f"Không có dòng nào cho biến bắt buộc: {', '.join(absent)}")

    if min_rows:
        thin = {var: st["n"] for var, st in stats.items() if st["n"] < min_rows}
        if thin:
            detail = ", ".join(f"{var}={n}" for var, n in thin.items())
            raise MacroSeriesInvalid(
                f"Ngưỡng tối thiểu {min_rows} dòng/biến không đạt: {detail}. "
                "Mẫu cụt ⇒ không đủ cơ sở cho ADF/Hurst/OU, cấm ghi."
            )

    return stats


def check_overlap(
    new_pairs: Mapping[str, float],
    old_pairs: Mapping[str, float],
    *,
    tol_pct: float = DEFAULT_OVERLAP_TOL_PCT,
) -> dict[str, Any]:
    """So bản tải mới với dữ liệu đã lưu trên các ngày chồng lấn.

    [WHY] Không có vùng chồng lấn là bình thường khi backfill lùi về quá khứ —
    trả `n = 0` rõ ràng, KHÔNG raise và cũng không được báo "khớp hoàn hảo"
    (một chữ OK giả sẽ làm mất cảnh giác cho lần sau).
    """
    shared = sorted(set(new_pairs) & set(old_pairs))
    if not shared:
        return {"n": 0, "max_abs_pct": 0.0, "worst_date": None, "tol_pct": tol_pct}

    worst_date = None
    worst = 0.0
    for date in shared:
        old = float(old_pairs[date])
        if old == 0:
            # [WHY] Không chia cho 0: một giá trị cũ bằng 0 là dữ liệu hỏng sẵn,
            # coi như sai lệch vô hạn để buộc người vận hành xem lại.
            return {
                "n": len(shared),
                "max_abs_pct": float("inf"),
                "worst_date": date,
                "tol_pct": tol_pct,
            }
        pct = abs(float(new_pairs[date]) / old - 1.0) * 100.0
        if pct > worst:
            worst, worst_date = pct, date
    return {"n": len(shared), "max_abs_pct": worst, "worst_date": worst_date, "tol_pct": tol_pct}


QUARANTINE_TABLE = "macro_history_quarantine"


def quarantine_series(
    conn,
    variable: str,
    *,
    reason: str,
    stamp: str,
    table: str = "macro_history",
) -> dict[str, Any]:
    """Chuyển TOÀN BỘ dòng của `variable` ở `table` sang `<table>_quarantine` rồi xoá.

    [WHY] Ca đo thật 30/10/2026: `XAGUSD` trong `macro_history` KHÔNG tái lập được
    từ một lần tải duy nhất — 361 dòng nhưng chỉ 319 ngày phân biệt (42 dòng
    trùng), 2 bar rơi vào Thứ Bảy/Chủ Nhật (hợp đồng tương lai COMEX không có
    phiên cuối tuần), và từ 2026-07-23 lệch tới 5,31% so với chuỗi tải lại ⇒
    chuỗi là hỗn hợp nhiều quy ước (snapshot trong phiên / settle / nguồn khác).
    Không được "giữ phần tốt, thay phần xấu" bằng suy đoán: chọn ranh giới nào
    cũng là bịa. Cách ly TOÀN BỘ rồi nạp lại từ MỘT nguồn là hành vi fail-closed.

    [WHY] Sao chép theo `PRAGMA table_info` thay vì liệt kê cột cứng: `macro_history`
    (v1) và `macro_history_v2` khác schema, và một bản sao cứng sẽ âm thầm rơi
    cột khi schema đổi — mất đúng phần provenance cần cho truy nguyên.

    [INVARIANT] (1) Chép nguyên trạng + `reason` + `stamp`. (2) Chỉ xoá sau khi
    bảng cách ly đã ghi xong và số dòng khớp. (3) Không có dòng nào ⇒ raise
    (không báo "thành công" cho thao tác không làm gì).
    """
    if not reason or not reason.strip():
        raise MacroSeriesInvalid("Cách ly chuỗi phải kèm lý do (audit trail).")
    if "variable" not in _table_columns(conn, table):
        raise MacroSeriesInvalid(f"Bảng {table} không có cột `variable`.")

    cols = _table_columns(conn, table)
    rows = conn.execute(f"SELECT {', '.join(cols)} FROM {table} WHERE variable = ?", (variable,)).fetchall()
    if not rows:
        raise MacroSeriesInvalid(f"Không có dòng nào của {variable} trong {table} để cách ly.")

    q_table = f"{table}_quarantine"
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS {q_table} ("
        + ", ".join(f"{_quote_ident(c)}" for c in cols)
        + ", reason TEXT NOT NULL, quarantined_at TEXT NOT NULL)"
    )
    placeholders = ", ".join("?" for _ in cols)
    conn.executemany(
        f"INSERT INTO {q_table} ({', '.join(_quote_ident(c) for c in cols)}, reason, quarantined_at) "
        f"VALUES ({placeholders}, ?, ?)",
        [tuple(row) + (reason, stamp) for row in rows],
    )
    cur = conn.execute(f"DELETE FROM {table} WHERE variable = ?", (variable,))
    moved = int(cur.rowcount)
    if moved != len(rows):
        raise MacroSeriesInvalid(f"Xoá {moved}/{len(rows)} dòng trong {table} — không khớp, dừng để kiểm tra tay.")

    date_idx = cols.index("date") if "date" in cols else None
    dates = [row[date_idx] for row in rows] if date_idx is not None else []
    return {
        "table": table,
        "variable": variable,
        "moved": moved,
        "distinct_dates": len(set(dates)),
        "first": min(dates) if dates else None,
        "last": max(dates) if dates else None,
    }


def _table_columns(conn, table: str) -> list[str]:
    """Danh sách cột của `table` (rỗng nếu bảng chưa tồn tại)."""
    return [row[1] for row in conn.execute(f"PRAGMA table_info({_quote_ident(table)})")]


def table_exists(conn, table: str) -> bool:
    """True nếu `table` tồn tại (dùng để bỏ qua bảng optional như `macro_history_v2`)."""
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?", (table,)).fetchone()
    return row is not None


def _quote_ident(name: str) -> str:
    """Bọc identifier bằng nháy kép (chống tên bảng/cột lạ từ tham số CLI)."""
    return '"' + str(name).replace('"', '""') + '"'
