"""Test VIEW sạch `v_market_foreign_daily` + cửa đọc `foreign_flow.daily_net_value`.

[WHY]
  `market_foreign_history` trộn hai thế hệ: `daily` (đơn vị tỷ đồng, đúng) và `legacy_level`
  (05/04→31/07/2026, scale sai). Đo 21/10/2026: 50.675 dòng legacy cộng ra **−106.613.841 tỷ** —
  `SELECT SUM(net_value)` trần trả về con số này mà KHÔNG báo lỗi. Cần một cửa đọc duy nhất + tripwire.

[INVARIANT được canh]
  1. View chỉ chứa `flow_kind='daily'`; tổng qua view KHÔNG phụ thuộc dòng legacy.
  2. `daily_net_value()` đọc view; view thiếu ⇒ raise (không fallback bảng gốc).
  3. Tripwire: |tổng| > 1 triệu tỷ ⇒ raise (nghi trộn đơn vị).
  4. Không file nào trong `backend/src` được `SELECT ... FROM market_foreign_history` mà thiếu
     `flow_kind` (trừ danh sách miễn trừ: ghi dữ liệu / bảo trì).
"""

from __future__ import annotations

import importlib.util
import re
import sqlite3
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from src.database import foreign_flow as ff

_SPEC = importlib.util.spec_from_file_location(
    "create_foreign_flow_view", BACKEND / "scripts" / "create_foreign_flow_view.py"
)
assert _SPEC and _SPEC.loader
mkview = importlib.util.module_from_spec(_SPEC)
sys.modules["create_foreign_flow_view"] = mkview
_SPEC.loader.exec_module(mkview)

# File được phép đọc bảng gốc không lọc flow_kind: ghi dữ liệu & bảo trì (COUNT/DELETE theo ngày).
RAW_ALLOWED = {"db_maintenance.py"}


def _sanctioned_filters() -> set[str]:
    """Tên các hằng số lọc hợp lệ (định nghĩa trong foreign_flow_guard chứa 'flow_kind').

    [WHY] `money_flow_engine` dùng `{DAILY_FLOW_FILTER}` (import từ foreign_flow_guard) — đúng chuẩn,
    nhưng guard quét văn bản sẽ không thấy chữ 'flow_kind' tại chỗ. Resolve hằng số thay vì miễn trừ file,
    nếu không thì hoặc guard báo oan, hoặc phải nới luật.
    """
    txt = (BACKEND / "src" / "database" / "foreign_flow_guard.py").read_text(encoding="utf-8", errors="ignore")
    return {
        m.group(1)
        for m in re.finditer(r"^([A-Z][A-Z0-9_]*)\s*=\s*[\"'][^\"']*flow_kind[^\"']*[\"']", txt, re.MULTILINE)
    }


def make_db() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.execute(
        "CREATE TABLE market_foreign_history (symbol TEXT, date TEXT, foreign_vol REAL, net_vol REAL,"
        " net_value REAL, flow_kind TEXT, source TEXT, fetched_at TEXT, is_verified INTEGER)"
    )
    rows = [
        ("AAA", "2026-10-19", -65_000, -0.467, "daily", "vci_price_board", 1),
        ("BBB", "2026-10-19", 120_000, 1.5, "daily", "vci_price_board", 1),
        ("VIC", "2026-06-26", -3_466_440_055, -790_348.33, "legacy_level", "unknown", 0),
        ("AAA", "2026-07-31", -387_261_741, -2_753.43, "legacy_level", "unknown", 0),
    ]
    for r in rows:
        c.execute(
            "INSERT INTO market_foreign_history (symbol,date,net_vol,net_value,flow_kind,source,is_verified)"
            " VALUES (?,?,?,?,?,?,?)",
            r,
        )
    c.commit()
    return c


