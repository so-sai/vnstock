# ECL v1 — Execution Contract Layer

> **Status:** DRAFT | **Target:** v1.0.0 | **Parent:** AGENTS.md (constitution)
> **Purpose:** Deployment determinism boundary for Sentinel Fortress .exe

---

## 1. Architecture Split

```
┌─────────────────────────────────────────────────────┐
│                 SENTINEL FORTRESS                     │
│  ┌──────────────────────┐ ┌──────────────────────┐   │
│  │  EXECUTION CORE       │ │  OBSERVABILITY LAYER │   │
│  │  (deterministic)      │ │  (non-blocking)      │   │
│  │  affects decisions    │ │  monitoring only     │   │
│  └──────────┬───────────┘ └──────────┬───────────┘   │
│             │                        │                │
│             ▼                        ▼                │
│        Decision Path           Telemetry + Shadow     │
└─────────────────────────────────────────────────────┘
```

---

## 2. Module Classification

### 2.1 EXECUTION CORE — Decision-Affecting

These modules **must** produce identical outputs given identical inputs across machines.

| Module | Path | Contract |
|--------|------|----------|
| **Decision Tensor v1** | `backend/src/portfolio/decision_tensor.py` | `compute()` → DecisionAction |
| **Decision Tensor v2** | `backend/src/portfolio/decision_tensor_v2.py` | `compute_v2()`, `compute_v2_decayed()` → CognitiveDecision |
| **Decision Fusion** | `backend/src/portfolio/decision_fusion.py` | `arbitrate()` |
| **Regime Engine** | `backend/src/engine/regime_engine.py` | `detect_regime()` |
| **RSI Regime** | `backend/src/engine/rsi_regime_engine.py` | `analyze_rsi_habitat()` |
| **Breadth Engine** | `backend/src/engine/breadth_engine.py` | `run_breadth_analysis()` |
| **RS Ranker** | `backend/src/engine/rs_ranker.py` | `calculate_rs_score()` |
| **Screener Logic** | `backend/src/engine/screener_logic.py` | `run_screener()` |
| **Mean Rev Engine** | `backend/src/engine/meanrev_engine.py` | `run_meanrev_scan()` |
| **Liquidity Wave** | `backend/src/engine/liquidity_wave.py` | `get_market_liquidity_health()`, `scan_liquidity_waves()` |
| **Sector Rotation** | `backend/src/engine/sector_rotation_graph.py` | `get_rotation_beta()` |
| **Breakout Continuation** | `backend/src/engine/breakout_continuation.py` | `get_breakout_market_context()` |
| **Flow Decay Engine** | `backend/src/engine/flow_decay_engine.py` | All decayed signal functions |
| **Exposure Engine** | `backend/src/portfolio/exposure_engine.py` | Heat, dampener, throttle |
| **Memory Engine** | `backend/src/portfolio/memory_engine.py` | Model performance tracking |
| **Position Sizer** | `backend/src/portfolio/position_sizer.py` | Size calculation |
| **Risk Budget** | `backend/src/portfolio/risk_budget.py` | Budget allocation |
| **Risk Governor** | `backend/src/portfolio/risk_governor_engine.py` | Throttle/governor |
| **Gold Regime Engine** | `core/macro/gold_regime_engine.py` | `analyze_gold_regime()` |
| **Gold Spread Engine** | `core/macro/gold_spread_engine.py` | `analyze_domestic_premium()` |
| **Gold Provenance** | `core/macro/gold_provenance.py` | `register_gold_nodes()` |
| **Holdings Models** | `core/holdings/models.py` | HoldingPosition, HoldingsView |
| **Holdings Exposure** | `core/holdings/exposure_engine.py` | `load_portfolio_positions()` |
| **Holdings View Builder** | `core/holdings/holdings_view_builder.py` | `build_holdings_view()` |
| **Schema Guard** | `core/guard/schema_lock.py` | `lock_schema()` |
| **Safe Mode** | `core/guard/safe_mode.py` | Safe mode trigger |
| **Color Engine** | `core/guard/color_engine.py` | Status color mapping |
| **Signal Provenance** | `core/signal_provenance/` | ProvenanceRegistry, graph |
| **Presentation Layer** | `core/presentation/` | decision_view, state_labels, narrative_matcher |
| **Data Quality Core** | `backend/src/core/data_quality/` | QualityScoreEngine, EventRegistry |
| **Trust Accumulator** | `backend/src/cao_validation/trust_accumulator.py` | Confidence accumulation |
| **Regime Promotion Matrix** | `backend/src/cao_validation/regime_promotion_matrix.py` | Promotion thresholds |
| **Activation Gate** | `backend/src/cao_validation/activation_gate.py` | Promotion verdict |
| **Consistency Engine** | `backend/src/cao_validation/consistency_engine.py` | Statistical consistency |
| **Shadow-Live Comparator** | `backend/src/cao_validation/shadow_live_comparator.py` | Distribution tests |
| **CAO Validation Models** | `backend/src/cao_validation/models.py` | Validation data models |
| **Market State Coordinator** | `backend/src/core/market_state_coordinator.py` | `build_market_state()` |
| **Unit Normalizer** | `backend/src/engine/unit_normalizer.py` | Price unit conversion |
| **Daily Updater** | `backend/src/daily_updater.py` | Data pipeline orchestration |
| **Daily Closer** | `backend/src/daily_closer.py` | Market close processing |

