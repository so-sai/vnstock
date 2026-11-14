"""Kiểm thử hồi quy & invariant cho công cụ kiểm toán dòng tiền MSCI (NQ-25).

[WHY / ROOT CAUSE / INVARIANT]
WHY:
  Đảm bảo công thức quy đổi dòng tiền 4 nhóm MSCI, cổng fail-closed `Missing > Fake`,
  bộ lọc `VALID_ROW_SQL AND is_stale = 0`, phân loại DTL, nút thắt room ngoại,
  bẫy thanh khoản thoát hàng cho cổ phiếu suy yếu, nhãn `NON-DECISIONAL` và sổ
  cái `eod_write_audit_ledger` hoạt động đúng trong mọi trường hợp biên.
ROOT CAUSE:
  Không có test khóa hợp đồng thì tương lai dễ xuất hiện hành vi tự điền `0` cho
  AUM/FOL/room bị thiếu, tính ADV20 trên < 20 phiên, hoặc xóa nhãn cảnh báo ATC.
INVARIANT:
  1. Mọi đầu vào thiếu/chưa verify/phi lý phải trả exit code 2, không ghi ledger.
  2. Test thuần công thức chạy không cần DB; test DB dùng `tmp_path` độc lập.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

from src.research.msci_flow_auditor import (
    DEFAULT_MANIFEST_PATH,
    audit_msci_manifest,
    run_msci_survey,
)
from src.tools.msci_flow import (
    EXIT_OK,
    EXIT_REFUSED,
    STATUS_COMPUTED,
    STATUS_REFUSED_INSUFFICIENT_HISTORY,
    STATUS_REFUSED_INVALID_INPUT,
    STATUS_REFUSED_MISSING_DATA,
    STATUS_REFUSED_ZERO_LIQUIDITY,
    STATUS_SURVEY_OK,
    FundSpec,
    SymbolMarketSnapshot,
    SymbolSpec,
    classify_dtl,
    compute_symbol_flow,
)
from src.tools.msci_flow.manifest import load_manifest
from src.tools.msci_flow.report import render_markdown_report, serialize_run_json
from src.tools.msci_flow_estimate import main as cli_main


def _create_screener_db(
    db_path: Path,
    *,
    ohlcv_rows: list[tuple[str, str, float, float, float, float, float, float, str, int]],
    fx_rate: float | None = 25_000.0,
    fx_date: str = "2026-11-13",
) -> None:
    """Dựng SQLite `screener_cache.db` giả lập tối thiểu cho test độc lập."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(
            """
            CREATE TABLE daily_ohlcv (
                symbol TEXT NOT NULL,
                date TEXT NOT NULL,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                adj_close REAL,
                volume REAL,
                source TEXT,
                is_stale INTEGER DEFAULT 0
            );
            CREATE TABLE macro_history_v2 (
                variable TEXT NOT NULL,
                date TEXT NOT NULL,
                value REAL,
                asset_class TEXT,
                unit TEXT,
                source TEXT
            );
            """
        )
        if ohlcv_rows:
            conn.executemany(
                "INSERT INTO daily_ohlcv VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ohlcv_rows,
            )
        if fx_rate is not None:
            conn.execute(
                "INSERT INTO macro_history_v2 VALUES ('USD_VND', ?, ?, 'FX', 'VND', 'test_fx')",
                (fx_date, fx_rate),
            )
        conn.commit()
    finally:
        conn.close()


