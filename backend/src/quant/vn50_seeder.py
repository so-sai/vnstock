"""vn50_seeder.py — VN50 Database Expansion (STANDARD + BANK blue chips).

Crawls 30 quarters of financial statements for the 50 largest/liquid VN
stocks into financial_facts.db, computes health ratios, then reports.

Pipeline reuses existing infra (CafeFCrawler + HealthEngine) — no new crawler.

Run:  python backend/src/quant/vn50_seeder.py [--dry-run] [--delay 3.0]
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.financial.cafef_crawler import CafeFCrawler
from src.financial.company_health_engine import HealthEngine
from src.financial.financial_facts import FinancialFactsDB

# ── VN50 blue-chip universe (curated, entity type correct) ────────────
VN50 = [
    # Banks (BANK)
    ("VCB", "BANK"),
    ("BID", "BANK"),
    ("CTG", "BANK"),
    ("TCB", "BANK"),
    ("ACB", "BANK"),
    ("MBB", "BANK"),
    ("STB", "BANK"),
    ("VPB", "BANK"),
    ("HDB", "BANK"),
    ("LPB", "BANK"),
    ("VIB", "BANK"),
    ("SHB", "BANK"),
    ("TPB", "BANK"),
    ("MSB", "BANK"),
    ("EIB", "BANK"),
    ("OCB", "BANK"),
    # Large caps / conglomerates (STANDARD)
    ("FPT", "STANDARD"),
    ("VIC", "STANDARD"),
    ("VHM", "STANDARD"),
    ("VNM", "STANDARD"),
    ("SAB", "STANDARD"),
    ("MSN", "STANDARD"),
    ("GAS", "STANDARD"),
    ("PLX", "STANDARD"),
    ("POW", "STANDARD"),
    ("PVD", "STANDARD"),
    ("BSR", "STANDARD"),
    ("GVR", "STANDARD"),
    ("DCM", "STANDARD"),
    ("DPM", "STANDARD"),
    ("DGC", "STANDARD"),
    # Steel / materials
    ("HPG", "STANDARD"),
    ("HSG", "STANDARD"),
    ("NKG", "STANDARD"),
    ("GMD", "STANDARD"),
    ("VSC", "STANDARD"),
    ("HAH", "STANDARD"),
    # Retail / consumer
    ("MWG", "STANDARD"),
    ("PNJ", "STANDARD"),
    ("FRT", "STANDARD"),
    ("DGW", "STANDARD"),
    ("VJC", "STANDARD"),
    ("HVN", "STANDARD"),
    # Securities / finance
    ("SSI", "STANDARD"),
    ("VCI", "STANDARD"),
    ("VND", "STANDARD"),
    ("BSI", "STANDARD"),
    ("HCM", "STANDARD"),
    ("MBS", "STANDARD"),
    # Utilities / industrials / real estate
    ("REE", "STANDARD"),
    ("POW", "STANDARD"),
    ("NVL", "STANDARD"),
    ("KDH", "STANDARD"),
    ("DXG", "STANDARD"),
    ("VRE", "STANDARD"),
]


def build_target_list() -> list:
    """Dedupe + keep VN50 order, include existing-symbol refresh."""
    seen = set()
    out = []
    for sym, ent in VN50:
        if sym not in seen:
            seen.add(sym)
            out.append((sym, ent))
    return out


def execute_vn50_sprint(dry_run: bool = False, delay: float = 3.0) -> dict:
    """Run full VN50 crawl + health compute."""
    targets = build_target_list()
    print("=" * 72)
    print(f"  VN50 EXPANSION — {len(targets)} blue chips (BANK + STANDARD)")
    print(f"  dry_run={dry_run} | delay={delay}s")
    print("=" * 72)

    db = FinancialFactsDB()
    db.init_schema()
    crawler = CafeFCrawler(db, use_playwright=False, delay=delay)
    health_engine = HealthEngine()
    health_engine.init_schema()

    total_facts = 0
    results = {"ok": [], "failed": [], "empty": []}

    for i, (sym, ent) in enumerate(targets):
        # register correct entity type first (registry has wrong STANDARD for banks)
        db.register_entity(sym, ent)
        if dry_run:
            print(f"  [DRY] {sym} ({ent})")
            continue
        try:
            r = crawler.crawl_symbol(sym, ent, source="vci")
            n = r.get("total_metrics", 0)
            if n == 0:
                results["empty"].append(sym)
                print(f"  [{i + 1}/{len(targets)}] {sym} ({ent}): EMPTY")
            else:
                total_facts += n
                results["ok"].append(sym)
                print(f"  [{i + 1}/{len(targets)}] {sym} ({ent}): {n} facts")
        except Exception as e:  # noqa: BLE001 - cố ý bắt rộng để fallback/phòng thủ an toàn
            results["failed"].append(sym)
            print(f"  [{i + 1}/{len(targets)}] {sym} ({ent}): ERROR {type(e).__name__}: {str(e)[:120]}")
        if i > 0 and delay > 0:
            time.sleep(delay)

    # compute health for all crawled symbols
    health_ok = []
    for sym, ent in targets:
        if dry_run:
            continue
        try:
            h = health_engine.compute_health(sym)
            if h.get("status") == "DONE" or h.get("total_ratios", 0) > 0:
                health_ok.append(sym)
        except Exception:  # noqa: BLE001, S110 - cố ý bắt rộng & bỏ qua phụ (fallback/phòng thủ)
            pass

    print("\n" + "=" * 72)
    print(
        f"  VN50 SPRINT COMPLETE: {len(results['ok'])} crawled, {len(results['empty'])} empty, {len(results['failed'])} failed"
    )
    if results["empty"]:
        print(f"  Empty: {', '.join(results['empty'])}")
    if results["failed"]:
        print(f"  Failed: {', '.join(results['failed'])}")
    print(f"  Total facts: {total_facts} | health computed: {len(health_ok)}")
    print("=" * 72)
    return results


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="VN50 Database Expansion")
    parser.add_argument("--dry-run", action="store_true", help="Chỉ liệt kê danh sách")
    parser.add_argument("--delay", type=float, default=3.0, help="Delay giây giữa các mã")
    args = parser.parse_args()
    execute_vn50_sprint(dry_run=args.dry_run, delay=args.delay)
