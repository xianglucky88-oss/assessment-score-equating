"""Input validation and record extraction.

This module owns the input contract.  Error messages are part of the contract
and must stay stable: callers and release verification compare them verbatim.
"""

from __future__ import annotations

import math
from typing import Any

ALLOWED_STATUSES = ("absent", "present", "violation")


def _require_mapping(value: Any, name: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError("%s must be an object" % name)
    return value


def _require_string(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError("%s must be a string" % name)
    return value


def _require_non_empty_string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("%s must be a non-empty string" % name)
    return value


def validate_payload(payload: Any) -> None:
    """Validate the complete input payload.

    Only the documented fields are inspected; unknown fields are ignored.  The
    checks are intentionally explicit so that verification can pin the exact
    error text across Python implementations.
    """

    if not isinstance(payload, dict):
        raise ValueError("input must be a JSON object")

    responses = payload.get("responses", [])
    if not isinstance(responses, list):
        raise ValueError("responses must be a list")
    for index, row in enumerate(responses):
        prefix = "responses[%d]" % index
        if not isinstance(row, dict):
            raise ValueError("%s must be an object" % prefix)
        for field in ("student", "form", "item"):
            if field not in row:
                raise ValueError("%s is missing field '%s'" % (prefix, field))
            _require_non_empty_string(row[field], "%s.%s" % (prefix, field))
        if "answer" not in row:
            raise ValueError("%s is missing field 'answer'" % prefix)
        _require_string(row["answer"], "%s.answer" % prefix)
        status = row.get("status", "present")
        if status not in ALLOWED_STATUSES:
            raise ValueError(
                "%s.status must be one of: %s"
                % (prefix, ", ".join(ALLOWED_STATUSES))
            )

    keys = _require_mapping(payload.get("keys", {}), "keys")
    for item in sorted(keys):
        _require_non_empty_string(item, "keys key")
        _require_string(keys[item], "keys[%r]" % item)

    void_items = payload.get("void_items", [])
    if not isinstance(void_items, list):
        raise ValueError("void_items must be a list")
    for index, item in enumerate(void_items):
        _require_non_empty_string(item, "void_items[%d]" % index)

    scale = _require_mapping(payload.get("scale", {}), "scale")
    for form in sorted(scale):
        _require_non_empty_string(form, "scale key")
        value = scale[form]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("scale[%r] must be a number" % form)
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("scale[%r] must be a finite number" % form)


def load_records(payload: dict) -> list[dict]:
    """Validate *payload* and return a defensive copy of its response rows."""

    validate_payload(payload)
    records = []
    for row in payload.get("responses", []):
        record = dict(row)
        record.setdefault("status", "present")
        records.append(record)
    return records
