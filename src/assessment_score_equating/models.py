"""Immutable result types for the scoring pipeline."""

from dataclasses import dataclass
from typing import Any, Dict, Tuple


@dataclass(frozen=True)
class ScoreEntry:
    """One (student, form) score row.

    ``scale_hundredths`` is the canonical exact scale value.  ``scale`` is a
    JSON convenience float derived from the integer so that its repr is stable
    across Python implementations.
    """

    student: str
    form: str
    raw: int
    scale_hundredths: int
    attempted: int
    absent: int
    violations: int

    def as_dict(self) -> Dict[str, Any]:
        try:
            scale = self.scale_hundredths / 100.0
        except OverflowError as exc:  # pragma: no cover - absurd scale values
            raise ValueError("scale result out of range") from exc
        return {
            "student": self.student,
            "form": self.form,
            "raw": self.raw,
            "scale": scale,
            "scale_hundredths": self.scale_hundredths,
            "attempted": self.attempted,
            "absent": self.absent,
            "violations": self.violations,
        }


@dataclass(frozen=True)
class PipelineResult:
    project: str
    status: str
    contract_version: int
    rounding: str
    scores: Tuple[ScoreEntry, ...]
    void_items: Tuple[str, ...]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "project": self.project,
            "status": self.status,
            "contract_version": self.contract_version,
            "rounding": self.rounding,
            "scores": [entry.as_dict() for entry in self.scores],
            "void_items": list(self.void_items),
        }
