# SPEC: TREND ENGINE — PRE-REGISTERED (2021–2025)

**Status:** 🔒 **FROZEN — v1.1 (khóa cứng)** — không được sửa sau khi nhìn thấy kết quả backtest
**Date:** 2026-10-09
**Version:** v1.1 · xem changelog §13.4 (không có v1.2; biến thể ⇒ file spec riêng)
**Related:** `SPEC_DUAL_TRACK_DECISION_2026-10.md`
**Pre-registration anchor:** xem `SPEC_TREND_ENGINE_2021_2025.sha256` (hash tách rời, xem mục 0.1)

---

## 0. PRE-REGISTRATION COMMITMENT

Spec này được viết **TRƯỚC** khi chạy bất kỳ backtest nào. Mọi tham số, ngưỡng, luật vào/ra đều **khóa cứng**.

**Cam kết bất biến:**
- Nếu Sharpe OOS < 0.5 → **BÁC BỎ VĨNH VIỄN**, không tinh chỉnh để "cứu".
- Không thêm/bớt biến sau khi xem kết quả.
- Không "thử đặc tả khác" nếu kết quả xấu (data-snooping).
- Mọi variant phải pre-register **trước** khi chạy, không chạy trước rồi chọn.

### 0.1 Vì sao hash nằm ở file RIÊNG (không nhúng vào spec)

Nếu ghi `sha256` của spec **vào chính spec** thì hash tự vô hiệu: thêm dòng đó làm nội dung đổi ⇒ hash đổi. Do đó anchor được ghi ở sidecar `SPEC_TREND_ENGINE_2021_2025.sha256`, chứa: hash file + git commit SHA + timestamp. Bản thân file spec là **bất biến tuyệt đối**.

---

## 1. MOTIVATION & CONTEXT

### Chẩn đoán ban đầu
Hệ thống hiện dùng **Veto Architecture** — bất kỳ cổng nào fail → 100% cash.
Trong CRISIS: đúng (bảo toàn vốn). Trong bull-run: có thể bỏ lỡ cơ hội (paralyzed).

### Bằng chứng ĐÃ THAY ĐỔI sau khi verify DB (2026-10-09)
| Claim ban đầu | Sau verify DB |
|---|---|
| Khối ngoại bán ròng 2020–2021 | ❌ Không verify được — `market_foreign_history` chỉ có **2026-04-05 → 2026-10-08** (76 ngày) |
| Universe 2020 đủ để backtest | ❌ Chỉ **107 mã** ≥200 phiên (2021: 903); thiếu VIC/FPT/VHM/MSN/ACB; **50% dòng volume = 0** |
| PVT/VIC bị VN20 chặn | ❌ Bị **CSI** chặn: PVT 0.37 (REDUCE) · VIC 0.22 (VETO/DISTRESSED) |

⇒ **Cửa sổ backtest hợp lệ: 2021-01-01 → 2025-12-31**
⇒ **Foreign gate BỊ LOẠI khỏi spec** (không có dữ liệu, không được giả định).

### Mục tiêu
Kiểm định **Trend Engine độc lập** có tạo OOS alpha hay không — **TRƯỚC** khi implement.

---

## 2. SCOPE

### 2.1 IN scope
Universe theo mục 3 · cửa sổ 2021-01-01 → 2025-12-31 · trend-following (RS + breakout) · shared risk budget với Track 2.

### 2.2 OUT of scope
Foreign flow (không có data) · dual-track integration (spec riêng) · live execution (chỉ backtest).

### 2.3 Quy tắc lọc sàn giao dịch & thanh khoản — LIMITATION & APPROXIMATION

- **LIMITATION:** cơ sở dữ liệu hiện tại **không lưu cột phân loại sàn** (HOSE / HNX / UPCoM). Đã kiểm cả `screener_cache.db` và `financial_facts.db` — `symbol_industry` chỉ có ICB.
- **Giải pháp xấp xỉ:** loại bỏ tiêu chí kiểm tra tên sàn, thay hoàn toàn bằng lọc thanh khoản:
  `ADV20 ≥ 500.000 cổ phiếu/phiên`
- **Đánh giá rủi ro:** ngưỡng ADV20 ≥ 500k tự loại ~95% mã UPCoM thanh khoản thấp. Nếu vài mã UPCoM thanh khoản cao lọt qua → **chấp nhận là nhiễu cho phép (tolerable noise)**, **gắn cờ trong báo cáo backtest**, **TUYỆT ĐỐI không sửa lại spec**.
- **Chiều sai an toàn:** nếu loại nhầm vài mã HOSE thanh khoản cao → mất cơ hội, **không mất vốn** (fail-closed).

