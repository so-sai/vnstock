"""
orchestrator.py — Bộ quyết định cuối cùng

Thứ tự ưu tiên (CAO → THẤP):
  1. Cấu trúc thị trường (nếu VỠ CẤU TRÚC → DỪNG NGOÀI, không xét gì thêm)
  2. Cảnh báo sớm (nếu chuyển pha → hạ mức hành động)
  3. Trạng thái thị trường (regime — ngữ cảnh)
  4. Độ tin cậy → Lớp bảo vệ quyết định (có thể chặn nếu quá nhiễu)

Nguyên tắc dữ liệu:
  - Mọi tầng dùng chung 1 ảnh chụp thị trường duy nhất mỗi lần chạy
  - Ảnh chụp được tạo bởi market_snapshot.tao_anh_chup()
  - Không đọc regime từ file cache, không recompute regime giữa chừng
"""

import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


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

QUYET_DINH = ["THAM GIA FULL", "THAM GIA", "THAM GIA DO", "QUAN SAT", "GIAM RUI RO", "DUNG NGOAI"]


def quyet_dinh_cuoi(target_date: Optional[str] = None) -> dict:
    """Trả về quyết định cuối cùng dựa trên 3 lớp phân tích.

    Luồng:
      1. Tạo ảnh chụp thị trường (regime + structural + cảnh báo sớm) — 1 lần
      2. Logic quyết định theo thứ tự ưu tiên
      3. Tính độ tin cậy — dùng chung ảnh chụp
      4. Lớp bảo vệ — kiểm tra an toàn cuối
    """
    if target_date is None:
        target_date = datetime.now().strftime("%Y-%m-%d")

    # ---- Bước 1: Ảnh chụp thị trường duy nhất ----
    from src.core.market_snapshot import tao_anh_chup
    anh_chup = tao_anh_chup(target_date)

    r = anh_chup.get("regime", {})
    c = anh_chup.get("cau_truc", {})
    ew = anh_chup.get("canh_bao_som", {})

    trang_thai_cau_truc = c.get("trang_thai", "N/A")
    so_tru_ok = c.get("so_tru", 0)
    entropy = c.get("entropy")
    regime_status = r.get("trang_thai", "N/A")
    early_warning = ew.get("co_canh_bao", False)

    # ---- Bước 0: Data Quality Guard ----
    try:
        import pandas as pd

        from src.database.db_core import get_connection
        with get_connection() as _conn:
            count_liquid = pd.read_sql(
                "SELECT COUNT(DISTINCT symbol) as cnt FROM daily_ohlcv "
                "WHERE date=? AND volume>50000 AND symbol!='VNINDEX'",
                _conn, params=(target_date,)
            ).iloc[0]['cnt']
        if count_liquid < 50:
            ket_qua_tam = {
                "ngay": target_date, "quyet_dinh": "DUNG NGOAI",
                "ly_do": [f"dữ liệu không đảm bảo — chỉ {int(count_liquid)} mã đủ thanh khoản",
                          "cảnh báo DATA QUALITY — nguy cơ dữ liệu nhiễu/lỗi feed",
                          "tuyệt đối không giao dịch trên dữ liệu méo"],
                "chi_tiet": {"data_quality_warning": True, "so_ma_du_lieu": int(count_liquid)},
            }
            ket_qua_tam["độ_tin_cậy_sau_hiệu_chỉnh"] = {"điểm_số": 0, "mức": "THAP", "tạm_ngưng": True, "lý_do_tạm_ngưng": "dữ liệu không đủ thanh khoản để ra quyết định"}
            ket_qua_tam["bi_chặn_bởi_bảo_vệ"] = True
            ket_qua_tam["lý_do_chặn"] = f"chỉ {int(count_liquid)} mã đủ volume > 50k"
            return ket_qua_tam
    except Exception:
        pass
    # ---- Bước 2: Logic quyết định theo thứ tự ưu tiên ----
    ly_do = []

    # ⭐ Ưu tiên 1: Cấu trúc thị trường
    if trang_thai_cau_truc == "VỠ CẤU TRÚC":
        quyet_dinh = "DUNG NGOAI"
        ly_do = [
            "cấu trúc thị trường đang vỡ",
            "dòng tiền không lan tỏa hoặc chỉ số lệch nội bộ",
            "rủi ro hệ thống cao",
        ]
    elif trang_thai_cau_truc == "PHÂN KỲ CẤU TRÚC":
        # ⭐ Ưu tiên 2: Cảnh báo sớm (chỉ có hiệu lực khi cấu trúc chưa vỡ)
        if early_warning:
            quyet_dinh = "GIAM RUI RO"
            ly_do = [
                "thị trường phân kỳ cấu trúc",
                "dòng tiền tập trung hẹp, chỉ số không đại diện",
                "có tín hiệu phòng thủ từ cảnh báo sớm",
            ]
        else:
            quyet_dinh = "GIAM RUI RO"
            ly_do = [
                "thị trường phân kỳ cấu trúc",
                "chỉ số tăng nhưng nội bộ yếu",
                "cần giảm rủi ro danh mục",
            ]
    elif trang_thai_cau_truc == "PHÂN HÓA BÌNH THƯỜNG":
        quyet_dinh = "QUAN SAT"
        ly_do = [
            "thị trường phân hóa nhẹ nhưng ổn định",
            "chưa có xu hướng rõ ràng",
            "ưu tiên theo dõi thêm",
        ]
    elif trang_thai_cau_truc == "ĐỒNG THUẬN":
        if early_warning:
            quyet_dinh = "QUAN SAT"
            ly_do = [
                "cấu trúc thị trường đồng thuận",
                "nhưng có tín hiệu phòng thủ từ cảnh báo sớm",
                "cần xác nhận thêm trước khi tham gia",
            ]
        else:
            quyet_dinh = "THAM GIA"
            ly_do = [
                "thị trường đồng thuận cả 3 trụ",
                "dòng tiền lan rộng, các ngành cùng tăng",
                "chỉ số phản ánh đúng thị trường",
            ]
    else:
        quyet_dinh = "QUAN SAT"
        ly_do = ["chưa đủ dữ liệu để kết luận"]

    # ---- Bổ sung entropy nếu cao ----
    if entropy is not None and entropy > 0.8:
        ly_do.append(f"entropy thị trường cao ({entropy:.2f})")

    # ---- ⭐ Ưu tiên 3: Trạng thái thị trường (regime) ----
    if regime_status != "N/A" and regime_status != "TRENDING":
        if quyet_dinh != "DUNG NGOAI":
            ly_do.append(f"thị trường đang {regime_status}")

    # ---- ⭐ Index Reality: inject context vào pipeline ----
    ir = anh_chup.get("phan_tich_chi_so", {})
    do_lech_pha_ir = ir.get("do_lech_pha")
    diem_thi_truong_that_ir = ir.get("diem_thi_truong_that")
    nhan_dien_ir = ir.get("nhan_dien")

    if do_lech_pha_ir == "MANH_GIA_TAO":
        # Override: thị trường tăng giả tạo → dừng ngoài
        quyet_dinh = "DUNG NGOAI"
        ly_do = ["thị trường tăng giả tạo — chỉ số không đại diện"]
    elif nhan_dien_ir == "THI_TRUONG_AO" or (diem_thi_truong_that_ir is not None and diem_thi_truong_that_ir < 0.2):
        quyet_dinh = "DUNG NGOAI"
        ly_do = ["thị trường ảo — chỉ số không phản ánh giá trị thực"]
    elif diem_thi_truong_that_ir is not None and diem_thi_truong_that_ir < 0.4:
        if quyet_dinh == "THAM GIA":
            quyet_dinh = "QUAN SAT"
            ly_do.append("chất lượng chỉ số thấp — hạ mức hành động")
        elif quyet_dinh == "GIAM RUI RO":
            ly_do.append("thị trường méo — xác nhận phân kỳ")

    # ---- Chỉ giữ 3 lý do chính ----
    ly_do = ly_do[:3]

    ket_qua = {
        "ngay": target_date,
        "quyet_dinh": quyet_dinh,
        "ly_do": ly_do,
        "chi_tiet": {
            "cau_truc": trang_thai_cau_truc,
            "regime": regime_status,
            "canh_bao_som": "có" if early_warning else "không",
            "entropy": round(entropy, 3) if entropy is not None else None,
            "so_tru_cau_truc": so_tru_ok,
            "adx": r.get("adx"),
            "delta_adx": r.get("delta_adx"),
        },
    }

    # ---- Bước 3: Tự đánh giá độ tin cậy (dùng chung ảnh chụp) ----
    try:
        from src.engine.confidence_layer import đánh_giá_độ_tin_cậy
        đg = đánh_giá_độ_tin_cậy(anh_chup=anh_chup)
        ket_qua["độ_tin_cậy_sau_hiệu_chỉnh"] = {
            "điểm_số": đg["điểm_tin_cậy"],
            "mức": đg["mức_đánh_giá"],
            "tạm_ngưng": đg["tạm_ngưng_kết_luận"],
            "lý_do_tạm_ngưng": đg["lý_do_tạm_ngưng"],
        }
    except Exception:
        ket_qua["độ_tin_cậy_sau_hiệu_chỉnh"] = {
            "điểm_số": 0.5, "mức": "TRUNG_BINH", "tạm_ngưng": False,
        }

    # ---- Bước 4: Lớp bảo vệ quyết định (Guard) ----
    du_lieu_lien_ngan_hang = None
    try:
        from src.services.macro.interbank_zscore import assess_interbank_risk
        du_lieu_lien_ngan_hang = assess_interbank_risk()
    except Exception:
        pass

    # ── STRUCTURE_UNKNOWN: sensor blind → force DỪNG NGOÀI + 0.0 ──
    if du_lieu_lien_ngan_hang and du_lieu_lien_ngan_hang.get("veto") == "STRUCTURE_UNKNOWN":
        ket_qua["sensor_status"] = "CRITICAL_SBV_CHANGED"
        ket_qua["quyet_dinh"] = "DUNG NGOAI"
        ket_qua["ly_do"] = [
            "SBV HTML structure changed — sensor blind",
            "Emergency shutoff: không thể đánh giá rủi ro vĩ mô",
            "chạy 'python ptck.py sbv-update' sau khi cập nhật fixtures",
        ]
        ket_qua["bi_chặn_bởi_bảo_vệ"] = True
        ket_qua["lý_do_chặn"] = "SBV sensor blind — STRUCTURE_UNKNOWN"
        ket_qua["he_so_giam_ty_trong"] = 0.0
        logger.critical("[ORCH] SBV STRUCTURE UNKNOWN — force DỪNG NGOÀI, position=0.0")

    # ── Bước 4a: On-demand interbank check (Emergency Recall) ──
    # Chỉ kích hoạt khi: stale_override=True + raw signal là THAM GIA FULL.
    # Dùng quyet_dinh_raw (trước guard) để tránh lệ thuộc thứ tự thực thi.
    # Giới hạn tần suất Chromium bằng cooldown 30 phút (persistent disk).
    _recall_triggered = False
    _quyet_dinh_raw = ket_qua.get("quyet_dinh", "")
    if du_lieu_lien_ngan_hang and du_lieu_lien_ngan_hang.get("stale_override"):
        if _quyet_dinh_raw in ("THAM GIA FULL", "THAM GIA"):
            try:
                from src.services.macro.interbank_seeder import kiem_tra_sbv_theo_yeu_cau
                _recall = kiem_tra_sbv_theo_yeu_cau()
                _recall_triggered = _recall.get("scraped", False)
                on_rate = _recall.get("ON")
                if on_rate is not None and on_rate > 10:
                    logger.warning(
                        "[RECALL] SBV on-demand phát hiện ON=%.2f%% > 10 — force DỪNG NGOÀI",
                        on_rate,
                    )
                    ket_qua["quyet_dinh"] = "DUNG NGOAI"
                    ket_qua["ly_do"] = [
                        "Emergency Recall: SBV ON rate vẫn > 10%",
                        "stale_override bị ghi đè bởi dữ liệu thực tế",
                        "hủy lệnh mua — dừng ngoài khẩn cấp",
                    ]
                    ket_qua["bi_chặn_bởi_bảo_vệ"] = True
                    ket_qua["lý_do_chặn"] = f"SBV on-demand check: ON={on_rate}% > 10%"
                    ket_qua["he_so_giam_ty_trong"] = 0.0
                elif on_rate is not None and on_rate > 5:
                    logger.info(
                        "[RECALL] SBV on-demand: ON=%.2f%% (elevated), giữ nguyên guard",
                        on_rate,
                    )
            except Exception as e:
                logger.warning("[RECALL] SBV on-demand check failed: %s", e)

    # ── Bước 4b: Guard chính ──
    if not _recall_triggered or ket_qua.get("quyet_dinh") != "DUNG NGOAI":
        try:
            from src.engine.decision_guard import kiem_tra_an_toan
            guarded = kiem_tra_an_toan(
                quyet_dinh_de_xuat=ket_qua["quyet_dinh"],
                ly_do_de_xuat=ket_qua.get("ly_do", []),
                do_tin_cay=ket_qua["độ_tin_cậy_sau_hiệu_chỉnh"],
                anh_chup=anh_chup,
                du_lieu_lien_ngan_hang=du_lieu_lien_ngan_hang,
            )
            ket_qua["quyet_dinh"] = guarded["quyet_dinh"]
            ket_qua["ly_do"] = guarded["ly_do"]
            ket_qua["bi_chặn_bởi_bảo_vệ"] = guarded["bi_chặn"]
            ket_qua["lý_do_chặn"] = guarded["ly_do_chặn"]
            ket_qua["he_so_giam_ty_trong"] = guarded.get("he_so_giam_ty_trong", 1.0)
        except Exception:
            if not _recall_triggered:
                ket_qua["bi_chặn_bởi_bảo_vệ"] = False
                ket_qua["lý_do_chặn"] = None
                ket_qua["he_so_giam_ty_trong"] = 1.0

    # ---- Bước 4c: Delta Divergence Index (DDI) Gate ----
    ddi = anh_chup.get("delta_divergence", {})
    ddi_filter = ddi.get("action_filter", "pass")
    ddi_healing = ddi.get("healing_illusion", False)
    if ddi_filter == "block" or ddi_healing:
        current = ket_qua.get("quyet_dinh", "")
        if current in ("THAM GIA FULL", "THAM GIA", "THAM GIA DO"):
            ket_qua["quyet_dinh"] = "QUAN SAT"
            ket_qua["ly_do"] = [
                "DDI Gate: Δ_SA dương kéo dài — stress vượt adaptation",
                "healing illusion detected — chờ xác nhận từ Validation Tier",
                "hạ mức hành động xuống QUAN SAT",
            ]
            ket_qua["bi_chặn_bởi_bảo_vệ"] = True
            ket_qua["lý_do_chặn"] = f"DDI Δ_SA={ddi.get('delta_sa')} > threshold — healing illusion"
    elif ddi_filter == "caution":
        current = ket_qua.get("quyet_dinh", "")
        if current == "THAM GIA FULL":
            ket_qua["quyet_dinh"] = "THAM GIA DO"
            ket_qua["ly_do"] = [
                "DDI Gate caution: Δ_SA dương nhẹ",
                "stress tăng nhanh hơn năng lực hấp thụ — giảm tỷ trọng",
                "tham gia thăm dò — chờ tín hiệu xác nhận",
            ]
    ket_qua["delta_divergence"] = ddi

    # ---- Bước 5: Recovery Override + Structural Healing ----
    # Hệ thống đang DỪNG NGOÀI → kiểm tra khả năng mở khóa
    if ket_qua.get("quyet_dinh") == "DUNG NGOAI":
        is_fake_market = (
            do_lech_pha_ir == "MANH_GIA_TAO"
            or nhan_dien_ir == "THI_TRUONG_AO"
            or (diem_thi_truong_that_ir is not None and diem_thi_truong_that_ir < 0.2)
        )
        if not is_fake_market:
            try:
                from src.engine.recovery_engine import evaluate_recovery_status
                from src.engine.structural_healing import phan_tich_hoi_phuc

                regime_details = anh_chup.get("_regime_details", {})
                velocity_5d = float(regime_details.get("breadth_momentum", 0))
                regime_data_for_recovery = {
                    "details": {
                        "breadth_pct": regime_details.get("breadth_pct", 0),
                        "atr_ratio": regime_details.get("atr_ratio", 0),
                        "breadth_std_10d": regime_details.get("breadth_std_10d", 0),
                    }
                }

                # 5a. Recovery Engine (xung lực giá/khối lượng)
                recovery = evaluate_recovery_status(
                    regime_data=regime_data_for_recovery,
                    velocity_5d=velocity_5d,
                    target_date=target_date,
                )
                ket_qua["recovery_status"] = recovery["status"]
                ket_qua["recovery_log"] = recovery.get("log", [])

                if recovery.get("is_recovery"):
                    ket_qua["quyet_dinh"] = "THAM GIA DO"
                    ket_qua["ly_do"] = [
                        "cấu trúc đã lành — recovery engine xác nhận",
                        "dòng tiền mồi quay lại (thrust/velocity)",
                        "mở lệnh thăm dò — giám sát chặt",
                    ]
                    ket_qua["bi_chặn_bởi_bảo_vệ"] = False
                    ket_qua["lý_do_chặn"] = None
                else:
                    # 5b. Structural Healing (chuyển trạng thái cấu trúc T-1→T-0)
                    healing = phan_tich_hoi_phuc(target_date=target_date)
                    ket_qua["healing_status"] = healing["trang_thai_hoi_phuc"]
                    ket_qua["chuyen_doi_cau_truc"] = healing["chuyen_doi"]

                    hs = healing["trang_thai_hoi_phuc"]
                    if hs == "TAI_PHAT_BENH":
                        ket_qua["ly_do"] = [
                            "cấu trúc tiếp tục vỡ — hồi phục thất bại",
                            "cấm tuyệt đối bắt đáy — rủi ro sập lần 2",
                            "chờ tín hiệu lành thực sự",
                        ]
                    elif hs in ("BAT_DAU_LANH", "DANG_LANH"):
                        ket_qua["quyet_dinh"] = "QUAN SAT"
                        ket_qua["ly_do"] = [
                            f"cấu trúc đang lành ({hs})",
                            "máu đã ngừng chảy — hé mắt quan sát",
                            "chưa mua — chờ recovery hoặc đồng thuận",
                        ]
                        ket_qua["bi_chặn_bởi_bảo_vệ"] = False
                        ket_qua["lý_do_chặn"] = None
            except Exception:
                pass

    # ---- Bước 6: Phase 3 — Structural Consensus (nâng cấp lên THAM GIA FULL) ----
    # Chỉ kích hoạt khi hệ thống đang ở trạng thái mở (THAM GIA DO / QUAN SAT)
    # và thị trường đạt đồng thuận tuyệt đối + retest confirmation
    if ket_qua.get("quyet_dinh") in ("THAM GIA DO", "QUAN SAT"):
        adx_value = r.get("adx")
        delta_adx_value = r.get("delta_adx")
        is_trending_up = delta_adx_value is not None and delta_adx_value > 0

        consensus_conditions = (
            trang_thai_cau_truc == "ĐỒNG THUẬN"
            and so_tru_ok == 3
            and entropy is not None and entropy > 2.0
            and adx_value is not None and adx_value > 25
            and is_trending_up
        )

        if consensus_conditions:
            # Kiểm tra retest: pullback T-1 trên volume thấp → xác nhận xu hướng
            retest_confirmed = False
            retest_log = ""
            try:
                import pandas as pd

                from src.database.db_core import get_connection
                with get_connection() as _rc:
                    prev_dates = pd.read_sql(
                        "SELECT DISTINCT date FROM daily_ohlcv WHERE date<? AND symbol='VNINDEX' ORDER BY date DESC LIMIT 2",
                        _rc, params=(target_date,)
                    )["date"].tolist()
                if len(prev_dates) >= 2:
                    t1, t2 = prev_dates[0], prev_dates[1]
                    with get_connection() as _rc2:
                        vnindex_data = pd.read_sql(
                            "SELECT date, close, volume FROM daily_ohlcv "
                            "WHERE symbol='VNINDEX' AND date IN (?, ?, ?) ORDER BY date",
                            _rc2, params=(target_date, t1, t2)
                        )
                    if len(vnindex_data) >= 3:
                        c0, c1, c2 = vnindex_data['close'].values
                        v1 = vnindex_data.iloc[1]['volume']
                        vol_hist = pd.read_sql(
                            "SELECT date, SUM(volume) as total FROM daily_ohlcv "
                            "WHERE date<? AND date>=date(?, '-27 days') AND symbol='VNINDEX' "
                            "GROUP BY date ORDER BY date",
                            _rc2, params=(target_date, target_date,)
                        )
                        v_ma20 = float(vol_hist['total'].tail(20).mean()) if len(vol_hist) >= 5 else 0
                        is_pullback = float(c1) < float(c2)
                        is_low_vol = v_ma20 > 0 and (float(v1) / v_ma20) < 0.8
                        is_recovery = float(c0) > float(c1)
                        if is_pullback and is_low_vol and is_recovery:
                            retest_confirmed = True
                            retest_log = f"retest T-1: pullback x{float(v1)/v_ma20:.2f} vol MA20 → xác nhận xu hướng"
            except Exception:
                pass

            if retest_confirmed:
                ket_qua["quyet_dinh"] = "THAM GIA FULL"
                ket_qua["ly_do"] = [
                    "đồng thuận cấu trúc hoàn toàn (3/3 trụ)",
                    "dòng tiền lan tỏa diện rộng (entropy > 2.0)",
                    "xu hướng tăng hữu cơ được xác nhận (ADX > 25, retest thành công)",
                    "nâng tỷ trọng lên FULL — mở toàn bộ vị thế",
                ]
                ket_qua["bi_chặn_bởi_bảo_vệ"] = False
                ket_qua["lý_do_chặn"] = None
                ket_qua["retest_confirmation"] = retest_log
            else:
                # Đồng thuận xảy ra nhưng chưa có retest → giữ ở THAM GIA DO
                if ket_qua.get("quyet_dinh") == "QUAN SAT":
                    ket_qua["quyet_dinh"] = "THAM GIA DO"
                    ket_qua["ly_do"] = [
                        "cấu trúc đồng thuận nhưng chưa có retest xác nhận",
                        "vào lệnh thăm dò trước — chờ retest để FULL",
                        "hạn chế rủi ro T+2.5",
                    ]
                    ket_qua["bi_chặn_bởi_bảo_vệ"] = False
                    ket_qua["lý_do_chặn"] = None
                ket_qua["retest_confirmation"] = "chờ retest"

    # ---- Bước 7: Fast-Exit Guard (bảo vệ sau khi vào lệnh FULL) ----
    # Nếu đang THAM GIA FULL mà phát hiện volume spike ở trụ cột → giảm gấp
    if ket_qua.get("quyet_dinh") == "THAM GIA FULL":
        try:
            from src.engine.fast_exit_guard import kiem_tra_phan_phoi
            phan_phoi = kiem_tra_phan_phoi(target_date=target_date)
            ket_qua["fast_exit_guard"] = phan_phoi
            if phan_phoi.get("co_phan_phoi") and phan_phoi.get("muc_do") == "CAO":
                ket_qua["quyet_dinh"] = "GIAM RUI RO"
                ket_qua["ly_do"] = [
                    "Fast-Exit Guard kích hoạt — volume spike ở trụ cột",
                    f"trụ nguy hiểm: {', '.join(phan_phoi['tru_nguy_hiem'])}",
                    "hạ tỷ trọng ngay — chờ tín hiệu xác nhận lại",
                ]
                ket_qua["bi_chặn_bởi_bảo_vệ"] = False
                ket_qua["lý_do_chặn"] = None
        except Exception:
            pass

    # ---- Gắn params_hash vào kết quả ----
    anh_chup = locals().get("anh_chup", {})
    ket_qua["params_hash"] = anh_chup.get("params_hash", "unresolved")

    # ---- Audit Trail: log transition từ previous decision ----
    try:
        from src.portfolio.decision_audit import log_transition
        _prev_path = Path(src.config.DATA_DIR) / "output" / "final_decision.json"
        _prev_state = ""
        if _prev_path.exists():
            try:
                _prev = json.loads(_prev_path.read_text(encoding="utf-8"))
                _prev_state = _prev.get("quyet_dinh", "")
            except Exception:
                pass
        _ddi = anh_chup.get("delta_divergence", {})
        _r = anh_chup.get("regime", {})
        log_transition(
            prev_state=_prev_state,
            new_state=ket_qua["quyet_dinh"],
            decision_id=ket_qua.get("decision_id", ket_qua.get("params_hash", "unk")[:8]),
            params_hash=ket_qua["params_hash"],
            confidence=ket_qua.get("độ_tin_cậy_sau_hiệu_chỉnh", {}).get("điểm_số", 0.5),
            delta_sa=_ddi.get("delta_sa", 0.0),
            regime=_r.get("trang_thai", "N/A"),
            trigger="machine",
        )
    except Exception:
        pass

    # ---- Lưu file (atomic write: temp → rename) ----
    out_dir = Path(src.config.DATA_DIR) / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    _tmp = out_dir / "final_decision.json.tmp"
    _dst = out_dir / "final_decision.json"
    with open(_tmp, "w", encoding="utf-8") as f:
        json.dump(ket_qua, f, indent=2, ensure_ascii=False)
    _tmp.replace(_dst)  # atomic rename (NTFS same-volume = atomic)

    return ket_qua


