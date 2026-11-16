"""Offline, dependency-free release tooling for this project.

The package is importable on every supported runtime without third-party
packages.  It is deliberately split into:

* ``config``   - reads the single source of truth (``pyproject.toml``).
* ``fsutil``   - hashing, deterministic archives, safe extraction.
* ``backend``  - PEP 517 sdist/wheel construction, no network, no clock.
* ``build``    - staging, reproducibility comparison, failure cleanup.
* ``verify``   - release verification across interpreters and environments.
* ``publish``  - verified, atomic, rollback-capable release publication.
"""

__all__ = ["config", "fsutil", "backend", "build", "verify", "publish"]
