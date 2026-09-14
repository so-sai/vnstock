# Gate 8: Temporal Slot Allocation (Cadence Pacing)

> **Ngày đóng:** 2026-08-16 | **Trạng thái:** GATE 8 = **CONDITIONAL PASS** (pacing chứng minh front-loading là thủ phạm năm 2023; nhưng KHÔNG productionize ngay — xem Đọc kết quả)
> **Script:** `backend/src/research/opportunity_score_gate8.py` (READ-ONLY)
> **Engine:** `opportunity_score_gate6.py::simulate(..., pacing=...)` — mở rộng backward-compatible, `pacing=None` = hành vi Gate 6/7 (regression verified).

## Verdict (1 câu)

**Pacing chữa được front-loading: `min_spacing=5` đưa 2023 từ −2.92% về ~0% và IS
tăng +1.89pp (IS sharpe 0.06→0.21) — xác nhận năm âm 2023 là TIMING/BUDGET, không
phải feature. Nhưng chọn pacing trên IS = chọn 1 trong 6 config → chỉ được coi là
giả thuyết policy, cần paper-trading để kiểm định (không productionize trực tiếp).**

```
3 BOUNDARY INVARIANTS (giữ nguyên):
  #1 Budget 20 = TRẦN (không nới, không ép dùng hết) — mọi policy vẫn dùng ≤20/năm.
  #2 So sánh/chọn pacing CHỈ trên IS 2022-2024; 2025 descriptive.
  #3 PIT: mọi quyết định chỉ dựa trên info tại t ≤ as_of_date.
```

## 1. Bảng kết quả (cost 0.45%, cap=1, budget=20, hold=20, dedup)

| policy | netR20% | **IS%** | OOS% | win% | PF | IS_sh | NAV% | MaxDD% | n |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0_baseline | +1.31 | +0.50 | +3.75 | 61.3 | 1.60 | 0.06 | +5.60 | −14.17 | 80 |
| 1_quarter_cap_5 | +0.05 | +0.07 | −0.03 | 48.8 | 1.01 | 0.01 | −18.03 | −35.93 | 80 |
| 2_spacing_5 | **+2.00** | **+2.39** | +0.84 | 56.2 | 1.69 | 0.21 | +24.13 | −26.60 | 80 |
| 2b_spacing_10 | −0.31 | +0.09 | −1.52 | 51.2 | 0.91 | 0.01 | −33.05 | −51.46 | 80 |
| 3_hurdle_1.60_1.55 | +1.31 | +0.50 | +3.75 | 61.3 | 1.60 | 0.06 | +5.60 | −14.17 | 80 |
| 4_regime_ranging2 | +0.94 | +1.40 | −0.44 | 43.8 | 1.35 | 0.13 | +3.58 | −22.20 | 80 |

Per-year Net R20 (IS years in bold):

| policy | 2022 | **2023** | **2024** | 2025(OOS) |
|---|---:|---:|---:|---:|
| 0_baseline | +0.02 | **−0.03** | +0.03 | +0.04 |
| 2_spacing_5 | +0.02 | **−0.00** | +0.05 | +0.01 |

## 2. Temporal dispersion (slot theo quý, first→last doy)

| policy | first→last doy (2022..2025) | Q1 | Q2 | Q3 | Q4 |
|---|---|---|---|---|---|
| 0_baseline | 4→38, 3→37, 2→29, 2→36 | 20 | 0 | 0 | 0 |
| 1_quarter_cap_5 | 4→280, ... | 5 | 5 | 5 | 5 |
| 2_spacing_5 | 4→147, ... | 12 | 8 | 0 | 0 |
| 2b_spacing_10 | 4→284, ... | 6 | 6-7 | 6-7 | 1 |
| 4_regime_ranging2 | 4→73, 3→101, ... | 15-20 | 3-5 | 0 | 0 |

- **Baseline tiêu hết 20 slot trong Q1 (last_doy ≤38)** — hiện tượng front-loading.
- **spacing_5 kéo việc giải ngân trải sang Q2 (12/8), last_doy ~147** → bắt được alpha
  xuất hiện nửa đầu năm thay vì dồn hết tháng 1-2.
- **quarter_cap/spacing_10 trải SANG cả Q3/Q4 nhưng PHÁ hủy alpha** (IS +0.07%/+0.09%):
  cơ hội value Q3/Q4 yếu hơn hẳn → trải quá tay không có ý nghĩa, chỉ thêm friction.

## 3. So sánh trên IS (bất biến #2)

