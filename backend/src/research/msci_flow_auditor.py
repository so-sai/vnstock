"""Bộ điều phối kiểm toán & khảo sát dòng tiền MSCI Q4-2026 (NON-DECISIONAL).

[WHY / ROOT CAUSE / INVARIANT]
WHY:
  Tạo điểm điều phối thống nhất cho cả CLI `ptck.py msci-audit` và runner
  `python -m backend.src.tools.msci_flow_estimate`, nối chặt đầu vào manifest
  ngoại sinh (đã verify) với dữ liệu giá/ADV20/FX/Health nội bộ.
ROOT CAUSE:
  Tách rời khảo sát DB (`--survey`) khỏi tính toán dòng tiền fail-closed khiến
  dễ sinh nhầm thói quen điền giả định tạm vào manifest rồi quên xóa trước khi
  xuất báo cáo định lượng.
INVARIANT:
  1. Ở chế độ tính toán mặc định (strict): mọi thiếu sót manifest/DB/thanh khoản
     đều trả exit code `2` (`EXIT_REFUSED`) và không ghi sổ cái.
  2. Chế độ `--survey` CHỈ đọc DB thực tại cho danh mục 15 mã, KHÔNG tự tính
     dòng tiền giả khi manifest chưa verify, KHÔNG ghi ledger, trả exit `0`.
  3. Mọi báo cáo đều thuộc vùng cách ly OOS 2025-2026 (`NON-DECISIONAL`) và gắn
     cảnh báo thiếu dữ liệu intraday ATC.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.tools.msci_flow.calculator import compute_symbol_flow
from src.tools.msci_flow.ledger import build_input_hash, record_msci_audit_entry
from src.tools.msci_flow.manifest import load_manifest, validate_manifest_strict
from src.tools.msci_flow.market_data import (
    DEFAULT_FINANCIAL_DB,
    DEFAULT_SCREENER_DB,
    load_fx_vnd_per_usd,
    load_symbol_market_snapshot,
)
from src.tools.msci_flow.models import (
    EXIT_OK,
    EXIT_REFUSED,
    MIN_ADV20_SESSIONS,
    STATUS_COMPUTED,
    STATUS_REFUSED_INSUFFICIENT_HISTORY,
    STATUS_REFUSED_INVALID_INPUT,
    STATUS_REFUSED_MISSING_DATA,
    STATUS_REFUSED_ZERO_LIQUIDITY,
    STATUS_SURVEY_OK,
    AuditRunResult,
    ManifestSpec,
    SymbolFlowResult,
)
from src.tools.msci_flow.report import write_report_outputs

BACKEND_DIR = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST_PATH = BACKEND_DIR / "config" / "msci_manifest_2026_11.json"

# [WHY]: Thứ tự ưu tiên mã từ chối khi nhiều mã hỏng theo các lỗi khác nhau trong cùng 1 lần chạy;
# ưu tiên lỗi dữ liệu sai trước lỗi thiếu lịch sử/thanh khoản.
_REFUSAL_PRIORITY: tuple[str, ...] = (
    STATUS_REFUSED_INVALID_INPUT,
    STATUS_REFUSED_INSUFFICIENT_HISTORY,
    STATUS_REFUSED_ZERO_LIQUIDITY,
    STATUS_REFUSED_MISSING_DATA,
)


def _resolve_fx(
    spec: ManifestSpec,
    screener_db_path: str | Path,
) -> tuple[float | None, str | None, str | None]:
    if spec.fx_vnd_per_usd_override is not None:
        return (
            float(spec.fx_vnd_per_usd_override),
            spec.effective_date or None,
            spec.fx_source or "manifest:fx_vnd_per_usd_override",
        )
    return load_fx_vnd_per_usd(screener_db_path)


def _pick_aggregate_refusal_status(statuses: set[str]) -> str:
    if len(statuses) == 1:
        return next(iter(statuses))
    for candidate in _REFUSAL_PRIORITY:
        if candidate in statuses:
            return candidate
    return STATUS_REFUSED_MISSING_DATA


def _load_manifest_or_refuse(
    manifest_path: str | Path | None,
    *,
    mode: str,
    dry_run: bool,
) -> tuple[ManifestSpec | None, AuditRunResult | None]:
    resolved_manifest = Path(manifest_path) if manifest_path is not None else DEFAULT_MANIFEST_PATH
    try:
        spec = load_manifest(resolved_manifest)
        return spec, None
    except FileNotFoundError as exc:
        return None, AuditRunResult(
            mode=mode,
            status=STATUS_REFUSED_MISSING_DATA,
            exit_code=EXIT_REFUSED,
            review_name="MSCI Index Review",
            effective_date="",
            announcement_date=None,
            fx_vnd_per_usd=None,
            fx_as_of_date=None,
            fx_source=None,
            results=(),
            refusal_reasons=(str(exc),),
            dry_run=dry_run,
        )
    except (ValueError, json.JSONDecodeError) as exc:
        return None, AuditRunResult(
            mode=mode,
            status=STATUS_REFUSED_INVALID_INPUT,
            exit_code=EXIT_REFUSED,
            review_name="MSCI Index Review",
            effective_date="",
            announcement_date=None,
            fx_vnd_per_usd=None,
            fx_as_of_date=None,
            fx_source=None,
            results=(),
            refusal_reasons=(f"Manifest JSON không hợp lệ: {exc}",),
            dry_run=dry_run,
        )


def run_msci_survey(
    *,
    manifest_path: str | Path | None = None,
    screener_db_path: str | Path = DEFAULT_SCREENER_DB,
    financial_db_path: str | Path = DEFAULT_FINANCIAL_DB,
    md_out: str | Path | None = None,
    json_out: str | Path | None = None,
) -> AuditRunResult:
    """Chế độ `--survey`: khảo sát độ phủ OHLCV/ADV20/FX/Health thực tế (read-only, exit 0)."""
    spec, err_run = _load_manifest_or_refuse(manifest_path, mode="survey", dry_run=True)
    if err_run is not None:
        write_report_outputs(err_run, md_out=md_out, json_out=json_out)
        return err_run

    assert spec is not None
    fx_rate, fx_as_of, fx_source = _resolve_fx(spec, screener_db_path)

    survey_rows: list[SymbolFlowResult] = []
    for sym_spec in spec.symbols:
        snap = load_symbol_market_snapshot(
            sym_spec.symbol,
            screener_db_path=screener_db_path,
            financial_db_path=financial_db_path,
        )
        flags: list[str] = []
        row_status = STATUS_SURVEY_OK
        detail = snap.market_error_detail

        if snap.market_error_code is not None:
            row_status = snap.market_error_code
            flags.append(snap.market_error_code)
        else:
            if snap.valid_session_count < MIN_ADV20_SESSIONS:
                flags.append(STATUS_REFUSED_INSUFFICIENT_HISTORY)
            if snap.adv20_shares is None or snap.adv20_shares <= 0:
                flags.append(STATUS_REFUSED_ZERO_LIQUIDITY)
            if not sym_spec.verified:
                flags.append("MISSING_EXOGENOUS_INPUTS")
            if sym_spec.available_room_shares is None and sym_spec.foreign_room_pct is None:
                flags.append("MISSING_FOREIGN_ROOM")
            if snap.health_archetype is None and snap.net_income_vnd is None:
                flags.append("MISSING_FUNDAMENTAL_HEALTH")
            is_weak = snap.health_archetype == "DISTRESSED" or (snap.net_income_vnd is not None and snap.net_income_vnd < 0)
            if is_weak and sym_spec.bucket in {"ADD_STD", "ADD_SC", "DOWNGRADE_STD_TO_SC"}:
                flags.append("POTENTIAL_EXIT_LIQUIDITY_TRAP")

        survey_rows.append(
            SymbolFlowResult(
                symbol=sym_spec.symbol,
                bucket=sym_spec.bucket,
                status=row_status,
                last_trade_date=snap.last_trade_date,
                close_vnd=snap.close_vnd,
                adv20_shares=snap.adv20_shares,
                valid_session_count=snap.valid_session_count,
                fx_vnd_per_usd=fx_rate,
                flow_std_vnd=None,
                flow_sc_vnd=None,
                net_flow_vnd=None,
                estimated_shares=None,
                dtl=None,
                dtl_bucket=None,
                fol=sym_spec.fol,
                available_room_shares=sym_spec.available_room_shares,
                foreign_room_pct=sym_spec.foreign_room_pct,
                on_exchange_buyable_shares=None,
                health_archetype=snap.health_archetype,
                latest_health_period=snap.latest_health_period,
                net_income_vnd=snap.net_income_vnd,
                net_income_period=snap.net_income_period,
                flags=tuple(flags),
                error_detail=detail,
            )
        )

    run = AuditRunResult(
        mode="survey",
        status=STATUS_SURVEY_OK,
        exit_code=EXIT_OK,
        review_name=spec.review_name,
        effective_date=spec.effective_date,
        announcement_date=spec.announcement_date,
        fx_vnd_per_usd=fx_rate,
        fx_as_of_date=fx_as_of,
        fx_source=fx_source,
        results=tuple(survey_rows),
        refusal_reasons=(),
        dry_run=True,
        audit_id=None,
        input_hash=None,
    )
    write_report_outputs(run, md_out=md_out, json_out=json_out)
    return run


def audit_msci_manifest(
    *,
    manifest_path: str | Path | None = None,
    screener_db_path: str | Path = DEFAULT_SCREENER_DB,
    financial_db_path: str | Path = DEFAULT_FINANCIAL_DB,
    dry_run: bool = False,
    md_out: str | Path | None = None,
    json_out: str | Path | None = None,
) -> AuditRunResult:
    """Kiểm toán dòng tiền MSCI fail-closed từ manifest JSON + DB nội bộ."""
    spec, err_run = _load_manifest_or_refuse(manifest_path, mode="audit", dry_run=dry_run)
    if err_run is not None:
        write_report_outputs(err_run, md_out=md_out, json_out=json_out)
        return err_run

    assert spec is not None
    err_status, err_reasons = validate_manifest_strict(spec)
    if err_status is not None:
        refused = AuditRunResult(
            mode="audit",
            status=err_status,
            exit_code=EXIT_REFUSED,
            review_name=spec.review_name,
            effective_date=spec.effective_date,
            announcement_date=spec.announcement_date,
            fx_vnd_per_usd=None,
            fx_as_of_date=None,
            fx_source=spec.fx_source,
            results=(),
            refusal_reasons=err_reasons,
            dry_run=dry_run,
            audit_id=None,
            input_hash=None,
        )
        write_report_outputs(refused, md_out=md_out, json_out=json_out)
        return refused

    fx_rate, fx_as_of, fx_source = _resolve_fx(spec, screener_db_path)
    if fx_rate is None:
        refused = AuditRunResult(
            mode="audit",
            status=STATUS_REFUSED_MISSING_DATA,
            exit_code=EXIT_REFUSED,
            review_name=spec.review_name,
            effective_date=spec.effective_date,
            announcement_date=spec.announcement_date,
            fx_vnd_per_usd=None,
            fx_as_of_date=fx_as_of,
            fx_source=fx_source,
            results=(),
            refusal_reasons=("Không đọc được tỷ giá USD/VND từ DB và không có fx_vnd_per_usd_override",),
            dry_run=dry_run,
            audit_id=None,
            input_hash=None,
        )
        write_report_outputs(refused, md_out=md_out, json_out=json_out)
        return refused

    input_hash = build_input_hash(spec, fx_rate)

    computed_rows: list[SymbolFlowResult] = []
    for sym_spec in spec.symbols:
        snap = load_symbol_market_snapshot(
            sym_spec.symbol,
            screener_db_path=screener_db_path,
            financial_db_path=financial_db_path,
        )
        computed_rows.append(
            compute_symbol_flow(
                spec=sym_spec,
                snapshot=snap,
                funds=spec.funds,
                fx_vnd_per_usd=fx_rate,
            )
        )

    failed_rows = [row for row in computed_rows if row.status != STATUS_COMPUTED]
    if failed_rows:
        agg_status = _pick_aggregate_refusal_status({row.status for row in failed_rows})
        reasons = tuple(f"{row.symbol}: {row.error_detail or row.status}" for row in failed_rows)
        refused = AuditRunResult(
            mode="audit",
            status=agg_status,
            exit_code=EXIT_REFUSED,
            review_name=spec.review_name,
            effective_date=spec.effective_date,
            announcement_date=spec.announcement_date,
            fx_vnd_per_usd=fx_rate,
            fx_as_of_date=fx_as_of,
            fx_source=fx_source,
            results=tuple(computed_rows),
            refusal_reasons=reasons,
            dry_run=dry_run,
            audit_id=None,
            input_hash=input_hash,
        )
        write_report_outputs(refused, md_out=md_out, json_out=json_out)
        return refused

    computed_run = AuditRunResult(
        mode="audit",
        status=STATUS_COMPUTED,
        exit_code=EXIT_OK,
        review_name=spec.review_name,
        effective_date=spec.effective_date,
        announcement_date=spec.announcement_date,
        fx_vnd_per_usd=fx_rate,
        fx_as_of_date=fx_as_of,
        fx_source=fx_source,
        results=tuple(computed_rows),
        refusal_reasons=(),
        dry_run=dry_run,
        audit_id=None,
        input_hash=input_hash,
    )

    if not dry_run:
        audit_id = record_msci_audit_entry(
            spec=spec,
            run_result=computed_run,
            screener_db_path=screener_db_path,
        )
        computed_run = AuditRunResult(
            mode=computed_run.mode,
            status=computed_run.status,
            exit_code=computed_run.exit_code,
            review_name=computed_run.review_name,
            effective_date=computed_run.effective_date,
            announcement_date=computed_run.announcement_date,
            fx_vnd_per_usd=computed_run.fx_vnd_per_usd,
            fx_as_of_date=computed_run.fx_as_of_date,
            fx_source=computed_run.fx_source,
            results=computed_run.results,
            refusal_reasons=computed_run.refusal_reasons,
            dry_run=computed_run.dry_run,
            audit_id=audit_id,
            input_hash=computed_run.input_hash,
            banners=computed_run.banners,
        )

    write_report_outputs(computed_run, md_out=md_out, json_out=json_out)
    return computed_run
