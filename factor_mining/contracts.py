"""Domain objects and replaceable component contracts. No market/platform assumptions."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence


@dataclass(frozen=True)
class FactorCandidate:
    """A candidate definition in any factor language (DSL, Python, C++, SQL, ...)."""

    name: str
    definition: str
    language: str = "expression"
    parameters: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.definition.strip():
            raise ValueError("factor name and definition must be non-empty")

    @property
    def fingerprint(self) -> str:
        """Content identity; a rename or descriptive metadata does not create a new factor."""
        payload = {
            "definition": self.definition,
            "language": self.language,
            "parameters": dict(self.parameters),
        }
        canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class MiningTask:
    """Research task. Dataset, frequency, universe, target and horizon live in context."""

    name: str
    context: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("task name must be non-empty")


@dataclass(frozen=True)
class EvaluationResult:
    """Actual platform result. Metrics are not assumed to be IC or Sharpe."""

    metrics: Mapping[str, float] = field(default_factory=dict)
    status: str = "ok"  # ok | error
    error: str | None = None
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in {"ok", "error"}:
            raise ValueError("status must be 'ok' or 'error'")


@dataclass(frozen=True)
class ScoreDecision:
    eligible: bool
    score: float | None
    reason: str


@dataclass(frozen=True)
class Trial:
    proposal_index: int
    candidate: FactorCandidate
    evaluation: EvaluationResult
    decision: ScoreDecision
    duplicate: bool = False


@dataclass(frozen=True)
class MiningResult:
    run_id: str
    task: MiningTask
    trials: tuple[Trial, ...]
    best: Trial | None
    evaluation_count: int
    stop_reason: str


class ProposalStrategy(Protocol):
    """Agent, hand-written list, tree search, BO, evolution, etc. can implement this."""

    def propose(self, task: MiningTask, history: Sequence[Trial]) -> FactorCandidate | None:
        ...


class FactorEvaluator(Protocol):
    """Adapter to a real factor backtest platform; responsible for causal data access."""

    def evaluate(self, candidate: FactorCandidate, task: MiningTask) -> EvaluationResult:
        ...


class FactorObjective(Protocol):
    def judge(self, evaluation: EvaluationResult) -> ScoreDecision:
        ...


class TrialRecorder(Protocol):
    def write_trial(self, run_id: str, task: MiningTask, trial: Trial) -> None:
        ...
