# Multi-Turn Injection

How the WildClawBench harness applies silent + loud + filesystem state mutations
between agent turns to simulate real-world drift over a multi-turn task.

Source of truth: `src/utils/inject_director.py` (loader/applier),
`eval/run_batch.py` (mock-stack admin plane + per-turn dispatch), and
`scripts/check_injection.py` (dry-run verifier).

---

## 1. What it is

A task can ship an `inject/` directory next to `prompts.txt`. The directory
holds one or more **stages**, each describing a batch of mutations that fire
between two specific agent turns. Mutations come in three flavors:

| Flavor | What it does | How the agent notices |
|---|---|---|
| **`filesystem`** | Drops files into the agent's `/tmp_workspace/` | Visible — the agent can list/read the new files |
| **`loud`** | Hits the mock API via the *public* endpoints | Visible in the API's audit feed if the agent re-queries |
| **`silent`** | Hits the mock API via the *admin plane* (`/admin/*`) | NOT in the audit feed — the agent only sees the new value if it re-reads the underlying record |

Loud mutations are mostly used for the **stage 0 seed** (baseline data the task
needs before turn 0). The mid-task drift that the agent is supposed to detect
or miss is almost always **silent**.

---

## 2. Directory layout

```
input/<task>/inject/
├── README.md              # human-facing rationale (optional)
├── stage0/
│   └── STAGE0_INJECT.json # seed — applied before turn 0
├── stage1/
│   └── STAGE1_INJECT.json # fires between two specific turns
└── stage2/
    └── STAGE2_INJECT.json
```

Per stage the loader accepts either `mutations.json` (LAYLA convention) or
`STAGE{N}_INJECT.json` (Talos export convention). Whichever exists is loaded;
otherwise the stage is skipped with a warning.

Stage subdirs are sorted by numeric index. **Stage 0 is always treated as the
seed**, regardless of what its file says about boundaries.

---

## 3. Stage file shapes

The loader accepts two shapes.

### Form A — LAYLA buckets

```json
{
  "applies_between_turns": [12, 13],
  "mutations": {
    "filesystem": [
      {"action": "copy", "src": "...", "dst": "/tmp_workspace/..."}
    ],
    "loud": [
      {"service": "airtable-api", "method": "PATCH",
       "url": "{AIRTABLE_API_URL}/...", "body": {...}}
    ],
    "silent": [
      {"service": "airtable-api", "method": "PATCH",
       "url": "{AIRTABLE_API_URL}/...", "body": {...}}
    ]
  }
}
```

### Form B — Talos flat array

```json
{
  "stage": "stage1",
  "description": "Müller portal silently revises price and date",
  "mutations": [
    {
      "mutation_id": "stage1.muller.price_revision",
      "service": "muller-portal-api",
      "method": "PATCH",
      "url": "{MULLER_API_URL}/orders/MUE-2026-0918",
      "body": {"price_eur": 668, "delivery_date": "2026-11-09"},
      "silent": true,
      "fires_after_turn": 5
    }
  ]
}
```

Some Talos exports use the key `injections:` instead of `mutations:` — the
loader tries `mutations` first, then `injections`.

In Form B the loader buckets each op:

| Op contains | Goes to bucket |
|---|---|
| `silent: true` OR `bucket: "silent"` | `silent` |
| `action: ...` OR `bucket: "filesystem"` | `filesystem` |
| otherwise (has `service` or `path`) | `loud` |

---

## 4. Boundary resolution (when does a stage fire?)

A stage fires **after** one turn finishes and **before** the next turn starts.
The pair of turn indexes is the stage's `(from_turn, to_turn)`.

The loader looks for the boundary in this order:

1. Top-level `applies_between_turns: [N, M]` (LAYLA).
2. Top-level `applied_between: [N, M]` (alias).
3. Top-level `fires_after_turn: N` (Talos seed/export) → synthesizes
   `[N, N+1]`.
4. **Stage 0 only**: defaults to `(None, None)` — interpreted as the seed
   that fires before turn 0.

If none of the above match and the stage index is not 0, the stage is loaded
as a seed (`(None, None)`) and `stage_for_boundary(turn_index)` will never
return it. **This is the most common silent failure mode** — see Pitfalls.

> **Known gap**: Talos-style Form B files with per-op `fires_after_turn`
> inside each mutation but no top-level `fires_after_turn` currently fall
> through to seed. Lift one op's `fires_after_turn` to the top level as a
> workaround until the loader patch lands.

---

## 5. Apply path

When a stage fires the harness loops through its three buckets:

### Filesystem ops
`copy_into_workspace(src, dst)` copies the host file into the agent's
`/tmp_workspace/` inside the running container.

### Loud ops
HTTP request through the **public** API endpoint (e.g.
`PATCH http://mocks-task-<batch>:<port>/airtable-api/v0/.../<row_id>`).
Recorded in the API's normal audit log. The mock-stack stage 0 baseline data
is usually pre-loaded into the overlay CSVs, so loud ops are rarely replayed
at run time.

### Silent ops
HTTP request through the **admin plane** — every mock-stack API publishes a
`/admin/*` namespace gated by the `X-Admin-Token` header. The admin plane
mutates the underlying row without producing an audit-feed event.

The applier resolves Talos-style placeholders before sending:

