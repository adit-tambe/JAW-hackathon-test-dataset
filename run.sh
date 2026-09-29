#!/usr/bin/env bash
#
# run.sh — the entire pipeline, end to end, from a clean checkout.
#
#   ./run.sh --docs /path/to/documents --questions /path/to/questions.json \
#            --out submission.csv
#
# No network access is required or attempted. Everything this needs is either
# in the repo or installed by requirements.txt / setup.sh beforehand.

set -euo pipefail

DOCS=""
QUESTIONS=""
OUT=""
EXTRA=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --docs)      DOCS="$2"; shift 2 ;;
    --questions) QUESTIONS="$2"; shift 2 ;;
    --out)       OUT="$2"; shift 2 ;;
    *)           EXTRA+=("$1"); shift ;;
  esac
done

if [[ -z "$DOCS" || -z "$QUESTIONS" || -z "$OUT" ]]; then
  echo "usage: ./run.sh --docs DIR --questions FILE.json --out FILE.csv" >&2
  exit 2
fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

# Pick an interpreter that actually runs. `command -v python3` can succeed on
# a stub (Windows' Store alias) that then fails on execution, so probe it.
PY=""
for cand in "${PYTHON:-}" python3 python; do
  [ -n "$cand" ] || continue
  if "$cand" -c "import sys; sys.exit(0)" >/dev/null 2>&1; then PY="$cand"; break; fi
done
if [ -z "$PY" ]; then echo "no working python interpreter found" >&2; exit 127; fi

# Scratch lives outside the repo when possible, so a read-only checkout works.
WORK="${JAW_DATA_DIR:-${TMPDIR:-/tmp}/jaw-run-$$}"
mkdir -p "$WORK"
export JAW_DATA_DIR="$WORK"
export PYTHONUNBUFFERED=1

echo "=================================================================="
echo "  JAW 2026 — bid intelligence pipeline"
echo "  python : $($PY --version 2>&1)"
echo "  llm    : ${LLM_BASE_URL:-<unset>}"
echo "  work   : $WORK"
echo "=================================================================="

exec "$PY" -X utf8 main.py \
  --docs "$DOCS" \
  --questions "$QUESTIONS" \
  --out "$OUT" \
  "${EXTRA[@]+"${EXTRA[@]}"}"
