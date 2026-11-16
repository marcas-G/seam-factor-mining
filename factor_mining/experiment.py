"""Multi-task research/holdout experiment orchestration with isolated feedback."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .contracts import EvaluationResult, MiningResult, MiningTask, ProposalStrategy
from .engine import MiningEngine


@dataclass(frozen=True)
class ExperimentCase:
    research: MiningTask
    holdout: MiningTask | None = None


@dataclass(frozen=True)
class ExperimentOutcome:
    case: ExperimentCase
    research: MiningResult
    holdout: EvaluationResult | None


@dataclass(frozen=True)
class ExperimentReport:
    outcomes: tuple[ExperimentOutcome, ...]


class ExperimentRunner:
    """Build a FRESH strategy per case; never share strategy state across tasks.

    Holdout is evaluated only after selection and never exposed in proposal history.
    A true sealed evaluation requires external permission/data isolation.
    """

    def __init__(self, engine: MiningEngine, strategy_factory: Callable[[], ProposalStrategy]):
        self.engine = engine
        self.strategy_factory = strategy_factory

    def run(self, cases: list[ExperimentCase]) -> ExperimentReport:
        outcomes: list[ExperimentOutcome] = []
        for case in cases:
            strategy = self.strategy_factory()
            result = self.engine.run(case.research, strategy)
            holdout = None
            if case.holdout is not None and result.best is not None:
                holdout = self.engine.evaluator.evaluate(result.best.candidate, case.holdout)
            outcomes.append(ExperimentOutcome(case, result, holdout))
        return ExperimentReport(tuple(outcomes))
