"""
decision_guard.py — Lớp bảo vệ quyết định cuối cùng

Vai trò:
  Không phải "tầng quyết định". Là "lớp kiểm tra an toàn".

  Nhiệm vụ duy nhất:
    Kiểm tra nếu Độ tin cậy quá thấp → chặn quyết định.
    Nếu không → giữ nguyên quyết định từ Bộ não chính.

Quy tắc:
  - Không tự đưa ra quyết định.
  - Chỉ có quyền phủ quyết (override → DỪNG NGOÀI).
  - Lý do phủ quyết phải rõ ràng, gắn với tín hiệu cụ thể.

  - He so giam ty trong (data_quality_multiplier):
      Là cơ chế điều chỉnh tỷ trọng giải ngân dựa trên chất lượng dữ liệu.
      Không phải binary block — là graduated risk scaling.
      1.0 = full position, 0.3 = 30% max position.
      Hàm suy giảm tuyến tính 2 pha:
        Pha 1 (24h→48h): 1.0→0.3 (suy giảm nhanh tuần đầu)
        Pha 2 (48h→168h): 0.3→0.1 (suy giảm chậm, duy trì probe position)
      INSUFFICIENT_DATA → 0.0 (không có dữ liệu, không giao dịch).

  - Breadth Trap Detector (CUSUM trên Δdivergence):
      Phát hiện thời điểm Breadth Trap suy yếu để chuẩn bị recovery.
      Tích hợp qua breadth_trap_state trong output.
"""

import logging

from src.engine.breadth_trap_detector import BreadthTrapDetector

logger = logging.getLogger(__name__)

# Singleton detector — duy trì CUSUM state xuyên suốt vòng đời
_breadth_trap_detector: BreadthTrapDetector | None = None


def get_trap_detector() -> BreadthTrapDetector:
    global _breadth_trap_detector
    if _breadth_trap_detector is None:
        _breadth_trap_detector = BreadthTrapDetector(window=20, h_factor=2.0)
    return _breadth_trap_detector


def reset_trap_detector() -> None:
    """Reset detector state (dùng trong test)."""
    global _breadth_trap_detector
    _breadth_trap_detector = None


def compute_policy_cap_boost(target_date: str | None = None) -> dict:
    """Tính mức nâng trần tỷ trọng giải ngân do chính sách thanh khoản.

    Chỉ các PolicyEvent loại KBNN_LDR_ADJUSTMENT mới cấp cap_boost:
      - Scale theo max benefit ratio trong clusters (Big3 = 1.0).
      - LDR relief 500bps @ benefit 1.0 -> cap_boost 0.15.
      - Trả về max boost trong các event active (non-additive để tránh trùng).

    Returns:
        {
            "cap_boost": float [0, 0.15],
            "active_events": [ {id, title, benefit_ratio} ],
            "beneficiary_cluster": str,
        }
    """
    best_boost: float = 0.0
    best_cluster: str = "NONE"
    events: list[dict] = []
    try:
        from datetime import date, datetime

        from src.governor.policy_impact_engine import PolicyImpactEngine

        engine = PolicyImpactEngine()
        active = engine.get_active_events(target_date)
        _td = target_date or date.today().isoformat()
        _today = datetime.strptime(_td, "%Y-%m-%d").date()
        for evt in active:
            if evt.event_type != "KBNN_LDR_ADJUSTMENT":
                continue
            max_ratio = max(evt.clusters.values()) if evt.clusters else 0.0
            if max_ratio <= 0:
                continue
            # LAW-009: transmission decay + Policy Cliff — không FOMO ngày 1,
            # không "vach da" ngày expiry
            life = engine._lifecycle_factor(evt, _today)
            if life <= 0:
                continue
            ldr_relief = evt.delta_params.get("ldr_relief_bps", 0.0)
            boost = min(0.15, (ldr_relief / 500.0) * 0.15 * max_ratio * life)
            if boost > best_boost:
                best_boost = round(boost, 4)
                best_cluster = max(evt.clusters, key=lambda k: evt.clusters.get(k, 0.0)) if evt.clusters else "NONE"
            events.append(
                {
                    "id": evt.id,
                    "title": evt.title,
                    "benefit_ratio": max_ratio,
                    "lifecycle_factor": round(life, 4),
                    "cap_boost": round(boost, 4),
                }
            )
    except Exception as exc:
        logger.warning("[GUARD] Policy cap boost error (non-blocking): %s", exc)
    return {"cap_boost": best_boost, "active_events": events, "beneficiary_cluster": best_cluster}


