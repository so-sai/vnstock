# AGENT COGNITIVE BOOTLOADER

> **STATUS:** Kit v1.2.5 GOLD | Phase 3 Complete | Full-Stack Quant OS
> **WARNING:** This file is the operating constitution. `kit` is the authority.
> **SENTINEL LAW:** All entry-points MUST use the `_hydrate_path()` v2.2 protocol (AGENTS.md Anchor) to ensure absolute portability.

### 🧭 1. Mandatory Startup Sequence

Before taking action in this repository:

```powershell
python backend/src/utils/sentinel_check.py; kit recall
```

1. Run the startup sequence above exactly as written.
2. **SENTINEL LAW v2.2:** Every executable .py file MUST include the `_hydrate_path()` v2.2 (AGENTS.md Anchor) header. Anchor on `AGENTS.md + backend is_dir` — NEVER use `.kit or src or screener.py` (false positive in backend/src).
3. Only open docs for syntax/reference after memory hydration.
4. If `kit` is missing, check your Global PATH.
5. **ALWAYS** clear `__pycache__` after modifying `_hydrate_path()`.

## 🛠️ 2. Minimal Navigation

1. `AGENTS.md` (Constitutional Laws)
2. `.kit/docs/reference.md` (Exact Syntax)
3. `.kit/local_brain.db` (The local brain SQLite store)
4. `.kit/scripts/kitf.ps1` (Friction Logger)

## 🧠 3. Iron Laws Of Memory

1. **Markdown is volatile.** `.kit/local_brain.db` is the source of truth.
2. **Log Friction.** Use `kit learn --tag friction` or `kit learn --tag pattern` to capture decisions.
3. **Inspect First.** Never guess paths or symbol structure.
4. **Evidence over Truth.** Treat structural signals as observations to be reasoned over, not as final truths. Log all structural noise or drifts into the `.kit` system.
5. **Architectural Alignment:**
    - **Kit** = Software Brain (Memory/Logic)
    - **Vantage** = Hardware Sensor (Structure/Forensics)
    - **Git** = Time Kernel (History)

## ⚡ 4. Fast Start

- `kit recall` -> Hydrate memory
- `kit context` -> View local awareness
- `kit learn --tag pattern "..."` -> Capture patterns
- `kit learn --tag friction "..."` -> Log bugs/issues

## 🔄 5. Operational Rituals

### 📅 Daily Loop (Stateless Worker)
1. **Recall:** `kit recall`
2. **Think:** `kit-agent ask`
3. **Act & Verify:** `kit recall; kit doctor --heal; kit learn`
4. **Friction Logging:** `kit learn --tag friction --namespace "area" "description"`

### 🧠 Weekly Reflection
`kit stats; kit where; kit doctor --heal; kit recall`

**Weekly Questions:**
- *Invariant Unused:* Invariant nào không bao giờ được recall?
- *Invariant Ignored:* Invariant nào bị agent bỏ qua?
- *Repeated Decisions:* Quyết định nào lặp lại?
- *High Friction Zones:* Vùng nào friction dày đặc?

### 📊 Monthly Synthesis
Generate 3 outputs:
1. `invariants_top_10`: Real-world task triggers (not theoretical).
2. `decision_patterns`: Repeated reasoning paths/fixes.
3. `failure_modes`: Categorized (auth, config, logic, infra).

## 🛡️ 6. System Modes
- **Sealing:** `kit stats` + `vantage seal` (Freeze knowledge).
- **Purge:** Reset cognitive space for a new 'Desert Journey'.
- **Verification:** `kit doctor --heal` (Check & repair).

## 🏗️ 7. Project Architecture (Phase 3 Complete)

### Completed Phases:
- **Phase 1:** Core Engines (Regime, Breadth, RS, Screener, Momentum, Mean Reversion)
- **Phase 2:** Full-Stack (FastAPI 12 endpoints + React 5 pages, Zero TS Errors, 1.1MB JS)
- **Phase 3:** Data Pipeline & Automation (daily_updater.py, db_maintenance.py, setup_scheduler.py)

