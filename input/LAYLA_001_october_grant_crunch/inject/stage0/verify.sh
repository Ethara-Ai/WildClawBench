#!/usr/bin/env bash
# stage0 verification — run AFTER seeding, BEFORE T0
#
# Asserts:
#   1) All F0-* filesystem artifacts exist at expected paths
#   2) All M0-* API mutations landed (verified via /audit/summary)
#
# Exits 0 on success, non-zero on first failure.

set -euo pipefail

WORKSPACE="${WORKSPACE_ROOT:-/workspace}"
echo "[stage0] verifying workspace at: $WORKSPACE"

# ─────────────────────────────────────────────────────────────────────────────
# Filesystem checks
# ─────────────────────────────────────────────────────────────────────────────
required_files=(
  "$WORKSPACE/persona/layla-mcbride/README.md"
  "$WORKSPACE/persona/layla-mcbride/SOUL.md"
  "$WORKSPACE/persona/layla-mcbride/MEMORY.md"
  "$WORKSPACE/persona/layla-mcbride/HEARTBEAT.md"
  "$WORKSPACE/persona/layla-mcbride/USER.md"
  "$WORKSPACE/persona/layla-mcbride/IDENTITY.md"
  "$WORKSPACE/persona/layla-mcbride/AGENTS.md"
  "$WORKSPACE/persona/layla-mcbride/TOOLS.md"
  "$WORKSPACE/grants/waita-eacri/2026-10/WAITA_proposal_v8.0_FINAL.pdf"
  "$WORKSPACE/grants/waita-eacri/2026-10/WAITA_budget_v8.0.xlsx"
  "$WORKSPACE/grants/waita-eacri/WAADA_grant_terms_excerpt.pdf"
  "$WORKSPACE/papers/cassava-biofort/cassava-biofort-analysis.xlsx"
  "$WORKSPACE/papers/cassava-biofort/draft_manuscript_v0.7.md"
  "$WORKSPACE/field/maps/field_trial_plot_map_UDI-2026.pdf"
  "$WORKSPACE/family/Sophia_school_permission_slip_2026-09-30.pdf"
  "$WORKSPACE/family/generator_fuel_receipt_2026-09-30.jpg"
  "$WORKSPACE/snapshots/Confluence_Y1_deliverables_snapshot_2026-09-30.png"
  "$WORKSPACE/calls/Amina_call_transcript_2026-09-30.txt"
  "$WORKSPACE/hiring/cassava-y3-field-asst/BambooHR_applicants_2026-10-03.csv"
  "$WORKSPACE/inbox/Spotify_playlist_marcus_calming.txt"
  "$WORKSPACE/inbox/MyFitnessPal_streak_reminder.txt"
  "$WORKSPACE/inbox/LinkedIn_post_draft_2026-09-29.md"
  "$WORKSPACE/inbox/NNU_outlook_compose_draft.eml"
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

# ─────────────────────────────────────────────────────────────────────────────
# API audit checks — verify M0-* mutations landed
# ─────────────────────────────────────────────────────────────────────────────
audit_check() {
  local service="$1" port="$2" min_req="$3"
  local got
  got=$(curl -s "http://localhost:${port}/audit/summary" | python3 -c "import sys,json; print(json.load(sys.stdin).get('total_requests', 0))")
  if (( got < min_req )); then
    echo "[stage0] FAIL — ${service} expected ≥${min_req} requests, got ${got}"; exit 1
  fi
  echo "[stage0] ${service} (port ${port}): ${got} requests — OK"
}

audit_check "notion-api"           8010 2
audit_check "confluence-api"       8053 2
audit_check "airtable-api"         8049 2
audit_check "google-calendar-api"  8016 1
audit_check "datadog-api"          8071 1

# ─────────────────────────────────────────────────────────────────────────────
# Distractor APIs MUST be untouched at this point
# ─────────────────────────────────────────────────────────────────────────────
distractor_check() {
  local service="$1" port="$2"
  local got
  got=$(curl -s "http://localhost:${port}/audit/summary" | python3 -c "import sys,json; print(json.load(sys.stdin).get('total_requests', 0))")
  if (( got > 0 )); then
    echo "[stage0] FAIL — distractor ${service} should have 0 requests, got ${got}"; exit 1
  fi
  echo "[stage0] distractor ${service} (port ${port}): 0 requests — OK"
}

distractor_check "spotify-api"        8039
distractor_check "myfitnesspal-api"   8005

echo "[stage0] ALL CHECKS PASSED"
