"""TDD tests for Governor Boundary Schemas (Pydantic v2 Data Membrane).

Tests follow TDD Red->Green: every schema must reject invalid data at the boundary.
Run: python -m pytest backend/tests/test_governor_schemas.py -v
"""

import sys

import pydantic
import pytest
from conftest import PROJECT_ROOT

sys.path.insert(0, str(PROJECT_ROOT / "backend" / "src"))

from src.governor.schemas import (
    CompanyHealthSnapshot,
    DeltaParamsSchema,
    HealthRatioSchema,
    MacroRecordSchema,
    MacroVectorSchema,
    PolicyEventInputSchema,
    StrictBaseModel,
    VN20GateInput,
    safe_validate,
)


# ── StrictBaseModel ──────────────────────────────────────────────────────────
class TestStrictBaseModel:
    """StrictBaseModel enforces frozen + extra-forbid."""

    def test_rejects_extra_fields(self):
        class Dummy(StrictBaseModel):
            x: int = 1

        with pytest.raises(pydantic.ValidationError):
            Dummy(x=1, y=2)

    def test_frozen_immutability(self):
        class Dummy(StrictBaseModel):
            x: int = 1

        obj = Dummy(x=10)
        with pytest.raises(pydantic.ValidationError):
            obj.x = 20


# ── MacroRecordSchema ────────────────────────────────────────────────────────
class TestMacroRecordSchema:
    """Validates macro_history rows from SQLite."""

    def test_valid_record(self):
        r = MacroRecordSchema(variable="US10Y", date="2026-08-05", value=4.25)
        assert r.variable == "US10Y"
        assert r.value == 4.25
        assert r.source == "vnstock"

    def test_rejects_invalid_date_format(self):
        with pytest.raises(pydantic.ValidationError):
            MacroRecordSchema(variable="US10Y", date="2026/08/05", value=4.25)

    def test_rejects_empty_variable(self):
        with pytest.raises(pydantic.ValidationError):
            MacroRecordSchema(variable="", date="2026-08-05", value=4.25)

    def test_rejects_non_numeric_value(self):
        with pytest.raises(pydantic.ValidationError):
            MacroRecordSchema(variable="US10Y", date="2026-08-05", value="high")

    def test_rejects_extra_fields(self):
        with pytest.raises(pydantic.ValidationError):
            MacroRecordSchema(variable="US10Y", date="2026-08-05", value=4.25, hack=True)

    def test_is_synthetic_default_zero(self):
        r = MacroRecordSchema(variable="US10Y", date="2026-08-05", value=4.25)
        assert r.is_synthetic == 0


# ── MacroVectorSchema ────────────────────────────────────────────────────────
class TestMacroVectorSchema:
    """Validates 5-component macro state vector normalized [0,1]."""

    def test_valid_vector(self):
        v = MacroVectorSchema(us10y=0.5, vnd_usd=0.6, interbank=0.3, fii_flow=0.7, breadth=0.8)
        assert v.us10y == 0.5

    def test_rejects_out_of_range(self):
        with pytest.raises(pydantic.ValidationError):
            MacroVectorSchema(us10y=1.5, vnd_usd=0.6, interbank=0.3, fii_flow=0.7, breadth=0.8)

    def test_rejects_negative(self):
        with pytest.raises(pydantic.ValidationError):
            MacroVectorSchema(us10y=-0.1, vnd_usd=0.6, interbank=0.3, fii_flow=0.7, breadth=0.8)


# ── HealthRatioSchema ────────────────────────────────────────────────────────
class TestHealthRatioSchema:
    """Validates health_ratios data after tuple unpacking."""

    def test_valid_ratio(self):
        h = HealthRatioSchema(symbol="VCB", period="2025Q3", roe=0.17)
        assert h.symbol == "VCB"
        assert h.period == "2025Q3"

    def test_rejects_invalid_period(self):
        with pytest.raises(pydantic.ValidationError):
            HealthRatioSchema(symbol="VCB", period="2025", roe=0.17)

    def test_rejects_period_without_q(self):
        with pytest.raises(pydantic.ValidationError):
            HealthRatioSchema(symbol="VCB", period="202503", roe=0.17)

    def test_optional_fields_default_none(self):
        h = HealthRatioSchema(symbol="VCB", period="2025Q3")
        assert h.roe is None
        assert h.capital_ratio is None
        assert h.nim is None

    def test_rejects_npl_out_of_range(self):
        with pytest.raises(pydantic.ValidationError):
            HealthRatioSchema(symbol="VCB", period="2025Q3", npl_ratio=1.5)


# ── CompanyHealthSnapshot ────────────────────────────────────────────────────
class TestCompanyHealthSnapshot:
    """Validates aggregated company health data."""

    def test_valid_snapshot(self):
        s = CompanyHealthSnapshot(
            symbol="VCB",
            target_date="2026-08-05",
            roe_annual=0.17,
            is_bank=True,
            capital_ratio=0.098,
            nim_annual=0.014,
            npl_ratio=0.014,
            volume=5_000_000,
        )
        assert s.is_bank is True

    def test_rejects_invalid_date(self):
        with pytest.raises(pydantic.ValidationError):
            CompanyHealthSnapshot(
                symbol="VCB",
                target_date="05-08-2026",
                roe_annual=0.17,
                volume=1000,
            )

    def test_rejects_roe_extreme(self):
        with pytest.raises(pydantic.ValidationError):
            CompanyHealthSnapshot(
                symbol="VCB",
                target_date="2026-08-05",
                roe_annual=10.0,
                volume=1000,
            )


