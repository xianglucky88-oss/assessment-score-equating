"""Minimal in-repo mutation-testing harness.

The harness copies the production package to a temporary directory, applies a
single textual mutation, then runs the full QA check suite against the mutant
in a fresh subprocess (so imports cannot be cached across mutants).

A mutant is "killed" when the QA checks report at least one violation.  The
harness asserts that every mutant is killed; if a mutation survives, the
tests have a blind spot and the gate fails.

This deliberately avoids third-party tooling so it works with the standard
library only.  It mutates *copies*; the repository source is never modified.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
PACKAGE = "assessment_score_equating"


@dataclass(frozen=True)
class Mutation:
    name: str
    target: str
    old: str
    new: str
    reason: str


# Each mutation breaks one property the QA layer claims to protect.  The
# ``old`` fragment must occur exactly once in the target file.
MUTATIONS: tuple[Mutation, ...] = (
    Mutation(
        name="void_ignored",
        target="engine.py",
        old='if row["item"] in void: continue',
        new='if False: continue',
        reason="void items are scored instead of excluded",
    ),
    Mutation(
        name="void_inverted",
        target="engine.py",
        old='if row["item"] in void: continue',
        new='if row["item"] not in void: continue',
        reason="only void items are scored",
    ),
    Mutation(
        name="void_counted_as_attempt",
        target="engine.py",
        old='if row["item"] in void: continue',
        new='if row["item"] in void: pass',
        reason="void item still contributes an attempt and can be correct",
    ),
    Mutation(
        name="void_order_unsorted",
        target="engine.py",
        old='"void_items": sorted(void)',
        new='"void_items": list(dict.fromkeys(payload.get("void_items", [])))',
        reason="reported void set follows input order instead of sorted order",
    ),
    Mutation(
        name="void_duplicates_reported",
        target="engine.py",
        old='"void_items": sorted(void)',
        new='"void_items": sorted(payload.get("void_items", []))',
        reason="duplicated void input is echoed instead of deduplicated",
    ),
    Mutation(
        name="grouping_key_uses_item",
        target="engine.py",
        old='scores.setdefault((row["student"], row["form"])',
        new='scores.setdefault((row["student"], row["item"])',
        reason="grouping key is (student, item) instead of (student, form)",
    ),
    Mutation(
        name="grouping_key_uses_student_only",
        target="engine.py",
        old='scores.setdefault((row["student"], row["form"])',
        new='scores.setdefault((row["student"], "ALL")',
        reason="one row per student, merging forms",
    ),
    Mutation(
        name="report_sort_removed",
        target="engine.py",
        old="for (student, form), b in sorted(scores.items()):",
        new="for (student, form), b in scores.items():",
        reason="report order follows input instead of (student, form)",
    ),
    Mutation(
        name="report_sort_reversed",
        target="engine.py",
        old="for (student, form), b in sorted(scores.items()):",
        new="for (student, form), b in sorted(scores.items(), reverse=True):",
        reason="report order is descending",
    ),
    Mutation(
        name="scale_uses_global_constant",
        target="engine.py",
        old="scale.get(form,10)",
        new="next(iter(scale.values()),10)",
        reason="scale factor is not selected by the row's own form",
    ),
    Mutation(
        name="scale_default_changed",
        target="engine.py",
        old="scale.get(form,10)",
        new="scale.get(form,0)",
        reason="missing form scale no longer defaults to 10",
    ),
    Mutation(
        name="rounding_truncated",
        target="engine.py",
        old='round(b["correct"]*scale.get(form,10),2)',
        new='round(b["correct"]*scale.get(form,10),1)',
        reason="scale is rounded to one place instead of two",
    ),
    Mutation(
        name="rounding_applied_to_count",
        target="engine.py",
        old='round(b["correct"]*scale.get(form,10),2)',
        new='round(b["correct"],2)*scale.get(form,10)',
        reason="rounding happens before scaling instead of at the boundary",
    ),
    Mutation(
        name="correctness_inverted",
        target="engine.py",
        old='row["answer"] == keys.get(row["item"])',
        new='row["answer"] != keys.get(row["item"])',
        reason="raw counts wrong answers",
    ),
    Mutation(
        name="attempted_double_counted",
        target="engine.py",
        old='bucket["attempted"]+=1',
        new='bucket["attempted"]+=2',
        reason="attempts are inflated",
    ),
    Mutation(
        name="bucket_reset_each_row",
        target="engine.py",
        old='bucket["attempted"]+=1; bucket["correct"] +=',
        new='bucket["attempted"]=1; bucket["correct"] =',
        reason="grouping does not accumulate across rows in a bucket",
    ),
    Mutation(
        name="attempted_counts_only_correct",
        target="engine.py",
        old='bucket["attempted"]+=1',
        new='bucket["attempted"]+= row["answer"] == keys.get(row["item"])',
        reason="attempted only counts correct responses",
    ),
    Mutation(
        name="report_status_changed",
        target="report.py",
        old='"status": "ok"',
        new='"status": "done"',
        reason="envelope status is not the documented value",
    ),
    Mutation(
        name="report_nests_data",
        target="report.py",
        old='{"project": result.project, "status": "ok", **result.data}',
        new='{"project": result.project, "status": "ok", "data": result.data}',
        reason="data is nested under a key instead of spread into the envelope",
    ),
    Mutation(
        name="report_drops_scores",
        target="report.py",
        old='**result.data',
        new='**{k: v for k, v in result.data.items() if k != "scores"}',
        reason="scores are dropped from the envelope",
    ),
    Mutation(
        name="report_invents_void_list",
        target="report.py",
        old='**result.data',
        new='**{**result.data, "void_items": []}',
        reason="void list is reported empty regardless of input",
    ),
    Mutation(
        name="parser_aliases_input",
        target="parser.py",
        old="[dict(row) for row in rows if isinstance(row, dict)]",
        new="[row for row in rows if isinstance(row, dict)]",
        reason="parser returns aliased input rows instead of copies",
    ),
    Mutation(
        name="parser_rejects_all_rows",
        target="parser.py",
        old="if isinstance(row, dict)]",
        new="if isinstance(row, list)]",
        reason="no mapping row is ever extracted",
    ),
    Mutation(
        name="parser_accepts_non_mapping",
        target="parser.py",
        old="if isinstance(row, dict)]",
        new="]",
        reason="non-mapping rows reach the engine",
    ),
    Mutation(
        name="pipeline_bypasses_parser",
        target="pipeline.py",
        old="calculate(records, payload)",
        new="calculate(payload.get('responses', []), payload)",
        reason="orchestration bypasses record validation/extraction",
    ),
    Mutation(
        name="pipeline_swaps_project_name",
        target="pipeline.py",
        old="'assessment-score-equating'",
        new="'score-equating'",
        reason="project identity in the envelope is wrong",
    ),
)


def apply_mutation(source_root: Path, mutation: Mutation) -> None:
    target = source_root / PACKAGE / mutation.target
    text = target.read_text()
    occurrences = text.count(mutation.old)
    if occurrences != 1:
        raise AssertionError(
            f"mutation {mutation.name!r} expects exactly one occurrence of "
            f"{mutation.old!r} in {target}, found {occurrences}"
        )
    target.write_text(text.replace(mutation.old, mutation.new))


RUNNER = r"""
import json
import sys
import traceback

