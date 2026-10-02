"""polarity_reconcile.py — C3-6 Signed Graph reconciliation hook (Design C3-1 §6).

WHY:
  - Commit 2e44e16 gave all 46 registry edges a polarity in {-1, 0, +1}, but
    polarity stays PASSIVE metadata until a consumer adjudicates it.
  - Risk: a narrative direction (BULLISH/BEARISH) can contradict the structural
    sign of the causal chain it rides on — silent inconsistency from the math
    core to the rendered UI.
  - §6 rule (ratified): polarity wins + POLARITY_NARRATIVE_CONFLICT warning.
    Không bao giờ silent: mỗi conflict đều log WARNING, không suppress.

CONTRACT:
  - calculate_path_sign(edges): product of edge polarities.
      * ANY edge polarity == 0  -> whole path = 0 (regime-conditional edge làm
        cả chuỗi indeterminate — bám semantics REGIME_CONDITIONAL_ALLOWLIST).
      * empty chain             -> 0 (không có cấu trúc = không có khẳng định;
        zero-hallucination: thiếu -> đứng ngoài, KHÔNG mặc định +1).
      * polarity missing (dict/attr) -> 0 (thiếu -> đứng ngoài).
      * polarity outside {-1,0,1}    -> ValueError (fail-fast; registry contract
        test test_causal_edge_polarity guarantees 46 edges không bao giờ chạm).
  - reconcile(path_sign, narrative, *, context):
      * path_sign == 0                  -> pass-through, no warning.
      * narrative not in {BULLISH,BEARISH} -> pass-through (không có khẳng định
        để mâu thuẫn — NEUTRAL/TRANSITIONAL/FRACTURED đứng ngoài).
      * structural == narrative         -> pass-through, no warning.
      * conflict -> direction = structural (polarity thắng, symmetric ±1),
        warning = POLARITY_NARRATIVE_CONFLICT + logger.warning("POLARITY_MISMATCH ...").
  - Consumer quyết định dùng direction (ép) hay chỉ hiển thị phân kỳ.
    Wiring hiện tại tại csi_explain là LOG-ONLY (render không đổi).
"""

import logging
from collections.abc import Iterable
from dataclasses import dataclass

logger = logging.getLogger(__name__)

WARNING_POLARITY_CONFLICT = "POLARITY_NARRATIVE_CONFLICT"

_DEFINITE_DIRECTIONS = frozenset({"BULLISH", "BEARISH"})


@dataclass(frozen=True)
class ReconcileResult:
    """Outcome của §6 reconciliation: hướng chốt + warning (hoặc None)."""

    direction: str
    warning: str | None
    path_sign: int


def _edge_polarity(edge: object) -> int:
    """Extract polarity từ hop dict hoặc CausalEdge — thiếu -> 0, sai kiểu -> raise."""
    if isinstance(edge, dict):
        raw = edge.get("polarity", 0)
    else:
        raw = getattr(edge, "polarity", 0)
    if raw is None:
        return 0
    if isinstance(raw, bool) or not isinstance(raw, int) or raw not in (-1, 0, 1):
        raise ValueError(f"Invalid edge polarity: {raw!r}")
    return raw


def calculate_path_sign(edges: Iterable) -> int:
    """Product polarity dọc chuỗi. ANY 0 -> 0; rỗng -> 0 (indeterminate)."""
    sign = 1
    count = 0
    for edge in edges:
        sign *= _edge_polarity(edge)
        count += 1
    return sign if count else 0


def reconcile(path_sign: int, narrative: str, *, context: str = "?") -> ReconcileResult:
    """§6: polarity thắng narrative khi xung đột — ép hướng + warning (không silent)."""
    if path_sign not in (-1, 0, 1):
        raise ValueError(f"Invalid path_sign: {path_sign!r}")
    if path_sign == 0 or narrative not in _DEFINITE_DIRECTIONS:
        return ReconcileResult(direction=narrative, warning=None, path_sign=path_sign)
    structural = "BULLISH" if path_sign > 0 else "BEARISH"
    if structural == narrative:
        return ReconcileResult(direction=narrative, warning=None, path_sign=path_sign)
    logger.warning(
        "POLARITY_MISMATCH on %s: structural=%s vs narrative=%s (path_sign=%d)",
        context,
        structural,
        narrative,
        path_sign,
    )
    return ReconcileResult(direction=structural, warning=WARNING_POLARITY_CONFLICT, path_sign=path_sign)
