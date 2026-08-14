# Baseline: Selection Layer v0 = FAIL (discrimination gate)

> **Ngày đóng:** 2026-08-14 | **Trạng thái:** baseline bất biến (checkpoint nghiên cứu)
> Mọi alpha mới phải đánh bại baseline này, không kế thừa artifact đã chứng minh.

## Tóm tắt kết luận

**Selection v0 không có incremental selection power. +147% (2022) là non-attributable alpha.**

```
PIT Data Repair → PIT Validation → Replay 2022–2025 → Discrimination Audit
        → Selection v0 = FAIL  ← đóng baseline tại đây
            ├── p_gain ≈ P(model says BUY)
            ├── RankIC ≈ 0 (cross-sectional)
            ├── Timing unstable (pooled ≈ 0)
            ├── bucket effect = timing artifact
            └── +147% = non-attributable
```

## Điều kiện dữ liệu của baseline

- Screener_cache chuẩn hoá toàn bộ nghìn→đồng (116 symbol + DMX + 4 leftover; backup `bak_pre_norm_20260814_220614`).
- `background_sweep.py` route qua `save_data_upsert` (Ingestion Scale Guard VND ×1000) — không còn bypass `to_sql` (commit 100ff88).
- Valuation PIT backfill: 58 symbols, 7300 rows, DONE=58, NO_DATA=0, FAILED=0 (VNM 2022Q1 PE=16.03 price=62400 — đơn vị đồng).
- Replay `data/replays/replay_58_ts/replay_58_{2022..2025}.db`: `--core-58`, PIT strict, budget 20/năm.

## Kết quả discrimination gate (replay_58_ts, sau scale fix)

| Năm | EXECUTE | WinRate | meanR20 | Eligible-rejected WinRate | meanR20 |
|---|---:|---:|---:|---:|---:|
| 2022 | 20 | 60.0% | +5.43% | 68.2% | +7.44% |
| 2023 | 0 | - | - | (p_gain max 0.51, không đạt floor) | |
| 2024 | 0 | - | - | (p_gain max 0.53) | |
| 2025 | 0 | - | - | (p_gain max 0.51) | |

- Bucket [>=0.60]: EXECUTE 50%/+2.50% **thua** rejected 76.3%/+7.46%.
- Bucket [0.50-0.55] rejected: 96.3%/+18.50% (bị bỏ lỡ).
- Budget 20 + daily_cap=1 → 20 EXECUTE dồn vào 20 ngày đầu 2022 (01-04 → 02-08) → trộn stock selection + timing + concentration.

## 3 phép kiểm chứng

### Experiment A — Cross-sectional RankIC (p_gain vs R20, within-day)
- **Pooled RankIC = +0.008** (n=997 ngày, pos=518/neg=479); mean|RankIC|=0.144-0.178 (nhiễu).
- Per-year: 2022 +0.076 / 2023 -0.052 / 2024 +0.059 / 2025 -0.050 → không ổn định.
- Deploy-only 2022: RankIC **-0.083**.
- Kết luận: p_gain **không rank được stock trong cùng ngày**.

### Experiment 2 — Bucket stratify (Simpson's paradox check)
- Pooled bucket trông phân hóa (0.30-0.40:-2.31% → >=0.60:+7.25%) nhưng là **trộn timing** (bucket ≥0.55 chỉ tồn tại tháng 1-2/2022).
- Stratify regime 2022: CRISIS [>=0.60] 85.7%/+9.99% đúng hướng; RANGING [>=0.60] 73.2%/+5.50%; TRENDING nghịch (n=15).
- Kết luận: hiệu ứng `[0.50-0.55] > [>=0.60]` của diagnostic v0 = **artifact subset nhỏ + timing**.

### Experiment B — Timing (per-day signal vs forward R20)
- Pooled corr mean_p_gain vs meanR20 = **+0.005** (≈0); frac≥0.50 = +0.100.
- Timing gate (eligible≥0.50) chỉ đúng 2022 (+0.13% vs -1.83%); **2023/2024/2025 đều ngược**.
- Kết luận: p_gain **không có timing power ổn định**.

### Semantics p_gain (theo action)
- OPEN mean=0.589 / SCALE_IN=0.552 / REDUCE=0.394 / VETO=0.269.
- => p_gain ≈ **P(model nói BUY)** (action chooser), không phải P(R>0|evidence).

## Root cause & hướng tiếp theo (chưa thực hiện)

Root cause ở `representation → evidence → probability semantics`, không phải budget.
p_gain giữ vai trò **diagnostic / optional timing feature**, không được dùng làm
hard gate cho tới khi có walk-forward evidence.

Kiến trúc v0.1 đề xuất (trung lập, chưa chứng minh Timing Gate):
```
Candidate Pool
    ├── p_gain → diagnostic / optional timing feature
    └── Cross-sectional features
            ↓
      Opportunity Score   (valuation-rel, momentum, liquidity anomaly,
                           quality, sector-relative, regime compatibility,
                           evidence independence)
            ↓
      Within-day Rank → Top-K ≤ 20/year → Budget
```
Guardrail: Opportunity Score **không dùng lại p_gain dưới tên khác**; phải qua
RankIC → top-decile spread → monotonic buckets → selected vs rejected →
walk-forward stability; chưa đạt discrimination gate thì không đưa vào Governor.
