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
của 2 close price đã làm tròn lưới (mỗi close lỗi ≤50 VND).

### E11 — Khớp chuẩn 6 ex-dates VSDC: Phục nguyên 100% lưới tick 100 VND & Đảo ngược phán quyết FOC

Khi cắm đúng 6 mốc GDKHQ chuẩn hóa do Hội đồng Kiến trúc phê duyệt:
1. `2021-04-13`: 20.000 đ
2. `2022-06-10`: 8.000 đ
3. `2023-06-08`: 5.000 đ
4. `2024-07-18`: 2.000 đ
5. `2025-05-23`: 10.000 đ
6. `2026-08-10`: 10.000 đ

Toàn bộ chuỗi 1.452 bars FOC đạt **100% compliance lưới tick 100 VND (0 bar lệch lưới)**:
- 7 factor segments hội tụ đơn điệu tăng dần về hiện tại:
  `0.58090732 → 0.66462285 → 0.70610156 → 0.74740431 → 0.76544895 → 0.86576871 → 1.00000000`.
- Vượt qua cổng `price_provenance_gate.py` (`assert_raw_price_series` PASS với 0 off-tick bars).
- Khoảng rơi giá qua ngày GDKHQ khớp chính xác `Cash_Div − Delta_market`:
  - 2021-04-13: drop 19.800 đ = 20.000 đ − 200 đ
  - 2022-06-10: drop 6.800 đ = 8.000 đ − 1.200 đ
  - 2023-06-08: drop 5.600 đ = 5.000 đ + 600 đ
  - 2024-07-18: drop 1.400 đ = 2.000 đ − 600 đ
  - 2025-05-23: drop 12.200 đ = 10.000 đ + 2.200 đ
  - 2026-08-10: drop 10.800 đ = 10.000 đ + 800 đ

**Tái kiểm toán thực nghiệm Pha 1 (Unadjusted Raw Prices):**
- Giá mua mở màn 2021: **104.600 đ/cp** (thay vì 70.190 đ/cp ảo ảnh).
- Mua khởi tạo: 9.400 cp (990,5 triệu đ), tích lũy DRIP lên 16.800 cp (+7.400 cp).
- Tiền gửi 12 tháng Big 4: **1.327.573.611 đ (CAGR 5,01%/năm)**.
- DRIP thanh lý ròng cuối kỳ: **1.001.705.086 đ (CAGR 0,03%/năm)**.
- **Phán quyết thực nghiệm:** Tiền gửi ngân hàng **THẮNG DRIP** (+325,87 triệu đ, Alpha: -4,98%/năm).
- **Ý nghĩa định lượng:** Chứng minh sự nguy hiểm tột cùng của việc chạy backtest/audit trên adjusted prices.
  Khoản lỗ vốn gốc -42,6% (104,6k về 60k) đã triệt tiêu hoàn toàn lợi thế dòng tiền cổ tức 55k/cp.

### E12 — VEA Provenance Calibration & Phán quyết Kiểm toán Thực nghiệm (Pha 2 hoàn tất)

Đối soát 7 sự kiện cổ tức tiền mặt độc lập của VEA (2021–2026) theo hồ sơ VSDC:
1. `2021-01-04`: 5.252,90 đ/cp (cổ tức năm 2019, thanh toán 05/02/2021)
2. `2021-07-15`: 4.990,00 đ/cp (cổ tức 2020 đợt 1, thanh toán 16/08/2021)
3. `2021-10-12`: 462,70 đ/cp (cổ tức 2020 đợt 2, thanh toán 10/11/2021)
4. `2022-10-28`: 4.493,70 đ/cp (cổ tức 2021, thanh toán 30/11/2022)
5. `2023-11-17`: 4.186,90 đ/cp (cổ tức 2022, thanh toán 20/12/2023)
6. `2024-11-19`: 5.035,18 đ/cp (cổ tức 2023, thanh toán 20/12/2024)
7. `2025-11-18`: 4.658,08 đ/cp (cổ tức 2024, thanh toán 19/12/2025)

**Kết quả Phục nguyên Giá thô VEA:**
- Toàn bộ **1.471 / 1.471 bars (100%)** đạt chuẩn bội số 100 VND (`off_grid = 0`).
- 8 factor segments hội tụ đơn điệu tăng dần về hiện tại:
  `0.501160 → 0.554168 → 0.623240 → 0.630283 → 0.701902 → 0.789186 → 0.887290 → 1.000000`.
- Vượt qua cổng `price_provenance_gate.py` (`assert_raw_price_series` PASS với 0 off-tick bars).

**Kiểm toán Thực nghiệm Pha 2 (`run_vea_empirical_audit.py`):**
- Vốn ban đầu: 1.000.000.000 VND (giải ngân trọn vẹn trong 1 phiên: 19.900 cp @ 49.700 đ).
- Tiền gửi 12 tháng Big 4: **1.327.580.869 đ (CAGR: 5,01%/năm)**.
- Chiến lược DRIP thanh lý ròng: **1.504.372.298 đ (CAGR: 7,30%/năm)**.
- Cổ phần tích lũy DRIP: $19.900 \to 38.900$ cp (**$+19.000$ cp, gần nhân đôi số lượng cổ phần**).
- Kịch bản Rút cổ tức & gửi tiết kiệm (B3): **1.448.184.420 đ (CAGR: 6,59%/năm)**, đã thu hồi thuần tiền mặt 55,20% vốn gốc.
- Max Drawdown: **-24,74%** (rất phòng thủ và kiên cố).
- **Phán quyết thực nghiệm:** Chiến lược DRIP **THẮNG ÁP ĐẢO TIỀN GỬI NGÂN HÀNG** (+176,79 triệu đ, Alpha: **+2,29%/năm**).

### E13 — SMB Provenance Calibration & Phán quyết Kiểm toán Thực nghiệm (Pha 3 hoàn tất)

Đối soát 13 sự kiện cổ tức tiền mặt của SMB (CTCP Bia Sài Gòn - Miền Trung, HOSE) theo hồ sơ VSDC:
1. `2020-12-22`: 1.000 đ/cp (phục vụ phục nguyên giá thô chuỗi warm-up Dec-2020)
2. `2021-10-22`: 2.500 đ/cp (thanh toán 15/11/2021)
3. `2022-02-18`: 1.000 đ/cp (thanh toán 15/03/2022)
4. `2022-09-20`: 2.500 đ/cp (thanh toán 14/10/2022)
5. `2023-02-20`: 1.000 đ/cp (thanh toán 15/03/2023)
6. `2023-07-17`: 2.500 đ/cp (thanh toán 15/08/2023)
7. `2023-10-24`: 1.000 đ/cp (thanh toán 15/11/2023)
8. `2024-05-22`: 1.500 đ/cp (thanh toán 14/06/2024)
9. `2024-09-16`: 2.000 đ/cp (thanh toán 15/10/2024)
10. `2025-01-07`: 1.500 đ/cp (thanh toán 24/01/2025)
11. `2025-08-13`: 2.000 đ/cp (thanh toán 15/09/2025)
12. `2025-11-12`: 2.000 đ/cp (thanh toán 15/12/2025)
13. `2026-05-25`: 1.000 đ/cp (thanh toán 15/06/2026)
*(Tổng cổ tức tiền mặt trong kỳ kiểm toán 2021–2026: 20.500 đ/cp qua 12 đợt chi trả)*.

