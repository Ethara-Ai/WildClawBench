#!/usr/bin/env bash
# stage2 verification: assert overnight email/PDF drops + SM2 contact auto-suggest + SM3 Hamilton price drift.
set -euo pipefail
WORKSPACE="${WORKSPACE_ROOT:-/workspace}"
CONTACTS_PORT="${CONTACTS_PORT:-8069}"
HAMILTON_PORT="${HAMILTON_PORT:-8088}"

echo "[stage2] verifying overnight drops at: $WORKSPACE"

required_files=(
  "$WORKSPACE/inbox/2026-12-08_mla_linkedin.eml"
  "$WORKSPACE/inbox/2026-12-08_yard_substitute.eml"
)
for f in "${required_files[@]}"; do
  if [[ ! -e "$f" ]]; then
    echo "FAIL: missing $f"
    exit 2
  fi
  echo "  ok: $f"
done

curl -fsS "http://localhost:$CONTACTS_PORT/v1/people:autosuggest" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); names=[c['contact']['displayName'] for c in d.get('suggestions',[])]; assert 'Brenda Thibault' in names, 'SM2 Brenda auto-suggest not present'; print('  ok: SM2 Brenda Thibault auto-suggest is queued')"

curl -fsS "http://localhost:$HAMILTON_PORT/inventory/3071" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); p=d['price_usd']; assert p==27.95, f'SM3 hamilton price wrong: {p}'; print('  ok: SM3 hamilton impeller price silently mutated to $27.95')"

# SM8 - Cummins TSB-247B silently re-issued as Rev1 with narrowed serial range 1700001-1799999
CUMMINS_PORT="${CUMMINS_PORT:-8089}"
curl -fsS "http://localhost:$CUMMINS_PORT/bulletins/TSB-247B" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); r=d.get('affected_serial_range',''); rv=d.get('revision',''); assert r=='1700001-1799999', f'SM8 TSB Rev1 range wrong: {r}'; assert rv=='Rev1', f'SM8 TSB revision wrong: {rv}'; print('  ok: SM8 TSB-247B silently re-issued as Rev1 with narrowed range 1700001-1799999')"

echo "[stage2] PASS"
exit 0
