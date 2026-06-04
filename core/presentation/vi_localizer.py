
_TRADE_STATE_VI: dict[str, str] = {
    "PROHIBITED": "Cấm giao dịch",
    "RESTRICTED": "Hạn chế giao dịch",
    "SELECTIVE": "Giao dịch chọn lọc",
    "ACTIVE": "Giao dịch chủ động",
    "AGGRESSIVE": "Giao dịch tấn công",
}

_REGIME_VI: dict[str, str] = {
    "TRENDING": "Xu hướng rõ",
    "RANGING": "Đi ngang",
    "CRISIS": "Khủng hoảng",
    "RECOVERY": "Phục hồi",
    "UNKNOWN": "Không xác định",
}

_BREADTH_VI: dict[str, str] = {
    "EXPANDING": "Mở rộng",
    "DIVERGING": "Phân kỳ",
    "CONTRACTING": "Thu hẹp",
    "WEAK": "Yếu",
}

_FLOW_VI: dict[str, str] = {
    "MỞ_RỘNG": "Dòng tiền mở rộng",
    "MỞ_RỘNG_TÍCH_CỰC": "Dòng tiền mở rộng tích cực",
    "DUY_TRÌ": "Dòng tiền duy trì",
    "ỔN_ĐỊNH": "Dòng tiền ổn định",
    "TRUNG_TÍNH": "Dòng tiền trung tính",
    "PHÂN_HÓA": "Dòng tiền phân hóa",
    "THU_HẸP": "Dòng tiền thu hẹp",
    "YẾU": "Dòng tiền yếu",
    "KÉM": "Dòng tiền kém",
    "UNKNOWN": "Dòng tiền không xác định",
}

_RISK_GOV_VI: dict[str, str] = {
    "NORMAL": "Bình thường",
    "DEFENSIVE": "Phòng thủ",
    "RESTRICTED": "Hạn chế",
    "LOCKDOWN": "Đóng băng rủi ro",
    "UNKNOWN": "Không xác định",
}

_BDI_VI: dict[str, str] = {
    "CAN_BANG": "Cân bằng",
    "PHAN_KY_DUONG": "Phân kỳ dương — Large-cap vượt trội",
    "PHAN_KY_AM": "Phân kỳ âm — Small-cap vượt trội",
}

_SSI_VI: dict[str, str] = {
    "HIGH": "Ổn định",
    "MEDIUM": "Đang chuyển pha",
    "LOW": "Nhiễu / Giả",
}

_ASSET_BIAS_VI: dict[str, str] = {
    "STRONG_PREFER": "Ưu tiên cao",
    "PREFER": "Ưu tiên",
    "NEUTRAL": "Trung tính",
    "AVOID": "Hạn chế",
    "STRONG_AVOID": "Tránh",
}

_DRIFT_VI: dict[str, str] = {
    "STRUCTURAL": "Lệch cấu trúc",
    "TRANSIENT": "Lệch tạm thời",
    "NOISE": "Nhiễu",
    "NONE": "Đồng bộ",
}

_ACTION_ALLOWED_VI: dict[str, str] = {
    "CASH_ONLY": "Chỉ giữ tiền mặt",
    "EXIT_ALL": "Đóng toàn bộ vị thế",
    "HOLD": "Nắm giữ",
    "REDUCE": "Giảm tỷ trọng",
    "EXIT": "Thoát vị thế",
    "BUY_SELECTIVE": "Mua chọn lọc",
    "ROTATE": "Xoay vòng danh mục",
    "BUY": "Mua",
    "ADD": "Thêm vị thế",
    "LEVERAGE": "Sử dụng đòn bẩy",
}

_DCL_VERDICT_VI: dict[str, str] = {
    "ACTIONABLE": "Có thể giao dịch",
    "OBSERVE": "Quan sát có điều kiện",
    "NO_TRADE": "Không giao dịch",
}

_DCL_VERDICT_COLOR: dict[str, str] = {
    "ACTIONABLE": "green",
    "OBSERVE": "yellow",
    "NO_TRADE": "red",
}

_MARKET_INTENT_VI: dict[str, str] = {
    "ACCUMULATION": "Thị trường đang trong pha tích lũy — dòng tiền lớn đang vào lệnh",
    "WAITING": "Thị trường đang chờ — tín hiệu chưa đủ mạnh để hành động",
    "DISTRIBUTION": "Thị trường đang trong pha phân phối — momentum suy yếu, dòng tiền giảm dần",
    "PRESERVATION": "Thị trường đang trong chế độ bảo toàn thanh khoản — ưu tiên giữ tiền mặt",
}