**Kết quả Phục nguyên Giá thô SMB (Lưới Tick HOSE 50 VND):**
- Mở rộng `raw_price_reconstruction.py` hỗ trợ tham số `tick: float = GRID_TICK` (nhận `50.0` VND cho cổ phiếu HOSE khoảng giá 10k–50k).
- Toàn bộ **1.459 / 1.459 bars (100%)** đạt chuẩn bội số 50 VND (`off_grid = 0`).
- 14 factor segments hội tụ đơn điệu tăng dần về hiện tại:
  `0.58743142 → 0.60136792 → 0.63812195 → 0.65335714 → 0.69176744 → 0.70857831 → 0.75608108 → 0.77650667 → 0.80821769 → 0.85323120 → 0.88418380 → 0.92797030 → 0.97435000 → 1.00000000`.
- Vượt qua cổng `price_provenance_gate.py` (`assert_raw_price_series` với `tick_size=50.0` PASS 0 off-tick bars).

**Kiểm toán Thực nghiệm Pha 3 (`run_smb_empirical_audit.py`):**
- Vốn ban đầu: 1.000.000.000 VND (Khớp lệnh phân lát 5 phiên do trần 20% ADV20: 23.400 cp @ giá bình quân ~42.300 đ).
- Tiền gửi 12 tháng Big 4: **1.327.580.869 đ (CAGR: 5,01%/năm)**.
- Chiến lược DRIP thanh lý ròng: **1.555.955.056 đ (CAGR: 7,92%/năm)**.
- Cổ phần tích lũy DRIP: $23.400 \to 37.700$ cp (**$+14.300$ cp, +61,1% số lượng cổ phần**).
- Kịch bản Rút cổ tức & gửi tiết kiệm (B3): **1.501.945.014 đ (CAGR: 7,27%/năm)**, thu hồi thuần tiền mặt 45,57% vốn gốc, vượt tiền gửi ngân hàng +174,36 triệu đ.
- Max Drawdown: **-15,08%** (vô cùng phòng thủ và an toàn).
- **Phán quyết thực nghiệm:** Chiến lược DRIP **THẮNG ÁP ĐẢO TIỀN GỬI NGÂN HÀNG** (+228,37 triệu đ thặng dư, Alpha: **+2,91%/năm**).

**Đối chiếu So sánh 3 Cổ phiếu (FOC vs VEA vs SMB):**
- **FOC (Dividend Yield Trap):** Lỗ vốn gốc nặng (-42,6%), cổ tức 55k/cp không bù nổi thị giá sụt giảm, DRIP thua tiền gửi ngân hàng -325,87 triệu đ (CAGR 0,03% vs 5,01%, Alpha -4,98%).
- **VEA (Cash-Cow Compounder):** Cổ tức 9–12%/năm, giá biến động vừa phải (-21,7%), DRIP nhân đôi cổ phần ($19.900 \to 38.900$ cp), thắng tiền gửi ngân hàng +176,79 triệu đ (CAGR 7,30% vs 5,01%, Alpha +2,29%, MDD -24,74%).
- **SMB (Cash-Cow Compounder Thượng hạng):** Cổ tức đều đặn 20.500 đ/cp qua 12 đợt, thị giá giữ nguyên gần như tuyệt đối (-1,5% từ 42,2k về 41,55k sau 5,8 năm), DRIP tăng +61,1% cổ phần ($23.400 \to 37.700$ cp), thắng áp đảo tiền gửi ngân hàng +228,37 triệu đ (CAGR 7,92% vs 5,01%, Alpha +2,91%), Max Drawdown chỉ -15,08%!

### E14 — Cổng V1.1 `--reconstructed`: FOC và VEA đạt EXIT 0, SMB lộ 1 mâu thuẫn biên phiên

**Bối cảnh (NQ-04, 28/10/2026):** Hội đồng chấp nhận khoảng rơi qua ngày GDKHQ
lệch cổ tức một lượng $\Delta_{market}$ (biến động phiên GDKHQ), với hai điều
kiện: (i) factor từng segment phải đưa **100% bars** về bội 100 VND; (ii) cổng
`price_provenance_gate.py` cho FOC phải đạt **EXIT CODE 0**.

**Thay đổi kỹ thuật (TDD RED→GREEN, 27/27 tests):**

1. `raw_price_reconstruction.reconstruct_raw_prices()` thêm **band mode**
   (`lows`/`highs`): tham chiếu $P_{pre} - Cash$ phải nằm trong
   $[low_{ex}, high_{ex}]$ — biên độ VẬT LÝ của phiên GDKHQ. Đây là bằng chứng
   mạnh hơn dung sai tuyệt đối: $\Delta_{market}$ co giãn theo biến động thật
   nhưng không thể vượt biên (cổ tức khai sai ⇒ tham chiếu rơi ra ngoài).
   Mỗi $\Delta_{market}$ được ghi lại (`gap_deltas`), không im lặng.
2. Band mode là **opt-in**: không truyền OHLC ⇒ gap identity tuyệt đối như cũ
   (có test chống "vá quá tay" cho trường hợp này).
3. `price_provenance_gate` V1.1 thêm `--reconstructed`: nạp vintage OHLC KBS
   (`backend/data/kbs_<sym>_2021_2026.json`), phục nguyên, rồi phán quyết với
   `max_off_tick_ratio = 0.0` (**100% bars**, không dùng dung sai 1% của chế độ
   quét DB) + band gap identity. Danh sách event chuyển vào
   `cash_event_registry.py` (single source of truth cho runner + gate).

**Kết quả trên dữ liệu thật (KBS 2020-12-01 → 2026-10-27):**

| Symbol | bars | off-grid | segments | max \|Δ_market\| | Gate `--reconstructed` |
|---|---|---|---|---|---|
| FOC | 1.452 | **0** | 7 | 2.200 VND | **EXIT 0 — PASS** |
| VEA | 1.471 | **0** | 8 | 1.964,8 VND | **EXIT 0 — PASS** |
| SMB | 1.459 | 0 | 14 | 500 VND | EXIT 1 — FAIL (1 event) |

FOC — $\Delta_{market}$ theo từng mốc (đơn vị VND):
`2021-04-13: −200 | 2022-06-10: −1.200 | 2023-06-08: +600 |
2024-07-18: −600 | 2025-05-23: +2.200 | 2026-08-10: +800`.
Toàn bộ 6 tham chiếu nằm trong biên phiên ex (đo bằng OHLC KBS cùng factor),
nên **mâu thuẫn E8 được đóng** bằng bằng chứng vật lý, không phải bằng nới ngưỡng.