### Key Path Resolution (v2.2):
- `PROJECT_ROOT` = parent of `AGENTS.md`
- `backend_dir` = `PROJECT_ROOT/backend`
- `DATA_DIR` = `backend/data` (NOT `PROJECT_ROOT/data`)
- `LIBS_DIR` = `backend/libs` (NOT `PROJECT_ROOT/libs`)
- `PORTFOLIO_PATH` = `backend/src/portfolio/my_portfolio.json`

## 🚨 8. Operator Protocol — CLI-First Law (Layer 2–3)

> **BẮT BUỘC:** Đây là quy tắc cứng. Vi phạm = architecture drift.

### 🔑 Core Principle

```
User/Agent request
       ↓
  [1] Đọc SYSTEM_MANIFEST.yaml  ← Layer 2: Procedure
       ↓
  [2] Tìm CLI tương ứng
       ↓
  [3] Gọi: python ptck.py <command>   ← Layer 3: Policy
       ↓
  [4] Chỉ fallback import trực tiếp nếu CLI không có
       ↓
  [5] Báo cáo vào .kit/friction nếu thiếu entrypoint
```

### 📋 Nghiêm cấm

- ❌ Viết script `.py` tạm để test / query / check
- ❌ Tự `sys.path.insert()` thủ công
- ❌ Import module trực tiếp từ CLI / scratch
- ❌ Tạo `check_market.py`, `test_*.py`, `quick_*.py` ở temp

### ✅ Bắt buộc

- ✅ Đọc `SYSTEM_MANIFEST.yaml` trước mọi hành động
- ✅ Gọi `python ptck.py <command>` cho mọi thao tác
- ✅ Nếu cần operation mới → thêm vào `ptck.py` + `SYSTEM_MANIFEST.yaml`

### 📖 Ví dụ

```bash
# Thay vì viết script test → dùng CLI:
python ptck.py market          # Market snapshot
python ptck.py regime          # Regime analysis
python ptck.py report weekly   # Báo cáo tuần
python ptck.py daily-close     # Daily closer
python ptck.py telemetry reputation --refresh  # Refresh reputation
python ptck.py snapshot        # Ảnh chụp thị trường duy nhất
python ptck.py confidence      # Độ tin cậy của quyết định
python ptck.py status          # Health check
python ptck.py gold            # Gold dashboard
```

### 🗺️ SYSTEM_MANIFEST.yaml

File `SYSTEM_MANIFEST.yaml` ở project root là **single source of truth** cho mọi entrypoint. Gồm:

- `entrypoints`: danh sách tất cả CLI commands
- Mỗi entrypoint có: `description`, `cli`, `category`
- CLI command ghi đúng cú pháp (kể cả arguments)

Cấu trúc:
```yaml
entrypoints:
  market_snapshot:
    description: "Snapshot tổng quan thị trường"
    cli: "python ptck.py market"
    category: market
```

### ⚠️ Hậu quả nếu vi phạm

Sau 6 tháng nếu mỗi Agent đều tự viết script tạm:
- `check_market.py`, `test_regime.py`, `quick_snapshot.py`...
- Mỗi file import khác nhau, path khác nhau, config khác nhau
- Không ai biết đâu là entrypoint thật
- **Architecture Drift** — hệ thống mất khả năng vận hành

Đây là lý do luật này là **cứng** (hard constraint), không phải khuyến nghị.

