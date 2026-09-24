# -*- coding: utf-8 -*-
"""Paper watch bid freeze — zero-hallucination paper stance after EOD.

PNJ: close below invalidation => DROPPED (do not catch falling knife).
HPG: close == hard line => RED_ALERT (drop next bar < 20800).
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.portfolio.paper_watch_state import (
    apply_eod_invalidation,
    load_paper_watch,
    save_paper_watch,
)


def test_pnj_dropped_on_break():
    state = load_paper_watch()
    state = apply_eod_invalidation(
        state,
        {"PNJ": 33300.0, "HPG": 20800.0},
        as_of="2026-09-24",
    )
    pnj = next(x for x in state["bids"] if x["symbol"] == "PNJ")
    assert pnj["status"] == "DROPPED"
    assert pnj["last_close"] == 33300.0
    assert pnj["invalidation"] == 35275.0


def test_hpg_red_alert_at_line():
    state = load_paper_watch()
    state = apply_eod_invalidation(
        state,
        {"PNJ": 33300.0, "HPG": 20800.0},
        as_of="2026-09-24",
    )
    hpg = next(x for x in state["bids"] if x["symbol"] == "HPG")
    assert hpg["status"] == "RED_ALERT"
    assert hpg["last_close"] == 20800.0
    assert hpg["hard_drop_below"] == 20800.0


def test_hpg_hard_drop_below_line():
    state = load_paper_watch()
    state = apply_eod_invalidation(
        state,
        {"PNJ": 35500.0, "HPG": 20700.0},
        as_of="2026-09-24",
    )
    hpg = next(x for x in state["bids"] if x["symbol"] == "HPG")
    assert hpg["status"] == "DROPPED"


def test_save_roundtrip(tmp_path):
    state = load_paper_watch()
    path = tmp_path / "paper_watch_state.json"
    assert save_paper_watch(state, path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["entry_lock"] is True
    assert any(b["symbol"] == "PNJ" for b in data["bids"])
