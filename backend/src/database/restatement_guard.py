"""restatement_guard.py — CHỐT #5 (Mục 27, NQ-17 §III): cấm ghi đè lịch sử thiếu nhãn `RESTATED`.

[WHY]
    Ngày 05/11/2026 lúc 17:31, `market_wide_breadth_daily` của các phiên ĐÃ CHỐT bị ghi lại
    âm thầm: 02/11 `−47,73 → −48,578` và 03/11 `+4,648 → +5,276` (total_active 839 → 834),
    không một dòng kiểm toán nào nêu ai đổi / căn cứ nào. Nghĩa là bất kỳ báo cáo nào đã công
    bố (kể cả báo cáo nghiệm thu EOD 03/11) đều có thể bị vô hiệu sau vài ngày mà không ai
    biết — mất hoàn toàn tính tái lập (reproducibility).

[ROOT CAUSE]
    `market_wide_breadth_daily._upsert_rows()` dùng `INSERT OR REPLACE` và ghi audit với
    `status = 'SUCCESS'` cho MỌI dòng, kể cả dòng ghi đè phiên cũ. Vì audit ghi "SUCCESS",
    không có cách nào phân biệt "tạo mới" với "viết lại số đã chốt". Bảng `regime_history`
    còn không có cột `created_at` nên không thể phát hiện ghi đè bằng dấu thời gian.

[INVARIANT]
    (a) Mọi lần ghi một phiên ĐÃ ĐÓNG (`date < current_processing_date`) vào
        `market_wide_breadth_daily` / `regime_history` BẮT BUỘC kèm một dòng
        `eod_write_audit_ledger` có `status = 'RESTATED'`, `caller_module`, `input_hash`,
        `reason` (nằm trong `parameters` + `notes`);
    (b) ghi đè + audit phải NẰM TRONG CÙNG TRANSACTION: audit không ghi được ⇒ rollback,
        dữ liệu cũ giữ nguyên (fail-closed, không có "ghi đè lậu");
    (c) phạm vi fail-closed khi QUÉT LẠI chỉ từ `RESTATEMENT_GUARD_EPOCH` trở đi — xem [WHY]
        của hằng số này; trước mốc đó chưa có cơ chế ghi vết nên không thể suy đoán.
"""

from __future__ import annotations

import logging
from datetime import datetime

from src.database.eod_write_audit import ensure_audit_ledger_schema, record_eod_write_audit

logger = logging.getLogger("PTCK_SYSTEM")

RESTATED_STATUS = "RESTATED"

# [WHY] Mốc hiệu lực của cổng kiểm tra NGƯỢC (quét lại). Đo trên `screener_cache.db`
# (06/11/2026): `market_wide_breadth_daily` có 1.707/1.708 dòng cùng `created_at =
# 2026-11-05T15:41:34` (một lần backfill TOÀN lịch sử), và `regime_history` KHÔNG có cột
# `created_at`. Nếu fail-closed trên toàn bộ lịch sử thì 1.706 dòng "vi phạm" vĩnh viễn ⇒
# cổng bị tắt trong tuần đầu. Trước mốc này ta cũng KHÔNG có bằng chứng để dựng lại lý do
# từng lần ghi đè — dựng audit giả sẽ vi phạm "missing > fake". — hạ mốc này sẽ tái diễn
# đúng lỗi "đỏ vĩnh viễn rồi bị xfail" mà NQ-17 §II.1 đã bác.
RESTATEMENT_GUARD_EPOCH = "2026-11-06"

# [WHY] Đúng hai bảng dẫn xuất mà NQ-17 §III chỉ định. KHÔNG mở rộng sang
# `paper_equity_curve`/`eod_run_ledger`: NQ-16 cho phép catch-up ghi bù các phiên đó, và
# quy ước `CATCHUP_FULL`/`CATCHUP_MTM_ONLY` đã là nhãn kiểm toán hợp lệ cho chúng.
RESTATEMENT_TARGETS: tuple[str, ...] = ("market_wide_breadth_daily", "regime_history")

# [WHY] Bảng KHÔNG có cột `created_at` ⇒ không thể phát hiện ghi đè bằng dấu thời gian.
# Khai báo tường minh để không ai tưởng "quét thấy 0 dòng = sạch" (AGENTS §4A: không biết ≠ 0).
TABLES_WITHOUT_PROVENANCE: tuple[str, ...] = ("regime_history",)


class UnauditedRestatementError(RuntimeError):
    """[WHY] Ghi đè một phiên đã chốt mà không có bản ghi `RESTATED` là lỗi TOÀN VẸN dữ
    liệu: số đã công bố thay đổi mà không có dấu vết. Không được nuốt (fail-closed).
    """


def is_backwrite(row_date: str, processing_date: str, target_table: str) -> bool:
    """True nếu `row_date` là phiên ĐÃ ĐÓNG so với `processing_date` của lần chạy hiện tại.

    [WHY] Tiêu chí là "phiên đã đóng", KHÔNG phải "khác created_at": catch-up hợp lệ ghi một
    phiên cũ lần ĐẦU cũng có `created_at > date` nhưng không phải restatement. Chỉ khi
    `date < processing_date` (phiên đã qua trong chính lần chạy này) mới cần nhãn.
    """
    if target_table not in RESTATEMENT_TARGETS:
        raise ValueError(
            f"{target_table!r} không nằm trong RESTATEMENT_TARGETS {RESTATEMENT_TARGETS} — "
            "nếu cần bảo vệ bảng mới, phải thêm tường minh kèm [WHY], không được lách."
        )
    return row_date < processing_date


