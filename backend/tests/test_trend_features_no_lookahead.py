"""Test chống LOOK-AHEAD BIAS cho trend_features.

Bài test trung tâm (invariant, không phụ thuộc dữ liệu cụ thể):

    Thêm dữ liệu TƯƠNG LAI vào kho ⇒ giá trị feature tại ngày T phải KHÔNG ĐỔI.

Đây là cách kiểm PIT mạnh hơn "đọc code xem có shift không": nó không quan tâm code
viết thế nào, chỉ quan tâm đầu ra có phụ thuộc tương lai hay không. Một bug look-ahead
kiểu "tính RS một lần cho cả giai đoạn rồi tra cứu" sẽ làm test này đỏ ngay.
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(PROJECT_ROOT / "backend"), str(PROJECT_ROOT / "backend" / "src")]

from src.backtest import trend_features as tf  # noqa: E402

SYMBOLS = ("AAA", "BBB", "CCC")
# Hệ số tăng giá mỗi phiên — AAA tăng nhanh nhất, CCC giảm.
DRIFT = {"AAA": 1.0020, "BBB": 1.0005, "CCC": 0.9990}


def _business_days(n: int, start: date = date(2019, 1, 2)) -> list[str]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _make_db(path: Path, n_sessions: int = 420) -> list[str]:
    """DB tối thiểu có đúng schema daily_ohlcv, dữ liệu xác định trước."""
    days = _business_days(n_sessions)
    con = sqlite3.connect(str(path))
    con.execute(
        """CREATE TABLE daily_ohlcv (
               symbol TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL,
               adj_close REAL, volume REAL, source TEXT, is_stale INTEGER)"""
    )
    rows = []
    for sym, g in DRIFT.items():
        px = 100.0
        for i, d in enumerate(days):
            close = round(px, 4)
            rows.append(
                (sym, d, round(px * 0.999, 4), round(px * 1.010, 4), round(px * 0.990, 4),
                 close, close, float(1_000_000 + i * 1_000), "test", 0)
            )
            px *= g
    con.executemany("INSERT INTO daily_ohlcv VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()
    return days


@pytest.fixture(scope="module")
def db(tmp_path_factory) -> tuple[Path, list[str]]:
    p = tmp_path_factory.mktemp("feat") / "f.db"
    days = _make_db(p)
    return p, days


# ══════════════════════════════════════════════════════════════════════
# 1. BẤT BIẾN: tương lai không được đổi quá khứ
# ══════════════════════════════════════════════════════════════════════
def test_them_tuong_lai_khong_doi_bat_ky_feature_nao(db):
    """Ghép tương lai vào kho rồi so từng ô tại ngày T. Phải giống hệt."""
    path, days = db
    t_idx = 330  # còn ≥ 80 phiên phía sau để "ghép tương lai"
    t = days[t_idx]

    store_pit = tf.FeatureStore(days[260], t, db=path)  # chỉ tới T
    store_full = tf.FeatureStore(days[260], days[-1], db=path)  # có cả tương lai

    for sym in SYMBOLS:
        for name, a, b in (
            ("rs", store_pit.rs(sym, t), store_full.rs(sym, t)),
            ("adv20", store_pit.adv20(sym, t), store_full.adv20(sym, t)),
            ("high_20", store_pit.high_20(sym, t), store_full.high_20(sym, t)),
            ("ma20", store_pit.ma20(sym, t), store_full.ma20(sym, t)),
            ("close", store_pit.close(sym, t), store_full.close(sym, t)),
        ):
            assert a is not None and b is not None, f"{sym}.{name} rỗng"
            assert a == pytest.approx(b, rel=0, abs=0), (
                f"LOOK-AHEAD: {sym}.{name} tại {t} đổi khi thêm tương lai: {a} → {b}"
            )


def test_feature_row_day_du_bi_dung_lai_khi_them_tuong_lai(db):
    """Kiểm ở tầng cao nhất: cả FeatureRow phải bất biến."""
    path, days = db
    t = days[330]
    row_pit = tf.FeatureStore(days[260], t, db=path).features("AAA", t)
    row_full = tf.FeatureStore(days[260], days[-1], db=path).features("AAA", t)
    assert row_pit.ok and row_full.ok
    assert row_pit == row_full, "FeatureRow đổi khi thêm dữ liệu tương lai"


def test_rs_tai_T_khong_doi_khi_T_la_ngay_cuoi_hay_ngay_giua(db):
    """Dạng bug nguy hiểm: RS tính một lần trên toàn kỳ rồi tra cứu.
    Nếu vậy, RS tại T sẽ khác nhau tuỳ T nằm ở đâu trong cửa sổ dựng pivot."""
    path, days = db
    t = days[300]
    a = tf.FeatureStore(days[260], t, db=path).rs("AAA", t)
    b = tf.FeatureStore(days[260], days[-1], db=path).rs("AAA", t)
    c = tf.FeatureStore(days[260], days[-1], db=path).rs("AAA", days[300])
    assert a == pytest.approx(b, abs=0) == pytest.approx(c, abs=0)


# ══════════════════════════════════════════════════════════════════════
# 2. Định nghĩa RS phải khớp production
# ══════════════════════════════════════════════════════════════════════
def test_trong_so_rs_khop_rs_ranker_production():
    """0.4·r63 + 0.2·r126 + 0.2·r189 + 0.2·r252 và scale 99."""
    assert tf.RS_WEIGHTS == {63: 0.4, 126: 0.2, 189: 0.2, 252: 0.2}
    assert tf.RS_RANK_SCALE == 99.0
    assert tf.RS_LOOKBACK_SESSIONS == 252
    assert sum(tf.RS_WEIGHTS.values()) == pytest.approx(1.0)


def test_rs_xep_hang_dung_thu_tu_momentum(db):
    """AAA tăng nhanh nhất ⇒ RS cao nhất; CCC giảm ⇒ thấp nhất."""
    path, days = db
    store = tf.FeatureStore(days[260], days[-1], db=path)
    t = days[-1]
    rs = {s: store.rs(s, t) for s in SYMBOLS}
    assert all(v is not None for v in rs.values()), f"RS rỗng bất thường: {rs}"
    a, b, c = rs["AAA"], rs["BBB"], rs["CCC"]
    assert a is not None and b is not None and c is not None
    assert a > b > c, f"thứ hạng sai: {rs}"
    assert a == pytest.approx(99.0), "mã mạnh nhất phải đạt trần 99"


def test_thieu_lich_su_thi_loai_chu_khong_gan_0(db):
    """Cùng một phiên: mã đủ lịch sử có RS, mã mới niêm yết (60 phiên) thì KHÔNG.

    Trả 0 cho mã mới sẽ (a) chấm nó như mã tệ nhất và (b) đẩy percentile của MỌI mã
    khác lên giả tạo — vì `rank` tính trên cả tập, thêm một số 0 bịa là thêm một đối
    thủ hạng giả.
    """
    path, days = db
    con = sqlite3.connect(str(path))
    px = 50.0
    for d in days[-60:]:  # NEW chỉ có 60 phiên ⇒ không thể tính r252
        con.execute(
            "INSERT INTO daily_ohlcv VALUES ('NEW', ?, ?, ?, ?, ?, ?, ?, 'test', 0)",
            (d, px * 0.999, px * 1.01, px * 0.99, px, px, 500_000.0),
        )
        px *= 1.001
    con.commit()
    con.close()

    store = tf.FeatureStore(days[260], days[-1], db=path)
    t = days[-1]
    assert store.rs("AAA", t) is not None, "mã đủ lịch sử phải có RS"
    assert store.rs("NEW", t) is None, "mã chỉ 60 phiên KHÔNG được có RS (không được gán 0)"

    row = store.features("NEW", t)
    assert row.ok is False
    assert any("missing > fake" in r for r in row.reasons)


def test_ma_khong_ton_tai_thi_loai(db):
    path, days = db
    store = tf.FeatureStore(days[260], days[-1], db=path)
    row = store.features("ZZZ", days[-1])
    assert row.ok is False
    assert row.reasons


# ══════════════════════════════════════════════════════════════════════
# 3. Cửa sổ phải TRƯỚC phiên T (không gồm T)
# ══════════════════════════════════════════════════════════════════════
def test_high_20_khong_bao_gom_phien_hien_tai(db, tmp_path):
    """Cú spike High tại chính phiên T không được xuất hiện trong high_20(T).

    Nếu high_20 gồm T thì điều kiện `close > high_20` trở thành bất khả thi — đúng
    loại lỗi làm breakout "không bao giờ đúng" mà không ai thấy.
    """
    path, days = db
    t = days[350]
    store = tf.FeatureStore(days[260], t, db=path)
    h_before = store.high_20("AAA", t)
    assert h_before is not None

    # Đỉnh thật của 20 phiên TRƯỚC T, tính tay từ DB
    con = sqlite3.connect(str(path))
    manual = con.execute(
        "SELECT MAX(high) FROM (SELECT high FROM daily_ohlcv WHERE symbol='AAA' AND date < ? "
        "ORDER BY date DESC LIMIT 20)",
        (t,),
    ).fetchone()[0]
    con.close()
    assert h_before == pytest.approx(manual, rel=1e-9), "high_20 không khớp 20 phiên trước T"


def test_adv20_khong_bao_gom_phien_hien_tai(db):
    """ADV20 tại T = trung bình volume 20 phiên TRƯỚC T (chủ hệ thống chốt: trượt 1 phiên)."""
    path, days = db
    t = days[350]
    store = tf.FeatureStore(days[260], t, db=path)
    got = store.adv20("AAA", t)
    con = sqlite3.connect(str(path))
    manual = con.execute(
        "SELECT AVG(volume) FROM (SELECT volume FROM daily_ohlcv WHERE symbol='AAA' AND date < ? "
        "ORDER BY date DESC LIMIT 20)",
        (t,),
    ).fetchone()[0]
    con.close()
    assert got == pytest.approx(manual, rel=1e-9)


def test_ma20_khong_bao_gom_phien_hien_tai(db):
    path, days = db
    t = days[350]
    store = tf.FeatureStore(days[260], t, db=path)
    got = store.ma20("AAA", t)
    con = sqlite3.connect(str(path))
    manual = con.execute(
        "SELECT AVG(close) FROM (SELECT close FROM daily_ohlcv WHERE symbol='AAA' AND date < ? "
        "ORDER BY date DESC LIMIT 20)",
        (t,),
    ).fetchone()[0]
    con.close()
    assert got == pytest.approx(manual, rel=1e-9)


def test_tat_ca_feature_deu_loai_phien_hien_tai():
    """Ghim cờ cấu hình — đổi nó là đổi định nghĩa feature, phải là quyết định có ý thức."""
    assert tf.EXCLUDE_CURRENT_SESSION is True
    assert tf.BASE_WINDOW == 20


# ══════════════════════════════════════════════════════════════════════
# 4. Warm-up + dòng nhiễm
# ══════════════════════════════════════════════════════════════════════
def test_warmup_lam_ngay_dau_cua_so_co_du_lieu(db):
    """Ngày đầu cửa sổ vẫn phải có RS nhờ warm-up, không được rỗng."""
    path, days = db
    start = days[300]
    store = tf.FeatureStore(start, days[-1], db=path)
    assert store.rs("AAA", start) is not None, "thiếu warm-up ⇒ RS rỗng ở ngày đầu"


def test_dong_nhiem_is_stale_khong_tham_gia(db, tmp_path):
    """Dòng is_stale=1 phải bị loại khỏi pivot — nếu không, giá nhiễm vào feature.

    Hai phép kiểm, vì chỉ một phép là chưa đủ:
      (a) mã CHỈ tồn tại ở dạng nhiễm ⇒ phải là None (chứng minh dòng bị loại);
      (b) mã có cả dòng sạch lẫn dòng nhiễm ⇒ phải trả giá SẠCH (chứng minh pivot
          không "trộn" bản ghi nhiễm vào).
    """
    path = tmp_path / "stale.db"
    days = _make_db(path)
    t = days[350]
    con = sqlite3.connect(str(path))
    con.execute(
        "INSERT INTO daily_ohlcv VALUES ('POISON', ?, 99999, 99999, 99999, 99999, 99999, 1, 'poisoned', 1)",
        (t,),
    )
    con.execute(
        "INSERT INTO daily_ohlcv VALUES ('AAA', ?, 99999, 99999, 99999, 99999, 99999, 1, 'poisoned', 1)",
        (t,),
    )
    con.commit()
    con.close()

    store = tf.FeatureStore(days[260], t, db=path)
    assert store.close("POISON", t) is None, "mã chỉ có dòng is_stale=1 vẫn lọt vào feature"
    aaa = store.close("AAA", t)
    assert aaa is not None and aaa != 99999.0, f"dòng nhiễm trộn vào giá sạch: {aaa}"
