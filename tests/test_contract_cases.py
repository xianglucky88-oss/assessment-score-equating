"""Contract tests pinned by tests/golden/expected_outputs.json.

These run inside the extracted sdist during release verification, so they must
use only the packaged files and must not depend on the current directory.
"""

import json
import unittest
from pathlib import Path

from assessment_score_equating.pipeline import run
from assessment_score_equating.report import render_json

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "tests" / "contract"
GOLDEN = ROOT / "tests" / "golden" / "expected_outputs.json"


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


class TestContractCases(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = load(CONTRACT / "pipeline_cases.json")["cases"]
        cls.invalid = load(CONTRACT / "invalid_cases.json")["cases"]
        cls.golden = load(GOLDEN)

    def test_pipeline_cases_match_golden(self):
        for case in self.cases:
            with self.subTest(case=case["name"]):
                payload = case.get("payload")
                if payload is None:
                    payload = load(ROOT / case["fixture"])
                actual = json.loads(render_json(run(payload)))
                self.assertEqual(actual, self.golden["outputs"][case["name"]])

    def test_invalid_inputs_are_rejected_with_stable_messages(self):
        from assessment_score_equating.parser import validate_payload

        expected = {item["name"]: item for item in self.golden["contract_errors"]}
        for case in self.invalid:
            with self.subTest(case=case["name"]):
                with self.assertRaises(ValueError) as caught:
                    validate_payload(case["payload"])
                self.assertEqual(str(caught.exception), expected[case["name"]]["message"])

    def test_cli_golden_is_newline_terminated_json(self):
        payload = load(ROOT / "fixtures" / "input.json")
        text = render_json(run(payload))
        self.assertEqual(text, self.golden["cli_stdout"])
        self.assertTrue(text.endswith("\n"))
        self.assertEqual(json.loads(text), self.golden["outputs"]["repository_fixture"])


if __name__ == "__main__":
    unittest.main()
