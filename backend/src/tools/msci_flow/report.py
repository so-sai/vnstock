"""Bộ xuất báo cáo Markdown & JSON cho kiểm toán dòng tiền MSCI (NON-DECISIONAL).

[WHY / ROOT CAUSE / INVARIANT]
WHY:
  Báo cáo dòng tiền định lượng phục vụ kiểm toán rủi ro thanh khoản và nút thắt
  room ngoại, KHÔNG phải tín hiệu giao dịch T+; mọi bản xuất phải hiển thị rõ
  quy chế `NON-DECISIONAL` và cảnh báo thiếu dữ liệu intraday ATC.
ROOT CAUSE:
  Báo cáo thiếu nhãn cảnh báo hoặc tự làm tròn missing thành `0` dễ gây hiểu nhầm
  chiến lược T+ trên cửa sổ OOS 2025-2026 đang bị cách ly.
INVARIANT:
  1. Mọi đầu ra Markdown và JSON BẮT BUỘC chứa `NON-DECISIONAL` và
     `⚠️ CẢNH BÁO VI CẤU TRÚC: THIẾU DỮ LIỆU INTRADAY ATC — KHÔNG THỂ BACKTEST CHIẾN LƯỢC T+`.
  2. Giá trị `None` hiển thị thành `MISSING` / `N/A`, tuyệt đối không in `0` giả.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .models import MICROSTRUCTURE_WARNING_BANNER, NON_DECISIONAL_BANNER, AuditRunResult

# [WHY]: Quy đổi VND sang tỷ đồng (10^9) để bảng Markdown đọc nhanh áp lực dòng tiền quy mô ETF;
# không làm thay đổi số thô trong JSON.
VND_PER_BILLION = 1_000_000_000.0

# [WHY]: Làm tròn số nguyên cổ phiếu / 2 chữ số DTL cho dễ đọc trên terminal/Markdown; JSON vẫn giữ đầy đủ độ chính xác float.
DTL_PRECISION = 2


def _fmt_num(val: float | None, decimals: int = 0) -> str:
    if val is None:
        return "MISSING"
    if decimals == 0:
        return f"{val:,.0f}"
    return f"{val:,.{decimals}f}"


def _fmt_vnd_billion(val: float | None) -> str:
    if val is None:
        return "MISSING"
    return f"{val / VND_PER_BILLION:+,.2f}B"


def render_markdown_report(run: AuditRunResult) -> str:
    """Biên xuất báo cáo Markdown kiểm toán / survey MSCI."""
    lines: list[str] = [
        f"# Báo cáo Kiểm toán Dòng tiền MSCI — {run.review_name}",
        "",
        f"> **{NON_DECISIONAL_BANNER}**",
        ">",
        f"> **{MICROSTRUCTURE_WARNING_BANNER}**",
        "",
        "## 1. Trạng thái Thực thi & Nguồn Dữ liệu",
        "",
        f"- **Chế độ (`mode`)**: `{run.mode}` | **Trạng thái (`status`)**: `{run.status}` | **Exit code**: `{run.exit_code}`",
        f"- **Ngày hiệu lực ATC**: `{run.effective_date or 'MISSING'}` | **Công bố SAIR**: `{run.announcement_date or 'N/A'}`",
        (
            f"- **Tỷ giá USD/VND**: `{_fmt_num(run.fx_vnd_per_usd, 1)}` "
            f"(ngày `{run.fx_as_of_date or 'N/A'}`, nguồn `{run.fx_source or 'MISSING'}`)"
        ),
        f"- **Dry-run**: `{run.dry_run}` | **Audit ID**: `{run.audit_id if run.audit_id is not None else 'NONE'}`",
    ]
    if run.input_hash:
        lines.append(f"- **Input SHA-256**: `{run.input_hash}`")

    if run.refusal_reasons:
        lines.extend(["", "### Lý do Từ chối Fail-Closed", ""])
        for reason in run.refusal_reasons:
            lines.append(f"- `{reason}`")

    if run.results:
        lines.extend(
            [
                "",
                "## 2. Chi tiết Cổ phiếu Theo Rổ & Thanh khoản DB Nội bộ",
                "",
                (
                    "| Mã | Nhóm | Trạng thái | Ngày | Close (VND) | ADV20 (cp) | "
                    "Flow Ròng | Est. Shares | DTL | Nhóm DTL | Buyable Floor | "
                    "Health V2 | Net Income (VND) | Cờ Cảnh báo |"
                ),
                "|---|---|---|---|---:|---:|---:|---:|---:|---|---:|---|---:|---|",
            ]
        )
        for row in run.results:
            flags_str = ", ".join(row.flags) if row.flags else "-"
            health_str = row.health_archetype or "MISSING"
            lines.append(
                f"| `{row.symbol}` | `{row.bucket}` | `{row.status}` | `{row.last_trade_date or 'MISSING'}` | "
                f"{_fmt_num(row.close_vnd)} | {_fmt_num(row.adv20_shares)} | "
                f"{_fmt_vnd_billion(row.net_flow_vnd)} | {_fmt_num(row.estimated_shares)} | "
                f"{_fmt_num(row.dtl, DTL_PRECISION) if row.dtl is not None else 'N/A'} | "
                f"`{row.dtl_bucket or 'N/A'}` | {_fmt_num(row.on_exchange_buyable_shares)} | "
                f"`{health_str}` | {_fmt_vnd_billion(row.net_income_vnd)} | `{flags_str}` |"
            )

    lines.append("")
    return "\n".join(lines)


def serialize_run_json(run: AuditRunResult) -> dict[str, Any]:
    """Chuyển `AuditRunResult` thành dict JSON-serializable giữ nguyên `None`."""
    return asdict(run)


def write_report_outputs(
    run: AuditRunResult,
    *,
    md_out: str | Path | None = None,
    json_out: str | Path | None = None,
) -> None:
    """Ghi file Markdown và/hoặc JSON nếu người dùng truyền `--out` / `--json-out`."""
    if md_out is not None:
        md_path = Path(md_out)
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(render_markdown_report(run), encoding="utf-8")

    if json_out is not None:
        json_path = Path(json_out)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        payload = serialize_run_json(run)
        json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