| policy | IS Δ | 2023 Δ | MaxDD | NAV |
|---|---:|---:|---:|---:|
| 2_spacing_5 | **+1.89pp** | **+0.03** (~0% vs −2.92%) | −26.60% | +24.13% |
| 4_regime_ranging2 | +0.90pp | +0.00 | −22.20% | +3.58% |
| 1_quarter_cap_5 | −0.43pp | +0.01 | −35.93% | −18.03% |
| 2b_spacing_10 | −0.41pp | +0.02 | −51.46% | −33.05% |
| 3_hurdle | +0.00 | +0.00 | −14.17% | +5.60% |

## 4. Ba phát hiện quan trọng

1. **Front-loading LÀ nguyên nhân năm âm 2023 (xác nhận, không còn nghi ngờ).**
   spacing_5 đưa 2023 từ −2.92% về ~0% trong khi giữ win >50% và PF >1.6 trên IS.
   Vẫn chưa chạm Oracle +1.85% vì Oracle không có budget (giới hạn 20 slot là
   ceiling tuyệt đối, spacing không nới). Phần chênh còn lại là giá của constraint.

2. **Hurdle M_value 1.60→1.55 KHÔNG BINDING** (kết quả trùng baseline 100%).
   Top-1 M_value hằng ngày của pool luôn ≥1.60 nên ngưỡng này không bao giờ chặn.
   Lưu ý: spec gốc dùng p_gain 0.60/0.55 — p_gain bị CẤM trong selection từ Gate 5,
   nên đã map sang M_value (M_value = 1+mean(pct_rank), range [1,2]). Ở mức map này
   cơ chế là no-op; muốn nó "cắn" phải nâng ngưỡng (~1.85+) = tham số tự chọn theo IS
   → NGUY CƠ overfit, KHÔNG làm ở đây.

3. **Spacing quá dài (10 ngày) và quarterly cap là phản tác dụng.**
   Trải slot sang Q3/Q4 (nơi alpha value yếu) làm IS giảm ~0.4-0.9pp, MaxDD sâu gấp
   2-3×. Pacing hữu ích nhất là "dãn vừa phải trong H1", không phải "trải đều cả năm".

## 5. Rủi ro / giới hạn (đọc trước khi quyết định)

- **spacing_5 được CHỌN từ IS (1 trong 6 config)** → đây là giả thuyết policy, chưa
  phải chứng minh ngoài mẫu. OOS 2025 descriptive: spacing_5 = +0.84% < baseline
  +3.75% → KHÔNG được dùng OOS để giữ/kill (bất biến #2), nhưng là tín hiệu thận
  trọng về độ ổn định.
- **MaxDD spacing_5 = −26.6% (baseline −14.2%)** — trải giải ngân làm tăng thời gian
  tiếp xúc thị trường → DD sâu hơn. NAV tổng cao hơn (+24% vs +5.6%) nhưng đánh đổi.
- **Số tham số pacing tăng (spacing days)** — đừng để thành quy tắc cần tinh chỉnh
  mỗi năm; nếu giữ, phải paper-trade qua nhiều chu kỳ regime.
- **B vẫn là replication machinery** (synthesized candidates) — pacing kiểm định
  trên cùng machinery đó, chưa qua Governor DB thật.
- **PIT giữ nguyên**: pacing chỉ dùng ngày hiện tại + luật cố định, không nhìn tương lai.

## Guardrail (không thay đổi)

- Budget 20 = ceiling, không quota; không ablation budget 10/20/40/unlimited.
- daily_cap=1 giữ nguyên; p_gain vẫn diagnostic-only.
- KHÔNG dùng 2025 để fit/cut pacing; KHÔNG dùng +147% làm benchmark.
- Nếu mở rộng pacing thành sản phẩm → phải vào `decision_budget.py` + paper-trading,
  KHÔNG productionize trực tiếp từ script research.

## Hướng tiếp theo

1. **Kiểm định pacing qua paper-trading** (đúng AGENTS.md: OOS 2025-2026 đã nhiễm):
   chạy `spacing_5` trên Governor DB qua nhiều chu kỳ regime trước khi nhận là policy.
2. **Sensitivity pacing spacing ∈ {3, 5, 7}** chỉ trên IS (nếu muốn dò điểm rơi) —
   nhưng cảnh giác overfit; spacing_5 là khoan dung nhất trong vùng "dãn vừa H1".
3. **Replay Governor thật với M_value rank** → Evidence Ledger thật + pacing.
4. Nếu sau paper-trading vẫn không ổn → quay lại câu hỏi budget 20 (đã đóng, không mở).