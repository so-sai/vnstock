"""freshness_sentinel.py — Giải pháp C: Freshness Sentinel Gate (fail-closed).

WHY: ngày 06–07/10/2026 CLI vẫn phân tích bình thường trong khi `daily_ohlcv`
dừng ở 02/10 — tức ra quyết định trên dữ liệu lệch 2–3 phiên mà không có gì
chặn. `session_resolver` (Patch C-1) đã có đủ ngữ nghĩa gate (GATE_DATA_STALE)
nhưng (a) chưa được nối vào entrypoint `ptck.py`, và (b) mặc định
`allow_fallback=True` nên chỉ "lùi ngày" trong im lặng. Module này nối nó vào
CLI và biến DATA_STALE thành HALT (exit 2), trừ khi có `--force-stale`.

Quyết định thiết kế (đã chốt):
  - Cutoff EOD **16:30** (cron EOD 16:00 + 30' buffer cho ingest + engine).
    Trước cutoff → kỳ vọng = phiên giao dịch liền trước; từ cutoff → hôm nay.
  - `latest_clean` phải có **>= MIN_CLEAN_ROWS** dòng `is_stale = 0`. Vì sao cần
    ngưỡng đếm: ngày 01/10 từng có 901/903 dòng nhiễm — nếu chỉ lấy MAX(date)
    trần thì gate bị "vượt rào" và tưởng phiên đó đã tươi (false security).
    Dải phủ sạch thực tế của hệ thống là 829–872 → 300 chặn được nhiễu 1–2 dòng
    mà không false-positive với phiên bình thường.
  - Override ghi ledger **append-only JSONL** (không tranh lock SQLite).

Fail-closed: mọi lỗi bất ngờ khi xác định độ tươi đều dẫn tới TỪ CHỐI chạy.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime
from datetime import time as _time
from pathlib import Path
from zoneinfo import ZoneInfo

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
EOD_CUTOFF = _time(16, 30)
DEFAULT_MIN_CLEAN_ROWS = 300
TEST_MODE_ENV = "PTCK_TEST_MODE"
LEDGER_REL = Path("backend") / "data" / "stale_override_ledger.jsonl"

EXIT_CODE_STALE = 2


class DataStaleHalt(RuntimeError):
    """Dữ liệu cũ hơn phiên kỳ vọng — fail-closed, CLI phải dừng."""


def project_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "AGENTS.md").exists() and (parent / "backend").is_dir():
            return parent
    return here.parents[3]


def ledger_path() -> Path:
    return project_root() / LEDGER_REL


def db_path() -> Path:
    return project_root() / "backend" / "data" / "screener_cache.db"


def min_clean_rows() -> int:
    raw = os.environ.get("PTCK_MIN_CLEAN_ROWS")
    if raw is None:
        return DEFAULT_MIN_CLEAN_ROWS
    try:
        return max(0, int(raw))
    except TypeError, ValueError:
        return DEFAULT_MIN_CLEAN_ROWS


def now_vn(now: datetime | None = None) -> datetime:
    """Giờ VN tường minh (server có thể chạy múi giờ khác)."""
    if now is not None:
        return now
    return datetime.now(VN_TZ)


def _calendar():
    from src.services.macro.time_series_aligner import VNSessionCalendar

    return VNSessionCalendar()


def expected_trading_date(now: datetime | None = None, calendar=None) -> str:
    """Phiên giao dịch ĐÁNG LẼ phải có trong DB, dạng YYYY-MM-DD.

    - Ngày không giao dịch (cuối tuần/lễ) → phiên giao dịch liền trước.
    - Ngày giao dịch TRƯỚC 16:30 → phiên liền trước (EOD hôm nay chưa chạy).
    - Ngày giao dịch TỪ 16:30 → chính hôm nay.
    """
    moment = now_vn(now)
    cal = calendar or _calendar()
    today = moment.date()
    if cal.is_trading_day(today) and moment.time() >= EOD_CUTOFF:
        return today.isoformat()
    return cal.prev_trading_day(today).isoformat()


def latest_clean_date(conn: sqlite3.Connection | None = None, min_rows: int | None = None) -> str | None:
    """MAX(date) chỉ xét ngày có >= min_rows dòng sạch (is_stale = 0).

    Ngày chỉ toàn dòng nhiễm (hoặc lèo tèo vài dòng) KHÔNG được coi là tươi.
    """
    threshold = min_clean_rows() if min_rows is None else min_rows
    own = conn is None
    if own:
        conn = sqlite3.connect(str(db_path()))
    try:
        row = conn.execute(
            "SELECT date FROM daily_ohlcv WHERE COALESCE(is_stale, 0) = 0 "
            "GROUP BY date HAVING COUNT(*) >= ? ORDER BY date DESC LIMIT 1",
            (threshold,),
        ).fetchone()
        return row[0] if row and row[0] else None
    finally:
        if own:
            conn.close()


def log_override(command: str, latest: str, expected: str, reason: str = "user_provided_force_flag") -> Path:
    path = ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "event": "DATA_STALE_OVERRIDE",
        "command": command,
        "max_clean_date": latest,
        "expected_date": expected,
        "reason": reason,
        "timestamp": now_vn().isoformat(),
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return path


def assert_fresh(
    command: str,
    force_stale: bool = False,
    conn: sqlite3.Connection | None = None,
    now: datetime | None = None,
) -> None:
    """Cổng chặn fail-closed. Raise DataStaleHalt nếu cũ và không có override."""
    if os.environ.get(TEST_MODE_ENV) == "1":
        return

    try:
        expected = expected_trading_date(now=now)
        latest = latest_clean_date(conn=conn)
    except DataStaleHalt:
        raise
    except Exception as exc:  # fail-closed: không xác định được độ tươi → TỪ CHỐI
        raise DataStaleHalt(
            f"[DATA_STALE_HALT] Không xác định được độ tươi dữ liệu cho lệnh '{command}' "
            f"({type(exc).__name__}: {exc}). Từ chối chạy trên trạng thái không kiểm chứng được."
        ) from exc

    if latest is None:
        raise DataStaleHalt(
            f"[DATA_STALE_HALT] Không có ngày nào đủ dữ liệu sạch (>= {min_clean_rows()} mã) trong DB. "
            f"Lệnh='{command}', kỳ vọng={expected}. Chạy 'ptck.py daily-update' trước."
        )

    if latest >= expected:
        return

    if force_stale:
        path = log_override(command, latest, expected)
        print(
            f"⚠️  CẢNH BÁO: ĐANG CHẠY Ở CHẾ ĐỘ OVERRIDE DỮ LIỆU CŨ — "
            f"data sạch đến {latest} < kỳ vọng {expected}. Đã ghi ledger: {path}",
            file=sys.stderr,
        )
        return

    raise DataStaleHalt(
        f"[DATA_STALE_HALT] Dữ liệu sạch mới nhất trong DB là {latest}, nhưng phiên giao dịch "
        f"kỳ vọng là {expected}. Hệ thống TỪ CHỐI sinh tín hiệu trên dữ liệu cũ!\n"
        f"   → Chạy 'python ptck.py daily-update --date {expected}' trước, "
        f"hoặc thêm cờ '--force-stale' để ghi đè (sẽ ghi ledger)."
    )


def main(argv: list[str] | None = None) -> int:
    """Tiện ích chẩn đoán: `python -m src.core.freshness_sentinel`."""
    argv = list(sys.argv[1:] if argv is None else argv)
    expected = expected_trading_date()
    latest = latest_clean_date()
    print(f"  now(VN)        : {now_vn().isoformat()}")
    print(f"  cutoff EOD     : {EOD_CUTOFF.strftime('%H:%M')}")
    print(f"  kỳ vọng        : {expected}")
    print(f"  latest sạch    : {latest}  (ngưỡng {min_clean_rows()} mã/phiên)")
    verdict = "OK" if (latest and latest >= expected) else "STALE → CLI phân tích sẽ bị HALT"
    print(f"  kết luận       : {verdict}")
    return 0 if verdict == "OK" else EXIT_CODE_STALE


if __name__ == "__main__":
    raise SystemExit(main())