def in_bao_cao(kq: dict):
    icons = {"THAM GIA FULL": "💎", "THAM GIA": "🟢", "THAM GIA DO": "🔵", "QUAN SAT": "🟡", "GIAM RUI RO": "🟠", "DUNG NGOAI": "🔴"}
    icon = icons.get(kq.get("quyet_dinh", ""), "⚪")
    print("\n" + "=" * 60)
    print("  BỘ QUYẾT ĐỊNH CUỐI CÙNG")
    print("=" * 60)
    print(f"  {icon} Quyết định: {kq.get('quyet_dinh', 'N/A')}")
    if kq.get("bi_chặn_bởi_bảo_vệ"):
        print("      ↳ Bị chặn bởi lớp bảo vệ")
    print()
    for i, ld in enumerate(kq.get("ly_do", []), 1):
        print(f"    {i}. {ld}")
    print()
    ct = kq.get("chi_tiet", {})
    print(f"  Cấu trúc:     {ct.get('cau_truc', 'N/A')} ({ct.get('so_tru_cau_truc', '?')}/3 trụ)")
    print(f"  Regime:       {ct.get('regime', 'N/A')}")
    print(f"  Cảnh báo sớm: {ct.get('canh_bao_som', 'N/A')}")
    adx_ct = ct.get('adx')
    da_ct = ct.get('delta_adx')
    if adx_ct is not None:
        adx_str = f"ADX {adx_ct}"
        if da_ct is not None:
            adx_str += f" (Δ{da_ct:+.1f})"
        print(f"  ADX:          {adx_str}")
    print(f"  Entropy:      {ct.get('entropy', 'N/A')}")
    rl = kq.get("recovery_log", [])
    if rl:
        print("  Recovery:")
        for line in rl:
            print(f"    {line}")
    hs = kq.get("healing_status")
    cd = kq.get("chuyen_doi_cau_truc")
    if hs:
        icons_hs = {
            "TAI_PHAT_BENH": "🚨", "DANG_VO": "🔴",
            "BAT_DAU_LANH": "🟠", "DANG_LANH": "🟡", "DA_LANH": "🟢",
        }
        icon_hs = icons_hs.get(hs, "⚪")
        print(f"  Lành:         {icon_hs} {hs} ({cd})")
    rc = kq.get("retest_confirmation")
    if kq.get("quyet_dinh") == "THAM GIA FULL":
        adx_info = kq.get("chi_tiet", {}).get("adx", "N/A")
        delta_info = kq.get("chi_tiet", {}).get("delta_adx")
        delta_str = f" (Δ{delta_info:+.1f})" if delta_info is not None else ""
        print(f"  Consensus:    💎 3/3 trụ + entropy>2.0 + ADX {adx_info}{delta_str}")
        if rc:
            print(f"  Retest:       ✅ {rc}")
    elif rc:
        print(f"  Retest:       ⏳ {rc}")
    fg = kq.get("fast_exit_guard")
    if fg:
        chi_tiet_fg = fg.get("chi_tiet", {})
        print(f"  Fast-Exit:    {fg.get('muc_do', 'N/A')}")
        for ten_tru, st in chi_tiet_fg.items():
            r = st.get("ratio", 0)
            pa = st.get("price_direction", "?")
            if pa == "TANG":
                icon_vol = "🟢"
                pa_desc = "inflow"
            elif pa == "GIAM":
                icon_vol = "🔴"
                pa_desc = "xả"
            else:
                icon_vol = "🟡"
                pa_desc = "trung tính"
            print(f"    {icon_vol} {ten_tru}: vol x{r} MA20 ({pa_desc})")
    # CLI continuous polling: check alert file every render
    try:
        from src.services.macro.interbank_seeder import _is_sbv_alert_active
        if _is_sbv_alert_active():
            print(f"  {'='*50}")
            print("  ⚠ [ACTION REQUIRED]: CẤU TRÚC SBV THAY ĐỔI — CẢM BIẾN MÙ.")
            print("  Dữ liệu gốc lưu tại: data/alerts/")
            print("  Chạy: python ptck.py sbv-update")
            print(f"  {'='*50}")
    except Exception:
        pass

    ss = kq.get("sensor_status")
    if ss == "CRITICAL_SBV_CHANGED":
        print("  ⚠ SENSOR: [CRITICAL CONTROL] BACKEND SENSOR CRASHED — DỪNG NGOÀI DO LỖI CẢM BIẾN, KHÔNG PHẢI TÍN HIỆU THỊ TRƯỜNG")

    đg = kq.get("độ_tin_cậy_sau_hiệu_chỉnh", {})
    if đg:
        icons_dg = {"CAO": "🟢", "TRUNG_BINH": "🟡", "THAP": "🔴"}
        icon_dg = icons_dg.get(đg.get("mức", ""), "⚪")
        print(f"  Tin cậy:      {icon_dg} {đg.get('điểm_số', 0):.0%} ({đg.get('mức', 'N/A')})")
        if đg.get("tạm_ngưng"):
            print(f"  ⚠ Tạm ngưng kết luận: {đg.get('lý_do_tạm_ngưng', '')}")
    print("=" * 60)


if __name__ == "__main__":
    kq = quyet_dinh_cuoi()
    in_bao_cao(kq)
