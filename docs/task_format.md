# WildClawBench Standard Task Format

Canonical layout for any task placed under `input/<task_id>/`. The harness
(`eval/run_batch.py` + `src/utils/task_parser.py`) consumes tasks by convention
— files in the right place with the right shape are honored automatically;
files elsewhere are silently ignored.

See also: `docs/multi_turn_injection.md`, `docs/task_validator_prompt.md`,
`docs/task_template/` (working example).

---

## 1. Directory layout

```
input/<task_id>/
├── prompt.txt                    # OR prompts.txt (Talos multi-turn). At least one required.
├── prompts.txt                   # Multi-turn — headers `--- TURN N (Day X, HH:MM, Light|Multi-Agent) ---`
├── rubric.json                   # REQUIRED. Judge criteria.
├── task.yaml                     # Optional metadata sidecar.
├── test_outputs.py               # Optional weighted pytest suite.
├── test_weights.json             # Required iff test_outputs.py present.
├── golden_steer_flow.md          # Author narrative reference (not consumed).
├── README.md                     # Optional human overview.
├── solve.sh                      # Optional golden-trajectory replay.
├── persona/                      # 7 standard MDs FLAT (not nested under persona/<name>/)
│   ├── AGENTS.md
│   ├── HEARTBEAT.md
│   ├── IDENTITY.md
│   ├── MEMORY.md
│   ├── SOUL.md
│   ├── TOOLS.md
│   └── USER.md
├── data/                         # Free-form task artifacts (.txt, .pdf, .xlsx, .jpg, .mp3, ...)
├── mock_data/                    # Per-API CSV/JSON overlays
│   └── <service>-api/
│       ├── records_<table>.csv
│       └── <feature>.json
└── inject/                       # Multi-turn injection (optional)
    ├── stage0/STAGE0_INJECT.json    # Always seed (loud baseline data)
    ├── stage1/STAGE1_INJECT.json
    └── stage2/STAGE2_INJECT.json
```

Hard rules:
- Task ID = directory name. Spaces allowed but quote in shell (`"input/Ruth Armstrong"`).
- Either `prompt.txt` OR `prompts.txt` must exist. YAML-only loading is rejected.
- `rubric.json` must exist for the judge council.
- Persona MDs are flat at `persona/*.md`. Nested `persona/<name>/*.md` is the
  legacy GLORIA layout that was migrated; do not reintroduce.

---

## 2. `prompts.txt` (multi-turn) header convention

Regex (from `src/utils/inject_director.py:233`):

```
^---\s*(TURN\s+T?(\d+)\b.*?)\s*---\s*$    (re.IGNORECASE)
```

Examples that match:
- `--- TURN 1 (Day 1, 08:00, Light) ---`
- `--- TURN T2 (Day 1, 09:40, Multi-Agent) ---`
- `--- TURN 12 (Day 3, 14:00, Light) ---`

The full inner text is preserved as a bracket-tag prefix in the turn body:

```
[TURN 2 (Day 1, 09:40, Multi-Agent)]

<body text the model sees>
```

Conventions:
- Turn numbers are 1-indexed in `prompts.txt`, converted to 0-indexed
  `turn_index` internally.
- `Light` label = single-threaded turn.
- `Multi-Agent` label triggers `_synthesize_multi_agent_config` auto-detect
  (default `min_subagents=2`, `checker_id=T<n>_MA`, aggregate `MA_C1`). You
  may override by shipping `task_config.yaml` with explicit `expected_per_turn`.

---

## 3. `rubric.json` schema

Top-level: array of 8–40 criterion objects. Each entry:

| Key | Type | Notes |
|---|---|---|
| `number` | string | `R1`, `R2`, ... |
| `criterion` | string | Natural-language judge prompt |
| `is_positive` | bool | True = must satisfy; False = must avoid |
| `type` | enum | `task completion` \| `factuality and hallucination` \| `safety & boundaries` \| `instruction following` |
| `evaluation_target` | enum | `user_facing_message` \| `tool_call` \| `artifact` \| `workspace_state` |
| `importance` | enum | `critically_important` \| `important` |
| `score` | int | Weight (typically 5 for critical, 3 for important) |

