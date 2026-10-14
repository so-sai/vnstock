# OOS-2026 — Phán quyết NON-DECISIONAL (ghi nhận quản trị)

**Loại:** Governance record — READ-ONLY. Không sửa code, không sinh tín hiệu.
**Ngày ghi:** 2026-10-14. **Phạm vi:** mọi kết quả chạy trên cửa sổ OOS 2026 của Spec V2
(`backend/scripts/run_oos_2026.py`, cửa sổ 2026-01-01 → 2026-10-09).

---

## 1. Phán quyết

> **Mọi con số OOS-2026 sinh ra TRƯỚC thời điểm vá 41 phiên VNINDEX và TRƯỚC khi sửa
> `_vnindex_drop_3d()` thành fail-closed đều bị gắn nhãn NON-DECISIONAL** — không dùng để
> chọn ứng viên (C1/C2), không dùng để tuyên bố hiệu quả chiến lược, không dùng để nới/siết
> ngưỡng nào.

Ba lý do, độc lập nhau:

1. **Phanh khẩn cấp bị mù 41 phiên.** `daily_ohlcv` thiếu bar `VNINDEX` ở 41/190 phiên của
   chính cửa sổ đó (2026-05 chỉ có **2/20** phiên). `_vnindex_drop_3d()` fail-open:
   ```python
   a, b = bench.get(days[i]), bench.get(days[i-3])
   if not a or not b:
       return 0.0          # thiếu bar = "KHÔNG giảm" ⇒ phanh không thể kích hoạt
   ```
   Vế `regime` của **cùng** điều kiện `emergency` đã được vá fail-closed; vế `vn3` thì chưa.
   Vùng khuyết trùng nhau (từ 2026-04-20) ⇒ phanh bị vô hiệu **kép** ⇒ kết quả lệch cùng
   chiều (ít lần thoát khẩn cấp hơn thực tế).
   *Trạng thái 2026-10-14: đã vá 40/41 phiên (`backend/scripts/backfill_vnindex_gap.py`),
   coverage cửa sổ OOS **78,4% → 99,47%**. Còn 1 lỗ: **2026-09-01** không có bar index ở
   CẢ vci lẫn kbs (thị trường vẫn giao dịch: 1.515 mã có bar) — lỗ phía nguồn.*
2. **Sửa hậu nghiệm trên chính mẫu đã thấy số.** C1 "+1,02" được phát hiện là số của lần
   chạy KHÔNG có phanh, rồi hành vi được sửa và chạy lại **cùng cửa sổ**. Số mới là quan sát
   mới của *cùng một mẫu* ⇒ không dùng để chọn giữa C1 và C2.
3. **Luật dự án.** `AGENTS.md`: "OOS25-26 nhiễm→cấm tune; valid mới=paper/IS".

## 2. Không phải vi phạm gì

Phần sửa trong `trend_loop.py` **không** phải tune ngưỡng giao dịch: (a) hook `universe_factory`
là luật universe khai báo trước ở spec §3 (đã hash, mặc định `None` = không đổi hành vi);
(b) kiểm tra fail-closed cho `regime` là sửa tính đúng đắn. Ghi nhận công khai, không giấu.

## 3. Việc còn lại để số OOS-2026 trở nên dùng được

1. Sửa `_vnindex_drop_3d()` thành **fail-closed** (raise khi thiếu bar) — đối xứng với vế regime.
2. Kiểm tra **bán kính ảnh hưởng** của `raise` mới trong `trend_loop.py`: nó vô điều kiện cho
   mọi caller, không chỉ script OOS — nếu đường sống (daily/live) thiếu `regime_history` thì
   sẽ sập ở production.
3. Tìm nguồn thứ ba cho bar index **2026-09-01** (hoặc chấp nhận lỗ 1 phiên).
4. Chỉ sau đó mới chạy lại, và kết quả vẫn phải validate trên **IS/paper** trước khi dùng.

## 4. Bằng chứng

- `src/database/panel_quality.py`: `expected_sessions`, `assert_index_series`, `assert_index_fresh`
  (cổng đã nối vào 7 điểm neo — commit `96ae491`).
- Đo coverage: OOS-2026 `78,4%` trước vá → `99,47%` sau vá; 2021→nay `99,93%`.
- `backend/scripts/backfill_vnindex_gap.py` (commit `37da7ee`): 40/41 phiên vá được.
