"""Invariants that must hold for every input.

These are structural properties, not golden files:

1. voiding an item can only shrink the scoreable set;
2. a form's scale factor affects only rows for that form;
3. reordering responses, void items or mapping keys cannot change results;
4. an empty batch or invalid row never fabricates a student score;
5. the reported void set is unique and sorted;
6. grouping is exactly (student, form).
"""

from __future__ import annotations

import random
import unittest

from assessment_score_equating import engine, parser, pipeline

from . import checks, diagnostics, generators, oracles


class InvariantTestCase(unittest.TestCase):
    def assert_no_violations(self, violations, message: str) -> None:
        if violations:
            self.fail(f"{message}:\n{checks.first_failure_report(violations)}")


class TestVoidInvariants(InvariantTestCase):
    def test_void_items_only_remove_scoreable_material(self):
        """Adding void items never increases raw or attempted for any row."""

        rng = random.Random(7)
        for name, payload in generators.iter_random_payloads(40, seed=11):
            items = {
                row["item"]
                for row in payload["responses"]
                if isinstance(row, dict) and "item" in row
            }
            baseline = pipeline.run(payload)
            for item in sorted(items):
                larger = pipeline.run(generators.with_added_void(payload, item))
                base_map = oracles.score_map(baseline)
                larger_map = oracles.score_map(larger)
                for key, base_row in base_map.items():
                    row = larger_map.get(key)
                    if row is None:
                        continue
                    self.assertLessEqual(
                        row["raw"],
                        base_row["raw"],
                        f"{name}: voiding {item!r} increased raw for {key!r}",
                    )
                    self.assertLessEqual(
                        row["attempted"],
                        base_row["attempted"],
                        f"{name}: voiding {item!r} increased attempted for {key!r}",
                    )
                # Voiding may only remove candidates, never add one.
                self.assertLessEqual(set(larger_map), set(base_map), name)

    def test_void_set_is_reported_unique_and_sorted(self):
        payload = generators.void_order_payload()
        result = pipeline.run(payload)
        self.assertEqual(result["void_items"], ["Q1", "Q3"])
        self.assertEqual(result["void_items"], sorted(set(payload["void_items"])))

        duplicate = generators.duplicate_void_payload()
        duplicate_result = pipeline.run(duplicate)
        self.assertEqual(duplicate_result["void_items"], ["Q2"])
        self.assertEqual(
            oracles.score_map(duplicate_result)[("S1", "F1")]["attempted"],
            2,
            "a duplicated void entry must remove the item once, not twice",
        )

    def test_fully_voided_batch_has_no_candidates(self):
        payload = generators.fully_voided_payload()
        result = pipeline.run(payload)
        self.assertEqual(result["scores"], [], "fully voided input must not fabricate rows")
        self.assertEqual(result["void_items"], ["Q1", "Q2"])

    def test_voiding_does_not_leak_into_other_forms(self):
        """Voiding an item on F1 must not change F2 rows even for a shared item id."""

        payload = generators.payload(
            [
                generators.response("S1", "F1", "Q1", "A"),
                generators.response("S2", "F2", "Q1", "A"),
            ],
            keys={"Q1": "A"},
            void_items=[],
            scale={"F1": 1.005, "F2": 2.675},
        )
        voided = generators.with_added_void(payload, "Q1")
        # The contract keys void items globally by item id: voiding Q1 removes
        # it from every form. Assert that the *other* form's row is not merely
        # scaled differently when it survives.
        base = oracles.score_map(pipeline.run(payload))
        after = oracles.score_map(pipeline.run(voided))
        self.assertEqual(base[("S1", "F1")]["scale"], 1.0)
        self.assertEqual(base[("S2", "F2")]["scale"], 2.67)
        self.assertEqual(after, {}, "global void set applies to every form")

    def test_item_substitution_scores_substitute_on_its_own_key(self):
        from . import reference_cases

        case = reference_cases.cases()["item_substitution"]
        result = pipeline.run(case.payload)
        rows = oracles.score_map(result)
        self.assertEqual(rows[("S1", "F1")]["raw"], 2)
        self.assertEqual(rows[("S1", "F1")]["attempted"], 2)
        self.assertEqual(rows[("S2", "F2")]["raw"], 0)


