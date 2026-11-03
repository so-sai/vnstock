"""market_wide_breadth.py — Do rong TOAN THI TRUONG tu daily_ohlcv (H1, Option B).

Nhan du lieu (DATA LABEL — bat buoc):
  breadth_type: ALL_EXCHANGES_UNWEIGHTED (MARKET_WIDE)
  VNINDEX constituent weights: NOT AVAILABLE
  => Equal-weighted count. Small-cap dominated, co the lech khoi HOSE breadth.
  TUYET DOI KHONG dan nhan HOSE_BREADTH.

Mau tai ngay T: volume_T > 0 AND close_{T-1} ton tai (khong impute).
Causal: chi doc bars date<=T. Degraded neu active < 80% median 60 phien.
Tran/san +/-6.8% la proxy cuc tri DANH NGHIA (HOSE 7/HNX 10/UPCOM 15 —
khong co nhan san nen khong chinh xac tuyet doi).

Bang: market_wide_breadth_daily (chi TAO MOI, khong sua bang cu).
Breadth la REGIME INPUT, khong phai alpha — khong tinh vao N trials DSR.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path


def _hydrate_path() -> Path:
    """Path Hydrator v2.1 (Anchor Fix): Auto-locate Project Root."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    current = Path(__file__).resolve().parent
    while current != current.parent:
        if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
            return current
        current = current.parent
    return Path(__file__).resolve().parent.parent.parent


PROJECT_ROOT = _hydrate_path()
DB = PROJECT_ROOT / "backend" / "data" / "screener_cache.db"
LABEL = "ALL_EXCHANGES_UNWEIGHTED"
EXTREME_PCT = 6.8
DEGRADED_RATIO = 0.8
DEGRADED_WIN = 60

TABLE_DDL = """
CREATE TABLE IF NOT EXISTS market_wide_breadth_daily (
    date TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    total_active INTEGER,
    advances INTEGER,
    declines INTEGER,
    unchanged INTEGER,
    ceiling INTEGER,
    floor INTEGER,
    breadth_pct REAL,
    ad_ratio REAL,
    degraded INTEGER,
    created_at TEXT
)
"""


def load_bars(start: str, end: str) -> dict[str, list[tuple]]:
    # Chỉ CỔ PHIẾU: loại rổ chỉ số. Volume VNINDEX (643.935.725 cp ngày 2026-08-28) lớn hơn cả
    # rổ cổ phiếu ⇒ nếu để nguyên, breadth đếm chỉ số như một "cổ phiếu" và mọi tổng KL bị sai ~2×.
    try:
        from src.database.data_validity import STOCK_ONLY_SQL, VALID_ROW_SQL
    except ModuleNotFoundError:  # chạy với root = backend/src
        from database.data_validity import STOCK_ONLY_SQL, VALID_ROW_SQL

    con = sqlite3.connect(str(DB))
    rows = con.execute(
        f"SELECT symbol, date, close, volume FROM daily_ohlcv "
        f"WHERE date>=? AND date<=? AND {STOCK_ONLY_SQL} AND {VALID_ROW_SQL} ORDER BY symbol, date",
        (start, end),
    ).fetchall()
    con.close()
    out: dict[str, list[tuple]] = {}
    for s, d, c, v in rows:
        out.setdefault(s, []).append((d, c, v))
    return out


def compute_day(symbols_at: dict[str, tuple[float | None, float]], prev: dict[str, float]) -> dict:
    """symbols_at: symbol -> (close_T, vol_T); prev: symbol -> close_{T-1}."""
    a = d = u = c = f = 0
    n = 0
    for s, (ct, vt) in symbols_at.items():
        if not vt or vt <= 0:
            continue
        cp = prev.get(s)
        if ct is None or cp is None or cp <= 0:
            continue
        n += 1
        if ct > cp:
            a += 1
        elif ct < cp:
            d += 1
        else:
            u += 1
        chg = (ct / cp - 1) * 100.0
        if chg >= EXTREME_PCT:
            c += 1
        elif chg <= -EXTREME_PCT:
            f += 1
    return {
        "total_active": n,
        "advances": a,
        "declines": d,
        "unchanged": u,
        "ceiling": c,
        "floor": f,
        "breadth_pct": round((a - d) / n * 100.0, 3) if n else 0.0,
        "ad_ratio": round(a / max(d, 1), 4),
    }


