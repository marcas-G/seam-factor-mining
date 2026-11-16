"""Contract tests for the public generic core. No external data or trading platform."""
from __future__ import annotations

import json

import pytest

from factor_mining import (
    CallbackStrategy, CallableEvaluator, EvaluationResult, FactorCandidate,
    JsonlRecorder, ListStrategy, MetricConstraint, MetricObjective,
    MiningEngine, MiningTask, PipelineEvaluator, RunConfig, evaluate_holdout,
)


def candidate(name: str, formula: str | None = None):
    return FactorCandidate(name=name, definition=formula or name)


def evaluator_with_scores(scores, calls=None):
    def evaluate(factor, task):
        if calls is not None:
            calls.append((factor.name, task.name))
        value = scores[factor.name]
        return EvaluationResult(metrics={"ic": value, "turnover": 0.2})
    return CallableEvaluator(evaluate)


def test_best_factor_is_selected_by_configured_metric():
    engine = MiningEngine(evaluator_with_scores({"A": 0.01, "B": 0.04, "C": -0.03}),
                          MetricObjective("ic"))
    result = engine.run(MiningTask("returns-5m"), ListStrategy([
        candidate("A"), candidate("B"), candidate("C"),
    ]))
    assert result.best.candidate.name == "B"
    assert result.evaluation_count == 3
    assert result.stop_reason == "strategy_exhausted"


def test_metric_minimization_and_constraints():
    values = {"A": (0.05, 0.3), "B": (0.01, 0.8), "C": (0.03, 0.2)}
    evaluator = CallableEvaluator(lambda c, t: EvaluationResult(
        metrics={"ic": values[c.name][0], "turnover": values[c.name][1]}
    ))
    objective = MetricObjective("turnover", maximize=False,
                               constraints=(MetricConstraint("ic", minimum=0.02),))
    result = MiningEngine(evaluator, objective).run(
        MiningTask("task"), ListStrategy([candidate(n) for n in values])
    )
    assert result.best.candidate.name == "C"
    assert not result.trials[1].decision.eligible


def test_content_deduplication_in_same_task():
    calls = []
    engine = MiningEngine(evaluator_with_scores({"A": 0.1}, calls),
                          MetricObjective("ic"))
    result = engine.run(MiningTask("t"), ListStrategy([
        candidate("A", "same"), candidate("Alias", "same"),
    ]))
    assert result.evaluation_count == 1
    assert len(calls) == 1
    assert len(result.trials) == 2
    assert result.trials[1].duplicate
    assert result.trials[1].decision.score is None


def test_duplicate_cache_does_not_cross_tasks():
    calls = []
    engine = MiningEngine(evaluator_with_scores({"A": 0.1}, calls), MetricObjective("ic"))
    for name in ("period-1", "period-2"):
        engine.run(MiningTask(name), ListStrategy([candidate("A", "same")]))
    assert [name for _, name in calls] == ["period-1", "period-2"]


def test_bad_evaluator_is_recorded_as_failure():
    engine = MiningEngine(CallableEvaluator(lambda c, t: 1 / 0),
                          MetricObjective("ic"))
    result = engine.run(MiningTask("t"), ListStrategy([candidate("A")]))
    assert result.best is None
    assert result.trials[0].evaluation.status == "error"
    assert "ZeroDivisionError" in result.trials[0].evaluation.error


def test_fail_fast_option():
    engine = MiningEngine(CallableEvaluator(lambda c, t: 1 / 0),
                          MetricObjective("ic"), config=RunConfig(fail_fast=True))
    with pytest.raises(ZeroDivisionError):
        engine.run(MiningTask("t"), ListStrategy([candidate("A")]))


def test_proposal_limit_prevents_duplicate_infinite_loop():
    calls = []
    engine = MiningEngine(evaluator_with_scores({"A": 0.1}, calls),
                          MetricObjective("ic"), RunConfig(max_evaluations=10, max_proposals=4))
    result = engine.run(MiningTask("t"),
                        CallbackStrategy(lambda task, history: candidate("A")))
    assert result.stop_reason == "proposal_limit"
    assert result.evaluation_count == 1
    assert len(result.trials) == 4


def test_budget_limit():
    engine = MiningEngine(evaluator_with_scores({"A": 1, "B": 2}),
                          MetricObjective("ic"), RunConfig(max_evaluations=1))
    result = engine.run(MiningTask("t"), ListStrategy([candidate("A"), candidate("B")]))
    assert result.evaluation_count == 1
    assert result.stop_reason == "evaluation_budget"


def test_missing_and_non_finite_metric_are_not_eligible():
    obj = MetricObjective("ic")
    assert not obj.judge(EvaluationResult(metrics={})).eligible
    assert not obj.judge(EvaluationResult(metrics={"ic": float("nan")})).eligible


def test_jsonl_recorder_keeps_auditable_history(tmp_path):
    path = tmp_path / "logs" / "runs.jsonl"
    result = MiningEngine(
        evaluator_with_scores({"A": 0.01}), MetricObjective("ic"),
        recorder=JsonlRecorder(path),
    ).run(MiningTask("t", {"horizon": 5}), ListStrategy([candidate("A")]))
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["run_id"] == result.run_id
    assert rows[0]["fingerprint"] == candidate("A").fingerprint
    assert rows[0]["trial"]["decision"]["eligible"]


def test_feedback_contains_previous_trials_but_not_holdout():
    snapshots = []
    labels = iter(("A", "B"))
    def propose(task, history):
        snapshots.append(len(history))
        value = next(labels, None)
        return candidate(value) if value else None

    calls = []
    engine = MiningEngine(evaluator_with_scores({"A": 0.1, "B": 0.2}, calls),
                          MetricObjective("ic"))
    result = engine.run(MiningTask("research"), CallbackStrategy(propose))
    assert snapshots == [0, 1, 2]
    checked = evaluate_holdout(
        result, MiningTask("held-out"),
        evaluator_with_scores({"B": -0.4}, calls),
    )
    assert checked.metrics["ic"] == -0.4
    assert calls[-1] == ("B", "held-out")
    assert all(name == "research" for _, name in calls[:-1])


def test_separated_data_execution_metrics_pipeline():
    calls = []
    class Data:
        def load(self, task):
            calls.append("data")
            return [1, 2, 3]
    class Execute:
        def execute(self, candidate, data, task):
            calls.append("execute")
            return sum(data)
    class Metrics:
        def calculate(self, signal, data, task):
            calls.append("metrics")
            return {"ic": signal / 10}
    pipeline = PipelineEvaluator(Data(), Execute(), Metrics())
    result = MiningEngine(pipeline, MetricObjective("ic")).run(
        MiningTask("t"), ListStrategy([candidate("A")])
    )
    assert result.best.decision.score == 0.6
    assert calls == ["data", "execute", "metrics"]
