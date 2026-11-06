# -*- coding: utf-8 -*-
"""CHỐT #5 — Mục 27 (NQ-17 §III): cấm ghi đè phiên đã chốt mà thiếu nhãn `RESTATED`.

[WHY]
    05/11/2026 17:31, `market_wide_breadth_daily` của hai phiên ĐÃ CHỐT bị ghi lại âm thầm:
    02/11 `−47,73 → −48,578`, 03/11 `+4,648 → +5,276` (total_active 839 → 834). Không một
    dòng kiểm toán nào nêu ai đổi / vì sao. Hệ quả: mọi báo cáo đã công bố (kể cả nghiệm thu
    EOD 03/11) có thể bị vô hiệu sau vài ngày mà không ai biết.

[ROOT CAUSE]
    `_upsert_rows()` dùng `INSERT OR REPLACE` và ghi audit `status='SUCCESS'` cho MỌI dòng,
    kể cả dòng ghi đè phiên cũ ⇒ không có cách nào phân biệt "tạo mới" với "viết lại số đã
    chốt". `regime_history` còn không có cột `created_at` nên không thể phát hiện bằng thời
    gian — buộc phải chốt tại THỜI ĐIỂM GHI.

[INVARIANT]
    (a) ghi đè phiên đã đóng (`date < processing_date`) ⇒ BẮT BUỘC có dòng `RESTATED` với
        `caller_module` + `input_hash` + `reason`;
    (b) ghi đè + audit CÙNG transaction: audit lỗi ⇒ rollback, dữ liệu cũ giữ nguyên;
    (c) tạo mới phiên cũ / refresh phiên hiện tại ⇒ KHÔNG phải restatement (chống vá quá tay);
    (d) quét ngược chỉ từ `RESTATEMENT_GUARD_EPOCH`, và phải khai báo bảng không có provenance.
"""

import sqlite3
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = None
for _par in [Path(__file__).resolve().parent.parent.parent] + list(
    Path(__file__).resolve().parent.parent.parent.parents
):
    if (_par / "AGENTS.md").exists() and (_par / "backend").is_dir():
        PROJECT_ROOT = _par
        break
BACKEND_DIR = PROJECT_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

LIVE_DB = BACKEND_DIR / "data" / "screener_cache.db"


def _row(date: str, advances: int = 300, declines: int = 200, degraded: int = 0) -> dict:
    unchanged = 100
    total = advances + declines + unchanged
    return {
        "date": date,
        "label": "ALL_EXCHANGES_UNWEIGHTED",
        "total_active": total,
        "advances": advances,
        "declines": declines,
        "unchanged": unchanged,
        "ceiling": 10,
        "floor": 5,
        "breadth_pct": round((advances - declines) / total * 100, 3),
        "ad_ratio": round(advances / declines, 3),
        "degraded": degraded,
    }


def _temp_db(tmp_path: Path, monkeypatch) -> Path:
    import src.analysis.market_wide_breadth as mwb

    path = tmp_path / "breadth.db"
    monkeypatch.setattr(mwb, "DB", path, raising=True)
    return path


def _audit_rows(path: Path) -> list[tuple]:
    con = sqlite3.connect(str(path))
    try:
        return list(
            con.execute(
                "SELECT as_of_date, status, caller_module, input_hash, parameters, notes "
                "FROM eod_write_audit_ledger WHERE status='RESTATED' ORDER BY as_of_date"
            )
        )
    finally:
        con.close()


