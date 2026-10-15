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

---

## Ghi chú vận hành

- Backlog này là nơi **duy nhất**; khi một mục được thực thi, ghi commit hash vào mục đó và đổi
  nhãn phán quyết, KHÔNG xóa (giữ lịch sử quyết định).
- Ba hạng mục 1–3 là dọn dẹp nợ cũ trong một lượt; hạng mục 4 là **đường ống dữ liệu mới** nên
  phải tách lượt và phải có giả thuyết tiền đăng ký trước khi dùng cho bất kỳ cổng nào.
