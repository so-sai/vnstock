# SPEC — TREND ENGINE V2 · MOMENTUM + REGIME · KIỂM ĐỊNH OUT-OF-SAMPLE (2026)

**Version:** v1.0
**Trạng thái:** pre-registered · **CỬA SỔ OOS DÙNG MỘT LẦN (one-shot)**
**Ngày khóa:** 2026-10-14
**Anchor:** `docs/SPEC_TREND_ENGINE_2021_2025.sha256` (cùng sidecar, cùng guard)
**Kế thừa:** `docs/SPEC_TREND_ENGINE_2021_2025.md` v1.1.2 (engine đã niêm phong) và
`docs/SPEC_TREND_ENGINE_ABLATION_STUDY.md` v1.1.1 (bằng chứng chọn ứng viên).

---

## §0. Mục đích và LUẬT MỘT LẦN

Ablation PIT-clean để lại **hai ứng viên** và **một bản án**:

| | PIT-clean 2021–2025 | |
|---|---|---|
| **V2** Momentum + Regime | Sharpe **+0,4378** · MaxDD 11,05 % · 173 lệnh | ứng viên đối chứng |
| **V5** V2 + `Health ≠ DISTRESSED` | Sharpe **+0,4058** · MaxDD **10,05 %** · 158 lệnh | **ứng viên chính** (R7 = A) |
| V4 baseline (3 cổng organ khắt khe) | Sharpe **−0,2542** · 39 lệnh | **FAIL confirmed** |

Cả hai đều **dưới ngưỡng 0,50** và đều **in-sample** (2021–2025 đã dùng để chọn). Nên câu hỏi
duy nhất còn lại là: **chúng có sống được trên dữ liệu chưa từng nhìn thấy không?**

**LUẬT MỘT LẦN (bất khả xâm phạm):**
1. Cửa sổ OOS được **đọc đúng MỘT lần**. Sau khi thấy số: **cấm** chạy lại, cấm đổi ngưỡng,
   cấm đổi universe, cấm đổi cách tính, cấm lọc bớt mã, cấm thêm biến thể.
2. **Cấm promote** vào live trên cơ sở của file này. Tệ nhất là "đủ tư cách vào paper-trade".
3. Nếu phát hiện **lỗi PIT/thiếu dữ liệu** trong lúc chạy: dừng, sửa, **tuyên bố lại**, và ghi
   rõ trong changelog rằng cửa sổ đã bị chạm. Lỗi ở tầng dữ liệu thì sửa; **không** sửa luật.
4. Nếu kết quả FAIL: **FAIL là FAIL**, báo cáo thẳng, không "thử thêm một cấu hình nữa".

---

## §1. Hai ứng viên (chốt cứng)

| | Định nghĩa | Vai trò |
|---|---|---|
| **C1 (chính)** | Momentum (RS≥90 ∧ ADV20≥500k ∧ breakout 20 phiên ∧ volume≥1,3×ADV20) **+ Regime overlay** (veto CRISIS + K3) **+ cổng `Health ≠ DISTRESSED`** | Ứng viên đề xuất |
| **C2 (đối chứng)** | Như C1 nhưng **bỏ hẳn** cổng health | Đo giá phải trả của tấm khiên |

Cả hai dùng **nguyên** `trend_engine` · `trend_sizing` · `trend_circuit_breaker` ·
`trend_organ_filter`. **Không sửa một dòng** trong các module đó.

---

## §2. Cửa sổ OOS và vì sao nó sạch

**Cửa sổ:** `2026-01-01 → 2026-10-09` (≈ 195 phiên).

**Vì sao sạch:** toàn bộ việc chọn ứng viên, chọn công tắc và chẩn đoán đều làm trên 2021–2025.
2026 chưa từng được dùng để chọn bất cứ thứ gì.

**Khuyai báo trung thực về dữ liệu 2026** (để người đọc biết mức tin cậy):
- Dữ liệu 2026 **đã được làm sạch** (cách ly 40.347 dòng OHLC không hợp lệ; rescale 4.633 dòng
  lệch thang vci). Việc làm sạch dựa trên **so sánh nhà cung cấp và bất biến nội tại**, không
  dựa trên kết quả đầu tư — nhưng nó được thực hiện **sau khi đã biết kết quả 2021–2025**, nên
  không phải "mù hoàn toàn".
- HDB 09/10/2026 được nạp qua **miễn trừ sự kiện quyền** (chia thưởng ~30 %, hệ số 1,2993).
- VNINDEX được nạp bù 06→09/10/2026 (trước đó đường cron **không bao giờ** nạp chỉ số).
- **PIT bắt buộc:** `get_pit_quarter` (Q1–Q3 ≥ 60 ngày, Q4 ≥ 120 ngày). BCTC dùng cho tháng
  10/2026 nhiều nhất là **Q2/2026** (Q3 chưa qua 60 ngày).

---

## §3. Universe — SỬA ĐỔI CÓ KHAI BÁO (điểm khác duy nhất về luật)

Spec cũ định nghĩa universe theo **cả năm dương lịch**: "mã có ≥ 200 phiên sạch trong năm".

**Vấn đề với cửa sổ OOS:** 2026 mới có ~195 phiên ⇒ **không mã nào đạt 200 phiên trong năm**
⇒ universe **RỖNG** ⇒ 0 lệnh ⇒ phép kiểm vô nghĩa. (Không được "nới" ngầm.)

