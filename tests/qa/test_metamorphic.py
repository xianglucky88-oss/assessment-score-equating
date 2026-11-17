"""Metamorphic tests: relations between outputs that must hold even when the
absolute values are not known in advance.

Relations covered:

===============  ==============================================
permutation      shuffling rows / void list / dict order is inert
duplication      importing the whole batch twice doubles counts
void addition    voiding an item only shrinks scoreable material
form voiding     a voided item removes that item from every form
scale isolation  rescaling one form leaves other forms untouched
identity         a student's row is unchanged by other students
===============  ==============================================
"""

from __future__ import annotations

import copy
import random
import unittest

from assessment_score_equating.pipeline import run

from . import diagnostics, generators, oracles


class TestPermutationMetamorphism(unittest.TestCase):
    def test_permuting_responses_is_inert_for_reference_cases(self):
        from . import reference_cases

        rng = random.Random(2026)
        for name, case in reference_cases.cases().items():
            if case.expected_error is not None:
                continue
            expected = run(case.payload)
            for _ in range(5):
                variant = generators.permuted(case.payload, rng)
                diagnostics.assert_same(self, variant, expected, run(variant))

    def test_permuting_fuzz_payloads_is_inert(self):
        rng = random.Random(77)
        for name, payload in generators.iter_random_payloads(60, seed=88):
            expected = run(payload)
            variant = generators.permuted(payload, rng)
            diagnostics.assert_same(self, variant, expected, run(variant))

    def test_permuting_only_void_items_is_inert(self):
        rng = random.Random(9)
        for name, payload in generators.iter_random_payloads(30, seed=10):
            variant = copy.deepcopy(payload)
            rng.shuffle(variant["void_items"])
            diagnostics.assert_same(self, variant, run(payload), run(variant))

    def test_permuting_keys_and_scale_mapping_order_is_inert(self):
        for name, payload in generators.iter_random_payloads(30, seed=12):
            variant = copy.deepcopy(payload)
            variant["keys"] = dict(reversed(list(variant["keys"].items())))
            variant["scale"] = dict(reversed(list(variant["scale"].items())))
            diagnostics.assert_same(self, variant, run(payload), run(variant))


class TestVoidMetamorphism(unittest.TestCase):
    def test_absolute_voiding_equals_removing_rows_up_front(self):
        """Voiding item X must equal deleting every X response before scoring.

        Only the score section is compared: the void list itself legitimately
        differs (one case reports the voided item, the other never saw it).
        """

        for name, payload in generators.iter_random_payloads(40, seed=13):
            items = sorted({row["item"] for row in payload["responses"]})
            if not items:
                continue
            item = items[0]
            voided = generators.with_added_void(payload, item)
            deleted = copy.deepcopy(payload)
            deleted["responses"] = [
                row for row in deleted["responses"] if row["item"] != item
            ]
            diagnostics.assert_scores_same(self, voided, run(deleted), run(voided))

    def test_voiding_preserves_other_students_exactly(self):
        payload = generators.payload(
            [
                generators.response("S1", "F1", "Q1", "A"),
                generators.response("S1", "F1", "Q2", "B"),
                generators.response("S2", "F1", "Q1", "A"),
                generators.response("S2", "F1", "Q2", "B"),
            ],
            keys={"Q1": "A", "Q2": "B"},
            void_items=[],
            scale={"F1": 2.5},
        )
        before = oracles.score_map(run(payload))
        after = oracles.score_map(run(generators.with_added_void(payload, "Q2")))
        # Voiding Q2 removes the Q2 attempt from each bucket; both students
        # had Q1 correct, so raw and attempted both drop from 2 to 1 and the
        # surviving row is rescaled from the new raw count.
        self.assertEqual(
            {key: value for key, value in after[("S2", "F1")].items() if key != "scale"},
            {"student": "S2", "form": "F1", "raw": 1, "attempted": 1},
        )
        self.assertEqual(
            {key: value for key, value in after[("S1", "F1")].items() if key != "scale"},
            {"student": "S1", "form": "F1", "raw": 1, "attempted": 1},
        )
        self.assertEqual(before[("S2", "F1")]["raw"], 2)
        self.assertEqual(before[("S2", "F1")]["attempted"], 2)

    def test_voiding_all_items_from_one_bucket_removes_only_that_bucket(self):
        payload = generators.payload(
            [
                generators.response("S1", "F1", "Q1", "A"),
                generators.response("S2", "F2", "Q1", "A"),
                generators.response("S2", "F2", "Q2", "B"),
            ],
            keys={"Q1": "A", "Q2": "B"},
            void_items=[],
            scale={"F1": 10, "F2": 12},
        )
        result = run(generators.with_added_void(payload, "Q1"))
        # Q1 is the only item that S1/F1 has; S2/F2 still has its Q2 attempt,
        # so only the fully voided bucket disappears.
        self.assertEqual(
            [(row["student"], row["form"]) for row in result["scores"]],
            [("S2", "F2")],
            "the bucket whose only item was voided must disappear entirely",
        )
        self.assertEqual(oracles.score_map(result)[("S2", "F2")]["attempted"], 1)

        all_voided = run(generators.with_added_void(payload, "Q1", "Q2"))
        self.assertEqual(all_voided["scores"], [], "no zero row may survive a full void")


