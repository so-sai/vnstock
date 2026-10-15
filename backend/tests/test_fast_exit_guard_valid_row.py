"""Regression test — Ca 6: `fast_exit_guard` phải đọc qua lưới vị từ + FAIL-CLOSED khi thiếu dữ liệu.

[WHY] `fast_exit_guard.kiem_tra_phan_phoi()` là lưới BẢO VỆ sau khi đã vào lệnh FULL: nó ra
lệnh "GIAM RUI RO" (hạ tỷ trọng ngay) khi thấy volume spike + giá xấu ở 3 trụ cột
(Ngân hàng / Bất động sản / Tài nguyên Cơ bản). Một lưới an toàn chỉ hữu ích nếu nó đọc
ĐÚNG dữ liệu — và chỉ đáng tin nếu nó nói "tôi không biết" khi thiếu dữ liệu.

[ROOT CAUSE] Hai lỗi độc lập trong CÙNG hàm `_get_tru_stats()`:
  1. TRUY VẤN KHÔNG CÓ VỊ TỪ (dòng 57 và 64): `FROM daily_ohlcv WHERE date=? AND symbol IN (...)`
     quét cả dòng đã cách ly/đã chết (`is_stale = 1`) và dòng rác (`close = 0`).
     ⇒ `AVG(close)/AVG(open)/AVG(high)/AVG(low)` của ngành bị bẩn, còn `SUM(volume)` thì bị
       thổi phồng ⇒ ngày lành bị chấm thành "volume spike + GIÁ GIẢM" = PHÂN PHỐI GIẢ
       ⇒ ra lệnh hạ tỷ trọng trên một vị thế đang khoẻ.
     Đo thật trên store (1.578.061 dòng): 41.998 dòng `is_stale = 1`, 38.661 dòng vi phạm vị
     từ hợp lệ VÀ cũng `is_stale = 1` — nhưng 3.337 dòng `is_stale = 1` VẪN HỢP LỆ về hình
     dạng (lệch thang giá), và 1 dòng vi phạm vị từ lại có `is_stale = 0`. Hai vị từ bắt hai
     lớp khác nhau ⇒ thiếu MỘT trong hai là hở lưới.
  2. BỊA SỐ 0 / NHÃN LÀNH KHI THIẾU DỮ LIỆU (dòng 55, 72-79): ngành không có mã, hoặc không
     có bar ⇒ `ratio = 0`, `volume_ma20 = 0`, `range_position = 0.5`, `price_direction =
     "TRUNG_TINH"` ⇒ mất dữ liệu đội lốt "yên bình" ⇒ guard im lặng giữa lúc cần nó nhất.

[INVARIANT] (a) Mọi `AVG`/`SUM` của trụ chỉ tính trên `is_stale = 0 AND {VALID_ROW_SQL}`.
    (b) Không đo được ⇒ `None` + nhãn `KHONG_XAC_DINH` + `du_lieu_thieu = True`; module
    KHÔNG được trả `"KHONG"` (yên bình) khi chưa đo được gì.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager

import src.engine.fast_exit_guard as feg

TRU_A = "Bất động sản"
TRU_B = "Ngân hàng"
TRU_C = "Tài nguyên Cơ bản"

_TARGET = "2026-07-01"
_PRIOR = [f"2026-06-{d:02d}" for d in range(1, 21)]  # 20 phiên nền → đủ điều kiện MA20

_SYMS_A = ["AAA", "BBB", "CCC", "DDD", "EEE"]
_SYMS_B = ["ACB", "TCB", "VCB", "BID", "MBB"]
_SYMS_C = ["HPG", "HSG", "NKG", "VPG", "TLH"]

_TRUS = ((TRU_A, _SYMS_A), (TRU_B, _SYMS_B), (TRU_C, _SYMS_C))


# ── Hạ tầng test ────────────────────────────────────────────────────────────
def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE symbol_industry (symbol TEXT, icb_name2 TEXT)")
    conn.execute(
        "CREATE TABLE daily_ohlcv (symbol TEXT, date TEXT, open REAL, high REAL, low REAL,"
        " close REAL, adj_close REAL, volume REAL, source TEXT, is_stale INTEGER)"
    )
    return conn


def _bar(conn, sym, date, o, h, low, c, vol, stale=0, source="vci"):
    conn.execute(
        "INSERT INTO daily_ohlcv VALUES (?,?,?,?,?,?,?,?,?,?)", (sym, date, o, h, low, c, c, vol, source, stale)
    )


def _industry(conn, industry, symbols):
    for s in symbols:
        conn.execute("INSERT INTO symbol_industry VALUES (?,?)", (s, industry))


@contextmanager
def _bind(monkeypatch, conn):
    @contextmanager
    def _conn():
        yield conn

    monkeypatch.setattr(feg, "get_connection", _conn)
    yield


def _baseline(conn, industry, symbols, vol=200.0, close=105.0):
    """20 phiên nền sạch cho 1 ngành (tổng volume/phiên = len(symbols) * vol)."""
    for d in _PRIOR:
        for s in symbols:
            _bar(conn, s, d, 100.0, 110.0, 95.0, close, vol)


def _valid_today(conn, symbols, o=100.0, h=110.0, low=95.0, c=105.0, vol=200.0):
    """Phiên hôm nay HỢP LỆ và TĂNG (không phân phối)."""
    for s in symbols:
        _bar(conn, s, _TARGET, o, h, low, c, vol)


def _clean_market(conn, trus=_TRUS, vol=200.0):
    """Thị trường SẠCH đủ cả 3 trụ: 20 phiên nền volume đều + hôm nay HỢP LỆ và TĂNG."""
    for industry, symbols in trus:
        _industry(conn, industry, symbols)
        _baseline(conn, industry, symbols, vol=vol)
        _valid_today(conn, symbols, vol=vol)


def _quarantined_bearish_today(conn, symbols, vol=500_000.0):
    """Dòng ĐÃ CÁCH LY (is_stale = 1) phiên hôm nay: giá xấu + volume khổng lồ.

    Hình dạng vẫn "hợp lệ" (close > 0, low <= close <= high) ⇒ chỉ `is_stale = 0` mới chặn được.
    """
    for s in symbols:
        _bar(conn, s, _TARGET, 110.0, 115.0, 90.0, 91.0, vol, stale=1, source="kbs|poisoned:auto_detect")


# ── 1. Dòng cách ly không được kéo lệch số đo của trụ ───────────────────────
def test_quarantined_rows_cannot_fabricate_a_distribution(monkeypatch):
    """[WHY] Lưới bảo vệ đọc dòng cách ly ⇒ ngày lành bị chấm thành "phân phối" ⇒ ra lệnh hạ tỷ
    trọng trên vị thế đang khoẻ.
    ĐIỀU KIỆN CHÍNH: 2 trụ bị bơm dòng cách ly bearish + volume khổng lồ ⇒ KHÔNG được
    chấm thành phân phối (nếu không, hệ thống hạ tỷ trọng một vị thế đang khoẻ)."""
    conn = _db()
    _clean_market(conn)
    _quarantined_bearish_today(conn, _SYMS_A)
    _quarantined_bearish_today(conn, _SYMS_B)

    with _bind(monkeypatch, conn):
        kq = feg.kiem_tra_phan_phoi(target_date=_TARGET)

    assert kq["tru_nguy_hiem"] == [], f"THOÁT LỆNH OAN: trụ bị gắn cờ từ dòng cách ly {kq['tru_nguy_hiem']!r}"
    assert kq["muc_do"] == "KHONG", f"dòng cách ly đã được tính là phân phối: muc_do={kq['muc_do']!r}"
    assert kq["co_phan_phoi"] is False
    assert kq["du_lieu_thieu"] is False


def test_quarantined_row_cannot_pollute_the_aggregate(monkeypatch):
    """[WHY] Đo đúng là điều kiện cần: nếu AVG/SUM còn dính dòng rác thì mọi ngưỡng phía sau vô nghĩa.
    Số đo của trụ phải BẰNG số đo tính trên đúng các bar hợp lệ (không dính dòng cách ly)."""
    conn = _db()
    _clean_market(conn, trus=((TRU_A, _SYMS_A),))
    _quarantined_bearish_today(conn, _SYMS_A)

    with _bind(monkeypatch, conn):
        stats = feg._get_tru_stats(_TARGET, TRU_A)

    assert stats["volume_today"] == 1000.0, f"SUM(volume) đã nuốt dòng cách ly: {stats['volume_today']!r}"
    assert stats["volume_ma20"] == 1000.0
    assert stats["ratio"] == 1.0, f"ratio bị thổi phồng bởi volume rác: {stats['ratio']!r}"
    assert stats["price_direction"] == "TANG", f"hướng giá bị dòng cách ly lật ngược: {stats['price_direction']!r}"


def test_close_zero_row_is_excluded_even_when_not_quarantined(monkeypatch):
    """[WHY] Hai vị từ bắt hai lớp khác nhau — dòng close=0 có thể CHƯA bị cách ly.
    Lớp thứ hai: dòng `close = 0` vẫn `is_stale = 0` (đo thật: đúng 1 dòng như vậy trong store)
    ⇒ chỉ `VALID_ROW_SQL` chặn được."""
    conn = _db()
    _clean_market(conn, trus=((TRU_A, _SYMS_A),))
    _bar(conn, "AAA", _TARGET, 0.0, 0.0, 0.0, 0.0, 250.0, stale=0, source="kbs")  # rác, CHƯA cách ly

    with _bind(monkeypatch, conn):
        stats = feg._get_tru_stats(_TARGET, TRU_A)

    assert stats["volume_today"] == 1000.0, f"dòng close=0 đã vào SUM(volume): {stats['volume_today']!r}"
    assert stats["n_bars"] == 5, f"số bar hợp lệ phải là 5, nhận {stats.get('n_bars')!r}"


# ── 2. Fail-closed: thiếu dữ liệu ⇒ "không xác định", KHÔNG phải "yên bình" ─
def test_no_data_is_not_reported_as_calm(monkeypatch):
    """[WHY] Guard bảo vệ mà nói 'KHONG' khi chưa đo được gì = mất dữ liệu mà vẫn tuyên bố an toàn."""
    conn = _db()
    _industry(conn, TRU_A, _SYMS_A)

    with _bind(monkeypatch, conn):
        kq = feg.kiem_tra_phan_phoi(target_date=_TARGET)

    assert kq["du_lieu_thieu"] is True
    assert kq["muc_do"] == "KHONG_XAC_DINH", f"thiếu dữ liệu nhưng báo {kq['muc_do']!r}"
    assert kq["co_phan_phoi"] is False
    for ten_tru, st in kq["chi_tiet"].items():
        assert st["du_lieu"] == "THIEU", f"{ten_tru}: {st['du_lieu']!r}"
        assert st["ratio"] is None, f"{ten_tru}: ratio phải là None, nhận {st['ratio']!r}"
        assert st["price_direction"] == "KHONG_XAC_DINH", f"{ten_tru}: {st['price_direction']!r}"
        assert st["range_position"] is None


def test_industry_without_symbols_is_declared_missing(monkeypatch):
    """[WHY] Bản cũ trả ratio 0 + TRUNG_TINH cho ngành rỗng ⇒ "yên bình" bịa.
    Ngành không có mã nào ⇒ THIẾU (bản cũ trả ratio 0 + TRUNG_TINH = yên bình giả)."""
    conn = _db()

    with _bind(monkeypatch, conn):
        stats = feg._get_tru_stats(_TARGET, TRU_A)

    assert stats["du_lieu"] == "THIEU"
    assert stats["ratio"] is None
    assert stats["price_direction"] == "KHONG_XAC_DINH"


def test_short_history_ratio_is_none_not_zero(monkeypatch):
    """[WHY] ratio 0 vừa có nghĩa "không spike" vừa có nghĩa "không đo được" — hai thứ phải khác nhau.
    < 5 phiên nền ⇒ chưa tính được MA20: ratio None (KHÔNG phải 0 = 'không có spike')."""
    conn = _db()
    _industry(conn, TRU_A, _SYMS_A)
    for d in _PRIOR[:3]:
        for s in _SYMS_A:
            _bar(conn, s, d, 100.0, 110.0, 95.0, 105.0, 200.0)
    _valid_today(conn, _SYMS_A)

    with _bind(monkeypatch, conn):
        stats = feg._get_tru_stats(_TARGET, TRU_A)

    assert stats["volume_ma20"] is None
    assert stats["ratio"] is None, f"ratio phải None khi thiếu nền, nhận {stats['ratio']!r}"


# ── 3. Chống vá quá tay: phân phối THẬT vẫn phải kích hoạt ─────────────────
def test_genuine_distribution_still_triggers(monkeypatch):
    """[WHY] Vá quá tay cũng là lỗi: chặn oan tín hiệu xả hàng thật nguy hiểm ngang bỏ sót.
    Bar HỢP LỆ + tăng/giảm thật: 2 trụ xả hàng thật ⇒ vẫn phải ra CAO + gắn cờ trụ."""
    conn = _db()
    _clean_market(conn)
    for _industry_name, symbols in ((TRU_A, _SYMS_A), (TRU_B, _SYMS_B)):
        for s in symbols:
            # thay phiên hôm nay: mở cao, đóng sát đáy, volume gấp 5 lần nền
            conn.execute("DELETE FROM daily_ohlcv WHERE symbol=? AND date=?", (s, _TARGET))
            _bar(conn, s, _TARGET, 110.0, 115.0, 90.0, 91.0, 1000.0)

    with _bind(monkeypatch, conn):
        kq = feg.kiem_tra_phan_phoi(target_date=_TARGET)

    assert set(kq["tru_nguy_hiem"]) == {TRU_A, TRU_B}, f"bỏ sót phân phối thật: {kq['tru_nguy_hiem']!r}"
    assert kq["muc_do"] == "CAO", f"phân phối thật bị hạ mức: {kq['muc_do']!r}"
    assert kq["co_phan_phoi"] is True


def test_valid_rising_day_is_not_flagged(monkeypatch):
    """[WHY] CHỐNG VÁ QUÁ TAY: ngày tăng, volume bình thường ⇒ không gắn cờ, không 'không xác định'."""
    conn = _db()
    _clean_market(conn)

    with _bind(monkeypatch, conn):
        kq = feg.kiem_tra_phan_phoi(target_date=_TARGET)

    assert kq["tru_nguy_hiem"] == []
    assert kq["muc_do"] == "KHONG"
    assert kq["du_lieu_thieu"] is False