_GATE_NAME_VI: dict[str, str] = {
    "sentinel": "Mô hình A (Sentinel)",
    "flow": "Mô hình B (Dòng tiền)",
    "breadth": "Độ rộng thị trường",
    "structure": "Cấu trúc thị trường",
    "ssi": "Độ ổn định state (SSI)",
    "trade_state": "Chính sách giao dịch",
}

_GATE_REASON_VI: dict[str, str] = {
    "GATE_SENTINEL_PASS": "Cả 3 lớp phòng thủ đều xanh — momentum, NH10 và dòng ngoại xác nhận",
    "GATE_SENTINEL_FAIL": "Sentinel RED — momentum chưa đủ rộng, NH10 chưa 3D, dòng ngoại chưa hấp thụ",
    "GATE_FLOW_PASS": "Dòng tiền tổ chức duy trì vị thế — flow alignment đạt ngưỡng",
    "GATE_FLOW_FAIL": "Dòng tiền tổ chức suy yếu — thanh khoản không ủng hộ vị thế mua",
    "GATE_BREADTH_PASS": "Độ rộng thị trường lan tỏa — index đại diện thực chất",
    "GATE_BREADTH_FAIL": "Độ rộng thị trường yếu — index giả tạo, thiếu lan tỏa thực",
    "GATE_STRUCTURE_PASS": "Cấu trúc thị trường cân bằng — LCR và BDI đồng thuận",
    "GATE_STRUCTURE_FAIL": "Cấu trúc thị trường lệch — LCR cao, BDI phân kỳ, index do vài trụ kéo",
    "GATE_SSI_PASS": "State thị trường ổn định — các tín hiệu nội tại nhất quán",
    "GATE_SSI_FAIL": "State thị trường nhiễu — tín hiệu chưa đồng thuận, độ tin cậy thấp",
    "GATE_TRADE_STATE_PASS": "Chính sách giao dịch cho phép — trade state ở ngưỡng hoạt động",
    "GATE_TRADE_STATE_FAIL": "Chính sách giao dịch đóng băng/hạn chế — không cho phép mở vị thế mới",
}

_COMP_CODE_VI: dict[str, str] = {
    "COMP_SENTINEL_BY_FLOW_SSI": "Dòng tiền ACCELERATION + SSI ổn bù cho Sentinel RED",
    "COMP_SENTINEL_BY_FLOW_BREADTH": "Flow mạnh + breadth phục hồi bù cho Sentinel",
    "COMP_BREADTH_BY_SENTINEL_FLOW": "Sentinel GREEN + Flow ACCELERATION bù cho breadth yếu",
    "COMP_BREADTH_BY_SENTINEL_SSI": "Sentinel GREEN + SSI ổn bù cho breadth thấp",
    "COMP_SSI_BY_FLOW_BREADTH": "Flow mạnh + breadth phục hồi bù cho SSI thấp",
    "COMP_SSI_BY_SENTINEL_TRADE_STATE": "Sentinel GREEN + trade state cho phép bù cho SSI trung bình",
    "COMP_FLOW_BY_SENTINEL_SSI": "Sentinel GREEN + SSI tốt bù cho flow chững",
    "COMP_FLOW_BY_BREADTH_TRADE_STATE": "Breadth rộng + trade state ACTIVE bù cho flow yếu",
    "COMP_STRUCTURE_BY_SENTINEL_FLOW_BREADTH": "Sentinel xanh + flow mạnh + breadth cơ bản bù cho cấu trúc lệch",
    "COMP_TRADE_STATE_BY_SENTINEL_FLOW_SSI": "Sentinel GREEN + Flow ACCELERATION + SSI ổn bù cho policy hạn chế",
}

_ACTION_CODE_VI: dict[str, str] = {
    "SELECTIVE_BUY": "Có thể giải ngân",
    "CONDITIONAL_BUY": "Giải ngân có điều kiện",
    "STAND_DOWN": "Đứng ngoài quan sát",
}

_DIRECTIONAL_BIAS_VI: dict[str, str] = {
    "BULLISH": "Xu hướng tăng",
    "TRANSITIONAL": "Chuyển pha",
    "NEUTRAL": "Trung tính",
    "BEARISH": "Xu hướng giảm",
    "FRACTURED": "Phân mảnh — không rõ hướng",
}

