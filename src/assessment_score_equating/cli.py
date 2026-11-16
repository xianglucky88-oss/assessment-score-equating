"""Deterministic JSON CLI.

The CLI is deliberately insensitive to the process environment:

* input bytes are decoded as UTF-8 and output is written as UTF-8 bytes,
  regardless of ``LANG``/``LC_ALL``/``PYTHONIOENCODING``;
* the fixture location is derived from this file (or an explicit argument),
  never from the current working directory;
* hashing order, clock, locale and decimal context are not consulted.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .pipeline import run
from .report import render_json

DEFAULT_FIXTURE_PARTS = ("fixtures", "input.json")
PACKAGED_FIXTURE_PARTS = ("data", "input.json")
EXIT_USAGE = 2
EXIT_INPUT = 3


def _candidate_fixture_paths():
    package_dir = Path(__file__).resolve().parent
    candidates = [package_dir.joinpath(*PACKAGED_FIXTURE_PARTS)]
    # Source checkout layout: <repo>/src/assessment_score_equating/cli.py
    candidates.append(package_dir.parents[1].joinpath(*DEFAULT_FIXTURE_PARTS))
    # Flat artifact layout: <artifact>/site/assessment_score_equating/cli.py
    candidates.append(package_dir.parents[2].joinpath(*DEFAULT_FIXTURE_PARTS))
    return candidates


def default_input_path() -> Path:
    for candidate in _candidate_fixture_paths():
        if candidate.is_file():
            return candidate
    return _candidate_fixture_paths()[0]


def read_payload(path: Path) -> dict:
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("input is not valid UTF-8: %s" % exc) from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("input is not valid JSON: %s" % exc) from exc


def _write_stdout(text: str) -> None:
    sys.stdout.buffer.write(text.encode("utf-8"))
    sys.stdout.buffer.flush()


def _write_stderr(text: str) -> None:
    sys.stderr.buffer.write(text.encode("utf-8"))
    sys.stderr.buffer.flush()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Score multiple exam forms with void-item and revision-aware scale rules."
    )
    parser.add_argument(
        "input",
        nargs="?",
        default=None,
        help="input JSON file (default: the bundled fixture)",
    )
    args = parser.parse_args(argv)

    path = Path(args.input) if args.input is not None else default_input_path()
    try:
        payload = read_payload(path)
        result = run(payload)
    except FileNotFoundError:
        _write_stderr(
            json.dumps(
                {"status": "error", "error": "input file not found: %s" % path},
                ensure_ascii=False,
                sort_keys=True,
            )
            + "\n"
        )
        return EXIT_USAGE
    except OSError as exc:
        _write_stderr(
            json.dumps(
                {"status": "error", "error": "cannot read input: %s" % exc.strerror},
                ensure_ascii=False,
                sort_keys=True,
            )
            + "\n"
        )
        return EXIT_USAGE
    except ValueError as exc:
        _write_stderr(
            json.dumps(
                {"status": "error", "error": str(exc)},
                ensure_ascii=False,
                sort_keys=True,
            )
            + "\n"
        )
        return EXIT_INPUT

    _write_stdout(render_json(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
