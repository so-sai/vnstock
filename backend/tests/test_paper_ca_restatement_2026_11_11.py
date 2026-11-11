"""test_paper_ca_restatement_2026_11_11.py — Vá CA bị bỏ sót + tái lập đường vốn.

WHY: Ngày 2026-11-11 phát hiện TCB chia cổ tức cổ phiếu 100:50 (GDKHQ 2026-10-29,
chốt 2026-10-30) bị bỏ sót khỏi `paper_corporate_actions`. Giá bar đã điều chỉnh
(÷1,5) nhưng sổ vẫn giữ 5.900 cp nên `market_value` của 9 phiên 2026-10-29 →
2026-11-10 hụt 577.610.000đ (~64 triệu/phiên). `apply_corporate_actions()` chỉ
quét `ex_date == ngày đang chạy` nên sự kiện quá khứ KHÔNG BAO GIỜ được áp dụng.

BOUNDARY: Sửa lịch sử kế toán phải fail-closed: không tái lập khi có giao dịch
trong cửa sổ, không suy diễn giá khi thiếu nến, không áp dụng CA hai lần.
"""
import pytest

from conftest import TEST_DATES, TEST_PORTFOLIO, TEST_SYMBOL


T0 = TEST_DATES[0]
T1 = TEST_DATES[2]
T2 = TEST_DATES[3]


def _equity_row(date):
    from src.database.db_core import get_connection

    with get_connection() as conn:
        return conn.execute(
            "SELECT settled_cash, market_value, unrealized_pnl, total_equity "
            "FROM paper_equity_curve WHERE portfolio_id=? AND date=?",
            (TEST_PORTFOLIO, date),
        ).fetchone()


def test_stock_div_retroactive_cap_nhat_dung_so_luong_va_gia_von(mtm):
    """[WHY] TCB 100:50 ⇒ 5.900 → 8.850 cp và giá vốn ÷1,5, tổng vốn giữ nguyên.
    Nếu chỉ sửa giá bar mà không nhân số lượng thì MV hụt đúng 1/3."""
    from src.database.db_core import get_connection

    mtm.book_buy(TEST_SYMBOL, 1000, 10000.0, T0)
    with get_connection() as conn:
        q0, cb0 = conn.execute(
            "SELECT quantity, cost_basis FROM paper_lots "
            "WHERE portfolio_id=? AND symbol=? AND is_closed=0",
            (TEST_PORTFOLIO, TEST_SYMBOL),
        ).fetchone()

    res = mtm.register_and_apply_corporate_action(TEST_SYMBOL, T1, "STOCK_DIV", ratio=0.5)
    assert res["status"] == "APPLIED"

    with get_connection() as conn:
        q1, cb1 = conn.execute(
            "SELECT quantity, cost_basis FROM paper_lots "
            "WHERE portfolio_id=? AND symbol=? AND is_closed=0",
            (TEST_PORTFOLIO, TEST_SYMBOL),
        ).fetchone()

    assert q1 == int(q0 * 1.5)
    assert cb1 == pytest.approx(cb0 / 1.5, rel=1e-9)
    # Tổng vốn bỏ ra không đổi (chỉ chia lại trên số CP nhiều hơn)
    assert q1 * cb1 == pytest.approx(q0 * cb0, rel=1e-9)


def test_restate_recompute_market_value_va_total_equity(mtm, seed_test_ohlcv):
    """[WHY] Sau khi áp CA retroactive, dòng equity của phiên ex-date phải được
    tính lại theo số lượng MỚI × close của chính phiên đó (không lấy close hôm nay).
    Đây đúng là khoản hụt 577.610.000đ đang sống trên dữ liệu thật."""
    mtm.book_buy(TEST_SYMBOL, 1000, 10000.0, T0)
    mtm.mark_to_market(T1)  # dòng SAI (chưa áp CA)
    old = _equity_row(T1)

    mtm.register_and_apply_corporate_action(TEST_SYMBOL, T1, "STOCK_DIV", ratio=0.5)
    result = mtm.restate_equity_since(T1)

    new = _equity_row(T1)
    expected_mv = int(1000 * 1.5) * seed_test_ohlcv[T1]
    assert new[1] == pytest.approx(expected_mv, rel=1e-9)
    assert new[3] == pytest.approx(old[0] + expected_mv, rel=1e-9)  # tiền mặt không đổi
    assert new[1] > old[1]
    assert len(result["rows"]) == 1
    assert result["rows"][0]["date"] == T1


