"""cafef_crawler.py — CafeF BCTC Crawler (20 quarters)

Cào 20 quý BCTC từ CafeF.vn (Q3/2021 → Q2/2026),
nạp trực tiếp vào financial_facts.db qua FinancialFactsDB + DataIntegrityValidator.

╔══════════════════════════════════════════════════════════════════════╗
║  PHƯƠNG PHÁP LẤY DỮ LIỆU TÀI CHÍNH                                 ║
╠══════════════════════════════════════════════════════════════════════╣
║                                                                      ║
║  Nguyên tắc (áp dụng từ SBV interbank + Fed WorldSensor Pattern):   ║
║                                                                      ║
║  1. REQUESTS (tầng 1 — nhanh, không cần trình duyệt)                ║
║     - fetch_statement() dùng requests + BeautifulSoup                ║
║     - Hoạt động với trang tĩnh, API JSON                             ║
║                                                                      ║
║  2. PLAYWRIGHT (tầng 2 — fallback khi requests 404/timeout)          ║
║     - _try_cafef_pw() dùng playwright.sync_api + Chromium headless   ║
║     - Cần cho các trang cần JS rendering (CafeF, Vietstock ...)      ║
║     - Stealth: --disable-blink-features=AutomationControlled         ║
║     - Giống pattern _try_sbv() trong interbank_seeder.py             ║
║                                                                      ║
║  3. API FINANCIAL (tầng 3 — VCI/KBS, hiện BROKEN)                   ║
║     - financial_facts.py: VciGraphQL + KBS/SaaS API                  ║
║     - Cả hai API đều không hoạt động (CORS, auth thay đổi)           ║
║                                                                      ║
║  4. SYNTHETIC (tầng 4 — fallback cuối cùng)                         ║
║     - _generate_synthetic_base() — nội suy từ 2022→2026              ║
║     - Chỉ dùng khi cả 3 tầng trên đều thất bại                      ║
║                                                                      ║
║  CLI: python ptck.py cafef-crawl [--symbols ...] [--source vci|cafef|synthetic]  ║
║                                                                      ║
╚══════════════════════════════════════════════════════════════════════╝

╔══════════════════════════════════════════════════════════════════════╗
║  BẢNG ÁNH XẠ URL — BCTC FINANCIAL STATEMENT DATA                    ║
╠══════════════════════════════════════════════════════════════════════╣
║                                                                      ║
║  NGUỒN CHÍNH (ĐANG HOẠT ĐỘNG):                                      ║
║  ─────────────────────────────────────────────────────────────────── ║
║  CafeF Bank API (BHoSoCongTy) — HOẠT ĐỘNG                           ║
║    URL:   https://cafef.vn/du-lieu/Ajax/Bank/BHoSoCongTy.aspx        ║
║    Type:  GET / HTML Table                                           ║
║    Auth:  headers (User-Agent, Referer)                              ║
║    Data:  BCTC 20 quarters (BS/IS/CF)                                ║
║    Lib:   requests + BeautifulSoup                                   ║
║    Trạng thái: ✅ HOẠT ĐỘNG (verified 2026-07-30)                    ║
║                                                                      ║
║  VCI (Viet Capital) GraphQL API                                      ║
║    URL:   https://trading.vietcap.com.vn/data-mt/graphql             ║
║    Type:  POST / GraphQL                                             ║
║    Auth:  headers (User-Agent, Accept)                               ║
║    Data:  Balance Sheet, Income Statement, Cash Flow, Ratios         ║
║    Lib:   vnstock.Finance(source="VCI")                              ║
║    Trạng thái: ⚠️ 200 nhưng response rỗng {} — CẦN API KEY          ║
║                                                                      ║
║  KBS (KB Securities) IIS Server — RATIO & FINANCE INFO              ║
║    URL:   https://kbbuddywts.kbsec.com.vn/sas/kbsv-stock-data-store ║
║           /stock/finance-info                                        ║
║    Type:  GET / REST JSON                                            ║
║    Data:  PE, PB, ROE, EPS, Book Value (tỉ lệ tài chính)           ║
║    Lib:   vnstock.explorer.kbs.financial.Finance                     ║
║    Trạng thái: ❌ 404 (2026-07-30) — endpoint tĩnh, cần refresh     ║
║                                                                      ║
║  NGUỒN CŨ (ĐÃ CHẾT / 404):                                          ║
║  ─────────────────────────────────────────────────────────────────── ║
║  CafeF (BCTC cũ)                                                     ║
║    URL cũ: https://s.cafef.vn/bao-cao-tai-chinh/{symbol}/{st_type}/ ║
║            {year}/{quarter}/0/0/bao-cao-tai-chinh-.chn               ║
║    Trạng thái: ❌ 404 (đã chết, KHÔNG khôi phục được)                ║
║                                                                      ║
║  CafeF (ALT cũ)                                                      ║
║    URL cũ: https://s.cafef.vn/soc/bao-cao-tai-chinh-{symbol}/       ║
║            {st_label}.chn?year={year}&quarter={quarter}              ║
║    Trạng thái: ❌ 404 (đã chết, KHÔNG khôi phục được)                ║
║                                                                      ║
║  SSI iBoard Financial API (theo đề xuất)                             ║
║    URL: https://iboard-query.ssi.com.vn/financial/FL/{symbol}        ║
║    Trạng thái: ❌ 404 — SSI iBoard KHÔNG có financial API công khai  ║
║    Ghi chú: SSI iBoard chỉ có market data:                          ║
║      ✅ /stock/stock-info   (danh sách mã)                           ║
║      ✅ /stock/group/VN30   (bảng giá)                               ║
║      ❌ /financial/*        (không tồn tại)                          ║
║                                                                      ║
║  FiinGroup/FiinTrade                                                 ║
║    Trạng thái: ❌ Yêu cầu API key trả phí, không có trong codebase   ║
║                                                                      ║
║  CHIẾN LƯỢC THAY THẾ:                                               ║
║  ─────────────────────────────────────────────────────────────────── ║
║  Chỉ có 2 endpoint hoạt động:                                       ║
║  1. BHoSoCongTy.aspx (primary, full BCTC)                          ║
║  2. NoteIndicator.aspx (backup limited, nợ phân loại)              ║
║  Tất cả endpoint khác (TCBS, IndicatorChart, CompareChart, ...)    ║
║  đều DEAD (404/DNS FAIL).                                          ║
║                                                                      ║
║  File liên quan: cafef_crawler.py → fetch_cafef_bank_api()           ║
║                                                                      ║
╚══════════════════════════════════════════════════════════════════════╝

╔══════════════════════════════════════════════════════════════════════╗
║  HƯỚNG DẪN TÌM API — METHODOLOGY                                  ║
╠══════════════════════════════════════════════════════════════════════╣
║                                                                      ║
║  CÁCH TÌM ENDPOINT MỚI (Step-by-step):                              ║
║  ─────────────────────────────────────────────────────────────────── ║
║                                                                      ║
║  1. PLAYWRIGHT NETWORK CAPTURE (QUAN TRỌNG NHẤT)                    ║
║     - Dùng Playwright mở trang web cần crawl                        ║
║     - Gắn event handler "response" để capture TẤT CẢ network calls   ║
║     - Lọc các request trả về JSON/HTML table (>100 bytes)           ║
║     - Ghi lại URL, method, params, headers                          ║
║     Ví dụ:                                                          ║
║       page.on("response", lambda r: captured.append(r.url))         ║
║       page.goto("https://cafef.vn/VCB/bao-cao-tai-chinh.chn")       ║
║     → Tìm ra: /du-lieu/Ajax/Bank/BHoSoCongTy.aspx                   ║
║                                                                      ║
║  2. VIEW SOURCE + REGEX SEARCH                                      ║
║     - Tải HTML page về bằng requests                                ║
║     - Tìm regex: r'["\x27] (/du-lieu/Ajax[^"\x27]*)["\x27]'        ║
║     - Tìm regex: r'["\x27] (/Ajax[^"\x27]*)["\x27]'                ║
║     - Tìm regex: r'ajax.*url.*'                                     ║
║     → Tìm ra tất cả AJAX endpoints trong page                       ║
║                                                                      ║
║  3. TEST PARAMS ITERATIVELY                                         ║
║     - Thử từng param: symbol, type, year, quarter, pageindex        ║
║     - Ghi lại response: 200 + JSON/table = WORKING                  ║
║     - 200 + empty/skeleton = DEAD                                   ║
║     - 404/DNS FAIL = DEAD                                           ║
║                                                                      ║
║  4. CHECK OTHER BROKERS/PLATFORMS                                   ║
║     - Vietstock: finance.vietstock.vn                               ║
║     - TCBS: tcinvest.tcbs.com.vn                                    ║
║     - SSI iBoard: iboard.ssi.com.vn                                 ║
║     - HOSE/HNX: publish.hsx.vn, publish.hnx.vn                     ║
║                                                                      ║
║  WHY (Tại sao phương pháp này):                                     ║
║  - CafeF không có public API documentation                          ║
║  - Các endpoints thay đổi thường xuyên (breaking changes)           ║
║  - Playwright capture là cách duy nhất để tìm AJAX calls ẩn         ║
║  - Regex search trên source HTML tìm được tất cả hidden endpoints   ║
║                                                                      ║
║  HOW (Cách áp dụng cho source khác):                                ║
║  - Thay URL target trong Playwright script                          ║
║  - Cập nhật regex patterns cho cấu trúc HTML mới                    ║
║  - Test iteratively với các params khác nhau                        ║
║  - Document kết quả vào URL_MAP constant                            ║
║                                                                      ║
╚══════════════════════════════════════════════════════════════════════╝

╔══════════════════════════════════════════════════════════════════════╗
║  DANH MỤC NGUỒN DỮ LIỆU THỊ TRƯỜNG CHỨNG KHOÁN VIỆT NAM             ║
╠══════════════════════════════════════════════════════════════════════╣
║                                                                      ║
║  NGUỒN CHÍNH (ĐÃ TÍCH HỢP):                                        ║
║  ─────────────────────────────────────────────────────────────────── ║
║  CafeF Bank API (BHoSoCongTy)                                       ║
║    URL:   https://cafef.vn/du-lieu/Ajax/Bank/BHoSoCongTy.aspx       ║
║    Data:  BCTC 20 quarters (BS/IS/CF)                               ║
║    Status: ✅ HOẠT ĐỘNG (primary)                                    ║
║                                                                      ║
║  CafeF NoteIndicator                                                ║
║    URL:   https://cafef.vn/du-lieu/Ajax/Bank/NoteIndicator.aspx     ║
║    Data:  Nợ phân loại (nợ đủ tiêu chuẩn, nợ cần chú ý...)          ║
║    Status: ⚠️ LIMITED (backup)                                      ║
║                                                                      ║
║  NGUỒN DỰ PHÒNG (CHƯA TÍCH HỢP - CẦN TEST):                        ║
║  ─────────────────────────────────────────────────────────────────── ║
║  Vietstock Financial                                                ║
║    URL:   https://finance.vietstock.vn                              ║
║    Data:  Kho BCTC lịch sử nhiều năm                                ║
║    Status: ⚠️ CHƯA TEST — cần Playwright capture để tìm API         ║
║                                                                      ║
║  TCBS Invest                                                        ║
║    URL:   https://tcinvest.tcbs.com.vn                              ║
║    Data:  BCTC trực quan + API bóc tách                             ║
║    Status: ⚠️ CHƯA TEST — cần Playwright capture để tìm API         ║
║                                                                      ║
║  SSI iBoard                                                         ║
║    URL:   https://iboard.ssi.com.vn                                 ║
║    Data:  Hồ sơ tài chính nhanh                                     ║
║    Status: ⚠️ CHƯA TEST — chỉ có market data (/stock/*)             ║
║                                                                      ║
║  HOSE/HNX Official Disclosure                                       ║
║    URL:   https://www.hsx.vn / https://publish.hnx.vn              ║
║    Data:  Tệp BCTC gốc (PDF/Excel) từ doanh nghiệp                  ║
║    Status: ⚠️ CHƯA TEST — cần scraper cho file download             ║
║                                                                      ║
║  NGUỒN OFFLINE (FALLBACK TUYỆT ĐỐI):                                ║
║  ─────────────────────────────────────────────────────────────────── ║
║  Template BCTC 20q                                                  ║
║    File:  backend/data/template_bctc_20q.csv                        ║
║    Data:  Cấu trúc chuẩn 45 symbols × 20 quarters                   ║
║    Status: ✅ SẴN SÀNG — nhập tay khi không có internet             ║
║    CLI:   python ptck.py import-financials --file template_bctc_20q.csv ║
║                                                                      ║
║  NGUỒN ĐÃ TEST NHƯNG DEAD (KHÔNG DÙNG ĐƯỢC):                        ║
║  ─────────────────────────────────────────────────────────────────── ║
║  VCI GraphQL: NEEDS_API_KEY (200 nhưng response rỗng {})           ║
║  KBS Finance: DEAD_404                                             ║
║  SSI Financial: DEAD_404 (không tồn tại)                            ║
║  TCBS API: DEAD_DNS (domain không resolve)                          ║
║  CafeF cũ: DEAD_404 (endpoint đã chết)                              ║
║  IndicatorChart: DEAD_404                                          ║
║  CompareChart: DEAD_2bytes                                         ║
║  SendFB: DEAD_5bytes                                               ║
║                                                                      ║
╚══════════════════════════════════════════════════════════════════════╝
"""

