"""Release verification: build reproducibility + runtime/contract matrix.

Verification is intentionally performed against *extracted artifacts* rather
than the source tree.  The wheel is unzipped into a clean ``site`` directory and
the sdist into a clean ``prefix``; harness processes are launched with
``python -I`` so neither the source checkout nor environment variables can leak
into ``sys.path``.

The verifier answers four separate questions and records each answer:

1. Does a repeated build reproduce the same bytes (and, if not, why)?
2. Does every supported interpreter produce identical output for the pinned
   cases (void items, absent/violation, empty input, rounding, ordering)?
3. Is the CLI output independent of cwd, locale, hash seed, TZ and PYTHONPATH?
4. Do imports, unit tests and the input contract behave identically everywhere?
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from . import build as build_mod
from . import config, fsutil, harness, manifest as manifest_mod

REPORT_NAME = "VERIFICATION.json"
DEFAULT_TIMEOUT = 120


class VerificationError(RuntimeError):
    pass


@dataclass
class StepResult:
    name: str
    python: str
    status: str  # "pass" | "fail" | "skip"
    detail: str = ""
    stdout: str = ""
    stderr: str = ""

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "python": self.python,
            "status": self.status,
            "detail": self.detail,
            "stdout": self.stdout[-4000:],
            "stderr": self.stderr[-8000:],
        }


@dataclass
class VerificationReport:
    project: str
    version: str
    build_id: str
    interpreters: List[Dict[str, str]] = field(default_factory=list)
    steps: List[StepResult] = field(default_factory=list)
    reproducibility: Optional[dict] = None
    clusters: Dict[str, dict] = field(default_factory=dict)
    artifacts: List[dict] = field(default_factory=list)
    manifest_sha256: str = ""
    candidate: str = ""
    not_installed: List[str] = field(default_factory=list)
    complete: bool = False
    abort_reason: str = ""

    REQUIRED_CLUSTERS = ("pipeline", "contract", "cli")
    REQUIRED_GOLDENS = ("outputs", "contract_errors", "cli_stdout")

    @property
    def passed(self) -> bool:
        # ``complete`` is set only at the very end of a full run, so an aborted
        # verification (exception, timeout) can never be mistaken for a pass.
        if not self.complete:
            return False
        if not self.steps or any(step.status == "fail" for step in self.steps):
            return False
        if self.reproducibility is None:
            return False
        if self.reproducibility.get("reproducible") is False:
            return False
        for name in self.REQUIRED_CLUSTERS:
            if not self.clusters.get(name, {}).get("consistent"):
                return False
        for name in self.REQUIRED_GOLDENS:
            if not any(
                step.name == "golden[%s]" % name and step.status == "pass"
                for step in self.steps
            ):
                return False
        return True

    def summary(self) -> dict:
        return {
            "project": self.project,
            "version": self.version,
            "build_id": self.build_id,
            "passed": self.passed,
            "complete": self.complete,
            "abort_reason": self.abort_reason,
            "interpreters": self.interpreters,
            "not_installed": self.not_installed,
            "steps": len(self.steps),
            "failures": [step.as_dict() for step in self.steps if step.status == "fail"],
            "reproducibility": self.reproducibility,
            "clusters": self.clusters,
            "artifacts": self.artifacts,
            "manifest_sha256": self.manifest_sha256,
        }

    def as_dict(self) -> dict:
        data = self.summary()
        data["steps"] = [step.as_dict() for step in self.steps]
        return data


# --------------------------------------------------------------------------
# interpreter discovery


def _version_of(python: str) -> Optional[str]:
    try:
        output = subprocess.check_output(
            [python, "-I", "-c", "import sys;print('%d.%d.%d' % sys.version_info[:3])"],
            stderr=subprocess.STDOUT,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return output.decode("utf-8", "replace").strip()


def _resolve_requested(requested: Sequence[str]) -> List[str]:
    resolved: List[str] = []
    missing: List[str] = []
    seen = set()
    for command in requested:
        if os.path.sep in command or (os.path.altsep and os.path.altsep in command):
            path = command if os.path.isfile(command) and os.access(command, os.X_OK) else None
        else:
            path = shutil.which(command)
        if path is None:
            missing.append(command)
            continue
        real = os.path.realpath(path)
        if real in seen:
            continue
        seen.add(real)
        resolved.append(path)
    return resolved, missing


def discover_interpreters(
    cfg: config.ProjectConfig,
    override: Optional[str] = None,
    strict: bool = False,
) -> tuple:
    """Resolve configured interpreter commands to executable paths.

    Duplicate paths (e.g. ``python3`` and ``python3.11`` pointing at the same
    binary) are collapsed so the "two runtimes" requirement is real.  Interpreters
    that are configured but not installed are returned separately so the report
    can state exactly which part of the matrix was exercised; the caller still
    enforces the minimum count.
    """

    if override:
        requested = [item.strip() for item in override.split(",") if item.strip()]
        strict = True  # an explicit list is a promise, not a wish
    else:
        requested = [item.strip() for item in cfg.supported_pythons]
    resolved, missing = _resolve_requested(requested)
    if missing and strict:
        raise VerificationError(
            "configured interpreter(s) not found: %s (override with --pythons or RELEASEKIT_PYTHONS)"
            % ", ".join(missing)
        )
    return resolved, missing


def min_python_version(spec: str) -> Optional[tuple]:
    match = re.search(r">=\s*(\d+)\.(\d+)", spec or "")
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _check_requires_python(cfg: config.ProjectConfig, python: str) -> Optional[str]:
    minimum = min_python_version(cfg.requires_python)
    if minimum is None:
        return None
    version = _version_of(python)
    if version is None:
        return "cannot determine interpreter version"
    major, minor, _ = (int(part) for part in version.split("."))
    if (major, minor) < minimum:
        return "interpreter %s does not satisfy requires-python %s" % (
            version,
            cfg.requires_python,
        )
    return None


# --------------------------------------------------------------------------
# process execution


def _base_env(home: Path) -> Dict[str, str]:
    env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": str(home),
        "TMPDIR": str(home / "tmp"),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
    }
    (home / "tmp").mkdir(parents=True, exist_ok=True)
    return env


HOSTILE_ENV_VARIANTS = (
    ("locale-c", {"LANG": "C", "LC_ALL": "C"}),
    ("ioencoding-ascii", {"PYTHONIOENCODING": "ascii"}),
    ("hash-seed-random", {"PYTHONHASHSEED": "random"}),
    ("timezone-pacific", {"TZ": "Pacific/Kiritimati"}),
    ("pythonpath-hostile", {"PYTHONPATH": "/nonexistent/releasekit"}),
    ("utf8-mode-off", {"PYTHONUTF8": "0"}),
)


def _run(
    command: Sequence[str],
    cwd: Path,
    env: Dict[str, str],
    timeout: int = DEFAULT_TIMEOUT,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(command),
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )


def _decode(data: bytes) -> str:
    return data.decode("utf-8", "replace")


# --------------------------------------------------------------------------
# verification


class Verifier:
    def __init__(
        self,
        root,
        pythons: Sequence[str],
        workdir: Optional[Path] = None,
        timeout: int = DEFAULT_TIMEOUT,
        min_runtimes: int = 2,
    ):
        self.cfg = config.load(root)
        self.root = self.cfg.root
        self.pythons = list(pythons)
        if len(self.pythons) < min_runtimes:
            raise VerificationError(
                "verification requires at least %d distinct Python runtimes; found %d (%s). "
                "Configure tool.releasekit.supported-pythons or pass more --pythons."
                % (min_runtimes, len(self.pythons), ", ".join(self.pythons) or "none")
            )
        self.timeout = timeout
        self.report = VerificationReport(
            project=self.cfg.name, version=self.cfg.version, build_id="v" + secrets.token_hex(5)
        )
        self._owns_workdir = workdir is None
        self.workdir = Path(workdir) if workdir else Path(
            tempfile.mkdtemp(prefix="releasekit-verify-")
        )
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.home = self.workdir / "home"
        self.home.mkdir(exist_ok=True)
        self.harness_dir = self.workdir / "harness"
        harness.write_harness(self.harness_dir)
        self.cases = self._load_cases()
        self.goldens = self._load_goldens()

    # -- setup ---------------------------------------------------------

    def _load_cases(self) -> dict:
        pipeline = self.root / "tests" / "contract" / "pipeline_cases.json"
        invalid = self.root / "tests" / "contract" / "invalid_cases.json"
        if not pipeline.is_file() or not invalid.is_file():
            raise VerificationError(
                "contract case files are missing; expected %s and %s" % (pipeline, invalid)
            )
        return {
            "pipeline": json.loads(pipeline.read_text(encoding="utf-8")),
            "invalid": json.loads(invalid.read_text(encoding="utf-8")),
        }

    def _load_goldens(self) -> dict:
        path = self.root / "tests" / "golden" / "expected_outputs.json"
        if not path.is_file():
            raise VerificationError("golden expectations missing: %s" % path)
        return json.loads(path.read_text(encoding="utf-8"))

    def _case_file(self) -> Path:
        """Materialise pipeline cases with fixture payloads inlined."""

        cases = []
        for case in self.cases["pipeline"]["cases"]:
            item = dict(case)
            if item.get("payload") is None and item.get("fixture"):
                item["payload"] = json.loads(
                    (self.root / item["fixture"]).read_text(encoding="utf-8-sig")
                )
            cases.append(item)
        path = self.workdir / "pipeline_cases.json"
        fsutil.write_text_atomic(path, json.dumps({"cases": cases}, sort_keys=True))
        return path

    def _invalid_case_file(self) -> Path:
        path = self.workdir / "invalid_cases.json"
        fsutil.write_text_atomic(
            path, json.dumps(self.cases["invalid"], sort_keys=True)
        )
        return path

    # -- recording -----------------------------------------------------

    def _record(self, step: StepResult) -> StepResult:
        self.report.steps.append(step)
        return step

    def _fail(self, name: str, python: str, detail: str, stdout="", stderr="") -> StepResult:
        return self._record(StepResult(name, python, "fail", detail, stdout, stderr))

    def _pass(self, name: str, python: str, detail: str = "", stdout="", stderr="") -> StepResult:
        return self._record(StepResult(name, python, "pass", detail, stdout, stderr))

    # -- phase 1: reproducibility --------------------------------------

    def check_reproducibility(self) -> Path:
        """Build once per interpreter and require identical manifests.

        Proves both that repeated builds are byte-stable and that the
        interpreter doing the building cannot change artifact bytes.  The first
        build is the candidate used for the rest of the matrix.
        """

        dist = self.workdir / "dist"
        outcomes = []
        for index, python in enumerate(self.pythons, start=1):
            build_id = "b%d%s" % (index, secrets.token_hex(3))
            proc = self._run_python(
                python,
                [
                    str(self.harness_dir / "run_build.py"),
                    "--tools", str(self.root / "tools"),
                    "--root", str(self.root),
                    "--dist", str(dist),
                    "--build-id", build_id,
                ],
                cwd=self.workdir,
                env=_base_env(self.home),
            )
            if proc.returncode != 0:
                self._fail(
                    "build",
                    python,
                    "build %s failed with exit code %d" % (build_id, proc.returncode),
                    _decode(proc.stdout),
                    _decode(proc.stderr),
                )
                raise VerificationError("build failed on %s; see logs/report" % python)
            outcome = json.loads(_decode(proc.stdout))
            outcomes.append(outcome)
            self._pass(
                "build",
                python,
                "built %s on %s" % (outcome["build_id"], _version_of(python)),
            )

        first = build_mod.load_manifest(Path(outcomes[0]["directory"]) / manifest_mod.MANIFEST_NAME)
        comparisons = []
        for python, outcome in zip(self.pythons[1:], outcomes[1:]):
            other = build_mod.load_manifest(Path(outcome["directory"]) / manifest_mod.MANIFEST_NAME)
            comparison = manifest_mod.compare_manifests(first, other)
            comparison["compared_python"] = python
            comparisons.append(comparison)
            if not comparison["reproducible"]:
                self._fail("reproducibility", python, "; ".join(comparison["explanations"]))

        reproducible = all(item["reproducible"] for item in comparisons)
        explanations = []
        for item in comparisons:
            explanations.extend(item["explanations"])
        self.report.reproducibility = {
            "reproducible": reproducible,
            "builds": len(outcomes),
            "comparisons": comparisons,
            "explanations": explanations,
        }
        if not reproducible:
            raise VerificationError("cross-interpreter builds were not byte-identical")
        self._pass(
            "reproducibility",
            "-",
            "%d builds across %d interpreter(s) produced identical bytes"
            % (len(outcomes), len(self.pythons)),
        )
        self.report.manifest_sha256 = fsutil.sha256_file(
            Path(outcomes[0]["directory"]) / manifest_mod.MANIFEST_NAME
        )
        self.report.artifacts = first["artifacts"]
        return Path(outcomes[0]["directory"])

    # -- extraction ----------------------------------------------------

    def check_contents(self, candidate: Path) -> None:
        """Assert required modules and fixtures are present in both artifacts.

        Guards against "silent omission" bugs (e.g. an exclusion rule dropping a
        tool module) that hash checks alone cannot detect, because the manifest
        faithfully describes whatever was produced.
        """

        import tarfile
        import zipfile

        wheel = candidate / self.cfg.wheel_name
        sdist = candidate / self.cfg.sdist_name
        package = self.cfg.packages[0]

        with zipfile.ZipFile(wheel) as zf:
            wheel_names = set(zf.namelist())
        problems = []
        for module in ("cli", "engine", "models", "parser", "pipeline", "report", "contract"):
            expected = "%s/%s.py" % (package, module)
            if expected not in wheel_names:
                problems.append("wheel is missing %s" % expected)
        for fixture in self.cfg.data_fixtures:
            expected = "%s/data/%s" % (package, Path(fixture).name)
            if expected not in wheel_names:
                problems.append("wheel is missing fixture %s" % expected)
        for name in wheel_names:
            if name.endswith(".pyc") or "/__pycache__/" in name or name.startswith((".", "/")):
                problems.append("wheel contains forbidden entry %s" % name)
        dist_info = [name for name in wheel_names if name.endswith(".dist-info/RECORD")]
        if len(dist_info) != 1:
            problems.append("wheel must contain exactly one RECORD file")

        with tarfile.open(sdist, mode="r:gz") as tf:
            sdist_names = set(tf.getnames())
        prefix = "%s-%s/" % (self.cfg.dist_name, self.cfg.version)
        required = [prefix + "pyproject.toml", prefix + "PKG-INFO"]
        required += ["%ssrc/%s/%s.py" % (prefix, package, module) for module in
                     ("cli", "engine", "models", "parser", "pipeline", "report", "contract")]
        required += [prefix + "tests/contract/pipeline_cases.json",
                     prefix + "tests/golden/expected_outputs.json",
                     prefix + "tools/build_release.py",
                     prefix + "tools/verify_release.py",
                     prefix + "tools/publish_release.py",
                     prefix + "tools/releasekit/build.py",
                     prefix + "tools/releasekit/verify.py",
                     prefix + "tools/releasekit/publish.py"]
        for name in required:
            if name not in sdist_names:
                problems.append("sdist is missing %s" % name)
        for name in sdist_names:
            if not name.startswith(prefix) or name.endswith(".pyc"):
                problems.append("sdist contains forbidden entry %s" % name)

        if problems:
            self._fail("contents", "-", "; ".join(problems[:12]), stderr="\n".join(problems))
            raise VerificationError("artifact contents failed contract checks")
        self._pass(
            "contents",
            "-",
            "wheel and sdist contain all required modules, fixtures and tooling",
        )

    def extract(self, candidate: Path) -> Dict[str, Path]:
        wheel = candidate / self.cfg.wheel_name
        sdist = candidate / self.cfg.sdist_name
        if not wheel.is_file() or not sdist.is_file():
            raise VerificationError(
                "candidate %s does not contain %s and %s"
                % (candidate, self.cfg.wheel_name, self.cfg.sdist_name)
            )
        site = self.workdir / "site"
        prefix = self.workdir / "prefix"
        for target in (site, prefix):
            fsutil.remove_tree(target)
            target.mkdir(parents=True)
        fsutil.extract_zip_safely(wheel.read_bytes(), site)
        fsutil.extract_targz_safely(sdist.read_bytes(), prefix)

        sdist_root = prefix / ("%s-%s" % (self.cfg.dist_name, self.cfg.version))
        if not sdist_root.is_dir():
            raise VerificationError("sdist does not contain %s" % sdist_root.name)
        return {"site": site, "prefix": prefix, "sdist_root": sdist_root}

    # -- phase 2: runtime matrix ---------------------------------------

    def _run_python(self, python, args, cwd, env, timeout=None):
        return _run([python, "-I"] + list(args), cwd, env, timeout or self.timeout)

    def check_imports(self, python, paths: Dict[str, Path]) -> None:
        module_names = self.cfg.packages[0]
        wheel_modules = ["cli", "engine", "models", "parser", "pipeline", "report", "contract"]
        for label, site in (("wheel", paths["site"]), ("sdist", paths["sdist_root"] / "src")):
            proc = self._run_python(
                python,
                [
                    str(self.harness_dir / "run_import.py"),
                    "--site", str(site),
                    "--package", module_names,
                    "--modules", ",".join(wheel_modules),
                ],
                cwd=self.workdir,
                env=_base_env(self.home),
            )
            stdout = _decode(proc.stdout)
            stderr = _decode(proc.stderr)
            if proc.returncode != 0:
                self._fail("import[%s]" % label, python, "import failed", stdout, stderr)
                continue
            data = json.loads(stdout)
            if data.get("version") != self.cfg.version:
                self._fail(
                    "import[%s]" % label,
                    python,
                    "artifact version %r != pyproject version %r"
                    % (data.get("version"), self.cfg.version),
                )
                continue
            if data.get("network_modules"):
                self._fail(
                    "import[%s]" % label,
                    python,
                    "artifact imports network modules: %s" % data["network_modules"],
                )
                continue
            self._pass("import[%s]" % label, python, "package %s imported" % data["package"])

    def _pipeline_outputs(self, python, site: Path, label: str):
        proc = self._run_python(
            python,
            [
                str(self.harness_dir / "run_pipeline.py"),
                "--site", str(site),
                "--cases", str(self._case_file()),
                "--offline-guard",
            ],
            cwd=self.workdir,
            env=_base_env(self.home),
        )
        stdout, stderr = _decode(proc.stdout), _decode(proc.stderr)
        if proc.returncode != 0:
            self._fail("pipeline[%s]" % label, python, "pipeline run failed", stdout, stderr)
            return None
        data = json.loads(stdout)
        if data.get("network_modules"):
            self._fail(
                "pipeline[%s]" % label,
                python,
                "network modules imported during scoring: %s" % data["network_modules"],
                stdout,
                stderr,
            )
            return None
        self._pass(
            "pipeline[%s]" % label,
            python,
            "%d cases scored" % len(data["outputs"]),
            stdout,
        )
        return data

    def check_pipeline(self, python, paths: Dict[str, Path]) -> Optional[dict]:
        wheel = self._pipeline_outputs(python, paths["site"], "wheel")
        sdist = self._pipeline_outputs(python, paths["sdist_root"] / "src", "sdist")
        if wheel is None or sdist is None:
            return None
        if wheel["outputs"] != sdist["outputs"]:
            self._fail(
                "pipeline[wheel==sdist]", python, "wheel and sdist outputs differ"
            )
            return None
        self._pass("pipeline[wheel==sdist]", python, "wheel and sdist agree")
        return wheel["outputs"]

    def check_contract(self, python, paths: Dict[str, Path]) -> Optional[list]:
        proc = self._run_python(
            python,
            [
                str(self.harness_dir / "run_contract_errors.py"),
                "--site", str(paths["site"]),
                "--cases", str(self._invalid_case_file()),
            ],
            cwd=self.workdir,
            env=_base_env(self.home),
        )
        stdout, stderr = _decode(proc.stdout), _decode(proc.stderr)
        if proc.returncode != 0:
            self._fail("contract", python, "contract harness failed", stdout, stderr)
            return None
        results = json.loads(stdout)["results"]
        problems = []
        for result in results:
            if result["raised"] != "ValueError":
                problems.append(
                    "%s raised %r instead of ValueError" % (result["name"], result["raised"])
                )
        if problems:
            self._fail("contract", python, "; ".join(problems), stdout, stderr)
            return None
        self._pass("contract", python, "%d invalid inputs rejected" % len(results), stdout)
        return results

    def check_cli(self, python, paths: Dict[str, Path]) -> Optional[Dict[str, bytes]]:
        env = _base_env(self.home)
        fixture = self.root / "fixtures" / "input.json"
        fixture_copy = self.workdir / "elsewhere" / "input.json"
        fixture_copy.parent.mkdir(parents=True, exist_ok=True)
        fsutil.write_atomic(fixture_copy, fixture.read_bytes())
        pdf_path = self.workdir / "no-such-input.json"
        scenario_cwds = {
            "cwd-temp": self.workdir,
            "cwd-root": self.root,
            "cwd-elsewhere": fixture_copy.parent,
        }
        scenario_variants: Dict[str, Dict[str, str]] = {"default": {}}
        scenario_variants.update(dict(HOSTILE_ENV_VARIANTS))

        outputs: Dict[str, Dict[str, bytes]] = {}
        failures: List[str] = []
        for site_label, site in (
            ("wheel", paths["site"]),
            ("sdist", paths["sdist_root"] / "src"),
        ):
            for cwd_label, cwd in scenario_cwds.items():
                for variant_label, extra in scenario_variants.items():
                    run_env = dict(env)
                    run_env.update(extra)
                    label = "cli[%s/%s/%s]" % (site_label, cwd_label, variant_label)
                    proc = self._run_python(
                        python,
                        [
                            str(self.harness_dir / "run_cli.py"),
                            "--site", str(site),
                        ],
                        cwd=cwd,
                        env=run_env,
                    )
                    if proc.returncode != 0:
                        failures.append(
                            "%s failed (exit %d): %s"
                            % (label, proc.returncode, _decode(proc.stderr).strip()[:400])
                        )
                        continue
                    outputs[label] = proc.stdout

            # explicit input path, from a different cwd
            label = "cli[%s/explicit-path]" % site_label
            proc = self._run_python(
                python,
                [
                    str(self.harness_dir / "run_cli.py"),
                    "--site", str(site),
                    "--", str(fixture_copy),
                ],
                cwd=self.workdir,
                env=env,
            )
            if proc.returncode != 0:
                failures.append("%s failed: %s" % (label, _decode(proc.stderr).strip()[:300]))
            else:
                outputs[label] = proc.stdout

            # module mode: python -m assessment_score_equating.cli
            label = "cli[%s/module]" % site_label
            proc = self._run_python(
                python,
                [
                    str(self.harness_dir / "run_cli.py"),
                    "--site", str(site),
                    "--module-mode",
                ],
                cwd=self.workdir,
                env=env,
            )
            if proc.returncode != 0:
                failures.append("%s failed: %s" % (label, _decode(proc.stderr).strip()[:300]))
            else:
                outputs[label] = proc.stdout

            # missing input must be a stable usage error, not a traceback
            label = "cli[%s/missing-input]" % site_label
            proc = self._run_python(
                python,
                [
                    str(self.harness_dir / "run_cli.py"),
                    "--site", str(site),
                    "--", str(pdf_path),
                ],
                cwd=self.workdir,
                env=env,
            )
            if proc.returncode != 2:
                failures.append("%s exit code %d, expected 2" % (label, proc.returncode))
            elif _decode(proc.stdout):
                failures.append("%s wrote to stdout on error" % label)

        if failures:
            self._fail("cli", python, "; ".join(failures[:12]), stderr="\n".join(failures))
            return None

        distinct = set(outputs.values())
        if len(distinct) != 1:
            differing = sorted(
                label for label, data in outputs.items() if data != outputs[sorted(outputs)[0]]
            )
            self._fail(
                "cli[drift]",
                python,
                "CLI output differs across %d run(s): %s"
                % (len(differing), ", ".join(differing[:8])),
            )
            return None
        self._pass(
            "cli", python, "%d scenarios produced byte-identical output" % len(outputs)
        )
        return outputs

    def check_unit_tests(self, python, paths: Dict[str, Path]) -> None:
        proc = self._run_python(
            python,
            [
                str(self.harness_dir / "run_tests.py"),
                "--root", str(paths["sdist_root"]),
                "--tests", str(paths["sdist_root"] / "tests"),
                "--src", str(paths["sdist_root"] / "src"),
            ],
            cwd=paths["sdist_root"],
            env=_base_env(self.home),
        )
        stdout, stderr = _decode(proc.stdout), _decode(proc.stderr)
        if proc.returncode != 0:
            self._fail("unit-tests", python, "unit tests failed", stdout, stderr)
            return
        data = json.loads(stdout)
        self._pass("unit-tests", python, "%d tests passed" % data["tests_run"], stdout)

    # -- clusters ------------------------------------------------------

    def _cluster(self, name: str, values: Dict[str, object]) -> None:
        """Record whether every interpreter produced the same value."""

        if not values:
            self.report.clusters[name] = {"consistent": False, "values": {}}
            self._fail("cluster[%s]" % name, "-", "no interpreter produced a value")
            return
        serialised = {key: json.dumps(value, sort_keys=True) for key, value in values.items()}
        distinct = set(serialised.values())
        consistent = len(distinct) == 1
        self.report.clusters[name] = {
            "consistent": consistent,
            "interpreters": sorted(values),
            "distinct_values": len(distinct),
        }
        if not consistent:
            grouped: Dict[str, List[str]] = {}
            for key, payload in serialised.items():
                grouped.setdefault(payload, []).append(key)
            description = " vs ".join(
                "%s -> %s" % (", ".join(items), key)
                for key, items in sorted(grouped.items(), key=lambda kv: len(kv[1]))
            )
            self._fail(
                "cluster[%s]" % name,
                "-",
                "output differs across interpreters: %s" % description,
            )
        else:
            self._pass(
                "cluster[%s]" % name,
                "-",
                "%d interpreters agree" % len(values),
            )

    def _check_golden(self, name: str, actual: Dict[str, object]) -> None:
        expected = self.goldens.get(name)
        if expected is None:
            self._fail("golden[%s]" % name, "-", "no golden recorded")
            return
        if json.dumps(actual, sort_keys=True) != json.dumps(expected, sort_keys=True):
            diff = _explain_json_diff(expected, actual)
            self._fail("golden[%s]" % name, "-", diff)
            return
        self._pass("golden[%s]" % name, "-", "matches pinned expectation")

    # -- main ----------------------------------------------------------

    def run(self, candidate: Optional[Path] = None) -> VerificationReport:
        if candidate is not None:
            # Invalidate any earlier approval *before* doing anything that can
            # fail.  Otherwise a stale VERIFICATION.json from a previous passing
            # run would still authorise publishing this candidate.
            self._invalidate_candidate_report(Path(candidate).resolve())

        for python in self.pythons:
            problem = _check_requires_python(self.cfg, python)
            if problem:
                self._fail("requires-python", python, problem)
            else:
                self._pass("requires-python", python, "satisfies %s" % self.cfg.requires_python)
            self.report.interpreters.append(
                {"command": python, "version": _version_of(python) or "unknown"}
            )

        built = self.check_reproducibility()

        if candidate is None:
            candidate = built
        else:
            candidate = Path(candidate).resolve()
            manifest = build_mod.load_manifest(candidate / manifest_mod.MANIFEST_NAME)
            problems = manifest_mod.verify_manifest(manifest, candidate)
            if problems:
                self._fail("candidate-manifest", "-", "; ".join(problems))
                raise VerificationError("candidate does not match its manifest")
            self._pass("candidate-manifest", "-", "candidate matches its manifest")

            # An external candidate must also equal a fresh build of these
            # sources; otherwise publishing it would ship unverifiable bytes.
            built_manifest = build_mod.load_manifest(built / manifest_mod.MANIFEST_NAME)
            comparison = manifest_mod.compare_manifests(built_manifest, manifest)
            if not comparison["reproducible"]:
                self._fail("candidate-matches-rebuild", "-", "; ".join(comparison["explanations"]))
                raise VerificationError("candidate differs from a fresh build of the same sources")
            self._pass("candidate-matches-rebuild", "-", "candidate equals a fresh build")

            self.report.manifest_sha256 = fsutil.sha256_file(
                candidate / manifest_mod.MANIFEST_NAME
            )
            self.report.artifacts = manifest["artifacts"]

        paths = self.extract(candidate)
        self.check_contents(candidate)

        pipeline_values: Dict[str, object] = {}
        contract_values: Dict[str, object] = {}
        cli_values: Dict[str, bytes] = {}
        for python in self.pythons:
            version = _version_of(python) or python
            self.check_imports(python, paths)
            outputs = self.check_pipeline(python, paths)
            if outputs is not None:
                pipeline_values[pipelines_key(python, version)] = outputs
            contract = self.check_contract(python, paths)
            if contract is not None:
                contract_values[pipelines_key(python, version)] = contract
            cli = self.check_cli(python, paths)
            if cli is not None:
                cli_values[pipelines_key(python, version)] = {
                    label: _decode(data) for label, data in cli.items()
                }
            self.check_unit_tests(python, paths)

        self._cluster("pipeline", pipeline_values)
        self._cluster("contract", contract_values)
        self._cluster("cli", cli_values)

        if len(pipeline_values) == len(self.pythons):
            first = pipeline_values[pipelines_key(
                self.pythons[0], self.report.interpreters[0]["version"]
            )]
            self._check_golden("outputs", first)
        if len(contract_values) == len(self.pythons):
            first = contract_values[pipelines_key(
                self.pythons[0], self.report.interpreters[0]["version"]
            )]
            self._check_golden("contract_errors", first)
        if len(cli_values) == len(self.pythons):
            first = cli_values[pipelines_key(
                self.pythons[0], self.report.interpreters[0]["version"]
            )]
            golden_cli = self.goldens.get("cli_stdout")
            if golden_cli is None:
                self._fail("golden[cli_stdout]", "-", "no golden recorded")
            elif first.get("cli[wheel/cwd-temp/default]") != golden_cli:
                self._fail(
                    "golden[cli_stdout]",
                    "-",
                    "CLI output does not match pinned expectation\n%s"
                    % _explain_text_diff(golden_cli, first.get("cli[wheel/cwd-temp/default]", "")),
                )
            else:
                self._pass("golden[cli_stdout]", "-", "matches pinned expectation")

        self.report.candidate = str(candidate)
        self.report.complete = True
        self._write_report()
        # Persist the report inside the candidate so publish can find it by
        # convention (VERIFICATION.json is an allowed extra file there).
        try:
            fsutil.write_text_atomic(
                Path(candidate) / "VERIFICATION.json",
                json.dumps(self.report.as_dict(), ensure_ascii=False, indent=2, sort_keys=True)
                + "\n",
            )
        except OSError:
            pass
        return self.report

    def _invalidate_candidate_report(self, candidate: Path) -> str:
        """Withdraw any earlier approval on *candidate*; return prior hash."""

        stale = candidate / "VERIFICATION.json"
        if not stale.is_file():
            return ""
        try:
            existing = json.loads(stale.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            existing = {}
        previous_hash = str(existing.get("manifest_sha256", ""))
        existing["passed"] = False
        existing["complete"] = False
        existing["invalidated"] = "re-verification started; previous approval withdrawn"
        try:
            fsutil.write_text_atomic(
                stale, json.dumps(existing, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
            )
        except OSError:  # pragma: no cover - candidate may be read-only
            pass
        return previous_hash

    def record_abort(self, reason: str) -> None:
        """Persist a report that can never be mistaken for an approval."""

        self.report.complete = False
        self.report.abort_reason = reason
        self.report.steps.append(StepResult("aborted", "-", "fail", reason))
        try:
            self._write_report()
        except OSError:  # pragma: no cover - workdir may be gone
            pass

    def _write_report(self) -> None:
        payload = json.dumps(self.report.as_dict(), ensure_ascii=False, indent=2, sort_keys=True)
        fsutil.write_text_atomic(self.workdir / REPORT_NAME, payload + "\n")

    def cleanup(self) -> None:
        if self._owns_workdir:
            fsutil.remove_tree(self.workdir)


def pipelines_key(python: str, version: str) -> str:
    return "%s (%s)" % (version, python)


def _explain_json_diff(expected, actual) -> str:
    lines = ["golden mismatch"]
    if isinstance(expected, dict) and isinstance(actual, dict):
        for key in sorted(set(expected) | set(actual)):
            if expected.get(key) != actual.get(key):
                lines.append(
                    "  %s: expected %s, got %s"
                    % (key, json.dumps(expected.get(key), sort_keys=True)[:300],
                       json.dumps(actual.get(key), sort_keys=True)[:300])
                )
    else:
        lines.append("  expected %r, got %r" % (expected, actual))
    return "\n".join(lines)


def _explain_text_diff(expected: str, actual: str) -> str:
    import difflib

    diff = list(
        difflib.unified_diff(
            expected.splitlines(), actual.splitlines(), fromfile="golden", tofile="actual", lineterm=""
        )
    )
    return "\n".join(diff[:80])


def resolve_pythons(
    cfg: config.ProjectConfig,
    pythons: Optional[Sequence[str]] = None,
    min_runtimes: int = 2,
) -> tuple:
    """Return (resolved interpreters, not-installed names) or raise."""

    if pythons is None:
        resolved, missing = discover_interpreters(cfg, os.environ.get("RELEASEKIT_PYTHONS"))
    else:
        resolved, missing = _resolve_requested([item.strip() for item in pythons if item.strip()])
        if missing:
            raise VerificationError("explicit interpreter(s) not found: %s" % ", ".join(missing))
    if len(resolved) < min_runtimes:
        raise VerificationError(
            "verification requires at least %d distinct Python runtimes; found %d (%s)%s. "
            "Install them, name them in tool.releasekit.supported-pythons, or pass --pythons."
            % (
                min_runtimes,
                len(resolved),
                ", ".join(resolved) or "none",
                ("; not installed: " + ", ".join(missing)) if missing else "",
            )
        )
    return resolved, missing


def verify(
    root,
    pythons: Optional[Sequence[str]] = None,
    candidate: Optional[Path] = None,
    workdir: Optional[Path] = None,
    timeout: int = DEFAULT_TIMEOUT,
    min_runtimes: int = 2,
    keep_workdir: bool = False,
) -> VerificationReport:
    cfg = config.load(root)
    resolved, missing = resolve_pythons(cfg, pythons, min_runtimes)
    verifier = Verifier(
        cfg.root, resolved, workdir=workdir, timeout=timeout, min_runtimes=min_runtimes
    )
    verifier.report.not_installed = missing
    if keep_workdir and workdir is None:
        verifier._owns_workdir = False
    try:
        return verifier.run(candidate)
    except VerificationError:
        verifier.record_abort("verification aborted before completion")
        raise
    except BaseException as exc:
        verifier.record_abort("verification crashed: %s: %s" % (type(exc).__name__, exc))
        raise
    finally:
        verifier.cleanup()
