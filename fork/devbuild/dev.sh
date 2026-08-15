#!/usr/bin/env bash
# Run the DEV build: this repo's code against the snapshot profile.
#
# Fork-owned. Two things are isolated, on two different axes:
#
#   code — comes from this checkout (whatever branch is checked out, normally
#          `dev`), via the repo's own .venv. Production runs from a separate
#          clone at AppData\Local\hermes\hermes-agent with its own venv.
#   data — HERMES_HOME points at profiles/dev, a COPY made by snapshot_prod.py.
#
# So nothing done here can touch production sessions, config, or credentials,
# and a schema migration run by dev code migrates the copy.
#
# Usage:
#   bash fork/devbuild/dev.sh                # interactive CLI
#   bash fork/devbuild/dev.sh desktop        # desktop app
#   bash fork/devbuild/dev.sh gateway run    # any other subcommand
#   bash fork/devbuild/dev.sh --             # just print the env and exit
#
# Refresh the data snapshot with:
#   .venv/Scripts/python.exe fork/devbuild/snapshot_prod.py

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

# Resolve the dev profile the same way snapshot_prod.py does, so the two cannot
# disagree about where the snapshot lives.
if [[ -n "${LOCALAPPDATA:-}" ]]; then
  PROD_HOME="$LOCALAPPDATA/hermes"
else
  PROD_HOME="$HOME/.hermes"
fi
DEV_HOME="$PROD_HOME/profiles/dev"

if [[ ! -d "$DEV_HOME" ]]; then
  echo "✗ No dev snapshot at: $DEV_HOME" >&2
  echo "  Create one first:" >&2
  echo "    .venv/Scripts/python.exe fork/devbuild/snapshot_prod.py" >&2
  exit 1
fi

# Pick the repo's interpreter. `.venv` is this checkout's; refuse to fall back to
# a system python, because that would silently run DIFFERENT code (a pip-installed
# hermes) against the dev profile and look like the dev build working.
if [[ -x ".venv/Scripts/python.exe" ]]; then
  PY=".venv/Scripts/python.exe"
elif [[ -x ".venv/bin/python" ]]; then
  PY=".venv/bin/python"
else
  echo "✗ No .venv in $REPO_ROOT — cannot guarantee this runs the repo's code." >&2
  echo "  Create it with: uv venv .venv && uv pip install -e ." >&2
  exit 1
fi

export HERMES_HOME="$DEV_HOME"

# Belt and braces against a stale lock in the snapshot making the dev instance
# think a gateway is live. snapshot_prod.py already excludes these; a snapshot
# taken by an older version of the script may still have them.
rm -f "$DEV_HOME/gateway.pid" "$DEV_HOME/gateway.lock" "$DEV_HOME/auth.lock" 2>/dev/null || true

# No `git -C "$REPO_ROOT"`: $REPO_ROOT comes from `pwd`, which under git-bash is
# an MSYS path (/c/...) that native git.exe cannot chdir into ("fatal: cannot
# change to '/c/...'"), so -C silently yielded '?' for the branch. The script has
# already cd'd to the repo, so a bare rev-parse is both correct and simpler.
BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?')"
echo "── DEV BUILD ─────────────────────────────────────────"
echo "  code   : $REPO_ROOT  (branch: $BRANCH)"
echo "  data   : $HERMES_HOME"
echo "  python : $PY"
echo "  prod   : untouched at $PROD_HOME"
echo "──────────────────────────────────────────────────────"

if [[ "${1:-}" == "--" ]]; then
  exit 0
fi

if [[ "$BRANCH" == "main" ]]; then
  echo "  ⚠ You are on main. The dev build normally runs the dev branch." >&2
  echo >&2
fi

exec "$PY" -m hermes_cli.main "$@"