### 2.2 OBSERVABILITY LAYER — Non-Blocking

These modules **never** affect decisions. If they fail or produce different results, the core decision path is unchanged.

| Module | Path | Reason |
|--------|------|--------|
| **Shadow CAO** | `backend/src/shadow_cao/` | Full module — ablation, attribution, belief, scheduler. Async, non-blocking. |
| **Telemetry** | `backend/src/telemetry/` | Recording, evaluation, attribution. Read-only by design. |
| **PSR Snapshot** | `backend/src/core/psr/snapshot.py` | State capture at decision time. Observability only. |
| **PSR Replay** | `backend/src/core/psr/replay.py` | Deterministic replay for debugging. |
| **PSR Version** | `backend/src/core/psr/version.py` | Version manifest. |
| **PSR Audit** | `backend/src/core/psr/audit.py` | Append-only audit trail. |
| **CAO Readiness** | `backend/src/cao_readiness/` | Pre-flight gates. Only runs on startup. |
| **All Services** | `backend/src/services/*.py` | Service layer (dashboard, macro, portfolio, screener, etc.) |
| **All API Routes** | `backend/src/api/routes/*.py` | FastAPI endpoints. |
| **Data Quality Monitor** | `backend/src/core/data_quality/hooks.py` | Non-blocking DQ hooks. |
| **Gold Services** | `backend/src/services/macro/gold_service.py`, `gold_world_service.py` | API wrapper + external fetch. |
| **Capital Flow Engines** | `backend/src/engine/capital_flow_forecasting_engine.py`, `capital_displacement_engine.py` | Experimental. Not in decision path. |
| **Sentinel Alert** | `backend/src/engine/sentinel_alert.py` | Alert system. |
| **Heatmap Engine** | `backend/src/engine/heatmap_engine.py` | Dashboard visualization. |
| **Dashboard Engine** | `backend/src/engine/dashboard_engine.py` | Dashboard aggregation. |
| **Elite Scanner** | `backend/src/engine/elite_scanner.py` | Institutional-grade signals (experimental). |
| **IPO Engine** | `backend/src/engine/ipo_engine.py` | IPO signals (experimental). |
| **Recovery Engine** | `backend/src/engine/recovery_engine.py` | Drawdown recovery detection. |
| **Strategy Commander** | `backend/src/engine/strategy_commander.py` | Multi-strategy orchestration (experimental). |
| **Backtest Engine** | `backend/src/engine/backtest_engine.py` | Historical replay. Not in production path. |
| **Universe** | `backend/src/engine/universe.py` | Universe selection (config-only). |
| **Shadow Tracker** | `backend/src/portfolio/shadow_tracker.py` | Paper trading ledger. |
| **Liquidity Concentration** | `core/flow/liquidity_concentration_engine.py` | Flow analysis (dashboard). |
| **Decision History File** | `backend/data/decision_history.json` | Log file. Not in contract. |
| **All Utils** | `backend/src/utils/*.py` | Utility scripts. |

---

## 3. Deterministic Runtime Definition

### 3.1 Random Seed Lock

```python
# Must be called at process start (run_sentinel.py:main)
SEED_CAO = 42
np.random.seed(SEED_CAO)
random.seed(SEED_CAO)
```

### 3.2 Timezone Lock

```python
# All datetime operations must use Asia/Ho_Chi_Minh
TZ = pytz.timezone("Asia/Ho_Chi_Minh")
# Never use datetime.now() without tz
# Never use pd.Timestamp("now") without tz
```

### 3.3 Float Precision Lock

