#!/usr/bin/env bash
# Stage 1 verification — confirms SM1 and SM2 landed between T12→T13
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

echo "=== Stage 1 Verification (Overnight Tue→Wed) ==="
echo ""

# --- SM1: Airtable mainspring qty change ---
echo "[SM1 — Airtable KR-001 qty: 3→5]"
check "KR-001 qty = 5" \
  "curl -s http://localhost:8032/v0/{base_id}/Workshop-Parts-Inventory/records | jq -r '.records[] | select(.fields.Part_ID==\"KR-001\") | .fields.qty'" \
  "5"
check "KR-001 last_modified_by = mark.jennings" \
  "curl -s http://localhost:8032/v0/{base_id}/Workshop-Parts-Inventory/records | jq -r '.records[] | select(.fields.Part_ID==\"KR-001\") | .fields.last_modified_by'" \
  "mark.jennings"

# --- SM2: Portal PDF qa_window change ---
echo "[SM2 — Portal PDF qa_window: 5→7 days]"
check "Portal PDF exists at expected path" \
  "ls -la /workspace/artifacts/stefan_muller_portal_oct6.pdf" \
  "stefan_muller_portal_oct6.pdf"
# Note: PDF content verification requires pdftotext or similar tool
# The orchestrator should verify the qa_window field after extraction
check "Portal PDF contains '7 business days'" \
  "pdftotext /workspace/artifacts/stefan_muller_portal_oct6.pdf - 2>/dev/null | tr -d '\n'" \
  "7 business days"

# --- Negative checks: values that should NOT have changed ---
echo "[Negative checks — unchanged values]"
check "KR-004 balance_staff_diameter still 0.85" \
  "curl -s http://localhost:8032/v0/{base_id}/Workshop-Parts-Inventory/records | jq -r '.records[] | select(.fields.Part_ID==\"KR-004\") | .fields.balance_staff_diameter'" \
  "0.85"
check "SC-003 balance_staff_diameter still 0.86" \
  "curl -s http://localhost:8032/v0/{base_id}/Workshop-Parts-Inventory/records | jq -r '.records[] | select(.fields.Part_ID==\"SC-003\") | .fields.balance_staff_diameter'" \
  "0.86"
check "Portal origin still Biel/Bienne" \
  "pdftotext /workspace/artifacts/stefan_muller_portal_oct6.pdf - 2>/dev/null | tr -d '\n'" \
  "Biel/Bienne"

echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="
exit $FAIL
