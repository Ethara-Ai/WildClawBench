#!/usr/bin/env bash
# stage1 verification: assert overnight email/note drops landed and SM1 calendar PATCH visible.
set -euo pipefail
WORKSPACE="${WORKSPACE_ROOT:-/workspace}"
CAL_PORT="${CAL_PORT:-8016}"

echo "[stage1] verifying overnight drops at: $WORKSPACE"

required_files=(
  "$WORKSPACE/inbox/2026-12-07_yard_final_reminder.eml"
  "$WORKSPACE/inbox/2026-12-08_cummins_tsb_reminder.eml"
  "$WORKSPACE/inbox/2026-12-08_donna_soup_note.md"
)
for f in "${required_files[@]}"; do
  if [[ ! -e "$f" ]]; then
    echo "FAIL: missing $f"
    exit 2
  fi
  echo "  ok: $f"
done

curl -fsS "http://localhost:$CAL_PORT/calendar/v3/calendars/primary/events/evt-haul-out-2026-12-09" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); s=d['start']['dateTime']; assert s=='2026-12-09T08:30:00-05:00', f'SM1 haul-out time wrong: {s}'; print('  ok: SM1 haul-out time silently moved to 08:30 ET')"

echo "[stage1] PASS"
exit 0
