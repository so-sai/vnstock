"""CLI runner cho công cụ kiểm toán dòng tiền MSCI (NQ-25 — NON-DECISIONAL).

[WHY / ROOT CAUSE / INVARIANT]
WHY:
  Cho phép gọi trực tiếp qua `python -m backend.src.tools.msci_flow_estimate` (hoặc
  uỷ quyền từ `python ptck.py msci-audit`) để khảo sát thanh khoản DB thực (`--survey`)
  hoặc kiểm toán fail-closed dòng tiền khi manifest ngoại sinh đã kiểm chứng.
ROOT CAUSE:
  Thư mục gốc repo và `backend/` có hai cách thiết lập `sys.path` khác nhau;
  runner cần chuẩn hóa đường dẫn `backend/` trước khi gọi `src.*` để không tạo hai
  bản sao module trong bộ nhớ Python.
INVARIANT:
  1. Exit code `0` chỉ khi `--survey` thành công hoặc `audit_msci_manifest` đạt
     `COMPUTED`.
  2. Thiếu/hỏng manifest, `verified=false`, thiếu lịch sử 20 phiên hoặc `ADV20 == 0`
     -> exit code `2` (`EXIT_REFUSED`) và không ghi `eod_write_audit_ledger`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]


def _ensure_backend_on_path() -> None:
    backend_str = str(BACKEND_DIR)
    if backend_str not in sys.path:
        sys.path.insert(0, backend_str)


def build_arg_parser() -> argparse.ArgumentParser:
    """Tạo parser CLI cho `msci_flow_estimate`."""
    parser = argparse.ArgumentParser(
        description="Kiểm toán dòng tiền cơ cấu MSCI (NON-DECISIONAL, fail-closed).",
    )
    parser.add_argument(
        "--manifest",
        type=str,
        default=None,
        help="Đường dẫn file manifest JSON (mặc định backend/config/msci_manifest_2026_11.json)",
    )
    parser.add_argument(
        "--survey",
        action="store_true",
        help="Khảo sát dữ liệu DB (close, ADV20, FX, Health V2) của các mã trong manifest (read-only)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Chạy kiểm toán fail-closed nhưng không ghi vào eod_write_audit_ledger",
    )
    parser.add_argument(
        "--out",
        type=str,
        default=None,
        help="Xuất báo cáo Markdown ra file",
    )
    parser.add_argument(
        "--json-out",
        type=str,
        default=None,
        help="Xuất báo cáo JSON ra file",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="In JSON ra stdout thay cho bảng Markdown",
    )
    parser.add_argument(
        "--screener-db",
        type=str,
        default=None,
        help="Ghi đè đường dẫn screener_cache.db (phục vụ kiểm thử)",
    )
    parser.add_argument(
        "--financial-db",
        type=str,
        default=None,
        help="Ghi đè đường dẫn financial_facts.db (phục vụ kiểm thử)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Điểm vào CLI trả về mã thoát nguyên (`0` hoặc `2`)."""
    if (
        sys.platform == "win32"
        and hasattr(sys.stdout, "reconfigure")
        and (getattr(sys.stdout, "encoding", "") or "").lower() != "utf-8"
    ):
        sys.stdout.reconfigure(encoding="utf-8")

    _ensure_backend_on_path()
    from src.research.msci_flow_auditor import audit_msci_manifest, run_msci_survey
    from src.tools.msci_flow.market_data import DEFAULT_FINANCIAL_DB, DEFAULT_SCREENER_DB
    from src.tools.msci_flow.report import render_markdown_report, serialize_run_json

    args = build_arg_parser().parse_args(argv)
    screener_db = Path(args.screener_db) if args.screener_db else DEFAULT_SCREENER_DB
    financial_db = Path(args.financial_db) if args.financial_db else DEFAULT_FINANCIAL_DB

    if args.survey:
        run = run_msci_survey(
            manifest_path=args.manifest,
            screener_db_path=screener_db,
            financial_db_path=financial_db,
            md_out=args.out,
            json_out=args.json_out,
        )
    else:
        run = audit_msci_manifest(
            manifest_path=args.manifest,
            screener_db_path=screener_db,
            financial_db_path=financial_db,
            dry_run=args.dry_run,
            md_out=args.out,
            json_out=args.json_out,
        )

    if args.json:
        print(json.dumps(serialize_run_json(run), indent=2, ensure_ascii=False))
    else:
        print(render_markdown_report(run))

    return run.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