### Critical Debug Patterns (stored in .kit):
- **NoneType API 500:** Always wrap `float()` with try/except in service layer
- **Unit Mismatch:** `entry_price * 1000` to normalize thousands → raw VND
- **Windows Encoding:** `sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')`
- **Hydrate Path Anchor:** Use `AGENTS.md + backend is_dir` — unique to project root
- **Package Manager:** Always use `bun` for frontend (e.g. `bun install`, `bun run build`, `bun run dev`). Do NOT run `npm` or `yarn`.
- **i18n Coverage Test:** Run `bun run i18n-coverage` from `frontend/` to check all `t()` calls in `.tsx` files have matching keys in `vi.json`. This catches bare English strings. ESLint custom rule `no-bare-english-strings` can be added via `eslint-plugin-react` with a custom rule that flags literal JSX text that isn't wrapped in `t()`.
- **Bộ tự đánh giá độ tin cậy (45% TRUNG_BINH):** 5 yếu tố có trọng số: thị trường rõ ràng(0.25) + cấu trúc lành mạnh(0.25) + tín hiệu đồng thuận(0.25) + biến động ổn định(0.15) + tín hiệu đáng tin(0.10). Tạm ngưng kết luận khi điểm <0.30 hoặc entropy>2.5+số trụ≤1. Dùng `python ptck.py confidence` để chạy. Output key tiếng Việt: `điểm_tin_cậy`, `mức_đánh_giá`, `tạm_ngưng_kết_luận`.

- **Thứ tự ưu tiên quyết định (CAO→THẤP):**
  1. Cấu trúc thị trường (VỠ CẤU TRÚC → DỪNG NGOÀI, không xét gì thêm)
  2. Cảnh báo sớm (chuyển pha mạnh → hạ mức hành động)
  3. Trạng thái thị trường (regime — ngữ cảnh, không override)
  4. Độ tin cậy — đo lường, không tự veto
  5. Lớp bảo vệ quyết định (decision_guard.py) — kiểm tra an toàn cuối, có quyền chặn nếu độ tin cậy < 30% hoặc entropy > 2.5 + trụ ≤ 1
  Nguyên tắc dữ liệu: regime tính 1 lần duy nhất, dùng chung cho mọi tầng. Không đọc regime từ file cache. Không recompute regime giữa chừng. Toàn bộ pipeline dùng `market_snapshot.tao_anh_chup()` làm ảnh chụp duy nhất mỗi lần chạy — chứa regime + structural + cảnh báo sớm. Dùng `python ptck.py snapshot` để xem.

### 🧠 TWO-BRAIN ARCHITECTURE & BOUNDARY GUARD
- **Operational Brain (thực tế):** `backend/data/screener_cache.db` — chứa OHLCV, macro (macro_history), regime, decisions, flow. Lưu ý: `backend/data/brain.db` là empty file (0 bytes) không được dùng.
- **Memory Brain:** `.kit/local_brain.db` — session context, friction logs, metadata, flow engine tracking.
- **Bootstraps:** `backend/data/bootstraps/` — chứa CSV seed cho dữ liệu lịch sử vĩ mô. Hiện tại: `interbank_3y_raw.csv` (3,648 rows, 2023-01-02 → 2026-06-30). Engine bootstrap: `backend/src/bootstraps/interbank_bootstrap.py`.
- **CRITICAL CONSTRAINT - Interbank Z-Score:** Dữ liệu lãi suất liên ngân hàng (`macro_history` variable = `INTERBANK_*`) hiện có N=913 rows, baseline 3.5 năm, đã đủ điều kiện tính Z-Score. Tuy nhiên: đây là dữ liệu sinh từ anchor points + noise, KHÔNG phải dữ liệu thật từ SBV. Cần thay thế bằng real data pipeline (SSI iBoard R&D) trước khi dùng Z-Score để ra quyết định giao dịch.
- **Unique Constraint:** Toàn bộ `macro_history` không thể có UNIQUE(date,variable) do legacy GOLD_XAU duplicates (11 bản sao/ngày). Interbank data sạch (0 dupes). Seeder đã được fix: xóa (date,variable) trước insert để tránh duplication.

## Session: Phase 4 Perception→Understanding (P0→P3) + P4 Calibration + P5 Counterfactual — Jul 30 2026

### Session Follow-up — Jul 31 2026: BCM Health Fix + Stdout Wrap Cleanup + BHO Sector

