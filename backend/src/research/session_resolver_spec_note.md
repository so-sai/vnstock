# Session Resolver — Specification (APPROVED, NOT IMPLEMENTED)

**Ngày duyệt:** 2026-08-23 | **Trạng thái:** SPEC READY — implementation CHƯA bắt đầu.
**Timing:** chỉ mở patch SAU KHI ghi FTSE D+1 (phiên đầu tiên sau sự kiện 21/08).
**Commit dự kiến:** `fix(core): canonicalize market session date resolution` — độc lập hoàn toàn khỏi market logic.

---

## 1. Vấn đề (quan sát thật 2026-08-23, Chủ nhật)

CLI mặc định (`final-decision` không `--date`) → `orchestrator.py:69-70` dùng
`datetime.now()` → query `date='2026-08-23'` → 0 rows → Data Quality Guard
(`orchestrator.py:108-134`) phát alert giả:

```
chỉ 0 mã đủ thanh khoản / cảnh báo DATA QUALITY / confidence 0%
```

Trong khi DB thực tế ổn: phiên 21/08 có 350 mã vol>50k (cao nhất tuần).

```
No trading session ≠ Missing market data
```

## 2. Root cause

Orchestrator thiếu **date-resolution layer**. Pattern đúng đã tồn tại nhưng bị
nhốt trong `breadth_engine.py:75-109` (fallback weekend/holiday + phân biệt
"chưa nạp dữ liệu"). Calendar chuẩn có sẵn: `VNSessionCalendar`
(`time_series_aligner.py:142`) + `backend/src/config/weekend_holidays.json`
(có scraper cập nhật qua `ptck.py check-calendar --auto-update`).

Duplicates hiện sống song song (migration targets — KHÔNG đụng trong patch 1):
`breadth_engine.py:91-100`, `erl_worker.py:78-89`, `interbank_bootstrap.py:81`.

## 3. Contract đã duyệt

```python
# src/core/session_resolver.py
@dataclass
class SessionResolution:
    requested_date: str          # ngày user yêu cầu
    effective_date: str          # phiên thực tế dùng để phân tích
    latest_available_date: str   # MAX(date) trong daily_ohlcv — provenance rõ hơn
    resolution: str              # SAME_SESSION | PREVIOUS_TRADING_SESSION | DATA_STALE | NO_DATA
    reason: str                  # ok | weekend | holiday | ingestion_pending
```

Output bắt buộc giữ provenance:

```
Requested date: 2026-08-23
Data as of:     2026-08-21
Resolution:     PREVIOUS_TRADING_SESSION
Reason:         weekend
```

### Ba invariant bất biến

1. **DATA_STALE ≠ PREVIOUS_TRADING_SESSION:**
   - CN + dữ liệu T6 tồn tại → `PREVIOUS_TRADING_SESSION`
   - Thứ 5 + dữ liệu thứ 5 thiếu → `DATA_STALE`
   - Không được dùng calendar fallback che giấu ingestion failure.
2. **Explicit `--date` không bao giờ bị silent override:** resolve vẫn chạy,
   nhưng nếu effective_date ≠ requested_date phải hiển thị cả hai + reason.
3. **Resolver quyết định NGÀY trước; Data Quality Guard tiếp tục nhiệm vụ cũ**
   (phát hiện dữ liệu bất thường trên ngày ĐÃ resolve). Không sửa Guard thành
   "0 rows = lùi ngày".

## 4. Thứ tự triển khai đã duyệt

1. Fail-first tests: T7 / CN / holiday / trading-day đủ data /
   trading-day thiếu data / explicit `--date`.
2. `VNSessionCalendar.prev_trading_day()` (copy pattern từ
   `USSessionCalendar.prev_trading_day`, cùng file).
3. `core/session_resolver.py` — calendar + DB freshness
   (`data_freshness.py` đã có staleness tracking).
4. Patch **duy nhất orchestrator** (Bước 0 gọi resolver trước Guard;
   thêm requested/effective/resolution vào output).
5. Regression toàn workspace.
6. Audit CLI còn lại — **không migrate cùng commit**.

## 5. Trạng thái

| Mục | Giá trị |
|---|---|
| Design | ✅ APPROVED (3 điều chỉnh đã nhập) |
| Implementation | ⛔ NOT STARTED |
| Điều kiện mở patch | FTSE D+1 đã ghi vào event ledger |
| Phạm vi patch 1 | core/session_resolver + VNSessionCalendar + orchestrator |
| Cấm kèm theo | breadth_engine, erl_worker, interbank_bootstrap, mọi market logic |