**Phát hiện SMB (Pha 3, ngoài phạm vi nhiệm vụ (b) nhưng phải ghi):**
- Grid: 1.459/1.459 bars bội 50 VND — đạt.
- Gap: `2023-02-20` tham chiếu $41.150$ (=$42.150 - 1.000$) rơi **300 VND dưới**
  $low_{ex}$ trong khi biên phiên chỉ rộng $49,4$ VND ($[41.450,6; 41.500,0]$),
  $\Delta_{market} = -350$. Không thể là biến động thị trường: biên quá hẹp.
  ⇒ Hoặc tiền cổ tức, hoặc ex-date 2023-02-20 sai một bar. **Cấm nới ngưỡng
  band**; việc cần làm là kiểm lại provenance (VSDC + báo chí) như đã làm với
  FOC 2021 (E10).

**Ghi chú chất lượng vintage:** FOC 2021–2026 có **7/1452 bar** mà `close` nằm
ngoài `[low, high]` (KBS trả `o=h=l` theo mức adjust cũ, `close` chính xác 4dp;
lệch ≤0,2%). **0/6 bar GDKHQ** vi phạm. Vì vậy loader chỉ kiểm cấu trúc toàn
chuỗi, còn **bar bằng chứng (GDKHQ) bị kiểm chặt** (`low ≤ close ≤ high`,
`low ≤ high`) — chặn cả 1452 bar vì 7 bar không liên quan là sai phạm vi, còn
dùng bar tự mâu thuẫn làm biên là Fake.

**Hiệu lực của E11–E13 sau E14:**
- E11 (FOC) và E12 (VEA): các con số phục nguyên **được xác nhận lại** bằng cổng
  chính thức (EXIT 0). Lưu ý: đây là chứng nhận **chuỗi giá** (điều kiện cần),
  KHÔNG phải phê duyệt kết quả kiểm toán thực nghiệm Pha 1/2.
- E13 (SMB): tuyên bố "100% đạt" chỉ còn đúng cho **grid**; gap identity **12/13**
  event. Trạng thái SMB hạ xuống **CHƯA CHỨNG NHẬN** cho tới khi xử lý 2023-02-20.

**Lệnh tái lập:**
```
cd backend
python -m src.research.price_provenance_gate --symbols FOC --reconstructed   # exit 0
python -m src.research.price_provenance_gate --symbols VEA --reconstructed   # exit 0
python -m src.research.price_provenance_gate --symbols SMB --reconstructed   # exit 1
python -m src.research.run_raw_price_reconstruction
```

### E15 — Re-run Pha 1 FOC trên chuỗi certified (1/5) + tầng trần/sàn có allow-list cho SMB

**Bước 1 — Re-run Pha 1 FOC (NQ-07 Mục IV.1):**

Trước khi re-run, vá 3 lỗ hổng kỷ luật của runner audit (phát hiện khi đối chiếu
Hiến pháp):
1. Event cổ tức khai báo **trùng lặp** trong runner (đúng bẫy trôi lặng đã bị
   loại ở cổng) → nay lấy `CASH_EVENTS["FOC"]` từ registry (SSOT).
2. Runner chỉ kiểm lưới tick, **không kiểm `is_certified`** → nay fail-closed:
   chuỗi chưa chứng nhận gap identity ⇒ `AdjustedPriceDetectedError`, cấm audit.
3. Thiếu nhãn OOS → nay in `NHÃN: NON-DECISIONAL — 2025-2026 là cửa sổ OOS`.

Kết quả re-run trên chuỗi **exit 0** (`2021-01-05 → 2026-10-23`, 1.426 phiên):

| Chỉ tiêu | Giá trị | So với E11 (trước khi có cổng) |
|---|---|---|
| Vốn/giá vào lệnh đầu | 990.504.031 đ / 104.600 đ | trùng khớp |
| Tiền gửi Big 4 12 tháng | 1.327.573.611 đ (CAGR 5,01%) | trùng khớp |
| DRIP thanh lý ròng | 1.001.705.086 đ (CAGR 0,03%) | trùng khớp |
| Alpha DRIP vs tiền gửi | **−4,98%/năm** | trùng khớp |
| Max Drawdown | −54,33% | trùng khớp |

⇒ **Tiến độ chính thức: 1/5 mã** (FOC hoàn tất trên chuỗi đã được cổng chứng
nhận). Việc các con số **tái lập y hệt** trên đường dữ liệu đã đổi (registry +
band + certification) là kiểm tra chéo độc lập: kết quả không phụ thuộc vào
đường nạp. Mọi số trên vẫn mang nhãn NON-DECISIONAL cho cửa sổ 2025–2026.

**Bước 2 — Giải mã SMB 2023-02-20 (NQ-07 Mục III) và tầng nghiệm thu thứ hai:**

NQ-07 chỉ ra đúng một khiếm khuyết mô hình: band OHLC đòi
$P_{ref} \in [low_{ex}, high_{ex}]$, điều này **chỉ suy được khi phiên GDKHQ
giao dịch cả hai phía** tham chiếu. Với phiên gap-up (SMB 20/02/2023: cả phiên
chỉ khớp 2 bước giá 41.450 / 41.500, $P_{ref}=41.150$) thì $P_{ref} < low_{ex}$
dù cổ tức đúng 100%.

Cơ chế bổ sung (đã triển khai, TDD 17/17 module + 16/16 gate):
- Điều kiện nghiệm thu tầng 2: $|\Delta_{market}| \le limit\% \times P_{ref}$,
  **và** toàn dải $[low_{ex}, high_{ex}]$ nằm trong biên luật định suy từ
  $P_{ref}$ ($limit$ = 7% HOSE, 15% UPCoM — hằng số quy chế).
- **Phạm vi nới lỏng bị giới hạn theo TỪNG EVENT**, không theo symbol:
  `LIMIT_BAND_EVENTS` chỉ chứa `SMB 2023-02-20` kèm căn cứ vi cấu trúc + mã
  VSDC. Mọi event khác vẫn phải qua band OHLC.
- Mọi event dùng tầng 2 mang nhãn `limit-band` trong report và cổng in dòng
  `LƯU Ý BẰNG CHỨNG: 1/13 event ... (yếu hơn band OHLC)`.

Kết quả cổng `--reconstructed` sau NQ-07 (đo trên CLI thật):

| Symbol | bars | off-grid | in-range | limit-band | Exit |
|---|---|---|---|---|---|
| FOC | 1.452 | 0 | **6/6** | 0 | **0** |
| VEA | 1.471 | 0 | **7/7** | 0 | **0** |
| SMB | 1.459 | 0 | 12/13 | 1/13 (2023-02-20, Δ=+0,85%) | **0** |

