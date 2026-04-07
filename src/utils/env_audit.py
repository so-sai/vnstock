import sys
import os
from pathlib import Path

def _hydrate_path():
    """Zero-Friction Sentinel v2.1: Tự động định vị Project Root (Bulletproof Anchor)"""
    if getattr(sys, 'frozen', False):
        root_path = Path(sys.executable).resolve().parent
    else:
        current = Path(__file__).resolve().parent
        root_path = current
        while current != current.parent:
            if (current / ".kit").exists() or (current / "src").is_dir() or (current / "screener.py").exists():
                root_path = current
                break
            current = current.parent
    if str(root_path) not in sys.path:
        sys.path.insert(0, str(root_path))
    return root_path

PROJECT_ROOT = _hydrate_path()

def audit_environment():
    print(f"🕵️  [Environment Audit] Starting health check...")
    print(f"🏠 Project Root: {PROJECT_ROOT}")
    
    # 1. Check Libraries
    required_libs = ["pandas", "yfinance", "sqlite3", "pathlib", "dotenv", "vnstock"]
    print("\n📦 Checking Dependencies:")
    for lib in required_libs:
        try:
            if lib == "dotenv":
                import dotenv
            elif lib == "vnstock":
                # Check if vnstock is in libs/
                if str(PROJECT_ROOT / "libs" / "vnstock") not in sys.path:
                    sys.path.append(str(PROJECT_ROOT / "libs" / "vnstock"))
                import vnstock
            else:
                __import__(lib)
            print(f"   ✅ {lib:15} | OK")
        except ImportError:
            print(f"   ❌ {lib:15} | MISSING")
            
    # 2. Check Internal Modules
    print("\n🏛️ Checking Internal Modules:")
    try:
        import src.config
        print(f"   ✅ src.config      | OK (DATA_DIR: {src.config.DATA_DIR})")
        from src.database.db_core import get_connection
        print(f"   ✅ src.database    | OK")
    except Exception as e:
        print(f"   ❌ src modules      | FAIL: {e}")
        
    # 3. Check Database
    print("\n🗳️ Checking Vault (DB):")
    db_path = PROJECT_ROOT / "data" / "screener_cache.db"
    if db_path.exists():
        size_mb = os.path.getsize(db_path) / (1024 * 1024)
        print(f"   ✅ DB Path         | OK ({size_mb:.2f} MB)")
    else:
        print(f"   ❌ DB Path         | MISSING ({db_path})")

if __name__ == "__main__":
    audit_environment()

