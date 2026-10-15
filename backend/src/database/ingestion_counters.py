"""Bộ đếm PHÂN LOẠI lý do loại/thêm dòng theo PHIÊN (log-only, không đổi ngữ nghĩa dữ liệu).

WHY: độ phủ (coverage) mất dòng theo ÍT NHẤT 5 cơ chế khác hẳn nhau, nhưng trước đây
tất cả đều gộp thành một con số "mã thiếu" mờ nghĩa:
  - nguồn KHÔNG có bar phiên đó (mã mỏng/treo)      → G1_DATE_MATCH_FAIL
  - dòng có nhưng bị CỔNG BIÊN ĐỘ chặn (đúng chức năng) → G2_BAND_REJECT / G2_NO_REFERENCE
  - lệch thang giá được tự sửa                       → INGESTION_SCALE_TRANSFORM
  - lệch thang KHÔNG khớp lịch sử mã nên bị loại     → INGESTION_SCALE_REJECT
  - lỗi dữ liệu tất định của nguồn                   → FETCH_ERR_DATA
  - mã treo/hủy bị bỏ qua TRƯỚC khi gọi API          → DORMANT_SKIPPED
Gộp chúng lại thì không thể kiểm toán: 81 mã "thiếu" của phiên 12/10 vừa rồi là mã mỏng
(nguồn thật sự không có bar), KHÁC hẳn NNT/PAP bị cổng biên độ chặn — hai nguyên nhân,
hai cách xử lý, một con số.

HỢP ĐỒNG: chỉ đếm, không quyết định. Không được dùng counter làm cổng (fail-closed vẫn do
các guard hiện có đảm nhiệm). Bộ đếm sống trong 1 tiến trình: script sweep reset() đầu lượt,
in bảng cuối lượt, và APPEND một dòng JSON vào `<run_dir>/ingestion_breakdown_<date>.jsonl`
để cộng dồn qua nhiều lát (mỗi lát là một tiến trình riêng).
"""

from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path

MAX_SAMPLES = 8

_categories: Counter = Counter()
_samples: dict[str, list[str]] = {}

# Nhãn hiển thị theo thứ tự cố định — thứ tự này là hợp đồng báo cáo.
ORDER = (
    "DORMANT_SKIPPED",
    "G1_DATE_MATCH_FAIL",
    "G2_BAND_REJECT",
    "G2_NO_REFERENCE",
    "INGESTION_SCALE_TRANSFORM",
    "INGESTION_SCALE_REJECT",
    "FETCH_ERR_DATA",
    "FETCH_ERR_OTHER",
)


def reset() -> None:
    _categories.clear()
    _samples.clear()


def bump(category: str, symbol: str | None = None, detail: str | None = None, n: int = 1) -> None:
    """+n cho `category`, lưu tối đa MAX_SAMPLES ví dụ (mã + chi tiết) để truy vết."""
    _categories[category] += n
    if symbol or detail:
        bucket = _samples.setdefault(category, [])
        if len(bucket) < MAX_SAMPLES:
            label = symbol if symbol else ""
            bucket.append(f"{label}" + (f" ({detail})" if detail else "") if label else str(detail))


def snapshot() -> dict:
    return {
        "categories": {k: int(v) for k, v in _categories.items()},
        "samples": {k: list(v) for k, v in _samples.items()},
    }


def report(date: str | None = None) -> str:
    """Bảng phân loại 1 khối, in cuối mỗi lượt sweep."""
    lines = [f"INGESTION_BREAKDOWN date={date or '-'}"]
    for cat in ORDER:
        n = int(_categories.get(cat, 0))
        if n == 0:
            continue
        ex = ", ".join(_samples.get(cat, [])[:5])
        lines.append(f"  {cat:<26} {n:>6}  {ex}")
    extra = {k: v for k, v in _categories.items() if k not in ORDER}
    for cat, n in sorted(extra.items()):
        lines.append(f"  {cat:<26} {int(n):>6}")
    if len(lines) == 1:
        lines.append("  (không có mã nào bị loại/theo dõi)")
    return "\n".join(lines)


def append_jsonl(date: str) -> Path | None:
    """Ghi 1 dòng JSON để cộng dồn qua các lát (mỗi lát = 1 tiến trình)."""
    run_dir = Path(os.environ.get("PTCK_RUN_DIR", str(Path(os.environ.get("TEMP", ".")) / "ptck_run")))
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
        p = run_dir / f"ingestion_breakdown_{date}.jsonl"
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps({"date": date, "pid": os.getpid(), **snapshot()}, ensure_ascii=False) + "\n")
        return p
    except OSError:  # ghi log phụ trợ không được làm hỏng lượt nạp
        return None


def aggregate_jsonl(date: str) -> dict:
    """Cộng dồn mọi lát của một phiên (dùng cho kiểm toán)."""
    run_dir = Path(os.environ.get("PTCK_RUN_DIR", str(Path(os.environ.get("TEMP", ".")) / "ptck_run")))
    p = run_dir / f"ingestion_breakdown_{date}.jsonl"
    total: Counter = Counter()
    slices = 0
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            slices += 1
            total.update({k: int(v) for k, v in d.get("categories", {}).items()})
    return {"date": date, "slices": slices, "categories": dict(total)}
