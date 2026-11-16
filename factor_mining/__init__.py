"""Generic, platform-neutral factor mining core."""
from .adapters import CallableEvaluator, PipelineEvaluator
from .contracts import (
    EvaluationResult, FactorCandidate, FactorEvaluator, FactorObjective,
    MiningResult, MiningTask, ProposalStrategy, ScoreDecision, Trial,
)
from .engine import MiningEngine, RunConfig, evaluate_holdout
from .objectives import MetricConstraint, MetricObjective
from .storage import JsonlRecorder
from .strategies import CallbackStrategy, ListStrategy

__all__ = [
    "FactorCandidate", "MiningTask", "EvaluationResult", "ScoreDecision",
    "Trial", "MiningResult", "FactorEvaluator", "FactorObjective",
    "ProposalStrategy", "MiningEngine", "RunConfig", "evaluate_holdout",
    "MetricConstraint", "MetricObjective", "ListStrategy", "CallbackStrategy",
    "CallableEvaluator", "PipelineEvaluator", "JsonlRecorder",
]
