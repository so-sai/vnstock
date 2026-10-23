# AUDIT: Phá vỡ tự sự (Narrative Breakdown) — Bản tổng kết tuần 19–23/10/2026

Ngày lập: **2026-10-23**. Trạng thái quyết định: **FAIL-CLOSED — `ENTRY_LOCK = TRUE`, 100% tiền mặt.**

Phạm vi: bản "Tổng kết thị trường tuần qua + Chiến lược bắt đáy" được đối chiếu độc lập với
`backend/data/screener_cache.db` (`daily_ohlcv`, `regime_history`, `market_wide_breadth_daily`,
`market_foreign_history`, `macro_history_v2`, `transmission_pit`), `backend/data/system_state.json`,
`backend/data/financial_facts.db` và sổ `preregistered_hypotheses.md`.

Kết luận: bản báo cáo **không đạt chuẩn zero-hallucination**, vi phạm Bất biến #1
(Missing > Fake, no provenance = no trust) ở ba dạng: *vintage bleeding*, *số liệu không có nguồn*
và *tự chế quy tắc giải ngân*. **Không được dùng để ra quyết định.**

Phiên bản dữ liệu: `MAX(daily_ohlcv.date) = 2026-10-22` ⇒ **không tồn tại bar 23/10** tại thời điểm
lập audit; mọi phát biểu về "tuần 19–23/10" là cửa sổ chưa đo xong.

---

## 1. Bảng đối chiếu định lượng

### 1.1. Giá / thanh khoản — EOD 2026-10-22

| Báo cáo nói | Hệ thống thực tế | Nguồn | Phán quyết |
|---|---|---|---|
| TCB +2,11% → 33.950, vol +55% | **34.150 (-0,73%)**, 14,61M cp = **0,80× ADV20** | `daily_ohlcv` | ❌ lấy số 20/10 |
| HPG +2,68% → 21.100 | **21.250 (+0,47%)** | `daily_ohlcv` | ❌ lấy số 20/10 |
| MBB "giữ vững 19.650" | **19.900 (-0,50%)** | `daily_ohlcv` | ❌ lấy số 20/10 |
| FPT +0,53% → 57.400 | **57.400 (0,00%)** | `daily_ohlcv` | ❌ lấy số 20/10 |
| SHB 10.550, "vol kỷ lục 37,8M" | **10.800 (0,00%), 39,4M cp**; kỷ lục tuần là **49,97M (16/10)** | `daily_ohlcv` | ❌ |
| PNJ 18.600 (-3,13%) | **18.600 (-3,12%)** | `daily_ohlcv` | ✅ |
| "Đáy ngắn hạn 1.689,84 (19/10)" | Đó là **close**; low thực **1.682,54** | `daily_ohlcv` | ⚠️ nhập nhằng close/low |
| "20/10 thanh khoản 565M cp" | **565,1M cp** | `daily_ohlcv` | ✅ |
| "22/10 bùng nổ +21,96 (+1,26%)" | **+21,96 (+1,26%)**, vol 672,1M | `daily_ohlcv` | ✅ |

**Sai một phiên (lỗi nặng nhất về cấu trúc):** báo cáo bỏ phiên **21/10** — `+46,70 điểm (+2,76%)`,
vol **947,8M cp** — rồi gọi 22/10 là "nến nỗ lực đầu tiên". Theo đúng định nghĩa breakout của hệ thống
(`governor/unfreeze_gates_v2.py::index_breakout`: ret ≥ 1,5% **và** vol ≥ 1,3× ADV20):

| Phiên | Ret | Vol | ADV20 (20 phiên trước) | Vol/ADV20 | G1 breakout |
|---|---|---|---|---|---|
| 21/10 | **+2,76%** | **947,8M** | 596,7M | **1,588×** | **ĐẠT** |
| 22/10 | +1,26% | 672,1M | 611,5M | 1,099× | KHÔNG |

