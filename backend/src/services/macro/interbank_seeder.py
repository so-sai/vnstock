import json
import logging
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import requests


def _hydrate_path():
    if getattr(sys, "frozen", False):
        root_path = Path(sys.executable).resolve().parent
    else:
        current = Path(__file__).resolve().parent
        root_path = current
        while current != current.parent:
            if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
                root_path = current
                break
            current = current.parent
    if str(root_path) not in sys.path:
        sys.path.insert(0, str(root_path))
    backend_dir = root_path / "backend"
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    return root_path


PROJECT_ROOT = _hydrate_path()

import pandas as pd

from src.database.db_core import get_connection, save_data_upsert
from src.utils.raise_parser_alert import raise_parser_alert

logger = logging.getLogger(__name__)

VIETNAMBiz_BUILD_ID = "4rZHofl9s0ftfNuzY0Phf"
VIETNAMBiz_RATES_URL = f"https://data.vietnambiz.vn/_next/data/{VIETNAMBiz_BUILD_ID}/currency-interest-rate.json"

INTERBANK_VARIABLES = [
    "INTERBANK_ON",
    "INTERBANK_1W",
    "INTERBANK_2W",
    "INTERBANK_1M",
    "INTERBANK_3M",
    "INTERBANK_6M",
    "INTERBANK_9M",
]


def _normalize(val) -> float | None:
    """Ép giá trị về float sạch, xoá ký tự rác / khoảng trắng."""
    if val is None:
        return None
    cleaned = re.sub(r"[^0-9\.]", "", str(val).replace(",", "."))
    try:
        return float(cleaned)
    except ValueError, TypeError:
        return None


def _get_latest_from_db(variable: str) -> tuple[float, str] | tuple[None, None]:
    """Lấy giá trị và ngày mới nhất của 1 biến từ DB."""
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT value, date FROM macro_history WHERE variable = ? ORDER BY date DESC LIMIT 1",
                (variable,),
            ).fetchone()
            if row is None:
                return None, None
            return float(row[0]), str(row[1])
    except sqlite3.Error, TypeError, ValueError, KeyError, IndexError:
        return None, None


# ---------------------------------------------------------------------------
#  SOURCE 1 — SBV website (Playwright, primary)
# ---------------------------------------------------------------------------
SBV_INTERBANK_URL = "https://sbv.gov.vn/l%C3%A3i-su%E1%BA%A5t1"

TERM_MAP = {
    "Qua đêm": "ON",
    "1 Tuần": "1W",
    "2 Tuần": "2W",
    "1 Tháng": "1M",
    "3 Tháng": "3M",
    "6 Tháng": "6M",
    "9 Tháng": "9M",
}

# ---------------------------------------------------------------------------
#  SBV Structure Change Detection
# ---------------------------------------------------------------------------
ALERT_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data" / "alerts"
ALERT_FILE = ALERT_DIR / "sbv_structure_changed.json"

SBV_TABLE_SIGNATURES = [
    "Qua đêm",
    "1 Tuần",
    "2 Tuần",
    "1 Tháng",
    "3 Tháng",
    "6 Tháng",
    "9 Tháng",
]

CLOUDFLARE_SIGNATURES = ["cf-browser-request", "Attention Required", "Just a moment", "sucuri"]

JS_RENDERING_SIGNATURES = ["shadow-root", "<script", "render(", "createElement", "appendChild"]


def _has_sbv_table_signature(html: str) -> bool:
    """Kiểm tra HTML có chứa dữ liệu lãi suất SBV không.

    Tầng 1: tìm trực tiếp các thuật ngữ trong SBV_TABLE_SIGNATURES
    (chữ Khmer + HTML table truyền thống).
    Tầng 2: regex tìm cặp term – số trong thẻ <div>/<span> (DOM hiện đại).
    """
    if any(term in html for term in SBV_TABLE_SIGNATURES):
        return True

    div_signal = re.search(
        r"<(?:div|span)[^>]*>.*?(Qua\s*đêm|1\s*Tuần|2\s*Tuần|1\s*Tháng|3\s*Tháng|6\s*Tháng|9\s*Tháng)",
        html,
        re.IGNORECASE | re.DOTALL | re.UNICODE,
    )
    return div_signal is not None


def _has_cloudflare_signature(html: str) -> bool:
    """Kiểm tra HTML có phải Cloudflare challenge không."""
    return any(sig in html for sig in CLOUDFLARE_SIGNATURES)


