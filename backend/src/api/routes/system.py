"""
Session Info API — endpoint nhẹ cho Header Frontend.
Chỉ trả về: phiên tác chiến (target_date), thời gian cập nhật, regime status ngắn gọn.
Không gọi engine nặng — đọc thẳng regime_history (1 query) → trả JSON tức thì.

Dùng cho: hiển thị "Phiên tác chiến: DD/MM/YYYY - HH:MM" trên Header.
"""
import io
import sys
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")


def _hydrate_path():
    if getattr(sys, "frozen", False):
        root = Path(sys.executable).resolve().parent
    else:
        current = Path(__file__).resolve().parent.parent.parent.parent
        root = current
        while current != current.parent:
            if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
                root = current
                break
            current = current.parent
    for p in (root, root / "backend"):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    return root


PROJECT_ROOT = _hydrate_path()

from src.core.canonical_output_adapter import localize_output
from src.database.db_core import get_connection

router = APIRouter()


@router.get("/session-info")
async def get_session_info():
    """
    Trả thông tin phiên tác chiến gọn cho Frontend Header.
    1 query duy nhất vào regime_history — KHÔNG gọi engine, KHÔNG compute.
    """
    try:
        with get_connection() as conn:
            cur = conn.execute("""
                SELECT date, status, regime_score, breadth_pct
                FROM regime_history
                ORDER BY date DESC
                LIMIT 1
            """)
            row = cur.fetchone()

        if row is None:
            # Fallback: chưa có regime_history → trả "no data" để UI biết
            return localize_output({
                "target_date": None,
                "session_time": None,
                "regime_status": "NO_DATA",
                "regime_score": None,
                "breadth_pct": None,
                "is_stale": True,
                "server_now": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })

        target_date = row["date"]  # YYYY-MM-DD
        # Format thành DD/MM/YYYY - HH:MM (lấy HH:MM từ server_now vì regime_history chỉ lưu date)
        server_now = datetime.now()
        session_time = server_now.strftime("%H:%M")
        target_date_vn = datetime.strptime(target_date, "%Y-%m-%d").strftime("%d/%m/%Y")

        return localize_output({
            "target_date": target_date,                # YYYY-MM-DD (cho code)
            "target_date_vn": target_date_vn,           # DD/MM/YYYY (cho UI)
            "session_label": f"{target_date_vn} - {session_time}",  # "14/06/2026 - 23:45"
            "session_time": session_time,
            "regime_status": row["status"],
            "regime_score": row["regime_score"],
            "breadth_pct": row["breadth_pct"],
            "is_stale": False,
            "server_now": server_now.strftime("%Y-%m-%d %H:%M:%S"),
        })
    except Exception as e:
        # KHÔNG throw 500 — Frontend cần luôn có data để hiển thị
        return localize_output({
            "target_date": None,
            "session_label": "Mất kết nối CSDL",
            "regime_status": "ERROR",
            "is_stale": True,
            "error": str(e)[:200],
            "server_now": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
