"""Failure-case reduction and diagnostic quality.

The point of this suite is not to break the implementation; it is to prove
that *when* something breaks, the QA layer produces an actionable report:

* the failing field is named, not a whole-JSON diff;
* the original response records and their input positions are shown;
* the rule snapshot (void set, keys, scale) is shown;
* the failing payload is reduced to a minimal reproduction.

Each scenario injects a realistic fault through a wrapper, then checks the
report and the reducer.
"""

from __future__ import annotations

import copy
import unittest

from assessment_score_equating.pipeline import run

from . import checks, diagnostics, generators, oracles


def _faulty_run(mutator):
    """Wrap the real pipeline so a known fault can be injected."""

    def faulty(payload):
        result = run(payload)
        return mutator(result, payload)

    return faulty


def _drop_s2(result, payload):
    result["scores"] = [row for row in result["scores"] if row["student"] != "S2"]
    return result


def _inflate_attempted(result, payload):
    for row in result["scores"]:
        row["attempted"] += 1
    return result


def _swap_sort(result, payload):
    result["scores"] = list(reversed(result["scores"]))
    return result


def _unsort_void(result, payload):
    result["void_items"] = list(dict.fromkeys(payload.get("void_items", [])))
    return result


def _change_scale_for_first(result, payload):
    if result["scores"]:
        result["scores"][0]["scale"] = 0
    return result


class TestDiagnosticsNameTheField(unittest.TestCase):
    def test_missing_row_report_names_field_source_and_rules(self):
        payload = generators.review_override_payload()
        expected = oracles.run(payload)
        actual = _drop_s2(run(payload), payload)

        diffs = diagnostics.compare_results(payload, expected, actual)
        self.assertTrue(diffs)
        rendered = diagnostics.describe(payload, expected, actual, diffs)
        self.assertIn("scores[('S2', 'F2')]", rendered)
        self.assertIn("missing report row", rendered)
        self.assertIn("source:", rendered)
        self.assertIn("rules:", rendered)
        self.assertIn("scale_table", rendered)
        # It must not dump the whole document as the assertion payload.
        self.assertNotIn("assertEqual", rendered)

    def test_attempted_report_shows_exact_delta_and_rows(self):
        payload = generators.minimal_payload()
        expected = oracles.run(payload)
        actual = _inflate_attempted(run(payload), payload)

        diffs = diagnostics.compare_results(payload, expected, actual)
        attempted = [diff for diff in diffs if diff.path.endswith(".attempted")]
        self.assertEqual(len(attempted), 2)
        for diff in attempted:
            self.assertEqual(diff.actual, diff.expected + 1)
            self.assertIn("scoreable source rows", diff.explanation)
        rendered = "".join(diff.render() for diff in diffs)
        self.assertIn("'student': 'S1'", rendered)

    def test_order_report_names_the_order_relation(self):
        payload = generators.payload(
            [
                generators.response("S2", "F1", "Q1", "A"),
                generators.response("S1", "F1", "Q1", "A"),
            ],
            keys={"Q1": "A"},
            void_items=[],
            scale={},
        )
        violations = checks.field_violations(payload, _faulty_run(_swap_sort))
        self.assertTrue(violations)
        self.assertIn("report order", violations[0].detail)
        self.assertIn("sorted (student, form)", violations[0].detail)

    def test_void_report_shows_input_and_expected_sorted_set(self):
        payload = generators.void_order_payload()
        violations = checks.field_violations(payload, _faulty_run(_unsort_void))
        self.assertTrue(violations)
        detail = violations[0].detail
        self.assertIn("unique sorted", detail)
        self.assertIn("['Q1', 'Q3']", detail)
        self.assertIn("['Q3', 'Q1']", detail)

    def test_scale_report_names_rule_snapshot(self):
        payload = generators.minimal_payload()
        expected = oracles.run(payload)
        actual = _change_scale_for_first(run(payload), payload)
        diffs = diagnostics.compare_results(payload, expected, actual)
        scale_diff = [diff for diff in diffs if diff.path.endswith(".scale")][0]
        self.assertIn("scale_for_form", scale_diff.rules)
        self.assertIn("scale_for_form", scale_diff.explanation)

    def test_assert_same_fails_with_field_report_not_json_blob(self):
        payload = generators.minimal_payload()
        expected = oracles.run(payload)
        actual = _inflate_attempted(run(payload), payload)
        with self.assertRaises(AssertionError) as caught:
            diagnostics.assert_same(self, payload, expected, actual)
        message = str(caught.exception)
        self.assertIn("field:", message)
        self.assertIn("expected:", message)
        self.assertIn("actual:", message)
        self.assertIn("why:", message)
        self.assertLessEqual(len(message.splitlines()), 60)


