"""End-to-end mutation gate.

The full mutant sweep takes a few seconds per mutant and spins up
subprocesses, so it is opt-in:

    ASE_RUN_MUTATION=1 PYTHONPATH=src python3 -m unittest tests.qa.test_mutation_gate

By default the test asserts the catalogue is well-formed and the baseline is
clean, so the regular suite still covers the gate's preconditions.
"""

from __future__ import annotations

import os
import unittest

from . import mutation_harness


class TestMutationGate(unittest.TestCase):
    def test_baseline_source_passes_all_checks(self):
        outcome = mutation_harness.baseline()
        self.assertEqual(
            outcome["violations"],
            0,
            f"unmutated source fails its own QA gate: {outcome['sample']}",
        )

    @unittest.skipUnless(
        os.environ.get("ASE_RUN_MUTATION") == "1",
        "set ASE_RUN_MUTATION=1 to run the full mutant sweep",
    )
    def test_every_mutant_is_killed(self):
        results = mutation_harness.evaluate_mutations()
        survivors = [result for result in results if not result["killed"]]
        self.assertFalse(
            survivors,
            "surviving mutants indicate blind spots: "
            + ", ".join(
                f"{result['name']} ({result['reason']})" for result in survivors
            ),
        )


if __name__ == "__main__":
    unittest.main()
