"""
structural_detector.py — Bộ phát hiện lệch cấu trúc thị trường

Đo 3 trụ:
  1. Lan tỏa dòng tiền (breadth + LCR)
  2. Đồng thuận ngành (sector diffusion)
  3. Chỉ số vs nội bộ (BDI + RS dispersion)

Output: 1 trong 4 trạng thái cấu trúc + vector nguyên nhân
"""

import sys, os, json
from pathlib import Path
from datetime import datetime
from typing import Optional

def _hydrate_path():
    if getattr(sys, 'frozen', False):
        root_path = Path(sys.executable).resolve().parent
    else:
        current = Path(__file__).resolve().parent
        root_path = current
        while current != current.parent:
            if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
                root_path = current
                break
            current = current.parent
    for p in (root_path, root_path / "backend"):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    return root_path

PROJECT_ROOT = _hydrate_path()
if sys.platform == "win32" and getattr(sys.stdout, 'encoding', '') != 'utf-8':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import src.config


def _doc_json(ten_file: str) -> Optional[dict]:
    """Đọc file JSON từ data/output hoặc data/"""
    paths = [
        Path(src.config.DATA_DIR) / "output" / ten_file,
        Path(src.config.DATA_DIR) / ten_file,
    ]
    for p in paths:
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                return None
    return None


def _lay_entropy(state: Optional[dict], pulse: Optional[dict], ms: Optional[dict]) -> Optional[float]:
    """Tính entropy từ driver_normalizer hoặc state proxy."""
    if state and isinstance(state, dict):
        e = state.get("entropy") or state.get("shannon_entropy")
        if e is not None:
            return float(e)
        meta = state.get("meta_state", {})
        if isinstance(meta, dict):
            e = meta.get("entropy")
            if e is not None:
                return float(e)

    try:
        health = pulse.get("health_score_ma20") if pulse else None
        lcr = ms.get("lcr_pct") if ms else None
        from src.engine.driver_normalizer import driver_state_from_engine_outputs
        driver = driver_state_from_engine_outputs(
            breadth_health=health,
            lcr_pct=lcr,
            flow_bias=0.5,
            v_score=0.5,
            t_score=1.0,
        )
        if driver and hasattr(driver, "entropy"):
            return float(driver.entropy)
    except Exception:
        pass

    if state and isinstance(state, dict):
        ss = state.get("state_stability", {})
        if isinstance(ss, dict):
            score = ss.get("score")
            if score is not None:
                return float(1.0 - score)
    return None


def detect_cau_truc(target_date: Optional[str] = None) -> dict:
    """Phát hiện lệch cấu trúc thị trường dựa trên 3 trụ."""
    if target_date is None:
        target_date = datetime.now().strftime("%Y-%m-%d")

    # ---- Đọc dữ liệu đầu vào từ các file JSON có sẵn ----
    pulse = _doc_json("market_pulse.json")
    ms = _doc_json("market_structure.json")
    heatmap = _doc_json("sector_heatmap.json")
    state = _doc_json("market_state.json")

    # ---- Trụ 1: Lan tỏa dòng tiền ----
    tru_1 = _tru_lan_toa(pulse, ms)

    # ---- Trụ 2: Đồng thuận ngành ----
    tru_2 = _tru_dong_thuan_nganh(heatmap)

    # ---- Trụ 3: Chỉ số vs nội bộ ----
    tru_3 = _tru_index_vs_noi_bo(ms)

    # ---- Entropy ----
    entropy = _lay_entropy(state, pulse, ms)

    # ---- Đếm số trụ OK ----
    tru_ok = sum([1 if tru_1.get("ok") else 0, 1 if tru_2.get("ok") else 0, 1 if tru_3.get("ok") else 0])

    # ---- Áp dụng luật entropy cho VỠ CẤU TRÚC ----
    if entropy is not None and entropy > 0.8 and tru_ok <= 1:
        trang_thai = "VỠ CẤU TRÚC"
    else:
        trang_thai = {
            3: "ĐỒNG THUẬN",
            2: "PHÂN HÓA BÌNH THƯỜNG",
            1: "PHÂN KỲ CẤU TRÚC",
            0: "VỠ CẤU TRÚC",
        }[tru_ok]

    ket_qua = {
        "ngay": target_date,
        "trang_thai": trang_thai,
        "so_tru_ok": tru_ok,
        "tong_so_tru": 3,
        "tru": {"lan_toa_dong_tien": tru_1, "dong_thuan_nganh": tru_2, "index_vs_noi_bo": tru_3},
        "entropy": round(entropy, 3) if entropy is not None else None,
        "nguyen_nhan": _tao_nguyen_nhan(trang_thai, tru_1, tru_2, tru_3, entropy),
    }

    # ---- Lưu file ----
    out_dir = Path(src.config.DATA_DIR) / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "structural_state.json", "w", encoding="utf-8") as f:
        json.dump(ket_qua, f, indent=2, ensure_ascii=False)

    return ket_qua