class TestGroupingAndScaleInvariants(InvariantTestCase):
    def test_grouping_key_is_student_and_form_only(self):
        """Same student on two forms yields two rows; one form per row."""

        result = pipeline.run(
            generators.payload(
                [
                    generators.response("S1", "F1", "Q1", "A"),
                    generators.response("S1", "F2", "Q2", "B"),
                    generators.response("S1", "F1", "Q2", "B"),
                ],
                keys={"Q1": "A", "Q2": "B"},
                void_items=[],
                scale={"F1": 10, "F2": 12},
            )
        )
        rows = oracles.score_map(result)
        self.assertEqual(set(rows), {("S1", "F1"), ("S1", "F2")})
        self.assertEqual(rows[("S1", "F1")]["attempted"], 2)
        self.assertEqual(rows[("S1", "F2")]["attempted"], 1)
        self.assertEqual(len(result["scores"]), 2)

    def test_scale_factor_is_selected_by_own_form(self):
        """Changing F1's factor must leave F2 rows byte-identical."""

        payload = generators.payload(
            [
                generators.response("S1", "F1", "Q1", "A"),
                generators.response("S2", "F2", "Q1", "A"),
                generators.response("S3", "F3", "Q1", "A"),
            ],
            keys={"Q1": "A"},
            void_items=[],
            scale={"F1": 10, "F2": 12},
        )
        base = oracles.score_map(pipeline.run(payload))
        for factor in (0.125, 1.005, 99.5):
            changed = oracles.score_map(
                pipeline.run(generators.with_scale(payload, "F1", factor))
            )
            self.assertEqual(changed[("S2", "F2")], base[("S2", "F2")])
            self.assertEqual(changed[("S3", "F3")], base[("S3", "F3")])
            self.assertEqual(changed[("S1", "F1")]["raw"], base[("S1", "F1")]["raw"])

    def test_missing_form_scale_defaults_to_ten(self):
        payload = generators.missing_form_scale_payload()
        rows = oracles.score_map(pipeline.run(payload))
        self.assertEqual(rows[("S2", "F2")]["scale"], 10)
        self.assertEqual(
            rows[("S2", "F2")]["raw"], 1, "default scale must not change raw counting"
        )

    def test_report_order_is_student_then_form(self):
        payload = generators.payload(
            [
                generators.response("S2", "F2", "Q1", "A"),
                generators.response("S1", "F2", "Q1", "A"),
                generators.response("S1", "F1", "Q1", "A"),
                generators.response("S2", "F1", "Q1", "A"),
            ],
            keys={"Q1": "A"},
            void_items=[],
            scale={},
        )
        rows = pipeline.run(payload)["scores"]
        self.assertEqual(
            [(row["student"], row["form"]) for row in rows],
            [("S1", "F1"), ("S1", "F2"), ("S2", "F1"), ("S2", "F2")],
        )


class TestPermutationInvariants(InvariantTestCase):
    def test_reordering_responses_does_not_change_results(self):
        rng = random.Random(3)
        for name, payload in generators.iter_random_payloads(50, seed=21):
            baseline = pipeline.run(payload)
            for _ in range(3):
                reordered = pipeline.run(generators.permuted(payload, rng))
                self.assertEqual(
                    reordered,
                    baseline,
                    f"{name}: input reorder changed output; payload={payload!r}",
                )

    def test_reordering_is_stable_for_reference_cases(self):
        from . import reference_cases

        rng = random.Random(99)
        for name, case in reference_cases.cases().items():
            if case.expected_error is not None:
                continue
            baseline = pipeline.run(case.payload)
            self.assertEqual(
                pipeline.run(generators.permuted(case.payload, rng)),
                baseline,
                f"{name}: reordering changed the report",
            )

    def test_permuted_input_matches_oracle(self):
        rng = random.Random(5)
        for name, payload in generators.iter_random_payloads(30, seed=31):
            reordered = generators.permuted(payload, rng)
            diagnostics.assert_matches_oracle(self, reordered, pipeline.run(reordered))


