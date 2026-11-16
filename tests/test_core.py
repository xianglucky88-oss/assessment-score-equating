import json
import unittest
from pathlib import Path
from assessment_score_equating.pipeline import run
from assessment_score_equating.parser import load_records
from assessment_score_equating.engine import calculate

def fixture():
    return json.loads((Path(__file__).parents[1] / "fixtures" / "input.json").read_text())

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
        self.assertEqual(result["project"], 'assessment-score-equating')

        assert result["scores"][0]["raw"] == 1
