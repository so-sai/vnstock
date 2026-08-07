import logging
import os
import sys
from pathlib import Path


def _hydrate_path():
    if getattr(sys, "frozen", False):
        root_path = Path(sys.executable).resolve().parent
    else:
        current = Path(__file__).resolve().parent
        root_path = current
        while current != current.parent:
            if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
                root_path = current
                break
            current = current.parent
    if str(root_path) not in sys.path:
        sys.path.insert(0, str(root_path))
    return root_path


PROJECT_ROOT = _hydrate_path()
if sys.platform == "win32":
    import io

    if isinstance(sys.stdout, io.TextIOWrapper):
        if getattr(sys.stdout, "encoding", "").lower() != "utf-8":
            try:
                sys.stdout.reconfigure(encoding="utf-8")
            except OSError, AttributeError, ValueError:
                logging.getLogger(__name__).debug("stdout.reconfigure(utf-8) không khả dụng — giữ nguyên encoding")
    elif hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import numpy as np
import pandas as pd

import src.config
from src.database.db_core import get_connection


def calculate_leadership(lookback=60, top_n=15, min_value=1e9):
    """
    Leadership Tracker v1.0 - Index Contribution Decomposition.
    Identifies top stocks driving VNINDEX using traded-value weighted contribution.
    """
    from src.database.data_integrity import ensure_vnindex_integrity

    ensure_vnindex_integrity(verbose=False)

    print("\n" + "=" * 65)
    print("LEADERSHIP TRACKER v1.0 — Phân tích mã dẫn dắt chỉ số")
    print("=" * 65)

    db_path = os.path.join(src.config.DATA_DIR, "screener_cache.db")
    if not os.path.exists(db_path):
        print("❌ Lỗi: CSDL Vault không tồn tại.")
        return

    with get_connection() as conn:
        idx_dates = pd.read_sql(
            """
            SELECT DISTINCT date FROM daily_ohlcv
            WHERE symbol='VNINDEX' ORDER BY date DESC LIMIT ?
        """,
            conn,
            params=(lookback,),
        )
        if idx_dates.empty:
            print("❌ Không có dữ liệu VNINDEX.")
            return
        start_date = idx_dates["date"].min()
        df = pd.read_sql(
            """
            SELECT symbol, date, close, volume
            FROM daily_ohlcv
            WHERE symbol != 'VNINDEX' AND date >= ?
            ORDER BY symbol, date
        """,
            conn,
            params=(start_date,),
        )
        df_idx = pd.read_sql(
            """
            SELECT date, close as idx_close
            FROM daily_ohlcv
            WHERE symbol='VNINDEX' AND date >= ?
            ORDER BY date
        """,
            conn,
            params=(start_date,),
        )

    if df.empty or df_idx.empty:
        print("⚠️ Không đủ dữ liệu để phân tích.")
        return

    df["date"] = pd.to_datetime(df["date"], format="mixed", errors="coerce").dt.normalize()
    df_idx["date"] = pd.to_datetime(df_idx["date"], format="mixed", errors="coerce").dt.normalize()
    df = df.dropna(subset=["date"]).copy()
    df_idx = df_idx.dropna(subset=["date"]).copy()

    df = df[df["close"] > 0]
    df_idx = df_idx[df_idx["idx_close"] > 100]

    if df.empty or df_idx.empty or len(df_idx) < 10:
        print("⚠️ Không đủ dữ liệu sạch để phân tích.")
        return

    df_idx = df_idx.sort_values("date")
    df_idx["idx_return"] = df_idx["idx_close"].pct_change()
    df_idx["idx_return"] = df_idx["idx_return"].replace([np.inf, -np.inf], 0.0)

    daily_value = df.groupby(["symbol", "date"], as_index=False).agg(close=("close", "last"), volume=("volume", "last"))
    daily_value = daily_value[daily_value["close"] > 0]

    daily_value.insert(3, "traded_value", daily_value["close"] * daily_value["volume"])

    recent = daily_value.loc[daily_value["date"] >= daily_value["date"].max() - pd.Timedelta(days=20)]
    avg_value = recent.groupby("symbol", as_index=False)["traded_value"].mean()
    avg_value.columns = ["symbol", "avg_value"]

    avg_value = avg_value.loc[avg_value["avg_value"] >= min_value]
    total_avg_value = avg_value["avg_value"].sum()
    avg_value.insert(2, "weight", avg_value["avg_value"] / total_avg_value)

    daily_value = daily_value.merge(avg_value[["symbol", "weight"]], on="symbol", how="inner")
    daily_value = daily_value.sort_values(["symbol", "date"]).reset_index(drop=True)
    daily_value.insert(5, "return", daily_value.groupby("symbol")["close"].transform(lambda x: x.pct_change()))
    daily_value["return"] = daily_value["return"].replace([np.inf, -np.inf], 0.0)
    daily_value["return"] = daily_value["return"].clip(-0.5, 0.5)

    daily_value.insert(6, "contribution", daily_value["weight"] * daily_value["return"])
    daily_sum = daily_value.groupby("date", as_index=False)["contribution"].sum()
    daily_sum.columns = ["date", "total_contrib"]

    daily_value = daily_value.merge(daily_sum, on="date", how="left")
    daily_value.insert(
        7,
        "contrib_pct",
        np.where(
            (daily_value["total_contrib"] != 0) & np.isfinite(daily_value["contribution"]),
            daily_value["contribution"] / daily_value["total_contrib"],
            0,
        ),
    )
    daily_value["contribution"] = daily_value["contribution"].replace([np.inf, -np.inf], 0)
    daily_value["contrib_pct"] = daily_value["contrib_pct"].replace([np.inf, -np.inf], 0)

    daily_value = daily_value.merge(df_idx[["date", "idx_close", "idx_return"]], on="date", how="left")

    leader_stats = (
        daily_value.groupby("symbol")
        .agg(
            total_contrib=("contribution", "sum"),
            avg_contrib=("contrib_pct", "mean"),
            max_contrib=("contrib_pct", "max"),
            avg_return=("return", "mean"),
        )
        .reset_index()
    )

    leader_stats = leader_stats.merge(avg_value[["symbol", "avg_value", "weight"]], on="symbol", how="left")
    leader_stats["weight_pct"] = leader_stats["weight"] * 100
    total_market_value = avg_value["avg_value"].sum()
    leader_stats["market_share"] = (leader_stats["avg_value"] / total_market_value * 100).round(2)

    corr_df = daily_value.dropna(subset=["return", "idx_return"]).copy()
    corr_df = corr_df[(corr_df["return"] != 0) & (corr_df["idx_return"] != 0)]

    def calc_corr(g):
        if len(g) < 5:
            return 0.0
        return g["return"].corr(g["idx_return"])

    symbol_corr = corr_df.groupby("symbol", group_keys=False).apply(calc_corr).reset_index()
    symbol_corr.columns = ["symbol", "consistency"]

    leader_stats = leader_stats.merge(symbol_corr, on="symbol", how="left")
    leader_stats["consistency"] = leader_stats["consistency"].fillna(0.0)

    latest_close = daily_value.groupby("symbol", as_index=False).last()[["symbol", "close"]]
    leader_stats = leader_stats.merge(latest_close, on="symbol", how="left")

    def compute_momentum(g):
        g = g.sort_values("date")
        if len(g) >= 21:
            return g["close"].iloc[-1] / g["close"].iloc[-21] - 1
        return np.nan

    momentum_series = daily_value.groupby("symbol", group_keys=False).apply(compute_momentum).reset_index()
    momentum_series.columns = ["symbol", "momentum_20d"]
    leader_stats = leader_stats.merge(momentum_series, on="symbol", how="left")

    sorted_idx = df_idx.sort_values("date")
    idx_20d_return = (sorted_idx["idx_close"].iloc[-1] / sorted_idx["idx_close"].iloc[-21] - 1) if len(sorted_idx) >= 21 else 0
    leader_stats["momentum_20d"] = leader_stats["momentum_20d"].fillna(0).clip(-5, 5)
    leader_stats.insert(len(leader_stats.columns), "rel_momentum", leader_stats["momentum_20d"] - idx_20d_return)

    contrib_p99 = leader_stats["total_contrib"].quantile(0.99)
    if contrib_p99 <= 0 or not np.isfinite(contrib_p99):
        contrib_p99 = 1
    leader_stats.insert(len(leader_stats.columns), "norm_contrib", np.clip(leader_stats["total_contrib"] / contrib_p99, 0, 1))

    contribution_is_positive = (leader_stats["total_contrib"] > 0).astype(float)
    leader_stats.insert(
        len(leader_stats.columns),
        "leadership_score",
        (
            leader_stats["norm_contrib"] * 0.50
            + leader_stats["consistency"].fillna(0) * 0.25
            + np.clip(leader_stats["rel_momentum"].fillna(0), 0, 1) * 0.25
        )
        * contribution_is_positive,
    )

    top_leaders = leader_stats.nlargest(top_n, "leadership_score")

    # === LEADERSHIP DECAY ===
    decay_window = min(20, lookback // 2)
    decay_data = []
    leader_syms = set(top_leaders["symbol"])

    # Compute slopes for all leader_stats stocks to get relative normalization
    all_slopes = {}
    for sym in leader_syms:
        sym_data = daily_value[daily_value["symbol"] == sym].sort_values("date").tail(decay_window)
        if len(sym_data) < 5:
            continue
        days = np.arange(len(sym_data))
        if days.std() > 0:
            slope = np.polyfit(days, sym_data["contribution"].values, 1)[0]
        else:
            slope = 0
        all_slopes[sym] = slope

    slope_vals = np.array(list(all_slopes.values()))
    slope_min, slope_max = slope_vals.min(), slope_vals.max()
    slope_range = slope_max - slope_min if slope_max != slope_min else 1

    for _, row in top_leaders.iterrows():
        sym = row["symbol"]
        sym_data = daily_value[daily_value["symbol"] == sym].sort_values("date").tail(decay_window).copy()
        if len(sym_data) < 5:
            continue

        # Contribution trend: 0 = best in class, 1 = worst in class
        raw_slope = all_slopes.get(sym, 0)
        slope_norm = 1 - (raw_slope - slope_min) / slope_range
        slope_norm = np.clip(slope_norm, 0, 1)

        # Contribution volatility: higher = less stable = more decay
        contrib_vals = sym_data["contribution"].values
        contrib_vol = contrib_vals.std()
        contrib_mean_abs = np.abs(contrib_vals).mean()
        vol_norm = np.clip(contrib_vol / (contrib_mean_abs * 4 + 1e-10), 0, 1)

        corr_pen = np.clip(1 - row["consistency"], 0, 1)

        recent_mom = sym_data["close"].iloc[-1] / sym_data["close"].iloc[0] - 1 if len(sym_data) >= 5 else 0
        rs_recent = recent_mom - idx_20d_return
        rs_pen = 1.0 if rs_recent < -0.1 else (rs_recent + 0.1) / 0.2 if rs_recent < 0.1 else 0
        rs_pen = np.clip(rs_pen, 0, 1)

        decay_score = 0.30 * slope_norm + 0.30 * vol_norm + 0.20 * corr_pen + 0.20 * rs_pen
        decay_score = np.clip(decay_score, 0, 1)

        if decay_score < 0.3:
            grade = "🟢"
        elif decay_score < 0.5:
            grade = "🟡"
        else:
            grade = "🔴"

        decay_data.append(
            {
                "symbol": sym,
                "decay_score": round(decay_score, 3),
                "contrib_trend": round(slope_norm, 2),
                "vol": round(vol_norm, 2),
                "corr_pen": round(corr_pen, 2),
                "rs_pen": round(rs_pen, 2),
                "grade": grade,
            }
        )

    decay_df = pd.DataFrame(decay_data)

    idx_start = df_idx.sort_values("date").iloc[0]
    idx_end = df_idx.sort_values("date").iloc[-1]
    total_idx_return = (idx_end["idx_close"] / idx_start["idx_close"] - 1) * 100

    print(
        f"\nKỳ phân tích: {daily_value['date'].min().strftime('%Y-%m-%d')} → {daily_value['date'].max().strftime('%Y-%m-%d')} ({lookback} phiên)"  # noqa: E501 - chuỗi nội dung dài (i18n/SQL)
    )
    print(f"VNINDEX: {idx_start['idx_close']:.1f} → {idx_end['idx_close']:.1f} ({total_idx_return:+.1f}%)")
    print(f"Số mã đủ điều kiện (value >= {min_value / 1e9:.0f}B VND): {len(leader_stats)}")

    def role_label(corr):
        if corr > 0.6:
            return "DAN DAT"
        if corr > 0.3:
            return "THEO XU HUONG"
        if corr > 0:
            return "YEU"
        return "NGHICH PHA"

    print(
        f"\n{'Xếp hạng':<8} {'Mã':<6} {'Giá':>8} {'% Giá trị':>9} {'Đóng góp':>9} {'Tương quan':>10} {'RS 20D':>7} {'Vai trò':>15}"  # noqa: E501 - chuỗi nội dung dài (i18n/SQL)
    )
    print("-" * 68)

    for i, (_, row) in enumerate(top_leaders.iterrows(), 1):
        close_val = row["close"]
        share = row.get("market_share", row["weight_pct"])
        corr = row["consistency"]
        label = role_label(corr)
        print(
            f"{f'#{i}':<8} {row['symbol']:<6} {close_val:>8.0f} {share:>7.2f}% {row['total_contrib']:>+8.4f} {corr:>+8.3f}  {row['rel_momentum']:>+6.1%} {label:>14}"  # noqa: E501 - chuỗi nội dung dài (i18n/SQL)
        )

    top_n_contrib = top_leaders["total_contrib"].abs().sum()
    total_contrib = leader_stats["total_contrib"].abs().sum()
    concentration = top_n_contrib / total_contrib * 100 if total_contrib != 0 else 0

    print("-" * 68)
    print(f"Tổng: Top {top_n} mã chiếm {concentration:.1f}% tổng ảnh hưởng chỉ số")

    if concentration > 70:
        print("⚠️ Cảnh báo: Leadership siêu tập trung — rủi ro đảo chiều cao nếu nhóm trụ yếu")
    elif concentration > 50:
        print("🟡 Leadership tập trung — cần theo dõi độ lan tỏa")
    else:
        print("🟢 Leadership phân bổ đều — thị trường khỏe")

    high_risk = decay_df[decay_df["decay_score"] >= 0.5]
    watch = decay_df[(decay_df["decay_score"] >= 0.3) & (decay_df["decay_score"] < 0.5)]
    print("\n——— SUY GIẢM DẪN DẮT (Leadership Decay) ———")
    print(
        f"{'Mã':<6} {'Điểm suy giảm':>13} {'Xu hướng CG':>11} {'BĐộng đgóp':>11} {'Trừ TQuan':>10} {'Trừ RS':>7} {'Phân loại':>9}"  # noqa: E501 - chuỗi nội dung dài (i18n/SQL)
    )
    print("-" * 68)
    for _, row in decay_df.iterrows():
        print(
            f"{row['symbol']:<6} {row['decay_score']:>10.3f}  {row['contrib_trend']:>8.2f}  {row['vol']:>8.2f}  {row['corr_pen']:>8.2f}  {row['rs_pen']:>5.2f}  {row['grade']:>8}"  # noqa: E501 - chuỗi nội dung dài (i18n/SQL)
        )

    print("-" * 68)
    if len(high_risk) > 0:
        print(f"🔴 Nguy cơ mất trụ (score >= 0.5): {', '.join(high_risk['symbol'])}")
    if len(watch) > 0:
        print(f"🟡 Theo dõi (0.3-0.5): {', '.join(watch['symbol'])}")
    if len(high_risk) == 0 and len(watch) == 0:
        print("🟢 Tất cả trụ đang ổn định")

    total_market_turnover_bn = total_market_value / 1e9 if total_market_value > 0 else 0
    return {
        "top_leaders": top_leaders.to_dict("records"),
        "decay_analysis": decay_data,
        "concentration": round(concentration, 1),
        "idx_return": round(total_idx_return, 2),
        "total_symbols": len(leader_stats),
        "market_turnover_bn": round(total_market_turnover_bn, 0),
        "analysis_dates": {
            "start": daily_value["date"].min().strftime("%Y-%m-%d"),
            "end": daily_value["date"].max().strftime("%Y-%m-%d"),
        },
    }


if __name__ == "__main__":
    calculate_leadership()
