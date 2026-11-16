"""Build manifest creation, validation and reproducible-diff explanation.

The manifest is the machine-readable identity of a candidate release.  It is
deterministic: no wall clock, interpreter path or user name is included, so two
builds from identical inputs must produce identical manifest bytes.

Responsibility boundary: the manifest *describes* bytes; ``verify`` decides
whether they are acceptable and ``publish`` only trusts a manifest that
``verify`` has approved.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

from . import config, fsutil

MANIFEST_NAME = "MANIFEST.json"
SUMS_NAME = "SHA256SUMS"
SCHEMA_VERSION = 1
# Files a completed build directory may contain in addition to the artifacts.
# Anything else is treated as stale and blocks publication.
ALLOWED_EXTRA_FILES = frozenset(
    {MANIFEST_NAME, SUMS_NAME, "REPRODUCIBILITY.json", "VERIFICATION.json"}
)


def canonical_json(data) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _file_record(base: Path, relative: Path) -> Dict[str, object]:
    path = Path(base) / relative
    return {
        "path": relative.as_posix(),
        "sha256": fsutil.sha256_file(path),
        "size": path.stat().st_size,
    }


def source_records(root: Path) -> List[Dict[str, object]]:
    """Hash every input that can affect the artifacts."""

    records: List[Dict[str, object]] = []
    pyproject = root / "pyproject.toml"
    records.append(_file_record(root, Path("pyproject.toml")))
    for name in ("README.md",):
        if (root / name).is_file():
            records.append(_file_record(root, Path(name)))
    for base, label in (
        (root / "src", "src"),
        (root / "fixtures", "fixtures"),
        (root / "tools", "tools"),
        (root / "scripts", "scripts"),
        (root / "tests", "tests"),
    ):
        if not base.is_dir():
            continue
        for relative in fsutil.iter_files(base):
            records.append(_file_record(base, relative))
            records[-1]["path"] = label + "/" + relative.as_posix()
    records.sort(key=lambda item: item["path"])
    return records


def build_manifest(cfg: config.ProjectConfig, artifact_paths: Iterable[Path]) -> Dict[str, object]:
    artifacts = []
    for path in sorted(Path(item) for item in artifact_paths):
        artifacts.append(
            {
                "name": path.name,
                "sha256": fsutil.sha256_file(path),
                "size": path.stat().st_size,
            }
        )
    contract = config.read_contract_metadata(cfg)
    return {
        "schema": SCHEMA_VERSION,
        "project": cfg.name,
        "version": cfg.version,
        "dist_name": cfg.dist_name,
        "requires_python": cfg.requires_python,
        "contract_version": contract["contract_version"],
        "rounding": contract["rounding"],
        "generator": {"name": "releasekit", "schema": SCHEMA_VERSION},
        "source_date_epoch": fsutil.FIXED_EPOCH,
        "artifacts": artifacts,
        "sources": source_records(cfg.root),
    }


def manifest_bytes(manifest: Dict[str, object]) -> bytes:
    return canonical_json(manifest).encode("utf-8")


def sha256sums_text(manifest: Dict[str, object], manifest_sha256: str) -> str:
    lines = [
        "%s  %s" % (artifact["sha256"], artifact["name"])
        for artifact in manifest["artifacts"]
    ]
    lines.append("%s  %s" % (manifest_sha256, MANIFEST_NAME))
    return "\n".join(sorted(lines)) + "\n"


def verify_manifest(manifest: Dict[str, object], directory: Path) -> List[str]:
    """Return a list of human-readable problems with *directory* contents."""

    problems: List[str] = []
    if manifest.get("schema") != SCHEMA_VERSION:
        problems.append("manifest schema is %r, expected %d" % (manifest.get("schema"), SCHEMA_VERSION))
    for field in ("project", "version", "artifacts"):
        if field not in manifest:
            problems.append("manifest is missing field %r" % field)
    if problems:
        return problems

    directory = Path(directory)
    listed = set()
    for artifact in manifest["artifacts"]:
        name = artifact.get("name")
        if not isinstance(name, str) or "/" in name or "\\" in name:
            problems.append("unsafe artifact name: %r" % (name,))
            continue
        listed.add(name)
        path = directory / name
        if not path.is_file():
            problems.append("missing artifact: %s" % name)
            continue
        actual_hash = fsutil.sha256_file(path)
        if actual_hash != artifact.get("sha256"):
            problems.append(
                "artifact hash mismatch: %s (manifest %s, actual %s)"
                % (name, artifact.get("sha256"), actual_hash)
            )
        if path.stat().st_size != artifact.get("size"):
            problems.append(
                "artifact size mismatch: %s (manifest %s, actual %s)"
                % (name, artifact.get("size"), path.stat().st_size)
            )

    stale = sorted(
        path.name
        for path in directory.iterdir()
        if path.is_file()
        and path.name not in listed
        and path.name not in ALLOWED_EXTRA_FILES
        and not path.name.startswith(".")
    )
    if stale:
        problems.append("unexpected stale artifact(s): %s" % ", ".join(stale))
    return problems


def _index(records) -> Dict[str, Dict[str, object]]:
    return {record["path"]: record for record in records}


def _changed_keys(old: Dict[str, Dict], new: Dict[str, Dict]) -> Tuple[List[str], List[str], List[str]]:
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    changed = sorted(
        key
        for key in set(old) & set(new)
        if old[key].get("sha256") != new[key].get("sha256")
    )
    return added, removed, changed


def compare_manifests(old: Dict[str, object], new: Dict[str, object]) -> Dict[str, object]:
    """Explain the differences between two manifests.

    ``reproducible`` is true only when the artifact set and all input hashes are
    identical, which is what a repeated build in the same environment must
    achieve.
    """

    old_artifacts = {item["name"]: item for item in old.get("artifacts", [])}
    new_artifacts = {item["name"]: item for item in new.get("artifacts", [])}
    added_artifacts, removed_artifacts, changed_artifacts = _changed_keys(
        old_artifacts, new_artifacts
    )

    old_sources = _index(old.get("sources", []))
    new_sources = _index(new.get("sources", []))
    added_sources, removed_sources, changed_sources = _changed_keys(old_sources, new_sources)

    metadata_fields = ("version", "requires_python", "contract_version", "rounding", "schema")
    metadata_changes = {
        field: {"old": old.get(field), "new": new.get(field)}
        for field in metadata_fields
        if old.get(field) != new.get(field)
    }

    explanations: List[str] = []
    if removed_artifacts or added_artifacts:
        explanations.append(
            "artifact set changed (added: %s; removed: %s); check the version and package list"
            % (", ".join(added_artifacts) or "-", ", ".join(removed_artifacts) or "-")
        )
    if changed_artifacts:
        explanations.append(
            "artifact bytes changed (%s); inspect the source/fixture diffs below"
            % ", ".join(changed_artifacts)
        )
    if changed_sources or added_sources or removed_sources:
        explanations.append(
            "inputs changed (added: %s; removed: %s; modified: %s)"
            % (
                ", ".join(added_sources) or "-",
                ", ".join(removed_sources) or "-",
                ", ".join(changed_sources) or "-",
            )
        )
    if metadata_changes:
        explanations.append(
            "release metadata changed (%s)" % ", ".join(sorted(metadata_changes))
        )
    if not explanations:
        explanations.append("no differences: repeated build is byte-for-byte reproducible")

    return {
        "old_version": old.get("version"),
        "new_version": new.get("version"),
        "reproducible": not (
            added_artifacts
            or removed_artifacts
            or changed_artifacts
            or added_sources
            or removed_sources
            or changed_sources
        ),
        "artifacts": {
            "added": added_artifacts,
            "removed": removed_artifacts,
            "changed": [
                {
                    "name": name,
                    "old_sha256": old_artifacts[name].get("sha256"),
                    "new_sha256": new_artifacts[name].get("sha256"),
                }
                for name in changed_artifacts
            ],
        },
        "sources": {
            "added": added_sources,
            "removed": removed_sources,
            "changed": [
                {
                    "path": path,
                    "old_sha256": old_sources[path].get("sha256"),
                    "new_sha256": new_sources[path].get("sha256"),
                }
                for path in changed_sources
            ],
        },
        "metadata": metadata_changes,
        "explanations": explanations,
    }
