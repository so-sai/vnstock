"""[WHY] Test cho backfill chuỗi macro lịch sử sâu (`XAGUSD` 10 năm, NQ-10 nhiệm vụ 1).

[WHY] Lý do tồn tại của module: chuỗi `XAGUSD` trong `macro_history` chỉ có 318
quan sát không-null (từ 2025-06-10) trong khi `GOLD_XAU` có 1.407. Nghị quyết
10/NQ-HĐKT yêu cầu kéo đủ lịch sử TRƯỚC khi dựng bất kỳ tham số GSR/beta nào.
Nhưng "kéo được nhiều dòng" ≠ "kéo được chuỗi hợp lệ": yfinance có thể trả 0.0
làm sentinel, trả bar trùng, hoặc trả một chuỗi mà giá trị các ngày cũ KHÔNG
khớp với giá trị đã lưu (đổi cách điều chỉnh hợp đồng tương lai). Nếu không
chặn các ca đó thì lần "làm dày dữ liệu" sẽ âm thầm viết lại lịch sử.

[ROOT CAUSE] `cmd_backfill_macro` cũ chỉ có `--days` cho toàn bộ 24 ticker:
(a) không chọn được một ticker (kéo 24 chuỗi để lấy 1); (b) không có cổng chất
lượng nào giữa download và `save_data_upsert`; (c) `period="252d"` mặc định
không đủ sâu để kiểm định thống kê, mà cũng không ai biết là không đủ vì không
có ngưỡng hàng tối thiểu.

[INVARIANT]
(1) Không ghi gì khi chuỗi vi phạm: value = 0.0 (sentinel "missing"), NaN, ngày
    trùng mà giá trị khác nhau, hoặc số dòng < ngưỡng khai báo.
(2) KHÔNG chặn value âm toàn cục: WTI 2020-04-20 đóng cửa −37,63 USD là dữ
    liệu THẬT; chặn âm sẽ là "vá quá tay" (xem test_chặn_0_nhưng_cho_phép_âm).
(3) Vùng chồng lấn với dữ liệu đã có phải khớp trong sai số khai báo; lệch quá
    ngưỡng ⇒ DỪNG, không ghi đè (đây là bẫy "scale-switch" của E19 áp dụng
    trước, thay vì phát hiện sau).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = None
for _par in [Path(__file__).resolve().parent.parent.parent] + list(
    Path(__file__).resolve().parent.parent.parent.parents
):
    if (_par / "AGENTS.md").exists() and (_par / "backend").is_dir():
        PROJECT_ROOT = _par
        break
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from src.engine.macro_backfill import (  # noqa: E402
    TICKERS,
    MacroSeriesInvalid,
    check_overlap,
    melt_close_frame,
    quarantine_series,
    resolve_tickers,
    validate_series,
)


def _close_frame() -> pd.DataFrame:
    """Frame giả lập output `yf.download([...], ...)["Close"]` (cột = ticker).

    [WHY] Dùng frame tổng hợp thay vì mạng: test phải chạy được offline và phải
    tái lập được đúng ca lỗi, không phụ thuộc dữ liệu vendor hôm nay.
    """
    idx = pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"])
    return pd.DataFrame(
        {"SI=F": [24.10, 23.95, 24.33], "GC=F": [2073.5, 2065.2, 2049.8]},
        index=idx,
    )


def test_resolve_tickers_mac_dinh_va_chon_subset():
    """[WHY] Kéo full history 24 ticker để lấy 1 chuỗi là lãng phí và tăng rủi
    ro rate-limit; `--variables` phải chọn đúng tập con nhưng KHÔNG được lặng
    lẽ bỏ qua tên sai (bỏ qua = kéo thiếu mà tưởng thành công)."""
    assert resolve_tickers(None) == dict(TICKERS)
    assert resolve_tickers("") == dict(TICKERS)
    assert set(resolve_tickers("XAGUSD")) == {"XAGUSD"}
    assert resolve_tickers(" XAGUSD , GOLD_XAU ") == {
        "XAGUSD": TICKERS["XAGUSD"],
        "GOLD_XAU": TICKERS["GOLD_XAU"],
    }


def test_resolve_tickers_ten_sai_thi_dung_han():
    """[WHY] Tên biến sai phải FAIL, và thông điệp phải nêu tên sai để người
    vận hành sửa ngay — im lặng bỏ qua sẽ tạo cảm giác "đã kéo xong"."""
    with pytest.raises(MacroSeriesInvalid) as exc:
        resolve_tickers("XAGUSD,SILVER,AG")
    msg = str(exc.value)
    assert "SILVER" in msg and "AG" in msg
    # [WHY] Thông điệp phải gợi ý danh sách hợp lệ để sửa ngay tại chỗ, không
    # phải mở file cấu hình đọc lại 24 tên.
    assert "Hợp lệ" in msg and "XAGUSD" in msg


def test_melt_doi_ten_cot_theo_bang_ma_va_bo_nan():
    """[WHY] `melt` phải map ticker → tên biến nội bộ (SI=F → XAGUSD) và bỏ NaN
    (ngày nghỉ lệch lịch giữa các thị trường). Giữ NaN sẽ thành 0 khi lưu kiểu
    float ở nơi khác — đúng lỗi Missing→Fake cần chặn."""
    df = melt_close_frame(_close_frame(), {v: k for k, v in TICKERS.items()})
    assert list(df.columns) == ["date", "variable", "value"]
    assert len(df) == 6
    assert set(df["variable"]) == {"XAGUSD", "GOLD_XAU"}
    assert df.loc[df["variable"] == "XAGUSD", "value"].tolist() == [24.10, 23.95, 24.33]
    assert df["date"].tolist()[:3] == ["2024-01-02", "2024-01-03", "2024-01-04"]


def test_melt_nan_bi_loai_chu_khong_thanh_0():
    """[WHY] Ca cụ thể: chỉ một ticker thiếu ngày. Nếu NaN bị đổi thành 0 (hoặc
    giữ nguyên rồi round), chuỗi có "giá bạc = 0 USD/oz" — đúng loại bar giả đã
    gặp ở PAT. Đo: 2 dòng còn lại, không có giá trị 0."""
    frame = _close_frame()
    frame.iloc[1, 0] = float("nan")
    df = melt_close_frame(frame, {v: k for k, v in TICKERS.items()})
    assert len(df) == 5
    assert (df["value"] == 0).sum() == 0


def test_validate_tra_ve_thong_ke_do_duoc():
    """[WHY] Cổng chất lượng phải trả về số đo (n, ngày đầu/cuối) chứ không chỉ
    "OK", để lần chạy sau so được trước/sau và để báo cáo không phải tự đếm lại."""
    df = melt_close_frame(_close_frame(), {v: k for k, v in TICKERS.items()})
    stats = validate_series(df)
    assert stats["XAGUSD"]["n"] == 3
    assert stats["XAGUSD"]["first"] == "2024-01-02"
    assert stats["XAGUSD"]["last"] == "2024-01-04"


def test_chan_value_0_nhu_sentinel_missing():
    """[WHY] 0.0 trong chuỗi giá là sentinel "không có dữ liệu" (đo thật: bar
    PAT 2026-06 o=h=l=c=0). Lưu nó vào = biến "không biết" thành "biết giá 0"."""
    df = melt_close_frame(_close_frame(), {v: k for k, v in TICKERS.items()})
    df.loc[0, "value"] = 0.0
    with pytest.raises(MacroSeriesInvalid) as exc:
        validate_series(df)
    assert "0" in str(exc.value)


def test_chan_0_nhung_CHO_PHEP_am():
    """[WHY] Chống vá quá tay: WTI 2020-04-20 đóng cửa −37,63 USD là dữ liệu
    thật của thị trường (hợp đồng tháng 5 âm trước khi đáo hạn). Một cổng chặn
    "value <= 0" sẽ xoá đúng phiên lịch sử quan trọng nhất của dầu."""
    df = melt_close_frame(_close_frame(), {v: k for k, v in TICKERS.items()})
    df.loc[0, "value"] = -37.63
    stats = validate_series(df)
    assert stats["XAGUSD"]["n"] == 3


def test_chan_ngay_trung_gia_tri_khac_nhau():
    """[WHY] Hai giá trị cho cùng một (variable, date) là nguồn không nhất quán;
    chọn bừa một cái là ghi lịch sử theo may rủi. Ghi đè im lặng sẽ khiến lần
    chạy sau ra số khác lần trước mà không ai biết."""
    df = melt_close_frame(_close_frame(), {v: k for k, v in TICKERS.items()})
    dup = pd.concat([df, df.iloc[[0]].assign(value=99.0)], ignore_index=True)
    with pytest.raises(MacroSeriesInvalid) as exc:
        validate_series(dup)
    assert "trùng" in str(exc.value)


def test_nguong_so_dong_toi_thieu_chang_duoc_ghi():
    """[WHY] Đây là chốt chặn trung tâm của NQ-10: 318 quan sát là "mẫu cụt".
    Ngưỡng phải chặn ở bước GHI, không phải ở bước đọc báo cáo — nếu không thì
    lần sau ai đó lại kéo 300 dòng rồi dựng tham số trên nó."""
    df = melt_close_frame(_close_frame(), {v: k for k, v in TICKERS.items()})
    with pytest.raises(MacroSeriesInvalid) as exc:
        validate_series(df, min_rows=1000)
    assert "1000" in str(exc.value)


def test_check_overlap_phat_hien_doi_thang_gia():
    """[WHY] Đúng bẫy E19 nhưng áp dụng TRƯỚC khi ghi: nếu bản tải mới trả giá
    đã điều chỉnh khác quy ước cũ (futures roll/scale), chênh lệch sẽ hiện ở
    vùng chồng lấn. Đo được chênh 4% phải DỪNG; chênh 0,01% (làm tròn 6 dp) thì
    cho qua."""
    new = {"2026-10-28": 54.08, "2026-10-29": 52.94}
    ok = check_overlap(new, {"2026-10-28": 54.080001, "2026-10-29": 52.94}, tol_pct=0.5)
    assert ok["max_abs_pct"] < 0.5 and ok["n"] == 2

    bad = check_overlap(new, {"2026-10-28": 54.08 * 1.04, "2026-10-29": 52.94}, tol_pct=0.5)
    # [WHY] Chênh thật ở đây là 1 − 1/1,04 = 3,846% (không phải 4%): mẫu số là
    # giá TRỊ CŨ. Ghi đúng con số đo được thay vì con số "tròn" dễ đọc.
    assert bad["max_abs_pct"] == pytest.approx(3.8462, abs=1e-3)
    assert bad["worst_date"] == "2026-10-28"


def test_check_overlap_khong_co_vung_chong_lan():
    """[WHY] Backfill lùi sâu về quá khứ thường KHÔNG có vùng chồng lấn (dữ liệu
    mới cũ hơn dữ liệu cũ). Đó không phải lỗi — phải trả n=0 rõ ràng, không raise
    và cũng không được bịa ra "khớp hoàn hảo"."""
    out = check_overlap({"2020-01-02": 18.0}, {"2026-10-29": 52.94}, tol_pct=0.5)
    assert out["n"] == 0
    assert out["max_abs_pct"] == 0.0


@pytest.fixture()
def mem_db():
    """DB in-memory có đúng schema cột mà `quarantine_series` đọc."""
    import sqlite3

    con = sqlite3.connect(":memory:")
    con.execute(
        "CREATE TABLE macro_history(date TEXT, variable TEXT, value REAL, "
        "is_stale INTEGER DEFAULT 0, source TEXT)"
    )
    con.executemany(
        "INSERT INTO macro_history(date, variable, value, is_stale, source) VALUES (?,?,?,?,?)",
        [
            ("2026-10-28", "XAGUSD", 54.08, 0, None),
            ("2026-10-28", "XAGUSD", 54.081, 0, None),  # bản trùng (đo thật: 42 dòng)
            ("2026-10-29", "XAGUSD", 52.94, 0, None),
            ("2026-10-04", "XAGUSD", 60.42, 0, None),  # Chủ nhật — không thể có với futures
            ("2026-10-29", "GOLD_XAU", 4040.0, 0, "yahoo"),
        ],
    )
    yield con
    con.close()


def test_cach_ly_chuyen_toan_bo_chuoi_va_giu_nguyen_trang(mem_db):
    """[WHY] Cách ly phải là chép-nguyên-trạng rồi mới xoá: nếu xoá trước, một lỗi
    giữa chừng là mất dữ liệu vĩnh viễn. Đo: 4 dòng XAGUSD chuyển sang bảng cách
    ly (kể cả dòng trùng và dòng Chủ nhật), GOLD_XAU không bị đụng."""
    out = quarantine_series(mem_db, "XAGUSD", reason="test", stamp="2026-10-30 10:00:00")
    assert out["moved"] == 4
    assert out["distinct_dates"] == 3
    assert out["first"] == "2026-10-04" and out["last"] == "2026-10-29"
    kept = mem_db.execute(
        "SELECT variable, COUNT(*) FROM macro_history GROUP BY variable"
    ).fetchall()
    assert kept == [("GOLD_XAU", 1)]
    q = mem_db.execute(
        "SELECT COUNT(*), COUNT(DISTINCT reason), MIN(quarantined_at) FROM macro_history_quarantine"
    ).fetchone()
    assert q == (4, 1, "2026-10-30 10:00:00")


def test_cach_ly_bat_buoc_co_ly_do(mem_db):
    """[WHY] Không có lý do thì sáu tháng sau không ai biết vì sao chuỗi bị thay —
    audit trail rỗng đúng là thứ khiến dữ liệu bị "dọn" oan."""
    with pytest.raises(MacroSeriesInvalid):
        quarantine_series(mem_db, "XAGUSD", reason="   ", stamp="2026-10-30 10:00:00")


def test_cach_ly_chuoi_khong_ton_tai_thi_fail(mem_db):
    """[WHY] Báo "đã cách ly 0 dòng" cho một biến gõ sai tên sẽ khiến người vận
    hành tưởng đã làm sạch. Phải raise."""
    with pytest.raises(MacroSeriesInvalid):
        quarantine_series(mem_db, "XAGUS", reason="typo", stamp="2026-10-30 10:00:00")
