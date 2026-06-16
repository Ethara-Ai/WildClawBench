#!/usr/bin/env bash
# Stage 0 verification — confirms all seeds landed before T0
set -euo pipefail

PASS=0
FAIL=0

check() {
  local desc="$1" cmd="$2" expected="$3"
  result=$(eval "$cmd" 2>/dev/null || echo "ERROR")
  if echo "$result" | grep -q "$expected"; then
    echo "  PASS: $desc"
    ((PASS++))
  else
    echo "  FAIL: $desc (expected '$expected', got '$result')"
    ((FAIL++))
  fi
}

echo "=== Stage 0 Verification ==="
echo ""

# --- Notion seeds ---
echo "[Notion — port 8010]"
check "Krasicki project page exists" \
  "curl -s http://localhost:8010/v1/databases/workshop_projects_db/query | jq -r '.results[].properties.Name.title[0].text.content'" \
  "Krasicki Estate Clock"
check "Radziwill balance cock = 28.2" \
  "curl -s http://localhost:8010/v1/databases/workshop_projects_db/query | jq -r '.results[] | select(.properties.Name.title[0].text.content==\"Radziwill Pocket Watch\") | .properties.Balance_Cock.number'" \
  "28.2"

# --- Airtable seeds ---
echo "[Airtable — port 8032]"
check "KR-004 balance_staff_diameter = 0.85" \
  "curl -s http://localhost:8032/v0/{base_id}/Workshop-Parts-Inventory/records | jq -r '.records[] | select(.fields.Part_ID==\"KR-004\") | .fields.balance_staff_diameter'" \
  "0.85"
check "SC-003 balance_staff_diameter = 0.86" \
  "curl -s http://localhost:8032/v0/{base_id}/Workshop-Parts-Inventory/records | jq -r '.records[] | select(.fields.Part_ID==\"SC-003\") | .fields.balance_staff_diameter'" \
  "0.86"
check "Shipping tracker ETA = 2026-10-12" \
  "curl -s http://localhost:8032/v0/{base_id}/Shipping-Tracker/records | jq -r '.records[0].fields.ETA'" \
  "2026-10-12"

# --- Google Calendar seeds ---
echo "[Google Calendar — port 8016]"
check "Krasicki delivery event exists" \
  "curl -s http://localhost:8016/calendar/v3/calendars/primary/events | jq -r '.items[].summary'" \
  "Krasicki Estate Clock"

# --- Linear seeds ---
echo "[Linear — port 8004]"
check "Barrel arbor issue completed = Oct 1 (wrong)" \
  "curl -s http://localhost:8004/issues | jq -r '.issues[] | select(.title | contains(\"Barrel arbor\")) | .completed'" \
  "2026-10-01"

# --- HubSpot seeds ---
echo "[HubSpot — port 8024]"
check "Stefan Muller contact exists" \
  "curl -s http://localhost:8024/contacts/v1/contact | jq -r '.contacts[].email'" \
  "stefan.muller@biel-time.ch"

# --- BambooHR seeds ---
echo "[BambooHR — port 8072]"
check "Mark address = Dietla (stale)" \
  "curl -s http://localhost:8072/api/gateway.php/woodard/v1/employees | jq -r '.employees[0].address'" \
  "Dietla"

# --- DocuSign seeds ---
echo "[DocuSign — port 8053]"
check "Krasicki agreement exists" \
  "curl -s http://localhost:8053/restapi/v2.1/accounts/{accountId}/envelopes | jq -r '.envelopes[0].subject'" \
  "Krasicki"

# --- QuickBooks seeds ---
echo "[QuickBooks — port 8007]"
check "September total expenses = 9530" \
  "curl -s http://localhost:8007/v3/company/{companyId}/journalentry | jq -r '.total_expenses'" \
  "9530"

# --- Box seeds ---
echo "[Box — port 8083]"
check "Radziwill condition report exists (old name)" \
  "curl -s http://localhost:8083/2.0/files | jq -r '.entries[].name'" \
  "radziwill_condition_report.pdf"

# --- Filesystem seeds ---
echo "[Filesystem]"
check "Krasicki parts manifest exists" \
  "ls /workspace/artifacts/krasicki_parts_manifest.*" \
  "krasicki_parts_manifest"
check "Monograph Sept draft exists" \
  "ls /workspace/monograph/ch4_sept_draft.md" \
  "ch4_sept_draft.md"
check "Provenance notes exist" \
  "ls /workspace/radziwill/provenance_notes.md" \
  "provenance_notes.md"

echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
