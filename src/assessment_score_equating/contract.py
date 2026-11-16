"""Frozen result contract shared by the engine, report and release checks."""

CONTRACT_VERSION = 1
ROUNDING_RULE = "HALF_UP@0.01"
DEFAULT_SCALE = 10

ENVELOPE_FIELDS = (
    "project",
    "status",
    "contract_version",
    "rounding",
    "scores",
    "void_items",
)
SCORE_FIELDS = (
    "student",
    "form",
    "raw",
    "scale",
    "scale_hundredths",
    "attempted",
    "absent",
    "violations",
)
STATUSES = ("absent", "present", "violation")
