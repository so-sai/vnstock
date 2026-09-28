"""conftest.py — Fixtures cho Test Suite hồi quy 3 chốt chặn Production.

Nguyên tắc cách ly (isolation):
  - Mọi test dùng portfolio_id/symbol/variable có tiền tố '__TEST__' để KHÔNG
    bao giờ đụng chạm dữ liệu paper trading / macro thật.
  - Fixture tự động dọn dẹp (teardown) sau mỗi test → DB luôn sạch.
  - Test chạy trên DB thật (screener_cache.db) vì engine hard-code connection,
    nhưng chỉ thao tác trên namespace test biệt lập.
"""
import pathlib
import sys

import pytest


def _hydrate_path():
    current = pathlib.Path(__file__).resolve().parent
    root_path = current
    while current != current.parent:
        if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
            root_path = current
            break
        current = current.parent
    for p in [str(root_path / "backend" / "src"), str(root_path / "backend"), str(root_path)]:
        if p not in sys.path:
            sys.path.append(p)
    # Guarantee: PROJECT_ROOT at index 0 for core/ resolution
    sp = str(root_path)
    if sp in sys.path:
        sys.path.remove(sp)
    sys.path.insert(0, sp)
    return root_path


PROJECT_ROOT = _hydrate_path()

TEST_PORTFOLIO = "__TEST__PF"
TEST_SYMBOL = "__TESTSYM__"
TEST_MACRO_PREFIX = "__TEST__"

# Lịch giao dịch giả lập (đủ để test T+2.5 lifecycle) — dùng cho daily_ohlcv test.
TEST_DATES = [
    "2099-01-04", "2099-01-05", "2099-01-06", "2099-01-07",
    "2099-01-08", "2099-01-11", "2099-01-12", "2099-01-13",
]


def _clean_test_data():
    """Xóa mọi dữ liệu test khỏi DB (idempotent)."""
    from src.database.db_core import get_connection
    with get_connection() as conn:
        paper_tables = [
            "paper_portfolio_state", "paper_lots", "paper_cash_ledger",
            "paper_realized_pnl", "paper_equity_curve", "paper_trades_log",
            "paper_performance_daily", "paper_catchup_queue",
        ]
        for t in paper_tables:
            try:
                conn.execute(f"DELETE FROM {t} WHERE portfolio_id=?",
                             (TEST_PORTFOLIO,))
            except Exception:
                pass
        try:
            conn.execute("DELETE FROM paper_corporate_actions WHERE symbol=?",
                         (TEST_SYMBOL,))
        except Exception:
            pass
        try:
            conn.execute("DELETE FROM daily_ohlcv WHERE symbol=?", (TEST_SYMBOL,))
        except Exception:
            pass
        try:
            conn.execute("DELETE FROM macro_history WHERE variable LIKE ?",
                         (TEST_MACRO_PREFIX + "%",))
        except Exception:
            pass
        try:
            conn.execute("DELETE FROM eod_run_ledger WHERE portfolio_id=?",
                         (TEST_PORTFOLIO,))
        except Exception:
            pass
        conn.commit()


@pytest.fixture
def clean_db():
    """Đảm bảo DB sạch trước và sau test."""
    _clean_test_data()
    yield
    _clean_test_data()


@pytest.fixture(autouse=True)
def reset_global_caches():
    """Test Contract 4 — Isolation Guard: xóa sạch cache toàn cục sau mỗi test.

    Ngăn ô nhiễm trạng thái (State Contamination) làm kết quả test phụ thuộc
    thứ tự chạy (test order dependency).
    """
    yield
    try:
        from src.utils.cache_guard import clear_all_caches

        clear_all_caches()
    except Exception:  # noqa: BLE001, S110 - isolation guard không được crash test
        pass


