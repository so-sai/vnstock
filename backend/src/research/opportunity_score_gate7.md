# Gate 7: Walk-Forward / OOS Policy Validation

> **Ngày đóng:** 2026-08-16 | **Trạng thái:** GATE 7 = **CONDITIONAL PASS**
> **Script:** `backend/src/research/opportunity_score_gate7.py` (READ-ONLY)
> **Output:** `backend/data/reports/opportunity_score_gate7_audit_all.csv`
> **Lưu ý:** `opportunity_score_gate6.py::simulate()` được mở rộng (thêm `cost`,
> per-year `win_rate`/`sharpe`, `IS/OOS_sharpe`, `retention_pct`) — backward
> compatible, kết quả Gate 6 không đổi.

## Verdict (1 câu)

**Alpha +1.31% chịu được stress friction (còn dương ở 1.00% round-trip), ổn
định theo regime (RANGING PASS), OOS 2025 dương — NHƯNG KHÔNG dương từng năm:
2023 âm (−2.92%) do TIMING/BUDGET machinery (20 slot tiêu ~ngày 38), KHÔNG phải
feature fail (oracle A 2023 = +1.85%).**

```
Gate 1 (p_gain FAIL) → Gate 2 (valuation PASS) → Gate 3A (M_value PASS)
→ Gate 4 (cluster KHÔNG incremental) → Gate 5 (selection alpha PASS)
→ Gate 6 (Governor integration PASS) → Gate 7 = CONDITIONAL PASS
        ├─ Cost: break-even ≈ +1.76% round-trip (1.00% stress: +0.76%)
        ├─ Regime: RANGING +1.39% win 60.7% PASS; TRENDING yếu nhất +0.21%
        ├─ OOS 2025 (desc): +3.75% dương
        └─ Per-year: 2022 +1.60 / 2023 −2.92 / 2024 +2.81 / 2025 +3.75
              └─ 2023 âm = budget timing (oracle k=1 2023 = +1.85% dương)
```

## 1. Year-by-year / IS vs OOS

Policy cố định (M_value, cap=1, budget=20, cost 0.45%): Net R20 per-trade.

| year | n | net r20% | win% | sharpe |
|---|---:|---:|---:|---:|
| 2022 | 20 | +1.60 | 50.0 | 0.16 |
| **2023** | 20 | **−2.92** | 50.0 | **−0.41** |
| 2024 | 20 | +2.81 | 65.0 | 0.56 |
| 2025 (OOS) | 20 | +3.75 | 80.0 | 0.93 |
| IS (22-24) | 60 | +0.50 | 55.0 | 0.06 |
| **OOS (25)** | 20 | +3.75 | 80.0 | 0.93 |

- **Retention Sharpe OOS/IS = 1495.9%** (target ≥70% → ĐẠT) nhưng **không có ý
  nghĩa**: IS sharpe = 0.06 ≈ 0 → retention đạt một cách tầm thường. Đọc theo
  per-year net r20, KHÔNG theo retention.
- **2023 âm là điểm duy nhất chặn "PASS tuyệt đối"**. Không phải feature:
  - A oracle k=1 2023 = **+1.85%** (feature alpha DƯƠNG trong 2023).
  - B machinery cap=1 2023 = −2.92% → chênh lệch do **budget exhaustion timing**
    (20 slot dùng hết ~ngày 38 của năm → chọn dồn 20 mã đầu năm).
  - Đây chính là **Temporal Slot Allocation** — đã xác định ở Gate 6, giữ làm
    bài toán policy riêng, KHÔNG đổ lỗi feature.
- **OOS 2025 (descriptive)**: +3.75% — không dùng để chọn tham số (AGENTS.md).

## 2. Transaction cost sensitivity

