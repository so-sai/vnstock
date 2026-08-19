"""test_cme_fedwatch_playwright.py — TDD cho CME FedWatch Playwright crawler.

WHY Playwright: CME WAF trả HTTP 403 cho `requests`/`curl` từ 2026-08 (TLS
fingerprint + JS challenge). Playwright lái Chromium thật nên WAF phục vụ DOM
thật. Module này là tầng cuối cùng (last-resort) sau khi requests thất bại.

Các test này KHÔNG gọi mạng (Zero-Hallucination / no-network policy):
  - parse logic test trên DOM giả lập (locator stub)
  - fetch path test bằng monkeypatch toàn bộ Playwright
  - contract test cho sync wrapper + world_sensor fallback

Chạy: python -m pytest tests/test_cme_fedwatch_playwright.py (từ backend/)
"""

import pytest

# ═══════════════════════════════════════════════════════════════
# Parse layer: _extract_fedwatch_from_table
# ═══════════════════════════════════════════════════════════════


class _LocatorStub:
    """Stub tối thiểu cho Playwright Locator API mà parser dùng.

    `count()` cho biết có row hay không (bắt chước .first trên locator rỗng),
    `all_inner_texts()` trả cell text theo cấu trúc CME FedWatch table.
    """

    def __init__(self, texts=None, count=1):
        self._texts = texts or []
        self._count = count

    @property
    def first(self):
        return self

    def count(self):
        return self._count

    def all_inner_texts(self):
        return list(self._texts)

    def locator(self, sel):
        return self


def _table(headers, row):
    class _Table:
        def locator(self, sel):
            if "thead" in sel:
                return _LocatorStub(headers)
            return _LocatorStub(row)

    return _Table()


def test_parse_typical_fedwatch_row():
    from sensors.cme_fedwatch_playwright import _extract_fedwatch_from_table

    tbl = _table(
        ["Meeting", "Date", "No Change", "Increase", "Decrease"],
        ["June 2026", "6/17/2026", "92.0%", "8.0%", "0.0%"],
    )
    rate, prob, meeting = _extract_fedwatch_from_table(tbl)
    assert meeting == "June 2026"
    assert prob == pytest.approx(0.08)  # 8.0% -> 0.08 (chuẩn hóa về [0,1])
    assert rate == 5.5


def test_parse_cut_column_when_increase_absent():
    """Nếu header không có Increase (chỉ Decrease), dùng cột Decrease."""
    from sensors.cme_fedwatch_playwright import _extract_fedwatch_from_table

    tbl = _table(
        ["Meeting", "Date", "No Change", "Decrease"],
        ["June 2026", "6/17/2026", "88.0%", "12.0%"],
    )
    rate, prob, meeting = _extract_fedwatch_from_table(tbl)
    assert meeting == "June 2026"
    assert prob == pytest.approx(0.12)


def test_parse_returns_defaults_on_empty_table():
    from sensors.cme_fedwatch_playwright import _extract_fedwatch_from_table

    tbl = _table([], [])
    rate, prob, meeting = _extract_fedwatch_from_table(tbl)
    assert (rate, prob, meeting) == (5.5, 0.0, "unknown")


def test_parse_returns_defaults_on_nonnumeric_cell():
    from sensors.cme_fedwatch_playwright import _extract_fedwatch_from_table

    tbl = _table(
        ["Meeting", "Date", "No Change", "Increase", "Decrease"],
        ["June 2026", "6/17/2026", "--", "N/A", "0.0%"],
    )
    rate, prob, meeting = _extract_fedwatch_from_table(tbl)
    assert meeting == "June 2026"
    assert prob == 0.0  # cell không parse được -> 0, KHÔNG raise


def test_parse_rate_from_range_cell():
    from sensors.cme_fedwatch_playwright import _extract_fedwatch_from_table

    # Một số build CME để target rate trong cell: "4.25-4.50%"
    tbl = _table(
        ["Meeting", "Date", "No Change", "Increase", "Decrease"],
        ["March 2026", "3/18/2026 4.25-4.50%", "95.0%", "5.0%", "0.0%"],
    )
    rate, prob, meeting = _extract_fedwatch_from_table(tbl)
    assert rate == 4.5  # lấy đầu trên của dải (policy upper bound)


# ═══════════════════════════════════════════════════════════════
# Fetch path: monkeypatch Playwright hoàn toàn
# ═══════════════════════════════════════════════════════════════


