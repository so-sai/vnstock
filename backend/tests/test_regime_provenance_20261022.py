"""Kiểm chứng provenance `regime_history` 2026-10-22 sau khi vá Self-Seed & Restatement.

[WHY]
  Sổ giả thuyết `H-2026-10-22-REGIME-UNFREEZE` (đóng băng trước giờ mở cửa 22/10) ghi
  `regime_score = 0,35 / CRISIS`, kết luận **3/4 điều kiện ⇒ kịch bản B, giữ `ENTRY_LOCK`**.
  Trước đây `screener_cache.db:regime_history` cùng ngày lại lưu `0,4551 / RANGING` do lỗi
  EMA self-seeding feedback loop. Sau remediation, dòng DB đã được restatement về `0,3459 / CRISIS`,
  khớp hoàn toàn với replay tất định và sổ đăng ký.

[ROOT CAUSE ĐÃ VÁ]
  `detect_regime(target_date=None)` trước đây truy vấn seed thiếu mệnh đề `WHERE date < as_of`.
  Khi `daily_updater.py:944` chạy nhiều lần trong ngày, `save_data_upsert` lấy chính giá trị của ngày
  hôm đó làm seed cho lần tính tiếp theo, làm điểm số trôi dần: 0,3459 -> 0,4057 -> 0,4526 -> 0,4551 -> 0,4914.
  Đã bổ sung vị từ bắt buộc `WHERE date < as_of_str` cho toàn bộ các truy vấn seed (b_hist, prev_score, v_breadth).

[INVARIANT]
  1. Cùng một ngày, `detect_regime` phải cho CÙNG một score, bất kể đường gọi (live/replay).
  2. Seed EMA phải là phiên TRƯỚC `as_of`, KHÔNG BAO GIỜ là chính `as_of`.
  3. Mọi lần cập nhật/restatement phải ghi vết kiểm toán vào `eod_write_audit_ledger` (Append-Only).
  4. Chạy `detect_regime` N lần liên tiếp trong ngày phải bất biến tuyệt đối (Drift = 0).
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from src.engine import regime_engine as re_

DB = BACKEND / "data" / "screener_cache.db"
AS_OF = "2026-10-22"
REGISTERED_SCORE = 0.35  # sổ H-2026-10-22, đóng băng trước mở cửa 22/10
REPLAY_SCORE = 0.3459  # detect_regime("2026-10-22") với seed 21/10 = 0,27

pytestmark = pytest.mark.skipif(not DB.exists(), reason="không có screener_cache.db")


def _db_row(date: str) -> tuple[float, str]:
    con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    try:
        row = con.execute(
            "SELECT regime_score, status FROM regime_history WHERE date = ? ORDER BY rowid DESC LIMIT 1",
            (date,),
        ).fetchone()
    finally:
        con.close()
    assert row is not None, f"thiếu dòng regime_history {date}"
    return float(row[0]), str(row[1])


def _has_breadth_row(date: str) -> bool:
    con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    try:
        return (
            con.execute("SELECT 1 FROM market_wide_breadth_daily WHERE date = ? LIMIT 1", (date,)).fetchone()
            is not None
        )
    finally:
        con.close()


# [WHY] Sổ giả thuyết chỉ đáng tin nếu con số 0,35 của nó TÁI LẬP ĐƯỢC bằng replay tất định.
def test_replay_22_10_tai_lap_dung_so_da_dang_ky():
    v = re_.detect_regime(AS_OF)
    assert v["status"] == "CRISIS"
    assert v["regime_score"] == pytest.approx(REPLAY_SCORE, abs=5e-3)
    assert v["regime_score"] == pytest.approx(REGISTERED_SCORE, abs=0.01)


# [WHY] Bất biến #1: đọc lịch sử phải idempotent. Hai lần replay cùng ngày không được lệch nhau.
def test_replay_idempotent():
    first = re_.detect_regime(AS_OF)["regime_score"]
    second = re_.detect_regime(AS_OF)["regime_score"]
    assert first == second


# [WHY] Đảm bảo truy vấn seed của LIVE (date < as_of) không bao giờ trỏ vào chính ngày đang tính.
def test_seed_query_live_khong_doc_chinh_dong_hom_nay():
    con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    try:
        as_of = con.execute("SELECT MAX(date) FROM daily_ohlcv").fetchone()[0]
        seed_date = con.execute(
            "SELECT date FROM regime_history WHERE date < ? AND is_degraded = 0 ORDER BY date DESC LIMIT 1",
            (as_of,),
        ).fetchone()[0]
    finally:
        con.close()
    assert as_of >= AS_OF
    assert seed_date < as_of, f"seed LIVE phải là phiên trước ({seed_date} < {as_of})"


# [WHY] Bất biến #1: Khi seed đã được cố định ở phiên trước (date < as_of), score LIVE
# và score REPLAY phải đồng nhất tuyệt đối, không còn bị đẩy trôi (drift).
def test_live_khop_hoan_toan_voi_replay():
    live = re_.detect_regime(None)
    # So sánh trên CÙNG một ngày: as_of của LIVE có thể đã tiến lên phiên mới, nên replay
    # phải dùng chính `live["date"]` — nếu hard-code 22/10, test sẽ so hai phiên khác nhau.
    replay = re_.detect_regime(live["date"])
    diff = abs(live["regime_score"] - replay["regime_score"])
    # Chặn drift THÔ (self-seeding cũ gây lệch 0,11) ngay cả khi phiên chưa chốt.
    assert diff < 0.02, f"drift live/replay quá lớn: {live['regime_score']} vs {replay['regime_score']}"
    if not _has_breadth_row(live["date"]):
        # Phiên chưa chốt EOD: LIVE đọc breadth từ tape hiện tại, REPLAY đọc từ bảng EOD.
        # Khác nguồn ⇒ không so tuyệt đối; chỉ kiểm drift thô ở trên. Chờ daily_closer.
        pytest.skip(f"{live['date']} chưa có market_wide_breadth_daily — chưa chốt EOD")
    assert diff == pytest.approx(0.0, abs=2e-3)
    assert live["status"] == replay["status"]


# [WHY] Bằng chứng remediation: Dòng DB 22/10 đã được Restatement về giá trị chuẩn xác 0,3459 / CRISIS,
# khớp hoàn toàn với replay tất định và sổ đăng ký H-2026-10-22.
def test_dong_db_22_10_da_duoc_restatement_khop_replay():
    db_score, db_status = _db_row(AS_OF)
    replay = re_.detect_regime(AS_OF)["regime_score"]
    assert db_score == pytest.approx(REPLAY_SCORE, abs=5e-3)
    assert db_status == "CRISIS"
    assert abs(db_score - replay) < 1e-3, f"dòng DB ({db_score}) phải khớp replay ({replay})"


# [WHY] Regression test chặn tái diễn: Chạy detect_regime N lần liên tiếp trong ngày,
# điểm số đầu ra phải bất biến tuyệt đối (Drift = 0).
def test_detect_regime_n_lan_lien_tiep_zero_drift():
    scores = [re_.detect_regime(None)["regime_score"] for _ in range(5)]
    assert len(set(scores)) == 1, f"Phát hiện drift giữa các lần chạy liên tiếp: {scores}"


# [WHY] Kiểm toán sổ cái: eod_write_audit_ledger phải ghi nhận bản ghi Restatement cho ngày 2026-10-22.
def test_eod_write_audit_ledger_ghi_nhan_restatement():
    con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    try:
        row = con.execute(
            "SELECT as_of_date, caller_module, output_score, status, notes "
            "FROM eod_write_audit_ledger WHERE as_of_date = ? AND target_table = 'regime_history' "
            "ORDER BY audit_id DESC LIMIT 1",
            (AS_OF,),
        ).fetchone()
    finally:
        con.close()
    assert row is not None, "thiếu bản ghi audit ledger cho 2026-10-22"
    assert row[0] == AS_OF
    assert row[2] == pytest.approx(REPLAY_SCORE, abs=5e-3)
    assert row[3] == "RESTATED"


# [WHY] Code và tài liệu phải nói cùng một sự thật: `input_hash` là SHA-256 ĐẦY ĐỦ 64 hex,
# không phải prefix 16 ký tự. Ca này chặn việc ai đó "tối ưu" cắt ngắn trở lại.
def test_input_hash_la_sha256_day_du_64_hex():
    from src.database.eod_write_audit import _calculate_input_hash

    first = _calculate_input_hash({"a": 1})
    second = _calculate_input_hash({"a": 2})
    assert len(first) == 64
    assert all(ch in "0123456789abcdef" for ch in first)
    assert first != second
