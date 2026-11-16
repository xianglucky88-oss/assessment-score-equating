"""Shared command-line plumbing for the release entry points."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def repo_root() -> Path:
    """Locate the repository root from this file, never from cwd."""

    return Path(__file__).resolve().parents[2]


def add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--root",
        default=None,
        help="repository root (default: derived from this script, not the cwd)",
    )


def resolve_root(args) -> Path:
    return Path(args.root).resolve() if args.root else repo_root()


def print_json(data, stream=None) -> None:
    stream = stream or sys.stdout
    text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)
    if hasattr(stream, "buffer"):
        stream.buffer.write((text + "\n").encode("utf-8"))
        stream.buffer.flush()
    else:
        stream.write(text + "\n")


def warn(message: str) -> None:
    sys.stderr.write(message.rstrip("\n") + "\n")
    sys.stderr.flush()
