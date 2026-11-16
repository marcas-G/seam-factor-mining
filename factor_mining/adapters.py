"""Adapters for a callable platform or a decomposed data/compute/metrics pipeline."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, Mapping, Protocol

from .contracts import EvaluationResult, FactorCandidate, MiningTask


class DataProvider(Protocol):
    def load(self, task: MiningTask) -> Any:
        ...


class FactorExecutor(Protocol):
    def execute(self, candidate: FactorCandidate, data: Any, task: MiningTask) -> Any:
        ...


class MetricCalculator(Protocol):
    def calculate(self, signal: Any, data: Any, task: MiningTask) -> Mapping[str, float]:
        ...


class CallableEvaluator:
    """Integrate an existing factor platform with a single adapter function."""

    def __init__(self, fn: Callable[[FactorCandidate, MiningTask], EvaluationResult]):
        self.fn = fn

    def evaluate(self, candidate: FactorCandidate, task: MiningTask) -> EvaluationResult:
        return self.fn(candidate, task)


class PipelineEvaluator:
    """Explicit source → factor computation → metrics composition."""

    def __init__(
        self,
        provider: DataProvider,
        executor: FactorExecutor,
        calculator: MetricCalculator,
    ):
        self.provider = provider
        self.executor = executor
        self.calculator = calculator

    def evaluate(self, candidate: FactorCandidate, task: MiningTask) -> EvaluationResult:
        data = self.provider.load(task)
        signal = self.executor.execute(candidate, data, task)
        metrics = self.calculator.calculate(signal, data, task)
        return EvaluationResult(metrics=dict(metrics))
