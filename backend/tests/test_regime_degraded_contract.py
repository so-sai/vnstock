"""Hợp đồng 2 tầng cho `regime_history.is_degraded` (Mục 1/5 backlog, GO 2026-10-16).

WHY có file này: phiên mà bảng độ rộng KHÔNG đủ phủ (`market_wide_breadth_daily.degraded = 1`) vẫn
được ghi vào `regime_history` như phiên bình thường. Tầng TÍNH TÍN HIỆU đọc chéo sẽ ăn cả phiên
degraded như phiên thật ⇒ mọi thống kê regime trên đó là số giả. Hợp đồng: tầng TÍNH TOÁN lọc cứng
`is_degraded = 0`; tầng HIỂN THỊ giữ dòng + phơi cờ. "Không biết" (NULL/thiếu cột) ≠ sạch ⇒ fail-closed.
"""

from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from src.database.series_quality import (  # noqa: E402
    LIVE_REGIME_SQL,
    assert_regime_degraded_column,
    breadth_degraded_flag,
)

DB = ROOT / "backend" / "data" / "screener_cache.db"

#: Đường TÍNH TÍN HIỆU/điểm: đọc `regime_history` ở đây BẮT BUỘC lọc phiên suy giảm.
LIVE_PATHS = (
    "backend/src/engine",
    "backend/src/governor",
    "backend/src/cao_readiness",
    "backend/src/portfolio",
)

#: Tầng HIỂN THỊ/API: giữ dòng degraded nhưng phải phơi cờ (không áp vị từ lọc).
DISPLAY_WHITELIST = ("backend/src/database/timeline_manager.py",)


def _scan_reads(text: str, *, window: int = 400) -> list[str]:
    """Đoạn SQL đọc `regime_history` mà thiếu vị từ lọc cờ suy giảm."""
    bad: list[str] = []
    for m in re.finditer(r"FROM\s+regime_history\b", text, flags=re.IGNORECASE):
        snippet = text[max(0, m.start() - window) : m.end() + window]
        if "SELECT" in snippet.upper() and not any(
            k in snippet for k in ("is_degraded", "LIVE_REGIME_SQL", "assert_regime_degraded_column")
        ):
            bad.append(f"…{snippet[:120]}…")
    return bad


def test_bo_quet_THAT_SU_phat_hien_duoc_doc_tran():
    """Ca chống vá quá tay: bộ quét phải BẮT được đoạn đọc trần, và KHÔNG báo oan đoạn đã lọc."""
    xau = 'conn.execute("SELECT date, status FROM regime_history ORDER BY date").fetchall()'
    assert _scan_reads(xau)
    tot = 'conn.execute(f"SELECT date, status FROM regime_history WHERE {LIVE_REGIME_SQL} ORDER BY date")'
    assert not _scan_reads(tot)


def test_khong_duong_tin_hieu_nao_doc_regime_history_tran():
    offenders: list[str] = []
    for d in LIVE_PATHS:
        for p in sorted((ROOT / d).rglob("*.py")):
            rel = p.relative_to(ROOT).as_posix()
            bad = _scan_reads(p.read_text(encoding="utf-8", errors="replace"))
            if bad:
                offenders.append(f"{rel}: {bad}")
    assert not offenders, "Đọc trần regime_history (ăn cả phiên degraded) ở đường tín hiệu:\n" + "\n".join(offenders)


def test_whitelist_display_trong_sang():
    for rel in DISPLAY_WHITELIST:
        assert (ROOT / rel).exists(), f"whitelist trỏ file không tồn tại: {rel}"


def test_moi_writer_regime_history_deu_phoi_co_suy_giam():
    """Có writer nào ghi `regime_history` mà KHÔNG set `is_degraded` ⇒ dòng đó thành NULL (mù với tầng tín hiệu)."""
    writers = []
    for d in ("backend/src",):
        for p in sorted((ROOT / d).rglob("*.py")):
            tx = p.read_text(encoding="utf-8", errors="replace")
            if re.search(r'save_data_upsert\(\s*"regime_history"', tx) or re.search(
                r"to_sql\(\s*\"regime_history\"", tx
            ):
                writers.append((p.relative_to(ROOT).as_posix(), "is_degraded" in tx))
    assert writers, "không tìm thấy writer nào — test này vô nghĩa, kiểm lại mẫu quét"
    thieu = [w for w, ok in writers if not ok]
    assert not thieu, f"writer ghi regime_history thiếu cờ suy giảm: {thieu}"


def test_assert_cot_fail_closed_khi_thieu():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE regime_history (date TEXT)")
    with pytest.raises(RuntimeError, match="is_degraded"):
        assert_regime_degraded_column(conn)
    conn.close()


def test_suy_ra_co_tu_bang_do_rong_chu_khong_doan():
    """Cờ phải suy ra từ `market_wide_breadth_daily.degraded` cùng ngày; ngày lạ ⇒ None (không biết ≠ 0)."""
    conn = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    try:
        # 2026-10-13 là phiên degraded đã đo (743/750 mã) — cờ phải = 1
        assert breadth_degraded_flag(conn, "2026-10-13") == 1
        # 2026-10-16 là phiên đủ phủ (865 mã, degraded=0)
        assert breadth_degraded_flag(conn, "2026-10-16") == 0
        assert breadth_degraded_flag(conn, "1990-01-01") is None
    finally:
        conn.close()


def test_store_that_co_bang_khop_voi_nguon_suy_ra():
    """Bất biến mạnh: MỌI dòng regime_history khớp được ngày với bảng độ rộng phải có cờ == nguồn."""
    conn = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    try:
        assert_regime_degraded_column(conn)
        total, degraded, nulls = conn.execute(
            "SELECT COUNT(*), SUM(COALESCE(is_degraded,0)), SUM(is_degraded IS NULL) FROM regime_history"
        ).fetchone()
        assert total > 1000, f"regime_history quá ít dòng ({total}) — kiểm lại DB"
        assert nulls == 0, f"{nulls} dòng thiếu cờ suy giảm"
        lech = conn.execute(
            "SELECT COUNT(*) FROM regime_history r JOIN market_wide_breadth_daily b ON b.date = r.date "
            "WHERE r.is_degraded IS NOT b.degraded"
        ).fetchone()[0]
        assert lech == 0, f"{lech} dòng có cờ LỆCH với bảng độ rộng"
        # và tầng tín hiệu chỉ thấy phần đã lọc
        live = conn.execute(f"SELECT COUNT(*) FROM regime_history WHERE {LIVE_REGIME_SQL}").fetchone()[0]
        assert live == total - degraded
        assert 0 < degraded < total * 0.1, f"số phiên degraded bất thường: {degraded}/{total}"
    finally:
        conn.close()