#### Work Completed
1. **Sector mapping fix**: `company_state.py` — "Bho"→"Bất động sản", "Bat dong san"→"Bất động sản". BCM không còn là "Basic Materials".
2. **CaféF parser mở rộng** (`cafef_crawler.py`): map thêm CFO, CAPEX, SHORT_TERM_DEBT, LONG_TERM_DEBT, INTEREST_EXPENSE, CASH_EQUIV, INVENTORY, RECEIVABLES + "Tổng tài sản lưu động ngắn hạn"→CURRENT_ASSETS + "Tổng nợ"→TOTAL_LIABILITIES (2 cột mới phát hiện từ BHoSoCongTy 17 rows tổng hợp, Type 1-4 giống nhau). Sort keys dài trước. `TOTAL_DEBT = short+long` khi có.
3. **DEBT fallback**: `company_health_engine.py` — `debt = TOTAL_LIABILITIES` khi TOTAL_DEBT không có (proxy đòn bẩy). BCM Bal thoát 0.0 → 0.644.
4. **NO_DATA protocol** (user quyết định giữ 0.00 = NO DATA, không bịa số): `company_health_v2.py` — `HealthLatentState.no_data_organs`, `_detect_no_data()`, `_classify_archetype` bỏ qua organ NO_DATA khi xét DISTRESSED, CLI hiển thị "Cash: NO DATA" thay 0.00 🔴. BCM → **STEADY_EARNER** (P=35%), vector [0.630, 0.000(NO DATA), 0.644, 0.640, 0.658].
5. **Stdout wrap cleanup toàn repo** (task 4): thay module-level `sys.stdout = io.TextIOWrapper(sys.stdout.buffer, ...)` unconditional bằng block an toàn: `reconfigure(encoding='utf-8')` nếu đã là TextIOWrapper, chỉ tạo wrapper mới khi có buffer. Áp dụng 36 files trong `backend/src/` + `tests/test_decision_guard_integration.py` + `root/ptck.py:23-40`. Root cause: wrapper cũ bị GC → buffer đóng → `ValueError: I/O operation on closed file` khi pytest capture teardown.
6. **Kết quả test**:
   - `pytest tests/test_bug_regression.py -q` → 98 passed, 1 skipped (skip pre-existing: `cannot import name 'backfill_symbol'` tại dòng 745).
   - Full suite `pytest tests/ -q` → **322 passed, 3 failed, 1 skipped** (lỗi capture hết hoàn toàn). 3 failures là logic pre-existing trong `test_decision_guard_integration.py`: `test_stale_tracker_veto_active`, `test_full_pipeline_blocks_trading_on_macro_veto`, `test_confidence_score_matches_expected` (diem_tin_cay=0.229 vs EXPECTED 0.158) — xác nhận KHÔNG phải do thay đổi task (diff confidence_layer.py chỉ là wrap+BOM).
7. **Tests mới**: `test_health_v2_no_data.py` (7 tests) + `test_cafef_crawler_parser.py` (11 tests, thêm CURRENT_ASSETS/TOTAL_LIABILITIES) + `test_governor_sector.py` → 24 passed.
8. **ptck.py chưa commit**: thay đổi tại line 22-40 (Windows encoding fix block). File có mojibake nhưng đã patch bằng Python script (tool edit fail do ký tự lạ).
9. **Full suite GREEN 326/326** (commit f720e86): fix 3 failures + 1 skip pre-existing:
   - `test_confidence_score_matches_expected`: EXPECTED_CONFIDENCE 0.158 → **0.229** (đã verify công thức: raw weighted sum 7 yếu tố = 0.32649 × phạt cấu trúc 0.70 = 0.2285 → 0.229, đúng theo mô hình 7 factor mới + phạt phi tuyến VỠ CẤU TRÚC).
   - `test_stale_tracker_veto_active` + `test_full_pipeline_blocks_trading_on_macro_veto`: nguyên nhân là **data drift** (screener_cache.db macro giờ fresh 2026-07-31, fresh_ratio=100% vs test viết cho stale 17%). Fix deterministic: fixture DB stale riêng (EVICT 2020-01-01) + monkeypatch StaleTracker.update.
   - `test_backfill_engine_signature` (skip 745): hàm thật là `backfill(symbols, dry_run)` không phải `backfill_symbol` — sửa import + param check.