---

## 3. UNIVERSE DEFINITION

**Inclusion:**
- ≥200 phiên sạch/năm (`is_stale = 0`)
- `ADV20 ≥ 500.000` cp
- Có organ data đầy đủ (Profit, Cash, BS, Eff, Moat)

**Exclusion:**
- `Health == DISTRESSED`
- Thiếu organ data

### 3.1 Mẫu số universe THỰC TẾ (~740 mã) — không phải 903

| Nguồn | 2021 | 2022 | 2023 | 2024 | 2025 |
|---|---|---|---|---|---|
| Dữ liệu giá (`daily_ohlcv`, ≥200 phiên sạch/năm) | **903** | 915 | 906 | 855 | 879 |
| Dữ liệu cơ bản (`financial_facts` / organ) | **737** | 737 | 750 | 750 | 744 |

Entry rule **bắt buộc cả hai** nguồn ⇒ mẫu số thật là **giao điểm ≈ 740 mã**.
**903 là cận trên lý thuyết về giá, không phải universe thực tế.**

⇒ Mọi tỷ lệ phần trăm và kết quả backtest của Trend Engine **phải báo cáo trên mẫu số ~740** này.
~165 mã (~18%) có giá nhưng không có cơ bản sẽ bị loại tự động — đó là hành vi fail-closed đúng, không phải lỗi.

---

## 4. ENTRY RULES (FROZEN)

```
TREND_BUY ⟺
    RS ≥ 90                          # momentum
  ∧ ADV20 ≥ 500.000                  # thanh khoản
  ∧ Breakout nền 20 phiên            # cấu trúc
  ∧ Volume ≥ 1.3 × ADV20             # xác nhận
  ∧ Health ≠ DISTRESSED              # cơ bản
  ∧ BS  ≥ 0.30                       # bảng cân đối
  ∧ Eff ≥ 0.60                       # hiệu quả
  ∧ Moat ≥ 0.50                      # lợi thế cạnh tranh
  ∧ nguồn_chặn ∈ {WORLD, MACRO}      # KHÔNG phải suy thoái nội tại
```

**`nguồn_chặn`:** nếu mã bị CSI REDUCE/VETO vì lý do **nội tại** (BS/Eff thấp, DISTRESSED) → loại. Nếu chỉ bị chặn vì **WORLD/MACRO** (lãi suất, transmission) → được phép.

**Breakout:** `Close > max(High[1:20])` **AND** `Close > MA20`.

---

## 5. EXIT RULES (FROZEN)

**Full exit khi BẤT KỲ:**
- Trailing stop −8% từ đỉnh gần nhất
- Regime → CRISIS
- Circuit breaker K3: VN-Index −5% trong 3 phiên
- RS < 70 hai phiên liên tiếp

**Partial exit (50%) khi:**
- Trailing stop −5% từ đỉnh
- ATR% > 2× trung bình 20 phiên

**Không re-entry trong 5 phiên** sau full exit (chống whipsaw).

---

## 6. POSITION SIZING (FROZEN)

```
alloc(Macro) + alloc(Trend) + alloc(Track2) ≤ 100% NAV
Max Trend allocation = 20% NAV
```
- Base size: **5% NAV / vị thế** · tối đa **4 vị thế** đồng thời
- Correlation cap: tổng beta-weighted exposure ≤ **25% NAV**
- **Position Discount Multiplier:** `POLARITY_MISMATCH` → × 0.5 · `Health == LEVERAGED_GROWTH` (không DISTRESSED) → × 0.8

---

## 7. INVARIANTS #8–#11

**#8 — CRISIS Override (bất biến):**
```
Macro Governor == CRISIS  ⟹  Trend Engine = OFF (0% NAV)
```
Không ngoại lệ. CRISIS ⇒ tương quan mọi tài sản tiến về 1.

**#9 — Shared Risk Budget (bất biến):** Trend Engine và Track 2 chia sẻ chung trần **20% NAV**. Không cộng dồn, không bypass.

**#10 — Circuit Breaker độc lập (bất biến):**
```
VN-Index −5% trong 3 phiên  ⟹  Trend Engine tự OFF
Trend portfolio drawdown > 8%  ⟹  Trend Engine tự OFF
```
Không phụ thuộc Macro Governor.

**#11 — Pre-registration & Anti-Data-Snooping (bất biến):** spec khóa cứng trước backtest; không tinh chỉnh sau khi thấy kết quả; không thử đặc tả khác để "cứu" Sharpe.

---

