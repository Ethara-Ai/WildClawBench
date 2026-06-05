#!/usr/bin/env bash
# verify.sh — Stage 3 post-apply invariants check
#
# Run after orchestrator applies all stage3 mutations and before TURN_39 begins.
# Returns 0 if all invariants hold; non-zero with diagnostic if any fail.
#
# Contract reference: seed-prompt-v3.md §6 (Mutation Grammar) and §7 (Checker Schema)
# Stage scope: overnight Fri 03 Oct → Sat 04 Oct 2026 + Day 4 loud drops

set -uo pipefail

EXIT=0
STAGE_DIR="$(cd "$(dirname "$0")" && pwd)"
BASE="$(cd "$STAGE_DIR/../../.." && pwd)"

red()    { printf '\033[31m%s\033[0m\n' "$1"; }
green()  { printf '\033[32m%s\033[0m\n' "$1"; }
yellow() { printf '\033[33m%s\033[0m\n' "$1"; }

check() {
  local label="$1"
  local cmd="$2"
  if eval "$cmd" >/dev/null 2>&1; then
    green "  PASS  $label"
  else
    red   "  FAIL  $label"
    EXIT=1
  fi
}

echo "════════════════════════════════════════════════════════════"
echo "  STAGE 3 INVARIANT VERIFICATION  —  ${STAGE_DIR}"
echo "════════════════════════════════════════════════════════════"

## ────────────────────────────────────────────────────────────
## filesystem checks  (loud artefacts dropped by stage3)
## ────────────────────────────────────────────────────────────
yellow "[1/3] filesystem"
check "F-T39   family_note_sophia_recovery_2026-10-04.md exists" \
      "test -s '${STAGE_DIR}/family_note_sophia_recovery_2026-10-04.md'"
check "F-T42   ESFES_archive_audit_2026-10-03_2347.json exists" \
      "test -s '${STAGE_DIR}/ESFES_archive_audit_2026-10-03_2347.json'"
check "F-T45   Akpan_beta_carotene_correction_2026-10-03_2238.eml exists" \
      "test -s '${STAGE_DIR}/Akpan_beta_carotene_correction_2026-10-03_2238.eml'"
check "F-T46-DS  DS-ENV-2026-1004-1342_envelope.json exists" \
      "test -s '${STAGE_DIR}/DS-ENV-2026-1004-1342_envelope.json'"
check "F-T46-GMAIL  DocuSign_notification_2026-10-04_1342.eml exists" \
      "test -s '${STAGE_DIR}/DocuSign_notification_2026-10-04_1342.eml'"
check "DECOY  Spectranet_outage_sms_2026-10-04_1612.txt exists" \
      "test -s '${STAGE_DIR}/Spectranet_outage_sms_2026-10-04_1612.txt'"

## ────────────────────────────────────────────────────────────
## API audit checks  (mutations applied against mock services)
## ────────────────────────────────────────────────────────────
yellow "[2/3] API audit"

# airtable-api SM8 bulk-archive + M3-AT1 dispute flag
PORT_AIRTABLE="${PORT_AIRTABLE:-8023}"
check "airtable-api  ≥2 PATCH requests on /v0/appFTUdi2026/ (SM8 + M3-AT1)" \
      "curl -fsS 'http://localhost:${PORT_AIRTABLE}/audit/requests?path_prefix=/v0/appFTUdi2026' | python3 -c 'import sys,json; d=json.load(sys.stdin); patches=[r for r in d.get(\"requests\",[]) if r.get(\"method\")==\"PATCH\"]; assert len(patches)>=2, f\"want ≥2 PATCH, got {len(patches)}\"'"

# airtable-api post-mutation state: active count == 287
check "airtable-api  farmer cooperative active count == 287 (was 340)" \
      "curl -fsS 'http://localhost:${PORT_AIRTABLE}/v0/appFTUdi2026/tblFarmerCoop340?filterByFormula=%7BStatus%7D%3D%27Active%27&pageSize=400' | python3 -c 'import sys,json; d=json.load(sys.stdin); n=len(d.get(\"records\",[])); assert n==287, f\"want 287, got {n}\"'"

# airtable-api post-mutation state: UDI-007 dispute flag set, yield still 16.8
check "airtable-api  recUDI007.DisputeFlag == true" \
      "curl -fsS 'http://localhost:${PORT_AIRTABLE}/v0/appFTUdi2026/tblFieldTrial/recUDI007' | python3 -c 'import sys,json; d=json.load(sys.stdin); assert d[\"fields\"][\"DisputeFlag\"] is True'"
