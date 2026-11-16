# Assessment Score Equating

Score multiple exam forms with void-item and revision-aware scale rules, and
release the result through a reproducible offline build.

## Quick start

```bash
# Score the bundled fixture (works from any directory, no network)
PYTHONPATH=src python3 -m assessment_score_equating.cli

# Tests: contract, unit and release-tooling
PYTHONPATH=src python3 -m unittest discover -s tests -v

# Full offline release: build -> verify -> publish
sh scripts/release.sh --python-a python3.11 --python-b python3.12

# Roll back to the previous published release
python3 -I tools/publish_release.py --rollback
```

## Modules

| Module | Responsibility |
| --- | --- |
| `src/assessment_score_equating/models.py` | Immutable result contract (`ScoreEntry`, `PipelineResult`) |
| `src/assessment_score_equating/contract.py` | Frozen field names, rounding rule, contract version |
| `src/assessment_score_equating/parser.py` | Input validation and record extraction; stable error text |
| `src/assessment_score_equating/engine.py` | Domain calculation only: no I/O, clock, locale or env access |
| `src/assessment_score_equating/report.py` | Stable JSON envelope and canonical serialisation |
| `src/assessment_score_equating/pipeline.py` | Orchestration boundary: validate → calculate → shape |
| `src/assessment_score_equating/cli.py` | Deterministic JSON CLI |

Scoring rules:

* items listed in `void_items` are ignored entirely (not attempted, not scored);
* `status: "absent"` and `status: "violation"` rows are counted separately and
  never contribute to `raw` or `attempted`;
* `scale` is rounded **half-up at 0.01** using exact decimal arithmetic
  (`scale_hundredths` is the canonical integer; `scale` is a JSON convenience
  float). `round()`/`Decimal` defaults are deliberately not used because they
  round halves to even;
* scores are sorted by `(student, form)`; `void_items` is sorted.

## Release responsibilities

The boundaries are deliberate; each layer has one job.

| Location | Owns |
| --- | --- |
| `pyproject.toml` | What the project *is*: name, version, `requires-python`, build backend, supported runtimes, packaged fixtures. No build logic. |
| `tools/releasekit/backend.py` | PEP 517 sdist/wheel construction: deterministic archives, fixed timestamps, no network, no bytecode. No staging or policy. |
| `tools/releasekit/build.py` | Transactional staging: build in `.staging-<id>`, promote by atomic rename, clean up and log on failure. |
| `tools/releasekit/manifest.py` | Manifest identity, hash verification, and explaining differences between builds. |
| `tools/releasekit/verify.py` | Policy: reproducibility, runtime matrix, CLI/environment drift, contract, imports, tests, goldens. |
| `tools/releasekit/publish.py` | Only trusts a passing, complete verification report; atomic promotion + rollback. |
| `scripts/release.sh` | Thin sequencing of build → verify → publish with distinct exit codes. |

### Artifacts

`python3 -I tools/build_release.py` writes `dist/<version>-<build_id>/`:

```
assessment_score_equating-0.1.0-py3-none-any.whl
assessment_score_equating-0.1.0.tar.gz
MANIFEST.json     # version, contract version, artifact + input hashes, no clock
SHA256SUMS        # sorted sha256 of both artifacts and the manifest
REPRODUCIBILITY.json  # only with --compare: explained diff vs an earlier build
```

Everything is timestamp-normalised: zip entries use `1980-01-01T00:00:00`,
tar entries use epoch `1577836800` with `uid=gid=0` and empty owner names, and
files in the archive keep their executable bit only. Line endings are
normalised to LF. `SOURCE_DATE_EPOCH` is recorded in the manifest for users who
repackage.

### Verification

`python3 -I tools/verify_release.py` needs **at least two distinct runtimes**
(from `tool.releasekit.supported-pythons` or `--pythons`); it refuses otherwise.
It then:

1. builds once per interpreter and requires byte-identical manifests, which
   proves both repeated-build stability and interpreter independence;
2. checks required modules, fixtures and tooling are actually present in both
   artifacts (a silent omission is not detectable from hashes alone);
3. imports every module from the extracted wheel *and* sdist with `python -I`,
   asserting the artifact version matches `pyproject.toml`;
4. runs the pinned scoring cases (void items, absent/violation, empty input,
   half-up ties, default scale, ordering) and requires identical output across
   interpreters and between wheel and sdist;
5. runs each malformed input through the contract and requires `ValueError`
   with the pinned message;
6. runs the CLI from three working directories × six hostile environment
   variants (locale, `PYTHONIOENCODING`, `PYTHONHASHSEED`, `TZ`, `PYTHONPATH`,
   `PYTHONUTF8`), plus explicit-path and `-m` modes, plus a stable usage error
   for a missing input — all 46+ outputs must be byte-identical;
7. runs the unit tests from the extracted sdist;
8. compares results with `tests/golden/expected_outputs.json`.

A report with `passed: true` is written to `--report` and to
`<candidate>/VERIFICATION.json`. Starting a re-verification immediately
withdraws any earlier approval, and an aborted run records `complete: false`,
so neither can authorise a publish.

### Publication

`python3 -I tools/publish_release.py --candidate dist/<build>` refuses to run
without a passing, complete report whose hash matches the candidate manifest,
re-hashes the candidate, copies it into `release/releases/`, then flips
`release/current` with an atomic symlink rename. If anything fails, the live
release and its metadata are left untouched. `release/previous` keeps the last
good build, so `--rollback` is a single verified symlink switch.

```
release/
  releases/<version>-<build_id>/   immutable copy of the build
  current -> releases/...          live release
  previous -> releases/...         rollback target
  published/                       MANIFEST.json, SHA256SUMS, VERIFICATION.json, RELEASE.json
  logs/                            build-*.log, publish-*.log, publish-failed.log, rollback.log
```

### Failure and stale-artifact behaviour

* a build that fails mid-way removes `.staging-*` and writes
  `dist/logs/build-<id>.log` containing the traceback and `status=failed`;
* `--clean` removes only interrupted staging directories, never completed
  builds;
* an older completed build stays published and untouched when a newer build
  fails;
* `--compare` explains differences: which artifacts changed, which input files
  changed, and whether metadata changed — repeated identical builds report
  "byte-for-byte reproducible";
* a candidate with stale, missing or modified artifacts is never published;
* `--keep N` prunes old builds but always keeps the newest.

## Offline guarantees

No build or verification step performs network access: the build backend has no
dependencies, `dependencies = []`, and the verification harnesses install a
socket guard. `pyproject.toml` declares `build-backend = "releasekit.backend"`
with `backend-path = ["tools"]`, so a PEP 517 frontend that is already installed
can build the wheel/sdist without downloading anything.