_BIAS_FORCE_VI: dict[str, str] = {
    "FLOW": "Dòng tiền",
    "STRUCTURE": "Cấu trúc",
    "BREADTH": "Độ rộng",
    "REGIME": "Regime",
    "SSI": "Độ tin cậy",
    "TRADE_STATE": "Chính sách GD",
    "NONE": "Không rõ",
}

_BIAS_COLOR: dict[str, str] = {
    "BULLISH": "green",
    "TRANSITIONAL": "lime",
    "NEUTRAL": "yellow",
    "BEARISH": "red",
    "FRACTURED": "purple",
}

_TREND_QUALITY_VI: dict[str, str] = {
    "PERSISTENT": "Xu hướng bền vững",
    "TRANSITIONAL": "Đang chuyển pha",
    "FLICKERING": "Nhiễu — chưa rõ hướng",
}

_FLICKER_RISK_VI: dict[str, str] = {
    "LOW": "Thấp",
    "MEDIUM": "Trung bình",
    "HIGH": "Cao",
}

_TREND_QUALITY_COLOR: dict[str, str] = {
    "PERSISTENT": "green",
    "TRANSITIONAL": "yellow",
    "FLICKERING": "red",
}

_TRANSITION_STATE_VI: dict[str, str] = {
    "STABLE": "Ổn định",
    "BREWING": "Đang hình thành chuyển pha",
    "TRIGGERED": "Chuyển pha đã kích hoạt",
}

_TRANSITION_TYPE_VI: dict[str, str] = {
    "NONE": "Không có",
    "FLICKER_TO_TREND": "Nhiễu → Xu hướng",
    "TREND_TO_FLICKER": "Xu hướng → Nhiễu",
    "REGIME_SHIFT": "Chuyển đổi Regime",
    "BIAS_FLIP": "Đảo chiều Bias",
}

_TRANSITION_STATE_COLOR: dict[str, str] = {
    "STABLE": "green",
    "BREWING": "yellow",
    "TRIGGERED": "red",
}

_VETO_WARN_CODE_VI: dict[str, str] = {
    "VETO_TRADE_STATE": "Trade state đóng băng/hạn chế — veto từ policy tổng quát",
    "WARN_TRADE_STATE": "Trade state hạn chế nhưng DCL cho phép override — yêu cầu xác nhận mạnh",
    "VETO_SENTINEL": "Sentinel RED — momentum chưa hội tụ, NH10 chưa xác nhận",
    "WARN_SENTINEL": "Sentinel RED nhưng DCL override — có tín hiệu bù trừ từ Flow/SSI",
    "VETO_FLOW": "Dòng tiền yếu — thanh khoản tổ chức không ủng hộ vị thế mua",
    "WARN_FLOW": "Dòng tiền yếu nhưng DCL override — cần xác nhận từ khối lượng và breadth",
    "VETO_BREADTH": "Độ rộng yếu — rủi ro index giả tạo, thiếu lan tỏa thực",
    "WARN_BREADTH": "Độ rộng yếu nhưng DCL override — chỉ chọn mã có xác nhận volume mạnh",
    "VETO_STRUCTURE": "Cấu trúc lệch — thanh khoản tập trung quá hẹp, index do vài trụ kéo",
    "WARN_STRUCTURE": "Cấu trúc lệch nhưng DCL override — chỉ tập trung mã trụ có dòng tiền",
    "VETO_SSI": "SSI dưới ngưỡng — tín hiệu thị trường đang nhiễu",
    "WARN_SSI": "SSI dưới ngưỡng nhưng DCL override — state nhiễu nhưng có cấu trúc bù trừ",
    "WARN_FLOW_DECELERATION": "Dòng tiền ở trạng thái trung bình — cần xác nhận thêm",
    "WARN_SENTINEL_RED_FLOW_STRONG": "Sentinel chưa xanh nhưng dòng tiền tích cực — mâu thuẫn cần theo dõi",
}


def localize_trade_state(level: str) -> str:
    return _TRADE_STATE_VI.get(level, level)


def localize_regime(status: str) -> str:
    return _REGIME_VI.get(status, status)


def localize_breadth(state: str) -> str:
    return _BREADTH_VI.get(state, state)


