# SPEC — ABLATION STUDY cho TREND ENGINE (chẩn đoán, KHÔNG phải chiến lược mới)

**Version:** v1.0
**Trạng thái:** pre-registered · **DIAGNOSTIC ONLY** · **cấm dùng để promote**
**Ngày khóa:** 2026-10-09
**Chủ hệ thống:** chốt hướng (A) — Ablation Study, sau khi baseline FAIL.
**Anchor:** `docs/SPEC_TREND_ENGINE_2021_2025.sha256` (cùng sidecar, cùng guard)

---

## §0. Vì sao có file này (và nó KHÔNG được phép làm gì)

Baseline niêm phong `SPEC_TREND_ENGINE_2021_2025.md` v1.1.2 đã chạy thật và **FAIL**:

| Chỉ số | Baseline (đã đăng ký) | Ngưỡng thành công |
|---|---|---|
| Sharpe | **−0,250** | ≥ 0,50 |
| Calmar | **−0,101** | ≥ 0,50 |
| MaxDD | 3,24 % | ≤ 20 % |
| WinRate | 37,5 % | (không ràng buộc) |
| Tổng lệnh | **28 lượt vào / 1245 phiên** | (không ràng buộc) |

**FAIL LÀ FAIL.** Con số trên là kết quả đã đăng ký, giữ nguyên vĩnh viễn (commit `ec76611`).

Nhưng FAIL đó **chưa nói được vì sao**: hội 9 điều kiện vào lệnh khiến 1245 phiên chỉ sinh
**28 lượt vào lệnh** trên ~900 mã ⇒ chiến lược ở tiền mặt gần như toàn bộ thời gian. Với cỡ
mẫu đó, Sharpe/Calmar **là nhiễu**, không phân biệt được −0,25 với +0,25. Đo funnel cho thấy
0–1 ứng viên/ngày, siết mạnh nhất ở **organ** và **breakout**.

⇒ Câu hỏi của study này là **PHÂN RÃ NGUYÊN NHÂN**, không phải "tìm cấu hình cho ra số đẹp".

**Ba điều file này CẤM:**

1. **Cấm promote**: không biến thể nào được wire vào `final-decision`, vào live, hay thay thế
   baseline — **bất kể kết quả đẹp đến đâu** (§6 R4).
2. **Cấm dịch ngưỡng**: không tham số nào được đổi. Ablation khác baseline **đúng hai công tắc**
   (§3, §6 R5).
3. **Cấm diễn giải cửa sổ 2021–2025 như OOS**: cửa sổ này **đã bị dùng** cho baseline ⇒ mọi
   biến thể chạy trên nó là **IN-SAMPLE**. Chỉ dùng để gán tác nhân (attribution).

---

## §1. Câu hỏi nghiên cứu (chốt trước khi chạy)

| # | Câu hỏi | Biến thể trả lời |
|---|---|---|
| Q1 | Bản thân động cơ đà (RS≥90 + ADV20≥500k + breakout + volume≥1,3×) có sinh alpha ở VN không? | **V1** |
| Q2 | Phanh regime (veto CRISIS + K3) giảm MaxDD hay bóp nghẹt cơ hội? | V1 vs **V2** |
| Q3 | Lọc chất lượng (organ) tăng WinRate hay chỉ "bỏ đói" dòng tiền? | V1 vs **V3** |
| Q4 | Hai cổng có tương tác không (mỗi cổng tốt một mình nhưng tệ khi hợp lại)? | số hạng tương tác §6 |

---

## §2. Thiết kế: factorial 2×2 (KHÔNG phải "4 chiến lược khác nhau")

Hai công tắc: `organ_on` × `regime_on`. Momentum **luôn bật** ở cả 4 ô.

