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

---

## H-2026-10-22-REGIME-UNFREEZE — Điều kiện mở khoá sau nhịp nảy 21/10

**Trạng thái:** PROPOSED — **đóng băng 2026-10-21 sau ATC, trước giờ mở cửa 22/10**.
Theo quy tắc dòng 9 của sổ này, giả thuyết `PROPOSED` **không tự cấp quyền quyết định**: kể cả khi
đạt 4/4, việc hạ `ENTRY_LOCK` vẫn phải do chủ hệ thống GO tường minh, không có tự động hoá nào được
đọc file này để mở khoá.

**Mục tiêu:** kiểm chứng tính bền của nhịp nảy phiên 21/10 (Breadth 46,7% → **65,5%** nhưng điểm
Regime lại **giảm** 0,31 → 0,28) — phân định "dòng tiền lớn nhập cuộc" với "nảy kỹ thuật thiếu cầu".

**Trạng thái xuất phát (đo từ `regime_history` ngày 2026-10-21, đã `DAT`):**

| Trường | Giá trị |
|---|---|
| `regime_score` | **0,28** (`status = CRISIS`) |
| `breadth_pct` (% mã trên MA20) | **65,5** (vận tốc +30,7) |
| `vol_score` | **0,20** (đang ở SÀN `max(0.2, …)`) |
| `trend_score` | **0,00** |
| Ngữ cảnh | VN-Index 1.691,97 < MA200 1.796,38 (−5,8%); ngoại 21/10 **+131,4 tỷ** (phiên mua ròng đầu tiên) |

### 4 điều kiện tiên quyết — đo bằng SQL trên `regime_history`

```sql
SELECT date, regime_score, breadth_pct, vol_score, trend_score
FROM regime_history WHERE date = :target;   -- :target = '2026-10-22', '2026-10-23'
```

1. `regime_score >= 0.40` — vượt hẳn vùng đệm 0,35–0,39 (RANGING kỹ thuật ≠ ngưỡng hành động).
2. `breadth_pct >= 60.0` — giữ được sắc xanh lan toả, không bị dội ngược T+2.
3. `vol_score > 0.20` — thoát sàn thanh khoản (sàn là hằng số trong `regime_engine.py:256`).
4. `trend_score > 0.00` — VN-Index bắt đầu có điểm cấu trúc xu hướng.

### Cây quyết định (đóng băng trước)

- **A — ĐẠT 4/4** (trong cửa sổ): đề xuất **hạ `ENTRY_LOCK`** lên chủ hệ thống để GO; nếu GO, giải ngân
  thăm dò **10–15% NAV** ở các mã đã qua T1–T4 và có breakout `Close > High20` + `Vol ≥ 1.3×ADV20`.
  **Không** được hạ ngưỡng nào trong 4 điều kiện trên để "cho vừa kết quả".
- **B — THIẾU dù chỉ 1 điều kiện** (hết cửa sổ): **GIỮ `ENTRY_LOCK = TRUE`**, 100% tiền mặt; ghi nhận
  nhịp nảy 21/10 là **hồi kỹ thuật chưa được xác nhận**. **Không** được nới ngưỡng 0,40 xuống 0,35 để
  hợp thức hoá một lệnh đã muốn mua.

### Cửa sổ kiểm chứng
**CHỈ tiến về phía trước:** các phiên **2026-10-22 và 2026-10-23** (EOD). Hết 23/10 là **hết hạn đo**;
muốn tiếp tục theo dõi phải **đăng ký giả thuyết mới** (không gia hạn ngầm).

### Tiêu chí bác bỏ / kết luận
- Hết 22/10 mà chưa đạt ⇒ chưa kết luận (còn 1 phiên).
- Hết **23/10** mà `regime_score < 0.40` **hoặc** `vol_score = 0.20` ⇒ **BÁC BỎ** giả thuyết "nhịp nảy
  có xác nhận dòng tiền"; ghi vào nhật ký là **B** và đóng H.
- Ghi chú mẫu: n = 2 phiên ⇒ **không** kết luận thống kê; đây là quan sát có đăng ký, không phải
  backtest và không được dùng để tinh chỉnh tham số Regime.

**Nguồn số liệu (provenance):** `backend/data/screener_cache.db` → `regime_history`
(ngày 19–21/10/2026, seed EMA đã nối liền tới 20/10 sau khi vá `is_degraded`).
