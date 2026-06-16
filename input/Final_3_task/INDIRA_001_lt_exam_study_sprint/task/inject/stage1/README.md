# FIRE_EDU_001 stage1 — day1_to_day2

**Persona:** Indira Hudson
**Stage:** 1 of 4
**Applied:** between Day 1 and Day 2

## Description

Mutations applied between Day 1 end-of-day and Day 2 wake-up. Includes overnight email batch, silent service-state edits, and decoy artifact arrivals.

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
