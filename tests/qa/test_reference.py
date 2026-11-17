"""Hand-computed reference oracles and combination-fixture boundaries.

Every expected value is written by hand in ``reference_cases``; this suite
also exercises each boundary combination explicitly:

* absent / present-but-void candidates;
* violation rows with no answer key;
* item substitution with a revised key;
* review override on the same item;
* rounding at .005 ties and near-ties;
* repeated import of the same batch;
* missing scale entries.

The assertions compare named fields and, on failure, print the source rows,
the rule snapshot and the exact differing field.
"""

from __future__ import annotations

import copy
import json
import math
import unittest
from pathlib import Path

from assessment_score_equating import engine, parser, pipeline, report

from . import checks, diagnostics, generators, oracles, reference_cases


class TestHandComputedOracle(unittest.TestCase):
    def test_every_reference_case_matches_field_by_field(self):
        for name, case in reference_cases.cases().items():
            if case.expected_error is not None:
                continue
            actual = pipeline.run(copy.deepcopy(case.payload))
            expected = reference_cases.expected_envelope(case)
            diagnostics.assert_same(self, case.payload, expected, actual)

    def test_reference_cases_are_reproducible(self):
        """Running twice must produce equal, order-stable results."""

        for name, case in reference_cases.cases().items():
            if case.expected_error is not None:
                continue
            first = pipeline.run(copy.deepcopy(case.payload))
            second = pipeline.run(copy.deepcopy(case.payload))
            self.assertEqual(first, second, name)

    def test_reference_case_rationale_stays_true(self):
        """A readable audit: expected fields must agree with the rationale.

        This guards the oracle itself against silent drift when fixtures are
        edited.  Each row must contain exactly the report fields, and totals
        must stay within the scoreable response budget.
        """

        for name, case in reference_cases.cases().items():
            if case.expected_error is not None:
                continue
            self.assertTrue(case.rationale, f"{name} needs a rationale")
            for row in case.expected_scores:
                self.assertEqual(set(row), checks.SCORE_KEYS, name)
            scoreable = len(checks._scoreable_source(case.payload))
            self.assertLessEqual(
                sum(row["attempted"] for row in case.expected_scores),
                scoreable,
                f"{name}: reported attempts exceed scoreable responses",
            )

    def test_combination_fixtures_cover_named_boundaries(self):
        fixtures = generators.combination_fixtures()
        for required in (
            "empty",
            "absent_student",
            "fully_voided",
            "duplicate_import",
            "item_substitution",
            "review_override",
            "rounding_threshold",
            "missing_form_scale",
            "irregular_rows",
        ):
            self.assertIn(required, fixtures)

    def test_shipped_fixture_matches_oracle(self):
        payload = json.loads(
            (Path(__file__).parents[2] / "fixtures" / "input.json").read_text()
        )
        diagnostics.assert_matches_oracle(self, payload, pipeline.run(payload))


class TestAbsentAndViolationBoundaries(unittest.TestCase):
    def test_absent_student_is_absent_even_with_declared_scale(self):
        payload = generators.absent_student_payload()
        # Add a scale entry for a form with no responses: it must not
        # conjure a candidate.
        payload = generators.with_scale(payload, "F3", 50)
        rows = pipeline.run(payload)["scores"]
        self.assertEqual([(r["student"], r["form"]) for r in rows], [("S1", "F1")])

    def test_violation_row_with_no_key_is_scored_as_attempt(self):
        payload = generators.review_override_payload()
        row = oracles.score_map(pipeline.run(payload))[("S2", "F2")]
        self.assertEqual(row["attempted"], 1)
        self.assertEqual(row["raw"], 0, "an unkeyed answer can never be correct")
        self.assertEqual(row["scale"], 0, "2.675 * 0 = 0, not a fabricated positive")

    def test_void_item_row_is_absent_from_counts_but_present_in_void_list(self):
        payload = generators.item_substitution_payload()
        result = pipeline.run(payload)
        self.assertEqual(result["void_items"], ["Q2"])
        raw_rows = result["scores"]
        self.assertTrue(all(row["attempted"] == 2 for row in raw_rows if row["form"] == "F1"))
        self.assertEqual(
            oracles.score_map(result)[("S1", "F1")]["raw"], 2
        )  # Q1 + substitute Q3

    def test_voiding_everything_leaves_empty_scores(self):
        payload = generators.fully_voided_payload()
        result = pipeline.run(payload)
        self.assertEqual(result["scores"], [])
        self.assertEqual(result["project"], oracles.PROJECT)
        self.assertEqual(result["status"], oracles.STATUS)


class TestReviewOverrideBoundary(unittest.TestCase):
    def test_original_and_review_are_both_counted(self):
        payload = generators.review_override_payload()
        row = oracles.score_map(pipeline.run(payload))[("S1", "F1")]
        self.assertEqual((row["attempted"], row["raw"], row["scale"]), (3, 2, 2.01))

    def test_review_without_key_does_not_become_correct(self):
        payload = generators.payload(
            [
                generators.response("S1", "F1", "Q1", "B"),
                generators.response("S1", "F1", "Q1", "A"),
            ],
            keys={},
            void_items=[],
            scale={"F1": 10},
        )
        row = pipeline.run(payload)["scores"][0]
        self.assertEqual(row["attempted"], 2)
        self.assertEqual(row["raw"], 0)