10. **hci-explain BCM sau sector Real Estate**: chain chuẩn — CREDIT_STRESS → LIQUIDITY_TRAP → Ngành Bất động sản (EARLY, score 2.2) → PRESALES → BCM HCI 0.24 ✖ VETO. Archetype REAL_ESTATE_DEVELOPER (STEADY_EARNER), Chain conf 0.68.

#### Active / Blocked
- Không còn failures — test suite GREEN **346/346** (340 baseline + 6 tests mới `test_archetype_icb_sector.py`).
- `cmd_hci_explain` vẫn còn module wrap unconditional tại function-level (ptck.py:2938-2939) — an toàn cho CLI nhưng chưa đổi sang reconfigure (không gây pytest fail vì không module-level).
- VNDirect/TCBS bridge: DNS vẫn UNVERIFIED từ môi trường dev (cần kiểm chứng live khi network cho phép).

### Session Follow-up #2 — Jul 31 2026: Cross-Sector Analysis + Archetype ICB Sector Fix

#### Work Completed
1. **Cross-sector analysis (Ngân hàng vs Bất động sản)**:
   - Ngân hàng (27 mã, Phase EARLY Score 9.6, Conf 0.711-0.726): FRANCHISE_BANK (CTG/TCB/BID 0.39, VCB 0.37, ACB 0.33) vs ASSET_BANK (0.32-0.33) — spread 0.09, attribution đồng nhất `policy_rate`.
   - Bất động sản (128 mã, Phase EARLY Score 2.2, Conf 0.681): toàn bộ REDUCE 0.35 (RETAIL_PLATFORM) trước fix; BCM 0.24 VETO + VHM 0.23 VETO (REAL_ESTATE_DEVELOPER).
   - **Phát hiện**: SIP/IDC/KBC (KCN điển hình) KHÔNG có health_ratios rows trong financial_facts.db → `_classify_by_ratios` rơi toàn bộ defaults → gán RETAIL_PLATFORM sai (chain SAME_STORE_SALES thay vì PRESALES).
2. **Fix archetype ICB sector** (`archetype.py`): thêm `_icb_sector()` (đọc icb_name2 từ screener_cache.db) + hard constraint trong `_classify_by_ratios`: ICB == "Bất động sản" → REAL_ESTATE_DEVELOPER (giống `_is_bank_symbol`). BĐS sector = mô hình phát triển dự án (PRESALES) bất kể ratio.
3. **Hệ quả sau fix**: toàn bộ 128 mã BĐS → REAL_ESTATE_DEVELOPER, VETO (0.27 median), spread thu hẹp 0.11 → 0.04 (VHM=0.23 → AAV=0.27); SIP/IDC/KBC → VETO (p_gain 0.27). Ngân hàng/COMPOUNDER/RETAIL_PLATFORM non-BĐS không đổi (MWG, FPT, SSI, VCB...).
4. **Tests mới** `backend/tests/test_archetype_icb_sector.py` (6 tests): KCN → REAL_ESTATE_DEVELOPER, non-BĐS không đổi, bank không bị chặn, `_icb_sector()` resolve. Full suite **346/346 PASS**.

#### Next Move
1. **VNDirect/TCBS API bridge** (commit `1906a11`): `fetch_vndirect_api()` + `fetch_tcbs_api()` + pure parsers. Fallback chain `vci`/`api`: VNDirect → TCBS → CafeF Bank API → NoteIndicator → synthetic. CLI: `python ptck.py cafef-crawl --symbols BCM --source api`. Status UNVERIFIED_DNS.
2. Khi DNS mở: chạy `python ptck.py cafef-crawl --symbols BCM --source api` → CFO + nợ vay vào financial_facts → `health-engine compute` + `health-v2` để Cash/DEBT thoát NO DATA.
3. Cân nhắc: BĐS 128 mã giờ VETO đồng loạt qua PRESALES chain — theo dõi nếu có mã nào cần ngoại lệ (ví dụ VRE là bán lẻ/REIT thuần).

