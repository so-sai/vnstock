"""Ghi sổ cái kiểm toán bất biến vào `eod_write_audit_ledger` cho công cụ MSCI.

[WHY / ROOT CAUSE / INVARIANT]
WHY:
  Mọi lần chạy tính toán dòng tiền MSCI có dữ liệu hợp lệ (không `--dry-run` và
  không bị từ chối fail-closed) phải lưu lại dấu vết kiểm toán gồm `input_hash`
  SHA-256 đầy đủ (64 ký tự hex), tham số kỳ cơ cấu và số mã được tính.
ROOT CAUSE:
  Chạy tính toán tự phát không lưu sổ cái khiến không thể đối chiếu lại phiên
  bản manifest và tỷ giá FX nào đã dùng khi tạo báo cáo lịch sử.
INVARIANT:
  1. Ở chế độ `--dry-run` hoặc `--survey` hoặc khi bị từ chối (exit 2): TUYỆT ĐỐI
     không ghi vào `eod_write_audit_ledger`.
  2. Khi ghi, sử dụng định danh logic `target_table='msci_flow_estimate'` và
     `status='COMPUTED'` thông qua `record_eod_write_audit` (SHA-256 64 hex).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import sqlite3
from dataclasses import asdict
from pathlib import Path

from src.database.eod_write_audit import record_eod_write_audit

from .market_data import DEFAULT_SCREENER_DB
from .models import AuditRunResult, ManifestSpec

MSCI_AUDIT_CALLER = "msci_flow_estimate"
MSCI_AUDIT_TARGET_TABLE = "msci_flow_estimate"


def build_input_hash(spec: ManifestSpec, fx_vnd_per_usd: float | None) -> str:
    """Tính mã băm SHA-256 đầy đủ (64 hex) đại diện bất biến cho payload đầu vào."""
    payload = {
        "review_name": spec.review_name,
        "effective_date": spec.effective_date,
        "announcement_date": spec.announcement_date,
        "fx_vnd_per_usd": fx_vnd_per_usd,
        "funds": [asdict(f) for f in spec.funds],
        "symbols": [asdict(s) for s in spec.symbols],
    }
    serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def record_msci_audit_entry(
    *,
    spec: ManifestSpec,
    run_result: AuditRunResult,
    screener_db_path: str | Path = DEFAULT_SCREENER_DB,
) -> int | None:
    """Ghi một bản ghi `COMPUTED` vào `eod_write_audit_ledger` (no-op khi dry_run).

    Trả về `audit_id` hoặc `None` nếu là dry-run / không đạt `COMPUTED`.
    """
    if run_result.dry_run or run_result.status != "COMPUTED":
        return None

    db_file = Path(screener_db_path)
    if not db_file.exists():
        raise FileNotFoundError(f"Không tìm thấy DB để ghi audit ledger: {db_file}")

    input_payload = {
        "review_name": spec.review_name,
        "effective_date": spec.effective_date,
        "announcement_date": spec.announcement_date,
        "fx_vnd_per_usd": run_result.fx_vnd_per_usd,
        "funds": [asdict(f) for f in spec.funds],
        "symbols": [asdict(s) for s in spec.symbols],
    }
    params = {
        "review_name": spec.review_name,
        "effective_date": spec.effective_date,
        "symbol_count": len(run_result.results),
        "fx_vnd_per_usd": run_result.fx_vnd_per_usd,
        "fx_as_of_date": run_result.fx_as_of_date,
        "fx_source": run_result.fx_source,
    }
    notes = f"MSCI audit COMPUTED for {len(run_result.results)} symbols (effective {spec.effective_date})"

    with contextlib.closing(sqlite3.connect(str(db_file))) as conn:
        audit_id = record_eod_write_audit(
            as_of_date=spec.effective_date,
            caller_module=MSCI_AUDIT_CALLER,
            target_table=MSCI_AUDIT_TARGET_TABLE,
            parameters=params,
            input_data=input_payload,
            output_score=float(len(run_result.results)),
            status="COMPUTED",
            notes=notes,
            conn=conn,
            commit=True,
        )
    return audit_id
