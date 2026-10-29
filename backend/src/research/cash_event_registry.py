"""
CASH EVENT REGISTRY — DANH SÁCH CỔ TỨC TIỀN MẶT ĐÃ XÁC MINH (PHA 2B)
PTCK_VNSTOCK Quantitative Research Suite

[WHY] Trước module này, danh sách event FOC/VEA/SMB chỉ tồn tại trong runner
calibration (`run_raw_price_reconstruction.py`). Khi cổng provenance mở chế
độ `--reconstructed`, HAI nơi (runner + gate) cần CÙNG một nguồn event — nếu
khai báo trùng lặp, một chỗ sửa mà chỗ kia không ⇒ gate certify bằng danh
sách cũ, mất giá trị fail-closed. Registry này là nguồn duy nhất
(single source of truth), có provenance ghi rõ nguồn xác minh.

[ROOT CAUSE] Pha 1 (commit 15e09c8) chạy trên chuỗi adjusted ⇒ cổ tức bị đếm
hai lần. Muốn phục nguyên (Pha 2b) phải có danh sách ngày GDKHQ + tiền cổ tức
THẬT, không suy ra từ chính chuỗi giá (suy ra từ data rồi "chứng nhận" bằng
data là lập luận vòng tròn).

[INVARIANT]
(1) Mọi event PHẢI có provenance bên ngoài data giá (VSDC/Nghị quyết
    ĐHĐCĐ/công bố chính thức) — ghi trong docstring từng symbol.
(2) `ex_date` là NGÀY GIAO DỊCH KHÔNG HƯỞNG QUYỀN (bar đầu tiên rơi giá),
    KHÔNG phải ngày chốt danh sách. Với FOC 2021, chốt 14/4 nhưng GDKHQ
    13/4 (xem biên bản E10) — lệch 1 bar là điều tra sai toàn bộ segment.
(3) Không tự thêm event khi chưa xác minh: danh sách thiếu ⇒ gate báo
    Δ_market bất thường (fail-closed), không bao giờ "đoán" cho khớp.
"""

from __future__ import annotations

from datetime import date

# [WHY] Hai đường import (repo-root `backend.src.research.*` và PYTHONPATH
# `backend/src`): runner audit cũ và pytest dùng cả hai; hard-code một đường
# sẽ làm gãy đường còn lại — đã đo được ModuleNotFoundError khi thêm import
# tuyệt đối `src.research.*` (28/10/2026).
try:
    from src.research.raw_price_reconstruction import CashEvent
except ImportError:  # pragma: no cover - chỉ xảy ra khi import bằng `backend.`
    from backend.src.research.raw_price_reconstruction import CashEvent

# [WHY]: Lưới tick theo sàn — UPCoM/HOSE bluechip 100 VND, HOSE mid-cap
# (10k–50k) 50 VND, HNX/UPCoM 100 VND. Dùng sai lưới ⇒ solve factor ra
# nghiệm vô nghĩa (mọi bar "lệch grid" hoặc family f×2 lọt vào).
# DSN 50 VND: đo 29/10/2026 trên 1.471 bar KBS — tick 50 cho off_grid=0,
# tick 100 làm gãy 64/115 bar ở segment cuối ⇒ 100 là SAI lưới cho DSN.
TICK_SIZES: dict[str, float] = {
    "FOC": 100.0,
    "VEA": 100.0,
    "SMB": 50.0,  # HOSE mid-cap — giá 30-40k, lưới 50 VND
    "PAT": 100.0,  # UPCoM — đo 1.072 bar, off_grid=0
    "DSN": 50.0,  # HOSE mid-cap — giá 34-62k, lưới 50 VND
}

# [WHY] Biên trần/sàn luật định theo sàn (NQ-07): HOSE ±7%, UPCoM ±15%. Đây là
# hằng số QUY CHẾ, không phải tham số nghiên cứu: sửa/nới ⇒ tầng nghiệm thu
# trần/sàn mất giá trị pháp lý và không còn chặn được cổ tức khai sai lớn.
DAILY_LIMIT_PCT: dict[str, float] = {
    "FOC": 0.15,  # UPCoM
    "VEA": 0.07,  # HOSE
    "SMB": 0.07,  # HOSE
    "PAT": 0.15,  # UPCoM
    "DSN": 0.07,  # HOSE
}

