"""Hàng rào dữ liệu TỔNG HỢP: cấm module tín hiệu/điểm tiêu thụ `is_synthetic = 1`.

WHY có file này: `volume_profile`/`active_demand` là **ước lượng mô hình** (chia đều khối lượng phiên
lên dải [low, high] từ OHLCV EOD — truy vết 2026-10-16), không phải khối lượng khớp thật tại từng mức
giá. Đã dán nhãn bằng migration; bộ test này giữ cho (a) hàng rào chặn thật, và (b) **không consumer nào
trong đường tín hiệu đọc trần 2 bảng đó** — kể cả người viết code sau này.
Fail-closed: thiếu cột nhãn hoặc nhãn NULL ⇒ coi như TỔNG HỢP và `raise`, không mặc định "sạch".
"""

from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from src.database.synthetic_guard import (
    SYNTHETIC_TABLES,
    SyntheticDataError,
    assert_consumable,
    describe_labels,
    has_label,
    synthetic_safe_predicate,
)

DB = ROOT / "backend" / "data" / "financial_facts.db"

#: Đường TÍN HIỆU/ĐIỂM: nếu ai đó đọc 2 bảng vi cấu trúc ở đây thì phải lọc `is_synthetic = 0`.
SIGNAL_PATHS = (
    "backend/src/engine",
    "backend/src/financial",
    "backend/src/analysis",
    "backend/src/governor",
    "backend/src/portfolio",
    "backend/src/core/holdings",
)

#: Ngoại lệ có lý do: đây là bên GHI/TẠO ra dữ liệu tổng hợp, không phải bên tiêu thụ để ra tín hiệu.
WRITER_WHITELIST = (
    "backend/src/financial/market_behavior_engine.py",  # compute_volume_profile (chia đều [low,high])
    "backend/src/backtest/backfill_behavior_pit.py",     # dựng bù lịch sử PIT
)


def _scan_reads(text: str, *, window: int = 400) -> list[str]:
    """Trả về các đoạn SQL đọc trần 2 bảng (có SELECT, thiếu dấu hiệu đã lọc nhãn).

    Chấp nhận cả 2 cách lọc hợp lệ: literal `is_synthetic` hoặc ghép vị từ từ hàng rào
    (`SYNTHETIC_SAFE_SQL` / `synthetic_safe_predicate` / `assert_consumable`).
    """
    safe_markers = ("is_synthetic", "SYNTHETIC_SAFE_SQL", "synthetic_safe_predicate", "assert_consumable")
    bad: list[str] = []
    for t in SYNTHETIC_TABLES:
        for m in re.finditer(rf"FROM\s+{t}\b", text, flags=re.IGNORECASE):
            lo = max(0, m.start() - window)
            snippet = text[lo : m.end() + window]
            if "SELECT" in snippet.upper() and not any(k in snippet for k in safe_markers):
                bad.append(f"{t}: …{snippet[:120]}…")
    return bad


def test_bo_quet_THAT_SU_phat_hien_duoc_doc_tran():
    """Ca chống vá quá tay: bộ quét phải BẮT được một đoạn đọc trần (nếu không thì test dưới vô nghĩa)."""
    xau = 'conn.execute("SELECT poc, vah, val FROM volume_profile WHERE symbol = ?", (s,))'
    assert _scan_reads(xau), "bộ quét KHÔNG phát hiện được đọc trần ⇒ hàng rào rỗng nghĩa"
    tot = 'conn.execute(f"SELECT poc FROM volume_profile WHERE symbol = ? AND {SYNTHETIC_SAFE_SQL}", (s,))'
    assert not _scan_reads(tot), "đoạn đã lọc đúng bị báo oan"


def test_khong_module_tin_hieu_nao_doc_tran_hai_bang_vi_cau_truc():
    """Quét toàn bộ đường tín hiệu/điểm: không file nào SELECT 2 bảng này mà thiếu vị từ nhãn."""
    offenders: list[str] = []
    for d in SIGNAL_PATHS:
        for p in sorted((ROOT / d).rglob("*.py")):
            rel = p.relative_to(ROOT).as_posix()
            if rel in WRITER_WHITELIST:
                continue
            bad = _scan_reads(p.read_text(encoding="utf-8", errors="replace"))
            if bad:
                offenders.append(f"{rel}: {bad}")
    assert not offenders, "Đọc trần dữ liệu TỔNG HỢP trong đường tín hiệu:\n" + "\n".join(offenders)


def test_whitelist_khong_muc_rua():
    """Whitelist phải trỏ tới file CÓ THẬT và đúng là bên tạo dữ liệu (chia đều dải ngày)."""
    for rel in WRITER_WHITELIST:
        p = ROOT / rel
        assert p.exists(), f"whitelist trỏ file không tồn tại: {rel}"
    tx = (ROOT / "backend/src/financial/market_behavior_engine.py").read_text(encoding="utf-8")
    assert "vol_per_unit" in tx or "span" in tx, "writer phải là bên chia đều khối lượng lên dải giá"


def test_guard_chan_dong_synthetic():
    with pytest.raises(SyntheticDataError, match="TỔNG HỢP"):
        assert_consumable(
            [{"is_synthetic": 1, "method": "UNIFORM_WITHIN_DAILY_RANGE", "derived_from": "x"}],
            table="volume_profile",
            consumer="test_scoring_engine",
        )


def test_guard_chan_nhan_NULL_va_nhan_thieu():
    """FAIL-CLOSED: không biết nhãn ⇒ cấm (không mặc định là sạch)."""
    with pytest.raises(SyntheticDataError):
        assert_consumable([{"is_synthetic": None}], table="active_demand", consumer="c")
    with pytest.raises(SyntheticDataError):
        assert_consumable([{}], table="active_demand", consumer="c")


def test_guard_cho_qua_dong_da_loc():
    assert_consumable([{"is_synthetic": 0}], table="volume_profile", consumer="c")  # không raise


def test_thieu_cot_nhan_thi_raise_fail_closed():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE volume_profile (id INTEGER)")
    with pytest.raises(SyntheticDataError, match="thiếu cột nhãn"):
        synthetic_safe_predicate(conn, "volume_profile")
    assert has_label(conn, "volume_profile") is False
    conn.close()


# ── CANARY trên store THẬT (số đo, không suy diễn) ───────────────────────────────


def _store() -> sqlite3.Connection:
    if not DB.exists():
        pytest.skip("không có financial_facts.db")
    return sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)


def test_store_that_hai_bang_da_duoc_dan_nhan_100pct():
    conn = _store()
    try:
        info = describe_labels(conn)
    finally:
        conn.close()
    for t in SYNTHETIC_TABLES:
        d = info[t]
        assert d["labelled"] is True, f"{t} chưa có cột nhãn"
        assert d["nulls"] == 0, f"{t} còn {d['nulls']} dòng nhãn NULL"
        assert d["synthetic"] == d["total"], f"{t}: {d['synthetic']}/{d['total']} gắn is_synthetic=1"
        assert d["methods"] == ["UNIFORM_WITHIN_DAILY_RANGE"], f"{t} method lạ: {d['methods']}"


def test_store_that_dong_that_bi_guard_chan():
    """Đọc 1 dòng thật từ `volume_profile` ⇒ `assert_consumable` PHẢI chặn."""
    conn = _store()
    try:
        conn.row_factory = sqlite3.Row
        row = dict(conn.execute("SELECT * FROM volume_profile LIMIT 1").fetchone())
    finally:
        conn.close()
    with pytest.raises(SyntheticDataError):
        assert_consumable([row], table="volume_profile", consumer="canary_store_that")