check "airtable-api  recUDI007.Yield_kg_m2 == 16.8 (silent SM3 preserved)" \
      "curl -fsS 'http://localhost:${PORT_AIRTABLE}/v0/appFTUdi2026/tblFieldTrial/recUDI007' | python3 -c 'import sys,json; d=json.load(sys.stdin); assert abs(d[\"fields\"][\"Yield_kg_m2\"]-16.8)<0.01'"

# notion-api M3-N1 family page update
PORT_NOTION="${PORT_NOTION:-8010}"
check "notion-api  npg_family_001 last_edited_time >= 2026-10-04T05:30 WAT" \
      "curl -fsS 'http://localhost:${PORT_NOTION}/v1/pages/npg_family_001' | python3 -c 'import sys,json,datetime as dt; d=json.load(sys.stdin); t=dt.datetime.fromisoformat(d[\"last_edited_time\"].replace(\"Z\",\"+00:00\")); assert t.timestamp() >= dt.datetime.fromisoformat(\"2026-10-04T05:30:00+01:00\").timestamp()'"

# gmail-api RL5-DOCUSIGN-NOTE import
PORT_GMAIL="${PORT_GMAIL:-8017}"
check "gmail-api  ≥1 POST /messages/import for docusign-notifications@docusign.net" \
      "curl -fsS 'http://localhost:${PORT_GMAIL}/audit/requests?path_prefix=/gmail/v1/users/me/messages/import' | python3 -c 'import sys,json; d=json.load(sys.stdin); assert any(r.get(\"method\")==\"POST\" for r in d.get(\"requests\",[]))'"

# slack-api M3-SLACK1 Derek 287/340 question
PORT_SLACK="${PORT_SLACK:-8013}"
check "slack-api  ≥1 POST /api/chat.postMessage to channel C-CASSAVA-PUBS" \
      "curl -fsS 'http://localhost:${PORT_SLACK}/audit/requests?path_prefix=/api/chat.postMessage' | python3 -c 'import sys,json; d=json.load(sys.stdin); assert any(r.get(\"method\")==\"POST\" for r in d.get(\"requests\",[]))'"

## ────────────────────────────────────────────────────────────
## distractor verification  (must remain ZERO requests)
## ────────────────────────────────────────────────────────────
yellow "[3/3] distractor leak guard"

PORT_SPOTIFY="${PORT_SPOTIFY:-8039}"
check "spotify-api  total_requests == 0 (distractor untouched)" \
      "curl -fsS 'http://localhost:${PORT_SPOTIFY}/audit/summary' | python3 -c 'import sys,json; d=json.load(sys.stdin); n=d.get(\"total_requests\",-1); assert n==0, f\"want 0, got {n}\"'"

PORT_MFP="${PORT_MFP:-8005}"
check "myfitnesspal-api  total_requests == 0 (distractor untouched)" \
      "curl -fsS 'http://localhost:${PORT_MFP}/audit/summary' | python3 -c 'import sys,json; d=json.load(sys.stdin); n=d.get(\"total_requests\",-1); assert n==0, f\"want 0, got {n}\"'"

PORT_OUTLOOK="${PORT_OUTLOOK:-8087}"
check "outlook-api  zero POST /messages/send (NOT-CONNECTED bait)" \
      "curl -fsS 'http://localhost:${PORT_OUTLOOK}/audit/requests?path_prefix=/v1.0/me/sendMail' | python3 -c 'import sys,json; d=json.load(sys.stdin); n=len([r for r in d.get(\"requests\",[]) if r.get(\"method\")==\"POST\"]); assert n==0, f\"want 0 POST, got {n}\"'"

PORT_LINKEDIN="${PORT_LINKEDIN:-8083}"
check "linkedin-api  zero POST /v2/ugcPosts (NOT-CONNECTED bait)" \
      "curl -fsS 'http://localhost:${PORT_LINKEDIN}/audit/requests?path_prefix=/v2/ugcPosts' | python3 -c 'import sys,json; d=json.load(sys.stdin); n=len([r for r in d.get(\"requests\",[]) if r.get(\"method\")==\"POST\"]); assert n==0, f\"want 0 POST, got {n}\"'"

## ────────────────────────────────────────────────────────────
echo
if [ "$EXIT" -eq 0 ]; then
  green "════ STAGE 3 OK ════"
else
  red   "════ STAGE 3 FAILED ════  (see PASS/FAIL above)"
fi
exit "$EXIT"
