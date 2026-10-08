"""test_daily_brief_builder.py — tầng narrative (spec: docs/SPEC_NARRATIVE_LAYER_2026-10.md).

Bảo vệ các bất biến:
  - Thiếu dữ liệu → "N/A", KHÔNG crash, KHÔNG bịa.
  - Khoá mà không nêu được gate fail → gắn cờ NARRATIVE_CONTRADICTION.
  - Chỉ cộng foreign có flow_kind='daily' (không lẫn legacy_level).
"""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "backend" / "scripts" / "daily_brief_builder.py"

SCHEMA = """
CREATE TABLE daily_ohlcv (symbol TEXT, date TEXT, close REAL, volume REAL, is_stale INTEGER DEFAULT 0);
CREATE TABLE regime_history (date TEXT, regime_score REAL, status TEXT, breadth_pct REAL);
CREATE TABLE market_foreign_history (symbol TEXT, date TEXT, foreign_vol INTEGER, net_vol INTEGER,
                                     net_value REAL, flow_kind TEXT DEFAULT 'daily');
"""


def _load():
    spec = importlib.util.spec_from_file_location("brief_builder", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _inputs(**over):
    base = {
        "as_of": "2026-10-08",
        "expected_date": "2026-10-08",
        "is_stale": False,
        "generated_at": "2026-10-08T15:00:00+07:00",
        "sources": ["daily_ohlcv:as_of=2026-10-08", "regime_history:2026-10-08"],
        "regime": {"date": "2026-10-08", "status": "CRISIS", "score": 0.28,
                   "breadth_pct": 31.1, "status_streak": 4, "breadth_delta": 3.0},
        "foreign": {"date": "2026-10-08", "d1": -942.7, "days": [("2026-10-08", -942.7)],
                    "sum": -3042.0, "n_days": 3, "streak": 3, "direction": "bán ròng", "sign": -1,
                    "missing_sessions": []},
        "watchlist": [{"symbol": "HPG", "net_value": 63.5, "close": 20500.0, "volume": 1.3e7}],
        "entry_lock": True,
        "gates": {"regime_ok": False, "breadth_ok": False, "foreign_ok": False},
        "failed_gates": ["regime_ok", "breadth_ok", "foreign_ok"],
        "narrative_contradiction": False,
        "notes": [],
    }
    base.update(over)
    return base


def test_du_du_lieu_render_du_5_muc():
    mod = _load()
    b = mod.render_brief(_inputs())
    for h in ("## 1. Trạng thái thị trường", "## 2. Quyết định", "## 3. Watchlist",
              "## 4. Việc tiếp theo", "## 5. Nguồn dữ liệu & độ tươi"):
        assert h in b
    assert "N/A" not in b.split("## 3.")[0], "mục 1-2 không được có N/A khi đủ dữ liệu"


def test_thieu_regime_thi_NA_khong_crash():
    mod = _load()
    b = mod.render_brief(_inputs(regime=None, notes=["regime_history không có dòng nào <= as_of."]))
    assert "- Regime: **N/A**" in b
    assert "## 5. Nguồn dữ liệu" in b  # vẫn render phần còn lại


def test_thieu_foreign_thi_NA_khong_crash():
    mod = _load()
    b = mod.render_brief(_inputs(foreign=None, notes=["market_foreign_history không có dòng flow_kind='daily' nào."]))
    assert "- Dòng ngoại: **N/A**" in b


def test_khoa_va_co_gate_fail_thi_khong_gan_co():
    mod = _load()
    inp = _inputs()  # entry_lock=True + 3 gate fail
    assert inp["narrative_contradiction"] is False
    assert "NARRATIVE_CONTRADICTION" not in mod.render_brief(inp)


def test_khoa_ma_khong_gate_nao_fail_thi_phai_gan_co():
    """Invariant #3: mâu thuẫn phải bị phát hiện, không được làm mượt câu chữ."""
    mod = _load()
    inp = _inputs(entry_lock=True, failed_gates=[],
                  gates={"regime_ok": True, "breadth_ok": True, "foreign_ok": True},
                  narrative_contradiction=True)
    assert "NARRATIVE_CONTRADICTION" in mod.render_brief(inp)


def test_verify_pass_tren_inputs_tot():
    mod = _load()
    inp = _inputs()
    brief = mod.render_brief(inp)
    results = mod.verify(brief, inp)
    assert len(results) == 3
    assert all(ok for _, ok, _ in results), results


def test_audit_record_du_key_va_khop():
    mod = _load()
    inp = _inputs()
    rec = mod.build_audit_record(inp)
    for k in ("ts", "as_of", "command", "decision", "inputs", "gates", "failed_gates",
              "narrative_contradiction", "sources"):
        assert k in rec
    assert rec["as_of"] == inp["as_of"]
    assert rec["decision"]["entry_lock"] is True
    assert rec["inputs"]["foreign"]["d1"] == -942.7


# ── DB-level: chỉ cộng flow_kind='daily' ───────────────────────────────
@pytest.fixture()
def memdb():
    c = sqlite3.connect(":memory:")
    c.executescript(SCHEMA)
    yield c
    c.close()


def _seed_session(c, date, n=300):
    for i in range(n):
        c.execute(
            "INSERT INTO daily_ohlcv (symbol,date,close,volume,is_stale) VALUES (?,?,?,?,0)",
            (f"{date.replace('-','')}{i:04d}", date, 10000.0, 1000.0),
        )
    c.commit()


def test_foreign_chi_cong_flow_kind_daily(memdb):
    mod = _load()
    _seed_session(memdb, "2026-10-08")
    _seed_session(memdb, "2026-10-07")
    # legacy_level có |net_value| khổng lồ — nếu lọt vào tổng thì vô nghĩa
    memdb.executemany(
        "INSERT INTO market_foreign_history (symbol,date,net_value,flow_kind) VALUES (?,?,?,?)",
        [
            ("AAA", "2026-10-08", -500.0, "daily"),
            ("BBB", "2026-10-08", -442.7, "daily"),
            ("AAA", "2026-10-08", -106_613_841.0, "legacy_level"),
            ("AAA", "2026-10-07", -900.0, "daily"),
        ],
    )
    memdb.commit()
    fo = mod.fetch_foreign(memdb, "2026-10-08")
    assert fo["d1"] == pytest.approx(-942.7), "chỉ được cộng daily"
    assert fo["streak"] == 2 and fo["direction"] == "bán ròng"


def test_streak_dut_khi_thieu_phien_ngoai(memdb):
    """BUG đã sửa: thiếu ngoại 1 phiên thì chuỗi phải ĐỨT, không đếm vắt qua lỗ hổng."""
    mod = _load()
    for d in ("2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08"):
        _seed_session(memdb, d)
    memdb.executemany(
        "INSERT INTO market_foreign_history (symbol,date,net_value,flow_kind) VALUES (?,?,?,?)",
        [
            ("AAA", "2026-10-08", -100.0, "daily"),
            ("AAA", "2026-10-07", -200.0, "daily"),
            # 2026-10-06 THIẾU dòng ngoại
            ("AAA", "2026-10-05", -300.0, "daily"),
        ],
    )
    memdb.commit()
    fo = mod.fetch_foreign(memdb, "2026-10-08")
    assert fo["streak"] == 2, "chuỗi phải đứt tại 10-06, không được là 3"
    assert fo["n_days"] == 2
    assert fo["gap_after"] is True
    assert fo["missing_sessions"] == ["2026-10-06", "2026-10-05"], "phải nêu rõ phiên khuyết"


# ── Điểm 1: idempotency audit trail ────────────────────────────────────
def test_audit_dedup_bo_qua_ban_ghi_tuong_duong(tmp_path):
    mod = _load()
    p = tmp_path / "audit_trail.jsonl"
    rec = mod.build_audit_record(_inputs())
    _, w1 = mod.append_audit(rec, path=p)
    _, w2 = mod.append_audit(dict(rec, ts="2026-10-08T16:00:00+07:00"), path=p)  # chỉ khác ts
    assert w1 is True and w2 is False, "lần 2 phải là no-op"
    assert len(p.read_text(encoding="utf-8").strip().splitlines()) == 1


def test_audit_van_ghi_khi_quyet_dinh_khac(tmp_path):
    mod = _load()
    p = tmp_path / "audit_trail.jsonl"
    mod.append_audit(mod.build_audit_record(_inputs()), path=p)
    mod.append_audit(mod.build_audit_record(_inputs(as_of="2026-10-09")), path=p)
    assert len(p.read_text(encoding="utf-8").strip().splitlines()) == 2


# ── Điểm 2 & 4: cảnh báo stale và ghi chú khoảng trống ─────────────────
def test_stale_thi_co_canh_bao_dau_brief():
    mod = _load()
    b = mod.render_brief(_inputs(is_stale=True, as_of="2026-10-07", expected_date="2026-10-08"))
    assert "CẢNH BÁO KIỂM TOÁN (DATA STALE)" in b
    assert b.index("CẢNH BÁO KIỂM TOÁN") < b.index("## 1."), "cảnh báo phải ở ĐẦU brief"


def test_khong_stale_thi_khong_co_canh_bao():
    mod = _load()
    assert "CẢNH BÁO KIỂM TOÁN" not in mod.render_brief(_inputs(is_stale=False))


def test_gap_thi_co_ghi_chu_chuoi_du_lieu():
    mod = _load()
    b = mod.render_brief(
        _inputs(foreign={"date": "2026-10-08", "d1": -900.0, "days": [("2026-10-08", -900.0)],
                        "sum": -900.0, "n_days": 1, "streak": 1, "direction": "bán ròng", "sign": -1,
                        "missing_sessions": ["2026-10-06", "2026-10-05"]})
    )
    assert "Ghi chú chuỗi dữ liệu" in b
    assert "2026-10-05" in b and "Missing > Fake" in b


def test_latest_clean_date_theo_nguong_300(memdb):
    mod = _load()
    _seed_session(memdb, "2026-10-08")
    for i in range(50):  # ngày chỉ có 50 dòng sạch → không đủ ngưỡng
        memdb.execute("INSERT INTO daily_ohlcv (symbol,date,close,volume,is_stale) VALUES (?,?,?,?,0)",
                      (f"T{i:04d}", "2026-10-09", 10000.0, 1000.0))
    memdb.commit()
    assert mod.latest_clean_date(memdb) == "2026-10-08"