def _has_js_rendering(html: str) -> bool:
    """Kiểm tra HTML có dấu hiệu JS rendering / Shadow DOM không."""
    return any(sig in html for sig in JS_RENDERING_SIGNATURES)


def _log_sbv_alert(raw_html: str = ""):
    """Ghi alert file khi cấu trúc SBV thay đổi (atomic write) + popup Windows (1 lần)."""
    import os
    import subprocess
    import time

    was_active = ALERT_FILE.exists()
    ALERT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "timestamp": time.time(),
        "error_type": "SBVStructureChanged",
        "raw_html_snapshot": raw_html[:2000],
    }
    tmp = ALERT_FILE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=4)
    os.replace(tmp, ALERT_FILE)

    # Popup chỉ 1 lần (singleton) — tránh zombie PowerShell
    if was_active:
        return
    try:
        ps_cmd = (
            "[Void][System.Reflection.Assembly]::LoadWithPartialName('System.Windows.Forms'); "
            "[System.Windows.Forms.MessageBox]::Show("
            "'SBV HTML structure changed — data parser unavailable. Position forced to 0.0. Run: python ptck.py sbv-update', "
            "'[PTCK_ALERT] DATA PARSER FAILED', "
            "[System.Windows.Forms.MessageBoxButtons]::OK, "
            "[System.Windows.Forms.MessageBoxIcon]::Warning)"
        )
        subprocess.Popen(
            ["powershell", "-Command", f"& {{{ps_cmd}}}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, ValueError, subprocess.SubprocessError) as _e:
        logger.debug("Popup cảnh báo SBV không hiển thị được (bỏ qua): %s", _e)


def _clear_sbv_alert():
    """Xóa alert file khi cấu trúc SBV đã được fix."""
    import os

    if ALERT_FILE.exists():
        os.remove(str(ALERT_FILE))
    tmp = ALERT_FILE.with_suffix(".tmp")
    if tmp.exists():
        os.remove(str(tmp))


def _is_sbv_alert_active() -> bool:
    """Kiểm tra alert file có tồn tại không.
    STRUCTURE_UNKNOWN là lỗi logic code, KHÔNG auto-expire.
    Chỉ sbv-update (manual) mới xóa được."""
    if not ALERT_FILE.exists():
        return False
    return True


TERM_RE_PATTERN = re.compile(
    r"(" + "|".join(re.escape(k) for k in TERM_MAP) + r")\s*[^<>]{0,60}?"
    r"(\d+[\s,]*\d*\.?\d+)",
    re.IGNORECASE | re.UNICODE,
)

CELL_RE_PATTERN = re.compile(
    r"<t[hd][^>]*>\s*([^<]+?)\s*</t[hd]>.*?<t[hd][^>]*>\s*([^<]+?)\s*</t[hd]>",
    re.IGNORECASE | re.DOTALL | re.UNICODE,
)


@raise_parser_alert(
    source="SBV",
    message="SBV HTML structure changed — OMO/Tín phiếu parser unavailable.",
    recovery="ptck.py sbv-update",
    fail_safe={},
)
def _parse_sbv_html(html_text: str) -> dict[str, float | None]:
    """Hybrid parser: Layer 1 DOM tables + Layer 2 context regex fallback.

    Layer 1 (DOM): extract <table> rows via lxml XPath (original approach).
    Layer 2 (Regex): scan raw HTML for Vietnamese term + rate pairs using
    TERM_RE_PATTERN (handles div/span/Jinja/JS-rendered layouts).

    Args:
        html_text: Full HTML content of the SBV page.

    Returns:
        dict mapping short codes (ON, 1W, 2W, ...) to float rates or None.
        Empty dict on total failure (decorator returns {fail_safe} and alerts).
    """
    result = _parse_sbv_dom(html_text)
    if result and _validate_structural_integrity(result):
        return result

    result = _parse_sbv_regex(html_text)
    if result:
        return result

    return {}


def _parse_sbv_dom(html_text: str) -> dict[str, float | None]:
    """Layer 1: lxml DOM table extraction (original approach)."""
    try:
        from lxml import html as lx

        tree = lx.fromstring(html_text)
    except Exception:  # noqa: BLE001 - fallback ladder: thử method parse lxml khác
        try:
            from lxml.html import fromstring as _hf

            tree = _hf(html_text)
        except Exception:  # noqa: BLE001 - fallback ladder: method dự phòng lxml.html
            return {}

    tables = tree.xpath("//table")
    if len(tables) < 1:
        return {}

    result: dict[str, float | None] = {}
    target = tables[1] if len(tables) > 1 else tables[0]

    for row in target.xpath(".//tr"):
        cells = row.xpath(".//td")
        if len(cells) >= 2:
            term = (cells[0].text_content() or "").strip()
            rate_str = (cells[1].text_content() or "").strip()
            if term in TERM_MAP:
                result[TERM_MAP[term]] = _normalize(rate_str)
        if len(cells) == 1 and len(row.xpath(".//th")) >= 1:
            term = (row.xpath(".//th")[0].text_content() or "").strip()
            if term in TERM_MAP:
                result[TERM_MAP[term]] = None

    return result


def _parse_sbv_regex(html_text: str) -> dict[str, float | None]:
    """Layer 2: context regex extraction — matches Vietnamese terms + rates
    from any DOM structure (table, div, span, script)."""
    result: dict[str, float | None] = {}
    for m in TERM_RE_PATTERN.finditer(html_text):
        term_raw = m.group(1).strip()
        rate_raw = m.group(2).strip()
        if term_raw in TERM_MAP:
            result[TERM_MAP[term_raw]] = _normalize(rate_raw)

    for m in CELL_RE_PATTERN.finditer(html_text):
        term_raw = m.group(1).strip()
        rate_raw = m.group(2).strip()
        if term_raw in TERM_MAP:
            result[TERM_MAP[term_raw]] = _normalize(rate_raw)

    return result


VALIDATION_BOUNDS = {
    "ON": (0, 35),
    "1W": (0, 35),
    "2W": (0, 35),
    "1M": (0, 30),
    "3M": (0, 25),
    "6M": (0, 20),
    "9M": (0, 20),
}
TENOR_ORDER_VAL = ["ON", "1W", "2W", "1M", "3M", "6M", "9M"]


def _validate_structural_integrity(rates: dict) -> bool:
    """Kiểm tra parser không nhầm cột (ngày→lãi suất, hoán đổi kỳ hạn).

    Ngưỡng rộng, chỉ bắt lỗi parser ngớ ngẩn. KHÔNG đo rủi ro thị trường.
    """
    for k, v in rates.items():
        lo, hi = VALIDATION_BOUNDS.get(k, (0, 100))
        if v is not None and not (lo <= v <= hi):
            logger.warning(f"SBV validation: {k}={v} outside [{lo}, {hi}]")
            return False

    present = [(t, rates[t]) for t in TENOR_ORDER_VAL if rates.get(t) is not None]
    if len(present) < 2:
        return True  # không đủ kỳ hạn để kiểm tra tương quan chéo

    # Crisis mode: nếu kỳ hạn ngắn nhất (ON hoặc 1W) ≥ 15%, bỏ qua kiểm tra đơn điệu
    # vì lúc này đảo ngược cực đoan là tín hiệu khủng hoảng thật, không phải lỗi parser.
    shortest = present[0][1]
    in_crisis = shortest >= 15.0

    if not in_crisis:
        for i in range(len(present) - 1):
            t1, r1 = present[i]
            t2, r2 = present[i + 1]
            if r1 is None or r2 is None:
                continue
            if r1 > r2 * 2.5:
                logger.warning(f"SBV validation: {t1}={r1} > {t2}={r2}*2.5 — column swap?")
                return False
            if r2 > r1 * 5.0:
                logger.warning(f"SBV validation: {t2}={r2} > {t1}={r1}*5.0 — column swap?")
                return False

    return True


def _try_sbv(force: bool = False) -> dict:
    """Dùng Playwright để trích xuất bảng lãi suất liên ngân hàng từ sbv.gov.vn.

    Args:
        force: Bỏ qua circuit breaker (dùng cho sbv-update).

    Trả về dict {ON, 1W, 2W, 1M, ...} hoặc {} nếu thất bại.
    """
    # Circuit breaker: nếu alert còn hiệu lực, không launch Playwright
    if not force and _is_sbv_alert_active():
        logger.warning("SBV: circuit breaker active — skip Playwright, trả về STRUCTURE_UNKNOWN")
        return {"type": "STRUCTURE_UNKNOWN", "data": {}, "http_status": None}

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("Playwright chưa được cài đặt — bỏ qua SBV")
        return {"type": "NO_PLAYWRIGHT", "data": {}, "http_status": None}

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                channel="chrome",
                args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
            )
            ctx = browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                locale="vi-VN",
            )
            ctx.add_init_script("""Object.defineProperty(navigator, 'webdriver', { get: () => undefined });""")
            page = ctx.new_page()
            # WHY domcontentloaded thay vì networkidle (2026-08-11): SBV duy trì
            # kết nối ngầm (long-lived/keep-alive) khiến `networkidle` không bao
            # giờ đạt → Timeout 30s mỗi phiên. Chỉ cần DOM + JS render xong bảng;
            # giữ wait_for_timeout(5000) bên dưới để JS render tĩnh hoàn tất.
            resp = page.goto(SBV_INTERBANK_URL, timeout=30000, wait_until="domcontentloaded")
            page.wait_for_timeout(5000)

            # ── Bước 1: Kiểm tra HTTP status ──
            http_status = resp.status if resp else None
            if http_status and http_status != 200:
                html_raw = page.content()
                if _has_cloudflare_signature(html_raw):
                    logger.warning(f"SBV HTTP {http_status} + Cloudflare — network blocked")
                    browser.close()
                    return {"type": "NETWORK_BLOCKED", "data": {}, "http_status": http_status}
                logger.warning(f"SBV HTTP {http_status} — network blocked")
                browser.close()
                return {"type": "NETWORK_BLOCKED", "data": {}, "http_status": http_status}

            # ── Bước 2: Kiểm tra HTML signature (Cloudflare trước, table sau) ──
            html_raw = page.content()
            if _has_cloudflare_signature(html_raw):
                logger.warning("SBV: Cloudflare challenge detected")
                browser.close()
                return {"type": "NETWORK_BLOCKED", "data": {}, "http_status": 403}
            if not _has_sbv_table_signature(html_raw):
                if _has_js_rendering(html_raw):
                    logger.warning("SBV: structure changed — JS/Shadow DOM detected")
                    _log_sbv_alert(html_raw)
                    browser.close()
                    return {"type": "STRUCTURE_CHANGED", "data": {}, "http_status": 200}
                logger.warning("SBV: unknown HTML structure — no SBV table signature")
                _log_sbv_alert(html_raw)
                browser.close()
                return {"type": "STRUCTURE_CHANGED", "data": {}, "http_status": 200}

            # ── Bước 3: Parse với lxml ──
            result = _parse_sbv_html(html_raw)

            # ── Bước 3a: Runtime structural validation ──
            # Mục đích: bắt lỗi parser (nhầm cột ngày→2026%), KHÔNG đo rủi ro thị trường.
            if result and not _validate_structural_integrity(result):
                logger.warning("SBV: structural integrity check failed — treating as STRUCTURE_CHANGED")
                _log_sbv_alert(html_raw)
                browser.close()
                return {"type": "STRUCTURE_CHANGED", "data": {}, "http_status": 200}

            # Trích xuất ngày áp dụng
            body = page.inner_text("body")
            m = re.search(r"Ngày áp dụng:\s*(\d{2}/\d{2}/\d{4})", body)
            if m:
                logger.info(f"SBV interbank data date: {m.group(1)}")

            browser.close()

        if result:
            logger.info(f"SBV Playwright: lấy được {len(result)} kỳ hạn: {result}")
            _clear_sbv_alert()
            return {"type": "SUCCESS", "data": result, "http_status": 200, "raw_html": html_raw}
        else:
            logger.warning("SBV Playwright: parse rỗng — kiểm tra structure")
            _log_sbv_alert(html_raw)
            return {"type": "STRUCTURE_CHANGED", "data": {}, "http_status": 200}

    except Exception as e:  # noqa: BLE001 - fallback ladder: SBV fail thì trả fallback cho nguồn VietnamBiz
        logger.warning(f"SBV Playwright thất bại: {e}")
        return {"type": "NETWORK_BLOCKED", "data": {}, "http_status": None}