⇒ Báo cáo đảo ngược đúng chỗ then chốt: FTD (theo ngưỡng hệ thống) **đã xuất hiện 21/10**, còn 22/10
không đạt. Việc "chưa có FTD" không thể dùng làm lý do duy nhất để trì hoãn.

### 1.2. Biến động tuần (close 16/10 → close 22/10)

| Mã | 16/10 | 22/10 | Tuần |
|---|---|---|---|
| VNINDEX | 1.705,91 | 1.760,63 | **+3,21%** |
| SHB | 10.650 | 10.800 | +1,41% |
| TCB | 33.200 | 34.150 | +2,86% |
| MBB | 19.850 | 19.900 | +0,25% |
| HPG | 20.900 | 21.250 | +1,68% |
| FPT | 58.200 | 57.400 | −1,37% |
| PNJ | 18.800 | 18.600 | **−1,06%** |

⇒ "Phân hóa tàn khốc nhất kể từ đầu năm" là phóng đại: PNJ chỉ kém VN-Index ~4,3pp trong
tuần, và **tăng +6,76% ngày 20/10** (18.500 → 19.750) — chi tiết bị báo cáo bỏ qua.

### 1.3. Regime / breadth / vĩ mô

| Báo cáo nói | Hệ thống thực tế | Nguồn | Phán quyết |
|---|---|---|---|
| Regime `CRISIS 0,28–0,35` | Row 22/10: **0,4551 / RANGING**; row 21/10: 0,27 / CRISIS | `regime_history` | ❌ (xem §3) |
| "Độ rộng phải cải thiện > 50%" (đang là rào cản) | Breadth đã **63,5–63,8%** từ 21/10 | `regime_history`, `market_wide_breadth_daily` | ❌ rào cản thật là **score**, không phải breadth |
| O/N 3,58% | **3,71%** (22/10) | `macro_history` | ⚠️ lệch |
| USD/VND 25.889 | **25.856** (22/10) | `macro_history` | ⚠️ lệch |
| US10Y 5,10% | **5,117%** (22/10) | `macro_history` | ⚠️ lệch |
| Latent Liquidity 64,2 / FRAGILE_STABILITY | `transmission_pit` mới nhất **2025-12-31: 64,8** | `transmission_pit` | ❌ số 10 tháng trước, không có provenance hiện hành |
| Ngoại giảm xả ròng, quay lại mua | +170,1 tỷ (21/10) → **+323,8 tỷ** (22/10) | `market_foreign_history` | ✅ chiều hướng đúng |

### 1.4. Cơ bản (kỳ 2026Q2)

| Mã | Báo cáo | Hệ thống | Phán quyết |
|---|---|---|---|
| SHB | P/B 0,75 · P/E 4,64 · BVPS 14.093 · NPL 2,2 · LLR 76,9 · ROE 17,5 | **0,77 · 4,75 · 14.093 · 2,2 · 76,9 · 17,5** | ⚠️ định giá lệch nhẹ |
| TCB | P/B 1,27 · BVPS 26.671 · NPL 1,10 · LLR 125,6 · VCSH 189k | **1,28 · 26.671 · 1,10 · 125,6 · 188.997 tỷ** | ✅ |
| MBB | P/B 1,26 · BVPS 15.569 · P/E 6,5 · NPL 1,4 · LLR 93,6 · ROE 22,1 · TD +39,5% | **1,28 · 15.569 · 6,66 · 1,4 · 93,6 · 22,1 · 39,5%** | ⚠️ định giá lệch nhẹ |
| HPG | P/B 1,20 · P/E 10,1 · BVPS 17.518 · VCSH 112k | **1,28 · 7,73 · 16.659 · 141.516 tỷ** | ❌ sai |
| PNJ | "Lỗ Q3 −258 tỷ"; BVPS sau pha loãng 13,8–14,5k | **2026Q2 `NET_INCOME` = −739 tỷ**; BVPS hệ thống **26.262**; không có trường nào cho "pha loãng 550M cp" / "trích lập 7.100 tỷ" | ❌ số không có nguồn |

