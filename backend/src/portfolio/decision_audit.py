"""
decision_audit.py — Append-only JSONL Audit Trail

Ghi mọi chuyển trạng thái quyết định kèm params_hash (chữ ký config).
Fire-and-forget: không block luồng thực thi chính.
"""

import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Schema nghiêm ngặt ─────────────────────────────────
REQUIRED_FIELDS = {"ts", "id", "from", "to", "trigger", "params_hash", "confidence", "delta_sa", "regime"}
ALLOWED_TRIGGERS = {"machine", "human", "system", "unknown"}
ALLOWED_STATES = {"THAM GIA FULL", "THAM GIA", "THAM GIA DO", "QUAN SAT", "GIAM RUI RO", "DUNG NGOAI", "PENDING"}


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
AUDIT_PATH = PROJECT_ROOT / "backend" / "data" / "decision_audit.jsonl"


def set_audit_path(p: Path):
    global AUDIT_PATH
    AUDIT_PATH = p


def _valid_entry(entry: dict) -> bool:
    """Kiểm tra schema của entry trước khi ghi."""
    if not isinstance(entry, dict):
        return False
    if not REQUIRED_FIELDS.issubset(entry.keys()):
        missing = REQUIRED_FIELDS - entry.keys()
        logger.warning("[AUDIT] Missing fields: %s", missing)
        return False
    if entry.get("trigger") not in ALLOWED_TRIGGERS:
        logger.warning("[AUDIT] Invalid trigger: %s", entry.get("trigger"))
        return False
    if entry.get("to") not in ALLOWED_STATES:
        logger.warning("[AUDIT] Invalid to-state: %s", entry.get("to"))
        return False
    return True


def log_transition(
    prev_state: str,
    new_state: str,
    decision_id: str,
    params_hash: str,
    confidence: float,
    delta_sa: float,
    regime: str,
    trigger: str = "machine",
) -> None:
    """Ghi 1 dòng JSONL — fire-and-forget."""
    if prev_state == new_state:
        return  # không log nếu không có transition

    entry = {
        "ts": datetime.now(UTC).isoformat(),
        "id": decision_id,
        "from": prev_state,
        "to": new_state,
        "trigger": trigger,
        "params_hash": params_hash,
        "confidence": round(confidence, 4),
        "delta_sa": round(delta_sa, 4),
        "regime": regime,
    }
    if not _valid_entry(entry):
        logger.error("[AUDIT] Invalid entry, skip: %s", entry)
        return

    try:
        AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(str(AUDIT_PATH), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
    except Exception as e:  # noqa: BLE001 - cố ý bắt rộng để fallback/phòng thủ an toàn
        logger.warning("[AUDIT] Write failed: %s", e)


def read_entries(limit: int | None = None) -> list[dict]:
    """Đọc các entry hợp lệ từ file audit."""
    if not AUDIT_PATH.exists():
        return []
    valid = []
    try:
        with open(str(AUDIT_PATH), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if _valid_entry(entry):
                        valid.append(entry)
                except json.JSONDecodeError, ValueError:
                    continue
    except Exception as e:  # noqa: BLE001 - cố ý bắt rộng để fallback/phòng thủ an toàn
        logger.warning("[AUDIT] Read failed: %s", e)
    return valid[-limit:] if limit else valid


def integrity_check() -> tuple[int, int]:
    """Kiểm tra + cắt bỏ dòng hỏng ở cuối file.

    Returns: (total_valid, total_corrupt)
    """
    if not AUDIT_PATH.exists():
        return 0, 0

    last_valid_pos = 0
    total = 0
    corrupt = 0
    try:
        with open(str(AUDIT_PATH), encoding="utf-8") as f:
            pos = 0
            for line in f:
                total += 1
                line_stripped = line.strip()
                if not line_stripped:
                    pos = f.tell()
                    continue
                try:
                    entry = json.loads(line_stripped)
                    if _valid_entry(entry):
                        last_valid_pos = f.tell()
                    else:
                        corrupt += 1
                except json.JSONDecodeError, ValueError:
                    corrupt += 1
                    last_valid_pos = pos  # pos at start of corrupt line
                    break  # corruption only at end
                pos = f.tell()
    except Exception as e:  # noqa: BLE001 - cố ý bắt rộng để fallback/phòng thủ an toàn
        logger.warning("[AUDIT] Integrity check failed: %s", e)
        return total, corrupt

    # Truncate at last valid line if corruption found at end
    if corrupt > 0 and last_valid_pos > 0:
        try:
            with open(str(AUDIT_PATH), "r+", encoding="utf-8") as f:
                f.truncate(last_valid_pos)
            logger.info("[AUDIT] Truncated %d corrupt line(s) at end of file", corrupt)
        except Exception as e:  # noqa: BLE001 - cố ý bắt rộng để fallback/phòng thủ an toàn
            logger.warning("[AUDIT] Truncate failed: %s", e)

    return total - corrupt, corrupt