def localize_flow(status: str) -> str:
    return _FLOW_VI.get(status, status)


def localize_risk_governor(state: str) -> str:
    return _RISK_GOV_VI.get(state, state)


def localize_bdi(signal: str) -> str:
    return _BDI_VI.get(signal, signal)


def localize_ssi(level: str) -> str:
    return _SSI_VI.get(level, level)


def localize_asset_bias(bias: str) -> str:
    return _ASSET_BIAS_VI.get(bias, bias)


def localize_drift(label: str) -> str:
    for eng, vi in _DRIFT_VI.items():
        if eng in label.upper():
            return label.replace(eng, vi)
    return label


def localize_allowed_action(action: str) -> str:
    return _ACTION_ALLOWED_VI.get(action, action)


def localize_dcl_verdict(verdict: str) -> str:
    return _DCL_VERDICT_VI.get(verdict, verdict)


def localize_dcl_color(verdict: str) -> str:
    return _DCL_VERDICT_COLOR.get(verdict, "gray")


def localize_market_intent(intent: str) -> str:
    return _MARKET_INTENT_VI.get(intent, intent)


def localize_gate_name(name: str) -> str:
    return _GATE_NAME_VI.get(name, name)


def localize_gate_reason(code: str) -> str:
    return _GATE_REASON_VI.get(code, code)


def localize_comp_code(code: str) -> str:
    return _COMP_CODE_VI.get(code, code)


def localize_action_code(code: str) -> str:
    return _ACTION_CODE_VI.get(code, code)


def localize_bias_code(code: str) -> str:
    return _DIRECTIONAL_BIAS_VI.get(code, code)


def localize_bias_force(force: str) -> str:
    return _BIAS_FORCE_VI.get(force, force)


def localize_bias_color(code: str) -> str:
    return _BIAS_COLOR.get(code, "gray")


def localize_trend_quality(code: str) -> str:
    return _TREND_QUALITY_VI.get(code, code)


def localize_flicker_risk(code: str) -> str:
    return _FLICKER_RISK_VI.get(code, code)


def localize_trend_quality_color(code: str) -> str:
    return _TREND_QUALITY_COLOR.get(code, "gray")


def localize_transition_state(code: str) -> str:
    return _TRANSITION_STATE_VI.get(code, code)


def localize_transition_type(code: str) -> str:
    return _TRANSITION_TYPE_VI.get(code, code)


def localize_transition_state_color(code: str) -> str:
    return _TRANSITION_STATE_COLOR.get(code, "gray")


def localize_veto_warn(code: str) -> str:
    return _VETO_WARN_CODE_VI.get(code, code)


def localize_veto_warn_list(codes: list[str]) -> list[str]:
    return [localize_veto_warn(c) for c in codes]


