"""test_executive_m4.py — M4 ALPHA/RS: hợp đồng nguồn dữ liệu.

BỐI CẢNH (2026-10-08)
---------------------
`rs_ranker` chết 4 tháng vì lỗi pandas 3 và đã được vá. Nhưng chạy `final-decision`
vẫn thấy M4 in "⚠ thiếu dữ liệu rs (radar RS chưa chạy)" — vì M4 đọc
`kq["rs_rating_table"]` trong khi **KHÔNG AI GHI khoá đó** (grep toàn repo: 1 chỗ
đọc, 0 chỗ ghi). Sửa engine không làm M4 sống lại; đó là lỗi tích hợp riêng.

Bài học được đóng thành guard ở đây:
    Một khoá trong `kq` chỉ được ĐỌC mà không ai GHI ⇒ nhánh chết vĩnh viễn.
    Nếu không có writer, consumer PHẢI có fallback đọc nguồn chuẩn.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(PROJECT_ROOT / "backend"), str(PROJECT_ROOT / "backend" / "src")]

MRS_PATH = PROJECT_ROOT / "backend" / "data" / "market_rs.json"


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, PROJECT_ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def er():
    return _load("executive_report", "backend/src/core/executive_report.py")


def _rows(n: int) -> list[dict]:
    return [
        {"symbol": f"S{i:03d}", "rs_rating": 99 - i, "price": 1000.0 + i, "avg_vol_20d": 1e6}
        for i in range(n)
    ]


# ── _m4_alpha ──────────────────────────────────────────────────────────
def test_m4_danh_sach_rong_thi_no_data(er):
    assert er._m4_alpha(None)["status"] == "NO_DATA"
    assert er._m4_alpha([])["status"] == "NO_DATA"
    assert er._m4_alpha({"FPT": {}})["status"] == "NO_DATA"  # dict không phải nguồn hợp lệ


def test_m4_tra_ve_top_n_va_tong_so_ma(er):
    out = er._m4_alpha(_rows(30), n=10)
    assert out["status"] == "OK"
    assert out["n_shown"] == 10
    assert out["n_total"] == 30
    assert len(out["top_rs"]) == 10


def test_m4_mac_dinh_top_10(er):
    """Trước đây cứng head(5); báo cáo giờ hiện top 10."""
    assert er._m4_alpha(_rows(30))["n_shown"] == 10


def test_m4_giu_nguyen_so_lieu_khong_bien_doi(er):
    """Zero-hallucination: không được biến đổi giá trị truyền vào."""
    out = er._m4_alpha(_rows(3))
    assert [r["symbol"] for r in out["top_rs"]] == ["S000", "S001", "S002"]
    assert out["top_rs"][0]["rs_rating"] == 99


# ── Guard cho lớp lỗi "khoá chỉ đọc" ───────────────────────────────────
def test_khoa_rs_rating_table_neu_khong_ai_ghi_thi_phai_co_fallback():
    """Nếu repo không có writer cho `rs_rating_table`, orchestrator PHẢI có fallback."""
    write_pat = re.compile(r"""\[["']rs_rating_table["']\]\s*=""")
    writers = [
        f"{p.relative_to(PROJECT_ROOT)}:{i}"
        for p in list((PROJECT_ROOT / "backend" / "src").rglob("*.py")) + [PROJECT_ROOT / "ptck.py"]
        if p.exists()
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if write_pat.search(line.split("#", 1)[0])
    ]
    orch = (PROJECT_ROOT / "backend" / "src" / "engine" / "orchestrator.py").read_text(encoding="utf-8")
    if not writers:
        assert "_load_rs_table_for_report" in orch, (
            "khoá 'rs_rating_table' không có writer nào ⇒ orchestrator phải có fallback "
            "đọc market_rs.json, nếu không M4 sẽ chết im lặng (đã từng xảy ra)"
        )
        assert 'kq.get("rs_rating_table")' in orch, "consumer phải vẫn ưu tiên kq khi có"


@pytest.mark.skipif(not MRS_PATH.exists(), reason="chưa có market_rs.json (chưa chạy rs_ranker)")
def test_market_rs_json_sap_giam_dan_theo_rs_rating():
    """Nguồn chuẩn cho M4: mọi bản ghi có khoá bắt buộc, sắp được theo rs_rating."""
    import json

    data = json.loads(MRS_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, list) and data
    for rec in data:
        assert {"symbol", "rs_rating", "price", "avg_vol_20d"} <= set(rec)
    scores = [r["rs_rating"] for r in data]
    assert scores == sorted(scores, reverse=True), "market_rs.json phải sắp giảm dần theo rs_rating"
