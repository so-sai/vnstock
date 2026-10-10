"""market_backfill.py — History-path backfill với G1/G2/G3 guard (P0 2026-10-02).

POISON ROOT CAUSE (sự cố 2026-10-02): update_market_batch() dùng price_board()
— snapshot real-time KHÔNG có date param — rồi stamp `date = target_date`.
Cron 16:00 (target=today) chạy hên nên đúng; backfill --date quá khứ dán
snapshot live vào ngày cũ → 902/903 mã có (close, volume) giống hệt nhau qua
30/09↔01/10, PNJ "30/09" = −19.4% vượt band ±7%. CLI vẫn báo SUCCESS →
silent poisoning, không guard nào bắn.

Backfill BẮT BUỘC qua provider.history(start, end) với 3 guard:
  G1 DATE_MATCH — chỉ giữ row date == target_date (root fix; kbs history
                  trả đúng từng phiên, chứng minh bằng VNINDEX path).
  G2 BAND_GUARD — |close/prev_close − 1| > band theo sàn (HOSE 7% /
                  HNX 10% / UPCOM+unknown 15%; niêm yết ≤28 ngày miễn trừ)
                  → reject từng row (missing > fake).
  G3 DUP_CANARY — >90% mã có close y hét phiên trước (n≥50) TRONG KHI
                  VNINDEX dịch chuyển (hoặc không verify được) → CRITICAL,
                  KHÔNG ghi gì (abort toàn bộ).

Scale: kbs history trả NGHÌN ĐỒNG → _ingestion_scale_guard tự ×1000 về VND
tại biên nạp (idempotent với save_data_upsert). FOREIGN flow: history() không
có dữ liệu ngoại → market_foreign_history để THIẾU cho ngày backfill
(thiếu > fake).
"""

import json
import logging
import os
import sqlite3
import time
from datetime import datetime

import pandas as pd

from src.database.db_core import _ingestion_scale_guard, get_connection, save_data_upsert

logger = logging.getLogger(__name__)

BAND_BY_EXCHANGE = {"HOSE": 0.07, "HNX": 0.10, "UPCOM": 0.15}
BAND_DEFAULT = 0.15
NEW_LISTING_EXEMPT_DAYS = 28

#: Sàn cho mã KHÔNG có dòng trong `ipo_calendar`. Hiện trạng: thiếu dòng ⇒ rơi vào
#: BAND_DEFAULT (15 %) — LỎNG HƠN dải thật của HOSE (7 %), tức guard yếu đi âm thầm ở
#: đúng những mã phổ biến nhất. Khai báo ở đây CHỈ khi đã kiểm chứng bằng dữ liệu
#: (biên độ quan sát được của mã không vượt dải của sàn đó).
KNOWN_EXCHANGE: dict[str, str] = {"HDB": "HOSE"}

#: Sự kiện quyền (chia thưởng/cổ tức) — danh sách CÓ HỒ SƠ, mỗi mục phải nêu cách đo.
#:
#: VÌ SAO KHÔNG dùng cơ chế miễn trừ của `NEW_LISTING` (trả `None` = bỏ qua guard):
#: bỏ qua guard sẽ cho lọt MỌI giá trị, kể cả một giá sai 10×. Sự kiện quyền không phải
#: "không có tham chiếu" — nó có tham chiếu, chỉ khác GỐC GIÁ. Nên cách đúng là quy
#: tham chiếu về gốc giá sau sự kiện rồi so như thường: guard VẪN hoạt động.
#:
#: Cơ sở bằng chứng: giá THÔ trong store so với chuỗi ĐÃ ĐIỀU CHỈNH của nhà cung cấp
#: cho tỉ lệ hằng số 1.2990–1.2996 trên 120 phiên chồng lấn (2026-01 → 2026-10).
CORPORATE_ACTION_EXEMPTIONS: dict[tuple[str, str], dict[str, float | str]] = {
    ("HDB", "2026-10-09"): {
        "factor": 1.2993,
        "reason": "chia thưởng ~30% (30% stock dividend), ex-date 2026-10-09",
        "measured": "store_raw/provider_adjusted, 120 phiên chồng lấn 2026-01→2026-10",
    },
}

