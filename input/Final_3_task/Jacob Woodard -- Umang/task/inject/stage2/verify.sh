#!/usr/bin/env bash
# Stage 2 verification — confirms SM4, SM3-bx, SM6-at, SM8-bx landed between T25→T26
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

echo "=== Stage 2 Verification (Overnight Wed→Thu) ==="
echo ""

# --- SM4: Portal PDF status change ---
echo "[SM4 — Portal PDF qa_status: in_progress→on_hold]"
check "Portal PDF exists" \
  "ls -la /workspace/artifacts/stefan_muller_portal_oct6.pdf" \
  "stefan_muller_portal_oct6.pdf"
check "Portal PDF contains 'on_hold'" \
  "pdftotext /workspace/artifacts/stefan_muller_portal_oct6.pdf - 2>/dev/null | tr -d '\n'" \
  "on_hold"
# Cross-modal contradiction: origin should STILL say Biel (stale)
check "Portal origin still shows Biel/Bienne (stale)" \
  "pdftotext /workspace/artifacts/stefan_muller_portal_oct6.pdf - 2>/dev/null | tr -d '\n'" \
  "Biel/Bienne"

# --- SM3-bx: Box file renames ---
echo "[SM3-bx — Radziwill file renames]"
check "New condition report exists (RW-2026-CR-001)" \
  "curl -s http://localhost:8083/2.0/files/radziwill_folder | jq -r '.entries[].name'" \
  "RW-2026-CR-001_condition_assessment.pdf"
check "New provenance record exists (RW-2026-PN-001)" \
  "curl -s http://localhost:8083/2.0/files/radziwill_folder | jq -r '.entries[].name'" \
  "RW-2026-PN-001_provenance_record.md"
check_not "Old condition report name removed" \
  "curl -s http://localhost:8083/2.0/files/radziwill_folder | jq -r '.entries[].name'" \
  "radziwill_condition_report.pdf"
check_not "Old provenance notes name removed" \
  "curl -s http://localhost:8083/2.0/files/radziwill_folder | jq -r '.entries[].name'" \
  "radziwill_provenance_notes.md"
# Filesystem mirrors
check "Filesystem: new condition report exists" \
  "ls /workspace/radziwill/RW-2026-CR-001_condition_assessment.pdf" \
  "RW-2026-CR-001_condition_assessment.pdf"
check "Filesystem: new provenance record exists" \
  "ls /workspace/radziwill/RW-2026-PN-001_provenance_record.md" \
  "RW-2026-PN-001_provenance_record.md"
check_not "Filesystem: old condition report removed" \
  "ls /workspace/radziwill/ 2>/dev/null" \
  "radziwill_condition_report.pdf"

# --- SM6-at: Wrong row edit ---
echo "[SM6-at — SC-003 balance_staff_diameter: 0.86→0.84 (wrong row)]"
check "SC-003 balance_staff_diameter = 0.84 (changed)" \
  "curl -s http://localhost:8032/v0/{base_id}/Workshop-Parts-Inventory/records | jq -r '.records[] | select(.fields.Part_ID==\"SC-003\") | .fields.balance_staff_diameter'" \
  "0.84"
# KR-004 should remain unchanged
check "KR-004 balance_staff_diameter still 0.85 (untouched)" \
  "curl -s http://localhost:8032/v0/{base_id}/Workshop-Parts-Inventory/records | jq -r '.records[] | select(.fields.Part_ID==\"KR-004\") | .fields.balance_staff_diameter'" \
  "0.85"
# RZ-005 should remain unchanged — now matches SC-003
check "RZ-005 balance_staff_diameter still 0.84" \
  "curl -s http://localhost:8032/v0/{base_id}/Workshop-Parts-Inventory/records | jq -r '.records[] | select(.fields.Part_ID==\"RZ-005\") | .fields.balance_staff_diameter'" \
  "0.84"

# --- SM8-bx: Unauthorized document ---
echo "[SM8-bx — Unauthorized assessment inject]"
check "Unauthorized PDF in Sophie Crawford folder" \
  "curl -s http://localhost:8083/2.0/files/sophie_crawford_folder | jq -r '.entries[].name'" \
  "Woodard_Horology_Preliminary_Assessment.pdf"
check "Filesystem: unauthorized PDF exists" \
  "ls /workspace/radziwill/Woodard_Horology_Preliminary_Assessment.pdf" \
  "Woodard_Horology_Preliminary_Assessment.pdf"

echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="
exit $FAIL
