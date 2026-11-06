# -*- coding: utf-8 -*-
"""Mục 29 (NQ-18 §II.2): LỊCH GIAO DỊCH PHẢI CÓ MỘT NGUỒN SỰ THẬT DUY NHẤT.

[WHY]
    Sự cố 31/08/2026 (`3bebe15`) không phải chuyện "quên một ngày" — nó là hệ quả của việc
    tồn tại **ba** bản lịch chép tay không ai đối chiếu:
    `daily_updater.VN_HOLIDAYS`, `src/main.rs:VN_HOLIDAYS`, `config/weekend_holidays.json`.
    Chúng đã lệch thật: Python thiếu `2026-08-31` (báo động giả mỗi ngày), cả ba cùng thiếu
    14 ngày nghỉ 2023–2026, và Rust + Python cùng THỪA `2024-02-15/16` (hai phiên thật).

[ROOT CAUSE]
    Lịch được bảo trì bằng cách sửa tay từng bản sao, không có (a) nguồn chuẩn duy nhất,
    (b) phép đối chiếu giữa các bản sao, (c) phép đối chiếu giữa lịch và DỮ LIỆU.

[INVARIANT]
    (a) `daily_updater.VN_HOLIDAYS` chỉ được ĐỌC từ `config/weekend_holidays.json` — không
        hard-code lại ngày nào trong file Python;
    (b) file SSOT thiếu/hỏng/rỗng ⇒ `RuntimeError` (fail-closed), KHÔNG trả set rỗng;
    (c) lịch Rust được đối chiếu tự động; mọi lệch MỚI làm test đỏ (hiện có 15 ngày lệch đã
        đo, ghim tên — không được nới);
    (d) lịch phải khớp DỮ LIỆU: ngày có >=100 bar phải là phiên; ngày không phải phiên không
        được có bar. Đây là lưới bắt được cả "thiếu ngày nghỉ" lẫn "thừa ngày nghỉ".
"""

import json
import re
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

PROJECT_ROOT = None
for _par in [Path(__file__).resolve().parent.parent.parent] + list(
    Path(__file__).resolve().parent.parent.parent.parents
):
    if (_par / "AGENTS.md").exists() and (_par / "backend").is_dir():
        PROJECT_ROOT = _par
        break
BACKEND_DIR = PROJECT_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

SSOT = BACKEND_DIR / "src" / "config" / "weekend_holidays.json"
RUST_MAIN = PROJECT_ROOT / "src" / "main.rs"
LIVE_DB = BACKEND_DIR / "data" / "screener_cache.db"

# [WHY] GHIM số đo 06/11/2026. Hai chiều lệch với Rust đều đã truy nguyên:
#   * rust_only  : 2024-02-15/16 — Rust THỪA (dữ liệu chứng minh là phiên thật). Không thể
#                  sửa `src/main.rs` (agent Rust đang giữ file) ⇒ ghim để tiến trình đó thấy.
#   * json_only  : 2023 (12 ngày) + 2024-04-29 — Rust THIẾU vì lịch Rust chỉ bắt đầu 2024.
# Bất kỳ lệch MỚI nào (thêm/bớt một ngày ở một bên) đều làm test đỏ.
KNOWN_RUST_ONLY = ["2024-02-15", "2024-02-16"]
KNOWN_JSON_ONLY = [
    "2023-01-02",
    "2023-01-20",
    "2023-01-23",
    "2023-01-24",
    "2023-01-25",
    "2023-01-26",
    "2023-05-01",
    "2023-05-02",
    "2023-05-03",
    "2023-09-01",
    "2023-09-04",
    "2024-04-29",
]

# [WHY] TRƯỚC 06/11/2026: `2026-09-01` có dòng dẫn xuất nhưng 0 bar ⇒ DÒNG MA (Mục 28 #3).
# Nay đã cách ly bằng `ptck.py quarantine-nonsession` (audit RESTATED #3478–3480) ⇒ phải RỖNG.
# Đây là nợ ĐÃ TRẢ, không phải nợ được miễn: một ngày ma MỚI là đỏ ngay.
KNOWN_PHANTOM_ROW_DAYS: list[str] = []

