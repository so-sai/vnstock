"""test_causal_edge_polarity.py — C3-1 Phase 3: polarity contract tests.

WHY (Sprint C3-1):
  - CausalEdge registry historically là reachability + confidence graph (vô dấu).
    Pha 1 thêm field `polarity: int = 0`; Pha 2 gán nhãn 46 edges.
  - Contract này khóa 3 bất biến:
    1. Mọi edge có polarity thuộc {-1, 0, +1}.
    2. Edge polarity=0 BẮT BUỘC nằm trong REGIME_CONDITIONAL_ALLOWLIST
       (dấu truly không xác định tĩnh — CASA-quyết định NIM) VÀ description
       phải ghi rõ 'regime_conditional' (chống "0 lười biếng").
    3. Tổng số edges đúng 46 — thêm/bớt edge phải update contract có chủ ý.

NOTE (deferred sang Phase 3b — KHÔNG implement ở đây vì APIs chưa tồn tại,
làm ngay = scope creep đổi behavior):
  - §5 path-confidence LOW_CONFIDENCE tag (cần path.quality API).
  - §6 polarity-wins reconciliation hook (cần reconcile() consumer).

Run:  python -m pytest tests/test_causal_edge_polarity.py -q   (từ backend/)
"""

from src.calibration.causal_edge import build_edge_registry

# Edges được phép mang polarity=0: dấu phụ thuộc regime, không gán tĩnh được.
# Hiện tại: 2 bank NIM edges (CASA cao nới NIM khi tăng lãi suất, CASA thấp bị ép).
REGIME_CONDITIONAL_ALLOWLIST: set[str] = {
    "BANK:INTEREST→NIM",
    "BANK2:INTEREST→NIM",
}

EXPECTED_EDGE_COUNT = 46


def test_registry_edge_count():
    """Tổng edges đúng 46 — thêm/bớt edge phải sửa contract có chủ ý."""
    registry = build_edge_registry()
    assert len(registry) == EXPECTED_EDGE_COUNT, (
        f"Kỳ vọng {EXPECTED_EDGE_COUNT} edges, thực tế: {len(registry)}"
    )


def test_all_edges_polarity_integrity():
    """100% edges có polarity hợp lệ; polarity=0 phải có allowlist + reason."""
    registry = build_edge_registry()
    for edge_id, edge in registry.items():
        assert edge.polarity in (-1, 0, 1), (
            f"Edge {edge_id} có polarity không hợp lệ: {edge.polarity}"
        )
        if edge.polarity == 0:
            assert edge_id in REGIME_CONDITIONAL_ALLOWLIST, (
                f"Edge {edge_id} polarity=0 nhưng không nằm trong allowlist"
            )
            assert "regime_conditional" in edge.description.lower(), (
                f"Edge {edge_id} cần ghi rõ 'regime_conditional' trong description"
            )


def test_allowlist_members_still_zero():
    """Allowlist không mốc: member phải tồn tại và vẫn polarity=0."""
    registry = build_edge_registry()
    for edge_id in REGIME_CONDITIONAL_ALLOWLIST:
        assert edge_id in registry, f"Allowlist chứa edge không tồn tại: {edge_id}"
        assert registry[edge_id].polarity == 0, (
            f"Allowlist chứa edge đã có dấu: {edge_id}"
        )
