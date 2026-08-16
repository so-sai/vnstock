"""ifs_gold_reserve_audit.py — Audit PIT coverage & revision của IFS vintage ladder.

Chạy trên gold_h2.db (đã ingest bằng ifs_gold_reserve_adapter.py). Trả về báo cáo:

  1. COVERAGE 2021-2026: với mỗi observation month, số vintage đã công bố
     (tức số file IFS có pub <= tháng sau) và publication lag (tháng công bố
     đầu tiên cách observation bao nhiêu tháng).
  2. REVISION: magnitude revision từng obs = max|final - each vintage|; nêu rõ
     obs nào có revision bất thường (candidate anomaly — cần xem lại nguồn).
  3. PIT USABILITY: số obs có publication <= t cho từng mốc t trong 2021-2026.

Chỉ đọc DB đã ingest — KHÔNG đọc lại xlsx, KHÔNG network. Idempotent.

ANOMALY ĐÃ BIẾT (giữ nguyên trong DB — PIT trung thực, chỉ flag, không tự loại):
  - pub=2024-10-03 (file Oct2024): obs 2024-07-31 = -2349.18t do Russian Federation
    ghi -2335.85t ở cột 2024-07 (mọi file khác ''/0). Data error trong nguồn.
  - pub=2024-10-03: obs 2024-05/06/10 tương tự (Nga ghi âm lớn) — 1-vintage spike.
  - obs 2015-05/06 = ~638t nhất quán mọi vintage (không phải error, dữ liệu thật).
  - obs 2002-2019: first_pub=2020-01-09 (ladder bắt đầu 2020) — revision lớn do
    so sánh giá trị revised 2020 vs 2026, KHÔNG phải PIT thật của thời đó.

Usage (từ project root):
  python -X utf8 backend/src/research/ifs_gold_reserve_audit.py --db backend/data/gold_h2.db
  python -X utf8 backend/src/research/ifs_gold_reserve_audit.py --db x.db --csv backend/data/reports/ifs_audit.csv
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from pathlib import Path


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

SERIES = "IFS_GOLD_RESERVE_CHANGE"
ENTITY = "GLOBAL"

# Ngưỡng magnitude bất thường: tổng thay đổi dự trữ toàn cầu thường <200t/tháng.
# |value| > SPIKE_TONS trong ĐÚNG 1 vintage của một obs → nghi data error nguồn.
SPIKE_TONS = 500.0

# Nhãn anomaly (chỉ FLAG, KHÔNG sửa dữ liệu):
#   SOURCE_ANOMALY  — spike 1-vintage (giá trị cực lớn chỉ xuất hiện 1 lần).
#   REVISION_LARGE  — revision giữa các vintage > 20t (không nhất thiết là lỗi).
ANOM_SOURCE = "SOURCE_ANOMALY"
ANOM_REVISION = "REVISION_LARGE"


def _month_shift(ymd: str, delta: int) -> str:
    """Cộng/trừ tháng cho 'YYYY-MM-DD' (giữ nguyên day)."""
    y, m, d = (int(x) for x in ymd.split("-"))
    total = y * 12 + (m - 1) + delta
    return f"{total // 12:04d}-{total % 12 + 1:02d}-{d:02d}"


def _classify_anomaly(vintages: list[tuple[str, float]], max_abs: float) -> str:
    """SOURCE_ANOMALY nếu obs có ĐÚNG 1 vintage |value| > SPIKE_TONS.

    Spike 1-vintage = dữ liệu khác thường chỉ tồn tại trong một snapshot nguồn
    (vd file Oct2024 ghi Russian Federation -2335.85t cho 2024-07); các vintage
    khác đều ''/0. Giá trị nhất quán nhiều vintage (vd 2015-06 ~638t) KHÔNG được
    gắn SOURCE_ANOMALY dù magnitude lớn — đó là dữ liệu thật.
    """
    n_spike = sum(1 for _, v in vintages if abs(v) > SPIKE_TONS)
    if n_spike == 1 and max_abs > 20.0:
        return ANOM_SOURCE
    return ANOM_REVISION


def audit(conn: sqlite3.Connection) -> dict:
    """Chạy toàn bộ audit → dict báo cáo."""
    rows = conn.execute(
        "SELECT observation_date, publication_date, value FROM gold_h2_series "
        "WHERE series=? AND entity=? AND publication_date IS NOT NULL "
        "ORDER BY observation_date, publication_date",
        (SERIES, ENTITY),
    ).fetchall()

    # obs → [(pub, value)]  (đã sort theo pub tăng dần)
    by_obs: dict[str, list[tuple[str, float]]] = {}
    for obs, pub, val in rows:
        by_obs.setdefault(obs, []).append((pub, float(val)))

    # ── 1. Coverage & lag ──────────────────────────────────────────────────
    coverage: dict[str, dict] = {}
    for obs, vintages in by_obs.items():
        first_pub = vintages[0][0]
        lag_months = (int(first_pub[:4]) * 12 + int(first_pub[5:7])) - (int(obs[:4]) * 12 + int(obs[5:7]))
        coverage[obs] = {
            "first_pub": first_pub,
            "lag_months": lag_months,
            "n_vintages": len(vintages),
        }

    # ── 2. Revision magnitude ──────────────────────────────────────────────
    revisions: dict[str, float] = {}
    anomalies: list[dict] = []
    for obs, vintages in by_obs.items():
        if len(vintages) < 2:
            revisions[obs] = 0.0
            continue
        final = vintages[-1][1]
        max_abs = max(abs(v - final) for _, v in vintages)
        revisions[obs] = round(max_abs, 2)
        # anomaly heuristic: revision > 20t (rất lớn so với magnitude thường) —
        # chỉ là CANDIDATE, không tự động loại dữ liệu.
        if max_abs > 20.0:
            anomalies.append(
                {
                    "obs": obs,
                    "first_pub": vintages[0][0],
                    "last_pub": vintages[-1][0],
                    "first_value": round(vintages[0][1], 2),
                    "final_value": round(final, 2),
                    "max_revision": round(max_abs, 2),
                    "anomaly_type": _classify_anomaly(vintages, max_abs),
                    "spike_vintages": [p for p, v in vintages if abs(v) > SPIKE_TONS],
                }
            )

    # ── 3. PIT usability ───────────────────────────────────────────────────
    pit_marks = [
        "2021-01-05",
        "2022-01-05",
        "2023-01-05",
        "2024-01-05",
        "2025-01-05",
        "2025-07-05",
        "2026-01-05",
        "2026-07-05",
    ]
    pit_usable: dict[str, int] = {}
    for t in pit_marks:
        n = sum(1 for vintages in by_obs.values() if any(p <= t for p, _ in vintages))
        pit_usable[t] = n

    return {
        "rows": rows,
        "by_obs": by_obs,
        "coverage": coverage,
        "revisions": revisions,
        "anomalies": anomalies,
        "pit_usable": pit_usable,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="IFS vintage ladder PIT audit")
    parser.add_argument("--db", default=str(DATA_DIR / "gold_h2.db"))
    parser.add_argument("--csv", default=str(REPORT_DIR / "ifs_gold_reserve_audit.csv"))
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    res = audit(conn)
    conn.close()

    print(f"\n  IFS GOLD RESERVE — PIT AUDIT ({args.db})")
    print(f"  series={SERIES} entity={ENTITY}")

    # coverage
    cov = res["coverage"]
    n_obs = len(cov)
    obs_2021p = {o for o in cov if o >= "2021-01-01"}
    print(f"\n  [1] COVERAGE: {n_obs} obs ({len(obs_2021p)} obs từ 2021)")
    if cov:
        first = min(cov)
        last = max(cov)
        print(f"      range: {first} .. {last}")
    lag = [c["lag_months"] for c in cov.values()]
    n1 = sum(1 for x in lag if x == 1)
    n2 = sum(1 for x in lag if x == 2)
    n3 = sum(1 for x in lag if x >= 3)
    print(f"      first-publication lag: lag1={n1} lag2={n2} lag>=3={n3}")

    # revision
    print(f"\n  [2] REVISION (n_vintage>=2 obs): {sum(1 for v in res['by_obs'].values() if len(v) >= 2)}")
    big = {o: r for o, r in res["revisions"].items() if r > 5.0}
    print(f"      obs có max-revision >5t: {len(big)}")
    if res["anomalies"]:
        print(f"\n  [ANOMALY CANDIDATES] revision > 20t ({len(res['anomalies'])}):")
        for a in res["anomalies"]:
            tag = f"[{a['anomaly_type']}]"
            extra = f" spike@{a['spike_vintages']}" if a["spike_vintages"] else ""
            print(
                f"    - {a['obs']} first={a['first_value']:+.2f} "
                f"final={a['final_value']:+.2f} max_delta={a['max_revision']:.2f} "
                f"({a['first_pub']} -> {a['last_pub']}) {tag}{extra}"
            )

    # pit usability
    print("\n  [3] PIT USABILITY (obs đã công bố tại t):")
    for t, n in res["pit_usable"].items():
        print(f"      as_of {t}: {n} obs")

    # csv export
    Path(args.csv).parent.mkdir(parents=True, exist_ok=True)
    with open(args.csv, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "obs",
                "first_pub",
                "lag_months",
                "n_vintages",
                "first_value",
                "final_value",
                "max_revision",
                "anomaly_type",
            ],
        )
        w.writeheader()
        for obs in sorted(cov):
            vs = res["by_obs"][obs]
            atyp = next(
                (a["anomaly_type"] for a in res["anomalies"] if a["obs"] == obs),
                "",
            )
            w.writerow(
                {
                    "obs": obs,
                    "first_pub": cov[obs]["first_pub"],
                    "lag_months": cov[obs]["lag_months"],
                    "n_vintages": cov[obs]["n_vintages"],
                    "first_value": round(vs[0][1], 2),
                    "final_value": round(vs[-1][1], 2),
                    "max_revision": res["revisions"][obs],
                    "anomaly_type": atyp,
                }
            )
    print(f"\n  [OK] audit CSV -> {args.csv}")


if __name__ == "__main__":
    main()
