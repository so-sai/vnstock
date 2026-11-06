# -*- coding: utf-8 -*-
"""Chiều NGƯỢC của Chốt #3: dòng tồn tại trên NGÀY KHÔNG GIAO DỊCH (NQ-18 §II.2).

[WHY]
    `assert_session_set_alignment` (Chốt #3, `6e013d8`) chỉ kiểm tra `expected ⊄ present`
    — tức "phiên cần có mà thiếu". Nó KHÔNG kiểm tra `present ⊄ expected` — tức "có dòng
    trên ngày sàn đóng cửa". Đo trên `screener_cache.db` (06/11/2026) đúng lỗ hổng này đang
    sống thật: ngày `2026-09-01` (Quốc khánh, `NON_TRADING_SESSIONS`) có dòng ở
    `market_wide_breadth_daily`, `regime_history` và `paper_equity_curve`, mà cả ba cổng
    hiện có đều báo XANH.

[ROOT CAUSE]
    Bất biến được phát biểu một chiều ("đủ phiên") thay vì hai chiều ("khớp đúng tập phiên").
    Hệ quả đo được: mọi consumer đọc "phiên liền trước" của một ngày quanh 01/09/2026 sẽ coi
    01/09 là một phiên có dữ liệu; và `paper_equity_curve` có một điểm vốn (equity) không
    ứng với phiên nào — làm mọi CAGR/drawdown tính qua nó sai một phiên.

[INVARIANT]
    (a) trong phạm vi hợp đồng, mọi ngày CÓ DÒNG phải là phiên giao dịch;
    (b) phạm vi phải THẬT: dòng ngoài phạm vi là nợ kế thừa, không fail-closed — nhưng khi
        hỏi toàn bộ từ epoch thì phải thấy (chống "bịt mắt");
    (c) thông điệp lỗi phải NÊU TÊN ngày + bảng, nếu không thì lúc 15:45 không ai biết
        phải xoá dòng nào.
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

# [WHY] GHIM số đo 06/11/2026: đúng 3 bảng có dòng trên ngày không giao dịch `2026-09-01`.
# Đây KHÔNG phải miễn trừ cho tương lai — nếu một dòng thừa MỚI xuất hiện (bất kỳ ngày nào,
# bất kỳ bảng nào trong phạm vi), dict này lệch và test đỏ ngay.
KNOWN_LIVE_ORPHANS: dict[str, list[str]] = {
    "daily_ohlcv": [],
    "market_wide_breadth_daily": ["2026-09-01"],
    "regime_history": ["2026-09-01"],
    "paper_equity_curve": ["2026-09-01"],
    "eod_run_ledger": [],
}


def _sessions_ending(as_of: str, n: int) -> list[str]:
    from src.database.session_continuity import session_window_end

    return session_window_end(as_of, n)


def _db(sessions: list[str], extra: dict[str, list[str]] | None = None) -> sqlite3.Connection:
    """Dựng đủ 5 bảng trên đúng `sessions`; `extra` chèn thêm dòng ở ngày bất kỳ."""
    extra = extra or {}
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE daily_ohlcv (symbol TEXT, date TEXT, is_stale INTEGER)")
    con.execute("CREATE TABLE market_wide_breadth_daily (date TEXT)")
    con.execute("CREATE TABLE regime_history (date TEXT)")
    con.execute("CREATE TABLE paper_equity_curve (date TEXT)")
    con.execute("CREATE TABLE eod_run_ledger (as_of_date TEXT)")

    bars, breadth, regime, equity, ledger = [], [], [], [], []
    for d in sessions:
        bars += [(f"S{i}", d, 0) for i in range(120)]
        breadth.append((d,))
        regime.append((d,))
        equity.append((d,))
        ledger.append((d,))
    for d in extra.get("daily_ohlcv", []):
        bars += [(f"X{i}", d, 0) for i in range(120)]
    for d in extra.get("market_wide_breadth_daily", []):
        breadth.append((d,))
    for d in extra.get("regime_history", []):
        regime.append((d,))
    for d in extra.get("paper_equity_curve", []):
        equity.append((d,))
    for d in extra.get("eod_run_ledger", []):
        ledger.append((d,))

    con.executemany("INSERT INTO daily_ohlcv VALUES (?,?,?)", bars)
    con.executemany("INSERT INTO market_wide_breadth_daily VALUES (?)", breadth)
    con.executemany("INSERT INTO regime_history VALUES (?)", regime)
    con.executemany("INSERT INTO paper_equity_curve VALUES (?)", equity)
    con.executemany("INSERT INTO eod_run_ledger VALUES (?)", ledger)
    con.commit()
    return con


def test_orphan_row_on_holiday_is_reported_and_fails_closed():
    """[WHY] Tái hiện đúng ca 2026-09-01: dòng breadth trên ngày Quốc khánh. Nếu cổng không
    thấy, một điểm dữ liệu "ma" tồn tại vĩnh viễn và mọi phép "phiên liền trước" quanh đó sai.
    """
    from src.database.session_continuity import SessionSetMismatchError, assert_session_set_alignment, collect_session_alignment

    s = _sessions_ending("2026-11-05", 60)
    con = _db(s, {"market_wide_breadth_daily": ["2026-09-01"]})
    try:
        report = collect_session_alignment(con, "2026-11-05")
        assert report["market_spurious"]["market_wide_breadth_daily"] == ["2026-09-01"], report
        assert report["ok"] is False
        with pytest.raises(SessionSetMismatchError) as exc:
            assert_session_set_alignment(con, "2026-11-05")
        msg = str(exc.value)
        assert "2026-09-01" in msg
        assert "market_wide_breadth_daily" in msg
    finally:
        con.close()


def test_spurious_only_on_table_that_has_the_row():
    """[WHY] Chống báo động lan: chỉ bảng THẬT SỰ có dòng mới bị nêu. Nếu cổng gán dòng thừa
    cho cả 5 bảng thì người trực sẽ đi kiểm 5 bảng và mất tin vào cổng.
    """
    from src.database.session_continuity import collect_session_alignment

    s = _sessions_ending("2026-11-05", 60)
    con = _db(s, {"paper_equity_curve": ["2026-09-01"]})
    try:
        # [WHY] `accounting_window=None` + epoch = đầu cửa sổ: 01/09/2026 nằm NGOÀI 20 phiên
        # kế toán gần nhất, nên phải hỏi toàn bộ từ epoch mới thấy — đúng ngữ nghĩa phạm vi.
        report = collect_session_alignment(
            con, "2026-11-05", accounting_window=None, contract_epoch=s[0]
        )
    finally:
        con.close()
    assert report["accounting_spurious"]["paper_equity_curve"] == ["2026-09-01"]
    assert report["accounting_spurious"]["eod_run_ledger"] == []
    assert report["market_spurious"] == {
        "daily_ohlcv": [],
        "market_wide_breadth_daily": [],
        "regime_history": [],
    }


def test_clean_db_has_no_spurious():
    """[WHY] Chống vá quá tay: dữ liệu sạch thì cả hai chiều đều rỗng và `ok` phải True —
    cổng đỏ vô cớ sẽ bị tắt trong tuần đầu (bài học 31/08/2026).
    """
    from src.database.session_continuity import assert_session_set_alignment

    s = _sessions_ending("2026-11-05", 60)
    con = _db(s)
    try:
        report = assert_session_set_alignment(con, "2026-11-05")
    finally:
        con.close()
    assert report["ok"] is True
    assert not any(report["market_spurious"].values())
    assert not any(report["accounting_spurious"].values())


def test_spurious_outside_scope_is_not_flagged_but_shows_in_full_epoch_scan():
    """[WHY] Phạm vi phải THẬT hai chiều: dòng thừa ở một ngày Thứ Bảy năm 2020 nằm ngoài 60
    phiên ⇒ không chặn (nợ kế thừa), nhưng khi mở rộng cửa sổ để bao trùm 2020 thì phải thấy.
    Nếu chỉ im lặng bỏ qua, ta vừa tạo đúng kiểu "cửa sổ che mắt" mà NQ-17 §II.1 cảnh báo.

    [WHY] Dùng 2020-01-04 (Thứ Bảy) chứ không phải 2020-01-02: 02/01/2020 là phiên THẬT, dùng
    nó sẽ đo nhầm sang "thiếu phiên" thay vì "dòng thừa".
    """
    from src.database.session_continuity import SessionSetMismatchError, assert_session_set_alignment, collect_session_alignment

    s = _sessions_ending("2026-11-05", 60)
    con = _db(s, {"market_wide_breadth_daily": ["2020-01-04"]})
    try:
        # Trong cửa sổ 60 phiên: dòng 2020 nằm NGOÀI ⇒ không báo.
        assert collect_session_alignment(con, "2026-11-05")["ok"] is True
        # Mở rộng cửa sổ bao trùm 2020 ⇒ phải thấy.
        with pytest.raises(SessionSetMismatchError):
            assert_session_set_alignment(con, "2026-11-05", market_window=2000)
    finally:
        con.close()


# ===================================================================
# CSDL THẬT — ghim nợ + chứng minh cổng đã vũ trang
# ===================================================================
def test_live_known_orphans_are_pinned_exactly():
    """[WHY] Ghim chính xác tập "dòng trên ngày không giao dịch" đo ngày 06/11/2026 (3 bảng,
    cùng ngày `2026-09-01`). Bất kỳ dòng thừa MỚI nào — kể cả một ngày lễ khác — làm dict
    lệch ⇒ đỏ ngay. Đây là cách giữ nợ "nhìn thấy được" thay vì để nó trôi.
    """
    from src.database.session_continuity import collect_session_alignment

    con = sqlite3.connect(f"file:{LIVE_DB.as_posix()}?mode=ro", uri=True)
    try:
        report = collect_session_alignment(con, accounting_window=None)
    finally:
        con.close()

    got = dict(report["market_spurious"])
    got.update(report["accounting_spurious"])
    assert got == KNOWN_LIVE_ORPHANS, (
        "tập dòng-trên-ngày-không-giao-dịch đã đổi — KHÔNG nới baseline, phải thanh lý dòng "
        f"thừa hoặc cập nhật kèm lý do: {got}"
    )


def test_live_gate_is_armed_on_the_known_orphans():
    """[WHY] Chứng minh cổng THẬT SỰ chặn trên CSDL sống, không chỉ trên DB giả: gọi
    `assert_session_set_alignment` phải ném lỗi và nêu tên `2026-09-01`. Nếu test này xanh
    nghĩa là cổng đã mù.
    """
    from src.database.session_continuity import SessionSetMismatchError, assert_session_set_alignment

    con = sqlite3.connect(f"file:{LIVE_DB.as_posix()}?mode=ro", uri=True)
    try:
        with pytest.raises(SessionSetMismatchError) as exc:
            assert_session_set_alignment(con, accounting_window=None)
    finally:
        con.close()
    assert "2026-09-01" in str(exc.value)
