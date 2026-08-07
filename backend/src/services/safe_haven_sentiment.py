import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def _hydrate_path():
    if getattr(sys, "frozen", False):
        root_path = Path(sys.executable).resolve().parent
    else:
        current = Path(__file__).resolve().parent
        root_path = current
        while current != current.parent:
            if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
                root_path = current
                break
            current = current.parent
    if str(root_path) not in sys.path:
        sys.path.insert(0, str(root_path))
    backend_dir = root_path / "backend"
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    return root_path


PROJECT_ROOT = _hydrate_path()


_ZONE_MAP = {
    "BÌNH_THƯỜNG": {
        "ten": "Bình thường",
        "ky_hieu": "🟢",
        "mo_ta": "Tâm lý ổn định, không có dấu hiệu trú ẩn",
    },
    "HƠI_THẬN_TRỌNG": {
        "ten": "Hơi thận trọng",
        "ky_hieu": "🟡",
        "mo_ta": "Bắt đầu có dấu hiệu phòng thủ nhẹ",
    },
    "PHÒNG_THỦ_RÕ_RỆT": {
        "ten": "Phòng thủ rõ rệt",
        "ky_hieu": "🟠",
        "mo_ta": "Tâm lý giữ tiền an toàn xuất hiện rõ",
    },
    "HOẢNG_LOẠN": {
        "ten": "Hoảng loạn / Khan hiếm thanh khoản",
        "ky_hieu": "🔴",
        "mo_ta": "Thị trường chuyển sang trạng thái phòng thủ mạnh",
    },
}


def phan_vung_tam_ly(
    gold_premium_pct: float | None = None,
    gold_premium_regime: str | None = None,
    market_state: dict | None = None,
    regime_data: dict | None = None,
) -> dict:
    diem = 0.0
    ly_do = []

    # 1. Giá trị tuyệt đối của chênh lệch vàng
    if gold_premium_pct is not None:
        if gold_premium_pct > 5:
            diem += 3.0
            ly_do.append(f"Chênh lệch vàng ở mức cao ({gold_premium_pct:.1f}%)")
        elif gold_premium_pct > 3:
            diem += 1.5
            ly_do.append(f"Chênh lệch vàng hơi cao ({gold_premium_pct:.1f}%)")
        elif gold_premium_pct > 1:
            diem += 0.5

    # 2. Regime premium từ gold_spread_engine
    if gold_premium_regime:
        reg = gold_premium_regime.upper()
        if "SURGE" in reg:
            diem += 2.0
            ly_do.append("Vàng nội địa đang tăng nóng so với thế giới")
        elif "ELEVATED" in reg:
            diem += 1.0
            ly_do.append("Vàng nội địa cao hơn thế giới đáng kể")
        elif "DISCOUNT" in reg:
            diem -= 1.0

    # 3. Đồng thuận thị trường
    if market_state:
        meta = market_state.get("meta_state", {})
        flow = market_state.get("flow_state", {})
        breadth = market_state.get("breadth_state", {})
        risk_appetite = (meta.get("risk_appetite") or "").upper()
        flow_status = (flow.get("status") or "").upper()
        health_score = breadth.get("health_score")

        if "ĐÓNG" in risk_appetite:
            diem += 2.0
            ly_do.append("Thị trường đóng trạng thái rủi ro")
        elif "THẬN_TRỌNG" in risk_appetite:
            diem += 1.0
            ly_do.append("Tâm lý thị trường thận trọng")

        if "THU_HẸP" in flow_status:
            diem += 1.5
            ly_do.append("Dòng tiền đang co lại")
        elif "PHÂN_HÓA" in flow_status:
            diem += 1.0

        if health_score is not None and health_score < 30:
            diem += 1.5
            ly_do.append("Thị trường rất yếu")
        elif health_score is not None and health_score < 50:
            diem += 0.5

    # 4. Tín hiệu từ regime
    if regime_data:
        rad = regime_data.get("rad", {})
        if rad.get("activated"):
            diem += 2.0
            ly_do.append("RAD phát hiện chuyển pha")
        delta_adx = rad.get("signals", {}).get("delta_adx")
        if delta_adx is not None and abs(delta_adx) > 8:
            diem += 1.0
            ly_do.append(f"Biến động ADX mạnh ({delta_adx:+.1f})")

    # 5. Phân vùng
    if diem >= 8.0:
        zone = "HOẢNG_LOẠN"
    elif diem >= 4.5:
        zone = "PHÒNG_THỦ_RÕ_RỆT"
    elif diem >= 2.0:
        zone = "HƠI_THẬN_TRỌNG"
    else:
        zone = "BÌNH_THƯỜNG"

    info = _ZONE_MAP.get(zone, _ZONE_MAP["BÌNH_THƯỜNG"])
    return {
        "vung": zone,
        "ten": info["ten"],
        "ky_hieu": info["ky_hieu"],
        "mo_ta": info["mo_ta"],
        "diem": round(diem, 1),
        "ly_do": ly_do,
    }