def record_restatement(
    *,
    as_of_date: str,
    target_table: str,
    caller_module: str,
    reason: str,
    old_values=None,
    new_values=None,
    processing_date: str | None = None,
    output_score: float | None = None,
    conn=None,
    commit: bool = True,
) -> int:
    """Ghi một dòng `RESTATED` (append-only) mô tả việc ghi đè phiên đã chốt.

    [WHY] `reason` là trường BẮT BUỘC và không được rỗng: một dòng `RESTATED` mà không nêu
    lý do thì chỉ là "đã đổi" — không giúp được ai 3 tháng sau truy vì sao số 02/11 khác báo
    cáo cũ. `caller_module` + `input_hash` (hash của old/new) cho phép đối chiếu nội dung.
    """
    if target_table not in RESTATEMENT_TARGETS:
        raise ValueError(f"{target_table!r} không nằm trong RESTATEMENT_TARGETS")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("record_restatement cần `reason` không rỗng (NQ-17 §III)")
    if processing_date is not None and as_of_date > processing_date:
        raise ValueError(
            f"as_of_date {as_of_date} > processing_date {processing_date}: không thể 'restate' một phiên chưa tới."
        )

    parameters = {
        "reason": reason.strip(),
        "old": old_values,
        "new": new_values,
        "processing_date": processing_date,
        "recorded_at": datetime.now().isoformat(),
    }
    if conn is not None:
        ensure_audit_ledger_schema(conn=conn, commit=False)
    return record_eod_write_audit(
        as_of_date=as_of_date,
        caller_module=caller_module,
        target_table=target_table,
        parameters=parameters,
        input_data={"old": old_values, "new": new_values},
        output_score=output_score,
        status=RESTATED_STATUS,
        notes=f"RESTATED: {reason.strip()}",
        conn=conn,
        commit=commit,
    )


def _audited_pairs(conn) -> set[tuple[str, str]]:
    try:
        rows = conn.execute(
            "SELECT as_of_date, target_table FROM eod_write_audit_ledger WHERE status = ?",
            (RESTATED_STATUS,),
        )
    except Exception:  # noqa: BLE001 - bảng chưa tồn tại (DB mới) ⇒ chưa có audit nào
        return set()
    return {(str(r[0]), str(r[1])) for r in rows}


def has_restatement_audit(conn, as_of_date: str, target_table: str) -> bool:
    """True nếu `(as_of_date, target_table)` đã có bản ghi `RESTATED`."""
    return (as_of_date, target_table) in _audited_pairs(conn)


def assert_restatement_audited(conn, as_of_date: str, target_table: str) -> None:
    """FAIL-CLOSED: không có bản ghi `RESTATED` ⇒ ném `UnauditedRestatementError`."""
    if not has_restatement_audit(conn, as_of_date, target_table):
        raise UnauditedRestatementError(
            f"GHI ĐÈ KHÔNG KIỂM TOÁN: {target_table} phiên {as_of_date} đã bị ghi lại nhưng "
            f"`eod_write_audit_ledger` không có dòng status='RESTATED'. "
            f"Gọi `record_restatement(...)` với caller_module + input_hash + reason."
        )


def find_unaudited_restatements(conn, since: str = RESTATEMENT_GUARD_EPOCH) -> dict:
    """Quét NGƯỢC các dòng bị ghi sau ngày phiên mà thiếu bản ghi `RESTATED`.

    [WHY] Đây là lưới THỨ HAI, không phải cổng chính: cổng chính nằm ở chỗ GHI
    (`market_wide_breadth._upsert_rows` → `record_restatement` trong cùng transaction), vì
    chỉ ở đó mới phân biệt được "tạo mới" với "ghi đè". Quét ngược bằng `created_at` không
    phân biệt được (catch-up ghi một phiên cũ lần đầu cũng có `created_at > date`), nên kết
    quả phải đọc là "CẦN ĐỐI CHIẾU", không phải "đã có vi phạm".

    [INVARIANT] `undetectable` liệt kê bảng không có cột thời gian — trả về danh sách đó
    thay vì im lặng coi như sạch (AGENTS §4A).
    """
    audited = _audited_pairs(conn)
    candidates: list[dict] = []
    for d, created in conn.execute(
        "SELECT date, created_at FROM market_wide_breadth_daily WHERE date >= ? ORDER BY date",
        (since,),
    ):
        created_day = str(created)[:10] if created else ""
        if created_day and created_day > str(d):
            if (str(d), "market_wide_breadth_daily") not in audited:
                candidates.append(
                    {
                        "date": str(d),
                        "target_table": "market_wide_breadth_daily",
                        "created_at": created,
                    }
                )
    return {
        "since": since,
        "checked_tables": ["market_wide_breadth_daily"],
        "undetectable": list(TABLES_WITHOUT_PROVENANCE),
        "candidates": candidates,
        "has_candidates": bool(candidates),
    }
