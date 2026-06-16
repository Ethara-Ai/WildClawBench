#!/usr/bin/env bash
# Stage 3 verification — confirms SM9 stale-data and SM10 Gniezno change between T37→T38
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

check_not() {
  local desc="$1" cmd="$2" unexpected="$3"
  result=$(eval "$cmd" 2>/dev/null || echo "ERROR")
  if echo "$result" | grep -q "$unexpected"; then
    echo "  FAIL: $desc (found unexpected '$unexpected')"
    ((FAIL++))
  else
    echo "  PASS: $desc"
    ((PASS++))
  fi
}

echo "=== Stage 3 Verification (Overnight Thu→Fri) ==="
echo ""

# --- SM9-n: Notion stale delivery date ---
# NOTE: SM9 mutations are "no change" traps — we verify the data is STILL stale
# (i.e., the orchestrator did NOT fix it). The AGENT is supposed to detect and fix it.
echo "[SM9-n — Notion: Krasicki delivery still stale]"
check "Notion Krasicki delivery still shows Oct 26 (stale — agent should fix)" \
  "curl -s http://localhost:8010/v1/databases/workshop_projects_db/query | jq -r '.results[] | select(.properties.Name.title[0].text.content==\"Krasicki Estate Clock\") | .properties.Delivery.date.start'" \
  "2026-10-26"

# --- SM9-ln: Linear stale parts arrival ---
echo "[SM9-ln — Linear: Krasicki parts arrival still stale]"
check "Linear Krasicki parts ETA still shows Oct 12 (stale — agent should fix)" \
  "curl -s http://localhost:8004/issues | jq -r '.[] | select(.title | contains(\"Parts QA\")) | .due_date'" \
  "2026-10-12"

# --- SM9-at: Airtable stale shipping tracker ---
echo "[SM9-at — Airtable: Shipping tracker still stale]"
check "Airtable shipping ETA still shows Oct 12 (stale — agent should fix)" \
  "curl -s http://localhost:8032/v0/{base_id}/Shipping-Tracker/records | jq -r '.records[0].fields.ETA'" \
  "2026-10-12"

# --- SM10: Gniezno attribution change ---
echo "[SM10 — Dropbox ch4_sept_draft.md: Gniezno 1798→1802]"
check "Dropbox chapter 4 now shows 1802" \
  "grep -c '1802' /workspace/monograph/ch4_sept_draft.md" \
  "1"
check_not "Dropbox chapter 4 no longer shows 1798 for Gniezno" \
  "grep 'Gniezno.*1798' /workspace/monograph/ch4_sept_draft.md" \
  "1798"
# Obsidian should still show 1798 (unchanged)
check "Obsidian chapter 4 still shows 1798 (unchanged)" \
  "grep -c '1798' /workspace/monograph/ch4_oct_working.md" \
  "1"
# Confluence should still show 1800 (seed value, read-only)
check "Confluence Gniezno still shows 1800 (seed, read-only)" \
  "curl -s http://localhost:8045/wiki/rest/api/content | jq -r '.results[0].body.storage.value'" \
  "1800"

# --- Verify three-way discrepancy exists ---
echo "[Three-way discrepancy verification]"
echo "  Dropbox: 1802 (changed by Mitchell)"
echo "  Obsidian: 1798 (unchanged)"
echo "  Confluence: 1800 (seed)"
echo "  Agent must flag this three-way conflict."

echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="
exit $FAIL
