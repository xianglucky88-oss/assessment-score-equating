"""Deterministic, minimal data generators for QA fixtures.

Every generator is seeded and returns plain payload dicts, so a failing case
is reproducible by the literal payload printed in the diagnostic report.
The generators never import production code.
"""

from __future__ import annotations

import copy
import random
from typing import Any, Iterable, Iterator

STUDENTS = ("S1", "S2", "S3", "S4")
FORMS = ("F1", "F2", "F3")
ITEMS = ("Q1", "Q2", "Q3", "Q4", "Q5")
ANSWERS = ("A", "B", "C", None)
SCALE_FACTORS = (0.125, 0.375, 1.005, 1.335, 2.345, 2.675, 10, 12, 7.5)
DEFAULT_SEED = 20261116


def response(student: Any, form: Any, item: Any, answer: Any) -> dict:
    return {"student": student, "form": form, "item": item, "answer": answer}


def payload(
    responses: Iterable[dict],
    *,
    keys: dict | None = None,
    void_items: Iterable[Any] | None = None,
    scale: dict | None = None,
) -> dict:
    """Build a payload with explicit, predictable section contents."""

    return {
        "responses": [copy.deepcopy(row) for row in responses],
        "keys": copy.deepcopy(keys or {}),
        "void_items": list(void_items or []),
        "scale": copy.deepcopy(scale or {}),
    }


def minimal_payload() -> dict:
    """The smallest payload that exercises every scoring field."""

    return payload(
        [
            response("S1", "F1", "Q1", "A"),
            response("S1", "F1", "Q2", "B"),
            response("S2", "F2", "Q1", "C"),
        ],
        keys={"Q1": "A", "Q2": "B"},
        void_items=["Q2"],
        scale={"F1": 10, "F2": 12},
    )


def empty_payload() -> dict:
    return payload([], keys={"Q1": "A"}, void_items=[], scale={"F1": 10})


def absent_student_payload() -> dict:
    """S2 never appears in the responses and must not get a zero row."""

    return payload(
        [
            response("S1", "F1", "Q1", "A"),
            response("S1", "F1", "Q2", "C"),
        ],
        keys={"Q1": "A", "Q2": "B"},
        void_items=[],
        scale={"F1": 10},
    )


def fully_voided_payload() -> dict:
    """Every response for S1 is void: no row, not a fabricated zero."""

    return payload(
        [
            response("S1", "F1", "Q1", "A"),
            response("S1", "F1", "Q2", "B"),
            response("S2", "F2", "Q1", "A"),
        ],
        keys={"Q1": "A", "Q2": "B"},
        void_items=["Q1", "Q2"],
        scale={"F1": 10, "F2": 12},
    )


def duplicate_import_payload() -> dict:
    """The same response imported twice, including a revised answer copy."""

    return payload(
        [
            response("S1", "F1", "Q1", "A"),
            response("S1", "F1", "Q1", "A"),
            response("S1", "F1", "Q2", "C"),
            response("S1", "F1", "Q2", "B"),
        ],
        keys={"Q1": "A", "Q2": "B"},
        void_items=[],
        scale={"F1": 2.5},
    )


def item_substitution_payload() -> dict:
    """Q2 is void; a substitute Q3 carries a different key and answer."""

    return payload(
        [
            response("S1", "F1", "Q1", "A"),
            response("S1", "F1", "Q2", "A"),
            response("S1", "F1", "Q3", "C"),
            response("S2", "F2", "Q3", "A"),
        ],
        keys={"Q1": "A", "Q2": "A", "Q3": "C"},
        void_items=["Q2"],
        scale={"F1": 10, "F2": 12},
    )


def review_override_payload() -> dict:
    """A reviewed answer replaces an earlier answer for the same item.

    The engine treats each input row as an attempt.  The QA contract records
    that behaviour explicitly instead of silently deduplicating or guessing
    which copy is authoritative.
    """

    return payload(
        [
            response("S1", "F1", "Q1", "B"),  # original, incorrect
            response("S1", "F1", "Q1", "A"),  # review override, correct
            response("S1", "F1", "Q2", "B"),
            response("S2", "F2", "Q1", "C"),  # violation: no key for C
        ],
        keys={"Q1": "A", "Q2": "B"},
        void_items=[],
        scale={"F1": 1.005, "F2": 2.675},
    )


def rounding_threshold_payload() -> dict:
    """Raw counts times tie-prone scale factors, exactly at .005 boundaries."""

    rows = [
        response("S1", "F1", "Q1", "A"),
        response("S1", "F1", "Q2", "A"),
        response("S1", "F1", "Q3", "A"),
        response("S2", "F2", "Q1", "A"),
        response("S2", "F2", "Q2", "A"),
        response("S2", "F2", "Q3", "A"),
        response("S3", "F3", "Q1", "A"),
        response("S3", "F3", "Q2", "A"),
        response("S3", "F3", "Q3", "A"),
    ]
    return payload(
        rows,
        keys={"Q1": "A", "Q2": "A", "Q3": "A"},
        void_items=[],
        scale={"F1": 0.125, "F2": 1.005, "F3": 2.345},
    )


def missing_form_scale_payload() -> dict:
    """F2 has no scale entry and must fall back to the documented default."""

    return payload(
        [
            response("S1", "F1", "Q1", "A"),
            response("S2", "F2", "Q1", "A"),
            response("S3", "F3", "Q1", "A"),
        ],
        keys={"Q1": "A"},
        void_items=[],
        scale={"F1": 2.5, "F3": 0.125},
    )


