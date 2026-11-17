"""Readable, independent reference oracles for the scoring contract.

This module never imports ``assessment_score_equating``.  It restates the
documented calculation in small, named stages so a disagreement points at a
stage instead of at a wall of output.  Tests may safely import anything here.

Contract restated from ``README.md``:

* the parser accepts a ``responses`` list and extracts mapping rows in order;
* void items are removed *before* any counting, so they can only shrink the
  scoreable set;
* the grouping key is exactly ``(student, form)``;
* the scale factor is selected by the record's own ``form`` only;
* a missing form scale defaults to 10;
* report rows are stably ordered by ``(student, form)`` and rounded once to
  two decimal places, ties to even, on the binary value actually computed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_EVEN, localcontext
from typing import Any, Iterable

PROJECT = "assessment-score-equating"
STATUS = "ok"
DEFAULT_SCALE = 10
REQUIRED_FIELDS = ("student", "form", "item", "answer")


class InvalidPayload(ValueError):
    """Payload cannot be interpreted as a batch of responses."""


class InvalidRecord(ValueError):
    """A mapping row is missing one of the four required fields."""

    def __init__(self, index: int, row: dict, missing: tuple[str, ...]):
        self.index = index
        self.row = row
        self.missing = missing
        super().__init__(f"response[{index}] is missing fields {missing}: {row!r}")


@dataclass(frozen=True)
class Response:
    """One raw response row, retaining its position for diagnostics."""

    index: int
    student: Any
    form: Any
    item: Any
    answer: Any
    raw: dict = field(compare=False, repr=False)


@dataclass(frozen=True)
class Bucket:
    """Scoreable activity for one (student, form) candidate."""

    correct: int = 0
    attempted: int = 0


@dataclass(frozen=True)
class ScoreRecord:
    student: Any
    form: Any
    raw: int
    scale: float | int
    attempted: int

    def as_dict(self) -> dict:
        return {
            "student": self.student,
            "form": self.form,
            "raw": self.raw,
            "scale": self.scale,
            "attempted": self.attempted,
        }


def response_rows(payload: dict) -> list:
    """Stage 0: the raw list, validated only for container type."""

    if not isinstance(payload, dict):
        raise InvalidPayload("payload must be a mapping")
    rows = payload.get("responses", [])
    if not isinstance(rows, list):
        raise InvalidPayload("responses must be a list")
    return rows


def extract_responses(payload: dict) -> list[Response]:
    """Stage 1: keep mapping rows in input order; drop non-mapping rows.

    Mapping rows are validated: a row that cannot supply student/form/item/
    answer must fail closed rather than contribute a fabricated score.
    """

    responses: list[Response] = []
    for index, row in enumerate(response_rows(payload)):
        if not isinstance(row, dict):
            continue
        missing = tuple(name for name in REQUIRED_FIELDS if name not in row)
        if missing:
            raise InvalidRecord(index, row, missing)
        responses.append(
            Response(
                index=index,
                student=row["student"],
                form=row["form"],
                item=row["item"],
                answer=row["answer"],
                raw=row,
            )
        )
    return responses


def invalid_records(payload: dict) -> list[tuple[int, dict, tuple[str, ...]]]:
    """Rows that a strict reader must reject, with their source positions."""

    rejected = []
    for index, row in enumerate(response_rows(payload)):
        if not isinstance(row, dict):
            continue
        missing = tuple(name for name in REQUIRED_FIELDS if name not in row)
        if missing:
            rejected.append((index, row, missing))
    return rejected


def void_items(payload: dict) -> tuple[Any, ...]:
    """Stage 2: the void set is unique and sorted, regardless of input order."""

    items = payload.get("void_items", [])
    return tuple(sorted(set(items)))


def keys(payload: dict) -> dict[Any, Any]:
    return dict(payload.get("keys", {}))


def scale_table(payload: dict) -> dict[Any, Any]:
    return dict(payload.get("scale", {}))


def scoreable(responses: Iterable[Response], void: Iterable[Any]) -> list[Response]:
    """Stage 3: void items are removed before any counting happens."""

    void_set = set(void)
    return [response for response in responses if response.item not in void_set]


def group(
    responses: Iterable[Response],
    answer_keys: dict[Any, Any],
) -> dict[tuple[Any, Any], Bucket]:
    """Stages 4 and 5: group by (student, form) and count scoreable attempts.

    Attempts and correct answers are counted in the same pass; membership in
    the grouping key is the only route into a report row.
    """

    buckets: dict[tuple[Any, Any], Bucket] = {}
    for response in responses:
        key = (response.student, response.form)
        bucket = buckets.get(key, Bucket())
        buckets[key] = Bucket(
            correct=bucket.correct + int(response.answer == answer_keys.get(response.item)),
            attempted=bucket.attempted + 1,
        )
    return buckets


def round_half_even(value: float | int, places: int = 2) -> float | int:
    """Reproduce Python's ``round`` on the exact binary value.

    ``Decimal.from_float`` exposes the value the interpreter actually holds,
    so half-even ties are resolved without re-parsing a decimal literal.
    """

    if isinstance(value, int):
        return value
    with localcontext() as context:
        context.prec = 80
        quantized = Decimal.from_float(float(value)).quantize(
            Decimal(1).scaleb(-places), rounding=ROUND_HALF_EVEN
        )
    return float(quantized)


def scale_scores(
    buckets: dict[tuple[Any, Any], Bucket],
    scale_by_form: dict[Any, Any],
) -> list[ScoreRecord]:
    """Stage 6: the factor is selected by the record's own form only."""

    records = [
        ScoreRecord(
            student=student,
            form=form,
            raw=bucket.correct,
            scale=round_half_even(bucket.correct * scale_by_form.get(form, DEFAULT_SCALE)),
            attempted=bucket.attempted,
        )
        for (student, form), bucket in buckets.items()
    ]
    return sort_scores(records)


def sort_scores(records: Iterable[ScoreRecord]) -> list[ScoreRecord]:
    """Stage 7: stable report order is (student, form)."""

    return sorted(records, key=lambda record: (record.student, record.form))


def parse(payload: dict) -> list[dict]:
    """Parser oracle: the extracted records, copied out of the payload."""

    return [dict(response.raw) for response in extract_responses(payload)]


def calculate(payload: dict) -> dict:
    """Reference implementation of the domain calculation."""

    responses = extract_responses(payload)
    void = void_items(payload)
    counted = scoreable(responses, void)
    buckets = group(counted, keys(payload))
    records = scale_scores(buckets, scale_table(payload))
    return {
        "scores": [record.as_dict() for record in records],
        "void_items": list(void),
    }


def envelope(data: dict, project: str = PROJECT) -> dict:
    """Report oracle: the flat output envelope wrapping the engine data."""

    return {"project": project, "status": STATUS, **data}


def run(payload: dict) -> dict:
    """Reference implementation of the full pipeline envelope."""

    return envelope(calculate(payload))


def score_map(result: dict) -> dict[tuple[Any, Any], dict]:
    return {(row["student"], row["form"]): row for row in result.get("scores", [])}


def bucket_key(row: dict) -> tuple[Any, Any]:
    return (row["student"], row["form"])


def is_structurally_valid(row: Any) -> bool:
    return isinstance(row, dict) and all(name in row for name in REQUIRED_FIELDS)
