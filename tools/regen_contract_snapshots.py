"""Regenerate or check the contract example snapshots.

Usage (from anywhere):
    python3 tools/regen_contract_snapshots.py          # rewrite snapshots
    python3 tools/regen_contract_snapshots.py --check  # diff only, exit 1 on mismatch

Snapshots are serialized exactly like the CLI
(json.dumps(..., ensure_ascii=False, indent=2, sort_keys=True)).
Cases that raise are stored as `<name>.expected.error.txt` containing
only the exception type and message, so snapshots stay environment
independent (no tracebacks, no absolute paths).
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from assessment_score_equating.pipeline import run  # noqa: E402

CASES = ROOT / "fixtures" / "contract"
SNAPSHOTS = CASES / "snapshots"


def render(payload: dict) -> tuple[str, str]:
    """Return (suffix, content) for the snapshot of one case payload."""
    try:
        out = run(payload)
    except Exception as exc:  # noqa: BLE001 - snapshot the failure mode
        return ".expected.error.txt", f"ERROR {type(exc).__name__}: {exc}\n"
    text = json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True)
    return ".expected.json", text + "\n"


def main() -> int:
    check = "--check" in sys.argv
    cases = sorted(CASES.glob("case-*.json"))
    if not cases:
        print(f"no case files found in {CASES}", file=sys.stderr)
        return 2

    expected_files = set()
    mismatches = []
    for case_path in cases:
        payload = json.loads(case_path.read_text(encoding="utf-8"))
        suffix, content = render(payload)
        snap_path = SNAPSHOTS / (case_path.stem + suffix)
        expected_files.add(snap_path.name)
        if check:
            if not snap_path.exists():
                mismatches.append(f"MISSING  {snap_path.relative_to(ROOT)}")
            elif snap_path.read_text(encoding="utf-8") != content:
                mismatches.append(f"CHANGED  {snap_path.relative_to(ROOT)}")
        else:
            snap_path.parent.mkdir(parents=True, exist_ok=True)
            snap_path.write_text(content, encoding="utf-8")
            print(f"wrote {snap_path.relative_to(ROOT)}")

    if check:
        orphans = sorted(
            p.name for p in SNAPSHOTS.glob("*.expected.*")
            if p.name not in expected_files
        )
        for name in orphans:
            mismatches.append(f"ORPHAN   fixtures/contract/snapshots/{name}")
        if mismatches:
            print("snapshot check failed:")
            print("\n".join(mismatches))
            return 1
        print(f"snapshot check passed ({len(cases)} cases)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
