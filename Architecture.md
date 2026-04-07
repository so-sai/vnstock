# 🏛️ ALPHA FORGE V1.0 GOLD - ARCHITECTURE BLUEPRINT

Bản thiết kế này quy định cấu trúc phân lớp (L0-L5) và các quy tắc bất biến nhằm bảo vệ sự ổn định của hệ thống trước mọi sai số từ API bên thứ 3.

---

## 🧩 1. CẤU TRÚC PHÂN LỚP (LAYERED ARCHITECTURAL STACK)

Hệ thống được vận hành theo mô hình **Modular Decoupling** (Tách biệt hoàn toàn phần nạp dữ liệu và phần tính toán logic).

### 📀 Layer 1 (L0/L1): DATA VAULT (CHÂN LÝ DỮ LIỆU) - `data/`
- **`screener_cache.db`**: SQLite database lưu trữ 110k+ dòng dữ liệu `adj_close`. Đây là "Nguồn Sự Thật Duy Nhất" cho các Engine.
- **`orders_today.txt`**: Kết quả xuất lệnh tín hiệu sau mỗi phiên quét.

### 🌉 Layer 2 (L2): VENDOR & BRIDGE (TẦNG NẠP DỮ LIỆU) - `libs/` & Root
- **`libs/vnstock/`**: **VÙNG VENDOR** (Patched Fork). Nơi thực hiện Patch `auth.py` và Mock `vnai.py` để đảm bảo độc lập với upstream.
- **`screener.py`**: **THE BRIDGE - API SURVIVAL ARMOR (L2.5)**. Script thực hiện lệnh gọi `libs/vnstock` -> Đổ dữ liệu vào Vault. Đã được cấy ghép giáp bảo vệ Rate Limit.

### 🧠 Layer 3 (L3): COGNITION LAYER (BỘ NHỚ NHẬN THỨC) - `.kit/`
- Chứa các Knowledge Items (KIs) và lịch sử hành vi của hệ thống AI. **KHÔNG CHẠM VÀO** để đảm bảo Intelligent Continuity.

### ⚙️ Layer 4 (L4): ANALYTICS & LOGIC (BỘ NÃO SNIPER) - `src/engine/`
- **`rs_ranker.py`**: [V1.0 Refactor] Xếp hạng sức mạnh tương đối (RS Score).
- **`sector_ranker.py`**: [V1.0 Refactor] Phân tích mật độ và luân chuyển dòng tiền ngành.
- **`backtest_engine.py`**: Vectorized Stress Test 2022-2026.

### 🛡️ Layer 4.5 (L4.5): FORENSIC & SHADOW LEDGER - `src/portfolio/` & `src/utils/`
- **`shadow_tracker.py`**: Module đối soát thực tế, độc lập hoàn toàn với luồng API tự động.
- **`my_portfolio.json`**: Sổ cái VNĐ chính xác theo lệnh khớp.
- **`macro_sensors.py`**: [V1.0 Move] Cảm biến vĩ mô và cảnh báo ngoại lệ.

### 🖥️ Layer 5 (L5): INTERFACE & CONTROL - Root & `src/gui/`
- **`main.py`**: Command Center - Điểm khỏi chạy App (CLI Dashboard).
- **`heatmap_engine.py`**: Sinh heatmap hiển thị tầng quan sát.

---

## 🛡️ 2. HAI LUỒNG VẬN HÀNH SONG SONG (DUAL-FLOW OPS)

### 📡 Luồng 1: Tín Hiệu Tự Động (Theoretical Signal)
`L2 (libs) → L2.5 (screener.py) → L0 (Vault) → L4 (Engine) → orders_today.txt`
> **Rủi ro:** API vnstock có thể lỗi hoặc chậm.

### 🛡️ Luồng 2: Shadow Ledger (Real Reality)
`User Input (Thủ công) → src/portfolio/my_portfolio.json → src/portfolio/shadow_tracker.py`
> **Ưu điểm:** Tuyệt đối chính xác theo lệnh khớp thực tế. Tự động cảnh báo Cut-loss -5%.

---

## ⚖️ 3. CÁC QUY TẮC BẤT BIẾN (ARCHITECTURAL INVARIANTS)

1.  **IRON GATE LAW (10B/10B):** `min_liquidity = 10_000_000_000 VNĐ`. Phải vượt qua bộ lọc thanh khoản kép mới được vào Alpha Universe.
2.  **SENTINEL LAW:** Mọi file `.py` phải dùng `_hydrate_path()` để xác định Project Root.
3.  **VENDOR PARTITION LAW:** Mọi bản Patch/Mock phải nằm trong `libs/vnstock/`.
4.  **SHADOW PRIORITY LAW:** Shadow Ledger luôn thắng API khi có xung đột dữ liệu.
5.  **STATELESS WORKER LAW:** Mỗi module làm đúng một việc, không side-effect ẩn.

---
*V1.0 Unified Backend SEALED - Antigravity AI* 🛡️🎯🚀⚖️🥇