def test_restate_tu_choi_khi_co_giao_dich_trong_cua_so(mtm, seed_test_ohlcv):
    """[WHY] Lot hiện tại không đại diện cho vị thế giữa cửa sổ nếu đã có mua/bán;
    ghi đè equity khi đó là bịa số liệu (Missing > Fake). Phải dừng và nêu lý do."""
    mtm.book_buy(TEST_SYMBOL, 1000, 10000.0, T0)
    mtm.mark_to_market(T1)
    mtm.mark_to_market(T2)
    mtm.book_buy(TEST_SYMBOL, 200, 12000.0, T2)  # giao dịch NẰM TRONG cửa sổ

    with pytest.raises(RuntimeError, match="giao dịch"):
        mtm.restate_equity_since(T1)


def test_restate_tu_choi_khi_thieu_nen_ohlcv(mtm, seed_test_ohlcv):
    """[WHY] Không có close ⇒ KHÔNG được lấy cost basis thay thế (fillna ngầm).
    Thiếu dữ liệu thì dừng, ghi rõ mã + ngày."""
    from src.database.db_core import get_connection

    mtm.book_buy(TEST_SYMBOL, 1000, 10000.0, T0)
    mtm.mark_to_market(T1)
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM daily_ohlcv WHERE symbol=? AND date=?", (TEST_SYMBOL, T1)
        )
        conn.commit()

    with pytest.raises(RuntimeError, match="Thiếu nến"):
        mtm.restate_equity_since(T1)


def test_restate_ghi_audit_trail_sha256(mtm, seed_test_ohlcv):
    """[WHY] Sửa lịch sử phải để lại dấu vết kiểm toán (NQ-19/NQ-22): một dòng
    `eod_write_audit_ledger` status RESTATED + SHA-256 của đúng payload đã ghi."""
    from src.database.db_core import get_connection

    mtm.book_buy(TEST_SYMBOL, 1000, 10000.0, T0)
    mtm.mark_to_market(T1)
    mtm.register_and_apply_corporate_action(TEST_SYMBOL, T1, "STOCK_DIV", ratio=0.5)
    result = mtm.restate_equity_since(T1)

    assert len(result["input_hash"]) == 64
    int(result["input_hash"], 16)  # hex hợp lệ

    with get_connection() as conn:
        row = conn.execute(
            "SELECT status, target_table, input_hash, notes FROM eod_write_audit_ledger "
            "WHERE caller_module='paper_mtm.restate_equity_since' "
            "ORDER BY audit_id DESC LIMIT 1"
        ).fetchone()
    assert row is not None
    assert row[0] == "RESTATED"
    assert row[1] == "paper_equity_curve"
    assert row[2] == result["input_hash"]


def test_restate_dry_run_khong_ghi_db(mtm, seed_test_ohlcv):
    """[WHY] Chế độ xem trước phải trả về đúng con số sẽ ghi nhưng tuyệt đối
    không UPDATE, để Hội đồng đối soát trước khi cho --apply."""
    mtm.book_buy(TEST_SYMBOL, 1000, 10000.0, T0)
    mtm.mark_to_market(T1)
    old = _equity_row(T1)
    mtm.register_and_apply_corporate_action(TEST_SYMBOL, T1, "STOCK_DIV", ratio=0.5)

    result = mtm.restate_equity_since(T1, dry_run=True)

    assert _equity_row(T1) == old  # KHÔNG đổi
    assert result["rows"][0]["market_value"] == pytest.approx(
        int(1000 * 1.5) * seed_test_ohlcv[T1], rel=1e-9
    )