# [WHY] Tầng trần/sàn YẾU hơn band OHLC ~5-10× (không phân biệt được cổ tức sai
# ≤7% với biến động thị trường). Vì vậy nó chỉ được bật cho TỪNG event đã khai
# báo + có căn cứ vi cấu trúc, KHÔNG bật theo symbol. Bật cả symbol ⇒ một mã
# mất toàn bộ khả năng phát hiện cổ tức thiếu.
# SMB 2023-02-20: cả phiên chỉ khớp 2 bước giá 41.450/41.500 (biên 50 đ) trong
# khi P_ref = 42.150 − 1.000 = 41.150 ⇒ phiên gap-up, P_ref nằm dưới low_ex
# 300 đ (+0,73% ≤ ±7%). VSDC/SMB/20230220/CASH_1K xác nhận 1.000 đ/cp.
#
# PAT (UPCoM, đo 29/10/2026 trên vintage KBS 1.072 bar): 4/9 event là phiên
# GAP — cả dải [low_ex, high_ex] nằm một phía P_ref nên band OHLC không dùng
# được. CẢ 4 event đều có xác nhận độc lập từ tầng 3 (tỷ số KBS/VCI):
#   2022-08-04: P_pre 148.000, ref 138.000, band [139.499, 149.900], Δ −2.000
#               (−1,45%) ⇒ giá mở cửa tăng ngay trên ref. Tầng 3: agree.
#   2024-04-12: P_pre 98.000, ref 97.000, band [97.600, 98.100], Δ −600
#               (−0,62%). Tầng 3: agree.
#   2025-04-24: P_pre 90.000, ref 87.500, band [88.200, 91.000], Δ −2.000
#               (−2,29%). Tầng 3: agree.
#   2026-09-15: P_pre 70.000, ref 67.100, band [66.500, 66.800], Δ +300
#               (+0,45%) — phiên gap-down. Tầng 3: vendor-lag, bước tỷ số
#               +4,361% quy ra 2.925 đ/cp vs khai 2.900 (lệch 0,87%).
# DSN (HOSE): 2026-05-18: P_pre 38.450, ref 36.850, band [37.150, 37.800],
#               Δ −650 (−1,76%) ≤ ±7%. Tầng 3: vendor-lag, bước +4,342% quy ra
#               1.600 đ/cp khớp tuyệt đối cash khai báo.
# Xóa một dòng ⇒ event quay lại fail-closed (đúng ý đồ): allow-list là NGHIỆM
# THU có bằng chứng, không phải công tắc tắt kiểm tra.
LIMIT_BAND_EVENTS: dict[str, dict[date, str]] = {
    "SMB": {
        date(2023, 2, 20): (
            "gap-up ATO: 2 bước giá 41.450/41.500, P_ref=41.150 ⇒ Δ_market +350 đ "
            "(+0,85%) trong ±7% HOSE; VSDC/SMB/20230220/CASH_1K"
        )
    },
    "PAT": {
        date(2022, 8, 4): (
            "gap-up: ref 138.000 dưới low_ex 139.499 ⇒ Δ_market −2.000 (−1,45%) "
            "trong ±15% UPCoM; tầng 3 cross-vendor agree (bước tỷ số −0,001%)"
        ),
        date(2024, 4, 12): (
            "gap-up: ref 97.000 dưới low_ex 97.600 ⇒ Δ_market −600 (−0,62%) "
            "trong ±15% UPCoM; tầng 3 cross-vendor agree (+0,003%)"
        ),
        date(2025, 4, 24): (
            "gap-up: ref 87.500 dưới low_ex 88.200 ⇒ Δ_market −2.000 (−2,29%) "
            "trong ±15% UPCoM; tầng 3 cross-vendor agree (−0,001%)"
        ),
        date(2026, 9, 15): (
            "gap-down: ref 67.100 trên high_ex 66.800 ⇒ Δ_market +300 (+0,45%) "
            "trong ±15% UPCoM; tầng 3 vendor-lag, implied 2.925 vs cash 2.900"
        ),
    },
    "DSN": {
        date(2026, 5, 18): (
            "gap-up: ref 36.850 dưới low_ex 37.150 ⇒ Δ_market −650 (−1,76%) "
            "trong ±7% HOSE; tầng 3 vendor-lag, implied 1.600 khớp cash khai báo"
        )
    },
}