def _he_so_tuoi_du_lieu(hours_stale: float) -> float:
    """Hàm suy giảm tuyến tính 2 pha dựa trên số giờ dữ liệu trễ.

    Grace period 24h: không suy giảm (he_so=1.0).
    Pha 1 (24→48h): suy giảm nhanh từ 1.0 xuống 0.3.
    Pha 2 (48→168h): suy giảm chậm từ 0.3 xuống 0.1.
    Trần 0.1 = absolute floor (giữ khả năng thăm dò tối thiểu).
    """
    if hours_stale <= 24:
        return 1.0
    elif hours_stale <= 48:
        progress = (hours_stale - 24) / 24
        return round(1.0 - 0.7 * progress, 2)
    else:
        progress = min((hours_stale - 48) / 120, 1.0)
        return round(max(0.1, 0.3 - 0.2 * progress), 2)


def kiem_tra_an_toan(
    quyet_dinh_de_xuat: str,
    ly_do_de_xuat: list,
    do_tin_cay: dict,
    anh_chup: dict | None = None,
    du_lieu_lien_ngan_hang: dict | None = None,
    chinh_sach: dict | None = None,
) -> dict:
    """Kiểm tra an toàn trước khi cho phép quyết định đi vào thực tế.

    Args:
        quyet_dinh_de_xuat: Quyết định từ Bộ quyết định cuối cùng
        ly_do_de_xuat: Lý do kèm theo
        do_tin_cay: Output từ Bộ tự đánh giá độ tin cậy
        anh_chup: Ảnh chụp thị trường (dùng để kiểm tra entropy + cấu trúc)
        du_lieu_lien_ngan_hang: Output từ assess_interbank_risk()
            (dùng để kiểm tra stale_override + điều chỉnh tỷ trọng)
        chinh_sach: Thông tin chính sách vĩ mô (PolicyImpactEngine).
            Truyền để tự động nâng trần tỷ trọng cho cụm hưởng lợi.
            Nếu None → tự tính qua compute_policy_cap_boost().

    Returns:
        dict: {
            "quyet_dinh": quyết định sau bảo vệ,
            "ly_do": lý do (có thể bổ sung nếu bị chặn),
            "bi_chặn": True/False,
            "ly_do_chặn": "..." hoặc None,
            "he_so_giam_ty_trong": 0.0 ~ 1.0 (mặc định 1.0),
            "policy_impact": {cap_boost, active_events, beneficiary_cluster},
        }
    """
    quyet_dinh = quyet_dinh_de_xuat
    ly_do = list(ly_do_de_xuat)
    bi_chặn = False
    ly_do_chặn = None

    # ── Bước 0: Data Quality Multiplier (graduated, không veto) ──
    he_so_giam_ty_trong = 1.0
    if du_lieu_lien_ngan_hang:
        ib_veto = du_lieu_lien_ngan_hang.get("veto", "NONE")
        stale = du_lieu_lien_ngan_hang.get("stale_override", False)
        hours_stale = du_lieu_lien_ngan_hang.get("hours_stale", 0)
        z_fast = du_lieu_lien_ngan_hang.get("zscore", {}).get("z_fast", 0)

        if ib_veto == "STRUCTURE_UNKNOWN":
            he_so_giam_ty_trong = 0.0
            logger.critical("[GUARD] SBV STRUCTURE UNKNOWN — sensor blind, he_so_giam_ty_trong=0.0")
        elif ib_veto == "INSUFFICIENT_DATA":
            he_so_giam_ty_trong = 0.0
            logger.warning("[GUARD] Khong du du lieu lien ngan hang, he_so_giam_ty_trong=0.0")
        elif stale:
            he_so_giam_ty_trong = _he_so_tuoi_du_lieu(hours_stale)
            logger.warning(
                "[GUARD] stale_override=True (Z_fast=%.1f, %gh), he_so_giam_ty_trong=%.2f",
                z_fast,
                hours_stale,
                he_so_giam_ty_trong,
            )
        elif ib_veto == "ACTIVE":
            he_so_giam_ty_trong = 0.0
            logger.warning("[GUARD] Interbank VETO ACTIVE, he_so_giam_ty_trong=0.0")

        # ── Cross-Layer Volatility Coupling (temporal + cooldown) ──
        on_rate = du_lieu_lien_ngan_hang.get("on_rate", 0.0)
        recovery_days = du_lieu_lien_ngan_hang.get("recovery_days", 0)
        is_lc = du_lieu_lien_ngan_hang.get("is_liquidity_crisis", False)

        from src.engine.partial_data_entropy import assess_crisis_unlock, compute_temporal_penalty, update_crisis_cooldown

        update_crisis_cooldown(on_rate, z_fast)

        if is_lc:
            he_so_giam_ty_trong = 0.0
            logger.critical("[GUARD] LIQUIDITY CRISIS (ON>=15%%) — he_so_giam_ty_trong=0.0")
        elif z_fast > 3.0:
            phi = compute_temporal_penalty(z_fast, is_liquidity_crisis=False)
            he_so_giam_ty_trong *= phi
            logger.info(
                "[GUARD] Temporal coupling: Z_fast=%.1f, Φ=%.3f, he_so=%.3f",
                z_fast,
                phi,
                he_so_giam_ty_trong,
            )

        # ── Crisis Cooldown Gate: chặn mở khóa nếu chưa đủ 3 phiên an toàn ──
        if he_so_giam_ty_trong > 0.0 and on_rate > 0:
            if not assess_crisis_unlock(on_rate, z_fast, recovery_days):
                he_so_giam_ty_trong = 0.0
                logger.warning("[GUARD] Crisis cooldown active — he_so_giam_ty_trong=0.0")

    # Lấy thông tin từ Bộ tự đánh giá
    tam_ngung = do_tin_cay.get("tạm_ngưng_kết_luận", False)

    # Lấy entropy, cấu trúc, breadth, và index reality từ ảnh chụp (nếu có)
    so_tru = 0
    diem_thi_truong_that = None
    do_lech_pha = None
    breadth_trap_state = None
    if anh_chup:
        c = anh_chup.get("cau_truc", {})
        so_tru = c.get("so_tru_ok", c.get("so_tru", 0))
        ir = anh_chup.get("phan_tich_chi_so", {})
        diem_thi_truong_that = ir.get("diem_thi_truong_that")
        do_lech_pha = ir.get("do_lech_pha")

        # Breadth Trap Detector — CUSUM trên Δdivergence
        breadth_pct = anh_chup.get("regime", {}).get("do_rong") or anh_chup.get("_regime_details", {}).get("breadth_pct")
        if breadth_pct is not None and so_tru > 0:
            detector = get_trap_detector()
            breadth_trap_state = detector.update(float(breadth_pct), so_tru)

    # ── Macro Staleness Veto (Layer 1+2: StaleTracker) ──
    macro_veto = False
    macro_veto_ly_do = None
    stale_state = None
    breadth_momentum = None
    try:
        from src.engine.macro_stale_tracker import StaleTracker

        tracker = StaleTracker.get_instance()
        from src.config import DATA_DIR

        db_path = str(DATA_DIR / "screener_cache.db")
        stale_state = tracker.update(db_path=db_path)
        if stale_state.get("veto"):
            macro_veto = True
            macro_veto_ly_do = (
                f"dữ liệu vĩ mô mất hiệu lực — "
                f"fresh_ratio={stale_state['fresh_ratio']:.0%}, "
                f"terminal={stale_state['terminal_ratio']:.0%}"
            )
            logger.warning("[GUARD] Macro stale veto: %s", macro_veto_ly_do)
    except Exception as exc:
        logger.warning("[GUARD] StaleTracker error (non-blocking): %s", exc)

    # ── Recovery Governor (Dual CUSUM) ──
    recovery_gov_state = None
    try:
        from src.engine.recovery_governor import RecoveryGovernor

        gov = RecoveryGovernor.get_instance()
        # Ghi nhận trạng thái veto (sẽ được xác định sau)
        # fresh_ratio từ stale_state, so_tru từ c, breadth_momentum từ regime
        # WHY: không gating bởi so_tru > 0 — khi so_tru=0 (cấu trúc vỡ) chính là
        # lúc governor phải phản ánh CASH_ONLY; update() xử lý so_tru=0 an toàn.
        if stale_state:
            regime_details = (anh_chup or {}).get("_regime_details", {})
            breadth_momentum = regime_details.get("breadth_momentum", 0)
            gov.record_veto(bi_chặn or macro_veto)
            recovery_gov_state = gov.update(
                fresh_ratio=stale_state["fresh_ratio"],
                so_tru=so_tru,
                breadth_momentum=float(breadth_momentum or 0),
            )
    except Exception as exc:
        logger.warning("[GUARD] RecoveryGovernor error (non-blocking): %s", exc)

    # ── Bước 1: Kiểm tra Cấu trúc & Index Reality (hard override — veto bất chấp confidence) ──

    # 1a — SYSTEMIC LIQUIDITY SHOCK from interbank Z-Score
    for ld in ly_do:
        if "SYSTEMIC_LIQUIDITY_SHOCK" in ld:
            quyet_dinh = "DUNG NGOAI"
            ly_do_chặn = "lãi suất liên ngân hàng tăng đột biến — Systemic Liquidity Shock"
            bi_chặn = True
            logger.warning("[GUARD] INTERBANK Z-SCORE SHOCK: veto kich hoat")
            break

    # 1b — Cấu trúc thị trường
    if not bi_chặn and so_tru <= 1:
        if diem_thi_truong_that is not None and diem_thi_truong_that >= 0.2:
            logger.warning(
                "[GUARD] DIEM_THI_TRUONG_THAT=%.2f >= 0.2 nhung so_tru=%d/3 — phân kỳ cấu trúc, tính thanh khoản bất thường",
                diem_thi_truong_that,
                so_tru,
            )
        quyet_dinh = "DUNG NGOAI"
        ly_do_chặn = f"cấu trúc thị trường vỡ ({so_tru}/3 trụ) — thiếu nền tảng đồng thuận"
        ly_do = ["cấu trúc thị trường vỡ — dừng ngoài"]
        bi_chặn = True
    elif do_lech_pha == "MANH_GIA_TAO":
        quyet_dinh = "DUNG NGOAI"
        ly_do_chặn = "thị trường 'mạnh giả tạo' — chỉ số bị kéo, không đại diện"
        ly_do = ["thị trường tăng giả tạo — dừng ngoài"]
        bi_chặn = True
    elif diem_thi_truong_that is not None and diem_thi_truong_that < 0.2:
        quyet_dinh = "DUNG NGOAI"
        ly_do_chặn = "thị trường ảo — chỉ số hoàn toàn mất kết nối với nội tại"
        ly_do = ["thị trường ảo — dừng mọi giao dịch"]
        bi_chặn = True

    # ── Bước 2: Kiểm tra Confidence Layer (tạm ngưng từ độ tin cậy) ──
    elif tam_ngung:
        quyet_dinh = "DUNG NGOAI"
        ly_do_chặn = do_tin_cay.get(
            "lý_do_tạm_ngưng",
            "độ tin cậy xuống dưới ngưỡng an toàn",
        )
        ly_do = ["thị trường quá nhiễu — tạm ngưng kết luận"]
        bi_chặn = True

    # ── Bước 3: Macro Staleness Veto ──
    if not bi_chặn and macro_veto:
        quyet_dinh = "DUNG NGOAI"
        ly_do_chặn = macro_veto_ly_do
        ly_do = ["dữ liệu vĩ mô mất hiệu lực — dừng ngoài"]
        bi_chặn = True

    # ── Nếu bị chặn, ghi đè he_so_giam_ty_trong = 0.0 ──
    if bi_chặn:
        he_so_giam_ty_trong = 0.0

    # ── LRI Integration: Dimmer Scaling thay thế VETO nhị phân ──
    # WHY: VETO đập bệt 0% Cash khiến hệ thống bỏ lỡ cơ hội khi Kinh tế thực
    # đang tăng trưởng mạnh nhưng Thanh khoản Tài chính còn thắt chặt.
    # LRI cho phép giải ngân co giãn theo tỷ lệ LRI × allocation.
    lri_result = None
    try:
        from src.governor.liquidity_recovery_index import compute_lri

        lri_result = compute_lri()
        lri_score = lri_result.lri

        # LRI modulates he_so_giam_ty_trong:
        # - If LRI < 0.3 (DEFENSIVE): force he_so = 0.0 (hard floor)
        # - If 0.3 <= LRI < 0.8 (PROBE): multiply he_so by LRI (graduated)
        # - If LRI >= 0.8 (AGGRESSIVE): no modulation (keep existing he_so)
        if lri_score < 0.3:
            he_so_giam_ty_trong = 0.0
            logger.warning(
                "[GUARD] LRI=%.4f DEFENSIVE — he_so_giam_ty_trong forced to 0.0",
                lri_score,
            )
        elif lri_score < 0.8:
            he_so_giam_ty_trong *= lri_score
            logger.info(
                "[GUARD] LRI=%.4f PROBE — he_so_giam_ty_trong scaled to %.3f",
                lri_score,
                he_so_giam_ty_trong,
            )
        else:
            logger.info(
                "[GUARD] LRI=%.4f AGGRESSIVE — he_so_giam_ty_trong unchanged (%.3f)",
                lri_score,
                he_so_giam_ty_trong,
            )
    except Exception as exc:
        logger.warning("[GUARD] LRI computation error (non-blocking): %s", exc)

    # ── Policy Impact Integration: nâng trần tỷ trọng cho cụm hưởng lợi ──
    # WHY: QD 1743 giai toa LDR -> Big3 duoc phep tang ty trong toi da.
    #      cap_boost chi cong khi he_so > 0 (khong pha vo veto/hard-floor).
    policy_impact = chinh_sach if chinh_sach is not None else compute_policy_cap_boost()
    policy_cap_boost = policy_impact.get("cap_boost", 0.0) or 0.0
    if policy_cap_boost > 0 and he_so_giam_ty_trong > 0 and not bi_chặn:
        he_so_giam_ty_trong = round(min(1.0, he_so_giam_ty_trong + policy_cap_boost), 4)
        logger.info(
            "[GUARD] Policy cap_boost=%.4f -> he_so_giam_ty_trong=%.4f (%s)",
            policy_cap_boost,
            he_so_giam_ty_trong,
            policy_impact.get("beneficiary_cluster", "NONE"),
        )

    # ── Contribution Breakdown ──
    contribution = _build_contribution(
        bi_chặn=bi_chặn,
        ly_do_chặn=ly_do_chặn,
        quyet_dinh=quyet_dinh,
        so_tru=so_tru,
        tam_ngung=tam_ngung,
        macro_veto=macro_veto,
        stale_state=stale_state,
        recovery_gov_state=recovery_gov_state,
        breadth_trap_state=breadth_trap_state,
        breadth_momentum=breadth_momentum,
    )

    return {
        "quyet_dinh": quyet_dinh,
        "ly_do": ly_do,
        "bi_chặn": bi_chặn,
        "ly_do_chặn": ly_do_chặn,
        "he_so_giam_ty_trong": he_so_giam_ty_trong,
        "lri": {
            "score": lri_result.lri if lri_result else None,
            "regime": lri_result.regime if lri_result else None,
            "max_allocation_pct": lri_result.max_allocation_pct if lri_result else None,
            "components": lri_result.components_raw if lri_result else {},
        }
        if lri_result
        else {},
        "breadth_trap": breadth_trap_state or {},
        "recovery_governor": recovery_gov_state or {},
        "macro_stale": {
            "fresh_ratio": stale_state.get("fresh_ratio") if stale_state else None,
            "terminal_ratio": stale_state.get("terminal_ratio") if stale_state else None,
            "veto": macro_veto,
        }
        if stale_state
        else {},
        "contribution": contribution,
        "policy_impact": policy_impact,
    }