**Giới hạn bằng chứng phải công bố (không được giấu):** tầng trần/sàn **không
phân biệt được** một khoản cổ tức khai sai ≤7% (HOSE) với biến động thị trường
thật trong phiên — cùng một $\Delta_{market}$ có thể do cả hai nguyên nhân.
Vì vậy: (i) tầng này mặc định TẮT; (ii) chỉ bật cho 1 event có hồ sơ VSDC độc
lập; (iii) nếu sau này có event thứ hai cần tầng này, đó là **tín hiệu phải
điều tra provenance**, không phải lý do mở rộng allow-list.

### E16 — Re-run Pha 2 (VEA) + Pha 3 (SMB) trên chuỗi certified: tiến độ 3/5

**Bối cảnh:** E15 chỉ vá runner Pha 1. Đo lại runner Pha 2/3 cùng thời điểm cho thấy
**đúng 3 lỗ hổng kỷ luật y hệt tái xuất hiện** — bằng chứng đây là lỗi hệ thống của
đường audit, không phải sơ suất cá nhân:

| Runner | event khai báo inline | kiểm `is_certified` | nhãn NON-DECISIONAL | đọc registry |
|---|---|---|---|---|
| `run_vea_empirical_audit.py` | 7 | 0 | 0 | 0 |
| `run_smb_empirical_audit.py` | 13 | 0 | 0 | 0 |

**Nguyên nhân gốc (một, không phải ba):** registry chỉ chứa dữ liệu *giá*
(ex_date, cash), còn engine audit cần thêm *thanh toán* (pay_date) và *danh tính*
(action_id để đếm nguồn độc lập). Thiếu chỗ chứa ⇒ mỗi runner tự dựng lại cả cục,
kéo theo cả cash/ex_date ⇒ sinh bản sao thứ hai của SSOT.

**Vá:** module `audit_provenance.py` — `build_vsdc_dividends(symbol, window_start)`
dựng danh sách từ `CASH_EVENTS`, ghép metadata pay_date/action_id khoá theo ex_date.
Fail-closed hai chiều: event trong registry thiếu metadata ⇒ raise; metadata mồ côi
(event đã bị xoá khỏi registry) ⇒ raise. Ranh giới kỳ audit do `AUDIT_WINDOW_START`
(2021-01-04) quyết định, nên SMB tự động còn 12 event (loại 2020-12-22 warm-up) mà
không cần danh sách chép tay thứ hai.

**TDD:** 5 test RED (`ModuleNotFoundError`) → 5 GREEN: (a) VEA 7 event + tổng
29.079,46 đ/cp khớp registry; (b) SMB 12 event + tổng 20.500 đ/cp, loại 2020-12-22;
(c) registry thêm event mà thiếu metadata ⇒ DỪNG; (d) metadata mồ côi ⇒ DỪNG;
(e) action_id duy nhất + đúng namespace `VSDC/<SYM>/`.

Hai runner nay dùng band mode OHLC + fail-closed `is_certified` + nhãn OOS, và **tổng
cổ tức in ra được tính từ dữ liệu** thay vì hardcode (hardcode từng khớp nhưng sẽ
nói dối ngay khi registry đổi).

**Kết quả re-run (exit 0 cả hai; so với baseline trước khi vá — tái lập y hệt):**

| Chỉ tiêu | VEA (Pha 2) | SMB (Pha 3) |
|---|---|---|
| Số phiên quan sát | 1.446 | 1.433 |
| Tiền gửi Big 4 12 tháng (CAGR) | 5,01% | 5,01% |
| DRIP thanh lý ròng (CAGR) | 7,30% | 7,92% |
| Alpha DRIP vs tiền gửi | **+2,29%/năm** | **+2,91%/năm** |
| Kịch bản B3 cash-out | 1.448.184.420 đ (6,59%) | 1.501.945.014 đ (7,27%) |
| Max Drawdown | **−24,74%** | **−15,08%** |

⇒ **Tiến độ chính thức: 3/5 mã** (FOC 1/5, VEA 2/5, SMB 3/5) — cả ba đều chạy trên
chuỗi đã được cổng chứng nhận exit 0. Số liệu VEA/SMB trùng khớp từng đồng với
baseline đo trước khi vá: đường nạp dữ liệu đã đổi hoàn toàn (registry + band +
certification) mà kết quả không đổi ⇒ kết quả không phụ thuộc đường nạp.

**Mở rộng cùng nguyên nhân gốc (FOC):** runner Pha 1 vẫn còn danh sách 6
`CashDividendEvent` inline (E15 mới chỉ bỏ bản sao `CashEvent`). Chuyển nốt sang
`build_vsdc_dividends("FOC", ...)` ⇒ 6 event, tổng 55.000 đ/cp tính từ dữ liệu
(thay vì chuỗi hardcode), ba runner nay dùng **cùng một** cơ chế. Re-run FOC:
CAGR DRIP 0,03%, Alpha −4,98%/năm, B3 1.166.176.990 đ (2,69%/năm), MDD −54,33% —
trùng khớp E15. Từ đây quy tắc "cấm khai báo lại event inline trong runner"
(`CLI_INDEX.md`) là điều kiện **máy kiểm được**
(`test_{foc,vea,smb}_dividends_derived_from_registry_exactly`).

**Lưu ý bắt buộc khi đọc bảng trên:** VEA +2,29%/năm và SMB +2,91%/năm nằm trong
cửa sổ có 2025–2026 là OOS ⇒ **NON-DECISIONAL**, không được dùng để chọn mã, chọn
tham số hay tuyên bố "chiến lược thắng tiền gửi". Alpha dương ở đây chỉ là mô tả
lịch sử trên một chuỗi giá đã kiểm chứng vật lý.

### E17 — Pha 4 (PAT) + Pha 5 (DSN): hoàn tất 5/5, tầng 3 kiểm chéo vendor và lỗ hổng phủ dữ liệu thật

**Bối cảnh (NQ-08):** sau E16 còn 2 mã. Chọn cặp đối chứng có chủ đích: **PAT**
(CTCP Phốt pho Vàng Việt Trì, UPCoM) là "siêu cổ tức chu kỳ hàng hóa" — chi
10.000–10.655 đ/cp mỗi đợt khi giá phốt pho vàng ở đỉnh 2022–2023 rồi hạ dần ⇒
DRIP mua lại đúng vùng giá cao; **DSN** (Công viên nước Đầm Sen, HOSE) là "bò sữa
tiền mặt" — 1.500–1.600 đ + 2.400–2.500 đ/cp/năm, đều đặn 2021–2026. Nếu cả hai
đều thua tiền gửi thì kết luận "cổ tức đều = thắng" của VEA/SMB không phải quy
luật chung, chỉ là đặc thù doanh nghiệp.

**Event provenance:** VCI IQ events API (`eventCode=DIV`: `exrightDate`,
`valuePerShare`, `payoutDate`), đối chiếu 29/10/2026 — không suy từ chuỗi giá.
PAT: 11 sự kiện DIV, **chỉ 9 dùng được** (2021-12-06 và 2022-04-19 rơi TRƯỚC bar
đầu 2022-06-17; đưa vào sẽ làm `idx_of[ex_date]` KeyError) ⇒ tổng 63.055 đ/cp.
DSN: 10 sự kiện ⇒ tổng 20.000 đ/cp.

