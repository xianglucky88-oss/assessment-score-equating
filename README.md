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
