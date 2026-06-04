from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from .core import AssetClass, CanonicalRecord, CanonicalUnit, DataSource
from .registry import CanonicalAssetRegistry, VariableSpec
from .validator import Validator


class Normalizer:
    """Transforms raw data points into validated CanonicalRecords."""

    def __init__(self):
        self.registry = CanonicalAssetRegistry()
        self.validator = Validator()

    def normalize(
        self,
        variable: str,
        date: str,
        raw_value: float,
        source: DataSource | str,
        raw_unit: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> CanonicalRecord:
        """Convert a single raw data point to canonical form.

        Steps:
        1. Look up variable spec from registry
        2. Verify source is allowed for this variable
        3. Apply normalization factor
        4. Validate range
        5. Build CanonicalRecord
        """
        spec = self.registry.get(variable)
        if spec is None:
            raise ValueError(
                f"Unknown variable '{variable}'. "
                f"Known: {sorted(self.registry.all_variables())}"
            )

        if isinstance(source, str):
            source = DataSource(source)

        if source not in spec.allowed_sources:
            raise ValueError(
                f"Source '{source.value}' not allowed for '{variable}'. "
                f"Allowed: {[s.value for s in spec.allowed_sources]}"
            )

        raw_unit = raw_unit or spec.raw_unit_label
        raw_val = float(raw_value)
        value = raw_val * spec.normalization_factor

        record = CanonicalRecord(
            variable=variable,
            date=date,
            value=value,
            asset_class=spec.asset_class,
            unit=spec.canonical_unit,
            source=source,
            raw_value=raw_val,
            raw_unit=raw_unit,
            confidence=self.registry.source_trust(source),
            valid_from=None,
            valid_to=None,
            freshness_score=1.0,
            latency_ms=0,
            metadata=metadata or {},
        )

        self.validator.validate(record, spec)
        return record

    def normalize_batch(
        self,
        variable: str,
        records: List[Tuple[str, float]],
        source: DataSource | str,
        raw_unit: Optional[str] = None,
    ) -> List[CanonicalRecord]:
        """Normalize a batch of (date, raw_value) pairs."""
        result = []
        for date, raw_val in records:
            try:
                r = self.normalize(variable, date, raw_val, source, raw_unit)
                result.append(r)
            except ValueError as e:
                continue
        return result

    def infer_variable_from_symbol(self, symbol: str) -> Optional[str]:
        """Try to map a raw ticker symbol to a canonical variable name."""
        mapping = {
            "VNINDEX": "VNINDEX",
            "VN30": "VN30",
            "VN30F1M": "VN30",
            "HNXINDEX": "HNXINDEX",
            "UPCOMINDEX": "UPCOMINDEX",
            "000001.SS": "SH_COMP",
            "DX-Y.NYB": "DXY",
            "GC=F": "GOLD_XAU",
            "^TNX": "US10Y",
            "HG=F": "COPPER_HG",
            "BZ=F": "BRENT_OIL",
            "CL=F": "WTI_OIL",
            "BTC-USD": "BTC",
            "USDVND=X": "USD_VND",
            "CNY=X": "USD_CNY",
            "CNH=X": "USD_CNH",
        }
        return mapping.get(symbol)
