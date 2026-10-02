# C3-1 — Signed Causal Graph (Design Doc v2.1)

**Status:** IMPLEMENTED (log-only wiring)
**Commits:** `5de3e85` (field) · `2e44e16` (46 polarity + contract) · `ef8f308` (C3-6 hook)
**Scope:** biến polarity từ metadata thụ động thành ground truth có tiêu dùng (consumer)
 qua reconciliation hook — vòng khép kín của Signed Graph.

---

## 1. Motivation

Registry 46 causal edges trước C3-1 chỉ có confidence/lag/attenuation — reachability
graph vô dấu. Hệ quả: narrative direction (BULLISH/BEARISH/action) có thể mâu thuẫn
với dấu cấu trúc của chính chuỗi nhân quả mà nó đang trưng ra, mà không ai phát hiện.

## 2. Data model — `CausalEdge.polarity`

- Field: `polarity: int = 0` (default 0 = indeterminate), allowlist giá trị `{-1, 0, +1}`.
- Phân bố 46 edges: **36 × (+1) / 8 × (−1) / 2 × 0**.
- **REGIME_CONDITIONAL_ALLOWLIST** (đúng 2 entry, `0` ngoài allowlist = contract fail):
  - `BANK:INTEREST→NIM` (FRANCHISE_BANK)
  - `BANK2:INTEREST→NIM` (ASSET_BANK)
  - Bắt buộc: description chứa `regime_conditional` (chống "0 lười biếng").
  - Cơ sở kinh tế: NIM tùy CASA — CASA cao nới NIM khi tăng lãi suất, CASA thấp bị ép;
    không gán dấu tĩnh được.
- Contract test: `backend/tests/test_causal_edge_polarity.py` (3 tests, khóa 46 edges
  + polarity ∈ {-1,0,1} + allowlist + reason-check).
- Ghi chú hiệu đính: `INFLATION→RETAIL_MARGIN = −1` thuộc RETAIL_PLATFORM (không phải
  CYCLICAL); `GLOBAL_LIQUIDITY→LIQUIDITY_TRAP = −1` (target là bad state — thanh khoản
  dồi dào *giảm* bẫy thanh khoản; bảng review ban đầu ghi nhầm +1 do đổi tên target).

## 3. Path semantics — `calculate_path_sign(edges)`

File: `backend/src/calibration/polarity_reconcile.py`

| Rule | Output | Lý do |
|---|---|---|
| Product các polarity dọc chuỗi | ±1 | dấu truyền dẫn tích lũy |
| BẤT KỲ edge `polarity == 0` | **0** | regime-conditional edge làm cả chuỗi indeterminate |
| Chuỗi rỗng | **0** | không có cấu trúc = không có khẳng định (không mặc định +1) |
| Thiếu key/attr `polarity` | **0** | thiếu → đứng ngoài (zero-hallucination) |
| Giá trị ngoài {-1,0,1} | `ValueError` | fail-fast; contract test bảo chứng 46 edges không chạm |

**Hop phải mang polarity:** `CausalGraph.trace_path` (`causal_edge.py` hop dict) thêm
`"polarity": e.polarity` — thiếu field này hook "mù" và luôn nhận 0 (bug được phát hiện
trước khi wiring; có test end-to-end khóa: LOGISTICS product = +1, BANK chain = 0).

## 4. Reconciliation rule (§6 — ratified)

```python
reconcile(path_sign, narrative, *, context) -> ReconcileResult(direction, warning, path_sign)
```

1. `path_sign == 0` → pass-through, **không warning** (indeterminate không được phán).
2. Narrative không phải BULLISH/BEARISH (NEUTRAL/TRANSITIONAL/FRACTURED/N/A) →
   pass-through (không có khẳng định để mâu thuẫn).
3. Structural == narrative → pass-through, không warning.
4. Xung đột (±1 symmetric) → **polarity thắng**: `direction = structural`,
   `warning = "POLARITY_NARRATIVE_CONFLICT"` +
   `logger.warning("POLARITY_MISMATCH on %s: structural=%s vs narrative=%s ...")`.
   Không silent, không `except: pass`.

Consumer tự quyết định dùng `direction` (ép) hay chỉ hiển thị phân kỳ.
Field `reason` (human-readable cho log audit) — chưa implement, xem §7.

## 5. Finding 2 — điểm hook (effected as-built)

