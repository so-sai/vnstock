from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .core import AssetClass, CanonicalUnit, DataSource


@dataclass
class VariableSpec:
    """Specification for a known canonical variable."""

    variable: str
    asset_class: AssetClass
    canonical_unit: CanonicalUnit
    allowed_sources: list[DataSource]
    primary_source: DataSource
    description: str = ""
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    # For raw→canonical conversion (multiply raw by this factor)
    normalization_factor: float = 1.0
    raw_unit_label: str = "raw"


class CanonicalAssetRegistry:
    """Central registry of all known financial variables and their canonical specs."""

    _instance: CanonicalAssetRegistry | None = None
    _registry: Dict[str, VariableSpec] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._build_registry()
        return cls._instance

    # ── Registry definition ──────────────────────────────────────────

    def _build_registry(self):
        self._registry = {
            # ── Indices ──
            "VNINDEX": self._spec(
                "VNINDEX", AssetClass.INDEX, CanonicalUnit.INDEX_LEVEL,
                [DataSource.KBS, DataSource.VCI, DataSource.MSN],
                DataSource.KBS, "VN Index", 500, 2000,
                1, "KBS_raw_after_div1000",
            ),
            "VN30": self._spec(
                "VN30", AssetClass.INDEX, CanonicalUnit.INDEX_LEVEL,
                [DataSource.KBS, DataSource.VCI],
                DataSource.KBS, "VN30 Index", 500, 2000,
                1, "KBS_raw_after_div1000",
            ),
            "HNXINDEX": self._spec(
                "HNXINDEX", AssetClass.INDEX, CanonicalUnit.INDEX_LEVEL,
                [DataSource.KBS, DataSource.VCI],
                DataSource.KBS, "HNX Index", 50, 600,
                1, "KBS_raw_after_div1000",
            ),
            "UPCOMINDEX": self._spec(
                "UPCOMINDEX", AssetClass.INDEX, CanonicalUnit.INDEX_LEVEL,
                [DataSource.KBS, DataSource.VCI],
                DataSource.KBS, "UPCOM Index", 50, 600,
                1, "KBS_raw_after_div1000",
            ),
            "SH_COMP": self._spec(
                "SH_COMP", AssetClass.INDEX, CanonicalUnit.INDEX_LEVEL,
                [DataSource.YAHOO],
                DataSource.YAHOO, "Shanghai Composite", 2000, 7000,
                1, "yahoo_raw",
            ),
            "DXY": self._spec(
                "DXY", AssetClass.MACRO_INDEX, CanonicalUnit.DXY_LEVEL,
                [DataSource.YAHOO],
                DataSource.YAHOO, "US Dollar Index (futures)", 80, 130,
                1, "yahoo_raw",
            ),

            # ── FX ──
            "USD_VND": self._spec(
                "USD_VND", AssetClass.FX, CanonicalUnit.FX_RATE,
                [DataSource.YAHOO, DataSource.VCB],
                DataSource.YAHOO, "USD/VND exchange rate", 20000, 30000,
                1, "yahoo_or_vcb_raw",
            ),
            "USD_CNY": self._spec(
                "USD_CNY", AssetClass.FX, CanonicalUnit.FX_RATE,
                [DataSource.YAHOO],
                DataSource.YAHOO, "USD/CNY onshore", 6.0, 8.0,
                1, "yahoo_raw",
            ),
            "USD_CNH": self._spec(
                "USD_CNH", AssetClass.FX, CanonicalUnit.FX_RATE,
                [DataSource.YAHOO],
                DataSource.YAHOO, "USD/CNH offshore", 6.0, 8.0,
                1, "yahoo_raw",
            ),

            # ── Commodities ──
            "GOLD_XAU": self._spec(
                "GOLD_XAU", AssetClass.COMMODITY, CanonicalUnit.USD_PER_OUNCE,
                [DataSource.YAHOO],
                DataSource.YAHOO, "World gold XAU/USD", 1500, 5000,
                1, "yahoo_gc_f_raw",
            ),
            "GOLD_SJC": self._spec(
                "GOLD_SJC", AssetClass.COMMODITY, CanonicalUnit.VND_PER_LUONG,
                [DataSource.SJC],
                DataSource.SJC, "SJC domestic gold VND/luong", 50000000, 200000000,
                1, "sjc_api_raw",
            ),
            "GOLD_BTMC": self._spec(
                "GOLD_BTMC", AssetClass.COMMODITY, CanonicalUnit.VND_PER_LUONG,
                [DataSource.BTMC],
                DataSource.BTMC, "BTMC domestic gold VND/luong (converted from chi)", 50000000, 200000000,
                10, "btmc_api_raw_vnd_per_chi",  # ×10: chỉ → lượng
            ),
            "XAGUSD": self._spec(
                "XAGUSD", AssetClass.COMMODITY, CanonicalUnit.USD_PER_OUNCE,
                [DataSource.YAHOO],
                DataSource.YAHOO, "World silver XAG/USD", 5, 100,
                1, "yahoo_si_f_raw",
            ),
            "COPPER_HG": self._spec(
                "COPPER_HG", AssetClass.COMMODITY, CanonicalUnit.USD_PER_LB,
                [DataSource.YAHOO],
                DataSource.YAHOO, "COMEX copper USD/lb", 2.0, 6.0,
                1, "yahoo_hg_f_raw",
            ),
            "BRENT_OIL": self._spec(
                "BRENT_OIL", AssetClass.COMMODITY, CanonicalUnit.USD_PER_BARREL,
                [DataSource.YAHOO],
                DataSource.YAHOO, "Brent crude USD/bbl", 30, 150,
                1, "yahoo_bz_f_raw",
            ),
            "WTI_OIL": self._spec(
                "WTI_OIL", AssetClass.COMMODITY, CanonicalUnit.USD_PER_BARREL,
                [DataSource.YAHOO],
                DataSource.YAHOO, "WTI crude USD/bbl", 30, 150,
                1, "yahoo_cl_f_raw",
            ),

            # ── Crypto ──
            "BTC": self._spec(
                "BTC", AssetClass.CRYPTO, CanonicalUnit.USD,
                [DataSource.YAHOO],
                DataSource.YAHOO, "Bitcoin USD", 5000, 500000,
                1, "yahoo_btc_usd_raw",
            ),

            # ── Macro yield ──
            "US2Y": self._spec(
                "US2Y", AssetClass.MACRO_YIELD, CanonicalUnit.PERCENT,
                [DataSource.YAHOO],
                DataSource.YAHOO, "US 2Y Treasury yield", 0.1, 7.0,
                1, "yahoo_2y_raw_percent",
            ),
            "US5Y": self._spec(
                "US5Y", AssetClass.MACRO_YIELD, CanonicalUnit.PERCENT,
                [DataSource.YAHOO],
                DataSource.YAHOO, "US 5Y Treasury yield", 0.2, 7.5,
                1, "yahoo_fvx_raw_percent",
            ),
            "US10Y": self._spec(
                "US10Y", AssetClass.MACRO_YIELD, CanonicalUnit.PERCENT,
                [DataSource.YAHOO],
                DataSource.YAHOO, "US 10Y Treasury yield", 0.5, 8.0,
                1, "yahoo_tnx_raw_percent",
            ),
            "US30Y": self._spec(
                "US30Y", AssetClass.MACRO_YIELD, CanonicalUnit.PERCENT,
                [DataSource.YAHOO],
                DataSource.YAHOO, "US 30Y Treasury yield", 0.5, 8.0,
                1, "yahoo_tyx_raw_percent",
            ),

            # ── TIPS / Real Yield ──
            "TIP_PRICE": self._spec(
                "TIP_PRICE", AssetClass.MACRO_INDEX, CanonicalUnit.USD,
                [DataSource.YAHOO],
                DataSource.YAHOO, "iShares TIPS Bond ETF price", 80, 150,
                1, "yahoo_tip_raw",
            ),
            "US_REAL_YIELD": self._spec(
                "US_REAL_YIELD", AssetClass.MACRO_YIELD, CanonicalUnit.PERCENT,
                [DataSource.YAHOO],
                DataSource.YAHOO, "US 10Y Real Yield (TIPS implied)", 0.0, 5.0,
                1, "yahoo_tip_div_yield",
            ),
            "BREAKEVEN_INFLATION": self._spec(
                "BREAKEVEN_INFLATION", AssetClass.MACRO_INDEX, CanonicalUnit.PERCENT,
                [DataSource.YAHOO],
                DataSource.YAHOO, "US 10Y Breakeven Inflation (US10Y - Real Yield)", 0.0, 8.0,
                1, "computed_breakeven",
            ),
        }

    def _spec(self, variable, asset_class, unit, sources, primary,
              desc, vmin, vmax, norm_factor, raw_unit) -> VariableSpec:
        return VariableSpec(
            variable=variable,
            asset_class=asset_class,
            canonical_unit=unit,
            allowed_sources=sources,
            primary_source=primary,
            description=desc,
            min_value=vmin,
            max_value=vmax,
            normalization_factor=norm_factor,
            raw_unit_label=raw_unit,
        )

    # ── Query API ──

    def get(self, variable: str) -> VariableSpec | None:
        return self._registry.get(variable)

    def has(self, variable: str) -> bool:
        return variable in self._registry

    def all_variables(self) -> List[str]:
        return list(self._registry.keys())

    def by_asset_class(self, cls: AssetClass) -> List[VariableSpec]:
        return [s for s in self._registry.values() if s.asset_class == cls]

    def by_source(self, source: DataSource) -> List[VariableSpec]:
        return [s for s in self._registry.values() if source in s.allowed_sources]

    def source_trust(self, source: DataSource) -> float:
        """Confidence score for each data source (0–1)."""
        scores = {
            DataSource.KBS: 0.95,
            DataSource.VCI: 0.92,
            DataSource.SJC: 0.90,
            DataSource.YAHOO: 0.85,
            DataSource.BTMC: 0.80,
            DataSource.VCB: 0.90,
            DataSource.MSN: 0.75,
            DataSource.FMP: 0.80,
        }
        return scores.get(source, 0.5)
