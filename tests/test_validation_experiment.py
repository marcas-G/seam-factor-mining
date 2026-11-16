from factor_mining import (
    CallableEvaluator, EvaluationResult, FactorCandidate, ListStrategy,
    MetricObjective, MiningEngine, MiningTask,
)
from factor_mining.experiment import ExperimentCase, ExperimentRunner
from factor_mining.validation import CompositeValidator, ExpressionPolicy, LanguageAllowlist


def test_validation_rejection_skips_platform_and_budget():
    calls = []
    def evaluate(candidate, task):
        calls.append(candidate.name)
        return EvaluationResult(metrics={"ic": 0.1})
    engine = MiningEngine(
        CallableEvaluator(evaluate), MetricObjective("ic"),
        validator=CompositeValidator((LanguageAllowlist(), ExpressionPolicy())),
    )
    result = engine.run(MiningTask("research"), ListStrategy([
        FactorCandidate("leaky", "lead(close, 1)"),
        FactorCandidate("valid", "delay(close, 1)"),
    ]))
    assert calls == ["valid"]
    assert result.evaluation_count == 1
    assert not result.trials[0].decision.eligible
    assert result.trials[0].evaluation.status == "error"
    assert result.best.candidate.name == "valid"


def test_rejected_factor_is_deduplicated():
    engine = MiningEngine(
        CallableEvaluator(lambda c, t: (_ for _ in ()).throw(AssertionError("must not evaluate"))),
        MetricObjective("ic"),
        validator=ExpressionPolicy(),
    )
    result = engine.run(MiningTask("t"), ListStrategy([
        FactorCandidate("a", "future(close)"),
        FactorCandidate("b", "future(close)"),
    ]))
    assert result.evaluation_count == 0
    assert result.trials[1].duplicate


def test_experiment_isolates_strategy_and_holdout_feedback():
    calls = []
    def evaluate(candidate, task):
        calls.append(task.name)
        return EvaluationResult(metrics={"ic": 0.1 if task.name.startswith("train") else -0.5})
    seen_histories = []
    def factory():
        def propose(task, history):
            seen_histories.append((task.name, len(history)))
            return FactorCandidate("x", "delay(close, 1)") if not history else None
        from factor_mining import CallbackStrategy
        return CallbackStrategy(propose)

    runner = ExperimentRunner(MiningEngine(CallableEvaluator(evaluate), MetricObjective("ic")), factory)
    report = runner.run([
        ExperimentCase(MiningTask("train-1"), MiningTask("test-1")),
        ExperimentCase(MiningTask("train-2"), MiningTask("test-2")),
    ])
    assert len(report.outcomes) == 2
    assert calls == ["train-1", "test-1", "train-2", "test-2"]
    assert seen_histories == [("train-1", 0), ("train-1", 1),
                              ("train-2", 0), ("train-2", 1)]
    assert all(x.holdout.metrics["ic"] == -0.5 for x in report.outcomes)