class TestFailureReduction(unittest.TestCase):
    """A failing case must shrink to a minimal literal reproduction."""

    def test_reduction_removes_unrelated_students_and_items(self):
        noise = []
        for index in range(6):
            noise.append(generators.response(f"N{index}", "F1", f"QX{index}", "A"))
        target_rows = [
            generators.response("S1", "F1", "Q1", "A"),
            generators.response("S1", "F1", "Q2", "C"),  # wrong -> raw 1
            generators.response("S2", "F2", "Q1", "A"),
        ]
        payload = generators.payload(
            noise + target_rows,
            keys={"Q1": "A", "Q2": "B", **{f"QX{i}": "A" for i in range(6)}},
            void_items=[],
            scale={"F1": 10, "F2": 12, "F3": 7.5},
        )

        def fails(candidate):
            return _drop_s2(run(candidate), candidate) != oracles.run(candidate)

        self.assertTrue(fails(payload), "fault must reproduce before reduction")
        reduced = diagnostics.reduce_failure(payload, fails)
        self.assertTrue(fails(reduced), "reduced payload must still reproduce the fault")
        self.assertLess(len(reduced["responses"]), len(payload["responses"]))
        # No N* noise student should survive reduction.
        self.assertFalse(
            [row for row in reduced["responses"] if str(row["student"]).startswith("N")]
        )
        # Unused scale entries are pruned too.
        self.assertNotIn("F3", reduced["scale"])

    def test_reduction_is_deterministic(self):
        payload = generators.review_override_payload()

        def fails(candidate):
            return _inflate_attempted(run(candidate), candidate) != oracles.run(candidate)

        first = diagnostics.reduce_failure(payload, fails)
        second = diagnostics.reduce_failure(copy.deepcopy(payload), fails)
        self.assertEqual(first, second)

    def test_reduction_preserves_original_when_nothing_fails(self):
        payload = generators.minimal_payload()
        reduced = diagnostics.reduce_failure(payload, lambda candidate: False)
        self.assertEqual(reduced, payload)

    def test_shrink_report_is_readable(self):
        payload = generators.rounding_threshold_payload()

        def fails(candidate):
            result = run(candidate)
            return any(row["scale"] != oracles.round_half_even(row["raw"] * 2) for row in result["scores"])

        report = diagnostics.shrink_report(payload, lambda candidate: False)
        self.assertIn("original responses=", report)
        self.assertIn("reduced payload:", report)

    def test_reduced_failure_still_matches_diagnostic_expectations(self):
        """After reduction, the field report should be small and specific."""

        base = generators.review_override_payload()
        noise = [generators.response(f"Z{i}", "F9", "Q1", "A") for i in range(5)]
        payload = copy.deepcopy(base)
        payload["responses"].extend(noise)

        def fails(candidate):
            return _drop_s2(run(candidate), candidate) != oracles.run(candidate)

        reduced = diagnostics.reduce_failure(payload, fails)
        diffs = diagnostics.compare_results(reduced, oracles.run(reduced), _drop_s2(run(reduced), reduced))
        self.assertTrue(diffs)
        self.assertTrue(
            all("scores[('S2'" in diff.path for diff in diffs),
            [diff.path for diff in diffs],
        )


class TestMutationGateContract(unittest.TestCase):
    """The mutation harness must itself be well-formed.

    Running every mutant is reserved for the standalone harness; here we
    verify the catalogue is valid (each ``old`` fragment occurs exactly once)
    without mutating the repository.
    """

    def test_every_mutation_fragment_is_unique(self):
        from .mutation_harness import MUTATIONS, PACKAGE, SRC_ROOT

        for mutation in MUTATIONS:
            text = (SRC_ROOT / PACKAGE / mutation.target).read_text()
            self.assertEqual(
                text.count(mutation.old),
                1,
                f"{mutation.name}: expected exactly one occurrence of {mutation.old!r}",
            )
            self.assertNotEqual(mutation.old, mutation.new, mutation.name)
            self.assertTrue(mutation.reason, mutation.name)

    def test_mutation_catalogue_covers_required_fault_classes(self):
        from .mutation_harness import MUTATIONS

        names = {mutation.name for mutation in MUTATIONS}
        required = {
            # void propagation
            "void_ignored",
            "void_inverted",
            "void_counted_as_attempt",
            "void_order_unsorted",
            # grouping keys
            "grouping_key_uses_item",
            "grouping_key_uses_student_only",
            # sorting
            "report_sort_removed",
            "report_sort_reversed",
            # report envelope
            "report_status_changed",
            "report_nests_data",
            "report_drops_scores",
            "report_invents_void_list",
            # scale / rounding
            "scale_uses_global_constant",
            "scale_default_changed",
            "rounding_truncated",
            "rounding_applied_to_count",
        }
        missing = required - names
        self.assertFalse(missing, f"missing mutation coverage: {sorted(missing)}")


if __name__ == "__main__":
    unittest.main()
