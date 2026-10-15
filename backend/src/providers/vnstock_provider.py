"""vnstock_provider.py — VnstockProvider adapter.

Wraps `vnstock` (installed as editable local package from
`backend/libs/vnstock`) behind the FinancialProvider contract. The Core
Engine never imports `vnstock` directly anymore — it talks to this adapter
(or whatever ProviderManager resolves).
"""

from typing import Any

import pandas as pd

from src.providers.base import FinancialProvider


class VnstockProvider(FinancialProvider):
    """Adapter exposing the vendored vnstock library via FinancialProvider."""

    name = "vnstock"

    def __init__(
        self,
        source: str = "VCI",
        finance_kwargs: dict[str, Any] | None = None,
        quote_kwargs: dict[str, Any] | None = None,
    ) -> None:
        self.source = source
        self._finance_kwargs = finance_kwargs or {}
        self._quote_kwargs = quote_kwargs or {}
        self._module = None
        # Chẩn đoán lỗi của lần gọi `history()` gần nhất. WHY: `history()` phải giữ
        # hợp đồng "fail → None" cho mọi caller, nhưng nuốt exception ở đây từng làm
        # log backfill chỉ ghi `reason=UNKNOWN`, không phân biệt được "nguồn không có
        # dữ liệu / mã không hợp lệ" với "lỗi mạng/parser". Thuộc tính này là kênh
        # phụ (không đổi giá trị trả về) để caller ghi được `FETCH_ERR:<ExceptionType>`.
        self.last_history_error: str | None = None

    def _import(self):
        """Lazy import of vnstock (deferred until first use)."""
        if self._module is None:
            from vnstock import Company, Finance, Listing, Quote, Trading

            self._module = {
                "Company": Company,
                "Finance": Finance,
                "Listing": Listing,
                "Quote": Quote,
                "Trading": Trading,
            }
        return self._module

    def is_available(self) -> bool:
        try:
            self._import()
            return True
        except Exception:  # noqa: BLE001 - external provider resilience: vnstock chưa cài/fail → báo unavailable
            return False

    def _finance(self, symbol: str):
        Finance = self._import()["Finance"]
        return Finance(
            source=self.source,
            symbol=symbol,
            period="quarter",
            get_all=True,
            show_log=False,
            **self._finance_kwargs,
        )

    def _quote(self, symbol: str):
        Quote = self._import()["Quote"]
        return Quote(source=self.source, symbol=symbol, show_log=False, **self._quote_kwargs)

    # ── Financial statements ───────────────────────────────────────────
    def income_statement(self, symbol: str, **kwargs: Any) -> pd.DataFrame | None:
        try:
            finance = self._finance(symbol)
            limit = kwargs.pop("limit", None)
            if limit is not None:
                return finance._get_financial_report("income_statement", limit=limit, **kwargs)
            return finance.income_statement(**kwargs)
        except Exception:  # noqa: BLE001 - external provider resilience: vnstock fail → trả None (fallback nguồn khác)
            return None

    def balance_sheet(self, symbol: str, **kwargs: Any) -> pd.DataFrame | None:
        try:
            finance = self._finance(symbol)
            limit = kwargs.pop("limit", None)
            if limit is not None:
                return finance._get_financial_report("balance_sheet", limit=limit, **kwargs)
            return finance.balance_sheet(**kwargs)
        except Exception:  # noqa: BLE001 - external provider resilience: vnstock fail → trả None (fallback nguồn khác)
            return None

    def cashflow(self, symbol: str, **kwargs: Any) -> pd.DataFrame | None:
        try:
            finance = self._finance(symbol)
            limit = kwargs.pop("limit", None)
            if limit is not None:
                return finance._get_financial_report("cash_flow", limit=limit, **kwargs)
            return finance.cash_flow(**kwargs)
        except Exception:  # noqa: BLE001 - external provider resilience: vnstock fail → trả None (fallback nguồn khác)
            return None

    # ── Market data ────────────────────────────────────────────────────
    def history(
        self,
        symbol: str,
        start: str | None = None,
        end: str | None = None,
        **kwargs: Any,
    ) -> pd.DataFrame | None:
        self.last_history_error = None
        try:
            q = self._quote(symbol)
            if start and end:
                df = q.history(start=start, end=end, **kwargs)
            else:
                df = q.history(**kwargs)
            if df is None or len(df) == 0:
                # Nguồn trả rỗng ≠ nguồn lỗi — tách mã để thống kê được về sau.
                self.last_history_error = "EMPTY_DF"
            return df
        except Exception as e:  # noqa: BLE001 - external provider resilience: vnstock fail → trả None (fallback nguồn khác)
            # Bóc 1 tầng tenacity: vnstock bọc lỗi gốc trong `RetryError`. Không phân biệt được
            # "mạng/timeout" với "dữ liệu rỗng (ValueError)" ⇒ retry MÙ 3 lần cho mỗi mã không có
            # bar (~8–9s/mã); nhân với ~650 mã không giao dịch/rổ UPCoM nhỏ ⇒ mỗi lượt quét toàn rổ
            # ngốn hàng chục phút — đây là nguyên nhân EOD bị cắt ở mốc 3600s, KHÔNG phải quota.
            root = e
            try:
                last = getattr(e, "last_attempt", None)
                if last is not None:
                    root = last.exception()
            except Exception:  # noqa: BLE001 - bóc lỗi chỉ là best-effort, không được che lỗi gốc
                root = e
            suffix = "" if root is e else f":{type(root).__name__}"
            self.last_history_error = f"FETCH_ERR:{type(e).__name__}{suffix}"
            return None

    # ── Company info ───────────────────────────────────────────────────
    def company_info(self, symbol: str, **kwargs: Any) -> dict[str, Any] | None:
        try:
            Company = self._import()["Company"]
            info = Company(symbol=symbol, source=self.source)
            return {"symbol": symbol, "raw": info}
        except Exception:  # noqa: BLE001 - external provider resilience: vnstock fail → trả None (fallback nguồn khác)
            return None

    def trading_stats(self, symbol: str, **kwargs: Any) -> pd.DataFrame | None:
        """vnstock-specific: Khối ngoại trading stats (foreign_volume).

        Not part of the FinancialProvider ABC — it's a vnstock extension
        consumed directly by MoneyFlowEngine.
        """
        try:
            Company = self._import()["Company"]
            return Company(
                source=self.source,
                symbol=symbol,
                show_log=kwargs.get("show_log", False),
            ).trading_stats(**{k: v for k, v in kwargs.items() if k != "show_log"})
        except Exception:  # noqa: BLE001 - external provider resilience: vnstock fail → trả None (fallback nguồn khác)
            return None

    def price_board(self, symbols: list[str], **kwargs: Any) -> pd.DataFrame | None:
        """vnstock-specific: Bảng giá real-time (Trading.price_board).

        Not part of the FinancialProvider ABC — it's a vnstock extension
        consumed directly by daily_updater's intraday batch update.
        """
        try:
            Trading = self._import()["Trading"]
            t = Trading(source=self.source, random_agent=kwargs.get("random_agent", True))
            return t.price_board(symbols)
        except Exception:  # noqa: BLE001 - external provider resilience: vnstock fail → trả None (fallback nguồn khác)
            return None

    def symbols(self, **kwargs: Any) -> list[str] | None:
        try:
            Listing = self._import()["Listing"]
            df = Listing(source=self.source).all_symbols()
            if df is not None and not df.empty:
                col = "symbol" if "symbol" in df.columns else df.columns[0]
                return df[col].dropna().astype(str).tolist()
            return None
        except Exception:  # noqa: BLE001 - external provider resilience: vnstock fail → trả None (fallback nguồn khác)
            return None

    # ── Audit ──────────────────────────────────────────────────────────
    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "source": self.source,
            "available": self.is_available(),
        }
