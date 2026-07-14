"""
market_snapshot.py — Ảnh chụp thị trường duy nhất

Nhiệm vụ:
  Tạo một bức ảnh duy nhất về thị trường tại thời điểm hiện tại.
  Mọi tầng phân tích đều đọc từ ảnh này, không đọc từ JSON cache riêng.

Nguyên tắc:
  - Gọi regime engine 1 lần duy nhất
  - Gọi early warning 1 lần duy nhất (dùng regime live)
  - Đọc structural từ snapshot file (cần chạy upstream trước)
  - Đóng gói tất cả vào 1 dict
  - Không có hàm nào trong pipeline được tự ý gọi detect_regime() nữa
"""

import json
import sys
from datetime import datetime
from pathlib import Path
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


DUONG_DAN_GOC = _hydrate_path()
if sys.platform == "win32" and getattr(sys.stdout, 'encoding', '') != 'utf-8':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import src.config


def _doc(ten_file: str) -> Optional[dict]:
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


def tao_anh_chup(target_date: Optional[str] = None, lang_mode: str = "compact") -> dict:
    """Tạo ảnh chụp thị trường duy nhất trong lần chạy này.

    Trả về dict gồm:
      - regime: trạng thái thị trường (từ regime engine live)
      - structural: trạng thái cấu trúc (từ file snapshot)
      - early_warning: cảnh báo sớm (từ early warning engine live)
      - constituents: các chỉ số thành phần
      - metadata: timestamp, target_date
    """
    if target_date is None:
        target_date = datetime.now().strftime("%Y-%m-%d")

    # ── Bước 1: Regime (live, 1 lần) ──
    from src.engine.regime_engine import detect_regime
    regime_data = detect_regime(target_date=target_date, lang_mode=lang_mode)

    # ── Bước 2: Cấu trúc ──
    hôm_nay = datetime.now().strftime("%Y-%m-%d")
    if target_date == hôm_nay:
        struct_data = _doc("structural_state.json") or {}
    else:
        from src.engine.structural_detector import detect_cau_truc
        struct_data = detect_cau_truc(target_date=target_date)
    trang_thai_cau_truc = struct_data.get("trang_thai", "N/A")
    so_tru_ok = struct_data.get("so_tru_ok", 0)
    entropy = struct_data.get("entropy")

    # ── Bước 3: Cảnh báo sớm (live, dùng regime vừa tính) ──
    from src.services.early_warning_engine import build_early_warning
    try:
        ew = build_early_warning(regime_data=regime_data)
        ew_cap_do = ew.get("cap_do_ma", "BÌNH_THƯỜNG")
        ew_diem = ew.get("tong_diem", 0)
        ew_canh_bao = ew.get("canh_bao", [])
    except Exception:
        ew = {}
        ew_cap_do = "BÌNH_THƯỜNG"
        ew_diem = 0
        ew_canh_bao = []

    # ── Bước 4: Phân tích chỉ số (Index Reality Unifier) ──
    from src.engine.index_reality_unifier import phan_tich_chi_so
    try:
        reality = phan_tich_chi_so(target_date=target_date)
    except Exception:
        reality = {}

    # ── Bước 4b: Ảnh hưởng nhóm trụ (Group Influence) ──
    from src.engine.group_influence_engine import tinh_anh_huong_nhom
    try:
        gi = tinh_anh_huong_nhom(target_date=target_date)
        gi_top = gi.group_contributions[0] if gi.group_contributions else None
    except Exception:
        gi = None
        gi_top = None

    # ── Bước 5: Các chỉ số thành phần ──
    details = regime_data.get("details", {})
    rad = regime_data.get("rad", {})
    rad_active = rad.get("activated", False)
    delta_adx = rad.get("signals", {}).get("delta_adx")

    # ── Đóng gói ──
    anh_chup = {
        "ngay": target_date,
        "thoi_gian_tao": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "regime": {
            "trang_thai": regime_data.get("status", "N/A"),
            "diem_so": regime_data.get("regime_score", 0.5),
            "adx": details.get("adx"),
            "delta_adx": delta_adx,
            "ty_le_atr": details.get("atr_ratio"),
            "diem_v": details.get("v_score"),
            "diem_b": details.get("b_score"),
            "diem_t": details.get("t_score"),
            "do_rong": details.get("breadth_pct"),
            "rad_kich_hoat": rad_active,
        },
        "cau_truc": {
            "trang_thai": trang_thai_cau_truc,
            "so_tru": so_tru_ok,
            "entropy": entropy,
            "chi_tiet_cot" if "cot" in struct_data else "chi_tiet": struct_data.get("tru", {}),
        },
        "canh_bao_som": {
            "cap_do": ew_cap_do,
            "diem": ew_diem,
            "canh_bao": ew_canh_bao,
            "co_canh_bao": ew_cap_do in ("RỦI_RO_HỆ_THỐNG", "CHUYỂN_PHA_MẠNH"),
        },
        "phan_tich_chi_so": {
            "diem_thi_truong_that": reality.get("diem_thi_truong_that"),
            "nhan_dien": reality.get("nhan_dien"),
            "chi_so_cong_bo": reality.get("chi_so_cong_bo"),
            "chi_so_noi_tai": reality.get("chi_so_noi_tai"),
            "do_lech_bdi": reality.get("do_lech_bdi"),
            "tap_trung_lcr": reality.get("tap_trung", {}).get("lcr_pct"),
            "do_lech_pha": reality.get("do_lech_pha"),
            "dien_giai": reality.get("dien_giai"),
        },
        "_regime_details": details,
        "nhom_anh_huong": {
            "breadth": gi.real_market_breadth if gi else None,
            "dominant_contribution_pct": gi.dominant_contribution_pct if gi else None,
            "dominant_group": gi.dominant_group if gi else None,
            "vnindex_ex_top10": gi.vnindex_ex_top10 if gi else None,
            "total_change_pct": gi.total_change_pct if gi else None,
            "artificial_market": gi.artificial_market if gi else False,
            "top_contribution_pts": (
                round(gi.vnindex_actual - gi.vnindex_ex_top10, 2)
                if gi and gi.vnindex_ex_top10 else None
            ),
        },
        "metadata": {
            "nguon_regime": "live (detect_regime)",
            "nguon_cau_truc": "live (detect_cau_truc)" if target_date != hôm_nay else "snapshot (structural_state.json)",
            "nguon_canh_bao": "live (build_early_warning)",
            "nguon_phan_tich_chi_so": "live (index_reality_unifier)",
            "nguon_nhom_anh_huong": "live (group_influence_engine)",
        },
    }

    # ── Bước 6: Asia Supply-chain Rotation (Layer 2 Reference Frame) ──
    try:
        from src.services.macro.time_series_aligner import compute_asia_rotation
        asia_rot = compute_asia_rotation()
        if asia_rot.get("status") == "OK":
            anh_chup["asia_rotation"] = {
                "angle_deg": asia_rot["rotation_angle_deg"],
                "lambda_max": asia_rot["lambda_max"],
                "lambda_ratio": asia_rot["lambda_ratio"],
                "spectral_entropy": asia_rot["spectral_entropy"],
                "n_days": asia_rot["n_days"],
                "pairwise_corr": asia_rot.get("pairwise_corr", {}),
                "stale_accumulated": asia_rot.get("stale_accumulated", 0),
                "covariance_inflated": asia_rot.get("covariance_inflated", False),
            }
            # Nếu Covariance đã bị lạm phát → gửi tín hiệu hạ governor_confidence
            if asia_rot.get("covariance_inflated"):
                try:
                    from src.services.macro.market_macro_coordinator import MarketMacroCoordinator
                    coordinator = MarketMacroCoordinator()
                    coordinator.set_governor_confidence(0.0)
                    anh_chup["governor_confidence"] = 0.0
                except Exception:
                    pass
        else:
            anh_chup["asia_rotation"] = {
                "angle_deg": None,
                "status": asia_rot.get("status", "ERROR"),
                "message": asia_rot.get("message", ""),
            }
    except Exception as exc:
        anh_chup["asia_rotation"] = {
            "angle_deg": None,
            "status": "ERROR",
            "message": str(exc),
        }

    # ── Bước 7: Delta Divergence Index (DDI) ──
    from src.alpha.delta_divergence import DeltaDivergenceIndex
    try:
        ddi = DeltaDivergenceIndex().calculate(anh_chup)
    except Exception:
        ddi = {"delta_sa": 0.0, "action_filter": "pass", "healing_illusion": False}
    anh_chup["delta_divergence"] = ddi

    # ── Bước 7: Atomic Parameter Identity (params_hash) ──
    from src.alpha.delta_divergence import build_params_registry
    from src.utils.params_hash import make_params_hash
    try:
        params_reg = build_params_registry()
        p_hash = make_params_hash(params_reg)
    except Exception:
        params_reg = {}
        p_hash = "unresolved"
    anh_chup["params_registry"] = params_reg
    anh_chup["params_hash"] = p_hash

    # ── Ghi vào snapshot_index.json ──
    try:
        out_dir = Path(src.config.DATA_DIR) / "output"
        out_dir.mkdir(parents=True, exist_ok=True)
        idx_path = out_dir / "snapshot_index.json"
        idx_entry = {
            "timestamp": anh_chup.get("thoi_gian_tao"),
            "ngay": target_date,
            "params_hash": p_hash,
            "regime": anh_chup.get("regime", {}).get("trang_thai"),
            "diem_so": anh_chup.get("regime", {}).get("diem_so"),
            "cau_truc": anh_chup.get("cau_truc", {}).get("trang_thai"),
            "delta_sa": ddi.get("delta_sa"),
            "action_filter": ddi.get("action_filter"),
        }
        if idx_path.exists():
            idx_data = json.loads(idx_path.read_text(encoding="utf-8"))
        else:
            idx_data = []
        idx_data.append(idx_entry)
        idx_path.write_text(json.dumps(idx_data, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass

    return anh_chup


def _ll(label: str, lang_mode: str = "annotated") -> str:
    """Localize a CLI label. Import is lazy to avoid circular imports at module level."""
    try:
        from src.core.canonical_output_adapter import localize_label
        return localize_label(label, lang_mode)
    except Exception:
        return label


def in_anh_chup(anh_chup: dict, lang_mode: str = "annotated"):
    """In ảnh chụp ra console để kiểm tra."""
    r = anh_chup.get("regime", {})
    c = anh_chup.get("cau_truc", {})
    ew = anh_chup.get("canh_bao_som", {})

    # Dynamic column auto-fit: compute max localized label width
    labels = ["Regime", "ADX", "Độ rộng", "ATR ratio", "Entropy", "Cảnh báo sớm", "Cấu trúc", "Asia θ"]
    max_w = max(len(_ll(label, lang_mode)) for label in labels)

    print("\n" + "=" * 55)
    print("  ẢNH CHỤP THỊ TRƯỜNG")
    print("=" * 55)
    print(f"  Ngày: {anh_chup.get('ngay', 'N/A')}")
    print(f"  Tạo lúc: {anh_chup.get('thoi_gian_tao', 'N/A')}")
    print()
    regime_raw = r.get('trang_thai', 'N/A')
    print(f"  {_ll('Regime', lang_mode):{max_w}s} {_ll(regime_raw, lang_mode)} ({r.get('diem_so', 0):.2f})")
    print(f"  {_ll('ADX', lang_mode):{max_w}s} {r.get('adx', 'N/A')}")
    do_rong_raw = r.get('do_rong')
    if do_rong_raw is not None:
        print(f"  {_ll('Độ rộng', lang_mode):{max_w}s} {do_rong_raw}%")
    else:
        print(f"  {_ll('Độ rộng', lang_mode):{max_w}s} BREADTH_SUSPENDED (đang cập nhật)")
    print(f"  {_ll('ATR ratio', lang_mode):{max_w}s} {r.get('ty_le_atr', 'N/A')}")
    print()
    print(f"  {_ll('Cấu trúc', lang_mode):{max_w}s} {_ll(c.get('trang_thai', 'N/A'), lang_mode)} ({c.get('so_tru', '?')}/3 trụ)")
    print(f"  {_ll('Entropy', lang_mode):{max_w}s} {c.get('entropy', 'N/A')}")

    # Asia supply-chain rotation (Layer 2 reference frame)
    asia_rot = anh_chup.get("asia_rotation", {})
    if asia_rot.get("angle_deg") is not None:
        angle = asia_rot["angle_deg"]
        lmax = asia_rot.get("lambda_max", "?")
        lrat = asia_rot.get("lambda_ratio", "?")
        print(f"  {_ll('Asia θ', lang_mode):{max_w}s} {angle}°  (Λ_max={lmax}, Λ_ratio={lrat})")

    print()

    ew_raw = ew.get('cap_do', 'N/A')
    print(f"  {_ll('Cảnh báo sớm', lang_mode):{max_w}s} {_ll(ew_raw, lang_mode)} ({ew.get('diem', 0)}đ)")
    for cb in ew.get("canh_bao", []):
        print(f"    • {cb}")

    # DDI
    ddi = anh_chup.get("delta_divergence", {})
    if ddi:
        ddi_icon = {"pass": "🟢", "caution": "🟡", "block": "🔴"}.get(ddi.get("action_filter"), "⚪")
        print()
        ddi_label = _ll('DDI', lang_mode)
        print(f"  {ddi_label:{max_w}s} {ddi_icon} Δ_SA={ddi.get('delta_sa', 'N/A')}  "
              f"({ddi.get('action_filter', 'N/A')})")
        print(f"    dS/dt={ddi.get('dS_dt', 'N/A')}  AC_lat={ddi.get('ac_latency', 'N/A')}  "
              f"α={ddi.get('alpha_regime', 'N/A')}")
        if ddi.get("healing_illusion"):
            hi_label = _ll('HEALING ILLUSION', lang_mode)
            print(f"    ⚠ {hi_label} — stress vượt adaptation")

    # params_hash
    p_hash = anh_chup.get("params_hash")
    if p_hash:
        print(f"  Params Hash:      {p_hash}")

    print("=" * 55)
