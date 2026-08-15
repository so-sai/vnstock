# Gate 2: Feature-level Discrimination Audit (Opportunity Score v0.1 — step 1)

> **Ngày đóng:** 2026-08-15 | **Trạng thái:** GATE 2 = PASS (valuation cluster) / candidate pool hoàn tất
> **Script:** `backend/src/research/opportunity_score_research.py` (READ-ONLY, `replay_58_ts`)
> **Output:** `backend/data/reports/opportunity_feature_audit_all.csv`

## Tóm tắt kết luận

**Valuation cluster là tín hiệu cross-sectional ổn định và mạnh nhất trong 4 năm 2022–2025.**
Momentum 120d và ROE z-score hỗ trợ nhưng yếu hơn. p_gain không đủ điều kiện làm feature.

```
Gate 1 (p_gain FAIL) → Gate 2 (feature-level alpha) = PASS cho valuation
        ├── PE_z_ts / PE_pct / PB_z_ts: |RankIC| 0.086–0.101, t-stat −14..−16
        ├── val_zone_ts: RankIC +0.083, sign_stability 100%, mono +0.253
        ├── ổn định mọi năm (2022→2025) và mọi regime
        └── → tiếp: Gate 3 (cross-sectional score) với valuation trọng số cao
```

## Phương pháp

- **Pool:** toàn bộ decision_ledger `replay_58_{2022..2025}.db` (57 symbols × 997 ngày, 56829 rows).
- **Target:** R(t→t+H), H = 20/60/120 (trading-day shift).
- **PIT strict:** valuation `period_key < quý(date)`; volume_profile `date <= t`; features chỉ dùng giá ≤ t.
- **Metric:** daily RankIC (Spearman per-day, ≥15 symbols/ngày) → pooled / t-stat / %pos / sign-stability / top-decile spread / monotonic bucket nội ngày.
- **Sanity check:** p_gain H20 pooled RankIC = **+0.0082** ≈ baseline 147afdd (+0.008) ✔ tái lập.

## Kết quả H20 (cross-sectional, nội ngày)

| Cluster | Feature | pooled IC | t-stat | %pos | signStab | topDecile% | mono |
|---|---:|---:|---:|---:|---:|---:|
| Valuation | PE_z_ts | **−0.101** | −15.6 | 32.9 | **100%** | −4.20 | −0.237 |
| Valuation | PE_pct | **−0.099** | −14.9 | 33.1 | **100%** | −4.16 | −0.234 |
| Valuation | PB_z_ts | **−0.086** | −14.3 | 31.4 | **100%** | −3.50 | −0.267 |
| Valuation | val_zone_ts | **+0.083** | +15.6 | 68.4 | **100%** | +3.51 | +0.253 |
| Valuation | PS_z_ts | −0.071 | −11.7 | 36.7 | 75% | −3.96 | −0.215 |
| Valuation | EV/EBITDA_z_ts | −0.064 | −10.0 | 37.5 | 75% | −2.69 | −0.173 |
| Quality | ROE_z_ts | +0.040 | +7.7 | 58.3 | **100%** | +1.65 | +0.098 |
| Market | ret_120d | +0.039 | +4.4 | 59.6 | **100%** | +1.95 | +0.130 |
| Market | dd_120d | +0.027 | +3.0 | 57.7 | 75% | +0.77 | +0.082 |
| Evidence(diag) | p_gain | +0.008 | +1.4 | 52.0 | 50% | +0.44 | +0.001 |
| Evidence(diag) | eu / kelly / quality / evidence | | ≤3.3 | | 50% | ≈0 | ≈0/− |

### H60 / H120 (chỉ ghi chú)

- val_zone_ts tăng dần: H60 +0.111 / H120 +0.166 — **monotonic theo horizon** (hiệu ứng value ngày càng rõ).
- PE_z_ts tương tự (H60 −0.12 / H120 −0.15). ret_120d H60 +0.046 / H120 +0.036.
- p_gain H120 +0.030 (55.7% pos) — cải thiện ở H120 nhưng vẫn dưới valuation và không ổn định năm.

## Đọc kết quả

1. **Valuation PASS:** 5/6 feature valuation có |t-stat| > 10, sign_stability 75–100%, top-decile spread lớn, monotonic nội ngày rõ. Đây là tín hiệu phân biệt mã tốt/xấu trong cùng ngày, không phải timing artifact.
2. **Cross-section, không timing:** đây là per-day RankIC — mỗi ngày rank 57 mã rồi đối chiếu return. Kết quả không bị trộn timing như p_gain.
3. **p_gain = loại:** RankIC +0.008, sign_stability 50%, spread 0.44%, mono 0.001 — không phân biệt được stock trong ngày (xác nhận Gate 1).
4. **ROE & momentum phụ trợ:** yếu hơn valuation nhưng đúng hướng; chỉ dùng ở mức weight nhỏ.
5. **Regime:** valuation IC dương nhất quán ở cả CRISIS/RANGING/TRENDING (vd val_zone_ts: +0.074/+0.100/+0.064 H20) — không phụ thuộc regime.

## Hướng tiếp theo

1. **Gate 3 — Cross-sectional score:** chuẩn hóa nội ngày z=(x−μ_t)/σ_t, trọng số tối thiểu cho valuation (PE_z_ts, PB_z_ts, val_zone_ts), ROE & ret_120d weight nhỏ; equal-weight/rank-average baseline trước, IC-weight w_k = IC_k/Σ|IC_k| sau (chỉ ước lượng trên training window rồi freeze).
2. **Gate 4 — Walk-forward stability** trên score tổng hợp.
3. **Gate 5 — Selection + budget ≤20**: top-K nội ngày, đối chiếu selected vs rejected.
4. **Gate 6 — Governor integration** (chỉ sau khi score qua discrimination gate).
5. **Không đưa p_gain vào score.** Nếu muốn kiểm tra incremental info của p_gain → thí nghiệm riêng conditional on score.