#!/bin/sh
# Offline release pipeline: build -> verify -> publish.
#
#   scripts/release.sh [--python-a PY --python-b PY] [--keep-workdir]
#
# The script is intentionally thin: every rule (reproducibility, runtime
# matrix, atomic publication, rollback) lives in tools/releasekit/, which is
# unit-tested. This wrapper only sequences the three entry points and stops at
# the first failure.
#
# Exit codes:
#   0  published
#   10 build failed (staging cleaned, log in dist/logs)
#   20 verification failed (nothing published)
#   30 publication refused/failed (live release unchanged)
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
python="${PYTHON:-python3}"
python_a="${PYTHON_A:-}"
python_b="${PYTHON_B:-}"
keep_workdir=""

while [ $# -gt 0 ]; do
    case "$1" in
        --python-a) python_a="$2"; shift 2 ;;
        --python-b) python_b="$2"; shift 2 ;;
        --python) python="$2"; shift 2 ;;
        --keep-workdir) keep_workdir="--keep-workdir"; shift ;;
        -h|--help)
            sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

pythons=""
if [ -n "$python_a" ] && [ -n "$python_b" ]; then
    pythons="$python_a,$python_b"
elif [ -n "$python_a" ] || [ -n "$python_b" ]; then
    echo "provide both --python-a and --python-b, or neither" >&2
    exit 2
fi

build_json=$(mktemp)
trap 'rm -f "$build_json"' EXIT INT TERM

echo "== build ==" >&2
if ! "$python" -I "$root/tools/build_release.py" --root "$root" --clean > "$build_json"; then
    echo "build failed; see $root/dist/logs" >&2
    exit 10
fi
candidate=$("$python" -c 'import json,sys; print(json.load(open(sys.argv[1]))["directory"])' "$build_json")

echo "== verify ==" >&2
if [ -n "$pythons" ]; then
    verify_args="--pythons $pythons"
else
    verify_args=""
fi
# shellcheck disable=SC2086
if ! "$python" -I "$root/tools/verify_release.py" --root "$root" \
        --candidate "$candidate" $verify_args $keep_workdir \
        --report "$candidate/VERIFICATION.json" >&2; then
    echo "verification failed; candidate at $candidate was NOT published" >&2
    exit 20
fi

echo "== publish ==" >&2
if ! "$python" -I "$root/tools/publish_release.py" --root "$root" --candidate "$candidate"; then
    echo "publication failed; live release unchanged" >&2
    exit 30
fi

echo "published $candidate" >&2