```python
# All decision-critical floats rounded to 4 decimal places
DECISION_PRECISION = 4
action_score = round(raw_score, DECISION_PRECISION)
```

### 3.4 External API Behavior

| API | Strategy | Fallback |
|-----|----------|---------|
| **yfinance (GC=F)** | Cache to DB. If fetch fails, use last cached value + stale flag. | `last_cached_value`, stale=True |
| **Gold world service** | DB-first. External fetch is async. | Same as yfinance |
| **vnstock API** | DB-first. Daily batch fills the vault. | Read from screener_cache.db |
| **vnai** | Mocked in vendor partition. | `libs/vnstock/` patched fork |

---

## 4. Dependency Freeze Strategy

### 4.1 requirements-core.txt

```txt
# EXECUTION CORE — Pinned versions
# Generated: yyyy-mm-dd
pandas==2.3.3
numpy==2.2.6
pydantic==2.11.3
pytz==2025.2
python-dateutil==2.9.0
requests==2.32.3
tenacity==9.1.2
importlib-metadata==8.7.0
packaging==25.0
beautifulsoup4==4.14.3
# FastAPI is part of observability layer, NOT core
# EXCLUDED: pyinstaller (build-time only)
```

### 4.2 requirements-observability.txt

```txt
# OBSERVABILITY LAYER — Less strict
fastapi>=0.115.0
uvicorn[standard]>=0.34.0
matplotlib>=3.5.0
seaborn>=0.12.0
openpyxl>=3.0.0
python-dotenv>=1.0.0
psutil>=5.0.0
# Frontend static files bundled separately
```

### 4.3 Version Lock Enforcement

At startup, `run_sentinel.py` must verify:

```python
from src.core.ecl.verifier import verify_dependency_lock
verify_dependency_lock()  # exits if mismatch
```

---

## 5. Execution Contract Hash

### 5.1 Definition

The execution contract hash is a SHA-256 digest of every file in the execution core.

```python
import hashlib
from pathlib import Path

EXECUTION_CORE_PATHS = [
    "backend/src/portfolio/decision_tensor.py",
    "backend/src/portfolio/decision_tensor_v2.py",
    "backend/src/portfolio/decision_fusion.py",
    "backend/src/portfolio/exposure_engine.py",
    "backend/src/portfolio/memory_engine.py",
    "backend/src/portfolio/position_sizer.py",
    "backend/src/portfolio/risk_budget.py",
    "backend/src/portfolio/risk_governor_engine.py",
    "backend/src/engine/regime_engine.py",
    "backend/src/engine/rsi_regime_engine.py",
    "backend/src/engine/breadth_engine.py",
    "backend/src/engine/rs_ranker.py",
    "backend/src/engine/screener_logic.py",
    "backend/src/engine/meanrev_engine.py",
    "backend/src/engine/liquidity_wave.py",
    "backend/src/engine/sector_rotation_graph.py",
    "backend/src/engine/breakout_continuation.py",
    "backend/src/engine/flow_decay_engine.py",
    "backend/src/engine/unit_normalizer.py",
    "backend/src/core/data_quality/",
    "backend/src/cao_validation/",
    "core/macro/gold_regime_engine.py",
    "core/macro/gold_spread_engine.py",
    "core/macro/gold_provenance.py",
    "core/holdings/",
    "core/guard/",
    "core/signal_provenance/",
    "core/presentation/",
]

EXECUTION_CONTRACT_VERSION = "ECL_v1.0.0"

def compute_execution_hash(project_root: Path) -> str:
    hasher = hashlib.sha256()
    hasher.update(EXECUTION_CONTRACT_VERSION.encode())
    for rel_path in sorted(EXECUTION_CORE_PATHS):
        full = project_root / rel_path
        if full.is_dir():
            for f in sorted(full.rglob("*.py")):
                hasher.update(f.read_bytes())
        elif full.exists():
            hasher.update(full.read_bytes())
    return hasher.hexdigest()[:32]
```

### 5.2 Verification at Startup

```python
# In run_sentinel.py:main()
from src.core.ecl.verifier import compute_execution_hash
expected_hash = "STORED_HASH_FROM_BUILD"
actual_hash = compute_execution_hash(PROJECT_ROOT)
assert actual_hash == expected_hash, (
    f"Execution contract broken: {actual_hash} != {expected_hash}"
)
```

### 5.3 Automatic Hash Stamping

During .exe build:

```python
# build step: compute + embed
hash_value = compute_execution_hash(project_root)
with open("backend/src/core/ecl/.build_hash", "w") as f:
    f.write(hash_value)
# Then .exe reads this at startup
```

