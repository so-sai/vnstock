import sqlite3
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
FIN_DB = ROOT / "backend" / "data" / "financial_facts.db"
SCREENER_DB = ROOT / "backend" / "data" / "screener_cache.db"

def audit():
    conn = sqlite3.connect(FIN_DB)
    cur = conn.cursor()

    # Tables in financial_facts.db
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [r[0] for r in cur.fetchall()]
    print("=== CÁC BẢNG TRONG financial_facts.db ===")
    for t in tables:
        cur.execute(f"SELECT COUNT(*) FROM {t}")
        print(f"  {t:25s}: {cur.fetchone()[0]:,d} dòng")

    # Symbols in financial_facts
    cur.execute("""
        SELECT symbol,
               COUNT(*) as facts_cnt,
               COUNT(DISTINCT period) as p_cnt,
               MIN(period) as min_p,
               MAX(period) as max_p,
               SUM(CASE WHEN metric = 'SHARES_OUT' THEN 1 ELSE 0 END) as shares_cnt,
               SUM(CASE WHEN metric LIKE '%NPL%' THEN 1 ELSE 0 END) as npl_cnt,
               SUM(CASE WHEN source = 'tcbs' THEN 1 ELSE 0 END) as tcbs_cnt
        FROM financial_facts
        GROUP BY symbol
        ORDER BY facts_cnt DESC
    """)
    rows = cur.fetchall()
    print(f"\n=== TỔNG CỘNG {len(rows)} MÃ TRONG financial_facts ===")
    tcbs_ingested = [r for r in rows if r[7] > 0]
    not_tcbs = [r for r in rows if r[7] == 0]
    print(f"  - Đã nạp từ TCBS (70+ quý, đầy đủ metrics): {len(tcbs_ingested)} mã -> {[r[0] for r in tcbs_ingested]}")
    print(f"  - Chưa nạp từ TCBS (chỉ có dữ liệu cũ ~34 quý VCI/CafeF/Vietstock): {len(not_tcbs)} mã")

    # Check VN30 symbols
    VN30 = [
        "ACB", "BCM", "BID", "BVH", "CTG", "FPT", "GAS", "GVR", "HDB", "HPG",
        "MBB", "MSN", "MWG", "PLX", "POW", "SAB", "SHB", "SSB", "SSI", "STB",
        "TCB", "TPB", "VCB", "VHM", "VIB", "VIC", "VJC", "VNM", "VPB", "VRE"
    ]
    present_symbols = {r[0] for r in rows}
    missing_vn30 = [s for s in VN30 if s not in present_symbols]
    old_vn30 = [s for s in VN30 if s in present_symbols and s not in {r[0] for r in tcbs_ingested}]

    print(f"\n=== KIỂM TOÁN RỔ VN30 (30 MÃ BLUECHIPS) ===")
    print(f"  - Đã có dữ liệu TCBS đầy đủ (70+ quý): {[r[0] for r in tcbs_ingested if r[0] in VN30]}")
    print(f"  - Có trong DB nhưng mới chỉ có dữ liệu cũ (~34 quý, thiếu SHARES_OUT / Bank ratios): {len(old_vn30)} mã")
    print(f"    -> {old_vn30}")
    print(f"  - Hoàn toàn chưa có trong DB: {missing_vn30}")

    # Check Banks in VN30 and other banks
    banks = ["VCB", "BID", "CTG", "ACB", "MBB", "TCB", "VPB", "HDB", "STB", "TPB", "VIB", "SHB", "SSB", "MSB", "LPB", "EIB", "OCB"]
    old_banks = [b for b in banks if b in present_symbols and b not in {r[0] for r in tcbs_ingested}]
    print(f"\n=== KIỂM TOÁN NHÓM NGÂN HÀNG (Cần bổ sung NPL, NIM, LLR, LDR, TTTD) ===")
    print(f"  - Đã có đủ metric Bank chuẩn: {[r[0] for r in tcbs_ingested if r[0] in banks]}")
    print(f"  - Đang THIẾU metric Bank (chỉ có raw balance sheet tóm tắt): {old_banks}")

    # Screener DB
    if SCREENER_DB.exists():
        s_conn = sqlite3.connect(SCREENER_DB)
        s_cur = s_conn.cursor()
        s_cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        s_tables = [r[0] for r in s_cur.fetchall()]
        print(f"\n=== CÁC BẢNG TRONG screener_cache.db ===")
        for t in s_tables:
            s_cur.execute(f"SELECT COUNT(*) FROM {t}")
            print(f"  {t:25s}: {s_cur.fetchone()[0]:,d} dòng")
            if t == "daily_ohlcv":
                s_cur.execute("SELECT MIN(date), MAX(date), COUNT(DISTINCT symbol) FROM daily_ohlcv")
                dmin, dmax, scnt = s_cur.fetchone()
                print(f"    -> {scnt} mã, từ ngày {dmin} đến ngày {dmax}")
        s_conn.close()

    conn.close()

if __name__ == "__main__":
    audit()
