# BACKLOG NỢ KỸ THUẬT (Technical Debt) — chu kỳ dọn dẹp kế tiếp

Sổ này là nơi DUY NHẤT liệt kê các hạng mục nợ kỹ thuật đã được phán quyết là **HOÃN** (không
phải quên). Mỗi mục phải có: WHAT / WHY (kèm số đo) / WHERE / DONE-WHEN / RISK.
Nguyên tắc: không tự thực thi khi chưa có GO; mọi mục phải fail-closed khi triển khai.

---

## 1. ✅ DONE — Migration `regime_history.is_degraded` (+ ghi cờ tại writer)

- **WHAT:** thêm cột `is_degraded` vào `regime_history`; `engine/regime_engine.py::backfill_regime_history`
  ghi cờ suy giảm của **chính phiên đó**.
- **WHY:** `regime_history.breadth_pct/breadth_velocity` được ghi từ board của phiên; nếu phiên đó
  thiếu mã thì giá trị vẫn được ghi **không kèm cờ**. Hiện đã bù ở **tầng đọc** (join sang
  `market_wide_breadth_daily`) nên không còn đường *âm thầm* tiêu thụ dữ liệu kém phủ.
- **WHERE:** `backend/src/database/db_core.py` (schema), `backend/src/engine/regime_engine.py`,
  `backend/src/services/breadth_service.py` (bỏ join khi có cột thật).
- **DONE-WHEN:** cột tồn tại, writer ghi cờ, cờ khớp với bảng breadth trên ≥ 30 phiên liên tiếp,
  và test hồi quy xanh.
- **RISK:** migration schema trên DB đang được cron/live dùng → phải làm ngoài cửa sổ EOD, có
  backup, và theo kiểu additive (ADD COLUMN) để không lock lâu.
- **PHÁN QUYẾT:** ✅ **DONE 2026-10-16** (GO của user, ưu tiên 3 sau Mục 8 — user gọi là "Mục 5",
  cùng nội dung `regime_history.is_degraded`). Bằng chứng:
  - **Migration** `backend/scripts/migrate_regime_history_is_degraded.py` — ADDITIVE + IDEMPOTENT, cột
    `is_degraded INTEGER` **KHÔNG default 0**; giá trị **suy ra từ nguồn đo được** (join ngày với
    `market_wide_breadth_daily.degraded`): **1.465/1.465 dòng khớp ngày**, **34 phiên degraded=1**,
    1.431 phiên =0, **0 dòng để NULL**; số dòng + khoảng ngày không đổi; chạy lại ⇒ 0 dòng đổi.
  - **Helper tầng đọc** trong `src/database/series_quality.py`: `LIVE_REGIME_SQL`, `CODE_REGIME_DEGRADED`,
    `assert_regime_degraded_column()` (fail-closed khi thiếu cột), `breadth_degraded_flag()` (None khi
    không có dòng độ rộng ⇒ "không biết" ≠ 0).
  - **3 WRITER đều phơi cờ:** `regime_engine.backfill_regime_history`, `daily_updater` (EOD),
    `timeline_manager.log_regime_state`. Có test quét toàn cây: **writer nào ghi `regime_history` mà
    không set cờ ⇒ fail build** (ngoại lệ duy nhất: `backtest/hsr_kernel.py` chỉ COPY vào `:memory:`
    bằng `SELECT *`).
  - **14 điểm ĐỌC ở tầng tín hiệu đã lọc:** `engine/recovery_engine` (2) · `engine/regime_engine` (6 —
    seed EMA + breadth stability; riêng truy vấn *liệt kê NGÀY* để backfill được **miễn trừ có ghi lý
    do**, vì lọc ở đó sẽ khiến phiên degraded bị coi là "thiếu" và bị ghi lại vô hạn) ·
    `engine/capital_flow_forecasting_engine` · `engine/cross_market_flow_map` ·
    `governor/liquidity_recovery_index` (2) · `governor/shock_detector` (**hợp nhất về một nguồn**:
    bỏ tập ngày join riêng, dùng cờ) · `governor/uncertainty_layer` · `governor/unfreeze_gates_v2` ·
    `portfolio/portfolio_recommendation_engine` · `quant/vn20_quant_filter` ·
    `cao_readiness/gate_c_regime_stability` (2).
  - **ĐO DRIFT = 0:** verdict live hôm nay `2026-10-16 CRISIS 0.2790` khớp dòng đã lưu
    (`0.28`, `is_degraded=0`) ⇒ việc lọc không làm lệch kết luận hiện tại.
  - **Kiểm kê không rò rỉ âm thầm:** test quét **cả cây `backend/src`**; file nào đọc `regime_history`
    mà không lọc thì phải nằm trong `DISPLAY_TIER` **kèm lý do** (11 file: API/UI, services, timeline,
    backtest, research, sensors, alpha) — nếu không ⇒ **fail build**. Tổng mặt cắt đo được: **41 điểm
    đọc trong 24 file**.
  - **ĐÃ PHÊ CHUẨN (2026-10-19) — giữ hợp đồng 2 tầng, KHÔNG lọc cứng ở backtest:**
    - Tầng **LIVE/SIGNAL**: lọc cứng 100% (không ngoại lệ) — 14 điểm ở trên.
    - Tầng **BACKTEST/REPLAY**: mặc định **KHÔNG lọc** để bảo toàn tính tái lập của nghiên cứu lịch sử
      đã công bố; cung cấp tham số opt-in `exclude_degraded: bool = False` cho phân tích độ nhạy /
      stress-test chất lượng dữ liệu (`42ed3cc`): `trend_loop.load_regime`,
      `decompose_replay_by_regime.load_regimes`, kernel `hsr_kernel` (bản sao in-memory).
      Một helper duy nhất `series_quality.regime_reader_sql()` sinh vị từ cho cả 3 nơi.
    - Test canh (`39c075d`): `inspect.signature` xác nhận **mặc định `False` ở cả 3 reader** (không ai
      lật cờ âm thầm) + e2e trên DB tạm: mặc định thấy **3/3 phiên** (có 1 degraded), opt-in còn **2/3**.
  - Test: `backend/tests/test_regime_degraded_contract.py` **8/8 PASS**; fixture của
    `test_vn20_quant_filter.py` đã sửa theo schema thật + 2 ca mới (bỏ qua phiên degraded; nhãn NULL
    không được coi là sạch); `test_valuation_latest_price.py` cập nhật theo dữ liệu PNJ đã sửa EPS.

## 2. Hợp đồng 2 tầng `degraded` ở NGUỒN (board) — không phải ở bảng

- **WHAT:** gắn cờ suy giảm ngay tại nguồn sinh `breadth_pct` của board, để các consumer đọc
  verdict/JSON (`core/executive_report.py`, `core/market_snapshot.py`, `backtest/system_replay.py`,
  `alpha/*`, `api/routes/system.py`) nhận được cờ.
- **WHY:** các file này **không** đọc bảng `market_wide_breadth_daily`; chúng đọc verdict EOD ⇒
  hợp đồng 2 tầng hiện chỉ phủ được đường `regime_history` (qua `breadth_service`).
- **WHERE:** nguồn tính board (verdict details) + 5 consumer ở trên.
- **DONE-WHEN:** tầng hiển thị xuất `is_degraded`; tầng tín hiệu lọc cứng; có test cho cả hai.
- **RISK:** chạm nhiều consumer → phải chia 2 commit (hiển thị / tín hiệu), không đổi số liệu.

## 3. Ngưỡng Sentinel `n_symbols_today < 1000` đã lỗi thời (báo động giả)

- **WHAT:** thay ngưỡng cứng 1000 trong `engine/startup_reminder.py::danh_gia_trang_thai_du_lieu`
  bằng cổng động: đếm trên **dòng HỢP LỆ** (`VALID_ROW_SQL`), ngưỡng `rolling_median × min_value_ratio`
  (chuẩn của `panel_quality.assert_market_breadth`), có thể đọc median đã cache từ pipeline EOD để
  giữ độ trễ thấp; nếu chưa cache được thì fallback sàn an toàn **580** (dùng chung với guard cron).
