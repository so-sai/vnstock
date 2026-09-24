# -*- coding: utf-8 -*-
"""Regression: create_markdown_report must not KeyError on Model B picks.

Mean-rev engine emits distance_from_ma50/rsi — never z_score.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_markdown_report_tolerates_missing_z_score(tmp_path, monkeypatch):
    import src.config
    from src.daily_closer import create_markdown_report

    monkeypatch.setattr(src.config, "DATA_DIR", tmp_path)

    verdict = {
        "decision": {
            "market_status": "RANGING",
            "regime_score": 0.5,
            "active_model": "B",
            "consensus": "WAIT FOR NICHES",
            "confidence": 0.22,
            "model_b": {
                "top_picks": [
                    {
                        "symbol": "MSB",
                        "distance_from_ma50": -6.21,
                        "rsi": 33.7,
                        # no z_score key
                    }
                ]
            },
            "recovery": {
                "status": "NONE",
                "ma10_reclaim": False,
                "details": {"velocity_5d": 1.0},
            },
        },
        "layer1_mom_expansion": {"status": "OFF", "value": 0, "threshold": 1},
        "layer2_nh10_consistency": {"status": "OFF", "value": 0, "threshold": 1},
        "layer3_foreign_absorption": {"status": "OFF"},
    }

    path = create_markdown_report(verdict, "2099-01-01")
    text = Path(path).read_text(encoding="utf-8")
    assert "MSB" in text
    assert "RSI: 33.7" in text


def test_markdown_report_minimal_board(tmp_path, monkeypatch):
    """decision_board-shaped payload (no layer1/recovery) must not crash."""
    import src.config
    from src.daily_closer import create_markdown_report

    monkeypatch.setattr(src.config, "DATA_DIR", tmp_path)
    board = {
        "market_status": "CRISIS",
        "regime_score": 0.3,
        "active_model": "NONE",
        "consensus": "PILOT ABORT (EXIT IMMEDIATELY)",
        "confidence": 0.0,
        "model_b": {
            "top_picks": [
                {"symbol": "MSB", "distance_from_ma50": -6.21, "rsi": 33.7},
            ]
        },
        "cognitive_modulation": {"driver_confidence": None, "drift_status": None, "ets_score": None},
    }
    path = create_markdown_report({"decision": board}, "2099-01-01")
    text = Path(path).read_text(encoding="utf-8")
    assert "MSB" in text
    assert "PILOT ABORT" in text