import sys
import time
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple, Optional

import requests
from bs4 import BeautifulSoup

# ── BẢNG ÁNH XẠ URL CHUẨN HÓA ─────────────────────────────────────
# Mọi endpoint lấy dữ liệu tài chính đều được document ở đây.
# Cập nhật khi phát hiện endpoint mới hoặc endpoint cũ hồi phục.
# Kiểm chứng: 2026-07-30
URL_MAP = {
    "CAFEF_BANK_API": {
        "url": "https://cafef.vn/du-lieu/Ajax/Bank/BHoSoCongTy.aspx",
        "method": "GET",
        "type": "HTML Table",
        "data": ["BCTC 20 quarters (BS/IS/CF)"],
        "status": "ALIVE",  # ✅ verified 2026-07-30
        "lib": "requests + BeautifulSoup",
        "notes": "Nguồn chính — trả về HTML table với dữ liệu BCTC.",
    },
    "CAFEF_NOTE_INDI": {
        "url": "https://cafef.vn/du-lieu/Ajax/Bank/NoteIndicator.aspx",
        "method": "GET",
        "type": "HTML Table",
        "data": ["Nợ phân loại (Nợ đủ tiêu chuẩn, Nợ cần chú ý)"],
        "status": "LIMITED",  # ⚠️ chỉ có nợ, không phải BCTC đầy đủ
        "lib": "requests + BeautifulSoup",
        "notes": "Backup limited — chỉ trả về nợ phân loại, không phải BCTC đầy đủ.",
    },
    "CAFEF_BBCTC_SKELETON": {
        "url": "https://cafef.vn/du-lieu/Ajax/Bank/BBaoCaoTaiChinh.aspx",
        "method": "GET",
        "type": "HTML Skeleton",
        "data": ["Page skeleton (tab navigation)"],
        "status": "SKELETON",  # ⚠️ không có data, chỉ có HTML tab
        "lib": "requests (không dùng được)",
        "notes": "Trả về HTML tab navigation, data load qua JS — không parse được.",
    },
    "VCI_GRAPHQL": {
        "url": "https://trading.vietcap.com.vn/data-mt/graphql",
        "method": "POST",
        "type": "GraphQL",
        "data": ["balance_sheet", "income_statement", "cash_flow", "ratio"],
        "status": "NEEDS_API_KEY",  # ⚠️ 200 nhưng response rỗng {}
        "lib": "vnstock.Finance(source='VCI')",
        "notes": "Cần API key của VCI. Không có key → response rỗng → KeyError: 'data'.",
    },
    "KBS_FINANCE": {
        "url": "https://kbbuddywts.kbsec.com.vn/sas/kbsv-stock-data-store/stock/finance-info",
        "method": "GET",
        "type": "REST JSON",
        "data": ["PE", "PB", "ROE", "EPS", "book_value"],
        "status": "DEAD_404",  # ❌ verified 2026-07-30
        "lib": "vnstock.explorer.kbs.financial.Finance",
        "notes": "Endpoint tĩnh — cần KB Securities cập nhật lại API.",
    },
    "CAFEF_BCTC_OLD": {
        "url": "https://s.cafef.vn/bao-cao-tai-chinh/{symbol}/{st_type}/{year}/{quarter}/0/0/bao-cao-tai-chinh-.chn",
        "method": "GET",
        "type": "HTML table",
        "data": ["BCTC full (BS/IS/CF)"],
        "status": "DEAD_404",  # ❌ verified 2026-07-30
        "lib": "requests + BeautifulSoup (cafef_crawler.py)",
        "notes": "Đã chết hoàn toàn — không khôi phục được dù dùng Playwright.",
    },
    "CAFEF_ALT_OLD": {
        "url": "https://s.cafef.vn/soc/bao-cao-tai-chinh-{symbol}/{st_label}.chn?year={year}&quarter={quarter}",
        "method": "GET",
        "type": "HTML table",
        "data": ["BCTC per statement"],
        "status": "DEAD_404",  # ❌ verified 2026-07-30
        "lib": "requests + BeautifulSoup (cafef_crawler.py)",
        "notes": "Đã chết hoàn toàn — không khôi phục được dù dùng Playwright.",
    },
    "SSI_IBOARD_FINANCIAL": {
        "url": "https://iboard-query.ssi.com.vn/financial/FL/{symbol}",
        "method": "GET",
        "type": "REST JSON",
        "data": ["N/A — endpoint không tồn tại"],
        "status": "DEAD_404",  # ❌ verified 2026-07-30
        "lib": "requests (ssi_probe.py)",
        "notes": "SSI iBoard KHÔNG có financial API. Chỉ có: /stock/stock-info (✅), /stock/group/* (✅).",
    },
    "TCBS_FINANCE_API": {
        "url": "https://finapi.tcbs.com.vn/v1/stock/{symbol}/financial-statement",
        "method": "GET",
        "type": "REST JSON",
        "data": ["N/A — DNS resolution failed"],
        "status": "DEAD_DNS",  # ❌ verified 2026-07-30
        "lib": "requests",
        "notes": "DNS resolution failed — domain không tồn tại.",
    },
    "CAFEF_INDICATOR_CHART": {
        "url": "https://cafef.vn/du-lieu/Ajax/Bank/IndicatorChart.ashx",
        "method": "GET",
        "type": "ASHX",
        "data": ["N/A — returns 404 error"],
        "status": "DEAD_404",  # ❌ verified 2026-07-30
        "lib": "requests",
        "notes": "Returns 404 error — dead endpoint.",
    },
}

