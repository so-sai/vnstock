"""
Breadth Service Layer v1.0
Độ rộng thị trường chi tiết — Market Pulse.
"""
import logging
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
    return root_path

PROJECT_ROOT = _hydrate_path()

import pandas as pd

from src.database.db_core import get_connection
from src.registry import registry

logger = logging.getLogger(__name__)


def get_breadth_analysis() -> dict:
    """
    Lấy phân tích độ rộng thị trường từ Breadth Engine.
    """
    try:
        result = registry.breadth_engine.run_breadth_analysis()
        if not result:
            return {"error": "No breadth data available"}

        health = result.get('health_score_ma20', 0)
        if health > 70:
            trend = "Bullish"
        elif health < 30:
            trend = "Bearish"
        else:
            trend = "Neutral"

        return {
            "date": result.get('date', ''),
            "totalActive": result.get('total_active', 0),
            "advancers": result.get('advancers', 0),
            "decliners": result.get('decliners', 0),
            "unchanged": result.get('unchanged', 0),
            "healthScoreMa20": health,
            "nh10Count": result.get('nh10_count', 0),
            "nh10Consistency3d": result.get('nh10_consistency_3d', 0),
            "trend": trend,
        }
    except Exception as e:
        logger.error(f"Breadth analysis failed: {e}")
        return {"error": str(e)}


def get_breadth_history(limit: int = 60) -> list:
    """
    Lấy lịch sử breadth_pct từ regime_history để vẽ biểu đồ.
    """
    try:
        with get_connection() as conn:
            df = pd.read_sql(f"""
                SELECT date, breadth_pct, breadth_velocity, status
                FROM regime_history
                WHERE breadth_pct IS NOT NULL
                ORDER BY date DESC
                LIMIT {limit}
            """, conn)

        if df.empty:
            return []

        df['date'] = pd.to_datetime(df['date'], format='mixed').dt.strftime('%Y-%m-%d')
        return df.to_dict(orient='records')
    except Exception as e:
        logger.error(f"Breadth history failed: {e}")
        return []
