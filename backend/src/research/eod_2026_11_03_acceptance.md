# Nghiệm thu EOD phiên 2026-11-03 (NQ-16 §III) — **NON-DECISIONAL**

> **Trạng thái tài liệu:** Số đo của **một phiên** dùng để nghiệm thu đường ống, **không**
> dùng để tối ưu/chọn tham số. Cửa sổ 2025–2026 là OOS của giai đoạn trước ⇒ mọi kết luận
> định lượng dưới đây vẫn **NON-DECISIONAL** theo Điều 3 Hiến pháp.
> Nguồn số: `C:\Users\Admin\AppData\Local\Temp\opencode\eod_0311_result.json` +
> `eod_0311.log`, đối chiếu lại trực tiếp trên `backend/data/screener_cache.db` ngày 04/11/2026.

## 1. Trình tự thực thi (runner PID 6648, không can thiệp tay)

| Bước | Thời điểm | Kết quả |
|---|---|---|
| Chờ tới ATC | 11:41 → 15:15 | poll 5', không ghi gì vào CSDL |
| `daily-update --date 2026-11-03` | 15:15:26 → **exit 0** 15:19:33 | **840/840 bar**, `is_stale = 0` |
| `eod-run --date 2026-11-03` | 15:19:33 → **exit 0** 15:20:28 | breadth + regime + paper trading + catch-up |
| DoD snapshot | 15:20:28 | `decision: DONE` |

Không có `--force`, không chạy lại bước nào, không gặp **exit 3**.

## 2. Độ phủ & chốt sổ

### 2.1 `market_wide_breadth_daily` ngày 03/11 (tự động, trong `daily-update`)

| Cột | Giá trị |
|---|---|
| `total_active` | **839** |
| `advances` / `declines` / `unchanged` | 332 / 293 / 214 |
| `breadth_pct` (ròng = adv−dec / toàn bộ mã) | **+4,648 %** |
| `degraded` | **0** |
| `created_at` | 2026-11-03T15:18:26 |

Chuỗi ròng 8 phiên (đo lại 04/11): −4,07 · −20,12 · −14,12 · −13,28 · −23,13 · −33,33 ·
**−47,73** (02/11) → **+4,648** (03/11). Đây là phiên **đầu tiên** đảo dấu dương sau 6 phiên
sụt liên tiếp.

### 2.2 `regime_history` ngày 03/11

| Cột | Giá trị |
|---|---|
| `regime_score` | **0,1684** (smoothed) |
| `status` | **CRISIS** |
| `breadth_pct` | **20,7 %** — *mẫu số khác breadth ở §2.1*: chỉ mã có `Vol20d > 50k` |
| `breadth_velocity` | −35,2 |
| `trend_score` / `vol_score` | 0,0 / 1,0 |
| `atr_ratio` | 0,9742 |
| `is_degraded` | **0** |
| `is_degraded IS NULL` toàn bảng | **0 dòng** |

## 3. Đối soát giá đóng cửa (bar 03/11, `is_stale = 0`)

| Mã | Open | High | Low | **Close** | % vs 02/11 | Vol (triệu cp) | Vị trí đóng trong nến |
|---|---|---|---|---|---|---|---|
| VNINDEX | 1.703,6 | 1.713,0 | 1.689,3 | **1.699,6** | −0,65 % | 624,37 | 43,5 % |
| FPT | 50.300 | 51.700 | 49.550 | **50.400** | +0,40 % | 9,25 | 39,5 % |
| SHB | 10.400 | 10.700 | 10.300 | **10.450** | +0,48 % | 43,55 | 37,5 % |
| VCB | 54.200 | 54.500 | 53.100 | **53.500** | −0,19 % | 8,91 | 28,6 % |
| MBB | 18.800 | 18.900 | 18.550 | **18.900** | +0,80 % | 20,61 | 100 % |
| TCB | 21.450 | 21.650 | 21.150 | **21.400** | 0,00 % | 10,79 | 50,0 % |
| PDR | 11.050 | 11.100 | 10.900 | **11.050** | −0,90 % | 4,96 | 75,0 % |
| HPG | 20.600 | 21.400 | 20.400 | **21.250** | +4,17 % | 32,47 | 85,0 % |

Diễn giải (chỉ mô tả, không phải khuyến nghị):
- **VN-Index đóng dưới 1.700 (1.699,6)** nhưng **rút chân** khỏi đáy 1.689,3 — đóng ở 43,5 %
  biên độ nến, tức đã hồi ~10 điểm từ low, không phải phiên bán tháo tiếp diễn.
- **FPT giữ được mốc 50.000**: trong phiên có dí xuống **49.550** (thủng 50) nhưng đóng
  **50.400** (+0,40 %), vol 9,25 triệu cp — không xác nhận kịch bản nhúng 47–49.
- Nhóm ngân hàng/gia công phân hoá: **HPG +4,17 %** (mạnh nhất nhóm), **MBB +0,80 %** đóng
  đúng đỉnh nến, **VCB −0,19 %**, **TCB 0,00 %**; **PDR −0,90 %** yếu nhất.

## 4. Kế toán tài sản & sổ cái

