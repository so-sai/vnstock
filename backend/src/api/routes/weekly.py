"""Weekly Cognitive Report — API Route.

Read-only aggregation endpoint. Single call = full weekly snapshot.
Never modifies system state.
"""

import sqlite3
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException


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

from src.core.canonical_output_adapter import localize_output
from src.services.weekly_cognitive_report import build_weekly_report

router = APIRouter()


@router.get("/")
async def get_weekly_report():
    """Get the full weekly cognitive snapshot (Vietnamese output).

    Returns:
        {
            "timestamp": "...",
            "market": { ... },   # all string values localized to VI
            "gold": { ... },     # all string values localized to VI
            "trust": { ... },    # all string values localized to VI
            "summary_vi": "..."
        }
    """
    try:
        return localize_output(build_weekly_report())
    except (TypeError, ValueError, KeyError, AttributeError, sqlite3.Error) as e:
        raise HTTPException(
            status_code=500,
            detail=f"Weekly report aggregation failed: {str(e)}",
        )
