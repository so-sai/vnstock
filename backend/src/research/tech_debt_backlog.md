# BACKLOG NỢ KỸ THUẬT (Technical Debt) — chu kỳ dọn dẹp kế tiếp

Sổ này là nơi DUY NHẤT liệt kê các hạng mục nợ kỹ thuật đã được phán quyết là **HOÃN** (không
phải quên). Mỗi mục phải có: WHAT / WHY (kèm số đo) / WHERE / DONE-WHEN / RISK.
Nguyên tắc: không tự thực thi khi chưa có GO; mọi mục phải fail-closed khi triển khai.

---

## 1. Migration `regime_history.is_degraded` (+ ghi cờ tại writer)

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
- **PHÁN QUYẾT:** NO-GO 2026-10-15 (chốt: giải pháp tầng đọc là đủ; xem `breadth_consumers_audit.md`).

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
- **PHÁN QUYẾT:** GO-with-guideline 2026-10-15, hẹn thực thi đầu chu kỳ kế tiếp.

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

## 8. Provenance cho `volume_profile` / `active_demand` (tài sản vi cấu trúc "ngủ đông")

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
- **PHÁN QUYẾT:** MỞ 2026-10-16 (chờ GO; cấm dùng cho quyết định tới khi có provenance).

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

---

## Ghi chú vận hành

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
