"""Field-level differential diagnostics and deterministic failure reduction.

Assertions in this layer never compare whole JSON blobs.  They compare named
fields and report, for every difference:

* the source response record(s) and their positions in the original input;
* the rule snapshot that governed the record (void set, answer keys, scale);
* the final expected and actual field values.

``describe`` renders that as a compact, stable block suitable for a test log.
``reduce_failure`` turns any failing payload into a minimal one by greedy
deletion, so a regression report stays readable.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from . import oracles

MAX_REPORTED_DIFFS = 12


@dataclass(frozen=True)
class FieldDiff:
    path: str
    expected: Any
    actual: Any
    record_indexes: tuple[int, ...] = ()
    records: tuple[dict, ...] = ()
    rules: dict | None = None
    explanation: str = ""

    def render(self) -> str:
        lines = [f"field: {self.path}"]
        lines.append(f"  expected: {self.expected!r}")
        lines.append(f"  actual:   {self.actual!r}")
        if self.explanation:
            lines.append(f"  why:      {self.explanation}")
        if self.record_indexes:
            lines.append(f"  source:   responses{[i for i in self.record_indexes]}")
            for index, record in zip(self.record_indexes, self.records):
                lines.append(f"    [{index}] {record!r}")
        else:
            lines.append("  source:   (no single response record; batch-level field)")
        if self.rules is not None:
            lines.append(f"  rules:    {self.rules!r}")
        return "\n".join(lines)


def _rules_for(payload: dict, form: Any, items: Iterable[Any] | None = None) -> dict:
    """Snapshot of every rule that can influence one report row."""

    item_filter = set(items) if items is not None else None
    answer_keys = {
        item: answer
        for item, answer in payload.get("keys", {}).items()
        if item_filter is None or item in item_filter
    }
    return {
        "void_items": sorted(set(payload.get("void_items", []))),
        "keys": answer_keys,
        "scale_for_form": payload.get("scale", {}).get(form, oracles.DEFAULT_SCALE),
        "scale_table": dict(payload.get("scale", {})),
    }


def _source_rows(payload: dict, student: Any, form: Any) -> tuple[tuple[int, ...], tuple[dict, ...]]:
    indexes = tuple(
        index
        for index, row in enumerate(payload.get("responses", []))
        if isinstance(row, dict)
        and row.get("student") == student
        and row.get("form") == form
    )
    return indexes, tuple(payload["responses"][index] for index in indexes)


def _bucket_items(payload: dict, student: Any, form: Any) -> set:
    return {
        row.get("item")
        for index, row in enumerate(payload.get("responses", []))
        if isinstance(row, dict)
        and row.get("student") == student
        and row.get("form") == form
    }


def compare_results(payload: dict, expected: dict, actual: dict) -> list[FieldDiff]:
    """Compare two pipeline-shaped results field by field."""

    diffs: list[FieldDiff] = []

    for name in ("project", "status"):
        if expected.get(name) != actual.get(name):
            diffs.append(
                FieldDiff(
                    path=name,
                    expected=expected.get(name),
                    actual=actual.get(name),
                    explanation="envelope field changed",
                )
            )

    expected_void = expected.get("void_items")
    actual_void = actual.get("void_items")
    if expected_void != actual_void:
        diffs.append(
            FieldDiff(
                path="void_items",
                expected=expected_void,
                actual=actual_void,
                rules={"input_void_items": payload.get("void_items", [])},
                explanation="void set must be unique and sorted; order is part of the contract",
            )
        )

    expected_scores = oracles.score_map(expected)
    actual_scores = oracles.score_map(actual)

    for key in sorted(set(expected_scores) | set(actual_scores), key=repr):
        indexes, records = _source_rows(payload, *key)
        rules = _rules_for(payload, key[1], _bucket_items(payload, *key))
        expected_row = expected_scores.get(key)
        actual_row = actual_scores.get(key)

        if expected_row is None:
            diffs.append(
                FieldDiff(
                    path=f"scores[{key!r}]",
                    expected="<no report row>",
                    actual=actual_row,
                    record_indexes=indexes,
                    records=records,
                    rules=rules,
                    explanation="invented score row: no scoreable input produces this candidate",
                )
            )
            continue
        if actual_row is None:
            diffs.append(
                FieldDiff(
                    path=f"scores[{key!r}]",
                    expected=expected_row,
                    actual="<missing report row>",
                    record_indexes=indexes,
                    records=records,
                    rules=rules,
                    explanation="score row dropped although scoreable input exists",
                )
            )
            continue

        for field in ("student", "form", "raw", "scale", "attempted"):
            if expected_row.get(field) != actual_row.get(field):
                diffs.append(
                    FieldDiff(
                        path=f"scores[{key!r}].{field}",
                        expected=expected_row.get(field),
                        actual=actual_row.get(field),
                        record_indexes=indexes,
                        records=records,
                        rules=rules,
                        explanation=_explain(field, expected_row, actual_row, records, rules),
                    )
                )

    extra_fields_actual = set(actual) - set(expected)
    extra_fields_expected = set(expected) - set(actual)
    for name in sorted(extra_fields_actual | extra_fields_expected, key=repr):
        diffs.append(
            FieldDiff(
                path=f"<top-level {name}>",
                expected="present" if name in expected else "absent",
                actual="present" if name in actual else "absent",
                explanation="output envelope keys must be stable",
            )
        )
    return diffs


def _explain(field: str, expected: dict, actual: dict, records: tuple[dict, ...], rules: dict) -> str:
    if field == "raw":
        correct_attempts = [
            record
            for record in records
            if record.get("item") not in set(rules.get("void_items", []))
            and record.get("answer") == rules.get("keys", {}).get(record.get("item"))
        ]
        return (
            "raw counts non-void responses whose answer equals the key; "
            f"scoreable-correct source rows = {len(correct_attempts)}"
        )
    if field == "attempted":
        scoreable_rows = [
            record for record in records if record.get("item") not in set(rules.get("void_items", []))
        ]
        return (
            "attempted counts non-void responses for this (student, form); "
            f"scoreable source rows = {len(scoreable_rows)}"
        )
    if field == "scale":
        return (
            "scale = round(raw * scale_for_form, 2), ties to even; "
            f"raw={expected.get('raw')!r}, factor={rules.get('scale_for_form')!r}"
        )
    if field in ("student", "form"):
        return "grouping key changed; one field must never borrow another record's value"
    return "field value changed"


def describe(payload: dict, expected: dict, actual: dict, diffs: list[FieldDiff] | None = None) -> str:
    """Render a stable diagnostic block for a failing comparison."""

    diffs = compare_results(payload, expected, actual) if diffs is None else diffs
    if not diffs:
        return "no differences"

    shown = diffs[:MAX_REPORTED_DIFFS]
    blocks = [f"{len(diffs)} field difference(s) between oracle and implementation"]
    blocks.extend(diff.render() for diff in shown)
    if len(diffs) > len(shown):
        blocks.append(f"... {len(diffs) - len(shown)} more difference(s) omitted")
    blocks.append("input payload (reproduce with this literal):")
    blocks.append(repr(payload))
    return "\n".join(blocks)


def assert_same(testcase, payload: dict, expected: dict, actual: dict) -> None:
    """Fail a TestCase with field-level evidence instead of a JSON dump."""

    diffs = compare_results(payload, expected, actual)
    if diffs:
        testcase.fail(describe(payload, expected, actual, diffs))


def compare_scores(payload: dict, expected: dict, actual: dict) -> list[FieldDiff]:
    """Compare report rows only, ignoring deliberate void-set differences."""

    stripped_expected = dict(expected)
    stripped_actual = dict(actual)
    stripped_expected["void_items"] = stripped_actual.get("void_items")
    return compare_results(payload, stripped_expected, stripped_actual)


def assert_scores_same(testcase, payload: dict, expected: dict, actual: dict) -> None:
    """Field-level comparison of scores, for relations that may alter void set."""

    diffs = compare_scores(payload, expected, actual)
    if diffs:
        testcase.fail(describe(payload, expected, actual, diffs))


def assert_matches_oracle(testcase, payload: dict, actual: dict) -> None:
    expected = oracles.run(payload)
    assert_same(testcase, payload, expected, actual)


def reduce_failure(payload: dict, fails: Callable[[dict], bool]) -> dict:
    """Greedy delta-debugging reduction of a failing payload.

    Only removals are attempted, so the reduced case stays explainable as a
    literal subset of the original input.  The returned payload is the
    smallest found whose ``fails`` predicate still holds; if the original does
    not fail, it is returned unchanged.
    """

    current = copy.deepcopy(payload)
    if not fails(current):
        return current

    improved = True
    while improved:
        improved = False
        candidates: list[dict] = []
        for section in ("responses", "void_items"):
            for index in range(len(current.get(section, []))):
                candidate = copy.deepcopy(current)
                del candidate[section][index]
                candidates.append(candidate)
        for section in ("keys", "scale"):
            for name in list(current.get(section, {})):
                candidate = copy.deepcopy(current)
                del candidate[section][name]
                candidates.append(candidate)

        for candidate in candidates:
            if candidate == current:
                continue
            if fails(candidate):
                current = candidate
                improved = True
                break
    return current


def shrink_report(payload: dict, fails: Callable[[dict], bool]) -> str:
    """Reduce and render, for use in failure messages and standalone tools."""

    reduced = reduce_failure(payload, fails)
    return (
        f"original responses={len(payload.get('responses', []))}, "
        f"void={payload.get('void_items', [])!r}, "
        f"scale={payload.get('scale', {})!r}\n"
        f"reduced responses={len(reduced.get('responses', []))}, "
        f"void={reduced.get('void_items', [])!r}, "
        f"scale={reduced.get('scale', {})!r}\n"
        f"reduced payload: {reduced!r}"
    )
