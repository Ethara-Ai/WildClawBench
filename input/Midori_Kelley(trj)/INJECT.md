# inject/

Staged world-state mutations for the Rimrock Valley Herd-Health Crunch task.

## Stage semantics

| Stage | When it fires | Purpose |
|---|---|---|
| `stage0/` | After TURN_0 (seed anchor) | Pre-scenario seed anchor — no mutations. Grounds the runtime anchor before T1; the Henderson Zoetis baseline values and the 22 `data/` seed objects ship pre-loaded via the `mock_data/` fixtures. |
| `stage1/` | Overnight between TURN_4 and TURN_5 (Thu evening to Fri morning) | Silent vendor-portal revision. Mutates hubspot.deals.amount $667.40→$695.60, fedex.tracking.estimated_delivery 2026-10-27→2026-11-02, and salesforce notes referencing the Nov 2 window. No email lands (Email = DISABLED on the distributor portal). This is the F1 trap family that Day-2 reconciliation must detect. |
| `stage2/` | Overnight between TURN_9 and TURN_10 (Fri evening to Sat morning) | Race-day adjacency mutations: weather, gate-in timing reaffirmation (09:30 PT addendum survives across surfaces), eventbrite run-order finalization. |

Each `stage<N>/` directory contains a `STAGE<N>_INJECT.json` driver describing the PATCH/POST operations to apply against the mock services, plus any payload files referenced by the driver.

Payload files referenced from stage drivers live under `data/` (canonical) or alongside the driver inside the stage folder.