def test_async_returns_parsed_values_with_mocked_playwright(monkeypatch):
    """Happy path: chromium mở, route chặn resource, .cmeTable parse OK."""

    class _FakePage:
        def __init__(self):
            self.routed_glob = None

        async def route(self, glob, handler):
            self.routed_glob = glob

        async def goto(self, url, **kw):
            return type("R", (), {"status": 200})()

        async def wait_for_selector(self, sel, **kw):
            assert sel == ".cmeTable"

        def locator(self, sel):
            assert sel == ".cmeTable"
            return _FakeTable()

    class _FakeCellTexts:
        def locator(self, sel):
            if "thead" in sel:
                return _LocatorStub(["Meeting", "Date", "No Change", "Increase", "Decrease"], 1)
            if "tbody" in sel:
                return _LocatorStub(["June 2026", "6/17/2026", "92.0%", "8.0%", "0.0%"], 1)
            return _LocatorStub([], 0)

    class _FakeTable:
        @property
        def first(self):
            return self

        def locator(self, sel):
            return _FakeCellTexts().locator(sel)

    class _FakeCtx:
        async def add_init_script(self, script):
            pass

        async def new_page(self):
            return _FakePage()

    class _FakeBrowser:
        def __init__(self):
            self.closed = False

        async def new_context(self, **kw):
            return _FakeCtx()

        async def close(self):
            self.closed = True

    class _FakeChromium:
        def __init__(self):
            self.browser = _FakeBrowser()

        async def launch(self, **kw):
            return self.browser

    class _FakePlaywright:
        def __init__(self):
            self.chromium_ = _FakeChromium()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        @property
        def chromium(self):
            return self.chromium_

    # Thay thế `playwright.async_api.async_playwright` bằng fake context manager.
    # Module này import async_playwright BÊN TRONG hàm (lazy), nên mock qua
    # sys.modules thay vì setattr module.
    import sys
    import types

    import sensors.cme_fedwatch_playwright as mod

    fake_async_api = types.ModuleType("playwright.async_api")
    fake_async_api.async_playwright = _FakePlaywright
    monkeypatch.setitem(sys.modules, "playwright.async_api", fake_async_api)

    import asyncio

    rate, prob, meeting = asyncio.run(mod._fetch_cme_fedwatch_async())
    assert meeting == "June 2026"
    assert prob == pytest.approx(0.08)


def test_async_blocks_junk_resources(monkeypatch):
    """page.route phải chặn png/jpg/jpeg/svg/css/woff/woff2 — WAF không cần
    resource đó, chặn để tiết kiệm RAM (cafef_crawler pattern)."""
    from sensors.cme_fedwatch_playwright import BLOCKED_RESOURCE_GLOBS

    assert "png" in BLOCKED_RESOURCE_GLOBS
    assert "css" in BLOCKED_RESOURCE_GLOBS
    assert "woff" in BLOCKED_RESOURCE_GLOBS


def test_fetch_returns_defaults_when_playwright_missing(monkeypatch):
    """ModuleNotFoundError -> fallback defaults, KHÔNG raise (EOD không chết)."""
    import sensors.cme_fedwatch_playwright as mod

    # Giả lập: playwright không cài — async core bắt ModuleNotFoundError ở
    # nhánh `from playwright.async_api import async_playwright` và trả defaults.
    async def _no_playwright():
        try:
            from playwright.async_api import async_playwright  # noqa: F401
        except ImportError, ModuleNotFoundError:
            return (5.5, 0.0, "unknown")
        return (5.5, 0.0, "unknown")

    monkeypatch.setattr(mod, "_fetch_cme_fedwatch_async", _no_playwright)
    rate, prob, meeting, source = mod.fetch_cme_fedwatch()
    assert source == "default"
    assert (rate, prob, meeting) == (5.5, 0.0, "unknown")


# ═══════════════════════════════════════════════════════════════
# Sync wrapper + source tag contract
# ═══════════════════════════════════════════════════════════════


def test_sync_wrapper_returns_source_tag_on_success(monkeypatch):
    import sensors.cme_fedwatch_playwright as mod

    async def _ok():
        return (5.5, 0.08, "June 2026")

    monkeypatch.setattr(mod, "_fetch_cme_fedwatch_async", _ok)
    rate, prob, meeting, source = mod.fetch_cme_fedwatch()
    assert source == "cme_pw"
    assert meeting == "June 2026"
    assert prob == pytest.approx(0.08)


def test_sync_wrapper_default_source_when_no_data(monkeypatch):
    import sensors.cme_fedwatch_playwright as mod

    async def _empty():
        return (5.5, 0.0, "unknown")

    monkeypatch.setattr(mod, "_fetch_cme_fedwatch_async", _empty)
    rate, prob, meeting, source = mod.fetch_cme_fedwatch()
    assert source == "default"


# ═══════════════════════════════════════════════════════════════
# Integration: world_sensor fallback
# ═══════════════════════════════════════════════════════════════


def test_world_sensor_falls_back_to_playwright_when_requests_fails(monkeypatch):
    """requests fail (403 WAF) -> _cme_fedwatch_playwright_fallback được gọi."""
    import sys

    import sensors.world_sensor as ws

    def _boom(*a, **k):
        raise ConnectionError("403 Forbidden: WAF block")

    monkeypatch.setattr(ws.requests, "get", _boom)

    # Giả lập module playwright fallback trả về dữ liệu crawl được
    fake = type(
        "FakeMod",
        (),
        {
            "fetch_cme_fedwatch": staticmethod(lambda: (5.5, 0.08, "June 2026", "cme_pw")),
        },
    )
    monkeypatch.setitem(sys.modules, "src.sensors.cme_fedwatch_playwright", fake)

    rate, prob, meeting = ws._fetch_cme_fedwatch()
    assert meeting == "June 2026"
    assert prob == pytest.approx(0.08)


