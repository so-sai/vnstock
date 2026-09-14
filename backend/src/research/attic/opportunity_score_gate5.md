# Gate 5: Selection Alpha — Top-K vs Rejected (M_value)

> **Ngày đóng:** 2026-08-15 | **Trạng thái:** GATE 5 = **PASS** — top-K beat rest
> **Script:** `backend/src/research/opportunity_score_gate5.py` (READ-ONLY)
> **Output:** `backend/data/reports/opportunity_score_gate5_audit_all.csv`

## Verdict (1 câu)

**Top-K theo M_value thực sự tốt hơn phần còn lại trong cùng ngày → valuation
chuyển từ feature alpha thành SELECTION alpha. p_gain tiếp tục âm.**

```
Gate 1 (p_gain FAIL) → Gate 2 (valuation PASS) → Gate 3A (M_value PASS)
→ Gate 4 (cluster KHÔNG incremental) → Gate 5 = PASS
        └── M_value_all K=5: spread +0.024 (random floor +0.000)
        └── M_value_dedup ≈ M_value_all → representation robust
        └── p_gain: spread ÂM (−0.004) → failed control
        └── → tiếp: Gate 6 Governor integration (chưa áp budget ≤20)
```

## 1. Top-K vs rejected (H20, nội ngày, không budget)

| model | K | spread% | %ngày+ | winTop% | PF | IS(22-24) | OOS25* |
|---|---:|---:|---:|---:|---:|---:|---:|
| **M_value_all** | 1 | **+0.031** | 53 | 59.9 | 2.78 | +0.020 | +0.065 |
| **M_value_all** | 3 | **+0.032** | 60 | 59.2 | 3.22 | +0.018 | +0.074 |
| **M_value_all** | 5 | **+0.024** | 63 | 57.0 | 3.15 | +0.015 | +0.050 |
| **M_value_all** | 10 | +0.017 | 60 | 55.8 | 2.55 | +0.014 | +0.028 |
| M_value_dedup | 1/3/5/10 | ≈ M_value_all (±0.003) | | | | | |
| M_p_gain | 1–10 | **−0.006…−0.002** | 40-44 | <51 | 1.2-1.6 | ~0 | **âm** |
| random | 1–10 | −0.000…−0.001 | — | 52 | — | — | — |

*OOS 2025 = descriptive (ĐÃ NHIỄM).

- **K=3 là sweet spot**: spread +0.032 (cao nhất), PF 3.22, IS/OOS đều dương.
- **M_value_dedup ≈ M_value_all** (lệch ≤0.003 mọi K): loại PE_pct (redundant)
  không làm mất signal → **representation robust**, không phụ thuộc chi tiết feature.
- **p_gain KHÔNG chọn được stock**: spread âm mọi K → xác nhận Gate 1 triệt để.
- **Δ vs random = +0.024 (K=5)** — vượt noise floor rõ ràng.

## 2. Theo horizon (M_value_all, K=5)

| H | spread% | %ngày+ | top% | rest% | PF | IS | OOS25* |
|---|---:|---:|---:|---:|---:|---:|---:|
| 20 | +0.024 | 63 | +3.27 | +0.88 | 3.15 | +0.015 | +0.050 |
| 60 | +0.074 | 63 | +9.68 | +2.27 | 6.75 | +0.034 | +0.194 |
| 120 | +0.140 | 67 | +18.86 | +4.82 | 11.30 | +0.057 | +0.392 |

- Spread tăng mạnh theo horizon (0.024 → 0.074 → 0.140): nhất quán với mean
  reversion của mispricing (Gate 3A đã thấy value effect rõ dần).
- PF cao ở H60/H120 (6.8/11.3) nhưng PHẢI cẩn trọng: forward return H60/H120
  overlap → cần kiểm tra autocorrelation trước khi diễn giải kinh tế.

## 3. Year / regime (H20, K=5)

- per_year spread: 2022 +0.020 / 2023 +0.014 / 2024 +0.013 / 2025 +0.035 —
  **dương mọi năm**, 2023-24 yếu hơn nhưng không âm.
- per_regime (từ Gate 4): M_value IC CRISIS +0.093 / RANGING +0.111 /
  TRENDING +0.042 — mạnh nhất RANGING, yếu nhất TRENDING (vẫn dương).

## Đọc kết quả

1. **Selection alpha CÓ:** câu hỏi "K mã tốt nhất có tốt hơn phần còn lại không"
   → YES với Δ rõ ràng so với random và p_gain.
2. **Robust representation:** M_value_dedup ≈ M_value_all → không cần dè dặt về
   việc chọn feature valuation nào; available-feature normalization hoạt động.
3. **p_gain xác nhận fail cuối cùng:** ở mọi K, spread âm — không được phép vào
   Selection Layer như feature chính.
4. **K=3–5 hợp lý cho thí nghiệm portfolio** (không phải khuyến nghị deployment):
   spread cao + win rate ổn định, đủ nhỏ để kiểm tra capacity/transaction sau.

## Guardrail

- Đây là **selection-level evidence**, chưa phải portfolio return đã trừ cost.
- Chưa áp budget ≤20 / threshold / Governor.
- H60/H120 spread cao → kiểm tra overlap/autocorrelation trước khi diễn giải.
- KHÔNG dùng +147% Selection v0 làm benchmark (non-attributable, checkpoint 147afdd).
- Budget chỉ được thêm sau khi Gate 5 PASS và trước khi hỏi "capacity/transaction
  có ăn hết alpha không" — không đổ lỗi budget nếu top-K không beat rest (đã PASS).

## Hướng tiếp theo

1. **Gate 6 — Governor integration (thí nghiệm)**: nối M_value (top-K ≤ 3-5) vào
   Selection Layer như rank function THAY p_gain, giữ budget ≤20 làm ràng buộc
   vật lý, kiểm tra portfolio return sau transaction cost; vẫn READ-ONLY ở mức
   research — chưa đổi config production.
2. Trước Gate 6: kiểm tra overlap H60/H120 và transaction cost ước tính.
