"""Factor validation policies; static checks are advisory, not leakage proofs."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol

from .contracts import FactorCandidate, MiningTask


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    issues: tuple[str, ...] = ()


class FactorValidator(Protocol):
    def validate(self, candidate: FactorCandidate, task: MiningTask) -> ValidationResult:
        ...


@dataclass(frozen=True)
class CompositeValidator:
    validators: tuple[FactorValidator, ...] = ()

    def validate(self, candidate: FactorCandidate, task: MiningTask) -> ValidationResult:
        issues: list[str] = []
        for validator in self.validators:
            result = validator.validate(candidate, task)
            if not result.valid:
                issues.extend(result.issues or ("validator rejected candidate",))
        return ValidationResult(not issues, tuple(issues))


@dataclass(frozen=True)
class LanguageAllowlist:
    languages: frozenset[str] = field(default_factory=lambda: frozenset({"expression"}))

    def validate(self, candidate: FactorCandidate, task: MiningTask) -> ValidationResult:
        if candidate.language not in self.languages:
            return ValidationResult(False, (f"language not allowed: {candidate.language}",))
        return ValidationResult(True)


@dataclass(frozen=True)
class ExpressionPolicy:
    """Optional simple DSL guard; NOT a parser, sandbox, or future-leakage detector.

    Configure banned operators to match the actual execution platform.
    """
    banned_identifiers: frozenset[str] = field(
        default_factory=lambda: frozenset({"future", "lead", "shift_negative"})
    )

    def validate(self, candidate: FactorCandidate, task: MiningTask) -> ValidationResult:
        if candidate.language != "expression":
            return ValidationResult(True)
        tokens = set(re.findall(r"[A-Za-z_][A-Za-z_0-9]*", candidate.definition.lower()))
        found = sorted(tokens & {s.lower() for s in self.banned_identifiers})
        if found:
            return ValidationResult(False, (f"disallowed identifiers: {', '.join(found)}",))
        return ValidationResult(True)
