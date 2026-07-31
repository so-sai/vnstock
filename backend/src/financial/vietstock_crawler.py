"""vietstock_crawler.py — Vietstock Finance BCTC crawler (summary, free tier).

WHY: CafeF Bank API (BHoSoCongTy) chỉ trả BCTC Tóm tắt 17 rows → BCM thiếu
CFO/nợ vay/chi phí lãi vay. Vietstock Finance là nguồn dữ liệu độc lập thứ 4
(sau VNDirect/TCBS/CafeF), dùng cho kiểm tra chéo (cross-check).

DISCOVERY (2026-07-31):
- Playwright MUST use channel="chrome" (system Chrome). Bundled chromium-1200
  mismatches playwright 1.61.0 expecting channel 1228 → launch fail.
- Trang: https://finance.vietstock.vn/{SYM}/tai-chinh.htm — render được bằng
  Playwright Windows Native (sync_api, channel="chrome"). Có form input __RequestVerificationToken.
- FREE ENDPOINT 1: POST /data/financeinfo (Code, Page, PageSize=4, ReportTermType=2,
  ReportType=BCTQ, Unit=1, __RequestVerificationToken) → JSON:
    [periods, {sections: [rows with Value1..Value4]}, audited, united]
  - Free tier: 4 quý gần nhất, KQKD 5 dòng + CDKT 6 dòng + CSTC 6 dòng.
  - Value{i} tương ứng period có Row == i (Row=1 mới nhất, Row=4 cũ nhất).
- FREE ENDPOINT 2 (BCTT tab): POST /data/BCTT_GetListReportData (StockCode,
  UnitedId=-1, AuditedStatusId=-1, Unit=1000000000, IsNamDuongLich=false,
  PeriodType=QUY, SortTimeType=Time_ASC, __RequestVerificationToken) → 38 periods
  (2013Q2→2026Q2). Sau đó POST /data/GetReportDataDetailValue_BCTT_ByReportDataIds
  với listReportDataIds → 46 norms × 9 periods (capped at 9).
  → Bao gồm CASH_EQUIV, RECEIVABLES, INVENTORY, LONG_TERM_DEBT, COGS — quan trọng
    cho BCM health engine.
- PAYWALL endpoints (KHÔNG free): CDKT_GetListReportData / KQKD_GetListReportData /
  LCTT_GetListReportData → {"error":{"ErrorCode":"RequestUpgradeAccount_Permission"}};
  CSTC_GetListTerms → Package_Permission. BCTC chi tiết 37 dòng → cần VietstockPro.
- Anti-bot: cần session cookie (ASP.NET_SessionId) + CSRF token lấy từ form input
  sau khi render trang. POST qua page.evaluate(fetch) để giữ cookie.

METRIC MAPPING (Vietstock → PTCK STANDARD_METRICS):
  financeinfo: Doanh thu thuần→REVENUE | Lợi nhuận gộp→GROSS_PROFIT
               LN thuần từ HĐKD→EBIT | LNST thu nhập DN→NET_INCOME
               Tài sản ngắn hạn→CURRENT_ASSETS | Tổng tài sản→TOTAL_ASSETS
               Nợ phải trả→TOTAL_LIABILITIES | Nợ ngắn hạn→CURRENT_LIAB
               Vốn chủ sở hữu→TOTAL_EQUITY | EPS 4 quý→EPS | BVPS cơ bản→BOOK_VALUE_PS
  BCTT:        Doanh thu thuần→REVENUE | Giá vốn→COGS | Lợi nhuận gộp→GROSS_PROFIT
               LN thuần HĐKD→EBIT | LNST→NET_INCOME | EPS→EPS
               Tiền & tương đương→CASH_EQUIV | Phải thu→RECEIVABLES | Tồn kho→INVENTORY
               Tài sản ngắn hạn→CURRENT_ASSETS | Tổng tài sản→TOTAL_ASSETS
               Nợ phải trả→TOTAL_LIABILITIES | Nợ ngắn hạn→CURRENT_LIAB
               Nợ dài hạn→LONG_TERM_DEBT | Vốn chủ sở hữu→TOTAL_EQUITY
               BVPS→BOOK_VALUE_PS | P/E | P/B | ROEA | ROAA

USAGE:
    from src.financial.vietstock_crawler import VietstockCrawler
    periods = VietstockCrawler().fetch_summary("BCM")  # merges financeinfo + BCTT
    # hoặc parser thuần (test-friendly):
    periods = VietstockCrawler.parse_financeinfo_payload(payload)
    periods = VietstockCrawler.parse_bctt_detail_payload(payload, periods)
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

# BCTT norms mapping — 46 norms from GetListReportNorm_BCTT_ByStockCode
# ReportTypeCode: KQ (Kết quả kinh doanh), CD (Cân đối), CSTC (Chỉ số tài chính)
BCTT_METRIC_MAP = {
    # KQ — Income Statement
    2216: "REVENUE",         # Doanh thu thuần về bán hàng và cung cấp dịch vụ
    2207: "COGS",            # Giá vốn hàng bán
    2217: "GROSS_PROFIT",    # Lợi nhuận gộp về bán hàng và cung cấp dịch vụ
    2221: "FINANCIAL_REVENUE",  # Doanh thu hoạt động tài chính (không map STANDARD)
    2222: "FINANCIAL_COST",     # Chi phí tài chính (không map STANDARD)
    2227: "SELLING_EXPENSE",    # Chi phí bán hàng (không map STANDARD)
    2224: "ADMIN_EXPENSE",      # Chi phí quản lý doanh nghiệp (không map STANDARD)
    2208: "EBIT",               # Lợi nhuận thuần từ hoạt động kinh doanh
    2209: "OTHER_INCOME",       # Lợi nhuận khác (không map STANDARD)
    2210: "JOINT_VENTURE_INCOME",  # Phần lợi nhuận/lỗ từ công ty liên kết (không map STANDARD)
    2211: "PRE_TAX_INCOME",     # Tổng lợi nhuận kế toán trước thuế (không map STANDARD)
    2212: "NET_INCOME",         # Lợi nhuận sau thuế thu nhập doanh nghiệp
    2214: "NET_INCOME_PARENT",  # Lợi nhuận sau thuế của cổ đông Công ty mẹ (không map STANDARD)
    2215: "EPS",                # Lãi cơ bản trên cổ phiếu (VNÐ)
    # CD — Balance Sheet
    3000: "CURRENT_ASSETS",     # Tài sản ngắn hạn
    3003: "CASH_EQUIV",         # Tiền và các khoản tương đương tiền
    3004: "SHORT_TERM_INVEST",  # Các khoản đầu tư tài chính ngắn hạn (không map STANDARD)
    3005: "RECEIVABLES",        # Các khoản phải thu ngắn hạn
    3006: "INVENTORY",          # Hàng tồn kho
    3007: "SHORT_TERM_ASSET_OTHER",  # Tài sản ngắn hạn khác (không map STANDARD)
    3001: "LONG_TERM_ASSET",    # Tài sản dài hạn (không map STANDARD)
    3009: "FIXED_ASSET",        # Tài sản cố định (không map STANDARD)
    3010: "INVESTMENT_REAL_ESTATE",  # Bất động sản đầu tư (không map STANDARD)
    3011: "LONG_TERM_INVEST",   # Các khoản đầu tư tài chính dài hạn (không map STANDARD)
    2996: "TOTAL_ASSETS",       # Tổng cộng tài sản
    2997: "TOTAL_LIABILITIES",  # Nợ phải trả
    3014: "CURRENT_LIAB",       # Nợ ngắn hạn
    3017: "LONG_TERM_DEBT",     # Nợ dài hạn
    2998: "TOTAL_EQUITY",       # Vốn chủ sở hữu
    3063: "PAID_IN_CAPITAL",    # Vốn đầu tư của chủ sở hữu (không map STANDARD)
    3064: "SHARE_PREMIUM",      # Thặng dư vốn cổ phần (không map STANDARD)
    3072: "UNALLOCATED_EARNINGS",  # Lợi nhuận sau thuế chưa phân phối (không map STANDARD)
    3002: "MINORITY_INTEREST",  # Lợi ích của cổ đông thiểu số (không map STANDARD)
    2999: "TOTAL_SOURCE",       # Tổng cộng nguồn vốn (không map STANDARD)
    # CSTC — Financial Indicators
    53: "EPS",                  # Thu nhập trên mỗi cổ phần của 4 quý gần nhất
    54: "BOOK_VALUE_PS",        # Giá trị sổ sách của cổ phiếu
    55: "PE_RATIO",             # Chỉ số giá thị trường trên thu nhập (P/E)
    57: "PB_RATIO",             # Chỉ số giá thị trường trên giá trị sổ sách (P/B)
    41: "GROSS_MARGIN",         # Tỷ suất lợi nhuận gộp biên
    44: "NET_MARGIN",           # Tỷ suất sinh lợi trên doanh thu thuần
    45: "ROEA",                 # Tỷ suất lợi nhuận trên vốn chủ sở hữu bình quân
    47: "ROAA",                 # Tỷ suất sinh lợi trên tổng tài sản bình quân
    4: "CURRENT_RATIO",         # Tỷ số thanh toán hiện hành (ngắn hạn)
    5: "INTEREST_COVERAGE",     # Khả năng thanh toán lãi vay
    8: "DEBT_TO_ASSET",         # Tỷ số Nợ trên Tổng tài sản
    11: "DEBT_TO_EQUITY",       # Tỷ số Nợ vay trên Vốn chủ sở hữu
}

# Subset of BCTT norms that map to STANDARD_METRICS (for quick lookup)
BCTT_STANDARD_METRICS = {
    2216: "REVENUE",
    2207: "COGS",
    2217: "GROSS_PROFIT",
    2208: "EBIT",
    2212: "NET_INCOME",
    2215: "EPS",
    3000: "CURRENT_ASSETS",
    3003: "CASH_EQUIV",
    3005: "RECEIVABLES",
    3006: "INVENTORY",
    2996: "TOTAL_ASSETS",
    2997: "TOTAL_LIABILITIES",
    3014: "CURRENT_LIAB",
    3017: "LONG_TERM_DEBT",
    2998: "TOTAL_EQUITY",
    54: "BOOK_VALUE_PS",
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

# BCTT endpoint params (must be exact for free access)
BCTT_REPORT_DATA_PARAMS = {
    "StockCode": "{symbol}",
    "UnitedId": "-1",
    "AuditedStatusId": "-1",
    "Unit": "1000000000",
    "IsNamDuongLich": "false",
    "PeriodType": "QUY",
    "SortTimeType": "Time_ASC",
}


class VietstockCrawler:
    """Crawler BCTC summary (free tier) từ Vietstock Finance.

    Dùng Playwright Windows Native (channel="chrome") để render trang báo cáo
    → lấy session + CSRF token → POST các endpoint free qua page.evaluate(fetch).
    Trả về list period dict dạng giống cafef_crawler: _fiscal_year,
    _fiscal_quarter, _entity_type + metric keys.

    Hai nguồn free:
    1. /data/financeinfo ReportType=BCTQ → 4 quý, 17 rows metric
    2. BCTT tab (GetListReportNorm_BCTT + BCTT_GetListReportData +
       GetReportDataDetailValue_BCTT_ByReportDataIds) → 9 quý, 46 norms
       (bao gồm CASH_EQUIV, RECEIVABLES, INVENTORY, LONG_TERM_DEBT, COGS)
    """

    def __init__(self, entity_type: str = "STANDARD"):
        self.entity_type = entity_type.upper()

    # ── Playwright fetch ─────────────────────────────────────────────────
    def fetch_summary(self, symbol: str, max_quarters: int = 4,
                      timeout_ms: int = 60000) -> List[Dict]:
        """Render trang tài chính Vietstock, merge financeinfo + BCTT.

        Priority: BCTT (9 quý, 46 norms) > financeinfo (4 quý, 17 rows).
        BCTT data overrides financeinfo cho cùng period.

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
                    browser.close()
                    return []

                token = page.evaluate(
                    "document.querySelector('input[name=__RequestVerificationToken]')"
                    "?.value || ''"
                )
                if not token:
                    logger.warning("Vietstock không tìm thấy CSRF token")
                    browser.close()
                    return []

                # ── Source 1: /data/financeinfo BCTQ (4 quý) ──
                periods = self._fetch_financeinfo(page, symbol, token,
                                                  max_quarters)

                # ── Source 2: BCTT tab (9 quý, 46 norms) ──
                bctt_periods = self._fetch_bctt_summary(page, symbol, token)

                browser.close()
        except Exception as e:
            logger.warning(f"Vietstock crawl thất bại: {e}")
            return []

        # Merge: BCTT overrides financeinfo cho cùng period
        merged = self._merge_periods(periods, bctt_periods)
        logger.info(f"Vietstock {symbol}: {len(merged)} periods merged "
                    f"(financeinfo={len(periods)}, BCTT={len(bctt_periods)})")
        return merged

    def _fetch_financeinfo(self, page, symbol, token, max_quarters):
        """GET /data/financeinfo ReportType=BCTQ → 4 quý, 17 rows."""
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
        if not body:
            logger.warning("Vietstock financeinfo trả về rỗng")
            return []
        try:
            payload = json.loads(body)
        except (ValueError, TypeError) as e:
            logger.warning(f"Vietstock financeinfo không phải JSON: {e}")
            return []
        return self.parse_financeinfo_payload(payload, self.entity_type)

    def _fetch_bctt_summary(self, page, symbol, token):
        """BCTT tab: GetListReportNorm_BCTT + BCTT_GetListReportData +
        GetReportDataDetailValue_BCTT_ByReportDataIds → 9 quý, 46 norms.

        Returns:
            List[Dict]: period dict có _fiscal_year/_fiscal_quarter + metric keys.
        """
        # Step 1: Get periods
        body = page.evaluate(
            """async (args) => {
                const [sym, token] = args;
                const params = new URLSearchParams({
                    StockCode: sym, UnitedId: '-1', AuditedStatusId: '-1',
                    Unit: '1000000000', IsNamDuongLich: 'false',
                    PeriodType: 'QUY', SortTimeType: 'Time_ASC',
                    __RequestVerificationToken: token,
                });
                const r = await fetch('/data/BCTT_GetListReportData', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                        'X-Requested-With': 'XMLHttpRequest',
                    },
                    body: params.toString(),
                });
                return await r.text();
            }""",
            [symbol, token],
        )
        if not body:
            logger.warning("Vietstock BCTT_GetListReportData trả về rỗng")
            return []
        try:
            data = json.loads(body)
        except (ValueError, TypeError):
            return []
        if isinstance(data, dict) and "error" in data:
            logger.warning(f"Vietstock BCTT_GetListReportData lỗi: {data['error']}")
            return []
        periods_raw = data.get("data", []) if isinstance(data, dict) else []
        if not periods_raw:
            return []

        # Step 2: Take latest 9, sort DESC (newest first)
        latest = periods_raw[-9:]
        latest_desc = list(reversed(latest))

        # Step 3: Build listReportDataIds for detail values
        ids_param = []
        for i, p in enumerate(latest_desc):
            ids_param.append(f"listReportDataIds%5B{i}%5D%5BIndex%5D={i}")
            ids_param.append(f"listReportDataIds%5B{i}%5D%5BReportDataId%5D={p['ReportDataID']}")
            ids_param.append(f"listReportDataIds%5B{i}%5D%5BIsShowData%5D=true")
            ids_param.append(f"listReportDataIds%5B{i}%5D%5BRowNumber%5D={p['RowNumber']}")
            ids_param.append(f"listReportDataIds%5B{i}%5D%5BYearPeriod%5D={p['YearPeriod']}")
            ids_param.append(f"listReportDataIds%5B{i}%5D%5BTotalCount%5D={len(periods_raw)}")
            ids_param.append(f"listReportDataIds%5B{i}%5D%5BSortTimeType%5D=Time_ASC")
        base = f"StockCode={symbol}&Unit=1000000000&TypeCompare=1&"
        post = base + "&".join(ids_param) + f"&__RequestVerificationToken={token}"

        # Step 4: Get detail values
        body2 = page.evaluate(
            """async (post) => {
                const r = await fetch('/data/GetReportDataDetailValue_BCTT_ByReportDataIds', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                        'X-Requested-With': 'XMLHttpRequest',
                    },
                    body: post,
                });
                return await r.text();
            }""",
            post,
        )
        if not body2:
            logger.warning("Vietstock BCTT_GetReportDataDetailValue trả về rỗng")
            return []
        try:
            vals = json.loads(body2)
        except (ValueError, TypeError):
            return []
        if isinstance(vals, dict) and "error" in vals:
            logger.warning(f"Vietstock BCTT detail lỗi: {vals['error']}")
            return []
        norm_rows = vals.get("data", []) if isinstance(vals, dict) else []
        if not norm_rows:
            return []

        # Step 5: Parse — map Value1..Value9 to periods (newest first)
        return self.parse_bctt_detail_payload(norm_rows, latest_desc, self.entity_type)

    @staticmethod
    def _merge_periods(financeinfo_periods, bctt_periods):
        """Merge financeinfo + BCTT. BCTT overrides financeinfo cho cùng period."""
        merged = {}
        for p in financeinfo_periods:
            key = (p["_fiscal_year"], p["_fiscal_quarter"])
            merged[key] = dict(p)
        for p in bctt_periods:
            key = (p["_fiscal_year"], p["_fiscal_quarter"])
            merged[key] = dict(p)  # BCTT overrides
        result = list(merged.values())
        result.sort(key=lambda d: (d["_fiscal_year"], d["_fiscal_quarter"]))
        # Drop periods with no metrics
        result = [p for p in result if any(not k.startswith("_") for k in p)]
        return result

    # ── Pure parser: financeinfo ─────────────────────────────────────────
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

    # ── Pure parser: BCTT detail ─────────────────────────────────────────
    @staticmethod
    def parse_bctt_detail_payload(norm_rows, periods_desc,
                                  entity_type: str = "STANDARD") -> List[Dict]:
        """Parse response của GET /data/GetReportDataDetailValue_BCTT_ByReportDataIds.

        Structure:
            data = list of 46 norm objects, each with ReportNormId, ReportTypeCode,
                   Value1..ValueN (N = number of periods requested, capped at 9).
            periods_desc = list of period dicts (newest first), each with
                           ReportDataID, YearPeriod, ReportTermID, PeriodBegin,
                           PeriodEnd, UnitedName.

        Value{i} tương ứng period_desc[i] (i=0 mới nhất).

        Args:
            norm_rows: JSON response data array (list of norm dicts).
            periods_desc: period list sorted newest first.
            entity_type: "STANDARD" hoặc "BANK".

        Returns:
            List[Dict]: period dict có _fiscal_year/_fiscal_quarter/_entity_type
            + metric keys (chỉ các metric có giá trị). Bỏ period trống.
        """
        if not norm_rows or not periods_desc:
            return []

        # Index periods by index (0 = newest)
        period_by_idx: Dict[int, Dict] = {}
        for i, p in enumerate(periods_desc):
            if not isinstance(p, dict):
                continue
            year = p.get("YearPeriod")
            # ReportTermID: 2=Q1, 3=Q2, 4=Q3, 5=Q4
            tid = p.get("ReportTermID")
            q = None
            if isinstance(tid, int) and 2 <= tid <= 5:
                q = tid - 1
            if year and q and q >= 1 and q <= 4:
                period_by_idx[i] = {
                    "_fiscal_year": int(year),
                    "_fiscal_quarter": q,
                    "_entity_type": entity_type.upper(),
                }

        if not period_by_idx:
            return []

        # Map norms to periods
        for r in norm_rows:
            if not isinstance(r, dict):
                continue
            norm_id = r.get("ReportNormId")
            metric = BCTT_STANDARD_METRICS.get(norm_id)
            if not metric:
                continue
            for i in range(1, 10):  # Value1..Value9
                period = period_by_idx.get(i - 1)  # Value1 → idx 0
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
        for i in sorted(period_by_idx.keys()):
            period = period_by_idx[i]
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