- **WHY:** đo 2026-10-15: phiên 15/10 = **829 dòng hợp lệ**, 0 `is_stale`, 0 NULL, VNINDEX ✓ —
  vét toàn sàn (19 lát, cấu hình tốt nhất) chỉ thêm **+4 dòng** ⇒ 829 là trần thực tế. Nhưng
  Sentinel vẫn báo `[!] Chua du du lieu EOD hom nay` vì 829 < 1000. Median dòng hợp lệ ~865/phiên
  và `min_symbols=1000` fail **92/95** phiên ⇒ cảnh báo này sẽ **luôn sáng** ⇒ Alarm Fatigue.
- **WHERE:** `backend/src/engine/startup_reminder.py` (+ có thể tái dùng helper của `panel_quality`).
- **DONE-WHEN:** phiên đủ phủ ⇒ `EOD_READY`; phiên thiếu thật ⇒ `NOT_READY`; có test cho cả hai
  chiều (đủ/thiếu) trên DB giả lập.
- **RISK:** nới ngưỡng quá tay sẽ che mất phiên thiếu thật ⇒ phải giữ được nhánh FAIL và test nó.
- **PHÁN QUYẾT:** ✅ **DONE 2026-10-16** (GO của user, ưu tiên 3) — ngưỡng động đã vào code:
  `MIN_VALUE_RATIO = 0.5`, `FALLBACK_MIN_SYMBOLS = 580`, `nguong_toi_thieu(median_valid)` =
  `max(580, median20_phiên_trước × 0,5)`; hàm đánh giá nhận thêm tham số `median_valid_20`;
  số mã của phiên nay đếm trên **dòng HỢP LỆ** (`VALID_ROW_SQL` + `STOCK_ONLY_SQL`) thay vì
  `COUNT(DISTINCT symbol)` thô (đếm thô tính cả dòng cách ly **và chỉ số** — thêm một điểm sai).
  Baseline PIT-safe: median của **20 phiên TRƯỚC** phiên đang xét. Bằng chứng:
  `backend/tests/test_startup_sentinel_threshold.py` **7 ca** (827 và 870 mã qua cổng ✓; 300 mã vẫn
  `NOT_READY` ✓; median 1.500 ⇒ ngưỡng tự siết lên 750 và 700 mã bị bắt ✓; thiếu VNINDEX ⇒ NOT_READY;
  thiếu baseline ⇒ dùng sàn 580, **không** coi là đủ) + kiểm đầu-cuối `ptck.py stale status` →
  `[+] DU LIEU EOD HOM NAY DA SAN SANG` (trước đó luôn báo thiếu). Không có nhánh "cảnh báo rồi đi tiếp".

## 4. Pipeline dữ liệu CƠ BẢN chuẩn Point-in-Time (BCTC) — *hạng mục mới*

> **CẬP NHẬT 2026-10-15 (audit):** kho cơ bản **ĐÃ TỒN TẠI** — `backend/data/financial_facts.db`
> (209 MB, `financial_facts` 615.528 dòng / 791 mã / 55 kỳ, kèm `valuation_scores`, `health_ratios`,
> `forensic_scores`, `ingestion_log`) + crawler `cafef_crawler.py`, `vietstock_crawler.py`,
> writer `financial/valuation_engine.py:430`. **KHÔNG xây lại từ đầu.** Nguồn thô vci/kbs qua
> `vnstock.api.financial.Finance` chỉ trả **4 kỳ** và **không có ngày công bố** ⇒ không dùng làm connector.
> Chi tiết + số đo: `backend/src/research/financial_facts_audit.md`.
> Việc thật sự còn lại là **A–E** ở §6 của note đó: (A) bulk-update `effective_date` +
> `effective_date_basis` (100% dòng suy ra được, `STATUTORY_LAG` là cơ chế chính); (B) tính lại
> `valuation_scores` theo PIT; (C) bổ sung chuỗi **số cổ phiếu lưu hành**; (D) bổ sung **metric ngân hàng**
> (NPL/LLR/NIM/cho vay/tiền gửi — hiện KHÔNG có trong 47 metric, nên luận điểm chất lượng tài sản
> không kiểm được); (E) winsorize/audit tầng ratio (PE<2: 40 dòng, PB max 79,66, EV/EBITDA max 3.386).
>
> **CẬP NHẬT 2026-10-15 (A ✅, E chẩn đoán ✅):**
> - **A xong** (commit `1bce817`): 615.528/615.528 dòng đã có `effective_date` + `effective_date_basis`;
>   615.438 dòng dùng được hôm nay, 90 dòng kỳ 2026Q3 (`effective_date=2026-10-30`) **chưa được phép đọc**.
>   Đo được look-ahead cũ: P/B SHB phải là **0,801** tại mốc 01/08/2026 (chỉ biết Q1) nhưng tầng cũ
>   cho **0,702** ⇒ sai lệch tới **~18%** theo hướng làm cổ phiếu trông rẻ hơn.
> - **E chẩn đoán xong** → `financial_facts_ratio_audit.md`: **root cause không phải "40 ca bẩn"** mà là
>   lỗi kỳ hạn trong công thức PE — `valuation_engine.py:266-274` nhân EPS ×4 **cho mọi kỳ** trong khi
>   chuỗi EPS **hỗn hợp quý/luỹ kế** (Q2+Q4 chiếm 65% số ca). Phát hiện thêm: writer dùng
>   `shares_now` (số cổ phiếu HIỆN TẠI) cho **mọi kỳ** ⇒ `SHARES_NOT_PIT`, cùng họ với look-ahead PIT;
>   và `EBITDA` **không tồn tại như metric** ⇒ mẫu số EV/EBITDA không truy vết được.
> - Vì vậy **C trở thành điều kiện tiên quyết của B** (không phải "nice to have"), và **E chỉ gắn cờ,
>   không sửa số** — việc sửa công thức thuộc B.

> **CẬP NHẬT 2026-10-19/20 (A–E ✅ ĐÓNG SỔ; consumer PIT ✅):**
> - **A ✅** (`1bce817`): `financial_facts` 615.528 dòng có `effective_date`/`_basis`.
> - **E ✅** (`3f02a93`/`2515c96` + `c9c5f7e` + tầng điểm): `valuation_scores` sạch ghost (**56.055** dòng),
>   winsorize `[p2,p98]` chỉ ở tầng z/percentile, `n_min=5`, `INSUFFICIENT_HISTORY` (13.348 dòng),
>   `EXTREME_ROE_ANOMALY`, EV/EBITDA `is_clean_metric=1` = **0**.
> - **B ✅**: recompute PIT sau khi vá 4 raw query trong `valuation_engine.py` (`c1df061`).
> - **C ✅ — `financial_facts_annual` (commit `d6e526e`)**: **28.730/28.730** dòng có
>   `effective_date` + `effective_date_basis='STATUTORY_LAG'`; công thức `31/12 + 90 ngày` **tái dùng
>   `LAG_DAYS` của migration quý** ⇒ năm nhuận ra **30/03** (7.514 dòng: FY2011/2015/2019/2023), năm
>   thường 31/03. Kiểm chứng độc lập: 0 NULL · 0 sai định dạng · 0 dòng `effective_date ≤ period_end` ·
>   idempotent. Backup `financial_facts.db.bak_annual_effdate_20261019_165032`.
> - **Consumer PIT ✅ (commit `e1675a4`)**: `vn20_quant_filter._annual_verification` bỏ hằng số tay
>   `_VERIFY_YEAR = 2025`, chuyển sang `effective_date <= target_date`, chỉ xét **năm mới nhất đã công
>   bố** và aggregate cả năm (MISMATCH thắng — **không `LIMIT 1`**: lấy đại 1 metric có thể nuốt veto).
>   Đo trên kho thật: luật cũ 79 mã VERIFIED / luật mới 79 mã ⇒ **0 flip**, look-ahead biến mất.
> - **Wire `target_date` (2026-10-20)**: `run_vn20_filter(target_date=)` → `_latest_period(asof=)` →
>   `periods` → `tier1_buffett_quality` → annual gate; `_liquid_universe(asof=)`; CLI `ptck.py vn20 --date`.
>   Bằng chứng trên kho thật: `asof=2022-06-30 → periods_last=2022Q1`, `asof=2024-08-13 → 2024Q1`
>   (Q2/2024 công bố 14/08), live → `2026Q3`.

