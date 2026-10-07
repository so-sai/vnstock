"""poison_detector.py — Giải pháp D: Poison Detector Auditor (quét toàn DB).

WHY: sự cố 30/09 + 01/10/2026 (backfill ghi "latest snapshot" vào ngày cũ) chỉ
bị phát hiện THỦ CÔNG, sau khi nó đã lọt vào store và làm méo breadth/MA20.
G3 canary trong pipeline backfill chỉ bảo vệ *lúc ghi*; nếu dữ liệu bẩn lọt vào
bằng đường khác (import tay, restore backup, provider ngụy tạo) thì không ai bắt.

Auditor này quét độc lập, KHÔNG can thiệp pipeline, và mặc định DRY-RUN.

Dấu hiệu nhận dạng (chọn lọc để tránh false-positive):
  Một mã bị "copy" khi cả CLOSE **và** VOLUME giống hệt phiên trước — đó là dấu
  vân của bản sao. Chỉ trùng CLOSE (volume khác) là *bình thường* (mã đứng giá,
  trần/sàn), nên KHÔNG tính.
  Cấp NGÀY: nếu tỷ lệ mã-copy >= ngưỡng (mặc định 50%) → nghi ngày đó bị dump.

Hành động: chỉ TAG cách ly (is_stale=1 + source += '|poisoned:auto_detect'),
KHÔNG auto-delete — để người review.

Usage:
    python backend/scripts/poison_detector.py                  # dry-run, quét cả DB
    python backend/scripts/poison_detector.py --last 60        # 60 phiên gần nhất
    python backend/scripts/poison_detector.py --apply          # mới thực sự tag
    python backend/scripts/poison_detector.py --json           # output máy đọc
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = PROJECT_ROOT / "backend" / "data" / "screener_cache.db"

DEFAULT_MIN_COMMON = 200  # số mã chung tối thiểu để một cặp ngày được xét
DEFAULT_MIN_RATIO = 0.50  # >= 50% mã copy ⇒ nghi ngày bị dump
TAG = "poisoned:auto_detect"


def _dates(conn: sqlite3.Connection, last: int | None = None) -> list[str]:
    rows = [r[0] for r in conn.execute("SELECT DISTINCT date FROM daily_ohlcv ORDER BY date")]
    return rows[-last:] if last else rows


def _copies_between(conn: sqlite3.Connection, prev: str, cur: str) -> list[str]:
    """Mã có CẢ close VÀ volume giống hệt phiên trước (dấu vân bản sao)."""
    rows = conn.execute(
        """
        SELECT c.symbol
        FROM daily_ohlcv c
        JOIN daily_ohlcv p ON p.symbol = c.symbol AND p.date = ?
        WHERE c.date = ?
          AND COALESCE(c.is_stale, 0) = 0
          AND c.close = p.close
          AND c.volume = p.volume
          AND c.volume IS NOT NULL
        """,
        (prev, cur),
    ).fetchall()
    return [r[0] for r in rows]


def _common_count(conn: sqlite3.Connection, prev: str, cur: str) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) FROM daily_ohlcv c
        JOIN daily_ohlcv p ON p.symbol = c.symbol AND p.date = ?
        WHERE c.date = ? AND COALESCE(c.is_stale, 0) = 0
          AND c.close IS NOT NULL AND c.volume IS NOT NULL
        """,
        (prev, cur),
    ).fetchone()
    return int(row[0]) if row else 0


def audit(
    conn: sqlite3.Connection,
    last: int | None = None,
    min_common: int = DEFAULT_MIN_COMMON,
    min_ratio: float = DEFAULT_MIN_RATIO,
) -> list[dict]:
    """Trả danh sách verdict cấp NGÀY. Không ghi gì."""
    dates = _dates(conn, last)
    verdicts: list[dict] = []
    for prev, cur in zip(dates, dates[1:]):
        common = _common_count(conn, prev, cur)
        if common < min_common:
            continue
        copies = _copies_between(conn, prev, cur)
        ratio = len(copies) / common
        verdicts.append(
            {
                "date": cur,
                "prev_date": prev,
                "common": common,
                "copies": len(copies),
                "ratio": round(ratio, 4),
                "suspect": ratio >= min_ratio,
                "sample": copies[:8],
            }
        )
    return verdicts


def apply_quarantine(conn: sqlite3.Connection, verdicts: list[dict]) -> int:
    """Tag cách ly các DÒNG COPY của những ngày nghi ngờ. Trả số dòng bị tag."""
    tagged = 0
    for v in verdicts:
        if not v["suspect"]:
            continue
        symbols = _copies_between(conn, v["prev_date"], v["date"])
        if not symbols:
            continue
        marks = ",".join("?" * len(symbols))
        cur = conn.execute(
            f"""
            UPDATE daily_ohlcv
            SET is_stale = 1,
                source = COALESCE(source, 'unknown') || '|{TAG}'
            WHERE date = ? AND symbol IN ({marks}) AND COALESCE(is_stale, 0) = 0
            """,
            (v["date"], *symbols),
        )
        tagged += cur.rowcount or 0
    conn.commit()
    return tagged


def main() -> int:
    ap = argparse.ArgumentParser(description="Poison Detector Auditor (Giải pháp D)")
    ap.add_argument("--db", default=str(DB_PATH))
    ap.add_argument("--last", type=int, default=None, help="chỉ quét N phiên gần nhất")
    ap.add_argument("--min-common", type=int, default=DEFAULT_MIN_COMMON)
    ap.add_argument("--min-ratio", type=float, default=DEFAULT_MIN_RATIO)
    ap.add_argument("--apply", action="store_true", help="thực sự TAG cách ly (mặc định chỉ báo cáo)")
    ap.add_argument("--json", action="store_true", help="xuất JSON")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    try:
        verdicts = audit(conn, last=args.last, min_common=args.min_common, min_ratio=args.min_ratio)
        suspects = [v for v in verdicts if v["suspect"]]

        if args.json:
            print(json.dumps({"checked": len(verdicts), "suspects": suspects}, ensure_ascii=False, indent=2))
        else:
            print(f"=== POISON DETECTOR — đã xét {len(verdicts)} cặp phiên liên tiếp ===")
            print(f"    ngưỡng: >= {args.min_common} mã chung, tỷ lệ copy >= {args.min_ratio:.0%}")
            if not verdicts:
                print("    (không có cặp phiên nào đủ dữ liệu để xét)")
            if not suspects:
                print("    ✅ KHÔNG phát hiện ngày nghi bị dump")
            for v in suspects:
                print(
                    f"    🔴 {v['date']} (so {v['prev_date']}): "
                    f"{v['copies']}/{v['common']} = {v['ratio']:.0%} mã copy close+volume"
                    f"  · ví dụ {', '.join(v['sample'][:5])}"
                )
            top = sorted(verdicts, key=lambda x: -x["ratio"])[:3]
            if top:
                print("    (3 tỷ lệ cao nhất, kể cả dưới ngưỡng):")
                for v in top:
                    print(f"      {v['date']}: {v['ratio']:.0%} ({v['copies']}/{v['common']})")

        if args.apply:
            if not suspects:
                print("    → --apply: không có ngày nào cần tag.")
                return 0
            n = apply_quarantine(conn, suspects)
            print(f"    → --apply: đã tag {n} dòng (is_stale=1, source += '|{TAG}')")
        elif suspects:
            print("    → ĐANG DRY-RUN. Thêm --apply để tag cách ly.")
        return 1 if suspects else 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
