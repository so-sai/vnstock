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
║  4. SYNTHETIC (tầng 4 — KHÓA VĨNH VIỄN — Zero-Hallucination)          ║
║     - _generate_synthetic_base() — ĐÃ BỊ VÔ HIỆU HÓA, luôn trả []      ║
║     - Sắc lệnh 2026-08-04: CẤM bịa dữ liệu. Nguồn thật chết →         ║
║       SEVERE_GAP → Governor ép DŨNG NGOẠI (100% Cash), KHÔNG sinh số  ║
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

# WHY (kiến trúc crawl):
# WHY: Xây dựng theo THÁP fallback 4 tầng (requests → Playwright → REST JSON
#   API → synthetic): endpoint nào cũng có thể chết bất kỳ lúc nào (404/DNS
#   thay đổi) → không có nguồn "duy nhất", cascade đảm bảo luôn nạp được dữ liệu.
# WHY: URL_MAP là bảng duy nhất ghi trạng thái ALIVE/DEAD của từng endpoint →
#   khỏi đoán lại nguồn nào còn sống mỗi lần crawl.
# WHY: Synthetic (nội suy) là phương án CUỐI CÙNG — đánh dấu rõ để không nhầm
#   dữ liệu giả với dữ liệu thật.

import logging
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

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
    "CAFEF_FULL_STATEMENT_CF": {
        "url": "https://cafef.vn/du-lieu/bao-cao-tai-chinh/{SYM}/CashFlow/{year}/{qtr}/0/0/...chn",
        "method": "GET",
        "type": "HTML table (4-quarter window)",
        "data": ["Bảng LƯU CHUYỂN TIỀN TỆ đầy đủ: CFO, CFI, CFF, CAPEX"],
        "status": "ALIVE",  # ✅ verified 2026-08-01 — GIẢI QUYẾT lỗ hổng CFO BCM/VRE
        "lib": "requests + BeautifulSoup (fetch_cafef_cashflow)",
        "notes": "Mỗi trang trả cửa sổ 4 quý (td.h_t label). Fetch Q4 mỗi năm + "
        "Q2 năm hiện tại phủ đủ 20 quý. Giá trị VND đầy đủ (không nhân đơn vị).",
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
    "VNDIRECT_FININFO": {
        "url": "https://fininfo-api.vndirect.com.vn/v4/financial_statements",
        "method": "GET",
        "type": "REST JSON",
        "data": ["BCTC 4 bảng JSON (CFO, nợ vay ngắn/dài hạn, chi phí lãi vay)"],
        "status": "UNVERIFIED_DNS",  # ⚠️ DNS fail từ môi trường dev 2026-07-31
        "lib": "requests (fetch_vndirect_api)",
        "notes": "VNDirect Fininfo API — q=reportType:QUARTER~symbol:{sym}~modelType:1. "
        "Mô hình: BCTC full 4 bảng, JSON chuẩn. Cần kiểm chứng lại khi network cho phép.",
    },
    "TCBS_FINAPI": {
        "url": "https://finapi.tcbs.com.vn/v1/stock/{symbol}/financial-statement",
        "method": "GET",
        "type": "REST JSON",
        "data": ["BCTC JSON (8 quý gần nhất, 4 bảng)"],
        "status": "UNVERIFIED_DNS",  # ⚠️ DNS fail từ môi trường dev 2026-07-31
        "lib": "requests (fetch_tcbs_api)",
        "notes": "TCBS FinAPI — type=BALANCE_SHEET|INCOME_STATEMENT|CASH_FLOW&size=20&isAll=true. "
        "Dữ liệu chuẩn hóa, đủ CFO + nợ chi tiết. Cần kiểm chứng lại khi network cho phép.",
    },
    "VIETSTOCK_FININFO": {
        "url": "https://finance.vietstock.vn/{symbol}/tai-chinh.htm",
        "method": "POST /data/financeinfo + BCTT_*",
        "type": "JSON (Playwright + fetch)",
        "data": [
            "financeinfo BCTQ: 4 quý, 17 rows (KQKD 5 + CDKT 6 + CSTC 6)",
            "BCTT tab: 9 quý, 46 norms (CASH_EQUIV, RECEIVABLES, INVENTORY, LONG_TERM_DEBT, COGS...)",
        ],
        "status": "ALIVE",  # ✅ verified 2026-07-31
        "lib": "Playwright sync_api channel='chrome' (fetch_vietstock_api)",
        "notes": "Nguồn thứ 4 (kiểm tra chéo). Merge financeinfo + BCTT. "
        "BCTC CHI TIẾT (CDKT/KQKD/LCTT_GetListReportData) → PAYWALL "
        "(RequestUpgradeAccount_Permission, cần VietstockPro). "
        "BCTT tab free: GetListReportNorm_BCTT_ByStockCode (46 norms) + "
        "BCTT_GetListReportData (38 periods) + "
        "GetReportDataDetailValue_BCTT_ByReportDataIds (9 periods, capped). "
        "BCTT metrics: REVENUE/COGS/GROSS_PROFIT/EBIT/NET_INCOME/EPS + "
        "CASH_EQUIV/RECEIVABLES/INVENTORY + CURRENT_ASSETS/TOTAL_ASSETS/"
        "TOTAL_LIABILITIES/CURRENT_LIAB/LONG_TERM_DEBT/TOTAL_EQUITY + "
        "BOOK_VALUE_PS. Playwright phải dùng channel='chrome'.",
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
    "tableContent",
    "Doanh thu thuần",
    "Lợi nhuận gộp",
    "Tổng cộng tài sản",
    "Vốn chủ sở hữu",
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
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
            }
        )

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
        # WHY: CafeF hiển thị đơn vị TRIỆU VND và số âm đặt trong ngoặc
        # "(1.234)" → *1_000_000 để quy về VND; dấu '.' là phân tách nghìn.
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
        return f"https://s.cafef.vn/bao-cao-tai-chinh/{symbol}/{st_type}/{year}/{quarter}/0/0/bao-cao-tai-chinh-.chn"

    def _try_alt_url(self, symbol: str, st_type: int, year: int, quarter: int) -> str:
        label = {1: "balance", 2: "incstament", 3: "cashflow"}
        return (
            f"https://s.cafef.vn/soc/bao-cao-tai-chinh-{symbol.lower()}/"
            f"{label.get(st_type, 'incstament')}.chn"
            f"?year={year}&quarter={quarter}"
        )

    def fetch_statement(self, symbol: str, st_type: int, year: int, quarter: int) -> Dict[str, float]:
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
                reason = "force Playwright" if self.use_playwright else "requests thất bại, thử Playwright"
                logger.info(f"  CafeF: {reason} cho {url}")
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
        entity_type = self.db.get_entity_type(symbol)

        data = {
            "_fiscal_year": year,
            "_fiscal_quarter": quarter,
        }

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
        """KHÓA VĨNH VIỄN — Zero-Hallucination Policy.

        Sắc lệnh 2026-08-04: TUYỆT ĐỐI CẤM sinh dữ liệu bịa (hallucinated data).
        Trước đây hàm này nội suy 20 quý dữ liệu giả (debt_2026=25e12, rev_2026=9.85e12...)
        để "che lỗi crash" khi mọi nguồn thật chết. Hậu quả: các giá trị vẽ ra này trá
        hình nguồn thật trong DB, dẫn tới quyết định giải ngân trăm tỷ dựa trên ảo giác.

        Quy tắc mới: nếu MỌI nguồn thật đều rỗng → trả [] (KHÔNG bịa dữ liệu). Lớp trên
        (crawl_symbol) ghi nhận NO_DATA, DataIntegrityAuditor đánh dấu SEVERE_GAP, Governor
        ép vị thế DŨNG NGOẠI (100% Cash). Thà lỡ cơ hội còn hơn mất tiền vì số vẽ.
        """
        logger.error(
            f"[ZERO-HALLUCINATION] _generate_synthetic_base KHÔNG CÒN ĐƯỢC PHÉP CHẠY "
            f"(symbol={symbol}, entity_type={entity_type}). Nguồn thật chết → trả [] thay vì bịa dữ liệu."
        )
        return []
        base_data = {
            "FPT": {
                "type": "STANDARD",
                "rev_2022": 5_500_000_000_000,
                "rev_2026": 9_850_000_000_000,
                "ni_2022": 1_200_000_000_000,
                "ni_2026": 2_100_000_000_000,
                "assets_2022": 55_000_000_000_000,
                "assets_2026": 82_000_000_000_000,
                "equity_2022": 22_000_000_000_000,
                "equity_2026": 35_000_000_000_000,
                "cfo_2022": 1_500_000_000_000,
                "cfo_2026": 2_520_000_000_000,
                "cash_2022": 7_000_000_000_000,
                "cash_2026": 12_000_000_000_000,
                "debt_2022": 15_000_000_000_000,
                "debt_2026": 25_000_000_000_000,
                "shares": 292_000_000,
            },
            "ACB": {
                "type": "BANK",
                "nii_2022": 12_000_000_000_000,
                "nii_2026": 18_000_000_000_000,
                "np_2022": 6_500_000_000_000,
                "np_2026": 10_000_000_000_000,
                "assets_2022": 380_000_000_000_000,
                "assets_2026": 578_000_000_000_000,
                "equity_2022": 45_000_000_000_000,
                "equity_2026": 70_000_000_000_000,
                "loans_2022": 320_000_000_000_000,
                "loans_2026": 480_000_000_000_000,
                "deposits_2022": 350_000_000_000_000,
                "deposits_2026": 520_000_000_000_000,
                "npl_2022": 0.018,
                "npl_2026": 0.015,
                "casa_2022": 0.22,
                "casa_2026": 0.30,
                "shares": 2_200_000_000,
            },
            "HDB": {
                "type": "BANK",
                "nii_2022": 9_000_000_000_000,
                "nii_2026": 14_000_000_000_000,
                "np_2022": 5_000_000_000_000,
                "np_2026": 8_000_000_000_000,
                "assets_2022": 280_000_000_000_000,
                "assets_2026": 428_000_000_000_000,
                "equity_2022": 32_000_000_000_000,
                "equity_2026": 50_000_000_000_000,
                "loans_2022": 230_000_000_000_000,
                "loans_2026": 350_000_000_000_000,
                "deposits_2022": 250_000_000_000_000,
                "deposits_2026": 380_000_000_000_000,
                "npl_2022": 0.022,
                "npl_2026": 0.018,
                "casa_2022": 0.18,
                "casa_2026": 0.25,
                "shares": 1_800_000_000,
            },
            "MBB": {
                "type": "BANK",
                "nii_2022": 10_500_000_000_000,
                "nii_2026": 16_000_000_000_000,
                "np_2022": 6_000_000_000_000,
                "np_2026": 9_500_000_000_000,
                "assets_2022": 340_000_000_000_000,
                "assets_2026": 507_000_000_000_000,
                "equity_2022": 42_000_000_000_000,
                "equity_2026": 65_000_000_000_000,
                "loans_2022": 280_000_000_000_000,
                "loans_2026": 420_000_000_000_000,
                "deposits_2022": 300_000_000_000_000,
                "deposits_2026": 450_000_000_000_000,
                "npl_2022": 0.020,
                "npl_2026": 0.016,
                "casa_2022": 0.20,
                "casa_2026": 0.28,
                "shares": 2_000_000_000,
            },
            "VCB": {
                "type": "BANK",
                "nii_2022": 9_500_000_000_000,
                "nii_2026": 14_500_000_000_000,
                "np_2022": 5_500_000_000_000,
                "np_2026": 9_200_000_000_000,
                "assets_2022": 450_000_000_000_000,
                "assets_2026": 650_000_000_000_000,
                "equity_2022": 55_000_000_000_000,
                "equity_2026": 85_000_000_000_000,
                "loans_2022": 310_000_000_000_000,
                "loans_2026": 450_000_000_000_000,
                "deposits_2022": 350_000_000_000_000,
                "deposits_2026": 500_000_000_000_000,
                "npl_2022": 0.016,
                "npl_2026": 0.012,
                "casa_2022": 0.24,
                "casa_2026": 0.32,
                "shares": 1_800_000_000,
            },
            "HPG": {
                "type": "STANDARD",
                "rev_2022": 55_000_000_000_000,
                "rev_2026": 68_000_000_000_000,
                "ni_2022": 6_500_000_000_000,
                "ni_2026": 8_200_000_000_000,
                "assets_2022": 170_000_000_000_000,
                "assets_2026": 210_000_000_000_000,
                "equity_2022": 90_000_000_000_000,
                "equity_2026": 115_000_000_000_000,
                "cfo_2022": 8_000_000_000_000,
                "cfo_2026": 10_500_000_000_000,
                "cash_2022": 12_000_000_000_000,
                "cash_2026": 18_000_000_000_000,
                "debt_2022": 45_000_000_000_000,
                "debt_2026": 55_000_000_000_000,
                "shares": 3_200_000_000,
            },
            "VHM": {
                "type": "STANDARD",
                "rev_2022": 68_000_000_000_000,
                "rev_2026": 80_000_000_000_000,
                "ni_2022": 12_000_000_000_000,
                "ni_2026": 15_000_000_000_000,
                "assets_2022": 520_000_000_000_000,
                "assets_2026": 600_000_000_000_000,
                "equity_2022": 190_000_000_000_000,
                "equity_2026": 240_000_000_000_000,
                "cfo_2022": 10_000_000_000_000,
                "cfo_2026": 14_000_000_000_000,
                "cash_2022": 15_000_000_000_000,
                "cash_2026": 25_000_000_000_000,
                "debt_2022": 180_000_000_000_000,
                "debt_2026": 200_000_000_000_000,
                "shares": 4_000_000_000,
            },
            "DGC": {
                "type": "STANDARD",
                "rev_2022": 12_000_000_000_000,
                "rev_2026": 18_000_000_000_000,
                "ni_2022": 2_800_000_000_000,
                "ni_2026": 4_200_000_000_000,
                "assets_2022": 22_000_000_000_000,
                "assets_2026": 35_000_000_000_000,
                "equity_2022": 14_000_000_000_000,
                "equity_2026": 22_000_000_000_000,
                "cfo_2022": 3_200_000_000_000,
                "cfo_2026": 5_000_000_000_000,
                "cash_2022": 3_500_000_000_000,
                "cash_2026": 6_000_000_000_000,
                "debt_2022": 4_500_000_000_000,
                "debt_2026": 7_000_000_000_000,
                "shares": 380_000_000,
            },
            "MWG": {
                "type": "STANDARD",
                "rev_2022": 45_000_000_000_000,
                "rev_2026": 55_000_000_000_000,
                "ni_2022": 1_800_000_000_000,
                "ni_2026": 2_800_000_000_000,
                "assets_2022": 60_000_000_000_000,
                "assets_2026": 75_000_000_000_000,
                "equity_2022": 22_000_000_000_000,
                "equity_2026": 32_000_000_000_000,
                "cfo_2022": 2_500_000_000_000,
                "cfo_2026": 4_000_000_000_000,
                "cash_2022": 5_000_000_000_000,
                "cash_2026": 8_000_000_000_000,
                "debt_2022": 25_000_000_000_000,
                "debt_2026": 30_000_000_000_000,
                "shares": 1_200_000_000,
            },
            "GAS": {
                "type": "STANDARD",
                "rev_2022": 85_000_000_000_000,
                "rev_2026": 100_000_000_000_000,
                "ni_2022": 8_500_000_000_000,
                "ni_2026": 11_000_000_000_000,
                "assets_2022": 80_000_000_000_000,
                "assets_2026": 100_000_000_000_000,
                "equity_2022": 50_000_000_000_000,
                "equity_2026": 65_000_000_000_000,
                "cfo_2022": 10_000_000_000_000,
                "cfo_2026": 13_000_000_000_000,
                "cash_2022": 12_000_000_000_000,
                "cash_2026": 18_000_000_000_000,
                "debt_2022": 18_000_000_000_000,
                "debt_2026": 22_000_000_000_000,
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

                data.update(
                    {
                        "NII": nii_adj,
                        "NET_PROFIT": np_adj,
                        "PROVISION_EXPENSE": provision,
                        "TOTAL_ASSETS": assets,
                        "TOTAL_LIABILITIES": assets - equity,
                        "CUSTOMER_LOANS": loans,
                        "CUSTOMER_DEPOSITS": deposits,
                        "TOTAL_EQUITY": equity,
                        "CASH_AND_BALANCES": cash,
                        "EPS": eps,
                        "SHARES_OUT": bd["shares"],
                        "NPL_RATIO": npl,
                        "CASA_RATIO": casa,
                        "CFO": np_adj * 1.05,
                        "TOI": toi,
                        "OPERATING_EXPENSE": opex,
                    }
                )
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

                data.update(
                    {
                        "REVENUE": rev_adj,
                        "COGS": rev_adj - gp,
                        "GROSS_PROFIT": gp,
                        "NET_INCOME": ni_adj,
                        "EBITDA": ebitda,
                        "INVENTORY": inv,
                        "RECEIVABLES": rec,
                        "INTEREST_EXPENSE": interest,
                        "EBIT": ebitda - interest * 0.4,
                        "TOTAL_ASSETS": assets,
                        "TOTAL_LIABILITIES": assets - equity,
                        "CURRENT_ASSETS": ca,
                        "CURRENT_LIAB": cl,
                        "TOTAL_EQUITY": equity,
                        "TOTAL_DEBT": debt,
                        "SHORT_TERM_DEBT": debt * 0.6,
                        "LONG_TERM_DEBT": debt * 0.4,
                        "CASH_EQUIV": cash,
                        "CFO": cfo_adj,
                        "CAPEX": capex,
                        "EPS": eps,
                        "SHARES_OUT": bd["shares"],
                        "BOOK_VALUE_PS": equity / bd["shares"],
                    }
                )

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
            html = r.text
        except Exception as e:
            logger.warning(f"CafeF Bank API: {symbol} — requests thất bại: {e}")
            html = None

        if not html:
            # ── Tầng 2: Playwright render URL sống (khi REST bị chặn/Cloudflare) ──
            logger.info(f"  CafeF Bank API: requests thất bại, thử Playwright render {base_url}")
            html = self._try_cafef_pw(base_url)
            if html:
                logger.info(f"  CafeF Bank API: Playwright render OK ({len(html)} bytes)")

        if not html:
            return []

        return self._parse_cafef_bank_api(symbol, entity_type, html)

    def _parse_cafef_bank_api(self, symbol: str, entity_type: str, html: str) -> List[Dict]:
        """Parse Bank API (BHoSoCongTy) HTML → list of quarter dicts.

        Tách riêng khỏi fetch_cafef_bank_api để tái dùng giữa requests
        (Tầng 1) và Playwright render (Tầng 2).
        """
        try:
            soup = BeautifulSoup(html, "html.parser")
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
            # WHY: gom theo key "YYYYQ" rồi mới chuyển sang list — các metric của
            # cùng 1 quý nằm rải ở nhiều dòng, dict key period cho phép update
            # từng metric mà không cần index mảng.
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
                        year_match = re.search(r"(\d{4})", quarter_label)
                        q_match = re.search(r"Quý\s+(\d+)", quarter_label)
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

    def fetch_cafef_cashflow(self, symbol: str) -> List[Dict]:
        """Dùng CafeF full-statement endpoint (/du-lieu/bao-cao-tai-chinh/...)
        lấy bảng LƯU CHUYỂN TIỀN TỆ (CF) cho 20 quý.

        Endpoint mới (verified 2026-08-01): mỗi trang trả 1 cửa sổ 4 quý
        với label rõ ràng (td.h_t = "Quý 3- 2025"). Chỉ cần fetch Q4 của
        từng năm + Q2 năm hiện tại là phủ đủ 20 quý.
        WHY: cửa sổ 4 quý dịch 1 quý/trang → fetch Q4 mỗi năm trùng lặp 3 quý
        kề trước, phủ toàn bộ chuỗi chỉ với ~6 request thay vì 20.

        Trả về list of dict (mỗi dict = 1 quarter) chỉ chứa các metric CF:
        CFO, CFI, CFF, CAPEX. Caller merge với dữ liệu BS/IS.
        """
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "*/*",
            "Accept-Language": "vi-VN,vi;q=0.9",
            "Referer": f"https://cafef.vn/du-lieu/{symbol.lower()}/bao-cao-tai-chinh.chn",
        }
        entity_type = self.db.get_entity_type(symbol) or "STANDARD"

        # Các trang cần fetch: Q4 mỗi năm 2021-2025 + Q2/2026 (dữ liệu mới nhất)
        pages = [(y, 4) for y in range(2021, 2026)]
        pages.append((2026, 2))

        quarters = {}
        for year, qtr in pages:
            url = (
                f"https://cafef.vn/du-lieu/bao-cao-tai-chinh/{symbol.upper()}/CashFlow/{year}/{qtr}/0/0/luu-chuyen-tien-te.chn"
            )
            try:
                r = self.session.get(url, headers=headers, timeout=20)
                if r.status_code != 200:
                    logger.warning(f"CafeF CF: {symbol} {year}Q{qtr} HTTP {r.status_code}")
                    continue
                periods = self._parse_cafef_cf_page(r.text, entity_type)
                for key, data in periods.items():
                    if key in quarters:
                        quarters[key].update(data)
                    else:
                        quarters[key] = data
                if periods:
                    logger.info(f"CafeF CF: {symbol} {year}Q{qtr} → {len(periods)} quý")
            except Exception as e:
                logger.warning(f"CafeF CF: {symbol} {year}Q{qtr} — {e}")

        if not quarters:
            return []

        result = []
        for key, data in quarters.items():
            parts = key.rsplit("Q", 1)
            if len(parts) != 2:
                continue
            result.append(
                {
                    "_fiscal_year": int(parts[0]),
                    "_fiscal_quarter": int(parts[1]),
                    "_entity_type": entity_type,
                    **data,
                }
            )
        result.sort(key=lambda d: (d["_fiscal_year"], d["_fiscal_quarter"]))
        cf_metrics = sorted({k for d in result for k in d if not k.startswith("_")})
        logger.info(f"CafeF CF: {symbol} — {len(result)} quý, metrics={cf_metrics}")
        return result

    def _parse_cafef_cf_page(self, html: str, entity_type: str) -> Dict[str, Dict]:
        """Parse 1 trang CashFlow → { '2025Q3': {'CFO': val, ...}, ... }.

        Cấu trúc: header row (td.h_t) chứa label 4 quý, dòng dữ liệu
        align theo cột. Giá trị là VND đầy đủ (không nhân đơn vị).
        """
        soup = BeautifulSoup(html, "html.parser")
        period_labels = [c.get_text(strip=True) for c in soup.select("td.h_t") if c.get_text(strip=True)]
        if not period_labels:
            return {}

        # Map mỗi cột label → key "YYYYQQ"
        col_periods = []
        for label in period_labels:
            ym = re.search(r"(\d{4})", label)
            qm = re.search(r"Quý\s+(\d+)", label)
            if ym and qm:
                col_periods.append(f"{ym.group(1)}Q{qm.group(1)}")
            else:
                col_periods.append(None)

        out = {}
        CF_ONLY_METRICS = {"CFO", "CFI", "CFF", "CAPEX"}
        for tr in soup.find_all("tr"):
            tds = tr.find_all(["td", "th"])
            if not tds:
                continue
            label = tds[0].get_text(strip=True)
            if not label:
                continue
            metric = self._map_cafef_metric(label, entity_type)
            if not metric or metric not in CF_ONLY_METRICS:
                continue
            for i, period in enumerate(col_periods):
                if period is None or i + 1 >= len(tds):
                    continue
                raw = tds[i + 1].get_text(strip=True)
                if not raw or raw == "-":
                    continue
                val = self._parse_cafef_value_full(raw)
                if val is None:
                    continue
                out.setdefault(period, {})[metric] = val
        return out

    def _parse_cafef_value_full(self, raw: str) -> Optional[float]:
        """Parse số VND đầy đủ: '-1.095.665.704.486' → float (không nhân đơn vị)."""
        # WHY: endpoint CashFlow trả giá trị VND ĐẦY ĐỦ (không theo donvi=1000)
        # → KHÔNG nhân 1_000_000 như _parse_cafef_value, tránh inflate 1000x.
        raw = raw.strip()
        if not raw or raw == "-":
            return None
        negative = raw.startswith("(") and raw.endswith(")")
        if negative:
            raw = raw[1:-1]
        raw = raw.replace(".", "")
        raw = raw.replace(",", "")
        try:
            val = float(raw)
            return -val if negative else val
        except ValueError:
            return None

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
            "Lưu chuyển tiền thuần từ hoạt động đầu tư": "CFI",
            "Lưu chuyển tiền thuần từ hoạt động tài chính": "CFF",
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
            "Lưu chuyển tiền thuần từ hoạt động đầu tư": "CFI",
            "Lưu chuyển tiền thuần từ hoạt động tài chính": "CFF",
            # ── Balance sheet — Nợ vay ────────────────────────────
            "Vay và nợ thuê tài chính ngắn hạn": "SHORT_TERM_DEBT",
            "Nợ vay ngắn hạn": "SHORT_TERM_DEBT",
            "Vay và nợ thuê tài chính dài hạn": "LONG_TERM_DEBT",
            "Nợ vay dài hạn": "LONG_TERM_DEBT",
            # ── Income statement — Chi phí lãi vay ───────────────
            "Chi phí lãi vay": "INTEREST_EXPENSE",
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
        """Dùng VCI (vnstock wide-format) làm nguồn chính.

        Bản đồ URL: URL_MAP["VCI_GRAPHQL"] (NEEDS_API_KEY).
        Endpoint: https://trading.vietcap.com.vn/data-mt/graphql

        Trả về list of dict (mỗi dict = 1 quarter thật) giống format
        của fetch_quarter(), để write_batch() xử lý.

        WHY: vnstock 4.0.5 trả WIDE format (item_id rows + period cols) —
        hàm cũ lấy df.iloc[0] (dòng đầu = 1 metric, không phải 1 quý) rồi
        nhân bản cho 20 quý → dữ liệu sai. Delegate qua
        VnstockCrawler.fetch_financials_vnstock() để dùng chung parser
        wide-format đã fix (VNSTOCK_ITEM_ID_MAP + _period_from_col).
        """
        try:
            from src.financial.financial_facts import VnstockCrawler
        except ImportError as e:
            logger.warning(f"VCI bridge: không import được financial_facts — {e}")
            return []

        try:
            crawler = VnstockCrawler(db=self.db, source="VCI")
            periods = crawler.fetch_financials_vnstock(symbol, limit=30)
        except Exception as e:
            logger.warning(f"VCI bridge: VCI API thất bại cho {symbol} — {e}")
            return []

        if not periods:
            logger.info(f"VCI bridge: {symbol} — không có dữ liệu parsed")
            return []

        entity_type = self.db.get_entity_type(symbol) or "STANDARD"
        for period_data in periods:
            # Tính TOTAL_DEBT từ nợ ngắn + dài hạn
            short_debt = period_data.get("SHORT_TERM_DEBT", 0) or 0
            long_debt = period_data.get("LONG_TERM_DEBT", 0) or 0
            if short_debt or long_debt:
                period_data["TOTAL_DEBT"] = short_debt + long_debt
            period_data["_entity_type"] = entity_type

        logger.info(f"VCI bridge: {symbol} — {len(periods)} periods parsed")
        return periods

    # ── Tầng 1c: VNDirect Fininfo API bridge ─────────────────
    def fetch_vndirect_api(self, symbol: str) -> List[Dict]:
        """Dùng VNDirect Fininfo API (JSON, BCTC đầy đủ 4 bảng).

        Bản đồ URL: URL_MAP["VNDIRECT_FININFO"] (UNVERIFIED_DNS — cần kiểm chứng
        lại khi network cho phép). Endpoint:
          https://fininfo-api.vndirect.com.vn/v4/financial_statements
          ?q=reportType:QUARTER~symbol:{SYM}~modelType:1&size=20

        WHY: CafeF Bank API (BHoSoCongTy) chỉ trả BCTC Tóm tắt 17 rows → BCM
        thiếu CFO / Nợ vay dài hạn / Chi phí lãi vay → Cash/DEBT=NO DATA.
        VNDirect trả JSON đủ 4 bảng (BS/IS/CF/ratios) → parse được CFO, CAPEX,
        SHORT_TERM_DEBT, LONG_TERM_DEBT, INTEREST_EXPENSE.
        """
        entity_type = self.db.get_entity_type(symbol) or "STANDARD"
        base_url = "https://fininfo-api.vndirect.com.vn/v4/financial_statements"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
            "Accept-Language": "vi-VN,vi;q=0.9",
        }
        params = {
            "q": f"reportType:QUARTER~symbol:{symbol.upper()}~modelType:1",
            "size": "20",
        }
        try:
            r = self.session.get(base_url, headers=headers, params=params, timeout=15)
            if r.status_code != 200:
                logger.warning(f"VNDirect Fininfo: {symbol} HTTP {r.status_code}")
                return []
            payload = r.json()
        except Exception as e:
            logger.warning(f"VNDirect Fininfo: {symbol} — {e}")
            return []

        periods = self._parse_vndirect_json(payload, entity_type)
        logger.info(f"VNDirect Fininfo: {symbol} — {len(periods)} periods parsed")
        return periods

    @staticmethod
    def _parse_vndirect_json(payload, entity_type: str = "STANDARD") -> List[Dict]:
        """Pure parser cho VNDirect Fininfo JSON — deterministic, testable.

        VNDirect trả list of rows; mỗi row chứa các cột camelCase như:
          - reportDate / reportYear / reportQuarter (period label)
          - cashFlowFromOperatingActivities  → CFO
          - shortTermBorrowings / longTermBorrowings → nợ vay
          - interestExpense / interestCost → chi phí lãi vay
          - cashAndCashEquivalents → CASH_EQUIV
          - inventory / receivables / totalCurrentAssets / totalLiabilities
        """
        if isinstance(payload, dict):
            rows = payload.get("data", payload.get("items", payload.get("result", [])))
        else:
            rows = payload or []
        if not isinstance(rows, list):
            return []

        # Ánh xạ cột VNDirect (nhiều alias) → PTCK metric
        col_map = {
            "cashFlowFromOperatingActivities": "CFO",
            "cashFlowFromOperating": "CFO",
            "cashAndCashEquivalents": "CASH_EQUIV",
            "cashEquivalents": "CASH_EQUIV",
            "shortTermBorrowings": "SHORT_TERM_DEBT",
            "shortTermBorrowing": "SHORT_TERM_DEBT",
            "longTermBorrowings": "LONG_TERM_DEBT",
            "longTermBorrowing": "LONG_TERM_DEBT",
            "interestExpense": "INTEREST_EXPENSE",
            "interestCost": "INTEREST_EXPENSE",
            "inventory": "INVENTORY",
            "accountReceivables": "RECEIVABLES",
            "receivables": "RECEIVABLES",
            "totalCurrentAssets": "CURRENT_ASSETS",
            "currentAssets": "CURRENT_ASSETS",
            "totalCurrentLiabilities": "CURRENT_LIAB",
            "currentLiabilities": "CURRENT_LIAB",
            "totalLiabilities": "TOTAL_LIABILITIES",
            "totalAssets": "TOTAL_ASSETS",
            "totalEquity": "TOTAL_EQUITY",
            "equity": "TOTAL_EQUITY",
            "revenue": "REVENUE",
            "totalRevenue": "REVENUE",
            "grossProfit": "GROSS_PROFIT",
            "netProfit": "NET_INCOME",
            "netIncome": "NET_INCOME",
            "capitalExpenditure": "CAPEX",
            "capex": "CAPEX",
        }
        result = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            data = {}
            for col, metric in col_map.items():
                if col in row and row[col] is not None:
                    try:
                        val = float(row[col])
                        if val != 0:
                            data[metric] = val
                    except TypeError, ValueError:
                        pass
            if not data:
                continue

            # Period label: reportDate "2025-06-30" → 2025Q2
            year, quarter = None, None
            rd = row.get("reportDate") or row.get("fiscalDate") or ""
            if isinstance(rd, str) and re.match(r"\d{4}-\d{2}-\d{2}", rd):
                year = int(rd[:4])
                quarter = (int(rd[5:7]) - 1) // 3 + 1
            if year is None:
                year = row.get("reportYear") or row.get("year")
            if quarter is None:
                quarter = row.get("reportQuarter") or row.get("quarter")
            if not year or not quarter:
                continue

            short_debt = data.get("SHORT_TERM_DEBT", 0) or 0
            long_debt = data.get("LONG_TERM_DEBT", 0) or 0
            if short_debt or long_debt:
                data["TOTAL_DEBT"] = short_debt + long_debt

            period_data = {
                "_fiscal_year": int(year),
                "_fiscal_quarter": int(quarter),
                "_entity_type": entity_type,
            }
            period_data.update(data)
            result.append(period_data)
        return result

    # ── Tầng 1d: TCBS FinAPI bridge ──────────────────────────
    def fetch_tcbs_api(self, symbol: str) -> List[Dict]:
        """Dùng TCBS FinAPI (JSON, BCTC 8 quý gần nhất, 4 bảng).

        Bản đồ URL: URL_MAP["TCBS_FINAPI"] (UNVERIFIED_DNS — cần kiểm chứng lại
        khi network cho phép). Endpoint:
          https://finapi.tcbs.com.vn/v1/stock/{SYM}/financial-statement
          ?type=BALANCE_SHEET|INCOME_STATEMENT|CASH_FLOW&size=20&isAll=true

        WHY: TCBS trả dữ liệu chuẩn hóa dạng JSON với CFO, nợ vay ngắn/dài hạn,
        chi phí lãi vay — nguồn thay thế chất lượng cao cho CafeF khi network cho phép.
        """
        entity_type = self.db.get_entity_type(symbol) or "STANDARD"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
            "Accept-Language": "vi-VN,vi;q=0.9",
        }
        sections = {
            "BALANCE_SHEET": "BS",
            "INCOME_STATEMENT": "IS",
            "CASH_FLOW": "CF",
        }
        merged_by_period: Dict[str, Dict] = {}
        for tcbs_type, tag in sections.items():
            url = f"https://finapi.tcbs.com.vn/v1/stock/{symbol.upper()}/financial-statement"
            params = {"type": tcbs_type, "size": "20", "isAll": "true"}
            try:
                r = self.session.get(url, headers=headers, params=params, timeout=15)
                if r.status_code != 200:
                    logger.warning(f"TCBS FinAPI {tcbs_type}: {symbol} HTTP {r.status_code}")
                    continue
                payload = r.json()
            except Exception as e:
                logger.warning(f"TCBS FinAPI {tcbs_type}: {symbol} — {e}")
                continue

            for row in self._parse_tcbs_rows(payload, tag):
                key = f"{row['_fiscal_year']}Q{row['_fiscal_quarter']}"
                merged_by_period.setdefault(key, {}).update(row)

        if not merged_by_period:
            logger.warning(f"TCBS FinAPI: {symbol} — không có dữ liệu")
            return []

        result = []
        for key, data in merged_by_period.items():
            year, quarter = key.rsplit("Q", 1)
            short_debt = data.get("SHORT_TERM_DEBT", 0) or 0
            long_debt = data.get("LONG_TERM_DEBT", 0) or 0
            if short_debt or long_debt:
                data["TOTAL_DEBT"] = short_debt + long_debt
            period_data = {
                "_fiscal_year": int(year),
                "_fiscal_quarter": int(quarter),
                "_entity_type": entity_type,
            }
            period_data.update(data)
            result.append(period_data)
        logger.info(f"TCBS FinAPI: {symbol} — {len(result)} periods")
        return result

    @staticmethod
    def _parse_tcbs_rows(payload, tag: str = "BS") -> List[Dict]:
        """Pure parser cho TCBS FinAPI payload — deterministic, testable.

        TCBS trả {"data": [...]} — mỗi row chứa quarter/year + các cột camelCase.
        Tag phân biệt bảng: BS / IS / CF.
        """
        if isinstance(payload, dict):
            rows = payload.get("data", payload.get("items", []))
        else:
            rows = payload or []
        if not isinstance(rows, list):
            return []

        col_map = {
            "BS": {
                "cashAndCashEquivalents": "CASH_EQUIV",
                "cash": "CASH_EQUIV",
                "shortTermBorrowings": "SHORT_TERM_DEBT",
                "longTermBorrowings": "LONG_TERM_DEBT",
                "inventory": "INVENTORY",
                "receivables": "RECEIVABLES",
                "totalCurrentAssets": "CURRENT_ASSETS",
                "currentAssets": "CURRENT_ASSETS",
                "totalCurrentLiabilities": "CURRENT_LIAB",
                "currentLiabilities": "CURRENT_LIAB",
                "totalLiabilities": "TOTAL_LIABILITIES",
                "totalAssets": "TOTAL_ASSETS",
                "shareHolderEquity": "TOTAL_EQUITY",
                "totalEquity": "TOTAL_EQUITY",
            },
            "IS": {
                "revenue": "REVENUE",
                "totalRevenue": "REVENUE",
                "grossProfit": "GROSS_PROFIT",
                "profitAfterTax": "NET_INCOME",
                "netProfit": "NET_INCOME",
                "interestExpense": "INTEREST_EXPENSE",
                "interestCost": "INTEREST_EXPENSE",
            },
            "CF": {
                "cashFlowFromOperatingActivities": "CFO",
                "cashFlowFromOperation": "CFO",
                "cashFlowFromInvestingActivities": "CFI",
                "cashFlowFromFinancingActivities": "CFF",
                "capitalExpenditure": "CAPEX",
                "capex": "CAPEX",
            },
        }
        mapping = col_map.get(tag, col_map["BS"])
        result = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            data = {}
            for col, metric in mapping.items():
                if col in row and row[col] is not None:
                    try:
                        val = float(row[col])
                        if val != 0:
                            data[metric] = val
                    except TypeError, ValueError:
                        pass
            if not data:
                continue
            year = row.get("year") or row.get("reportYear")
            quarter = row.get("quarter") or row.get("reportQuarter")
            if not year or not quarter:
                continue
            period_data = {
                "_fiscal_year": int(year),
                "_fiscal_quarter": int(quarter),
            }
            period_data.update(data)
            result.append(period_data)
        return result

    # ── Tầng 1e: Vietstock Finance summary bridge (cross-check) ──
    def fetch_vietstock_api(self, symbol: str) -> List[Dict]:
        """Dùng Vietstock Finance free summary (Playwright Windows Native).

        Bản đồ URL: URL_MAP["VIETSTOCK_FININFO"] (ALIVE — verified 2026-07-31).

        WHY: Nguồn dữ liệu độc lập thứ 4 (sau VNDirect/TCBS/CafeF). Dùng cho
        kiểm tra chéo (cross-check) khi VNDirect/TCBS chưa verify DNS.

        ⚠️ FREE TIER GIỚI HẠN: chỉ 4 quý gần nhất, BCTC Tóm tắt — KHÔNG có
        CFO/CAPEX/nợ vay chi tiết. BCTC chi tiết 37 dòng → PAYWALL (VietstockPro).

        Returns:
            List[Dict]: các period có metric summary, rỗng nếu thất bại.
        """
        try:
            from src.financial.vietstock_crawler import VietstockCrawler
        except ImportError:
            try:
                from financial.vietstock_crawler import VietstockCrawler
            except ImportError:
                logger.warning("Không import được VietstockCrawler — bỏ qua")
                return []

        entity_type = self.db.get_entity_type(symbol) or "STANDARD"
        crawler = VietstockCrawler(entity_type=entity_type)
        periods = crawler.fetch_summary(symbol)
        logger.info(f"Vietstock Fininfo: {symbol} — {len(periods)} periods parsed")
        return periods

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
                        year_match = re.search(r"(\d{4})", year_label)
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

        # Windows 11 RAM tối ưu: tắt GPU + chặn tài nguyên rác (image/css/font/media)
        # → RAM dao động 150-220MB. Block stylesheet an toàn vì _try_cafef_pw chỉ đọc
        # page.content() DOM (table signatures), không phụ thuộc CSS render.
        # ⚠️ KHÔNG dùng --single-process: test thực tế trên Windows gây
        #    TargetClosedError (Page.goto: browser closed) — Chromium unstable.
        #
        # WIN11 BLACK-SCREEN BUG (2026-08-01):
        #   --disable-gpu is MANDATORY on Windows 11 for headless Playwright.
        #   Without it, Chromium attempts GPU hardware acceleration even in
        #   headless mode. When the monitor is off (Modern Standby S0), GPU
        #   is in D3 cold. Chromium tries to acquire a GPU render context →
        #   DWM handshake fails → TDR timeout → driver reset gets stuck →
        #   BLACK SCREEN permanently.
        #   --disable-gpu forces software rendering, bypassing the GPU entirely.
        #   This is why ALL scheduled Playwright tasks MUST include this flag.
        WINDOWS_LAUNCH_FLAGS = [
            "--disable-gpu",
            "--no-sandbox",
            "--disable-accelerated-2d-canvas",
            "--no-first-run",
            "--disable-blink-features=AutomationControlled",
        ]
        BLOCKED_RESOURCE_TYPES = {"image", "stylesheet", "font", "media"}

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True,
                    channel="chrome",
                    args=WINDOWS_LAUNCH_FLAGS,
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
                ctx.add_init_script("""Object.defineProperty(navigator, 'webdriver', { get: () => undefined });""")
                page = ctx.new_page()

                # BLOCK RESOURCE: chặn ảnh/css/font/media tiết kiệm RAM (chỉ giữ
                # document + script + xhr/fetch để crawl bảng tài chính).
                def _route(route):
                    rtype = route.request.resource_type
                    if rtype in BLOCKED_RESOURCE_TYPES:
                        route.abort()
                    else:
                        route.continue_()

                page.route("**/*", _route)

                # WHY: networkidle + chờ thêm 5s để JS render xong bảng tài
                # chính (CafeF load data qua AJAX sau DOMContentLoaded).
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

    def crawl_symbol(self, symbol: str, entity_type: str = None, source: str = "vci") -> Dict:
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

        # WHY: entity_type quyết định map metric (STANDARD vs BANK) — auto-detect
        # qua danh sách cứng khi registry chưa có để tránh parse sai bảng BCTC.
        # Bank symbols list — auto-detect nếu entity_type chưa có
        BANK_SYMBOLS = {
            "ACB",
            "HDB",
            "MBB",
            "VCB",
            "VPB",
            "TPB",
            "SHB",
            "OCB",
            "BIDV",
            "AGRIBANK",
            "EXIMBANK",
            "VIETINBANK",
            "SAIGONBANK",
            "NVB",
            "PVCOMBANK",
            "HBANK",
            "UOB",
            "LVB",
            "NAB",
            "SEABANK",
            "KAB",
            "LPB",
            "ABBANK",
            "WINGBANK",
            "POB",
        }
        detected_type = self.db.get_entity_type(symbol)
        if not detected_type or detected_type not in ("STANDARD", "BANK"):
            detected_type = "BANK" if symbol.upper() in BANK_SYMBOLS else "STANDARD"
            self.db.register_entity(symbol, detected_type)
        actual_type = detected_type

        logger.info(f"=== Crawling {symbol} ({actual_type}) 20 quarters, source={source} ===")

        quarters = self.generate_20_quarters()
        all_periods = None
        use_synthetic = False
        # Provenance guard: dấu vết nguồn gốc dữ liệu cuối cùng (No Provenance = No Trust).
        # WHY: ghi chính xác nguồn nào đã sinh ra dữ liệu để DataIntegrityAuditor phát hiện
        # dữ liệu bịa (is_synthetic=1) thay vì chỉ đo density/magnitude mù với nguồn gốc.
        data_source = source
        data_is_synthetic = 0

        # WHY: cascade nguồn xếp theo chất lượng — VCI (BCTC full 4 bảng) →
        # VNDirect/TCBS (JSON chuẩn, đủ CFO) → CafeF Bank API (17 rows) →
        # NoteIndicator (nợ) → Vietstock (cross-check) → synthetic (cuối).
        if source == "vci":
            # ── VCI bridge (khuyên dùng) ─────────────────────
            all_periods = self.fetch_vci_bridge(symbol)
            if not all_periods:
                logger.info(f"  VCI bridge không có dữ liệu cho {symbol}, fallback VNDirect Fininfo")
                all_periods = self.fetch_vndirect_api(symbol)
            if not all_periods:
                logger.info(f"  VNDirect Fininfo không có dữ liệu cho {symbol}, fallback TCBS FinAPI")
                all_periods = self.fetch_tcbs_api(symbol)
            if not all_periods:
                logger.info(f"  TCBS FinAPI không có dữ liệu cho {symbol}, fallback CafeF Bank API")
                all_periods = self.fetch_cafef_bank_api(symbol)
            if not all_periods:
                logger.info(f"  CafeF Bank API không có dữ liệu cho {symbol}, fallback NoteIndicator")
                all_periods = self.fetch_note_indicator(symbol)
            if not all_periods:
                logger.info(f"  NoteIndicator không có dữ liệu cho {symbol}, fallback Vietstock Fininfo")
                all_periods = self.fetch_vietstock_api(symbol)
            if not all_periods:
                logger.info(f"  Vietstock Fininfo không có dữ liệu cho {symbol}, fallback synthetic")
                use_synthetic = True
        elif source == "cafef":
            # ── CafeF Bank API (nguồn chính mới) ────────────
            all_periods = self.fetch_cafef_bank_api(symbol)
            if not all_periods:
                logger.info(f"  CafeF Bank API không có dữ liệu cho {symbol}, fallback NoteIndicator")
                all_periods = self.fetch_note_indicator(symbol)
            if not all_periods:
                logger.info("  NoteIndicator cũng không có, dùng synthetic")
                use_synthetic = True
        elif source == "api":
            # ── REST JSON bridges (VNDirect/TCBS — ưu tiên CFO + nợ chi tiết) ──
            all_periods = self.fetch_vndirect_api(symbol)
            if not all_periods:
                logger.info(f"  VNDirect Fininfo không có dữ liệu cho {symbol}, fallback TCBS FinAPI")
                all_periods = self.fetch_tcbs_api(symbol)
            if not all_periods:
                logger.info(f"  TCBS FinAPI không có dữ liệu cho {symbol}, fallback CafeF Bank API")
                all_periods = self.fetch_cafef_bank_api(symbol)
            if not all_periods:
                logger.info(f"  CafeF Bank API không có dữ liệu cho {symbol}, fallback Vietstock Fininfo")
                all_periods = self.fetch_vietstock_api(symbol)
            if not all_periods:
                logger.info("  Vietstock Fininfo cũng không có, dùng synthetic")
                use_synthetic = True
        elif source == "synthetic":
            use_synthetic = True
        elif source == "vietstock":
            # ── Vietstock Finance summary (free 4 quý — kiểm tra chéo) ──
            all_periods = self.fetch_vietstock_api(symbol)
            if not all_periods:
                logger.info(f"  Vietstock Fininfo không có dữ liệu cho {symbol}, fallback synthetic")
                use_synthetic = True
        else:
            # ── CafeF cũ (legacy, thường 404) ───────────────
            test_data = self.fetch_quarter(symbol, 2026, 2)
            test_metrics = sum(1 for k in test_data if not k.startswith("_") and test_data[k] is not None)
            if test_metrics == 0:
                logger.info("  CafeF cũ không có dữ liệu, thử CafeF Bank API")
                all_periods = self.fetch_cafef_bank_api(symbol)
            if not all_periods:
                logger.info("  CafeF Bank API cũng không có, thử NoteIndicator")
                all_periods = self.fetch_note_indicator(symbol)
            if not all_periods:
                logger.info("  NoteIndicator cũng không có, dùng synthetic")
                use_synthetic = True

        # ── Merge CafeF CashFlow (CFO/CFI/CFF/CAPEX) vào các period ──
        # WHY: nguồn BS/IS (Bank API/VCI/Vietstock) KHÔNG có bảng lưu chuyển
        # tiền tệ → BCM/VRE thiếu CFO. Endpoint /du-lieu/bao-cao-tai-chinh/
        # CashFlow (verified 2026-08-01) trả đủ CF cho 20 quý. Merge theo
        # (year, quarter), chỉ ghi đè khi metric CF chưa có.
        if all_periods:
            cf_periods = self.fetch_cafef_cashflow(symbol)
            if cf_periods:
                cf_by_key = {f"{p['_fiscal_year']}Q{p['_fiscal_quarter']}": p for p in cf_periods}
                existing_keys = {f"{p.get('_fiscal_year')}Q{p.get('_fiscal_quarter')}" for p in all_periods}
                merged = 0
                # Tạo thêm period CF-only cho các quý nguồn BS/IS không có.
                # WHY: chỉ lấy metric CF thuần (CFO/CFI/CFF/CAPEX) — các dòng
                # RECEIVABLES/INVENTORY trong bảng CF là SỐ BIẾN ĐỘNG (delta)
                # không phải số dư BCTC, viết vào sẽ làm hỏng balance sheet.
                CF_ONLY_METRICS = {"CFO", "CFI", "CFF", "CAPEX"}
                for period_data in all_periods:
                    key = f"{period_data.get('_fiscal_year')}Q{period_data.get('_fiscal_quarter')}"
                    cf = cf_by_key.get(key)
                    if not cf:
                        continue
                    for k in CF_ONLY_METRICS:
                        v = cf.get(k)
                        if v is not None and period_data.get(k) is None:
                            period_data[k] = v
                            merged += 1
                # Tạo thêm period CF-only cho các quý nguồn BS/IS không có
                for key, cf in cf_by_key.items():
                    if key not in existing_keys:
                        all_periods.append(
                            {
                                "_fiscal_year": cf["_fiscal_year"],
                                "_fiscal_quarter": cf["_fiscal_quarter"],
                                "_entity_type": actual_type,
                                **{k: v for k, v in cf.items() if not k.startswith("_") and k in CF_ONLY_METRICS},
                            }
                        )
                        merged += 1
                logger.info(f"  CafeF CF merge: {symbol} — {merged} facts bổ sung")

        if use_synthetic:
            all_periods = self._generate_synthetic_base(symbol, actual_type)
            # Nếu (bất kỳ lý do nào) vẫn sinh ra dữ liệu bịa, đánh dấu provenance để auditor
            # phát hiện. Thực tế hàm đã khóa trả [] nhưng giữ guard phòng tái phát quằn.
            if all_periods:
                data_source = "synthetic"
                data_is_synthetic = 1
                logger.warning(f"  [ZERO-HALLUCINATION GUARD] {symbol} ghi dữ liệu synthetic (is_synthetic=1)")

        if not all_periods:
            logger.warning(f"  {symbol}: không có dữ liệu từ bất kỳ nguồn nào")
            return {
                "symbol": symbol,
                "entity_type": actual_type,
                "total_quarters": 0,
                "success": 0,
                "empty": len(quarters),
                "total_metrics": 0,
            }

        success = 0
        empty = 0
        total_metrics = 0

        for period_data in all_periods:
            year = period_data.get("_fiscal_year")
            q = period_data.get("_fiscal_quarter")
            if not year or not q:
                continue
            period = f"{year}Q{q}"

            metrics_count = sum(1 for k in period_data if not k.startswith("_") and period_data[k] is not None)
            if metrics_count == 0:
                empty += 1
                logger.warning(f"  [{period}] No data")
                continue

            result = self.db.write_batch(
                symbol, period_data, actual_type, self.batch_id, source=data_source, is_synthetic=data_is_synthetic
            )
            if result["status"] == "SUCCESS":
                success += 1
            total_metrics += result.get("facts_written", 0)
            logger.info(f"  [{result['status']}] {symbol} {period}: {result['facts_written']} facts")

        logger.info(f"=== {symbol} done: {success} OK, {empty} empty, {total_metrics} total facts ===")
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

    # ── Cross-check đa nguồn ─────────────────────────────────
    @staticmethod
    def cross_check(reference: List[Dict], candidate: List[Dict], tolerance_pct: float = 20.0) -> Dict:
        """So sánh 2 nguồn dữ liệu trên cùng metric (kiểm tra chéo).

        Dùng cho Vietstock summary (reference/candidate) với nguồn khác
        (CafeF/VNDirect/TCBS) — phát hiện lệch dữ liệu theo period+metric.

        Args:
            reference: list period dict (dạng _fiscal_year/_fiscal_quarter + metrics).
            candidate: list period dict cùng cấu trúc.
            tolerance_pct: sai lệch % tối đa được chấp nhận.

        Returns:
            Dict: {"checked": n_period_metric, "matched": n_ok,
                   "mismatched": n_lệch, "details": [...]}
        """
        ref_index = {}
        for p in reference:
            key = (p.get("_fiscal_year"), p.get("_fiscal_quarter"))
            if key[0] and key[1]:
                ref_index[key] = p
        cand_index = {}
        for p in candidate:
            key = (p.get("_fiscal_year"), p.get("_fiscal_quarter"))
            if key[0] and key[1]:
                cand_index[key] = p

        # WHY: chỉ so sánh các metric mà mọi nguồn đều trả đáng tin cậy (bỏ
        # CFO/CAPEX vì nguồn free thường thiếu); tolerance 20% dung sai khác
        # biệt đơn vị/làm tròn giữa các nguồn.
        common_metrics = (
            "REVENUE",
            "GROSS_PROFIT",
            "EBIT",
            "NET_INCOME",
            "TOTAL_ASSETS",
            "CURRENT_ASSETS",
            "TOTAL_LIABILITIES",
            "CURRENT_LIAB",
            "TOTAL_EQUITY",
            "EPS",
            "BOOK_VALUE_PS",
        )
        details = []
        checked = 0
        matched = 0
        mismatched = 0
        for key, ref_p in ref_index.items():
            cand_p = cand_index.get(key)
            if not cand_p:
                continue
            for metric in common_metrics:
                rv = ref_p.get(metric)
                cv = cand_p.get(metric)
                if rv is None or cv is None or rv == 0:
                    continue
                checked += 1
                diff_pct = abs(rv - cv) / abs(rv) * 100.0
                if diff_pct <= tolerance_pct:
                    matched += 1
                else:
                    mismatched += 1
                    details.append(
                        {
                            "period": f"{key[0]}Q{key[1]}",
                            "metric": metric,
                            "reference": rv,
                            "candidate": cv,
                            "diff_pct": round(diff_pct, 2),
                        }
                    )
        return {
            "checked": checked,
            "matched": matched,
            "mismatched": mismatched,
            "details": details,
        }


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
    logger.info(f"=== ALL DONE: {overall['symbols']} symbols, {overall['total_facts']} total facts ===")
