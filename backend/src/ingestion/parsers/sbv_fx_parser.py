"""sbv_fx_parser.py — Track 3 SBV FX parser (Session B).

Parse DOM da dong bang (sha-verified). Chi t0 (trung tam) + t1 (So GD).
t2 cross-rate: DROPPED (Q1a). intervention_type=NONE tru khi co van ban
can thiep (Q3). PK=document_id (Q2). Thieu server_time => tier forward (Fix 4).
Moi record moi audit_status=PENDING (Fix 5). Vi pham invariant =>
IngestionInvariantError, khong ghi DB.

Usage:
  python backend/src/ingestion/parsers/sbv_fx_parser.py --frozen DOM.html
    --sha <expected16> --source-url URL --db staging.db [--server-time "..."]
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import re
import sqlite3
from html.parser import HTMLParser
from pathlib import Path

EXPECTED_SHA = "328fc26a21f001fa"  # sha256 file sbv_fx_20260914.html (frozen artifact).
# (Probe body_hash bc92686a la hash innerText luc render — artifact khac.)
SOURCE_URL = "https://sbv.gov.vn/t%E1%BB%B7-gi%C3%A1"


class IngestionSourceBrokenError(Exception):
    """DOM bi sua / cau truc doi — dung, khong parse."""


class IngestionInvariantError(Exception):
    """Vi pham bat bien — tu choi ghi DB."""


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


class _TableGrabber(HTMLParser):
    """Thu text theo table.bi01-table (giua nguyen thu tu)."""

    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._cur: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            d = dict(attrs)
            if "bi01-table" in (d.get("class") or ""):
                self._cur = []
                self.tables.append(self._cur)
                self._depth += 1
        elif tag == "tr" and self._cur is not None:
            self._row = []
            self._cur.append(self._row)
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append("".join(self._cell).strip())
            self._cell = None
        elif tag == "tr":
            self._row = None
        elif tag == "table" and self._cur is not None and self._depth:
            self._depth -= 1
            if self._depth == 0:
                self._cur = None


def _num(txt: str) -> float | None:
    """So VN: dau '.' = phan cach nghin (bo), dau ',' = thap phan."""
    t = txt.replace("\u00a0", "").replace(" ", "").replace(".", "").replace(",", ".")
    t = re.sub(r"[^0-9.\-]", "", t)
    try:
        return float(t) if t not in ("", "-", ".") else None
    except ValueError:
        return None


def load_frozen_dom(path: Path, expected_sha: str) -> tuple[str, str]:
    raw = path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()[:16]
    if sha != expected_sha:
        raise IngestionSourceBrokenError(f"DOM tampered: {path} sha={sha}")
    return raw.decode("utf-8", errors="replace"), sha


def parse_fx(html: str) -> dict:
    g = _TableGrabber()
    g.feed(html)
    if len(g.tables) < 2:
        raise IngestionSourceBrokenError(f"expected >=2 bi01-table, got {len(g.tables)}")
    t0, t1 = g.tables[0], g.tables[1]
    flat0 = " | ".join(c for r in t0 for c in r)
    m_rate = re.search(r"1\s*Đô la Mỹ\s*=\s*\|?\s*([\d.,]+)\s*VND", flat0)
    m_doc = re.search(r"Số văn bản\s*\|\s*([\w/\-]+)", flat0)
    m_iss = re.search(r"Ngày ban hành\s*\|\s*([\d/]+)", flat0)
    if not m_rate:
        raise IngestionInvariantError("missing central_rate")
    if not m_doc:
        raise IngestionInvariantError("missing document_id (FAIL, khong nullable)")
    central = _num(m_rate.group(1))
    usd_row = next((r for r in t1 if r and r[0] == "1" and "USD" in (r[1] if len(r) > 1 else "")), None)
    if usd_row is None:
        usd_row = next((r for r in t1 if any("USD" in c for c in r)), None)
    bid = _num(usd_row[-2]) if usd_row and len(usd_row) >= 4 else None
    ask = _num(usd_row[-1]) if usd_row and len(usd_row) >= 4 else None
    iss = m_iss.group(1) if m_iss else None
    try:
        d, mo, y = iss.split("/")
        issue_iso = f"{y}-{mo.zfill(2)}-{d.zfill(2)}"
    except ValueError, AttributeError:
        raise IngestionInvariantError(f"bad issue_date: {iss!r}")
    return {
        "central_rate": central,
        "sbv_bid_rate": bid,
        "sbv_ask_rate": ask,
        "document_id": m_doc.group(1).strip(),
        "issue_date": issue_iso,
    }


def check_invariants(rec: dict) -> None:
    cr = rec["central_rate"]
    if cr is None or not (22000 < cr < 30000):
        raise IngestionInvariantError(f"central_rate out of [22000,30000]: {cr}")
    b, a = rec["sbv_bid_rate"], rec["sbv_ask_rate"]
    if b is not None and a is not None and not (b < cr < a):
        raise IngestionInvariantError(f"bid<central<ask violated: {b} {cr} {a}")


def main() -> int:
    ap = argparse.ArgumentParser(description="SBV FX parser — Track 3")
    ap.add_argument("--frozen", required=True)
    ap.add_argument("--sha", default=EXPECTED_SHA)
    ap.add_argument("--source-url", default=SOURCE_URL)
    ap.add_argument("--server-time", default=None)
    ap.add_argument("--db", required=True)
    ap.add_argument("--as-of", default=None, help="ngay hieu luc YYYY-MM-DD")
    a = ap.parse_args()
    html, sha = load_frozen_dom(Path(a.frozen), a.sha)
    print(f"dom_sha_verified: {sha}")
    rec = parse_fx(html)
    check_invariants(rec)
    tier = "live" if a.server_time else "forward"  # Fix 4
    now = datetime.datetime.now().isoformat(timespec="seconds")
    row = {
        "document_id": rec["document_id"],
        "date": a.as_of or rec["issue_date"],
        "central_rate": rec["central_rate"],
        "sbv_bid_rate": rec["sbv_bid_rate"],
        "sbv_ask_rate": rec["sbv_ask_rate"],
        "issue_date": rec["issue_date"],
        "intervention_type": "NONE",
        "data_tier": tier,
        "source_url": a.source_url,
        "server_time": a.server_time,
        "raw_hash": sha,
        "crawled_at": now,
        "audit_status": "PENDING",
    }
    con = sqlite3.connect(a.db)
    con.execute(DDL)
    cur = con.execute(
        "INSERT OR IGNORE INTO staging_sbv_fx_status VALUES "
        "(:document_id,:date,:central_rate,:sbv_bid_rate,:sbv_ask_rate,"
        ":issue_date,:intervention_type,:data_tier,:source_url,:server_time,"
        ":raw_hash,:crawled_at,:audit_status)",
        row,
    )
    con.commit()
    print(f"rows_inserted={cur.rowcount} (0 = duplicate document, skipped)")
    con.close()
    print(json.dumps(row, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