CANARY_TC_LIMIT = 0.60  # F-c: hạ 90% → 60% (bắt bài flat-price / stale dump sớm hơn)
CANARY_MIN_N = 50
CANARY_INDEX_MOVED = 0.002
ROLLING_REF_LOOKBACK = 20  # F-b: số phiên cho median tham chiếu dự phòng
# F-d: retry + exponential backoff. Hạ qua env cho lượt dọn dữ liệu lịch sử:
#   PTCK_BACKFILL_ATTEMPTS=1 → mã không có dữ liệu chỉ tốn 1 lần fetch (~3s thay vì ~10s)
FETCH_MAX_ATTEMPTS = max(1, int(os.environ.get("PTCK_BACKFILL_ATTEMPTS", "3")))
FETCH_BACKOFF_BASE = 0.4
OHLCV_COLS = ["symbol", "date", "open", "high", "low", "close", "adj_close", "volume", "source"]


def normalize_history_df(raw, symbol, target_date):
    """G1 DATE_MATCH: chuẩn hóa response history(); None nếu không có row đúng ngày."""
    if raw is None or raw.empty:
        return None
    df = raw.copy()
    df.columns = [str(c).lower() for c in df.columns]
    if "time" in df.columns:
        df = df.rename(columns={"time": "date"})
    if "date" not in df.columns or "close" not in df.columns:
        logger.error("G1_DATE_MATCH_FAIL symbol=%s: response thiếu cột date/close", symbol)
        return None
    df["date"] = pd.to_datetime(df["date"], format="mixed").dt.strftime("%Y-%m-%d")
    df = df[df["date"] == str(target_date)]
    if df.empty:
        logger.error(
            "G1_DATE_MATCH_FAIL symbol=%s target=%s: provider trả ngày khác — đứng ngoài",
            symbol,
            target_date,
        )
        return None
    if df["date"].duplicated().any():
        logger.warning("G1_DATE_DEDUP symbol=%s: %d rows cùng ngày, giữ row đầu", symbol, int(df["date"].duplicated().sum()))
        df = df[~df["date"].duplicated(keep="first")]
    df["symbol"] = symbol
    if "adj_close" not in df.columns:
        df["adj_close"] = df["close"]
    if "volume" not in df.columns:
        df["volume"] = 0
    for c in ("open", "high", "low"):
        if c not in df.columns:
            df[c] = float("nan")
    df["source"] = "kbs"
    return df


def _band_limit(symbol, target_date, conn):
    """Band ±fraction cho symbol tại target_date; None = miễn trừ (niêm yết mới)."""
    try:
        row = conn.execute(
            "SELECT exchange, listing_date FROM ipo_calendar WHERE symbol=? ORDER BY listing_date DESC LIMIT 1",
            (symbol,),
        ).fetchone()
    except sqlite3.Error as e:
        logger.warning("BAND_LOOKUP_FAIL symbol=%s: %s", symbol, e)
        return BAND_DEFAULT
    if not row:
        # Không có dòng ipo_calendar ⇒ mặc định cũ là 15 %, tức LỎNG HƠN dải thật của
        # HOSE (7 %) ngay ở những mã phổ biến nhất. Tra bảng đã kiểm chứng trước.
        known = KNOWN_EXCHANGE.get(symbol)
        if known:
            logger.info("G2_BAND_BY_KNOWN_EXCHANGE symbol=%s exchange=%s", symbol, known)
            return BAND_BY_EXCHANGE.get(known, BAND_DEFAULT)
        return BAND_DEFAULT
    exchange = (row[0] or "").upper()
    listing_date = row[1]
    if listing_date:
        try:
            ld = datetime.strptime(str(listing_date)[:10], "%Y-%m-%d").date()
            td = datetime.strptime(str(target_date)[:10], "%Y-%m-%d").date()
            if 0 <= (td - ld).days <= NEW_LISTING_EXEMPT_DAYS:
                logger.info("G2_BAND_EXEMPT_NEW_LISTING symbol=%s listing=%s", symbol, listing_date)
                return None
        except ValueError:
            pass
    return BAND_BY_EXCHANGE.get(exchange, BAND_DEFAULT)


