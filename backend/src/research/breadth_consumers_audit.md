# Bản đồ consumer của độ rộng thị trường — Hợp đồng 2 tầng `degraded`

Kiểm kê 2026-10-15 bằng grep toàn `backend/` (SQL trực tiếp + import + tên cột).
Quy tắc áp dụng (theo chỉ đạo): tầng **tính toán tín hiệu** lọc cứng `degraded = 0`;
tầng **hiển thị** giữ nguyên dòng nhưng gắn cờ `is_degraded: true`.

## 1. Ai đọc bảng `market_wide_breadth_daily`

| Consumer | Tầng | Đọc gì | Trạng thái |
|---|---|---|---|
| `governor/unfreeze_gates_v2.py:400` | tín hiệu | SQL trực tiếp lên bảng | ✅ **đã lọc sẵn** `(degraded IS NULL OR degraded = 0)` |
| `governor/shock_detector.py::_breadth` | tín hiệu | `regime_history.breadth_velocity` | ✅ **nối 2026-10-15**: loại cứng mọi ngày có `degraded=1` bên bảng breadth; trace ghi rõ số phiên đã loại |
| `services/breadth_service.py::get_breadth_history` → `api/routes/breadth.py:/history` | hiển thị | `regime_history` | ✅ **nối 2026-10-15**: LEFT-join sang bảng breadth, mỗi bản ghi thêm `is_degraded` (giữ nguyên dòng, không lọc) |
| `analysis/market_wide_breadth.py` | writer + CLI | bảng (INSERT) | tự sinh/backfill |
| `tests/test_unfreeze_gates_v2.py:260` | test fixture | bảng | ✅ 24 test PASS sau thay đổi |

**API hợp đồng mới** (`analysis/market_wide_breadth.py::load_series`):
`load_series()` = **mặc định lọc cứng** `degraded = 0` (tầng tín hiệu);
`load_series(include_degraded=True)` = giữ hết + mỗi bản ghi có `is_degraded` (tầng hiển thị).
Đo 2026-10-15: 1.656 dòng (đã lọc) vs 1.692 dòng (giữ hết, 36 phiên `is_degraded=True`) — chênh đúng 36.

## 2. Phát hiện provenance (quan trọng): HAI chuỗi breadth khác nhau

| Chuỗi | Bản chất | Nguồn ghi |
|---|---|---|
| `market_wide_breadth_daily.breadth_pct` | Δ(adv−dec)/total_active × 100 của **rổ cổ phiếu** (đã loại `INDEX_SYMBOLS`) | `analysis/market_wide_breadth.py` |
| `regime_history.breadth_pct` / `.breadth_velocity` | **Điểm của board phiên** (không cùng công thức) | `engine/regime_engine.py:backfill_regime_history`, `database/timeline_manager.py` |

Cảnh báo này đã có sẵn trong `unfreeze_gates_v2.py:24-26`; kiểm kê lần này xác nhận lại và
bổ sung hệ quả: **hai chuỗi có thể lệch nhau**, nên không được dùng thay thế nhau trong cùng
một phép tính, và mọi z-score/percentile phải khai rõ đang dùng chuỗi nào.

## 3. Consumer KHÔNG đọc bảng (đã kiểm, không phải bỏ sót)

`core/executive_report.py:45`, `core/market_snapshot.py:158`, `backtest/system_replay.py:268`,
`alpha/regime_return_matrix.py`, `api/routes/system.py:61` đọc `breadth_pct` từ **JSON/verdict của
board phiên hiện tại** (hoặc từ `regime_history`), **không** đọc bảng `market_wide_breadth_daily`.
⇒ Hợp đồng 2 tầng cho nhóm này phải áp ở **nguồn (board)**, không phải ở bảng. Đã gắn cờ ở
`breadth_service` cho đường `regime_history`.

## 4. Gap còn lại — **ĐÃ HOÃN (NO-GO 2026-10-15) → đưa vào BACKLOG tái cấu trúc schema**

`regime_engine.backfill_regime_history` ghi `regime_history` từ board của chính phiên đó: nếu
phiên đó **thiếu mã**, giá trị vẫn được ghi **không kèm cờ suy giảm**.

Phán quyết: **KHÔNG migration schema SQLite ở thời điểm này** — tránh rủi ro lock DB / xung đột
với các tiến trình live/cron khác. Rủi ro đã được chặn đứng ở **tầng đọc** (join sang bảng
breadth), nên không còn đường nào *âm thầm* tiêu thụ dữ liệu kém phủ. Việc thêm cột
`is_degraded` vào `regime_history` + ghi cờ tại writer được ghi vào **backlog tái cấu trúc
schema dài hạn**, không thuộc chu kỳ nâng cấp hạ tầng này.

→ Đã gom vào sổ nợ kỹ thuật duy nhất: `backend/src/research/tech_debt_backlog.md` (mục 1–4),
cùng với ngưỡng Sentinel lỗi thời và pipeline BCTC Point-in-Time.