- **WHAT:** ~~nạp báo cáo tài chính theo quý kèm ngày công bố~~ → **hoàn thiện provenance PIT cho kho đã có**
  (A–E ở trên), rồi mới tính tới việc dùng nó làm bộ lọc.
- **WHY:** (câu gốc 2026-10-15 — **đã tự sửa**): *"store có 43 bảng và không có bảng BCTC"* chỉ đúng với
  `backend/data/screener_cache.db`. Kho cơ bản nằm ở `financial_facts.db` (xem audit ở trên), và nó
  **đã kiểm chứng được luận điểm "SHB rẻ"**: P/B = 0,663 tại giá 10.400 (BVPS 15.677,66 kỳ 2026Q2).
  Cái **KHÔNG kiểm được** là (a) khối chỉ tiêu ngân hàng (NPL/LLR/NIM/cho vay/tiền gửi — không có trong
  47 metric), và (b) **thời điểm** số liệu trở nên công khai (`reported_at` rỗng 99,999% ⇒ chưa có PIT).
- **NGUỒN KHẢ THI (đã xác minh có trong repo, chưa gọi):**
  `backend/libs/vnstock/vnstock/api/financial.py` → `Finance(source="vci"|"kbs", symbol=S,
  period="quarter"|"annual")` với `.balance_sheet()`, `.income_statement()`, `.cash_flow()`, `.ratio()`.
- **YÊU CẦU TỐI THIỂU (fail-closed):**
  1. Bảng mới `financials_pit(symbol, period_end, publication_date, source, fetched_at, ...)`.
  2. **Bắt buộc** có `publication_date`; nếu nguồn không trả ngày công bố thì dùng **quy tắc trễ
     thận trọng** (vd quý Q chỉ hiển thị sau +45 ngày) và **ghi rõ đây là giả định**, không phải
     dữ kiện.
  3. Cổng: mọi phép tính định giá phải đi qua `panel_quality`-style assert; thiếu ⇒ DỪNG, cấm nội suy.
  4. Cấm tuyệt đối lấy số từ ảnh chụp app/website làm nguồn.
- **DONE-WHEN:** ≥ 3 mã ngân hàng (SHB/VCB/ACB) có ≥ 8 quý dữ liệu, P/B tính lại từ store khớp
  ±5% với một nguồn độc lập thứ hai, và mọi truy vấn định giá đều trả kèm `publication_date`.
- **RISK:** (a) nguồn có thể trả số ĐÃ HIỆU CHỈNH (restated) ⇒ phải lưu cả `as_reported` nếu có;
  (b) rate-limit khi quét toàn sàn ⇒ chạy theo lát như backfill; (c) đây là đường ống dữ liệu MỚI,
  không được trộn vào chu kỳ dọn dẹp — cần lượt riêng có pre-registration.
- **PHÁN QUYẾT:** ĐƯỢC CHẤP THUẬN vào backlog (2026-10-15). Chưa thực thi.

> **CẬP NHẬT 2026-10-16 — A ✅ · C ✅ · B ✅ · E ✅ · D 🟡 (5 mã):**
> - **A** (`1bce817`, chạy lại `migrate_financial_facts_effective_date.py --apply`): **617.699/617.699**
>   dòng có `effective_date`, 0 ngày sai định dạng, basis 100% `STATUTORY_LAG` (bù 2.715 dòng SHB +
>   7.287 dòng mã mới do ingestion thêm sau).
> - **C** (`migrate_shares_history.py`): `shares_history` **lưu CẢ HAI basis** (PK `symbol,period,basis`)
>   — `CHARTER_PIT` 22.009 dòng/71 kỳ/777 mã và `TCBS_REPORTED` 71 dòng/1 mã; cột `divergence_pct`
>   làm canary pha loãng. Phát hiện: hai nguồn lệch **+0,0% (2008Q4) → +1.234% (2010Q1–Q3) → +16,3%
>   (2026Q2)** ⇒ **cấm nối chuỗi**; guard 25% + cờ `SHARES_BASIS_UNEXPLAINED`.
> - **B** (`a72114d`, `e2bb73d`, `542dd35`, `d8d2ed4`): bỏ fallback giá tương lai (**8.601 cặp** mà bản
>   cũ SẼ gán giá tương lai > con số 636 đo sơ bộ 13,5×), bỏ ×4 mù quáng (hiệu chuẩn chu kỳ EPS tự
>   động: QUARTER/TTM/YTD/UNKNOWN/AMBIGUOUS), cờ Hạng mục E ghi vào chính hàng điểm, **gate PIT theo
>   `effective_date`**. Kết quả: **35.784 dòng sạch**, PE sạch 4.455, **PE<2 hợp lệ = 0**, median PE
>   9,48; 2.719 dòng cũ gắn `LEGACY_PRE_PIT`, 19 dòng kỳ tương lai gắn `PIT_FUTURE_PERIOD`.
> - **E** ✅ gắn cờ (không sửa số): `EXTREME_LOW_PE`, `NEGATIVE_EARNINGS`, `DISTRESSED_EQUITY`,
>   `NEAR_ZERO_DENOMINATOR`, `UNTRACEABLE_DENOMINATOR`, `BASIS_MISMATCH`, `EPS_LEVEL_MISMATCH` (thông
>   tin), `EPS_PERIODICITY_UNKNOWN`/`AMBIGUOUS` (chặn) + `FLAG_SCOPE` theo từng ratio.
> - **D** 🟡: metric ngân hàng **đã có** nhưng mới cho 5 mã nguồn tcbs (`BANK_NPL_RATIO`, `BANK_LLR`,
>   `BANK_NIM`, `BANK_LDR`, `BANK_CREDIT_GROWTH`, `NII`, `NPL_RATIO`).
> - **CÒN LẠI (mới):** **24.641 dòng mang `EPS_PERIODICITY_UNKNOWN`** — đo được là do mã không có "đa
>   số kỳ khớp cùng quy ước", **không** phải thiếu phủ (0/344 mã thiếu `NET_INCOME`) và **không** phải
>   trộn nguồn (0/622.992 ô có >1 nguồn). Hai đòn bẩy thật: `NET_PROFIT_PARENT` + **số cổ phiếu bình
>   quân gia quyền** — **0/66 metric** trong kho.

## 5. Lớp lỗi fail-open `.get(khoá, giá_trị_lành)` trên dữ liệu thị trường

- **WHAT:** phân loại và chuyển đổi mọi chỗ dùng default "lành" cho dữ liệu thị trường sang
  `None` / raise fail-closed, kèm khai báo suy giảm (`*_MISSING`) ở tầng hiển thị.
- **WHY (số đo 2026-10-15, `scan_timeseries_hazards.py --top 1000`):** **425 chỗ** (Mẫu hình 2).
  Phân bố: `engine/` 105 · `services/` 96 · `core/` 82 · `portfolio/` 28 · `backtest/` 22 ·
  `api/` 20 · `governor/` 16 · `telemetry/` 10 · `tools/` 7 · `alpha/` 6 · `financial/` 5 · còn lại 28.
  Theo định nghĩa **"đường quyết định" = `engine/` + `services/` + `portfolio/` + `governor/` +
  `alpha/`** ⇒ **251 chỗ**. (Con số 285 nêu trước đó dùng một định nghĩa rộng hơn, chưa cố định —
  **phải chốt định nghĩa trước khi thi hành**, nếu không thì "DONE-WHEN" không đo được.)
  Hiệu quả thật của lớp lỗi này đã kiểm chứng ở 4 ca: Ca 1 (`regime.get("RANGING")` ⇒ mở đường
  breakout giữa CRISIS, lật OOS 2026 `+0,8956` → `−0,7839`), Ca 2 (`breadth` thiếu ⇒ 0% ⇒ tín
  hiệu giả), Ca 3 (`vni_info` thiếu ⇒ `vol_ratio = 0` ⇒ nhãn `LOW_VOLUME_REGIME`), Ca 6 (`ratio = 0`
  + `TRUNG_TINH` ⇒ lưới bảo vệ im lặng khi mất dữ liệu).
- **WHERE:** theo module, ưu tiên nơi có tiền sử nhiễm và nơi ra quyết định/ra lệnh hạ tỷ trọng.
- **DONE-WHEN:** mỗi chỗ trên đường quyết định được phân loại có ghi lý do (đo được · không đo
  được ⇒ `None` · raise), mỗi module có test chống vá quá tay, và scanner chạy như **thước đo
  trước/sau** (log-only, KHÔNG phải cổng cho tới khi số chỗ còn lại đạt ngưỡng chốt trước).