Ghi chú fail-closed: báo cáo dùng tình huống "PNJ lỗ Q3" trong khi DB chỉ có tới **2026Q2** ⇒ quý được
viện dẫn chưa tồn tại trong hệ thống; phải ghi `*_MISSING`, không được điền số.

---

## 2. Vi phạm bất biến

1. **#1 Missing > Fake / provenance.** Trộn 4 mã lấy giá 20/10 gán nhãn 22/10; viện dẫn Q3/pha loãng/
   latent liquidity không có trong DB; dùng `transmission_pit` đã đóng băng từ 2025-12-31 như số hiện hành.
2. **#2 Phân tầng dữ liệu.** Báo cáo không phân biệt *thiếu bar* (23/10 chưa có), *mã bị bán* và
   *cấu trúc xấu*: gộp tất cả thành một tự sự "thanh lọc sinh tử".
3. **#3 Kỷ luật kiểm định.** Tự chế thang giải ngân **30–30–40%** trái với thang đã đăng ký
   `0 / 30 / 70 / 100` (`unfreeze_gates_v2.py::TRANCHES`) và mức tối đa **10–15% NAV** của kịch bản A;
   đồng thời tự đặt ngưỡng "score > 0,40, breadth > 50%" trong khi rổ điều kiện đã đóng băng là 4 điều kiện
   (`preregistered_hypotheses.md`, H-2026-10-22-REGIME-UNFREEZE).

---

## 3. Điều tra xung đột provenance: `0,35 / CRISIS` vs `0,4551 / RANGING` (2026-10-22)

### 3.1. Kết quả tái lập

Test: `backend/tests/test_regime_provenance_20261022.py`.

| Đường chạy | Seed | `regime_score` | `status` | Tái lập được |
|---|---|---|---|---|
| HISTORICAL REPLAY `detect_regime("2026-10-22")` | 21/10 = **0,27** | **0,3459** | **CRISIS** | ✅ tất định |
| Sổ đăng ký H-2026-10-22 | 21/10 = 0,27 | **0,35** | CRISIS | ✅ (là bản làm tròn 2 chữ số của 0,3459) |
| LIVE `detect_regime(None)` trước khi vá | **22/10 = 0,4551** | **0,4914** | RANGING | ❌ feedback loop |
| LIVE `detect_regime(None)` sau khi vá | **21/10 = 0,27** | **0,3459** | **CRISIS** | ✅ đồng nhất replay |
| `regime_history` sau Restatement | — | **0,3459** | **CRISIS** | ✅ đã hiệu chỉnh sạch |

Bằng chứng số học (alpha = 0,2154; raw = 0,6225 replay / 0,6236 live):

```
replay : 0,2154 × 0,6225 + 0,7846 × 0,27   = 0,3459   (CRISIS)
live   : 0,2154 × 0,6236 + 0,7846 × 0,27   = 0,3459   (CRISIS)
quỹ đạo cũ (lỗi self-seed): 0,3459 → 0,4057 → 0,4526 → [0,4551] → 0,4892 …
```

Điểm mấu chốt: trước khi vá, seed của đường LIVE **chính là dòng 22/10 đang lưu** (0,4551), không phải 21/10 (0,27).
Vì vậy 0,4551 **không phải** "kết quả tính lại sau khi nạp bar đóng cửa 22/10" — nếu nạp bar rồi replay
tử tế thì phải ra 0,3459. Nó là **EMA tự ăn chính mình** qua nhiều lượt cập nhật trong ngày.

### 3.2. Root cause (một nguyên nhân gốc)

- `backend/src/engine/regime_engine.py:310-320`: nhánh `target_date=None` dùng
  `SELECT date, regime_score FROM regime_history WHERE is_degraded = 0 ORDER BY date DESC LIMIT 1`
  — **thiếu vế `date < as_of`** ⇒ trả về dòng cùng ngày nếu đã tồn tại.
