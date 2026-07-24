import os
import sys
import warnings
from pathlib import Path

# Pandas warnings suppression:
#   - ChainedAssignmentError (FutureWarning): from external libs (vnstock/yfinance).
#     These are harmless in pandas 2.x — the code works correctly, pandas just warns
#     about future 3.0 behavior. We suppress them to keep output clean.
#   - mode.chained_assignment = None: silences SettingWithCopyWarning from same sources.
#   - Pyarrow warning: pandas 3.0 will require pyarrow; not actionable for us now.
import pandas as pd
from dotenv import load_dotenv

pd.set_option('mode.chained_assignment', None)

# ChainedAssignmentError in pandas 2.x is raised as FutureWarning (not Warning subclass)
# so we filter by message + FutureWarning category for maximum coverage
warnings.filterwarnings('ignore', message='.*ChainedAssignmentError.*', category=FutureWarning)
warnings.filterwarnings('ignore', message='.*chained assignment.*', category=FutureWarning)
warnings.filterwarnings('ignore', message='Pyarrow will become a required dependency')

# Suppress vnstock/vnai version check noise in system output
os.environ.setdefault("VNSTOCK_QUIET", "1")
os.environ.setdefault("VNAI_QUIET", "1")

# 1. Định vị tọa độ Gốc (Bất chấp ngài chạy lệnh từ thư mục nào hoặc đóng gói .exe)
def _hydrate_path():
    """Path Hydrator v2.2: Auto-locate Project Root (frozen-safe)."""
    # ==============================================================================
    # WHY: Nuitka 4.1.3 on Python 3.14 does NOT reliably set sys.frozen.
    # Without this, onefile mode extracts to a temp dir and the AGENTS.md anchor
    # resolves to the temp dir, not the install dir. Databases are at the install
    # dir, not the temp dir, so the server crashes with "DB not found".
    # BOUNDARY: sys.frozen check + executable name + temp dir location fallback.
    # ==============================================================================
    import os as _os
    import tempfile as _tempfile
    _temp_root = Path(_tempfile.gettempdir()).resolve()
    is_frozen = (
        getattr(sys, 'frozen', False)
        or not Path(sys.executable).stem.lower().startswith("python")
    )
    if is_frozen:
        root_path = Path(sys.executable).resolve().parent
    else:
        current = Path(__file__).resolve().parent
        root_path = current
        while current != current.parent:
            if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
                root_path = current
                break
            current = current.parent
    # Safety: if root_path is inside a system temp dir (Nuitka onefile extraction),
    # fall back to sys.executable parent (install dir)
    try:
        resolved = root_path.resolve()
        if _temp_root in resolved.parents or resolved == _temp_root:
            root_path = Path(sys.executable).resolve().parent
    except (OSError, RuntimeError):
        pass
    if str(root_path) not in sys.path:
        sys.path.insert(0, str(root_path))
    return root_path

PROJECT_ROOT = _hydrate_path()

# 2. Nạp cấu hình từ .env (Nếu tệp tồn tại)
load_dotenv(PROJECT_ROOT / '.env')

# 3. Phân bổ các khu vực chiến lược
# ==============================================================================
# WHY: When frozen (Nuitka onefile), the extracted temp dir has source code
# but NO databases. The real databases are at the install dir next to the .exe.
# DATA_DIR must point to PROJECT_ROOT/backend/data where the installed binary lives.
# %LOCALAPPDATA%/PTCK_VN/data is only for runtime-generated files.
# ==============================================================================
# Ưu tiên lấy đường dẫn từ env (api_server.py set CUSTOM_DATA_PATH khi frozen)
data_path_env = os.getenv("CUSTOM_DATA_PATH")
if data_path_env:
    DATA_DIR = Path(data_path_env)
elif (PROJECT_ROOT / "backend" / "data").is_dir():
    DATA_DIR = PROJECT_ROOT / "backend" / "data"
else:
    DATA_DIR = PROJECT_ROOT / "data"

# LIBS_DIR may be at PROJECT_ROOT/libs or PROJECT_ROOT/backend/libs
if (PROJECT_ROOT / "libs").is_dir():
    LIBS_DIR = PROJECT_ROOT / "libs"
elif (PROJECT_ROOT / "backend" / "libs").is_dir():
    LIBS_DIR = PROJECT_ROOT / "backend" / "libs"
else:
    LIBS_DIR = PROJECT_ROOT / "libs"

# Đảm bảo thư mục Data luôn tồn tại
DATA_DIR.mkdir(parents=True, exist_ok=True)

# 4. Giải phóng xiềng xích "sys.path"
# Thêm Project Root trước để ưu tiên package chính (core/, data/, docs/)
root_path = str(PROJECT_ROOT)
if root_path not in sys.path:
    sys.path.insert(0, root_path)

# Chèn thư viện vnstock vào bộ nhớ ngay khi import config
vnstock_path = str(LIBS_DIR / "vnstock")
if vnstock_path not in sys.path:
    sys.path.insert(0, str(vnstock_path))

# Thêm LIBS_DIR vào sys.path để Nuitka tìm được canonical package
libs_root = str(LIBS_DIR)
if libs_root not in sys.path:
    sys.path.append(libs_root)

# Thêm backend/ vào sys.path để import được package 'src' sau project root
backend_dir = PROJECT_ROOT / "backend"
if backend_dir.is_dir() and str(backend_dir) not in sys.path:
    sys.path.append(str(backend_dir))

# =================================================================
# [PHASE 7.5] HARDENING CONFIGURATION
# =================================================================

RECOVERY_CONFIG = {
    "breadth_std_threshold": 8.0,
    "cooldown_days": 5,
    "min_index_drawdown": -8.0  # Percent from peak
}

MODEL_B_CONFIG = {
    "secondary_context": {
        "min_breadth": 25.0,
        "max_breadth_std": 14.0,
        "min_ma50_slope": -0.05,
        "slope_window": 5,
        "momentum_window": 5
    },
    "pullback_range": {
        "min_pct": -15.0,
        "max_pct": -3.0
    },
    "breadth_expansion": {
        "min_velocity": 0.0,
        "min_adv_dec_ratio": 1.2
    },
    "adaptive_rsi": {
        "low_vol": 42,
        "mid_vol": 39,
        "standard": 37
    }
}

# [SAFEGUARD]: DO NOT REMOVE DEBUG PRINTS HERE - Use env var DEBUG=1 to enable

