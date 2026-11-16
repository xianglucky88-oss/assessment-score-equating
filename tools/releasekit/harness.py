"""Standalone harness scripts, kept outside extracted artifacts.

Each script below is written to a fresh directory by ``verify`` and executed
with ``python -I``.  ``-I`` means no ``PYTHONPATH``, no user site-packages and
no script directory on ``sys.path``, so every successful import is an explicit
statement about the artifact under test.

The scripts are stdlib-only and must remain compatible with Python 3.9.
"""

from __future__ import annotations

RUN_PIPELINE = r'''\
import argparse
import json
import os
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--site", required=True)
parser.add_argument("--cases", required=True)
parser.add_argument("--offline-guard", action="store_true")
args = parser.parse_args()

if args.offline_guard:
    import socket

    def _blocked(*_a, **_k):
        raise RuntimeError("network access attempted during offline verification")

    socket.socket.connect = _blocked
    socket.create_connection = _blocked
    socket.getaddrinfo = _blocked

sys.path.insert(0, args.site)

# Snapshot modules present *before* importing the package so the guard's own
# socket import is not attributed to project code.
_baseline_modules = frozenset(sys.modules)

import assessment_score_equating
from assessment_score_equating.pipeline import run
from assessment_score_equating.report import render_json

with open(args.cases, "r", encoding="utf-8") as handle:
    cases = json.load(handle)

base = os.path.dirname(os.path.abspath(args.cases))
outputs = {}
for case in cases["cases"]:
    payload = case.get("payload")
    if payload is None and case.get("path"):
        with open(os.path.join(base, case["path"]), "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    outputs[case["name"]] = json.loads(render_json(run(payload)))

# Report any network module the project pulled in transitively; it is local-only.
network_modules = sorted(
    name
    for name in ("socket", "ssl", "http", "urllib.request", "ftplib", "smtplib")
    if name in sys.modules and name not in _baseline_modules
)
print(json.dumps({
    "version": assessment_score_equating.__version__,
    "implementation": sys.implementation.name,
    "python": "%d.%d.%d" % sys.version_info[:3],
    "outputs": outputs,
    "network_modules": network_modules,
}, sort_keys=True))
'''

RUN_CONTRACT_ERRORS = r'''\
import argparse
import json
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--site", required=True)
parser.add_argument("--cases", required=True)
args = parser.parse_args()

sys.path.insert(0, args.site)

from assessment_score_equating.parser import validate_payload

with open(args.cases, "r", encoding="utf-8") as handle:
    cases = json.load(handle)

results = []
for case in cases["cases"]:
    try:
        validate_payload(case["payload"])
    except ValueError as exc:
        results.append({"name": case["name"], "raised": "ValueError", "message": str(exc)})
    except BaseException as exc:  # noqa: BLE001 - report exact class
        results.append({"name": case["name"], "raised": type(exc).__name__, "message": str(exc)})
    else:
        results.append({"name": case["name"], "raised": None, "message": ""})
print(json.dumps({"results": results}, sort_keys=True))
'''

RUN_CLI = r'''\
import argparse
import json
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--site", required=True)
parser.add_argument("--module-mode", action="store_true")
parser.add_argument("args", nargs=argparse.REMAINDER)
parsed = parser.parse_args()

sys.path.insert(0, parsed.site)

if parsed.module_mode:
    import runpy
    sys.argv = ["assessment_score_equating"] + list(parsed.args)
    try:
        runpy.run_module("assessment_score_equating.cli", run_name="__main__", alter_sys=True)
    except SystemExit as exc:
        raise SystemExit(exc.code)
else:
    from assessment_score_equating.cli import main
    raise SystemExit(main(list(parsed.args)))
'''

RUN_TESTS = r'''\
import argparse
import json
import sys
import unittest

parser = argparse.ArgumentParser()
parser.add_argument("--root", required=True)
parser.add_argument("--tests", required=True)
parser.add_argument("--src", required=True)
args = parser.parse_args()

sys.path.insert(0, args.src)
loader = unittest.TestLoader()
suite = loader.discover(args.tests, top_level_dir=args.root)
result = unittest.TextTestRunner(verbosity=2, stream=sys.stderr).run(suite)
print(json.dumps({
    "tests_run": result.testsRun,
    "failures": len(result.failures),
    "errors": len(result.errors),
    "skipped": len(result.skipped),
    "successful": result.wasSuccessful(),
}, sort_keys=True))
raise SystemExit(0 if result.wasSuccessful() else 1)
'''

RUN_IMPORT = r'''\
import argparse
import json
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--site", required=True)
parser.add_argument("--package", required=True)
parser.add_argument("--modules", required=True)
args = parser.parse_args()

sys.path.insert(0, args.site)

import importlib

package = importlib.import_module(args.package)
imported = {}
for name in args.modules.split(","):
    module = importlib.import_module(args.package + "." + name) if name else package
    imported[name or "<package>"] = module.__name__
print(json.dumps({
    "package": package.__name__,
    "version": getattr(package, "__version__", None),
    "file": package.__file__,
    "modules": imported,
    "network_modules": sorted(
        name for name in ("socket", "ssl", "http", "urllib.request")
        if name in sys.modules
    ),
}, sort_keys=True))
'''

RUN_BUILD = r'''\
import argparse
import json
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--tools", required=True)
parser.add_argument("--root", required=True)
parser.add_argument("--dist", required=True)
parser.add_argument("--build-id", required=True)
args = parser.parse_args()

sys.path.insert(0, args.tools)

from releasekit import build

outcome = build.build(args.root, dist_dir=args.dist, build_id=args.build_id, keep=True)
print(json.dumps({
    "build_id": outcome.build_id,
    "directory": str(outcome.directory),
    "artifacts": [
        {"name": item["name"], "sha256": item["sha256"], "size": item["size"]}
        for item in outcome.manifest["artifacts"]
    ],
}, sort_keys=True))
'''

SCRIPTS = {
    "run_pipeline.py": RUN_PIPELINE,
    "run_contract_errors.py": RUN_CONTRACT_ERRORS,
    "run_cli.py": RUN_CLI,
    "run_tests.py": RUN_TESTS,
    "run_import.py": RUN_IMPORT,
    "run_build.py": RUN_BUILD,
}


def write_harness(directory) -> None:
    from pathlib import Path

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for name, content in SCRIPTS.items():
        (directory / name).write_text(content, encoding="utf-8")