# [WHY] FPT Online (UPCoM). Nguồn: VSDC + nghị quyết ĐHĐCĐ + báo chí, đã đối
# chiếu 3 nguồn trong biên bản P2 (E8/E10). 2021: chốt danh sách 14/4/2021,
# GDKHQ thực tế 13/4/2021 (drop 19,801 ≈ 20,000 VND — E10). 2022: tỷ lệ 50%
# (8,000đ). 2023: 5,000đ (chốt 9/6, trả 19/6). 2025/2026: 100% (10,000đ,
# 184 tỷ / 18.4 triệu cp). Xóa/sửa một dòng ⇒ toàn bộ segment trước đó sai
# factor và gate sẽ certify một chuỗi giá không có thật.
FOC_EVENTS: tuple[CashEvent, ...] = (
    CashEvent(ex_date=date(2021, 4, 13), cash=20_000.0),
    CashEvent(ex_date=date(2022, 6, 10), cash=8_000.0),
    CashEvent(ex_date=date(2023, 6, 8), cash=5_000.0),
    CashEvent(ex_date=date(2024, 7, 18), cash=2_000.0),
    CashEvent(ex_date=date(2025, 5, 23), cash=10_000.0),
    CashEvent(ex_date=date(2026, 8, 10), cash=10_000.0),
)

# [WHY] VEA (Tổng công ty Máy động lực & Máy nông nghiệp, HOSE): cổ tức tiền
# mặt đều đặn ~4,000-5,000đ/năm cộng đợt 462.7đ (2021). Đây là 7 event đã
# xác minh VSDC (Pha 2). Sai một event ⇒ survey breakpoints lệch ~1 quý.
VEA_EVENTS: tuple[CashEvent, ...] = (
    CashEvent(ex_date=date(2021, 1, 4), cash=5_252.9),
    CashEvent(ex_date=date(2021, 7, 15), cash=4_990.0),
    CashEvent(ex_date=date(2021, 10, 12), cash=462.7),
    CashEvent(ex_date=date(2022, 10, 28), cash=4_493.7),
    CashEvent(ex_date=date(2023, 11, 17), cash=4_186.9),
    CashEvent(ex_date=date(2024, 11, 19), cash=5_035.18),
    CashEvent(ex_date=date(2025, 11, 18), cash=4_658.08),
)

# [WHY] SMB (Sài Gòn - Mỹ Tho, HOSE mid-cap, lưới 50 VND): 13 đợt cổ tức tiền
# mặt 1,000-2,500đ từ 2020 — đây là ca kiểm định lưới tick KHÁC 100 VND của
# engine (Pha 3). Thiếu event ⇒ factor sai 1 bậc, toàn bộ 50 VND grid FAIL.
SMB_EVENTS: tuple[CashEvent, ...] = (
    CashEvent(ex_date=date(2020, 12, 22), cash=1_000.0),
    CashEvent(ex_date=date(2021, 10, 22), cash=2_500.0),
    CashEvent(ex_date=date(2022, 2, 18), cash=1_000.0),
    CashEvent(ex_date=date(2022, 9, 20), cash=2_500.0),
    CashEvent(ex_date=date(2023, 2, 20), cash=1_000.0),
    CashEvent(ex_date=date(2023, 7, 17), cash=2_500.0),
    CashEvent(ex_date=date(2023, 10, 24), cash=1_000.0),
    CashEvent(ex_date=date(2024, 5, 22), cash=1_500.0),
    CashEvent(ex_date=date(2024, 9, 16), cash=2_000.0),
    CashEvent(ex_date=date(2025, 1, 7), cash=1_500.0),
    CashEvent(ex_date=date(2025, 8, 13), cash=2_000.0),
    CashEvent(ex_date=date(2025, 11, 12), cash=2_000.0),
    CashEvent(ex_date=date(2026, 5, 25), cash=1_000.0),
)

