"""Minimal TOML reader used when ``tomllib``/``tomli`` is unavailable.

Only the subset used by this repository's ``pyproject.toml`` is supported:

* table headers (``[a.b]``)
* ``key = value`` lines with JSON-compatible strings, integers, floats,
  booleans and arrays
* comments that start at ``#`` outside a string

The parser is intentionally strict: unsupported syntax raises ``ValueError``
instead of being silently ignored, so a configuration error cannot silently
change a release.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict

_SECTION_RE = re.compile(r"^\[\s*([A-Za-z0-9_.-]+)\s*\]$")
_KEY_RE = re.compile(r"^([A-Za-z0-9_-]+)\s*=\s*(.+)$")


def _strip_comment(line: str) -> str:
    in_string = False
    escaped = False
    for index, char in enumerate(line):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == "#":
            return line[:index]
    return line


def _parse_value(text: str, lineno: int) -> Any:
    candidate = text.strip()
    if candidate == "true":
        return True
    if candidate == "false":
        return False
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        # TOML bare keys are not valid JSON; accept only the date/number-free
        # subset we need and reject everything else loudly.
        raise ValueError(
            "pyproject.toml line %d: unsupported value %r" % (lineno, text)
        )


def loads(text: str) -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    current = data
    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = _strip_comment(raw_line).strip()
        if not line:
            continue
        section = _SECTION_RE.match(line)
        if section:
            current = data
            for part in section.group(1).split("."):
                current = current.setdefault(part, {})
                if not isinstance(current, dict):
                    raise ValueError(
                        "pyproject.toml line %d: %s is not a table" % (lineno, part)
                    )
            continue
        match = _KEY_RE.match(line)
        if not match:
            raise ValueError("pyproject.toml line %d: unsupported syntax" % lineno)
        key, value = match.group(1), _parse_value(match.group(2), lineno)
        if key in current:
            raise ValueError("pyproject.toml line %d: duplicate key %r" % (lineno, key))
        current[key] = value
    return data


def load(path) -> Dict[str, Any]:
    with open(str(path), "rb") as handle:
        raw = handle.read()
    return loads(raw.decode("utf-8"))
