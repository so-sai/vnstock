"""Daily Brief Snapshot Producer (Phase 1a — `ptck.py daily-brief`).

[WHY] Bản tin Zalo Rust (`minh_chung.exe`) trước đây đọc trực tiếp SQL thô
trên `daily_ohlcv` không qua bộ lọc thanh khoản/RS/Sức khỏe tài chính, dẫn
tới lọt cổ phiếu penny (TTF, DST, IDJ), phải gán cứng (hardcode) nhận xét
lỗi thời và lộ thuật ngữ kỹ thuật nội bộ. Snapshot JSON chuẩn hóa nối tầng
tính toán PIT/fail-closed của Python sang Rust mà không nhân đôi logic.

[ROOT CAUSE] Chưa có cầu nối dữ liệu chuẩn hóa giữa `FeatureStore` /
`CompanyHealthV2` / `regime_history` (Python) và bộ dựng bản tin (Rust).

[INVARIANT]
1. Missing > Fake (fail-closed): mọi bảng/dòng thiếu hoặc `is_degraded != 0`
   trả về `current = None` và `status = "MISSING"`; không dùng `.get(k, 0)`.
2. Bộ lọc `momentum_clean` bắt buộc có `close >= 10_000`, `adv20_shares >= 500_000`,
   `rs >= 85.0`, `is_stock(symbol)`, và `health_archetype` hợp lệ khác `DISTRESSED`.
3. Ghi snapshot nguyên tử qua temp file + `os.replace` để Rust không bao giờ
   đọc phải JSON đang ghi dở.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import math
import os
import sqlite3
import sys
import tempfile
from collections.abc import Iterable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any


def _resolve_project_root() -> Path:
    """Tìm thư mục gốc dự án chứa `AGENTS.md` và `backend/`."""
    current = Path(__file__).resolve().parent
    while current != current.parent:
        if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
            return current
        current = current.parent
    return Path(__file__).resolve().parents[3]


PROJECT_ROOT = _resolve_project_root()
BACKEND_DIR = PROJECT_ROOT / "backend"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from src.backtest.trend_features import FeatureStore
from src.database.data_validity import STOCK_ONLY_SQL, VALID_ROW_SQL, is_stock
from src.financial.company_health_v2 import CompanyHealthV2

# [WHY]: Phiên bản schema snapshot cho Rust kiểm tra fail-closed — đổi cấu trúc phải tăng version để tránh đọc sai.
SCHEMA_VERSION = 1

# [WHY]: Ngưỡng điểm sức khỏe tối thiểu để Governor mở giải ngân — hạ thấp sẽ mua sớm trong pha khủng hoảng.
GOV_REGIME_SCORE_TARGET = 0.40

# [WHY]: Ngưỡng độ rộng tối thiểu (>= 60% mã trên MA20) — thấp hơn dễ dính bull-trap khi dòng tiền chưa lan tỏa.
GOV_BREADTH_PCT_TARGET = 60.0

# [WHY]: Ngưỡng ổn định biến động (> 0.20) — mức <= 0.20 phản ánh biến động cực đoan không an toàn giải ngân.
GOV_VOL_SCORE_TARGET = 0.20

# [WHY]: Ngưỡng xác nhận xu hướng (> 0.00) — bằng 0 nghĩa là xu hướng chỉ số chung chưa đảo chiều tăng.
GOV_TREND_SCORE_TARGET = 0.0

# [WHY]: Giá sàn 10.000đ/cp loại bỏ penny (TTF, DST, IDJ) — hạ xuống sẽ làm bản tin Zalo tràn mã đầu cơ thị giá thấp.
MOMENTUM_MIN_CLOSE_VND = 10_000.0

# [WHY]: Thanh khoản tối thiểu 500.000 cp/phiên (ADV_20D) — thấp hơn không đủ quy mô giải ngân thực tế.
MOMENTUM_MIN_ADV20_SHARES = 500_000.0

# [WHY]: Giá trị giao dịch trung bình 20 phiên tối thiểu — chặn lọt mã thiếu quy mô thanh khoản VND thực tế.
MOMENTUM_MIN_ADV20_VND = 500_000.0

# [WHY]: Sức mạnh giá RS >= 85.0 lọc nhóm dẫn dắt sớm trước ngưỡng TrendEngine (90) — hạ thấp gây nhiễu radar.
MOMENTUM_MIN_RS = 85.0

# [WHY]: Hệ số vượt đỉnh 20 phiên (close > high_20 * 1.0) — tránh gắn cờ breakout khi giá chưa vượt đỉnh gần nhất.
BREAKOUT_HIGH20_MULT = 1.0

# [WHY]: Số phiên nhìn lại mặc định cho FeatureStore PIT (1 năm giao dịch ~252 phiên) để xếp hạng RS chuẩn.
DEFAULT_WARMUP_SESSIONS = 252

# [WHY]: Giới hạn tối đa 10 cổ phiếu sạch trong snapshot để bản tin Zalo ngắn gọn dưới giới hạn ký tự.
DEFAULT_TOP_N = 10

# [WHY]: Truy vấn 2 phiên liền kề để tính biến động 1 phiên chỉ khi khớp lịch VNINDEX (chặn gộp gap nhiều phiên).
SESSION_PAIR_LIMIT = 2

# [WHY]: Làm tròn 4 chữ số thập phân cho chỉ số Governor — tránh sai số biểu diễn float làm nhiễu JSON.
DECIMALS_GOV = 4

# [WHY]: Làm tròn 2 chữ số thập phân cho đại lượng hiển thị (%) và RS — thống nhất hiển thị Python/Rust.
DECIMALS_DISPLAY = 2

# [WHY]: Hệ số nhân đổi tỷ lệ biến động giá sang phần trăm (= 100.0) để hiển thị nhất quán trên bản tin.
PCT_SCALE = 100.0

DEFAULT_SCREENER_DB = BACKEND_DIR / "data" / "screener_cache.db"
DEFAULT_FINANCIAL_DB = BACKEND_DIR / "data" / "financial_facts.db"
DEFAULT_SNAPSHOT_PATH = BACKEND_DIR / "data" / "daily_brief_snapshot.json"

_GOVERNOR_SPEC: tuple[tuple[str, str, str, float, str], ...] = (
    ("regime_score", "Điểm sức khỏe thị trường", ">=", GOV_REGIME_SCORE_TARGET, "điểm"),
    ("breadth_pct", "Tỷ lệ mã trên MA20", ">=", GOV_BREADTH_PCT_TARGET, "%"),
    ("vol_score", "Độ ổn định biến động", ">", GOV_VOL_SCORE_TARGET, "điểm"),
    ("trend_score", "Xác nhận xu hướng", ">", GOV_TREND_SCORE_TARGET, "điểm"),
)

_REQUIRED_FIN_TABLES = ("health_ratios", "financial_facts", "entity_registry")


def _is_finite_number(value: Any) -> bool:
    """Kiểm tra giá trị là số hữu hạn (loại trừ bool, None, NaN, Inf)."""
    if isinstance(value, bool) or value is None:
        return False
    try:
        return math.isfinite(float(value))
    except TypeError, ValueError:
        return False


def evaluate_governor_progress(regime_row: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Đánh giá tiến độ 4 ngưỡng mở khóa giải ngân (fail-closed khi thiếu/suy giảm).

    Không dùng `.get(k, 0)`: thiếu dòng, thiếu cột, NaN hoặc `is_degraded != 0`
    đều phải trả về `current = None` và `status = "MISSING"`.
    """
    is_degraded_row = False
    if regime_row is not None and "is_degraded" in regime_row:
        deg_val = regime_row["is_degraded"]
        if deg_val is None or int(deg_val) != 0:
            is_degraded_row = True

    progress: list[dict[str, Any]] = []
    for key, label, op_str, target, unit in _GOVERNOR_SPEC:
        if regime_row is None or is_degraded_row:
            progress.append(
                {
                    "key": key,
                    "label": label,
                    "operator": op_str,
                    "current": None,
                    "target": target,
                    "unit": unit,
                    "status": "MISSING",
                }
            )
            continue

        raw_val = regime_row.get(key)
        if not _is_finite_number(raw_val):
            progress.append(
                {
                    "key": key,
                    "label": label,
                    "operator": op_str,
                    "current": None,
                    "target": target,
                    "unit": unit,
                    "status": "MISSING",
                }
            )
            continue

        cur_val = round(float(raw_val), DECIMALS_GOV)
        if op_str == ">=":
            passed = cur_val >= target
        else:
            passed = cur_val > target
        progress.append(
            {
                "key": key,
                "label": label,
                "operator": op_str,
                "current": cur_val,
                "target": target,
                "unit": unit,
                "status": "PASS" if passed else "LOCKED",
            }
        )
    return progress