def _tru_lan_toa(pulse: Optional[dict], ms: Optional[dict]) -> dict:
    """Trụ 1: Dòng tiền có lan rộng không?"""
    nguyen_nhan = []
    ok = False

    health = pulse.get("health_score_ma20") if pulse else None
    lcr = ms.get("lcr_pct") if ms else None

    if health is not None:
        he_so = 1.0
        if lcr is not None and lcr > 40:
            he_so = 0.7
        elif lcr is not None and lcr > 30:
            he_so = 0.85
        hieu_chinh = health * he_so
        if hieu_chinh > 50:
            ok = True
            nguyen_nhan.append(f"dòng tiền lan rộng (breadth={health:.0f}%)")
        elif hieu_chinh > 30:
            nguyen_nhan.append(f"dòng tiền trung bình (breadth={health:.0f}%, LCR={lcr}%)" if lcr else f"dòng tiền trung bình (breadth={health:.0f}%)")
        else:
            nguyen_nhan.append(f"dòng tiền co hẹp (breadth={health:.0f}%, LCR={lcr}%)" if lcr else f"dòng tiền co hẹp (breadth={health:.0f}%)")
    else:
        nguyen_nhan.append("chưa có dữ liệu độ rộng thị trường")

    return {"ok": ok, "do_rong": round(health, 1) if health is not None else None, "lcr": lcr, "nguyen_nhan": "; ".join(nguyen_nhan)}


def _tru_dong_thuan_nganh(heatmap) -> dict:
    """Trụ 2: Các ngành có đồng thuận không?"""
    nguyen_nhan = []
    ok = False

    sectors = None
    if isinstance(heatmap, list):
        sectors = heatmap
    elif isinstance(heatmap, dict):
        sectors = heatmap.get("sectors") if isinstance(heatmap.get("sectors"), list) else None
    if sectors:
        tong = len(sectors)
        tang = sum(1 for s in sectors if s.get("avg_change", 0) > 0)
        if tong > 0:
            ty_le = (tang / tong) * 100
            if ty_le > 60:
                ok = True
                nguyen_nhan.append(f"{tang}/{tong} ngành tăng ({ty_le:.0f}%)")
            elif ty_le > 40:
                nguyen_nhan.append(f"{tang}/{tong} ngành tăng ({ty_le:.0f}%) — phân hóa nhẹ")
            else:
                nguyen_nhan.append(f"chỉ {tang}/{tong} ngành tăng ({ty_le:.0f}%) — thiếu đồng thuận")
        else:
            nguyen_nhan.append("không có dữ liệu ngành")
    else:
        nguyen_nhan.append("không có dữ liệu đồng thuận ngành")

    return {"ok": ok, "nguyen_nhan": "; ".join(nguyen_nhan)}


def _tru_index_vs_noi_bo(ms: Optional[dict]) -> dict:
    """Trụ 3: Chỉ số có phản ánh đúng thị trường không?"""
    nguyen_nhan = []
    ok = False

    bdi = ms.get("bdi_pct") if ms else None
    bdi_signal = ms.get("bdi_signal") if ms else None

    if bdi is not None:
        abs_bdi = abs(bdi)
        if abs_bdi < 5:
            ok = True
            nguyen_nhan.append(f"chỉ số phản ánh đúng thị trường (BDI={bdi:+.1f}%)")
        elif abs_bdi < 10:
            nguyen_nhan.append(f"chỉ số lệch nhẹ (BDI={bdi:+.1f}%, {bdi_signal})")
        else:
            nguyen_nhan.append(f"chỉ số lệch nội bộ mạnh (BDI={bdi:+.1f}%, {bdi_signal})")
    else:
        nguyen_nhan.append("không có dữ liệu BDI")

    return {"ok": ok, "bdi": bdi, "bdi_signal": bdi_signal, "nguyen_nhan": "; ".join(nguyen_nhan)}


def _tao_nguyen_nhan(trang_thai: str, tru_1: dict, tru_2: dict, tru_3: dict, entropy: Optional[float]) -> list:
    nguyen_nhan = []
    for t in [tru_1, tru_2, tru_3]:
        nn = t.get("nguyen_nhan", "")
        if nn:
            nguyen_nhan.append(nn)
    if entropy is not None and entropy > 0.8:
        nguyen_nhan.append(f"entropy cao ({entropy:.2f}) — thị trường mất ổn định")
    if trang_thai == "VỠ CẤU TRÚC":
        nguyen_nhan.append("cấu trúc thị trường không còn đồng bộ")
    return nguyen_nhan


def in_bao_cao(ket_qua: dict):
    """In báo cáo console."""
    print("\n" + "=" * 60)
    print("  BỘ PHÁT HIỆN LỆCH CẤU TRÚC THỊ TRƯỜNG")
    print("=" * 60)
    tt = ket_qua.get("trang_thai", "N/A")
    icons = {"ĐỒNG THUẬN": "🟢", "PHÂN HÓA BÌNH THƯỜNG": "🟡", "PHÂN KỲ CẤU TRÚC": "🟠", "VỠ CẤU TRÚC": "🔴"}
    icon = icons.get(tt, "⚪")
    print(f"  {icon} Trạng thái: {tt} ({ket_qua.get('so_tru_ok', 0)}/3 trụ)")
    print(f"  Entropy: {ket_qua.get('entropy', 'N/A')}")
    print()
    for ten_tru, tru in ket_qua.get("tru", {}).items():
        ok_icon = "✔" if tru.get("ok") else "✘"
        print(f"    {ok_icon} {ten_tru}: {tru.get('nguyen_nhan', '')}")
    print()
    nn = ket_qua.get("nguyen_nhan", [])
    if nn:
        print("  Nguyên nhân:")
        for n in nn:
            print(f"    • {n}")
    print("=" * 60)


if __name__ == "__main__":
    kq = detect_cau_truc()
    in_bao_cao(kq)