def test_apply_ca_hai_lan_khong_nhan_doi_so_luong(mtm, seed_test_ohlcv):
    """[WHY] Chạy lại lệnh vá (idempotent) không được biến 8.850 thành 13.275 cp."""
    from src.database.db_core import get_connection

    mtm.book_buy(TEST_SYMBOL, 1000, 10000.0, T0)
    mtm.register_and_apply_corporate_action(TEST_SYMBOL, T1, "STOCK_DIV", ratio=0.5)
    second = mtm.register_and_apply_corporate_action(TEST_SYMBOL, T1, "STOCK_DIV", ratio=0.5)

    assert second["status"] == "ALREADY_APPLIED"
    with get_connection() as conn:
        qty = conn.execute(
            "SELECT quantity FROM paper_lots WHERE portfolio_id=? AND symbol=? AND is_closed=0",
            (TEST_PORTFOLIO, TEST_SYMBOL),
        ).fetchone()[0]
    assert qty == 1500


def test_apply_ca_tu_choi_tham_so_khac_voi_su_kien_da_dang_ky(mtm, seed_test_ohlcv):
    """[WHY] Nếu sự kiện đã applied với ratio 0,5 mà ai đó chạy lại với 0,6 thì
    phải DỪNG — im lặng nhận tham số mới là nguồn gốc của sổ sách lệch."""
    mtm.book_buy(TEST_SYMBOL, 1000, 10000.0, T0)
    mtm.register_and_apply_corporate_action(TEST_SYMBOL, T1, "STOCK_DIV", ratio=0.5)

    with pytest.raises(RuntimeError, match="tham số"):
        mtm.register_and_apply_corporate_action(TEST_SYMBOL, T1, "STOCK_DIV", ratio=0.6)


def test_preview_adjust_khong_cham_vao_lots(mtm, seed_test_ohlcv):
    """[WHY] `--dry-run` phải là XEM TRƯỚC thật: nếu nó áp CA vào `paper_lots`
    thì Hội đồng không còn gì để duyệt trước khi `--apply`, và một lần dry-run
    sai tham số sẽ để lại sổ đã sửa không thể hoàn tác."""
    from src.database.db_core import get_connection

    mtm.book_buy(TEST_SYMBOL, 1000, 10000.0, T0)
    mtm.mark_to_market(T1)
    old = _equity_row(T1)

    result = mtm.restate_equity_since(
        T1, dry_run=True, preview_adjust={TEST_SYMBOL: {"qty_factor": 1.5, "cost_shift": 0.0}}
    )

    assert result["rows"][0]["market_value"] == pytest.approx(
        int(1000 * 1.5) * seed_test_ohlcv[T1], rel=1e-9
    )
    assert _equity_row(T1) == old  # sổ chưa đổi
    with get_connection() as conn:
        qty, cb = conn.execute(
            "SELECT quantity, cost_basis FROM paper_lots "
            "WHERE portfolio_id=? AND symbol=? AND is_closed=0",
            (TEST_PORTFOLIO, TEST_SYMBOL),
        ).fetchone()
    assert qty == 1000  # lot cũng chưa đổi
    assert cb == pytest.approx(10000.0 * (1 + 15.0 / 10000.0), rel=1e-9)


def test_preview_adjust_bi_tu_choi_khi_khong_phai_dry_run(mtm, seed_test_ohlcv):
    """[WHY] Điều chỉnh chỉ-trong-bộ-nhớ mà lọt vào chế độ ghi thật sẽ tạo dòng
    equity dựa trên vị thế KHÔNG tồn tại — phải chặn ngay ở cửa vào."""
    mtm.book_buy(TEST_SYMBOL, 1000, 10000.0, T0)
    mtm.mark_to_market(T1)

    with pytest.raises(RuntimeError, match="dry_run"):
        mtm.restate_equity_since(
            T1, dry_run=False, preview_adjust={TEST_SYMBOL: {"qty_factor": 1.5, "cost_shift": 0.0}}
        )

