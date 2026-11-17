# Assessment Score Equating

Score multiple exam forms with void-item and revision-aware scale rules.

## Modules

- `src/assessment_score_equating/models.py`: immutable result contract.
- `src/assessment_score_equating/parser.py`: input validation and record extraction.
- `src/assessment_score_equating/engine.py`: domain calculation only.
- `src/assessment_score_equating/report.py`: stable output envelope.
- `src/assessment_score_equating/pipeline.py`: orchestration boundary.
- `src/assessment_score_equating/cli.py`: deterministic JSON CLI.

## Run

```bash
PYTHONPATH=src python -m assessment_score_equating.cli
PYTHONPATH=src python -m unittest discover -s tests -v
```

The fixture is local and deterministic; no network or credentials are required.

## Quality-assurance layer

`tests/qa/` is an independent gate over the scoring pipeline.  It does not
change scoring behaviour and does not import production code into its oracles.

| Module | Purpose |
| --- | --- |
| `oracles.py` | Readable reference implementation, staged so a disagreement names the stage. |
| `reference_cases.py` | Hand-computed expected reports plus the rationale for each value. |
| `generators.py` | Seeded minimal payloads and combination fixtures. |
| `diagnostics.py` | Field-level diffs with source rows, rule snapshot and failure reduction. |
| `checks.py` | Reusable invariants used by both the tests and the mutation gate. |
| `mutation_harness.py` | In-repo mutation testing over a temporary copy of `src/`. |
| `gate.py` | Standalone entry point for CI. |

```bash
# full test suite (invariants, metamorphic, reference, diagnostics, gate contract)
PYTHONPATH=src python3 -m unittest discover -s tests -t . -v

# standalone QA gate
PYTHONPATH=src:. python3 -m tests.qa.gate

# QA gate plus the full mutation sweep (26 mutants must all be killed)
PYTHONPATH=src:. python3 -m tests.qa.gate --mutants

# reduce a failing payload to a minimal reproduction
PYTHONPATH=src:. python3 -m tests.qa.gate --shrink path/to/failing.json
```

Uncovered invariants are stated as executable relations rather than golden
files: voiding an item only shrinks the scoreable set, a form's scale factor
only touches that form's rows, input reordering is inert, and empty or invalid
batches never fabricate a candidate.  The mutation gate breaks void
propagation, grouping keys, report ordering, scale selection, rounding and the
report envelope in turn, and fails if any mutant survives.

