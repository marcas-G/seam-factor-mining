"""Algorithm-independent orchestration. Strategies and evaluators are injected."""
from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from .contracts import (
    EvaluationResult, FactorEvaluator, FactorObjective, MiningResult,
    MiningTask, ProposalStrategy, ScoreDecision, Trial, TrialRecorder,
)


@dataclass(frozen=True)
class RunConfig:
    max_evaluations: int = 20
    max_proposals: int = 100
    fail_fast: bool = False

    def __post_init__(self) -> None:
        if self.max_evaluations < 1 or self.max_proposals < 1:
            raise ValueError("budgets must be positive")


class MiningEngine:
    """Proposal → deduplicate → actual evaluation → objective → record.

    The engine has no market-data access, factor formula parser or LLM dependency.
    Duplicates are scoped to ONE run/task (never reused across periods/universes).
    """

    def __init__(
        self,
        evaluator: FactorEvaluator,
        objective: FactorObjective,
        config: RunConfig | None = None,
        recorder: TrialRecorder | None = None,
    ) -> None:
        self.evaluator = evaluator
        self.objective = objective
        self.config = config or RunConfig()
        self.recorder = recorder

    def run(self, task: MiningTask, strategy: ProposalStrategy) -> MiningResult:
        run_id = uuid4().hex
        trials: list[Trial] = []
        seen: dict[str, Trial] = {}
        best: Trial | None = None
        evaluations = 0
        stop_reason = "proposal_limit"

        for index in range(1, self.config.max_proposals + 1):
            if evaluations >= self.config.max_evaluations:
                stop_reason = "evaluation_budget"
                break

            # Feedback contains research-window trials only; no holdout results.
            candidate = strategy.propose(task, tuple(trials))
            if candidate is None:
                stop_reason = "strategy_exhausted"
                break
            if not isinstance(candidate, type_seen_candidate()):
                raise TypeError("strategy must return FactorCandidate or None")

            prior = seen.get(candidate.fingerprint)
            if prior is not None:
                trial = Trial(
                    index, candidate, prior.evaluation,
                    ScoreDecision(False, None, "duplicate in current task/run"),
                    duplicate=True,
                )
            else:
                try:
                    evaluation = self.evaluator.evaluate(candidate, task)
                    if not isinstance(evaluation, EvaluationResult):
                        raise TypeError("evaluator must return EvaluationResult")
                except Exception as exc:
                    if self.config.fail_fast:
                        raise
                    evaluation = EvaluationResult(
                        status="error", error=f"{type(exc).__name__}: {exc}"
                    )
                decision = self.objective.judge(evaluation)
                trial = Trial(index, candidate, evaluation, decision)
                seen[candidate.fingerprint] = trial
                evaluations += 1
                if decision.eligible and decision.score is not None:
                    if best is None or decision.score > best.decision.score:
                        best = trial
            trials.append(trial)
            if self.recorder is not None:
                self.recorder.write_trial(run_id, task, trial)

        return MiningResult(run_id, task, tuple(trials), best, evaluations, stop_reason)


def type_seen_candidate():
    # Local import keeps the runtime type check readable without tying the engine
    # to a particular strategy implementation.
    from .contracts import FactorCandidate
    return FactorCandidate


def evaluate_holdout(
    result: MiningResult,
    holdout_task: MiningTask,
    evaluator: FactorEvaluator,
) -> EvaluationResult | None:
    """One-way final check. No score is returned to the search strategy."""
    if result.best is None:
        return None
    return evaluator.evaluate(result.best.candidate, holdout_task)
