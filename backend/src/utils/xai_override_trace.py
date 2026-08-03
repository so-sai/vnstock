"""xai_override_trace.py — Causal Override Trace (XAI transparency).

Prints a point-in-time causal chain when the Governor's final decision
differs from the raw Daily-Report signal. Goal: no silent override — every
VETO/law/punishment that changed the decision is shown on the CLI.

Usage (from orchestrator.in_bao_cao):
    from utils.xai_override_trace import print_override_trace
    print_override_trace(kq)
"""

from src.utils.cli_theme import c_cyan, c_dim, c_green, c_red, c_yellow

# Action → display label + icon
_ACTION_ICON = {
    "THAM GIA FULL": "💎",
    "THAM GIA": "🟢",
    "THAM GIA DO": "🔵",
    "QUAN SAT": "🟡",
    "GIAM RUI RO": "🟠",
    "DUNG NGOAI": "🔴",
    "N/A": "⚪",
}

_ACTION_VN = {
    "THAM GIA FULL": "THAM GIA FULL",
    "THAM GIA": "THAM GIA (PARTICIPATE)",
    "THAM GIA DO": "THAM GIA DO",
    "QUAN SAT": "QUAN SAT (WATCH)",
    "GIAM RUI RO": "GIAM RUI RO",
    "DUNG NGOAI": "DUNG NGOAI (STAND ASIDE)",
}


def _label(action: str) -> str:
    return _ACTION_VN.get(action, action)


def _veto_reasons(kq: dict) -> list[str]:
    """Derive the VETO chain from the decision detail fields."""
    reasons = []
    ct = kq.get("chi_tiet", {})
    ldo = kq.get("ly_do_chặn") or ""

    # 1. Structural breakdown
    cau_truc = ct.get("cau_truc", "")
    so_tru = ct.get("so_tru_cau_truc", 0)
    if cau_truc == "VỠ CẤU TRÚC" or (isinstance(so_tru, int) and so_tru <= 1):
        reasons.append(f"[LAW-006_VETO] Cấu trúc thị trường : ❌ BỊ CHẶN ({so_tru}/3 trụ cột ổn định)")

    # 2. DDI / delta divergence gate
    ddi = kq.get("delta_divergence", {})
    if ddi.get("action_filter") == "block" or ddi.get("healing_illusion"):
        reasons.append(f"[DDI_GATE] Delta Divergence   : ❌ BỊ CHẶN (Δ_SA={ddi.get('delta_sa')} healing illusion)")

    # 3. Index reality override
    if "tăng giả tạo" in " ".join(kq.get("ly_do", [])):
        reasons.append("[INDEX_REALITY] Chỉ số        : ❌ TĂNG GIẢ TẠO — không đại diện")
    if "thị trường ảo" in " ".join(kq.get("ly_do", [])):
        reasons.append("[INDEX_REALITY] Chỉ số        : ❌ THỊ TRƯỜNG ẢO")

    # 4. Interbank / SBV veto
    ib = ldo
    if ib:
        reasons.append(f"[INTERBANK_VETO] Lãi suất liên NH : ❌ BỊ CHẶN ({ib})")

    # 5. Fallback / data-quality lock
    if kq.get("sensor_status") == "CRITICAL_SBV_CHANGED":
        reasons.append("[SENSOR_LOCK] SBV HTML thay đổi   : ❌ STRUCTURE_UNKNOWN — emergency shutoff")
    if kq.get("chi_tiet", {}).get("data_quality_warning"):
        reasons.append("[DATA_QUALITY] Dữ liệu          : ❌ Không đủ mã thanh khoản")

    # 6. Explicitly captured block reason from guard
    if ldo and not any(ldo in r for r in reasons):
        reasons.append(f"[GUARD_VETO] Lớp bảo vệ       : ❌ BỊ CHẶN ({ldo})")

    return reasons


def print_override_trace(kq: dict, lang_mode: str = "annotated") -> None:
    """Print the causal override chain if final != raw decision.

    Raw reference priority: quyet_dinh_raw_daily (Daily-Report trigger) if
    present, else quyet_dinh_raw (structural pre-guard signal).
    """
    final = kq.get("quyet_dinh") or "N/A"

    # The "trigger" the Governor overrode: prefer the Daily-Report-style raw
    # signal; fall back to the structural pre-guard signal.
    raw = kq.get("quyet_dinh_raw_daily") or kq.get("quyet_dinh_raw") or final

    if raw == final:
        return  # no override → no trace

    raw_icon = _ACTION_ICON.get(raw, "⚪")
    fin_icon = _ACTION_ICON.get(final, "⚪")

    vetoes = _veto_reasons(kq)
    conf = kq.get("độ_tin_cậy_sau_hiệu_chỉnh", {})
    conf_pct = round(float(conf.get("điểm_số", 0.5)) * 100, 1)
    he_so = kq.get("he_so_giam_ty_trong")
    if isinstance(he_so, (int, float)) and he_so <= 0.01:
        penalty_str = "Phạt 100% — đóng băng giải ngân (position = 0.0)"
    elif isinstance(he_so, (int, float)):
        penalty_str = f"Phạt x{he_so:.2f}"
    else:
        penalty_str = "?"

    print("\n" + "=" * 80)
    print(c_red("🛡️  GOVERNOR DECISION OVERRIDE TRACE (XAI COMPLIANT)"))
    print("=" * 80)

    # RAW signal
    print(f"[{c_cyan('RAW_SIGNAL')}]  Daily Report Trigger : {c_green(raw_icon + ' ' + _label(raw))}")
    print("                              │")
    print("                              ▼ (Đi qua bộ lọc Governor Laws)")

    # VETO chain
    if not vetoes:
        vetoes = [c_red("[GOVERNOR] Không có veto cụ thể — hạ mức hành động")]
    for v in vetoes:
        print(f"{c_red(v)}")

    # Confidence punishment
    print("                              │")
    print("                              ▼ (Kích hoạt phạt tự phủ định)")
    print(
        f"[{c_yellow('CONFIDENCE')}]  Hệ số phạt cấu trúc : {c_yellow(penalty_str + ' (Độ tin cậy ' + str(conf_pct) + '%)')}"
    )

    # Final decision
    print("                              │")
    print("                              ▼ (Phán quyết cuối cùng)")
    print(
        f"[{c_red('GOVERNOR_DEC')}] Final Capital Action : {c_red(fin_icon + ' ' + _label(final))} — "
        f"{c_dim('100% Cash / Bảo toàn vốn')}"
    )
    print("=" * 80)
    print()


def print_raw_signal_summary(kq: dict) -> None:
    """Print the raw (pre-guard) signal for reference when no override."""
    raw = kq.get("quyet_dinh_raw")
    if not raw:
        return
    print(f"  {c_dim('Signal thô trước Governor:')} {c_green(_ACTION_ICON.get(raw, '') + ' ' + _label(raw))}")