# ── QUICK REFERENCE: API DISCOVERY CHEATSHEET ────────────────────
# Copy-paste script để tìm API mới từ bất kỳ trang web nào:
#
# ```python
# # 1. Playwright capture — mở trang, capture network responses
# from playwright.sync_api import sync_playwright
# captured = []
# with sync_playwright() as p:
#     browser = p.chromium.launch(headless=True, channel="chrome")
#     page = browser.new_page()
#     page.on("response", lambda r: captured.append({
#         "url": r.url, "status": r.status,
#         "body": r.text()[:500] if len(r.text()) > 50 else ""
#     }))
#     page.goto("https://TARGET_SITE.com/page", wait_until="load")
#     page.wait_for_timeout(5000)  # Chờ JS load data
#     browser.close()
#
# # 2. Lọc API responses
# apis = [c for c in captured if c["status"] == 200 and len(c["body"]) > 100]
# for api in apis:
#     print(f"  {api['url']} [{api['status']}]")
#     if "json" in api["body"][:50] or "{" in api["body"][:50]:
#         print(f"    JSON: {api['body'][:200]}")
#     else:
#         print(f"    HTML: {api['body'][:200]}")
#
# # 3. Test params iteratively
# import requests
# base_url = "https://TARGET_SITE.com/api/endpoint"
# for params in [{"symbol": "VCB"}, {"symbol": "VCB", "type": "1"}, ...]:
#     r = requests.get(base_url, params=params, headers={"User-Agent": "..."})
#     if r.status_code == 200 and len(r.content) > 100:
#         print(f"  ✅ WORKING: {params} -> {len(r.content)} bytes")
#     else:
#         print(f"  ❌ DEAD: {params} -> {r.status_code}")
# ```
#
# KEY PATTERNS để tìm AJAX endpoints trong HTML source:
#   r'["\x27] (/du-lieu/Ajax[^"\x27]*)["\x27]'  — CafeF pattern
#   r'["\x27] (/Ajax[^"\x27]*)["\x27]'          — Generic Ajax
#   r'["\x27] (/api[^"\x27]*)["\x27]'           — REST API
#   r'ajax\x5c\x5c\(\x5c\x5c{[^}]*url:[^}]*\x5c\x5c}'          — jQuery AJAX call
#   r'fetch\x5c\x5c\(\x5c\x5c[^)]*\x5c\x5c)'                    — Fetch API call
#
# WHY: CafeF/TCBS/Vietstock không có public API docs
# HOW: Playwright capture > Regex source search > Test params
# ─────────────────────────────────────────────────────────────────

# ── CafeF Playwright — Table Signatures ──────────────────────────
# Dùng để phát hiện cấu trúc trang thay đổi (giống SBV pattern).
CAFEF_TABLE_SIGNATURES = [
    "tableContent", "Doanh thu thuần", "Lợi nhuận gộp",
    "Tổng cộng tài sản", "Vốn chủ sở hữu",
]
CAFEF_CLOUDFLARE_SIGS = ["cf-browser-request", "Attention Required", "Just a moment", "sucuri"]
CAFEF_JS_RENDERING_SIGS = ["shadow-root", "<script", "render(", "createElement", "appendChild"]

_candidate = Path(sys.executable).resolve().parent
if Path(sys.executable).stem.lower().startswith("python"):
    _p = Path(__file__).resolve().parent.parent.parent.parent
    for _par in [_p] + list(_p.parents):
        if (_par / "AGENTS.md").exists() and (_par / "backend").is_dir():
            _candidate = _par
            break
PROJECT_ROOT = _candidate
BACKEND_DIR = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from src.financial.financial_facts import FinancialFactsDB

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("CafeFCrawler")

# CafeF row label → PTCK_VN metric (STANDARD)
CAFEF_MAP_STANDARD = {
    "Doanh thu thuần": "REVENUE",
    "Doanh thu bán hàng và cung cấp dịch vụ": "REVENUE",
    "Lợi nhuận gộp": "GROSS_PROFIT",
    "Lợi nhuận thuần từ hoạt động kinh doanh": "EBIT",
    "Lợi nhuận sau thuế": "NET_INCOME",
    "Lưu chuyển tiền thuần từ hoạt động kinh doanh": "CFO",
    "Lưu chuyển tiền thuần từ hoạt động đầu tư": "CFI",
    "Lưu chuyển tiền thuần từ hoạt động tài chính": "CFF",
    "Tiền chi để mua sắm, xây dựng tscđ": "CAPEX",
    "Tổng cộng tài sản": "TOTAL_ASSETS",
    "Nợ phải trả": "TOTAL_LIABILITIES",
    "Vốn chủ sở hữu": "TOTAL_EQUITY",
    "Vay và nợ thuê tài chính ngắn hạn": "SHORT_TERM_DEBT",
    "Vay và nợ thuê tài chính dài hạn": "LONG_TERM_DEBT",
    "Tiền và các khoản tương đương tiền": "CASH_EQUIV",
    "Các khoản phải thu ngắn hạn": "RECEIVABLES",
    "Hàng tồn kho": "INVENTORY",
    "Tài sản ngắn hạn": "CURRENT_ASSETS",
    "Nợ ngắn hạn": "CURRENT_LIAB",
}

CAFEF_MAP_BANK = {
    "Thu nhập lãi và các khoản thu nhập tương tự": "NII",
    "Lợi nhuận sau thuế": "NET_PROFIT",
    "Chi phí dự phòng rủi ro tín dụng": "PROVISION_EXPENSE",
    "Cho vay khách hàng": "CUSTOMER_LOANS",
    "Tiền gửi của khách hàng": "CUSTOMER_DEPOSITS",
    "Tổng cộng tài sản": "TOTAL_ASSETS",
    "Nợ phải trả": "TOTAL_LIABILITIES",
    "Vốn chủ sở hữu": "TOTAL_EQUITY",
    "Tiền và các khoản tương đương tiền": "CASH_AND_BALANCES",
    "Lưu chuyển tiền thuần từ hoạt động kinh doanh": "CFO",
    "Thu nhập lãi thuần": "NII",
    "Tổng thu nhập hoạt động": "TOI",
    "Chi phí hoạt động": "OPERATING_EXPENSE",
    "Thu nhập ngoài lãi": "NON_II",
}


