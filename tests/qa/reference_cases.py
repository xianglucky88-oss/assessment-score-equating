"""Hand-computed reference cases.

Each case records the expected report literally, plus why the values are what
they are.  These are the readable oracle: if the implementation and the
reference disagree, the reference is small enough to audit by hand.  No
expected value here is produced by calling the implementation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import generators


@dataclass(frozen=True)
class ReferenceCase:
    name: str
    payload: dict
    expected_scores: list[dict]
    expected_void: list[Any]
    rationale: str
    expected_error: type[BaseException] | None = None


def _row(student, form, raw, scale, attempted) -> dict:
    return {
        "student": student,
        "form": form,
        "raw": raw,
        "scale": scale,
        "attempted": attempted,
    }


def cases() -> dict[str, ReferenceCase]:
    payloads = generators.combination_fixtures()

    built = {
        "minimal": ReferenceCase(
            name="minimal",
            payload=payloads["minimal"],
            expected_scores=[
                _row("S1", "F1", 1, 10, 1),
                _row("S2", "F2", 0, 0, 1),
            ],
            expected_void=["Q2"],
            rationale=(
                "S1/F1: Q1 correct, Q2 void -> 1 attempt, 1 raw, x10. "
                "S2/F2: Q1 wrong (key A, answer C) -> 1 attempt, 0 raw."
            ),
        ),
        "empty": ReferenceCase(
            name="empty",
            payload=payloads["empty"],
            expected_scores=[],
            expected_void=[],
            rationale="An empty batch yields no candidate rows at all, not a zero row.",
        ),
        "absent_student": ReferenceCase(
            name="absent_student",
            payload=payloads["absent_student"],
            expected_scores=[_row("S1", "F1", 1, 10, 2)],
            expected_void=[],
            rationale=(
                "Only students present in the input are reported. Attempted counts both "
                "non-void answers; only Q1 matches the key."
            ),
        ),
        "fully_voided": ReferenceCase(
            name="fully_voided",
            payload=payloads["fully_voided"],
            expected_scores=[],
            expected_void=["Q1", "Q2"],
            rationale=(
                "When every response is void the scoreable set is empty; no candidate "
                "row may be fabricated with raw=0."
            ),
        ),
        "duplicate_import": ReferenceCase(
            name="duplicate_import",
            payload=payloads["duplicate_import"],
            expected_scores=[_row("S1", "F1", 3, 7.5, 4)],
            expected_void=[],
            rationale=(
                "The contract counts every imported row, including the duplicate and the "
                "revised answer: 3 correct of 4 attempts, x2.5."
            ),
        ),
        "item_substitution": ReferenceCase(
            name="item_substitution",
            payload=payloads["item_substitution"],
            expected_scores=[
                _row("S1", "F1", 2, 20, 2),
                _row("S2", "F2", 0, 0, 1),
            ],
            expected_void=["Q2"],
            rationale=(
                "Q2 is void and must not count; substitute Q3 is scored on its own key. "
                "S1/F1: Q1 and Q3 correct -> 2. S2/F2: Q3 wrong -> 0."
            ),
        ),
        "review_override": ReferenceCase(
            name="review_override",
            payload=payloads["review_override"],
            expected_scores=[
                _row("S1", "F1", 2, 2.01, 3),
                _row("S2", "F2", 0, 0, 1),
            ],
            expected_void=[],
            rationale=(
                "Both Q1 rows are counted (original wrong, review correct); the corrected "
                "answer adds one raw point. 2 x 1.005 = 2.01 exactly at two places."
            ),
        ),
        "rounding_threshold": ReferenceCase(
            name="rounding_threshold",
            payload=payloads["rounding_threshold"],
            expected_scores=[
                _row("S1", "F1", 3, 0.38, 3),
                _row("S2", "F2", 3, 3.01, 3),
                _row("S3", "F3", 3, 7.04, 3),
            ],
            expected_void=[],
            rationale=(
                "raw=3 at tie-prone factors: 3*0.125=0.375 -> 0.38, "
                "3*1.005=3.0149999999999997 -> 3.01, 3*2.345=7.035 -> 7.04. "
                "Rounding is half-even once, at the report boundary."
            ),
        ),
        "missing_form_scale": ReferenceCase(
            name="missing_form_scale",
            payload=payloads["missing_form_scale"],
            expected_scores=[
                _row("S1", "F1", 1, 2.5, 1),
                _row("S2", "F2", 1, 10, 1),
                _row("S3", "F3", 1, 0.12, 1),
            ],
            expected_void=[],
            rationale=(
                "F2 has no scale entry and falls back to the documented default 10. "
                "F3's 0.125 hits a half-even tie: 0.125 -> 0.12 (not 0.13)."
            ),
        ),
        "irregular_rows": ReferenceCase(
            name="irregular_rows",
            payload=payloads["irregular_rows"],
            expected_scores=[_row("S1", "F1", 1, 10, 1)],
            expected_void=[],
            rationale=(
                "Non-mapping rows are not responses and must not create candidates; only "
                "the mapping row is scored."
            ),
        ),
        "shared_student_two_forms": ReferenceCase(
            name="shared_student_two_forms",
            payload=generators.payload(
                [
                    generators.response("S1", "F2", "Q1", "A"),
                    generators.response("S1", "F1", "Q1", "A"),
                    generators.response("S2", "F1", "Q1", "A"),
                ],
                keys={"Q1": "A"},
                void_items=[],
                scale={},
            ),
            expected_scores=[
                _row("S1", "F1", 1, 10, 1),
                _row("S1", "F2", 1, 10, 1),
                _row("S2", "F1", 1, 10, 1),
            ],
            expected_void=[],
            rationale=(
                "One student on two forms yields two rows; report order is (student, form), "
                "so F1 precedes F2 even though F2 appeared first in the input. "
                "An absent scale table means default 10 for every form."
            ),
        ),
        "untouched_student": ReferenceCase(
            name="untouched_student",
            payload=generators.payload(
                [
                    generators.response("S1", "F1", "Q1", "A"),
                    generators.response("S2", "F2", "Q4", "Z"),
                ],
                keys={"Q1": "A"},
                void_items=[],
                scale={"F1": 10, "F2": 12},
            ),
            expected_scores=[
                _row("S1", "F1", 1, 10, 1),
                _row("S2", "F2", 0, 0, 1),
            ],
            expected_void=[],
            rationale=(
                "A response to an item with no answer key is still an attempt (raw 0). "
                "Having no key must not silently drop the candidate."
            ),
        ),
        "malformed_row": ReferenceCase(
            name="malformed_row",
            payload=generators.malformed_row_payload(),
            expected_scores=[],
            expected_void=[],
            expected_error=Exception,
            rationale=(
                "A mapping row without an answer cannot be scored and must fail the batch "
                "closed instead of emitting a partial candidate score."
            ),
        ),
    }
    return built


def expected_envelope(case: ReferenceCase) -> dict:
    return {
        "project": "assessment-score-equating",
        "status": "ok",
        "scores": case.expected_scores,
        "void_items": case.expected_void,
    }
