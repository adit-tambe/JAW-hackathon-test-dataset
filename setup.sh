#!/usr/bin/env bash
#
# setup.sh — anything that needs a network, done here rather than during the
# timed run. Executed after `pip install -r requirements.txt`.
#
# This pipeline ships no model weights: parsing is deterministic, and every
# generative call goes to the endpoint the harness provides. So there is
# nothing to download. What this script does instead is fail loudly, now,
# for the packaging mistakes that would otherwise surface mid-run.

set -euo pipefail

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

echo "== preflight =================================================="
"$PY" - <<'PYCODE'
import importlib, sys

required = ["fitz", "openpyxl", "requests"]
missing = []
for name in required:
    try:
        module = importlib.import_module(name)
        version = getattr(module, "__version__", getattr(module, "VersionBind", "?"))
        print(f"  ok    {name:12s} {version}")
    except Exception as exc:
        missing.append(name)
        print(f"  FAIL  {name:12s} {exc}")

if missing:
    sys.exit(f"missing dependencies: {', '.join(missing)}")

# The pipeline itself must import cleanly with no document root present.
for name in ["src.discover", "src.ingest", "src.build_db", "src.answer_engine"]:
    importlib.import_module(name)
    print(f"  ok    import {name}")
print("  preflight passed")
PYCODE

chmod +x run.sh 2>/dev/null || true
echo "== setup complete ============================================="