@pytest.fixture(scope="session", autouse=True)
def isolate_macro_state_history(tmp_path_factory):
    """P1 (E7, 2026-09-28) — test KHÔNG được ghi macro_state_history.json THẬT.

    Nguồn phát hiện: 31 bản ghi trùng ngày 2022-01-07 trong
    backend/data/macro/macro_state_history.json (đã dedupe ở E6).

    Đường ghi:
        test_replay_ledger_2022 → backtest.replay_ledger_2022.run_day
        → governor.company_state.BayesianGovernor._load_macro_pit('2022-01-07')
        → MacroStateClassifier().classify() → _save_state()
        → ghi thẳng vào persistence_dir mặc định = backend/data/macro (PRODUCTION)

    Hậu quả: mỗi lần chạy test, record 2022-01-07 bị recompute và ghi đè
    (posterior flip-flop 0.324 → 0.3237 → 0.3242 qua các lần chạy) → đếm record
    vẫn đúng nhưng NỘI DUNG bị nhiễu → provenance mất.

    Cách ly: copy snapshot 1 lần/session vào tmp, redirect MACRO_STATE_DIR sang
    đó. Test đọc được dữ liệu như thật, mọi ghi chỉ vào shadow copy.
    """
    import shutil

    from src.core.macro import macro_state_classifier as msc

    real_dir = msc.MACRO_STATE_DIR
    shadow = tmp_path_factory.mktemp("macro_state_history_shadow")
    real_file = real_dir / "macro_state_history.json"
    if real_file.exists():
        shutil.copy2(real_file, shadow / "macro_state_history.json")
    msc.MACRO_STATE_DIR = shadow
    yield
    msc.MACRO_STATE_DIR = real_dir


_SANDBOX: dict = {}