class TestUnitSubstitutionBoundary(unittest.TestCase):
    def test_substitute_item_uses_substitute_key(self):
        payload = generators.item_substitution_payload()
        rows = oracles.score_map(pipeline.run(payload))
        self.assertEqual(rows[("S2", "F2")]["raw"], 0)
        self.assertEqual(rows[("S2", "F2")]["attempted"], 1)

    def test_void_item_never_scores_even_if_answer_matches(self):
        payload = generators.payload(
            [generators.response("S1", "F1", "Q1", "A")],
            keys={"Q1": "A"},
            void_items=["Q1"],
            scale={"F1": 10},
        )
        self.assertEqual(pipeline.run(payload)["scores"], [])


class TestRoundingBoundaries(unittest.TestCase):
    def test_half_even_ties_are_exact(self):
        # Hand-derived: round(raw * factor, 2) with half-even ties.
        cases = [
            (1, 0.125, 0.12),
            (3, 0.125, 0.38),
            (5, 0.125, 0.62),
            (1, 0.375, 0.38),
            (1, 2.675, 2.67),
            (3, 2.675, 8.02),
            (2, 1.005, 2.01),
            (3, 1.005, 3.01),
            (3, 2.345, 7.04),
            (3, 1.335, 4.0),
            (5, 0.005, 0.03),
        ]
        for raw, factor, expected in cases:
            payload = generators.payload(
                [generators.response("S1", "F1", "Q1", "A")],
                keys={"Q1": "A"},
                void_items=[],
                scale={"F1": factor},
            )
            # Only one row exists; burn additional correct answers to reach
            # the requested raw count deterministically.
            responses = [
                generators.response("S1", "F1", f"Q{i}", "A") for i in range(raw)
            ]
            keys = {f"Q{i}": "A" for i in range(raw)}
            payload = generators.payload(
                responses, keys=keys, void_items=[], scale={"F1": factor}
            )
            row = pipeline.run(payload)["scores"][0]
            self.assertEqual(row["raw"], raw)
            self.assertEqual(
                row["scale"],
                expected,
                f"raw={raw}, factor={factor}: half-even rounding boundary",
            )
            self.assertEqual(row["scale"], oracles.round_half_even(raw * factor))

    def test_threshold_fixture_rows_are_exact(self):
        payload = generators.rounding_threshold_payload()
        rows = oracles.score_map(pipeline.run(payload))
        self.assertEqual(rows[("S1", "F1")]["scale"], 0.38)
        self.assertEqual(rows[("S2", "F2")]["scale"], 3.01)
        self.assertEqual(rows[("S3", "F3")]["scale"], 7.04)

    def test_rounding_is_monotonic_in_raw(self):
        """For a fixed positive factor, more raw never yields a lower scale."""

        for factor in (0.125, 1.005, 2.345, 2.675, 1.335):
            previous = -math.inf
            for raw in range(0, 12):
                responses = [
                    generators.response("S1", "F1", f"Q{i}", "A") for i in range(raw)
                ]
                keys = {f"Q{i}": "A" for i in range(raw)}
                value = pipeline.run(
                    generators.payload(
                        responses, keys=keys, void_items=[], scale={"F1": factor}
                    )
                )
                scale = value["scores"][0]["scale"] if value["scores"] else 0
                self.assertGreaterEqual(scale, previous, f"factor={factor}, raw={raw}")
                self.assertEqual(scale, oracles.round_half_even(raw * factor))
                previous = scale

    def test_rounding_is_applied_once_at_report_boundary(self):
        """No intermediate rounding: 7 * 2.345 must not be rounded in steps."""

        responses = [
            generators.response("S1", "F1", f"Q{i}", "A") for i in range(7)
        ]
        keys = {f"Q{i}": "A" for i in range(7)}
        row = pipeline.run(
            generators.payload(responses, keys=keys, void_items=[], scale={"F1": 2.345})
        )["scores"][0]
        self.assertEqual(row["scale"], 16.42)
        self.assertEqual(row["scale"], round(7 * 2.345, 2))


class TestRepeatedImportBoundary(unittest.TestCase):
    def test_repeated_import_doubles_each_component(self):
        payload = generators.duplicate_import_payload()
        once = pipeline.run(payload)["scores"]
        twice = pipeline.run(generators.duplicate_rows(payload))["scores"]
        self.assertEqual(len(once), len(twice))
        for a, b in zip(once, twice):
            self.assertEqual(b["raw"], 2 * a["raw"])
            self.assertEqual(b["attempted"], 2 * a["attempted"])

    def test_repeated_import_preserves_candidate_set(self):
        for name, payload in generators.iter_random_payloads(25, seed=51):
            once = oracles.score_map(pipeline.run(payload))
            twice = oracles.score_map(pipeline.run(generators.duplicate_rows(payload)))
            self.assertEqual(set(once), set(twice), name)

    def test_repeated_import_of_empty_batch_stays_empty(self):
        payload = generators.empty_payload()
        self.assertEqual(pipeline.run(generators.duplicate_rows(payload))["scores"], [])


