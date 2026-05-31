# 🛡️ IPO MARKET STRUCTURE SIGNAL DETECTOR — Kiến trúc Tập Trung

## 📋 Tóm tắt

Hệ thống phát hiện IPO không phải như một **tin doanh nghiệp thường** mà như một **tín hiệu cấu trúc thị trường** ảnh hưởng đến:
- Chế độ thanh khoản (Expansion vs Tightening)
- Chu kỳ thị trường (Bull vs Late-cycle warning)
- Áp lực luân chuyển (VN30 rebalance → Midcap/Smallcap pressure)
- Khẩu vị rủi ro chung (Retail euphoria detection)

---

## 🏗️ Kiến trúc 4 Lớp (4-Layer Architecture)

```
┌─────────────────────────────────────────────────────────────────┐
│ TẦNG 1: ĐỊNH GIÁ (Valuation Risk Layer)                         │
├─────────────────────────────────────────────────────────────────┤
│ - Aftermarket performance (Nếu +30% → peak narrative warning)   │
│ - Concentration risk (Nhiều IPO tốt cùng lúc)                  │
│ - Output: valuation_risk_score (0-100)                         │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│ TẦNG 2: THANH KHOẢN (Liquidity Absorption Layer)                │
├─────────────────────────────────────────────────────────────────┤
│ - Quy mô IPO vs tổng vốn hóa → ipo_intensity (CAO/TRUNG/THAP)  │
│ - Capital absorption trend (TANG_MANH / ON_DINH / GIAM)         │
│ - Secondary market pressure (0-100%)                            │
│ - Output: Sức mạnh của "vòi nước hút tiền"                      │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│ TẦNG 3: LUÂN CHUYỂN (Rotation & Narrative Layer)                │
├─────────────────────────────────────────────────────────────────┤
│ - Narrative heat (BAT_THUONG / BINH_THUONG / THAP)              │
│ - Rotation risk (CAO / TRUNG_BINH / THAP)                       │
│ - Midcap/Smallcap pressure (%)                                  │
│ - Output: Rủi ro bị ép bán để mua IPO vào rổ chỉ số            │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│ TẦNG 4: CHU KỲ (Regime Classification Layer)                    │
├─────────────────────────────────────────────────────────────────┤
│ - Liquidity regime (DONG_TIEN_MO_RONG / TANG_GIAN / THOAI_LUI)   │
│ - Regime confidence (0-1.0)                                      │
│ - Output: Phân biệt Expansion vs Late-cycle euphoria            │
└─────────────────────────────────────────────────────────────────┘
```

---

## 📁 Cấu trúc File (File Structure)

```
backend/src/
├── engine/
│   ├── ipo_engine.py                    ← Core: 4-layer analysis
│   ├── ipo_integration_guide.py          ← Hướng dẫn + ví dụ DMX
│   └── decision_engine.py                ← CẦN CHỈNH SỬA (thêm IPO logic)
│
├── models/
│   ├── ipo_models.py                    ← Pydantic models
│   └── models.py                         ← CẦN CHỈNH SỬA (thêm IpoSignal)
│
├── services/
│   ├── ipo_signal_service.py            ← Integration layer
│   ├── decision_engine.py                ← CẦN CHỈNH SỬA (gọi ipo_service)
│   └── actionable_intelligence_service.py ← CẦN CHỈNH SỬA (thêm IPO info)
│
└── (data/)
    └── CẦN CHỈNH SỬA database schema:
        - ipo_calendar (symbol, listing_date, market_cap, sector)
```

---

## 🔄 Data Flow (Luồng Dữ liệu)

```
Daily Update (16:30 EOD)
    ↓
[1] Fetch IPO history (last 90 days) + aftermarket returns
    ↓
[2] IpoEngine.analyze()
    ├─ Tầng 1: Valuation risk (aftermarket return % + concentration)
    ├─ Tầng 2: Liquidity impact (market cap IPO vs total market)
    ├─ Tầng 3: Rotation risk (breadth + narrative heat)
    └─ Tầng 4: Regime classification (expand vs tighten)
    ↓
[3] IpoSignalService.get_daily_ipo_signal()
    ├─ Convert to API response (camelCase)
    ├─ Calculate regime_modifier (0.7-1.2)
    └─ Generate action_command
    ↓
[4] Integration:
    ├─ → decision_engine: regime_score *= regime_modifier
    ├─ → frontend: IpoSignalResponse JSON
    └─ → logging: Daily traffic light (🟢/🟡/🔴)
```

