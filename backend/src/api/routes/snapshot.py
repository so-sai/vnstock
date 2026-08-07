"""
/api/v1/snapshot — Delta Divergence Index + Snapshot Index
Phase: DDI Gate (Delta Divergence Index + Atomic Parameter Identity)
"""

import json
import logging
import sqlite3
import sys
from pathlib import Path

from fastapi import APIRouter

logger = logging.getLogger(__name__)

from src.core.canonical_output_adapter import localize_output

router = APIRouter()


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


@router.get("/ddi")
async def get_ddi():
    """Tính DDI từ snapshot hiện tại (live)."""
    try:
        from src.core.market_snapshot import tao_anh_chup

        anh_chup = tao_anh_chup()
        ddi = anh_chup.get("delta_divergence", {})
        ddi["params_hash"] = anh_chup.get("params_hash", "unresolved")
        return localize_output(ddi)
    except (ImportError, AttributeError, TypeError, ValueError, KeyError, sqlite3.Error) as e:
        logger.warning("[SNAPSHOT] DDI computation failed: %s", e)
        return localize_output({"error": str(e)})


@router.get("/index")
async def get_snapshot_index():
    """Đọc snapshot_index.json — lịch sử snapshots."""
    idx_path = PROJECT_ROOT / "backend" / "data" / "output" / "snapshot_index.json"
    if not idx_path.exists():
        return localize_output({"entries": [], "count": 0})
    try:
        data = json.loads(idx_path.read_text(encoding="utf-8"))
        return localize_output({"entries": data, "count": len(data)})
    except (json.JSONDecodeError, OSError, TypeError, ValueError, KeyError) as e:
        return localize_output({"error": str(e)})


@router.get("/params")
async def get_params_registry():
    """Xem params_registry hiện tại + params_hash."""
    try:
        from src.alpha.delta_divergence import build_params_registry
        from src.utils.params_hash import make_params_hash

        registry = build_params_registry()
        return localize_output(
            {
                "params_registry": registry,
                "params_hash": make_params_hash(registry),
            }
        )
    except (ImportError, AttributeError, TypeError, ValueError, KeyError) as e:
        return localize_output({"error": str(e)})
