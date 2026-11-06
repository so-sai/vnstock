# Regression Armor — phán quyết thiết kế 4 chốt chặn hồi quy (NQ-17, 05/11/2026) — **NON-DECISIONAL**

> Số đo trong tài liệu này là dữ liệu vận hành (không phải kết quả nghiên cứu) và **không**
> dùng để tối ưu tham số. Mọi kết luận về hành vi thị trường vẫn NON-DECISIONAL.

## 0. Phán quyết ngắn

**DUYỆT 4 chốt chặn, nhưng phải sửa 3 điểm thiết kế** — vì đo trên mã nguồn và CSDL thật cho
thấy 2/4 chốt **đã tồn tại ở phía Rust**, và 1/4 chốt **không thể thoả ở dạng viết ban đầu**
(dữ liệu 2020 lệch tập ngày có lý do lịch sử, không phải lỗi).

| # | Chốt chặn | Trạng thái đo được (05/11/2026) | Phán quyết |
|---|---|---|---|
> **CẬP NHẬT 06/11/2026 (NQ-18):** bảng dưới là **ảnh chụp 05/11/2026**, giữ nguyên để truy vết
> quyết định. Trạng thái thực tế hiện nay: **#1, #2, #3, #5 DONE** (`9c36690`, `4104639`, `6e013d8`
> + `20295b5`, `c6fa870`); **#4 vẫn OPEN** (chờ `src/main.rs` sạch). Chi tiết: `muc27_acceptance_2026_11_06.md`
> và `muc29_calendar_ssot_2026_11_06.md`.