def filter_clean_momentum_candidates(
    candidates: Iterable[Mapping[str, Any]],
    *,
    top_n: int = DEFAULT_TOP_N,
) -> list[dict[str, Any]]:
    """Lọc cổ phiếu động lượng sạch: chặn penny, thanh khoản yếu, RS yếu và mã suy yếu/thiếu BCTC."""
    clean: list[dict[str, Any]] = []
    for item in candidates:
        symbol_raw = item.get("symbol")
        if not isinstance(symbol_raw, str):
            continue
        symbol = symbol_raw.strip().upper()
        if not symbol or not is_stock(symbol):
            continue

        close_raw = item.get("close")
        adv20_shares_raw = item.get("adv20_shares")
        rs_raw = item.get("rs")
        if not (_is_finite_number(close_raw) and _is_finite_number(adv20_shares_raw) and _is_finite_number(rs_raw)):
            continue

        close_val = float(close_raw)
        adv20_shares_val = float(adv20_shares_raw)
        rs_val = float(rs_raw)
        adv20_vnd_raw = item.get("adv20_vnd")
        adv20_vnd_val = float(adv20_vnd_raw) if _is_finite_number(adv20_vnd_raw) else adv20_shares_val * close_val

        if close_val < MOMENTUM_MIN_CLOSE_VND:
            continue
        if adv20_shares_val < MOMENTUM_MIN_ADV20_SHARES:
            continue
        if adv20_vnd_val < MOMENTUM_MIN_ADV20_VND:
            continue
        if rs_val < MOMENTUM_MIN_RS:
            continue

        arch_raw = item.get("health_archetype")
        if not isinstance(arch_raw, str):
            continue
        arch = arch_raw.strip()
        if not arch or arch.upper() == "DISTRESSED":
            continue

        ret_raw = item.get("ret_1d_pct")
        ret_1d_pct = round(float(ret_raw), DECIMALS_DISPLAY) if _is_finite_number(ret_raw) else None
        breakout_20d = bool(item.get("breakout_20d"))

        clean.append(
            {
                "symbol": symbol,
                "close": round(close_val, DECIMALS_DISPLAY),
                "ret_1d_pct": ret_1d_pct,
                "rs": round(rs_val, DECIMALS_DISPLAY),
                "adv20_shares": round(adv20_shares_val, DECIMALS_DISPLAY),
                "adv20_vnd": round(adv20_vnd_val, DECIMALS_DISPLAY),
                "health_archetype": arch,
                "breakout_20d": breakout_20d,
            }
        )

    clean.sort(key=lambda row: (-row["rs"], -row["adv20_vnd"], row["symbol"]))
    if top_n <= 0:
        return []
    return clean[:top_n]


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table_name,),
    ).fetchone()
    return row is not None


