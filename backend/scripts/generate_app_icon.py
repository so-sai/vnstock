from PIL import Image, ImageDraw
import os

def build_governor_icon(output_path: str = None):
    if output_path is None:
        # Resolve relative to project root
        current_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(os.path.dirname(current_dir))
        output_path = os.path.join(project_root, "frontend", "src-tauri", "icons", "app-icon.png")

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    
    size = (1024, 1024)
    # Nền tối Dark Slate (#0F172A)
    img = Image.new("RGBA", size, (15, 23, 42, 255))
    draw = ImageDraw.Draw(img)

    # 1. Vẽ Khiên Governor (Shield Outer Contour)
    shield_points = [
        (512, 120),   # Đỉnh giữa
        (820, 220),   # Góc trên phải
        (760, 620),   # Thân phải
        (512, 900),   # Đáy nhọn
        (264, 620),   # Thân trái
        (204, 220),   # Góc trên trái
    ]
    # Viền khiên xanh Cyan (#0EA5E9) & Lõi Xanh Đậm
    draw.polygon(shield_points, fill=(30, 41, 59, 255), outline=(14, 165, 233, 255))
    
    # 2. Vẽ Nến Nhật Định Lượng bên trong Khiên
    # Nến Xanh (Up Candle - Tăng trưởng)
    draw.line([(400, 320), (400, 700)], fill=(34, 197, 94, 255), width=12) # Bấc nến
    draw.rounded_rectangle([360, 420, 440, 620], radius=8, fill=(34, 197, 94, 255)) # Thân nến

    # Nến Đỏ (Down Candle / Risk Control)
    draw.line([(624, 280), (624, 660)], fill=(239, 68, 68, 255), width=12) # Bấc nến
    draw.rounded_rectangle([584, 340, 664, 520], radius=8, fill=(239, 68, 68, 255)) # Thân nến

    img.save(output_path, "PNG")
    print(f"OK: Da tao file Icon goc 1024x1024px tai: {output_path}")

if __name__ == "__main__":
    build_governor_icon()