| # | Chốt chặn | Trạng thái đo được (05/11/2026) | Phán quyết |
|---|---|---|---|
| 1 | Temporal Gap Guard (cấm tính biến động qua ngày khuyết) | Rust **đã có** (`missing_trading_days_between`, dùng ở 1235/1315/1596/1634, 9 unit test); Python **chưa có** → đã bổ sung | ✅ **DONE** (Mục 26, `9c36690`) |
| 2 | Gap-aware Sync (sync phải biết bù ngày) | **Chưa có** ở Python: `daily-update --date X` chỉ cào X, không quét `MAX(date) → X` | ✅ **DONE** — chọn phương án (B): báo động, KHÔNG tự bù (`4104639`) |
| 3 | Date-set Asymmetry Guard | Không thể "khớp 100%" toàn lịch sử — xem §2 | ✅ **DONE** — scoped window (`6e013d8`) + **chiều ngược** (`20295b5`) |
| 4 | Linter AST Rust | Guard đã có + đã có unit test; `src/main.rs` đang **dirty** (agent khác) | ⧗ **OPEN** (Mục 28 #5) — lint **call-site**, không viết lại guard |

## 1. Vì sao 2.400 test mà vẫn lọt lỗi vận hành — bằng chứng cụ thể

### 1.1 Test chỉ chạy trên dữ liệu "đẹp" (happy-path), không có ca đối kháng

Toàn bộ 154 test regression hiện tại (16 file) chạy trên DB tạm do test tự dựng hoặc trên
CSDL thật **ở trạng thái đã lành**. Không có test nào mô phỏng:

- phiên liền trước **khuyết** (04/11/2026),
- cron **miss** rồi hôm sau chạy tiếp,
- dữ liệu bị **ghi lại** sau khi đã chốt sổ.

### 1.2 Bằng chứng sống của lỗ hổng "khuyết ngày" (04/11/2026)

| Thời điểm | Sự kiện đo được |
|---|---|
| 04/11/2026 | **EOD không chạy** — không có bar, không có equity, không có ledger row 04/11 |
| 05/11 15:42 | `eod-run --date 2026-11-05` → ledger 05/11 = **SUCCESS**, equity 05/11 được ghi **trên nền khuyết 04/11** |
| 05/11 17:31 | breadth 02/11 và 03/11 bị **ghi lại**: `−47,73 → −48,578` và `+4,648 → +5,276` (active 839 → 834), `created_at` mới `2026-11-05T17:31:24` |
| 05/11 17:33 | catch-up bù 04/11 = **CATCHUP_FULL**, rồi **ghi lại** equity 05/11 (`created_at 17:33:12`) |

⇒ Trong 2 giờ, hệ thống vừa **tính biến động trên nền khuyết**, vừa **sửa lại số đã chốt**,
và **không có cảnh báo nào**. Đây chính là cái "CSDL tích tụ lỗi ở điểm nối" mà Hội đồng nêu.

### 1.3 Lỗi Python-vs-Rust ở điểm nối (The Seam) — đã kiểm chứng

| Năng lực | Rust (`src/main.rs`) | Python |
|---|---|---|
| Lịch phiên (T2–T6, lễ VN) | `is_vn_trading_day` + `VN_HOLIDAYS` | `daily_updater.VN_HOLIDAYS` + `is_trading_day` |
| Phiên liền trước | `expected_prev_trading_day` | **không có** (đến 05/11/2026) |
| Danh sách phiên khuyết | `find_missing_past_trading_days` | **không có** (đến 05/11/2026) |
| Guard chặn biến động đa phiên | có (4 call-site + 9 unit test) | **không có** (đến 05/11/2026) |

Rust đã đúng. Python mới là bên thiếu ⇒ lỗi chỉ hiện ở nơi hai bên gặp nhau: dữ liệu do
Python nạp, nhưng số liệu publish/kiểm tra lại do Rust đọc.

## 2. Vì sao "TEST 3: khớp 100%" không thể thực thi nguyên văn

Đo trên `screener_cache.db` (05/11/2026):

| Tập | Số ngày | Khoảng | Ghi chú |
|---|---|---|---|
| `daily_ohlcv` (chỉ ngày có ≥ 100 mã hợp lệ) | **1.666** | 2020-03-06 → 2026-11-05 | trước 03/2020 dữ liệu quá thưa |
| `market_wide_breadth_daily` | **1.708** | 2020-01-02 → 2026-11-05 | backfill xa hơn OHLCV |
| `regime_history` | **1.479** | 2020-12-01 → 2026-11-05 | regime engine ra đời sau |
| `paper_equity_curve` | **80** | 2026-06-15 → 2026-11-05 | paper trading ra đời muộn hơn |

- `OHLCV \ breadth` = **0** ngày (tốt: không phiên nào có bar mà thiếu breadth).
- `breadth \ regime` = **229** ngày (2020-01-02 … 2020-11-30) — **hợp lệ vì regime chưa tồn tại**.
- 40 phiên gần nhất: **khớp 100%** cả 3 tập.

⇒ Invariant đúng phải là: **"không có LỖ (hole) bên trong cửa sổ hợp đồng"**, ví dụ
`sessions >= CONTRACT_EPOCH` (mốc cả 3 bảng cùng tồn tại) và/hoặc **N phiên gần nhất**.
Viết "khớp 100% toàn lịch sử" sẽ đỏ vĩnh viễn và bị tắt — đúng thứ cần tránh.

## 3. Chốt chặn #1 đã hoàn tất (Mục 26, commit `9c36690`)

- Module mới `src/database/session_continuity.py` (hàm thuần, không I/O):
  `is_session_day`, `previous_session`, `sessions_between`, `find_missing_sessions`,
  `assert_previous_session_present`.
- `eod_runner`: `EODSessionGapError` + `_assert_previous_session_accounted`, gọi **sau catch-up,
  trước `PaperTradingEngine`**; quyết định bằng `DISTINCT date` của `daily_ohlcv` (cửa sổ 40 ngày),
  không tin vào lịch suy diễn.
- `ptck.py`: `EODSessionGapError` → **exit 4** (phân biệt với 3 = phiên hiện tại 0 nến).
- 9 test, gồm ca chống-vá-quá-tay (đủ 04/11 ⇒ chạy bình thường), phantom holiday 01/09
  không bị báo khuyết, và test sống trên CSDL thật. **RED 2 → GREEN 9/9; regression 154/154.**

## 4. Hai chốt còn lại — thiết kế đã sửa, chờ Hội đồng

> **CẬP NHẬT 06/11/2026 (NQ-18):** mục này đã **được Hội đồng chốt và thực thi**. §4.1 chốt theo
> **phương án (B)**: `daily-update` chỉ báo động, `eod-run` dừng exit 4, người vận hành chạy
> `daily-update --date YYYY-MM-DD`; **cấm** tự bù mạng (`4104639`). §4.2 làm đúng phạm vi nêu ở đây
> và bổ sung **chiều ngược** (`6e013d8`, `20295b5`). §4.3 **vẫn OPEN** (Mục 28 #5).

### 4.1 Chốt #2 — Gap-aware Sync (Mục 27)

Hiện trạng: `daily-update --date X` chỉ cào X. Nếu cron miss 04/11 thì 05/11 vẫn chạy, và chỉ
khi có người gọi `daily-update --date 2026-11-04` thì dữ liệu mới về (đó là điều đã xảy ra lúc
17:31 ngày 05/11).

Thiết kế **đề xuất** (chưa làm vì đụng đường mạng + giới hạn 30 ngày của Hội đồng):
1. Trước khi cào X, tính `find_missing_sessions(present, contract_epoch, X)`.
2. Với mỗi phiên khuyết **trong hạn** (≤ 30 ngày lịch): gọi `backfill_market_history` tuần tự.
3. Phiên khuyết **ngoài hạn** (như 10 phiên 18/09–02/10 của Mục 22): **không** tự bù, chỉ báo.
4. Ghi `session_gaps` vào report + audit; nếu sau khi bù vẫn khuyết ⇒ vẫn chạy nhưng
   `is_degraded = 1` (kế toán non-blocking theo NQ-15), còn cổng tín hiệu fail-closed.

Cần Hội đồng chốt: có cho phép **tự động** gọi backfill mạng trong `daily-update` (tăng thời
gian chạy, phụ thuộc nguồn) hay chỉ **báo động + chặn EOD** như Mục 26 đã làm.

### 4.2 Chốt #3 — Date-set Asymmetry (scoped)

- `CONTRACT_EPOCH = 2026-06-15` (ngày paper trading bắt đầu) cho cặp `equity/ledger`.
- Với `ohlcv/breadth/regime`: cửa sổ **60 phiên gần nhất**, so **tập ngày**, bỏ qua
  `NON_TRADING_SESSIONS`.
- Test phải đỏ khi có **hole** (đúng ca 04/11), xanh khi chỉ lệch do "ra đời muộn".

### 4.3 Chốt #4 — Lint call-site (không viết lại guard)

Rust đã có guard + unit test. Việc còn thiếu là bảo đảm **mọi** chỗ đọc 2 bar liên tiếp đều
đi qua guard. Cách làm đề xuất: test Python quét `src/main.rs`, tìm các truy vấn
`ORDER BY date DESC LIMIT 2` và khẳng định trong **cùng hàm** có gọi
`missing_trading_days_between`. Không dùng regex trên chuỗi SQL đơn lẻ (dễ "vá mồm").

## 5. Chốt #5 (mới, do số đo sinh ra) — Restatement Audit

Sự kiện 05/11 17:31 (breadth 02/11 và 03/11 bị ghi lại, `−47,73 → −48,578`, `+4,648 → +5,276`)
cho thấy: **số của phiên đã chốt có thể thay đổi mà không có bản ghi "ai/căn cứ nào"**.
Đề xuất: cấm UPDATE `market_wide_breadth_daily`/`regime_history` của phiên **đã đóng** trừ khi
có dòng audit `RESTATED` nêu lý do (thường là do panel thay đổi). Đây là chốt bảo vệ
**tính tái lập** của mọi báo cáo đã công bố.