# ---------------------------------------------------------------------------
#  SOURCE 2 — VietnamBiz (SSG Next.js data, ON rate confirmed working)
# ---------------------------------------------------------------------------
def _try_vietnambiz() -> float | None:
    """Lấy Lãi suất liên ngân hàng _ON từ VietnamBiz."""
    try:
        resp = requests.get(
            VIETNAMBiz_RATES_URL,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        if resp.status_code != 200:
            return None
        items = resp.json().get("pageProps", {}).get("data", [])
        for item in items:
            title = item.get("title", "")
            if "Lãi suất liên ngân hàng _ON" in title or "Lai suat lien ngan hang _ON" in title:
                return _normalize(item.get("value"))
    except (requests.RequestException, OSError, ValueError, KeyError) as e:
        logger.warning(f"VietnamBiz API không khả dụng: {e}")
    return None


# ---------------------------------------------------------------------------
#  ON-DEMAND CHECK (Emergency Recall — only fires when stale + big buy signal)
# ---------------------------------------------------------------------------
RECALL_STATE_PATH = Path(__file__).resolve().parent.parent.parent.parent / "data" / "probe_cache" / "recall_state.json"


def _doc_cooldown() -> float:
    """Đọc epoch timestamp từ recall_state.json.
    Dọn dẹp file .tmp còn sót từ lần crash trước.
    Trả về 0.0 nếu không có / hỏng.
    """
    try:
        import json
        import os

        # Dọn file .tmp còn sót (OOM/Task Manager kill giữa chừng)
        tmp = RECALL_STATE_PATH.with_suffix(".tmp")
        if tmp.exists():
            try:
                with open(tmp, encoding="utf-8") as f:
                    json.load(f)
                # .tmp hợp lệ → replace vào file chính (phục hồi sau crash)
                os.replace(tmp, RECALL_STATE_PATH)
            except json.JSONDecodeError, ValueError, OSError:
                # .tmp hỏng → xóa, không dùng
                tmp.unlink(missing_ok=True)
        # Đọc file chính (không bao giờ corrupt nhờ os.replace)
        if RECALL_STATE_PATH.exists():
            with open(RECALL_STATE_PATH, encoding="utf-8") as f:
                return float(json.load(f).get("last_check_epoch", 0.0))
    except (json.JSONDecodeError, OSError, TypeError, ValueError, KeyError) as _e:
        logger.debug("Đọc recall_state.json thất bại (dùng 0.0): %s", _e)
    return 0.0


def _ghi_cooldown_atomic(epoch: float) -> bool:
    """Ghi epoch vào recall_state.json bằng atomic write (temp + os.replace).
    An toàn khi Ctrl+C/OOM giữa chừng — file gốc không bao giờ bị corrupt.
    os.replace = MoveFileEx(MOVEFILE_REPLACE_EXISTING) trên Windows.
    """
    try:
        import json
        import os

        tmp = RECALL_STATE_PATH.with_suffix(".tmp")
        RECALL_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"last_check_epoch": epoch}, f)
        os.replace(tmp, RECALL_STATE_PATH)
        return True
    except OSError, TypeError, ValueError:
        return False


