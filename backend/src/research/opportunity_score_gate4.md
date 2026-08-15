# Gate 4: Walk-forward Stability + Incremental Discrimination (M_value vs M_cluster)

> **Ngày đóng:** 2026-08-15 | **Trạng thái:** GATE 4 = **KHÔNG có incremental** — giữ M_value
> **Script:** `backend/src/research/opportunity_score_gate4.py` (READ-ONLY)
> **Output:** `backend/data/reports/opportunity_score_gate4_audit_all.csv`

## Verdict (1 câu)

**Cluster-composite KHÔNG vượt M_value — ROE không có information conditional on
valuation; ret_120d có signal yếu nhưng không đủ để bù độ nhiễu. Giữ M_value.**

```
Gate 1 (p_gain FAIL) → Gate 2 (valuation PASS) → Gate 3A (M_value PASS)
        → Gate 4 = KẾT LUẬN: M_cluster KHÔNG incremental
            ├── M_value  H20 IC +0.088 (t=15.0)   ← baseline giữ nguyên
            ├── M_cluster H20 IC +0.063 (t=10.3)  ← composite thấp hơn
            ├── ROE   residual IC −0.007 (control val)  → redundant
            ├── ret_120d residual IC +0.040 (control val) → yếu, không đủ
            └── → tiếp: Gate 5 với M_value (valuation-only)
```

## 1. M_value vs M_cluster (cùng gate)

| Horizon | M_value IC | t | M_cluster IC | t | topDecile M_value | M_cluster |
|---|---:|---:|---:|---:|---:|---:|
| H20 | **+0.088** | 15.0 | +0.063 | 10.3 | +3.87% | +2.30% |
| H60 | **+0.120** | 20.9 | +0.089 | 15.6 | +9.99% | +5.11% |
| H120 | **+0.203** | 38.2 | +0.113 | 19.3 | +20.16% | +7.52% |

- **M_value thắng toàn diện.** Thêm ROE + ret_120d vào composite làm score YẾU
  hơn ở mọi horizon (dilution: valuation là tín hiệu chính, feature phụ là noise).
- Per-year H20: M_value 2022 +0.115 / 2023 +0.037 / 2024 +0.047 / 2025 +0.152
  vs M_cluster +0.058 / +0.048 / +0.083 / +0.063 → M_value nhạy hơn 2022/2025,
  M_cluster "đều" hơn nhưng thấp hơn ở các năm mạnh.
- Per-regime H20: M_value CRISIS +0.093 / RANGING +0.111 / TRENDING +0.042 —
  valuation mạnh nhất ở RANGING, yếu nhất TRENDING (vẫn dương).

## 2. Incremental double-sort (control valuation) — câu hỏi chính của Gate 4

RankIC của feature TRONG TỪNG valuation quintile (nội ngày). Nếu ≈ 0 →
feature không thêm gì conditional on valuation.

| Feature | H20 residual IC | pos% | H120 residual IC | pos% | Kết luận |
|---|---:|---:|---:|---:|---|
| ROE_z_ts | **−0.007** | 48 | −0.040 | 45 | **REDUNDANT** — nghịch valuation, không thêm info |
| ret_120d | **+0.040** | 55 | +0.046 | 59 | signal yếu, có thật nhưng nhỏ |

- **ROE KHÔNG có incremental discrimination.** Residual IC âm/sấp xỉ 0 trong mọi
  quintile (q1 −0.020 → q5 +0.015). Giải thích: PE↔ROE corr −0.69 (Gate 3A
  redundancy) — ROE đã nằm TRONG valuation (rẻ = ROE thấp). Đếm thêm = double count.
- **ret_120d có residual dương** (+0.040) nhưng phụ thuộc quintile (q2 +0.004,
  q3 +0.069, q5 +0.091) và không đủ để composite thắng M_value.

## 3. Walk-forward expanding (H20)

| test | train | M_value IC | t | M_cluster IC | t |
|---|---|---:|---:|---:|---:|
| 2023 | 2022 | +0.037 | 3.0 | +0.048 | 4.2 |
| 2024 | 2022+23 | +0.047 | 4.5 | +0.083 | 10.3 |
| 2025* | 2022+23+24 | +0.152 | 13.5 | +0.063 | 6.2 |

*2025 = descriptive (ĐÃ NHIỄM). M_cluster thắng 2024, M_value thắng 2025 rõ;
không có verdict ổn định — càng củng cố "giữ M_value, đừng đánh đổi độ ổn định
để lấy vài điểm IS".

## Đọc kết quả

1. **Chống overfitting thành công:** kết quả Gate 4 làm đúng điều guardrail yêu
   cầu — KHÔNG thêm feature chỉ vì "có signal". M_cluster làm score tệ hơn.
2. **ROE bị loại vì redundancy:** đã nằm trong valuation (−0.69 corr). Thêm nó
   như evidence độc lập = đếm cùng latent 2 lần → nhiễu, không phải alpha.
3. **ret_120d có tiềm năng nhỏ** (residual +0.04 H20) — không đủ đưa vào score
   ngay; nếu muốn thử: chỉ ở mức weight rất nhỏ, kiểm tra lại sau Gate 5 với
   selected/rejected, không thay thế valuation.
4. **M_value là model Gate 5:** đơn giản, robust, không fit weight, IC ổn định
   dương mọi năm/mọi regime IS.

## Guardrail

- Score-level evidence, chưa phải trading alpha. Chưa áp budget/Governor.
- 2025 contaminated → walk-forward 2025 chỉ descriptive.
- KHÔNG thêm ROE vào score (redundant). Nếu thử ret_120d → weight nhỏ + re-audit.
- Tiếp Gate 5: top-K selection vs rejected dùng M_value.

## Hướng tiếp theo

1. **Gate 5 — Selection:** top-K nội ngày từ M_value, so selected vs rejected
   (win-rate, mean R20, spread), có transaction/capacity/risk check; budget ≤20
   chỉ sau khi Gate 5 PASS.
2. Optional: test ret_120d ở weight nhỏ (5-10%) như thí nghiệm riêng, KHÔNG
   default vào score.
