"""GUARD THỨ BA — kiểm chứng NGỮ NGHĨA của các con số trong spec.

Vì sao cần guard này (bài học trực tiếp từ ERRATUM 1):

    | Guard                      | Chứng minh                        | MÙ với                      |
    |----------------------------|-----------------------------------|-----------------------------|
    | hash (test_preregistration)| file == anchor                    | con số trong file có ĐÚNG   |
    | structure (test_spec_str.) | file đủ 16 mục + tham số còn nguyên| con số trong file có ĐÚNG   |
    | **claims (file này)**      | **con số khớp DB thật**           | —                           |

Con số ~740 sống sót qua cả hash guard lẫn structure guard. Nó chỉ chết khi có
người ĐO LẠI. Guard này tự động hoá việc đo lại đó.

CƠ CHẾ: mỗi con số định lượng trong spec §3.1 phải HOẶC (a) khớp số đo được, HOẶC
(b) được ERRATUM nêu đúng số đo được. Nhờ vậy:
    · suite xanh khi sai lệch đã được đính chính công khai;
    · suite ĐỎ khi có con số mới sai xuất hiện mà không ai đính chính.
Đây là điều kiện để pre-registration không bị "trôi số" âm thầm.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(PROJECT_ROOT / "backend"), str(PROJECT_ROOT / "backend" / "src")]

from src.backtest import backtest_trend_2021_2025 as bt  # noqa: E402

SPEC_PATH = PROJECT_ROOT / "docs" / "SPEC_TREND_ENGINE_2021_2025.md"

# Con số NGUYÊN VĂN trong bảng §3.1 của spec. Nếu ai sửa bảng, sửa ở đây — và test
# sẽ nói ngay con số mới có khớp DB không.
SPEC_TABLE_3_1: dict[str, dict[str, int]] = {
    "2021": {"price": 903, "facts": 737},
    "2022": {"price": 915, "facts": 737},
    "2023": {"price": 906, "facts": 750},
    "2024": {"price": 855, "facts": 750},
    "2025": {"price": 879, "facts": 744},
}

# Câu khẳng định mô tả trong §3.1 và số tương ứng nó nói ra.
SPEC_NARRATIVE_CLAIMS = [
    ("universe ≈ 740", 740),
    ("cận trên 903", 903),
    ("~165 mã có giá không cơ bản", 165),
]

# Claim đã bị ERRATUM 1 thay thế — PHẢI KHAI BÁO TƯỜNG MINH, kèm số đúng.
#
# Vì sao không "thấy số đo được trong erratum là tha": cách đó tha cả số bị tiêm vào,
# vì mọi giá trị thật vốn đã có mặt trong erratum. Một ngoại lệ phải được NÊU TÊN, có
# phạm vi (năm, loại), nếu không nó biến thành giấy thông hành chung.
SUPERSEDED_BY_ERRATUM_1: dict[tuple[str, str], int] = {
    ("2021", "facts"): 738,  # spec §3.1 ghi 737
    ("2022", "facts"): 738,  # spec §3.1 ghi 737
}


def _spec_text_lf() -> str:
    return SPEC_PATH.read_bytes().replace(b"\r\n", b"\n").decode("utf-8")


def _erratum_section(text: str) -> str:
    """Chỉ phần ERRATUM — KHÔNG phải cả tài liệu.

    Bài học từ chính guard này: thoạt đầu tôi kiểm `str(actual) in text` trên TOÀN BỘ
    spec. Sai — bảng §3.1 vốn đã chứa 903/915/... nên MỌI con số thật đều "được đính
    chính" một cách giả tạo, và guard tha cho cả con số tiêm vào. Phải giới hạn phạm vi
    kiểm vào đúng nơi erratum được phép nói.
    """
    idx = text.find("## ERRATUM")
    return text[idx:] if idx >= 0 else ""


@pytest.fixture(scope="module")
def pf() -> dict:
    return bt.preflight()


def test_moi_con_so_bang_31_khop_do_duoc_hoac_duoc_erratum(pf):
    """Số trong §3.1 phải khớp DB, hoặc phải đã được ERRATUM đính chính."""
    text = _spec_text_lf()
    erratum = _erratum_section(text)
    problems: list[str] = []
    for year, expected in SPEC_TABLE_3_1.items():
        measured = pf["universe_per_year"][year]
        for kind, claimed in expected.items():
            actual = measured[kind]
            if actual == claimed:
                continue
            # Sai lệch chỉ hợp lệ khi claim được NÊU TÊN trong danh sách đã thay thế,
            # số đúng khớp, VÀ erratum thật sự nêu con số đó.
            excused = SUPERSEDED_BY_ERRATUM_1.get((year, kind)) == actual
            if excused and str(actual) in erratum:
                continue
            problems.append(
                f"§3.1 {year} {kind}: spec ghi {claimed}, ĐO ĐƯỢC {actual}, "
                "và không có erratum nào nêu số đúng"
            )
    assert not problems, "con số trong spec KHÔNG khớp DB thật:\n  " + "\n  ".join(problems)


def test_danh_sach_tha_khong_bi_muc_rua(pf):
    """Ngoại lệ phải còn đúng lý do tồn tại.

    Nếu spec được sửa cho khớp DB, entry tha trở thành thừa — và một danh sách tha
    đầy entry thừa sẽ dần trở thành giấy thông hành chung. Test này buộc dọn.
    """
    stale = []
    for (year, kind), excused_value in SUPERSEDED_BY_ERRATUM_1.items():
        claimed = SPEC_TABLE_3_1[year][kind]
        actual = pf["universe_per_year"][year][kind]
        if claimed == actual:
            stale.append(f"{year}.{kind}: spec đã khớp DB ({actual}) ⇒ bỏ entry tha")
        elif excused_value != actual:
            stale.append(f"{year}.{kind}: số tha {excused_value} ≠ đo được {actual}")
    assert not stale, "danh sách tha cần dọn:\n  " + "\n  ".join(stale)


def test_con_so_740_da_duoc_dinh_chinh(pf):
    """~740 phải nằm trong ERRATUM kèm số đo thật, không được 'sống' như một sự thật."""
    text = _spec_text_lf()
    erratum = _erratum_section(text)
    assert "~740" in text, "erratum phải nhắc lại con số sai (dấu vết kiểm toán)"
    assert "## ERRATUM 1" in erratum
    measured = {int(v["intersection"]) for v in pf["universe_per_year"].values()}
    assert measured, "preflight không đo được giao điểm"
    missing = [m for m in measured if str(m) not in erratum]
    assert not missing, f"erratum phải nêu MỌI số đo được theo năm, thiếu: {missing}"


def test_giao_diem_do_duoc_khong_bao_gio_la_740(pf):
    """Khẳng định trực tiếp: 740 không phải kết quả đo. Nếu nó đúng trở lại, test này
    báo để ta biết dữ liệu đã đổi — chứ không âm thầm dùng lại con số cũ."""
    inter = {y: v["intersection"] for y, v in pf["universe_per_year"].items()}
    assert 740 not in inter.values(), f"740 xuất hiện lại như số đo: {inter}"
    assert sum(inter.values()) / len(inter) < 700, f"giao điểm trung bình vẫn ~740?: {inter}"


def test_guard_nay_THAT_SU_bat_duoc_so_sai(pf, monkeypatch):
    """Chứng minh guard không phải hình thức: tiêm một con số sai vào và bắt nó đỏ.

    Một guard không tự chứng minh được nó bắt được lỗi thì không phải guard — đúng
    như hash guard đã im lặng trước con số 740.
    """
    fake = {"2021": {"price": 999_999, "facts": 999_999}}
    monkeypatch.setattr(sys.modules[__name__], "SPEC_TABLE_3_1", fake)
    with pytest.raises(AssertionError):
        test_moi_con_so_bang_31_khop_do_duoc_hoac_duoc_erratum(pf)


def test_mau_so_bao_cao_phai_la_theo_NAM_khong_phai_union(pf):
    """Spec §3.1: mẫu số là giao điểm THEO NĂM. Union cả cửa sổ là số khác — dùng nó
    làm mẫu số sẽ thổi phồng/tô nhỏ mọi tỷ lệ phần trăm."""
    per_year = {v["intersection"] for v in pf["universe_per_year"].values()}
    assert pf["effective_universe"] >= max(per_year), (
        "union của cả cửa sổ phải ≥ mẫu số từng năm (kiểm tra chiều so sánh)"
    )


def test_cac_cau_khang_dinh_mo_ta_trong_spec_deu_co_so_do_hoac_erratum():
    """Mọi con số trong câu mô tả của §3.1 phải truy được về đo lường hoặc erratum."""
    text = _spec_text_lf()
    thieu = [label for label, num in SPEC_NARRATIVE_CLAIMS if str(num) not in text]
    assert not thieu, f"mất dấu vết của các khẳng định: {thieu}"
