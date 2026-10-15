"""Replay FROZEN SPEC: Capitulation + T+2 Absorption (spec chốt 2026-10-14).

ĐK1 (T0): drawdown10 = (low_T0 − max(high[T−9..T0])) / max(high[T−9..T0]) ≤ −0,15
          range = (high_T0 − low_T0)/low_T0 ≥ 0,04
          close_pos = (close_T0 − low_T0)/(high_T0 − low_T0) ≥ 0,45
          volume_T0 ≥ 2,5 × ADV20_{T−1}
ĐK2 (T+1): low_{T+1} ≥ 0,99 × close_T0  AND  close_{T+1} > close_T0
ĐK3 (T+2): low_{T+2} ≥ close_T0
          0,7 × ADV20_{T−1} ≤ volume_{T+2} ≤ 1,5 × ADV20_{T−1}
          (close_{T+2} − low_{T+2})/(high_{T+2} − low_{T+2}) ≥ 0,80  AND close_{T+2} ≥ close_{T+1}

Entry = close_{T+2}; ra tại close_{T+5} và close_{T+10}; chi phí 0,3% (khứ hồi) trừ thẳng.
ADV20_{T−1} = trung bình volume 20 PHIÊN LIỀN TRƯỚC T0 (tuyệt đối không dùng T0).

KỶ LUẬT (zero-hallucination):
  - Chỉ cổ phiếu (STOCK_ONLY_SQL), chỉ dòng HỢP LỆ (VALID_ROW_SQL), phiên bóng ma đã quarantine.
  - Vị trí theo LỊCH PHIÊN THỊ TRƯỜNG: nếu mã thiếu bar ở bất kỳ phiên nào trong cửa sổ
    (T−9 … T+10) thì loại sự kiện — KHÔNG fillna, KHÔNG trượt sang bar kế tiếp.
    (SHB: T+1/T+2 là phiên kế tiếp THẬT, không phải bar kế tiếp của mã.)
  - Chặn gãy thang giá: nếu chuỗi close của mã có bước nhảy > 6× trong cửa sổ T−10..T+10
    (dấu vết trộn nguồn VND ↔ nghìn đồng) thì loại sự kiện.
  - 2026 là OOS nhiễm → mọi con số 2026 chỉ để ĐỐI CHIẾU MÔ TẢ, KHÔNG dùng để kết luận
    hiệu quả và KHÔNG được dùng để tune.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]  # repo root (chạy: python backend/scripts/capitulation_replay.py)
DB = ROOT / "backend" / "data" / "screener_cache.db"
OUT = Path(os.environ.get("PTCK_RUN_DIR", str(Path(os.environ.get("TEMP", ".")) / "ptck_run")))
COST = 0.003
IS_START, IS_END = "2021-01-04", "2024-12-31"


def load_matrices():
    con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    q = """
        SELECT symbol, date, high, low, close, volume
        FROM daily_ohlcv
        WHERE high IS NOT NULL AND low IS NOT NULL AND close IS NOT NULL
          AND volume IS NOT NULL
          AND close > 0 AND low > 0 AND high >= low
          AND symbol NOT IN ('VNINDEX','VN30','HNXINDEX','HNX30','UPCOMINDEX','UPINDEX')
          AND date >= '2020-11-15'
        ORDER BY symbol, date
    """
    df = pd.read_sql(q, con)
    con.close()
    cal = sorted(df["date"].unique())
    pos = {d: i for i, d in enumerate(cal)}
    df["p"] = df["date"].map(pos)
    n_sym = df["symbol"].nunique()
    syms = sorted(df["symbol"].unique())
    idx = {s: i for i, s in enumerate(syms)}
    shape = (n_sym, len(cal))
    H = np.full(shape, np.nan)
    L = np.full(shape, np.nan)
    C = np.full(shape, np.nan)
    V = np.full(shape, np.nan)
    r = df["symbol"].map(idx).to_numpy()
    c = df["p"].to_numpy()
    H[r, c] = df["high"].to_numpy()
    L[r, c] = df["low"].to_numpy()
    C[r, c] = df["close"].to_numpy()
    V[r, c] = df["volume"].to_numpy()
    return syms, cal, H, L, C, V


def main() -> int:
    syms, cal, H, L, C, V = load_matrices()
    n_sym, n_cal = C.shape
    print(f"  ma trận: {n_sym} mã × {n_cal} phiên ({cal[0]} → {cal[-1]})")

    Hd, Ld, Cd, Vd = (pd.DataFrame(x) for x in (H, L, C, V))

    def _roll_mean(df, w):
        return df.T.rolling(w, min_periods=w).mean().T

    def _roll_max(df, w):
        return df.T.rolling(w, min_periods=w).max().T

    adv20_prev = _roll_mean(Vd.shift(1, axis=1), 20)
    maxH10 = _roll_max(Hd, 10)  # T-9..T0 (gồm cả T0)
    ddown = (Ld - maxH10) / maxH10
    rng = (Hd - Ld) / Ld
    cpos = (Cd - Ld) / (Hd - Ld)
    volmul = Vd / adv20_prev

    t0 = (ddown <= -0.15) & (rng >= 0.04) & (cpos >= 0.45) & (volmul >= 2.5)
    t0 = t0.fillna(False)

    c0 = Cd
    c1, l1 = Cd.shift(-1, axis=1), Ld.shift(-1, axis=1)
    c2, l2, h2, v2 = (
        Cd.shift(-2, axis=1),
        Ld.shift(-2, axis=1),
        Hd.shift(-2, axis=1),
        Vd.shift(-2, axis=1),
    )
    c5, c10 = Cd.shift(-5, axis=1), Cd.shift(-10, axis=1)

    k2 = (l1 >= 0.99 * c0) & (c1 > c0)
    k3 = (l2 >= c0) & (v2 >= 0.7 * adv20_prev) & (v2 <= 1.5 * adv20_prev) & ((c2 - l2) / (h2 - l2) >= 0.80) & (c2 >= c1)
    # Cửa sổ BẮT BUỘC để ĐỦ ĐIỀU KIỆN: T-20..T+2 (ADV20 cần 20 phiên trước T0;
    # T+5/T+10 chỉ là ĐO KẾT QUẢ nên được phép NaN — không dùng để chặn sự kiện,
    # nếu chặn thì mọi sự kiện gần đây (T+10 chưa xảy ra) sẽ biến mất.
    win_ok = pd.DataFrame(True, index=Cd.index, columns=Cd.columns)
    # X.shift(k, axis=1) tại cột p = X[p−k] ⇒ để đòi bar tại p−20 dùng k=+20,
    # tại p+2 dùng k=−2. (Bản đầu ghi range(−20,3) nên vô tình đòi 20 phiên TƯƠNG LAI
    # ⇒ mọi sự kiện gần đây bị loại, 10/2026 trắng bảng.)
    for k in range(-2, 21):
        win_ok &= Ld.shift(k, axis=1).notna() & Hd.shift(k, axis=1).notna() & Cd.shift(k, axis=1).notna()

    # chặn gãy thang giá (trộn nguồn) trong T-10..T+10
    ratio = Cd / Cd.shift(1, axis=1)
    brk = ((ratio > 6) | (ratio < 1 / 6)).fillna(False)
    breaks_in_win = pd.DataFrame(0, index=Cd.index, columns=Cd.columns)
    for k in range(0, 11):
        breaks_in_win = breaks_in_win + brk.shift(-k, axis=1).fillna(False).astype(int)
        breaks_in_win = breaks_in_win + brk.shift(k, axis=1).fillna(False).astype(int)
    scale_ok = breaks_in_win == 0

    ev = t0 & k2 & k3 & win_ok & scale_ok
    r5 = (c5 / c2 - 1) - COST
    r10 = (c10 / c2 - 1) - COST

    sym_of = np.array(syms)
    rows = []
    ix = np.argwhere(ev.to_numpy())
    for i, p in ix:
        rows.append(
            dict(
                symbol=sym_of[i],
                t0=cal[p],
                entry_date=cal[p + 2],
                t2=cal[p + 2],
                c0=float(Cd.iat[i, p]),
                c2=float(Cd.iat[i, p + 2]),
                r5=float(r5.iat[i, p]),
                r10=float(r10.iat[i, p]),
            )
        )
    E = pd.DataFrame(rows)
    t0_only = t0.copy()  # để đo breadth ĐK1 theo ngày

    # breadth ĐK1 (mã thoả ĐK1 trong ngày) và breadth sự kiện đủ 3 điều kiện
    b1 = t0_only.sum(axis=0)
    b1 = pd.DataFrame({"date": cal, "dk1": b1.to_numpy()})
    if not E.empty:
        ev_breadth = E.groupby("t0").size().rename("events")
        b1 = b1.merge(ev_breadth, left_on="date", right_index=True, how="left").fillna({"events": 0})

    E["t0dt"] = pd.to_datetime(E["t0"])
    is_m = E[(E["t0"] >= IS_START) & (E["t0"] <= IS_END)].copy()
    oct_m = E[E["t0"].str.startswith("2026-10")].copy()

    def describe(df, label):
        out = {"tập": label, "N": int(len(df))}
        if df.empty:
            return out
        for h, col in (("T+5", "r5"), ("T+10", "r10")):
            x = df[col].dropna()
            if x.empty:
                continue
            # sai số chuẩn theo CỤM NGÀY T0 (nhiều mã cùng ngày không độc lập với nhau)
            g = df.dropna(subset=[col]).groupby("t0")[col].mean()
            n_eff = len(g)
            se = g.std(ddof=1) / np.sqrt(n_eff) if n_eff > 1 else np.nan
            out[h] = {
                "mean": round(float(x.mean()), 4),
                "median": round(float(x.median()), 4),
                "winrate": round(float((x > 0).mean()), 3),
                "se_cluster_ngay": (round(float(se), 4) if se == se else None),
                "t_cluster": (round(float(x.mean() / se), 2) if se and se == se and se > 0 else None),
                "n_ngay_T0": int(n_eff),
            }
        return out

    rep = {
        "so_su_kien_toan_ky": int(len(E)),
        "so_ngay_T0_dk1_toan_ky": int((b1["dk1"] > 0).sum()),
        "IS": describe(is_m, f"IS {IS_START}..{IS_END}"),
        "breadth_IS": {
            "mean_ma_dk1_moi_ngay_co_tin_hieu": round(float(b1[b1["dk1"] > 0]["dk1"].mean()), 1)
            if (b1["dk1"] > 0).any()
            else 0,
            "max_ma_dk1": int(b1["dk1"].max()),
            "mean_su_kien_du_3_dk_moi_t0": round(float(is_m.groupby("t0").size().mean()), 2) if len(is_m) else 0,
            "max_su_kien_mot_ngay": int(is_m.groupby("t0").size().max()) if len(is_m) else 0,
        },
    }

    # tương quan breadth ↔ xác suất thắng (T+10), theo cụm ngày
    if len(is_m):
        day = is_m.dropna(subset=["r10"]).groupby("t0").agg(r10=("r10", "mean"), n=("r10", "size"))
        day = day.join(b1.set_index("date")[["dk1"]], how="left")
        day = day.dropna()
        if len(day) > 3:
            rep["breadth_vs_winrate"] = {
                "spearman_dk1_vs_r10": round(float(day["dk1"].corr(day["r10"], method="spearman")), 3),
                "n_ngay": int(len(day)),
                "winrate_theo_nhom_breadth": {
                    str(k): {
                        "n_ngay": int(len(g)),
                        "winrate_T10": round(float((g["r10"] > 0).mean()), 3),
                        "mean_r10": round(float(g["r10"].mean()), 4),
                    }
                    for k, g in day.groupby(
                        pd.cut(day["dk1"], [0, 2, 5, 10, 1e9], labels=["1-2", "3-5", "6-10", ">10"]), observed=True
                    )
                },
            }

    # đối chiếu mô tả 10/2026 (OOS nhiễm — chỉ mô tả)
    cand = t0 & k2 & k3

    def _sum(df):
        return int(df.to_numpy().sum())

    rep["chan_doan_loai"] = {
        "ung_vien_du_3_dk_truoc_kiem_cua_so": _sum(cand),
        "bi_loai_thieu_bar_T-20..T+2": _sum(cand & ~win_ok),
        "bi_loai_gay_thang_gia": _sum(cand & win_ok & ~scale_ok),
        "con_lai": _sum(ev),
        "thieu_DK2": _sum(t0 & ~k2),
        "thieu_DK3": _sum(t0 & k2 & ~k3),
    }

    sym_arr = np.array(syms)
    rows_oct = []
    for d in [x for x in cal if x.startswith("2026-10")]:
        p = cal.index(d)
        if p + 2 >= len(cal):
            break
        col = win_ok.iloc[:, p].to_numpy()
        dk1 = t0.iloc[:, p].to_numpy() & col
        dk2 = dk1 & k2.iloc[:, p].to_numpy()
        dk3 = dk2 & k3.iloc[:, p].to_numpy()
        rows_oct.append(
            {
                "T0": d,
                "T+2": cal[p + 2],
                "ma_danh_gia_duoc": int(col.sum()),
                "DK1": int(dk1.sum()),
                "DK1+2": int(dk2.sum()),
                "du_3_dk": int(dk3.sum()),
                "ma": ", ".join(sorted(sym_arr[dk3].tolist())),
            }
        )
    rep["thang_10_2026"] = {
        "T0_dates_co_su_kien_du_3_dk": sorted(oct_m["t0"].unique().tolist()),
        "N_su_kien_du_3_dk": int(len(oct_m)),
        "ma_thoa_3_dk": sorted(oct_m["symbol"].unique().tolist()),
        "theo_ngay": rows_oct,
        "ghichu": "OOS nhiễm — chỉ đối chiếu mô tả, KHÔNG kết luận hiệu quả, KHÔNG tune",
    }

    OUT.mkdir(parents=True, exist_ok=True)
    E.to_csv(OUT / "capitulation_events_all.csv", index=False)
    is_m.to_csv(OUT / "capitulation_events_is.csv", index=False)
    oct_m.to_csv(OUT / "capitulation_events_oct2026.csv", index=False)
    (OUT / "capitulation_summary.json").write_text(json.dumps(rep, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(rep, indent=2, ensure_ascii=False))
    print(f"\n  CSV: {OUT / 'capitulation_events_all.csv'} | IS | oct2026 | summary.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