`evaluation_target` should NOT be uniformly `user_facing_message` for every
criterion — that pattern means tool-call and artifact criteria are missing
(suspicious; see `docs/ruth_armstrong_task_issues.md` issue #6).

---

## 4. `task.yaml` metadata

**Honored keys** (whitelist in `src/utils/task_parser.py:_overlay_yaml_metadata`):

| Key | Effect |
|---|---|
| `difficulty` | Surfaced in trajectory metadata |
| `modalities` | Sets `multimodal` flag |
| `l1` / `taxonomy_l1` | Taxonomy label |
| `l2` / `taxonomy_l2` | Taxonomy label |
| `task_type` / `category` | Multi-label classification |
| `required_apis` / `required_mock_apis` | Mock stack starts these services |
| `distractor_apis` / `distractor_mock_apis` | Mock stack adds these distractors |

**Silently dropped** (NOT honored — agent never sees them):

- `system_prompt` — agent uses openclaw default boot prompt
- `task_description` — informational only
- `platform` — informational only

If you want a custom agent system prompt, the harness does not currently
deliver one through `task.yaml`. Document it for human readers but expect
it to be ignored at runtime.

---

## 5. `test_outputs.py` + `test_weights.json`

Two valid forms:

### Form A — HTTP-probe (recommended)

Tests open URLs directly against the running mock stack. No state fixture
needed.

```python
import os
from urllib.request import Request, urlopen
import pytest

GMAIL_API_URL = os.environ.get('GMAIL_API_URL', 'http://localhost:8017')

class TestGmailDraftPresent:
    def test_draft_present(self):
        resp = urlopen(f'{GMAIL_API_URL}/gmail/v1/users/me/drafts')
        assert resp.status == 200
```

### Form B — state fixture (legacy)

Tests take `state` fixture and read `state['Mock Data'][...]`,
`state['checkers'][...]`, `state['violations'][...]`. Only
`state['checkers']` + `state['violations']` are reliably populated; other
keys are not, so HTTP-probe Form A is preferred.

### `test_weights.json`

```json
{
  "TestGmailDraftPresent::test_draft_present": 3,
  "TestNoForbiddenSend::test_violation_absent": -5
}
```

- Positive weight = test must pass to earn points
- Negative weight = test that PASSES indicates a violation (penalty applied)
- Reward = `max(0, (pos_earned - neg_penalty) / pos_total)` (kensei2 formula)
- Avoid `test_behavioral_` / `test_negative_weight_` prefixes — use bare names
  (`test_twilio_queried`, `test_gmail_forbidden_send_detected`)

---

## 6. `persona/` directory

Seven standard MDs flat at `persona/*.md`:

| File | Purpose |
|---|---|
| `AGENTS.md` | Boot persona + standing rules + multi-agent guidance |
| `HEARTBEAT.md` | Dated session memory |
| `IDENTITY.md` | Who the persona is |
| `MEMORY.md` | Durable facts that survive sessions |
| `SOUL.md` | Tone / values |
| `TOOLS.md` | Active tool palette (required + distractors only) |
| `USER.md` | Who is talking to the agent |

If the task uses Multi-Agent turns, `AGENTS.md` should include a
`## Multi-Agent Turns` section (5 bullets):

1. When to fan out (trigger condition)
2. Where the skill lives (`spawn-subagent-connector`)
3. One sub-agent per angle (no monolith)
4. Sub-agents cannot spawn further sub-agents
5. Light turns are single-threaded by default

---

## 7. `mock_data/` overlays

One subdirectory per API named `<service>-api/`. Contains CSV and/or JSON
files that overlay the mock stack's default state.

```
mock_data/
└── gmail-api/
    ├── records_messages.csv
    ├── records_drafts.csv
    └── labels.json
```

Filename conventions:
- `records_<table>.csv` for table-shaped data
- `<feature>.json` for document-shaped data
- Headers must match the mock API's pydantic model — schema mismatch makes
  the API start but its `/healthz` never goes green
- All files must parse cleanly (validate with `csv.reader` + `json.load`)

---

## 8. `inject/` multi-turn stages

See `docs/multi_turn_injection.md` for full details. Quick reference:

```
inject/
├── stage0/STAGE0_INJECT.json    # Seed, replayed BEFORE turn 1
├── stage1/STAGE1_INJECT.json    # Applied between turns N and N+1
└── stage2/STAGE2_INJECT.json
```

Two supported shapes for each stage file:

**Form A — LAYLA buckets:**
```json
{
  "applies_between_turns": [5, 6],
  "mutations": {
    "filesystem": [...],
    "loud": [...],
    "silent": [...]
  }
}
```

**Form B — Talos flat array (Ruth Armstrong shape):**
```json
{
  "stage": "stage1",
  "description": "...",
  "mutations": [
    {
      "mutation_id": "stage1.gcal.attendance",
      "service": "google-calendar-api",
      "method": "PATCH",
      "url": "{GCAL_API_URL}/...",
      "body": {...},
      "silent": true,
      "fires_after_turn": 5
    }
  ]
}
```

Boundary resolution order:
1. Top-level `applies_between_turns: [N, M]`
2. Top-level `applied_between: [N, M]`
3. Top-level `fires_after_turn: N` → `[N, N+1]`
4. (Post-L fix) Per-op `fires_after_turn` scan → `[min, min+1]`
5. Stage 0 is ALWAYS seed regardless of file contents

Stage 0 loud ops are NOT replayed by default (already in `mock_data/`);
only filesystem drops seed. Set `replay_loud=True` to override.

---

## 9. `task.toml` (auto-emitted)

Generated by `src/utils/harbor/task_toml.py` from `task.yaml`. Key default:

| Key | Default | Notes |
|---|---|---|
| `pass_at_k` | `1` | Single-Claude-run. Was 8 pre-fix-D, now 1. Override per-task by setting explicitly. |

Do not hand-edit `task.toml`; edit `task.yaml` and let harbor regenerate.

---

## 10. Cross-file consistency rules

These checks must hold across a task pack:

1. **Mock APIs**: every service listed in `task.yaml:required_apis` + the ones
   referenced in `inject/*/STAGE*_INJECT.json` (via `service:` key) must have
   a corresponding `mock_data/<service>-api/` directory.

2. **Persona tools**: `persona/TOOLS.md` should list exactly the services in
   `task.yaml:required_apis` ∪ `distractor_apis`. Extra tools confuse the
   agent; missing tools cause silent unavailability.

3. **Multi-agent turns**: every turn header in `prompts.txt` labelled
   `Multi-Agent` must be matched by either (a) an explicit
   `task_config.yaml:expected_per_turn` entry, or (b) acceptance of the
   auto-detect default (`min_subagents=2`, `checker_id=T<n>_MA`).

4. **Rubric vs tests**: rubric criteria and `test_outputs.py` checks should
   not duplicate each other. Rubric tests subjective quality; pytest tests
   objective behavior (API calls, deliverable presence).

5. **Test weights**: every key in `test_weights.json` must be a real
   `TestClass::test_method` that pytest will discover. Stale keys are
   silently ignored.

6. **Inject service URLs**: `url` placeholders like `{GMAIL_API_URL}` must
   match env var names the mock stack publishes. The harness substitutes at
   apply time; typos cause `unresolved` status in `inject_timeline.jsonl`.

7. **Inject placeholders**: `{rec_UDI-2026-007}`-style placeholders are
   resolved via `_SERVICE_RESOLUTION` (`inject_director.py:280-284`).
   Currently supported: `airtable-api` → `records_*` / `PlotID`-style;
   `notion-api` → `pages*` / `title`-style; `confluence-api` → `pages*` /
   `title`-style. Other services require a literal pk in the URL.

---

## 11. Validation tools

- `scripts/check_injection.py <task_dir>` — dry-runs inject loader + applier
  against the mock stack without an LLM call (~30–60s, free).
- `docs/task_validator_prompt.md` — LLM checklist prompt (input: task tree
  + key file contents; output: per-section pass/fail).

---

## 12. Reference tasks

- `input/Ruth Armstrong/` — 15-turn Talos format with `inject/stage{0,1,2}/`.
  HTTP-probe `test_outputs.py`. Reference for new Talos tasks.
- `input/GLORIA/` — 13-turn with auto-detect multi-agent. Form-A
  `mock_data/` overlay.
- `input/LAYLA_001_october_grant_crunch/` — 50-turn LAYLA Form-A inject
  buckets.
- `input/Jacob Woodard/` — 12-turn, NO `inject/` (multi-turn injection axis
  not exercised — task author should add).
