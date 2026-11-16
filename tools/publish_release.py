"""Publication entry point: verified, atomic, rollback-capable.

Usage::

    python3 -I tools/publish_release.py --candidate dist/0.1.0-abc \
        --verification VERIFICATION.json
    python3 -I tools/publish_release.py --rollback

Only candidates with a passing verification report are published.  Publication
copies the candidate into ``release/releases/`` and switches
``release/current`` with an atomic symlink rename; the prior release stays
reachable via ``release/previous`` for one-command rollback.

Exit codes: 0 published/rolled back, 30 publication refused or failed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# See build_release.py: ``-I`` removes the script directory from sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from releasekit import entrypoints, publish as publish_mod

EXIT_OK = 0
EXIT_FAILED = 30


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Publish a verified release atomically.")
    entrypoints.add_common(parser)
    parser.add_argument("--candidate", default=None, help="verified build directory")
    parser.add_argument(
        "--release-dir", default=None, help="release root (default: <root>/release)"
    )
    parser.add_argument(
        "--verification",
        default=None,
        help="verification report (default: <release>/VERIFICATION.json)",
    )
    parser.add_argument("--rollback", action="store_true", help="switch back to the previous release")
    parser.add_argument("--dry-run", action="store_true", help="check everything, change nothing")
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    root = entrypoints.resolve_root(args)
    release = Path(args.release_dir).resolve() if args.release_dir else root / "release"
    try:
        if args.rollback:
            outcome = publish_mod.rollback(release)
            entrypoints.print_json(
                {"status": "rolled-back", "release_dir": str(outcome.release_dir)}
            )
            return EXIT_OK

        if not args.candidate:
            entrypoints.warn("nothing to publish: pass --candidate or --rollback")
            return EXIT_FAILED
        outcome = publish_mod.publish(
            root,
            Path(args.candidate).resolve(),
            release_dir=release,
            verification_report=args.verification,
            dry_run=args.dry_run,
        )
    except publish_mod.PublishError as exc:
        entrypoints.warn("PUBLISH REFUSED/FAILED: %s" % exc)
        return EXIT_FAILED

    entrypoints.print_json(
        {
            "status": "dry-run" if args.dry_run else "published",
            "release_dir": str(outcome.release_dir),
            "published_dir": str(outcome.published_dir),
            "previous": outcome.previous_target,
        }
    )
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
