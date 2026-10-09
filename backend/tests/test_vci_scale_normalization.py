"""test_vci_scale_normalization.py — chặn tái phát lỗi scale 1000× (lần thứ 3).

BỐI CẢNH
--------
`vci.history()` trả giá theo NGHÌN đồng (20.15); `daily_ohlcv` lưu theo ĐỒNG
(20,150). So sánh trực tiếp ⇒ "lệch" GIẢ 1000×. Lỗi này đã xuất hiện **2 lần**
(05/10 và 08/10) trong script kiểm tra viết tay, mỗi lần phải phát hiện bằng mắt.

Trước đây mỗi script tự viết `ref * 1000`. Nay mọi quy đổi đi qua
`src.utils.price_scale` — một hàm duy nhất, có test.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(PROJECT_ROOT / "backend"), str(PROJECT_ROOT / "backend" / "src")]

from src.utils.price_scale import (  # noqa: E402
    UNIT_THOUSAND_VND,
    UNIT_VND,
    compare_with_store,
    normalize_provider_price,
    prices_match,
    to_thousand_vnd,
    to_vnd,
)


# ── Quy đổi ────────────────────────────────────────────────────────────
def test_quy_doi_tu_nghin_dong():
    assert to_vnd(20.15, UNIT_THOUSAND_VND) == 20150.0
    assert to_vnd(18.25, UNIT_THOUSAND_VND) == 18250.0


def test_quy_doi_tuong_minh_thi_khong_doan():
    """Có unit tường minh ⇒ giá trị lớn cũng KHÔNG bị nhân thêm lần nữa."""
    assert to_vnd(20150.0, UNIT_VND) == 20150.0
    assert to_vnd(20150.0, UNIT_THOUSAND_VND) == 20_150_000.0


def test_heuristic_nguong_1000_la_fallback():
    assert to_vnd(20.15) == 20150.0  # < 1000 ⇒ nghìn đồng
    assert to_vnd(20150.0) == 20150.0  # > 1000 ⇒ đã là đồng


def test_nguoc_ve_nghin_dong():
    assert to_thousand_vnd(20150.0) == 20.15


def test_normalize_provider_price():
    assert normalize_provider_price(20.15, source="vci") == 20150.0


# ── So sánh (đây là chỗ bug thật sự xảy ra) ────────────────────────────
def test_so_sanh_truc_tiep_thi_bao_lech_sai():
    """ĐÂY LÀ BUG: 20.15 (nghìn) vs 20,150 (đồng) phải KHÔNG khớp nếu chưa chuẩn hoá."""
    assert prices_match(20.15, 20150.0) is False


def test_so_sanh_sau_chuan_hoa_thi_khop():
    match, ref_vnd = compare_with_store(20150.0, 20.15, provider_unit=UNIT_THOUSAND_VND)
    assert match is True
    assert ref_vnd == 20150.0


def test_lech_that_thi_van_bao_lech():
    """Chuẩn hoá không được che lệch thật (19.05 ≠ 20,150)."""
    match, ref_vnd = compare_with_store(20150.0, 19.05, provider_unit=UNIT_THOUSAND_VND)
    assert match is False
    assert ref_vnd == 19050.0


def test_none_thi_khong_khop():
    assert prices_match(None, 20150.0) is False
    assert prices_match(20150.0, None) is False
    assert compare_with_store(None, None, provider_unit=UNIT_THOUSAND_VND) == (False, None)


# ── Cảnh báo heuristics: penny stock ──────────────────────────────────
def test_heuristic_co_the_sai_voi_penny_va_phai_truyen_unit():
    """Giá 900đ THẬT sẽ bị heuristic nhân sai 1000× ⇒ luôn truyền unit khi biết."""
    assert to_vnd(900.0) == 900_000.0, "heuristic (có chủ đích, cần biết để tránh)"
    assert to_vnd(900.0, UNIT_VND) == 900.0, "truyền unit ⇒ đúng"


# ── Guard theo LỚP: cấm tái xuất hiện quy đổi ad-hoc ──────────────────
ADHOC = re.compile(r"\bref\s*\*\s*1000|\b1000\s*\*\s*\bref\b")


def _code_only(path: Path) -> dict[int, str]:
    """{dòng: các token CODE} — đã bỏ comment VÀ string literal.

    Cần thiết vì lần chạy đầu guard này bắt nhầm chính docstring của price_scale.py
    (nó nhắc `ref * 1000` để giải thích lỗi cũ). Bỏ string ⇒ chỉ còn code thật.
    """
    import io
    import tokenize

    try:
        src = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    skip = (tokenize.STRING, tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT)
    out: dict[int, str] = {}
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type in skip:
                continue
            out[tok.start[0]] = out.get(tok.start[0], "") + tok.string + " "
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return {}
    return out


def test_khong_con_quy_doi_adhoc_trong_repo():
    """`ref * 1000` là mẫu hình của 2 lần bug đã qua — phải dùng price_scale."""
    offenders = [
        f"{p.relative_to(PROJECT_ROOT)}:{ln}"
        for root in (PROJECT_ROOT / "backend" / "scripts", PROJECT_ROOT / "backend" / "src")
        for p in root.rglob("*.py")
        for ln, code in _code_only(p).items()
        if ADHOC.search(code)
    ]
    assert not offenders, f"còn quy đổi ad-hoc `ref * 1000` (dùng price_scale.compare_with_store): {offenders}"


def test_verify_backfill_day_dung_ham_chuan_hoa():
    """Consumer đã gặp bug này phải import helper, không tự nhân 1000."""
    src = (PROJECT_ROOT / "backend" / "scripts" / "verify_backfill_day.py").read_text(encoding="utf-8")
    assert "from src.utils.price_scale import" in src
    assert "compare_with_store(" in src


@pytest.mark.parametrize(
    "store,provider,expected",
    [(20150.0, 20.15, True), (18250.0, 18.25, True), (20150.0, 21.15, False)],
)
def test_bang_so_lieu_gia_thuc_te(store, provider, expected):
    """Chép lại đúng các số đã gây bug: HPG 20,150 · PNJ 18,250."""
    assert compare_with_store(store, provider, provider_unit=UNIT_THOUSAND_VND)[0] is expected