# ── DeltaParamsSchema ────────────────────────────────────────────────────────
class TestDeltaParamsSchema:
    """Validates delta_params inside PolicyEvent."""

    def test_valid_params(self):
        d = DeltaParamsSchema(ldr_relief_bps=500.0, nim_boost_bps=10.0)
        assert d.ldr_relief_bps == 500.0

    def test_all_optional(self):
        d = DeltaParamsSchema()
        assert d.ldr_relief_bps is None

    def test_rejects_ldr_out_of_range(self):
        with pytest.raises(pydantic.ValidationError):
            DeltaParamsSchema(ldr_relief_bps=5000.0)

    def test_rejects_negative_npl_boost(self):
        with pytest.raises(pydantic.ValidationError):
            DeltaParamsSchema(nim_boost_bps=-200.0)

    def test_gate_relaxation_dict(self):
        d = DeltaParamsSchema(gate_relaxation={"CAPITAL_RATIO": 0.05})
        assert d.gate_relaxation["CAPITAL_RATIO"] == 0.05


# ── PolicyEventInputSchema ───────────────────────────────────────────────────
class TestPolicyEventInputSchema:
    """Validates raw dict before constructing PolicyEvent dataclass."""

    def test_valid_event(self):
        e = PolicyEventInputSchema(
            id="QD1743",
            title="QD 1743",
            effective_date="2026-08-01",
            expiry_date="2028-07-31",
            event_type="KBNN_LDR_ADJUSTMENT",
            half_life_days=15.0,
            decay_window_days=90,
            clusters={"SOCB_BIG4": 1.0},
            delta_params={"ldr_relief_bps": 500.0},
        )
        assert e.id == "QD1743"

    def test_rejects_missing_required(self):
        with pytest.raises(pydantic.ValidationError):
            PolicyEventInputSchema(id="X", title="X")

    def test_rejects_invalid_effective_date(self):
        with pytest.raises(pydantic.ValidationError):
            PolicyEventInputSchema(
                id="X",
                title="X",
                effective_date="not-a-date",
                expiry_date="2028-07-31",
                event_type="KBNN_LDR_ADJUSTMENT",
                half_life_days=15.0,
            )

    def test_rejects_invalid_delta_params_value(self):
        with pytest.raises(pydantic.ValidationError):
            PolicyEventInputSchema(
                id="X",
                title="X",
                effective_date="2026-08-01",
                expiry_date="2028-07-31",
                event_type="KBNN_LDR_ADJUSTMENT",
                half_life_days=15.0,
                delta_params={"ldr_relief_bps": 9999.0},
            )

    def test_rejects_half_life_zero(self):
        with pytest.raises(pydantic.ValidationError):
            PolicyEventInputSchema(
                id="X",
                title="X",
                effective_date="2026-08-01",
                expiry_date="2028-07-31",
                event_type="KBNN_LDR_ADJUSTMENT",
                half_life_days=0.0,
            )


# ── VN20GateInput ────────────────────────────────────────────────────────────
class TestVN20GateInput:
    """Validates inputs to _vn20_gate before branching logic."""

    def test_valid_bank_input(self):
        g = VN20GateInput(
            symbol="VCB",
            target_date="2026-08-05",
            roe_quarterly=0.042,
            capital_ratio=0.098,
            nim_quarterly=0.014,
            volume=5_000_000,
        )
        assert g.capital_ratio == 0.098

    def test_rejects_empty_symbol(self):
        with pytest.raises(pydantic.ValidationError):
            VN20GateInput(symbol="", target_date="2026-08-05", roe_quarterly=0.04)

    def test_rejects_negative_volume(self):
        with pytest.raises(pydantic.ValidationError):
            VN20GateInput(symbol="VCB", target_date="2026-08-05", roe_quarterly=0.04, volume=-1)


# ── safe_validate ────────────────────────────────────────────────────────────
class TestSafeValidate:
    """safe_validate returns None on failure instead of raising."""

    def test_valid_data_returns_model(self):
        data = {"variable": "US10Y", "date": "2026-08-05", "value": 4.25}
        result = safe_validate(MacroRecordSchema, data, "test")
        assert result is not None
        assert result.variable == "US10Y"

    def test_invalid_data_returns_none(self):
        data = {"variable": "", "date": "bad", "value": "not_float"}
        result = safe_validate(MacroRecordSchema, data, "test")
        assert result is None

    def test_none_data_returns_none(self):
        result = safe_validate(MacroRecordSchema, None, "test")
        assert result is None

    def test_empty_dict_returns_none(self):
        result = safe_validate(MacroRecordSchema, {}, "test")
        assert result is None