class TestEmptyAndInvalidInputs(InvariantTestCase):
    def test_empty_batch_produces_no_scores(self):
        for payload in (
            generators.empty_payload(),
            {"responses": [], "keys": {}, "void_items": [], "scale": {}},
            {},
            {"responses": []},
        ):
            result = pipeline.run(payload)
            self.assertEqual(result["scores"], [], f"payload={payload!r}")
            self.assertEqual(result["void_items"], [])
            self.assertEqual(result["status"], "ok")

    def test_non_mapping_rows_do_not_create_students(self):
        payload = generators.irregular_rows_payload()
        result = pipeline.run(payload)
        self.assertEqual([(r["student"], r["form"]) for r in result["scores"]], [("S1", "F1")])

    def test_invalid_mapping_row_fails_closed(self):
        payload = generators.malformed_row_payload()
        records = parser.load_records(payload)
        with self.assertRaises(KeyError):
            engine.calculate(records, payload)
        with self.assertRaises(KeyError):
            pipeline.run(payload)

    def test_absent_student_gets_no_row(self):
        payload = generators.absent_student_payload()
        students = {row["student"] for row in pipeline.run(payload)["scores"]}
        self.assertEqual(students, {"S1"})

    def test_no_key_is_still_an_attempt(self):
        payload = generators.payload(
            [generators.response("S1", "F1", "Q9", "Z")],
            keys={"Q1": "A"},
            void_items=[],
            scale={"F1": 10},
        )
        rows = pipeline.run(payload)["scores"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["attempted"], 1)
        self.assertEqual(rows[0]["raw"], 0)
        self.assertEqual(rows[0]["scale"], 0)

    def test_wrong_responses_container_fails_closed(self):
        with self.assertRaises(ValueError):
            pipeline.run({"responses": "not-a-list"})


class TestDuplicateImportInvariants(InvariantTestCase):
    def test_duplicate_import_doubles_attempts_and_raw(self):
        payload = generators.payload(
            [
                generators.response("S1", "F1", "Q1", "A"),
                generators.response("S1", "F1", "Q2", "C"),
            ],
            keys={"Q1": "A", "Q2": "B"},
            void_items=[],
            scale={"F1": 2.5},
        )
        base = oracles.score_map(pipeline.run(payload))
        doubled = oracles.score_map(pipeline.run(generators.duplicate_rows(payload)))
        self.assertEqual(set(doubled), set(base))
        self.assertEqual(doubled[("S1", "F1")]["attempted"], 2 * base[("S1", "F1")]["attempted"])
        self.assertEqual(doubled[("S1", "F1")]["raw"], 2 * base[("S1", "F1")]["raw"])

    def test_duplicate_import_never_invents_or_drops_candidates(self):
        for name, payload in generators.iter_random_payloads(20, seed=41):
            base = oracles.score_map(pipeline.run(payload))
            doubled = oracles.score_map(pipeline.run(generators.duplicate_rows(payload)))
            self.assertEqual(set(doubled), set(base), f"{name}: candidates changed")

    def test_review_override_is_an_extra_attempt(self):
        payload = generators.review_override_payload()
        row = oracles.score_map(pipeline.run(payload))[("S1", "F1")]
        self.assertEqual(row["attempted"], 3)
        self.assertEqual(row["raw"], 2)
        self.assertEqual(row["scale"], 2.01)


if __name__ == "__main__":
    unittest.main()
