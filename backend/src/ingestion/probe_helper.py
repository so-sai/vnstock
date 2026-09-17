"""probe_helper.py — Phase 1b-i codified probing (NO parser, NO DB writes).

Chuan hoa bai hoc Phase 1a:
  - Playwright: channel="chrome" (bundle 1228 thieu), domcontentloaded + sleep
    (networkidle treo vo han tren web VN), stdout utf-8.
  - requests: timeout 15s, UA dinh danh, capture Date header lam server_time.
  - Backfill 3 tang: live (<24h + server_time) / forward (thieu server_time)
    / backfill (cam vao backtest + production).

Usage:
  from src.ingestion.probe_helper import http_probe, render_probe
"""

from __future__ import annotations

import hashlib
import sys
import time
from pathlib import Path


def _hydrate_path() -> Path:
    """Path Hydrator v2.1 (Anchor Fix): Auto-locate Project Root."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    current = Path(__file__).resolve().parent
    while current != current.parent:
        if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
            return current
        current = current.parent
    return Path(__file__).resolve().parent.parent.parent


PROJECT_ROOT = _hydrate_path()
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) PTCK-ingest/1.0"


def _keywords_hit(text: str, keywords: list[str]) -> dict[str, int]:
    low = text.lower()
    return {kw: low.count(kw.lower()) for kw in keywords}


def http_probe(url: str, keywords: list[str], timeout: int = 15) -> dict:
    """1 HTTP GET. Tra ve evidence dict (khong parse nghiep vu)."""
    import requests

    rec: dict = {
        "url": url,
        "method": "GET",
        "status": 0,
        "bytes": 0,
        "server_time": None,
        "server_time_missing": True,
        "tier": "backfill",
        "error": None,
        "keywords": {},
        "title": "",
    }
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=timeout)
        rec["status"] = r.status_code
        rec["bytes"] = len(r.content)
        rec["raw_hash"] = hashlib.sha256(r.content).hexdigest()[:16]
        date_h = r.headers.get("Date")
        if date_h:
            rec["server_time"] = date_h
            rec["server_time_missing"] = False
            rec["tier"] = "live"
        else:
            rec["tier"] = "forward"
        ct = r.headers.get("Content-Type", "")
        if "html" in ct:
            import re

            m = re.search(r"<title>(.*?)</title>", r.text, re.S)
            rec["title"] = m.group(1).strip()[:100] if m else ""
            rec["keywords"] = _keywords_hit(r.text, keywords)
        rec["final_url"] = r.url[:120]
    except Exception as e:  # noqa: BLE001 - probe bat moi loi mang, ghi nhan
        rec["error"] = f"{type(e).__name__}: {str(e)[:100]}"
    return rec


def render_probe(url: str, keywords: list[str], sleep_s: int = 6, max_tables: int = 25) -> dict:
    """1 Playwright render (channel=chrome). Tra ve DOM evidence."""
    from playwright.sync_api import sync_playwright

    rec: dict = {
        "url": url,
        "method": "RENDER",
        "title": "",
        "body_chars": 0,
        "tables": 0,
        "table_previews": [],
        "keywords": {},
        "tier": "live",
        "error": None,
    }
    t0 = time.time()
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True, channel="chrome")
            pg = b.new_page(user_agent=UA)
            resp = pg.goto(url, timeout=45000, wait_until="domcontentloaded")
            if resp:
                date_h = resp.headers.get("date")
                if date_h:
                    rec["server_time"] = date_h
                    rec["server_time_missing"] = False
                else:
                    rec["server_time_missing"] = True
                    rec["tier"] = "forward"
            pg.wait_for_timeout(sleep_s * 1000)
            rec["title"] = pg.title()[:100]
            body = pg.inner_text("body")
            rec["body_chars"] = len(body)
            rec["body_hash"] = hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]
            rec["keywords"] = _keywords_hit(body, keywords)
            tables = pg.eval_on_selector_all(
                "table", "els => els.map(t => ({rows: t.rows.length, text: t.innerText.slice(0, 300)}))"
            )
            rec["tables"] = len(tables)
            rec["table_previews"] = tables[:max_tables]
            b.close()
    except Exception as e:  # noqa: BLE001 - probe bat moi loi render, ghi nhan
        rec["error"] = f"{type(e).__name__}: {str(e)[:150]}"
    rec["elapsed_s"] = round(time.time() - t0, 1)
    return rec