# [WHY] PAT (CTCP Phốt pho Vàng Việt Trì, UPCoM, niêm yết 06/2022) — "siêu cổ
# tức chu kỳ hàng hóa": chi 10.000-10.655 đ/cp mỗi đợt trong 2022-2023 rồi hạ
# dần khi giá phốt pho vàng hạ nhiệt. Nguồn: VCI IQ events API
# (`iq-insight-service/v1/events`, eventCode=DIV) — 11 sự kiện DIV, đối chiếu
# 29/10/2026. CHỈ lấy 9 event có ex_date nằm TRONG chuỗi bar (bar đầu
# 2022-06-17): 2021-12-06 (2.000 đ) và 2022-04-19 (7.600 đ) rơi TRƯỚC bar đầu
# nên không dùng để phục nguyên — thêm vào sẽ làm `idx_of[ex_date]` KeyError.
# Sai một dòng ⇒ factor segment sai bậc ⇒ toàn bộ 1.072 bar lệch lưới 100 VND.
PAT_EVENTS: tuple[CashEvent, ...] = (
    CashEvent(ex_date=date(2022, 8, 4), cash=10_000.0),
    CashEvent(ex_date=date(2022, 12, 19), cash=10_000.0),
    CashEvent(ex_date=date(2023, 4, 7), cash=10_655.0),
    CashEvent(ex_date=date(2023, 12, 19), cash=9_000.0),
    CashEvent(ex_date=date(2024, 4, 12), cash=1_000.0),
    CashEvent(ex_date=date(2024, 11, 19), cash=7_000.0),
    CashEvent(ex_date=date(2025, 4, 24), cash=2_500.0),
    CashEvent(ex_date=date(2025, 12, 24), cash=10_000.0),
    CashEvent(ex_date=date(2026, 9, 15), cash=2_900.0),
)

# [WHY] DSN (CTCP Công viên nước Đầm Sen, HOSE) — "bò sữa tiền mặt thuần túy":
# 2.400-2.500 đ/cp/năm (đợt chính) + 1.500-1.600 đ (đợt giữa năm) suốt 2021-2026.
# Nguồn: VCI IQ events API (eventCode=DIV), 10 sự kiện, đối chiếu 29/10/2026.
# Đây là ca kiểm định lưới tick 50 VND trên HOSE (SMB đã xác nhận 50 VND; DSN
# xác nhận lần hai, độc lập về cấu trúc giá 34-62k). Sửa cash ⇒ factor lệch ⇒
# 1.471 bar rơi khỏi lưới 50.
DSN_EVENTS: tuple[CashEvent, ...] = (
    CashEvent(ex_date=date(2021, 2, 26), cash=2_500.0),
    CashEvent(ex_date=date(2021, 12, 28), cash=1_500.0),
    CashEvent(ex_date=date(2022, 11, 15), cash=1_500.0),
    CashEvent(ex_date=date(2023, 4, 18), cash=2_500.0),
    CashEvent(ex_date=date(2023, 12, 28), cash=2_400.0),
    CashEvent(ex_date=date(2024, 3, 14), cash=1_600.0),
    CashEvent(ex_date=date(2024, 12, 26), cash=2_400.0),
    CashEvent(ex_date=date(2025, 3, 20), cash=1_600.0),
    CashEvent(ex_date=date(2026, 1, 13), cash=2_400.0),
    CashEvent(ex_date=date(2026, 5, 18), cash=1_600.0),
)

# [WHY] Sàn niêm yết là DỮ LIỆU PROVENANCE, không phải nhãn trang trí: nó quyết
# định biên trần/sàn (UPCoM ±15%, HOSE ±7%) và bảng so sánh 5 mã. Trước đây nhãn
# này sống inline trong `cli_dividend_service.py` và đã in SAI "VEA — UPCoM" (VEA
# là HOSE, đo 29/10/2026) ⇒ người đọc báo cáo hiểu sai cả cơ chế giá lẫn biên
# luật định. Gom về registry để chỉ còn MỘT nguồn.
EXCHANGES: dict[str, str] = {
    "FOC": "UPCoM",
    "VEA": "HOSE",
    "SMB": "HOSE",
    "PAT": "UPCoM",
    "DSN": "HOSE",
}

CASH_EVENTS: dict[str, tuple[CashEvent, ...]] = {
    "FOC": FOC_EVENTS,
    "VEA": VEA_EVENTS,
    "SMB": SMB_EVENTS,
    "PAT": PAT_EVENTS,
    "DSN": DSN_EVENTS,
}
