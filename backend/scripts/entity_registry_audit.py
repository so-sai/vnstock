"""entity_registry_audit.py — Batch Audit chuẩn hóa entity_registry theo ICB Sector.

Kiến trúc 2 tầng (2026-08-07):
  TẦNG 1 — Khung Kế toán (entity_type): 4 nhóm BCTC VAS khác biệt căn bản
      BANK       : Ngân hàng (NII, TOI, NPL, NIM, CAR, Dư nợ/Tiền gửi).
      SECURITIES : Công ty Chứng khoán (FVTPL, HTM, AFS, Margin, Doanh thu môi giới).
      INSURANCE  : Công ty Bảo hiểm (Phí bảo hiểm gốc, Dự phòng nghiệp vụ).
      STANDARD   : Tất cả các ngành còn lại (Thép, BĐS, Bán lẻ, CNTT, Dược...).
  TẦNG 2 — Tham số Ngành (ICB Policy Rules): ngưỡng lọc đặc thù trong nhóm
      STANDARD, đọc từ icb_name (vd: T1_GROSS_MARGIN_MIN_STEEL = 0.15 cho Thép).
      Không làm thay đổi cấu trúc BCTC.

Script này quét bảng symbol_industry (screener_cache.db), quy đổi ICB sector →
entity_type theo quy tắc trên, và cập nhật entity_registry (financial_facts.db)
bằng INSERT OR REPLACE. Idempotent: chạy lại không đúp dòng.

LƯU Ý: SECURITIES/INSURANCE hiện chưa có khung tính riêng trong
company_health_engine/vn20 (chỉ BANK vs STANDARD). Gán nhãn này là chuẩn hóa
metadata cho tương lai — engine vẫn xử lý chúng như STANDARD cho đến khi có
khung kế toán riêng (không phá vỡ luồng hiện tại).

Chạy:  python -m scripts.entity_registry_audit          (dry-run mặc định)
       python -m scripts.entity_registry_audit --apply  (ghi vào registry)
"""

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = PROJECT_ROOT / "backend"
DATA_DIR = BACKEND_DIR / "data"
SCREENER_DB = DATA_DIR / "screener_cache.db"
FINANCIAL_DB = DATA_DIR / "financial_facts.db"

# ── Quy tắc quy đổi ICB → entity_type (ưu tiên BANK > SECURITIES > INSURANCE) ──
# LƯU Ý: chỉ khớp "chứng khoán" (không khớp "môi giới" đơn lẻ — tránh lẫn
# "Môi giới Bất động sản" như BVL/DCH/DXS thuộc BĐS, không phải chứng khoán).
_RULES: tuple = (
    ("BANK", ("ngân hàng",)),
    ("SECURITIES", ("chứng khoán",)),
    ("INSURANCE", ("bảo hiểm",)),
)


def map_icb_to_entity_type(icb_name2: str = "", icb_name3: str = "", icb_name4: str = "") -> str:
    """Quy đổi ICB sector name → khung kế toán (entity_type).

    Khớp keyword trên toàn bộ chuỗi ICB (cấp 2/3/4), ưu tiên BANK trước.
    Mặc định STANDARD cho mọi ngành phi tài chính (Thép, BĐS, Bán lẻ, CNTT...).
    """
    joined = " ".join(x.lower() for x in (icb_name2 or "", icb_name3 or "", icb_name4 or ""))
    if not joined:
        return "STANDARD"
    for entity_type, keywords in _RULES:
        if any(k in joined for k in keywords):
            return entity_type
    return "STANDARD"


@dataclass
class AuditResult:
    total: int = 0
    updated: dict = field(default_factory=dict)  # entity_type -> list[symbol]
    unchanged: int = 0
    skipped: int = 0


def audit_registry(apply: bool = False) -> AuditResult:
    sc = sqlite3.connect(str(SCREENER_DB))
    sc.row_factory = sqlite3.Row
    fin = sqlite3.connect(str(FINANCIAL_DB))
    fin.row_factory = sqlite3.Row

    rows = sc.execute("SELECT symbol, icb_name2, icb_name3, icb_name4 FROM symbol_industry ORDER BY symbol").fetchall()

    existing = {
        r["symbol"]: r["entity_type"] for r in fin.execute("SELECT symbol, entity_type FROM entity_registry").fetchall()
    }

    result = AuditResult(total=len(rows))
    for r in rows:
        symbol = r["symbol"].upper()
        target = map_icb_to_entity_type(r["icb_name2"], r["icb_name3"], r["icb_name4"])
        current = existing.get(symbol)
        if current == target:
            result.unchanged += 1
            continue
        result.updated.setdefault(target, []).append(symbol)
        if apply:
            fin.execute(
                """
                INSERT OR REPLACE INTO entity_registry (symbol, entity_type, full_name, updated_at)
                VALUES (?, ?, NULL, datetime('now'))
                """,
                (symbol, target),
            )
    fin.commit()
    fin.close()
    sc.close()
    return result


def _fmt(symbols):
    return ", ".join(symbols[:15]) + ("..." if len(symbols) > 15 else "")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Batch Audit entity_registry theo ICB")
    parser.add_argument("--apply", action="store_true", help="Ghi vào registry (mặc định dry-run)")
    args = parser.parse_args()

    result = audit_registry(apply=args.apply)
    mode = "APPLIED" if args.apply else "DRY-RUN"
    print(f"[entity_registry_audit] {mode} — total symbols: {result.total}")
    print(f"  unchanged: {result.unchanged}")
    for entity_type, symbols in sorted(result.updated.items()):
        print(f"  -> {entity_type}: {len(symbols)} symbols  [{_fmt(symbols)}]")
    if not args.apply:
        print("  (dry-run: dùng --apply để ghi registry)")


if __name__ == "__main__":
    main()
