"""Single source of truth for release metadata.

``pyproject.toml`` declares *what the project is* (name, version, supported
runtimes, packages).  Build scripts never hard-code that information: they read
it here so a version bump cannot diverge between metadata and artifacts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple

from . import toml_compat

PROJECT_NAME = "assessment-score-equating"
DIST_NAME = "assessment_score_equating"


class ConfigError(ValueError):
    """Raised when pyproject.toml is missing or inconsistent."""


@dataclass(frozen=True)
class ProjectConfig:
    root: Path
    name: str
    version: str
    requires_python: str
    packages: Tuple[str, ...]
    supported_pythons: Tuple[str, ...]
    data_fixtures: Tuple[str, ...]
    source_dir: Path

    @property
    def dist_name(self) -> str:
        return self.name.replace("-", "_")

    @property
    def wheel_name(self) -> str:
        return "%s-%s-py3-none-any.whl" % (self.dist_name, self.version)

    @property
    def sdist_name(self) -> str:
        return "%s-%s.tar.gz" % (self.dist_name, self.version)


def _require_table(data, name):
    value = data.get(name)
    if not isinstance(value, dict):
        raise ConfigError("pyproject.toml: missing [%s]" % name)
    return value


def _require_str(table, key, where):
    value = table.get(key)
    if not isinstance(value, str) or not value:
        raise ConfigError("pyproject.toml: %s.%s must be a non-empty string" % (where, key))
    return value


def load(root) -> ProjectConfig:
    root = Path(root).resolve()
    pyproject = root / "pyproject.toml"
    if not pyproject.is_file():
        raise ConfigError("pyproject.toml not found at %s" % pyproject)
    data = toml_compat.load(pyproject)

    project = _require_table(data, "project")
    name = _require_str(project, "name", "project")
    if name != PROJECT_NAME:
        raise ConfigError(
            "pyproject.toml: project.name is %r, expected %r" % (name, PROJECT_NAME)
        )
    version = _require_str(project, "version", "project")
    if not re.match(r"^[0-9]+(\.[0-9]+){1,3}([a-z0-9.]*)$", version) or "-" in version:
        raise ConfigError("pyproject.toml: project.version %r is not PEP 440-like" % version)
    requires_python = _require_str(project, "requires-python", "project")

    packages = []
    tool = data.get("tool", {})
    releasekit = tool.get("releasekit", {}) if isinstance(tool, dict) else {}
    if not isinstance(releasekit, dict):
        raise ConfigError("pyproject.toml: [tool.releasekit] must be a table")

    package_table = releasekit.get("packages", [])
    if package_table:
        if not isinstance(package_table, list) or not all(
            isinstance(item, str) and item for item in package_table
        ):
            raise ConfigError("pyproject.toml: tool.releasekit.packages must be a list of names")
        packages = list(package_table)
    else:
        packages = [DIST_NAME]

    supported = releasekit.get("supported-pythons", [])
    if not isinstance(supported, list) or len(supported) < 2 or not all(
        isinstance(item, str) and item for item in supported
    ):
        raise ConfigError(
            "pyproject.toml: tool.releasekit.supported-pythons must list at least two interpreters"
        )

    fixtures = releasekit.get("package-fixtures", [])
    if not isinstance(fixtures, list) or not all(
        isinstance(item, str) and item for item in fixtures
    ):
        raise ConfigError("pyproject.toml: tool.releasekit.package-fixtures must be a list")

    source_dir = root / "src"
    for package in packages:
        if not (source_dir / package / "__init__.py").is_file():
            raise ConfigError(
                "pyproject.toml: package %r not found under %s" % (package, source_dir)
            )
    for fixture in fixtures:
        if not (root / fixture).is_file():
            raise ConfigError("pyproject.toml: packaged fixture not found: %s" % fixture)

    return ProjectConfig(
        root=root,
        name=name,
        version=version,
        requires_python=requires_python,
        packages=tuple(packages),
        supported_pythons=tuple(supported),
        data_fixtures=tuple(fixtures),
        source_dir=source_dir,
    )


def read_contract_metadata(cfg: ProjectConfig) -> dict:
    """Read contract constants from source without importing project code."""

    contract_path = cfg.source_dir / cfg.packages[0] / "contract.py"
    constants = {}
    if contract_path.is_file():
        import ast

        tree = ast.parse(contract_path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target = node.targets[0]
                if isinstance(target, ast.Name) and target.id in (
                    "CONTRACT_VERSION",
                    "ROUNDING_RULE",
                ):
                    constants[target.id] = ast.literal_eval(node.value)
    return {
        "contract_version": constants.get("CONTRACT_VERSION"),
        "rounding": constants.get("ROUNDING_RULE"),
    }