| Ô | `organ_on` | `regime_on` | Tên | Vai trò |
|---|---|---|---|---|
| **V1** | ✗ | ✗ | Pure Momentum | đối chứng nền |
| **V2** | ✗ | ✓ | Momentum + Regime | tác nhân của regime |
| **V3** | ✓ | ✗ | Momentum + Organ | tác nhân của organ |
| **V4** | ✓ | ✓ | **Toàn phần = baseline** | **Ô kiểm chứng harness** (§6 R1) |

Vì sao factorial: tách được **tác nhân chính** của từng cổng **và** **tương tác** giữa chúng.
So sánh từng biến thể với một baseline duy nhất chỉ cho biết "có khác", không cho biết "ai gây ra".

---

## §3. Định nghĩa hai công tắc (chính xác từng dòng)

**`regime_on = False`** (V1, V3) tắt:
- veto vào lệnh khi `regime_history.status == 'CRISIS'`;
- trigger **K3** toàn thị trường (VN-Index giảm ≥5 % trong 3 phiên) ⇒ **không thoát khẩn cấp**
  theo thị trường.

**`regime_on = True`** (V2, V4): giữ nguyên hành vi đã niêm phong (**thoát khẩn cấp khớp
Close T** theo quyết định 2026-10-09).

**`organ_on = False`** (V1, V2) tắt:
- toàn bộ `OrganFilter.evaluate(...).passed` (BS ≥ 0,30 ∧ Eff ≥ 0,60 ∧ Moat ≥ 0,50 ∧
  Health ≠ DISTRESSED).

**`organ_on = True`** (V3, V4): giữ nguyên.

**KHÔNG ĐƯỢC ĐỔI ở mọi ô** (bất biến của study):
`RS_MIN=90` · `ADV20_MIN=500000` · `VOLUME_MULT=1.3` · `BREAKOUT_LOOKBACK=20` ·
`BASE_SIZE_PCT=5` · `MAX_POSITIONS=4` · `MAX_TREND_PCT=20` · `BETA_CAP_PCT=25` ·
`DISCOUNT_POLARITY=0.5` · `TRAILING_FULL=8` · `TRAILING_PARTIAL=5` · `RS_EXIT=70` ·
`ATR_SPIKE=2.0` · `REENTRY_COOLDOWN=5` · RS theo §4.1 · Entry Open T+1 · Exit Open T+1 ·
MTM close · warm-up 280 phiên · universe động theo năm · cả hai kịch bản chi phí.

---

## §4. Số liệu phải báo cáo (cố định trước khi chạy)

Mỗi ô báo cáo **đủ** các số sau — không được chọn lọc cái đẹp:

**Nhóm "đói hay no" (chẩn đoán chính):**
`candidates` (số ngày×mã qua được công tắc vào lệnh) · `days_with_candidates` ·
`entries` · `positions` · `trades` · `time_in_market_pct` (tỉ lệ phiên có ≥1 vị thế) ·
`avg_hold_sessions` · `emergency_exits` · `partial_exits`.

**Nhóm hiệu suất:** `sharpe` · `calmar` · `maxdd_pct` · `win_rate_pct` · `profit_factor` ·
`total_return_pct` (primary) và chênh lệch so với robustness.

**Nhóm phơi nhiễm:** `avg_positions` · `max_positions_held` · `pct_days_flat`.

---

## §5. Cửa sổ chạy

`2021-01-01 → 2025-12-31` · universe động theo năm · **cùng dữ liệu đã làm sạch** của baseline
(40.347 dòng cách ly + 4.633 dòng rescale). Không refetch, không thêm mã.

---

## §6. Luật đọc kết quả (CHỐT TRƯỚC — không sửa sau khi thấy số)

**R1 — Toàn vẹn harness (điều kiện tiên quyết).**
V4 **phải tái lập baseline tới 12 chữ số có nghĩa** trên `sharpe`, `calmar`, `maxdd`, `trades`,
`entries`. Nếu lệch ⇒ **study VÔ HIỆU**, sửa harness, **không diễn giải bất cứ ô nào**. Lý do:
V4 khác baseline đúng bằng 0 công tắc; lệch nghĩa là công tắc đã lén đổi thứ khác.

