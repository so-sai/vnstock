# RAW PRICE RECONSTRUCTION — BIÊN BẢN HIỆU CHUẨN P2 (PHA 2B)

**Ngày:** 2026-10-27 · **Số hiệu:** NQ-04/BCK-P2B · **Trạng thái:** CALIBRATION FINDINGS (NON-DECISIONAL)

## Mục đích

Hiệu chuẩn `raw_price_reconstruction.py` trên case FOC (6 sự kiện Phase-1 có
provenance) và blind-scan VEA 2021–2026, theo Phát lệnh Bước 1–2 Nghị quyết
04/NQ-HĐKT. Nguồn: KBS API `data_day` full precision 4 dp (probe 11:
FOC 1452 bars, VEA 1471 bars, 2020-12-01 → 2026-10-27).

## Phương pháp reverse-engineering factor

Giả thiết: chuỗi KBS là back-adjusted multiplicative stepwise —

```
P_adj(t) = P_raw(t) × F(seg(t)),   F_step đổi tại các ex-date.
```

`F_seg` giải bằng **grid constraint**: tồn tại `f` sao cho `P_adj/f` là bội số
100 VND cho mọi bar trong segment (tolerance 10 VND — noise do KBS làm tròn
tỉ lệ 4 dp, RMS đo được ≈ 0.66 VND/bar, tích lũy theo số event).

## Bằng chứng

### E6 — Factor segments GIẢI ĐƯỢC, duy nhất, verify 100% bars

| Segment FOC | f (solve từ data) | bad bars (tol 0.1 grid) |
|---|---|---|
| 2020-12-01 → 2021-04-09 | 0.580907 (k=1025) | 0/88 |
| → 2022-06-09 | 0.664624 | 0/289 (ngoại suy: 04-12, 04-13 — xem E8) |
| → 2023-06-07 | 0.706102 | 0/232 |
| → 2024-07-17 | 0.747404 | 0/276 |
| → 2025-05-22 | 0.765449 | 0/209 |
| → 2026-08-07 | 0.865769 | 0/304 |
| → 2026-10-27 | 1.000000 | 0/54 |

Raw prices suy ra: FOC 2020-12-01 = 102,500; 2021-04-09 = 161,000;
2026-08-07 = 73,700 — đều bội 100 VND. Reciprocal family (f, f/2, f/3) loại
bằng backward induction `f_prev = f_next × r`, `r ∈ (0.4, 1]`, plus
continuity check `P_ex/P_pre ∈ [0.5, 1.05]`.

### E7 — VEA: 7 factors, breakpoints ~tháng 10–11 hằng năm

`0.554168 → 0.62324 → 0.630283 → 0.701902 → 0.789186 → 0.88729 → 1.0`

Breakpoints ước lượng: ~2021-07, ~2021-10, ~2022-10, ~2023-10/11,
~2024-10/11, ~2025-10/11 — nhất quán với **cổ tức tiền mặt thường niên
~9–11% giá** của VEA (chưa có provenance đối chiếu — NON-DECISIONAL).

### E8 — Ex-gap identity MÂU THUẪN Phase-1 (FAIL-CLOSED)

Drop suy ra (`P_adj(pre)/f_old − P_adj(ex)/f_new`) vs Phase-1 VSDC:

| Ex-date | Drop suy ra | Phase-1 div | Chênh |
|---|---|---|---|
| 2021-04-12 | 26,659 | 20,000 | **+6,659** |
| 2022-06-10 | 6,800 | 8,000 | −1,200 |
| 2023-06-08 | 5,600 | 5,000 | +600 |
| 2024-07-18 | 1,400 | 2,000 | −600 |
| 2025-05-23 | 12,200 | 10,000 | +2,200 |
| 2026-08-10 | 10,800 | 10,000 | +800 |

Biên bản P1 (E1–E5) xác nhận FOC = **CTCP Dịch vụ Trực tuyến FPT (FPT
Online)**; tin tức 22-03-2021 xác nhận cổ tức tiền mặt **200% mệnh giá
(20,000 đ/cp)** cho event 2021 — nhưng data cho drop 26,659 (khoảng thêm
6,660 chưa giải thích). Series KBS **không smooth** tại ex-date (contrary
đến backward-ratio chuẩn); bar ex 2021-04-12 off-grid 59 VND với segment
factor — nhiều event/rounding chồng lấp.

**Cross-source:** DB `screener_cache.db` FOC vintage VCI (2021-04, factor
1.15504× KBS) và vintage KBS (2026-08, factor 1.0) cho CÙNG raw prices khi
divide bằng factor tương ứng — 2026-08-07→08-10 drop DB = 10,800 khớp
KBS-derived. Nghĩa là **mọi nguồnagree với nhau**, nhưng **Phase-1 event
values không khớp drop thực của data** — hoặc Phase-1 thiếu sub-event
(cổ thưởng/phần bổ sung), hoặc có convention khác chưa nhận diện.

### E10 — Boundary event 2021 DỊCH 1–2 bars: provenance 20,000đ xác nhận

