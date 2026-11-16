"""Domain calculation only: no I/O, no environment access, no clock."""

from __future__ import annotations

from decimal import Context, Decimal, ROUND_HALF_UP

from .contract import DEFAULT_SCALE
from .models import ScoreEntry

# A private context keeps rounding independent of the process-wide decimal
# context, which applications or test code may have changed.
_ROUNDING_CONTEXT = Context(prec=60, rounding=ROUND_HALF_UP)
_HUNDREDTHS = Decimal("0.01")


def _scale_hundredths(raw: int, scale) -> int:
    """Return ``round_half_up(raw * scale, 2)`` as an exact integer count.

    Arithmetic is done on the decimal spelling of *scale* so the result does not
    depend on binary floating point or on the interpreter's repr/format
    behaviour.
    """

    exact = _ROUNDING_CONTEXT.multiply(Decimal(raw), Decimal(str(scale)))
    rounded = exact.quantize(_HUNDREDTHS, rounding=ROUND_HALF_UP, context=_ROUNDING_CONTEXT)
    cents = _ROUNDING_CONTEXT.multiply(rounded, Decimal(100))
    if cents != cents.to_integral_value(context=_ROUNDING_CONTEXT):
        raise ValueError("scale result is not representable in hundredths")
    return int(cents)


def calculate(rows, payload):
    keys = payload.get("keys", {})
    void = frozenset(payload.get("void_items", []))
    scale = payload.get("scale", {})

    buckets = {}
    for row in rows:
        if row["item"] in void:
            continue
        bucket = buckets.setdefault(
            (row["student"], row["form"]),
            {"raw": 0, "attempted": 0, "absent": 0, "violations": 0},
        )
        status = row.get("status", "present")
        if status == "absent":
            bucket["absent"] += 1
            continue
        if status == "violation":
            bucket["violations"] += 1
            continue
        bucket["attempted"] += 1
        if row["answer"] == keys.get(row["item"]):
            bucket["raw"] += 1

    scores = []
    for (student, form), bucket in sorted(buckets.items()):
        form_scale = scale.get(form, DEFAULT_SCALE)
        scores.append(
            ScoreEntry(
                student=student,
                form=form,
                raw=bucket["raw"],
                scale_hundredths=_scale_hundredths(bucket["raw"], form_scale),
                attempted=bucket["attempted"],
                absent=bucket["absent"],
                violations=bucket["violations"],
            )
        )
    return {"scores": scores, "void_items": tuple(sorted(void))}
