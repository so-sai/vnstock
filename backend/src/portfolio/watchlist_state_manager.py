"""
Watchlist State Manager v2 — two separate layers:
  Layer 1: USER PINS — user's personal watchlist (persistent, manual)
  Layer 2: AI TIERS — system's dynamic recommendations (read-only, daily recompute)

No DEFAULT_STATE with hardcoded symbols. No merging of pins with AI tiers.
"""
import sys
from pathlib import Path


def _hydrate_path():
    if getattr(sys, 'frozen', False):
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
    if backend_dir.is_dir() and str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    return root_path

PROJECT_ROOT = _hydrate_path()

import json
import logging
from datetime import datetime
from typing import List, Optional

from src.database.db_core import get_connection

logger = logging.getLogger(__name__)

# Vietnamese tier labels (used when exposing AI recommendations)
TIER_LABELS = {
    'core': 'NHÓM ỔN ĐỊNH',
    'rotation': 'DÒNG TIỀN DẪN SÓNG',
    'opportunity': 'CƠ HỘI THEO DÕI',
}


class WatchlistStateManager:
    def __init__(self):
        self.state_dir = Path(str(PROJECT_ROOT)) / "backend" / "data" / "state"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.state_dir / "watchlist_state.json"
        self._state = None

    def _get_default(self) -> dict:
        return {
            "user_pins": [],
            "ai_tiers": {
                "core": [],
                "rotation": [],
                "opportunity": [],
            },
            "last_updated": datetime.now().strftime("%Y-%m-%d"),
            "version": 1,
        }

    def load(self) -> dict:
        if self._state is not None:
            return self._state
        if self.state_path.exists():
            try:
                with open(self.state_path, encoding='utf-8') as f:
                    data = json.load(f)
                if 'user_pins' in data:
                    self._state = data
                    return self._state
            except:
                pass
        self._state = self._get_default()
        self.save()
        return self._state

    def save(self, state: Optional[dict] = None) -> bool:
        if state:
            self._state = state
        if self._state is None:
            return False
        self._state['last_updated'] = datetime.now().strftime('%Y-%m-%d')
        try:
            with open(self.state_path, 'w', encoding='utf-8') as f:
                json.dump(self._state, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            logger.error(f"Save watchlist state failed: {e}")
            return False

    # --- Layer 1: User Pins ---

    def get_pins(self) -> List[str]:
        state = self.load()
        return state.get('user_pins', [])

    def add_pin(self, symbol: str) -> bool:
        state = self.load()
        if symbol not in state['user_pins']:
            state['user_pins'].append(symbol)
            state['version'] = state.get('version', 0) + 1
            return self.save(state)
        return True

    def remove_pin(self, symbol: str) -> bool:
        state = self.load()
        if symbol in state.get('user_pins', []):
            state['user_pins'] = [s for s in state['user_pins'] if s != symbol]
            state['version'] = state.get('version', 0) + 1
            return self.save(state)
        return True

    def is_pinned(self, symbol: str) -> bool:
        return symbol in self.get_pins()

    # --- Layer 2: AI Tiers (read-only snapshot from recommendation engine) ---

    def set_ai_tiers(self, core: list, rotation: list, opportunity: list):
        state = self.load()
        state['ai_tiers'] = {
            'core': core,
            'rotation': rotation,
            'opportunity': opportunity,
        }
        state['version'] = state.get('version', 0) + 1
        self.save(state)

    def get_ai_tiers(self) -> dict:
        state = self.load()
        return state.get('ai_tiers', {'core': [], 'rotation': [], 'opportunity': []})

    def sync_from_recommendations(self, recommendations: dict) -> bool:
        recs = recommendations.get('recommendations', {})
        core = [r['symbol'] for r in recs.get('core', [])]
        rotation = [r['symbol'] for r in recs.get('rotation', [])]
        opportunity = [r['symbol'] for r in recs.get('opportunity', [])]
        self.set_ai_tiers(core, rotation, opportunity)
        return True

    # --- Combined view ---

    def to_tiered_dict(self) -> dict:
        state = self.load()
        return {
            'user_pins': state.get('user_pins', []),
            'ai_tiers': state.get('ai_tiers', {'core': [], 'rotation': [], 'opportunity': []}),
            'tier_labels': TIER_LABELS,
        }


def _store_pin_history(symbol: str, action: str):
    try:
        with get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS watchlist_pin_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT,
                    action TEXT,
                    timestamp TEXT
                )
            """)
            conn.execute(
                "INSERT INTO watchlist_pin_history (symbol, action, timestamp) VALUES (?, ?, ?)",
                (symbol, action, datetime.now().isoformat())
            )
            conn.commit()
    except Exception as e:
        logger.warning(f"Store pin history error: {e}")


def export_state(target_date: Optional[str] = None) -> dict:
    today = target_date or datetime.now().strftime('%Y-%m-%d')
    manager = WatchlistStateManager()
    state = manager.load()

    result = {
        'date': today,
        'user_pins': state.get('user_pins', []),
        'ai_tiers': state.get('ai_tiers', {'core': [], 'rotation': [], 'opportunity': []}),
        'tier_labels': TIER_LABELS,
        'last_updated': state.get('last_updated', today),
        'version': state.get('version', 1),
    }

    out_path = Path(str(PROJECT_ROOT)) / "backend" / "data" / "state" / "watchlist_state.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"\nWatchlist state saved to: {out_path}")
    print(f"  User pins: {', '.join(result['user_pins'])}")
    ai = result['ai_tiers']
    print(f"  AI Core: {', '.join(ai.get('core', [])[:3])}")
    print(f"  AI Rotation: {', '.join(ai.get('rotation', [])[:3])}")
    print(f"  AI Opportunity: {', '.join(ai.get('opportunity', [])[:3])}")
    return result


if __name__ == "__main__":
    if sys.platform == "win32":
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    export_state()
