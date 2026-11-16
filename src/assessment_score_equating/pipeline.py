"""Orchestration boundary: validate, calculate, shape the output envelope."""

from .engine import calculate
from .parser import load_records
from .report import build_result

PROJECT = "assessment-score-equating"


def run(payload: dict) -> dict:
    records = load_records(payload)
    return build_result(PROJECT, calculate(records, payload))