---

## 🟢🟡🔴 Tín Hiệu Giao Tiếp (Traffic Light Signal)

### 🟢 XANH — An toàn, tiếp tục

**Điều kiện:**
- IPO aftermarket +15% trở lên
- Secondary market pressure < 30%
- Breadth score > 0.55
- Rotation risk = THAP
- Valuation risk < 30

**Hành động:**
- PRIMARY ACTION: BUY / HOLD
- Margin Setting: 1.2x (tăng nhẹ)
- Risk Level: THAP

---

### 🟡 VÀNG — Thận trọng, đóng margin

**Điều kiện:**
- IPO thành công nhưng secondary market liquidity tụt
- Breadth 0.4-0.55 (trung bình)
- Rotation risk = TRUNG_BINH
- Hoặc: liquidity regime = TANG_GIAN

**Hành động:**
- PRIMARY ACTION: HOLD
- Margin Setting: 1.0x (normal)
- Risk Level: TRUNG_BINH

---

### 🔴 ĐỎ — Nguy hiểm, kích hoạt phòng thủ

**Điều kiện:**
- IPO nóng nhưng Midcap/Smallcap chết liquidity (bị ép bán)
- Breadth score < 0.4 + narrative heat BAT_THUONG
- Rotation risk = CAO
- Hoặc: valuation risk > 70 + capital absorption TANG_MANH

**Hành động:**
- PRIMARY ACTION: SELL / REDUCE
- Margin Setting: 0.5x (giảm nửa)
- Risk Level: CAO
- Sector Watch: Midcap, Smallcap, Secondary volume

---

## 📊 Ví dụ: DMX IPO 14.360 Tỷ (2026-05-25)

### Input (Dữ liệu thị trường)

| Tham số | Giá trị | Ý nghĩa |
|--------|--------|--------|
| Quy mô IPO | 14.360T | 0.32% vốn hóa (VỪA PHẢI) |
| Aftermarket return | +25% | Retail phát cuồng (peak narrative) |
| Breadth score | 0.38 | Giảm mạnh (⚠️ warning) |
| Secondary volume | 0.85x | Giảm 15% (hút tiền) |
| Midcap pressure | 65% | Bị ép bán mạnh |

### Output (Tín hiệu)

```yaml
TẦNG 1 (ĐỊNH GIÁ):
  Valuation Risk Score: 65
  Giải thích: IPO +25% = peak narrative, cảnh báo quá cao

TẦNG 2 (THANH KHOẢN):
  IPO Intensity: CAO
  Capital Absorption: TANG_MANH
  Secondary Pressure: 50%
  Giải thích: Tiền bị hút, secondary market yếu

TẦNG 3 (LUÂN CHUYỂN):
  Narrative Heat: BAT_THUONG
  Rotation Risk: CAO
  Midcap Pressure: 65%
  Giải thích: Retail phát cuồng, Midcap bị ép bán

TẦNG 4 (CHU KỲ):
  Liquidity Regime: TANG_GIAN
  Regime Confidence: 0.78
  Giải thích: Breadth yếu + tiền giảm = tightening regime
```

### Traffic Light: 🔴 ĐỎ

**Khẩu lệnh:**
- Primary Action: **SELL / REDUCE** (hạ vị thế)
- Margin Setting: **0.5x** (đóng margin nửa)
- Risk Level: **CAO**
- Sector Watch: **Midcap, Smallcap, Retail chains**
- Interpretation: "🔴 IPO nóng + Midcap/Smallcap chết liquidity. Kích hoạt phòng thủ, tránh cổ phiếu nhỏ vốn hóa."

### Regime Modifier: **0.82x**

```
Nếu decision_engine có:
  regime_score = 0.50 (RANGING)
  
Điều chỉnh IPO:
  regime_score_adjusted = 0.50 × 0.82 = 0.41 (CRISIS mode!)
  
Kết quả: Decision engine tự động chuyển từ CAUTIOUS BUY → CASH/STANDBY
         MỚI CẦN CAN THIỆP THỦ CÔNG!
```