def _sandbox_configure() -> None:
    """E10 (2026-09-28) — Test Sandbox: toàn bộ suite KHÔNG được ghi vào backend/data thật.

    Nguồn phát hiện (full suite trước fix, md5 thật): 16 file backend/data đổi nội
    dung, 2 thêm, 2 xóa — git status vẫn sạch vì data/ bị gitignore.
    Attribution (stat per-test + md5 probe):
      - calibration.db             ← test_daily_cycle_orchestrator, test_replay_ledger_2022,
                                     test_v1_epistemic_api (ModelRegistry._db_path)
      - screener_cache.db          ← test_catchup, test_catchup_execution, test_scheduler,
                                     test_production_gates, test_sensor_validation
                                     (toàn bộ qua db_core.get_connection/DB_PATH)
      - macro_warmup_state.json    ← test_decision_guard_{emergency,integration}
      - output/snapshot_index.json ← test_decision_guard_integration (tao_anh_chup)
      - output/market_pulse.json   ← test_csi_scan_breadth_fallback,
                                     test_decision_guard_integration,
                                     test_session_info_whitelist
      - output/danh_gia_tin_cay.json, output/index_reality.json ← test_decision_guard_integration
      - reports/ablation_studies/* ← test_walk_forward_harness, test_replay_integration
      - psr/audit.jsonl + SNAP_*, shadow_cao.db ← test_recent_fixes
      - probe_cache/sel_w1_history.json ← MacroGovernor → StructureEvolutionLayer.assess()

    Cách ly: chạy lúc pytest_configure (TRƯỚC khi pytest import test module → an toàn
    cả với `from x import CONST` value-copy), copy snapshot 1 lần vào tmp, redirect
    mọi điểm ghi (module constant + staticmethod) sang shadow. Test đọc dữ liệu
    như thật; mọi ghi chỉ vào tmp. Mỗi module patch cả 2 alias ('src.x' và 'x').
    """
    import importlib
    import shutil
    import tempfile

    from src.config import DATA_DIR as _DATA

    data = pathlib.Path(_DATA)
    shadow = pathlib.Path(tempfile.mkdtemp(prefix="ptck_sandbox_"))
    patches: list[tuple[object, str, object]] = []

    def _snapshot(rel: str) -> pathlib.Path:
        dst = shadow / rel
        src_file = data / rel
        if src_file.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_file, dst)
        return dst

    def _copytree(rel: str) -> pathlib.Path:
        dst = shadow / rel
        if (data / rel).is_dir():
            shutil.copytree(data / rel, dst, dirs_exist_ok=True)
        else:
            dst.mkdir(parents=True, exist_ok=True)
        return dst

    def _patch(mod_name: str, attr: str, value) -> None:
        parts = attr.split(".")
        for alias in (mod_name, mod_name.split(".", 1)[-1]):
            try:
                mod = importlib.import_module(alias)
            except ImportError:
                continue
            target = mod
            try:
                for p in parts[:-1]:
                    target = getattr(target, p)
            except AttributeError:
                continue
            if parts[-1] not in vars(target) and not hasattr(target, parts[-1]):
                continue
            old = vars(target)[parts[-1]] if parts[-1] in vars(target) else getattr(target, parts[-1])
            patches.append((target, parts[-1], old))
            setattr(target, parts[-1], value)

    calib = _snapshot("calibration.db")
    _patch("src.calibration.prediction_log", "CALIB_DB", calib)
    _patch("src.calibration.evidence_ledger", "CALIB_DB", calib)
    _patch("src.backtest.replay_ledger_2022", "LEDGER_DB", calib)
    _patch("src.orchestration.daily_cycle_orchestrator", "_cycle_db_path", lambda: str(calib))
    _patch("src.calibration.model_registry", "ModelRegistry._db_path", staticmethod(lambda: calib))
    _patch("src.calibration.causal_edge", "CausalGraph._db_path", staticmethod(lambda: calib))

    screener = _snapshot("screener_cache.db")
    for side in ("screener_cache.db-wal", "screener_cache.db-shm"):
        _snapshot(side)
    _patch("src.database.db_core", "DB_PATH", str(screener))

    fin = _snapshot("financial_facts.db")
    for side in ("financial_facts.db-wal", "financial_facts.db-shm"):
        _snapshot(side)
    for mod_name, attr in (
        ("src.financial.financial_facts", "FINANCIAL_DB_PATH"),
        ("src.financial.forensic_engine", "FINANCIAL_DB_PATH"),
        ("src.financial.fingerprint_compiler", "FINANCIAL_DB"),
        ("src.financial.market_behavior_engine", "FINANCIAL_DB"),
        ("src.financial.company_health_v2", "FINANCIAL_DB"),
        ("src.audit.data_integrity_auditor", "FINANCIAL_DB"),
        ("src.backtest.replay_ledger_2022", "FINANCIAL_FACTS_DB"),
        ("src.backtest.walk_forward_harness", "FINANCIAL_DB_PATH"),
        ("src.backtest.grid_search_v2", "FINANCIAL_DB_PATH"),
        ("src.backtest.ablation_5model", "FINANCIAL_DB_PATH"),
        ("src.backtest.unified_system_replay", "FINANCIAL_DB_PATH"),
        ("src.business.archetype", "FINANCIAL_DB"),
        ("src.business.competitive_position", "FINANCIAL_DB"),
        ("src.governor.company_state", "FINANCIAL_DB"),
        ("src.governor.fair_multiple_engine", "FINANCIAL_DB"),
        ("src.quant.vn20_quant_filter", "FIN_DB"),
        ("src.research.opportunity_score_research", "FIN_DB"),
    ):
        _patch(mod_name, attr, str(fin) if attr == "FINANCIAL_DB" and mod_name.endswith("company_health_v2") else fin)

    out_shadow = _copytree("output")
    _patch("src.core.market_snapshot", "SNAPSHOT_OUTPUT_DIR", out_shadow)
    _patch("src.engine.breadth_engine", "PULSE_OUTPUT_DIR", out_shadow)
    _patch("src.engine.confidence_layer", "OUTPUT_DIR", out_shadow)
    _patch("src.engine.index_reality_unifier", "OUTPUT_DIR", out_shadow)
    _patch("src.core.market_state_coordinator", "OUTPUT_DIR", out_shadow)
    _patch("src.research.ftse_event_ledger", "OUTPUT_DIR", out_shadow)

    reports = _copytree("reports/ablation_studies")
    for mod in (
        "src.backtest.walk_forward_harness",
        "src.backtest.grid_search_v2",
        "src.backtest.ablation_5model",
        "src.backtest.unified_system_replay",
    ):
        _patch(mod, "REPORTS_DIR", reports)
    _patch("src.backtest.grid_search_v2", "LRI_CACHE_PATH", reports / "grid_lri_cache.json")

    warmup = _snapshot("macro_warmup_state.json")
    _patch(
        "src.engine.macro_stale_tracker",
        "StaleTracker._warmup_path",
        lambda self, _p=warmup: _p,
    )

    psr_shadow = _copytree("psr")
    _patch("src.core.psr.audit", "AUDIT_DIR", psr_shadow)
    _patch("src.core.psr.audit", "AUDIT_FILE", psr_shadow / "audit.jsonl")
    _patch("src.core.psr.snapshot", "PSR_DIR", psr_shadow)
    _patch("src.core.psr.snapshot", "DATA_DIR", psr_shadow)

    shadow_db = _snapshot("shadow_cao.db")
    _patch("src.shadow_cao.storage", "SHADOW_DB_PATH", str(shadow_db))

    probe = shadow / "probe_cache"
    w1 = _snapshot("probe_cache/sel_w1_history.json")
    reg = _snapshot("probe_cache/sel_regime_library.json")
    _patch("src.engine.structure_evolution", "W1_LIBRARY_FILE", w1)
    _patch("src.engine.structure_evolution", "REGIME_LIBRARY_FILE", reg)
    _patch("src.engine.structure_evolution", "STATE_DIR", probe)

    _SANDBOX["shadow"] = shadow
    _SANDBOX["patches"] = patches