# ===================================================================
# 1. Ghi đè phiên đã chốt ⇒ PHẢI có RESTATED
# ===================================================================
def test_overwrite_of_closed_session_records_restated_audit(tmp_path, monkeypatch):
    """[WHY] Tái hiện đúng 05/11 17:31: phiên 02/11 đã chốt, lần ghi sau (processing_date
    mới hơn) đổi advances/declines ⇒ phải sinh dòng RESTATED nêu old→new + caller + hash.
    """
    import src.analysis.market_wide_breadth as mwb

    path = _temp_db(tmp_path, monkeypatch)
    mwb._upsert_rows([_row("2026-11-02"), _row("2026-11-03")])
    assert _audit_rows(path) == []

    mwb._upsert_rows(
        [_row("2026-11-02", advances=133, declines=543), _row("2026-11-04")],
        caller_module="analysis.market_wide_breadth.catchup",
        reason="catch-up 04/11 rồi tính lại breadth",
    )

    rows = _audit_rows(path)
    assert len(rows) == 1, rows
    as_of, status, caller, input_hash, params, notes = rows[0]
    assert as_of == "2026-11-02"
    assert caller == "analysis.market_wide_breadth.catchup"
    assert input_hash and len(input_hash) == 64
    assert "catch-up 04/11" in notes
    assert '"old"' in params and '"new"' in params


def test_new_past_session_is_not_a_restatement(tmp_path, monkeypatch):
    """[WHY] Chống vá quá tay: catch-up ghi một phiên CŨ lần ĐẦU (04/11 sau khi cron miss)
    không phải ghi đè — nếu bắt nó mang nhãn RESTATED thì nhãn mất ý nghĩa và mọi catch-up
    sẽ bị đọc nhầm là "sửa số đã công bố".
    """
    import src.analysis.market_wide_breadth as mwb

    path = _temp_db(tmp_path, monkeypatch)
    mwb._upsert_rows([_row("2026-11-03")])
    mwb._upsert_rows([_row("2026-11-04"), _row("2026-11-05")])
    assert _audit_rows(path) == []


def test_refreshing_current_session_is_not_a_restatement(tmp_path, monkeypatch):
    """[WHY] Chống vá quá tay: `sync_missing` cố ý ghi lại phiên HIỆN TẠI khi panel trong
    ngày đổi (sửa lỗi ingest một phần). `date == processing_date` ⇒ phiên chưa đóng.
    """
    import src.analysis.market_wide_breadth as mwb

    path = _temp_db(tmp_path, monkeypatch)
    mwb._upsert_rows([_row("2026-11-05", advances=300)])
    mwb._upsert_rows([_row("2026-11-05", advances=355, declines=335)])
    assert _audit_rows(path) == []


# ===================================================================
# 2. Fail-closed: audit không ghi được ⇒ KHÔNG được ghi đè
# ===================================================================
def test_audit_failure_rolls_back_the_overwrite(tmp_path, monkeypatch):
    """[WHY] Đây là bản chất của NQ-17 §III: "ghi đè lậu" phải BẤT KHẢ THI, không chỉ bị
    phát hiện sau. Nếu audit lỗi mà dữ liệu vẫn bị ghi đè, hệ thống lại về đúng lỗ hổng cũ.
    """
    import src.analysis.market_wide_breadth as mwb
    import src.database.restatement_guard as guard

    path = _temp_db(tmp_path, monkeypatch)
    mwb._upsert_rows([_row("2026-11-02", advances=300), _row("2026-11-03")])

    def _boom(**kwargs):
        raise sqlite3.OperationalError("audit ledger bị khóa")

    monkeypatch.setattr(guard, "record_restatement", _boom, raising=True)
    with pytest.raises(sqlite3.OperationalError):
        mwb._upsert_rows(
            [_row("2026-11-02", advances=133, declines=543), _row("2026-11-04")],
        )

    con = sqlite3.connect(str(path))
    try:
        advances = con.execute(
            "SELECT advances FROM market_wide_breadth_daily WHERE date='2026-11-02'"
        ).fetchone()[0]
    finally:
        con.close()
    assert advances == 300, "ghi đè phải bị ROLLBACK khi audit không ghi được"


