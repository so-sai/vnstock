"""test_spec_structure.py — chốt an toàn KÉP cho spec đã niêm phong.

VÌ SAO CẦN (bài học 2026-10-09)
--------------------------------
`test_preregistration_hash.py` chứng minh **file không đổi so với anchor**. Nó KHÔNG
chứng minh **file còn đầy đủ**: trong lúc tôi sửa spec lên v1.1, một patch có
`old_string` bao trọn cả mục §13 đã **xoá mất header + 3 câu hỏi mở**. Hash guard
không hề bắt được — nó chỉ báo "hash khác anchor" (đúng, vì tôi đang sửa thật), rồi
sau khi cập nhật anchor thì "pass". Một lần xoá nhầm và một lần sửa chủ đích **trông
giống hệt nhau** với hash guard.

Hai guard bù nhau:
    hash guard      → file == anchor          (tính toàn vẹn)
    structure guard → file có đủ mục + từ khoá (tính đầy đủ)
Không cái nào thay được cái kia.
"""

from __future__ import annotations

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SPEC = PROJECT_ROOT / "docs" / "SPEC_TREND_ENGINE_2021_2025.md"

# 16 mục cấp 1, 0..15
REQUIRED_SECTIONS = [f"## {i}." for i in range(16)]

# Mục con bắt buộc (số + tiêu đề; chỉ cần số mục là đủ để phát hiện mất mục)
REQUIRED_SUBSECTIONS = ["### 2.3", "### 3.1", "### 13.4", "### 15.1", "### 15.2", "### 15.3"]

# Từ khoá cốt tử: mỗi cái đại diện một quyết định đã khóa. Mất từ khoá = mất luật.
REQUIRED_KEYWORDS = {
    "TREND_BUY": "công thức entry (§4)",
    "POLARITY_MISMATCH": "hệ số phạt vị thế ×0.5 (§6, §13)",
    "Sharpe": "tiêu chí thành công (§8)",
    "Calmar": "tiêu chí thành công (§8)",
    "DISTRESSED": "chặn tuyệt đối theo Health (§3, §4)",
    "ADV20": "cổng thanh khoản + xấp xỉ tiêu chí sàn (§2.3)",
    "Moat": "ngưỡng organ (§4)",
    "v1.1": "phiên bản đã niêm phong",
    "KHÔNG CÓ BẢN v1.2": "cam kết khóa cứng (§13.4)",
    "2021-01-01": "cửa sổ backtest (§1, §11)",
}


def _spec_text_lf() -> str:
    return SPEC.read_text(encoding="utf-8").replace("\r\n", "\n")


def test_spec_ton_tai():
    assert SPEC.exists(), f"thiếu spec đã niêm phong: {SPEC}"


def test_du_16_muc_cap_1():
    text = _spec_text_lf()
    missing = [s for s in REQUIRED_SECTIONS if not re.search(rf"^{re.escape(s)}", text, re.M)]
    assert not missing, f"spec MẤT mục cấp 1: {missing} — nếu cố ý, phải là file spec mới (variant)"


def test_du_muc_con_quan_trong():
    text = _spec_text_lf()
    missing = [s for s in REQUIRED_SUBSECTIONS if s not in text]
    assert not missing, f"spec MẤT mục con bắt buộc: {missing}"


def test_du_tu_khoa_cot_tu():
    """Mỗi từ khoá đại diện một luật đã khóa — mất từ khoá là mất luật."""
    text = _spec_text_lf()
    missing = {k: why for k, why in REQUIRED_KEYWORDS.items() if k not in text}
    assert not missing, "spec MẤT từ khoá cốt tử (mất luật tương ứng): " + "; ".join(
        f"{k} ({why})" for k, why in missing.items()
    )


def test_khong_co_dau_hieu_spec_bi_cat_cut():
    """Heuristic: file phải kết thúc bằng mục 15 và không có mục nào rỗng ruột."""
    text = _spec_text_lf()
    assert "## 15." in text, "mất mục 15 (pre-flight findings)"
    # mỗi mục cấp 1 phải có ít nhất 1 dòng nội dung sau header
    blocks = re.split(r"^## \d+\.", text, flags=re.M)[1:]
    thin = [i for i, b in enumerate(blocks) if len(b.strip()) < 40]
    assert not thin, f"mục cấp 1 số {thin} gần như RỖNG — nghi bị xoá nội dung"


# ── ERRATUM: phải tồn tại, VÀ tham số phải nguyên vẹn ──────────────────
# Tham số chiến lược đã khóa. Erratum CHỈ được sửa số mô tả — nếu ai đó sửa
# những chuỗi này dưới danh nghĩa "erratum", test phải đỏ.
PARAMS_FROZEN = [
    "RS ≥ 90",
    "BS ≥ 0.30",
    "Eff ≥ 0.60",
    "Moat ≥ 0.50",
    "ADV20 ≥ 500.000",
    "Health ≠ DISTRESSED",
    "Volume ≥ 1.3 × ADV20",
    "→ CRISIS",  # exit khi Regime → CRISIS
    "Sharpe OOS",
]


def _has_param(text: str, param: str) -> bool:
    """Khớp bất chấp khoảng trắng (spec có chỗ dùng 2 space cho căn cột)."""
    pat = re.escape(param).replace(r"\ ", r"\s+").replace(" ", r"\s+")
    return re.search(pat, text) is not None


def test_erratum_1_ton_tai():
    text = _spec_text_lf()
    assert "## ERRATUM 1" in text, "spec phải có ERRATUM 1 (đính chính universe ~740 → ~575)"
    assert "~575" in text, "erratum phải nêu số ĐO ĐƯỢC (~575)"
    assert "740" in text, "erratum KHÔNG được xoá dấu vết con số sai (không viết lại lịch sử)"


def test_erratum_khong_duoc_doi_tham_so():
    """Một erratum chỉ sửa SỐ MÔ TẢ. Tham số chiến lược phải còn nguyên từng chữ."""
    text = _spec_text_lf()
    broken = [p for p in PARAMS_FROZEN if not _has_param(text, p)]
    assert not broken, f"THAM SỐ đã bị đổi/biến mất (erratum không được phép): {broken}"


def test_erratum_khong_co_van_ban_goc_bi_viet_lai():
    """§3.1 gốc phải còn nguyên văn '903', '737' và câu khẳng định ~740."""
    text = _spec_text_lf()
    assert "903" in text and "737" in text, "số liệu gốc của §3.1 đã bị sửa/xoá"
    assert re.search(r"kỳ vọng:\s*⚠️?\s*\*?\*?~?740", text) or "~740" in text, "câu gốc về ~740 đã biến mất"
