# FIRE_EDU_001 stage3 — day3_to_day4

**Persona:** Indira Hudson
**Stage:** 3 of 4
**Applied:** between Day 3 and Day 4

## Description

Mutations applied between Day 3 end-of-day and Day 4 wake-up. Includes F9 saturation seeds (Day-1 facts to recall) and final-day audit setup.

## Mutations

See `mutations.json` for the full mutation manifest. Key categories:
- `filesystem.*`: Files copied or placed at the orchestrator workspace
- `api.*`: HTTP POST/PATCH calls replayed against mock APIs

## Verify

```bash
bash verify.sh
```

The verify script checks:
1. All required files exist on the workspace volume
2. Primary APIs received the expected mutations (via `/audit/summary`)
3. Distractor and NOT-CONNECTED APIs show zero requests (their state is still untouched)

Exit code 0 = stage OK and ready to advance to the next turn boundary.