def localize_market_state(state: dict) -> dict:
    result = dict(state)

    ts = result.get("trade_state")
    if ts:
        ts = dict(ts)
        ts["level_vi"] = localize_trade_state(ts.get("level", ""))
        ts["label_vi"] = localize_trade_state(ts.get("level", ""))
        allowed = ts.get("allowed_actions", [])
        ts["allowed_actions_vi"] = [localize_allowed_action(a) for a in allowed]
        result["trade_state"] = ts

    ap = result.get("asset_preference")
    if ap:
        ap = dict(ap)
        entries = []
        for e in ap.get("entries", []):
            e = dict(e)
            e["label_vi"] = localize_asset_bias(e.get("bias", ""))
            entries.append(e)
        ap["entries"] = entries
        result["asset_preference"] = ap

    ss = result.get("state_stability")
    if ss:
        ss = dict(ss)
        ss["label_vi"] = localize_ssi(ss.get("level", ""))
        for axis_key in ("state_consistency", "breadth_confirmation", "drift_alignment", "flow_stability"):
            axis = ss.get(axis_key)
            if axis and isinstance(axis, dict):
                axis["label_vi"] = localize_ssi(
                    "HIGH" if axis.get("score", 0) >= 0.65
                    else "MEDIUM" if axis.get("score", 0) >= 0.35
                    else "LOW"
                )
        result["state_stability"] = ss

    mr = result.get("market_regime")
    if mr:
        mr = dict(mr)
        mr["status_vi"] = localize_regime(mr.get("status", ""))
        result["market_regime"] = mr

    fs = result.get("flow_state")
    if fs:
        fs = dict(fs)
        fs["status_vi"] = localize_flow(fs.get("status", ""))
        result["flow_state"] = fs

    rs = result.get("risk_state")
    if rs:
        rs = dict(rs)
        rs["governor_vi"] = localize_risk_governor(rs.get("governor_state", ""))
        result["risk_state"] = rs

    ms = result.get("market_structure")
    if ms:
        ms = dict(ms)
        ms["bdi_signal_vi"] = localize_bdi(ms.get("bdi_signal", ""))
        result["market_structure"] = ms

    dcl = result.get("decision_closure")
    if dcl:
        dcl = dict(dcl)
        dcl["verdict_vi"] = localize_dcl_verdict(dcl.get("verdict", ""))
        dcl["color"] = localize_dcl_color(dcl.get("verdict", ""))
        dcl["market_intent_vi"] = localize_market_intent(dcl.get("market_intent", ""))
        gates = dcl.get("gates", {})
        if gates and isinstance(gates, dict):
            localized_gates = {}
            for gname, gscore in gates.items():
                if isinstance(gscore, dict):
                    gscore = dict(gscore)
                    gscore["label_vi"] = localize_gate_name(gname)
                    gscore["reason_vi"] = localize_gate_reason(gscore.get("reason_code", ""))
                    if gscore.get("comp_code"):
                        gscore["compensation_detail_vi"] = localize_comp_code(gscore["comp_code"])
                    else:
                        gscore["compensation_detail_vi"] = None
                    localized_gates[gname] = gscore
                else:
                    localized_gates[gname] = gscore
            dcl["gates"] = localized_gates
        comps = dcl.get("compensations_applied", [])
        if comps and isinstance(comps, list):
            localized_comps = []
            for c in comps:
                if isinstance(c, dict):
                    c = dict(c)
                    c["description_vi"] = localize_comp_code(c.get("comp_code", ""))
                    localized_comps.append(c)
                else:
                    localized_comps.append(c)
            dcl["compensations_applied"] = localized_comps
        result["decision_closure"] = dcl

    db = result.get("directional_bias")
    if db:
        db = dict(db)
        db["label_vi"] = localize_bias_code(db.get("bias_code", ""))
        db["color"] = localize_bias_color(db.get("bias_code", ""))
        drivers = db.get("bias_drivers", [])
        if drivers and isinstance(drivers, list):
            localized_drivers = []
            for d in drivers:
                if isinstance(d, dict):
                    d = dict(d)
                    d["source_vi"] = localize_bias_force(d.get("source", ""))
                    localized_drivers.append(d)
                else:
                    localized_drivers.append(d)
            db["bias_drivers"] = localized_drivers
        db["dominant_force_vi"] = localize_bias_force(db.get("dominant_force", ""))
        result["directional_bias"] = db

    dp = result.get("direction_persistence")
    if dp:
        dp = dict(dp)
        dp["label_vi"] = localize_trend_quality(dp.get("trend_quality_code", ""))
        dp["color"] = localize_trend_quality_color(dp.get("trend_quality_code", ""))
        dp["flicker_label_vi"] = localize_flicker_risk(dp.get("flicker_risk_code", ""))
        result["direction_persistence"] = dp

    tt = result.get("transition_trigger")
    if tt:
        tt = dict(tt)
        tt["label_vi"] = localize_transition_state(tt.get("transition_state", ""))
        tt["color"] = localize_transition_state_color(tt.get("transition_state", ""))
        tt["transition_type_vi"] = localize_transition_type(tt.get("transition_type", ""))
        result["transition_trigger"] = tt

    vd = result.get("investment_verdicts")
    if vd:
        vd = dict(vd)
        verdicts_list = vd.get("verdicts", [])
        if verdicts_list and isinstance(verdicts_list, list):
            localized_verdicts = []
            for v in verdicts_list:
                if isinstance(v, dict):
                    v = dict(v)
                    v["action_label"] = localize_action_code(v.get("action_code", ""))
                    veto_localized = [localize_veto_warn(r) for r in v.get("veto_reasons", [])]
                    warn_localized = [localize_veto_warn(w) for w in v.get("warnings", [])]
                    v["veto_reasons_vi"] = veto_localized
                    v["warnings_vi"] = warn_localized
                    localized_verdicts.append(v)
                else:
                    localized_verdicts.append(v)
            vd["verdicts"] = localized_verdicts
        result["investment_verdicts"] = vd

    return result