src, repo = sys.argv[1], sys.argv[2]
sys.path.insert(0, src)
sys.path.insert(0, repo)

from assessment_score_equating.pipeline import run
from tests.qa import checks

violations = checks.suite_violations(run, random_count=24)

# The structural checks cannot see every module boundary (e.g. parser copies),
# so the behavioural unittest suites run too.  This list excludes the mutation
# gate itself to avoid recursion.
import unittest

loader = unittest.TestLoader()
suite = unittest.TestSuite()
for module in ("tests.qa.test_invariants", "tests.qa.test_metamorphic", "tests.qa.test_reference"):
    try:
        suite.addTests(loader.loadTestsFromName(module))
    except Exception:
        traceback.print_exc()

result = unittest.TextTestRunner(stream=open("/dev/null", "w"), verbosity=0).run(suite)
unit_failures = len(result.failures) + len(result.errors)
unit_samples = [
    test.id() + ": " + text.strip().splitlines()[-1]
    for test, text in (result.failures + result.errors)[:3]
]

print(json.dumps({
    "violations": len(violations) + unit_failures,
    "checks": sorted({v.check for v in violations}) + (["unittest"] if unit_failures else []),
    "sample": [v.render()[:400] for v in violations[:3]] + unit_samples,
}))
"""


def run_mutant(source_root: Path, repo_root: Path = REPO_ROOT, timeout: int = 120) -> dict:
    """Execute the QA suite in a subprocess against a mutated source tree."""

    completed = subprocess.run(
        [sys.executable, "-c", RUNNER, str(source_root), str(repo_root)],
        capture_output=True,
        text=True,
        timeout=timeout,
        env={"PATH": "/usr/bin:/bin"},
    )
    if completed.returncode != 0:
        return {
            "violations": -1,
            "checks": ["crash"],
            "sample": [completed.stderr.strip().splitlines()[-1] if completed.stderr else ""],
        }
    last_line = completed.stdout.strip().splitlines()[-1]
    return json.loads(last_line)


def evaluate_mutations(only: set[str] | None = None) -> list[dict]:
    results: list[dict] = []
    for mutation in MUTATIONS:
        if only is not None and mutation.name not in only:
            continue
        with tempfile.TemporaryDirectory(prefix="ase-mutant-") as temp:
            source_root = Path(temp) / "src"
            shutil.copytree(SRC_ROOT, source_root)
            apply_mutation(source_root, mutation)
            outcome = run_mutant(source_root)
        results.append(
            {
                "name": mutation.name,
                "reason": mutation.reason,
                "target": mutation.target,
                "killed": outcome["violations"] != 0,
                "violations": outcome["violations"],
                "checks": outcome["checks"],
                "sample": outcome["sample"],
            }
        )
    return results


def baseline() -> dict:
    with tempfile.TemporaryDirectory(prefix="ase-baseline-") as temp:
        source_root = Path(temp) / "src"
        shutil.copytree(SRC_ROOT, source_root)
        return run_mutant(source_root)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    selected = set(argv) or None
    base = baseline()
    print(f"baseline violations: {base['violations']}")
    if base["violations"] != 0:
        print("baseline is already failing; mutation results are not meaningful")
        return 2

    results = evaluate_mutations(selected)
    survivors = []
    for result in results:
        status = "KILLED" if result["killed"] else "SURVIVED"
        checks = ",".join(result["checks"])
        print(f"{status:9} {result['name']:38} {result['violations']:>3} violation(s)  {checks}")
        if not result["killed"]:
            survivors.append(result)
            for line in result["sample"]:
                print(f"          sample: {line}")

    print(f"\n{len(results) - len(survivors)}/{len(results)} mutants killed")
    return 0 if not survivors else 1


if __name__ == "__main__":
    raise SystemExit(main())
