# Declarative task authoring

Author a task as **four data files — no Python** — and compile it into a
native WildClawBench bundle. The harness is untouched: compiled bundles are
ordinary native tasks (`prompts.txt` + `rubric.json` + `test_outputs.py` +
`inject/`), validated by `script/preflight_task.py` like any other.

**The reference exemplar is `standard_task_template/` — copy it as the
starting point for every new task.** The full authoring rules live in
`docs/TASK_STANDARD.md`.

```
declarative/<task_id>/
├── metadata.json     # identity: difficulty, modalities, l1, l2, task_type,
│                     #   required_apis, distractor_apis; optional "banner"
├── prompt.txt        # the TURN T0 user message, verbatim
├── stages.toml       # [[stages]]: first block = pre-T0 world seed; each later
│                     #   block = next turn's message + optional mutations
├── rubrics.json      # "judge": LLM-council criteria; "checks": declarative
│                     #   deterministic checks (compiled to pytest)
├── persona/          # 7-file persona set            (copied verbatim)
├── data/             # agent-visible workspace files (copied verbatim)
├── mock_data/        # per-API seed overlays         (copied verbatim)
└── assets/           # files referenced by filesystem mutations
```

Compile and validate:

```bash
python3 script/compile_declarative_task.py declarative/<task_id> [--force]
python3 script/preflight_task.py declarative/_build/<task_id>
```

Promote to the corpus by copying `declarative/_build/<task_id>` into `input/`.
`input/` is gitignored — the declarative source is the only version-controlled
representation of a task, so edit the source and recompile; never hand-edit
the compiled copy.

## What the compiler guarantees (that hand-authoring doesn't)

- **Turn bookkeeping is generated**: `applies_between_turns` and
  `fires_at_turn` come from stage position, so the classic
  "stage silently never fires" bug cannot be authored
  (HARNESS_FLOW_README §5 calls it the most common injection failure).
- **Service names normalized** to their `service.toml` form (`gmail` →
  `gmail-api`) — the form both preflight and the runtime admin-URL map key on.
- **Weight vocabulary enforced** ({±5, ±3, ±1}) for judge criteria and checks.
- **test_weights.json keys always match** the generated test function names.
- **Everything validated at compile time** with file+location errors; the
  generated `test_outputs.py` is `py_compile`d before the bundle is written.

## stages.toml

```toml
[[stages]]                 # first block: pre-T0 seed MARKER (no `turn`).
name = "seed_world"        # Pre-T0 state does NOT go here — it lives in the
description = "..."        # mock_data/ overlays (API state) + data/ (files);
applied_at = "2026-07-27T08:00:00-05:00"   # stage0 API ops are never applied
turn_label = "Day 1, 08:15"          # optional: label for the T0 header

[[stages.silent]]          # in LATER blocks — buckets: silent / loud / filesystem
service = "gmail"          # normalized to gmail-api
method = "POST"            # descriptive provenance; the admin block is
path = "/gmail/v1/users/me/messages"   # what actually dispatches
description = "..."
[stages.silent.admin]      # REQUIRED on every API op: direct-dispatch form
op = "upsert"              # patch | upsert | update_where | doc_set
table = "messages"
[stages.silent.admin.row]  # typed values (ints/bools/arrays) — admin writes
id = "msg-1"               # bypass load-time coercion
# ...

[[stages]]                 # later blocks: one per turn T1..Tn
turn = "the user's next message"
turn_label = "Day 1, 14:40"
name = "silent_correction"
description = "..."

[[stages.filesystem]]
action = "copy"
src = "assets/report.pdf"  # relative to the source dir; copied into the stage
dst = "/root/workspace/home/Desktop/report.pdf"
```

A `[[stages]]` block with a `turn` but no mutations contributes only a
`prompts.txt` turn (no inject dir). Mutations attached to a turn's block fire
at the boundary **before** that turn.

