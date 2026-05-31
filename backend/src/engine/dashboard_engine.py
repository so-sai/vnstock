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
            if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
                root_path = current
                break
            current = current.parent
    if str(root_path) not in sys.path:
        sys.path.insert(0, str(root_path))
    return root_path

PROJECT_ROOT = _hydrate_path()
import pandas as pd
import os
import glob
from datetime import datetime
from typing import List, Dict, Any
import src.config

def generate_dashboard() -> str:
    """
    Dashboard Engine v2.0
    Chuyển đổi báo cáo CSV Supreme Alpha mới nhất thành Dashboard HTML Premium.
    """
    print("🎨 Đang khởi tạo Dashboard Engine v2.0...")
    
    # 1. Tìm báo cáo CSV mới nhất
    report_dir = os.path.join(src.config.DATA_DIR, "output", "reports")
    list_of_files = glob.glob(os.path.join(report_dir, "supreme_alpha_report_*.csv"))
    
    if not list_of_files:
        print("⚠️ Không tìm thấy báo cáo CSV nào. Hãy chạy elite_scanner.py trước.")
        return ""
    
    latest_file = max(list_of_files, key=os.path.getctime)
    print(f"📊 Đang đọc báo cáo: {os.path.basename(latest_file)}")
    
    df = pd.read_csv(latest_file)
    
    # 2. Đọc Template HTML
    template_path = os.path.join(src.config.PROJECT_ROOT, "src", "templates", "dashboard_v2.html")
    if not os.path.exists(template_path):
        print("⚠️ Không tìm thấy file template dashboard_v2.html.")
        return ""
        
    with open(template_path, 'r', encoding='utf-8') as f:
        html_template = f.read()

    # 3. Chuẩn bị Dữ liệu Meta (Nấc 0 & 1)
    market_phase = df['Market Phase'].iloc[0] if not df.empty else "N/A"
    breadth = df['Breadth'].iloc[0] if not df.empty else "N/A"
    buy_count = len(df[df['Action'].str.contains("BUY|ACCUMULATE")])
    
    # 4. Render Table Body (Sử dụng logic thay thế đơn giản thay cho Jinja2 để tương thích 100%)
    # Lưu ý: Ở đây tôi build chuỗi HTML thô để nhúng vào template
    table_rows = ""
    for _, row in df.iterrows():
        rs_class = "rs-high" if float(row['RS Score']) > 90 else ""
        action_class = "buy" if "BUY" in str(row['Action']) else ("watch" if "WATCH" in str(row['Action']) else "hold")
        
        row_html = f"""
        <tr>
            <td class="symbol-cell">{row['Symbol']}</td>
            <td><span class="rs-badge {rs_class}">{row['RS Score']}</span></td>
            <td>{row['RVOL']:.2f}</td>
            <td class="f-acc">{row['Foreign 10D Acc (Bn)']}</td>
            <td><span class="sector-tag">{row['Sector']}</span></td>
            <td><span class="action-chip {action_class}">{row['Action']}</span></td>
        </tr>
        """
        table_rows += row_html

    # 5. Thay thế các biến trong Template
    final_html = html_template.replace("{{ timestamp }}", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    final_html = final_html.replace("{{ market_phase }}", str(market_phase))
    final_html = final_html.replace("{{ breadth }}", str(breadth))
    final_html = final_html.replace("{{ buy_count }}", f"{buy_count} mã")
    
    # Thay thế phần tbody (Dùng regex hoặc split/join đơn giản)
    start_tag = "<tbody>"
    end_tag = "</tbody>"
    parts = final_html.split(start_tag)
    if len(parts) > 1:
        prefix = parts[0] + start_tag
        suffix = parts[1].split(end_tag)[1]
        final_html = prefix + table_rows + end_tag + suffix

    # 6. Xuất bản Dashboard
    output_path = os.path.join(src.config.DATA_DIR, "output", "dashboard_v2.html")
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(final_html)
        
    print(f"✨ Dashboard v2.0 đã sẵn sàng: {output_path}")
    return output_path

if __name__ == "__main__":
    generate_dashboard()

