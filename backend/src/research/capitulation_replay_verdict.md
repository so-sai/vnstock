# PHÁN QUYẾT: Replay "Capitulation + T+2 Absorption" — **BỊ BÁC BỎ LÀM CỔNG QUYẾT ĐỊNH**

Ngày phán quyết: **2026-10-15**. Phạm vi: đặc tả đóng băng (Frozen Spec) 3 điều kiện
ĐK1 (stopping volume) → ĐK2 (giữ nền T+1) → ĐK3 (hấp thụ T+2), entry `close_{T+2}`,
đo tại `close_{T+5}`/`close_{T+10}`, chi phí 0,3%.

Công cụ: `backend/scripts/capitulation_replay.py` (commit `ef71f75`).

## 1. Bằng chứng đo được (IS 2021-01-04 → 2024-12-31)

| Chỉ tiêu | T+5 | T+10 |
|---|---|---|
| N sự kiện | 24 | 24 |
| Mean | +1,28% | +3,26% |
| **Median** | **−0,30%** | +2,50% |
| Win-rate | 41,7% | 70,8% |
| t (sai số chuẩn theo CỤM NGÀY T0, n=21 ngày) | **0,58** | **0,98** |

**Phễu lọc (fail-closed):** ĐK1 ≈ 5.561 lần → ĐK2 loại 4.517 → ĐK3 loại thêm 1.014 → **30 sự kiện
toàn kỳ** (2021: 8, 2022: 10, 2023: 2, 2024: 4, 2025: 4, 2026: 2). 0 sự kiện bị loại vì thiếu
bar hay gãy thang giá ⇒ con số không bị thổi phồng bởi cửa sổ.

## 2. Vì sao bác bỏ

- **t < 1 ở cả hai horizon** ⇒ không phân biệt được với nhiễu ngẫu nhiên.
- Tần suất ~6 sự kiện/năm: không đủ mẫu để bác bỏ phương án "lợi suất kỳ vọng bằng 0".
- Ca SHB ngày 13/10/2026 (mã **duy nhất** toàn rổ thoả trọn 3 điều kiện) là **quan sát mô tả
  đơn lẻ**, KHÔNG phải bằng chứng thống kê. Gọt chân cho vừa giày từ một ca thắng đơn lẻ là
  đúng cái bẫy mà hệ thống phải chống.

## 3. Quyết định có hiệu lực

1. Module ở trạng thái **nghiên cứu / nhật ký giấy** (`research/capitulation_paper_ledger.py`
   tiếp tục là nơi ghi nhận quan sát; `capitulation_replay.py` chỉ là công cụ replay).
2. **CẤM** nối vào `ptck.py final-decision`, **CẤM** dùng để gỡ `ENTRY_LOCK`.
   Kiểm chứng 2026-10-15: `system_state.json:portfolio_critical_lock = True` (vẫn khoá).
   `alpha/capitulation_detector.py` (CAS-DSM Phase 4) **không** được import ở bất kỳ đường
   quyết định nào (grep toàn `backend/src`: 0 consumer ngoài chính nó).
3. Điều kiện để xem lại: chỉ khi store có **dữ liệu vi cấu trúc trong phiên** (tỷ trọng lệnh
   ATC ≥ 30%, VWAP thực tế) — thứ mà store OHLCV ngày hiện tại không lượng hoá được.
4. Mọi số liệu OOS 2025-2026 giữ nguyên nhãn **NON-DECISIONAL** (`oos_2026_non_decisional_note.md`).

## 4. Hệ quả kiểm định đã ghi nhận

Kết quả này **không thay đổi**: việc replay vẫn chạy được trên nền dữ liệu đã dọn (phiên bóng ma
`2026-09-01` đã quarantine, chỉ số tách khỏi breadth, phiên `2026-10-12` phủ 865 mã hợp lệ).
Chạy lại sau khi vá dữ liệu cho kết quả **trùng khít** ⇒ kết luận không phải artefact của lỗ hổng dữ liệu.
