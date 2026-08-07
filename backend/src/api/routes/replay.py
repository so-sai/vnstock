import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query


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
    backend_dir = root_path / "backend"
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    return root_path


PROJECT_ROOT = _hydrate_path()

from src.core.canonical_output_adapter import localize_output
from src.services.replay_service import get_replay_timeline

router = APIRouter()


@router.get("/timeline")
async def replay_timeline_endpoint(limit: int = Query(365, ge=30, le=1000)):
    """Serves combined daily replay data: VNINDEX price + MA50/200 + regime state."""
    try:
        data = get_replay_timeline(limit=limit)
        return localize_output(data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Replay timeline error: {str(e)}")
