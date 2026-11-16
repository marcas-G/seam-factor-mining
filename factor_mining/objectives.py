"""Business-configured objective and metric constraints; no privileged factor score."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .contracts import EvaluationResult, ScoreDecision


@dataclass(frozen=True)
class MetricConstraint:
    metric: str
    minimum: float | None = None
    maximum: float | None = None

    def __post_init__(self) -> None:
        if self.minimum is None and self.maximum is None:
            raise ValueError("constraint requires at least one bound")
        if self.minimum is not None and self.maximum is not None:
            if self.minimum > self.maximum:
                raise ValueError("minimum cannot exceed maximum")


@dataclass(frozen=True)
class MetricObjective:
    """Rank by one metric after checking constraints. Negative sign for minimization."""

    primary: str
    maximize: bool = True
    constraints: tuple[MetricConstraint, ...] = field(default_factory=tuple)

    def judge(self, evaluation: EvaluationResult) -> ScoreDecision:
        if evaluation.status != "ok":
            return ScoreDecision(False, None, evaluation.error or "evaluation failed")
        required = (self.primary,) + tuple(c.metric for c in self.constraints)
        values: dict[str, float] = {}
        for metric in required:
            try:
                value = float(evaluation.metrics[metric])
            except (KeyError, TypeError, ValueError):
                return ScoreDecision(False, None, f"missing or invalid metric: {metric}")
            if not math.isfinite(value):
                return ScoreDecision(False, None, f"non-finite metric: {metric}")
            values[metric] = value

        for constraint in self.constraints:
            value = values[constraint.metric]
            if constraint.minimum is not None and value < constraint.minimum:
                return ScoreDecision(False, None, f"{constraint.metric} below minimum")
            if constraint.maximum is not None and value > constraint.maximum:
                return ScoreDecision(False, None, f"{constraint.metric} above maximum")
        primary = values[self.primary]
        return ScoreDecision(True, primary if self.maximize else -primary, "eligible")
