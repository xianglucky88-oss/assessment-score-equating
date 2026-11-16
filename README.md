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

## Data contract and maintenance assets

The score data contract, change-review assets, and contract example snapshots
live outside this README:

- `docs/README.md`: index of the contract and maintenance package.
- `docs/contract/`: rules catalog, compatibility matrix, ambiguity cases.
- `docs/maintenance/`: review checklist, change record template, pre-release checks.
- `fixtures/contract/`: minimal contract example inputs and generated snapshots.
- `tools/regen_contract_snapshots.py`: regenerates or checks those snapshots.
