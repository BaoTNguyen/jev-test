#!/usr/bin/env bash
# Fetch the three benchmarks at pinned commits into vendor/ and install tau2.
# Safe to re-run. Usage: scripts/setup_benchmarks.sh [--no-tau-install]
set -euo pipefail
cd "$(dirname "$0")/.."
VENDOR="${VENDOR_DIR:-$PWD/vendor}"
mkdir -p "$VENDOR"

fetch() {  # repo sha dir
  local repo=$1 sha=$2 dir="$VENDOR/$3"
  if [ -d "$dir/.git" ] && [ "$(git -C "$dir" rev-parse HEAD)" = "$sha" ]; then
    echo "ok   $3 @ ${sha:0:8}"; return
  fi
  rm -rf "$dir"; mkdir -p "$dir"
  git -C "$dir" init -q
  git -C "$dir" remote add origin "https://github.com/$repo.git"
  git -C "$dir" fetch -q --depth 1 origin "$sha"
  git -C "$dir" checkout -q FETCH_HEAD
  echo "got  $3 @ ${sha:0:8}"
}

# Pinned 2026-11-13. Bump deliberately, not by accident.
fetch sierra-research/tau2-bench      c06b07dd7c4d4ec61ba6ff9bcdf0b15dd7ef36d4 tau2-bench
fetch stanfordmlgroup/MedAgentBench   99260117137b09f04837a8c18d18a1107efa55ae MedAgentBench
fetch mcp-tool-bench/MCPToolBenchPP   2bd35e58ed5ebe79bbb12df0f9919a34ed2cb037 MCPToolBenchPP

if [ "${1:-}" != "--no-tau-install" ]; then
  echo "installing tau2 (editable) into the project environment..."
  if [ -n "${UV_PROJECT_ENVIRONMENT:-}" ] || [ -d .venv ]; then
    uv pip install -q -e "$VENDOR/tau2-bench"
  else
    uv venv -q && uv pip install -q -e . -e "$VENDOR/tau2-bench"
  fi
fi

REFSOL="$VENDOR/MedAgentBench/src/server/tasks/medagentbench/refsol.py"
if [ ! -f "$REFSOL" ]; then
  cat <<MSG

  !! MedAgentBench grader missing.
  !! Download refsol.py from the link in the MedAgentBench README
  !!   (https://stanfordmedicine.box.com/s/fizv0unyjgkb1r3a83rfn5p3dc673uho)
  !! and save it as:
  !!   $REFSOL
  !! Without it MedAgentBench episodes still run and are timed, but score = null.
MSG
fi
echo "done. TAU2_DATA_DIR=$VENDOR/tau2-bench/data"
