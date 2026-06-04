from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class AssetClass(Enum):
    FX = "fx"
    INDEX = "index"
    COMMODITY = "commodity"
    CRYPTO = "crypto"
    MACRO_YIELD = "macro_yield"
    MACRO_INDEX = "macro_index"
    STOCK = "stock"


class CanonicalUnit(Enum):
    # FX
    FX_RATE = "fx_rate"                      # VND per foreign unit
    # Index
    INDEX_LEVEL = "index_level"              # raw index points
    # Commodity
    VND_PER_LUONG = "vnd_per_luong"          # domestic gold (1 luong = 37.5g)
    USD_PER_OUNCE = "usd_per_ounce"          # world gold XAU
    USD_PER_LB = "usd_per_lb"                # copper COMEX
    USD_PER_BARREL = "usd_per_barrel"        # oil
    # Crypto
    USD = "usd"                              # BTC-USD
    # Macro yield
    PERCENT = "percent"                      # 4.5 = 4.5%
    # Macro index
    DXY_LEVEL = "dxy_level"                  # US dollar index
    # Stock
    VND_PER_SHARE = "vnd_per_share"          # domestic stock (raw VND)
    VND_THOUSAND = "vnd_thousand"            # domestic stock (thousands VND)


class DataSource(Enum):
    YAHOO = "yahoo"
    KBS = "kbs"
    VCI = "vci"
    VCB = "vcb"
    BTMC = "btmc"
    SJC = "sjc"
    MSN = "msn"
    FMP = "fmp"


@dataclass
class CanonicalRecord:
    """A single validated, normalized data point."""

    variable: str
    date: str
    value: float
    asset_class: AssetClass
    unit: CanonicalUnit
    source: DataSource
    raw_value: float
    raw_unit: str
    confidence: float = 1.0
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    freshness_score: float = 1.0
    latency_ms: int = 0
    metadata: dict = field(default_factory=dict)