class TestDuplicateMetamorphism(unittest.TestCase):
    def test_duplicating_batch_doubles_counts_and_preserves_scale_rule(self):
        for name, payload in generators.iter_random_payloads(30, seed=14):
            base = oracles.score_map(run(payload))
            doubled = oracles.score_map(run(generators.duplicate_rows(payload)))
            self.assertEqual(set(doubled), set(base), name)
            for key, row in base.items():
                self.assertEqual(doubled[key]["attempted"], 2 * row["attempted"], f"{name}/{key}")
                self.assertEqual(doubled[key]["raw"], 2 * row["raw"], f"{name}/{key}")
                factor = payload.get("scale", {}).get(key[1], oracles.DEFAULT_SCALE)
                self.assertEqual(
                    doubled[key]["scale"],
                    oracles.round_half_even(2 * row["raw"] * factor),
                    f"{name}/{key}: duplicate import must be rescaled from the doubled raw",
                )

    def test_duplicate_import_of_voided_item_stays_void(self):
        payload = generators.payload(
            [
                generators.response("S1", "F1", "Q1", "A"),
                generators.response("S1", "F1", "Q2", "B"),
            ],
            keys={"Q1": "A", "Q2": "B"},
            void_items=["Q2"],
            scale={"F1": 10},
        )
        doubled = run(generators.duplicate_rows(payload))
        self.assertEqual(doubled["scores"][0]["attempted"], 2)
        self.assertEqual(doubled["scores"][0]["raw"], 2)


class TestScaleMetamorphism(unittest.TestCase):
    def test_rescaling_one_form_never_touches_other_forms(self):
        rng = random.Random(15)
        for name, payload in generators.iter_random_payloads(40, seed=16):
            base = oracles.score_map(run(payload))
            forms = sorted({row["form"] for row in payload["responses"]})
            for form in forms:
                factor = rng.choice(generators.SCALE_FACTORS)
                changed = oracles.score_map(run(generators.with_scale(payload, form, factor)))
                for key, row in base.items():
                    if key[1] != form and key in changed:
                        self.assertEqual(
                            changed[key],
                            row,
                            f"{name}: rescale {form!r} leaked into {key!r}",
                        )

    def test_zero_raw_rows_scale_to_zero_for_any_factor(self):
        payload = generators.payload(
            [generators.response("S1", "F1", "Q1", "C")],
            keys={"Q1": "A"},
            void_items=[],
            scale={},
        )
        for factor in generators.SCALE_FACTORS:
            row = run(generators.with_scale(payload, "F1", factor))["scores"][0]
            self.assertEqual(row["scale"], 0)
            self.assertEqual(row["raw"], 0)

    def test_present_form_factor_beats_default(self):
        payload = generators.payload(
            [generators.response("S1", "F1", "Q1", "A")],
            keys={"Q1": "A"},
            void_items=[],
            scale={"F1": 3.5},
        )
        self.assertEqual(run(payload)["scores"][0]["scale"], 3.5)
        self.assertEqual(run(generators.with_scale(payload, "F1", 10))["scores"][0]["scale"], 10)


class TestStudentIsolationMetamorphism(unittest.TestCase):
    def test_adding_an_unrelated_student_leaves_existing_rows_identical(self):
        payload = generators.payload(
            [
                generators.response("S1", "F1", "Q1", "A"),
                generators.response("S1", "F1", "Q2", "C"),
            ],
            keys={"Q1": "A", "Q2": "B"},
            void_items=[],
            scale={"F1": 1.005},
        )
        expected = oracles.score_map(run(payload))
        augmented = copy.deepcopy(payload)
        augmented["responses"].append(generators.response("S9", "F1", "Q1", "B"))
        actual = oracles.score_map(run(augmented))
        self.assertEqual(actual[("S1", "F1")], expected[("S1", "F1")])
        self.assertIn(("S9", "F1"), actual)

    def test_removing_a_student_leaves_others_identical(self):
        payload = generators.payload(
            [
                generators.response("S1", "F1", "Q1", "A"),
                generators.response("S2", "F1", "Q1", "B"),
                generators.response("S2", "F1", "Q2", "B"),
            ],
            keys={"Q1": "A", "Q2": "B"},
            void_items=[],
            scale={"F1": 2.345},
        )
        expected = oracles.score_map(run(payload))
        reduced = copy.deepcopy(payload)
        reduced["responses"] = [row for row in reduced["responses"] if row["student"] != "S2"]
        actual = oracles.score_map(run(reduced))
        self.assertEqual(actual[("S1", "F1")], expected[("S1", "F1")])


if __name__ == "__main__":
    unittest.main()