#### Relevant Files
- `backend/src/governor/company_state.py` — sector mapping fix
- `backend/src/business/archetype.py` — `_icb_sector()` + ICB hard constraint (BĐS → REAL_ESTATE_DEVELOPER)
- `backend/tests/test_archetype_icb_sector.py` — 6 tests archetype ICB (SIP/IDC/KBC → REAL_ESTATE_DEVELOPER)
- `backend/src/financial/cafef_crawler.py` — parser mở rộng
- `backend/src/financial/company_health_engine.py` — DEBT fallback
- `backend/src/financial/company_health_v2.py` — NO_DATA protocol
- `backend/tests/test_health_v2_no_data.py`, `backend/tests/test_cafef_crawler_parser.py`, `backend/tests/test_governor_sector.py`
- `root/ptck.py` — Windows encoding fix block (patched)
- `backend/tests/test_decision_guard_integration.py` — wrap fixed, 3 logic failures fixed (f720e86)

### Status: ✅ P0+P1+P2 VERIFIED | ✅ P3 BAYESIAN GOVERNOR LIVE | ✅ P4 CALIBRATION WIRED | ✅ P5 COUNTERFACTUAL ONLINE

### Work Completed
1. **P0 MacroStateClassifier**: Bridges PTD pipeline → Governor. Output: discrete label + posterior + Shannon entropy + 7D normalized drivers + 18 raw macro values. Calibrated with interbank Override (max ON/1W/3M > 6% → CREDIT_STRESS). Persisted to `backend/data/macro/macro_state_history.json`.

2. **P1 EconomicTransmissionEngine**: 3 latent states (Liquidity 78.3, Credit 18.0, Confidence 53.6) + 6 transmission phases. Detected LIQUIDITY_TRAP ("capital waiting") — consistent with macro CREDIT_STRESS + high liquidity. Persisted to `backend/data/macro/transmission_history.json`.

3. **P1 SectorStateEngine**: 19 ICB supersectors ranked on Momentum(0.35)+Health(0.25)+Flow(0.25)+Valuation(0.15). All 19 weak (0 healthy). Banking leads EARLY phase. Rotation chain: Hóa chất→Ngân hàng→...→Dịch vụ tài chính. Persisted to `backend/data/macro/sector_rotation_latest.json`.

4. **P2 CompanyHealthV2**: 5-organ latent vector (Profitability, Cash, BalanceSheet, Efficiency, Moat) from health_ratios + financial_facts. 6 archetypes:
   - HIGH_QUALITY_COMPOUNDER: FPT(77%), HPG(84%), DGC(81%) — strong+cashy+moaty
   - STEADY_EARNER: ACB, HDB, MBB, VCB, VHM, MWG, GAS — solid but cash/moat below HQC threshold
   - DISTRESSED / CYCLICAL / TURNAROUND / LOW_QUALITY: (none detected)

5. **P3 BayesianGovernor**: Replaced hard-coded IF/THEN (7 rules cascade) with Bayesian Weight-of-Evidence:
   - 6 evidence nodes: MacroState(0.30) + Transmission(0.20) + Sector(0.15) + Health(0.15) + Valuation(0.10) + Behavior(0.10)
   - Prior P(Gain) = 0.53 → combined via log-odds → posterior P(Gain|Evidence)
   - Expected Utility matrix for 7 actions (VETO→AVOID→REDUCE→WAIT→HOLD→SCALE_IN→OPEN)
   - Kelly Criterion position sizing with entropy/credit calibration penalty
   - 30/07/2026 snapshot: All 10 symbols → REDUCE (P=39-41%, Alloc=-5% to -7%)
   - Correctly captures: good companies in bad macro (HQC + CHEAP still REDUCE due to CREDIT_STRESS+LIOUIDITY_TRAP dominance)

