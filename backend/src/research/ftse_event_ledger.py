"""ftse_event_ledger.py — FTSE GEIS Event Observation Layer (READ-ONLY research).

Mục đích: biến phiên công bố FTSE GEIS 21/08/2026 thành một NATURAL EXPERIMENT
có ghi nhận bằng chứng, thay vì lý do phá vỡ quyết định STAND ASIDE của Governor.

Ranh giới (bắt buộc):
  - READ-ONLY đối với decision pipeline: module này KHÔNG import src.engine /
    src.governor / decision_guard / orchestrator; chỉ ĐỌC artifact JSON do hệ
    thống xuất (data/output/*.json) và ghi vào SQLite ledger riêng.
  - Baseline FROZEN: freeze_baseline() ghi MỘT lần cho event_date; gọi lại raise
    BaselineFrozenError. Không sửa baseline sau khi event diễn ra.
  - Zero-Hallucination: mọi con số phải có provenance. Số từ artifact hệ thống
    được đánh dấu theo tên file nguồn; số từ tin tức người dùng phải truyền qua
    news_inputs kèm trường "source" (vd "USER_NEWS_2026-08-20_unverified").
  - KHÔNG sinh tín hiệu mua/bán. evaluate_gates() chỉ trả NHÃN MÔ TẢ
    (FTSE_EVENT_ROTATION / MARKET_REGIME_CHANGE_CANDIDATE / INSUFFICIENT_EVIDENCE)
    phục vụ nghiên cứu truyền dẫn catalyst → breadth → recovery gates.

Câu hỏi nghiên cứu (không phải dự báo):
  Event alpha = Return(selected) − Return(matched controls)
  và tách: event-driven rotation ≠ market-wide recovery.

Layers:
  A. Surprise      : expected list (04/2026, unverified) vs official list (21/08).
  B. Price/Flow    : D+1 / D+5 / D+20 cho từng nhóm surprise + market.
  C. Transmission  : breadth, liquidity, structure pillars, MA reclaim,
                     recovery gates — chỉ lớp C mới có khả năng ảnh hưởng regime.

Usage:
  python -X utf8 backend/src/research/ftse_event_ledger.py status
  python -X utf8 backend/src/research/ftse_event_ledger.py freeze
  python -X utf8 backend/src/research/ftse_event_ledger.py gates --json metrics.json
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sqlite3
import sys
from pathlib import Path

# ── Path hydration (Sentinel v2.1 Anchor) ──────────────────────────────────
_current = Path(__file__).resolve().parent
PROJECT_ROOT = _current
while PROJECT_ROOT != PROJECT_ROOT.parent:
    if (PROJECT_ROOT / "AGENTS.md").exists() and (PROJECT_ROOT / "backend").is_dir():
        break
    PROJECT_ROOT = PROJECT_ROOT.parent
BACKEND = PROJECT_ROOT / "backend"
DATA_DIR = BACKEND / "data"
OUTPUT_DIR = DATA_DIR / "output"
LEDGER_DB = DATA_DIR / "ftse_event_ledger.db"

EVENT_DATE = "2026-08-21"  # FTSE GEIS announcement (catalyst)
PRE_EVENT_DATE = "2026-08-20"  # baseline EOD freeze

# ── Layer A labels ──────────────────────────────────────────────────────────
SURPRISE_EXPECTED_INCLUDED = "EXPECTED_INCLUDED"
SURPRISE_INCLUDED = "SURPRISE_INCLUDED"
SURPRISE_EXPECTED_EXCLUDED = "EXPECTED_EXCLUDED"
NON_EVENT_CONTROL = "NON_EVENT_CONTROL"

# ── Gate labels (mô tả research, KHÔNG phải action giao dịch) ───────────────
GATE_ROTATION = "FTSE_EVENT_ROTATION"
GATE_REGIME_CHANGE = "MARKET_REGIME_CHANGE_CANDIDATE"
GATE_INSUFFICIENT = "INSUFFICIENT_EVIDENCE"
_ALLOWED_GATES = {GATE_ROTATION, GATE_REGIME_CHANGE, GATE_INSUFFICIENT}

# Ngưỡng mô tả cho gate research (KHÔNG nối vào Governor/DecisionGuard):
CONTROL_NEUTRAL_MAX_PCT = 0.5  # control ≤ +0.5% coi là "≈ flat/down"
BREADTH_STALL_TOL_PP = 1.0  # breadth chưa mở rộng quá +1.0pp so baseline
BREADTH_EXPANSION_MIN_PP = 5.0  # mở rộng ≥ +5.0pp mới tính là market-wide

_SCHEMA = """
CREATE TABLE IF NOT EXISTS event_baseline (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_date TEXT NOT NULL UNIQUE,
    pre_event_date TEXT,
    frozen_at TEXT NOT NULL,
    params_hash TEXT,
    payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS surprise_classification (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    class_label TEXT NOT NULL,
    expected_source TEXT,
    actual_source TEXT,
    recorded_at TEXT NOT NULL,
    UNIQUE(event_date, symbol)
);
CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_date TEXT NOT NULL,
    window TEXT NOT NULL,
    subject TEXT NOT NULL,
    class_label TEXT,
    metrics_json TEXT NOT NULL,
    source TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    UNIQUE(event_date, window, subject)
);
CREATE TABLE IF NOT EXISTS gate_evaluations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_date TEXT NOT NULL,
    window TEXT NOT NULL,
    label TEXT NOT NULL,
    rationale_json TEXT NOT NULL,
    inputs_json TEXT NOT NULL,
    evaluated_at TEXT NOT NULL
);
"""


class BaselineFrozenError(RuntimeError):
    """Baseline cho event_date đã bị khóa — cấm ghi đè."""


class ProvenanceError(ValueError):
    """Thiếu provenance/source — Zero-Hallucination: không nhận số vô nguồn."""


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or LEDGER_DB))
    conn.executescript(_SCHEMA)
    return conn


def _ensure_schema() -> None:
    conn = _connect()
    conn.commit()
    conn.close()


def _require_source(source: str) -> str:
    if not isinstance(source, str) or not source.strip():
        raise ProvenanceError("trường 'source' (provenance) là bắt buộc")
    return source.strip()


# ── Layer A: Surprise classification ────────────────────────────────────────


def classify_surprise(
    expected: list[str],
    actual: list[str],
    controls: list[str] | None = None,
) -> dict[str, str]:
    """Phân loại 4 nhóm: expected∩actual, surprise, excluded, control."""
    exp = {s.strip().upper() for s in expected if s and s.strip()}
    act = {s.strip().upper() for s in actual if s and s.strip()}
    if not exp or not act:
        raise ValueError("expected và actual đều phải khác rỗng để phân loại surprise")
    out: dict[str, str] = {}
    for s in sorted(exp & act):
        out[s] = SURPRISE_EXPECTED_INCLUDED
    for s in sorted(act - exp):
        out[s] = SURPRISE_INCLUDED
    for s in sorted(exp - act):
        out[s] = SURPRISE_EXPECTED_EXCLUDED
    for s in sorted({c.strip().upper() for c in (controls or []) if c and c.strip()} - exp - act):
        out[s] = NON_EVENT_CONTROL
    return out


def compute_event_alpha(selected_returns: list[float], control_returns: list[float]) -> float:
    """Event alpha = mean(selected) − mean(matched controls)."""
    if not selected_returns or not control_returns:
        raise ValueError("selected/control returns phải khác rỗng")
    return sum(selected_returns) / len(selected_returns) - sum(control_returns) / len(control_returns)


# ── Gates (mô tả research) ───────────────────────────────────────────────────

_GATE_REQUIRED_KEYS = (
    "selected_return_pct",
    "control_return_pct",
    "breadth_pct",
    "breadth_baseline_pct",
    "liquidity_change_pct",
    "structure_pillars",
    "ma_reclaim",
    "recovery_gates_pass",
)


def evaluate_gates(metrics: dict) -> dict:
    """Nhãn mô tả kết quả truyền dẫn catalyst. KHÔNG trả action mua/bán.

    Rotation : selected ↑ AND controls ≈ flat/down AND breadth vẫn yếu.
    Regime   : selected ↑ AND breadth mở rộng AND liquidity ↑ AND structure
               repair AND MA reclaim AND recovery gates pass.
    Còn lại  : INSUFFICIENT_EVIDENCE.
    """
    missing = [k for k in _GATE_REQUIRED_KEYS if k not in metrics]
    if missing:
        raise ProvenanceError(f"thiếu metrics bắt buộc: {missing}")

    selected_up = float(metrics["selected_return_pct"]) > 0.0
    controls_not_confirming = float(metrics["control_return_pct"]) <= CONTROL_NEUTRAL_MAX_PCT
    breadth_delta_pp = float(metrics["breadth_pct"]) - float(metrics["breadth_baseline_pct"])
    breadth_weak = breadth_delta_pp <= BREADTH_STALL_TOL_PP
    breadth_expanded = breadth_delta_pp >= BREADTH_EXPANSION_MIN_PP
    liquidity_up = float(metrics["liquidity_change_pct"]) > 0.0
    structure_repaired = int(metrics["structure_pillars"]) >= 1
    ma_reclaim = bool(metrics["ma_reclaim"])
    recovery_pass = bool(metrics["recovery_gates_pass"])

    rationale = {
        "selected_up": selected_up,
        "controls_not_confirming": controls_not_confirming,
        "breadth_delta_pp": round(breadth_delta_pp, 2),
        "breadth_weak": breadth_weak,
        "breadth_expanded": breadth_expanded,
        "liquidity_up": liquidity_up,
        "structure_repaired": structure_repaired,
        "ma_reclaim": ma_reclaim,
        "recovery_gates_pass": recovery_pass,
    }

    if selected_up and breadth_expanded and liquidity_up and structure_repaired and ma_reclaim and recovery_pass:
        label = GATE_REGIME_CHANGE
    elif selected_up and controls_not_confirming and breadth_weak:
        label = GATE_ROTATION
    else:
        label = GATE_INSUFFICIENT
    assert label in _ALLOWED_GATES
    return {"label": label, "rationale": rationale}


# ── Freeze baseline ──────────────────────────────────────────────────────────


def _read_json(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"thiếu artifact hệ thống: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def freeze_baseline(
    output_dir: Path | None = None,
    db_path: Path | None = None,
    news_inputs: list[dict] | None = None,
) -> dict:
    """Chụp nguyên trạng artifact EOD pre-event vào ledger. Ghi MỘT lần.

    news_inputs (tuỳ chọn): số liệu báo chí người dùng cung cấp — mỗi item PHẢI
    có trường "source"; module không tự chế số flow.
    """
    out = Path(output_dir) if output_dir else OUTPUT_DIR
    fd_raw = _read_json(out / "final_decision.json")
    st_raw = _read_json(out / "structural_state.json")
    snap_raw = _read_json(out / "snapshot_index.json")
    if not isinstance(snap_raw, list) or not snap_raw:
        raise FileNotFoundError(f"snapshot_index.json rỗng hoặc sai cấu trúc: {out}")
    snap_last = snap_raw[-1]

    if news_inputs is not None:
        for item in news_inputs:
            if not isinstance(item, dict) or not item.get("source"):
                raise ProvenanceError("mỗi news_input phải có trường 'source'")

    payload = {
        "event_date": EVENT_DATE,
        "pre_event_date": PRE_EVENT_DATE,
        "frozen_at": _now(),
        "sources": {
            "final_decision": "data/output/final_decision.json",
            "structural_state": "data/output/structural_state.json",
            "snapshot_index": "data/output/snapshot_index.json (entry cuối)",
        },
        "final_decision": fd_raw,
        "structural_state": st_raw,
        "snapshot_last_entry": snap_last,
        "news_inputs": news_inputs,
        "note": (
            "Baseline frozen PRE-EVENT. Không sửa sau khi event diễn ra. "
            "Số trong news_inputs là tin tức chưa kiểm chứng, tách khỏi system outputs."
        ),
    }
    conn = _connect(db_path)
    try:
        exists = conn.execute("SELECT 1 FROM event_baseline WHERE event_date=?", (EVENT_DATE,)).fetchone()
        if exists:
            raise BaselineFrozenError(f"baseline {EVENT_DATE} đã frozen — cấm ghi đè (append-only research)")
        conn.execute(
            "INSERT INTO event_baseline (event_date, pre_event_date, frozen_at, params_hash, payload_json) VALUES (?,?,?,?,?)",
            (
                EVENT_DATE,
                PRE_EVENT_DATE,
                payload["frozen_at"],
                fd_raw.get("params_hash"),
                json.dumps(payload, ensure_ascii=False),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return payload


def load_baseline(db_path: Path | None = None) -> dict | None:
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT payload_json FROM event_baseline WHERE event_date=?", (EVENT_DATE,)).fetchone()
    finally:
        conn.close()
    return json.loads(row[0]) if row else None


# ── Observations & classification records ───────────────────────────────────


def record_observation(
    window: str,
    subject: str,
    metrics: dict,
    source: str,
    class_label: str | None = None,
    db_path: Path | None = None,
) -> bool:
    """Append-only observation cho 1 (window, subject). Trả True nếu ghi mới.

    Key bất biến: (event_date, window, subject) — window/subject đều normalized
    uppercase nên 'd+1' và 'D+1' va chạm cùng một key. Trùng key → REJECT
    (INSERT OR IGNORE), KHÔNG BAO GIỜ ghi đè outcome đã ghi.
    """
    _require_source(source)
    if not subject or not str(subject).strip():
        raise ValueError("subject là bắt buộc")
    json.dumps(metrics)  # fail-fast nếu metrics không JSON-able
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "INSERT OR IGNORE INTO observations "
            "(event_date, window, subject, class_label, metrics_json, source, recorded_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                EVENT_DATE,
                str(window).strip().upper(),
                str(subject).strip().upper(),
                class_label,
                json.dumps(metrics, ensure_ascii=False),
                source.strip(),
                _now(),
            ),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def record_surprise(
    expected: list[str],
    actual: list[str],
    expected_source: str,
    actual_source: str,
    controls: list[str] | None = None,
    db_path: Path | None = None,
) -> int:
    """Ghi toàn bộ phân loại surprise (4 nhóm) vào ledger. Trả số symbol ghi mới."""
    _require_source(expected_source)
    _require_source(actual_source)
    classified = classify_surprise(expected, actual, controls=controls)
    conn = _connect(db_path)
    inserted = 0
    try:
        for sym, cls in classified.items():
            cur = conn.execute(
                "INSERT OR IGNORE INTO surprise_classification "
                "(event_date, symbol, class_label, expected_source, actual_source, recorded_at) "
                "VALUES (?,?,?,?,?,?)",
                (EVENT_DATE, sym, cls, expected_source.strip(), actual_source.strip(), _now()),
            )
            inserted += cur.rowcount
        conn.commit()
    finally:
        conn.close()
    return inserted


def load_observations(db_path: Path | None = None) -> list[dict]:
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT event_date, window, subject, class_label, metrics_json, source, recorded_at FROM observations ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "event_date": r[0],
            "window": r[1],
            "subject": r[2],
            "class_label": r[3],
            "metrics": json.loads(r[4]),
            "source": r[5],
            "recorded_at": r[6],
        }
        for r in rows
    ]


def load_surprises(db_path: Path | None = None) -> list[dict]:
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT event_date, symbol, class_label, expected_source, actual_source, recorded_at "
            "FROM surprise_classification ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "event_date": r[0],
            "symbol": r[1],
            "class_label": r[2],
            "expected_source": r[3],
            "actual_source": r[4],
            "recorded_at": r[5],
        }
        for r in rows
    ]


def record_gate_evaluation(window: str, result: dict, db_path: Path | None = None) -> None:
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO gate_evaluations (event_date, window, label, rationale_json, inputs_json, evaluated_at) "
            "VALUES (?,?,?,?,?,?)",
            (
                EVENT_DATE,
                window,
                result["label"],
                json.dumps(result.get("rationale", {}), ensure_ascii=False),
                json.dumps(result.get("inputs", {}), ensure_ascii=False),
                _now(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


# ── CLI (mini, độc lập — không đụng ptck.py) ────────────────────────────────


def _cli_status() -> None:
    base = load_baseline()
    print("=" * 60)
    print("  FTSE EVENT LEDGER — STATUS (READ-ONLY RESEARCH)")
    print("=" * 60)
    print(f"  Event date     : {EVENT_DATE} (pre-event freeze: {PRE_EVENT_DATE})")
    print(f"  Baseline frozen: {'YES @ ' + base['frozen_at'] if base else 'NO — chạy: freeze'}")
    if base:
        fd = base["final_decision"]
        print(f"  params_hash    : {fd.get('params_hash')}")
        print(f"  Quyết định     : {fd.get('quyet_dinh')} | recovery={fd.get('recovery_status')}")
        print(f"  Healing illusion: {fd.get('delta_divergence', {}).get('healing_illusion')}")
    print(f"  Surprises      : {len(load_surprises())} symbols")
    print(f"  Observations   : {len(load_observations())} rows")
    print("=" * 60)


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError, OSError, ValueError:
        pass
    parser = argparse.ArgumentParser(description="FTSE Event Observation Ledger (READ-ONLY)")
    parser.add_argument(
        "action",
        nargs="?",
        default="status",
        choices=["status", "freeze", "gates"],
    )
    parser.add_argument("--json", type=Path, default=None, help="metrics JSON cho gates")
    parser.add_argument("--window", default="D+1", help="window tag cho gates")
    args = parser.parse_args()

    if args.action == "status":
        _cli_status()
    elif args.action == "freeze":
        payload = freeze_baseline()
        print(
            f"[freeze] OK: {EVENT_DATE} frozen @ {payload['frozen_at']} "
            f"(params_hash={payload['final_decision'].get('params_hash')})"
        )
    elif args.action == "gates":
        if args.json is None:
            parser.error("gates cần --json metrics.json")
        metrics = json.loads(args.json.read_text(encoding="utf-8"))
        res = evaluate_gates(metrics)
        res["inputs"] = metrics
        record_gate_evaluation(args.window, res)
        print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