def _financial_db_ready(financial_db: Path) -> bool:
    """Kiểm tra DB tài chính tồn tại và đủ 3 bảng chuẩn trước khi gọi CompanyHealthV2."""
    if not financial_db.exists():
        return False
    uri = f"file:{financial_db.resolve().as_posix()}?mode=ro"
    with contextlib.closing(sqlite3.connect(uri, uri=True)) as conn:
        return all(_table_exists(conn, tbl) for tbl in _REQUIRED_FIN_TABLES)


def _fetch_vni_sessions(
    conn: sqlite3.Connection,
    as_of_date: str | None,
) -> tuple[str, str | None]:
    """Lấy (d0, d1) phiên VNINDEX hợp lệ để neo ngày snapshot và chặn gộp gap nhiều phiên."""
    base_sql = (
        f"SELECT DISTINCT date FROM daily_ohlcv WHERE symbol = 'VNINDEX' AND COALESCE(is_stale, 0) = 0 AND {VALID_ROW_SQL}"
    )
    if as_of_date is not None:
        rows = conn.execute(
            f"{base_sql} AND date <= ? ORDER BY date DESC LIMIT {SESSION_PAIR_LIMIT}",
            (as_of_date,),
        ).fetchall()
        if not rows or rows[0][0] != as_of_date:
            raise ValueError(f"Ngày {as_of_date} không có phiên VNINDEX hợp lệ trong daily_ohlcv")
    else:
        rows = conn.execute(f"{base_sql} ORDER BY date DESC LIMIT {SESSION_PAIR_LIMIT}").fetchall()
        if not rows:
            raise ValueError("Không tìm thấy phiên VNINDEX hợp lệ nào trong daily_ohlcv")

    vni_d0 = str(rows[0][0])
    vni_d1 = str(rows[1][0]) if len(rows) >= SESSION_PAIR_LIMIT else None
    return vni_d0, vni_d1


