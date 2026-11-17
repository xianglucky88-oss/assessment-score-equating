"""Command-line entry point for the standalone QA gate.

Usage::

    PYTHONPATH=src:. python3 -m tests.qa.gate            # checks only
    PYTHONPATH=src:. python3 -m tests.qa.gate --mutants  # checks + mutation gate
    PYTHONPATH=src:. python3 -m tests.qa.gate --shrink   # print a reduced case

Exit status is 0 only when every check passes and every mutant is killed.
This is deliberately deployment-free: it never builds, packages or ships.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mutants", action="store_true", help="run the mutation gate")
    parser.add_argument("--only", nargs="*", default=None, help="select mutant names")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument(
        "--shrink",
        metavar="PAYLOAD_JSON",
        default=None,
        help="print a minimal reproduction of the failing checks for a payload file",
    )
    args = parser.parse_args(argv)

    from assessment_score_equating.pipeline import run

    from . import checks, diagnostics, generators

    if args.shrink:
        payload = json.loads(Path(args.shrink).read_text())
        reduced = diagnostics.reduce_failure(
            payload,
            lambda candidate: bool(checks.all_checks(candidate, run)),
        )
        print("reduced payload:")
        print(json.dumps(reduced, indent=2, sort_keys=True))
        return 0

    seed = generators.DEFAULT_SEED if args.seed is None else args.seed
    violations = checks.suite_violations(run, seed=seed)
    if violations:
        print(f"{len(violations)} QA violation(s):")
        print(checks.first_failure_report(violations))
        return 1
    print("QA checks: 0 violations")

    if args.mutants:
        from .mutation_harness import evaluate_mutations

        results = evaluate_mutations(set(args.only) if args.only else None)
        survivors = [result for result in results if not result["killed"]]
        for result in results:
            status = "KILLED" if result["killed"] else "SURVIVED"
            print(f"  {status:9} {result['name']}")
        print(f"mutation gate: {len(results) - len(survivors)}/{len(results)} killed")
        if survivors:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
