from collections import deque
from typing import Dict, Any, List, Optional, Set, Tuple
from .models import SignalProvenanceNode, EpistemicState
from .graph import SignalEdge, SignalProvenanceGraph


class TemporalCausalityViolation(ValueError):
    pass


class CycleDetected(ValueError):
    pass


class NodeNotFound(ValueError):
    pass


class ProvenanceRegistry:
    def __init__(self):
        self.graph = SignalProvenanceGraph(graph_id="global_market_state")

        # Internal maps for O(1) lookups (avoid linear scans over graph.edges)
        self._forward_index: Dict[str, List[str]] = {}
        self._reverse_index: Dict[str, List[str]] = {}
        self._node_map: Dict[str, SignalProvenanceNode] = {}

    # ───────────────────────────────
    #  Node lifecycle
    # ───────────────────────────────

    def register_node(self, node: SignalProvenanceNode):
        if node.node_id in self._node_map:
            raise ValueError(f"Node ID '{node.node_id}' already registered.")

        self._node_map[node.node_id] = node
        self.graph.nodes[node.node_id] = node
        self._forward_index.setdefault(node.node_id, [])
        self._reverse_index.setdefault(node.node_id, [])

    def get_node(self, node_id: str) -> Optional[SignalProvenanceNode]:
        return self._node_map.get(node_id)

    # ───────────────────────────────
    #  Edge lifecycle + DAG enforcement
    # ───────────────────────────────

    def add_edge(self, from_id: str, to_id: str, weight: float,
                 edge_type: str = "DERIVATION",
                 attenuation: Optional[dict] = None) -> SignalEdge:
        if from_id not in self._node_map:
            raise NodeNotFound(f"Source node '{from_id}' not found.")
        if to_id not in self._node_map:
            raise NodeNotFound(f"Target node '{to_id}' not found.")

        parent = self._node_map[from_id]
        child = self._node_map[to_id]

        # Temporal causality: parent must exist before child
        if parent.timestamp > child.timestamp:
            raise TemporalCausalityViolation(
                f"Parent '{from_id}' (t={parent.timestamp}) is newer "
                f"than child '{to_id}' (t={child.timestamp})."
            )

        edge = SignalEdge(
            from_node=from_id,
            to_node=to_id,
            type=edge_type,
            weight=weight,
            attenuation=attenuation or {"noise_gain": 0.0, "information_loss": 0.0},
        )

        # Tentatively add to index, then check for cycle
        self._forward_index.setdefault(from_id, []).append(to_id)
        self._reverse_index.setdefault(to_id, []).append(from_id)

        if self._has_cycle():
            # Rollback index mutation
            self._forward_index[from_id].remove(to_id)
            self._reverse_index[to_id].remove(from_id)
            raise CycleDetected(
                f"Adding edge '{from_id}' -> '{to_id}' would create a cycle."
            )

        self.graph.edges.append(edge)
        return edge

    def _has_cycle(self) -> bool:
        WHITE, GRAY, BLACK = 0, 1, 2
        color: Dict[str, int] = {nid: WHITE for nid in self._node_map}

        def dfs(nid: str) -> bool:
            color[nid] = GRAY
            for neighbor in self._forward_index.get(nid, []):
                if color.get(neighbor) == GRAY:
                    return True
                if color.get(neighbor) == WHITE and dfs(neighbor):
                    return True
            color[nid] = BLACK
            return False

        return any(dfs(nid) for nid in self._node_map if color[nid] == WHITE)

    def detect_cycles(self) -> List[List[str]]:
        """Returns all elementary cycles (back edges found during DFS)."""
        WHITE, GRAY, BLACK = 0, 1, 2
        color: Dict[str, int] = {nid: WHITE for nid in self._node_map}
        cycles = []
        path_stack = []

        def dfs(nid: str):
            color[nid] = GRAY
            path_stack.append(nid)
            for neighbor in self._forward_index.get(nid, []):
                if color.get(neighbor) == GRAY:
                    cycle_start = path_stack.index(neighbor)
                    cycles.append(path_stack[cycle_start:] + [neighbor])
                elif color.get(neighbor) == WHITE:
                    dfs(neighbor)
            path_stack.pop()
            color[nid] = BLACK

        for nid in self._node_map:
            if color[nid] == WHITE:
                dfs(nid)
        return cycles

    # ───────────────────────────────
    #  Graph traversal
    # ───────────────────────────────

    def get_parents(self, node_id: str) -> List[SignalProvenanceNode]:
        return [self._node_map[pid] for pid in self._reverse_index.get(node_id, [])
                if pid in self._node_map]

    def get_children(self, node_id: str) -> List[SignalProvenanceNode]:
        return [self._node_map[cid] for cid in self._forward_index.get(node_id, [])
                if cid in self._node_map]

    def find_root_cause(self, node_id: str) -> List[str]:
        """Traverse reverse index to find all root (RAW) ancestors."""
        if node_id not in self._node_map:
            return []

        visited: Set[str] = set()
        roots: List[str] = []
        queue = deque([node_id])

        while queue:
            nid = queue.popleft()
            if nid in visited:
                continue
            visited.add(nid)

            parents = self._reverse_index.get(nid, [])
            if not parents:
                roots.append(nid)
            else:
                queue.extend(pid for pid in parents if pid not in visited)

        return roots

    # ───────────────────────────────
    #  Attenuation propagation (entropy engine foundation)
    # ───────────────────────────────

    def compute_effective_confidence(self, node_id: str) -> float:
        """
        Propagate confidence through the causality chain.

        effective_confidence = base_confidence * Π(1 - noise_gain)
        for every edge on the path from the nearest root.
        """
        node = self._node_map.get(node_id)
        if node is None:
            return 0.0

        base = node.confidence_hint
        total_attenuation = 1.0

        queue = deque([node_id])
        visited: Set[str] = set()

        while queue:
            nid = queue.popleft()
            if nid in visited:
                continue
            visited.add(nid)

            parents = self._reverse_index.get(nid, [])
            for pid in parents:
                edge = self._find_edge(pid, nid)
                if edge is not None:
                    ng = edge.attenuation.get("noise_gain", 0.0)
                    total_attenuation *= (1.0 - ng)
                queue.append(pid)

        return base * total_attenuation

    def _find_edge(self, from_id: str, to_id: str) -> Optional[SignalEdge]:
        for e in self.graph.edges:
            if e.from_node == from_id and e.to_node == to_id:
                return e
        return None

    # ───────────────────────────────
    #  Node expiry
    # ───────────────────────────────

    def purge_expired(self, reference_time) -> List[str]:
        """Remove nodes whose ttl_seconds has elapsed. Returns purged node IDs."""
        purged = []
        for nid, node in list(self._node_map.items()):
            if node.ttl_seconds is not None:
                age = (reference_time - node.timestamp).total_seconds()
                if age > node.ttl_seconds:
                    purged.append(nid)
        for nid in purged:
            self._remove_node(nid)
        return purged

    def _remove_node(self, node_id: str):
        self._node_map.pop(node_id, None)
        self.graph.nodes.pop(node_id, None)
        # Remove from indices
        for children in self._forward_index.get(node_id, []):
            rev_list = self._reverse_index.get(children, [])
            if node_id in rev_list:
                rev_list.remove(node_id)
        for parents in self._reverse_index.get(node_id, []):
            fwd_list = self._forward_index.get(parents, [])
            if node_id in fwd_list:
                fwd_list.remove(node_id)
        self._forward_index.pop(node_id, None)
        self._reverse_index.pop(node_id, None)
        # Remove graph edges
        self.graph.edges = [
            e for e in self.graph.edges
            if e.from_node != node_id and e.to_node != node_id
        ]

    # ───────────────────────────────
    #  Export
    # ───────────────────────────────

    def export(self) -> Dict[str, Any]:
        return self.graph.model_dump(mode="json")