def _prev_closes(conn, target_date):
    """Close phiên gần nhất TRƯỚC target_date (is_stale=0 — chỉ dùng data sạch)."""
    rows = conn.execute(
        "SELECT d.symbol, d.close FROM daily_ohlcv d JOIN ("
        " SELECT symbol, MAX(date) AS md FROM daily_ohlcv"
        " WHERE date < ? AND is_stale = 0 GROUP BY symbol) m"
        " ON d.symbol = m.symbol AND d.date = m.md",
        (str(target_date),),
    ).fetchall()
    return {str(r[0]): float(r[1]) for r in rows if r[1] is not None}


def _rolling_median_refs(conn, target_date, lookback=ROLLING_REF_LOOKBACK):
    """F-b: median close (>0) `lookback` phiên gần nhất TRƯỚC target, mọi symbol.

    Dùng làm tham chiếu dự phòng khi phiên sạch gần nhất không có mã đó
    (mã mới / mất dấu / phiên trước bị is_stale).
    """
    rows = conn.execute(
        "SELECT symbol, close FROM ("
        "  SELECT symbol, close,"
        "         ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY date DESC) rn"
        "  FROM daily_ohlcv WHERE date < ? AND close > 0"
        ") WHERE rn <= ?",
        (str(target_date), int(lookback)),
    ).fetchall()
    buckets: dict[str, list[float]] = {}
    for sym, close in rows:
        if close is None:
            continue
        buckets.setdefault(str(sym), []).append(float(close))
    out: dict[str, float] = {}
    for sym, vals in buckets.items():
        vals.sort()
        n = len(vals)
        out[sym] = vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2.0
    return out


def _vnindex_pct(provider, target_date, conn):
    """% dịch chuyển VNINDEX target vs phiên gần nhất trước đó; None nếu thiếu."""
    prev = conn.execute(
        "SELECT close FROM daily_ohlcv WHERE symbol='VNINDEX' AND date < ? AND is_stale = 0 ORDER BY date DESC LIMIT 1",
        (str(target_date),),
    ).fetchone()
    if prev is None or not prev[0]:
        return None
    try:
        idx = provider.history("VNINDEX", start=str(target_date), end=str(target_date))
    except Exception as e:  # noqa: BLE001 - canary best-effort: index fetch lỗi → không verify được
        logger.warning("G3_VNINDEX_FETCH_FAIL target=%s: %s", target_date, e)
        return None
    if idx is None or idx.empty:
        return None
    df = idx.copy()
    df.columns = [str(c).lower() for c in df.columns]
    if "time" in df.columns:
        df = df.rename(columns={"time": "date"})
    if "date" not in df.columns:
        return None
    df["date"] = pd.to_datetime(df["date"], format="mixed").dt.strftime("%Y-%m-%d")
    df = df[df["date"] == str(target_date)]
    if df.empty:
        return None
    cur = float(df.iloc[0]["close"])
    return (cur - float(prev[0])) / float(prev[0])


BACKFILL_SOURCES = ("kbs", "vci")