---

## 6. .exe Packaging Blueprint

### 6.1 Build Order

```
Step 1: Compute execution contract hash
Step 2: Stamp hash into backend/src/core/ecl/.build_hash
Step 3: npm run build (frontend)
Step 4: pip install -r backend/requirements-core.txt
Step 5: PyInstaller --clean sentinel_fortress.spec
Step 6: Verify executable starts
```

### 6.2 Updated sentinel_fortress.spec

Key changes from current spec:

```python
# ADDED:
datas += [(str(project_root / "backend" / "src" / "core" / "ecl"), "src/core/ecl")]

# HIDDEN IMPORTS — Core only:
hiddenimports = [
    # Core execution modules
    "src.portfolio.decision_tensor",
    "src.portfolio.decision_tensor_v2",
    "src.portfolio.decision_fusion",
    "src.portfolio.exposure_engine",
    "src.portfolio.memory_engine",
    "src.portfolio.position_sizer",
    "src.portfolio.risk_budget",
    "src.portfolio.risk_governor_engine",
    "src.engine.regime_engine",
    "src.engine.rsi_regime_engine",
    "src.engine.breadth_engine",
    "src.engine.rs_ranker",
    "src.engine.screener_logic",
    "src.engine.meanrev_engine",
    "src.engine.liquidity_wave",
    "src.engine.sector_rotation_graph",
    "src.engine.breakout_continuation",
    "src.engine.flow_decay_engine",
    "src.engine.unit_normalizer",
    "src.core.data_quality",
    "src.cao_validation.consistency_engine",
    "src.cao_validation.trust_accumulator",
    "src.cao_validation.regime_promotion_matrix",
    "src.cao_validation.shadow_live_comparator",
    "src.cao_validation.activation_gate",
    "core.macro.gold_regime_engine",
    "core.macro.gold_spread_engine",
    "core.macro.gold_provenance",
    "core.holdings.models",
    "core.holdings.exposure_engine",
    "core.holdings.holdings_view_builder",
    "core.guard.schema_lock",
    "core.guard.safe_mode",
    "core.guard.color_engine",
    "core.signal_provenance",
    "core.presentation",
]
```

### 6.3 Post-Build Verification

```powershell
# After build, run:
.\dist\Sentinel_Fortress_v1.0.exe --ecl-verify
# Expected output:
#   ECL v1.0.0 — Execution Contract INTACT
#   Hash: a1b2c3d4... (32 chars)
#   Decision path: DETERMINISTIC
```

---

## 7. Implementation Files

| File | Purpose |
|------|---------|
| `backend/src/core/ecl/__init__.py` | Package entry, re-exports |
| `backend/src/core/ecl/verifier.py` | `compute_execution_hash()`, `verify_dependency_lock()`, `ecl_self_check()` |
| `backend/src/core/ecl/classifier.py` | `is_execution_core(path)`, `is_observability(path)` — module classification at runtime |
| `backend/src/core/ecl/.build_hash` | Stamped hash file (gitignored, generated at build time) |
| `backend/requirements-core.txt` | Pinned core dependencies |
| `backend/requirements-observability.txt` | Observability deps (not in .exe core) |
| `sentinel_fortress.spec` | Updated PyInstaller spec with ECL-aware hidden imports |

---

## 8. Deployment Rules

### Rule 1 — Core-Only Mode
The .exe shall run without any observability module. All shadow_cao, telemetry, psr imports shall be behind `try/except ImportError` guards.

### Rule 2 — Deterministic Fallback
Every external API call in the execution core must have a deterministic fallback (cached value + stale flag). No API call shall block the decision path.

### Rule 3 — Build-Test-Release
Every .exe build must:
1. Run `kit doctor --heal` on the source tree
2. Compute execution contract hash
3. Run all tests with `pytest tests/ -m "not slow"`
4. Build .exe
5. Run .exe in --ecl-verify mode
6. Tag git commit with the execution hash

---

## 9. Migration Path

### Phase 1 — ECL Spec (this document)
### Phase 2 — Create `backend/src/core/ecl/` package
### Phase 3 — Wrap all execution-core external calls with deterministic fallback
### Phase 4 — Split requirements files
### Phase 5 — Update sentinel_fortress.spec
### Phase 6 — Build + test .exe
### Phase 7 — Sealing: `kit stats` + `vantage seal`

---

*ECL v1.0.0 — Deployment determinism boundary*  
*Parent: AGENTS.md (constitution)*