| cost% | net r20% | win% | PF | NAV% | MaxDD% |
|---:|---:|---:|---:|---:|---:|
| 0.15 | +1.61 | 62.5 | 1.77 | +11.51 | −12.56 |
| **0.45** | **+1.31** | 61.3 | 1.60 | +5.60 | −14.17 |
| 0.75 | +1.01 | 60.0 | 1.44 | −0.00 | −15.74 |
| 1.00 | +0.76 | 58.8 | 1.31 | −4.45 | −17.04 |

- **Break-even friction ≈ +1.76% round-trip** (vì net_r20 = gross_r20 − cost →
  break-even = gross). Alpha còn dương ở 1.00% — chịu được stress friction gấp
  ~2× baseline.
- MaxDD tăng dần theo cost (−12.6 → −17.0%) nhưng chưa có dấu hiệu sụp đổ;
  NAV chuyển âm ở 0.75%+ do cost drag trên vòng quay, KHÔNG phải alpha chết.

## 3. Regime stability

| regime | n | net r20% | win% |
|---|---:|---:|---:|
| CRISIS | 25 | +2.41 | 68.0 |
| **RANGING** | 28 | **+1.39** | **60.7** |
| TRENDING | 27 | +0.21 | 55.6 |

- **RANGING (đa số thời gian): +1.39%, win 60.7% → PASS** (cả hai tiêu chí).
- TRENDING yếu nhất (+0.21%) — kỳ vọng: value mean-reversion chạy chậm, ít hiệu
  lực trong uptrend. Nhưng win vẫn 55.6% > 50%.
- CRISIS mạnh nhất (+2.41%) — nhất quán Gate 4 (M_value IC CRISIS +0.093).

## Đọc kết quả

1. **Alpha robust theo cost**: break-even 1.76%, sống qua stress 1.00% — transaction
   cost KHÔNG phải rào cản chặn production.
2. **Alpha robust theo regime**: RANGING (chiếm phần lớn thời gian) PASS — đúng
   chế độ chính của thị trường VN.
3. **2025 OOS dương** nhưng chỉ descriptive; retention Sharpe "đạt" là vô nghĩa
   vì IS sharpe ~0 — không dùng retention làm cứng.
4. **2023 âm = policy timing, không phải feature**: bằng chứng oracle A 2023
   dương (+1.85%) trong khi machinery âm (−2.92%). Đây là mục tiêu của Temporal
   Slot Allocation, KHÔNG phải lý do từ bỏ M_value.
5. **Per-year KHÔNG monotonic**: pooled +1.31% ≠ từng năm. Policy production phải
   chấp nhận năm âm từ budget timing hoặc giải quyết temporal allocation.

## Guardrail

- **OOS 2025 chỉ descriptive** (AGENTS.md: 2025-2026 đã nhiễm) — KHÔNG chọn
  cost/threshold/K từ OOS.
- **Break-even 1.76% KHÔNG phải lý do tăng cost giả định** — chỉ là thước đo sức
  chịu đựng, không phải tham số để tối ưu.
- **B vẫn là replication machinery** (synthesized candidates), chưa phải replay
  thật qua Governor DB.
- **Retention Sharpe đọc thận trọng** (IS sharpe ~0 → dễ đạt tầm thường).
- **2023 âm KHÔNG được dùng để fit tham số** — nó chỉ chứng minh "cần temporal
  allocation", không chứng minh "M_value hỏng".
- KHÔNG dùng +147% Selection v0 (checkpoint 147afdd) làm benchmark.

## Hướng tiếp theo

1. **Temporal Slot Allocation** (bài toán policy, tách biệt): thay vì budget
   ceiling tiêu hết ngày 38, phân bổ slot theo thời điểm alpha xuất hiện. Cần
   CHECKPOINT rõ ràng về realized alpha sau cost + walk-forward trước — gate này
   đã cho: alpha sống qua cost/regime, 2023 âm do timing → đủ cơ sở mở bài toán.
2. **Replay Governor thật** với M_value làm rank function → Evidence Ledger thật.
3. Capacity/ADV check (position ≤10-15% ADV_20D) với 20 slot/năm.
