"""CAO Readiness Gate — Data models for readiness report"""
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


def _hydrate_path():
    if getattr(sys, 'frozen', False):
        root_path = Path(sys.executable).resolve().parent
    else:
        current = Path(__file__).resolve().parent
        root_path = current
        while current != current.parent:
            if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
                root_path = current
                break
            current = current.parent
    if str(root_path) not in sys.path:
        sys.path.insert(0, str(root_path))
    return root_path

PROJECT_ROOT = _hydrate_path()


@dataclass
class GateResult:
    gate_name: str
    status: str  # PASS | FAIL | WARN
    score: float
    threshold: float
    message: str
    details: dict = field(default_factory=dict)


@dataclass
class EngineCorrelation:
    engine_a: str
    engine_b: str
    correlation: float
    flagged: bool


@dataclass
class IndependenceReport:
    passed: bool
    correlation_matrix: dict
    flagged_pairs: list[EngineCorrelation]
    max_correlation: float
    condition_number: Optional[float]
    verdict: str


@dataclass
class CounterfactualResult:
    engine: str
    baseline_action: str
    baseline_confidence: float
    removed_action: str
    removed_confidence: float
    action_changed: bool
    confidence_delta: float
    injectable: bool


@dataclass
class InjectabilityReport:
    passed: bool
    results: list[CounterfactualResult]
    decorative_engines: list[str]
    avg_confidence_delta: float
    verdict: str


@dataclass
class RegimeEntropyPoint:
    date: str
    regime: str
    regime_score: float
    entropy: float


@dataclass
class RegimeStabilityReport:
    passed: bool
    entropy_series: list[RegimeEntropyPoint]
    current_entropy: float
    max_entropy: float
    stability_index: float
    regime_transition_count: int
    window_days: int
    verdict: str


@dataclass
class ReadinessVerdict:
    timestamp: str
    overall_pass: bool
    independent: IndependenceReport
    injectable: InjectabilityReport
    stable: RegimeStabilityReport
    gates: list[GateResult]
    summary: str

    @property
    def pass_count(self) -> int:
        return sum(1 for g in self.gates if g.status == "PASS")

    @property
    def fail_count(self) -> int:
        return sum(1 for g in self.gates if g.status == "FAIL")
