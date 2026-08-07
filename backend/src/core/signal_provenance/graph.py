from pydantic import BaseModel, Field

from .models import SignalProvenanceNode


class SignalEdge(BaseModel):
    from_node: str = Field(description="Source node ID.")
    to_node: str = Field(description="Target node ID.")

    type: str = Field(description="Type of connection: DERIVATION | TRANSFORMATION | AGGREGATION")

    weight: float = Field(..., ge=0.0, le=1.0, description="Trust propagation coefficient (0.0 to 1.0).")

    attenuation: dict = Field(
        default_factory=lambda: {"noise_gain": 0.0, "information_loss": 0.0},
        description="Model for information decay along the edge.",
    )


class SignalProvenanceGraph(BaseModel):
    graph_id: str = Field(description="Unique ID for the entire market state graph.")

    nodes: dict[str, SignalProvenanceNode] = Field(
        default_factory=dict,
        description="Map of node IDs to SignalProvenanceNode objects.",
    )
    edges: list[SignalEdge] = Field(default_factory=list, description="List of edges connecting nodes.")

    root_signals: list[str] = Field(default_factory=list, description="IDs of initial, raw signals in the graph.")

    global_context: dict = Field(default_factory=dict)