def run_series(bars: dict[str, list[tuple]]) -> list[dict]:
    """Duyet causal theo ngay. Moi ngay T chi thay bars date<=T."""
    by_date: dict[str, dict[str, tuple]] = {}
    for s, rows in bars.items():
        for d, c, v in rows:
            by_date.setdefault(d, {})[s] = (c, v)
    dates = sorted(by_date)
    prev: dict[str, float] = {}
    out: list[dict] = []
    for dt in dates:
        rec = compute_day(by_date[dt], prev)
        rec["date"] = dt
        out.append(rec)
        for s, (c, _v) in by_date[dt].items():
            if c:
                prev[s] = c
    # degraded flag: active < 80% median 60 phien truoc (khong nhin tuong lai)
    actives = [r["total_active"] for r in out]
    for i, r in enumerate(out):
        win = actives[max(0, i - DEGRADED_WIN) : i]
        med = sorted(win)[len(win) // 2] if win else r["total_active"]
        r["degraded"] = int(bool(win) and r["total_active"] < DEGRADED_RATIO * med)
        r["label"] = LABEL
    # ── Cổng coverage (AGENTS.md) — hai mức, cố ý không đối xử như nhau ──
    # (1) PHIÊN MỚI NHẤT = đường sống của pipeline: thiếu phủ ⇒ DỪNG (fail-fast).
    # (2) Phiên cũ trong 60 phiên gần nhất: KHÔNG dừng cả chuỗi vì một phiên hỏng, nhưng
    #     đánh dấu degraded=1 — tuyệt đối không fillna(0) để lấp chỗ trống.
    # Ngưỡng hiệu chuẩn trên 2026-06..10: 750 mã (median dòng hợp lệ ~865; 1000 mã chặn 92/95 phiên);
    # tỷ lệ giá trị so với MEDIAN TRƯỢT 20 phiên, ngưỡng 0,5 (so với phiên đầy nhất thì chặn 97%).
    from src.database.panel_quality import PanelCoverageError, assert_market_breadth

    counts = {r["date"]: r["total_active"] for r in out}
    vals = {dt: sum((c or 0) * (v or 0) for c, v in by_date[dt].values()) / 1e12 for dt in dates}
    newest = dates[-1]
    assert_market_breadth({newest: counts[newest]}, min_symbols=750, label="market_wide_breadth/newest")
    prior = sorted(vals[d] for d in dates[-21:-1])
    ref = prior[len(prior) // 2] if prior else 0.0
    if ref > 0 and vals[newest] / ref < 0.5:
        raise PanelCoverageError(
            f"[market_wide_breadth/newest] {newest}: giá trị khớp {vals[newest]:.2f} = "
            f"{vals[newest] / ref:.0%} median 20 phiên trước ({ref:.2f}) — nghi thiếu bar."
        )
    stale_sessions = [d for d in dates[-60:] if counts[d] < 750]
    for r in out:
        if r["date"] in set(stale_sessions):
            r["degraded"] = 1
    if stale_sessions:
        print(
            f"[market_wide_breadth] {len(stale_sessions)} phiên không đủ phủ (<750 mã) "
            f"-> degraded=1, KHÔNG lấp trống: {stale_sessions[:5]}"
        )
    return out


def _signature(r: dict) -> tuple:
    """[WHY] So khớp một dòng đã lưu với kết quả tính lại **theo đúng 9 trường nghiệp vụ**.
    Không so `created_at` (provenance chứ không phải nội dung) — nếu so cả nó, mọi dòng
    đều bị coi là "khác" và bị ghi đè vô hạn.
    """
    return (
        r["total_active"],
        r["advances"],
        r["declines"],
        r["unchanged"],
        r["ceiling"],
        r["floor"],
        r["breadth_pct"],
        r["ad_ratio"],
        r["degraded"],
    )


def _read_existing() -> dict[str, tuple]:
    """Đọc các dòng đã có (chỉ để so khớp, không sửa)."""
    con = sqlite3.connect(str(DB))
    try:
        cur = con.execute(
            "SELECT date, total_active, advances, declines, unchanged, ceiling, floor, "
            "breadth_pct, ad_ratio, degraded FROM market_wide_breadth_daily"
        )
        return {r[0]: tuple(r[1:]) for r in cur.fetchall()}
    except sqlite3.OperationalError:
        # bảng chưa tồn tại (DB mới) → coi như chưa có dòng nào
        return {}
    finally:
        con.close()


def sync_missing(start: str = "2020-01-01", end: str | None = None) -> dict:
    """Đồng bộ bảng độ rộng cho pipeline EOD: chỉ ghi phần THIẾU + phiên hiện tại.

    [WHY] `eod-run` là đường chạy EOD duy nhất nhưng trước 03/11/2026 không hề ghi
    `market_wide_breadth_daily` (bước đó chỉ nằm trong `daily_closer`). Bảng đóng băng
    từ 23/10 ⇒ `breadth_degraded_flag()` trả `None` ⇒ `regime_history.is_degraded`
    NULL 6 phiên (26/10..02/11), làm 14 consumer lọc `is_degraded = 0` hiểu sai phiên.

    [ROOT CAUSE] Thiếu caller ở `daily_updater.run_post_update_engines()` — nơi EOD
    chạy các engine sau cập nhật — và `backfill()` sẵn có thì ghi đè TOÀN chuỗi
    (INSERT OR REPLACE mọi ngày), tức mỗi lần EOD sẽ viết lại 1.465 dòng lịch sử và
    đổi `created_at` của chúng. Hàm này tách đúng phần cần ghi.

    [INVARIANT]
      - Tính chuỗi từ lịch sử ĐẦY ĐỦ để cửa sổ `degraded` (60 phiên) có đủ mẫu số;
        nhưng CHỈ ghi (a) phiên chưa có dòng, (b) phiên `end` khi giá trị tính lại
        khác dòng đã lưu (sửa lỗi ingest một phần trong ngày).
      - Dòng lịch sử không bao giờ bị ghi đè ⇒ `created_at` giữ nguyên provenance.
      - Cổng coverage của `run_series` vẫn fail-closed: panel < 750 mã ⇒ ném lỗi,
        không ghi gì.
      - Lệch số liệu lịch sử (drift) được BÁO CÁO chứ không tự sửa: sửa lịch sử phải
        là quyết định của con người, không phải tác dụng phụ của EOD hàng ngày.

    Returns:
        dict: end, inserted, refreshed, drift_count, drift (tối đa 20 ngày).
    """
    from datetime import date as _date
    from datetime import timedelta as _td

    end = end or latest_db_date()
    if not end:
        raise RuntimeError("daily_ohlcv rỗng — không xác định được phiên cần đồng bộ độ rộng.")

    # Buffer 10 ngày lịch để phiên đầu cửa sổ có `prev` thật (xem main()).
    _buf_start = (_date.fromisoformat(start) - _td(days=10)).isoformat()
    bars = load_bars(_buf_start, end)
    series = run_series(bars)  # ném PanelCoverageError nếu phiên mới nhất < 750 mã
    # [WHY] Buffer 10 ngày chỉ để tính `prev` cho phiên đầu; KHÔNG được ghi ra ngoài cửa sổ
    # `start..end` (bảng bắt đầu 2020-01-02; dry-run 03/11/2026 lộ ra 7 ngày 23..31/12/2019
    # sẽ bị chèn nếu thiếu clamp).
    series = [s for s in series if start <= s["date"] <= end]
    if not series or series[-1]["date"] != end:
        got = series[-1]["date"] if series else "none"
        raise RuntimeError(f"không tính được độ rộng cho {end}: phiên mới nhất có bar hợp lệ là {got} — từ chối ghi lùi ngày.")

    existing = _read_existing()
    to_write: list[dict] = []
    inserted = 0
    refreshed = False
    for r in series:
        stored = existing.get(r["date"])
        if stored is None:
            to_write.append(r)
            inserted += 1
        elif r["date"] == end and _signature(r) != stored:
            to_write.append(r)
            refreshed = True

    drift = [
        r["date"]
        for r in series
        if r["date"] != end and existing.get(r["date"]) is not None and _signature(r) != existing[r["date"]]
    ]
    if to_write:
        _upsert_rows(to_write)
    return {
        "end": end,
        "inserted": inserted,
        "refreshed": refreshed,
        "drift_count": len(drift),
        "drift": drift[:20],
    }


def _upsert_rows(series: list[dict]) -> int:
    """Đường ghi DUY NHẤT vào `market_wide_breadth_daily` (INSERT OR REPLACE)."""
    import datetime

    con = sqlite3.connect(str(DB))
    con.execute(TABLE_DDL)
    now = datetime.datetime.now().isoformat(timespec="seconds")
    rows = [
        (
            r["date"],
            r["label"],
            r["total_active"],
            r["advances"],
            r["declines"],
            r["unchanged"],
            r["ceiling"],
            r["floor"],
            r["breadth_pct"],
            r["ad_ratio"],
            r["degraded"],
            now,
        )
        for r in series
    ]
    con.executemany("INSERT OR REPLACE INTO market_wide_breadth_daily VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    try:
        from src.database.eod_write_audit import record_eod_write_audit

        for r in series:
            record_eod_write_audit(
                as_of_date=r["date"],
                caller_module="analysis.market_wide_breadth",
                target_table="market_wide_breadth_daily",
                parameters={"advances": r["advances"], "declines": r["declines"], "total": r["total_active"]},
                input_data=r,
                output_score=float(r["breadth_pct"]),
                status="DEGRADED" if r["degraded"] else "SUCCESS",
                notes=f"Breadth batch insert: {r['advances']}/{r['declines']} (degraded={r['degraded']})",
                conn=con,
            )
    except (sqlite3.Error, TypeError, ValueError) as _e_audit:
        import logging

        logging.getLogger("PTCK_SYSTEM").debug("Audit write skipped: %s", _e_audit)
    n = con.total_changes
    con.close()
    return n


def backfill(series: list[dict]) -> int:
    """Ghi ĐÈ toàn bộ chuỗi được truyền vào (giữ cho `main --backfill` và daily_closer).

    [WHY] Đây là đường thủ công có chủ đích (người vận hành quyết định viết lại). Pipeline
    EOD KHÔNG được dùng hàm này — xem `sync_missing()` để tránh ghi đè lịch sử mỗi phiên.
    """
    return _upsert_rows(series)


def proxy_comparison(bars: dict[str, list[tuple]], target: str) -> dict:
    """Proxy cu: ADV20>=50k (breadth_engine) vs market-wide moi, cung ngay."""
    adv_n = adv_a = adv_d = 0
    for s, rows in bars.items():
        sub = [(d, c, v) for d, c, v in rows if d <= target]
        if len(sub) < 21:
            continue
        if sub[-1][0] != target:
            continue
        vols = [v or 0 for _, _, v in sub[-20:]]
        if sum(vols) / 20 < 50000:
            continue
        ct, cp = sub[-1][1], sub[-2][1]
        if ct is None or cp is None:
            continue
        adv_n += 1
        if ct > cp:
            adv_a += 1
        elif ct < cp:
            adv_d += 1
    return {
        "proxy_n": adv_n,
        "proxy_adv": adv_a,
        "proxy_dec": adv_d,
        "proxy_breadth_pct": round((adv_a - adv_d) / adv_n * 100, 2) if adv_n else 0.0,
    }


def selftest() -> dict:
    bars = load_bars("2024-01-01", "2024-12-31")
    full = {r["date"]: r for r in run_series(bars)}
    cut = "2024-06-28"
    trunc = {s: [(d, c, v) for d, c, v in rows if d <= cut] for s, rows in bars.items()}
    part = {r["date"]: r for r in run_series(trunc)}
    keys = [k for k in ("total_active", "advances", "declines", "unchanged", "breadth_pct", "ad_ratio", "ceiling", "floor")]
    mism = [d for d in part if any(part[d][k] != full[d][k] for k in keys)]
    return {"ok": not mism, "cut": cut, "checked_days": len(part), "mismatches": mism[:5]}


def load_series(include_degraded: bool = False, limit: int | None = None) -> list[dict]:
    """Đọc chuỗi độ rộng từ bảng đã tính — HỢP ĐỒNG 2 TẦNG (bắt buộc).

    Tầng TÍNH TOÁN TÍN HIỆU (`alpha/*`, `governor/*`, `backtest/*`):
        `include_degraded=False` — MẶC ĐỊNH. Phiên `degraded=1` (thiếu mã) bị loại CỨNG,
        vì một phiên phủ 594/1.515 mã sẽ tạo cú sụt/trồi độ rộng giả tạo và làm lệch
        mọi z-score/percentile tính trên cửa sổ.

    Tầng HIỂN THỊ (`api/routes/*`, `core/executive_report.py`, `core/market_snapshot.py`):
        `include_degraded=True` — giữ nguyên dòng nhưng mỗi bản ghi mang `is_degraded`
        để người đọc biết phiên đó không đủ phủ (KHÔNG được lặng lẽ bỏ đi, cũng KHÔNG
        được lặng lẽ trình bày như phiên bình thường).
    """
    sql = (
        "SELECT date, total_active, advances, declines, unchanged, breadth_pct, "
        "ad_ratio, ceiling, floor, degraded FROM market_wide_breadth_daily "
    )
    params: tuple = ()
    if not include_degraded:
        sql += "WHERE (degraded IS NULL OR degraded = 0) "
    sql += "ORDER BY date DESC"
    if limit:
        sql += " LIMIT ?"
        params = (int(limit),)
    con = sqlite3.connect(str(DB))
    try:
        cur = con.execute(sql, params)
        cols = [c[0] for c in cur.description]
        rows = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
    finally:
        con.close()
    for r in rows:
        r["is_degraded"] = bool(r.get("degraded"))
    return rows


def latest_db_date() -> str | None:
    """max(date) trong daily_ohlcv — thay cho --end hardcode (E4b)."""
    con = sqlite3.connect(str(DB))
    try:
        row = con.execute("SELECT MAX(date) FROM daily_ohlcv").fetchone()
    finally:
        con.close()
    return row[0] if row and row[0] else None


def main() -> int:
    ap = argparse.ArgumentParser(description="Market-wide breadth — H1 Option B")
    ap.add_argument("--start", default="2020-01-01")
    # E4b (2026-09-25): default "2026-09-11" hardcode khiến bảng
    # market_wide_breadth_daily đóng băng đúng tại 11/09. Mặc định = phiên mới nhất.
    ap.add_argument("--end", default=None, help="Ngày cuối (mặc định: max(date) của daily_ohlcv)")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--compare-date", default=None)
    a = ap.parse_args()
    if a.selftest:
        r = selftest()
        print(json.dumps(r, ensure_ascii=False))
        return 0 if r["ok"] else 1
    end = a.end or latest_db_date()
    if not end:
        print("ERR: daily_ohlcv rỗng — không xác định được --end.")
        return 1
    # WHY (sửa 2026-10-16): `load_bars` KHÔNG lấy lookback ⇒ phiên ĐẦU của cửa sổ không có
    # `prev` ⇒ mọi chỉ số của nó = 0 và bị upsert đè lên dòng cũ (đã xảy ra với 09-24/09-25 khi
    # chạy bù 15–16/10: dòng đầu cửa sổ bị zero). Nay nạp THÊM 10 ngày lịch trước `--start` để
    # tính, nhưng CHỈ ghi các phiên trong [start, end] ⇒ phiên đầu luôn có `prev` thật.
    from datetime import date as _date
    from datetime import timedelta as _td

    _buf_start = (_date.fromisoformat(a.start) - _td(days=10)).isoformat()
    bars = load_bars(_buf_start, end)
    series = [s for s in run_series(bars) if a.start <= s["date"] <= end]
    print(
        f"days={len(series)} label={LABEL} "
        f"latest={series[-1]['date']} active={series[-1]['total_active']} "
        f"breadth={series[-1]['breadth_pct']}% degraded={series[-1]['degraded']}"
    )
    if a.backfill:
        print(f"backfilled rows={backfill(series)}")
    if a.compare_date:
        rec = next((r for r in series if r["date"] == a.compare_date), None)
        px = proxy_comparison(bars, a.compare_date)
        print(json.dumps({"date": a.compare_date, "market_wide": rec, "proxy_adv20_50k": px}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