def _resolve_backfill_provider(symbols, target_date, sources=BACKFILL_SOURCES):
    """Chọn nguồn THỰC SỰ có dữ liệu đúng `target_date`.

    WHY: nguồn index (VNINDEX) có thể đã cập nhật phiên mới trong khi cổ phiếu
    còn trễ 1 phiên (kbs 2026-10-02: VNINDEX có, HPG không; vci có cả hai).
    Probe bằng vài mã cổ phiếu đầu danh sách — KHÔNG dùng VNINDEX.
    Trả (provider, source_name).
    """
    from src.providers.vnstock_provider import VnstockProvider

    probes = [str(s) for s in list(symbols)[:5]] or ["VNINDEX"]
    target_date = str(target_date)
    for src in sources:
        p = VnstockProvider(source=src)
        for sym in probes:
            try:
                df = p.history(sym, start=target_date, end=target_date)
            except Exception as e:  # noqa: BLE001 - probe best-effort, không chặn chọn nguồn khác
                logger.warning("BACKFILL_PROBE_FAIL source=%s symbol=%s: %s", src, sym, e)
                df = None
            if normalize_history_df(df, sym, target_date) is not None:
                logger.info("BACKFILL_SOURCE_SELECTED source=%s probe=%s target=%s", src, sym, target_date)
                return p, src
    logger.error(
        "BACKFILL_NO_SOURCE target=%s: không nguồn nào %s có dữ liệu đúng ngày — đứng ngoài",
        target_date,
        sources,
    )
    return VnstockProvider(source=sources[0]), sources[0]


