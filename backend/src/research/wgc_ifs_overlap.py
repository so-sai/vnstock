"""wgc_ifs_overlap.py — Gate O1: Overlap Diagnostic WGC ↔ IFS.

Câu hỏi gate (không phải "correlation bao nhiêu"):
  "Khi CẢ HAI measurement systems đã available tại cùng thời điểm PIT, chúng
   có chứa thông tin nhất quán về cùng underlying phenomenon hay không?"

Hai measurement systems ĐỘC LẬP, không merge:
  WGC = CB_GOLD_PURCHASES / GLOBAL / WGC_CB_BLOG   (blog-reported net purchases)
  IFS = IFS_GOLD_RESERVE_CHANGE / GLOBAL / IMF_IFS (change in official reserves)

THIẾT KẾ:
  - PIT-align theo publication_date ≤ t (resolve_pit của gold_h2_series).
  - Rolling evaluation dates (monthly grid): tại mỗi t, pair các observation
    month có CẢ HAI value published ≤ t. KHÔNG forward-fill qua tháng thiếu.
  - Hai view:
      1) final_observed : latest vintage mỗi series tại t  (measurement agreement)
      2) first_available: vintage đầu tiên mỗi obs công bố  (real-time signal)
  - Anomaly sensitivity: IFS_raw vs IFS_excl_source_anomaly (chỉ diagnostic,
    không đụng DB). Nếu correlation đổi mạnh → ghi rõ anomaly-sensitive.
  - Revision stability: với mỗi obs, first vs final value.

Gate O1 KHÔNG: fit conversion, regression, calibrate, composite CB signal,
đưa vào Gold v0.2, đánh giá Gold return. Chỉ trả lời WGC ≈ IFS?

Usage (từ backend/):
  python -X utf8 src/research/wgc_ifs_overlap.py --db data/gold_h2.db
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import numpy as np
from scipy import stats


# ── Sentinel v2.1 (Anchor Fix) ──────────────────────────────────────────────
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
    for p in [str(root_path / "backend" / "src"), str(root_path / "backend"), str(root_path)]:
        if p not in sys.path:
            sys.path.insert(0, p)
    return root_path


PROJECT_ROOT = _hydrate_path()
BACKEND = PROJECT_ROOT / "backend"
DATA_DIR = BACKEND / "data"
REPORT_DIR = DATA_DIR / "reports"

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except AttributeError, OSError, ValueError:
    pass

WGC_SERIES = "CB_GOLD_PURCHASES"
WGC_ENTITY = "GLOBAL"
WGC_SOURCE = "WGC_CB_BLOG"
IFS_SERIES = "IFS_GOLD_RESERVE_CHANGE"
IFS_ENTITY = "GLOBAL"
IFS_SOURCE = "IMF_IFS"

# Chống dependency vòng: tái dùng rule phân loại anomaly từ audit IFS.
# SOURCE_ANOMALY = spike 1-vintage (|value| > 500t chỉ xuất hiện 1 lần).
from src.research.ifs_gold_reserve_audit import (
    SPIKE_TONS,
)


def _rows(conn: sqlite3.Connection, series: str) -> list[tuple[str, str, float]]:
    """(observation_date, publication_date, value) sorted theo pub tăng dần."""
    source = WGC_SOURCE if series == WGC_SERIES else IFS_SOURCE
    return conn.execute(
        "SELECT observation_date, publication_date, value "
        "FROM gold_h2_series "
        "WHERE series=? AND entity=? "
        "  AND publication_date IS NOT NULL AND source=? "
        "ORDER BY observation_date, publication_date",
        (series, WGC_ENTITY, source),
    ).fetchall()


def latest_at(rows: list[tuple[str, str, float]], as_of: str) -> dict[str, tuple[str, float]]:
    """{obs: (pub, value)} — latest vintage có pub <= as_of (per obs)."""
    out: dict[str, tuple[str, float]] = {}
    for obs, pub, val in rows:
        if pub <= as_of:
            out[obs] = (pub, float(val))
    return out


def first_available(rows: list[tuple[str, str, float]]) -> dict[str, tuple[str, float]]:
    """{obs: (pub, value)} — vintage ĐẦU TIÊN mỗi obs (min pub)."""
    out: dict[str, tuple[str, float]] = {}
    for obs, pub, val in rows:
        if obs not in out or pub < out[obs][0]:
            out[obs] = (pub, float(val))
    return out


def is_source_anomaly(rows: list[tuple[str, str, float]], obs: str) -> bool:
    """Obs có ĐÚNG 1 vintage |value| > SPIKE_TONS (spike 1-vintage)?"""
    vals = [v for o, _, v in rows if o == obs]
    return sum(1 for v in vals if abs(v) > SPIKE_TONS) == 1


def paired_frame(
    wgc: dict[str, tuple[str, float]],
    ifs: dict[str, tuple[str, float]],
) -> list[dict]:
    """Pair các obs có CẢ HAI measurement tại cùng mốc PIT.

    Không forward-fill qua tháng thiếu — obs nào thiếu 1 bên thì bỏ khỏi pair.
    """
    out = []
    for obs in sorted(set(wgc) & set(ifs)):
        wpub, wval = wgc[obs]
        ipub, ival = ifs[obs]
        out.append(
            {
                "obs": obs,
                "wgc": wval,
                "ifs": ival,
                "wgc_pub": wpub,
                "ifs_pub": ipub,
            }
        )
    return out


def _pearson(a: list[float], b: list[float]) -> float | None:
    if len(a) < 3:
        return None
    r, _ = stats.pearsonr(a, b)
    if np.isnan(r):
        return None
    return round(float(r), 4)


def _spearman(a: list[float], b: list[float]) -> float | None:
    if len(a) < 3:
        return None
    r, _ = stats.spearmanr(a, b)
    if np.isnan(r):
        return None
    return round(float(r), 4)


def diagnostics(pairs: list[dict]) -> dict:
    """Pearson/Spearman/MAE/median-AD/sign agreement/confusion matrix."""
    n = len(pairs)
    res: dict = {"n": n}
    if n == 0:
        return res
    a = [p["wgc"] for p in pairs]
    b = [p["ifs"] for p in pairs]
    res["pearson"] = _pearson(a, b)
    res["spearman"] = _spearman(a, b)
    res["mae"] = round(float(np.mean(np.abs(np.array(a) - np.array(b)))), 2)
    res["median_ad"] = round(float(np.median(np.abs(np.array(a) - np.array(b)))), 2)
    # sign agreement + confusion (WGC làm reference)
    tp = fp = fn = tn = 0
    for p in pairs:
        ws = 1 if p["wgc"] > 0 else (-1 if p["wgc"] < 0 else 0)
        is_ = 1 if p["ifs"] > 0 else (-1 if p["ifs"] < 0 else 0)
        if ws > 0 and is_ > 0:
            tp += 1
        elif ws < 0 and is_ < 0:
            tn += 1
        elif ws > 0 and is_ < 0:
            fn += 1
        elif ws < 0 and is_ > 0:
            fp += 1
    res["sign_agreement"] = round((tp + tn) / n, 4) if n else None
    res["confusion"] = {"TP": tp, "FP": fp, "FN": fn, "TN": tn}
    return res


def rolling_grid(
    conn: sqlite3.Connection,
    exclude_anomaly: bool = False,
) -> list[dict]:
    """Monthly evaluation grid → {t, n, pearson, spearman, sign_agreement, mae}."""
    wrows = _rows(conn, WGC_SERIES)
    irows = _rows(conn, IFS_SERIES)
    wobs = set(o for o, _, _ in wrows)
    iobs = set(o for o, _, _ in irows)
    obs_all = wobs & iobs
    if not obs_all:
        return []
    # IFS obs bị loại nếu exclude_anomaly
    if exclude_anomaly:
        obs_all = {o for o in obs_all if not is_source_anomaly(irows, o)}

    t_start = min(p for _, p, _ in wrows)
    t_end = max(p for _, p, _ in irows)
    # monthly grid: ngày 5 mỗi tháng từ t_start đến t_end
    marks: list[str] = []
    y, m = int(t_start[:4]), int(t_start[5:7])
    while f"{y:04d}-{m:02d}-05" <= t_end:
        marks.append(f"{y:04d}-{m:02d}-05")
        m += 1
        if m > 12:
            m = 1
            y += 1

    out = []
    for t in marks:
        wgc = latest_at(wrows, t)
        ifs = latest_at(irows, t)
        pairs = paired_frame(wgc, ifs)
        if not pairs:
            continue
        diag = diagnostics(pairs)
        out.append({"t": t, **diag})
    return out


def detect_transient(grid: list[dict]) -> list[dict]:
    """Flag các mốc rolling bất thường = revision-transient.

    Một mốc t được coi là transient nếu correlation khác biệt MẠNH so với trạng
    thái ổn định quanh nó (trung vị của các mốc khác) — dấu hiệu revision tạm
    thời (vd file Oct2024 nhiễm nhiều obs cùng lúc). Chỉ FLAG, không sửa.

    Yêu cầu n >= 5 để tránh false-positive từ sample nhỏ (n=4 với 1 obs lệch
    do lag khác biệt không phải transient).
    """
    if len(grid) < 4:
        return []
    r_vals = [r.get("pearson") for r in grid if r.get("pearson") is not None]
    if not r_vals:
        return []
    median_r = float(np.median(r_vals))
    mad = float(np.median(np.abs(np.array(r_vals) - median_r))) or 1e-9
    flags = []
    for r in grid:
        if r.get("pearson") is None or r.get("n", 0) < 5:
            continue
        if abs(r["pearson"] - median_r) > max(3.0 * mad, 0.3):
            flags.append(
                {
                    "t": r["t"],
                    "pearson": r["pearson"],
                    "sign_agreement": r.get("sign_agreement"),
                    "n": r["n"],
                    "median_r": round(median_r, 3),
                }
            )
    return flags


def final_observed_view(conn: sqlite3.Connection, as_of: str, exclude_anomaly: bool = False) -> list[dict]:
    """View 1: latest known vintage mỗi series tại t (measurement agreement)."""
    wrows = _rows(conn, WGC_SERIES)
    irows = _rows(conn, IFS_SERIES)
    wgc = latest_at(wrows, as_of)
    ifs = latest_at(irows, as_of)
    if exclude_anomaly:
        obs_all = set(wgc) & set(ifs)
        obs_all = {o for o in obs_all if not is_source_anomaly(irows, o)}
        wgc = {o: v for o, v in wgc.items() if o in obs_all}
        ifs = {o: v for o, v in ifs.items() if o in obs_all}
    return paired_frame(wgc, ifs)


def first_available_view(conn: sqlite3.Connection) -> list[dict]:
    """View 2: vintage ĐẦU TIÊN mỗi obs công bố (real-time signal)."""
    wrows = _rows(conn, WGC_SERIES)
    irows = _rows(conn, IFS_SERIES)
    return paired_frame(first_available(wrows), first_available(irows))


def coverage(conn: sqlite3.Connection) -> dict:
    """Overlap months / WGC-only / IFS-only / theo năm."""
    wobs = set(o for o, _, _ in _rows(conn, WGC_SERIES))
    iobs = set(o for o, _, _ in _rows(conn, IFS_SERIES))
    inter = wobs & iobs
    wonly = wobs - iobs
    ionly = iobs - wobs
    by_year: dict[str, dict] = {}
    for o in sorted(inter | wonly | ionly):
        y = o[:4]
        d = by_year.setdefault(y, {"overlap": 0, "wgc_only": 0, "ifs_only": 0})
        if o in inter:
            d["overlap"] += 1
        elif o in wonly:
            d["wgc_only"] += 1
        else:
            d["ifs_only"] += 1
    return {
        "overlap_months": len(inter),
        "wgc_only": len(wonly),
        "ifs_only": len(ionly),
        "by_year": by_year,
    }


def revision_stability(conn: sqlite3.Connection) -> dict:
    """Với mỗi IFS obs: first vs final value (WGC blog 1 vintage/obs)."""
    rows = _rows(conn, IFS_SERIES)
    by_obs: dict[str, list[tuple[str, float]]] = {}
    for obs, pub, val in rows:
        by_obs.setdefault(obs, []).append((pub, float(val)))
    deltas = []
    sign_flips = 0
    for obs, vs in by_obs.items():
        if len(vs) < 2:
            continue
        first = vs[0][1]
        final = vs[-1][1]
        deltas.append(abs(final - first))
        if (first > 0) != (final > 0):
            sign_flips += 1
    if not deltas:
        return {"n_obs": 0}
    return {
        "n_obs_with_vintage": len(deltas),
        "mean_abs_delta": round(float(np.mean(deltas)), 2),
        "median_abs_delta": round(float(np.median(deltas)), 2),
        "max_abs_delta": round(float(np.max(deltas)), 2),
        "sign_flips": sign_flips,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Gate O1: WGC ↔ IFS overlap diagnostic")
    parser.add_argument("--db", default=str(DATA_DIR / "gold_h2.db"))
    parser.add_argument("--as-of", default="2026-08-31", help="mốc final-observed")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)

    print("\n  GATE O1 — WGC ↔ IFS OVERLAP DIAGNOSTIC")
    print(f"  WGC={WGC_SERIES}/{WGC_ENTITY}/{WGC_SOURCE}")
    print(f"  IFS={IFS_SERIES}/{IFS_ENTITY}/{IFS_SOURCE}")

    # A. Coverage
    cov = coverage(conn)
    print("\n  [A] COVERAGE")
    print(f"      overlap months = {cov['overlap_months']}")
    print(f"      WGC-only       = {cov['wgc_only']}")
    print(f"      IFS-only       = {cov['ifs_only']} (IFS 2001-2026, kỳ vọng lớn)")
    print(f"      theo năm       = {cov['by_year']}")

    # C. Revision stability (IFS)
    rev = revision_stability(conn)
    print(f"\n  [D] IFS REVISION STABILITY ({rev['n_obs_with_vintage']} obs ≥2 vintages)")
    print(
        f"      mean|first-final|={rev['mean_abs_delta']} median={rev['median_abs_delta']} "
        f"max={rev['max_abs_delta']} sign_flips={rev['sign_flips']}"
    )

    # View 1: final observed (measurement agreement)
    print(f"\n  [B1] VIEW 1 — FINAL OBSERVED (as_of {args.as_of})")
    for label, excl in (("IFS_raw", False), ("IFS_excl_source_anomaly", True)):
        pairs = final_observed_view(conn, args.as_of, exclude_anomaly=excl)
        d = diagnostics(pairs)
        print(
            f"      {label:<28s} n={d['n']} pearson={d.get('pearson')} "
            f"spearman={d.get('spearman')} sign_agree={d.get('sign_agreement')} "
            f"MAE={d.get('mae')} conf={d.get('confusion')}"
        )

    # View 2: first available (real-time)
    pairs = first_available_view(conn)
    d = diagnostics(pairs)
    print("\n  [B2] VIEW 2 — FIRST AVAILABLE (real-time signal)")
    print(
        f"      n={d['n']} pearson={d.get('pearson')} spearman={d.get('spearman')} "
        f"sign_agree={d.get('sign_agreement')} MAE={d.get('mae')} conf={d.get('confusion')}"
    )

    # Rolling grid: anomaly sensitivity theo thời gian
    print("\n  [B3] ROLLING GRID (monthly) — anomaly sensitivity")
    raw = rolling_grid(conn, exclude_anomaly=False)
    excl = rolling_grid(conn, exclude_anomaly=True)
    by_t = {r["t"]: r for r in excl}
    print(f"      {'t':<12} {'n':>3} {'r_raw':>8} {'r_excl':>8} {'s_raw':>6} {'s_excl':>6}")
    for r in raw:
        e = by_t.get(r["t"])
        r_raw = r.get("pearson")
        r_excl = e.get("pearson") if e else None
        s_raw = r.get("sign_agreement")
        s_excl = e.get("sign_agreement") if e else None
        print(
            f"      {r['t']:<12} {r['n']:>3} "
            f"{r_raw if r_raw is not None else '—':>8} "
            f"{r_excl if r_excl is not None else '—':>8} "
            f"{s_raw if s_raw is not None else '—':>6} "
            f"{s_excl if s_excl is not None else '—':>6}"
        )

    # Anomaly verdict
    d_raw = diagnostics(final_observed_view(conn, args.as_of, exclude_anomaly=False))
    d_excl = diagnostics(final_observed_view(conn, args.as_of, exclude_anomaly=True))
    if d_raw.get("pearson") is not None and d_excl.get("pearson") is not None:
        delta = abs(d_raw["pearson"] - d_excl["pearson"])
        verdict = "ANOMALY-SENSITIVE" if delta > 0.15 else "stable"
        print(f"\n  [VERDICT] correlation delta raw→excl = {delta:.3f} → {verdict}")

    # Revision-transient (rolling): mốt có correlation lệch mạnh so median
    trans = detect_transient(raw)
    if trans:
        print("\n  [REVISION-TRANSIENT] mốc rolling lệch mạnh so median (revision tạm thời):")
        for tr in trans:
            print(
                f"      {tr['t']} pearson={tr['pearson']:.3f} "
                f"sign_agree={tr['sign_agreement']} n={tr['n']} "
                f"(median_r={tr['median_r']})"
            )
    else:
        print("\n  [REVISION-TRANSIENT] không có mốc rolling lệch mạnh — stable.")

    conn.close()


if __name__ == "__main__":
    main()
