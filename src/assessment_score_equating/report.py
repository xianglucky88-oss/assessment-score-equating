"""Stable output envelope."""

from . import __version__
from .contract import CONTRACT_VERSION, ROUNDING_RULE
from .models import PipelineResult


def build_result(project: str, data: dict) -> dict:
    result = PipelineResult(
        project=project,
        status="ok",
        contract_version=CONTRACT_VERSION,
        rounding=ROUNDING_RULE,
        scores=tuple(data["scores"]),
        void_items=tuple(data["void_items"]),
    )
    return result.as_dict()


def render_json(result: dict) -> str:
    """Serialise *result* using the single canonical output style."""

    import json

    return json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
