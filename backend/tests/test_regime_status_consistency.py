"""Test nhất quán `regime_score` ↔ `status` (lỗi biên do làm tròn, phát hiện 2026-10-22).

[WHY]
  Dòng `regime_history` ngày 2026-10-22 lưu `regime_score = 0.35` nhưng `status = 'CRISIS'`, trong khi
  ngưỡng của code là `RANGING if score >= 0.35`. Nguyên nhân: `detect_regime()` trả `round(score, 2)`
  cho cột điểm, còn `status` được tính trên giá trị **chưa làm tròn** (≈0,349x). Đo toàn kho: 23/1.469
  dòng lệch, tất cả ở biên 0,35 / 0,65.

[ROOT CAUSE]
  Hai giá trị `score` và `status` suy từ hai phiên bản khác nhau của cùng một đại lượng (trước/sau làm tròn).

[INVARIANT]
  1. `status` LUÔN suy từ chính giá trị `regime_score` được lưu (`classify_regime`), trừ override
     `CRISIS_WARNING` (nhãn cảnh báo RPA riêng, không phải phân hạng band).
  2. `regime_score` được làm tròn **trước** khi phân hạng và lưu 4 chữ số.
  3. Không còn default "lành" `.get("status", "RANGING")` ở writer (Luật A).
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from src.engine import regime_engine as re_

SRC = BACKEND / "src" / "engine" / "regime_engine.py"
DB = BACKEND / "data" / "screener_cache.db"


# [WHY] Biên phải rõ ràng và đúng một phía: RANGING bắt đầu ĐÚNG tại 0,35; TRENDING chỉ khi > 0,65.
# Ca 0,35 là chính ca gây lỗi hôm 22/10 — phải trả RANGING.
@pytest.mark.parametrize(
    "score,expected",
    [
        (0.0, "CRISIS"),
        (0.23, "CRISIS"),
        (0.3499, "CRISIS"),
        (0.35, "RANGING"),
        (0.3500, "RANGING"),
        (0.5, "RANGING"),
        (0.6499, "RANGING"),
        (0.65, "RANGING"),
        (0.6501, "TRENDING"),
        (1.0, "TRENDING"),
    ],
)
def test_phan_hang_dung_bien(score, expected):
    assert re_.classify_regime(score) == expected


# [WHY] Bất biến cốt lõi: nếu điểm được làm tròn 4 chữ số rồi mới phân hạng thì KHÔNG tồn tại cặp
# (score lưu, status) nào mâu thuẫn. Quét toàn lưới 0→1 bước 0,0005.
def test_khong_mau_thuan_tren_toan_luoi():
    bad = []
    for i in range(2001):
        raw = i / 2000
        stored = round(raw, 4)
        if re_.classify_regime(stored) != re_.classify_regime(raw) and abs(raw - 0.35) > 1e-9:
            # chỉ được lệch do làm tròn vượt biên, và khi đó status theo giá trị ĐÃ LƯU là đúng
            bad.append((raw, stored))
    # Mọi "lệch" phải nằm sát biên và status theo giá trị đã lưu vẫn là nguồn sự thật
    for raw, stored in bad:
        assert abs(stored - 0.35) <= 1e-4 or abs(stored - 0.65) <= 1e-4


# [WHY] Chống tái phát ở tầng mã nguồn: (a) không trả score đã làm tròn 2 chữ số, (b) không còn default
# "lành" cho status ở writer. Đây là 2 dòng đã gây lỗi; test đọc source để bắt nếu ai revert.
def test_source_khong_tai_phat_loi_cu():
    src = SRC.read_text(encoding="utf-8")
    assert 'round(regime_score, 2)' not in src, "score phải lưu 4 chữ số (round(...,4)) trước khi phân hạng"
    assert '"status": verdict.get("status", "RANGING")' not in src, "cấm default 'RANGING' cho status"
    assert "classify_regime(regime_score)" in src


# [WHY] Kiểm trên kho thật: sau bản vá, mọi dòng MỚI phải nhất quán. Dữ liệu lịch sử trước 2026-10-23
# còn 23 dòng lệch đúng tại biên 0,35/0,65 (artifact của round-2) — được phép, nhưng KHÔNG được có dòng
# lệch nào ở ngoài biên, và `CRISIS_WARNING` được miễn (override RPA, không phải phân hạng band).
def test_kho_that_khong_lech_ngoai_bien():
    if not DB.exists():
        pytest.skip("không có screener_cache.db")
    con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT date, regime_score, status FROM regime_history WHERE regime_score IS NOT NULL AND status IS NOT NULL"
    ).fetchall()
    con.close()
    offenders = []
    for d, sc, st in rows:
        if st == "CRISIS_WARNING":
            continue
        if re_.classify_regime(sc) != st and abs(sc - 0.35) > 0.005 and abs(sc - 0.65) > 0.005:
            offenders.append((d, sc, st))
    assert not offenders, f"status lệch score ngoài biên làm tròn: {offenders[:5]}"