AUDIT_START = "2023-01-01"


def _load_json() -> dict:
    return json.loads(SSOT.read_text(encoding="utf-8"))


def _rust_holidays() -> set[str]:
    src = RUST_MAIN.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"const VN_HOLIDAYS: &\[&str\] = &\[(.*?)\];", src, re.S)
    assert m, "không tìm thấy `const VN_HOLIDAYS` trong src/main.rs — lịch Rust đã đổi cấu trúc"
    return set(re.findall(r'"(20\d\d-\d\d-\d\d)"', m.group(1)))


# ===================================================================
# 1. Nguồn sự thật duy nhất
# ===================================================================
def test_python_reads_calendar_from_ssot_only():
    """[WHY] Chống tái phát gốc rễ: nếu ai đó thêm lại một set ngày lễ vào `daily_updater.py`,
    ta lại có bản sao thứ tư và lịch sẽ trôi lệch tiếp. Test này khoá: Python CHỈ đọc JSON.
    """
    from src.daily_updater import VN_HOLIDAYS

    assert set(VN_HOLIDAYS) == set(_load_json()["holidays"]), (
        "VN_HOLIDAYS phải bằng đúng `holidays` trong SSOT — nếu khác, có bản sao thứ hai"
    )

    src = (BACKEND_DIR / "src" / "daily_updater.py").read_text(encoding="utf-8")
    assert "VN_HOLIDAYS = {" not in src, "cấm hard-code lại set ngày lễ trong daily_updater.py"


def test_ssot_contract_no_weekend_entries_and_valid_iso():
    """[WHY] File tự cam kết "chỉ ngày lễ ĐẶC BIỆT (weekend tự check)". Một ngày Thứ Bảy lọt
    vào đây không gây sai số ngay, nhưng làm người đọc tưởng đã kiểm tra — và che mất ngày
    lễ thật nếu ai đó 'dọn' nhầm.
    """
    days = _load_json()["holidays"]
    assert days == sorted(days), "danh sách phải sắp xếp để diff trong git có nghĩa"
    assert len(days) == len(set(days)), "không được trùng ngày"
    for d in days:
        parsed = date.fromisoformat(d)
        assert parsed.isoformat() == d, f"{d} không phải ISO chuẩn"
        assert parsed.weekday() < 5, f"{d} là cuối tuần — không được liệt kê trong file này"


def test_missing_or_broken_ssot_fails_closed(monkeypatch, tmp_path):
    """[WHY] Đây là bất biến quan trọng nhất của file này: lịch hỏng phải DỪNG, không được
    "chạy tiếp với set rỗng". Set rỗng biến mọi ngày lễ thành phiên ⇒ báo động giả mỗi ngày
    và `daily-update` đi cào phiên không tồn tại (AGENTS §4A: không biết ≠ 0).
    """
    import src.daily_updater as du

    def _with(path):
        monkeypatch.setattr(du, "_CALENDAR_PATH_CANDIDATES", (path,), raising=True)
        return du._load_vn_holidays()

    with pytest.raises(RuntimeError, match="Không tìm thấy"):
        _with(tmp_path / "khong_ton_tai.json")

    broken = tmp_path / "broken.json"
    broken.write_text("{ khong phai json", encoding="utf-8")
    with pytest.raises(RuntimeError, match="hỏng"):
        _with(broken)

    empty = tmp_path / "empty.json"
    empty.write_text('{"holidays": []}', encoding="utf-8")
    with pytest.raises(RuntimeError, match="rỗng"):
        _with(empty)

    bad_day = tmp_path / "bad.json"
    bad_day.write_text('{"holidays": ["2026-13-45"]}', encoding="utf-8")
    with pytest.raises(RuntimeError, match="sai định dạng"):
        _with(bad_day)


