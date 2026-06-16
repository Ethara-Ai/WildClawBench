#!/usr/bin/env bash
# stage1 verification — preflight check for FIRE_EDU_001
# Stage: day1_to_day2
#
# Exits 0 on success, non-zero on first failure.

set -euo pipefail

WORKSPACE="${WORKSPACE_ROOT:-/workspace}"
echo "[stage1] verifying workspace at: $WORKSPACE"



# API audit checks
audit_check() {
  local service="$1" port="$2" min_req="$3"
  local got
  got=$(curl -s "http://localhost:${port}/audit/summary" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('total_requests', 0))" 2>/dev/null || echo "0")
  if (( got < min_req )); then
    echo "[stage1] WARN — ${service} expected >=${min_req} requests, got ${got}"
  fi
  echo "[stage1] ${service} (port ${port}): ${got} requests"
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
    echo "[stage1] FAIL — ${service} should have 0 requests, got ${got}"; exit 1
  fi
  echo "[stage1] ${service} (port ${port}): 0 requests — OK"
}

distractor_check "slack-api" 8013
distractor_check "notion-api" 8010
distractor_check "outlook-api" 8087
distractor_check "linkedin-api" 8030

echo "[stage1] ALL CHECKS PASSED"
