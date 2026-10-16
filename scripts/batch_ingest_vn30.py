"""Batch ingest TCBS data for VN30 Bluechips.

Tự động fetch từ TCBS và nạp vào financial_facts.db, health_ratios, valuation_scores
với Zero-Exposure (đọc token từ .env, không lưu credentials).
"""

import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "scripts"))
from ingest_tcbs_to_db import ingest_tcbs_json

VN30_REMAINING = [
    "ACB", "BCM", "BID", "BVH", "CTG", "GAS", "GVR", "HDB", "HPG", "MSN",
    "MWG", "PLX", "POW", "SAB", "SSB", "SSI", "STB", "TPB", "VCB", "VHM",
    "VIB", "VIC", "VJC", "VNM", "VPB", "VRE"
]

def main():
    print("=" * 75)
    print(f"BẮT ĐẦU BATCH INGESTION CHO {len(VN30_REMAINING)} MÃ VN30 CÒN LẠI")
    print("=" * 75)

    results = []
    start_all = time.time()

    for idx, sym in enumerate(VN30_REMAINING, start=1):
        print(f"\n[{idx}/{len(VN30_REMAINING)}] Đang xử lý {sym}...")
        t0 = time.time()
        try:
            res = ingest_tcbs_json(sym, force_fetch=True)
            elapsed = time.time() - t0
            print(f"  --> [OK] {sym}: {res['periods_written']} quý | {res['total_facts_written']} facts | {elapsed:.1f}s")
            results.append({
                "symbol": sym,
                "status": "OK",
                "entity_type": res["entity_type"],
                "periods": res["periods_written"],
                "facts": res["total_facts_written"],
                "health_ratios": res["health_ratios_total"],
                "valuation": res.get("valuation_status"),
                "elapsed": elapsed,
            })
        except Exception as e:
            elapsed = time.time() - t0
            print(f"  --> [ERR] {sym}: {e}")
            results.append({
                "symbol": sym,
                "status": f"ERR: {e}",
                "elapsed": elapsed,
            })
        time.sleep(0.5)

    total_time = time.time() - start_all
    print("\n" + "=" * 75)
    print(f"HOÀN TẤT BATCH TRONG {total_time:.1f} GIÂY")
    print("=" * 75)
    print(f"{'Mã':6s} | {'Loại':10s} | {'Số quý':8s} | {'Facts':8s} | {'Ratios':8s} | {'Status':15s}")
    print("-" * 75)
    for r in results:
        if r.get("status") == "OK":
            print(f"{r['symbol']:6s} | {r['entity_type']:10s} | {r['periods']:8d} | {r['facts']:8d} | {r['health_ratios']:8d} | OK ({r['elapsed']:.1f}s)")
        else:
            print(f"{r['symbol']:6s} | {'-':10s} | {'-':8s} | {'-':8s} | {'-':8s} | {r['status']}")
    print("=" * 75)

if __name__ == "__main__":
    main()