def _build_contribution(
    bi_chặn: bool,
    ly_do_chặn: str | None,
    quyet_dinh: str,
    so_tru: int,
    tam_ngung: bool,
    macro_veto: bool,
    stale_state: dict | None,
    recovery_gov_state: dict | None,
    breadth_trap_state: dict | None,
    breadth_momentum: float | None,
) -> dict:
    """Phân rã đóng góp 3 mô hình quản trị rủi ro vào DecisionGuard:
    1. macro  (20%): Mô hình Vĩ mô (FedState, Credit Stress, Liquidity Trap)
    2. quant  (30%): Mô hình Định lượng (Confidence, Macro Entropy)
    3. regime (50%): Mô hình Trạng thái Thị trường (Ranging, ATR Shock, Breadth)

    LƯU Ý KĨ THUẬT: 'regime' ở đây là Trạng thái Thị trường (Market Regime),
    KHÔNG PHẢI thuật toán Hồi quy (Regression). Hồi quy Beta/Entropy nằm ở
    fair_multiple_engine.py và market_macro_coordinator.py.

    Returns:
        {
            "models": { macro/quant/regime: { weight, status, detail, impact_pct } },
            "blocking_model": "..." | None,
            "position_level": 0.0~1.0,
            "position_label": "...",
        }
    """
    pos_level = 0.0
    pos_label = "CASH_ONLY"
    if recovery_gov_state:
        pos_level = recovery_gov_state.get("position_level", 0.0)
        pos_label = recovery_gov_state.get("position_label", "CASH_ONLY")

    # Xác định model chặn
    blocking_model = None
    if bi_chặn:
        if tam_ngung:
            blocking_model = "quant"
        elif so_tru <= 1:
            blocking_model = "regime"
        elif macro_veto or (ly_do_chặn and ("vĩ mô" in str(ly_do_chặn) or "liên ngân" in str(ly_do_chặn))):
            blocking_model = "macro"
        else:
            blocking_model = "regime"

    # Macro model
    fresh = stale_state.get("fresh_ratio", 0) if stale_state else 0
    terminal = stale_state.get("terminal_ratio", 0) if stale_state else 0
    if macro_veto:
        macro_status = "VETO"
        macro_detail = f"fresh_ratio={fresh:.0%} < 50%, terminal={terminal:.0%} >= 50%"
        macro_impact = 1.0
    elif fresh < 0.5:
        macro_status = "WARNING"
        macro_detail = f"fresh_ratio={fresh:.0%} < 50%"
        macro_impact = 0.3
    elif fresh < 0.8:
        macro_status = "DEGRADED"
        macro_detail = f"fresh_ratio={fresh:.0%}"
        macro_impact = 0.1
    else:
        macro_status = "NORMAL"
        macro_detail = f"fresh_ratio={fresh:.0%}, all sensors healthy"
        macro_impact = 0.0

    # Quant model
    bt_active = breadth_trap_state.get("trap_active", False) if breadth_trap_state else False
    bt_weakening = breadth_trap_state.get("trap_weakening", False) if breadth_trap_state else False
    if tam_ngung:
        quant_status = "VETO"
        quant_detail = "confidence < 30% — tam ngung ket luan"
        quant_impact = 1.0
    elif bt_active and bt_weakening:
        quant_status = "WARNING"
        quant_detail = "breadth trap weakening — recovery approaching"
        quant_impact = 0.2
    elif bt_active:
        quant_status = "WARNING"
        quant_detail = "breadth trap active"
        quant_impact = 0.3
    else:
        quant_status = "NORMAL"
        quant_detail = f"breadth_momentum={breadth_momentum or 0:.1f}%"
        quant_impact = 0.0

    # Regime model
    if so_tru <= 1:
        regime_status = "VETO"
        regime_detail = f"cấu trúc vỡ ({so_tru}/3 trụ)"
        regime_impact = 1.0
    elif so_tru == 2:
        regime_status = "DEGRADED"
        regime_detail = f"cấu trúc yếu ({so_tru}/3 trụ)"
        regime_impact = 0.2
    else:
        regime_status = "NORMAL"
        regime_detail = f"cấu trúc vững ({so_tru}/3 trụ)"
        regime_impact = 0.0

    models = {
        "macro": {
            "weight": 0.20,
            "status": macro_status,
            "detail": macro_detail,
            "impact_pct": round(macro_impact, 4),
        },
        "quant": {
            "weight": 0.30,
            "status": quant_status,
            "detail": quant_detail,
            "impact_pct": round(quant_impact, 4),
        },
        "regime": {
            "weight": 0.50,
            "status": regime_status,
            "detail": regime_detail,
            "impact_pct": round(regime_impact, 4),
        },
    }

    return {
        "models": models,
        "blocking_model": blocking_model,
        "position_level": pos_level,
        "position_label": pos_label,
    }