# [WHY] Đây là toàn bộ mục đích của view: cộng đúng phần `daily`, loại hẳn thế hệ legacy sai scale.
def test_view_chi_chua_daily():
    c = make_db()
    assert mkview.ensure_view(c) is True  # vừa tạo mới

    assert c.execute(f"SELECT COUNT(*) FROM {mkview.VIEW}").fetchone()[0] == 2
    assert c.execute(f"SELECT COUNT(*) FROM {mkview.VIEW} WHERE flow_kind <> 'daily'").fetchone()[0] == 0
    raw = c.execute("SELECT SUM(net_value) FROM market_foreign_history").fetchone()[0]
    clean = c.execute(f"SELECT SUM(net_value) FROM {mkview.VIEW}").fetchone()[0]
    assert raw < -790_000  # tổng trần bị legacy kéo xuống vô lý
    assert clean == pytest.approx(-0.467 + 1.5)  # chỉ còn 2 dòng daily


# [WHY] Idempotent: gọi lại không lỗi, không tạo trùng, không đụng dữ liệu (chạy được trong pipeline).
def test_tao_view_idempotent():
    c = make_db()
    assert mkview.ensure_view(c) is True
    assert mkview.ensure_view(c) is False
    assert c.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='view'").fetchone()[0] == 1
    assert mkview.verify(c) == []


# [WHY] Cửa đọc phải trả đúng tổng sạch, cả khi lọc khoảng ngày.
def test_daily_net_value_doc_view():
    c = make_db()
    mkview.ensure_view(c)
    assert ff.daily_net_value(c) == pytest.approx(1.033)
    assert ff.daily_net_value(c, start="2026-10-19", end="2026-10-19") == pytest.approx(1.033)
    assert ff.daily_net_value(c, start="2026-11-01") == 0.0


# [WHY] Fail-closed: view chưa tạo thì PHẢI nổ, tuyệt đối không lặng lẽ đọc bảng gốc (nơi có legacy).
def test_view_thieu_thi_raise():
    c = make_db()  # chưa ensure_view
    with pytest.raises(RuntimeError, match="chưa tồn tại"):
        ff.daily_net_value(c)


# [WHY] Tripwire đơn vị: nếu view (hoặc dữ liệu daily) bị nhân sai scale, tổng vượt 1 triệu tỷ ⇒ phải DỪNG
# thay vì trả một con số vô nghĩa cho tầng quyết định.
def test_tripwire_do_lon_bat_kha_thi():
    c = make_db()
    mkview.ensure_view(c)
    c.execute(
        "INSERT INTO market_foreign_history (symbol,date,net_vol,net_value,flow_kind,source,is_verified)"
        " VALUES ('XXX','2026-10-20',1,-5_000_000,'daily','vci_price_board',1)"
    )
    c.commit()
    with pytest.raises(ValueError, match="bất khả thi"):
        ff.daily_net_value(c)


# [WHY] Guard tĩnh: consumer TƯƠNG LAI không được lặp lại lỗi cũ. Bắt ngay ở CI thay vì chờ phát hiện
# bằng một con số sai trong báo cáo.
def test_khong_ai_select_bang_goc_thieu_flow_kind():
    src = BACKEND / "src"
    sanctioned = _sanctioned_filters()
    assert sanctioned, "không tìm thấy hằng số lọc nào trong foreign_flow_guard — guard sẽ báo oan"
    offenders = []
    for p in src.rglob("*.py"):
        if p.name in RAW_ALLOWED:
            continue
        txt = p.read_text(encoding="utf-8", errors="ignore")
        for m in re.finditer(r"FROM\s+market_foreign_history", txt):
            window = txt[max(0, m.start() - 400) : m.end() + 400]
            if "flow_kind" in window or any(name in window for name in sanctioned):
                continue
            offenders.append(f"{p.relative_to(src)}:{txt[: m.start()].count(chr(10)) + 1}")
    assert not offenders, f"SELECT bảng gốc thiếu flow_kind: {offenders}"
