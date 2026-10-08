"""commodity_exposure.py — Registry GIẢ THUYẾT phơi nhiễm hàng hoá (Tiên quyết F).

Spec: docs/SPEC_DUAL_TRACK_DECISION_2026-10.md — mục 3 và Addendum A.

NGUYÊN TẮC CỐT TỬ
------------------
`prior_sign` ở đây là **GIẢ THUYẾT KHAI BÁO TRƯỚC (priori)**, KHÔNG phải kết quả đo.
Nó được khai báo cùng `rationale_source` TRƯỚC khi chạy bất kỳ phép đo nào, để bộ đo
(`commodity_hit_rate.calculate_non_overlapping_hit_rate`) có thể **PHẢN BÁC** nó.

Quy tắc vàng (chống P-hacking):
    Giả thuyết tiên nghiệm KHÔNG khớp dữ liệu  ⇒  LOẠI MÃ khỏi universe.
    TUYỆT ĐỐI KHÔNG lật dấu để uốn theo dữ liệu quá khứ.

Vì sao phải tách "khai báo" khỏi "đo": nếu lấy dấu từ chính dữ liệu rồi lại dùng dữ
liệu đó để gate, cổng trở thành vòng tròn (đúng lỗi của `confidence` tĩnh trong
`causal_edge.py` mà spec đã bác).

Nguồn ICB: bảng `symbol_industry` trong screener_cache.db (đã kiểm chứng 2026-10-08).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DB_PATH = PROJECT_ROOT / "backend" / "data" / "screener_cache.db"

# Chuỗi vĩ mô có thật trong macro_history (kiểm 2026-10-08: 2021-04-05 → 2026-10-06)
AVAILABLE_DRIVERS = {"BRENT_OIL", "WTI_OIL"}


@dataclass(frozen=True)
class CommodityExposure:
    """Một dòng giả thuyết phơi nhiễm hàng hoá của một mã."""

    symbol: str
    driver: str
    prior_sign: int  # +1 / -1 (KHÔNG dùng 0 — mã không rõ dấu thì không vào registry)
    lag_weeks: tuple[int, int]
    rationale_source: str
    icb_path: str
    tier: str  # "U1" đã validate archetype · "U2" ICB-validated chờ kiểm chứng
    prior_confidence: str = "MEDIUM"  # LOW ⇒ ngưỡng hit-rate cao hơn, không dùng ở P1
    notes: str = ""

    @property
    def driver_available(self) -> bool:
        return self.driver in AVAILABLE_DRIVERS

    @property
    def lag_mid_k(self) -> int:
        """Lag trung bình (TUẦN) — chỉ để hiển thị."""
        return (self.lag_weeks[0] + self.lag_weeks[1]) // 2

    @property
    def lag_mid_sessions(self) -> int:
        """Lag trung bình quy ra PHIÊN (1 tuần ≈ 5 phiên giao dịch).

        ⚠️ Sửa lỗi: bản đầu truyền thẳng lag_mid_k (tuần) vào bộ đo theo PHIÊN
        ⇒ lệch đơn vị 5×, làm lag ngắn hơn thực tế cả tháng ở các cạnh dài.
        """
        return max(1, self.lag_mid_k * 5)


# ─────────────────────────────────────────────────────────────────────
# REGISTRY — mỗi dòng phải có rationale_source + ICB. Thiếu ⇒ không vào.
# ─────────────────────────────────────────────────────────────────────
COMMODITY_EXPOSURE_REGISTRY: dict[str, CommodityExposure] = {
    # ── U1: archetype đã human-validated (BASELINE_MAP) ──
    "BSR": CommodityExposure(
        symbol="BSR",
        driver="BRENT_OIL",
        prior_sign=+1,
        lag_weeks=(2, 6),
        rationale_source="Nhà máy lọc dầu Bình Sơn — biên phụ thuộc CRACK SPREAD (chênh giá sản phẩm vs dầu thô). "
        "Dầu thô tăng kéo giá xăng/dầu thành phẩm tăng theo, thường đồng pha trong 2–6 tuần.",
        icb_path="Dầu khí / Sản xuất Dầu khí / Sản xuất và Khai thác dầu khí",
        tier="U1",
        prior_confidence="MEDIUM",
    ),
    "GAS": CommodityExposure(
        symbol="GAS",
        driver="BRENT_OIL",
        prior_sign=+1,
        lag_weeks=(4, 9),
        rationale_source="PV GAS — giá khí bán theo hợp đồng có chiết khấu gắn với dầu (LNG/gas link). "
        "Đồ thị đã có cạnh `UTILITY:OIL→GAS_PRICE` (Brent → 2–9 tuần → giá khí hợp đồng).",
        icb_path="Điện, nước & xăng dầu khí đốt / Nước & Khí đốt / Phân phối xăng dầu & khí đốt",
        tier="U1",
        prior_confidence="MEDIUM",
    ),
    # ── U2: ICB-validated, chờ bộ đo kiểm chứng ──
    "PVD": CommodityExposure(
        symbol="PVD",
        driver="BRENT_OIL",
        prior_sign=+1,
        lag_weeks=(2, 8),
        rationale_source="PV Drilling — cho thuê giàn khoan; giá thuê (day rate) đồng pha với chu kỳ capex dầu khí, "
        "bản thân chu kỳ này bám giá dầu với độ trễ vài tháng.",
        icb_path="Dầu khí / Thiết bị, Dịch vụ và Phân phối Dầu khí / Thiết bị và Dịch vụ Dầu khí",
        tier="U2",
        prior_confidence="MEDIUM",
    ),
    "PVS": CommodityExposure(
        symbol="PVS",
        driver="BRENT_OIL",
        prior_sign=+1,
        lag_weeks=(2, 8),
        rationale_source="PTSC — dịch vụ kỹ thuật & lắp đặt công trình biển; khối lượng công việc "
        "gắn chu kỳ đầu tư thượng nguồn, đồng pha giá dầu với độ trễ.",
        icb_path="Dầu khí / Thiết bị, Dịch vụ và Phân phối Dầu khí / Thiết bị và Dịch vụ Dầu khí",
        tier="U2",
        prior_confidence="MEDIUM",
    ),
    "PLX": CommodityExposure(
        symbol="PLX",
        driver="BRENT_OIL",
        prior_sign=+1,
        lag_weeks=(0, 3),
        rationale_source="Petrolimex — ICB xếp 'Sản xuất Dầu khí' nhưng thực tế là NHÀ PHÂN PHỐI (mua–bán lại). "
        "Dấu KHÔNG hiển nhiên: giá nhập tăng ngay trong khi giá bán lẻ trong nước điều hành theo chu kỳ liên Bộ "
        "+ trích lập quỹ BOG ⇒ biên gộp có thể BỊ NÉN ngắn hạn (phơi nhiễm chi phí) hoặc mở rộng (tồn kho giá thấp). "
        "Giữ +1 theo ICB nhưng đánh dấu LOW — bộ đo PHẢI xác nhận trước khi dùng.",
        icb_path="Dầu khí / Sản xuất Dầu khí / Sản xuất và Khai thác dầu khí",
        tier="U2",
        prior_confidence="LOW",
        notes="Dấu tranh chấp: nếu hit-rate đo được ngược dấu ⇒ LOẠI, không lật dấu.",
    ),
    "PVT": CommodityExposure(
        symbol="PVT",
        driver="BRENT_OIL",
        prior_sign=-1,
        lag_weeks=(0, 4),
        rationale_source="PV Trans — ICB 'Vận tải Thủy'. Dầu là NHIÊN LIỆU (chi phí), không phải doanh thu. "
        "Đồ thị đã có cạnh chi phí `LOGI:OIL_PRICE→OPERATING_EXPENSE`. Giá dầu tăng ⇒ bào mòn biên.",
        icb_path="Hàng & Dịch vụ Công nghiệp / Vận tải / Vận tải Thủy",
        tier="U2",
        prior_confidence="HIGH",
        notes="Ca duy nhất KHÔNG thể tranh cãi về dấu — dùng làm contract test.",
    ),
    # ── Loại vì THIẾU CHUỖI DỮ LIỆU (ghi lại để không bị lãng quên) ──
    "MSR": CommodityExposure(
        symbol="MSR",
        driver="METALS",
        prior_sign=+1,
        lag_weeks=(2, 8),
        rationale_source="Masan Resources — ICB 'Tài nguyên Cơ bản / Khai khoáng'. Doanh thu là giá KIM LOẠI (vonfram/than), "
        "KHÔNG phải dầu ⇒ không dùng BRENT làm driver.",
        icb_path="Tài nguyên Cơ bản / Khai khoáng / Khai khoáng",
        tier="EXCLUDED",
        prior_confidence="LOW",
        notes="Không có chuỗi giá kim loại trong macro_history ⇒ không đo được ⇒ LOẠI (fail-closed).",
    ),
}


# ─────────────────────────────────────────────────────────────────────
# Truy vấn
# ─────────────────────────────────────────────────────────────────────
def get(symbol: str) -> CommodityExposure | None:
    return COMMODITY_EXPOSURE_REGISTRY.get(symbol.upper())


def active_universe(tier: str | None = None) -> list[CommodityExposure]:
    """Các mã ĐỦ ĐIỀU KIỆN ĐO: driver có chuỗi thật + không thuộc tier EXCLUDED."""
    out = [e for e in COMMODITY_EXPOSURE_REGISTRY.values() if e.driver_available and e.tier != "EXCLUDED"]
    if tier:
        out = [e for e in out if e.tier == tier]
    return sorted(out, key=lambda e: e.symbol)


def excluded() -> list[tuple[str, str]]:
    """Danh sách (symbol, lý do loại) — để audit không bị mất dấu."""
    return [
        (e.symbol, e.notes or f"driver '{e.driver}' không có chuỗi dữ liệu")
        for e in COMMODITY_EXPOSURE_REGISTRY.values()
        if not e.driver_available or e.tier == "EXCLUDED"
    ]


def hit_rate_threshold(exp: CommodityExposure) -> float:
    """Ngưỡng kích hoạt theo độ tin cậy của tiên nghiệm.

    Tiên nghiệm YẾU phải chứng minh mạnh hơn mới được dùng — chống việc lấy
    một giả thuyết mơ hồ rồi hạ chuẩn cho nó qua cổng.
    """
    return {"HIGH": 0.60, "MEDIUM": 0.60, "LOW": 0.65}.get(exp.prior_confidence, 0.65)


# ─────────────────────────────────────────────────────────────────────
# Self-check: ICB khai báo có khớp bảng symbol_industry không?
# ─────────────────────────────────────────────────────────────────────
def verify_icb_against_db(db_path: Path | str = DB_PATH) -> list[tuple[str, str]]:
    """Đối chiếu `icb_path` khai báo với DB. Trả về danh sách (symbol, mô tả lệch)."""
    mismatches: list[tuple[str, str]] = []
    try:
        conn = sqlite3.connect(str(db_path))
    except sqlite3.Error as e:
        return [("*", f"không mở được DB: {e}")]
    try:
        for e in COMMODITY_EXPOSURE_REGISTRY.values():
            row = conn.execute(
                "SELECT icb_name2, icb_name3, icb_name4 FROM symbol_industry WHERE symbol = ?",
                (e.symbol,),
            ).fetchone()
            if row is None:
                mismatches.append((e.symbol, "không có trong symbol_industry"))
                continue
            actual = " / ".join(str(x) for x in row if x)
            if actual != e.icb_path:
                mismatches.append((e.symbol, f"DB='{actual}' ≠ khai báo='{e.icb_path}'"))
    finally:
        conn.close()
    return mismatches


__all__ = [
    "CommodityExposure",
    "COMMODITY_EXPOSURE_REGISTRY",
    "AVAILABLE_DRIVERS",
    "get",
    "active_universe",
    "excluded",
    "hit_rate_threshold",
    "verify_icb_against_db",
]
