import json
import unittest
from pathlib import Path

from assessment_score_equating.cli import default_input_path, read_payload
from assessment_score_equating.engine import calculate
from assessment_score_equating.parser import load_records
from assessment_score_equating.pipeline import run

ROOT = Path(__file__).resolve().parents[1]


def fixture():
    return json.loads((ROOT / "fixtures" / "input.json").read_text(encoding="utf-8"))


class TestCore(unittest.TestCase):
    def test_pipeline_and_modules(self):
        payload = fixture()
        rows = load_records(payload)
        self.assertTrue(rows)
        data = calculate(rows, payload)
        self.assertIsInstance(data, dict)
        self.assertTrue(data)
        result = run(payload)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["project"], "assessment-score-equating")
        self.assertEqual(result["contract_version"], 1)
        self.assertEqual(result["rounding"], "HALF_UP@0.01")
        self.assertEqual(result["scores"][0]["raw"], 1)
        self.assertEqual(result["void_items"], ["Q2"])

    def test_default_input_path_is_not_cwd_dependent(self):
        path = default_input_path()
        self.assertTrue(path.is_file(), path)
        self.assertEqual(read_payload(path), fixture())

    def test_void_items_do_not_count_attempts(self):
        payload = fixture()
        data = calculate(load_records(payload), payload)
        self.assertEqual(data["scores"][0].attempted, 1)

    def test_absent_and_violation_are_reported_not_scored(self):
        payload = {
            "responses": [
                {"student": "S1", "form": "F1", "item": "Q1", "answer": "A"},
                {"student": "S1", "form": "F1", "item": "Q2", "answer": "B", "status": "absent"},
                {"student": "S1", "form": "F1", "item": "Q3", "answer": "C", "status": "violation"},
            ],
            "keys": {"Q1": "A", "Q2": "B", "Q3": "C"},
            "scale": {"F1": 10},
        }
        entry = run(payload)["scores"][0]
        self.assertEqual(entry["raw"], 1)
        self.assertEqual(entry["attempted"], 1)
        self.assertEqual(entry["absent"], 1)
        self.assertEqual(entry["violations"], 1)

    def test_empty_input_returns_empty_result(self):
        self.assertEqual(run({})["scores"], [])
        self.assertEqual(run({"responses": []})["void_items"], [])

    def test_half_up_ties_are_exact(self):
        payload = {
            "responses": [{"student": "S1", "form": "F1", "item": "Q1", "answer": "A"}],
            "keys": {"Q1": "A"},
            "scale": {"F1": 0.145},
        }
        entry = run(payload)["scores"][0]
        self.assertEqual(entry["scale_hundredths"], 15)
        self.assertEqual(entry["scale"], 0.15)

    def test_scores_are_sorted_by_student_then_form(self):
        payload = {
            "responses": [
                {"student": "S2", "form": "F1", "item": "Q1", "answer": "A"},
                {"student": "S1", "form": "F2", "item": "Q1", "answer": "A"},
                {"student": "S1", "form": "F1", "item": "Q1", "answer": "A"},
            ],
            "keys": {"Q1": "A"},
        }
        keys = [(entry["student"], entry["form"]) for entry in run(payload)["scores"]]
        self.assertEqual(keys, [("S1", "F1"), ("S1", "F2"), ("S2", "F1")])


if __name__ == "__main__":
    unittest.main()
