"""Build entry point: transactional, offline, reproducible sdist+wheel.

Usage::

    python3 -I tools/build_release.py --clean
    python3 -I tools/build_release.py --compare dist/<dir>/MANIFEST.json

Exit codes: 0 success, 10 build failure (logged, staging removed).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# ``python -I`` implies ``-P``: the script directory is NOT on sys.path.  Add
# this file's own directory explicitly so the release tooling is importable
# without relying on cwd, PYTHONPATH or user site-packages.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from releasekit import build as build_mod
from releasekit import entrypoints, fsutil

EXIT_OK = 0
EXIT_FAILED = 10


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a reproducible offline release (sdist, wheel, manifest, hashes)."
    )
    entrypoints.add_common(parser)
    parser.add_argument("--dist", default=None, help="output directory (default: <root>/dist)")
    parser.add_argument("--build-id", default=None, help="stable id for this build (testing)")
    parser.add_argument(
        "--compare",
        default=None,
        help="manifest of an earlier build; differences are explained in REPRODUCIBILITY.json",
    )
    parser.add_argument("--clean", action="store_true", help="remove interrupted staging dirs first")
    parser.add_argument(
        "--keep", default=1, type=int, help="number of completed builds to retain (>=1)"
    )
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    root = entrypoints.resolve_root(args)
    dist = Path(args.dist).resolve() if args.dist else build_mod.default_dist_dir(root)

    try:
        if args.clean:
            removed = build_mod.clean(dist)
            for path in removed:
                entrypoints.warn("removed interrupted staging dir: %s" % path)

        reference = None
        if args.compare:
            reference = build_mod.load_manifest(args.compare)

        outcome = build_mod.build(
            root,
            dist_dir=dist,
            build_id=args.build_id,
            reference_manifest=reference,
        )
    except build_mod.BuildError as exc:
        entrypoints.warn("BUILD FAILED: %s" % exc)
        return EXIT_FAILED
    except Exception as exc:  # noqa: BLE001 - keep the exit contract
        entrypoints.warn("BUILD FAILED: %s: %s" % (type(exc).__name__, exc))
        return EXIT_FAILED

    summary = {
        "build_id": outcome.build_id,
        "directory": str(outcome.directory),
        "manifest_sha256": fsutil.sha256_file(outcome.manifest_path),
        "artifacts": outcome.manifest["artifacts"],
        "reproducible": outcome.reproducibility["reproducible"]
        if outcome.reproducibility
        else None,
        "explanations": outcome.reproducibility["explanations"]
        if outcome.reproducibility
        else [],
    }
    entrypoints.print_json(summary)

    try:
        kept = build_mod.prune(dist, max(1, args.keep))
        for path in kept:
            entrypoints.warn("pruned old build: %s" % path)
    except OSError as exc:
        entrypoints.warn("warning: could not prune old builds: %s" % exc)

    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
