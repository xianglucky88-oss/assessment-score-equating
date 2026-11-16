"""Dependency-free PEP 517 backend.

The backend implements the hooks required by PEP 517 while staying importable
by the build scripts directly (``backend.build_wheel(...)``).  It performs *no*
network access, reads no clock, and writes no bytecode, so building twice from
identical sources produces identical artifacts.

Design boundary: this module only turns a source tree into bytes.  Staging,
verification, comparison and publication live in sibling modules.
"""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path

from . import config, fsutil

WHEEL_CONTENT_TYPE = "text/x-python"

WHEEL_METADATA = """\
Wheel-Version: 1.0
Generator: releasekit ({generator})
Root-Is-Purelib: true
Tag: py3-none-any
"""

METADATA_TEMPLATE = """\
Metadata-Version: 2.1
Name: {name}
Version: {version}
Summary: Score multiple exam forms with void-item and revision-aware scale rules.
Requires-Python: {requires_python}
"""


def get_requires_for_build_wheel(config_settings=None):
    return []


def get_requires_for_build_sdist(config_settings=None):
    return []


def _project_root(root=None) -> Path:
    if root is not None:
        return Path(root).resolve()
    return Path(__file__).resolve().parents[2]


def _source_entries(cfg: config.ProjectConfig):
    """Yield (relative_name, data, mode) for every packaged source file."""

    for package in cfg.packages:
        package_dir = cfg.source_dir / package
        for relative in fsutil.iter_files(package_dir):
            data = fsutil.read_normalised(package_dir / relative)
            mode = fsutil.canonical_mode(package_dir / relative)
            yield (package + "/" + relative.as_posix()), data, mode


def _fixture_entries(cfg: config.ProjectConfig):
    """Package declared fixtures under ``<package>/data/`` for offline use."""

    for relative in cfg.data_fixtures:
        source = cfg.root / relative
        name = cfg.packages[0] + "/data/" + Path(relative).name
        yield (name, fsutil.read_normalised(source), fsutil.FILE_MODE)


def _dist_info_files(cfg: config.ProjectConfig):
    metadata = METADATA_TEMPLATE.format(
        name=cfg.name,
        version=cfg.version,
        requires_python=cfg.requires_python,
    ).encode("utf-8")
    wheel = WHEEL_METADATA.format(generator="releasekit").encode("utf-8")
    return [
        ("%s-%s.dist-info/METADATA" % (cfg.dist_name, cfg.version), metadata, fsutil.FILE_MODE),
        ("%s-%s.dist-info/WHEEL" % (cfg.dist_name, cfg.version), wheel, fsutil.FILE_MODE),
    ]


def _record_line(name: str, data: bytes) -> str:
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=")
    return "%s,sha256=%s,%d" % (name, digest.decode("ascii"), len(data))


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None, root=None):
    """Build a deterministic wheel and return its filename."""

    cfg = config.load(root or _project_root())
    entries = list(_source_entries(cfg)) + list(_fixture_entries(cfg)) + _dist_info_files(cfg)
    entries.sort(key=lambda item: item[0])

    record_name = "%s-%s.dist-info/RECORD" % (cfg.dist_name, cfg.version)
    record_lines = [_record_line(name, data) for name, data, _ in entries]
    record_lines.append("%s,," % record_name)
    record = ("\n".join(record_lines) + "\n").encode("utf-8")
    entries.append((record_name, record, fsutil.FILE_MODE))

    payload = fsutil.make_zip(entries)
    filename = cfg.wheel_name
    fsutil.write_atomic(Path(wheel_directory) / filename, payload)
    return filename


def build_sdist(sdist_directory, config_settings=None, root=None):
    """Build a deterministic sdist and return its filename."""

    cfg = config.load(root or _project_root())
    prefix = "%s-%s" % (cfg.dist_name, cfg.version)
    entries = []
    project_files = ["pyproject.toml", "README.md"]

    for name in project_files:
        path = cfg.root / name
        if not path.is_file():
            raise config.ConfigError("required sdist file missing: %s" % name)
        entries.append(
            (prefix + "/" + name, fsutil.read_normalised(path), fsutil.canonical_mode(path))
        )

    for base, label in ((cfg.source_dir, "src"), (cfg.root / "fixtures", "fixtures"),
                        (cfg.root / "tests", "tests"), (cfg.root / "tools", "tools"),
                        (cfg.root / "scripts", "scripts")):
        if not base.is_dir():
            continue
        for relative in fsutil.iter_files(base):
            data = fsutil.read_normalised(base / relative)
            mode = fsutil.canonical_mode(base / relative)
            entries.append((prefix + "/" + label + "/" + relative.as_posix(), data, mode))

    # PKG-INFO makes the sdist self-describing even without a build frontend.
    pkg_info = METADATA_TEMPLATE.format(
        name=cfg.name, version=cfg.version, requires_python=cfg.requires_python
    ).encode("utf-8")
    entries.append((prefix + "/PKG-INFO", pkg_info, fsutil.FILE_MODE))

    entries.sort(key=lambda item: item[0])
    payload = fsutil.make_targz(entries)
    filename = cfg.sdist_name
    fsutil.write_atomic(Path(sdist_directory) / filename, payload)
    return filename
