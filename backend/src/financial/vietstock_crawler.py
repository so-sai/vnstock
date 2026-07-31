"""vietstock_crawler.py — Vietstock Finance BCTC crawler (summary, free tier).

WHY: CafeF Bank API (BHoSoCongTy) chỉ trả BCTC Tóm tắt 17 rows → BCM thiếu
CFO/nợ vay/chi phí lãi vay. Vietstock Finance là nguồn dữ liệu độc lập thứ 4
(sau VNDirect/TCBS/CafeF), dùng cho kiểm tra chéo (cross-check).

DISCOVERY (2026-07-31):
- Playwright MUST use channel="chrome" (system Chrome). Bundled chromium-1200
  mismatches playwright 1.61.0 expecting channel 1228 → launch fail.
- Trang: https://finance.vietstock.vn/{SYM}/tai-chinh.htm — render được bằng
  Playwright Windows Native (sync_api, channel="chrome"). Có form input __RequestVerificationToken.
- Free endpoint: POST /data/financeinfo (Code, Page, PageSize=4, ReportTermType=2,
  ReportType=BCTQ, Unit=1, __RequestVerificationToken) → JSON:
    [periods, {sections: [rows with Value1..Value4]}, audited, united]
  - Free tier: 4 quý gần nhất, KQKD 5 dòng + CDKT 6 dòng + CSTC 6 dòng.
  - Value{i} tương ứng period có Row == i (Row=1 mới nhất, Row=4 cũ nhất).
- PAYWALL endpoints (KHÔNG free): CDKT_GetListReportData / KQKD_GetListReportData /
  LCTT_GetListReportData → {"error":{"ErrorCode":"RequestUpgradeAccount_Permission"}};
  CSTC_GetListTerms → Package_Permission. BCTC chi tiết 37 dòng → cần VietstockPro.
- Anti-bot: cần session cookie (ASP.NET_SessionId) + CSRF token lấy từ form input
  sau khi render trang. POST qua page.evaluate(fetch) để giữ cookie.

METRIC MAPPING (Vietstock → PTCK STANDARD_METRICS):
  KQKD:  Doanh thu thuần → REVENUE | Lợi nhuận gộp → GROSS_PROFIT
         LN thuần từ HĐKD → EBIT | LNST thu nhập DN → NET_INCOME
  CDKT:  Tài sản ngắn hạn → CURRENT_ASSETS | Tổng tài sản → TOTAL_ASSETS
         Nợ phải trả → TOTAL_LIABILITIES | Nợ ngắn hạn → CURRENT_LIAB
         Vốn chủ sở hữu → TOTAL_EQUITY
  CSTC:  EPS 4 quý → EPS | BVPS cơ bản → BOOK_VALUE_PS

Không có trên free tier: CFO, CFI, CFF, CAPEX, FCF, TOTAL_DEBT, SHORT/LONG_TERM_DEBT,
CASH_EQUIV, RECEIVABLES, INVENTORY, INTEREST_EXPENSE, COGS, EBITDA.

USAGE:
    from src.financial.vietstock_crawler import VietstockCrawler
    periods = VietstockCrawler().fetch_summary("BCM")
    # hoặc parser thuần (test-friendly):
    periods = VietstockCrawler.parse_financeinfo_payload(payload)
"""

import json
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

VIETSTOCK_BASE_URL = "https://finance.vietstock.vn"
VIETSTOCK_REPORT_URL = "{base}/{sym}/tai-chinh.htm"
VIETSTOCK_FININFO_URL = "{base}/data/financeinfo"

VIETSTOCK_METRIC_MAP = {
    # Income Statement
    "Doanh thu thuần": "REVENUE",
    "Lợi nhuận gộp": "GROSS_PROFIT",
    "LN thuần từ HĐKD": "EBIT",
    "LNST thu nhập DN": "NET_INCOME",
    # Balance Sheet
    "Tài sản ngắn hạn": "CURRENT_ASSETS",
    "Tổng tài sản": "TOTAL_ASSETS",
    "Nợ phải trả": "TOTAL_LIABILITIES",
    "Nợ ngắn hạn": "CURRENT_LIAB",
    "Vốn chủ sở hữu": "TOTAL_EQUITY",
    # Per Share
    "EPS 4 quý": "EPS",
    "BVPS cơ bản": "BOOK_VALUE_PS",
}

WINDOWS_LAUNCH_FLAGS = [
    "--disable-gpu",
    "--no-sandbox",
    "--disable-accelerated-2d-canvas",
    "--no-first-run",
    "--disable-blink-features=AutomationControlled",
]
BLOCKED_RESOURCE_TYPES = {"image", "stylesheet", "font", "media"}

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


