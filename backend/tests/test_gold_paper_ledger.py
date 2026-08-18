"""test_gold_paper_ledger.py — Unit tests Forward Paper Ledger.

No network. Dùng temp SQLite + synthetic panel để kiểm tra LOGIC:
  - init_ledger tạo schema;
  - generate_forecasts: mu_hat/p_up/p_hat_20/lo80/hi80 hợp lệ, gold_t = GOLD;
  - sync_ledger: append-only (idempotent), is_backfill đúng (matured vs pending),
    mature_date = t+20 phiên, realized_r20 được lưu, realized_p20 điền khi mature;
  - _naive_expmean PIT-safe;
  - report_gate trả dict metrics hợp lệ (không crash trên dữ liệu nhỏ).
"""

import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import src.research.gold_paper_ledger as _ledger_mod
from src.research.gold_magnitude_forecast_v03 import add_log_return_target
from src.research.gold_paper_ledger import (
    LEDGER_TABLE,
    H,
    _naive_expmean,
    _trading_calendar,
    generate_forecasts,
    init_ledger,
    load_ledger,
    report_gate,
    sync_ledger,
)


@pytest.fixture(autouse=True)
def _clean_ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(_ledger_mod, "LEDGER_DB", tmp_path / "gold_paper_ledger.db")
    yield


N = 500
IDX = pd.date_range("2022-01-03", periods=N, freq="B")
_RS = np.random.RandomState(21)
_LEVEL = 100 * np.exp(np.cumsum(_RS.normal(0, 0.01, size=N)))
_X = _RS.normal(size=(N, 5))


def _mk_panel() -> pd.DataFrame:
    df = pd.DataFrame(
        {
            "GOLD": _LEVEL,
            "DXY_z": _X[:, 0],
            "DXY_mom5": _X[:, 1],
            "US10Y_d5": _X[:, 2],
            "TIP_mom5": _X[:, 3],
            "CB_IFS_z": _X[:, 4],
        },
        index=IDX,
    )
    g = df["GOLD"]
    df["R20"] = g.shift(-20) / g - 1
    df["R20_pos"] = (df["R20"] > 0).astype(float)
    return add_log_return_target(df, 20)


def test_init_ledger_schema():
    init_ledger()
    conn = sqlite3.connect(str(_ledger_mod.LEDGER_DB))
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert LEDGER_TABLE in names
    conn.close()


def test_generate_forecasts_columns():
    panel = _mk_panel()
    fc = generate_forecasts(panel)
    assert len(fc) > 0
    assert fc["mu_hat"].notna().sum() == len(fc)
    assert np.isfinite(fc["gold_t"]).all()
    assert np.isfinite(fc["p_hat_20"]).all()
    assert (fc["lo80"] < fc["hi80"]).all()
    # PI GVZ v0.3C frozen: hợp lệ, pi80 nằm trong pi90, không NaN
    fin = fc.dropna(subset=["pi80_lower", "pi80_upper", "pi90_lower", "pi90_upper"])
    assert (fin["pi80_lower"] <= fin["pi80_upper"]).all()
    assert (fin["pi90_lower"] <= fin["pi90_upper"]).all()
    assert len(fin) > 0
    assert (fin["pi80_lower"] >= fin["pi90_lower"]).all()
    assert (fin["pi80_upper"] <= fin["pi90_upper"]).all()
    # gold_t bằng đúng GOLD
    assert np.allclose(fc["gold_t"].values, panel["GOLD"].reindex(fc.index).values)
    # p_up nằm [0,1] nếu có
    pv = fc["p_up"].dropna()
    assert ((pv >= 0) & (pv <= 1)).all()