**Luật thay thế (khai báo trước):**

> Universe tại ngày T = mã có **≥ 200 phiên sạch trong 250 phiên gần nhất tính đến T**.

Vì sao luật này **tốt hơn** (không chỉ để chạy được):
- Nó **PIT đúng** — luật cũ lấy cả năm dương lịch, tức universe của phiên tháng 1 được quyết
  bởi dữ liệu tới tháng 12 (một dạng nhìn trước nhẹ, tôi đã ghi nhận ở ablation).
- Nó **độc lập độ dài cửa sổ** ⇒ dùng được cho cả 2021–2025 lẫn cửa sổ ngắn.
- Cùng một ngưỡng 200 phiên, chỉ đổi **cách đếm** (cửa sổ trượt thay vì năm dương lịch).

**Điều KHÔNG được đổi:** ngưỡng 200 · ADV20 ≥ 500k · RS ≥ 90 · breakout 20 · volume 1,3× ·
BASE 5 % · MAX_POSITIONS 4 · trần 20 % · beta cap 25 % · trailing 8/5 · RS exit 70 ·
ATR spike 2,0 · cooldown 5 · Entry Open T+1 · Exit Open T+1 · khẩn cấp Close T.

---

## §4. Cách chạy

`python backend/scripts/run_oos_2026.py` — chạy C1 và C2, **cả hai kịch bản chi phí**
(0,50 % spec · 0,60 % robustness). Warm-up 280 phiên trước 2026-01-01 (từ 2024-12) để RS có
giá trị ngay phiên đầu cửa sổ. **Không ghi DB.**

**Đầu ra** (thư mục riêng, không ghi đè kết quả nào khác):
`backend/reports/backtest/oos_2026/oos_summary.json` · `oos_arms.csv`

**Số phải báo cáo cho mỗi ứng viên:** Sharpe · Calmar · MaxDD · số lệnh · số trade · WinRate ·
PF · % phiên trống · số phiên · số ứng viên bị cổng health chặn · cả hai kịch bản chi phí.

---

## §5. Tiêu chí đạt (chốt TRƯỚC khi chạy)

$$\text{Sharpe} \ge 0{,}50 \;\wedge\; \text{MaxDD} \le 12\% \;\wedge\; \text{số lệnh} \ge 25$$

Cả hai kịch bản chi phí đều phải đạt (kịch bản robustness là điều kiện, không phải tham khảo).

| Kết quả | Phán quyết định trước |
|---|---|
| **C1 đạt, C2 đạt** | Cổng health **đáng giữ** ⇒ đề xuất `ENTRY_LOCK` mở ở mức **paper-trade 1 tháng**, không tiền thật |
| **C1 đạt, C2 không** | Health là yếu tố quyết định ⇒ paper-trade **C1** |
| **C2 đạt, C1 không** | Cổng health **là vật cản** ⇒ bỏ hẳn health; paper-trade **C2** |
| **Cả hai không đạt** | **Framework không vượt qua OOS** ⇒ không promote, dừng nhánh này, viết báo cáo FAIL |

---

## §6. Giới hạn đã biết — khai báo trước, không phải biện minh sau

1. **Mẫu MỎNG và đây là giới hạn nghiêm trọng nhất.** Với tốc độ vào lệnh đã đo (~0,14 lệnh/phiên),
   ~195 phiên cho **khoảng 25–30 lệnh** — đúng sát ngưỡng 25. Ta đã đo được ở ablation rằng với
   cỡ mẫu này khoảng tin cậy của Sharpe **rộng**.
   ⇒ **Hệ quả khai báo trước:** phép kiểm này **kết luận chắc được cái FAIL** (dưới ngưỡng là dưới
   ngưỡng), nhưng **một PASS là bằng chứng YẾU**, chỉ đủ để vào paper-trade — **không** đủ để gọi
   là "alpha đã được xác nhận".
2. **Ngưỡng MaxDD ≤ 12 %** chặt hơn 20 % của spec cũ (theo yêu cầu chủ hệ thống). Đây là tiêu chí
   **mới của file này**; nó không sửa tiêu chí cũ.
3. **Một thị trường, một cửa sổ.** Không suy rộng ra ngoài 2026 hay ra thị trường khác.
4. **Đường đi phụ thuộc (path dependence)** đã quan sát được: kết quả bị ảnh hưởng bởi mã nào
   lấp chỗ trống. Vì vậy mọi kết luận phải kèm số lệnh.

---

## §7. Bất biến phải giữ

- `get_pit_quarter` **bắt buộc** trên mọi truy vấn tài chính có ngày (không nhìn trước BCTC).
- Warm-up ≥ 280 phiên trước mốc đầu cửa sổ.
- Không tham số nào được đọc từ file này — mọi ngưỡng **import** từ module đã niêm phong.
- Không có ngưỡng sao chép trong harness (kiểm bằng AST như `test_ablation_arms.py`).

---

## §8. Anchor & changelog

Hash trong `docs/SPEC_TREND_ENGINE_2021_2025.sha256` (dòng anchor cho file này), được xác minh
bởi chính guard đã có (`check_anchors()` duyệt mọi dòng của sidecar).

| Phiên bản | Nội dung |
|---|---|
| v1.0 | Bản gốc. Chốt trước khi chạy. Cửa sổ 2026 dùng một lần. |