def kiem_tra_sbv_theo_yeu_cau(min_interval_s: int = 1800) -> dict:
    """On-demand interbank check, gated by persistent cooldown (disk-based).

    Chỉ chạy Playwright nếu đã qua ít nhất `min_interval_s` giây kể từ lần
    kiểm tra on-demand trước. Cooldown được ghi atomic vào
    `backend/data/probe_cache/recall_state.json` nên tồn tại qua nhiều CLI invocation.

    Args:
        min_interval_s: thời gian tối thiểu giữa các lần chạy (mặc định 1800s = 30 phút).

    Returns:
        dict with:
          - scraped: True/False (có cào mới hay dùng cache)
          - ON: float or None
          - signal: 'SHOCK' if ON>10, 'ELEVATED' if ON>5, 'NORMAL' otherwise
          - cooldown_hit: True nếu bỏ qua vì cooldown
          - sbv_type: 'SUCCESS' | 'STRUCTURE_CHANGED' | 'NETWORK_BLOCKED' | None
          - sbv_http_status: HTTP status code or None
    """
    import time

    now = time.time()
    kq = {"scraped": False, "ON": None, "signal": "NORMAL", "cooldown_hit": False, "sbv_type": None, "sbv_http_status": None}

    # --- Kiểm tra cooldown từ disk ---
    last_check = _doc_cooldown()
    if last_check > 0 and now - last_check < min_interval_s:
        val, dt = _get_latest_from_db("INTERBANK_ON")
        if val is not None:
            kq["ON"] = val
            kq["signal"] = "SHOCK" if val > 10 else ("ELEVATED" if val > 5 else "NORMAL")
        kq["cooldown_hit"] = True
        logger.info("[ON-DEMAND] cooldown %ds, return cache ON=%.2f", int(now - last_check), kq.get("ON"))
        return kq

    # --- Cào mới ---
    sbv_result = _try_sbv()
    _ghi_cooldown_atomic(now)

    rates = sbv_result.get("data", {})
    on = rates.get("ON")
    kq["scraped"] = True
    kq["ON"] = on
    kq["sbv_type"] = sbv_result.get("type", "UNKNOWN")
    kq["sbv_http_status"] = sbv_result.get("http_status")

    if on is not None:
        kq["signal"] = "SHOCK" if on > 10 else ("ELEVATED" if on > 5 else "NORMAL")
        logger.info("[ON-DEMAND] SBV scrape OK: ON=%.2f%%, signal=%s", on, kq["signal"])
    else:
        logger.warning("[ON-DEMAND] SBV scrape failed (ON=None)")
        val, _ = _get_latest_from_db("INTERBANK_ON")
        if val is not None:
            kq["ON"] = val
            kq["signal"] = "SHOCK" if val > 10 else ("ELEVATED" if val > 5 else "NORMAL")

    return kq