# ===================================================================
# 2. Đối chiếu Python ↔ Rust
# ===================================================================
def test_rust_calendar_diff_is_exactly_the_known_set():
    """[WHY] Đây là lưới chống trôi lệch ĐƯỢC YÊU CẦU ở Mục 29. Nó không thể "sửa" Rust (file
    đang bị agent khác giữ), nhưng nó bảo đảm: lệch hiện tại là ĐÚNG 15 ngày đã đo, và bất kỳ
    lệch mới nào — thêm/bớt một ngày ở một bên — làm test đỏ ngay.

    [WHY] So trên ngày TRONG TUẦN: Rust có `2026-02-15` (Chủ Nhật) — vô nghĩa về giao dịch,
    nên không tính là lệch (nếu tính, ta buộc phải sửa Rust mới xanh được, mà không được sửa).
    """
    ssot = {d for d in _load_json()["holidays"] if date.fromisoformat(d).weekday() < 5}
    rust = {d for d in _rust_holidays() if date.fromisoformat(d).weekday() < 5}

    rust_only = sorted(rust - ssot)
    ssot_only = sorted(ssot - rust)
    assert rust_only == KNOWN_RUST_ONLY, (
        f"lịch Rust có ngày lễ MỚI mà SSOT không có: {rust_only} — phải đo dữ liệu rồi cập nhật SSOT"
    )
    assert ssot_only == KNOWN_JSON_ONLY, (
        f"SSOT có ngày nghỉ mà Rust KHÔNG có: {ssot_only} — Rust sẽ coi đó là phiên (nguy hiểm)"
    )


# ===================================================================
# 3. Đối chiếu Lịch ↔ Dữ liệu thật (hai chiều)
# ===================================================================
def test_calendar_agrees_with_live_data_both_directions():
    """[WHY] Đây là lưới mạnh nhất và là thứ đã bắt được CẢ HAI lỗi lịch (31/08 thiếu,
    15–16/02 thừa). Nó gồm ba phép đo tách bạch (AGENTS §2):
        A. phiên mà cả 3 bảng đều trống  ⇒ lịch THIẾU ngày nghỉ (hoặc nguồn chết — phải xem);
        B. ngày không phải phiên mà có bar ⇒ lịch THỪA ngày nghỉ (đè mất phiên thật);
        C. ngày không phải phiên mà có dòng dẫn xuất nhưng 0 bar ⇒ DÒNG MA (nợ dữ liệu,
           không phải lỗi lịch) — ghim riêng để không lẫn với A/B.
    """
    from src.database.session_continuity import is_session_day

    con = sqlite3.connect(f"file:{LIVE_DB.as_posix()}?mode=ro", uri=True)
    try:
        ohlcv = {
            str(r[0]): int(r[1])
            for r in con.execute(
                "SELECT date, COUNT(*) FROM daily_ohlcv WHERE COALESCE(is_stale,0)=0 "
                "GROUP BY date"
            )
        }
        breadth = {str(r[0]) for r in con.execute("SELECT DISTINCT date FROM market_wide_breadth_daily")}
        regime = {str(r[0]) for r in con.execute("SELECT DISTINCT date FROM regime_history")}
    finally:
        con.close()

    end = date.fromisoformat(max(ohlcv))
    cur = date.fromisoformat(AUDIT_START)
    missing_holiday, extra_holiday, phantom = [], [], []
    while cur <= end:
        iso = cur.isoformat()
        bars = ohlcv.get(iso, 0)
        if is_session_day(iso):
            if bars == 0 and iso not in breadth and iso not in regime:
                missing_holiday.append(iso)
        else:
            if bars >= 100:
                extra_holiday.append(iso)
            elif iso in breadth or iso in regime:
                phantom.append(iso)
        cur += timedelta(days=1)

    assert missing_holiday == [], (
        f"lịch coi là phiên nhưng cả 3 bảng trống (thiếu ngày nghỉ?): {missing_holiday}"
    )
    assert extra_holiday == [], (
        f"lịch coi là nghỉ nhưng có >=100 bar (thừa ngày nghỉ?): {extra_holiday}"
    )
    assert phantom == KNOWN_PHANTOM_ROW_DAYS, (
        f"tập ngày có dòng dẫn xuất nhưng 0 bar đã đổi: {phantom} — xem Mục 28 #3"
    )
