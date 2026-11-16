"""Build orchestration: clean staging, artifact creation, failure cleanup.

A build is transactional in the filesystem sense:

1. work happens in ``<dist>/.staging-<id>``;
2. on success the staging directory is renamed into ``<dist>/<version>-<id>``
   (an atomic directory rename on POSIX);
3. on any exception the staging directory and the partial output directory are
   removed, and a log is written to ``<dist>/logs``.

Nothing is ever written directly into a published release directory.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from . import backend, config, fsutil, manifest as manifest_mod

EXIT_OK = 0
EXIT_BUILD_FAILED = 10


@dataclass
class BuildOutcome:
    build_id: str
    directory: Path
    manifest: dict
    manifest_path: Path
    reproducibility: Optional[dict]


class BuildError(RuntimeError):
    """Raised for build failures that should produce a logged, clean exit."""


def default_dist_dir(root: Path) -> Path:
    override = os.environ.get("RELEASEKIT_DIST_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return Path(root).resolve() / "dist"


def _new_build_id() -> str:
    return "b" + secrets.token_hex(6)


def _safe_log_name(build_id: str) -> str:
    if not re.match(r"^[A-Za-z0-9._-]+$", build_id):
        raise BuildError("invalid build id: %r" % build_id)
    return "build-%s.log" % build_id


def _write_log(log_dir: Path, build_id: str, lines: List[str]) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / _safe_log_name(build_id)
    fsutil.write_text_atomic(path, "\n".join(lines) + "\n")
    return path


def build(
    root,
    dist_dir=None,
    build_id: Optional[str] = None,
    reference_manifest: Optional[dict] = None,
    keep: bool = True,
) -> BuildOutcome:
    """Build sdist+wheel+manifest into a fresh directory.

    ``reference_manifest`` is compared with the new manifest and the result is
    attached to the outcome (used by repeated-build checks).
    """

    cfg = config.load(root)
    dist = Path(dist_dir) if dist_dir is not None else default_dist_dir(cfg.root)
    dist.mkdir(parents=True, exist_ok=True)
    build_id = build_id or _new_build_id()
    _safe_log_name(build_id)  # validate before touching anything

    staging = dist / (".staging-%s" % build_id)
    final = dist / ("%s-%s" % (cfg.version, build_id))
    log_lines: List[str] = [
        "releasekit build",
        "build_id=%s" % build_id,
        "project=%s" % cfg.name,
        "version=%s" % cfg.version,
        "root=%s" % cfg.root,
        "dist=%s" % dist,
        "python=%s" % sys.version.replace("\n", " "),
        "implementation=%s" % sys.implementation.name,
    ]

    if final.exists():
        raise BuildError("refusing to reuse existing build directory: %s" % final)

    try:
        staging.mkdir()
        log_lines.append("staging=%s" % staging)

        sdist_name = backend.build_sdist(staging, root=cfg.root)
        wheel_name = backend.build_wheel(staging, root=cfg.root)
        log_lines.append("sdist=%s" % sdist_name)
        log_lines.append("wheel=%s" % wheel_name)

        artifacts = [staging / sdist_name, staging / wheel_name]
        for artifact in artifacts:
            if not artifact.is_file() or artifact.stat().st_size == 0:
                raise BuildError("artifact missing or empty: %s" % artifact.name)
            log_lines.append(
                "artifact=%s sha256=%s size=%d"
                % (artifact.name, fsutil.sha256_file(artifact), artifact.stat().st_size)
            )

        data = manifest_mod.build_manifest(cfg, artifacts)
        payload = manifest_mod.manifest_bytes(data)
        manifest_sha = fsutil.sha256_bytes(payload)
        sums = manifest_mod.sha256sums_text(data, manifest_sha)

        fsutil.write_atomic(staging / manifest_mod.MANIFEST_NAME, payload)
        fsutil.write_text_atomic(staging / manifest_mod.SUMS_NAME, sums)
        log_lines.append("manifest_sha256=%s" % manifest_sha)

        problems = manifest_mod.verify_manifest(data, staging)
        if problems:
            raise BuildError("manifest self-check failed: %s" % "; ".join(problems))

        os.replace(str(staging), str(final))
        log_lines.append("status=ok")
        log_lines.append("directory=%s" % final)
        _write_log(dist / "logs", build_id, log_lines)

        comparison = None
        if reference_manifest is not None:
            comparison = manifest_mod.compare_manifests(reference_manifest, data)
            fsutil.write_text_atomic(
                final / "REPRODUCIBILITY.json", manifest_mod.canonical_json(comparison)
            )

        if not keep:
            fsutil.remove_tree(final)

        return BuildOutcome(
            build_id=build_id,
            directory=final,
            manifest=data,
            manifest_path=final / manifest_mod.MANIFEST_NAME,
            reproducibility=comparison,
        )
    except BaseException as exc:  # noqa: BLE001 - log then re-raise/cleanup
        log_lines.append("status=failed")
        log_lines.append("error=%s: %s" % (type(exc).__name__, exc))
        log_lines.extend(traceback.format_exc().rstrip("\n").splitlines())
        try:
            fsutil.remove_tree(staging)
            log_lines.append("cleanup=staging removed")
        except OSError as cleanup_exc:  # pragma: no cover - hostile filesystem
            log_lines.append("cleanup=staging removal failed: %s" % cleanup_exc)
        try:
            log_path = _write_log(dist / "logs", build_id, log_lines)
        except OSError:  # pragma: no cover - log dir unusable
            log_path = None
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        if isinstance(exc, BuildError):
            raise BuildError(str(exc) + ("" if log_path is None else " (log: %s)" % log_path))
        raise BuildError(
            "build failed: %s%s" % (exc, "" if log_path is None else " (log: %s)" % log_path)
        ) from exc


def load_manifest(path) -> dict:
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BuildError("cannot read manifest %s: %s" % (path, exc))
    if not isinstance(data, dict):
        raise BuildError("manifest %s is not a JSON object" % path)
    return data


def clean(dist_dir) -> List[Path]:
    """Remove interrupted staging directories; keep completed releases/logs."""

    dist = Path(dist_dir)
    removed = []
    if not dist.is_dir():
        return removed
    for path in sorted(dist.iterdir()):
        if path.name.startswith(".staging-"):
            fsutil.remove_tree(path)
            removed.append(path)
    return removed


def prune(dist_dir, keep: int) -> List[Path]:
    """Delete old completed build dirs beyond *keep* (never the newest)."""

    dist = Path(dist_dir)
    if not dist.is_dir() or keep < 1:
        return []
    releases = [
        path
        for path in dist.iterdir()
        if path.is_dir()
        and not path.name.startswith((".", "_"))
        and (path / manifest_mod.MANIFEST_NAME).is_file()
    ]
    releases.sort(key=lambda item: (item.stat().st_mtime, item.name), reverse=True)
    removed = []
    for path in releases[keep:]:
        if path.name == "previous":
            continue
        fsutil.remove_tree(path)
        removed.append(path)
    return removed