- `backend/src/daily_updater.py:944`: mỗi lượt cập nhật gọi `detect_regime(lang_mode="compact")`
  (target_date = None) rồi `save_data_upsert` (`db_core.py:642`, `INSERT OR REPLACE`) ⇒ ghi đè tại chỗ,
  **không để lại vết** (không có lịch sử giá trị cũ).
- Hệ quả kèm theo: `detect_regime` **không idempotent** giữa live và replay cho cùng một ngày; mọi
  so sánh "trước/sau" trên `regime_history` đều có thể nhiễm.

### 3.3. Phán quyết provenance

1. **Giá trị đúng cho 22/10 là 0,3459 / CRISIS** (replay, cùng phiên bản code hiện hành).
2. **0,4551 là artifact self-seeding**, bị cấm dùng làm bằng chứng mở khoá. Nếu dùng nó, điều kiện #1
   (`score >= 0,40`) sẽ "ĐẠT" một cách giả tạo và đảo ngược kết luận 3/4 → kịch bản A.
3. Trạng thái vận hành giữ nguyên: **`ENTRY_LOCK = TRUE`**, lý do `DAILY_GOVERNOR_CRISIS_OVERLAY`
   (`system_state.json`), **100% tiền mặt**, không mở 20–30%.

---

## 4. Nhật ký Thực thi Remediation (Đã hoàn tất 2026-10-23 theo Lệnh GO)

| # | Hành động | Trạng thái | Minh chứng nghiệm thu |
|---|---|:---:|---|
| 1 | Vá self-seed: ép vị từ `date < as_of_str` trên mọi truy vấn seed (`regime_engine.py`) | ✅ **DONE** | `detect_regime(None) == detect_regime(as_of) == 0,3459 / CRISIS`; 5 lần chạy liên tiếp drift = 0 (`test_detect_regime_n_lan_lien_tiep_zero_drift` PASS). |
| 2 | Ghi Amendment Restatement cho dòng 22/10: `0,4551 / RANGING` → `0,3459 / CRISIS` | ✅ **DONE** | Cập nhật `regime_history` ngày 2026-10-22; ghi nhật ký kiểm toán vào `eod_write_audit_ledger` (audit_id=1, status='RESTATED'). |
| 3 | Hồi sinh sổ nhật ký `eod_write_audit_ledger` (Append-Only Write Audit) | ✅ **DONE** | Tạo bảng `eod_write_audit_ledger`, kết nối 4 writer chính (`daily_updater`, `regime_engine`, `market_wide_breadth`, `eod_runner`). |
| 4 | Đóng H-2026-10-22 khi có bar 23/10 | ⏳ **CHỜ EOD 23/10** | Giữ vững `ENTRY_LOCK = TRUE`, 100% tiền mặt theo đúng kịch bản B. |

---

## Phụ lục — Truy vấn tái lập

```sql
-- 1) Tuần giá
SELECT date, open, high, low, close, volume FROM daily_ohlcv
WHERE symbol = 'VNINDEX' AND date >= '2026-10-12' ORDER BY date;

-- 2) Regime
SELECT date, regime_score, status, breadth_pct, vol_score, trend_score, is_degraded
FROM regime_history WHERE date >= '2026-10-19' ORDER BY date;

-- 3) Seed mà nhánh LIVE đang dùng (có vế date < as_of_str)
SELECT date, regime_score FROM regime_history WHERE date < '2026-10-22' AND is_degraded = 0 ORDER BY date DESC LIMIT 1;

-- 4) Sổ kiểm toán ghi nhật ký bất biến
SELECT * FROM eod_write_audit_ledger ORDER BY audit_id DESC LIMIT 10;
```

```powershell
# Tái lập engine (đặt PYTHONIOENCODING=utf-8 nếu console cp1252)
$env:PYTHONPATH="<repo>\backend"
backend\.venv\Scripts\python.exe -m pytest backend/tests/test_regime_provenance_20261022.py -q
```

Nguồn phán quyết: `capitulation_replay_verdict.md` (mẫu kỷ luật), `preregistered_hypotheses.md`
(H-2026-10-22-REGIME-UNFREEZE), `tech_debt_backlog.md`.
