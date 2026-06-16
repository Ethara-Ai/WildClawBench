# FIRE_EDU_001 stage0 — seed_initial_state

**Persona:** Indira Hudson
**Stage:** 0 of 4
**Applied:** before T0 (seed)

## Description

Initial state seeded BEFORE T0. Establishes persona workspace, baseline artifacts, recurring calendar events, and baseline metric data. No traps triggered yet.

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
