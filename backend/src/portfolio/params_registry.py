"""
params_registry.py — Param Fingerprint Registry

Xây dựng kho dữ liệu params_hash từ decision_audit.jsonl.
Inverse lookup: cho regime → tìm params_hash có track record tốt nhất.
Không cần AI — thuần thống kê.
"""

import json
import logging
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


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
REGISTRY_PATH = PROJECT_ROOT / "backend" / "data" / "params_registry.json"

from src.alpha.delta_divergence import ALPHA_DEFAULT as AD

CONSERVATIVE_DEFAULTS = {r: {"alpha": AD[r], "label": f"Mặc định an toàn ({r})"} for r in AD}

MARKET_IMPACT_THRESHOLD = 0.1  # 10% of total capital max per slice


def set_registry_path(p: Path):
    global REGISTRY_PATH
    REGISTRY_PATH = p


def set_audit_path(p: Path):
    global AUDIT_PATH
    AUDIT_PATH = p


def build_registry() -> dict:
    """Quét decision_audit.jsonl → registry nhóm theo (params_hash, regime)."""
    if not AUDIT_PATH.exists():
        logger.warning("[REGISTRY] Audit file not found: %s", AUDIT_PATH)
        return {
            "registry": {},
            "best_by_regime": {},
            "conservative_defaults": CONSERVATIVE_DEFAULTS,
            "meta": {"total_hashes": 0, "source": AUDIT_PATH.name, "built_at": datetime.now().isoformat()},
        }

    reg: dict[str, dict[str, dict]] = defaultdict(
        lambda: defaultdict(
            lambda: {
                "trade_count": 0,
                "first_seen": None,
                "last_seen": None,
                "decision_to": defaultdict(int),
                "trigger_machine": 0,
                "trigger_human": 0,
                "confidence_sum": 0.0,
                "delta_sa_sum": 0.0,
            }
        )
    )

    try:
        with open(str(AUDIT_PATH), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                h = entry.get("params_hash", "unknown")
                r = entry.get("regime", "UNKNOWN")
                bucket = reg[h][r]
                bucket["trade_count"] += 1
                ts = entry.get("ts", "")
                if bucket["first_seen"] is None or ts < bucket["first_seen"]:
                    bucket["first_seen"] = ts
                if bucket["last_seen"] is None or ts > bucket["last_seen"]:
                    bucket["last_seen"] = ts
                bucket["decision_to"][entry.get("to", "N/A")] += 1
                if entry.get("trigger", "").startswith("human"):
                    bucket["trigger_human"] += 1
                else:
                    bucket["trigger_machine"] += 1
                bucket["confidence_sum"] += entry.get("confidence", 0.0)
                bucket["delta_sa_sum"] += entry.get("delta_sa", 0.0)
    except Exception as e:
        logger.warning("[REGISTRY] Read audit failed: %s", e)

    registry = {}
    for h in reg:
        registry[h] = {}
        for r in reg[h]:
            b = dict(reg[h][r])
            b["decision_to"] = dict(b["decision_to"])
            tc = b["trade_count"]
            b["avg_confidence"] = round(b["confidence_sum"] / tc, 4) if tc > 0 else 0.0
            b["avg_delta_sa"] = round(b["delta_sa_sum"] / tc, 4) if tc > 0 else 0.0
            del b["confidence_sum"]
            del b["delta_sa_sum"]
            registry[h][r] = b

    best_by_regime = {}
    for r in CONSERVATIVE_DEFAULTS:
        candidates = [(h, registry[h].get(r, {}).get("trade_count", 0)) for h in registry if r in registry[h]]
        if candidates:
            best_hash = max(candidates, key=lambda x: x[1])[0]
            best_by_regime[r] = {
                "params_hash": best_hash,
                "trade_count": registry[best_hash][r]["trade_count"],
                "decisions": registry[best_hash][r]["decision_to"],
            }
        else:
            best_by_regime[r] = None

    return {
        "registry": registry,
        "best_by_regime": best_by_regime,
        "conservative_defaults": CONSERVATIVE_DEFAULTS,
        "market_impact_threshold": MARKET_IMPACT_THRESHOLD,
        "meta": {
            "total_hashes": len(registry),
            "source": AUDIT_PATH.name,
            "built_at": datetime.now().isoformat(),
        },
    }


def save_registry(data: dict) -> Path:
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return REGISTRY_PATH


def load_or_build() -> dict:
    if REGISTRY_PATH.exists():
        try:
            return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    data = build_registry()
    save_registry(data)
    return data


def lookup_params(regime: str) -> dict:
    """Inverse lookup: regime → params_hash + alpha.

    Zero Match → conservative default + flag human_review.
    """
    data = load_or_build()
    best = data.get("best_by_regime", {}).get(regime)
    if best is not None:
        return {
            "params_hash": best["params_hash"],
            "alpha": AD.get(regime, 0.5),
            "source": "registry",
            "trade_count": best["trade_count"],
            "human_review": False,
        }
    return {
        "params_hash": "conservative_default",
        "alpha": AD.get(regime, 0.5),
        "source": "conservative_default",
        "trade_count": 0,
        "human_review": True,
        "note": (
            f"Không tìm thấy dữ liệu lịch sử cho regime '{regime}'. Dùng α={AD.get(regime, 0.5)}. Vui lòng kiểm tra thủ công."
        ),
    }


def in_bao_cao(regime: str | None = None):
    """In báo cáo registry ra console."""
    data = load_or_build()
    reg = data.get("registry", {})
    best = data.get("best_by_regime", {})
    defaults = data.get("conservative_defaults", {})

    print("\n" + "=" * 70)
    print("  SỔ TAY DẤU VÂN TAY THAM SỐ (PARAM FINGERPRINT REGISTRY)")
    print("=" * 70)
    print(f"  Tổng số params_hash: {data['meta']['total_hashes']}")
    print(f"  Nguồn:              {data['meta']['source']}")
    print(f"  Xây dựng lúc:       {data['meta']['built_at']}")
    print()

    if regime:
        target_regimes = [regime]
    else:
        target_regimes = list(defaults.keys())

    for r in target_regimes:
        b = best.get(r)
        print(f"  ── Regime: {r} {'─' * (55 - len(r))}")
        if b:
            print(f"    * Tốt nhất:      {b['params_hash']}")
            print(f"    * Số lệnh:        {b['trade_count']}")
            print(f"    * Phân bố QĐ:     {b['decisions']}")
        else:
            d = defaults.get(r, {})
            print(f"    * Zero match — dùng mặc định: α={d.get('alpha', '?')}")
            print(f"    * {d.get('label', '')}")
        print()

    print("  ── Top params_hash theo tổng số lệnh ──")
    hash_totals = [(h, sum(reg[h][r2]["trade_count"] for r2 in reg[h])) for h in reg]
    hash_totals.sort(key=lambda x: -x[1])
    for i, (h, tc) in enumerate(hash_totals[:5], 1):
        print(f"    {i}. {h:<18} {tc} lệnh")
    print("=" * 70)