def _fetch_regime_row(conn: sqlite3.Connection, target_date: str) -> dict[str, Any] | None:
    """Đọc dòng `regime_history` đúng ngày (fail-closed nếu thiếu bảng hoặc thiếu dòng)."""
    if not _table_exists(conn, "regime_history"):
        return None
    row = conn.execute(
        """
        SELECT regime_score, breadth_pct, vol_score, trend_score,
               COALESCE(is_degraded, 1) AS is_degraded
        FROM regime_history
        WHERE date = ?
        LIMIT 1
        """,
        (target_date,),
    ).fetchone()
    if row is None:
        return None
    return {
        "regime_score": row[0],
        "breadth_pct": row[1],
        "vol_score": row[2],
        "trend_score": row[3],
        "is_degraded": row[4],
    }


def _compute_ret_1d_pct(
    conn: sqlite3.Connection,
    symbol: str,
    target_date: str,
    vni_d0: str,
    vni_d1: str | None,
) -> float | None:
    """Tính % biến động 1 phiên chỉ khi 2 nến hợp lệ gần nhất khớp đúng 2 phiên VNINDEX liền kề."""
    if vni_d1 is None:
        return None
    rows = conn.execute(
        f"""
        SELECT date, close FROM daily_ohlcv
        WHERE symbol = ?
          AND date <= ?
          AND COALESCE(is_stale, 0) = 0
          AND {STOCK_ONLY_SQL}
          AND {VALID_ROW_SQL}
        ORDER BY date DESC
        LIMIT {SESSION_PAIR_LIMIT}
        """,
        (symbol, target_date),
    ).fetchall()
    if len(rows) < SESSION_PAIR_LIMIT:
        return None
    d0, c0 = str(rows[0][0]), rows[0][1]
    d1, c1 = str(rows[1][0]), rows[1][1]
    if d0 != vni_d0 or d1 != vni_d1:
        return None
    if not (_is_finite_number(c0) and _is_finite_number(c1)) or float(c1) <= 0.0:
        return None
    return ((float(c0) - float(c1)) / float(c1)) * PCT_SCALE


def build_daily_brief_snapshot(
    *,
    as_of_date: str | None = None,
    screener_db: Path = DEFAULT_SCREENER_DB,
    financial_db: Path = DEFAULT_FINANCIAL_DB,
    top_n: int = DEFAULT_TOP_N,
    warmup: int = DEFAULT_WARMUP_SESSIONS,
) -> dict[str, Any]:
    """Tổng hợp snapshot JSON chuẩn hóa từ `regime_history`, `FeatureStore`, và `CompanyHealthV2`."""
    if not screener_db.exists():
        raise FileNotFoundError(f"Không tìm thấy screener DB: {screener_db}")

    uri = f"file:{screener_db.resolve().as_posix()}?mode=ro"
    warnings: list[str] = []
    with contextlib.closing(sqlite3.connect(uri, uri=True)) as conn:
        vni_d0, vni_d1 = _fetch_vni_sessions(conn, as_of_date)
        regime_row = _fetch_regime_row(conn, vni_d0)
        governor_progress = evaluate_governor_progress(regime_row)
        if regime_row is None:
            warnings.append("REGIME_ROW_MISSING")
        elif int(regime_row["is_degraded"]) != 0:
            warnings.append("REGIME_DEGRADED")

        fin_ready = _financial_db_ready(financial_db)
        if not fin_ready:
            warnings.append("FINANCIAL_DB_UNAVAILABLE")
            raw_candidates: list[dict[str, Any]] = []
            universe_count = 0
        else:
            health_engine = CompanyHealthV2()
            health_engine._db = str(financial_db.resolve())
            fs = FeatureStore(vni_d0, vni_d0, warmup=warmup, db=screener_db)
            universe = fs.rs_universe(vni_d0)
            universe_count = len(universe)
            raw_candidates = []
            for sym in universe:
                if not is_stock(sym):
                    continue
                rs_val = fs.rs(sym, vni_d0)
                close_val = fs.close(sym, vni_d0)
                adv20_shares = fs.adv20(sym, vni_d0)
                if not (_is_finite_number(rs_val) and _is_finite_number(close_val) and _is_finite_number(adv20_shares)):
                    continue
                if (
                    float(rs_val) < MOMENTUM_MIN_RS
                    or float(close_val) < MOMENTUM_MIN_CLOSE_VND
                    or float(adv20_shares) < MOMENTUM_MIN_ADV20_SHARES
                ):
                    continue
                state = health_engine.analyze(sym, vni_d0)
                archetype = state.archetype if state is not None else None
                if archetype is None or archetype.upper() == "DISTRESSED":
                    continue
                high_20 = fs.high_20(sym, vni_d0)
                breakout_20d = bool(
                    _is_finite_number(high_20)
                    and float(high_20) > 0.0
                    and float(close_val) > float(high_20) * BREAKOUT_HIGH20_MULT
                )
                ret_1d_pct = _compute_ret_1d_pct(conn, sym, vni_d0, vni_d0, vni_d1)
                raw_candidates.append(
                    {
                        "symbol": sym,
                        "close": float(close_val),
                        "ret_1d_pct": ret_1d_pct,
                        "rs": float(rs_val),
                        "adv20_shares": float(adv20_shares),
                        "adv20_vnd": float(adv20_shares) * float(close_val),
                        "health_archetype": archetype,
                        "breakout_20d": breakout_20d,
                    }
                )

    momentum_clean = filter_clean_momentum_candidates(raw_candidates, top_n=top_n)
    return {
        "schema_version": SCHEMA_VERSION,
        "as_of_date": vni_d0,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "universe_count": universe_count,
        "governor_progress": governor_progress,
        "momentum_clean": momentum_clean,
        "warnings": warnings,
    }