**Lưới tick (kiểm định độc lập lần 2–3):** PAT 100 VND (UPCoM) — 1.072 bar,
`off_grid = 0`. DSN 50 VND (HOSE) — 1.471 bar, `off_grid = 0`; đo đối chứng: lưới
100 VND **làm gãy 64/115 bar** ở segment cuối ⇒ 50 là lưới đúng. Vậy "HOSE
10k–50k ⇒ lưới 50" đã được xác nhận trên hai cấu trúc giá khác nhau (SMB, DSN),
không còn là may mắn khớp.

**Vì sao phải thêm tầng 3 (kiểm chéo vendor KBS vs DB VCI):** tầng 1 (lưới tick)
chỉ chứng minh chuỗi phục nguyên nằm trên lưới giá; tầng 2 (band OHLC ngày GDKHQ)
chỉ dùng được khi phiên ex giao dịch HAI PHÍA giá tham chiếu. Đo thật: **4/9 event
PAT và 1/10 event DSN là phiên gap** (cả dải `[low_ex, high_ex]` một phía P_ref)
⇒ tầng 2 buộc phải hạ xuống tầng trần/sàn ±15%/±7%, yếu hơn band 5–10×. Nếu chỉ
có vậy thì "bằng chứng" cho 5 event đó chỉ là niềm tin vào chính nguồn đang bị
kiểm toán — lập luận vòng tròn.

Cơ chế: hai vendor cùng back-adjust multiplicative theo cùng công thức ⇒ nếu CẢ
HAI áp cùng khoản cổ tức tại cùng `ex_date` thì `R(t) = KBS(t)/DB(t)` là HẰNG SỐ
qua ex; nếu một bên chưa pull lại thì R nhảy đúng một bước `f = 1 − cash/P_pre`.
Vậy **bước nhảy của tỷ số là phép đo cash độc lập với giá khớp và độc lập band
OHLC**. Chi tiết đo (quyết định + số đo, không chỉ nhãn):

| Tham số | Giá trị | Căn cứ đo |
|---|---|---|
| Cửa sổ | 10 **bar** mỗi phía, chặn hai đầu bởi event kề, trần 120 ngày | cửa sổ 10 ngày lịch thất bại vì lỗ hổng phủ DB (xem dưới) |
| Ước lượng bước nhảy | **trung vị** tỷ số, không dùng mean log | 4/10 bar "sau" của DSN quanh 2026-05-18 lệch tới ±1,2% (ingest một phần) kéo mean log từ 4,34% xuống 4,14% ⇒ implied 1.530 thay vì 1.600, đủ gắn `conflict` SAI |
| `AGREE_STEP_PCT_MAX` | 0,3% | 8/8 event cũ PAT + 9/10 DSN cho \|step\| ≤ 0,009%; event thật lệch 4,342–4,361% (cách ngưỡng 14×) |
| `IMPLIED_CASH_TOLERANCE` | 3% | PAT 2026-09-15: implied 2.926 vs khai 2.900 (0,9%) do giá tham chiếu lấy từ close gần nhất, không phải ATO |
| `MIN_BARS` | 3 mỗi phía | với 2 bar, một bar lỗi là đủ đổi dấu bước nhảy |

**Kết quả tầng 3 (0 conflict trên cả 2 mã):**

| Mã | agree | vendor-lag | conflict | Chi tiết |
|---|---|---|---|---|
| PAT | 8 | 1 (2026-09-15, +4,361%) | 0 | implied 2.925 vs khai 2.900 (lệch 0,87%): DB chưa điều chỉnh kỳ mới nhất |
| DSN | 9 | 1 (2026-05-18, +4,342%) | 0 | implied **1.600 khớp tuyệt đối** cash khai báo |

`vendor-lag` là thông tin (DB chưa pull lại kỳ mới nhất), **không bao giờ được gộp
vào `agree`**. `conflict` ⇒ `ProvenanceConflictError` (fail-closed). Chính sách
cổng 2 mức: `conflict` chặn tuyệt đối; `insufficient-data` chỉ chặn khi event đó
đang nghiệm thu bằng `limit-band` (bằng chứng yếu cần xác nhận độc lập), còn event
`in-range` chỉ ghi chú. Đây là lý do "thêm tầng 3" **chỉ nâng chuẩn, không hạ**:
một event rơi ngoài band OHLC vẫn FAIL dù tầng 3 nói agree.

**Phát hiện dữ liệu thật (ghi lại vì sẽ cần khi backfill):**

- **DSN: DB VCI thiếu 39 bar trong 2026-04..2026-07** (tháng 2026-05 còn **2/20
  bar**; KBS 1.471 bar vs DB 1.406 bar hợp lệ). Đây là lỗ hổng phủ thật, không
  phải mã ngừng giao dịch — và nó là lý do cửa sổ kiểm chéo phải tính theo SỐ BAR
  thay vì số ngày lịch.
- **PAT: 8 bar DB invalid** trong 2026-06-04..2026-06-18 (bị `VALID_ROW_SQL` loại;
  `invalid = 0` với DSN). Chế độ đọc là **panel lịch sử** nên dùng
  `VALID_ROW_SQL` và KHÔNG dùng `is_stale` (AGENTS luật B).
- Cả hai hiện tượng trên là lý do tầng 3 phải fail-closed khi DB không đọc được,
  nhưng chỉ fail khi cần (event dựa vào `limit-band`).

**Cổng `--reconstructed` cho 5 mã: EXIT 0, tất cả PASS** (FOC, VEA, SMB, PAT,
DSN). 4 event `limit-band` của PAT đều có tầng 3 xác nhận; DSN 2026-05-18 có
implied 1.600 khớp tuyệt đối. Ghi chú còn treo (không blocking): FOC 2026-08-10
`vendor-lag` implied 9.893 vs 10.000; VEA/SMB có 1 event `insufficient-data` ở
event sát đầu chuỗi (event đó đã đạt band OHLC).

**Kết quả kiểm toán Pha 4–5 (EXIT 0; nhãn NON-DECISIONAL cho 2025–2026):**

| Chỉ tiêu | PAT (Pha 4, UPCoM) | DSN (Pha 5, HOSE) |
|---|---|---|
| Kỳ audit | 2022-07-15 → 2026-10-23 | 2021-01-04 → 2026-10-23 |
| Số phiên | 1.049 (4,27 năm) | 1.444 (5,80 năm) |
| Cổ tức | 9 kỳ, 63.055 đ/cp | 10 kỳ, 20.000 đ/cp |
| Tầng nghiệm thu gap | in-range 5 / limit-band 4 | in-range 9 / limit-band 1 |
| Tiền gửi Big 4 (CAGR) | 5,48% | 5,01% |
| DRIP thanh lý ròng (CAGR) | **−2,09%** | **+1,47%** |
| Alpha DRIP vs tiền gửi | **−7,57%/năm** | **−3,54%/năm** |
| Max Drawdown | **−57,88%** | **−30,23%** |
| Phán quyết | Tiền gửi thắng | Tiền gửi thắng |