def backfill_market_history(symbols, target_date, provider=None, throttle_sec=0.0, conn_factory=get_connection):
    """Nạp daily_ohlcv cho ngày quá khứ qua provider.history + G1/G2/G3 guard.

    Returns (success, failed, skipped): success = số dòng ghi được.
    Bất kỳ guard nào trip → KHÔNG ghi (missing > fake).
    """
    if provider is None:
        provider, _src = _resolve_backfill_provider(symbols, target_date)
    target_date = str(target_date)
    frames = []
    fetch_fail = 0
    with conn_factory() as conn:
        prev_map = _prev_closes(conn, target_date)
        median_map = _rolling_median_refs(conn, target_date)  # F-b: tham chiếu dự phòng
        for i, sym in enumerate(symbols):
            df = None
            last_reason = "UNKNOWN"
            for attempt in range(FETCH_MAX_ATTEMPTS):  # F-d: retry + exponential backoff
                raw = None
                try:
                    raw = provider.history(sym, start=target_date, end=target_date)
                except Exception as e:  # noqa: BLE001 - retry: 1 mã lỗi không dừng backfill
                    last_reason = f"{type(e).__name__}: {e}"
                if raw is not None and not raw.empty:
                    df = normalize_history_df(raw, sym, target_date)
                    if df is not None:
                        break
                    last_reason = "G1_DATE_MATCH_FAIL"
                elif raw is not None:
                    last_reason = "EMPTY"
                if attempt < FETCH_MAX_ATTEMPTS - 1:
                    time.sleep(FETCH_BACKOFF_BASE * (2**attempt))
            if df is None:
                logger.error(
                    "BACKFILL_FETCH_FAIL symbol=%s attempts=%d reason=%s",
                    sym,
                    FETCH_MAX_ATTEMPTS,
                    last_reason,
                )
                fetch_fail += 1
                continue
            frames.append(df)
            if throttle_sec and (i + 1) % 50 == 0:
                time.sleep(throttle_sec)
            if (i + 1) % 200 == 0:
                logger.info(
                    "BACKFILL_PROGRESS %d/%d fetched=%d fail=%d target=%s",
                    i + 1,
                    len(symbols),
                    len(frames),
                    fetch_fail,
                    target_date,
                )

        if not frames:
            logger.error("BACKFILL_EMPTY target=%s: 0/%d mã có dữ liệu đúng ngày", target_date, len(symbols))
            return 0, len(symbols), 0

        out = pd.concat(frames, ignore_index=True)
        out = out.drop_duplicates(subset=["symbol", "date"], keep="first")

        close_num = pd.to_numeric(out["close"], errors="coerce")
        out = out[~(close_num.isna() | (close_num <= 0))].copy()

        out = _ingestion_scale_guard(out, conn)

        kept = []
        band_reject = 0
        no_ref = 0
        for _, row in out.iterrows():
            sym = str(row["symbol"])
            limit = _band_limit(sym, target_date, conn)
            if limit is None:
                # Miễn trừ band (niêm yết mới) — không cần tham chiếu.
                kept.append(row)
                continue
            prev = prev_map.get(sym)
            ref = prev if (prev is not None and prev > 0) else median_map.get(sym)
            ref_kind = "prev_close" if (prev is not None and prev > 0) else "rolling_median"
            # Sự kiện quyền: quy tham chiếu về gốc giá SAU sự kiện rồi so như thường.
            # Không bỏ qua guard (xem CORPORATE_ACTION_EXEMPTIONS): bỏ qua sẽ cho lọt cả
            # một giá sai 10×, còn quy gốc giá thì vẫn bắt được mọi bất thường thật.
            ca = CORPORATE_ACTION_EXEMPTIONS.get((sym, str(target_date)[:10]))
            if ca and ref and ref > 0:
                factor = float(ca["factor"])
                ref = ref / factor
                ref_kind = f"prev_close/{factor:.4f}(corp_action)"
                logger.info(
                    "G2_CORPORATE_ACTION_ADJUSTED symbol=%s factor=%.4f ref=%.0f — %s",
                    sym,
                    factor,
                    ref,
                    ca.get("reason", ""),
                )
            if not ref or ref <= 0:
                logger.error(
                    "G2_NO_REFERENCE symbol=%s target=%s — REJECT (không có giá tham chiếu)",
                    sym,
                    target_date,
                )
                no_ref += 1
                band_reject += 1
                continue
            pct = (float(row["close"]) - ref) / ref
            if abs(pct) > limit + 1e-9:
                logger.error(
                    "G2_BAND_REJECT symbol=%s close=%.0f ref=%.0f(%s) pct=%.2f%% limit=%.0f%% target=%s",
                    sym,
                    float(row["close"]),
                    ref,
                    ref_kind,
                    pct * 100,
                    limit * 100,
                    target_date,
                )
                band_reject += 1
                continue
            kept.append(row)

        if not kept:
            logger.error("G2_BAND_ALL_REJECTED target=%s: %d/%d row bị reject", target_date, band_reject, len(out))
            return 0, len(symbols), 0

        with_prev = [(float(r["close"]), prev_map[str(r["symbol"])]) for r in kept if str(r["symbol"]) in prev_map]
        if len(with_prev) >= CANARY_MIN_N:
            tc_ratio = sum(1 for c, p in with_prev if abs(c - p) < 1.0) / len(with_prev)
            if tc_ratio > CANARY_TC_LIMIT:
                idx_pct = _vnindex_pct(provider, target_date, conn)
                if idx_pct is None or abs(idx_pct) > CANARY_INDEX_MOVED:
                    logger.critical(
                        "G3_SNAPSHOT_DUPLICATION_SUSPECTED target=%s tc_ratio=%.1f%% n=%d "
                        "idx_pct=%s — TỪ CHỐI ghi toàn bộ %d dòng",
                        target_date,
                        tc_ratio * 100,
                        len(with_prev),
                        "N/A" if idx_pct is None else f"{idx_pct * 100:+.2f}%",
                        len(kept),
                    )
                    return 0, len(symbols), 0

        final = pd.DataFrame(kept)[OHLCV_COLS]
        save_data_upsert("daily_ohlcv", final, conn)

        now = datetime.now().isoformat()
        meta = json.dumps({"path": "history_backfill", "guards": "G1+G2+G3"}, ensure_ascii=False)
        try:
            conn.executemany(
                "INSERT OR REPLACE INTO data_freshness "
                "(symbol, date, last_updated, source, staleness_hours, api_status, data_hash, metadata_json) "
                "VALUES (?, ?, ?, 'API', 0, 'OK', NULL, ?)",
                [(str(r["symbol"]), target_date, now, meta) for r in kept],
            )
            conn.commit()
        except sqlite3.Error as e:
            logger.warning("BACKFRESHNESS_WRITE_FAIL target=%s: %s", target_date, e)

        written = len(final)
        logger.info(
            "BACKFILL_DONE target=%s written=%d/%d band_reject=%d no_ref=%d fetch_fail=%d",
            target_date,
            written,
            len(symbols),
            band_reject,
            no_ref,
            fetch_fail,
        )
        return written, len(symbols) - written, 0
