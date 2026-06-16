#!/usr/bin/env bash
# stage0 verification: assert all filesystem drops landed and API seeds visible.
# Run AFTER the orchestrator applies stage0 and BEFORE turn T0.
# Exits 0 on success, non-zero on first failure.
set -euo pipefail
WORKSPACE="${WORKSPACE_ROOT:-/workspace}"
GMAIL_PORT="${GMAIL_PORT:-8017}"
CAL_PORT="${CAL_PORT:-8016}"
CONTACTS_PORT="${CONTACTS_PORT:-8069}"
HAMILTON_PORT="${HAMILTON_PORT:-8088}"
CUMMINS_PORT="${CUMMINS_PORT:-8089}"

echo "[stage0] verifying workspace at: $WORKSPACE"

required_files=(
  "$WORKSPACE/persona/alden-croft/USER.md"
  "$WORKSPACE/persona/alden-croft/MEMORY.md"
  "$WORKSPACE/persona/alden-croft/AGENTS.md"
  "$WORKSPACE/persona/alden-croft/SOUL.md"
  "$WORKSPACE/persona/alden-croft/IDENTITY.md"
  "$WORKSPACE/persona/alden-croft/HEARTBEAT.md"
  "$WORKSPACE/persona/alden-croft/TOOLS.md"
  "$WORKSPACE/inbox/2026-12-04_yard_pre_haul.eml"
  "$WORKSPACE/inbox/2026-12-06_coop_weekly.eml"
  "$WORKSPACE/inbox/2026-11-30_cummins_bulletin.eml"
  "$WORKSPACE/inbox/Cummins_TSB-247B.pdf"
  "$WORKSPACE/inbox/Hamilton_order_confirmation_2026-12-07.pdf"
  "$WORKSPACE/templates/Rockland_Marine_work_order_template.docx"
  "$WORKSPACE/templates/yard_prep_instructions.md"
  "$WORKSPACE/data/Alden_catch_log_2026-11-30_to_12-06.csv"
  "$WORKSPACE/data/Alden_engine_log_2026.csv"
  "$WORKSPACE/data/Alden_finances_seed_2026-12.csv"
  "$WORKSPACE/data/Eileen_C_vessel_engine_spec.md"
  "$WORKSPACE/data/Alden_finances_tracker_2026.xlsx"
)
for f in "${required_files[@]}"; do
  if [[ ! -e "$f" ]]; then
    echo "FAIL: missing $f"
    exit 2
  fi
  echo "  ok: $f"
done

curl -fsS "http://localhost:$GMAIL_PORT/audit/summary" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); assert d.get('POST',0)>=3, 'gmail POST count < 3'; print('  ok: stage0 gmail seed POSTs visible')"
curl -fsS "http://localhost:$CAL_PORT/audit/summary" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); assert d.get('POST',0)>=2, 'calendar POST count < 2'; print('  ok: stage0 calendar seed POSTs visible')"
curl -fsS "http://localhost:$CONTACTS_PORT/audit/summary" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); assert d.get('POST',0)>=1, 'contacts POST count < 1'; print('  ok: stage0 contacts seed POSTs visible')"
curl -fsS "http://localhost:$HAMILTON_PORT/audit/summary" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); assert d.get('POST',0)>=1, 'hamilton POST count < 1'; print('  ok: stage0 hamilton inventory POST visible')"
curl -fsS "http://localhost:$CUMMINS_PORT/audit/summary" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); assert d.get('POST',0)>=1, 'cummins POST count < 1'; print('  ok: stage0 cummins bulletin POST visible')"

echo "[stage0] PASS"
exit 0