- **RISK:** sửa hàng loạt dễ chặn oan nhánh hợp lệ ⇒ phải theo từng file, mỗi lát 1 commit, có
  ca "dữ liệu HỢP LỆ đi qua nguyên vẹn"; nguy cơ thứ hai là làm số đẹp bằng cách đổi default từ
  lành sang một default khác cũng không đo được (`None` giả) — cấm.
- **PHÁN QUYẾT:** mở 2026-10-15, chờ GO theo lát.

## 6. Truy vấn `daily_ohlcv` trần thiếu `VALID_ROW_SQL`

- **WHAT:** bịt các đường đọc trực tiếp `daily_ohlcv` đi vòng qua lưới vị từ; chuyển sang hàm
  DAO dùng chung (đọc "sạch") thay vì mỗi nơi tự viết `WHERE`.
- **WHY (số đo 2026-10-15, cùng scanner):** **156 chỗ** (Mẫu hình 3) — trước bản vá Ca 6 là **158**;
  hai chỗ của `fast_exit_guard.py` đã bịt ở commit `7545a2c` ⇒ hiệu quả đo được, không suy diễn.
  Phân bố: `engine/` 60 · `backtest/` 15 · `database/` 14 · `core/` 11 · `services/` 10 ·
  `governor/` 9 · `telemetry/` 7 · `portfolio/` 5 · `financial/` 5 · `research/` 4 · `calibration/` 4 ·
  `scripts/` 3. Rủi ro định lượng: store có **41.998 dòng `is_stale = 1`**; trên 3 trụ cột, việc
  nuốt dòng cách ly làm `AVG(close)` lệch **19–28%** và **LẬT HƯỚNG** giá (phiên 2026-06-15/16/18),
  đủ để ra lệnh thoát vị thế oan (`fast_exit_guard`) hoặc chấm điểm sai.
  File có mật độ cao đã xác minh: `engine/entity_resilience_layer.py` (134, 236, 265, 302),
  `engine/flow_decay_engine.py` (234, 310, 409), `calibration/calibrator.py` (131, 148).
- **WHERE:** toàn bộ Mẫu hình 3; lưu ý 14 chỗ trong `database/` (`data_validity`, quarantine)
  nhiều khả năng là **cố ý** (chúng phải nhìn thấy dòng bẩn để cách ly) ⇒ phân loại trước, không
  sửa máy móc.
- **DONE-WHEN:** mỗi chỗ được gắn nhãn `PIT-live` (thêm `is_stale = 0 AND VALID_ROW_SQL`) hoặc
  `panel` (chỉ `VALID_ROW_SQL`) hoặc `CỐ Ý` (có lý do ghi tại chỗ); scanner Mẫu hình 3 giảm và
  không tăng lại; có test cho một file đường quyết định đại diện.
- **RISK:** thêm `is_stale = 0` vào truy vấn **panel lịch sử** sẽ rụng ~40% mẫu (đúng lỗi đã
  cấm trong AGENTS.md) ⇒ phải phân biệt chế độ đọc TRƯỚC khi sửa; sửa sai chế độ còn nguy hiểm
  hơn không sửa.
- **PHÁN QUYẾT:** mở 2026-10-15, chờ GO theo lát (mỗi lát 1 file/module + commit riêng).

## 7. Đồng bộ hạ tầng công cụ: sàn Python · phạm vi hook · danh tính metadata

- **WHAT:** (a) sàn Python khai báo phải khớp cú pháp thật; (b) hook pre-commit phải lint cả
  `backend/scripts/`; (c) khối `[project]` trong `backend/pyproject.toml` phải mang danh tính
  dự án thật, không phải bản sao upstream.
- **WHY (số đo):**
  (a) `requires-python` ghi `>=3.10` trong khi cây nguồn dùng **332 chỗ** cú pháp PEP 758
  (`except A, B:` không ngoặc — hợp lệ từ 3.14). *Con số 93 nêu trước đó là grep thô; đã đính
  chính bằng quét token (bỏ chuỗi + chú thích) trong `tests/test_python_floor_matches_syntax.py`.*
  Hệ quả: `pip` cho cài trên 3.10–3.13 rồi `SyntaxError` ngay khi import — không test nào bắt
  được vì mọi môi trường ở đây đều 3.14.
  (b) Hook chỉ lint `backend/src` ⇒ **3 lỗi ruff** trong `scripts/repair_capital_displacement_vnindex.py`
  lọt qua commit `aa1ff1d`, phải sửa ở commit riêng `b56b5a7` — cổng có lỗ hổng, không phải lỗi lẻ.
  (c) Khối `[project]` vẫn là metadata upstream **vnstock v3.5.0** (`name`, `authors`, `license`,
  deps) trong khi bản thật đang dùng là `backend/libs/vnstock` **v4.0.5** (gitignored) ⇒ sai danh
  tính dự án và dễ gây hiểu nhầm khi ai đó đọc cấu hình.
- **WHERE:** `backend/pyproject.toml`, `backend/scripts/pre_commit_hook.py`,
  `backend/tests/test_python_floor_matches_syntax.py`, `backend/crates/ptck_core/pyproject.toml`.
- **DONE-WHEN:**
  - (a) **XONG** — commit `0db323f`: sàn `>=3.14` cho khai báo tracked + bỏ classifiers 3.10–3.13
    + cổng `test_python_floor_matches_syntax.py` (5 test, có ca chống cổng rỗng và ca mô phỏng
    điều kiện CI). Ghi chú: `backend/libs/vnstock/pyproject.toml` đã sửa trên máy nhưng **không
    vào git** (gitignored) ⇒ tái vendor sẽ ghi đè.
  - (b) `backend/scripts/` nằm trong phạm vi ruff check/format của hook, và **chứng minh hook
    chặn thật** bằng một commit cố tình vi phạm bị từ chối.
  - (c) khối `[project]` trùng được xoá hoặc đổi thành `name = "ptck"` (kèm quyết định:
    `backend/libs/vnstock` có trở thành nguồn metadata duy nhất không).
- **RISK:** (b) mở rộng phạm vi lint sẽ **ĐỎ hàng loạt file cũ ngay lần commit đầu** ⇒ phải đo
  trước số file đang vướng; nếu lớn thì chia 2 bước (chặn file mới/đang sửa trước, dọn nợ cũ sau),
  nếu không hook sẽ bị gỡ bỏ và mất luôn cổng. (c) đổi `name` có thể ảnh hưởng script cài đặt /
  build ⇒ phải grep nơi đọc tên gói trước khi đổi.
- **PHÁN QUYẾT:** mở 2026-10-15; **(a) đã thực thi** (`0db323f`); (b), (c) chờ GO.

## 8. ✅ RESOLVED (LABELED_SYNTHETIC) — Provenance cho `volume_profile` / `active_demand` (tài sản vi cấu trúc "ngủ đông")

- **WHAT:** bổ sung cột `source` (+ `fetched_at`, `window_start/window_end`, `bin_semantics`) cho hai
  bảng `volume_profile` và `active_demand`; nếu không truy được nguồn ⇒ gắn cờ `PROVENANCE_UNKNOWN`
  và **cấm** dùng chúng trong bất kỳ cổng/điểm nào (hiển thị thì được, kèm cờ).
- **WHY (số đo 2026-10-16):** phát hiện khi fact-check bản "4 hố đen dữ liệu" — kho **có**
  `volume_profile` **54.765 dòng** (cột `poc`, `vah`, `val`, `poc_volume`, `value_area_volume`,
  `bin_size`, và **`hvns` = phân bổ khối lượng theo từng mức giá**) và `active_demand` **2.835 dòng**
  (`signal_type`, `support_level`, `volume_ratio`, `close_position`, `strength`). Phân bổ KL theo
  mức giá **không thể suy ra từ OHLCV EOD** ⇒ tồn tại một nguồn intraday nào đó, **nhưng cả hai bảng
  KHÔNG có cột `source`** ⇒ theo bất biến #1 (không provenance ⇒ đứng ngoài) chúng **không được dùng
  cho quyết định**. Đây là *tài sản ngủ đông chờ chuẩn hoá*, không phải khoảng trống.
