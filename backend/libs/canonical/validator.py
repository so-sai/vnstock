from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Optional

from .core import CanonicalRecord
from .registry import VariableSpec


class ValidationError(ValueError):
    """Raised when a CanonicalRecord fails validation."""


class Validator:
    """Validates CanonicalRecords against their VariableSpec constraints."""

    def validate(self, record: CanonicalRecord, spec: VariableSpec):
        """Run all validation checks. Raises ValidationError on first failure."""
        self._check_range(record, spec)
        self._check_unit_consistency(record, spec)
        self._check_source(record, spec)

    def _check_range(self, record: CanonicalRecord, spec: VariableSpec):
        if spec.min_value is not None and record.value < spec.min_value:
            raise ValidationError(
                f"{record.variable} value {record.value} < min {spec.min_value} "
                f"(unit={spec.canonical_unit.value})"
            )
        if spec.max_value is not None and record.value > spec.max_value:
            raise ValidationError(
                f"{record.variable} value {record.value} > max {spec.max_value} "
                f"(unit={spec.canonical_unit.value})"
            )

    def _check_unit_consistency(self, record: CanonicalRecord, spec: VariableSpec):
        if record.unit != spec.canonical_unit:
            raise ValidationError(
                f"{record.variable} unit mismatch: "
                f"record has {record.unit.value}, spec requires {spec.canonical_unit.value}"
            )

    def _check_source(self, record: CanonicalRecord, spec: VariableSpec):
        if record.source not in spec.allowed_sources:
            raise ValidationError(
                f"{record.variable} source '{record.source.value}' not in "
                f"allowed: {[s.value for s in spec.allowed_sources]}"
            )

    def validate_batch(self, records: List[CanonicalRecord]) -> List[CanonicalRecord]:
        """Filter batch: return only valid records."""
        valid = []
        for r in records:
            spec = CanonicalAssetRegistry().get(r.variable)
            if spec is None:
                continue
            try:
                self.validate(r, spec)
                valid.append(r)
            except ValidationError:
                continue
        return valid
