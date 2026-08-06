"""executive_report.py — 5-Model Consolidated Executive Report (pure builder).

Aggregates the 5 analytical tiers into a single explainable dict for the
final-decision CLI. PURE: never queries the DB, never runs engines. It only
renders data that is PASSED IN (kq / vn20 / volume_profile / rs).

Zero-Hallucination contract:
  - Every section renders ONLY from its real source.
  - Missing source -> status="NO_DATA" with a human reason. NEVER fabricate.
  - Numeric provenance is preserved (no transform that alters the value).

Usage:
    report = xay_dung_bao_cao_5_mo_hinh(kq, vn20=..., volume_profile=..., rs=...)
"""

from __future__ import annotations

from typing import Any


def _v(d: Any, key: str, default: Any = None):
    """Safely read nested dict value."""
    if not isinstance(d, dict):
        return default
    return d.get(key, default)


def _m1_macro(kq: dict) -> dict:
    """M1 — MACRO & LRI (nguồn: kq['lri'])."""
    lri = _v(kq, "lri")
    if not isinstance(lri, dict):
        return {"status": "NO_DATA", "reason": "thiếu dữ liệu kq['lri']"}
    comp = _v(lri, "components", {})
    return {
        "status": "OK",
        "lri_score": _v(lri, "score"),
        "lri_regime": _v(lri, "regime"),
        "max_allocation_pct": _v(lri, "max_allocation_pct"),
        "interbank_on": _v(comp, "interbank_on"),
        "interbank_regime": _v(comp, "interbank_regime"),
        "usd_vnd": _v(comp, "usd_vnd"),
        "usdvnd_deviation_pct": _v(comp, "usdvnd_deviation_pct"),
        "omo_proxy": _v(comp, "omo_proxy"),
        "fii_flow_10d": _v(comp, "fii_flow_10d"),
        "breadth_pct": _v(comp, "breadth_pct"),
    }


def _m2_fundamental(vn20: Any) -> dict:
    """M2 — FUNDAMENTAL / VN20 GATE (nguồn: vn20_quant_filter result)."""
    if not isinstance(vn20, dict):
        return {"status": "NO_DATA", "reason": "thiếu dữ liệu vn20 (chạy vn20-filter trước)"}
    qualified = _v(vn20, "qualified", [])
    top_mos = []
    for item in qualified:
        if isinstance(item, dict) and _v(item, "symbol") and _v(item, "mos") is not None:
            top_mos.append(
                {
                    "symbol": _v(item, "symbol"),
                    "sector": _v(item, "sector"),
                    "phase": _v(item, "phase"),
                    "mos": _v(item, "mos"),
                    "score": _v(item, "score"),
                }
            )
    top_mos.sort(key=lambda x: (x["mos"] is not None, x["mos"] or 0.0), reverse=True)
    return {
        "status": "OK",
        "period": _v(vn20, "period"),
        "stage_counts": _v(vn20, "stage_counts", {}),
        "n_qualified": len(top_mos),
        "top_mos": top_mos[:5],
    }


def _m3_behavioral(kq: dict, volume_profile: Any) -> dict:
    """M3 — BEHAVIORAL / DDI + FII + VOLUME PROFILE.

    Nguồn DDI: kq['delta_divergence']; nguồn FII: lri.components.fii_flow_10d;
    nguồn Volume Profile: absorption detector (truyền vào).
    """
    ddi = _v(kq, "delta_divergence")
    if not isinstance(ddi, dict):
        return {"status": "NO_DATA", "reason": "thiếu dữ liệu kq['delta_divergence']"}
    lri = _v(kq, "lri", {})
    comp = _v(lri, "components", {})
    section = {
        "status": "OK",
        "delta_sa": _v(ddi, "delta_sa"),
        "dS_dt": _v(ddi, "dS_dt"),
        "ac_latency": _v(ddi, "ac_latency"),
        "action_filter": _v(ddi, "action_filter"),
        "healing_illusion": _v(ddi, "healing_illusion"),
        "fii_flow_10d": _v(comp, "fii_flow_10d"),
    }
    if isinstance(volume_profile, dict):
        section["volume_profile"] = {
            "poc_price": _v(volume_profile, "poc_price"),
            "val": _v(volume_profile, "val"),
            "vah": _v(volume_profile, "vah"),
            "price_current": _v(volume_profile, "price_current"),
            "price_in_value_area": _v(volume_profile, "price_in_value_area"),
            "volume_ratio": _v(volume_profile, "volume_ratio"),
            "volume_converged": _v(volume_profile, "volume_converged"),
            "vol_value_area": _v(volume_profile, "vol_value_area"),
            "vol_total_5d": _v(volume_profile, "vol_total_5d"),
        }
    else:
        section["volume_profile"] = None
    return section


def _m4_alpha(rs: Any) -> dict:
    """M4 — ALPHA / RS RATING (nguồn: rs_ranker result DataFrame/list)."""
    if rs is None:
        return {"status": "NO_DATA", "reason": "thiếu dữ liệu rs (radar RS chưa chạy)"}
    rows = []
    if hasattr(rs, "to_dict") and hasattr(rs, "head"):  # pandas DataFrame
        items = rs.head(5).to_dict("records")
    elif isinstance(rs, list):
        items = rs[:5]
    else:
        return {"status": "NO_DATA", "reason": "nguồn rs không đúng định dạng"}
    for item in items:
        if isinstance(item, dict) and _v(item, "symbol") is not None:
            rows.append(
                {
                    "symbol": _v(item, "symbol"),
                    "rs_rating": _v(item, "rs_rating"),
                    "price": _v(item, "price"),
                    "avg_vol_20d": _v(item, "avg_vol_20d"),
                }
            )
    if not rows:
        return {"status": "NO_DATA", "reason": "danh sách rs rỗng"}
    return {"status": "OK", "top_rs": rows}


def _m5_governor(kq: dict) -> dict:
    """M5 — GOVERNOR & DECISION GUARD (nguồn: kq fields)."""
    conf = _v(kq, "độ_tin_cậy_sau_hiệu_chỉnh")
    return {
        "status": "OK",
        "he_so_giam_ty_trong": _v(kq, "he_so_giam_ty_trong"),
        "confidence": _v(conf, "điểm_số") if isinstance(conf, dict) else None,
        "confidence_level": _v(conf, "mức") if isinstance(conf, dict) else None,
        "blocked": _v(kq, "bi_chặn_bởi_bảo_vệ"),
        "block_reason": _v(kq, "lý_do_chặn"),
        "recovery_status": _v(kq, "recovery_status"),
        "healing_status": _v(kq, "healing_status"),
    }


def xay_dung_bao_cao_5_mo_hinh(
    kq: dict,
    vn20: Any = None,
    volume_profile: Any = None,
    rs: Any = None,
) -> dict:
    """Build the 5-model consolidated executive report dict."""
    return {
        "M1_MACRO": _m1_macro(kq),
        "M2_FUNDAMENTAL": _m2_fundamental(vn20),
        "M3_BEHAVIORAL": _m3_behavioral(kq, volume_profile),
        "M4_ALPHA": _m4_alpha(rs),
        "M5_GOVERNOR": _m5_governor(kq),
    }
