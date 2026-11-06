"""nonsession_quarantine.py — cách ly DÒNG TRÊN NGÀY KHÔNG GIAO DỊCH (Mục 28 #3, NQ-18).

[WHY]
    Chốt #3 chiều ngược (`20295b5`) phát hiện trên CSDL sống đúng 3 dòng nằm trên ngày
    Quốc khánh `2026-09-01` — một ngày sàn đóng cửa (`NON_TRADING_SESSIONS`, đồng thời là
    ngày lễ trong SSOT). Nội dung các dòng đó tự tố cáo là dữ liệu bịa:
      • `paper_equity_curve`: `total_equity = 1.008.488.848,3466899` — GIỐNG HỆT dòng
        `2026-08-28` đến từng chữ số ⇒ chép lại trạng thái phiên trước, giá không cập nhật;
      • `market_wide_breadth_daily`: `advances=1, declines=0, unchanged=938` trên 939 mã ⇒
        tính từ panel đứng yên trong ngày sàn đóng;
      • `regime_history`: `breadth_pct=0,0` dẫn xuất từ chính breadth hỏng đó.
    Đây là vi phạm trực tiếp AGENTS §1 (Missing > Fake): hệ thống đã BỊA một phiên thay vì
    chịu thiếu. Hệ quả kế toán: `paper_equity_curve` có một điểm vốn không ứng với phiên nào
    ⇒ mọi CAGR/drawdown/max-DD tính qua nó lệch một phiên.

[ROOT CAUSE]
    Không có cổng nào kiểm tra `present ⊆ expected` (dòng thừa) cho tới `20295b5`, và cũng
    chưa có đường thanh lý CÓ KIỂM TOÁN. Nay cổng đã bật ⇒ phải có công cụ trả nợ, nếu không
    cổng sẽ đỏ vĩnh viễn và bị tắt (đúng bài học 31/08/2026).

[INVARIANT]
    (a) TUYỆT ĐỐI không xoá dữ liệu thị trường thật: một ngày không phải phiên mà
        `daily_ohlcv` có >= `MIN_VALID_BARS_PER_SESSION` bar hợp lệ ⇒ **LỊCH SAI**, không
        phải dòng ma ⇒ ném `CalendarDataConflictError`, KHÔNG xoá gì. Đây chính là ca
        `2024-02-15/16` (1.092 và 1.139 bar thật) — nếu công cụ này tồn tại sớm hơn và
        "dọn" theo lịch cũ thì đã xoá mất 2 phiên thật.
    (b) Xoá và ghi kiểm toán nằm trong CÙNG một transaction; audit lỗi ⇒ rollback ⇒ dòng
        vẫn còn (không có "xoá lén").
    (c) Sau khi xoá phải TỰ KIỂM LẠI ngay trong cùng transaction: nếu còn dòng trên ngày
        không giao dịch trong phạm vi ⇒ rollback. "Đã xoá" không được tin bằng lời.
    (d) Xoá theo `rowid` + điều kiện ngày (không xoá theo khoảng ngày) ⇒ không thể lan sang
        phiên bên cạnh nếu dữ liệu đổi giữa lúc quét và lúc xoá.
    (e) Mỗi bảng/ngày có đúng MỘT dòng `RESTATED` trong `eod_write_audit_ledger`, chứa
        nguyên bản payload cũ + `input_hash` ⇒ 3 tháng sau vẫn dựng lại được đã xoá cái gì.
"""

from __future__ import annotations

import logging
from datetime import datetime

from src.database.eod_write_audit import ensure_audit_ledger_schema, record_eod_write_audit
from src.database.restatement_guard import RESTATED_STATUS
from src.database.session_continuity import (
    CONTRACT_EPOCH,
    MIN_VALID_BARS_PER_SESSION,
    is_session_day,
)

logger = logging.getLogger("PTCK_SYSTEM")

# [WHY] Bảng -> cột ngày. Khai báo TƯỜNG MINH thay vì dò `PRAGMA`: nếu một bảng đổi tên cột
# ngày, ta phải biết ngay (import/scan lỗi), không được im lặng bỏ qua bảng đó — "không quét
# được" mà báo "sạch" là đúng kiểu fail-open nguy hiểm nhất.
NONSESSION_TABLES: dict[str, str] = {
    "daily_ohlcv": "date",
    "market_wide_breadth_daily": "date",
    "regime_history": "date",
    "paper_equity_curve": "date",
    "eod_run_ledger": "as_of_date",
}