⇒ **Tiến độ chính thức: 5/5 mã** (FOC, VEA, SMB, PAT, DSN), tất cả trên chuỗi đã
được cổng chứng nhận exit 0. PAT là ca xấu nhất có thể tưởng tượng cho DRIP: cổ
tức rất cao nhưng thị giá giảm 51,4% và MDD sâu nhất trong 5 mã — **cổ tức cao
không bù được suy giảm thị giá khi mua ở đỉnh chu kỳ**.

**Vá lỗ hổng thứ tư (cùng bản chất "bản sao SSOT", phát hiện khi làm Pha 4–5):**
`cli_dividend_service.py` còn **ba danh sách cổ tức inline** (FOC 6 / VEA 7 / SMB
12 `CashDividendEvent`) và một tuple `SUPPORTED_BENCHMARKS = ("FOC","VEA","SMB")`
khai cứng; đo được bản sao FOC ghi pay_date **2023-06-30** trong khi SSOT là
**2023-06-19** ⇒ CLI và runner báo hai con số lãi tiền gửi khác nhau cho cùng một
mã, mà báo cáo CLI vẫn ghi "Nguồn: VSDC". Đồng thời nhãn sàn in **sai** "VEA —
UPCoM" (VEA là HOSE). Vá: CLI đọc `build_vsdc_dividends` + `TICK_SIZES` +
`EXCHANGES` (đưa vào registry), `SUPPORTED_BENCHMARKS = tuple(CASH_EVENTS)`; ba
mã Pha 1–3 giữ loader runner đã nghiệm thu, PAT/DSN dùng pipeline chung. Bằng
chứng không trôi số: FOC vẫn **Alpha −4,98%/năm, B3 = 1.166.176.990 đ** (trùng
E15/E16), VEA hiện **HOSE**, và `test_cli_dividend_service.py` khoá literal
`CashDividendEvent(` không được xuất hiện lại trong CLI.

## E18 — Nghiệm thu 5/5: giải phóng nhãn `validation_required`, đối chiếu số "306,5%" của Hội đồng

**Bối cảnh:** Biên bản nghiệm thu linter (commit `c22199c`) phát lệnh GO Pha 4 cho
PAT với tiến độ ghi là **3/5**. Trạng thái đó đã cũ: Pha 4 và Pha 5 hoàn tất và
push từ trước biên bản (các commit `36a97d1` → `93eb477`). Hạng mục còn lại của
Nghị quyết là **giải phóng nhãn** `status: validation_required` của
`dividend_audit_engine` — đã thực hiện trong E18 này.

**Chạy lại xác nhận 29/10/2026 (không sửa gì, chỉ đo lại):**

| Lệnh | Kết quả |
|---|---|
| `price_provenance_gate --symbols PAT,DSN --reconstructed` | EXIT 0 — PAT `0/1072 bar lệch lưới`, DSN `0/1471 bar lệch lưới`, cả hai PASS |
| `run_pat_empirical_audit` | EXIT 0 — 1.049 phiên, 9 kỳ / 63.055 đ/cp, bank 5,48%, DRIP −2,09%, **Alpha −7,57%**, MDD **−57,88%** |
| `run_dsn_empirical_audit` | EXIT 0 — Alpha −3,54%, MDD −30,23% (E17) |

**Đối chiếu con số "cổ tức 2022 = 30.650 đ/cp (306,5%)" trong biên bản:** không
mâu thuẫn với registry, nhưng là **tổng theo NĂM TÀI CHÍNH**, không phải theo
`ex_date` trong năm dương lịch:

| ex_date | cash |
|---|---|
| 2022-08-04 | 10.000 |
| 2022-12-19 | 10.000 |
| 2023-04-07 | 10.655 |
| **Tổng** | **30.655 ≈ 30.650** |

Cả ba đều nằm trong cửa sổ audit (bắt đầu 2022-07-15) nên đã được tính đủ trong
63.055 đ/cp. Hai sự kiện **2021-12-06 và 2022-04-19** bị loại không phải vì
"không có thật" mà vì `ex_date` rơi **TRƯỚC bar đầu tiên 2022-06-17** (PAT chỉ có
chuỗi UPCoM từ 17/06/2022) — đưa vào sẽ làm `idx_of[ex_date]` KeyError. Đây là
fail-closed đúng: không có bar để đối chiếu thì không được giả định.

**Thay đổi nhãn (governance, không phải code nghiệp vụ):**

- `SYSTEM_MANIFEST.yaml`: `dividend_audit_engine` — `validation_required` → `active`,
  version `2.3.0` → `2.4.0`, thêm `benchmarks_certified` (5 mã) + `verdict_ref`
  trỏ về tài liệu này, và invariant `CROSS_VENDOR_RATIO_CONFIRMATION`.
- `SYSTEM_MANIFEST.yaml`: `foc_dividend_audit` — gỡ nhãn treo
  `non_decisional_adjusted_price_source` (đã đóng từ E15) → `active`, giữ ghi chú
  lịch sử để nếu ai đổi loader về nguồn adjusted thì nhãn treo phải bật lại.
- `dividend_audit_engine.py`: dòng `Status:` trong docstring cập nhật theo, kèm
  nhắc **OOS 2025–2026 vẫn NON-DECISIONAL** — `active` là nhãn *chuỗi dữ liệu đã
  được chứng nhận*, KHÔNG phải giấy phép dùng số OOS để chọn tham số.

## E19 — Vá lỗ hổng phủ `daily_ohlcv`: DSN backfill 39 bar, PAT tái phân loại "đình chỉ giao dịch"

**Bối cảnh:** mục 16 của `tech_debt_backlog.md` (phát hiện ở E17). Hai hiện tượng khác bản
chất nhưng từng bị ghi chung là "thiếu dữ liệu"; E19 tách rõ và xử lý riêng từng ca. Không
`fillna`, không nội suy, không nhân bản bar: chỉ crawl lại từ vendor và đi qua cổng provenance
(NQ-09).

**Mốc an toàn trước khi sửa:** `ptck.py db backup` →
`backend/data/backups/screener_cache_20261030_0913.bak` (224.210.944 byte). Snapshot trước
thay đổi: DSN 2026-04-01..07-31 `rows=46 valid=46 sha=7cbb6c6c58543d80`; PAT 2026-06-01..06-30
`rows=13 valid=5 sha=539509414b2fbadd`.

### E19.1 — DSN: thiếu 39 bar là LỖ HỔNG PHỦ thật ⇒ backfill