- **WHERE:** `backend/data/financial_facts.db` (2 bảng + `*_backup`), `src/financial/*` (nơi ghi).
- **DONE-WHEN:** mỗi bảng có `source` không rỗng cho ≥ 99% dòng mới; dòng cũ được gắn
  `PROVENANCE_UNKNOWN` thay vì suy đoán; có test từ chối dùng dòng thiếu provenance trong đường điểm.
- **RISK:** hai bảng này trông rất "hợp mắt" (POC/VAH/VAL, active demand) ⇒ dễ bị đưa vào cổng trước
  khi có provenance. Việc cần làm trước tiên là **truy lại nguồn ghi** (crawler nào, ngày nào), không
  phải tinh chỉnh tham số.
- **PHÁN QUYẾT:** ✅ **DONE 2026-10-16 (truy vết)** — và **lật ngược giả thuyết đã ghi ở mục này**:
  giả thuyết cũ ("phân bổ KL theo mức giá không thể suy ra từ OHLCV EOD ⇒ tồn tại nguồn intraday
  chưa chuẩn hoá") là **SAI**. Writer đã tìm được: **`backend/src/financial/market_behavior_engine.py`**
  (`compute_volume_profile`, dòng ~185–250) + `backend/src/backtest/backfill_behavior_pit.py` (dựng bù
  lịch sử). Nguồn vào là **`screener_cache.daily_ohlcv` (EOD!)** — `get_ohlcv()` đọc `close/high/low/volume`,
  rồi **CHIA ĐỀU khối lượng của phiên lên dải [low, high]** (`vol_per_unit = vol / span`) ⇒ POC/VAH/VAL
  và `hvns` là **ƯỚC LƯỢNG MÔ HÌNH**, không phải khối lượng khớp thật tại từng mức giá. Đây là *giả định
  hình học*, không phải đo lường ⇒ **nguy hiểm hơn cả "thiếu provenance"**: một consumer có thể đọc
  POC như số liệu quan sát được.
  **Khuyết điểm thật:** bảng thiếu `method`/`is_synthetic`/`derived_from` (tác giả không thấy cần
  `source` vì dữ liệu suy ra tại chỗ) ⇒ **KHÔNG đóng băng, KHÔNG đòi nguồn intraday**: việc đúng là
  (i) ghi rõ `method='UNIFORM_WITHIN_DAILY_RANGE'`, `derived_from='screener_cache.daily_ohlcv'`,
  `is_synthetic=1` cho dòng hiện có; (ii) cấm mọi cổng/điểm dùng nó như số đo (chỉ hiển thị kèm cờ).
  **Phát hiện phụ (nợ mới, thuộc mục 6):** `get_ohlcv()` truy vấn `daily_ohlcv` **trần, không
  `VALID_ROW_SQL`/`is_stale`** ⇒ dòng cách ly chảy thẳng vào bảng dẫn xuất — thêm một điểm mẫu hình 3
  ở tầng **WRITER** (không chỉ reader). Kèm ghi chú: `volume_profile` chỉ phủ **57 mã**, phiên cuối
  2026-10-02; `active_demand` phiên cuối 2026-09-18 ⇒ cả hai đã **ngừng được nuôi**.
- **KẾT QUẢ THỰC THI (GO 2026-10-16):**
  - **Migration** `backend/scripts/migrate_label_synthetic_microstructure.py` (commit `e646089`) —
    ADDITIVE + IDEMPOTENT: thêm `is_synthetic INTEGER DEFAULT 1`,
    `method TEXT DEFAULT 'UNIFORM_WITHIN_DAILY_RANGE'`, `derived_from TEXT DEFAULT 'screener_cache.daily_ohlcv'`;
    vật chất hoá **54.765 + 2.835 dòng = 100% `is_synthetic=1`, 0 NULL**; **không đổi số dòng, không đổi
    khoảng ngày** (bất biến chứng minh bằng đếm trước/sau); chạy lại lần 2 ⇒ "không có gì để làm".
  - **Hàng rào** `src/database/synthetic_guard.py` + `backend/tests/test_synthetic_microstructure_guard.py`
    (commit `1b9aa3b`) — `SYNTHETIC_SAFE_SQL`, `synthetic_safe_predicate()`, `assert_consumable()`,
    `describe_labels()`; **fail-closed** khi thiếu cột nhãn hoặc nhãn NULL ("không biết" ≠ "sạch").
    Bộ test gồm **ca chống vá quá tay** (bộ quét phải bắt được một đoạn đọc trần giả) + **quét toàn bộ
    đường tín hiệu** (`engine/`, `financial/`, `analysis/`, `governor/`, `portfolio/`, `core/holdings/`)
    + whitelist-writer không mục rữa + canary trên store thật (**9/9 PASS**).
  - **CONSUMER THẬT ĐÃ VÁ** (bộ quét bắt ngay lần chạy đầu): `src/governor/company_state.py::L4BehaviorLoader`
    đọc `volume_profile` (POC/VAH/VAL để chấm điểm L4_BEHAVIOR) và `active_demand` **không lọc nhãn** ⇒
    đã thêm `AND is_synthetic = 0` vào **4 truy vấn** + `_assert_microstructure_label()` fail-closed.
  - **⚠️ HỆ QUẢ Ở TẦNG QUYẾT ĐỊNH (đo được, cần phê chuẩn):** sau khi lọc, **0/57 mã** còn dữ liệu vi cấu
    trúc dùng được ⇒ **L4_BEHAVIOR = `NO_DATA` cho mọi mã** (SHB/VNM/FPT đo thật: `score=0, grade=NO_DATA`).
    Đúng theo bất biến #1 (không có số đo thật ⇒ đứng ngoài), nhưng đây là thay đổi ở **tầng ra quyết định**
    của governor — hiện **không** tác động live vì `ENTRY_LOCK = True`/`Hard Shutdown = True`.
    → **ĐÃ PHÊ CHUẨN (2026-10-19): RATIFIED.** Cơ sở: `vol_per_unit = vol / span` là **giả định hình học
    phân phối đều**, không phản ánh sổ lệnh thật; trả `NO_DATA/score 0` an toàn hơn để thuật toán chấm
    điểm sức mạnh trên một ảo ảnh toán học. Không cần rollback; chỉ mở lại khi có nguồn intraday thật.
  - **Ca EPS ≤ 0 (2026-10-19):** dùng **fixture TỔNG HỢP**, không săn mã lỗ ngoài thị trường (VN30 2026Q2
    đều lãi; một fixture phụ thuộc doanh nghiệp sẽ thành flaky). Test `39c075d`:
    `backend/tests/test_valuation_negative_eps_synthetic.py` — EPS âm/0 ⇒ **không có PE**, flag
    `NEGATIVE_EARNINGS`, `is_clean=False`; **kèm ca đối chứng dương** (EPS > 0 ⇒ PE = 30.000/4.772) và ca
    chứng minh **PB không bị ảnh hưởng**.

## 9. Pipeline EOD đuổi kịp bảng dẫn xuất (`market_wide_breadth_daily`, `regime_history`)

- **WHAT:** chạy bù phần sinh bảng dẫn xuất cho các phiên đã có OHLCV nhưng chưa có dòng dẫn xuất.
- **WHY (số đo 2026-10-16, sau phiên):** `daily_ohlcv` = **16/10** · `market_foreign_history(daily)` =
  **16/10** · nhưng `market_wide_breadth_daily` = **14/10** (trễ **2 phiên**; 12/10 `degraded=1` với
  chỉ **89 dòng** active) và `regime_history` = **09/10** (trễ **1 tuần**). Hệ quả định lượng: tầng
  hiển thị đang thiếu cờ suy giảm của 15–16/10 và engine regime *bảng* khác engine regime *live*
  (live ra CRISIS 0,3271 nhưng bảng chưa ghi) ⇒ hai nguồn dễ mâu thuẫn khi đọc chéo.
- **WHERE:** script rebuild breadth (đã có) + đường ghi `regime_history` trong pipeline EOD.
- **DONE-WHEN:** `MAX(date)` của hai bảng **bằng** `MAX(date)` của `daily_ohlcv` sau mỗi phiên; phiên
  thiếu phủ vẫn được ghi kèm `degraded=1` (không bỏ im lặng).
- **RISK:** chạy bù muộn có thể ghi `regime_history` từ board **đóng băng** ⇒ phải kiểm phiên bóng ma
  (`detect_duplicate_sessions`) trước khi ghi, và tuyệt đối không để job bù ghi đè phiên đã có dữ liệu
  thật.

- **PHÁN QUYẾT:** ✅ **DONE 2026-10-16** (GO with guard của user, ưu tiên 2). Kết quả đo:
  - **Guard 1 (phiên sạch):** 15/10 (829 dòng hợp lệ, 0 `is_stale`, 0 NULL, có VNINDEX) · 16/10
    (870, 0 `is_stale`, 0 NULL, có VNINDEX) ⇒ **đạt**.
  - **Guard 2 (không xoá lịch sử):** tổng dòng breadth **1.692 → 1.694** (+2 = đúng 2 phiên mới);
    `market_wide_breadth_daily` **KHÔNG** bị xoá dòng nào; `regime_history` bù **28/28 phiên** trong 10s.
  - **Guard 3 (kiểm toán sau ghi):** `MAX(date)` = **2026-10-16** cho cả ba bảng (`daily_ohlcv`,
    `market_wide_breadth_daily`, `regime_history`) ✓.
  - **Số mới vào:** breadth 15/10 **+2,056%** (active 824) · 16/10 **+12,687%** (active 865, degraded=0)
    — khớp độc lập với A/D tự tính (+2,4% và **+12,6%**).
  - **⚠️ ĐỔI SỐ LỊCH SỬ (khai báo, không giấu):** 14 dòng 24/09→14/10 bị upsert giá trị MỚI vì dòng cũ
    được tính bằng **bản code CŨ** (khác vị từ). Hai ca là **sửa lỗi**: 12/10 active **89 → 849**
    (breadth +16,85% → +0,824% — dòng cũ là rác của lượt rebuild cụt) và 13/10 breadth −17,16% →
    **−24,23%** (khớp A/D tự tính −24,0%; vẫn `degraded=1` vì 743 < 750). Các ca còn lại lệch ≤1pp và
    **đều dịch về phía khớp phép đo độc lập** ⇒ coi là hiệu chỉnh, cần user phê chuẩn.
  - **BUG GỐC GÂY RA NÓ + ĐÃ VÁ:** `load_bars` **không lấy lookback** ⇒ phiên ĐẦU của mọi cửa sổ không
    có `prev` ⇒ toàn bộ chỉ số = 0 và bị upsert đè lên dòng cũ (đã xảy ra thật với 24/09 và 25/09 trong
    lượt chạy đầu của tôi). Vá: nạp thêm **10 ngày lịch** trước `--start` để tính, **chỉ ghi** phiên
    trong `[start, end]` (`market_wide_breadth.py` `main()`) ⇒ phiên đầu luôn có `prev` thật. Đây là
    dạng lỗi "im lặng ghi số sai" đúng loại bất biến #1 cấm.
  - **Ghi chú vận hành mới:** muốn chạy bù breadth phải đặt `PYTHONPATH=<repo>/backend`
    (script `from database...`/`from src.database...` cần `backend` trên sys.path).

---

## 10. Tách đôi cờ khoá vốn: `data_stale_lock` vs `governor_regime_lock`

- **WHAT:** `system_state.json` hiện chỉ có **một** cờ `portfolio_critical_lock`, đang gánh hai vai khác nhau:
  (a) *khoá do dữ liệu* — `stale_manager._writeoff` gọi `_unlock_state()` khi stale% dưới ngưỡng
  (`stale_manager.py:154-156`); (b) *khoá do chính sách* — chính sách 100% tiền mặt / regime CRISIS.
- **WHY:** một cờ hai vai thì không kiểm toán được: khi khoá bị gỡ, không biết cơ chế nào gỡ; khi re-assert
  (`ptck_assert_lock.sh`, cron 07:00 hằng ngày, `reason='DAILY_GOVERNOR_CRISIS_OVERLAY'`) không phân biệt
  được với khoá dữ liệu. Đây là lớp bảo vệ tiền nên phải tường minh.
- **ĐỀ XUẤT:** hai cờ độc lập — `data_stale_lock` (engine dữ liệu tự set/clear) + `governor_regime_lock`
  (chính sách, chỉ người/job chính sách set/clear). `is_locked()` = OR hai cờ; `status()` in cả hai + lý do.
  Ngưỡng khoá dữ liệu giữ nguyên; `ENTRY_LOCK` hiệu lực = `data_stale_lock or governor_regime_lock`.
- **HIỆN TRẠNG:** đã có lớp phủ bù (cron 07:00 `ptck-assert-lock-0700` + `lock(reason=...)`), đủ an toàn
  cho giai đoạn hiện tại; việc tách cờ là chuẩn hoá kiến trúc, không phải sự cố.

## 11. Phát hiện phụ trong phiên PIT 2026-10-19/20 (chưa xử lý)

1. **`health_ratios` có kỳ 2026Q3 trước hạn công bố luật định (2026-10-30).** Đo trên kho thật: live
   `_latest_period` = `2026Q3` với ngày công bố 30/10 (tương lai 10 ngày). `health_ratios` là chỉ số **dẫn
   xuất** (engine tính từ `financial_facts`) nên có thể được tính từ dữ liệu chưa đủ kỳ ⇒ cần kiểm provenance
   của writer (`company_health_engine`) xem có ghi ratios cho kỳ chưa đủ 90 ngày không. Ảnh hưởng: run live
   dùng ratios "chưa công bố"; backtest thì **đã** an toàn nhờ `_latest_period(asof=)`.
2. **Backtest vn20 lịch sử cho T1 = 0** (`asof=2022-06-30` và `2024-08-13`): không phải lỗi logic mà do cờ
   `verification_status` mới chỉ tồn tại cho **FY2025** (197 dòng / 83 mã) ⇒ cổng kiểm toán annual từ chối
   mọi kỳ trước đó (fail-closed đúng luật). Muốn backtest vn20 lịch sử phải sinh lại cờ xác minh cho các năm
   trước (đối chiếu 4 quý vs annual) — **chưa làm**, cần GO riêng.
3. **Nit:** `vn20_quant_filter._periods_n_years` bị **định nghĩa hai lần** (một stub rỗng ở dòng ~199, bản
   thật ngay sau) — bản đầu là dead code; nên xoá khi có dịp. Lưu ý file này dùng cú pháp **PEP 758**
   (`except A, B:` không ngoặc) nên **bắt buộc Python ≥ 3.14**.

## 12. Đồng bộ cửa sổ nạp bar giữa Live và Replay trong Breadth Engine

- **WHAT:** `detect_regime(target_date=None)` (live) nạp `daily_ohlcv` từ mốc cố định `2025-10-01`,
  còn `detect_regime(target_date=...)` (replay) chỉ nạp **60 ngày lịch** trước target. Vì
  `ma20`/`avg_vol_20d` dùng `rolling(20)` trên **chính cửa sổ đã nạp**, cửa sổ ngắn loại oan các mã
  giao dịch thưa ⇒ mẫu số breadth đổi.
- **WHY (số đo 2026-10-23):** cùng seed 0,3459 (22/10), live ra `0,4214` với universe thanh khoản
  **353 mã**, breadth **60,91 %**; replay ra `0,4244` với **326 mã**, breadth **63,80 %**. Lệch
  **Δ = 0,0030**, vượt ngưỡng test 2e-3 ⇒ `test_live_khop_hoan_toan_voi_replay` **ĐỎ**. Nguyên nhân
  trực tiếp: **150 mã** không đủ 20 phiên trong cửa sổ 60 ngày (ACM 6, BEL 3, …) so với **5 mã** ở
  đường live.
- **WHERE:** `backend/src/engine/regime_engine.py` (nhánh nạp `df_all` khi `target_date` khác rỗng),
  đối chiếu nhánh live.
- **DONE-WHEN:** `detect_regime(T)` và `detect_regime(None)` cho **cùng score trên cùng ngày** (sai
  số ≤ 2e-3 sau làm tròn hiển thị), với mọi ngày trong 2021–2026; test
  `test_regime_provenance_20261022.py::test_live_khop_hoan_toan_voi_replay` xanh; **không** đổi
  `regime_history` lịch sử trừ khi có quyết định restatement riêng kèm audit ledger.
- **RISK:** nới cửa sổ replay sẽ **đổi B-Score lịch sử** ⇒ đổi các báo cáo nghiên cứu cũ. Vì vậy
  phải chạy **so sánh trước/sau** trên toàn bộ 2021–2026, liệt kê số dòng đổi, và chỉ restatement
  khi có GO. Đây là lý do **chưa vá ngay 2026-10-23**.
- **PHÁN QUYẾT:** ✅ **FIXED 2026-10-23** — chọn hướng **kéo LIVE về cửa sổ trượt 60 ngày** (KHÔNG nới
  replay), nên **không đụng một dòng lịch sử nào** (mọi dòng cũ vẫn được dựng bằng đường replay ⇒
  bất biến). Bằng chứng:
  - `engine/regime_engine.py::detect_regime`: bỏ hai nhánh nạp khác nhau, thay bằng **một `as_of` duy
    nhất** + cửa sổ `date >= date(as_of,'-60 days') AND date <= as_of` cho cả LIVE (`as_of = MAX(date)`
    của `daily_ohlcv` không-stale) lẫn REPLAY. B-Score 23/10: LIVE `0,4214 (353 mã) → 0,4244 (326 mã)`,
    **khớp tuyệt đối** REPLAY.
  - Dòng `regime_history 2026-10-23` (do chính phiên hôm nay ghi bằng đường LIVE cũ) được **restatement
    về 0,4244/RANGING** kèm vết append-only `eod_write_audit_ledger` (`caller_module =
    regime_engine.window_unify_restatement_20261023`, `status = RESTATED`). **Không** restatement dòng nào khác.
  - Test: `tests/test_regime_provenance_20261022.py` **8/8 PASS** (gồm `test_live_khop_hoan_toan_voi_replay`
    nay XANH với `diff = 0`), `test_regime_degraded_contract.py` 8/8, ruff sạch.
  - **Tác động backtest = 0** (backtest dùng đường REPLAY, đã không đổi) — chạy lại 2021–2025 tái lập
    đúng kết quả lưu trữ (verdict FAIL, xem `BACKTEST_RESULTS.md`).

## 13. BVPS vendor (PIT shares) bị ưu tiên khi giá là `ADJUSTED_TODAY` — ĐÃ VÁ

- **WHAT (bản đầu, đã đính chính):** không phải "mọi dòng trộn basis" như ghi lúc đăng ký. Engine
  đã có BASIS-FIX 2026-08-19 cho nhánh **fallback**; lỗi thật là **`BOOK_VALUE_PS` của vendor
  (chia theo số cp TẠI KỲ) được dùng thẳng** khi có mặt, nên guard `BASIS_MISMATCH` không bao giờ
  chạy. Hệ quả đo được: `HPG 2018Q1` P/B = **1,333** thay vì **2,522**; toàn bộ nhóm có BVPS vendor
  bị lệch một hằng số bằng nghịch đảo tỉ lệ pha loãng.
- **WHY:** `valuation_engine.compute_ratios_for_period` dòng 557–578 cũ chỉ đặt
  `bvps_source="SHARES_NOW"` khi `not bvps`; `BOOK_VALUE_PS` (3.680 dòng, 675 mã) đi đường tắt.
- **FIX:** commit **`ab81b6c`** — với `PRICE_BASIS = ADJUSTED_TODAY`, BVPS **luôn** =
  `TOTAL_EQUITY / shares_now`; thiếu equity/shares_now ⇒ cờ chặn `BVPS_BASIS_UNVERIFIED`.
  Test RED→GREEN: `test_bvps_provided_khong_duoc_de_len_shares_now`,
  `test_bvps_provided_thieu_equity_thi_bi_chan`. Rebuild 791 mã: 0 lỗi, backup
  `financial_facts.db.bak_recompute_scores_20261023_165414`, **13.547 dòng P/B sạch**, chỉ 175 dòng
  `BVPS_BASIS_UNVERIFIED`.
- **CÒN LẠI (chuyển Mục 14):** Basis A chỉ nhất quán nội bộ; `close` là chuỗi back-adjust **tổng lợi
  suất**, nên P/B tuyệt đối vẫn không dùng được cho ngưỡng 0,80.
- **PHÁN QUYẾT:** ✅ **FIXED (nội bộ)** — `ab81b6c`, 2026-10-23.

## 14. Thiếu chuỗi giá RAW theo ngày ⇒ P/B, P/E, P/S tuyệt đối không kiểm định được

- **WHAT:** `daily_ohlcv` chỉ có một chuỗi giá đã back-adjust (`close == adj_close` trên **100 %**
  dòng, đo 2026-10-23). Điều chỉnh này gồm cả **cổ tức tiền mặt**, không chỉ chia/thưởng. Vì vậy
  mọi tỉ số giá/giá-trị-sổ, giá/thu-nhập đều bị hạ thấp tích lũy theo thời gian và **không so được**
  với P/B thị trường hay ngưỡng tuyệt đối (`P/B ≤ 0,80`).
- **WHY (số đo):** HPG `2020-01-02` lưu `close = 7.390` trong khi giá khớp lệnh thực tế cao hơn
  nhiều lần; HPG 2018Q1 P/B theo Basis A = 2,522 nhưng theo giá raw/BVPS PIT xấp xỉ 5. Trộn hai
  cách đọc này là nguồn "rẻ ảo" (đúng lớp lỗi HPG 2018Q1 đã ghi trong
  `financial_facts_ratio_audit.md`).
- **WHERE:** crawler giá (`backend/src/data/...`, trường `close`/`adj_close`), `valuation_engine`
  (`_get_price_with_flag`), và mọi consumer dùng P/B tuyệt đối.
- **DONE-WHEN:** có cột `close_raw` (hoặc bảng `price_raw`) đúng giá khớp lệnh ngày T cho toàn bộ
  lịch sử; chứng minh bằng 3 ca chia/thưởng + 3 ca cổ tức tiền mặt (giá raw không đổi tại ex-date,
  giá adjusted giảm đúng tỉ lệ); `valuation_engine` tính P/B bằng raw price + BVPS PIT.
- **RISK:** nguồn dữ liệu miễn phí thường chỉ trả adjusted. Nếu không có raw ⇒ **vĩnh viễn không
  dùng ngưỡng P/B tuyệt đối**; chỉ được dùng P/B **tương đối trong cùng một mã** (z-score thời
  gian), và phải khai báo rõ.
- **PHÁN QUYẾT:** ⏳ **OPEN** (phát hiện 2026-10-23; là điều kiện mở Khoang 1).

## 15. `_run_child` decode stdout bằng cp1252 làm chết reader thread

- **WHAT:** `recompute_valuation_scores_pit.py::_run_child` gọi `subprocess.run(..., text=True)`
  không chỉ định encoding ⇒ trên Windows dùng cp1252, gặp tiếng Việt trong log con thì
  `UnicodeDecodeError` nổ trong reader thread và **mất toàn bộ chẩn đoán** của script con.
- **WHY (đo 2026-10-23):** lần chạy `--apply` thật in traceback
  `charmap codec can't decode byte 0x8d` dù bước [C] exit=0. Mã thoát vẫn lan truyền đúng, nhưng khi
  con **thất bại** thì 20 dòng chẩn đoán có thể rỗng ⇒ che nguyên nhân.
- **WHERE:** `backend/scripts/recompute_valuation_scores_pit.py`, hàm `_run_child`.
- **DONE-WHEN:** `encoding="utf-8", errors="replace"`; test dựng script con in tiếng Việt rồi thoát
  mã 3, khẳng định (a) trả 3, (b) chẩn đoán chứa nguyên văn tiếng Việt, (c) không traceback.
- **PHÁN QUYẾT:** ⏳ **OPEN** (không ảnh hưởng dữ liệu; ưu tiên thấp).

## 9. 🔴 OPEN — Phục nguyên GIÁ THÔ 2021–2026 cho FOC & VEA + chạy lại Pha 1/Pha 2 audit cổ tức

- **WHAT:** (a) dựng lại chuỗi giá THÔ (lưới tick 100 VND) cho FOC & VEA 2021–2026 từ corporate
  actions có provenance VSDC (giá thô = giá điều chỉnh `/` hệ số tính tuần tự), tự kiểm bằng
  `price_provenance_gate` (100% bar phải nằm trên bội số 100; fail-closed nếu không đạt);
  (b) đối soát VSDC 2 chiều cho VEA 2021–2026 (ngày GDKHQ + đ/cp);
  (c) chạy lại `run_foc_empirical_audit.py` trên giá thô;
  (d) mới chạy Pha 2 VEA trọn vẹn (DRIP vs Dividend Sweeper B3).
- **WHY:** Kiểm toán 2026-10-27 chứng minh nguồn `kbs` trả chuỗi back-adjusted server-side
  (VEA 2021: 89,2% bar lệch lưới tick; FOC 2021: 92,8%; API live trả close=27542.1496;
  ex-date FOC 12/04/2021 rơi −4,5% thay vì −18,5%). Engine cộng cổ tức tiền mặt trên giá đã
  loại cổ tức ⇒ **đếm cổ tức hai lần**; toàn bộ số liệu Pha 1 FOC (1,596 tỷ · 8,41% · B3 10,01% ·
  8 phiên · −46,91%) là NON-DECISIONAL. Chi tiết: `vea_phase2_price_integrity_verdict.md`.
- **WHERE:** `backend/src/research/price_provenance_gate.py` (cổng, ĐÃ CÓ) ·
  `run_foc_empirical_audit.py` (chờ giá thô) · runner Pha 2 VEA (chưa viết, chờ Bước 1–2).
- **DONE-WHEN:** gate PASS (0% lệch lưới) cho FOC & VEA 2021–2026; ex-date gap khớp cổ tức
  công bố ±2%; Pha 1 & Pha 2 chạy lại có số liệu mới; manifest đổi trạng thái
  `foc_dividend_audit` sang giá trị hợp lệ và `research_modules` ghi 1/5 (hoặc hơn) mã kiểm định.
- **RISK:** (a) danh sách sự kiện VEA 2021–2026 thiếu 1 đợt ⇒ toàn bộ hệ số phục nguyên sai
  (chặn bằng kiểm lưới tick); (b) vintage trộn trong DB cũ (không pull lại) ⇒ PHẢI phục nguyên
  từ API vintage hiện tại, không từ DB; (c) CafeF export endpoint đang trả 400 — không dựa vào
  một nguồn duy nhất; (d) mọi suy luận thanh khoản chỉ hiệu lực trong cửa sổ raw đã chứng minh (2026).
- **PHÁN QUYẾT:** ✅ **ĐÓNG 29/10/2026 (E15/E16/E17)** — *giữ nguyên nội dung phát hiện để làm lịch
  sử*: cổng `--reconstructed` nay certify **cả 5 mã** (FOC, VEA, SMB, PAT, DSN) exit 0; Pha 1–5 đã
  chạy lại trên chuỗi thô đã chứng nhận (`raw_price_reconstruction_findings.md` E15–E17). Phần
  "chờ GO Hội đồng" ở trên không còn hiệu lực.

## 16. 🟡 OPEN — Lỗ hổng PHỦ dữ liệu `daily_ohlcv` của DSN (2026-04..07) và PAT (2026-06)

- **WHAT:** (a) DSN thiếu **39 bar** trong 2026-04..2026-07 (tháng 2026-05 chỉ còn **2/20 bar**);
  (b) PAT có **8 bar invalid** trong 2026-06-04..2026-06-18 (bị `VALID_ROW_SQL` loại vì
  `low/close/high` không hợp lệ). Cần crawl lại từ nguồn và backfill **qua cổng provenance**.
- **WHY:** phát hiện 29/10/2026 khi làm tầng 3 kiểm chéo vendor (E17). Đo bằng SQL trực tiếp trên
  `screener_cache.db` (chế độ panel lịch sử, `VALID_ROW_SQL`): DSN 1.406 bar hợp lệ / KBS 1.471;
  PAT invalid 8, DSN invalid 0. Hệ quả đã thấy: cửa sổ kiểm chéo 10 ngày lịch quanh ex DSN
  2026-05-18 chỉ còn 1 bar mỗi phía ⇒ **phải đổi sang cửa sổ theo SỐ BAR** và dùng trung vị
  (4/10 bar "sau" lệch tới ±1,2% làm mean log suy ra implied 1.530 thay vì 1.600 ⇒ `conflict` SAI).
  Không chặn 5/5 vì cổng + tầng 3 vẫn certify được chuỗi từ vintage KBS.
- **WHERE:** `backend/data/screener_cache.db` (`daily_ohlcv`), đường ingest/backfill hiện có;
  kiểm chứng lại bằng `python -m src.research.price_provenance_gate --symbols DSN,PAT --reconstructed`
  và `tests/test_cross_vendor_agreement.py`.
- **DONE-WHEN:** độ phủ DSN/PAT khớp lịch giao dịch (không thiếu bar trong 2026-04..07 và
  2026-06); 0 bar invalid; tầng 3 vẫn `agree`/`vendor-lag` (không sinh `conflict` mới).
- **RISK:** (a) `fillna`/nội suy bar thiếu là vi phạm Missing > Fake ⇒ tuyệt đối cấm, phải crawl
  nguồn; (b) bar PAT invalid không được "sửa tay" cho khớp band — nếu sửa để tầng 3 hết conflict
  thì đúng là vá quá tay mà tầng 3 tồn tại để chặn; (c) backfill có thể đổi `close` lịch sử ⇒ phải
  chạy lại cổng + 5 runner và so số trước/sau, không được im lặng đổi số đã báo Hội đồng.
- **PHÁN QUYẾT:** ⏳ **OPEN** (phát hiện 2026-10-29, không chặn nghiệm thu 5/5).

## Ghi chú vận hành

- **CẢNH BÁO PHỐI HỢP (2026-10-16):** trong working tree này có **một tiến trình auto-commit khác**
  (author `Alpha Forge V4.6 <alpha@ptck-vnstock.local>`) quét `git add`-style toàn cây ⇒ **các bản vá
  đang dở bị nuốt vào commit của nó với thông điệp KHÔNG mô tả đúng nội dung**. Bản đồ thật của 3
  commit 20:42–20:43 hôm nay: `38dc9e3` ("...VN30 universe ingestion") thực chất chứa **4 file của
  Mục 3 + lookback breadth + canary VNM** (của Agent 1) cùng 2 script mới của agent kia;
  `f67563a` ("update resolution status...") và `2e5f43d` ("normalize CRLF...") chứa **phần backlog
  của Mục 3/8** do tôi viết. Hệ quả: **`git status` có thể TRỐNG dù vừa sửa file**, và `git add` +
  `git commit` của mình báo "no changes added". Bài học: **commit ngay sau mỗi lát nhỏ**, truy vết
  bằng `git log -- <file>` / `git show --stat`, đừng tin `git status`. (File `backend/scripts/rescale_unit_history.py`
  đang untracked là việc dở của agent kia — KHÔNG chạm.)
- Backlog này là nơi **duy nhất**; khi một mục được thực thi, ghi commit hash vào mục đó và đổi
  nhãn phán quyết, KHÔNG xóa (giữ lịch sử quyết định).
- Ba hạng mục 1–3 là dọn dẹp nợ cũ trong một lượt; hạng mục 4 là **đường ống dữ liệu mới** nên
  phải tách lượt và phải có giả thuyết tiền đăng ký trước khi dùng cho bất kỳ cổng nào.
- Ba hạng mục 5–7 là **nợ theo LỚP LỖI** (không theo từng ca), lộ ra sau chuỗi Ca 1–Ca 6: mục 5 và
  6 phải thi hành theo lát (1 module = 1 commit, scanner làm thước đo trước-sau), mục 7 là hạ tầng
  công cụ. Riêng 7(a) đã thực thi ở commit `0db323f`.
- Số đo trong mục 5–7 là số ĐO ĐƯỢC tại 2026-10-15 bằng `scan_timeseries_hazards.py --top 1000`.
  Khi thi hành phải đo LẠI và cập nhật số kèm ngày — không chép lại số cũ như thể còn đúng.
- Mục 6 có bẫy chế độ đọc: `is_stale = 0` chỉ đúng cho đường PIT/live, CẤM cho panel lịch sử
  (xem luật B trong `AGENTS.md`). Phân loại chế độ TRƯỚC khi sửa.
