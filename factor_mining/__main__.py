"""Runnable synthetic smoke demo: python -m factor_mining demo."""
from __future__ import annotations

import argparse
import json

from . import (
    CallbackStrategy, EvaluationResult, FactorCandidate, MetricConstraint,
    MetricObjective, MiningEngine, MiningTask, RunConfig,
)


def demo() -> None:
    # Toy values only. No claim that this is a real market/backtest evaluator.
    scores = {"A": (0.03, 0.30), "B": (0.07, 0.80), "C": (0.05, 0.25)}
    labels = iter(scores)

    def propose(task, history):
        name = next(labels, None)
        return FactorCandidate(name, f"demo_formula_{name}") if name else None

    def evaluate(candidate, task):
        ic, overlap = scores[candidate.name]
        return EvaluationResult(metrics={"ic": ic, "overlap": overlap})

    engine = MiningEngine(
        evaluator=type("ToyEvaluator", (), {"evaluate": staticmethod(evaluate)})(),
        objective=MetricObjective(
            primary="ic",
            constraints=(MetricConstraint("overlap", maximum=0.60),),
        ),
        config=RunConfig(max_evaluations=10),
    )
    result = engine.run(MiningTask("synthetic-example"), CallbackStrategy(propose))
    print(json.dumps({
        "run_id": result.run_id,
        "tested": result.evaluation_count,
        "best": result.best.candidate.name if result.best else None,
        "stop_reason": result.stop_reason,
    }, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Generic factor mining core")
    parser.add_argument("command", choices=["demo"])
    args = parser.parse_args()
    if args.command == "demo":
        demo()


if __name__ == "__main__":
    main()