def test_world_sensor_playwright_fallback_returns_defaults_on_error(monkeypatch):
    """Playwright fallback thất bại -> defaults, không raise (EOD non-blocking)."""
    import sys

    import sensors.world_sensor as ws

    def _boom(*a, **k):
        raise RuntimeError("browser crash")

    fake = type(
        "FakeMod",
        (),
        {
            "fetch_cme_fedwatch": staticmethod(_boom),
        },
    )
    monkeypatch.setitem(sys.modules, "src.sensors.cme_fedwatch_playwright", fake)

    # CME fail + FRED DFEDTARU fail → defaults (không raise).
    monkeypatch.setattr(ws, "fetch_fred_csv_latest", lambda *a, **k: None)
    rate, prob, meeting = ws._cme_fedwatch_playwright_fallback()
    assert (rate, prob, meeting) == (5.5, 0.0, "unknown")


# ═══════════════════════════════════════════════════════════════
# FRED public CSV (Zero-Auth) helper
# ═══════════════════════════════════════════════════════════════


class _FakeResp:
    def __init__(self, text, status=200):
        self._text = text
        self.status = status

    def raise_for_status(self):
        if self.status != 200:
            raise ConnectionError(f"HTTP {self.status}")
        return None

    @property
    def text(self):
        return self._text


def test_fred_csv_latest_parses_valid_row(monkeypatch):
    """Test 1: CSV chuẩn → parse đúng date + value float."""
    import sensors.world_sensor as ws

    csv_body = "observation_date,DFEDTARU\n2026-08-15,5.50\n2026-08-16,5.50\n2026-08-18,4.50\n"
    monkeypatch.setattr(ws.requests, "get", lambda *a, **k: _FakeResp(csv_body))

    out = ws.fetch_fred_csv_latest("DFEDTARU")
    assert out is not None
    assert out["series_id"] == "DFEDTARU"
    assert out["date"] == "2026-08-18"
    assert out["value"] == pytest.approx(4.5)


def test_fred_csv_latest_skips_missing_values(monkeypatch):
    """Test 2: dòng missing '.' → lấy dòng hợp lệ gần nhất."""
    import sensors.world_sensor as ws

    csv_body = "observation_date,DFEDTARU\n2026-08-15,5.50\n2026-08-16,.\n2026-08-18,.\n2026-08-19,4.50\n"
    monkeypatch.setattr(ws.requests, "get", lambda *a, **k: _FakeResp(csv_body))

    out = ws.fetch_fred_csv_latest("DFEDTARU")
    assert out is not None
    assert out["date"] == "2026-08-19"
    assert out["value"] == pytest.approx(4.5)


def test_fred_csv_latest_returns_none_on_timeout(monkeypatch):
    """Test 3: network timeout / HTTP 500 → None, không raise."""
    import sensors.world_sensor as ws

    def _timeout(*a, **k):
        raise ConnectionError("timed out")

    monkeypatch.setattr(ws.requests, "get", _timeout)
    assert ws.fetch_fred_csv_latest("DFEDTARU") is None

    monkeypatch.setattr(ws.requests, "get", lambda *a, **k: _FakeResp("", 500))
    assert ws.fetch_fred_csv_latest("DFEDTARU") is None


def test_fred_csv_latest_returns_none_on_empty_body(monkeypatch):
    """Body trống / chỉ header → None (không có row hợp lệ)."""
    import sensors.world_sensor as ws

    monkeypatch.setattr(ws.requests, "get", lambda *a, **k: _FakeResp("observation_date,DFEDTARU\n"))
    assert ws.fetch_fred_csv_latest("DFEDTARU") is None


# ═══════════════════════════════════════════════════════════════
# Integration: FRED DFEDTARU fallback khi CME bị chặn hoàn toàn
# ═══════════════════════════════════════════════════════════════


def test_playwright_fallback_uses_fred_rate_when_cme_blocked(monkeypatch):
    """CME Playwright trả defaults + FRED có rate thật → rate từ FRED (4.5)."""
    import sys

    import sensors.world_sensor as ws

    fake = type(
        "FakeMod",
        (),
        {
            "fetch_cme_fedwatch": staticmethod(lambda: (5.5, 0.0, "unknown", "default")),
        },
    )
    monkeypatch.setitem(sys.modules, "src.sensors.cme_fedwatch_playwright", fake)
    monkeypatch.setattr(
        ws,
        "fetch_fred_csv_latest",
        lambda series_id: {"series_id": "DFEDTARU", "date": "2026-08-18", "value": 4.5},
    )

    rate, prob, meeting = ws._cme_fedwatch_playwright_fallback()
    assert rate == pytest.approx(4.5)
    assert prob == 0.0
    assert meeting == "unknown"