## 8. SUCCESS CRITERIA (FROZEN TRƯỚC BACKTEST)

Pass nếu **TẤT CẢ** đúng:

| Metric | Ngưỡng |
|---|---|
| Sharpe OOS | ≥ 0.5 |
| Calmar Ratio | ≥ 0.5 |
| Max Drawdown | ≤ 20% |
| Win Rate | ≥ 40% |
| Profit Factor | ≥ 1.3 |

**Benchmark:** Buy & Hold VN-Index 2021–2025. Nếu Trend Engine **thua Buy&Hold về Sharpe** → **fail** (dù absolute return cao hơn).

**Nếu fail ⇒ KHÔNG implement. Giữ nguyên Veto Architecture.**

---

## 9. ANTI-DATA-SNOOPING COMMITMENTS

**Sẽ KHÔNG làm:** đổi ngưỡng sau khi chạy (0.30 → 0.25) để tăng Sharpe · thêm feature sau khi thấy kết quả xấu · chọn cửa sổ con (2021–2023) để show kết quả đẹp · thử 10 variant rồi chọn cái tốt nhất.

**Sẽ làm nếu muốn variant mới:** pre-register variant B **trước** khi chạy · commit variant B vào git trước · chạy **1 lần duy nhất** trên OOS.

**Nếu Sharpe < 0.5:** kết luận Trend Engine không tạo alpha trong universe này trong sprint này; mở ticket **"Trend Engine v2"** với spec mới pre-registered.

---

## 10. KILL SWITCH PRIORITY

| Ưu tiên | Trigger | Hành động |
|---|---|---|
| 1 | Circuit breaker K3 (VN-Index −5%) | FULL EXIT |
| 2 | Regime → CRISIS | FULL EXIT |
| 3 | Trailing stop −8% | FULL EXIT |
| 4 | RS < 70 hai phiên | FULL EXIT |
| 5 | Trailing stop −5% | PARTIAL 50% |
| 6 | Volatility spike | PARTIAL 50% |

Log **TẤT CẢ** trigger đang bật, không chỉ trigger được thi hành.

---

## 11. TEST PLAN

**Unit:** `test_entry_rules.py` (8 điều kiện) · `test_exit_rules.py` (6) · `test_sizing.py` (budget + discount) · `test_circuit_breaker.py` (priority).

**Integration:** `test_invariant_8/9/10/11` (CRISIS override · shared budget · CB độc lập · hash pre-registration không đổi).

**Replay (bắt buộc):** replay 08/10/2026 — Regime CRISIS + tín hiệu mua → **total allocation PHẢI = 0%** (chứng minh không có bypass).

**Backtest (chạy 1 lần duy nhất):** window 2021-01-01 → 2025-12-31 · vốn ban đầu 100% NAV · phí 0.15%/lượt (round-trip) · trượt giá 0.1% · benchmark VN-Index buy & hold.

---

## 12. IMPLEMENTATION NOTES

```
backend/src/engine/trend_engine.py          # logic chính
backend/src/engine/trend_sizing.py          # position sizing
backend/src/engine/trend_circuit_breaker.py # kill switch
backend/tests/test_trend_*.py               # tests
backend/scripts/backtest_trend_2021_2025.py # runner
```
- **KHÔNG** wire vào `final-decision` cho tới khi pass OOS.
- Chạy **shadow mode 1 tháng** trước khi quyết định tích hợp live.

---

## 13. OPEN QUESTIONS

1. **PVT borderline** — spec chạy **độc lập với PVT**; PVT là case-study riêng.
2. **`POLARITY_MISMATCH`** — position discount × 0.5 + log warning, **không** block.
3. **Foreign gate** — đã gỡ; nếu muốn test có cổng ngoại → **variant B pre-register riêng** (và cần bổ sung chuỗi ngoại lịch sử trước).

### 13.4 Pre-registration CHANGELOG & Cam kết bất biến

| Phiên bản | Commit | Nội dung |
|---|---|---|
| **v1.0** | `6279fcc` | Pre-registration khởi tạo |
| **v1.1** | *(commit này)* | Mẫu số universe thực tế ~740 (giao điểm Giá + Organ) · bỏ tiêu chí sàn cứng, xấp xỉ bằng `ADV20 ≥ 500k` (§2.3) · chốt mục 15 |

🔒 **CAM KẾT BẤT BIẾN: TUYỆT ĐỐI KHÔNG CÓ BẢN v1.2.**

