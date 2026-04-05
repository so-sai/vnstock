import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# 1. Định vị tọa độ Gốc (Bất chấp ngài chạy lệnh từ thư mục nào hoặc đóng gói .exe)
def _hydrate_path():
    """Zero-Friction Sentinel v2.1: Tự động định vị Project Root (Bulletproof Anchor)"""
    if getattr(sys, 'frozen', False):
        root_path = Path(sys.executable).resolve().parent
    else:
        current = Path(__file__).resolve().parent
        root_path = current
        while current != current.parent:
            if (current / ".kit").exists() or (current / "src").is_dir() or (current / "seed_data.py").exists():
                root_path = current
                break
            current = current.parent
    if str(root_path) not in sys.path:
        sys.path.insert(0, str(root_path))
    return root_path

PROJECT_ROOT = _hydrate_path()

# 2. Nạp cấu hình từ .env (Nếu tệp tồn tại)
load_dotenv(PROJECT_ROOT / '.env')

# 3. Phân bổ các khu vực chiến lược
# Ưu tiên lấy đường dẫn từ .env, nếu không có thì dùng mặc định: PROJECT_ROOT/data
data_path_env = os.getenv("CUSTOM_DATA_PATH")
DATA_DIR = Path(data_path_env) if data_path_env else PROJECT_ROOT / "data"
LIBS_DIR = PROJECT_ROOT / "libs"

# Đảm bảo thư mục Data luôn tồn tại
DATA_DIR.mkdir(parents=True, exist_ok=True)

# 4. Giải phóng xiềng xích "sys.path"
# Chèn thư viện vnstock vào bộ nhớ ngay khi import config
vnstock_path = str(LIBS_DIR / "vnstock")
if vnstock_path not in sys.path:
    sys.path.insert(0, vnstock_path)

# Thêm Root vào để Python nhận diện thư mục src/
root_path = str(PROJECT_ROOT)
if root_path not in sys.path:
    sys.path.insert(0, root_path)

# [SAFEGUARD]: DO NOT REMOVE DEBUG PRINTS HERE - Use env var DEBUG=1 to enable