- `{rec_UDI-2026-007}` → `UDI-2026-007` (case-insensitive).
- `{AIRTABLE_API_URL}` → the host URL injected by the mock-stack.
- Business-key columns by service (`_SERVICE_RESOLUTION`):
  - `airtable-api` → table prefix `records_`, keys `PlotID|plot_id|Name|name|id`
  - `notion-api` / `confluence-api` → table prefix `pages`, key `title|Name|name|id`

If a placeholder cannot be resolved against any existing row, the op is logged
as `unresolved` (not dropped — it appears in the timeline for diagnosis).

---

## 6. Harness wiring

```
eval/run_batch.py
  ↓ detects input/<task>/inject/
  ↓ sets task['inject_path']
  ↓ starts mock-stack in admin mode → exposes /admin/* + MOCK_ADMIN_TOKEN
  ↓ InjectScript.load(inject_path) → list[InjectStage]
  ↓ InjectApplier(host_api_to_url, admin_token, copy_into_workspace)
  ↓ builds stage_before_turn(turn_index) closure
  ↓ passes into AgentTaskSpec(before_turn=stage_before_turn)

openclaw runner (per turn)
  ↓ before each turn calls spec.before_turn(turn_index)
  ↓ → InjectScript.stage_for_boundary(turn_index)
  ↓ → if non-seed stage with stage.to_turn == turn_index:
  ↓      applier.apply_stage(stage, turn_index)
```

Seed stages (`from_turn=None`) never match `stage_for_boundary` queries — they
are applied separately at run start before turn 0.

---

## 7. Telemetry: `inject_timeline.jsonl`

Every applied/skipped op writes a row to `<run_dir>/inject_timeline.jsonl`.

```json
{
  "ts": "2026-06-12T13:08:10Z",
  "stage": "stage1",
  "turn_index": 5,
  "mutation_id": "stage1.muller.price_revision",
  "service": "muller-portal-api",
  "bucket": "silent",
  "status": "applied",
  "before": {"price_eur": 645, "delivery_date": "2026-10-26"},
  "after":  {"price_eur": 668, "delivery_date": "2026-11-09"},
  "changed": ["price_eur", "delivery_date"]
}
```

Status enum:

| Status | Meaning |
|---|---|
| `applied` | Admin/public call returned 2xx, row updated |
| `unresolved` | Placeholder couldn't be matched to a row |
| `skipped` | Stage matched but op was a no-op (e.g. `silent:false` in stage-0 replay) |
| `error` | Admin/public call returned non-2xx or network failed |

Greppable summary header is also logged: e.g.
`inject stage 'stage1' applied before turn 5: 3 silent op(s)`.

---

## 8. Prompt parsing — header preservation

`parse_prompts_file()` reads `prompts.txt` headers with this regex:

```python
_TURN_RE = re.compile(r"^---\s*(TURN\s+T?(\d+)\b.*?)\s*---\s*$", re.IGNORECASE)
```

Group 1 = full inner text (`TURN 5 (Day 2, 09:00, Multi-Agent)`), Group 2 =
numeric index. Each parsed turn body is prefixed with the bracket-tagged
header so the model sees:

```
[TURN 5 (Day 2, 09:00, Multi-Agent)]

<user prompt body>
```

This is what lets persona rules like *"when a turn is labelled Multi-Agent
you must fan out"* fire reliably — the literal `Multi-Agent` token survives
to the model's user turn.

---

## 9. Verification: `scripts/check_injection.py`

Dry-runs the loader + applier against the task's own mock stack with no agent
and no LLM. Use it before any expensive end-to-end run.

```bash
python3 scripts/check_injection.py "input/Ruth Armstrong"
```

Reports per-op `applied | unresolved | skipped | error` along with the
before/after snapshots from `/admin/data/<table>/<pk>`. Spins a dedicated
network + mock container, tears them down at exit. Cost: ~30–60 s, free.

If you see every silent op as `applied` here but `inject_timeline.jsonl` is
empty in a real run, the failure is in boundary resolution (Section 4), not in
the applier.

---

## 10. Common pitfalls

1. **Per-op `fires_after_turn` without a top-level one** — Talos Form B
   variant. Loader treats the whole stage as a seed → stages 1+, 2+ never fire
   even though every op looks valid. Workaround: lift the value to the top
   level until the loader patch lands.
2. **`mutations.json` vs `STAGE{N}_INJECT.json`** — pick one; don't ship both,
   loader takes the first hit and warns on the other.
3. **`mutations:` vs `injections:`** — both work, but pick one shape per stage.
4. **Service name typo** — op lands in `unresolved`; check `host_api_to_url`
   for the expected name.
5. **Placeholder doesn't match any row** — check `_SERVICE_RESOLUTION` for the
   service's business-key columns. Add a row to mock_data or fix the
   placeholder.
6. **Stage 0 ops trying to fire mid-task** — stage 0 is always seeded before
   turn 0, no matter what its file says about boundaries.
7. **Mock-stack started without admin mode** — silent ops will 404 because
   `/admin/*` isn't published. The harness auto-enables admin mode when
   `task['inject_path']` is set, so this only happens if you bypass `run_batch`.
8. **Header preservation skipped** — if a task uses `--- TURN 5 ---` without
   the trailing label (e.g. no `(Multi-Agent)`), the regex still matches but
   the persona has nothing to route on. Always carry the label.

---

## Quick mental model

> The harness is a state machine. Stage 0 sets the world. Each subsequent
> stage is a scheduled diff against the world. The agent sees the world
> through API calls; silent stages change the world without leaving a
> footprint in the audit feed, so the agent only notices if it actively
> re-reads.
