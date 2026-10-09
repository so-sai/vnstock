"""test_pandas3_loc_dtype.py — chặn tái phát lớp lỗi pandas 3.

Bối cảnh (root cause phiên 08/10):
    rs_ranker FAIL với `TypeError: Invalid value '<DatetimeArray>...' for dtype 'str'`
    Nguồn: gán `pd.to_datetime(...)` vào cột `date` BẰNG `.loc[:, ...]` — pandas 3 (3.0.2) không
    còn cho `.loc[:, col]` ĐỔI DTYPE của một cột đã tồn tại: `column_setitem` cố giữ
    dtype 'str' rồi raise. Cột `date` từ SQLite là str → mọi site kiểu này vỡ.

    Nguy hiểm vì nó nằm sâu trong `data_quality.danh_gia_chat_luong_du_lieu()`,
    được gọi TRƯỚC logic của rs_ranker → engine báo lỗi ở chỗ không ai ngờ.

Cách đúng: gán trực tiếp `df["date"] = pd.to_datetime(...)` (luôn THAY cột).
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC = PROJECT_ROOT / "backend" / "src"

# `.loc[:, "date"] = ...` hoặc `.loc[:, 'date'] = ...`
UNSAFE = re.compile(r"""\.loc\[\s*:\s*,\s*["']date["']\s*\]\s*=""")


def test_pandas3_loc_setitem_doi_dtype_thi_raise():
    """Đặc tả hành vi pandas 3 để không ai vô tình dùng lại pattern này."""
    df = pd.DataFrame({"date": ["2026-10-08", "2026-10-07"]})
    assert df["date"].dtype == object or str(df["date"].dtype) == "str"

    with pytest.raises(TypeError, match="for dtype"):
        df.loc[:, "date"] = pd.to_datetime(df["date"], format="mixed")  # pandas3-ok: ca khẳng định pandas 3 raise


def test_gan_truc_tiep_thi_thanh_cong():
    df = pd.DataFrame({"date": ["2026-10-08", "2026-10-07"]})
    df["date"] = pd.to_datetime(df["date"], format="mixed")
    assert str(df["date"].dtype).startswith("datetime64")


def test_khong_con_site_loc_date_datetime_trong_src():
    """Guard: cấm tái xuất hiện việc gán đổi dtype cột `date` qua `.loc[:, ...]` trong backend/src.

    (Đã sửa 4 site: data_quality.py, rs_ranker.py, index_reality_unifier.py x2.)
    """
    offenders: list[str] = []
    for path in SRC.rglob("*.py"):
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]  # bỏ comment (tài liệu được phép nhắc pattern)
            if UNSAFE.search(code):
                offenders.append(f"{path.relative_to(PROJECT_ROOT)}:{i}")
    assert not offenders, (
        "pandas 3 sẽ raise TypeError khi .loc[:, 'date'] đổi dtype cột str → "
        f"dùng df['date'] = pd.to_datetime(...). Site vi phạm: {offenders}"
    )
