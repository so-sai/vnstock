"""audit_sbv_fx_track3.py — Track 3 in-repo audit (reproducible, CI-ready).

5 buoc: tamper-check frozen DOM -> Method A extraction (title="25607",
bypass regex text) -> invariants -> staging INSERT OR IGNORE (PASS) ->
ledger audit_passed.

CORRECTION vs brief: brief ghi expected sha `bc92686a` — DO LA BODY_HASH
luc probe (innerText), KHONG PHAI file-sha. File dong bang
sbv_fx_20260914.html sha256 = `328fc26a21f001fa` (xac minh 2 lan doc lap).
Audit dung file-sha; ledger ghi ca 2 de truy vet. Khong sua lich su.

Usage:
  python backend/src/ingestion/audits/audit_sbv_fx_track3.py
    --frozen-dom backend/src/ingestion/forensics/sbv_fx_20260914.html
    --staging-db backend/data/staging_microstructure.db
    --ledger backend/src/ingestion/ledger/ingestion-ledger.jsonl
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import re
import sqlite3
from pathlib import Path

FILE_SHA = "328fc26a21f001fa"  # sha256 file sbv_fx_20260914.html
BODY_SHA_PROBE = "bc92686a19fa3b4b"  # sha innerText luc probe (artifact khac)
SOURCE_URL = "https://sbv.gov.vn/t%E1%BB%B7-gi%C3%A1"


class AuditTamperError(Exception):
    pass


class AuditInvariantError(Exception):
    pass


DDL = """
CREATE TABLE IF NOT EXISTS staging_sbv_fx_status (
    document_id TEXT PRIMARY KEY,
    date TEXT NOT NULL,
    central_rate REAL NOT NULL,
    sbv_bid_rate REAL,
    sbv_ask_rate REAL,
    issue_date TEXT NOT NULL,
    intervention_type TEXT NOT NULL DEFAULT 'NONE'
        CHECK (intervention_type IN ('NONE','SPOT_SALE','FORWARD_SALE','BUY_USD','UNKNOWN')),
    data_tier TEXT NOT NULL CHECK (data_tier IN ('live','forward','backfill')),
    source_url TEXT NOT NULL,
    server_time TEXT,
    raw_hash TEXT NOT NULL,
    crawled_at TEXT NOT NULL,
    audit_status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (audit_status IN ('PENDING','PASS','FAIL','CORRUPTED'))
)
"""


def main() -> int:
    ap = argparse.ArgumentParser(description="Track 3 SBV FX audit")
    ap.add_argument("--frozen-dom", required=True)
    ap.add_argument("--staging-db", required=True)
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--server-time", default="Mon, 14 Sep 2026 04:07:57 GMT")
    ap.add_argument("--as-of", default="2026-09-14")
    a = ap.parse_args()

    # 1. Tamper check on FILE bytes
    raw = Path(a.frozen_dom).read_bytes()
    sha = hashlib.sha256(raw).hexdigest()[:16]
    if sha != FILE_SHA:
        raise AuditTamperError(f"frozen DOM sha={sha} != {FILE_SHA}")
    print(f"tamper_check: OK sha={sha}")
    html = raw.decode("utf-8", errors="replace")

    # 2. Method A extraction (title attributes = clean integers)
    m = re.search(r'title="1 Đô la Mỹ ="[^>]*>.*?title="(?P<rate>\d+)"', html, re.DOTALL)
    if not m:
        m = re.search(r'1 Đô la Mỹ =</strong></td>\s*<td[^>]*title="(?P<rate>\d+)"', html, re.DOTALL)
    if not m:
        raise AuditInvariantError("central_rate title attribute not found")
    central = float(m.group("rate"))
    m_doc = re.search(r"Số văn bản.*?</td>\s*<td[^>]*>([\w/\-]+)</td>", html, re.DOTALL)
    m_iss = re.search(r"Ngày ban hành.*?</td>\s*<td[^>]*>([\d/]+)</td>", html, re.DOTALL)
    if not m_doc:
        raise AuditInvariantError("document_id missing (FAIL)")
    d, mo, y = (m_iss.group(1) if m_iss else "").split("/")
    issue_iso = f"{y}-{mo.zfill(2)}-{d.zfill(2)}"
    # t1 USD row: cells STT=1, USD, name, bid, ask (VN format)
    m_usd = re.search(
        r"<td[^>]*>1</td>\s*<td[^>]*>USD</td>\s*<td[^>]*>[^<]*</td>\s*"
        r"<td[^>]*>([\d.,]+)</td>\s*<td[^>]*>([\d.,]+)</td>",
        html,
        re.DOTALL,
    )

    def vnnum(t: str) -> float:
        return float(t.replace(".", "").replace(",", "."))

    bid = vnnum(m_usd.group(1)) if m_usd else None
    ask = vnnum(m_usd.group(2)) if m_usd else None

    # 3. Invariants
    if not (22000 < central < 30000):
        raise AuditInvariantError(f"central_rate range: {central}")
    if bid is not None and ask is not None and not (bid < central < ask):
        raise AuditInvariantError(f"bid<central<ask: {bid} {central} {ask}")
    if not m_doc.group(1).strip():
        raise AuditInvariantError("empty document_id")

    # 4. Staging INSERT OR IGNORE with PASS
    now = datetime.datetime.now().isoformat(timespec="seconds")
    row = {
        "document_id": m_doc.group(1).strip(),
        "date": a.as_of,
        "central_rate": central,
        "sbv_bid_rate": bid,
        "sbv_ask_rate": ask,
        "issue_date": issue_iso,
        "intervention_type": "NONE",
        "data_tier": "live" if a.server_time else "forward",
        "source_url": SOURCE_URL,
        "server_time": a.server_time,
        "raw_hash": sha,
        "crawled_at": now,
        "audit_status": "PASS",
    }
    con = sqlite3.connect(a.staging_db)
    con.execute(DDL)
    cur = con.execute(
        "INSERT OR IGNORE INTO staging_sbv_fx_status VALUES "
        "(:document_id,:date,:central_rate,:sbv_bid_rate,:sbv_ask_rate,"
        ":issue_date,:intervention_type,:data_tier,:source_url,:server_time,"
        ":raw_hash,:crawled_at,:audit_status)",
        row,
    )
    con.commit()
    inserted = cur.rowcount
    stored = con.execute(
        "SELECT central_rate, sbv_bid_rate, sbv_ask_rate, issue_date,"
        " intervention_type, data_tier, source_url, audit_status"
        " FROM staging_sbv_fx_status WHERE document_id=?",
        (row["document_id"],),
    ).fetchone()
    keys = ("central_rate", "sbv_bid_rate", "sbv_ask_rate", "issue_date", "intervention_type", "data_tier", "source_url")
    mismatch = [k for k, v in zip(keys, stored[:7]) if v != row[k]]
    if mismatch:
        con.execute("UPDATE staging_sbv_fx_status SET audit_status='FAIL' WHERE document_id=?", (row["document_id"],))
        con.commit()
        con.close()
        raise AuditInvariantError(f"stored row diverges: {mismatch}")
    con.execute("UPDATE staging_sbv_fx_status SET audit_status='PASS' WHERE document_id=?", (row["document_id"],))
    con.commit()
    final_status = "PASS"
    con.close()
    print(f"extract: central={central} bid={bid} ask={ask} doc={row['document_id']}")
    print(f"inserted={inserted} final_status={final_status}")

    # 5. Ledger
    with open(a.ledger, "a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "event": "audit_passed",
                    "track": "track3_sbv_fx",
                    "document_id": row["document_id"],
                    "rate": central,
                    "hash": sha,
                    "body_hash_probe": BODY_SHA_PROBE,
                    "sha_correction": "brief said bc92686a (body); file is 328fc26a",
                    "status": "VERIFIED_PASS" if inserted else "ALREADY_PRESENT",
                    "db_status": final_status,
                },
                ensure_ascii=False,
            )
            + "\n"
        )
    print("ledger: audit_passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
