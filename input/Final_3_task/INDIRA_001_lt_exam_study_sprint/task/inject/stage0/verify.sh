#!/usr/bin/env bash
# stage0 verification — preflight check for FIRE_EDU_001
# Stage: seed_initial_state
#
# Exits 0 on success, non-zero on first failure.

set -euo pipefail

WORKSPACE="${WORKSPACE_ROOT:-/workspace}"
echo "[stage0] verifying workspace at: $WORKSPACE"

required_files=(
  "$WORKSPACE/persona/indira-hudson/README.md"
  "$WORKSPACE/persona/indira-hudson/SOUL.md"
  "$WORKSPACE/persona/indira-hudson/MEMORY.md"
  "$WORKSPACE/persona/indira-hudson/HEARTBEAT.md"
  "$WORKSPACE/persona/indira-hudson/USER.md"
  "$WORKSPACE/persona/indira-hudson/IDENTITY.md"
  "$WORKSPACE/persona/indira-hudson/AGENTS.md"
  "$WORKSPACE/persona/indira-hudson/TOOLS.md"
)

required_dirs=(
  "$WORKSPACE/audits"
  "$WORKSPACE/logs"
)

for f in "${required_files[@]}"; do
  if [[ ! -f "$f" ]]; then
    echo "[stage0] FAIL — missing file: $f"; exit 1
  fi
done

for d in "${required_dirs[@]}"; do
  if [[ ! -d "$d" ]]; then
    echo "[stage0] FAIL — missing directory: $d"; exit 1
  fi
done

echo "[stage0] filesystem OK (${#required_files[@]} files + ${#required_dirs[@]} dirs)"

# API audit checks
audit_check() {
  local service="$1" port="$2" min_req="$3"
  local got
  got=$(curl -s "http://localhost:${port}/audit/summary" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('total_requests', 0))" 2>/dev/null || echo "0")
  if (( got < min_req )); then
    echo "[stage0] WARN — ${service} expected >=${min_req} requests, got ${got}"
  fi
  echo "[stage0] ${service} (port ${port}): ${got} requests"
}

audit_check "gmail-api" 8017 1
audit_check "google-calendar-api" 8016 1
audit_check "google-drive-api" 8019 1

# Distractor APIs MUST be untouched at this point
distractor_check() {
  local service="$1" port="$2"
  local got
  got=$(curl -s "http://localhost:${port}/audit/summary" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('total_requests', 0))" 2>/dev/null || echo "0")
  if (( got > 0 )); then
    echo "[stage0] FAIL — ${service} should have 0 requests, got ${got}"; exit 1
  fi
  echo "[stage0] ${service} (port ${port}): 0 requests — OK"
}

distractor_check "slack-api" 8013
distractor_check "notion-api" 8010
distractor_check "outlook-api" 8087
distractor_check "linkedin-api" 8030

echo "[stage0] ALL CHECKS PASSED"