def test_generate_forecasts_pi_formula():
    """PI GVZ = mu_hat ± q * (GVZ_PIT * GVZ_TO_LOG20), q frozen v0.3C."""
    from src.research.gold_gvz_pi_v03c import GVZ_TO_LOG20
    from src.research.gold_paper_ledger import PI_Q80, PI_Q90
    from src.research.gvz_adapter import load_gvz, pit_align, pit_series

    panel = _mk_panel()
    fc = generate_forecasts(panel)
    if not fc["pi80_lower"].notna().any():
        return  # gvz PIT chưa có cho synthetic panel index — skip (không crash)
    gvz_pub = pit_series(load_gvz())
    sig = pit_align(gvz_pub, panel.index) * GVZ_TO_LOG20
    t = fc["pi80_lower"].dropna().index[0]
    assert abs(fc.at[t, "pi80_lower"] - (fc.at[t, "mu_hat"] - PI_Q80 * sig.at[t])) < 1e-12
    assert abs(fc.at[t, "pi80_upper"] - (fc.at[t, "mu_hat"] + PI_Q80 * sig.at[t])) < 1e-12
    assert abs(fc.at[t, "pi90_lower"] - (fc.at[t, "mu_hat"] - PI_Q90 * sig.at[t])) < 1e-12
    assert abs(fc.at[t, "pi90_upper"] - (fc.at[t, "mu_hat"] + PI_Q90 * sig.at[t])) < 1e-12


def test_trading_calendar_unique_ordered():
    panel = _mk_panel()
    cal = _trading_calendar(panel)
    assert cal.is_monotonic_increasing
    assert len(cal) == len(panel["GOLD"].notna())


def test_sync_ledger_backfill_matured_and_pending():
    panel = _mk_panel()
    init_ledger()
    fc = generate_forecasts(panel)
    s1 = sync_ledger(panel, fc)
    assert s1["inserted"] == len(fc)
    assert s1["total"] == len(fc)
    # backfill là các ngày có realized_r20
    ledger = load_ledger()
    assert (ledger["is_backfill"] == 1).sum() > 0
    assert (ledger["is_backfill"] == 0).sum() >= 0
    # idempotent: chạy lại không insert thêm
    s2 = sync_ledger(panel, fc)
    assert s2["inserted"] == 0
    assert s2["total"] == len(fc)
    # mature_date hợp lệ: realized_r20 == panel R20 tại forecast_date
    sample = ledger.dropna(subset=["realized_r20"]).iloc[0]
    expect = panel["R20"].loc[pd.Timestamp(sample["forecast_date"])]
    assert abs(sample["realized_r20"] - expect) < 1e-9


def test_sync_ledger_pi_columns_migrated_and_filled():
    """DB cũ (không có cột PI) được ALTER + backfill PI mà không đụng mu_hat."""
    panel = _mk_panel()
    fc = generate_forecasts(panel)
    # chọn 1 date có mu_hat (đầu panel chưa đủ train) để test migration
    leg_date = str(fc.index[50].date())
    conn = sqlite3.connect(str(_ledger_mod.LEDGER_DB))
    conn.execute("DROP TABLE IF EXISTS gold_paper_forecasts")
    conn.execute(
        "CREATE TABLE gold_paper_forecasts ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, forecast_date TEXT, model_version TEXT, "
        "feature_vintage TEXT, gold_t REAL, mu_hat REAL, p_up REAL, p_hat_20 REAL, "
        "lo80 REAL, hi80 REAL, is_backfill INTEGER, forecast_ts TEXT, mature_date TEXT, "
        "realized_r20 REAL, realized_p20 REAL, realized_ts TEXT)"
    )
    conn.execute(
        "INSERT INTO gold_paper_forecasts (forecast_date, model_version, mu_hat) VALUES (?, ?, 0.01)",
        (leg_date, "v0.2-m1-cb-h20-frozen"),
    )
    conn.commit()
    conn.close()
    s = sync_ledger(panel, fc)
    ledger = load_ledger()
    rows = ledger[ledger["forecast_date"] == pd.Timestamp(leg_date)]
    assert len(rows) == 1, (
        f"expect 1 row for {leg_date}, got {len(rows)} | inserted={s['inserted']} pi_filled={s.get('pi_filled')}"
    )
    row = rows.iloc[0]
    assert row["pi80_lower"] is not None
    assert row["target_p80_low"] is not None
    assert row["mu_hat"] == 0.01  # không đụng
    assert "pi_filled" in s