# ===================================================================
# 3. Hàm thuần + phạm vi
# ===================================================================
def test_is_backwrite_rules():
    """[WHY] `is_backwrite` là định nghĩa DUY NHẤT của "phiên đã đóng" — sai một ngày ở đây
    là hoặc bỏ sót restatement, hoặc gắn nhãn RESTATED cho mọi lần ghi bình thường.
    """
    from src.database.restatement_guard import is_backwrite

    assert is_backwrite("2026-11-04", "2026-11-05", "market_wide_breadth_daily") is True
    assert is_backwrite("2026-11-05", "2026-11-05", "market_wide_breadth_daily") is False
    assert is_backwrite("2026-11-06", "2026-11-05", "market_wide_breadth_daily") is False
    with pytest.raises(ValueError):
        is_backwrite("2026-11-04", "2026-11-05", "some_other_table")


def test_record_restatement_rejects_empty_reason():
    """[WHY] Một dòng RESTATED không có lý do chỉ nói "đã đổi", không giúp ai 3 tháng sau
    truy vì sao số 02/11 khác báo cáo cũ — đúng thứ NQ-17 §III cấm.
    """
    from src.database.restatement_guard import record_restatement

    with pytest.raises(ValueError):
        record_restatement(
            as_of_date="2026-11-02",
            target_table="market_wide_breadth_daily",
            caller_module="x",
            reason="   ",
        )


def test_record_restatement_rejects_unlisted_table_and_future_date():
    """[WHY] Không cho "lách" sang bảng khác hay restate phiên chưa tới — cả hai đều là cách
    vô hiệu hoá cổng mà vẫn trông như đang tuân thủ.
    """
    from src.database.restatement_guard import record_restatement

    with pytest.raises(ValueError):
        record_restatement(
            as_of_date="2026-11-02",
            target_table="paper_equity_curve",
            caller_module="x",
            reason="lách",
        )
    with pytest.raises(ValueError):
        record_restatement(
            as_of_date="2026-11-09",
            target_table="market_wide_breadth_daily",
            caller_module="x",
            reason="tương lai",
            processing_date="2026-11-05",
        )


# ===================================================================
# 4. CSDL THẬT
# ===================================================================
def test_live_has_no_unaudited_restatement_since_guard_epoch():
    """[WHY] Sentinel thường trực từ mốc hiệu lực: từ 06/11/2026, không phiên nào được ghi
    lại mà thiếu nhãn RESTATED.
    """
    from src.database.restatement_guard import find_unaudited_restatements

    con = sqlite3.connect(f"file:{LIVE_DB.as_posix()}?mode=ro", uri=True)
    try:
        report = find_unaudited_restatements(con)
    finally:
        con.close()
    assert report["candidates"] == [], report
    assert report["undetectable"] == ["regime_history"], (
        "regime_history không có created_at — phải khai báo là KHÔNG phát hiện được, "
        "không được im lặng coi là sạch (AGENTS §4A)"
    )


def test_pre_epoch_backfill_rewrites_are_not_flagged():
    """[WHY] Đo 06/11/2026: 1.707/1.708 dòng breadth cùng `created_at = 2026-11-05T15:41:34`
    (một lần backfill toàn lịch sử). Nếu cổng quét cả quá khứ, nó "phát hiện" 1.706 vi phạm
    vĩnh viễn và sẽ bị tắt trong tuần đầu — đúng cái bẫy NQ-17 §II.1 đã bác.
    """
    from src.database.restatement_guard import RESTATEMENT_GUARD_EPOCH, find_unaudited_restatements

    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE market_wide_breadth_daily (date TEXT, created_at TEXT)")
    con.executemany(
        "INSERT INTO market_wide_breadth_daily VALUES (?,?)",
        [("2026-11-02", "2026-11-05T15:41:34"), ("2026-11-03", "2026-11-05T15:41:34")],
    )
    con.execute(
        "CREATE TABLE eod_write_audit_ledger (as_of_date TEXT, target_table TEXT, status TEXT)"
    )
    try:
        report = find_unaudited_restatements(con)
    finally:
        con.close()
    assert report["candidates"] == []
    assert report["since"] == RESTATEMENT_GUARD_EPOCH == "2026-11-06"
