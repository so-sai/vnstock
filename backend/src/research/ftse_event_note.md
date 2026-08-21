# FTSE Event Ledger — Natural Experiment 21/08/2026

**Loại:** Event Observation Layer — READ-ONLY research checkpoint.
**Code:** `backend/src/research/ftse_event_ledger.py` | **Tests:** `backend/tests/test_ftse_event_ledger.py` (20 pass).
**Ranh giới:** KHÔNG import `src.engine` / `src.governor` / decision pipeline (có test chặn). KHÔNG sinh tín hiệu mua/bán. KHÔNG đụng `ptck.py`, Governor, threshold.

---

## 1. Baseline frozen (PRE-EVENT, 2026-08-20)

Ghi một lần vào `backend/data/ftse_event_ledger.db` → bảng `event_baseline`.
Re-freeze bị chặn bởi `BaselineFrozenError`. `params_hash = 9dd929b52363f4f0`.

### System outputs (provenance: data/output/*.json)

| Trường | Giá trị | Nguồn |
|---|---|---|
| Quyết định | QUAN SAT / DUNG NGOAI | final_decision.json |
| Regime | RANGING (replay 0.36; live CRISIS 0.3373 theo CLI) | snapshot_index.json + regime_engine |
| Cấu trúc | VỠ CẤU TRÚC — so_tru=0 (final_decision) / so_tru_ok=1 (structural_state) | *mâu thuẫn nguồn, ghi nguyên trạng, ledger không phân xử* |
| Breadth | 39.8% (lri) / 39.7% (structural lan_toa) | final_decision.json |
| Recovery | PILOT_ABORT: DD −9.7% (>−8%), stab 14.59 (>8), MA10 NO, thrust YES | final_decision.json |
| Confidence | 0.28 THẬP, tạm ngưng TRUE | final_decision.json |
| DDI | Δ_SA 0.273, dS/dt 0.4283, AC_lat 0.3106, **healing_illusion TRUE**, caution | final_decision.json |
| LRI | 0.5001 PROBE, max_alloc 50.01%, ON 4.89 NORMAL, USD/VND 26179, FII 10D −554.36 tỷ | final_decision.json |

### News inputs (provenance: `USER_NEWS_2026-08-20_unverified`) — lớp observations, KHÔNG trộn system metrics

| Trường | Giá trị |
|---|---|
| VN-Index phiên 20/08 | +0.44% |
| Tự doanh | +508 tỷ (front-run hypothesis) |
| Khối ngoại | −617 tỷ |
| Thanh khoản | 14.929 → 13.472 tỷ (−9.7%) |

---

## 2. Hypothesis đang kiểm nghiệm

> Mua trước FTSE ≠ dòng vốn nâng hạng đã vào. FTSE là **catalyst**, không tự động là **regime change**.

Kịch bản 21/08: surprise positive → continuation | in-line → sell the fact | disappointment → unwind.
Bối cảnh `HEALING ILLUSION` (stress vượt adaptation) dự báo trước khả năng giá hồi cục bộ trong khi cấu trúc chưa sửa.

## 3. Layers ghi nhận

- **A — Surprise:** expected list (04/2026, unverified) vs official list (21/08) →
  `EXPECTED_INCLUDED / SURPRISE_INCLUDED / EXPECTED_EXCLUDED / NON_EVENT_CONTROL`
  qua `record_surprise(expected, actual, controls, expected_source, actual_source)`.
- **B — Price/Flow:** D+1 / D+5 / D+20 cho từng nhóm + market:
  return, rel_return_vs_VNINDEX, volume_change, foreign_flow, proprietary_flow
  qua `record_observation(window, subject, metrics, source)` — provenance bắt buộc.
- **C — Transmission:** breadth, liquidity, structure pillars, MA reclaim, recovery gates
  qua `evaluate_gates(metrics)`.

## 4. Gates (nhãn mô tả — không phải action)

```
selected ↑ AND controls ≈ flat/down AND breadth yếu        → FTSE_EVENT_ROTATION
selected ↑ AND breadth +≥5pp AND liquidity ↑ AND structure ≥1/3
             AND MA reclaim AND recovery gates pass         → MARKET_REGIME_CHANGE_CANDIDATE
còn lại                                                     → INSUFFICIENT_EVIDENCE
```

Ngưỡng mô tả (`CONTROL_NEUTRAL_MAX_PCT=0.5`, `BREADTH_STALL_TOL_PP=1.0`,
`BREADTH_EXPANSION_MIN_PP=5.0`) là hằng số research trong module, **không nối** vào DecisionGuard.

## 5. Những gì KHÔNG được làm

1. Không sửa baseline sau event (append-only).
2. Không dùng kết quả event để tune model/threshold (OOS 2025–2026 đã nhiễm).
3. Không biến divergence Gold-bullish × Equity-CRISIS thành causal story trước khi có dữ liệu D+20.
4. Không thêm feature RMB/Japan/ETF vào model đang frozen (Forward Paper Gate #1 đang chạy, H20 ~15/09).

## 6. Trạng thái

| Mục | Trạng thái |
|---|---|
| Baseline freeze | ✅ DONE @ 2026-08-21T10:55:42 |
| Official FTSE list | ⏳ chờ công bố 21/08 |
| Surprise classification | ⏳ 0 symbols |
| Observations D+1/D+5/D+20 | ⏳ 1 row (PRE news) |
| Gate evaluation | ⏳ chưa đủ dữ liệu |
