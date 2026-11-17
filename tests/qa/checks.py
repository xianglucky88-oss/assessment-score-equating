"""Reusable, framework-free QA checks.

The checks take the code under test as callables so the mutation harness can
run the *same* assertions against deliberately broken copies.  Each check
returns structured ``Violation`` records; nothing here compares whole JSON
documents.

This module is intentionally independent of ``unittest``.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from . import generators, oracles, reference_cases
from .diagnostics import compare_results, describe

RunFn = Callable[[dict], dict]

ENVELOPE_KEYS = {"project", "status", "scores", "void_items"}
SCORE_KEYS = {"student", "form", "raw", "scale", "attempted"}


@dataclass(frozen=True)
class Violation:
    check: str
    detail: str

    def render(self) -> str:
        return f"[{self.check}] {self.detail}"


def _safe_run(run_fn: RunFn, payload: dict) -> tuple[dict | None, BaseException | None]:
    try:
        return run_fn(copy.deepcopy(payload)), None
    except BaseException as error:  # noqa: BLE001 - QA must observe all failures
        return None, error


def oracle_violations(payload: dict, run_fn: RunFn) -> list[Violation]:
    """Differential check against the independent reference oracle."""

    actual, error = _safe_run(run_fn, payload)
    if error is not None:
        return [
            Violation(
                "oracle",
                f"implementation raised {type(error).__name__}: {error}; "
                f"payload={payload!r}",
            )
        ]
    diffs = compare_results(payload, oracles.run(payload), actual)
    if not diffs:
        return []
    return [Violation("oracle", describe(payload, oracles.run(payload), actual, diffs))]


def envelope_violations(payload: dict, run_fn: RunFn) -> list[Violation]:
    """The report wrapper must expose a stable envelope around the data."""

    actual, error = _safe_run(run_fn, payload)
    if error is not None:
        return [Violation("envelope", f"raised {type(error).__name__}: {error}")]
    violations: list[Violation] = []
    keys = set(actual)
    if keys != ENVELOPE_KEYS:
        violations.append(
            Violation(
                "envelope",
                f"top-level keys are {sorted(keys)!r}, expected {sorted(ENVELOPE_KEYS)!r}",
            )
        )
    if actual.get("project") != oracles.PROJECT:
        violations.append(
            Violation("envelope", f"project={actual.get('project')!r}")
        )
    if actual.get("status") != oracles.STATUS:
        violations.append(Violation("envelope", f"status={actual.get('status')!r}"))

    scores = actual.get("scores")
    if not isinstance(scores, list):
        violations.append(Violation("envelope", f"scores is {type(scores).__name__}, not list"))
        return violations
    for index, row in enumerate(scores):
        if not isinstance(row, dict):
            violations.append(Violation("envelope", f"scores[{index}] is not a mapping"))
            continue
        if set(row) != SCORE_KEYS:
            violations.append(
                Violation(
                    "envelope",
                    f"scores[{index}] keys are {sorted(row)!r}, expected {sorted(SCORE_KEYS)!r}",
                )
            )
    void = actual.get("void_items")
    if not isinstance(void, list):
        violations.append(Violation("envelope", f"void_items is {type(void).__name__}, not list"))
    return violations


def _scoreable_source(payload: dict) -> list[dict]:
    source = [
        row
        for row in payload.get("responses", [])
        if isinstance(row, dict) and all(name in row for name in oracles.REQUIRED_FIELDS)
    ]
    void = set(payload.get("void_items", []))
    return [row for row in source if row["item"] not in void]


def candidate_violations(payload: dict, run_fn: RunFn) -> list[Violation]:
    """No report row may exist without scoreable input, and vice versa."""

    actual, error = _safe_run(run_fn, payload)
    if error is not None:
        return [Violation("candidates", f"raised {type(error).__name__}: {error}")]
    scoreable = _scoreable_source(payload)
    expected_keys = {(row["student"], row["form"]) for row in scoreable}
    actual_keys = [(row.get("student"), row.get("form")) for row in actual.get("scores", [])]
    violations: list[Violation] = []
    for key in sorted(set(actual_keys) - expected_keys, key=repr):
        violations.append(
            Violation(
                "candidates",
                f"fabricated candidate {key!r}: no scoreable input row; "
                f"payload={payload!r}",
            )
        )
    for key in sorted(expected_keys - set(actual_keys), key=repr):
        violations.append(
            Violation(
                "candidates",
                f"missing candidate {key!r}: scoreable input rows exist; "
                f"rows={[r for r in scoreable if (r['student'], r['form']) == key]!r}",
            )
        )
    if len(actual_keys) != len(set(actual_keys)):
        violations.append(Violation("candidates", f"duplicate report rows: {actual_keys!r}"))
    return violations


def field_violations(payload: dict, run_fn: RunFn) -> list[Violation]:
    """Per-row arithmetic: attempted, raw, scale, and report order."""

    actual, error = _safe_run(run_fn, payload)
    if error is not None:
        return [Violation("fields", f"raised {type(error).__name__}: {error}")]
    rows = actual.get("scores", [])
    violations: list[Violation] = []
    expected_rows = oracles.calculate(payload)["scores"]
    expected_order = [(row["student"], row["form"]) for row in expected_rows]
    order = [(row.get("student"), row.get("form")) for row in rows]
    if order != expected_order:
        violations.append(
            Violation(
                "order",
                f"report order {order!r} != sorted (student, form) order {expected_order!r}",
            )
        )
    for expected, row in zip(expected_rows, rows):
        for field in ("raw", "attempted", "scale"):
            if row.get(field) != expected[field]:
                violations.append(
                    Violation(
                        "fields",
                        f"{expected['student']!r}/{expected['form']!r}.{field}="
                        f"{row.get(field)!r}, expected {expected[field]!r}; "
                        f"rules={_rule_snapshot(payload, expected['form'])!r}; "
                        f"source_rows={_source_rows(payload, expected)!r}",
                    )
                )
    void = actual.get("void_items", [])
    expected_void = list(oracles.void_items(payload))
    if void != expected_void:
        violations.append(
            Violation(
                "void",
                f"void_items={void!r}, expected unique sorted {expected_void!r} "
                f"from input {payload.get('void_items', [])!r}",
            )
        )
    return violations


def _rule_snapshot(payload: dict, form: Any) -> dict:
    return {
        "void": payload.get("void_items", []),
        "scale": payload.get("scale", {}).get(form, oracles.DEFAULT_SCALE),
        "keys": payload.get("keys", {}),
    }


def _source_rows(payload: dict, expected_row: dict) -> list[dict]:
    return [
        row
        for row in payload.get("responses", [])
        if isinstance(row, dict)
        and row.get("student") == expected_row["student"]
        and row.get("form") == expected_row["form"]
    ]


def all_checks(payload: dict, run_fn: RunFn) -> list[Violation]:
    return [
        *envelope_violations(payload, run_fn),
        *candidate_violations(payload, run_fn),
        *field_violations(payload, run_fn),
        *oracle_violations(payload, run_fn),
    ]


def check_fixtures(
    run_fn: RunFn,
    fixtures: dict[str, dict] | None = None,
    *,
    random_count: int = 40,
    seed: int = generators.DEFAULT_SEED,
) -> list[Violation]:
    """Run every check over the named fixtures, reference cases and fuzz input."""

    cases = generators.combination_fixtures() if fixtures is None else fixtures
    violations: list[Violation] = []
    for name, payload in cases.items():
        for violation in all_checks(payload, run_fn):
            violations.append(Violation(violation.check, f"fixture {name}: {violation.detail}"))

    for name, case in reference_cases.cases().items():
        if case.expected_error is not None:
            continue
        for violation in all_checks(case.payload, run_fn):
            violations.append(
                Violation(violation.check, f"reference case {name}: {violation.detail}")
            )
        expected = reference_cases.expected_envelope(case)
        actual, error = _safe_run(run_fn, case.payload)
        if error is None:
            for diff in compare_results(case.payload, expected, actual):
                violations.append(
                    Violation(
                        "reference",
                        f"reference case {name}: {diff.render()}",
                    )
                )

    for name, payload in generators.iter_random_payloads(random_count, seed=seed):
        for violation in all_checks(payload, run_fn):
            violations.append(Violation(violation.check, f"{name}: {violation.detail}"))
    return violations


def check_error_cases(run_fn: RunFn) -> list[Violation]:
    """Invalid records must fail closed and never return a partial result."""

    violations: list[Violation] = []
    for name, case in reference_cases.cases().items():
        if case.expected_error is None:
            continue
        try:
            result = run_fn(copy.deepcopy(case.payload))
        except case.expected_error:
            continue
        except BaseException as error:  # noqa: BLE001
            violations.append(
                Violation(
                    "errors",
                    f"{name}: raised {type(error).__name__}, expected "
                    f"{case.expected_error.__name__}: {error}",
                )
            )
            continue
        violations.append(
            Violation(
                "errors",
                f"{name}: returned a result instead of failing closed: {result!r}",
            )
        )
    return violations


def metamorphic_violations(run_fn: RunFn, seed: int = generators.DEFAULT_SEED) -> list[Violation]:
    """Permutation, void monotonicity, scale isolation, duplicate import."""

    import random

    violations: list[Violation] = []
    rng = random.Random(seed)
    payloads = list(
        generators.iter_random_payloads(30, seed=seed + 1, allow_invalid_rows=True)
    )

    for name, payload in payloads:
        try:
            baseline = run_fn(payload)
        except BaseException:
            # A payload with invalid rows is allowed to fail closed; skip it.
            continue

        permuted = run_fn(generators.permuted(payload, rng))
        if permuted != baseline:
            violations.append(
                Violation(
                    "permutation",
                    f"{name}: shuffling responses/void/dict order changed the result; "
                    f"baseline={baseline!r} permuted={permuted!r}",
                )
            )

        void_candidates = sorted(
            {
                row.get("item")
                for row in payload.get("responses", [])
                if isinstance(row, dict)
            }
        )
        for item in void_candidates[:2]:
            augmented_payload = generators.with_added_void(payload, item)
            try:
                augmented = run_fn(augmented_payload)
            except BaseException as error:  # noqa: BLE001
                violations.append(
                    Violation(
                        "void_monotonic",
                        f"{name}: adding void item {item!r} raised {type(error).__name__}: {error}",
                    )
                )
                continue
            base_map = oracles.score_map(baseline)
            augmented_map = oracles.score_map(augmented)
            for key, base_row in base_map.items():
                if key not in augmented_map:
                    continue  # the candidate may legitimately disappear
                augmented_row = augmented_map[key]
                if augmented_row["attempted"] > base_row["attempted"]:
                    violations.append(
                        Violation(
                            "void_monotonic",
                            f"{name}: voiding {item!r} increased attempts for {key!r}: "
                            f"{base_row['attempted']} -> {augmented_row['attempted']}",
                        )
                    )
                if augmented_row["raw"] > base_row["raw"]:
                    violations.append(
                        Violation(
                            "void_monotonic",
                            f"{name}: voiding {item!r} increased raw for {key!r}: "
                            f"{base_row['raw']} -> {augmented_row['raw']}",
                        )
                    )

        forms = {}
        for row in payload.get("responses", []):
            if isinstance(row, dict) and "form" in row:
                forms.setdefault(row["form"], row.get("item"))
        for form in list(forms)[:2]:
            factor = rng.choice(generators.SCALE_FACTORS)
            changed = run_fn(generators.with_scale(payload, form, factor))
            changed_map = oracles.score_map(changed)
            base_map = oracles.score_map(baseline)
            for key, base_row in base_map.items():
                if key[1] == form or key not in changed_map:
                    continue
                if changed_map[key] != base_row:
                    violations.append(
                        Violation(
                            "scale_isolation",
                            f"{name}: changing scale[{form!r}] altered unrelated row "
                            f"{key!r}: {base_row!r} -> {changed_map[key]!r}",
                        )
                    )
            for key in changed_map:
                if key[1] != form and key not in base_map:
                    violations.append(
                        Violation(
                            "scale_isolation",
                            f"{name}: changing scale[{form!r}] invented row {key!r}",
                        )
                    )

        doubled_payload = generators.duplicate_rows(payload)
        try:
            doubled = run_fn(doubled_payload)
        except BaseException:
            continue
        base_map = oracles.score_map(baseline)
        doubled_map = oracles.score_map(doubled)
        if set(doubled_map) != set(base_map):
            violations.append(
                Violation(
                    "duplicate_import",
                    f"{name}: duplicating all responses changed candidates "
                    f"{sorted(base_map, key=repr)!r} -> {sorted(doubled_map, key=repr)!r}",
                )
            )
        for key, base_row in base_map.items():
            row = doubled_map.get(key)
            if row is None:
                continue
            if row["attempted"] != 2 * base_row["attempted"]:
                violations.append(
                    Violation(
                        "duplicate_import",
                        f"{name}: duplicate import changed attempted for {key!r}: "
                        f"{base_row['attempted']} -> {row['attempted']}",
                    )
                )
            if row["raw"] != 2 * base_row["raw"]:
                violations.append(
                    Violation(
                        "duplicate_import",
                        f"{name}: duplicate import changed raw for {key!r}: "
                        f"{base_row['raw']} -> {row['raw']}",
                    )
                )
    return violations


def suite_violations(
    run_fn: RunFn,
    *,
    random_count: int = 40,
    seed: int = generators.DEFAULT_SEED,
    include_metamorphic: bool = True,
) -> list[Violation]:
    """Everything the QA gate asserts about a ``run(payload)`` callable."""

    violations = check_fixtures(run_fn, random_count=random_count, seed=seed)
    violations.extend(check_error_cases(run_fn))
    if include_metamorphic:
        violations.extend(metamorphic_violations(run_fn, seed=seed))
    return violations


def first_failure_report(violations: Iterable[Violation], limit: int = 6) -> str:
    rendered = [violation.render() for violation in violations]
    shown = rendered[:limit]
    if len(rendered) > limit:
        shown.append(f"... {len(rendered) - limit} more violation(s)")
    return "\n".join(shown)