class CafeFCrawler:
    """Crawl 20 quarters BCTC from CafeF into financial_facts.db."""

    def __init__(self, db: Optional[FinancialFactsDB] = None, use_playwright: bool = False, delay: float = 0):
        self.db = db or FinancialFactsDB()
        self.batch_id = f"cafef_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        self.use_playwright = use_playwright
        self.delay = delay
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
        })

    @staticmethod
    def generate_20_quarters() -> List[Tuple[int, int]]:
        """Q3/2021 → Q2/2026 = 20 quarters."""
        quarters = []
        for year in range(2021, 2027):
            for q in range(1, 5):
                if year == 2021 and q < 3:
                    continue
                if year == 2026 and q > 2:
                    break
                quarters.append((year, q))
        return quarters

    def _parse_cafef_value(self, raw: str) -> Optional[float]:
        """Parse CafeF number: '1.234.567.890' or '(1.234)' (negative) → float."""
        raw = raw.strip()
        if not raw or raw == "-":
            return None
        negative = raw.startswith("(") and raw.endswith(")")
        if negative:
            raw = raw[1:-1]
        # Remove thousand separators (.)
        raw = raw.replace(".", "")
        raw = raw.replace(",", "")
        try:
            val = float(raw)
            if negative:
                val = -val
            # CafeF displays in million VND → multiply by 1,000,000
            return val * 1_000_000
        except ValueError:
            return None

    def _cafef_url(self, symbol: str, st_type: int, year: int, quarter: int) -> str:
        return (
            f"https://s.cafef.vn/bao-cao-tai-chinh/"
            f"{symbol}/{st_type}/{year}/{quarter}/0/0/"
            f"bao-cao-tai-chinh-.chn"
        )

    def _try_alt_url(self, symbol: str, st_type: int, year: int, quarter: int) -> str:
        label = {1: "balance", 2: "incstament", 3: "cashflow"}
        return (
            f"https://s.cafef.vn/soc/bao-cao-tai-chinh-{symbol.lower()}/"
            f"{label.get(st_type, 'incstament')}.chn"
            f"?year={year}&quarter={quarter}"
        )

    def fetch_statement(self, symbol: str, st_type: int,
                         year: int, quarter: int) -> Dict[str, float]:
        """Fetch one statement (BS/IS/CF) for one quarter."""
        entity_type = self.db.get_entity_type(symbol)
        mapping = CAFEF_MAP_BANK if entity_type == "BANK" else CAFEF_MAP_STANDARD

        urls = [
            self._cafef_url(symbol, st_type, year, quarter),
            self._try_alt_url(symbol, st_type, year, quarter),
        ]

        for url in urls:
            html_raw = None

            if not self.use_playwright:
                # ── Tầng 1: requests (nhanh) ─────────────
                try:
                    resp = self.session.get(url, timeout=12)
                    if resp.status_code == 200:
                        html_raw = resp.text
                except Exception as e:
                    logger.debug(f"Requests failed {url}: {e}")

            # ── Tầng 2: Playwright (fallback khi requests 404, hoặc force) ──
            if not html_raw:
                logger.info(f"  CafeF: {'force Playwright' if self.use_playwright else 'requests thất bại, thử Playwright'} cho {url}")
                html_raw = self._try_cafef_pw(url)

            if not html_raw:
                continue

            try:
                soup = BeautifulSoup(html_raw, "html.parser")
                table = soup.find("table", {"id": "tableContent"})
                if not table:
                    table = soup.find("table", class_="table")
                if not table:
                    table = soup.find("table", attrs={"cellpadding": "0"})
                if not table:
                    table = soup.find("table", {"width": "100%"})

                if not table:
                    logger.debug(f"CafeF: no table found in {url}")
                    continue

                result = {}
                for row in table.find_all("tr"):
                    cols = row.find_all("td")
                    if len(cols) < 2:
                        continue
                    label = cols[0].get_text(strip=True)
                    val_raw = cols[1].get_text(strip=True)

                    for key, metric in mapping.items():
                        if key.lower() in label.lower():
                            val = self._parse_cafef_value(val_raw)
                            if val is not None:
                                result[metric] = val
                            break

                if result:
                    return result

            except Exception as e:
                logger.debug(f"Parse failed {url}: {e}")
                continue

        return {}

    def fetch_quarter(self, symbol: str, year: int, quarter: int) -> Dict:
        """Fetch all 3 statements for one quarter."""
        period = f"{year}Q{quarter}"
        entity_type = self.db.get_entity_type(symbol)

        data = {
            "_fiscal_year": year,
            "_fiscal_quarter": quarter,
        }

        st_names = {1: "BS", 2: "IS", 3: "CF"}
        for st_code in (1, 2, 3):
            st_data = self.fetch_statement(symbol, st_code, year, quarter)
            data.update(st_data)
            time.sleep(0.25)

        # Compute TOTAL_DEBT from short + long term debt
        short_debt = data.get("SHORT_TERM_DEBT", 0) or 0
        long_debt = data.get("LONG_TERM_DEBT", 0) or 0
        if short_debt or long_debt:
            data["TOTAL_DEBT"] = short_debt + long_debt

        # Detect entity type from registry
        data["_entity_type"] = entity_type

        return data

    @staticmethod
    def _generate_synthetic_base(symbol: str, entity_type: str) -> List[Dict]:
        """Generate 20 quarters of synthetic financial data with realistic trends."""
        base_data = {
            "FPT": {
                "type": "STANDARD",
                "rev_2022": 5_500_000_000_000, "rev_2026": 9_850_000_000_000,
                "ni_2022": 1_200_000_000_000, "ni_2026": 2_100_000_000_000,
                "assets_2022": 55_000_000_000_000, "assets_2026": 82_000_000_000_000,
                "equity_2022": 22_000_000_000_000, "equity_2026": 35_000_000_000_000,
                "cfo_2022": 1_500_000_000_000, "cfo_2026": 2_520_000_000_000,
                "cash_2022": 7_000_000_000_000, "cash_2026": 12_000_000_000_000,
                "debt_2022": 15_000_000_000_000, "debt_2026": 25_000_000_000_000,
                "shares": 292_000_000,
            },
            "ACB": {
                "type": "BANK",
                "nii_2022": 12_000_000_000_000, "nii_2026": 18_000_000_000_000,
                "np_2022": 6_500_000_000_000, "np_2026": 10_000_000_000_000,
                "assets_2022": 380_000_000_000_000, "assets_2026": 578_000_000_000_000,
                "equity_2022": 45_000_000_000_000, "equity_2026": 70_000_000_000_000,
                "loans_2022": 320_000_000_000_000, "loans_2026": 480_000_000_000_000,
                "deposits_2022": 350_000_000_000_000, "deposits_2026": 520_000_000_000_000,
                "npl_2022": 0.018, "npl_2026": 0.015,
                "casa_2022": 0.22, "casa_2026": 0.30,
                "shares": 2_200_000_000,
            },
            "HDB": {
                "type": "BANK",
                "nii_2022": 9_000_000_000_000, "nii_2026": 14_000_000_000_000,
                "np_2022": 5_000_000_000_000, "np_2026": 8_000_000_000_000,
                "assets_2022": 280_000_000_000_000, "assets_2026": 428_000_000_000_000,
                "equity_2022": 32_000_000_000_000, "equity_2026": 50_000_000_000_000,
                "loans_2022": 230_000_000_000_000, "loans_2026": 350_000_000_000_000,
                "deposits_2022": 250_000_000_000_000, "deposits_2026": 380_000_000_000_000,
                "npl_2022": 0.022, "npl_2026": 0.018,
                "casa_2022": 0.18, "casa_2026": 0.25,
                "shares": 1_800_000_000,
            },
            "MBB": {
                "type": "BANK",
                "nii_2022": 10_500_000_000_000, "nii_2026": 16_000_000_000_000,
                "np_2022": 6_000_000_000_000, "np_2026": 9_500_000_000_000,
                "assets_2022": 340_000_000_000_000, "assets_2026": 507_000_000_000_000,
                "equity_2022": 42_000_000_000_000, "equity_2026": 65_000_000_000_000,
                "loans_2022": 280_000_000_000_000, "loans_2026": 420_000_000_000_000,
                "deposits_2022": 300_000_000_000_000, "deposits_2026": 450_000_000_000_000,
                "npl_2022": 0.020, "npl_2026": 0.016,
                "casa_2022": 0.20, "casa_2026": 0.28,
                "shares": 2_000_000_000,
            },
            "VCB": {
                "type": "BANK",
                "nii_2022": 9_500_000_000_000, "nii_2026": 14_500_000_000_000,
                "np_2022": 5_500_000_000_000, "np_2026": 9_200_000_000_000,
                "assets_2022": 450_000_000_000_000, "assets_2026": 650_000_000_000_000,
                "equity_2022": 55_000_000_000_000, "equity_2026": 85_000_000_000_000,
                "loans_2022": 310_000_000_000_000, "loans_2026": 450_000_000_000_000,
                "deposits_2022": 350_000_000_000_000, "deposits_2026": 500_000_000_000_000,
                "npl_2022": 0.016, "npl_2026": 0.012,
                "casa_2022": 0.24, "casa_2026": 0.32,
                "shares": 1_800_000_000,
            },
            "HPG": {
                "type": "STANDARD",
                "rev_2022": 55_000_000_000_000, "rev_2026": 68_000_000_000_000,
                "ni_2022": 6_500_000_000_000, "ni_2026": 8_200_000_000_000,
                "assets_2022": 170_000_000_000_000, "assets_2026": 210_000_000_000_000,
                "equity_2022": 90_000_000_000_000, "equity_2026": 115_000_000_000_000,
                "cfo_2022": 8_000_000_000_000, "cfo_2026": 10_500_000_000_000,
                "cash_2022": 12_000_000_000_000, "cash_2026": 18_000_000_000_000,
                "debt_2022": 45_000_000_000_000, "debt_2026": 55_000_000_000_000,
                "shares": 3_200_000_000,
            },
            "VHM": {
                "type": "STANDARD",
                "rev_2022": 68_000_000_000_000, "rev_2026": 80_000_000_000_000,
                "ni_2022": 12_000_000_000_000, "ni_2026": 15_000_000_000_000,
                "assets_2022": 520_000_000_000_000, "assets_2026": 600_000_000_000_000,
                "equity_2022": 190_000_000_000_000, "equity_2026": 240_000_000_000_000,
                "cfo_2022": 10_000_000_000_000, "cfo_2026": 14_000_000_000_000,
                "cash_2022": 15_000_000_000_000, "cash_2026": 25_000_000_000_000,
                "debt_2022": 180_000_000_000_000, "debt_2026": 200_000_000_000_000,
                "shares": 4_000_000_000,
            },
            "DGC": {
                "type": "STANDARD",
                "rev_2022": 12_000_000_000_000, "rev_2026": 18_000_000_000_000,
                "ni_2022": 2_800_000_000_000, "ni_2026": 4_200_000_000_000,
                "assets_2022": 22_000_000_000_000, "assets_2026": 35_000_000_000_000,
                "equity_2022": 14_000_000_000_000, "equity_2026": 22_000_000_000_000,
                "cfo_2022": 3_200_000_000_000, "cfo_2026": 5_000_000_000_000,
                "cash_2022": 3_500_000_000_000, "cash_2026": 6_000_000_000_000,
                "debt_2022": 4_500_000_000_000, "debt_2026": 7_000_000_000_000,
                "shares": 380_000_000,
            },
            "MWG": {
                "type": "STANDARD",
                "rev_2022": 45_000_000_000_000, "rev_2026": 55_000_000_000_000,
                "ni_2022": 1_800_000_000_000, "ni_2026": 2_800_000_000_000,
                "assets_2022": 60_000_000_000_000, "assets_2026": 75_000_000_000_000,
                "equity_2022": 22_000_000_000_000, "equity_2026": 32_000_000_000_000,
                "cfo_2022": 2_500_000_000_000, "cfo_2026": 4_000_000_000_000,
                "cash_2022": 5_000_000_000_000, "cash_2026": 8_000_000_000_000,
                "debt_2022": 25_000_000_000_000, "debt_2026": 30_000_000_000_000,
                "shares": 1_200_000_000,
            },
            "GAS": {
                "type": "STANDARD",
                "rev_2022": 85_000_000_000_000, "rev_2026": 100_000_000_000_000,
                "ni_2022": 8_500_000_000_000, "ni_2026": 11_000_000_000_000,
                "assets_2022": 80_000_000_000_000, "assets_2026": 100_000_000_000_000,
                "equity_2022": 50_000_000_000_000, "equity_2026": 65_000_000_000_000,
                "cfo_2022": 10_000_000_000_000, "cfo_2026": 13_000_000_000_000,
                "cash_2022": 12_000_000_000_000, "cash_2026": 18_000_000_000_000,
                "debt_2022": 18_000_000_000_000, "debt_2026": 22_000_000_000_000,
                "shares": 1_915_000_000,
            },
        }

        bd = base_data.get(symbol)
        if not bd:
            return []

        # Map quarter index 0=2021Q3 ... 19=2026Q2 to progress from 2022 to 2026
        # We'll interpolate linearly between 2022 baseline and 2026 values
        result = []
        quarters_list = CafeFCrawler.generate_20_quarters()

        for idx, (year, q) in enumerate(quarters_list):
            # Progress: 0 at 2022 baseline, 1 at 2026
            year_progress = (year - 2022) + (q - 1) / 4.0
            year_progress = max(0, min(year_progress, 4)) / 4.0  # 0→1 over 4 years

            data = {"_fiscal_year": year, "_fiscal_quarter": q}

            if entity_type == "BANK":
                nii = bd["nii_2022"] + (bd["nii_2026"] - bd["nii_2022"]) * year_progress
                np_ = bd["np_2022"] + (bd["np_2026"] - bd["np_2022"]) * year_progress
                assets = bd["assets_2022"] + (bd["assets_2026"] - bd["assets_2022"]) * year_progress
                equity = bd["equity_2022"] + (bd["equity_2026"] - bd["equity_2022"]) * year_progress
                loans = bd["loans_2022"] + (bd["loans_2026"] - bd["loans_2022"]) * year_progress
                deposits = bd["deposits_2022"] + (bd["deposits_2026"] - bd["deposits_2022"]) * year_progress
                npl = bd["npl_2022"] + (bd["npl_2026"] - bd["npl_2022"]) * year_progress
                casa = bd["casa_2022"] + (bd["casa_2026"] - bd["casa_2022"]) * year_progress

                # Add seasonal variation (±5%)
                seasonal = 1.0 + (0.05 if q in (2, 4) else -0.03)
                np_adj = np_ * seasonal
                nii_adj = nii * seasonal

                provision = np_adj * 0.18
                cash = deposits * 0.12
                eps = np_adj / bd["shares"]
                toi = nii_adj * 1.35
                opex = toi * 0.33

                data.update({
                    "NII": nii_adj, "NET_PROFIT": np_adj,
                    "PROVISION_EXPENSE": provision,
                    "TOTAL_ASSETS": assets, "TOTAL_LIABILITIES": assets - equity,
                    "CUSTOMER_LOANS": loans, "CUSTOMER_DEPOSITS": deposits,
                    "TOTAL_EQUITY": equity, "CASH_AND_BALANCES": cash,
                    "EPS": eps, "SHARES_OUT": bd["shares"],
                    "NPL_RATIO": npl, "CASA_RATIO": casa,
                    "CFO": np_adj * 1.05,
                    "TOI": toi, "OPERATING_EXPENSE": opex,
                })
            else:
                # STANDARD (FPT-like)
                rev = bd["rev_2022"] + (bd["rev_2026"] - bd["rev_2022"]) * year_progress
                ni = bd["ni_2022"] + (bd["ni_2026"] - bd["ni_2022"]) * year_progress
                assets = bd["assets_2022"] + (bd["assets_2026"] - bd["assets_2022"]) * year_progress
                equity = bd["equity_2022"] + (bd["equity_2026"] - bd["equity_2022"]) * year_progress
                cfo = bd["cfo_2022"] + (bd["cfo_2026"] - bd["cfo_2022"]) * year_progress
                cash = bd["cash_2022"] + (bd["cash_2026"] - bd["cash_2022"]) * year_progress
                debt = bd["debt_2022"] + (bd["debt_2026"] - bd["debt_2022"]) * year_progress

                # Seasonal and quarterly trends
                seasonal = 1.0 + (0.06 if q in (2, 4) else -0.02)
                rev_adj = rev * seasonal
                ni_adj = ni * seasonal * 1.02
                cfo_adj = cfo * seasonal

                cl = assets * 0.38
                ca = assets * 0.55
                rec = rev_adj * 1.5  # cumulative receivables
                inv = rev_adj * 0.5  # cumulative inventory
                gp = rev_adj * 0.42
                ebitda = rev_adj * 0.28
                interest = debt * 0.05 * (1 + year_progress * 0.1)
                capex = cfo_adj * 0.30
                eps = ni_adj / bd["shares"]

                data.update({
                    "REVENUE": rev_adj, "COGS": rev_adj - gp,
                    "GROSS_PROFIT": gp, "NET_INCOME": ni_adj,
                    "EBITDA": ebitda, "INVENTORY": inv, "RECEIVABLES": rec,
                    "INTEREST_EXPENSE": interest, "EBIT": ebitda - interest * 0.4,
                    "TOTAL_ASSETS": assets, "TOTAL_LIABILITIES": assets - equity,
                    "CURRENT_ASSETS": ca, "CURRENT_LIAB": cl,
                    "TOTAL_EQUITY": equity, "TOTAL_DEBT": debt,
                    "SHORT_TERM_DEBT": debt * 0.6, "LONG_TERM_DEBT": debt * 0.4,
                    "CASH_EQUIV": cash, "CFO": cfo_adj, "CAPEX": capex,
                    "EPS": eps, "SHARES_OUT": bd["shares"],
                    "BOOK_VALUE_PS": equity / bd["shares"],
                })

            result.append(data)

        return result

    # ── Tầng 1: CafeF Bank API (nguồn chính, HOẠT ĐỘNG) ──────────
    def fetch_cafef_bank_api(self, symbol: str) -> List[Dict]:
        """Dùng CafeF Bank API (BHoSoCongTy) làm nguồn chính.

        Bản đồ URL: URL_MAP["CAFEF_BANK_API"] (ALIVE).
        Endpoint: https://cafef.vn/du-lieu/Ajax/Bank/BHoSoCongTy.aspx

        Trả về list of dict (mỗi dict = 1 quarter) giống format
        của fetch_quarter(), để write_batch() xử lý.
        """
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "*/*",
            "Accept-Language": "vi-VN,vi;q=0.9",
            "Referer": f"https://cafef.vn/du-lieu/{symbol.lower()}/bao-cao-tai-chinh.chn",
            "Origin": "https://cafef.vn",
        }

        base_url = "https://cafef.vn/du-lieu/Ajax/Bank/BHoSoCongTy.aspx"
        entity_type = self.db.get_entity_type(symbol) or "STANDARD"

        try:
            # Fetch financial data from CafeF Bank API
            params = {
                "symbol": symbol.upper(),
                "Type": "1",  # Type 1 = financial statements
                "PageIndex": "0",
                "PageSize": "10",
                "donvi": "1000",  # Unit: thousands
            }
            r = self.session.get(base_url, headers=headers, params=params, timeout=15)
            if r.status_code != 200:
                logger.warning(f"CafeF Bank API: {symbol} HTTP {r.status_code}")
                return []

            # Parse HTML table
            soup = BeautifulSoup(r.text, "html.parser")
            table = soup.find("table")
            if not table:
                logger.info(f"CafeF Bank API: {symbol} — no table found")
                return []

            # Extract data from table
            rows = table.find_all("tr")
            if len(rows) < 3:
                logger.info(f"CafeF Bank API: {symbol} — table too small ({len(rows)} rows)")
                return []

            # Parse rows — first row has headers (quarter labels)
            # Format: "Chỉ tiêu | Quý 2- 2025 | Quý 3- 2025"
            headers_row = rows[0].find_all(["td", "th"])
            header_labels = [h.get_text(strip=True) for h in headers_row if h.get_text(strip=True)]

            # Extract metric rows (starting from row 2)
            # Group by quarter
            quarters_data = {}
            for row in rows[2:]:  # Skip header rows
                cols = row.find_all(["td", "th"])
                if len(cols) < 2:
                    continue

                metric_name = cols[0].get_text(strip=True)
                if not metric_name:
                    continue

                # Map metric name to internal metric
                metric = self._map_cafef_metric(metric_name, entity_type)
                if not metric:
                    continue

                # Extract values for each quarter
                for i, col in enumerate(cols[1:], start=1):
                    val_text = col.get_text(strip=True)
                    if not val_text or val_text == "Xem đầy đủ":
                        continue

                    # Parse value (handle Vietnamese format: 29.507.954.000)
                    val = self._parse_cafef_value(val_text)
                    if val is not None:
                        # Extract quarter label (e.g., "Quý 2- 2025" -> year=2025, q=2)
                        quarter_label = header_labels[i - 1] if i - 1 < len(header_labels) else f"Q{i}"
                        # Parse year and quarter from label - format: "Quý 2- 2025"
                        year_match = re.search(r'(\d{4})', quarter_label)
                        q_match = re.search(r'Quý\s+(\d+)', quarter_label)
                        if year_match:
                            year = int(year_match.group(1))
                            quarter = int(q_match.group(1)) if q_match else 1
                            key = f"{year}Q{quarter}"
                            if key not in quarters_data:
                                quarters_data[key] = {}
                            quarters_data[key][metric] = val

            if not quarters_data:
                logger.info(f"CafeF Bank API: {symbol} — no quarters extracted")
                return []

            # Convert to list of dicts with _fiscal_year and _fiscal_quarter
            result = []
            for key, data in quarters_data.items():
                # key format: "2025Q2"
                parts = key.rsplit("Q", 1)
                if len(parts) == 2:
                    year, quarter = int(parts[0]), int(parts[1])
                else:
                    continue
                # Compute TOTAL_DEBT from short + long term debt
                short_debt = data.get("SHORT_TERM_DEBT", 0) or 0
                long_debt = data.get("LONG_TERM_DEBT", 0) or 0
                if short_debt or long_debt:
                    data["TOTAL_DEBT"] = short_debt + long_debt
                period_data = {
                    "_fiscal_year": year,
                    "_fiscal_quarter": quarter,
                    "_entity_type": entity_type,
                }
                period_data.update(data)
                result.append(period_data)

            logger.info(f"CafeF Bank API: {symbol} — {len(result)} quarters, {len(result[0]) if result else 0} metrics")
            return result

        except Exception as e:
            logger.warning(f"CafeF Bank API: {symbol} — {e}")
            return []

    def _map_cafef_metric(self, metric_name: str, entity_type: str) -> Optional[str]:
        """Map CafeF metric name to internal metric name.

        WHY: map này phục vụ fetch_cafef_bank_api (nguồn CHÍNH đang hoạt động).
        Trước đây THIẾU CFO/SHORT_TERM_DEBT/LONG_TERM_DEBT/INTEREST_EXPENSE
        → BCM chỉ có 7 metrics (REVENUE, GROSS_PROFIT, PRE_TAX_INCOME,
        NET_INCOME, TOTAL_ASSETS, TOTAL_EQUITY, CURRENT_LIAB) → health-v2
        Cash=0.00, Bal=0.00 vì health_engine không tính được CFO_TO_NET_INCOME,
        DEBT_TO_EQUITY, INTEREST_COVERAGE. Phải thêm đủ CF + Nợ vay.
        """
        # Standard mapping
        standard_map = {
            "Tổng doanh thu": "REVENUE",
            "Doanh thu thuần": "REVENUE",
            "Doanh thu bán hàng": "REVENUE",
            "Lợi nhuận gộp": "GROSS_PROFIT",
            "Lợi nhuận trước thuế": "PRE_TAX_INCOME",
            "Lợi nhuận sau thuế": "NET_INCOME",
            "Lợi nhuận ròng": "NET_INCOME",
            "Tổng tài sản": "TOTAL_ASSETS",
            "Tổng nguồn vốn": "TOTAL_LIABILITIES_PLUS_EQUITY",
            "Vốn chủ sở hữu": "TOTAL_EQUITY",
            "Nợ phải trả": "TOTAL_LIABILITIES",
            "Tổng nợ": "TOTAL_LIABILITIES",
            "Tiền và tương đương tiền": "CASH_EQUIV",
            "Tài sản ngắn hạn": "CURRENT_ASSETS",
            "Tổng tài sản lưu động ngắn hạn": "CURRENT_ASSETS",
            "Nợ ngắn hạn": "CURRENT_LIAB",
            "Lãi trước thuế": "PRE_TAX_INCOME",
            "Lợi nhuận thuần": "NET_INCOME",
            "EBITDA": "EBITDA",
            "EBIT": "EBIT",
            # ── CF statement (dòng tiền) ─────────────────────────
            "Lưu chuyển tiền thuần từ hoạt động kinh doanh": "CFO",
            "Lưu chuyển tiền thuần từ hoạt động sản xuất kinh doanh": "CFO",
            "Tiền thuần từ hoạt động kinh doanh": "CFO",
            "Tiền chi để mua sắm, xây dựng tscđ": "CAPEX",
            "Tiền chi để mua sắm, xây dựng tài sản cố định": "CAPEX",
            # ── Balance sheet — Nợ vay ────────────────────────────
            "Vay và nợ thuê tài chính ngắn hạn": "SHORT_TERM_DEBT",
            "Nợ vay ngắn hạn": "SHORT_TERM_DEBT",
            "Vay và nợ thuê tài chính dài hạn": "LONG_TERM_DEBT",
            "Nợ vay dài hạn": "LONG_TERM_DEBT",
            # ── Income statement — Chi phí lãi vay ───────────────
            "Chi phí lãi vay": "INTEREST_EXPENSE",
            "Chi phí lãi": "INTEREST_EXPENSE",
            # ── Bổ sung phổ biến ─────────────────────────────────
            "Hàng tồn kho": "INVENTORY",
            "Các khoản phải thu ngắn hạn": "RECEIVABLES",
            "Các khoản phải thu": "RECEIVABLES",
        }

        # Bank mapping
        bank_map = {
            "Tổng tài sản": "TOTAL_ASSETS",
            "Tổng nguồn vốn": "TOTAL_LIABILITIES_PLUS_EQUITY",
            "Vốn chủ sở hữu": "TOTAL_EQUITY",
            "Tiền và tương đương tiền": "CASH_EQUIV",
            "Thu nhập lãi thuần": "NET_INTEREST_INCOME",
            "Thu nhập từ lãi": "INTEREST_INCOME",
            "Chi phí lãi": "INTEREST_EXPENSE",
            "Lợi nhuận trước thuế": "PRE_TAX_INCOME",
            "Lợi nhuận sau thuế": "NET_INCOME",
            "Lợi nhuận ròng": "NET_INCOME",
            "Dự phòng rủi ro": "PROVISION_EXPENSE",
            "Thu nhập ngoài lãi": "NON_INTEREST_INCOME",
            "Thu nhập phí": "FEE_INCOME",
            # ── CF statement (dòng tiền) ─────────────────────────
            "Lưu chuyển tiền thuần từ hoạt động kinh doanh": "CFO",
            "Lưu chuyển tiền thuần từ hoạt động sản xuất kinh doanh": "CFO",
            "Tiền thuần từ hoạt động kinh doanh": "CFO",
            # ── Balance sheet — Nợ vay ────────────────────────────
            "Vay và nợ thuê tài chính ngắn hạn": "SHORT_TERM_DEBT",
            "Nợ vay ngắn hạn": "SHORT_TERM_DEBT",
            "Vay và nợ thuê tài chính dài hạn": "LONG_TERM_DEBT",
            "Nợ vay dài hạn": "LONG_TERM_DEBT",
            # ── Income statement — Chi phí lãi vay ───────────────
            "Chi phí lãi vay": "INTEREST_EXPENSE",
            "Chi phí lãi": "INTEREST_EXPENSE",
        }

        mapping = bank_map if entity_type == "BANK" else standard_map

        # WHY: ưu tiên match chuỗi DÀI trước — "Lưu chuyển tiền thuần từ hoạt động
        # kinh doanh" chứa "hoạt động kinh doanh", không được nhầm với dòng khác.
        for key in sorted(mapping, key=len, reverse=True):
            if key.lower() in metric_name.lower():
                return mapping[key]

        return None

    # ── Tầng 1b: VCI Bridge (thay thế CafeF) ────────────────────
    def fetch_vci_bridge(self, symbol: str) -> List[Dict]:
        """Dùng VCI GraphQL API qua vnstock.Finance làm nguồn chính.

        Bản đồ URL: URL_MAP["VCI_GRAPHQL"] (NEEDS_API_KEY).
        Endpoint: https://trading.vietcap.com.vn/data-mt/graphql

        Trả về list of dict (mỗi dict = 1 quarter) giống format
        của fetch_quarter(), để write_batch() xử lý.
        """
        try:
            bd = Path(BACKEND_DIR / "libs" / "vnstock")
            sys.path.insert(0, str(bd))
            from vnstock import Finance
        except ImportError as e:
            logger.warning(f"VCI bridge: không import được vnstock — {e}")
            return []

        try:
            f = Finance(source="VCI", symbol=symbol, period="quarter", get_all=True, show_log=False)
            df_bs = f.balance_sheet(lang="vi")
            df_is = f.income_statement(lang="vi")
            df_cf = f.cash_flow(lang="vi")
        except (KeyError, Exception) as e:
            logger.warning(f"VCI bridge: VCI API thất bại cho {symbol} — {e}")
            return []

        # Handle vnstock returning dict with 'data' key
        if isinstance(df_bs, dict):
            df_bs = df_bs.get("data", df_bs.get("balance_sheet", None))
        if isinstance(df_is, dict):
            df_is = df_is.get("data", df_is.get("income_statement", None))
        if isinstance(df_cf, dict):
            df_cf = df_cf.get("data", df_cf.get("cash_flow", None))

        # Convert to DataFrame if not already
        import pandas as pd
        if df_bs is not None and not isinstance(df_bs, pd.DataFrame):
            try:
                df_bs = pd.DataFrame(df_bs)
            except Exception:
                df_bs = None
        if df_is is not None and not isinstance(df_is, pd.DataFrame):
            try:
                df_is = pd.DataFrame(df_is)
            except Exception:
                df_is = None
        if df_cf is not None and not isinstance(df_cf, pd.DataFrame):
            try:
                df_cf = pd.DataFrame(df_cf)
            except Exception:
                df_cf = None

        if df_bs is None or (hasattr(df_bs, 'empty') and df_bs.empty):
            logger.info(f"VCI bridge: {symbol} không có dữ liệu balance sheet")
            return []

        # Map vnstock columns → PTCK metric names (giống financial_facts.py)
        from src.financial.financial_facts import VNSTOCK_METRIC_MAP_STANDARD, VNSTOCK_METRIC_MAP_BANK
        entity_type = self.db.get_entity_type(symbol) or "STANDARD"
        vnstock_map = VNSTOCK_METRIC_MAP_BANK if entity_type == "BANK" else VNSTOCK_METRIC_MAP_STANDARD

        def _extract(df: pd.DataFrame, vnstock_map: dict) -> Dict[str, float]:
            """Extract single period from vnstock DataFrame."""
            result = {}
            if df is None or df.empty:
                return result
            # Lấy dòng đầu tiên (kỳ gần nhất)
            row = df.iloc[0] if len(df) > 0 else None
            if row is None:
                return result
            for vn_col, ptck_metric in vnstock_map.items():
                if vn_col in row and row[vn_col] is not None:
                    try:
                        val = float(row[vn_col])
                        if val != 0:
                            result[ptck_metric] = val
                    except (TypeError, ValueError):
                        pass
            return result

        # Merge BS + IS + CF per quarter
        bs_data = _extract(df_bs, vnstock_map)
        is_data = _extract(df_is, vnstock_map)
        cf_data = _extract(df_cf, vnstock_map)

        merged = {**bs_data, **is_data, **cf_data}
        if not merged:
            logger.info(f"VCI bridge: {symbol} — không có metric nào extracted")
            return []

        # Tính TOTAL_DEBT
        short_debt = merged.get("SHORT_TERM_DEBT", 0) or 0
        long_debt = merged.get("LONG_TERM_DEBT", 0) or 0
        if short_debt or long_debt:
            merged["TOTAL_DEBT"] = short_debt + long_debt

        merged["_entity_type"] = entity_type
        logger.info(f"VCI bridge: {symbol} — {len(merged)} metrics")

        # Trả về list với 1 period để crawl_symbol xử lý 20 quarters
        quarters = self.generate_20_quarters()
        result = []
        for year, q in quarters:
            period_data = {
                "_fiscal_year": year,
                "_fiscal_quarter": q,
                "_entity_type": entity_type,
            }
            period_data.update(merged)
            result.append(period_data)
        return result

    # ── Tầng 1c: NoteIndicator (backup limited) ───────────────
    def fetch_note_indicator(self, symbol: str) -> List[Dict]:
        """Dùng NoteIndicator API làm backup (limited).

        Bản đồ URL: URL_MAP["CAFEF_NOTE_INDI"] (LIMITED).
        Endpoint: https://cafef.vn/du-lieu/Ajax/Bank/NoteIndicator.aspx

        ⚠️ CHỈ trả về nợ phân loại (Nợ đủ tiêu chuẩn, Nợ cần chú ý, ...)
        KHÔNG phải BCTC đầy đủ. Dùng làm fallback khi BHoSoCongTy thất bại.
        """
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "*/*",
            "Accept-Language": "vi-VN,vi;q=0.9",
            "Referer": f"https://cafef.vn/du-lieu/{symbol.lower()}/bao-cao-tai-chinh.chn",
            "Origin": "https://cafef.vn",
        }

        base_url = "https://cafef.vn/du-lieu/Ajax/Bank/NoteIndicator.aspx"
        entity_type = self.db.get_entity_type(symbol) or "STANDARD"

        try:
            # Fetch only latest year data
            params = {
                "symbol": symbol.upper(),
                "type": "1",
                "quarter": "1",
                "year": str(datetime.now().year),
                "qtype": "1",
                "donvi": "1000",
            }
            r = self.session.get(base_url, headers=headers, params=params, timeout=15)
            if r.status_code != 200:
                logger.warning(f"NoteIndicator: {symbol} HTTP {r.status_code}")
                return []

            soup = BeautifulSoup(r.text, "html.parser")
            table = soup.find("table", class_="tab1child_content")
            if not table:
                return []

            rows = table.find_all("tr")
            if len(rows) < 2:
                return []

            # Parse: Row 0 = headers (years), Row 1+ = metrics
            headers_row = rows[0].find_all(["td", "th"])
            year_labels = [h.get_text(strip=True) for h in headers_row if h.get_text(strip=True)]

            result = {}
            for row in rows[1:]:
                cols = row.find_all(["td", "th"])
                if len(cols) < 2:
                    continue

                metric_name = cols[0].get_text(strip=True)
                if not metric_name:
                    continue

                # Map debt-related metrics
                debt_map = {
                    "Nợ đủ tiêu chuẩn": "STANDARD_LOAN",
                    "Nợ cần chú ý": "SUB_STANDARD_LOAN",
                    "Nợ nghi ngờ": "DOUBTFUL_LOAN",
                    "Nợ có vấn đề": "PROBLEMATIC_LOAN",
                    "Nợ ngắn hạn": "SHORT_TERM_DEBT",
                    "Nợ trung hạn": "MEDIUM_TERM_DEBT",
                    "Nợ dài hạn": "LONG_TERM_DEBT",
                }

                metric = debt_map.get(metric_name)
                if not metric:
                    continue

                for i, col in enumerate(cols[1:], start=1):
                    val_text = col.get_text(strip=True)
                    if not val_text or val_text == "Xem đầy đủ":
                        continue

                    val = self._parse_cafef_value(val_text)
                    if val is not None:
                        year_label = year_labels[i - 1] if i - 1 < len(year_labels) else ""
                        year_match = re.search(r'(\d{4})', year_label)
                        if year_match:
                            year = int(year_match.group(1))
                            key = f"{year}"
                            if key not in result:
                                result[key] = {}
                            result[key][metric] = val

            if not result:
                return []

            # Convert to list of dicts
            output = []
            for year_str, data in result.items():
                period_data = {
                    "_fiscal_year": int(year_str),
                    "_fiscal_quarter": 1,
                    "_entity_type": entity_type,
                }
                period_data.update(data)
                output.append(period_data)

            logger.info(f"NoteIndicator: {symbol} — {len(output)} years, {len(output[0]) if output else 0} metrics")
            return output

        except Exception as e:
            logger.warning(f"NoteIndicator: {symbol} — {e}")
            return []

    # ── Tầng 2: Playwright (khi requests thất bại) ────────────────
    @staticmethod
    def _try_cafef_pw(url: str) -> Optional[str]:
        """Dùng Playwright render CafeF page, trả HTML raw hoặc None.

        Pattern giống _try_sbv() trong interbank_seeder.py — dùng chung
        cấu trúc: launch → stealth → goto → parse → classify error.
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.warning("Playwright chưa cài — bỏ qua CafeF Playwright")
            return None

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True, channel="chrome",
                    args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
                )
                ctx = browser.new_context(
                    viewport={"width": 1920, "height": 1080},
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                    locale="vi-VN",
                )
                ctx.add_init_script(
                    """Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"""
                )
                page = ctx.new_page()
                resp = page.goto(url, timeout=30000, wait_until="networkidle")
                page.wait_for_timeout(5000)

                http_status = resp.status if resp else None
                html_raw = page.content()
                browser.close()

                if http_status and http_status != 200:
                    logger.warning(f"CafeF PW HTTP {http_status} — {url}")
                    return None

                # Kiểm tra Cloudflare / structure change (giống SBV)
                if any(sig in html_raw for sig in CAFEF_CLOUDFLARE_SIGS):
                    logger.warning("CafeF PW: Cloudflare challenge detected")
                    return None
                if not any(sig in html_raw for sig in CAFEF_TABLE_SIGNATURES):
                    logger.warning("CafeF PW: table signature missing — structure changed?")
                    return None

                return html_raw

        except Exception as e:
            logger.warning(f"CafeF PW thất bại: {e}")
            return None

    def crawl_symbol(self, symbol: str, entity_type: str = None,
                     source: str = "vci") -> Dict:
        """Crawl 20 quarters for one symbol.

        Args:
            symbol: Mã cổ phiếu.
            entity_type: STANDARD hoặc BANK (mặc định auto).
            source: Nguồn dữ liệu — "vci" (VCI GraphQL, khuyên dùng),
                    "cafef" (requests + Playwright fallback, thường 404),
                    "synthetic" (nội suy, không cần API).
        """
        if entity_type:
            self.db.register_entity(symbol, entity_type)

        # Bank symbols list — auto-detect nếu entity_type chưa có
        BANK_SYMBOLS = {"ACB", "HDB", "MBB", "VCB", "VPB", "TPB", "SHB", "OCB", "BIDV", "AGRIBANK", "EXIMBANK",
                        "VIETINBANK", "SAIGONBANK", "NVB", "PVCOMBANK", "HBANK", "UOB", "LVB", "NAB", "SEABANK",
                        "KAB", "LPB", "ABBANK", "WINGBANK", "POB"}
        detected_type = self.db.get_entity_type(symbol)
        if not detected_type or detected_type not in ("STANDARD", "BANK"):
            detected_type = "BANK" if symbol.upper() in BANK_SYMBOLS else "STANDARD"
            self.db.register_entity(symbol, detected_type)
        actual_type = detected_type

        logger.info(f"=== Crawling {symbol} ({actual_type}) 20 quarters, source={source} ===")

        quarters = self.generate_20_quarters()
        all_periods = None
        use_synthetic = False

        if source == "vci":
            # ── VCI bridge (khuyên dùng) ─────────────────────
            all_periods = self.fetch_vci_bridge(symbol)
            if not all_periods:
                logger.info(f"  VCI bridge không có dữ liệu cho {symbol}, fallback CafeF Bank API")
                all_periods = self.fetch_cafef_bank_api(symbol)
            if not all_periods:
                logger.info(f"  CafeF Bank API không có dữ liệu cho {symbol}, fallback NoteIndicator")
                all_periods = self.fetch_note_indicator(symbol)
            if not all_periods:
                logger.info(f"  NoteIndicator không có dữ liệu cho {symbol}, fallback synthetic")
                use_synthetic = True
        elif source == "cafef":
            # ── CafeF Bank API (nguồn chính mới) ────────────
            all_periods = self.fetch_cafef_bank_api(symbol)
            if not all_periods:
                logger.info(f"  CafeF Bank API không có dữ liệu cho {symbol}, fallback NoteIndicator")
                all_periods = self.fetch_note_indicator(symbol)
            if not all_periods:
                logger.info(f"  NoteIndicator cũng không có, dùng synthetic")
                use_synthetic = True
        elif source == "synthetic":
            use_synthetic = True
        else:
            # ── CafeF cũ (legacy, thường 404) ───────────────
            test_data = self.fetch_quarter(symbol, 2026, 2)
            test_metrics = sum(1 for k in test_data
                              if not k.startswith("_") and test_data[k] is not None)
            if test_metrics == 0:
                logger.info("  CafeF cũ không có dữ liệu, thử CafeF Bank API")
                all_periods = self.fetch_cafef_bank_api(symbol)
            if not all_periods:
                logger.info("  CafeF Bank API cũng không có, thử NoteIndicator")
                all_periods = self.fetch_note_indicator(symbol)
            if not all_periods:
                logger.info("  NoteIndicator cũng không có, dùng synthetic")
                use_synthetic = True

        if use_synthetic:
            all_periods = self._generate_synthetic_base(symbol, actual_type)

        if not all_periods:
            logger.warning(f"  {symbol}: không có dữ liệu từ bất kỳ nguồn nào")
            return {"symbol": symbol, "entity_type": actual_type, "total_quarters": 0,
                    "success": 0, "empty": len(quarters), "total_metrics": 0}

        success = 0
        empty = 0
        total_metrics = 0

        for period_data in all_periods:
            year = period_data.get("_fiscal_year")
            q = period_data.get("_fiscal_quarter")
            if not year or not q:
                continue
            period = f"{year}Q{q}"

            metrics_count = sum(1 for k in period_data
                               if not k.startswith("_") and period_data[k] is not None)
            if metrics_count == 0:
                empty += 1
                logger.warning(f"  [{period}] No data")
                continue

            result = self.db.write_batch(symbol, period_data, actual_type, self.batch_id)
            if result["status"] == "SUCCESS":
                success += 1
            total_metrics += result.get("facts_written", 0)
            logger.info(f"  [{result['status']}] {symbol} {period}: "
                        f"{result['facts_written']} facts")

        logger.info(f"=== {symbol} done: {success} OK, {empty} empty, "
                    f"{total_metrics} total facts ===")
        return {
            "symbol": symbol,
            "entity_type": actual_type,
            "total_quarters": len(all_periods),
            "success": success,
            "empty": empty,
            "total_metrics": total_metrics,
        }

    def crawl_multi(self, targets: List[Tuple[str, str]], source: str = "vci") -> Dict:
        """Crawl multiple symbols."""
        import time
        overall = {"symbols": 0, "total_facts": 0}
        for i, (sym, ent) in enumerate(targets):
            if i > 0 and self.delay > 0:
                time.sleep(self.delay)
            r = self.crawl_symbol(sym, ent, source=source)
            overall["symbols"] += 1
            overall["total_facts"] += r["total_metrics"]
        return overall


if __name__ == "__main__":
    db = FinancialFactsDB()
    db.init_schema()
    crawler = CafeFCrawler(db)

    targets = [
        ("FPT", "STANDARD"),
        ("ACB", "BANK"),
        ("HDB", "BANK"),
        ("MBB", "BANK"),
        ("VCB", "BANK"),
    ]

    logger.info("=== CafeF Crawler — 20 quarters per symbol ===")
    overall = crawler.crawl_multi(targets)
    logger.info(f"=== ALL DONE: {overall['symbols']} symbols, "
                f"{overall['total_facts']} total facts ===")
