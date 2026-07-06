import warnings

import pandas as pd

# Suppress pandas 3.0 CoW chained assignment warnings (cosmetic only)
# These do not affect correctness in current pandas versions.
warnings.filterwarnings("ignore", category=FutureWarning, module="pandas")


class FeatureLatticeBuilder:
    """
    Tang 0: bien OHLCV thanh Feature Lattice.
    Chi: physics cua gia (toan hoc thuan).
    KHONG phu thuoc engine nao.
    KHONG chua logic quyet dinh.
    """

    def __init__(self, feature_version: str = "v1.0"):
        self.feature_version = feature_version

    def build(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Input:  OHLCV DataFrame (columns: date, symbol, open, high, low, close, volume)
        Output: Feature Lattice DataFrame (moi dong = (date, symbol) + chi bao)
        """
        result = df.sort_values(["symbol", "date"]).copy()
        parsed = pd.to_datetime(result["date"], format="mixed")
        result.loc[:, "date"] = parsed

        # ===== PRICE FEATURES =====
        grp = result.groupby("symbol")["close"]
        result["return_1d"] = grp.pct_change(1, fill_method=None)
        result["return_5d"] = grp.pct_change(5, fill_method=None)
        result["return_20d"] = grp.pct_change(20, fill_method=None)

        returns = result.groupby("symbol")["return_1d"]
        result["volatility_20d"] = returns.transform(lambda x: x.rolling(20).std())

        # ===== MOVING AVERAGES =====
        result["ma_5"] = grp.transform(lambda x: x.rolling(5).mean())
        result["ma_10"] = grp.transform(lambda x: x.rolling(10).mean())
        result["ma_20"] = grp.transform(lambda x: x.rolling(20).mean())
        result["ma_slope_20"] = result.groupby("symbol")["ma_20"].pct_change(5, fill_method=None)

        # ===== MOMENTUM =====
        result["rsi_14"] = self._rsi(result, 14)
        macd, signal = self._macd(result)
        result["macd"] = macd
        result["macd_signal"] = signal

        # ===== VOLUME FEATURES =====
        result["volume_z"] = self._zscore(result, "volume", 20)
        vol_grp = result.groupby("symbol")["volume"]
        ma5_vol = vol_grp.transform(lambda x: x.rolling(5).mean())
        ma20_vol = vol_grp.transform(lambda x: x.rolling(20).mean())
        result["volume_trend"] = ma5_vol / (ma20_vol + 1e-9)
        result["turnover_rate"] = result["volume"] / (ma20_vol + 1e-9)

        # ===== STRUCTURE =====
        result["high_breakout"] = (
            result["close"] > result.groupby("symbol")["high"].transform(lambda x: x.rolling(20).max())
        ).astype(int)
        result["low_breakdown"] = (
            result["close"] < result.groupby("symbol")["low"].transform(lambda x: x.rolling(20).min())
        ).astype(int)
        result["range_compression"] = (
            (result["high"] - result["low"]) / (result.groupby("symbol")["close"].transform(lambda x: x.rolling(20).mean()) + 1e-9)
        )

        # ===== CROSS-SECTIONAL =====
        result["rel_strength_20"] = self._relative_strength(result)

        # ===== METADATA =====
        result["feature_version"] = self.feature_version

        return result

    def _rsi(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        delta = df.groupby("symbol")["close"].diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.groupby(df["symbol"]).transform(lambda x: x.rolling(period).mean())
        avg_loss = loss.groupby(df["symbol"]).transform(lambda x: x.rolling(period).mean())
        rs = avg_gain / (avg_loss + 1e-9)
        return 100 - (100 / (1 + rs))

    def _macd(self, df: pd.DataFrame):
        ema12 = df.groupby("symbol")["close"].transform(lambda x: x.ewm(span=12).mean())
        ema26 = df.groupby("symbol")["close"].transform(lambda x: x.ewm(span=26).mean())
        macd = ema12 - ema26
        signal = macd.groupby(df["symbol"]).transform(lambda x: x.ewm(span=9).mean())
        return macd, signal

    def _zscore(self, df: pd.DataFrame, col: str, window: int = 20) -> pd.Series:
        mean = df.groupby("symbol")[col].transform(lambda x: x.rolling(window).mean())
        std = df.groupby("symbol")[col].transform(lambda x: x.rolling(window).std())
        return (df[col] - mean) / (std + 1e-9)

    def _relative_strength(self, df: pd.DataFrame) -> pd.Series:
        market_return = df.groupby("date")["close"].transform("mean") + 1e-9
        return df["close"] / market_return


def build_feature_lattice(
    df: pd.DataFrame,
    version: str = "v1.0",
) -> pd.DataFrame:
    builder = FeatureLatticeBuilder(feature_version=version)
    return builder.build(df)


def get_feature_at(
    lattice: pd.DataFrame,
    date: str,
    symbol: str = None,
) -> pd.Series | pd.DataFrame:
    mask = lattice["date"] == pd.Timestamp(date)
    if symbol:
        mask = mask & (lattice["symbol"] == symbol)
    subset = lattice.loc[mask]
    if symbol and len(subset) == 1:
        return subset.iloc[0]
    return subset