class VietstockCrawler:
    """Crawler BCTC summary (free tier) từ Vietstock Finance.

    Dùng Playwright Windows Native (channel="chrome") để render trang báo cáo
    → lấy session + CSRF token → POST /data/financeinfo qua page.evaluate(fetch).
    Trả về list period dict dạng giống cafef_crawler: _fiscal_year,
    _fiscal_quarter, _entity_type + metric keys (REVENUE, TOTAL_ASSETS, ...).
    """

    def __init__(self, entity_type: str = "STANDARD"):
        self.entity_type = entity_type.upper()

    # ── Playwright fetch ─────────────────────────────────────────────────
    def fetch_summary(self, symbol: str, max_quarters: int = 4,
                      timeout_ms: int = 60000) -> List[Dict]:
        """Render trang tài chính Vietstock, POST financeinfo, parse JSON.

        Returns:
            List[Dict]: các period đã map metric, rỗng nếu thất bại.
        """
        symbol = symbol.upper()
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.warning("Playwright chưa cài — bỏ qua Vietstock crawler")
            return []

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True, channel="chrome", args=WINDOWS_LAUNCH_FLAGS,
                )
                ctx = browser.new_context(
                    viewport={"width": 1920, "height": 1080},
                    user_agent=DEFAULT_USER_AGENT,
                    locale="vi-VN",
                )
                ctx.add_init_script(
                    "Object.defineProperty(navigator, 'webdriver', "
                    "{ get: () => undefined });"
                )
                page = ctx.new_page()

                def _route(route):
                    rtype = route.request.resource_type
                    if rtype in BLOCKED_RESOURCE_TYPES:
                        route.abort()
                    else:
                        route.continue_()

                page.route("**/*", _route)

                url = VIETSTOCK_REPORT_URL.format(
                    base=VIETSTOCK_BASE_URL, sym=symbol,
                )
                resp = page.goto(url, wait_until="domcontentloaded",
                                 timeout=timeout_ms)
                page.wait_for_timeout(3000)
                if resp is None or resp.status != 200:
                    logger.warning(f"Vietstock goto {url} → "
                                   f"status {resp.status if resp else 'None'}")
                    return []

                token = page.evaluate(
                    "document.querySelector('input[name=__RequestVerificationToken]')"
                    "?.value || ''"
                )
                if not token:
                    logger.warning("Vietstock không tìm thấy CSRF token")
                    return []

                body = page.evaluate(
                    """async (args) => {
                        const [sym, token, maxQ] = args;
                        const params = new URLSearchParams({
                            Code: sym, Page: '1', PageSize: String(maxQ),
                            ReportTermType: '2', ReportType: 'BCTQ',
                            Unit: '1', __RequestVerificationToken: token,
                        });
                        const r = await fetch('/data/financeinfo', {
                            method: 'POST',
                            headers: {
                                'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                                'X-Requested-With': 'XMLHttpRequest',
                            },
                            body: params.toString(),
                        });
                        return await r.text();
                    }""",
                    [symbol, token, max_quarters],
                )
                browser.close()
        except Exception as e:
            logger.warning(f"Vietstock crawl thất bại: {e}")
            return []

        if not body:
            logger.warning("Vietstock financeinfo trả về rỗng")
            return []

        try:
            payload = json.loads(body)
        except (ValueError, TypeError) as e:
            logger.warning(f"Vietstock financeinfo không phải JSON: {e}")
            return []

        periods = self.parse_financeinfo_payload(payload, self.entity_type)
        logger.info(f"Vietstock {symbol}: {len(periods)} periods parsed")
        return periods

    # ── Pure parser (test-friendly, deterministic) ───────────────────────
    @staticmethod
    def parse_financeinfo_payload(payload, entity_type: str = "STANDARD") -> List[Dict]:
        """Parse response của POST /data/financeinfo.

        Structure:
            payload[0] = list period (Row, YearPeriod, TermCode, PeriodBegin, ...)
            payload[1] = dict {section_name: [row {Name, Value1..Value4, ...}]}

        Value{i} ứng với period có Row == i (Row=1 mới nhất).

        Args:
            payload: JSON response (list 4 phần tử).
            entity_type: "STANDARD" hoặc "BANK".

        Returns:
            List[Dict]: period dict có _fiscal_year/_fiscal_quarter/_entity_type
            + metric keys (chỉ các metric có giá trị). Bỏ period trống.
        """
        if not isinstance(payload, list) or len(payload) < 2:
            return []
        periods = payload[0]
        sections = payload[1]
        if not isinstance(periods, list) or not isinstance(sections, dict):
            return []

        # Index period theo Row
        period_by_row: Dict[int, Dict] = {}
        for p in periods:
            if not isinstance(p, dict):
                continue
            row = p.get("Row")
            year = p.get("YearPeriod")
            term = str(p.get("TermCode") or "")
            q = None
            if term.startswith("Q") and term[1:].isdigit():
                q = int(term[1:])
            if row is None or not year or not q:
                continue
            period_by_row[int(row)] = {
                "_fiscal_year": int(year),
                "_fiscal_quarter": q,
                "_entity_type": entity_type.upper(),
            }

        if not period_by_row:
            return []

        # Gom metric từ các section
        for section_name, rows in sections.items():
            if not isinstance(rows, list):
                continue
            for r in rows:
                if not isinstance(r, dict):
                    continue
                name = str(r.get("Name") or "").strip()
                metric = VIETSTOCK_METRIC_MAP.get(name)
                if not metric:
                    continue
                for i in range(1, 5):
                    period = period_by_row.get(i)
                    if period is None:
                        continue
                    val = r.get(f"Value{i}")
                    if val is None:
                        continue
                    try:
                        period[metric] = float(val)
                    except (TypeError, ValueError):
                        continue

        # Chỉ giữ period có ít nhất 1 metric thật
        result = []
        for i in sorted(period_by_row.keys()):
            period = period_by_row[i]
            if any(not k.startswith("_") for k in period):
                result.append(period)
        return result

    # ── Convenience: chuyển list period sang dạng DB-ready ──────────────
    @staticmethod
    def to_db_periods(periods: List[Dict], entity_type: str = "STANDARD") -> List[Dict]:
        """Clone periods (tránh mutate), đảm bảo _entity_type nhất quán."""
        result = []
        for p in periods:
            clone = dict(p)
            clone["_entity_type"] = entity_type.upper()
            result.append(clone)
        return result
