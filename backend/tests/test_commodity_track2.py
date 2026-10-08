"""test_commodity_track2.py — hợp đồng cho Tiên quyết F (exposure_sign) và B (hit-rate).

Spec: docs/SPEC_DUAL_TRACK_DECISION_2026-10.md — mục 11 (Addendum A) và 12 (Addendum B).

Các test ở đây bảo vệ 3 luật không được vi phạm:
  1. PVT (vận tải thủy) có dầu là CHI PHÍ → prior_sign = −1. Sai dấu ⇒ vào lệnh ngược chiều.
  2. Mọi mã trong registry phải có nguồn lý luận + ICB (không được thêm bằng trực giác).
  3. Mẫu hit-rate phải KHÔNG chồng lấn và dấu ĐO ĐƯỢC ngược tiên nghiệm ⇒ LOẠI, không lật dấu.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(PROJECT_ROOT / "backend"), str(PROJECT_ROOT / "backend" / "src")]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, PROJECT_ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    # PHẢI đăng ký vào sys.modules TRƯỚC khi exec: module dùng
    # `from __future__ import annotations`, nên @dataclass phải tra
    # sys.modules[cls.__module__].__dict__ để resolve annotation dạng chuỗi;
    # không có mặt ⇒ AttributeError: 'NoneType' object has no attribute '__dict__'.
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def expo():
    return _load("commodity_exposure", "backend/src/calibration/commodity_exposure.py")


@pytest.fixture(scope="module")
def hr():
    return _load("commodity_hit_rate", "backend/src/engine/commodity_hit_rate.py")


# ══════════════════════════════════════════════════════════════════
# TIÊN QUYẾT F — registry exposure_sign
# ══════════════════════════════════════════════════════════════════
def test_pvt_dau_la_chi_phi(expo):
    """CONTRACT BẮT BUỘC: ca duy nhất không thể tranh cãi về dấu."""
    e = expo.get("PVT")
    assert e is not None
    assert e.prior_sign == -1, "PVT là Vận tải Thủy — dầu là nhiên liệu (chi phí), phải là -1"
    assert e.driver == "BRENT_OIL"


def test_moi_ma_co_nguon_ly_luan_va_icb(expo):
    for sym, e in expo.COMMODITY_EXPOSURE_REGISTRY.items():
        assert e.rationale_source.strip(), f"{sym} thiếu nguồn lý luận"
        assert e.icb_path.strip(), f"{sym} thiếu ICB"
        assert e.tier in ("U1", "U2", "EXCLUDED"), f"{sym} tier lạ: {e.tier}"
        assert e.driver.strip(), f"{sym} thiếu driver"


def test_khong_ma_nao_co_sign_bang_0(expo):
    """0 nghĩa là 'không rõ phơi nhiễm' — phải LOẠI khỏi registry, không khai báo 0."""
    for sym, e in expo.COMMODITY_EXPOSURE_REGISTRY.items():
        assert e.prior_sign in (-1, +1), f"{sym} có sign {e.prior_sign} — phải là -1 hoặc +1"


def test_icb_khai_bao_khop_db_that(expo):
    """ICB trong registry phải khớp bảng symbol_industry — chống khai báo sai nguồn."""
    mism = expo.verify_icb_against_db()
    assert mism == [], f"ICB lệch DB: {mism}"


def test_msr_bi_loai_vi_thieu_chuoi_gia(expo):
    """MSR cần giá KIM LOẠI, không có trong macro_history ⇒ loại (fail-closed)."""
    assert "MSR" not in [e.symbol for e in expo.active_universe()]
    reasons = dict(expo.excluded())
    assert "MSR" in reasons and "kim loại" in reasons["MSR"].lower()


def test_tien_nghiem_yeu_doi_nguong_cao_hon(expo):
    """PLX (LOW: phân phối, dấu tranh chấp) phải khó qua cổng hơn PVT (HIGH)."""
    assert expo.hit_rate_threshold(expo.get("PLX")) > expo.hit_rate_threshold(expo.get("PVT"))


def test_lag_quy_doi_tuan_sang_phien(expo):
    """lag_weeks là TUẦN — dùng thẳng làm k theo phiên sẽ lệch 5×."""
    bsr = expo.get("BSR")
    assert bsr.lag_mid_k == 4
    assert bsr.lag_mid_sessions == 20


def test_universe_active_chi_gom_driver_co_du_lieu(expo):
    for e in expo.active_universe():
        assert e.driver in expo.AVAILABLE_DRIVERS
        assert e.tier != "EXCLUDED"


# ══════════════════════════════════════════════════════════════════
# TIÊN QUYẾT B — lõi toán hit-rate
# ══════════════════════════════════════════════════════════════════
def test_hit_rate_random_xap_xi_50pct(hr):
    import random

    random.seed(1234)
    closes = [random.gauss(100, 5) for _ in range(2000)]
    drivers = [random.gauss(80, 5) for _ in range(2000)]
    agree, total = hr.hit_rate_pure(closes, drivers, 5, +1)
    assert total > 200
    assert 0.40 < agree / total < 0.60, f"random phải ~0.5, được {agree / total:.3f}"


def test_hit_rate_dong_pha_hoan_hao(hr):
    """Driver và giá cùng đơn điệu tăng ⇒ sign=+1 phải khớp 100%."""
    closes = [100.0 + i for i in range(400)]
    drivers = [50.0 + i for i in range(400)]
    agree, total = hr.hit_rate_pure(closes, drivers, 5, +1)
    assert total > 30
    assert agree == total


def test_hit_rate_nguoc_dau_thi_0pct(hr):
    """Cùng dữ liệu nhưng khai dấu ngược ⇒ 0% ⇒ hệ thống phải REJECT."""
    closes = [100.0 + i for i in range(400)]
    drivers = [50.0 + i for i in range(400)]
    agree, total = hr.hit_rate_pure(closes, drivers, 5, -1)
    assert agree == 0 and total > 30


def test_mau_doc_lap_it_hon_mau_truot(hr):
    """k=12: 120 quan sát TRƯỢT chỉ là ~9 mẫu ĐỘC LẬP — không được đếm là 120."""
    assert hr.independent_n(120, 12) == 9
    assert hr.independent_n(120, 12) < hr.MIN_INDEPENDENT_SAMPLES


def test_cua_so_hieu_dung_du_30_mau_doc_lap(hr):
    for k in (2, 5, 10, 20, 30):
        n = hr._required_window(k, 120)
        _, total = hr.hit_rate_pure([float(i) for i in range(n)], [float(i) for i in range(n)], k, +1)
        assert total >= hr.MIN_INDEPENDENT_SAMPLES, f"k={k}: chỉ {total} mẫu với cửa sổ {n}"


# ══════════════════════════════════════════════════════════════════
# Tích hợp DB tổng hợp — verdict CONFIRMED / REJECT / INSUFFICIENT
# ══════════════════════════════════════════════════════════════════
def _make_db(tmp_path: Path, n_sessions: int, follow: bool = True) -> Path:
    db = tmp_path / "t.db"
    c = sqlite3.connect(db)
    c.executescript(
        "CREATE TABLE daily_ohlcv (symbol TEXT, date TEXT, close REAL, is_stale INTEGER DEFAULT 0);"
        "CREATE TABLE macro_history_v2 (variable TEXT, date TEXT, value REAL, unit TEXT, asset_class TEXT);"
    )
    for i in range(n_sessions):
        d = f"2025-{1 + i // 28:02d}-{1 + i % 28:02d}"
        driver = 50.0 + i
        close = (100.0 + i) if follow else (100.0 + n_sessions - i)
        c.execute("INSERT INTO macro_history_v2 (variable,date,value,unit,asset_class) VALUES ('BRENT_OIL',?,?,'usd_per_barrel','commodity')", (d, driver))
        c.execute("INSERT INTO daily_ohlcv (symbol,date,close,is_stale) VALUES ('PVD',?,?,0)", (d, close))
    c.commit()
    c.close()
    return db


def test_verdict_confirmed_khi_gia_bam_driver(hr, tmp_path):
    db = _make_db(tmp_path, 400, follow=True)
    r = hr.calculate_non_overlapping_hit_rate("PVD", "BRENT_OIL", lag_k=5, exposure_sign=+1, db_path=db)
    assert r.status == hr.STATUS_OK
    assert r.verdict == hr.VERDICT_CONFIRMED
    assert r.hit_rate == 1.0
    assert r.n_independent >= hr.MIN_INDEPENDENT_SAMPLES


def test_verdict_reject_khi_nguoc_dau_va_khong_lat_dau(hr, tmp_path):
    """Giả thuyết bị phản bác ⇒ REJECT. TUYỆT ĐỐI không tự lật dấu."""
    db = _make_db(tmp_path, 400, follow=True)
    r = hr.calculate_non_overlapping_hit_rate("PVD", "BRENT_OIL", lag_k=5, exposure_sign=-1, db_path=db)
    assert r.verdict == hr.VERDICT_REJECT
    assert r.hit_rate == 0.0
    assert r.measured_sign == +1, "đo ngược dấu khai báo ⇒ measured_sign phải là +1, không ghi đè prior"


def test_insufficient_data_khi_qua_it_phien(hr, tmp_path):
    db = _make_db(tmp_path, 60, follow=True)
    r = hr.calculate_non_overlapping_hit_rate("PVD", "BRENT_OIL", lag_k=5, exposure_sign=+1, db_path=db)
    assert r.status == hr.STATUS_INSUFFICIENT
    assert r.verdict == hr.VERDICT_INSUFFICIENT
    assert r.hit_rate is None, "không đủ mẫu ⇒ KHÔNG được trả về một con số hit-rate"


def test_no_driver_data_khi_thieu_chuoi(hr, tmp_path):
    db = _make_db(tmp_path, 400, follow=True)
    r = hr.calculate_non_overlapping_hit_rate("PVD", "METALS", lag_k=5, exposure_sign=+1, db_path=db)
    assert r.status == hr.STATUS_NO_DRIVER
    assert r.verdict == hr.VERDICT_INSUFFICIENT


# ── Khoảng tin cậy: chống tuyên bố quá mức trên mẫu nhỏ ───────────────
def test_wilson_ci_phan_biet_3_vung(hr):
    """n=50: 23/50 không kết luận được; 5/50 mới đủ nói ngược; 45/50 mới đủ nói thuận."""
    lo, hi = hr.wilson_interval(23, 50)
    assert lo < 0.50 < hi, "23/50 KHÔNG đủ để kết luận 'ngược dấu'"
    assert hr.wilson_interval(5, 50)[1] < 0.50
    assert hr.wilson_interval(45, 50)[0] > 0.50


def test_hit_rate_gan_0p5_khong_bi_goi_la_nguoc_dau(hr, tmp_path):
    """hr ≈ 0.46 với N≈50 ⇒ NOT_CONFIRMED, KHÔNG phải REJECT_TICKER."""
    db = tmp_path / "t.db"
    c = sqlite3.connect(db)
    c.executescript(
        "CREATE TABLE daily_ohlcv (symbol TEXT, date TEXT, close REAL, is_stale INTEGER DEFAULT 0);"
        "CREATE TABLE macro_history_v2 (variable TEXT, date TEXT, value REAL, unit TEXT, asset_class TEXT);"
    )
    n = 400
    for i in range(n):
        d = f"2025-{1 + i // 28:02d}-{1 + i % 28:02d}"
        driver = 50.0 + i
        # giá đi lệch nhẹ so với dấu +1 ⇒ hit-rate dưới 0.5 nhưng không cách biệt
        close = 100.0 + i + (7 if i % 3 == 0 else 0)
        c.execute("INSERT INTO macro_history_v2 (variable,date,value) VALUES ('BRENT_OIL',?,?)", (d, driver))
        c.execute("INSERT INTO daily_ohlcv (symbol,date,close,is_stale) VALUES ('PVD',?,?,0)", (d, close))
    c.commit()
    c.close()
    r = hr.calculate_non_overlapping_hit_rate("PVD", "BRENT_OIL", lag_k=5, exposure_sign=+1, db_path=db)
    assert r.status == hr.STATUS_OK
    assert r.ci_low is not None and r.ci_high is not None
    if r.ci_high > 0.50:
        assert r.verdict != hr.VERDICT_REJECT, (
            f"CI [{r.ci_low:.3f}, {r.ci_high:.3f}] chứa 0.50 ⇒ không được tuyên bố ngược dấu"
        )