def pytest_configure(config) -> None:
    if not _SANDBOX:
        _sandbox_configure()


def sandbox_shadow() -> pathlib.Path | None:
    """Thư mục shadow của Test Sandbox (None nếu chạy ngoài pytest)."""
    return _SANDBOX.get("shadow")


def pytest_unconfigure(config) -> None:
    import shutil

    for mod, attr, old in reversed(_SANDBOX.get("patches", [])):
        setattr(mod, attr, old)
    shadow = _SANDBOX.pop("shadow", None)
    _SANDBOX.pop("patches", None)
    if shadow is not None:
        shutil.rmtree(shadow, ignore_errors=True)


@pytest.fixture
def seed_test_ohlcv(clean_db):
    """Bơm dữ liệu OHLCV giả lập cho TEST_SYMBOL trên TEST_DATES.

    Giá tăng dần đều để P&L dễ kiểm chứng. Trả về dict {date: close}.
    """
    from src.database.db_core import get_connection
    closes = {}
    base = 10000.0
    with get_connection() as conn:
        for i, d in enumerate(TEST_DATES):
            close = base + i * 100.0  # tăng 100đ/phiên
            open_ = close - 50.0
            closes[d] = close
            conn.execute(
                "INSERT OR REPLACE INTO daily_ohlcv "
                "(symbol, date, open, high, low, close, adj_close, volume, source) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (TEST_SYMBOL, d, open_, close + 100, open_ - 100, close,
                 close, 1_000_000, "TEST")
            )
        conn.commit()
    return closes


@pytest.fixture
def mtm(seed_test_ohlcv):
    """PaperMtM instance cách ly, vốn 1 tỷ."""
    from src.engine.paper_mtm import PaperMtM
    engine = PaperMtM(portfolio_id=TEST_PORTFOLIO,
                      initial_capital=1_000_000_000.0)
    return engine