Tin tức (Bing, 22-03-2021 + bài "chốt quyền trả cổ tức 20.000 đồng/cp vào
ngày 14/4"): FOC chốt danh sách **14/4/2021**, thanh toán 29/4. Phase-1 khai
ex 2021-04-12. Scan offset ±2..+3 bars trên event 1–2 (probe 17) với hard
verify max_bad=0 toàn bộ:

| Boundary 2021 | Drop suy ra | Cash provenance | Sai số |
|---|---|---|---|
| 2021-04-13 (+1) | 19,801 | 20,000 | −199 (≤ 2 tick rounding) |
| 2021-04-14 (+2, = record date) | 21,697 | 20,000 | +697 ✗ |

Boundary **2021-04-13** cho drop 19,800 ≈ 20,000 trong ±200 VND rounding
của 2 close price đã làm tròn lưới (mỗi close lỗi ≤50 VND). Các event
không dịch được (2022–2026 vẫn lệch −1,200..+2,200): giả thuyết — Phase-1
lấy NGÀY CHỐT (record date) làm ex, nhưng drop thực tại boundary tính theo
giao dịch chứa thêm overnight return 1 ngày. Cần đối chiếu từng năm
(2022: tin "tỷ lệ 50%" — 5,000đ, chốt 9/6; 2023: 5,000đ chốt 9/6/thanh
toán 19/6/2023; 2025: 100% = 184 tỷ, 18.4tr cp) trước khi certify.



1. **Cơ chế phục nguyên TỐN TẠI và khả thi:** factor solve từ grid
   constraint + backward induction trả raw prices bội số 100 VND ≈ 100%
   bars (E6/E7). Đây là nền tảng kỹ thuật cho `raw_price_reconstruction.py`.
2. **KHÔNG chứng nhận được gap identity** `P_ex = P_pre − Cash_Div` với danh
   sách 6 sự kiện Phase-1 hiện có (E8) — sai số +13% ở event 2021.
3. **Fail-closed:** không phát hành reconstructed prices vào checkpoint
   decisional; reconstructor chạy ở chế độ REPORT (xuất factors, drops,
   anomalies) — nâng cấp sang CERTIFY chỉ khi danh sách event đầy đủ
   (cash + stock) có provenance đối chiếu.

## Hành động

- [x] Viết `raw_price_reconstruction.py` (TDD RED→GREEN): synthetic
      adjusted → recover raw grid 100%; missing/ambiguous factor → raise.
      **8/8 tests GREEN** (`test_raw_price_reconstruction.py`); regression
      `test_price_provenance_gate.py` 7/7 GREEN.
- [x] Runner calibration `run_raw_price_reconstruction.py` — kết quả
      trên dữ liệu KBS thật (2026-10-27):
      - **FOC: FAIL-CLOSED đúng dự báo E8** — `FactorSolveError: segment 1
        (2021-04-12): không có factor grid-consistent`. Ex-bar 2021-04-12
        mang factor khác segment liền kề (nhiều event/rounding chồng lấp,
        xem E8) — module KHÔNG chọn approximate factor. Điều này trảm:
        **không thể phục nguyên FOC chứng nhận được với ranh giới event
        Phase-1 hiện tại**.
      - **VEA blind survey (runner-level, NON-DECISIONAL)**: 5 breakpoints
        factor nhảy lớn: ~2021-07-15 (0.554→0.623), ~2022-10-28
        (0.630→0.702), ~2023-11/12 (→0.79), ~2024-11-21 (→0.894),
        ~2025-11-18 (→1.0). Nhất quán với E7 (cổ tức thường niên Q4).
        Nhiều "breakpoint giả" từ window jitter — plateau threshold cần
        hiệu chuẩn thêm; mọi số liệu chỉ là calibration finding.
- [ ] Điều tra provenance FOC 2021: 20,000 cash + ~6,660=? (cổ thưởng?
      event liền kề?); FOC 2022–2026 các event value mismatch. **Ưu tiên
      cao nhất** — đây là chốt chặn duy nhất còn lại của Pha 1 re-run.
- [ ] Chỉ sau khi gap identity certified ≤ 1 tick: mở Pha 1 re-run (Bước 3).

## Quyết định kỹ thuật bổ sung (sau GREEN)

1. **Core KHÔNG làm blind breakpoint detection.** Sliding-window scan
   không distinguish được reciprocal/neighbor-k families trên window ngắn
   (thí nghiệm chứng minh: 179 nghiệm đồng điểm trên segment 6 bars).
   Report mode = single-segment assumption; undisclosed breakpoints ⇒
   FactorSolveError. Blind survey thuộc runner (non-certifying).
2. **Tie-break cộng dồn:** khi nhiều factor đồng điểm grid-residual toàn
   segment, chọn theo implied boundary ratio gần 1 nhất (drop nhỏ nhất) —
   spurious f/2 (drop 50%) bị loại; vẫn đồng điểm ⇒ fail-closed.
3. **Synthetic test data phải tránh multiples of 500** (family f×5/n
   preserve grid, artificial ambiguity).
