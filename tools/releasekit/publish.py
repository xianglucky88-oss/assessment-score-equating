"""Atomic, rollback-capable publication of a verified candidate build.

Layout under the release directory::

    <release>/
      releases/<version>-<build_id>/   immutable published build
      previous -> releases/...          symlink to the last good build
      current  -> releases/...          symlink to the live build
      published/MANIFEST.json           copy of the live manifest
      published/SHA256SUMS              copy of the live sums
      logs/

Publication rules:

* only a candidate whose verification report says ``passed`` may be published;
* the candidate bytes are re-hashed against its manifest immediately before
  promotion (a build that changed after verification is rejected);
* the live release is switched with ``os.replace`` of a symlink, so readers see
  either the old or the new build, never a half-copied one;
* ``previous`` is retained so ``rollback`` is a single atomic switch.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from . import build as build_mod
from . import config, fsutil, manifest as manifest_mod

CURRENT_LINK = "current"
PREVIOUS_LINK = "previous"
RELEASES_DIR = "releases"
LOGS_DIR = "logs"
PUBLISHED_DIR = "published"


class PublishError(RuntimeError):
    pass


@dataclass
class PublishOutcome:
    release_dir: Path
    published_dir: Path
    previous_target: Optional[str]
    rolled_back: bool = False


def _atomic_symlink(target: str, link: Path) -> None:
    tmp = link.parent / (".%s.tmp-%d" % (link.name, os.getpid()))
    if tmp.exists() or tmp.is_symlink():
        tmp.unlink()
    os.symlink(target, str(tmp))
    os.replace(str(tmp), str(link))


def _read_link(link: Path) -> Optional[str]:
    if not link.is_symlink():
        return None
    return os.readlink(str(link))


def _log(release: Path, name: str, lines: List[str]) -> Path:
    log_dir = release / LOGS_DIR
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / name
    fsutil.write_text_atomic(path, "\n".join(lines) + "\n")
    return path


def _load_verification(path) -> dict:
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PublishError("cannot read verification report %s: %s" % (path, exc))
    if not isinstance(data, dict):
        raise PublishError("verification report %s is not an object" % path)
    return data


def _check_verification(report: dict, cfg: config.ProjectConfig, manifest: dict) -> None:
    if report.get("passed") is not True:
        raise PublishError(
            "verification report did not pass; refusing to publish "
            "(run verify first and inspect its failures)"
        )
    if report.get("complete") is False:
        raise PublishError(
            "verification report is incomplete (the run was interrupted), so it cannot "
            "authorise publication"
        )
    if report.get("project") != cfg.name or report.get("version") != cfg.version:
        raise PublishError(
            "verification report is for %s %s, not %s %s"
            % (report.get("project"), report.get("version"), cfg.name, cfg.version)
        )
    expected_hash = report.get("manifest_sha256")
    actual_hash = fsutil.sha256_bytes(manifest_mod.manifest_bytes(manifest))
    if expected_hash and expected_hash != actual_hash:
        raise PublishError(
            "candidate manifest changed after verification (expected %s, actual %s)"
            % (expected_hash, actual_hash)
        )


def publish(
    root,
    candidate,
    release_dir=None,
    verification_report=None,
    dry_run: bool = False,
) -> PublishOutcome:
    cfg = config.load(root)
    root = cfg.root
    candidate = Path(candidate).resolve()
    release = Path(release_dir).resolve() if release_dir else root / "release"

    manifest_path = candidate / manifest_mod.MANIFEST_NAME
    manifest = build_mod.load_manifest(manifest_path)
    if manifest.get("version") != cfg.version or manifest.get("project") != cfg.name:
        raise PublishError(
            "candidate manifest is %s %s, expected %s %s"
            % (manifest.get("project"), manifest.get("version"), cfg.name, cfg.version)
        )
    problems = manifest_mod.verify_manifest(manifest, candidate)
    if problems:
        raise PublishError(
            "candidate is not publishable (stale or modified artifacts): %s"
            % "; ".join(problems)
        )

    if verification_report:
        report_path = Path(verification_report)
        if not report_path.is_absolute():
            report_path = (Path.cwd() / report_path)
            if not report_path.is_file():
                report_path = candidate / Path(verification_report)
    else:
        report_path = candidate / "VERIFICATION.json"
    report = _load_verification(report_path)
    _check_verification(report, cfg, manifest)

    release.mkdir(parents=True, exist_ok=True)
    log_lines = [
        "releasekit publish",
        "project=%s" % cfg.name,
        "version=%s" % cfg.version,
        "candidate=%s" % candidate,
        "manifest_sha256=%s" % fsutil.sha256_bytes(manifest_mod.manifest_bytes(manifest)),
        "verification=%s" % report_path,
        "verification_passed=true",
    ]
    if dry_run:
        log_lines.append("status=dry-run")
        _log(release, "publish-dry-run.log", log_lines)
        return PublishOutcome(
            release_dir=release,
            published_dir=release / PUBLISHED_DIR,
            previous_target=_read_link(release / CURRENT_LINK),
        )

    release.mkdir(parents=True, exist_ok=True)
    releases = release / RELEASES_DIR
    releases.mkdir(exist_ok=True)
    staged_release = releases / ("%s-%s" % (cfg.version, candidate.name.rsplit("-", 1)[-1] + "-staging"))
    if staged_release.exists():
        fsutil.remove_tree(staged_release)

    try:
        # Copy into the release tree so the published build cannot be mutated
        # through the build directory afterwards.
        shutil.copytree(str(candidate), str(staged_release), symlinks=False)
        copied_manifest = build_mod.load_manifest(staged_release / manifest_mod.MANIFEST_NAME)
        problems = manifest_mod.verify_manifest(copied_manifest, staged_release)
        if problems:
            raise PublishError("copy verification failed: %s" % "; ".join(problems))
        final_release = releases / staged_release.name[: -len("-staging")]
        if final_release.exists():
            fsutil.remove_tree(final_release)
        os.replace(str(staged_release), str(final_release))
    except BaseException as exc:
        log_lines.append("status=failed")
        log_lines.append("error=%s: %s" % (type(exc).__name__, exc))
        if staged_release.exists():
            fsutil.remove_tree(staged_release)
            log_lines.append("cleanup=staged release removed")
        else:
            log_lines.append("cleanup=no staged release to remove")
        log_lines.append("cleanup=live release unchanged")
        _log(release, "publish-failed.log", log_lines)
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        raise PublishError("publication failed: %s (log: %s)" % (exc, release / LOGS_DIR / "publish-failed.log"))

    previous = _read_link(release / CURRENT_LINK)
    published = release / PUBLISHED_DIR
    staged_published = release / (".published-staging-%d" % os.getpid())
    old_published = release / (".published-old-%d" % os.getpid())
    if staged_published.exists():
        fsutil.remove_tree(staged_published)
    staged_published.mkdir()
    try:
        for name in (manifest_mod.MANIFEST_NAME, manifest_mod.SUMS_NAME):
            fsutil.copy_atomic(final_release / name, staged_published / name)
        reproducibility = final_release / "REPRODUCIBILITY.json"
        if reproducibility.is_file():
            fsutil.copy_atomic(reproducibility, staged_published / "REPRODUCIBILITY.json")
        fsutil.copy_atomic(report_path, staged_published / "VERIFICATION.json")
        fsutil.write_text_atomic(
            staged_published / "RELEASE.json",
            manifest_mod.canonical_json(
                {
                    "project": cfg.name,
                    "version": cfg.version,
                    "release_path": os.path.relpath(final_release, release),
                    "manifest_sha256": fsutil.sha256_file(final_release / manifest_mod.MANIFEST_NAME),
                    "previous": previous or "",
                }
            ),
        )

        # ``current`` is the commit point: it only moves after every byte of the
        # release and its metadata has been staged and verified.
        if previous:
            _atomic_symlink(previous, release / PREVIOUS_LINK)
        _atomic_symlink(os.path.relpath(final_release, release), release / CURRENT_LINK)

        try:
            if published.exists():
                os.replace(str(published), str(old_published))
                os.replace(str(staged_published), str(published))
                fsutil.remove_tree(old_published)
            else:
                os.replace(str(staged_published), str(published))
        except BaseException:
            # Restore the previous live state so readers never see a release
            # whose metadata copy does not match ``current``.
            if previous:
                _atomic_symlink(previous, release / CURRENT_LINK)
            if old_published.exists() and not published.exists():
                os.replace(str(old_published), str(published))
            raise
    except BaseException as exc:
        if staged_published.exists():
            fsutil.remove_tree(staged_published)
        log_lines.append("status=failed")
        log_lines.append("error=%s: %s" % (type(exc).__name__, exc))
        log_lines.append("cleanup=live release left unchanged")
        _log(release, "publish-failed.log", log_lines)
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        raise PublishError("publication failed while updating metadata: %s" % exc) from exc

    current_link_target = os.path.relpath(final_release, release)
    log_lines.append("status=ok")
    log_lines.append("published=%s" % final_release)
    log_lines.append("current=%s" % current_link_target)
    log_lines.append("previous=%s" % (previous or "-"))
    _log(release, "publish-%s.log" % final_release.name, log_lines)
    return PublishOutcome(release_dir=release, published_dir=published, previous_target=previous)


def rollback(release_dir) -> PublishOutcome:
    release = Path(release_dir).resolve()
    current = _read_link(release / CURRENT_LINK)
    previous = _read_link(release / PREVIOUS_LINK)
    if previous is None:
        raise PublishError("no previous release to roll back to")
    target = previous
    if not (release / target).resolve().is_dir():
        raise PublishError("previous release directory is missing: %s" % (release / target))
    manifest = build_mod.load_manifest((release / target) / manifest_mod.MANIFEST_NAME)
    problems = manifest_mod.verify_manifest(manifest, (release / target))
    if problems:
        raise PublishError("previous release failed self-check: %s" % "; ".join(problems))

    _atomic_symlink(target, release / CURRENT_LINK)
    if current is not None:
        _atomic_symlink(current, release / PREVIOUS_LINK)

    published = release / PUBLISHED_DIR
    staged = release / (".published-staging-%d" % os.getpid())
    if staged.exists():
        fsutil.remove_tree(staged)
    staged.mkdir()
    for name in (manifest_mod.MANIFEST_NAME, manifest_mod.SUMS_NAME):
        fsutil.copy_atomic((release / target) / name, staged / name)
    reproducibility = (release / target) / "REPRODUCIBILITY.json"
    if reproducibility.is_file():
        fsutil.copy_atomic(reproducibility, staged / "REPRODUCIBILITY.json")
    verification = (release / target) / "VERIFICATION.json"
    if verification.is_file():
        fsutil.copy_atomic(verification, staged / "VERIFICATION.json")
    fsutil.write_text_atomic(
        staged / "RELEASE.json",
        manifest_mod.canonical_json(
            {
                "project": manifest.get("project"),
                "version": manifest.get("version"),
                "release_path": os.path.relpath((release / target).resolve(), release),
                "manifest_sha256": fsutil.sha256_file((release / target) / manifest_mod.MANIFEST_NAME),
                "previous": current or "",
                "rolled_back": True,
            }
        ),
    )
    if published.exists():
        backup = release / (".published-old-%d" % os.getpid())
        os.replace(str(published), str(backup))
        os.replace(str(staged), str(published))
        fsutil.remove_tree(backup)
    else:
        os.replace(str(staged), str(published))

    _log(release, "rollback.log", ["status=ok", "current=%s" % target, "previous=%s" % (current or "-")])
    return PublishOutcome(release_dir=release, published_dir=published, previous_target=current, rolled_back=True)