**KHÔNG phải `telemetry/attribution.py:224,247`** (đề xuất ban đầu) — 3 bằng chứng:

| # | Bằng chứng | Kết luận |
|---|---|---|
| 1 | `attribution.py:42` `ENGINES=[regime,liquidity,...]`; `:224,247` gán nhãn từ **engine score**; `decompose_attribution` không nhận chain/edge | `calculate_path_sign(chain_edges)` không có input — ép vào = nhầm ngữ nghĩa |
| 2 | `epistemic_engine.compute_causal_coherence` (đọc BULLISH/BEARISH của `causal_chain`) chỉ có caller là `test_epistemic_engine.py:65`; production chỉ dùng `compute_coverage` (`law_bridges.py:121`) | Dead code — không phải producer sống |
| 3 | Producer sống duy nhất có cả chain lẫn direction: `csi_explain` — `_trace_chain:286-339` giữ `hops` từ `trace_path` + action arrows `:612-618, :804` | **Điểm hook đúng** |

**Wiring (`ef8f308`, log-only — render giữ nguyên 100%):**
- `csi_explain._ACTION_DIRECTION`: `OPEN/SCALE_IN → BULLISH`,
  `REDUCE/AVOID/VETO → BEARISH`; `HOLD/WAIT/N/A/unknown → không adjudicate`.
- `_reconcile_action_polarity(symbol, trace, action)` gọi sau khi trace + action sẵn có;
  conflict → log `POLARITY_MISMATCH` (không đổi output render).

## 6. Behavioral evidence (as-run)

- Unit: `test_polarity_reconcile.py` — **19/19** (§6 contract + hook + end-to-end).
- Full suite: **1931/1932** (1 fail baseline `bun`, pre-existing, không liên quan).
- Smoke CLI: VCB (chain sign=0, action REDUCE) → **im lặng** (indeterminate không phán);
  HPG (sign=+1, action VETO) → warning bắn, render y nguyên.
- Production (`daily-update` governor matrix, 11 symbols): 5 warnings
  (FPT/HPG/DGC/MWG/GAS — sign=+1 vs BEARISH action). Xem §8 caveat.

## 7. Deferred (có chủ ý — không implement trong C3-1/C3-6)

| ID | Nội dung | Lý do defer |
|---|---|---|
| C3-7 | §5 path-confidence threshold 0.5 → tag `LOW_CONFIDENCE` (render warning, không suppress) | Cần `path.quality` API — `trace_path` trả `list[dict]`, chưa có quality |
| C3-7b | Cờ hiển thị phân kỳ trong `csi-explain` (dùng `ReconcileResult.direction`) | Sau khi có test cho render; constraint: cấm đổi behavior render trước đó |
| — | Field `reason` trong `ReconcileResult` (audit log 3 tháng sau đọc hiểu ngay) | Enhancements, không chặn vòng khép kín |
| — | Gắn hook tại `attribution.py` | Bằng chứng §5 — ngoài ngữ nghĩa; cấm scope creep |

## 8. Known caveat (symmetric rule)

`action` là claim về **CSI**, không phải claim về transmission — nên rule symmetric sẽ
cảnh báo ở **mọi chuỗi +1 + action BEARISH** (5/11 symbols trong bearish market).
Log-only nên không nguy hiểm, nhưng nếu thành nhiễu → tinh chỉnh pairing (chỉ adjudicate
khi narrative được suy từ chính chain) là task riêng, sửa sau khi có telemetry đo được.

## 9. Backlog liên quan

| ID | Nội dung | Priority | Gating |
|---|---|---|---|
| C3-5 | Universal edge `US10Y → GLOBAL_DEMAND` (fix chuỗi VTP/LOGISTICS gap — GLOBAL_DEMAND orphan) | P1 | Chờ hook log evidence (INDETERMINATE/path) xác nhận cần thật; thêm edge → update contract 46→47 |
| C3-7 | Path-confidence tag + render divergence flag | P2 | Sau C3-6 |
| C3-8 | Prune HOẶC wire `compute_causal_coherence` (dead code — chỉ test gọi) | P3 | Finding 3, tách scope |
| C3-4 | `QT_IMPULSE`: `FRED_API_KEY` + fix đơn vị (dấu y hệt nhau `:414-425`) | P2 | Parallelizable |