---

## 🔧 Cách Triển Khai (Implementation Steps)

### Bước 1: Thêm IPO Engine vào Decision Engine

**File:** `backend/src/engine/decision_engine.py`

```python
# Thêm import
from engine.ipo_engine import IpoEngine
from services.ipo_signal_service import IpoSignalService

# Trong __init__:
self.ipo_engine = IpoEngine()
self.ipo_service = IpoSignalService(self.ipo_engine)

# Trong decide() — trước khi gọi Model A/B:
ipo_signal = self.ipo_service.get_daily_ipo_signal(
    current_date=decision_date,
    active_ipo_symbols=kwargs.get('active_ipo_symbols', []),
    aftermarket_returns=kwargs.get('aftermarket_returns', {}),
    breadth_score=breadth_score,
    secondary_volume_ratio=kwargs.get('secondary_volume_ratio', 1.0)
)

regime_score_adjusted = regime_score * ipo_signal.regime_modifier
```

### Bước 2: Tạo IPO Calendar Table

**Database schema:**
```sql
CREATE TABLE ipo_calendar (
    symbol TEXT PRIMARY KEY,
    listing_date DATETIME,
    listing_price FLOAT,
    listing_volume INTEGER,
    market_cap_listing FLOAT,  -- VND billions
    sector TEXT,
    exchange TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

### Bước 3: Chạy hàng ngày

**File:** `backend/src/daily_updater.py`

```python
# Thêm vào schedule:
def update_ipo_signals():
    # Load IPO lịch sử từ DB
    # Tính aftermarket returns
    # Gọi ipo_service.get_daily_ipo_signal()
    # Lưu kết quả
    pass

schedule.every().day.at("16:30").do(update_ipo_signals)
```

---

## 📚 Tham Khảo Nhanh

| Thành phần | File | Mục đích |
|-----------|------|---------|
| **Engine** | `ipo_engine.py` | 4-layer analysis + traffic light |
| **Models** | `ipo_models.py` | Pydantic dataclasses |
| **Service** | `ipo_signal_service.py` | API response + regime modifier |
| **Integration** | `ipo_integration_guide.py` | Hướng dẫn + demo |
| **Decision** | `decision_engine.py` | 🔴 CẦN CHỈNH: thêm ipo_service |
| **Daily** | `daily_updater.py` | 🔴 CẦN CHỈNH: schedule IPO update |

---

## ✅ Checklist Triển Khai

- [x] Tạo ipo_engine.py (4-layer analysis)
- [x] Tạo ipo_models.py (data structures)
- [x] Tạo ipo_signal_service.py (integration)
- [x] Tạo ipo_integration_guide.py (documentation + example)
- [ ] Chỉnh sửa decision_engine.py (thêm ipo_service)
- [ ] Chỉnh sửa daily_updater.py (schedule IPO update)
- [ ] Tạo ipo_calendar table trong database
- [ ] Test với DMX IPO data
- [ ] Deploy & monitor

---

## 🎯 Tính Năng Chính

✅ **Phát hiện tự động:** Các IPO lớn lên sàn → tự động tính tín hiệu
✅ **Điều chỉnh động:** regime_modifier tự động ảnh hưởng decision_engine
✅ **3 màu tín hiệu:** 🟢🟡🔴 dễ hiểu cho người dùng
✅ **Dự báo chu kỳ:** Phân biệt Expansion vs Late-cycle
✅ **Giám sát Midcap/Smallcap:** Cảnh báo rotation risk
✅ **Khẩu lệnh thực chiến:** Hành động cụ thể (BUY/HOLD/SELL)

---

## 🛡️ Bảo vệ

Hệ thống này sẽ **tự động bảo vệ** khỏi:
- 📊 Peak narrative pricing (định giá quá cao)
- 💧 Liquidity vacuum (hốnước hút tiền)
- 🌊 Rotation panic (Midcap/Smallcap bị ép)
- 📉 Late-cycle crash (chu kỳ chót)

---

**Pháo đài Sentinel v1.0 — Bây giờ có "Radar IPO"! 🛡️⚖️🏛️🚀**