6. **P4 Calibration Layer** (Meta-Cognition):
   - `prediction_log.py` — SQLite persistence for every P3 prediction (date, symbol, P(Gain), EU, Kelly alloc, evidence vector)
   - `scoring.py` — Log-Loss (strictly proper primary loss), Brier Score, ECE, MCE, reliability curve
   - `calibrator.py` — Beta-posterior conjugate update: `α_new = α + y, β_new = β + (1-y)` per evidence level → `LR_new = (α/(α+β)) / (1-α/(α+β)) / prior_odds`
   - Auto-logging: every `BayesianGovernor.assess()` call inserts into `backend/data/calibration.db`
   - CLI: `python ptck.py calibrate {eval|update-beta|lrs|status}`

7. **P5 Counterfactual Reasoning**:
   - `counterfactual/engine.py` — 6 counterfactual scenarios: STABLE_MACRO, CREDIT_UNFREEZE, BULLISH_SECTOR, BEST_COMPANY, OPTIMISTIC, PESSIMISTIC
   - Per-node leverage analysis: reveals macro (63% of variance) ≫ transmission (37%) ≫ micro factors (<5%)
   - Key finding 30/07/2026: Even BEST_COMPANY (HQC+CHEAP+IN_VA) only adds +2.2% to P(Gain) — still REDUCE
   - CLI: `python ptck.py counterfactual [--symbols ...]`

8. **Giai đoạn 7 — ModelRegistry BMA competition**: Governor v3 with 8 evidence nodes (model_registry=0.15).
   - `compute_model_registry_lr()`: LR = 1 - 0.5 × P(M1_MACRO) — penalizes when macro model dominates
   - BMA posterior lazy-cached per batch: 30/07/2026 snapshot shows M1_MACRO=48%, M3=27%, M2=24%
   - Model LR=0.758 correctly reflects macro-driven uncertainty under CREDIT_STRESS
   - daily_updater Step 11c: feeds resolved outcomes into ModelRegistry.record_outcome()
   - CLI: `python ptck.py governor --symbols ...` (same CLI, now includes BMA line in header + GĐ7 in per-symbol detail)

9. **CLIs**: `macro-state`, `transmission`, `sector`, `health-v2`, `governor`, `calibrate`, `counterfactual` — all 7 in `ptck.py` + `SYSTEM_MANIFEST.yaml`.

10. **EOD Automation**: daily_updater.py Steps 6-10 (P0→P3). Step 11 (P4 logging) inline in govern.assess(). Step 11c (ModelRegistry outcome feed) after calibration resolve.

11. **Architecture**: 4-stage pipeline: Perception (P0–P2), Understanding (P3), Meta-Cognition (P4), Reasoning (P5). Self-correction via Beta posteriors O(1) + BMA model lifecycle (Sprint 4).

### Key Architectural Insight
All four layers converge: CREDIT_STRESS(0.30) + LIQUIDITY_TRAP(0.20) + BMA_M1_MACRO(0.15) = 0.65 evidence weight dominates even HIGH_QUALITY_COMPOUNDER(0.11) + CHEAP(0.09) + IN_VA(0.09) = 0.29. The BMA framework automatically weights competing market hypotheses, confirming that macro/systemic risk dominates micro quality under CREDIT_STRESS.

### Next Move
1. Run stress test / historical backtest once 30d forward data accumulates for the 73 pending predictions
2. Run counterfactual repeat with new evidence weights: "What if macro were STABLE?" now includes BMA weight shift (M2_FUNDAMENTAL would dominate, model_registry LR → ~1.0)
3. Monitor BMA posterior evolution as real outcomes resolve (73 unresolved predictions → will update M1/M2/M3 posterior weights via record_outcome)
