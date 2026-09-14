# Gate 3A: Score Construction — M_value / M_rankavg / M_IC walk-forward

> **Ngày đóng:** 2026-08-15 | **Trạng thái:** GATE 3A = PASS (valuation score alpha) + redundancy triage
> **Script:** `backend/src/research/opportunity_score_gate3.py` (READ-ONLY)
> **Output:** `backend/data/reports/opportunity_score_gate3_audit_all.csv`, `opportunity_redundancy_audit_all.csv`

## Tóm tắt kết luận

**M_value (valuation-only score) có cross-sectional alpha đáng kể và ổn định; M_IC
walk-forward xác nhận không cần fitting trọng số phức tạp.**

```
Gate 1 (p_gain FAIL) → Gate 2 (feature PASS: valuation) → Gate 3A = PASS
        ├── REDUNDANCY: PE_z_ts ≡ PE_pct (corr 0.90, 100% ngày >0.8) → 1 latent
        ├── M_value >> M_random >> M_p_gain  (mọi horizon)
        ├── M_IC walk-forward: dương 3/3 test window (2023/24/25)
        └── → tiếp: Gate 4 walk-forward stability (rankavg được ưu tiên)
```

## Redundancy audit — valuation không phải 6 bằng chứng độc lập

| Cặp | mean_corr | % ngày \|corr\|>0.8 | Kết luận |
|---|---:|---:|---|
| PE_z_ts ↔ PE_pct | **0.900** | 100.0 | **CÙNG MỘT latent** — PE level |
| PE_pct ↔ EV_EBITDA_z_ts | 0.804 | 63.1 | cao, PE ≈ EV/EBITDA |
| PE_z_ts ↔ EV_EBITDA_z_ts | 0.800 | 69.6 | cao |
| PB_z_ts ↔ PS_z_ts | 0.719 | 12.7 | trung bình-cao |
| PE_* ↔ ROE_z_ts | −0.69…−0.67 | 6–25 | nghịch (rẻ thường đi kèm ROE thấp) |
| val_zone_ts ↔ ret_120d | −0.114 | 0.0 | độc lập |

**Hệ quả xây score:** không rank-average 6 feature valuation (sẽ đếm PE 2–3 lần).
Chuẩn cấu trúc: VALUATION = {PE, PB, PS/EV_EBITDA} → sau đó trộn cross-cluster
{Quality, Behavior} theo evidence independence. M_rankavg dùng **1 đại diện mỗi
cluster** (không phải mọi feature).

## Model diagnostics (cross-sectional, nội ngày, pool=all)

### H20
| model | IC | tstat | %pos | IS 2022-24 | OOS 2025* | topDecile% | mono |
|---|---:|---:|---:|---:|---:|---:|---:|
| M_random | −0.007 | −1.8 | 47 | −0.008 | −0.007 | −0.19 | −0.022 |
| M_p_gain | +0.008 | +1.4 | 52 | +0.027 | −0.050 | +0.44 | +0.001 |
| **M_value** | **+0.088** | **+15.0** | 67 | +0.066 | +0.152 | +3.87 | +0.247 |
| **M_rankavg** | **+0.088** | **+15.8** | 68 | +0.071 | +0.140 | +4.29 | +0.244 |

### H60
| model | IC | tstat | topDecile% | mono |
|---|---:|---:|---:|---:|
| M_random | −0.006 | −1.4 | −0.23 | −0.014 |
| M_p_gain | +0.018 | +2.7 | +1.78 | +0.012 |
| **M_value** | **+0.120** | **+20.9** | +9.99 | +0.325 |
| **M_rankavg** | **+0.120** | **+22.1** | +10.14 | +0.313 |

### H120
| model | IC | tstat | topDecile% | mono |
|---|---:|---:|---:|---:|
| M_random | +0.001 | +0.2 | +0.15 | +0.006 |
| M_p_gain | +0.030 | +4.3 | +1.39 | +0.026 |
| **M_value** | **+0.203** | **+38.2** | +20.16 | +0.544 |
| **M_rankavg** | **+0.193** | **+37.0** | +20.01 | +0.511 |

*OOS 2025 = ĐÃ NHIỄM → chỉ descriptive, không dùng chọn feature/weight.

### Walk-forward M_IC (weights CHỈ từ training window)
| test | train | IC | tstat | topDecile% | mono |
|---|---|---:|---:|---:|---:|
| 2023 | 2022 | +0.034 | +2.8 | +0.46 | +0.029 |
| 2024 | 2022+23 | +0.054 | +5.7 | +2.07 | +0.296 |
| 2025* | 2022+23+24 | +0.145 | +13.6 | +7.00 | +0.381 |

M_IC weights luôn dương, PE_z/PE_pct chiếm ~40% — nhất quán với redundancy audit.
M_IC dương mọi test window nhưng **không vượt M_rankavg/M_value đáng kể** →
fit trọng số chưa chứng minh thêm giá trị.

## Đọc kết quả

1. **Valuation là alpha chính:** M_value vs M_random gap rất lớn (H20: 0.088 vs
   −0.007; H120: 0.203 vs 0.001). M_p_gain thậm chí âm ở OOS — xác nhận Gate 1.
2. **M_value ≈ M_rankavg:** thêm ROE + momentum (rankavg) không cải thiện IC
   (H20: 0.088/0.088; H120: 0.203/0.193). → hiện tại alpha chủ yếu là valuation.
3. **M_IC không vượt rankavg:** weighting chưa tạo thêm alpha; equal-weight
   rank-average là baseline đủ tốt, tránh overfit weight.
4. **Khoảnh khắc value:** H60/H120 mạnh dần (0.088→0.120→0.203) — consistent
   với mean reversion của mispricing, không phải correlation ngắn hạn ngẫu nhiên.
5. **Per-year M_value H20:** 2022 +0.115 / 2023 +0.037 / 2024 +0.047 /
   2025 +0.152 — dương mọi năm nhưng 2023-24 yếu hơn (vẫn vượt M_random).

## Guardrail

- Đây là **score-level evidence**, KHÔNG phải trading alpha.
- Chưa áp budget ≤20, chưa đưa vào Selection Layer / Governor.
- 2025 contaminated → M_IC/OOS chỉ descriptive.
- M_rankavg là model ưu tiên cho Gate 4 (không fit weight → ít risk overfit).
- ROE nghịch valuation cross-cluster → khi trộn Quality phải dùng evidence
  independence, không rank-average cùng lúc PE + ROE không kiểm soát.

## Hướng tiếp theo

1. **Gate 4 — Walk-forward stability** trên M_value / M_rankavg (equal-weight):
   kiểm tra ổn định IC qua expanding window, IC/IR, %pos, per-regime.
2. **Cluster composite thử nghiệm:** M_rankavg theo 1 đại diện/cluster
   (VALUATION=PE_z, QUALITY=ROE, BEHAVIOR=ret_120d) + redundancy re-check.
3. **Gate 5 — Selection:** top-K nội ngày, selected vs rejected, transaction/
   capacity/risk; budget ≤20 chỉ sau khi qua Gate 4.
4. **Không đưa p_gain vào score** (M_p_gain OOS âm).