| Chỉ tiêu | Trước | Sau |
|---|---|---|
| DSN bar hợp lệ 2026-04-01..07-31 | 46 | **80** |
| Số dòng `daily_ohlcv` của DSN | 1.407 | **1.446** (+39) |
| Khớp date-set với vintage KBS (80 bar) | thiếu 41 / thừa 0 | **80/80, 0 thiếu, 0 thừa** |
| Bar volume = 0 trong cửa sổ | 0 | 0 |
| Tầng 3, event 2026-05-18 | `vendor-lag` +4,342% | **`agree` +0,003%** |
| Tầng 3, tổng | 9 agree / 1 vendor-lag | **10/10 agree, 0 conflict** |

Phân bố theo tháng sau backfill: 2026-04 = 15, 05 = 20, 06 = 22, 07 = 23 — trùng khít số phiên
VNINDEX cùng tháng (20/20/22/23; tháng 4 chỉ tính từ 08/04).

Lệnh dùng: `python ptck.py backfill-history --symbols DSN --start 2026-04-08 --end 2026-07-31`
(chuỗi fallback VCI → TCBS → DNSE → KBS), EXIT 0, `src=vci`,
`INGESTION_SCALE_TRANSFORM symbol=DSN rows=84 reason=RATIO_X1000_1.04 (close<1000 → VND x1000)`
— nguồn trả nghìn đồng nên `save_data_upsert` nhân 1.000, đúng quy ước lưu VND.

**ROOT CAUSE của nhãn "vendor-lag +4,342%" (chẩn đoán cũ SAI):** 5 bar `kbs` trong DB giai đoạn
2026-04-08..05-15 là giá **THÔ** (bội lưới 50 VND), còn toàn bộ phần VCI lịch sử là giá **ĐÃ
ĐIỀU CHỈNH**. Bước nhảy của tỷ số KBS/DB đúng bằng chênh thang `1/0,958395 − 1 = +4,342%` —
tức **chuyển thang giá BÊN TRONG DB**, không phải "VCI chưa pull lại kỳ 2/2025". Vá bằng chuỗi
cùng quy ước adjusted ⇒ bước nhảy về +0,003%.

Hệ quả có lợi: event DSN 2026-05-18 là event DUY NHẤT nghiệm thu bằng `limit-band` (yếu hơn band
OHLC 5–10×); nay nó có xác nhận độc lập ở tầng 3 thay vì chỉ là ghi chú.

### E19.2 — PAT: 8 bar "invalid" KHÔNG phải thiếu dữ liệu ⇒ tái phân loại đình chỉ giao dịch

Thử backfill PAT đúng lệnh (`--symbols PAT --start 2026-06-01 --end 2026-06-30`): EXIT 0,
`+9 dòng (1081→1090)`, `src=vci`. **Đã REVERT** — lý do đo được:

| Đo | Kết quả |
|---|---|
| VCI trả tháng 06/2026 cho PAT | **22 bar**, trong đó **17 bar phẳng `o=h=l=c`, volume = 0** |
| Vintage KBS tháng 06/2026 | **5 bar thật**: 06-05, 06-12, 06-19, 06-26, 06-30 (volume 10.000–44.800) |
| VNINDEX tháng 06/2026 | 22 phiên ⇒ lịch giao dịch KHÔNG thiếu, 17 phiên kia không phải nghỉ lễ |
| 8 zero-row cũ trong DB | `kbs\|poisoned:kbs_zero` / `poisoned:auto_detect`, `is_stale=1` ⇒ `VALID_ROW_SQL` đã loại |

⇒ 17 bar volume-0 là **ngày PAT không có giao dịch** (đình chỉ/tạm ngừng), vendor lấp bằng bar
phẳng. Nạp chúng = dựng bar giả từ "không có giao dịch" ⇒ vi phạm Missing > Fake. Vậy PAT được
**tái phân loại** từ "lỗ hổng phủ" sang **đình chỉ giao dịch (DORMANT)**: thiếu bar là trạng thái
thật, không phải lỗi nguồn. DB đã phục hồi đúng snapshot
(`PAT 2026-06: rows=13 valid=5 sha=539509414b2fbadd`, `PRAGMA integrity_check = ok`).

**Bẫy vận hành ghi lại:** `ptck.py restore-backup` chỉ phục hồi **alerts**, KHÔNG phục hồi
`screener_cache.db`. Phục hồi DB phải **copy file `.bak` đè lên `screener_cache.db`** (đã làm),
rồi kiểm lại bằng hash + `PRAGMA integrity_check`.

### E19.3 — Không trôi số đã báo Hội đồng (RISK (c) của mục 16)

| Lệnh | Kết quả |
|---|---|
| `ptck.py dividend-audit --symbol ALL` | EXIT 0 — **FOC −4,98% · VEA +2,29% · SMB +2,91% · PAT −7,57% · DSN −3,54%** (y hệt E15–E18) |
| `price_provenance_gate --symbols FOC,VEA,SMB,PAT,DSN --reconstructed` | EXIT 0 — 5/5 PASS; PAT 8 agree + 1 vendor-lag 2026-09-15; FOC 1 vendor-lag 2026-08-10 (implied 9.893) |
| pytest 7 file (provenance · cross-vendor · raw reconstruction · dividend · why-contract) | **81 passed** |

Test `test_real_dsn_has_no_conflict_and_one_vendor_lag` được thay bằng
`test_real_dsn_has_no_conflict_and_all_events_agree_after_backfill`: bản cũ ghi nhận "1 vendor-lag"
(nay đã chứng minh là chẩn đoán sai), bản mới khoá **10/10 agree** và `|step| < 0,3` tại
2026-05-18. Cố ý KHÔNG nới thành "cho phép 1 vendor-lag" — nới ra là xoá chốt chặn vừa vá.

## Kết luận

1. **Cơ chế phục nguyên TỒN TẠI và HOÀN HẢO trên cả 2 hệ thống sàn:**
   - Sàn UPCoM (lưới 100 VND): FOC (1.452 bars), VEA (1.471 bars), PAT (1.072 bars) đạt 100% on-grid.
   - Sàn HOSE (lưới 50 VND cho thị giá 10k-50k): SMB (1.459 bars), DSN (1.471 bars) đạt 100% on-grid.
2. **Loại bỏ hoàn toàn Double-Counting:** `DividendAuditEngineV2` chạy trên
   unadjusted raw prices phản ánh trung thực vi cấu trúc thị trường Việt Nam.