**R2 — Đủ lực mẫu.**
Ô nào có **< 30 lệnh** ⇒ mọi chỉ số hiệu suất của ô đó báo **"KHÔNG ĐỦ LỰC"**, in số nhưng
**gắn nhãn không kết luận được**. Không được trích Sharpe của ô thiếu mẫu như bằng chứng.
(Đồng bộ với luật 30 mẫu độc lập của cổng hit-rate.)

**R3 — Gán tác nhân (attribution).**
- Tác nhân chính của **organ** = trung bình Sharpe ở {V3,V4} − trung bình ở {V1,V2}.
- Tác nhân chính của **regime** = trung bình Sharpe ở {V2,V4} − trung bình ở {V1,V3}.
- **Tương tác** = (V4−V3) − (V2−V1).
Báo cáo **cả ba**, kể cả khi R2 gắn cờ — nhưng khi đó phải ghi rõ **độ tin cậy thấp**.

**R4 — Không promote.**
Dù kết quả ra sao, không ô nào được đưa vào `final-decision`/live. Muốn promote một biến thể ⇒
phải có **spec mới + cửa sổ OOS MỚI** (2021–2025 đã nhiễm). Ghi vào `ENTRY_LOCK` decision log.

**R5 — Không ngưỡng nào được dịch.**
Harness **không được chứa bản sao** của bất kỳ ngưỡng nào; phải import từ module đã niêm phong.
Có test khẳng định 4 ô chỉ khác nhau bởi **đúng hai công tắc**.

**R6 — Báo cáo trung thực.**
Nếu cả 4 ô đều dưới ngưỡng, báo cáo là "động cơ đà không sinh alpha ở VN 2021–2025", KHÔNG phải
"cần tinh chỉnh thêm". Nếu một ô vượt ngưỡng, báo cáo kèm **R2** và **cấm** gọi đó là "thành công".

---

## §7. Hiện thực

- **Module**: `backend/src/backtest/ablation_arms.py` — `ArmSwitches(organ: bool, regime: bool)`.
- **Mở rộng tối thiểu** `trend_loop.simulate(..., arm: ArmSwitches = ArmSwitches(True, True))`.
  Mặc định **giữ nguyên hành vi niêm phong** ⇒ đường chạy baseline không đổi một bit.
- **Script**: `backend/scripts/run_ablation.py` — chạy 4 ô, **kiểm R1 trước**, ghi kết quả.
- **Guard**: `backend/tests/test_ablation_arms.py` — (a) R1 tái lập baseline; (b) 4 ô chỉ khác
  hai công tắc; (c) không có ngưỡng sao chép trong harness (AST scan).

**Đầu ra (thư mục RIÊNG, không ghi đè kết quả niêm phong):**
`backend/reports/backtest/ablation/ablation_summary.json` · `ablation_arms.csv`

---

## §8. Ngoài phạm vi (ghi rõ để không trôi)

KHÔNG: thêm chỉ báo mới · tìm tham số · tối ưu tỉ trọng · đổi universe · đổi chi phí · thêm mã
mới · đổi định nghĩa RS · đổi timing khớp lệnh · bỏ warm-up · "thử nhanh vài ngưỡng".

---

## §9. Anchor & changelog

Hash: `docs/SPEC_TREND_ENGINE_2021_2025.sha256` (dòng anchor cho file này).
Anchor được xác minh bởi **chính guard đã có**: `check_anchors()` duyệt mọi dòng của sidecar,
nên file này tự động được bảo vệ bởi pre-commit hook + preflight của runner + test suite.

| Phiên bản | Nội dung |
|---|---|
| v1.0 | Bản gốc. Chốt trước khi chạy bất kỳ ô nào. |
