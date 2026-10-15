# SỔ GIẢ THUYẾT TIỀN ĐĂNG KÝ (Pre-registered Hypotheses)

Quy tắc chung của sổ này:
- Mỗi giả thuyết phải ghi **phát biểu định lượng**, **cách đo**, **cửa sổ kiểm chứng**, và
  **tiêu chí bác bỏ** TRƯỚC khi chạy kiểm chứng xuôi.
- **CẤM** hồi quy ngược / tinh chỉnh tham số dựa trên kết quả đã quan sát.
- **CẤM** dùng cửa sổ OOS 2025-2026 (đã nhiễm, xem `oos_2026_non_decisional_note.md`) làm nơi
  hợp thức hoá. Chỉ nhận bằng chứng **kiểm chứng xuôi (forward paper-trading)**.
- Giả thuyết ở trạng thái `PROPOSED` không được cấp quyền quyết định (không cổng, không entry).

---

## H-2026-10-15-A — Market-Wide Capitulation Washout

**Trạng thái:** PROPOSED (chưa kiểm chứng xuôi) — **KHÔNG dùng để ra quyết định**.

**Nguồn phát sinh (quan sát, không phải bằng chứng):** trong replay Frozen Spec
(`capitulation_replay.py`), nhóm ngày T0 có **số mã thoả ĐK1 > 10** cho win-rate T+10 = 80%
và lợi suất trung bình **+11,2%**, nhưng chỉ với **n = 5 ngày** thuộc IS 2021-2024.
Spearman(ĐK1 breadth, lợi suất T+10) toàn IS = **+0,314** (n=21 ngày).

**Phát biểu tiền đăng ký:**
> Khi độ rộng hoảng loạn toàn thị trường vượt ngưỡng — **số mã thoả ĐK1 trong một phiên > 10** —
> thì các sự kiện thoả trọn 3 điều kiện của Frozen Spec trong **cùng phiên T0** có
> kỳ vọng lợi suất T+10 **dương rõ rệt** (win-rate ≥ 80%, mean ≥ +11,2%) so với nhóm breadth thấp.

**Cách đo (giữ nguyên, không đổi tham số):**
- ĐK1/ĐK2/ĐK3 y hệt Frozen Spec 2026-10-14; breadth = số mã thoả ĐK1 trong phiên T0.
- Đo lợi suất ròng tại T+10 (entry `close_{T+2}`, chi phí 0,3%).
- So sánh nhóm `breadth > 10` với nhóm `breadth ≤ 10`; sai số chuẩn theo **cụm ngày T0**.

**Cửa sổ kiểm chứng:** CHỈ tiến về phía trước, kể từ 2026-10-16. Mọi phiên có ĐK1 breadth > 10
phải được ghi vào nhật ký giấy (`research/capitulation_paper_ledger.py`) kèm mã + ngày, để
đo T+10 khi đủ dữ liệu.

**Tiêu chí bác bỏ:** sau ≥ 20 ngày T0 độc lập có breadth > 10, nếu win-rate T+10 < 60%
**hoặc** mean ≤ 0 ⇒ BÁC BỎ, gỡ khỏi sổ.

**Ghi chú kỷ luật:** n=5 ngày là mẫu quá nhỏ; đây là **máy sinh giả thuyết**, không phải bằng chứng.
Việc cổng hoá theo breadth > 10 (nếu sau này có) phải là một quyết định RIÊNG, sau khi giả thuyết
này vượt tiêu chí trên — và phải được đăng ký lại như một thay đổi cổng mới.
