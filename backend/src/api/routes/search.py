import sqlite3
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query


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
    if backend_dir.exists() and str(backend_dir) not in sys.path:
        sys.path.append(str(backend_dir))
    return root_path

PROJECT_ROOT = _hydrate_path()
import src.config

router = APIRouter()


@router.get("/search")
async def instant_search(
    q: str = Query(..., min_length=1, max_length=50, description="Từ khóa tìm kiếm mã/tên CK"),
    limit: int = Query(10, ge=1, le=50),
):
    """Tìm kiếm toàn văn siêu tốc bằng FTS5 — gõ mã (VCB) hoặc tên ngành (Ngân hàng)."""
    db_path = src.config.DATA_DIR / "screener_cache.db"
    if not db_path.exists():
        raise HTTPException(404, "Chưa có dữ liệu — hãy chạy daily-update trước")

    safe_query = _sanitize_fts_query(q)

    try:
        conn = sqlite3.connect(str(db_path), timeout=5)
        conn.execute("PRAGMA query_only=1")
        cursor = conn.cursor()

        sql = """
            SELECT symbol, icb_name2, icb_name3, icb_name4
            FROM symbol_fts
            WHERE symbol_fts MATCH ?
            ORDER BY rank
            LIMIT ?
        """
        cursor.execute(sql, (safe_query, limit))
        rows = cursor.fetchall()
        conn.close()

        return {
            "query": q,
            "count": len(rows),
            "results": [
                {"symbol": r[0], "icb_name2": r[1], "icb_name3": r[2], "icb_name4": r[3]}
                for r in rows
            ],
        }
    except sqlite3.OperationalError as e:
        raise HTTPException(500, f"FTS5 query failed: {e}")


def _sanitize_fts_query(raw: str) -> str:
    """Biến đổi từ khóa thành FTS5 query an toàn, hỗ trợ gõ không dấu/tiếng Việt.

    FTS5 unicode61 mặc định có remove_diacritics=1, strip dấu Latin cơ bản
    (â→a, ế→e, ọ→o, vv). Nhưng 'đ' là ký tự RIÊNG trong bảng chữ cái Latin
    mở rộng, không phải "d + dấu" — unicode61 KHÔNG map đ→d.

    Hàm này:
    1. Map đ/Đ → d (bù điểm mù của unicode61)
    2. Lower-then-upper để chuẩn hoá
    3. Wrap FTS5 prefix query
    """
    if not raw:
        return ""
    q = raw.strip()
    # Xử lý riêng 'đ' và 'Đ' trước khi uppercase (đã lower để cover cả 2)
    q = q.replace("đ", "d").replace("Đ", "D")
    q = q.upper()
    if not q:
        return ""
    # Escape ký tự đặc biệt FTS5 bằng cách wrap trong "..." — ký tự " bên trong
    # được nhân đôi (chuẩn FTS5 string escaping).
    parts = q.split()
    if len(parts) == 1:
        safe = parts[0].replace('"', '""')
        return f'"{safe}"*'
    return " AND ".join(f'"{p.replace(chr(34), chr(34)*2)}"*' for p in parts)
