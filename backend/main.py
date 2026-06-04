import sys
import os
import io
from pathlib import Path

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

def _hydrate_path():
    """Zero-Friction Sentinel v2.0: Tự động định vị Project Root hỗ trợ cả .exe"""
    if getattr(sys, 'frozen', False):
        root_path = Path(sys.executable).resolve().parent
    else:
        current = Path(__file__).resolve().parent
        root_path = current
        while current != current.parent:
            if (current / ".kit").exists() or (current / "src").exists():
                root_path = current
                break
            current = current.parent
    if str(root_path) not in sys.path:
        sys.path.insert(0, str(root_path))
    return root_path

PROJECT_ROOT = _hydrate_path()
import src.config
from src.engine.breadth_engine import run_breadth_analysis
from src.engine.heatmap_engine import run_sector_heatmap
from src.engine.rs_ranker import calculate_rs_score
from src.engine.screener_logic import run_screener

def main():
    """
    Hệ điều hành PTCK_VNSTOCK (V1.5.2 - Zero Friction)
    Lộ trình: Market Pulse -> Sector Heatmap -> RS Ranking -> Screener V1
    """
    # Ép Terminal dùng UTF-8 để hiển thị Emoji an toàn trên Windows
    if sys.platform == "win32":
        try:
            import os
            os.system('chcp 65001 > nul')
        except:
            pass

    print("\n" + "💎" * 25)
    print("💎 DIAMOND SHIELD TRADING SYSTEM 💎")
    print("💎" * 25 + "\n")

    # 1. Market Breadth (Nhịp đập thị trường)
    pulse = run_breadth_analysis()
    
    # 2. Sector Heatmap (Dòng tiền ngành)
    heatmap = run_sector_heatmap()

    # 3. RS Ranking (Sức mạnh tương quan - Alpha V2)
    rs_results = calculate_rs_score()

    # 4. Screener V1 (Điểm nổ Breakout)
    signals = run_screener()

    print("\n" + "="*50)
    print("✅ TOÀN BỘ HỆ THỐNG VẬN HÀNH HOÀN HẢO (SENTINEL STATUS)")
    print("="*50)

if __name__ == "__main__":
    main()