# ---------------------------------------------------------------------------
#  ORCHESTRATOR
# ---------------------------------------------------------------------------
def refresh_interbank_rate() -> bool:
    """Pipeline 2 tầng: SBV (Playwright) → VietnamBiz.

    Returns:
        True nếu seed được ít nhất INTERBANK_ON, False nếu hoàn toàn thất bại.
    """
    today = datetime.now().strftime("%Y-%m-%d")

    # --- Log độ trễ dữ liệu hiện tại ---
    for var in INTERBANK_VARIABLES:
        val, dt = _get_latest_from_db(var)
        if val is not None:
            try:
                days_old = (datetime.now() - datetime.strptime(dt, "%Y-%m-%d")).days
                logger.info(f"{var} hiện tại: {val} (từ {dt}, {days_old} ngày trước)")
            except Exception:  # noqa: BLE001 - batch isolation: 1 biến ngày hỏng không dừng toàn bộ
                logger.debug("Không tính được độ trễ cho %s (bỏ qua)", var)

    # --- Tầng 1: SBV website (Playwright) ---
    sbv_result = _try_sbv()
    rates = sbv_result.get("data", {})
    source_tag = "SBV"

    # Nếu cấu trúc SBV thay đổi, cảnh báo đã được ghi bởi _try_sbv()
    if sbv_result.get("type") == "STRUCTURE_CHANGED":
        logger.critical("SBV STRUCTURE CHANGED — sensor blind. Alert file written.")

    # --- Tầng 2: VietnamBiz (ON rate fallback) ---
    if not rates.get("ON"):
        on_rate = _try_vietnambiz()
        if on_rate is not None:
            rates["ON"] = on_rate
            source_tag = "VietnamBiz"
            logger.info(f"FALLBACK → VietnamBiz: ON = {on_rate}")

    # --- Kiểm tra: ít nhất ON phải có ---
    if rates.get("ON") is None:
        logger.warning("Tất cả nguồn đều fail — giữ dữ liệu cũ.")
        return False

    # --- Ghi DB ---
    mapping = {
        "INTERBANK_ON": rates.get("ON"),
        "INTERBANK_1W": rates.get("1W"),
        "INTERBANK_2W": rates.get("2W"),
        "INTERBANK_1M": rates.get("1M"),
        "INTERBANK_3M": rates.get("3M"),
        "INTERBANK_6M": rates.get("6M"),
        "INTERBANK_9M": rates.get("9M"),
    }

    rows = []
    for var, val in mapping.items():
        if val is not None:
            rows.append({"variable": var, "date": today, "value": val})

    if not rows:
        return False

    df = pd.DataFrame(rows)
    try:
        with get_connection() as conn:
            # MERGE STRATEGY: xóa rows hiện tại cho (date, variable) trùng
            # để tránh duplicate khi bootstrap đã có dữ liệu lịch sử
            for row in rows:
                conn.execute(
                    "DELETE FROM macro_history WHERE date = ? AND variable = ?",
                    (row["date"], row["variable"]),
                )
            save_data_upsert("macro_history", df, conn)
        for row in rows:
            logger.info(f"Đã cập nhật {row['variable']}: {row['value']}% (nguồn: {source_tag})")
        return True
    except (sqlite3.Error, TypeError, ValueError, KeyError, IndexError) as e:
        logger.error(f"Seed interbank rates thất bại: {e}")
        return False