def write_daily_brief_snapshot(
    snapshot: Mapping[str, Any],
    out_path: Path = DEFAULT_SNAPSHOT_PATH,
) -> Path:
    """Ghi nguyên tử snapshot JSON ra đĩa (temp file cùng thư mục + `os.replace`)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=out_path.parent,
            prefix=".tmp_daily_brief_",
            suffix=".json",
            delete=False,
        ) as tmp_file:
            tmp_path = tmp_file.name
            json.dump(snapshot, tmp_file, ensure_ascii=False, indent=2)
            tmp_file.write("\n")
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_path, out_path)
        tmp_path = None
    finally:
        if tmp_path is not None and os.path.exists(tmp_path):
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)
    return out_path


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ptck.py daily-brief",
        description="Tạo snapshot JSON chuẩn hóa (governor_progress + momentum_clean) cho bản tin Zalo Rust.",
    )
    parser.add_argument("--date", type=str, default=None, help="Ngày phiên YYYY-MM-DD (mặc định phiên VNINDEX mới nhất)")
    parser.add_argument(
        "--out",
        type=str,
        default=str(DEFAULT_SNAPSHOT_PATH),
        help="Đường dẫn file JSON đầu ra (mặc định backend/data/daily_brief_snapshot.json)",
    )
    parser.add_argument("--top", type=int, default=DEFAULT_TOP_N, help="Số mã momentum_clean tối đa")
    parser.add_argument(
        "--warmup",
        type=int,
        default=DEFAULT_WARMUP_SESSIONS,
        help="Số phiên lịch sử nhìn lại cho FeatureStore (mặc định 252)",
    )
    parser.add_argument("--screener-db", type=str, default=str(DEFAULT_SCREENER_DB), help="Đường dẫn screener_cache.db")
    parser.add_argument("--financial-db", type=str, default=str(DEFAULT_FINANCIAL_DB), help="Đường dẫn financial_facts.db")
    parser.add_argument("--json", action="store_true", help="In toàn bộ snapshot JSON ra stdout")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entrypoint CLI `ptck.py daily-brief`: xuất snapshot và trả mã thoát chuẩn fail-closed."""
    args = _build_parser().parse_args(argv)
    try:
        snapshot = build_daily_brief_snapshot(
            as_of_date=args.date,
            screener_db=Path(args.screener_db),
            financial_db=Path(args.financial_db),
            top_n=args.top,
            warmup=args.warmup,
        )
        out_path = write_daily_brief_snapshot(snapshot, out_path=Path(args.out))
    except (FileNotFoundError, ValueError, sqlite3.Error) as exc:
        print(f"[DAILY-BRIEF] FAIL-CLOSED: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(snapshot, ensure_ascii=False, indent=2))
    else:
        print(
            f"[DAILY-BRIEF] OK as_of={snapshot['as_of_date']} momentum_clean={len(snapshot['momentum_clean'])} -> {out_path}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