| Chỉ tiêu | 02/11 | **03/11** | Δ |
|---|---|---|---|
| `market_value` | 823.914.000 | **832.578.000** | +8.664.000 |
| `settled_cash` | 8.568.848,35 | **8.568.848,35** | 0 |
| `unrealized_pnl` | −169.598.596,65 | **−160.934.596,65** | +8.664.000 |
| **`total_equity`** | 832.482.848,35 | **841.146.848,35** | **+8.664.000 (+1,04 %)** |

`eod_run_ledger` ngày 03/11: **`status = SUCCESS`**, `attempts = 0`, `last_error = NULL`,
`updated_at = 2026-11-04T09:36:07` (ghi lại bằng chính writer sau khi vá Mục 24 — xem §6).
Sổ cái: **45 dòng**, `MAX(as_of_date) = 2026-11-03`. Bảng `eod_write_audit_ledger` có dòng
audit **1757** (`EOD runner ledger update: SUCCESS`).

## 5. Catch-up & Mục 22 (NQ-16 §II.2)

- **12 phiên 05/10 → 20/10 đã bù** trong chính lần chạy này, bằng `CATCHUP_MTM_ONLY`,
  `catchup_of = 2026-11-03`, `created_at = 2026-11-03T15:20:27`; equity sinh ra trong
  **933,4M … 949,3M** (mốc thật 21/10 = 954,3M) ⇒ định giá bằng close **lịch sử của đúng phiên**.
- **9 phiên 21/10 → 02/11** đồng bộ `SUCCESS` cùng lúc (ledger 24 → 45 dòng).
- `CATCHUP_MAX_CALENDAR_GAP = 30` **không** bị nới; **0** lỗi `database is locked`.
- **Còn 10 phiên** `18/09, 22/09 … 02/10` **vẫn khuyết** — giữ nguyên trong Mục 22, chờ rà soát
  `corporate_actions` theo NQ-16. Không phiên nào trong 10 phiên này có dòng `eod_run_ledger`
  (nhất quán: ledger không nói dối là đã chạy).

## 6. Hai phát hiện mới phát sinh trong quá trình nghiệm thu (đã xử lý)

1. **Mục 23 — 8 dòng ngày `2099`** (7 `paper_equity_curve` + 1 `paper_cash_ledger`,
   `SEL_PAPER_V1`, `created_at 2026-09-25/28`): đã **purge** kèm backup
   `backend/data/backups/screener_cache_20261104_0927_pre_purge2099.bak` (228,7 MB) + payload
   cách ly `iso_purge2099_20261104_0927.json`. `MAX(date)`: equity `2099-01-12 → 2026-11-03`,
   cash ledger `2099-01-04 → 2026-07-23`; quét 46 bảng có cột ngày: **0 dòng 2099**. Cổng mới:
   `src/database/date_sanity.py` + 6 test (commit `74c7e03`).
2. **Mục 24 — `eod_run_ledger` không bao giờ ghi `SUCCESS` cho phiên hiện tại**: `return`
   sớm trong `with global_transaction(...)` làm khối ledger fallback không chạy ⇒ phiên 03/11
   đã xong nhưng ledger ghi `FAILED` (nợ giả ⇒ nguy cơ double-entry hôm sau). Đã vá
   (commit `4f470bb`), 4 test, regression **145/145**; ghi lại `SUCCESS` cho 03/11 bằng chính
   writer.
3. **Mục 25 (OPEN)** — ngoài tiến trình `eod-run` (exit 15:20:28) còn một writer ghi
   `regime_history` 15:20:51 và `paper_equity_curve` 15:20:56 **không** đi qua
   `run_eod_pipeline` (không có dòng ledger/audit nào trong khoảng đó). Giá trị khớp 3 nguồn nên
   không phải sai số, nhưng là rủi ro **đồng thời/provenance** cần truy PID.

## 7. Tiêu chuẩn nghiệm thu (DoD của NQ-15 §IV)

| Tiêu chí | Yêu cầu | Thực tế | Kết luận |
|---|---|---|---|
| Bar nạp vào | ≥ 750 mã, `is_stale = 0` | **840/840** | ✅ |
| `market_wide_breadth_daily` 03/11 | có dòng | có, `created_at 15:18:26` | ✅ |
| `regime_history` 03/11 | `is_degraded ∈ {0,1}` | **0** (toàn bảng 0 NULL) | ✅ |
| Mã thoát `eod-run` | **0** | **0** | ✅ |
| Đường thoát hiểm | nếu exit 3 ⇒ **cấm `--force`** | không gặp exit 3 | ✅ (không áp dụng) |

## 8. Trạng thái vận hành sau nghiệm thu

- **`ENTRY_LOCK = ON`** — `portfolio_critical_lock = True`
  (`last_lock_reason = DAILY_GOVERNOR_CRISIS_OVERLAY`), `stale 0/0`, `Hard Shutdown = False`.
- Regime **CRISIS** (0,1684), breadth (mẫu số Vol20d > 50k) **20,7 %** ⇒ **giữ 100 % tiền mặt**,
  không mở vị thế mới.
- Phiên kế tiếp: **04/11/2026**, chạy đúng runbook sau 15:15 (`daily-update` → `eod-run`), kỳ vọng
  lần này `eod_run_ledger(2026-11-04)` sẽ mang `SUCCESS` **do chính đường ống ghi** (Mục 24).

---
**Phán quyết:** ✅ **ĐẠT DoD** cho phiên 03/11/2026. Dữ liệu phiên này **NON-DECISIONAL**.