class TestModuleContracts(unittest.TestCase):
    """Parser, engine and report each keep their own boundary."""

    def test_parser_matches_its_own_oracle(self):
        for name, payload in generators.iter_random_payloads(25, seed=61):
            self.assertEqual(
                parser.load_records(payload),
                oracles.parse(payload),
                f"{name}: parser output differs from the reference extraction",
            )

    def test_parser_oracle_on_reference_cases(self):
        for name, case in reference_cases.cases().items():
            if case.expected_error is not None:
                continue
            self.assertEqual(parser.load_records(case.payload), oracles.parse(case.payload), name)

    def test_parser_returns_independent_copies(self):
        payload = generators.minimal_payload()
        records = parser.load_records(payload)
        records[0]["student"] = "MUTATED"
        self.assertEqual(payload["responses"][0]["student"], "S1")
        self.assertEqual(pipeline.run(payload), oracles.run(payload))

    def test_parser_rejects_non_list_responses(self):
        with self.assertRaises(ValueError):
            parser.load_records({"responses": {"student": "S1"}})
        with self.assertRaises(ValueError):
            parser.load_records({"responses": 42})

    def test_parser_ignores_non_mapping_rows(self):
        payload = generators.irregular_rows_payload()
        records = parser.load_records(payload)
        self.assertEqual(len(records), 1)
        self.assertTrue(all(isinstance(record, dict) for record in records))

    def test_engine_matches_oracle_on_reference_cases(self):
        for name, case in reference_cases.cases().items():
            if case.expected_error is not None:
                continue
            data = engine.calculate(parser.load_records(case.payload), case.payload)
            self.assertEqual(data, oracles.calculate(case.payload), name)

    def test_engine_returns_oracle_data_directly(self):
        payload = generators.minimal_payload()
        data = engine.calculate(parser.load_records(payload), payload)
        self.assertEqual(data, oracles.calculate(payload))

    def test_engine_is_pure(self):
        payload = generators.minimal_payload()
        records = parser.load_records(payload)
        first = engine.calculate(records, payload)
        second = engine.calculate(records, payload)
        self.assertEqual(first, second)
        self.assertEqual(payload, generators.minimal_payload())

    def test_engine_does_not_mutate_input_rows(self):
        payload = generators.duplicate_import_payload()
        records = parser.load_records(payload)
        snapshot = copy.deepcopy(records)
        engine.calculate(records, payload)
        self.assertEqual(records, snapshot)

    def test_report_matches_its_own_oracle(self):
        for name, payload in generators.iter_random_payloads(20, seed=62):
            data = engine.calculate(parser.load_records(payload), payload)
            self.assertEqual(
                report.build_result(oracles.PROJECT, data),
                oracles.envelope(data),
                f"{name}: report envelope differs from the reference wrapper",
            )

    def test_report_wraps_without_altering_data(self):
        payload = generators.minimal_payload()
        data = engine.calculate(parser.load_records(payload), payload)
        wrapped = report.build_result("assessment-score-equating", data)
        self.assertEqual(wrapped["scores"], data["scores"])
        self.assertEqual(wrapped["void_items"], data["void_items"])
        self.assertEqual(wrapped["project"], "assessment-score-equating")
        self.assertEqual(wrapped["status"], "ok")

    def test_report_envelope_is_flat_and_stable(self):
        wrapped = report.build_result("assessment-score-equating", {"scores": [], "void_items": []})
        self.assertEqual(set(wrapped), checks.ENVELOPE_KEYS)
        self.assertNotIn("data", wrapped, "data must be spread, not nested")

    def test_report_does_not_leak_the_dataclass(self):
        wrapped = report.build_result("assessment-score-equating", {"scores": [], "void_items": []})
        for value in wrapped.values():
            self.assertNotIsInstance(value, type)  # no class objects
        self.assertEqual(
            json.dumps(wrapped, sort_keys=True),
            '{"project": "assessment-score-equating", "scores": [], "status": "ok", "void_items": []}',
        )


class TestWholeSuiteGate(unittest.TestCase):
    def test_all_checks_pass_on_correct_implementation(self):
        violations = checks.suite_violations(pipeline.run, random_count=30)
        if violations:
            self.fail(checks.first_failure_report(violations))

    def test_cli_is_deterministic_for_fixture(self):
        import subprocess
        import sys

        root = Path(__file__).parents[2]
        env = {"PYTHONPATH": str(root / "src"), "PATH": "/usr/bin:/bin"}
        first = subprocess.run(
            [sys.executable, "-m", "assessment_score_equating.cli"],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        second = subprocess.run(
            [sys.executable, "-m", "assessment_score_equating.cli"],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(first.stdout, second.stdout)
        parsed = json.loads(first.stdout)
        fixture = json.loads((root / "fixtures" / "input.json").read_text())
        diagnostics.assert_same(self, fixture, oracles.run(fixture), parsed)


if __name__ == "__main__":
    unittest.main()
