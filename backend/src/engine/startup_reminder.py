import sys
from pathlib import Path
from datetime import datetime

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
    for p in (root_path, root_path / "backend"):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    return root_path

PROJECT_ROOT = _hydrate_path()

from src.database.db_core import get_connection

_SESSION_SHOWN = False


def kiem_tra_va_nhac_nho():
    global _SESSION_SHOWN
    if _SESSION_SHOWN:
        return
    _SESSION_SHOWN = True

    bay_gio = datetime.now()
    ngay_hien_tai = bay_gio.strftime("%Y-%m-%d")
    gio_hien_tai = bay_gio.hour

    try:
        with get_connection() as conn:
            dong = conn.execute(
                "SELECT MAX(date) FROM macro_history WHERE variable = 'VGB10Y'"
            ).fetchone()
            ngay_du_lieu = dong[0] if dong else None
    except Exception:
        ngay_du_lieu = None

    print()
    print("=" * 55)
    print("  PHÁO ĐÀI SENTINEL — NHẮC NHỞ TÁC CHIẾN")
    print("=" * 55)

    if ngay_du_lieu == ngay_hien_tai:
        print("  [+] DU LIEU HOM NAY DA SAN SANG")
        print("  Go: python ptck.py flow-map")
    elif gio_hien_tai >= 16:
        print("  [!] Da qua 16:00 nhung chua co du lieu hom nay.")
        print("  Go: python ptck.py daily-update")
    else:
        print("  [~] Thi truong chua dong cua.")
        print(f"  Du lieu gan nhat: {ngay_du_lieu or 'khong co'}")
        print("  He thong tu dong cap nhat luc 16:00.")
    print("=" * 55)
    print()