Silent/loud mutation ops must carry an explicit `admin` block
(`{op: patch|update_where|upsert|doc_set, table, pk, set/row/...}`) alongside
the descriptive REST fields. The runtime applier dispatches an `admin` block
directly against the store — no fuzzy path/table resolution — which is the
only reliable form: fuzzy resolution fails on tables whose rows don't expose
an `id`/`pk` column (domain keys like `order_id`) and can never insert new
rows. Verify firings in the run's `inject_timeline.jsonl`.

## rubrics.json

```json
{
  "judge": [
    {"criterion": "One self-contained Yes/No-judgeable sentence.",
     "weight": 5, "type": "task completion", "evaluation_target": "final_answer"}
  ],
  "checks": [
    {"id": "outcome_reply_sent", "weight": 5, "description": "...",
     "check": {"type": "audit_request", "service": "gmail",
               "where": [
                 {"field": "method", "equals": "POST"},
                 {"field": "path", "contains_any": ["/messages/send"]},
                 {"field": "request_body", "contains_any": ["1,480"]}
               ]}}
  ]
}
```

Judge entries become `rubric.json` (numbered `R1..`, `is_positive` from the
weight sign, `importance` from |weight| unless the entry carries an explicit
`importance`). Negative-weight criteria must be phrased as "the forbidden
thing happened" (SATISFIED:Yes = violation).

`evaluation_target` declares which evidence channel proves a judge criterion:
`final_answer` (the agent's final chat message), `trajectory` (the tool-use
log), `state_change` (a system-of-record mutation), `user_facing_message`, or
`workspace_artifact` (a FILE the agent wrote under `/root/workspace/`). Only
`workspace_artifact` changes how the judge is steered: file-target criteria are
grounded to the `<output_files>` evidence — the judge grades the file's actual
contents, not the agent's narration, and an existence-only requirement passes
on the file's presence alone. Binary deliverables (pdf/xlsx/docx/pptx) surface
as present-but-not-extractable, so a *content* requirement on a binary scores
No until host-side text extraction lands; keep binary-file criteria
existence-only. `file_output`, `produced_file`, and bare `workspace` are
accepted aliases and normalize to `workspace_artifact`.

Check types compiled into `test_outputs.py` (stdlib-only, hermetic):

| type | params | passes when |
|---|---|---|
| `audit_request` | service, where, min_count=1 | ≥ min_count entries of the mock's `/audit/requests` match all conditions |
| `api_get` | service, path, where, result_key?, min_count=1 | ≥ min_count rows returned by GET match all conditions. `result_key` picks the row list from a dict response; `result_key = "."` treats the whole response object as the single row (for detail endpoints like `/v1/orders/{id}` whose response embeds unrelated lists) |
| `file_exists` / `file_valid_json` | path | workspace file exists / parses |
| `file_contains` | path, contains_any / regex_any | workspace file content matches |
| `any_of` / `all_of` | checks | boolean combinators |

Conditions: `{field, equals | equals_any | not_in | contains_any |
contains_all | regex_any}` — all present operators must hold; `{any = [...]}`
is an OR group. Values are normalized (lowercased, whitespace collapsed) on
both sides for the equality/substring operators; `regex_any` patterns run
against the normalized value with `re.IGNORECASE`.
Negative checks are violation detectors: write the check to MATCH the bad
behavior and give it a negative weight — and anchor them (regex on the exact
field/id) so a value an honest agent may legitimately mention cannot trigger
them.

## Worked example

`declarative/standard_task_template/` — 3 turns, an overlay-seeded inbox, a
silent revised-quote admin-upsert plus a filesystem drop between T0 and T1, a
turn-only stage, 6 judge criteria, and 8 deterministic checks (file checks,
anchored regexes, `any_of`/`all_of` send-path coverage, and two anchored
violation detectors — every check grades the agent, none grade the
environment). Its compiled bundle passes `script/preflight_task.py` clean.
For a graded `result_key: "."` example see `outcome_flagged_order_status` in
the davis_meal_calorie_check task.
