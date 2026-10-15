"""Regression test — Ca 3: VNINDEX context KHÔNG được bịa số 0 khi thiếu dữ liệu.

[WHY] `scan_liquidity_concentration()` là engine quyết định "chế độ thanh khoản thị trường".
Khi thiếu dữ liệu VNINDEX, hàm tự bịa `{"close": 0, "chg": 0, "vol_ratio": 0}`. Số 0 KHÔNG
phải "không biết" — nó là một GIÁ TRỊ ĐO ĐƯỢC, và nó đi thẳng vào nhánh quyết định:

    elif vni_info.get("vol_ratio", 1) < 0.6:      # 0 < 0,6 ⇒ TRUE
        classification = "LOW_VOLUME_REGIME"      # nhãn chế độ + conviction MEDIUM

⇒ Một lần fetch lỗi biến thành tuyên bố chắc chắn "thị trường thanh khoản thấp".

[ROOT CAUSE] 3 ổ phát tán CÙNG một lớp lỗi ("không biết" bị mã hoá thành 0):
    1. `vni_info = {}` (dòng 526) — fetch xong nhưng rỗng ⇒ mọi `.get(k, default)` rơi vào
       default, và default ở hai nhánh ĐÁ NGHAU: `.get("vol_ratio", 1)` (621) vs `.get("vol_ratio", 0)` (624).
    2. `else 0` cho `chg` / `vol_ratio` / `ma20` (dòng 535/539/540) — lịch sử chưa đủ dài
       (chưa đủ 21 phiên) cũng bị ghi thành 0.
    3. `vni_info = {"close": 0, "chg": 0, "vol_ratio": 0}` (dòng 544) — nhánh except.
    4. Hệ quả ghi xuống DB `_store_reference_case()` với `.get(k, 0)` (dòng 713–715).

[INVARIANT] "Không biết" PHẢI là `None` xuyên suốt: verdict → log → DB (SQL NULL).
    Không nhánh nào được suy ra chế độ thanh khoản từ một giá trị không đo được; khi thiếu
    dữ liệu, engine phải KHAI BÁO thiếu (`MISSING_VNI_VOL_RATIO`) và hạ nhãn xuống
    `VNINDEX_DATA_MISSING` / conviction `ZERO` — không gán nhãn chế độ nào khác.

BẰNG CHỨNG DỮ LIỆU THẬT (bảng `capital_displacement_history`, 10 dòng, 2026-05-26 → 2026-07-15):
    ngày 2026-06-30 → vnindex_close=0.0, vnindex_chg=0.0, vnindex_vol_ratio=0.0
    ⇒ 1/10 dòng (10%) mang số 0 bịa ra, đã nằm thường trú trong DB (không phân biệt được
      với "VNINDEX đóng cửa 0 điểm" nếu chỉ nhìn dữ liệu).
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager

import pandas as pd
import requests

import src.engine.capital_displacement_engine as cde

# 12 mã: 4 BANK (33,3% < 35% ⇒ không rơi vào BANK_SHELTERING), 8 phi-BANK tăng giá
# (sector_breadth = 100% > 60). Khối lượng bằng nhau ⇒ top1 = 8,3% (< 12% ⇒ không rơi
# vào SELECTIVE_CONCENTRATION). Nhờ vậy bài test đo ĐÚNG nhánh Rule 7 (VNINDEX).
_CONSTITUENTS = ["ACB", "TCB", "VCB", "BID", "SSI", "HPG", "VNM", "FPT", "MWG", "REE", "GMD", "VTP"]
_MEASURED_LABELS = {"LOW_VOLUME_REGIME", "BROAD_EXPANSION", "MIXED_LIQUIDITY"}


def _stub_snapshot() -> dict:
    """OHLCV tối thiểu (2 phiên) cho toàn bộ rổ: phiên cuối tăng 1%."""
    return {s: pd.DataFrame({"close": [100.0, 101.0], "volume": [1000, 1000]}) for s in _CONSTITUENTS}


class _RaisingProvider:
    """Provider mô phỏng mạng lỗi (nhánh except của VNINDEX context)."""

    def __init__(self, *args, **kwargs):
        pass

    def history(self, *args, **kwargs):
        raise requests.RequestException("mạng lỗi — mô phỏng Ca 3")


def _fixed_provider(df):
    """Provider trả đúng một khung VNINDEX cho trước."""

    class _Fixed:
        def __init__(self, *args, **kwargs):
            pass

        def history(self, *args, **kwargs):
            return df

    return _Fixed


def _vni_frame(n_rows: int, last_volume: float) -> pd.DataFrame:
    """Khung VNINDEX n_rows phiên: giá phẳng 1000, volume nền 1000 trừ phiên cuối."""
    return pd.DataFrame({"close": [1000.0] * n_rows, "volume": [1000.0] * (n_rows - 1) + [last_volume]})


def _mem_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE capital_displacement_history (date TEXT PRIMARY KEY, classification TEXT, conviction TEXT,"
        " signals TEXT, top1_symbol TEXT, top1_concentration REAL, wl_top1_symbol TEXT, wl_top1_concentration REAL,"
        " top3_concentration REAL, top10_concentration REAL, bank_share REAL, sector_breadth REAL,"
        " defensive_avg_chg REAL, vnindex_close REAL, vnindex_chg REAL, vnindex_vol_ratio REAL)"
    )
    return conn


def _bind_mem_db(monkeypatch, conn: sqlite3.Connection) -> None:
    @contextmanager
    def _conn():
        yield conn

    monkeypatch.setattr(cde, "get_connection", _conn)


def _scan_missing_vnindex(monkeypatch, provider):
    """Chạy scan với VNINDEX hỏng theo `provider`, trả verdict."""
    import src.services.macro.interbank_zscore as ib

    monkeypatch.setattr(cde, "_fetch_batch", lambda *a, **kw: _stub_snapshot())
    monkeypatch.setattr(ib, "assess_interbank_risk", lambda: {"signal": "NORMAL", "reason": "test", "zscore": {}})
    monkeypatch.setattr(cde, "VnstockProvider", provider)
    return cde.scan_liquidity_concentration("2026-06-30")


# ─────────────────────────────────────────────────────────────────────────────
# 1. Fetch LỖI ⇒ "không biết", không phải 0
# ─────────────────────────────────────────────────────────────────────────────
def test_failed_fetch_does_not_fabricate_zero(monkeypatch):
    """ĐIỀU KIỆN CHÍNH: provider raise ⇒ mọi trường VNINDEX là None (KHÔNG phải 0)."""
    verdict = _scan_missing_vnindex(monkeypatch, _RaisingProvider)
    vni = verdict["vnindex"]
    assert vni["vol_ratio"] is None, f"FAIL-OPEN: thiếu VNINDEX nhưng vol_ratio = {vni['vol_ratio']!r}"
    assert vni["close"] is None, f"FAIL-OPEN: thiếu VNINDEX nhưng close = {vni['close']!r}"
    assert vni["chg"] is None, f"FAIL-OPEN: thiếu VNINDEX nhưng chg = {vni['chg']!r}"


def test_failed_fetch_does_not_claim_a_liquidity_regime(monkeypatch):
    """vol_ratio 0 bịa ra ⇒ 0 < 0,6 ⇒ LOW_VOLUME_REGIME (tuyên bố chế độ từ hư không)."""
    verdict = _scan_missing_vnindex(monkeypatch, _RaisingProvider)
    assert verdict["classification"] not in _MEASURED_LABELS, (
        f"FAIL-OPEN: thiếu VNINDEX nhưng engine vẫn tuyên bố chế độ {verdict['classification']!r}"
    )
    assert verdict["classification"] == "VNINDEX_DATA_MISSING"
    assert verdict["conviction"] == "ZERO", "Thiếu dữ liệu mà vẫn giữ conviction > ZERO là tự tin giả"


def test_missing_vnindex_is_declared_in_signals(monkeypatch):
    """Suy giảm phải được KHAI BÁO (log-only, không phải cổng im lặng)."""
    verdict = _scan_missing_vnindex(monkeypatch, _RaisingProvider)
    assert "MISSING_VNI_VOL_RATIO" in verdict["signals"]


# ─────────────────────────────────────────────────────────────────────────────
# 2. Fetch THÀNH CÔNG nhưng khung rỗng ⇒ cũng là "không biết" (ổ dịch thứ 2)
# ─────────────────────────────────────────────────────────────────────────────
def test_empty_frame_is_treated_as_missing(monkeypatch):
    """`vni_info = {}` ⇒ hai nhánh Rule 7 dùng default ĐÁ NHAU (1 và 0) cho cùng trạng thái."""
    verdict = _scan_missing_vnindex(monkeypatch, _EmptyProvider)
    vni = verdict["vnindex"]
    assert vni.get("vol_ratio") is None, f"Khung rỗng nhưng vol_ratio = {vni.get('vol_ratio')!r}"
    assert verdict["classification"] == "VNINDEX_DATA_MISSING"


def test_none_frame_is_treated_as_missing(monkeypatch):
    """Provider trả None (một số nguồn trả None thay vì DataFrame rỗng)."""
    verdict = _scan_missing_vnindex(monkeypatch, _NoneProvider)
    assert verdict["vnindex"].get("vol_ratio") is None
    assert verdict["classification"] == "VNINDEX_DATA_MISSING"


class _EmptyProvider:
    def __init__(self, *args, **kwargs):
        pass

    def history(self, *args, **kwargs):
        return pd.DataFrame()


class _NoneProvider:
    def __init__(self, *args, **kwargs):
        pass

    def history(self, *args, **kwargs):
        return None


# ─────────────────────────────────────────────────────────────────────────────
# 3. Lịch sử CHƯA ĐỦ DÀI ⇒ None, không phải 0 (ổ dịch 535/539/540)
# ─────────────────────────────────────────────────────────────────────────────
def test_single_row_history_yields_none_chg_not_zero(monkeypatch):
    """1 phiên ⇒ không tính được %thay đổi: phải là None, không được bịa 0%."""
    verdict = _scan_missing_vnindex(monkeypatch, _fixed_provider(_vni_frame(1, 1000.0)))
    vni = verdict["vnindex"]
    assert vni["close"] == 1000.0, "Giá ĐO ĐƯỢC phải đi qua nguyên vẹn"
    assert vni["chg"] is None
    assert vni["vol_ratio"] is None


def test_short_history_vol_ratio_is_none_not_zero(monkeypatch):
    """5 phiên (< 21) ⇒ chưa có nền so sánh: None, không phải vol_ratio 0."""
    verdict = _scan_missing_vnindex(monkeypatch, _fixed_provider(_vni_frame(5, 1000.0)))
    assert verdict["vnindex"]["vol_ratio"] is None
    assert verdict["classification"] == "VNINDEX_DATA_MISSING"


# ─────────────────────────────────────────────────────────────────────────────
# 4. CHỐNG VÁ QUÁ TAY: dữ liệu HỢP LỆ phải đi qua nguyên vẹn
# ─────────────────────────────────────────────────────────────────────────────
def test_low_volume_still_classifies_low_volume_regime(monkeypatch):
    """vol_ratio đo được 0,5 (< 0,6) ⇒ LOW_VOLUME_REGIME vẫn phải nổ (không chặn oan)."""
    verdict = _scan_missing_vnindex(monkeypatch, _fixed_provider(_vni_frame(22, 500.0)))
    assert verdict["vnindex"]["vol_ratio"] == 0.5
    assert verdict["classification"] == "LOW_VOLUME_REGIME"
    assert "MISSING_VNI_VOL_RATIO" not in verdict["signals"]


def test_broad_expansion_still_fires(monkeypatch):
    """vol_ratio đo được 0,9 (> 0,7) + breadth 100% ⇒ BROAD_EXPANSION vẫn phải nổ."""
    verdict = _scan_missing_vnindex(monkeypatch, _fixed_provider(_vni_frame(22, 900.0)))
    assert verdict["vnindex"]["vol_ratio"] == 0.9
    assert verdict["classification"] == "BROAD_EXPANSION"


# ─────────────────────────────────────────────────────────────────────────────
# 5. Đường GHI DB: không được hạ cánh số 0 bịa ra xuống bảng (ổ dịch 713–715)
# ─────────────────────────────────────────────────────────────────────────────
def test_store_reference_case_writes_null_not_zero(monkeypatch):
    """Bản ghi thiếu VNINDEX phải để SQL NULL — 0 trong DB không phân biệt được với giá thật.

    Đầu-cuối: verdict lấy từ CHÍNH engine khi fetch lỗi, rồi mới đi qua đường ghi DB.
    """
    verdict = _scan_missing_vnindex(monkeypatch, _RaisingProvider)
    conn = _mem_conn()
    _bind_mem_db(monkeypatch, conn)
    verdict["date"] = "2026-06-30"
    cde._store_reference_case(verdict)
    row = conn.execute(
        "SELECT vnindex_close, vnindex_chg, vnindex_vol_ratio FROM capital_displacement_history WHERE date='2026-06-30'"
    ).fetchone()
    assert row is not None, "Bản ghi không được ghi xuống (đường ghi DB đã vỡ)"
    assert row == (None, None, None), f"FAIL: số 0 bịa ra đã xuống DB: {row!r}"


def test_store_reference_case_keeps_measured_values(monkeypatch):
    """CHỐNG VÁ QUÁ TAY: đo được thật (22 phiên) ⇒ lưu số thật, KHÔNG NULL hoá hàng loạt."""
    verdict = _scan_missing_vnindex(monkeypatch, _fixed_provider(_vni_frame(22, 900.0)))
    conn = _mem_conn()
    _bind_mem_db(monkeypatch, conn)
    verdict["date"] = "2026-07-15"
    cde._store_reference_case(verdict)
    row = conn.execute(
        "SELECT vnindex_close, vnindex_chg, vnindex_vol_ratio FROM capital_displacement_history WHERE date='2026-07-15'"
    ).fetchone()
    assert row == (1000.0, 0.0, 0.9)


# ─────────────────────────────────────────────────────────────────────────────
# 6. Ca 3c: `defensive_avg_chg` hard-code 0 ⇒ chênh lệch giả với các nhóm ngành khác
# ─────────────────────────────────────────────────────────────────────────────
def test_defensive_avg_chg_is_null_not_zero(monkeypatch):
    """[WHY] `"defensive_avg_chg": 0` là một con số ĐO ĐƯỢC bị bịa ra: 0.0% ngụ ý "rổ phòng thủ
    đi ngang", trong khi sự thật là KHÔNG có rổ phòng thủ nào được tính trong engine này.

    [INVARIANT] Không có nguồn tính ⇒ SQL NULL. Bản ghi thiếu trường phải phân biệt được với
    bản ghi có giá trị thật bằng 0.0 (nếu không thì không thể truy vết về sau).
    """
    verdict = _scan_missing_vnindex(monkeypatch, _RaisingProvider)
    conn = _mem_conn()
    _bind_mem_db(monkeypatch, conn)
    verdict["date"] = "2026-06-30"
    cde._store_reference_case(verdict)
    row = conn.execute("SELECT defensive_avg_chg FROM capital_displacement_history WHERE date='2026-06-30'").fetchone()
    assert row is not None, "Bản ghi không được ghi xuống (đường ghi DB đã vỡ)"
    assert row[0] is None, f"FAIL: 0.0% bịa ra đã xuống DB: {row[0]!r}"


def test_other_measured_columns_still_store_real_values(monkeypatch):
    """[WHY] Chống vá quá tay: chỉ cột KHÔNG có nguồn mới thành NULL; các cột có số đo thật
    (top1_concentration, bank_share, sector_breadth) phải giữ nguyên giá trị đo được.
    """
    verdict = _scan_missing_vnindex(monkeypatch, _RaisingProvider)
    conn = _mem_conn()
    _bind_mem_db(monkeypatch, conn)
    verdict["date"] = "2026-07-02"
    cde._store_reference_case(verdict)
    row = conn.execute(
        "SELECT top1_concentration, bank_share, sector_breadth, defensive_avg_chg"
        " FROM capital_displacement_history WHERE date='2026-07-02'"
    ).fetchone()
    assert row[0] is not None and row[0] > 0, f"số đo thật bị NULL hoá: {row!r}"
    assert row[1] is not None
    assert row[2] is not None
    assert row[3] is None
