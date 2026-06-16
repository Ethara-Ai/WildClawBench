#!/usr/bin/env bash
# stage3 verification: assert overnight drops + SM4 work-order $230 + SM5 boat fund auto-debit + SM6 Co-op final $384.50.
set -euo pipefail
WORKSPACE="${WORKSPACE_ROOT:-/workspace}"
GMAIL_PORT="${GMAIL_PORT:-8017}"
COOP_PORT="${COOP_PORT:-8090}"

echo "[stage3] verifying overnight drops at: $WORKSPACE"

required_files=(
  "$WORKSPACE/inbox/2026-12-09_yard_completion.eml"
  "$WORKSPACE/inbox/2026-12-09_hanover_janet.eml"
  "$WORKSPACE/inbox/2026-12-10_coop_final.eml"
)
for f in "${required_files[@]}"; do
  if [[ ! -e "$f" ]]; then
    echo "FAIL: missing $f"
    exit 2
  fi
  echo "  ok: $f"
done

curl -fsS "http://localhost:$GMAIL_PORT/messages?subject=Eileen+C+work+order" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); m=d['messages'][0]; assert '\$230' in m['body'], 'SM4 work-order \$230 not in body'; print('  ok: SM4 yard email shows \$230 line item (verbal said \$210)')"

if [[ -f "$WORKSPACE/data/Alden_finances_seed_2026-12.csv" ]]; then
  bf=$(awk -F, '/boat_fund/ {print $NF}' "$WORKSPACE/data/Alden_finances_seed_2026-12.csv" | tr -d '$,' )
  if [[ "$bf" == "2970"* ]] || [[ "$bf" == "2970.00" ]]; then
    echo "  ok: SM5 boat fund silently auto-debited to \$2,970.00"
  else
    echo "FAIL: SM5 boat fund expected 2970.00, got $bf"
    exit 3
  fi
fi

curl -fsS "http://localhost:$COOP_PORT/settlements/final?period=2026-11" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); a=d['amount_usd']; assert a==384.50, f'SM6 Co-op final wrong: {a}'; print('  ok: SM6 Co-op final settlement posted \$384.50 (decoy \$420 in archive)')"

echo "[stage3] PASS"
exit 0
