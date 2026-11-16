"""Verification entry point.

Builds twice (proving byte-for-byte reproducibility), then validates the
artifacts on every configured interpreter:

* import of every module from wheel and sdist with ``python -I``;
* pipeline output for void items, absent/violation and empty input;
* input-contract rejection of malformed payloads;
* CLI output under 3 working directories x 6 hostile environment variants,
  plus explicit-path and ``-m`` mode, plus a stable missing-input error;
* the unit-test suite from the extracted sdist.

Cross-interpreter and cross-scenario clusters must agree exactly.  Golden
expectations in tests/golden pin the values independently of the runtime.

Usage::

    python3 -I tools/verify_release.py
    python3 -I tools/verify_release.py --pythons /usr/bin/python3.11,python3.12
    python3 -I tools/verify_release.py --candidate dist/0.1.0-abc --report out.json

Exit codes: 0 verified, 20 verification failed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# See build_release.py: ``-I`` removes the script directory from sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from releasekit import entrypoints, verify as verify_mod

EXIT_OK = 0
EXIT_FAILED = 20


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify a release candidate across Python runtimes and environments."
    )
    entrypoints.add_common(parser)
    parser.add_argument(
        "--pythons",
        default=None,
        help="comma-separated interpreter commands (default: tool.releasekit.supported-pythons)",
    )
    parser.add_argument(
        "--candidate",
        default=None,
        help="verify an existing build directory instead of building twice",
    )
    parser.add_argument("--report", default=None, help="write the JSON report here")
    parser.add_argument("--timeout", default=verify_mod.DEFAULT_TIMEOUT, type=int)
    parser.add_argument("--keep-workdir", action="store_true", help="keep the temporary workdir")
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    root = entrypoints.resolve_root(args)
    pythons = None
    if args.pythons:
        pythons = [item.strip() for item in args.pythons.split(",") if item.strip()]

    try:
        report = verify_mod.verify(
            root,
            pythons=pythons,
            candidate=Path(args.candidate).resolve() if args.candidate else None,
            timeout=args.timeout,
            keep_workdir=args.keep_workdir,
        )
    except verify_mod.VerificationError as exc:
        entrypoints.warn("VERIFICATION FAILED: %s" % exc)
        return EXIT_FAILED

    # stdout stays small enough to read: full per-step output goes to --report.
    data = report.as_dict()
    entrypoints.print_json(report.summary())

    if args.report:
        path = Path(args.report).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if not report.passed:
        entrypoints.warn("VERIFICATION FAILED: see failed steps above")
        return EXIT_FAILED
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
