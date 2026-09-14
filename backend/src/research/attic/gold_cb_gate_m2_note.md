# Gate M2 — CB/Reserve-demand (IFS-primary) incremental-power audit (verdict note)

## Thiết kế
- **IFS-primary**: CB feature = `IFS_GOLD_RESERVE_CHANGE` GLOBAL PIT-step (latest vintage
  per obs, `publication_date <= t`). WGC_CB chỉ phủ 2024-02+ (517/1142 ngày) → KHÔNG
  làm feature riêng; dùng làm confirmation trên overlap (Gate O1: Pearson 0.91).
- WGC ≈ IFS là 2 measurement của CÙNG latent reserve-demand phenomenon → KHÔNG xếp
  2 series làm 2 evidence độc lập.
- PIT STRICT thật (vintage ladder IFS) → đây là walk-forward CHÍNH THỨC (khác E1 ETF
  là lag-simulation).
- Panel: 2021-08-09 → 2026-02-23 (1142 ngày). IS 2022-2024; 2025 chỉ descriptive.

## Kết quả (walk-forward logistic, OOS)

### H20 (primary)
| Model | AUC | ΔAUC vs M1 |
|---|---|---|
| M1_monetary | 0.513 | — |
| M2 + CB_IFS_level | 0.556 | +0.043 |
| M2 + CB_IFS_z | 0.573 | +0.060 |
| M2 + CB_IFS_sum3 | 0.558 | +0.045 |
| standalone CB_IFS_z | 0.569 | — |

Per-year ΔAUC(M2_z−M1): 2022:+0.004, 2023:+0.194, 2024:+0.083, 2025:+0.081 → **dương ổn định qua 4 năm**.

### H60
| Model | AUC | ΔAUC vs M1 |
|---|---|---|
| M1_monetary | 0.504 | — |
| M2 + CB_IFS_level | 0.615 | +0.111 |
| M2 + CB_IFS_z | 0.629 | +0.125 |
| M2 + CB_IFS_sum3 | 0.607 | +0.103 |
| standalone CB_IFS_z | 0.642 | — |

Per-year: 2022:+0.277, 2023:+0.157, 2024:+0.362, **2025:−0.268** → pooled dương nhưng **2025 đảo dấu** (không ổn định).

### H120
| Model | AUC | ΔAUC vs M1 |
|---|---|---|
| M1_monetary | 0.474 | — |
| M2 + CB_IFS_level | 0.395 | −0.079 |
| M2 + CB_IFS_z | 0.525 | +0.051 |
| M2 + CB_IFS_sum3 | 0.391 | −0.083 |
| standalone CB_IFS_z | 0.373 | — |

Per-year: chỉ có 2023 (+0.220). **Standalone 0.373 (dưới 0.5)** → H120 yếu/âm, không dùng được.

## Redundancy CB vs M1 (spearman, độc lập information)
- CB_IFS_z vs DXY_z = +0.058; vs TIP_mom5 = +0.035 → **CB hầu như độc lập với M1 monetary**
  (không phải duplicate của dollar/real-yield trend).
- CB_IFS_level vs DXY_z = −0.193 (vừa phải).

## Verdict
```
M2 vs M1:
   H20  → ΔAUC +0.060, dương ổn định qua 2022-2025   ✓
   H60  → ΔAUC +0.125 nhưng 2025 đảo dấu −0.268      ⚠ unstable
   H120 → ΔAUC +0.051, standalone 0.373 (dưới 0.5)   ✗ yếu/âm
        → PROMISING tại H20, CHƯA đủ stable toàn bộ
```
**Kết luận: CB/IFS có latent signal tại H20 (ổn định qua 4 năm, độc lập với M1) nhưng
không ổn định ở H60 (đảo dấu 2025) và yếu ở H120.**

→ **PROMISING, NOT FULLY DEPLOYABLE.** Chưa gọi là "de-dollarization alpha". Chưa
nâng cấp thesis — chỉ ghi nhận reserve-demand có incremental info tại H20.

## Khuyến nghị tiếp theo
1. Không đưa CB vào production vội: cần xem sensitivity tại H60/H120 và kiểm định thêm.
2. Hướng kiểm tra tiếp: interaction M2×regime (M4) — nhưng KHÔNG dùng để cứu M2 nếu
   standalone yếu; chỉ dùng nếu có bằng chứng regime-conditional.
3. Forward crawl ETF vẫn chạy (chi phí thấp) — khi có vintage đủ dài, chạy lại E1.
4. Gold v0.2: nếu mở, M1→M2 với CB_IFS_z tại H20 là representation khả dĩ nhất.

## Data/Artifacts
- Module: `gold_cb_feature_audit.py`.
- CSV: `data/reports/gold_cb_m2_audit.csv` (gitignored qua `data/`).
- Tests: `test_gold_cb_feature_audit.py` (7 tests).