def _create_financial_db(
    db_path: Path,
    *,
    facts: list[tuple[str, str, str, float, int]] = (),
    health_distressed_symbol: str | None = None,
) -> None:
    """Dựng SQLite `financial_facts.db` tối thiểu để test Health V2 & NET_INCOME."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(
            """
            CREATE TABLE financial_facts (
                symbol TEXT NOT NULL,
                period TEXT NOT NULL,
                fiscal_year INTEGER,
                fiscal_quarter INTEGER,
                entity_type TEXT,
                statement_type TEXT,
                metric TEXT NOT NULL,
                value REAL,
                unit TEXT,
                source TEXT,
                is_synthetic INTEGER DEFAULT 0
            );
            CREATE TABLE health_ratios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                period TEXT NOT NULL,
                fiscal_year INTEGER,
                fiscal_quarter INTEGER,
                entity_type TEXT DEFAULT 'STD',
                ratio_name TEXT NOT NULL,
                ratio_value REAL
            );
            CREATE TABLE entity_registry (
                symbol TEXT PRIMARY KEY,
                entity_type TEXT DEFAULT 'STD'
            );
            """
        )
        for sym, period, metric, val, is_syn in facts:
            conn.execute(
                """
                INSERT INTO financial_facts
                    (symbol, period, fiscal_year, fiscal_quarter, entity_type,
                     statement_type, metric, value, unit, source, is_synthetic)
                VALUES (?, ?, 2026, 2, 'STD', 'IS', ?, ?, 'VND', 'vnstock_test', ?)
                """,
                (sym, period, metric, val, is_syn),
            )
        if health_distressed_symbol is not None:
            conn.execute(
                "INSERT INTO entity_registry (symbol, entity_type) VALUES (?, 'STD')",
                (health_distressed_symbol,),
            )
            # Bơm 4 kỳ tỷ số suy yếu (ROE/ROA âm, CFO/NI âm, DEBT_TO_EQUITY rất cao)
            distressed_ratios = [
                ("ROE", -0.25),
                ("ROA", -0.10),
                ("NET_MARGIN", -0.15),
                ("CFO_TO_NET_INCOME", -0.50),
                ("FCF_TO_NET_INCOME", -0.80),
                ("DEBT_TO_EQUITY", 8.0),
                ("DEBT_TO_ASSETS", 0.85),
                ("INTEREST_COVERAGE", 0.3),
            ]
            for q_idx, period in enumerate(["2025Q3", "2025Q4", "2026Q1", "2026Q2"]):
                for r_name, r_val in distressed_ratios:
                    conn.execute(
                        """
                        INSERT INTO health_ratios
                            (symbol, period, fiscal_year, fiscal_quarter, entity_type, ratio_name, ratio_value)
                        VALUES (?, ?, 2026, ?, 'STD', ?, ?)
                        """,
                        (health_distressed_symbol, period, q_idx + 1, r_name, r_val),
                    )
        conn.commit()
    finally:
        conn.close()


def _make_20_sessions(
    symbol: str,
    *,
    close: float = 20_000.0,
    volume: float = 500_000.0,
    count: int = 20,
    start: date = date(2026, 10, 19),
    stale_indices: set[int] | None = None,
    zero_close_indices: set[int] | None = None,
) -> list[tuple[str, str, float, float, float, float, float, float, str, int]]:
    rows = []
    stale_set = stale_indices or set()
    zero_set = zero_close_indices or set()
    for i in range(count):
        d = (start + timedelta(days=i)).isoformat()
        c = 0.0 if i in zero_set else close
        low = 0.0 if i in zero_set else close * 0.99
        high = 0.0 if i in zero_set else close * 1.01
        stale = 1 if i in stale_set else 0
        rows.append((symbol, d, c, high, low, c, c, volume, "vci", stale))
    return rows


def _write_manifest(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _valid_manifest_payload(symbols: list[dict], funds: list[dict] | None = None) -> dict:
    return {
        "review_name": "MSCI Q4-2026 Test",
        "announcement_date": "2026-11-12",
        "effective_date": "2026-11-30",
        "funds": funds
        if funds is not None
        else [
            {
                "fund_id": "FM_STD",
                "tier": "STANDARD",
                "aum_usd": 500_000_000.0,
                "aum_source": "iShares FactSheet",
                "aum_as_of": "2026-11-12",
                "verified": True,
            },
            {
                "fund_id": "FM_SC",
                "tier": "SMALLCAP",
                "aum_usd": 100_000_000.0,
                "aum_source": "Verified Tracker",
                "aum_as_of": "2026-11-12",
                "verified": True,
            },
        ],
        "symbols": symbols,
    }


def test_formula_purity_positive_negative_and_downgrade_dual_leg():
    """[WHY] Khóa công thức dòng tiền VND, cổ phiếu ước tính và DTL trên 3 nhóm ADD_STD, REMOVE_SC, DOWNGRADE."""
    std_fund = FundSpec(
        fund_id="FM_STD",
        tier="STANDARD",
        aum_usd=500_000_000.0,
        aum_source="iShares",
        aum_as_of="2026-11-12",
        verified=True,
    )
    sc_fund = FundSpec(
        fund_id="FM_SC",
        tier="SMALLCAP",
        aum_usd=100_000_000.0,
        aum_source="iShares SC",
        aum_as_of="2026-11-12",
        verified=True,
    )
    funds = (std_fund, sc_fund)
    fx = 25_000.0

    # 1. ADD_STD (PNJ): 500M * (0.40/100) * 1.0 * 25_000 = 50_000_000_000 VND (2,000,000 cp @ 25k, ADV20=1M => DTL=2.0)
    pnj_res = compute_symbol_flow(
        spec=SymbolSpec(symbol="PNJ", bucket="ADD_STD", delta_weight_pct=0.40, fol=1.0, verified=True),
        snapshot=SymbolMarketSnapshot(
            symbol="PNJ",
            last_trade_date="2026-11-13",
            close_vnd=25_000.0,
            adv20_shares=1_000_000.0,
            valid_session_count=20,
        ),
        funds=funds,
        fx_vnd_per_usd=fx,
    )
    assert pnj_res.status == STATUS_COMPUTED
    assert pnj_res.flow_std_vnd == pytest.approx(50_000_000_000.0)
    assert pnj_res.net_flow_vnd == pytest.approx(50_000_000_000.0)
    assert pnj_res.estimated_shares == pytest.approx(2_000_000.0)
    assert pnj_res.dtl == pytest.approx(2.0)
    assert pnj_res.dtl_bucket == "ELEVATED"

    # 2. REMOVE_SC (STK): 100M * (-0.30/100) * 0.5 * 25_000 = -3_750_000_000 VND (-375,000 cp @ 10k, ADV20=50k => DTL=7.5)
    stk_res = compute_symbol_flow(
        spec=SymbolSpec(symbol="STK", bucket="REMOVE_SC", delta_weight_pct=-0.30, fol=0.5, verified=True),
        snapshot=SymbolMarketSnapshot(
            symbol="STK",
            last_trade_date="2026-11-13",
            close_vnd=10_000.0,
            adv20_shares=50_000.0,
            valid_session_count=20,
        ),
        funds=funds,
        fx_vnd_per_usd=fx,
    )
    assert stk_res.status == STATUS_COMPUTED
    assert stk_res.flow_sc_vnd == pytest.approx(-3_750_000_000.0)
    assert stk_res.estimated_shares == pytest.approx(-375_000.0)
    assert stk_res.dtl == pytest.approx(7.5)
    assert stk_res.dtl_bucket == "DISTORTED"

    # 3. DOWNGRADE_STD_TO_SC (PC1): Standard ban -0.20% (-25B) + SmallCap mua +0.50% (+12.5B) -> net -12.5B
    pc1_res = compute_symbol_flow(
        spec=SymbolSpec(
            symbol="PC1",
            bucket="DOWNGRADE_STD_TO_SC",
            delta_weight_std_pct=-0.20,
            delta_weight_sc_pct=0.50,
            fol=1.0,
            verified=True,
        ),
        snapshot=SymbolMarketSnapshot(
            symbol="PC1",
            last_trade_date="2026-11-13",
            close_vnd=20_000.0,
            adv20_shares=2_000_000.0,
            valid_session_count=20,
        ),
        funds=funds,
        fx_vnd_per_usd=fx,
    )
    assert pc1_res.status == STATUS_COMPUTED
    assert pc1_res.flow_std_vnd == pytest.approx(-25_000_000_000.0)
    assert pc1_res.flow_sc_vnd == pytest.approx(12_500_000_000.0)
    assert pc1_res.net_flow_vnd == pytest.approx(-12_500_000_000.0)
    assert pc1_res.estimated_shares == pytest.approx(-625_000.0)
    assert pc1_res.dtl == pytest.approx(0.3125)
    assert pc1_res.dtl_bucket == "ABSORBED"


@pytest.mark.parametrize(
    ("mutator", "expected_status"),
    [
        ("missing_aum", STATUS_REFUSED_MISSING_DATA),
        ("missing_delta_weight", STATUS_REFUSED_MISSING_DATA),
        ("missing_fol", STATUS_REFUSED_MISSING_DATA),
        ("unverified_symbol", STATUS_REFUSED_MISSING_DATA),
        ("unverified_fund", STATUS_REFUSED_MISSING_DATA),
        ("fx_zero", STATUS_REFUSED_INVALID_INPUT),
        ("price_zero", STATUS_REFUSED_INSUFFICIENT_HISTORY),
    ],
)
def test_fail_closed_refuses_missing_unverified_and_zero_price_fx(tmp_path: Path, mutator: str, expected_status: str):
    """[WHY] Bất biến Missing > Fake: thiếu trường/unverified/FX=0/giá=0 trả exit 2, không ghi ledger."""
    screener_db = tmp_path / "screener.db"
    fin_db = tmp_path / "financial.db"
    _create_financial_db(fin_db)

    ohlcv = _make_20_sessions("PNJ", close=20_000.0, volume=500_000.0)
    if mutator == "price_zero":
        # Gắn phiên lỗi close=0 cho cả 20 phiên -> bị VALID_ROW_SQL lọc sạch -> 0 phiên hợp lệ
        ohlcv = _make_20_sessions("PNJ", close=20_000.0, volume=500_000.0, zero_close_indices=set(range(20)))
    _create_screener_db(screener_db, ohlcv_rows=ohlcv, fx_rate=25_000.0)

    payload = _valid_manifest_payload(
        symbols=[
            {
                "symbol": "PNJ",
                "bucket": "ADD_STD",
                "delta_weight_pct": 0.40,
                "fol": 1.0,
                "verified": True,
            }
        ]
    )
    if mutator == "missing_aum":
        payload["funds"][0]["aum_usd"] = None
    elif mutator == "missing_delta_weight":
        payload["symbols"][0]["delta_weight_pct"] = None
    elif mutator == "missing_fol":
        payload["symbols"][0]["fol"] = None
    elif mutator == "unverified_symbol":
        payload["symbols"][0]["verified"] = False
    elif mutator == "unverified_fund":
        payload["funds"][0]["verified"] = False
    elif mutator == "fx_zero":
        payload["fx_vnd_per_usd_override"] = 0.0

    manifest_file = _write_manifest(tmp_path / "manifest.json", payload)
    run = audit_msci_manifest(
        manifest_path=manifest_file,
        screener_db_path=screener_db,
        financial_db_path=fin_db,
        dry_run=False,
    )

    assert run.exit_code == EXIT_REFUSED
    assert run.status == expected_status
    assert run.audit_id is None
    with sqlite3.connect(str(screener_db)) as conn:
        tbls = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "eod_write_audit_ledger" not in tbls


def test_empty_template_manifest_refused_fail_closed(tmp_path: Path):
    """[WHY] Đảm bảo template commit sẵn (`msci_manifest_2026_11.json`) luôn từ chối với REFUSED_MISSING_DATA (exit 2)."""
    screener_db = tmp_path / "screener.db"
    fin_db = tmp_path / "financial.db"
    _create_screener_db(screener_db, ohlcv_rows=_make_20_sessions("PNJ"))
    _create_financial_db(fin_db)

    run = audit_msci_manifest(
        manifest_path=DEFAULT_MANIFEST_PATH,
        screener_db_path=screener_db,
        financial_db_path=fin_db,
        dry_run=True,
    )
    assert run.exit_code == EXIT_REFUSED
    assert run.status == STATUS_REFUSED_MISSING_DATA


def test_adv20_zero_and_insufficient_sessions_and_index_filter(tmp_path: Path):
    """[WHY] Cửa sổ 20 phiên sạch: ADV20=0 báo ZERO_LIQUIDITY, <20 phiên báo INSUFFICIENT_HISTORY, lọc INDEX_SYMBOLS."""
    screener_db = tmp_path / "screener.db"
    fin_db = tmp_path / "financial.db"
    _create_financial_db(fin_db)

    # Case 1: 20 phiên hợp lệ nhưng volume = 0 -> ZERO_LIQUIDITY (exit 2)
    rows_zero_vol = _make_20_sessions("RAL", close=70_000.0, volume=0.0, count=20)
    # Case 2: 20 dòng nhưng có 2 dòng is_stale=1 => chỉ còn 18 phiên sạch -> INSUFFICIENT_HISTORY
    rows_stale = _make_20_sessions("VFG", close=38_000.0, volume=10_000.0, count=20, stale_indices={0, 1})
    # Case 3: Chèn cả chỉ số VNINDEX 20 phiên để kiểm chứng STOCK_ONLY_SQL
    rows_idx = _make_20_sessions("VNINDEX", close=1_250.0, volume=600_000_000.0, count=20)
    _create_screener_db(screener_db, ohlcv_rows=rows_zero_vol + rows_stale + rows_idx, fx_rate=25_000.0)

    m_zero = _write_manifest(
        tmp_path / "m_zero.json",
        _valid_manifest_payload(
            [{"symbol": "RAL", "bucket": "REMOVE_SC", "delta_weight_pct": -0.10, "fol": 1.0, "verified": True}]
        ),
    )
    res_zero = audit_msci_manifest(manifest_path=m_zero, screener_db_path=screener_db, financial_db_path=fin_db)
    assert res_zero.exit_code == EXIT_REFUSED
    assert res_zero.status == STATUS_REFUSED_ZERO_LIQUIDITY

    m_stale = _write_manifest(
        tmp_path / "m_stale.json",
        _valid_manifest_payload(
            [{"symbol": "VFG", "bucket": "REMOVE_SC", "delta_weight_pct": -0.10, "fol": 1.0, "verified": True}]
        ),
    )
    res_stale = audit_msci_manifest(manifest_path=m_stale, screener_db_path=screener_db, financial_db_path=fin_db)
    assert res_stale.exit_code == EXIT_REFUSED
    assert res_stale.status == STATUS_REFUSED_INSUFFICIENT_HISTORY
    assert res_stale.results[0].valid_session_count == 18

    # Anti-index: cố gài VNINDEX vào manifest thì STOCK_ONLY_SQL chặn toàn bộ -> 0 phiên -> exit 2
    m_idx = _write_manifest(
        tmp_path / "m_idx.json",
        _valid_manifest_payload(
            [{"symbol": "VNINDEX", "bucket": "ADD_STD", "delta_weight_pct": 0.5, "fol": 1.0, "verified": True}]
        ),
    )
    res_idx = audit_msci_manifest(manifest_path=m_idx, screener_db_path=screener_db, financial_db_path=fin_db)
    assert res_idx.exit_code == EXIT_REFUSED
    assert res_idx.results[0].valid_session_count == 0


@pytest.mark.parametrize(
    ("dtl_val", "expected_bucket"),
    [
        (0.49, "ABSORBED"),
        (0.50, "ELEVATED"),
        (1.99, "ELEVATED"),
        (2.00, "ELEVATED"),
        (2.01, "DISTORTED"),
    ],
)
def test_dtl_exact_boundary_classification(dtl_val: float, expected_bucket: str):
    """[WHY] Khóa chính xác biên phân loại thanh khoản DTL (0.5 và 2.0) tránh lệch dấu < vs <=."""
    assert classify_dtl(dtl_val) == expected_bucket
    assert classify_dtl(None) is None


def test_foreign_room_zero_and_partial_bottleneck():
    """[WHY] Nút thắt room ngoại: room=0 bật ATC_DEMAND_ZERO; room nhỏ hơn lực mua giới hạn on_exchange_buyable_shares."""
    funds = (
        FundSpec(
            fund_id="FM_STD",
            tier="STANDARD",
            aum_usd=500_000_000.0,
            aum_source="iShares",
            aum_as_of="2026-11-12",
            verified=True,
        ),
    )
    snap = SymbolMarketSnapshot(
        symbol="PNJ",
        last_trade_date="2026-11-13",
        close_vnd=25_000.0,
        adv20_shares=1_000_000.0,
        valid_session_count=20,
    )
    # Tổng cầu = 2,000,000 cp
    # Case 1: available_room_shares == 0 -> ATC_DEMAND_ZERO
    r_zero = compute_symbol_flow(
        spec=SymbolSpec(
            symbol="PNJ",
            bucket="ADD_STD",
            delta_weight_pct=0.40,
            fol=1.0,
            foreign_room_pct=0.0,
            available_room_shares=0.0,
            verified=True,
        ),
        snapshot=snap,
        funds=funds,
        fx_vnd_per_usd=25_000.0,
    )
    assert r_zero.on_exchange_buyable_shares == pytest.approx(0.0)
    assert "ATC_DEMAND_ZERO" in r_zero.flags
    assert "ROOM_CONSTRAINED" in r_zero.flags
    assert "FOL_CONSTRAINED" in r_zero.flags

    # Case 2: Nghẽn cổ chai một phần: cần mua 2M cp nhưng chỉ còn room 350,000 cp
    r_partial = compute_symbol_flow(
        spec=SymbolSpec(
            symbol="PNJ",
            bucket="ADD_STD",
            delta_weight_pct=0.40,
            fol=1.0,
            foreign_room_pct=1.2,
            available_room_shares=350_000.0,
            verified=True,
        ),
        snapshot=snap,
        funds=funds,
        fx_vnd_per_usd=25_000.0,
    )
    assert r_partial.estimated_shares == pytest.approx(2_000_000.0)
    assert r_partial.on_exchange_buyable_shares == pytest.approx(350_000.0)
    assert "ROOM_CONSTRAINED" in r_partial.flags
    assert "ATC_DEMAND_ZERO" not in r_partial.flags

    # Case 3: Không khai báo room -> MISSING_FOREIGN_ROOM, giữ None (Missing > Fake)
    r_missing = compute_symbol_flow(
        spec=SymbolSpec(symbol="PNJ", bucket="ADD_STD", delta_weight_pct=0.40, fol=1.0, verified=True),
        snapshot=snap,
        funds=funds,
        fx_vnd_per_usd=25_000.0,
    )
    assert r_missing.on_exchange_buyable_shares is None
    assert "MISSING_FOREIGN_ROOM" in r_missing.flags


def test_no_smallcap_tracking_fund_outputs_zero_and_flags():
    """[WHY] Không có ETF mô phỏng Small Cap (NO_TRACKING_FUND): chân SC ước tính 0 và gắn cờ cảnh báo, không bịa AUM."""
    funds = (
        FundSpec(
            fund_id="FM_STD",
            tier="STANDARD",
            aum_usd=500_000_000.0,
            aum_source="iShares",
            aum_as_of="2026-11-12",
            verified=True,
        ),
        FundSpec(
            fund_id="FM_SC_NONE",
            tier="SMALLCAP",
            verified=True,
            no_tracking_fund=True,
        ),
    )
    # 1. Pure SmallCap add -> flow = 0, NO_TRACKING_FUND
    sc_only = compute_symbol_flow(
        spec=SymbolSpec(symbol="HII", bucket="ADD_SC", delta_weight_pct=0.25, fol=1.0, verified=True),
        snapshot=SymbolMarketSnapshot(
            symbol="HII",
            last_trade_date="2026-11-13",
            close_vnd=7_500.0,
            adv20_shares=600_000.0,
            valid_session_count=20,
        ),
        funds=funds,
        fx_vnd_per_usd=25_000.0,
    )
    assert sc_only.status == STATUS_COMPUTED
    assert sc_only.net_flow_vnd == pytest.approx(0.0)
    assert sc_only.estimated_shares == pytest.approx(0.0)
    assert "NO_TRACKING_FUND" in sc_only.flags
    assert "UNVERIFIED_SMALLCAP_AUM" in sc_only.flags

    # 2. Downgrade DIG: Standard leg van tinh am dung; SmallCap leg = 0
    dig = compute_symbol_flow(
        spec=SymbolSpec(
            symbol="DIG",
            bucket="DOWNGRADE_STD_TO_SC",
            delta_weight_std_pct=-0.10,
            delta_weight_sc_pct=0.30,
            fol=1.0,
            verified=True,
        ),
        snapshot=SymbolMarketSnapshot(
            symbol="DIG",
            last_trade_date="2026-11-13",
            close_vnd=10_000.0,
            adv20_shares=5_000_000.0,
            valid_session_count=20,
        ),
        funds=funds,
        fx_vnd_per_usd=25_000.0,
    )
    assert dig.flow_std_vnd == pytest.approx(-12_500_000_000.0)
    assert dig.flow_sc_vnd == pytest.approx(0.0)
    assert dig.net_flow_vnd == pytest.approx(-12_500_000_000.0)
    assert "NO_TRACKING_FUND" in dig.flags


def test_intrinsic_health_trap_pnj_vs_anti_overfitting_negative_flow(tmp_path: Path):
    """[WHY] Bắt bẫy PASSIVE_EXIT_LIQUIDITY_TRAP khi mua ròng mã DISTRESSED, chống vá quá tay khi bán ròng."""
    screener_db = tmp_path / "screener.db"
    fin_db = tmp_path / "financial.db"
    rows = _make_20_sessions("PNJ", close=15_000.0, volume=2_000_000.0) + _make_20_sessions(
        "STK", close=8_000.0, volume=100_000.0
    )
    _create_screener_db(screener_db, ohlcv_rows=rows, fx_rate=25_000.0)
    _create_financial_db(
        fin_db,
        facts=[
            ("PNJ", "2026Q2", "NET_INCOME", -739_000_000_000.0, 0),
            ("STK", "2026Q2", "NET_INCOME", -6_600_000_000.0, 0),
        ],
        health_distressed_symbol="PNJ",
    )

    manifest_file = _write_manifest(
        tmp_path / "m_health.json",
        _valid_manifest_payload(
            [
                {
                    "symbol": "PNJ",
                    "bucket": "ADD_STD",
                    "delta_weight_pct": 0.40,
                    "fol": 1.0,
                    "available_room_shares": 10_000_000.0,
                    "verified": True,
                },
                {
                    "symbol": "STK",
                    "bucket": "REMOVE_SC",
                    "delta_weight_pct": -0.20,
                    "fol": 1.0,
                    "verified": True,
                },
            ]
        ),
    )

    run = audit_msci_manifest(
        manifest_path=manifest_file,
        screener_db_path=screener_db,
        financial_db_path=fin_db,
        dry_run=True,
    )
    assert run.exit_code == EXIT_OK
    by_sym = {r.symbol: r for r in run.results}

    # PNJ dương tiền + DISTRESSED / NET_INCOME âm => PASSIVE_EXIT_LIQUIDITY_TRAP
    assert by_sym["PNJ"].health_archetype == "DISTRESSED"
    assert by_sym["PNJ"].net_income_vnd == pytest.approx(-739_000_000_000.0)
    assert "PASSIVE_EXIT_LIQUIDITY_TRAP" in by_sym["PNJ"].flags

    # Chống vá quá tay: STK cũng âm NET_INCOME nhưng REMOVE_SC (dòng tiền âm) => KHÔNG gắn cờ bẫy mua thụ động
    assert by_sym["STK"].net_flow_vnd is not None and by_sym["STK"].net_flow_vnd < 0
    assert "PASSIVE_EXIT_LIQUIDITY_TRAP" not in by_sym["STK"].flags


def test_mandatory_banners_ledger_dry_run_vs_apply_and_survey(tmp_path: Path):
    """[WHY] Nhãn bắt buộc NON-DECISIONAL + thiếu ATC trên MD/JSON; dry-run 0 dòng ledger vs apply 64 hex SHA-256; --survey."""
    screener_db = tmp_path / "screener.db"
    fin_db = tmp_path / "financial.db"
    _create_screener_db(screener_db, ohlcv_rows=_make_20_sessions("PNJ", close=15_000.0, volume=1_000_000.0))
    _create_financial_db(fin_db)

    manifest_file = _write_manifest(
        tmp_path / "m_ok.json",
        _valid_manifest_payload(
            [
                {
                    "symbol": "PNJ",
                    "bucket": "ADD_STD",
                    "delta_weight_pct": 0.30,
                    "fol": 1.0,
                    "available_room_shares": 5_000_000.0,
                    "verified": True,
                }
            ]
        ),
    )
    md_out = tmp_path / "out.md"
    json_out = tmp_path / "out.json"

    # 1. Dry-run: exit 0, COMPUTED, nhưng không ghi audit ledger
    dry_run_res = audit_msci_manifest(
        manifest_path=manifest_file,
        screener_db_path=screener_db,
        financial_db_path=fin_db,
        dry_run=True,
        md_out=md_out,
        json_out=json_out,
    )
    assert dry_run_res.exit_code == EXIT_OK
    assert dry_run_res.status == STATUS_COMPUTED
    assert dry_run_res.audit_id is None
    assert dry_run_res.input_hash is not None and len(dry_run_res.input_hash) == 64

    md_text = render_markdown_report(dry_run_res)
    json_payload = serialize_run_json(dry_run_res)
    for banner_needle in (
        "NON-DECISIONAL",
        "THIẾU DỮ LIỆU INTRADAY ATC — KHÔNG THỂ BACKTEST CHIẾN LƯỢC T+",
    ):
        assert banner_needle in md_text
        assert any(banner_needle in b for b in json_payload["banners"])
    assert "NON-DECISIONAL" in md_out.read_text(encoding="utf-8")
    assert len(json.loads(json_out.read_text(encoding="utf-8"))["results"]) == 1

    with sqlite3.connect(str(screener_db)) as conn:
        tbls = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "eod_write_audit_ledger" not in tbls

    # 2. Survey mode: đọc template chưa verified vẫn trả exit 0, flow=None, không ghi ledger
    survey_res = run_msci_survey(
        manifest_path=DEFAULT_MANIFEST_PATH,
        screener_db_path=screener_db,
        financial_db_path=fin_db,
    )
    assert survey_res.exit_code == EXIT_OK
    assert survey_res.status == STATUS_SURVEY_OK
    assert all(r.net_flow_vnd is None and r.estimated_shares is None for r in survey_res.results)

    # 3. Apply run: ghi đúng 1 dòng COMPUTED kèm SHA-256 64 hex
    apply_code = cli_main(
        [
            "--manifest",
            str(manifest_file),
            "--screener-db",
            str(screener_db),
            "--financial-db",
            str(fin_db),
        ]
    )
    assert apply_code == EXIT_OK

    with sqlite3.connect(str(screener_db)) as conn:
        rows = conn.execute(
            "SELECT caller_module, target_table, status, length(input_hash) FROM eod_write_audit_ledger"
        ).fetchall()
    assert rows == [("msci_flow_estimate", "msci_flow_estimate", "COMPUTED", 64)]

    # 4. CLI exit 2 trên template rỗng
    empty_code = cli_main(
        [
            "--manifest",
            str(DEFAULT_MANIFEST_PATH),
            "--screener-db",
            str(screener_db),
            "--financial-db",
            str(fin_db),
            "--dry-run",
        ]
    )
    assert empty_code == EXIT_REFUSED


def test_default_manifest_basket_classification_matches_nq26():
    """[WHY] Đối soát phân loại 15 mã rổ MSCI Q4-2026 theo NQ-26 (NLG thuộc ADD_STD, không phải ADD_SC)."""
    spec = load_manifest(DEFAULT_MANIFEST_PATH)
    by_bucket: dict[str, set[str]] = {}
    for s in spec.symbols:
        by_bucket.setdefault(s.bucket, set()).add(s.symbol)
    assert by_bucket["ADD_STD"] == {"PNJ", "NLG"}
    assert by_bucket["DOWNGRADE_STD_TO_SC"] == {"DIG", "FTS", "PC1"}
    assert by_bucket["ADD_SC"] == {"HII", "HPA", "HVH", "MZG"}
    assert by_bucket["REMOVE_SC"] == {"DBD", "GHC", "JVC", "RAL", "STK", "VFG"}