# [WHY] Trần số dòng payload ghi vào audit cho MỘT (bảng, ngày). Ca thật chỉ có 1 dòng/bảng.
# Vượt trần thì vẫn ghi `rowid` + hash của TOÀN BỘ payload, để không ai phải đoán.
MAX_PAYLOAD_ROWS = 20


class NonsessionQuarantineError(RuntimeError):
    """[WHY] Lỗi cấu trúc khi quét/cách ly (thiếu bảng, thiếu cột, xoá không ăn)."""


class CalendarDataConflictError(RuntimeError):
    """[WHY] Ngày không phải phiên nhưng có DỮ LIỆU THỊ TRƯỜNG THẬT ⇒ lịch sai, cấm xoá.

    Ca `2024-02-15` (1.092 bar) là bằng chứng: xoá theo lịch cũ sẽ phá huỷ 2 phiên thật.
    """


def _table_columns(conn, table: str) -> list[str]:
    rows = list(conn.execute(f"PRAGMA table_info({table})"))
    if not any(str(r[1]) == NONSESSION_TABLES[table] for r in rows):
        raise NonsessionQuarantineError(
            f"bảng {table} không có cột ngày {NONSESSION_TABLES[table]!r} — cấu trúc đã đổi, "
            "dừng lại thay vì bỏ qua bảng (fail-closed)."
        )
    return [str(r[1]) for r in rows]


def _valid_bars(conn, date_iso: str) -> int:
    """Số bar hợp lệ của `daily_ohlcv` trong một ngày (cùng định nghĩa với Chốt #2/#3)."""
    row = conn.execute(
        "SELECT COUNT(*) FROM daily_ohlcv WHERE date=? AND COALESCE(is_stale, 0) = 0",
        (date_iso,),
    ).fetchone()
    return int(row[0])


def scan_nonsession_rows(
    conn,
    *,
    since: str = CONTRACT_EPOCH,
    include_history: bool = False,
) -> dict:
    """Liệt kê mọi dòng nằm trên NGÀY KHÔNG GIAO DỊCH (chỉ đọc, không sửa gì).

    [WHY] Tách "quét" khỏi "xoá" để `--dry-run` và báo cáo nghiệm thu đọc CÙNG một nguồn số
    với lệnh `--apply`; nếu hai đường tự đếm riêng thì sẽ có ngày lệch nhau.

    [WHY] Mặc định chỉ quét từ `CONTRACT_EPOCH`: trước mốc đó dữ liệu là nợ kế thừa và không
    có kiểm toán, xoá hàng loạt lịch sử cũ là việc phải quyết riêng (`include_history=True`
    mới chạm tới) — nhưng khi quét thì phải THẤY, không được im lặng bỏ qua.
    """
    tables = tuple(NONSESSION_TABLES)
    rows: list[dict] = []
    conflicts: list[dict] = []

    for table in tables:
        column = NONSESSION_TABLES[table]
        columns = _table_columns(conn, table)
        sql = f"SELECT rowid, * FROM {table}"
        if not include_history:
            sql += f" WHERE {column} >= ?"
            cursor = conn.execute(sql, (since,))
        else:
            cursor = conn.execute(sql)
        for record in cursor:
            rowid = record[0]
            payload = {c: v for c, v in zip(columns, record[1:])}
            date_iso = str(payload[column])
            if is_session_day(date_iso):
                continue
            entry = {
                "table": table,
                "date": date_iso,
                "rowid": int(rowid),
                "payload": payload,
                "valid_bars": _valid_bars(conn, date_iso),
            }
            rows.append(entry)
            if table == "daily_ohlcv" and entry["valid_bars"] >= MIN_VALID_BARS_PER_SESSION:
                conflicts.append(
                    {
                        "table": table,
                        "date": date_iso,
                        "valid_bars": entry["valid_bars"],
                    }
                )

    rows.sort(key=lambda r: (r["table"], r["date"], r["rowid"]))
    return {
        "since": since,
        "include_history": bool(include_history),
        "tables_scanned": list(tables),
        "count": len(rows),
        "rows": rows,
        "conflicts": conflicts,
        "clean": not rows and not conflicts,
    }