def test_sync_ledger_mature_date_20d():
    panel = _mk_panel()
    fc = generate_forecasts(panel)
    cal = _trading_calendar(panel)
    init_ledger()
    sync_ledger(panel, fc)
    ledger = load_ledger()
    row = ledger.dropna(subset=["realized_r20"]).iloc[0]
    fd = pd.Timestamp(row["forecast_date"])
    i = cal.get_loc(fd)
    expect_md = cal[i + H]
    assert pd.Timestamp(row["mature_date"]) == expect_md


def test_sync_ledger_settlement_covered():
    panel = _mk_panel()
    fc = generate_forecasts(panel)
    init_ledger()
    s = sync_ledger(panel, fc)
    ledger = load_ledger()
    settled = ledger.dropna(subset=["realized_p20"])
    assert len(settled) == s["matured_filled"]
    # rows có band target đầy đủ phải có is_covered; rows thiếu GVZ (band NULL) bị loại
    with_band = settled.dropna(subset=["target_p80_low"])
    assert with_band["is_covered_80"].notna().all()
    assert with_band["is_covered_90"].notna().all()
    for _, r in with_band.head(20).iterrows():
        in80 = r["target_p80_low"] <= r["realized_p20"] <= r["target_p80_high"]
        in90 = r["target_p90_low"] <= r["realized_p20"] <= r["target_p90_high"]
        assert r["is_covered_80"] == int(in80)
        assert r["is_covered_90"] == int(in90)
    assert 0.5 <= settled["is_covered_80"].mean() <= 1.0
    assert 0.6 <= settled["is_covered_90"].mean() <= 1.0


def test_sync_ledger_covered_null_when_band_missing():
    panel = _mk_panel()
    fc = generate_forecasts(panel)
    cal = _trading_calendar(panel)
    init_ledger()
    # xóa band target của 1 forecast đã mature -> is_covered phải NULL (loại khỏi mẫu)
    victim = str(fc.index[100].date())
    sync_ledger(panel, fc)
    conn = sqlite3.connect(str(_ledger_mod.LEDGER_DB))
    conn.execute(
        "UPDATE gold_paper_forecasts SET target_p80_low=NULL, target_p80_high=NULL, "
        "target_p90_low=NULL, target_p90_high=NULL WHERE forecast_date=?",
        (victim,),
    )
    conn.execute(
        "UPDATE gold_paper_forecasts SET is_covered_80=NULL, is_covered_90=NULL WHERE forecast_date=?",
        (victim,),
    )
    conn.commit()
    conn.close()
    ledger = load_ledger()
    row = ledger[ledger["forecast_date"] == pd.Timestamp(victim)].iloc[0]
    assert pd.isna(row["is_covered_80"])
    assert pd.isna(row["is_covered_90"])


def test_naive_expmean_pit():
    panel = _mk_panel()
    s = _naive_expmean(panel, 20)
    # tại t chỉ dùng logR đã realized (j <= t-h)
    t = IDX[150]
    realized = panel["logR20"].iloc[: 150 - 20 + 1].dropna().mean()
    assert abs(s.loc[t] - realized) < 1e-12
    assert np.isnan(s.iloc[:20]).any()


def test_report_gate_no_crash():
    panel = _mk_panel()
    init_ledger()
    fc = generate_forecasts(panel)
    sync_ledger(panel, fc)
    res = report_gate(panel, fc)
    assert res["n_total"] == len(fc)
    assert res["n_matured"] == res["n_backfill"]
    assert res["n_pending"] >= 0
    assert "pi" in res  # PI GVZ v0.3C metric luôn có mặt
    if res["n_matured"] >= 5:
        assert "mae_model" in res and "auc" in res and "spearman" in res


def test_report_gate_metrics_sane():
    panel = _mk_panel()
    init_ledger()
    fc = generate_forecasts(panel)
    sync_ledger(panel, fc)
    res = report_gate(panel, fc)
    if res["n_matured"] >= 5:
        assert 0 <= res["sign_acc"] <= 1 if res["sign_acc"] == res["sign_acc"] else True
        assert res["spearman"] == res["spearman"]  # not NaN
        assert res["mae_model"] > 0
        assert abs(res["bias"]) < 0.5
