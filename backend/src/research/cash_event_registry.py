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
TICK_SIZES: dict[str, float] = {
    "FOC": 100.0,
    "VEA": 100.0,
    "SMB": 50.0,  # HOSE mid-cap — giá 30-40k, lưới 50 VND
}

# [WHY] Biên trần/sàn luật định theo sàn (NQ-07): HOSE ±7%, UPCoM ±15%. Đây là
# hằng số QUY CHẾ, không phải tham số nghiên cứu: sửa/nới ⇒ tầng nghiệm thu
# trần/sàn mất giá trị pháp lý và không còn chặn được cổ tức khai sai lớn.
DAILY_LIMIT_PCT: dict[str, float] = {
    "FOC": 0.15,  # UPCoM
    "VEA": 0.07,  # HOSE
    "SMB": 0.07,  # HOSE
}

# [WHY] Tầng trần/sàn YẾU hơn band OHLC ~5-10× (không phân biệt được cổ tức sai
# ≤7% với biến động thị trường). Vì vậy nó chỉ được bật cho TỪNG event đã khai
# báo + có căn cứ vi cấu trúc, KHÔNG bật theo symbol. Bật cả symbol ⇒ một mã
# mất toàn bộ khả năng phát hiện cổ tức thiếu.
# SMB 2023-02-20: cả phiên chỉ khớp 2 bước giá 41.450/41.500 (biên 50 đ) trong
# khi P_ref = 42.150 − 1.000 = 41.150 ⇒ phiên gap-up, P_ref nằm dưới low_ex
# 300 đ (+0,73% ≤ ±7%). VSDC/SMB/20230220/CASH_1K xác nhận 1.000 đ/cp.
LIMIT_BAND_EVENTS: dict[str, dict[date, str]] = {
    "SMB": {
        date(2023, 2, 20): (
            "gap-up ATO: 2 bước giá 41.450/41.500, P_ref=41.150 ⇒ Δ_market +350 đ "
            "(+0,85%) trong ±7% HOSE; VSDC/SMB/20230220/CASH_1K"
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

CASH_EVENTS: dict[str, tuple[CashEvent, ...]] = {
    "FOC": FOC_EVENTS,
    "VEA": VEA_EVENTS,
    "SMB": SMB_EVENTS,
}
