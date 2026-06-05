#!/usr/bin/env bash
set -euo pipefail
WORKSPACE="${WORKSPACE_ROOT:-/workspace}"

echo "[stage1] verifying overnight Wed->Thu mutations landed"

required_files=(
  "$WORKSPACE/grants/waita-eacri/2026-10/WAITA_proposal_v8.1_FINAL.pdf"
  "$WORKSPACE/grants/waita-eacri/2026-10/WAITA_budget_v8.1.xlsx"
  "$WORKSPACE/grants/waita-eacri/inbox/Adaeze_WAADA_email_2026-10-02_0700.eml"
  "$WORKSPACE/family/Marcus_whatsapp_2026-10-02_0234.txt"
)

for f in "${required_files[@]}"; do
  [[ -f "$f" ]] || { echo "[stage1] FAIL — missing file: $f"; exit 1; }
done
echo "[stage1] filesystem OK"

audit_check_increase() {
  local service="$1" port="$2" method="$3" min_count="$4"
  local got
  got=$(curl -s "http://localhost:${port}/audit/summary" | python3 -c "import sys,json; s=json.load(sys.stdin); print(s.get('by_method',{}).get('${method}',0))")
  if (( got < min_count )); then
    echo "[stage1] FAIL — ${service} expected at least ${min_count} ${method} requests, got ${got}"; exit 1
  fi
  echo "[stage1] ${service} ${method} count = ${got} (OK, >= ${min_count})"
}

audit_check_increase "airtable-api" 8049 "PATCH" 1
audit_check_increase "gmail-api"    8017 "POST"  1

PLOT_007_YIELD=$(curl -s "http://localhost:8049/v0/appWAITAEACRI/Field-Trial-Udi/records?filterByFormula=plot_id%3D%22UDI-2026-007%22" \
  | python3 -c "import sys,json; r=json.load(sys.stdin); print(r['records'][0]['fields']['yield_kg_m2'])")
if [[ "$PLOT_007_YIELD" != "16.8" ]]; then
  echo "[stage1] FAIL — SM3 did not land; UDI-2026-007 yield_kg_m2 = $PLOT_007_YIELD (expected 16.8)"; exit 1
fi
echo "[stage1] SM3 landed (UDI-2026-007 yield_kg_m2 = 16.8 mutated, canonical was 14.2)"

distractor_total() {
  local port="$1"
  curl -s "http://localhost:${port}/audit/summary" | python3 -c "import sys,json; print(json.load(sys.stdin).get('total_requests', 0))"
}
[[ "$(distractor_total 8039)" -eq 0 ]] || { echo "[stage1] FAIL — spotify-api leaked"; exit 1; }
[[ "$(distractor_total 8005)" -eq 0 ]] || { echo "[stage1] FAIL — myfitnesspal-api leaked"; exit 1; }
echo "[stage1] distractor APIs clean"

echo "[stage1] ALL CHECKS PASSED"