Mọi điều chỉnh ngưỡng hoặc biến thể thuật toán sau thời điểm này **bắt buộc phải là một file spec mới**
(`SPEC_TREND_ENGINE_VARIANT_B.md`) và chạy kiểm định độc lập. Sửa spec này thêm lần nữa sẽ làm
pre-registration mất ý nghĩa — guard `test_preregistration_hash.py` (invariant #11) sẽ chặn về mặt kỹ thuật.

---

## 14. SIGN-OFF

| Role | Người | Ngày |
|---|---|---|
| Quant Lead (chủ hệ thống) | [user] | 2026-10-09 |
| Reviewer | [assistant] | 2026-10-09 |

**Pre-registration locked:** YES · **Hash:** xem sidecar `.sha256`

---

## 15. ⚠️ PRE-FLIGHT FINDINGS — PHẢI CHỐT **TRƯỚC KHI CHẠY BACKTEST**

Kiểm tra tính khả thi 3 input của spec trên DB thật (2026-10-09). **2 vấn đề phải quyết trước khi backtest chạy**; sửa lúc này vẫn hợp lệ vì **chưa nhìn thấy kết quả nào**.

### 15.1 Universe thật là ~740, KHÔNG phải 903 (ràng buộc: dữ liệu tài chính)

| Nguồn | Độ phủ |
|---|---|
| `daily_ohlcv` (≥200 phiên/năm) | 2021: **903** mã · 2022: 915 · 2023: 906 · 2024: 855 · 2025: 879 |
| `financial_facts` / `health_ratios` (organ data) | FY2021: **737** · FY2022: 737 · FY2023: 750 · FY2024: 750 · FY2025: 744 |

⇒ Entry rule yêu cầu organ data (BS/Eff/Moat/Health) nên **~165 mã (18%) có giá nhưng không có cơ bản sẽ bị loại tự động**. Universe hiệu dụng ≈ **740**, không phải 903.

**Đề xuất:** giữ nguyên (loại vì thiếu data là đúng fail-closed), nhưng **ghi nhận con số 740** làm mẫu số thật, và báo cáo kết quả theo mẫu số đó — không so với 903.

### 15.2 ❌ Tiêu chí "sàn HOSE/HNX, loại UPCOM" — **BẤT KHẢ THI với DB hiện tại**

Đã kiểm `screener_cache.db` + `financial_facts.db`: **không có cột/bảng nào chứa thông tin sàn** (HOSE/HNX/UPCOM). `symbol_industry` chỉ có ICB.

**3 phương án:**
- **(a) Bỏ tiêu chí** — dựa vào `ADV20 ≥ 500k` để loại UPCOM (thực tế gần như toàn bộ UPCOM thanh khoản thấp sẽ bị loại bởi cổng này). *(đơn giản nhất, có căn cứ)*
- **(b) Proxy gián tiếp** — ví dụ dùng sự hiện diện trong VN30/VNINDEX constituents. *(không đầy đủ, chỉ phủ được blue-chip)*
- **(c) Nạp dữ liệu sàn** từ nguồn ngoài. *(chính xác nhất, nhưng là hạng mục dữ liệu riêng, cần chủ hệ thống duyệt)*

**✅ ĐÃ CHỐT (2026-10-09): chọn (a)** — bỏ tiêu chí sàn, xấp xỉ bằng `ADV20 ≥ 500k` (§2.3).
Đây là **xấp xỉ có chủ đích**: nếu kết quả backtest nằm sát ngưỡng pass, phải nêu rõ trong báo cáo rằng
universe của (a) rộng hơn so với (b)/(c); **không được sửa spec để bù**.

### 15.3 ĐÃ KIỂM, DÙNG ĐƯỢC ✅
| Input | Nguồn | Ghi chú |
|---|---|---|
| Benchmark VN-Index | `daily_ohlcv` symbol `VNINDEX` | 1.392 phiên, 2021-01-04 → 2026-10-05 |
| Circuit breaker K3 | `VNINDEX` | tính được |
| RS ≥ 90 lịch sử | `daily_ohlcv` | tính được |
| Breakout `Close > max(High[1:20])` | `daily_ohlcv` OHLC | có |
| ADV20 | `daily_ohlcv` volume | có (lưu ý 2020 volume 50% = 0, **ngoài cửa sổ** nên không ảnh hưởng) |

**Trạng thái mục 15:** ✅ **ĐÃ CHỐT cả 15.1 và 15.2** (2026-10-09) tại bản **v1.1** — xem §13.4.
Spec đã commit lại + **hash mới** trong sidecar, **vẫn TRƯỚC khi chạy backtest** nên pre-registration còn nguyên giá trị.
Kể từ đây spec **KHÓA CỨNG**: không có v1.2, mọi biến thể là file spec riêng.