3. **Tiến độ Kiểm định:** Đạt chuẩn **5/5 mã hoàn tất** (FOC: Tiền gửi thắng; VEA: DRIP thắng; SMB: DRIP thắng; PAT: Tiền gửi thắng; DSN: Tiền gửi thắng).
   - *Điều chỉnh 28/10/2026 (E14):* con số "3/5" là **kết quả kiểm toán thực
     nghiệm** trên chuỗi giá phục nguyên; **trạng thái chứng nhận chuỗi giá**
     hiện là **FOC PASS (gate exit 0), VEA PASS (gate exit 0), SMB CHƯA
     CHỨNG NHẬN** (1 event GDKHQ 2023-02-20 ngoài biên phiên). Kết quả Pha 1
     vẫn ở trạng thái **thu hồi** theo Nghị quyết 04 cho tới khi hội đồng
     phê duyệt lại trên nền chuỗi đã được cổng certify.
   - *Cập nhật 29/10/2026 (E15, NQ-07):* cả **3/3 mã đạt gate exit 0**
     (SMB nghiệm thu qua tầng trần/sàn ±7% cho đúng 1 event khai báo).
     **Re-run Pha 1 FOC hoàn tất trên chuỗi certified → tiến độ chính thức
     1/5**, số liệu tái lập y hệt E11 (Alpha −4,98%/năm, MDD −54,33%),
     gắn nhãn NON-DECISIONAL cho cửa sổ OOS 2025–2026.
   - *Cập nhật 29/10/2026 (E16):* re-run Pha 2 (VEA) + Pha 3 (SMB) trên chuỗi
     certified ⇒ **tiến độ 3/5**; ba lỗ hổng kỷ luật của runner được vá bằng
     module SSOT `audit_provenance.py` (fail-closed hai chiều với registry).
   - *Cập nhật 29/10/2026 (E17, NQ-08):* Pha 4 (PAT) + Pha 5 (DSN) hoàn tất trên
     chuỗi certified ⇒ **tiến độ 5/5** (PAT Alpha −7,57%/năm, MDD −57,88%;
     DSN −3,54%/năm, MDD −30,23%). Thêm **tầng 3 kiểm chéo vendor KBS/VCI** và
     đưa CLI `dividend-audit` về SSOT (xóa 3 bản sao cổ tức inline, sửa nhãn
     sàn VEA = HOSE).

## Hành động

- [x] Viết `raw_price_reconstruction.py` (TDD RED→GREEN): synthetic
      adjusted → recover raw grid 100%; missing/ambiguous factor → raise.
      **9/9 tests GREEN** (`test_raw_price_reconstruction.py`); regression
      `test_price_provenance_gate.py` 7/7 GREEN.
- [x] Runner calibration `run_raw_price_reconstruction.py` — hiệu chuẩn
      FOC (6 sự kiện), VEA (7 sự kiện), SMB (13 sự kiện): **100% bars on-grid, factors giải duy nhất**.
- [x] Runner kiểm toán FOC `run_foc_empirical_audit.py` dùng giá thô unadjusted: hoàn tất.
- [x] Runner kiểm toán VEA `run_vea_empirical_audit.py` dùng giá thô unadjusted: hoàn tất.
- [x] Runner kiểm toán SMB `run_smb_empirical_audit.py` dùng giá thô unadjusted: hoàn tất.
- [x] Chạy toàn bộ regression test suite: 22/22 passed.
- [x] **NQ-04 nhiệm vụ (b) — E14:** band mode OHLC (`lows`/`highs`) + ghi
      `gap_deltas`; `price_provenance_gate --reconstructed` (V1.1);
      `cash_event_registry.py` làm nguồn event duy nhất. **27/27 tests GREEN**;
      FOC **exit 0**, VEA **exit 0**, SMB **exit 1** (2023-02-20).
- [ ] **SMB provenance re-check 2023-02-20** (biên phiên chỉ rộng 49,4 VND,
      tham chiếu lệch 300 VND) — cấm nới band, phải kiểm lại cash/ex-date.
      → **ĐÓNG 29/10/2026 (NQ-07):** VSDC `VSDC/SMB/20230220/CASH_1K` xác nhận
      ngày + 1.000 đ/cp đúng; phiên gap-up 2 bước giá ⇒ nghiệm thu bằng tầng
      trần/sàn ±7% (allow-list đúng 1 event, nhãn `limit-band`).
- [ ] **Pha 1 re-run chính thức** trên FOC đã certified (chờ hội đồng phê duyệt
      điều kiện tiên quyết).
      → **HOÀN TẤT 29/10/2026:** runner audit FOC dùng registry + fail-closed
      certification; kết quả tái lập y hệt E11; tiến độ **1/5**.
- [ ] Pha 2 VEA + Pha 3 SMB re-run chính thức trên chuỗi certified (2/5, 3/5).
      → **HOÀN TẤT 29/10/2026 (E16):** vá cùng 3 lỗ hổng qua module SSOT
      `audit_provenance.py`; VEA Alpha +2,29%/năm (MDD −24,74%), SMB +2,91%/năm
      (MDD −15,08%); **tiến độ 3/5**, nhãn NON-DECISIONAL.
- [x] **Linter `[WHY]` report-only (NQ-04):** `backend/src/tools/why_contract.py` +
      `ptck.py why-audit`; quét thật `backend/src` = **1.047 vi phạm** (constant 581,
      literal-threshold 466), 0 parse-error, exit 0 (chỉ `--strict` trả 1).
- [x] **Pha 4 — PAT (UPCoM, siêu cổ tức chu kỳ):** registry 9 event (tổng
      63.055 đ/cp từ VCI IQ), lưới 100 VND, tầng 3 xác nhận 4/4 event gap;
      `run_pat_empirical_audit.py` EXIT 0, Alpha −7,57%/năm, MDD −57,88%.
- [x] **Pha 5 — DSN (HOSE, cash cow):** registry 10 event (20.000 đ/cp), lưới
      50 VND (đo đối chứng: lưới 100 gãy 64/115 bar), tầng 3 xác nhận event gap
      2026-05-18 (implied 1.600 khớp tuyệt đối); `run_dsn_empirical_audit.py`
      EXIT 0, Alpha −3,54%/năm, MDD −30,23%.
- [x] **Tầng 3 `cross_vendor_agreement.py`** (tỷ số KBS/DB, trung vị, cửa sổ 10
      bar, `conflict` ⇒ fail-closed): 6/6 test GREEN; tích hợp vào cổng với
      chính sách 2 mức (`limit-band` thiếu bằng chứng ⇒ chặn).
- [x] **CLI `dividend-audit` về SSOT:** xóa 3 danh sách cổ tức inline, nhãn sàn
      + lưới tick + danh sách mã đọc từ registry; FOC tái lập đúng
      −4,98%/năm & 1.166.176.990 đ; test khoá không tái xuất hiện bản sao.
- [x] **Backfill lỗ hổng phủ DB** (phát hiện ở E17, KHÔNG chặn 5/5): DSN thiếu
      39 bar trong 2026-04..07, PAT có 8 bar invalid 2026-06-04..06-18. Không
      được `fillna`; phải crawl lại từ nguồn và đi qua cổng provenance.
      → **ĐÓNG 30/10/2026 (E19):** DSN backfill `backfill-history` +39 dòng
      (46→80 bar hợp lệ, khớp tuyệt đối 80 bar vintage KBS), tầng 3 **10/10
      agree** (2026-05-18: +4,342% → +0,003%); PAT **tái phân loại** — 17/22
      bar VCI là padding volume-0 cho ngày đình chỉ, KBS chỉ có 5 bar thật
      ⇒ đã revert, giữ 8 zero-row bị `VALID_ROW_SQL` loại. Số 5/5 không trôi.

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
