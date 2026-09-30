"""audit_veto_returns.py — Do hieu thuc nhan VETO/REDUCE cua CSI (read-only, khong ghi DB).

Cau hoi tra loi:
    Nhan VETO/REDUCE dang BAO VE von (forward return am / thap hon VNINDEX)
    hay dang Tao CHI PHI CO HOI (forward return duong / cao hon VNINDEX)?

Nguon (Zero-Hallucination — chi doc, khong suy doan):
    - backend/data/output/csi_history.json : {symbol, date, csi:{action, p_gain}, ...}
      (thuc te: 350 ban ghi, 10 ma, 35 phien 2026-07-31..2026-09-29 -> khong phai
       "20 ma/60 phien" nhu trong ban trinh bay; script bao cao dung pham vi co that)
    - backend/data/screener_cache.db (mode=ro): daily_ohlcv -> close phien + VNINDEX

Quy uoc tinh toan (de doc duoc, khong an):
    - Lich phien: cac ngay co trong daily_ohlcv (tat ca cac ma) — KHONG dung date('now'),
      nen khong phu thuộc system clock (ngay = du lieu trong DB).
    - Signal den tu JSON csi_history (khong co bang `csi_history` trong DB; cac cot
      daily_ohlcv la symbol/date/open/high/low/close/volume — khong co trade_date).
    - CHONG LOOK-AHEAD (mac dinh): entry = close phien T0+1 (signal chi biet SAU khi
      T0 dong cua); horizon h tinh TU entry: exit = entry + h phien.
      `--entry signal` chi dung de doi chieu loi nhan vao T0 (bao cao in ca 2).
    - Forward H: dung CUNG cap ngay cho cohort va benchmark:
        entry = close tai entry_date, exit = close tai target_date (= entry_pos + h)
        (neu ma khong co bar dung ngay -> lay close gan nhat khong sau ngay do, ghi ro)
      Thieu bat ky khon -> NO_DATA (khong uoc luong, khong sua loi).
    - Danh gia dua tren ALPHA (mean excess vs VNINDEX) + dong-kiem tuyet doi, khong dua
      tren return tuyet doi don thuan (ma am van co the thua chi so).
    - Khong du 60 phien phia truoc -> cot 60D = NO_DATA (khong ket luan gi ve 60D).
    - Bat thuong du lieu: phien co |daily return| > --anomaly-band (mac dinh 8% — vuot band
      +/-7% cua HOSE) -> cua so forward chua phien do duoc danh dau va duoc tinh rieng
      (cot clean) — tinh rieng cho ca 2 kieu entry, khong an thong so.

Chay:
    python backend/scripts/audit_veto_returns.py
    python backend/scripts/audit_veto_returns.py --entry signal --json out.json
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
from bisect import bisect_right
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CSI_PATH = PROJECT_ROOT / "backend" / "data" / "output" / "csi_history.json"
DB_PATH = PROJECT_ROOT / "backend" / "data" / "screener_cache.db"
ACTIONS = ("VETO", "REDUCE")
BENCHMARK = "VNINDEX"


def load_signals(path: Path) -> list[dict]:
    """Doc csi_history.json -> signal doc lac theo (symbol, date) va action in ACTIONS."""
    if not path.exists():
        raise FileNotFoundError(f"khong tim thay {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"csi_history phai la JSON list, nhan duoc {type(raw).__name__}")
    seen: dict[tuple[str, str], dict] = {}
    for rec in raw:
        symbol = rec.get("symbol")
        date = rec.get("date")
        action = (rec.get("csi") or {}).get("action")
        if not symbol or not date or action not in ACTIONS:
            continue
        key = (str(symbol), str(date)[:10])
        if key in seen:
            continue
        seen[key] = {
            "symbol": key[0],
            "date": key[1],
            "action": action,
            "p_gain": (rec.get("csi") or {}).get("p_gain"),
            "primary": (rec.get("attribution") or {}).get("primary"),
            "message": (rec.get("attribution") or {}).get("message"),
        }
    return sorted(seen.values(), key=lambda s: (s["date"], s["symbol"]))


def session_calendar(db: Path) -> list[str]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT DISTINCT date FROM daily_ohlcv ORDER BY date").fetchall()
    finally:
        con.close()
    return [r[0] for r in rows]


def load_closes(db: Path, symbols: set[str]) -> dict[str, tuple[list[str], list[float]]]:
    """{symbol: (dates_asc, closes)} — chi lay cac ma can thiet (giam I/O)."""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        query = "SELECT symbol, date, close FROM daily_ohlcv"
        params: tuple = ()
        if symbols:
            query += f" WHERE symbol IN ({','.join('?' * len(symbols))})"
            params = tuple(sorted(symbols))
        out: dict[str, tuple[list[str], list[float]]] = {}
        for symbol, date, close in con.execute(query, params):
            if close is None:
                continue
            dates, closes = out.setdefault(symbol, ([], []))
            dates.append(date)
            closes.append(float(close))
    finally:
        con.close()
    return dict(out)


def close_on_or_before(series: tuple[list[str], list[float]] | None, target: str) -> tuple[str, float] | None:
    """Close gan nhat khong sau target (bisect tren danh sach da sap xep)."""
    if series is None:
        return None
    dates, closes = series
    idx = bisect_right(dates, target) - 1
    if idx < 0:
        return None
    return dates[idx], closes[idx]


def session_pos(calendar: list[str], date: str) -> int:
    """Vi tri phien gan nhat khong sau date; -1 neu khong co."""
    return bisect_right(calendar, date) - 1


def breach_dates(series: tuple[list[str], list[float]] | None, band_pct: float) -> list[str]:
    """Cac phien co |daily return| > band_pct (mac dinh 8%, khat khe hon band +/-7% cua HOSE)
    = bat thuong du lieu — ganh manh co ngan xet corporate action chua dieu chinh;
    khong phai return thi truong that."""
    if series is None:
        return []
    dates, closes = series
    out: list[str] = []
    prev: float | None = None
    for date, close in zip(dates, closes):
        if prev is not None and prev > 0 and abs(close / prev - 1.0) * 100.0 > band_pct:
            out.append(date)
        prev = close
    return out


def row_forward(
    calendar: list[str],
    mine: tuple[list[str], list[float]] | None,
    bench: tuple[list[str], list[float]] | None,
    signal_date: str,
    horizons: list[int],
    breaches: list[str],
    entry_offset: int = 1,
) -> dict[str, dict | None]:
    """Tinh forward cho moi horizon tren dung cap entry/target_date. Thieu -> None.

    entry_offset (chong look-ahead bias):
      1 (mac dinh) = vao tai phien T0+1 — signal chi duoc biet SAU khi T0 dong cua,
                     nen khong the mua duoc tai close T0.
      0 (legacy)   = vao tai close phien T0 — chi dung de doi chieu anh huong.
    Horizon h tinh TU diem vao: exit = entry_pos + h phien (ca cohort va benchmark
    deu dung cung 1 cap ngay -> so sanh duoc).
    """
    pos = session_pos(calendar, signal_date)
    if pos < 0:
        return {str(h): None for h in horizons}
    entry_pos = pos + entry_offset
    if entry_pos >= len(calendar):
        return {str(h): None for h in horizons}
    entry_date = calendar[entry_pos]
    start = close_on_or_before(mine, entry_date)
    bstart = close_on_or_before(bench, entry_date)
    if start is None or bstart is None:
        return {str(h): None for h in horizons}

    out: dict[str, dict | None] = {}
    for h in sorted(horizons):
        target_pos = entry_pos + h
        if target_pos >= len(calendar):
            out[str(h)] = None
            continue
        target_date = calendar[target_pos]
        end = close_on_or_before(mine, target_date)
        bend = close_on_or_before(bench, target_date)
        if end is None or bend is None or start[1] <= 0 or bstart[1] <= 0:
            out[str(h)] = None
            continue
        hits = [b for b in breaches if entry_date < b <= target_date]
        out[str(h)] = {
            "entry_date": start[0],
            "entry": start[1],
            "exit_date": end[0],
            "exit": end[1],
            "target_date": target_date,
            "ret": (end[1] / start[1] - 1.0) * 100.0,
            "vnindex_ret": (bend[1] / bstart[1] - 1.0) * 100.0,
            "anomaly": hits,
        }
    return out


def mean(values: list[float]) -> float | None:
    return statistics.mean(values) if values else None


def median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def fmt(value: float | None) -> str:
    return "NO_DATA" if value is None else f"{value:+.2f}%"


def summarize(rows: list[dict], horizons: list[int]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for h in horizons:
        block: dict[str, dict] = {}
        groups = [(a, [r for r in rows if r["action"] == a]) for a in ACTIONS]
        groups.append(("ALL", rows))
        for label, group in groups:
            rets = [r[f"{h}D"] for r in group if r[f"{h}D"] is not None]
            base = [r[f"VNINDEX_{h}D"] for r in group if r[f"VNINDEX_{h}D"] is not None]
            excess = [r[f"{h}D_excess"] for r in group if r[f"{h}D_excess"] is not None]
            clean = [r for r in group if not r[f"{h}D_anomaly"]]
            c_rets = [r[f"{h}D"] for r in clean if r[f"{h}D"] is not None]
            c_excess = [r[f"{h}D_excess"] for r in clean if r[f"{h}D_excess"] is not None]
            block[label] = {
                "n": len(group),
                "cover": len(rets),
                "mean_ret": mean(rets),
                "median_ret": median(rets),
                "mean_vnindex": mean(base),
                "mean_excess": mean(excess),
                "neg_rate": (sum(1 for x in rets if x < 0) / len(rets) * 100.0) if rets else None,
                "beat_rate": (sum(1 for x in excess if x > 0) / len(excess) * 100.0) if excess else None,
                "anomaly_rows": sum(1 for r in group if r[f"{h}D_anomaly"]),
                "n_clean": len(clean),
                "cover_clean": len(c_rets),
                "mean_ret_clean": mean(c_rets),
                "mean_excess_clean": mean(c_excess),
            }
        out[str(h)] = block
    return out


def audit(signals: list[dict], db: Path, horizons: list[int], band_pct: float = 8.0, entry_offset: int = 1) -> dict:
    calendar = session_calendar(db)
    if not calendar:
        raise RuntimeError("daily_ohlcv rong -> khong co lich phien")
    symbols = {s["symbol"] for s in signals}
    closes = load_closes(db, symbols | {BENCHMARK})
    bench = closes.get(BENCHMARK)
    breaches = {sym: breach_dates(closes.get(sym), band_pct) for sym in symbols}

    rows: list[dict] = []
    for sig in signals:
        fw = row_forward(
            calendar,
            closes.get(sig["symbol"]),
            bench,
            sig["date"],
            horizons,
            breaches.get(sig["symbol"], []),
            entry_offset=entry_offset,
        )
        entry: dict = {"symbol": sig["symbol"], "date": sig["date"], "action": sig["action"], "p_gain": sig["p_gain"]}
        for h in horizons:
            cell = fw[str(h)]
            entry[f"{h}D"] = cell["ret"] if cell else None
            entry[f"VNINDEX_{h}D"] = cell["vnindex_ret"] if cell else None
            entry[f"{h}D_excess"] = (cell["ret"] - cell["vnindex_ret"]) if cell else None
            entry[f"{h}D_window"] = f"{cell['entry_date']}->{cell['target_date']}" if cell else None
            entry[f"{h}D_anomaly"] = cell["anomaly"] if cell else []
        rows.append(entry)

    by_symbol: dict[str, dict] = {}
    for sym in sorted(symbols):
        group = [r for r in rows if r["symbol"] == sym]
        entry = {
            "signals": len(group),
            "action": group[0]["action"] if group else None,
            "first": group[0]["date"] if group else None,
            "last": group[-1]["date"] if group else None,
        }
        for h in horizons:
            rets = [r[f"{h}D"] for r in group if r[f"{h}D"] is not None]
            entry[f"{h}D_mean"] = mean(rets)
            entry[f"{h}D_cover"] = len(rets)
        by_symbol[sym] = entry

    attribution: dict[str, dict] = {}
    for sig in signals:
        key = str(sig.get("primary") or "KHONG_RO")
        blk = attribution.setdefault(key, {"VETO": 0, "REDUCE": 0, "total": 0, "message": sig.get("message")})
        blk[sig["action"]] += 1
        blk["total"] += 1

    return {
        "rows": rows,
        "summary": summarize(rows, horizons),
        "by_symbol": by_symbol,
        "attribution": attribution,
        "n_signals": len(signals),
        "n_symbols": len(symbols),
        "date_min": signals[0]["date"] if signals else None,
        "date_max": signals[-1]["date"] if signals else None,
        "sessions": len(calendar),
        "calendar_min": calendar[0],
        "calendar_max": calendar[-1],
        "horizons": horizons,
        "band_pct": band_pct,
        "entry_offset": entry_offset,
        "entry_mode": "T0+1 (khong look-ahead)" if entry_offset == 1 else "close T0 (LEGACY, co look-ahead)",
        "breaches": {sym: dates for sym, dates in sorted(breaches.items()) if dates},
    }


def classify(mean_ret: float | None, mean_excess: float | None) -> str:
    """Ma tran 2x2 trung thuc: dong lenh nham hay lam viec, va so voi chi so."""
    if mean_ret is None or mean_excess is None:
        return "NO_DATA — khong ket luan"
    lo = mean_ret < 0
    under = mean_excess < 0
    if lo and under:
        return f"BAO VE VON co dieu kien (tranh lo {abs(mean_ret):.2f}pp NHUNG thua chi so {abs(mean_excess):.2f}pp)"
    if lo and not under:
        return f"BAO VE VON (tranh lo {abs(mean_ret):.2f}pp va thang chi so {abs(mean_excess):.2f}pp)"
    if not lo and under:
        return f"CHI PHI CO HOI (lai {mean_ret:.2f}pp NHUNG thua chi so {abs(mean_excess):.2f}pp)"
    return f"TAO GIA TRI (lai {mean_ret:.2f}pp va thang chi so {mean_excess:.2f}pp)"


def report(result: dict, horizons: list[int]) -> str:
    lines: list[str] = []
    lines.append("=" * 78)
    lines.append("  AUDIT VETO/REDUCE — HIEU THUC THUC TE (read-only)")
    lines.append("=" * 78)
    lines.append(
        f"  Signals: {result['n_signals']} | ma doc lac: {result['n_symbols']} | {result['date_min']}..{result['date_max']}"
    )
    lines.append(f"  Lich phien DB: {result['sessions']} phien {result['calendar_min']}..{result['calendar_max']}")
    lines.append(
        f"  Entry mode: {result.get('entry_mode', 'T0+1 (khong look-ahead)')} — horizon tinh TU diem vao, "
        "ca cohort va VNINDEX dung cung cap ngay"
    )
    if result["n_symbols"] < 20:
        lines.append(
            f"  [CANH BAO] chi co {result['n_symbols']} ma (khong phai 20 ma/60 phien nhu ban trinh bay) "
            "-> ket luan chi ap dung cho tap ma governor dang theo doi"
        )
    breaches = result.get("breaches") or {}
    if breaches:
        lines.append(
            f"  [DU LIEU BAT THUONG] co phien co |daily return| > nguong {result['band_pct']}% "
            "(mac dinh 8% — vuot band +/-7% cua HOSE, co the la corporate action chua dieu chinh) tai:"
        )
        for sym, dates in breaches.items():
            lines.append(f"      {sym}: {', '.join(dates)}")
        lines.append("  -> cot '_clean' loai cac cua so forward chua phien bat thuong nay (tinh cho ca 2 kieu entry)")
    lines.append("-" * 78)
    for h in horizons:
        all_row = result["summary"][str(h)]["ALL"]
        neg = "NO_DATA" if all_row["neg_rate"] is None else f"{all_row['neg_rate']:.1f}%"
        lines.append(
            f"  [{h}D] cover {all_row['cover']}/{all_row['n']} | mean {fmt(all_row['mean_ret'])} "
            f"| VNINDEX {fmt(all_row['mean_vnindex'])} | excess {fmt(all_row['mean_excess'])} | so dong am {neg}"
        )
        lines.append(
            f"        [clean] cover {all_row['cover_clean']}/{all_row['n_clean']} "
            f"| mean {fmt(all_row['mean_ret_clean'])} | excess {fmt(all_row['mean_excess_clean'])} "
            f"| dong bat thuong {all_row['anomaly_rows']}"
        )
        for action in ACTIONS:
            row = result["summary"][str(h)][action]
            beat = "NO_DATA" if row["beat_rate"] is None else f"{row['beat_rate']:.0f}%"
            lines.append(
                f"        {action:6s} n={row['n']:3d} cover={row['cover']:3d} "
                f"mean={fmt(row['mean_ret'])} median={fmt(row['median_ret'])} "
                f"excess={fmt(row['mean_excess'])} beat_VNINDEX={beat} | "
                f"clean mean={fmt(row['mean_ret_clean'])} excess={fmt(row['mean_excess_clean'])} "
                f"(bat thuong {row['anomaly_rows']})"
            )
    compare = result.get("entry_compare")
    if compare:
        lines.append("-" * 78)
        lines.append("  DOI CHIEU ENTRY (do luong anh huong look-ahead; chi so khac biet = loi nhan vao T0):")
        for h in horizons:
            main_row = result["summary"][str(h)]["ALL"]
            other = compare["summary"].get(str(h)) or {}
            lines.append(
                f"    [{h}D] chinh/{compare['main_mode']}: mean {fmt(main_row['mean_ret'])} "
                f"excess {fmt(main_row['mean_excess'])} | {compare['other_mode']}: "
                f"mean {fmt(other.get('mean_ret'))} excess {fmt(other.get('mean_excess'))}"
            )
    lines.append("-" * 78)
    lines.append("  THEO MA (mean forward cua cac signal cua ma do):")
    for sym, entry in result["by_symbol"].items():
        cells = " ".join(f"{h}D={fmt(entry[f'{h}D_mean'])}({entry[f'{h}D_cover']})" for h in horizons)
        lines.append(f"    {sym:5s} {entry['action']:6s} n={entry['signals']:2d} {cells}")
    lines.append("-" * 78)
    lines.append("  ATTRIBUTION (biến nào bị phạt — theo attribution.primary cua CSI):")
    attr = result.get("attribution") or {}
    for key, blk in sorted(attr.items(), key=lambda kv: -kv[1]["total"]):
        lines.append(f"    {key}: {blk['total']} signal (VETO {blk['VETO']} / REDUCE {blk['REDUCE']})")
        if blk.get("message"):
            lines.append(f"        message: {blk['message']}")
    if len(attr) == 1:
        only = next(iter(attr))
        lines.append(
            f"  [NHAN XET] tat ca signal deu bi ghi cung 1 nguon '{only}' -> CSI khong phan biet "
            f"nguyen nhan theo tung ma; neu '{only}' bi phat qua nang thi ca tap bi phat qua nang."
        )
    lines.append("-" * 78)
    main_h = 20 if 20 in horizons else horizons[0]
    lines.append(f"  KET LUAN [{main_h}D] (ma tran dong-kiem vs chi so):")
    for label in ("ALL", *ACTIONS):
        row = result["summary"][str(main_h)][label]
        if row["cover"] == 0:
            lines.append(f"    {label:6s}: NO_DATA — khong du phien phia truoc, khong ket luan")
            continue
        lines.append(
            f"    {label:6s} (cover {row['cover']}/{row['n']}): mean {fmt(row['mean_ret'])} | "
            f"VNINDEX {fmt(row['mean_vnindex'])} | excess {fmt(row['mean_excess'])} -> "
            f"{classify(row['mean_ret'], row['mean_excess'])}"
        )
        if row["anomaly_rows"]:
            lines.append(
                f"           [clean loai {row['anomaly_rows']} dong] mean {fmt(row['mean_ret_clean'])} | "
                f"excess {fmt(row['mean_excess_clean'])} -> "
                f"{classify(row['mean_ret_clean'], row['mean_excess_clean'])}"
            )
    lines.append(
        "  LUU Y: 60D toan bo = NO_DATA (csi_history moi tu 2026-07-31, chua du 60 phien) "
        "-> khong dua ra bat ky ket luan nao ve 60D."
    )
    lines.append("=" * 78)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--csi", type=Path, default=CSI_PATH)
    ap.add_argument("--db", type=Path, default=DB_PATH)
    ap.add_argument("--horizons", type=int, nargs="+", default=[5, 10, 20, 60])
    ap.add_argument(
        "--anomaly-band",
        type=float,
        default=8.0,
        help="nguong |daily return| danh dau bat thuong du lieu (mac dinh 8%% > band 7%% cua HOSE)",
    )
    ap.add_argument(
        "--entry",
        choices=("next", "signal"),
        default="next",
        help="diem vao: next = phien T0+1 (mac dinh, khong look-ahead); signal = close T0 (legacy)",
    )
    ap.add_argument("--json", type=Path, default=None, help="ghi ket qua JSON (khong mac dinh)")
    args = ap.parse_args(argv)
    horizons = sorted(set(args.horizons))

    if not args.db.exists():
        print(f"khong tim thay DB: {args.db}")
        return 2
    signals = load_signals(args.csi)
    if not signals:
        print("khong co signal VETO/REDUCE nao trong csi_history")
        return 1
    offset = 1 if args.entry == "next" else 0
    result = audit(signals, args.db, horizons, band_pct=args.anomaly_band, entry_offset=offset)
    other = audit(signals, args.db, horizons, band_pct=args.anomaly_band, entry_offset=0 if offset == 1 else 1)
    result["entry_compare"] = {
        "main_mode": result["entry_mode"],
        "other_mode": other["entry_mode"],
        "summary": {str(h): other["summary"][str(h)]["ALL"] for h in horizons},
    }
    print(report(result, horizons))
    if args.json:
        args.json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"da ghi JSON: {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
