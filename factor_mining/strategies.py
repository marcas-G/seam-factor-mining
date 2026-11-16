"""Minimal interchangeable proposal strategies."""
from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field

from .contracts import FactorCandidate, MiningTask, Trial


@dataclass
class ListStrategy:
    """Finite deterministic set: useful for manual hypotheses and smoke tests."""

    candidates: Iterable[FactorCandidate]
    _iterator: object = field(default=None, init=False, repr=False)

    def propose(self, task: MiningTask, history: Sequence[Trial]) -> FactorCandidate | None:
        if self._iterator is None:
            self._iterator = iter(self.candidates)
        return next(self._iterator, None)


@dataclass
class CallbackStrategy:
    """Connect an LLM agent, tree, genetic search, BO, or other research algorithm."""

    propose_fn: Callable[[MiningTask, Sequence[Trial]], FactorCandidate | None]

    def propose(self, task: MiningTask, history: Sequence[Trial]) -> FactorCandidate | None:
        return self.propose_fn(task, history)