def irregular_rows_payload() -> dict:
    """Non-mapping rows are skipped; mapping rows missing fields fail closed."""

    return payload(
        [
            "not-a-record",
            42,
            None,
            response("S1", "F1", "Q1", "A"),
        ],
        keys={"Q1": "A"},
        void_items=[],
        scale={"F1": 10},
    )


def malformed_row_payload() -> dict:
    return payload(
        [
            response("S1", "F1", "Q1", "A"),
            {"student": "S1", "form": "F1", "item": "Q2"},  # no answer
        ],
        keys={"Q1": "A", "Q2": "B"},
        void_items=[],
        scale={"F1": 10},
    )


def void_order_payload() -> dict:
    """Void items arrive unsorted and must be reported unique and sorted."""

    return payload(
        [
            response("S1", "F1", "Q1", "A"),
            response("S1", "F1", "Q2", "B"),
            response("S1", "F1", "Q3", "C"),
        ],
        keys={"Q1": "A", "Q2": "B", "Q3": "C"},
        void_items=["Q3", "Q1"],
        scale={"F1": 2.5},
    )


def duplicate_void_payload() -> dict:
    """A duplicated void entry must not double-count or duplicate output."""

    return payload(
        [
            response("S1", "F1", "Q1", "A"),
            response("S1", "F1", "Q2", "B"),
            response("S1", "F1", "Q3", "C"),
        ],
        keys={"Q1": "A", "Q2": "B", "Q3": "C"},
        void_items=["Q2", "Q2", "Q2"],
        scale={"F1": 1.335},
    )


def null_answer_payload() -> dict:
    """A blank answer is an attempt with no key match, not an absent row."""

    return payload(
        [
            response("S1", "F1", "Q1", None),
            response("S1", "F1", "Q2", "B"),
            response("S2", "F2", "Q1", None),
        ],
        keys={"Q1": "A", "Q2": "B"},
        void_items=[],
        scale={"F1": 1.005, "F2": 2.675},
    )


def combination_fixtures() -> dict[str, dict]:
    """Named fixtures that combine the boundary features under test."""

    return {
        "minimal": minimal_payload(),
        "empty": empty_payload(),
        "absent_student": absent_student_payload(),
        "fully_voided": fully_voided_payload(),
        "duplicate_import": duplicate_import_payload(),
        "item_substitution": item_substitution_payload(),
        "review_override": review_override_payload(),
        "rounding_threshold": rounding_threshold_payload(),
        "missing_form_scale": missing_form_scale_payload(),
        "irregular_rows": irregular_rows_payload(),
        "void_order": void_order_payload(),
        "duplicate_void": duplicate_void_payload(),
        "null_answer": null_answer_payload(),
    }


def random_payload(
    rng: random.Random,
    *,
    max_rows: int = 14,
    allow_duplicates: bool = True,
    allow_invalid_rows: bool = False,
) -> dict:
    """A bounded random payload; small enough to reduce by hand."""

    forms = rng.sample(FORMS, rng.randint(1, len(FORMS)))
    students = rng.sample(STUDENTS, rng.randint(1, len(STUDENTS)))
    items = rng.sample(ITEMS, rng.randint(1, len(ITEMS)))
    keys = {item: rng.choice(ANSWERS) for item in items if rng.random() < 0.8}
    void = [item for item in items if rng.random() < 0.25]
    scale = {form: rng.choice(SCALE_FACTORS) for form in forms if rng.random() < 0.8}

    rows: list[dict] = []
    for _ in range(rng.randint(0, max_rows)):
        if allow_invalid_rows and rng.random() < 0.1:
            rows.append(rng.choice(["bad", 7, None, {"student": "S9"}]))
            continue
        rows.append(
            response(
                rng.choice(students),
                rng.choice(forms),
                rng.choice(items),
                rng.choice(ANSWERS),
            )
        )
    if allow_duplicates and rows and rng.random() < 0.5:
        rows.append(copy.deepcopy(rng.choice(rows)))
    rng.shuffle(rows)
    return payload(rows, keys=keys, void_items=void, scale=scale)


def iter_random_payloads(
    count: int,
    *,
    seed: int = DEFAULT_SEED,
    max_rows: int = 14,
    allow_invalid_rows: bool = False,
) -> Iterator[tuple[str, dict]]:
    rng = random.Random(seed)
    for index in range(count):
        yield f"random[{seed}:{index}]", random_payload(
            rng, max_rows=max_rows, allow_invalid_rows=allow_invalid_rows
        )


def permuted(payload_dict: dict, rng: random.Random) -> dict:
    """Reorder responses and void_items while preserving content."""

    variant = copy.deepcopy(payload_dict)
    rng.shuffle(variant["responses"])
    rng.shuffle(variant["void_items"])
    # Dict order must not matter either; rebuild in reverse to prove it.
    variant["keys"] = dict(reversed(list(variant.get("keys", {}).items())))
    variant["scale"] = dict(reversed(list(variant.get("scale", {}).items())))
    return variant


def with_added_void(payload_dict: dict, *items: Any) -> dict:
    variant = copy.deepcopy(payload_dict)
    variant["void_items"] = list(variant.get("void_items", [])) + list(items)
    return variant


def with_scale(payload_dict: dict, form: Any, factor: Any) -> dict:
    variant = copy.deepcopy(payload_dict)
    variant.setdefault("scale", {})[form] = factor
    return variant


def duplicate_rows(payload_dict: dict) -> dict:
    variant = copy.deepcopy(payload_dict)
    variant["responses"] = variant["responses"] + copy.deepcopy(variant["responses"])
    return variant