def _audit_old_payload(rows: list[dict]) -> dict:
    """Gói payload cũ để ghi vào audit — đủ để dựng lại, không phơi cả bảng."""
    payloads = [r["payload"] for r in rows]
    return {
        "row_count": len(payloads),
        "rowids": [r["rowid"] for r in rows],
        "payload": payloads[:MAX_PAYLOAD_ROWS],
        "payload_truncated": len(payloads) > MAX_PAYLOAD_ROWS,
    }


def quarantine_nonsession_rows(
    conn,
    report: dict,
    *,
    caller_module: str,
    reason: str,
    commit: bool = True,
) -> dict:
    """Xoá các dòng trong `report`, mỗi (bảng, ngày) kèm MỘT dòng audit `RESTATED`.

    [WHY] `commit=False` cho phép caller gộp vào transaction lớn hơn; mặc định tự chốt. Dù
    chốt hay không, audit và DELETE luôn nằm cùng transaction ⇒ không tồn tại trạng thái
    "đã xoá nhưng không có vết".
    """
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("quarantine cần `reason` không rỗng — xoá dữ liệu không lý do là xoá lén")
    if not isinstance(caller_module, str) or not caller_module.strip():
        raise ValueError("quarantine cần `caller_module` — 3 tháng sau phải biết ai xoá")

    if report.get("conflicts"):
        detail = ", ".join(f"{c['table']}@{c['date']}={c['valid_bars']} bar" for c in report["conflicts"])
        raise CalendarDataConflictError(
            "CẤM XOÁ: ngày không phải phiên nhưng có DỮ LIỆU THỊ TRƯỜNG THẬT "
            f"({detail}). Đây là LỊCH SAI, không phải dòng ma — sửa lịch (SSOT) rồi chạy lại. "
            "Xoá theo lịch sai sẽ phá huỷ phiên thật (bài học 2024-02-15/16)."
        )

    grouped: dict[tuple[str, str], list[dict]] = {}
    for row in report.get("rows", []):
        grouped.setdefault((row["table"], row["date"]), []).append(row)

    deleted = 0
    audit_ids: list[int] = []
    if grouped:
        ensure_audit_ledger_schema(conn=conn, commit=False)
        # [WHY] Transaction TƯỜNG MINH (không `with conn:`) vì `commit=False` nghĩa là caller
        # giữ quyền chốt: `with conn:` sẽ tự commit khi thoát, phá đúng hợp đồng đó.
        try:
            for (table, date_iso), rows in sorted(grouped.items()):
                column = NONSESSION_TABLES[table]
                audit_id = record_eod_write_audit(
                    as_of_date=date_iso,
                    caller_module=caller_module.strip(),
                    target_table=table,
                    parameters={
                        "reason": reason.strip(),
                        "action": "DELETE_NON_SESSION_ROW",
                        "table": table,
                        "date": date_iso,
                        "old": _audit_old_payload(rows),
                        "recorded_at": datetime.now().isoformat(),
                    },
                    input_data=_audit_old_payload(rows),
                    status=RESTATED_STATUS,
                    notes=(f"RESTATED: cách ly {len(rows)} dòng trên NGÀY KHÔNG GIAO DỊCH {date_iso} — {reason.strip()}"),
                    conn=conn,
                    commit=False,
                )
                audit_ids.append(audit_id)
                for row in rows:
                    cur = conn.execute(
                        f"DELETE FROM {table} WHERE rowid=? AND {column}=?",
                        (row["rowid"], date_iso),
                    )
                    deleted += int(cur.rowcount or 0)

            # [INVARIANT] (c) tự kiểm trong cùng transaction; không tin lời của chính mình.
            verify = scan_nonsession_rows(
                conn,
                since=report.get("since", CONTRACT_EPOCH),
                include_history=bool(report.get("include_history")),
            )
            if verify["rows"] or verify["conflicts"]:
                raise NonsessionQuarantineError(
                    "sau khi xoá vẫn còn dòng trên ngày không giao dịch: "
                    f"{[(r['table'], r['date']) for r in verify['rows']]} — ROLLBACK."
                )
        except Exception:
            conn.rollback()
            raise
        if commit:
            conn.commit()

    return {
        "deleted": deleted,
        "audit_ids": audit_ids,
        "groups": [{"table": t, "date": d, "rows": len(r)} for (t, d), r in sorted(grouped.items())],
    }
