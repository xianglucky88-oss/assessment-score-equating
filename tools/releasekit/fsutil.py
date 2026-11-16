"""Deterministic filesystem helpers: hashing, archives, safe extraction.

Every archive written here has normalised metadata (mtime, uid/gid, uname/gname,
permission bits) so that two builds from identical inputs produce identical
bytes on any supported interpreter.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import os
import stat
import tarfile
import zipfile
from pathlib import Path
from typing import Iterable, List, Tuple
# Timestamp used by every generated archive and generated metadata file.
# 2020-01-01T00:00:00Z: a fixed, tz-independent point in time.
FIXED_EPOCH = 1577836800
FIXED_DATE_TIME = "2020-01-01T00:00:00Z"

FILE_MODE = 0o644
EXEC_MODE = 0o755
DIR_MODE = 0o755

EXCLUDED_DIR_NAMES = frozenset(
    {"__pycache__", ".git", ".hg", ".svn", ".mypy_cache", ".ruff_cache", ".pytest_cache"}
)
EXCLUDED_DIR_PREFIXES = ("dist", "build", ".releasekit", ".venv")
EXCLUDED_FILE_SUFFIXES = (".pyc", ".pyo", ".so", ".pyd")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path) -> str:
    digest = hashlib.sha256()
    with open(str(path), "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_mode(path: Path) -> int:
    """Return the normalised permission bits for *path*.

    Executable bits are preserved (scripts must stay runnable); group/other
    bits are normalised so the build user's umask cannot leak into the archive.
    """

    if path.is_dir():
        return DIR_MODE
    mode = path.stat().st_mode
    return EXEC_MODE if mode & stat.S_IXUSR else FILE_MODE


def is_excluded_file(name: str) -> bool:
    """Return True for files that must never enter an artifact.

    Only exact names and bytecode suffixes are matched here.  Prefix matching
    is reserved for directories: applying it to files would silently drop
    legitimate modules such as ``releasekit/build.py``.
    """

    return name in EXCLUDED_DIR_NAMES or name.endswith(EXCLUDED_FILE_SUFFIXES)


def is_excluded_dir(name: str) -> bool:
    return name in EXCLUDED_DIR_NAMES or name.startswith(EXCLUDED_DIR_PREFIXES)


def iter_files(base: Path) -> List[Path]:
    """Return sorted, relative files below *base*, skipping build noise."""

    base = Path(base)
    collected: List[Path] = []
    for dirpath, dirnames, filenames in os.walk(str(base)):
        rel_dir = Path(dirpath).relative_to(base)
        dirnames[:] = sorted(name for name in dirnames if not is_excluded_dir(name))
        for filename in sorted(filenames):
            if is_excluded_file(filename):
                continue
            collected.append(Path(dirpath).relative_to(base) / filename)
    collected.sort(key=lambda item: item.as_posix())
    return collected


def normalise_newlines(data: bytes) -> bytes:
    """Collapse CRLF to LF so checkouts with autocrlf produce identical files."""

    return data.replace(b"\r\n", b"\n")


def read_normalised(path: Path) -> bytes:
    return normalise_newlines(path.read_bytes())


def make_targz(entries: Iterable[Tuple[str, bytes, int]]) -> bytes:
    """Create a deterministic .tar.gz from (name, data, mode) tuples."""

    tar_buffer = io.BytesIO()
    with tarfile.open(fileobj=tar_buffer, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for name, data, mode in entries:
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            info.mode = mode
            info.mtime = FIXED_EPOCH
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.type = tarfile.REGTYPE
            tar.addfile(info, io.BytesIO(data))
    gz_buffer = io.BytesIO()
    with gzip.GzipFile(
        fileobj=gz_buffer, mode="wb", compresslevel=9, mtime=0, filename=""
    ) as gz:
        gz.write(tar_buffer.getvalue())
    return gz_buffer.getvalue()


def make_zip(entries: Iterable[Tuple[str, bytes, int]]) -> bytes:
    """Create a deterministic wheel (zip) from (name, data, mode) tuples."""

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for name, data, mode in entries:
            info = zipfile.ZipInfo(filename=name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | mode) << 16
            info.create_system = 3  # Unix, so external_attr is interpreted
            zf.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    return buffer.getvalue()


def extract_zip_safely(data: bytes, destination: Path) -> None:
    """Extract *data* into *destination*, rejecting traversal and symlinks."""

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        members = zf.infolist()
        names = set()
        for member in members:
            _check_member_name(member.filename)
            if member.filename in names:
                raise ValueError("duplicate archive member: %s" % member.filename)
            names.add(member.filename)
            if member.is_dir():
                continue
            target = destination / member.filename
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as source, open(str(target), "wb") as sink:
                sink.write(source.read())


def extract_targz_safely(data: bytes, destination: Path) -> None:
    """Extract *data* into *destination*, rejecting traversal, links, devices."""

    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for member in tar:
            _check_member_name(member.name)
            if not member.isfile():
                if member.isdir():
                    continue
                raise ValueError("unsafe archive member type: %s" % member.name)
            target = destination / member.name
            target.parent.mkdir(parents=True, exist_ok=True)
            source = tar.extractfile(member)
            if source is None:  # pragma: no cover - defensive
                raise ValueError("unreadable archive member: %s" % member.name)
            with open(str(target), "wb") as sink:
                sink.write(source.read())


def _check_member_name(name: str) -> None:
    if not name or name.startswith("/") or "\\" in name or ":" in name:
        raise ValueError("unsafe archive member path: %r" % name)
    parts = name.split("/")
    if any(part in ("", ".", "..") for part in parts[:-1]) and name != parts[-1]:
        raise ValueError("unsafe archive member path: %r" % name)
    if ".." in parts:
        raise ValueError("unsafe archive member path: %r" % name)


def write_atomic(path: Path, data: bytes) -> None:
    """Write *data* to *path* via a same-directory temporary file + rename."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / (".%s.tmp-%d" % (path.name, os.getpid()))
    try:
        with open(str(tmp), "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(tmp), str(path))
    finally:
        if tmp.exists():
            tmp.unlink()


def write_text_atomic(path: Path, text: str) -> None:
    write_atomic(Path(path), text.encode("utf-8"))


def copy_atomic(source: Path, destination: Path) -> None:
    write_atomic(Path(destination), Path(source).read_bytes())


def remove_tree(path: Path) -> None:
    """Best-effort removal that never follows symlinks out of *path*."""

    path = Path(path)
    if not path.exists() and not path.is_symlink():
        return
    if path.is_symlink() or path.is_file():
        path.unlink()
        return
    for dirpath, dirnames, filenames in os.walk(str(path), topdown=False):
        for filename in filenames:
            target = Path(dirpath) / filename
            if target.is_symlink() or target.is_file():
                target.unlink()
        for dirname in dirnames:
            target = Path(dirpath) / dirname
            if target.is_symlink():
                target.unlink()
            else:
                try:
                    target.rmdir()
                except OSError:
                    pass
    path.rmdir()
