# Task Format Validation Report — `Midori _Kelley2`

Reference spec: `c:\Users\User\Downloads\task-3\TASK_FORMAT\task_format.md`
Validator prompt: `c:\Users\User\Downloads\task-3\TASK_FORMAT\task_validator_prompt.md`
Task directory: `c:\Users\User\Downloads\task-3\FINAL_BUNDLE\Midori _Kelley2\`
Inject form: **Talos Form B** (per-stage `mutations: [...]` arrays, top-level `fires_after_turn`).

---

## 1. Verdict

**PASS WITH WARNINGS** — zero `[hard]` failures; six `[warn]` items (all in §4 / §9 territory or documentation-drift caveats around inject).

---

## 2. Section checklist

| Section | Check | Result | Evidence | Fix |
|---|---|---|---|---|
| §1.1 | exactly one of `prompt.txt` / `prompts.txt` at root | pass | `prompts.txt` present (55 lines, 14 turns); no `prompt.txt` | — |
| §1.2 | `rubric.json` at root | pass | `rubric.json` (596 lines, 66 criteria) | — |
| §1.3 | every `mock_data/` child ends `-api` | pass | All 26 children named `<svc>-api/` (airtable-api, bamboohr-api, …, zoom-api) | — |
| §1.4 | every `inject/` child matches `stage[0-9]+` | pass | `stage0/`, `stage1/`, `stage2/` | — |
| §1.5 | `persona/*.md` flat (not nested) | pass | 7 files directly under `persona/` (AGENTS, HEARTBEAT, IDENTITY, MEMORY, SOUL, TOOLS, USER) | — |
| §2.1 | every turn header matches `^---\s*(TURN\s+T?(\d+)\b.*?)\s*---\s*$` (case-insensitive) | pass | 14 headers (`prompts.txt` lines 1, 5, 9, 13, 17, 21, 25, 29, 33, 37, 41, 45, 49, 53) all conform | — |
| §2.2 | 1-indexed, strictly monotonic from `TURN 1`, no gaps | pass | T1..T14 sequential | — |
| §2.3 | each header carries `Light` or `Multi-Agent` label | pass | T1, 3, 4, 6, 7, 8, 10, 12, 14 = Light; T2, 5, 9, 11, 13 = Multi-Agent | — |
| §2.4 | `Multi-Agent` exact spelling (hyphenated, both capitalised) | pass | All 5 MA headers spell `Multi-Agent` exactly | — |
| §3.1 | top-level list of objects with the 7 required keys | pass | All 66 entries carry `number, criterion, is_positive, type, evaluation_target, importance, score` | — |
| §3.2 | `type` ∈ {task completion, factuality and hallucination, safety & boundaries, instruction following} | pass | All 4 enum values appear; no unknown values | — |
| §3.3 | `evaluation_target` mix not uniformly `user_facing_message` | pass | Mix observed: `user_facing_message` (≈26), `artifact` (≈34), `workspace_state` (3), `tool_call` (3) — diverse | — |
| §3.4 | `importance` ∈ {critically_important, important} | pass | Both values present, no other values | — |
| §3.5 | `score` integer | pass | Integers only: −5, −3, −1, 1, 3, 5 | — |
| §3.6 | `is_positive` boolean | pass | Pure booleans throughout | — |
| §3.7 | `number` unique | pass | R1..R66 sequential, no repeats | — |
| §4.1 | top-level keys limited to whitelist ∪ documented-but-dropped | **fail [warn]** | `task.yaml` carries `id` (L8) and `name` (L9), which are neither honored nor in the documented-dropped set | Either drop `id`/`name` or accept that they are silently ignored by `_overlay_yaml_metadata`; the runtime task id comes from the directory name |
| §4.2 | `system_prompt` / `task_description` / `platform` warning | **fail [warn]** | `task.yaml` ships `system_prompt:` (L38), `task_description:` (L17), `platform:` (L560) — all three are silently dropped at runtime | Move custom system-prompt expectations into `persona/AGENTS.md` (already done) and treat the `task.yaml` copies as human-only documentation, or delete to avoid drift |
| §4.3 | `required_apis` ∩ `distractor_apis` = ∅ | pass | required (16): airtable, bamboohr, confluence, discord, eventbrite, fedex, gmail, google-calendar, google-maps, hubspot, instagram, openweather, salesforce, slack, twilio, whatsapp; distractor (9): dropbox, outlook, notion, trello, zoom, mailchimp, linear, plaid, stripe — disjoint. (Note: `google-drive-api` was removed from required; agent-authored docs now land as workspace files read directly by `test_outputs.py`.) | — |
| §5.1 | both `test_outputs.py` and `test_weights.json` present together | pass | Both present | — |
| §5.2 | every key in `test_weights.json` resolves to a real test in `test_outputs.py` | pass | All 45 keys map to top-level `def test_…` in `test_outputs.py` (matched bare names) | — |
| §5.3 | no unused `subprocess` / `sqlite3` imports | pass | Imports limited to `json`, `os`, `urllib.request` (L1-3) | — |
| §5.4 | no `try: import pytest / except ImportError` block | pass | No such block | — |
| §5.5 | no `test_behavioral_` / `test_negative_weight_` prefixes | pass | All names use bare/`test_outcome_` prefixes | — |
| §5.6 | negative-weight tests assert violation **present** | pass | 4 negatives: `test_discord_post_detected` (asserts `len(posts) > 0`), `test_instagram_auto_published` (asserts published+17.18 > 0), `test_hubspot_q1_reorder_auto_queued` (asserts `len(hits) > 0` for $695-696 POST), `test_gmail_kenji_content_detected` (asserts `len(hits) > 0`) — all detect-and-penalise direction | — |
| §6.1 | all 7 canonical persona MDs present | pass | AGENTS, HEARTBEAT, IDENTITY, MEMORY, SOUL, TOOLS, USER all present | — |
| §6.2 | `## Multi-Agent Turns` section in AGENTS.md when MA turns exist | pass | `persona/AGENTS.md` line 56 carries `## Multi-Agent Turns` with the 5 required bullets (fan-out trigger, spawn-subagent-connector, one angle per sub-agent, no recursive spawn, Light = single-threaded) | — |
| §6.3 | `persona/TOOLS.md` services ⊆ `required_apis ∪ distractor_apis` | pass | `persona/TOOLS.md` lists exactly the 25 services in the union (16 required + 9 distractor). Note: `task.yaml:system_prompt` embeds a much larger ~70-service TOOLS.md, but that copy is silently dropped at runtime, so only `persona/TOOLS.md` matters | — |
| §7.1 | each mock_data API dir has at least one CSV or JSON | pass | All 26 `<svc>-api/` dirs populated (CSV and/or JSON each) | — |
| §7.2 | every CSV parses cleanly | pass | `Import-Csv` on all 96 CSVs under `mock_data/` returned zero errors | — |
| §7.3 | every JSON parses cleanly | pass | `ConvertFrom-Json` on all JSON files (mock_data + rubric + stage0/1/2 + MANIFEST + test_weights) returned zero errors | — |
| §7.4 | `records_<table>.csv` / `<feature>.json` filename convention | pass | Airtable uses `records_*.csv` (vaccines, contacts, projects, tasks, competition_log) per convention; other services use feature-named csv/json | — |
| §8.1 | inject stage indices start at `stage0` | pass | `inject/stage0/STAGE0_INJECT.json` present (seed anchor, no mutations, fires after T0); mutation stages begin at `stage1` per harness convention | — |
| §8.2 | each stage has exactly one of `mutations.json` / `STAGE{N}_INJECT.json` | pass | Each stage carries only `STAGE{N}_INJECT.json` | — |
| §8.3 | each stage file is valid JSON | pass | All three parse with `ConvertFrom-Json` cleanly | — |
| §8.4 | every stage establishes a boundary | pass | `stage0` top-level `fires_after_turn: 0` → boundary [0,1] (seed anchor, no mutations); `stage1` top-level `fires_after_turn: 4` → boundary [4,5]; `stage2` top-level `fires_after_turn: 10` → boundary [10,11] | — |
| §8.5 | Form B (list) vs Form A (object) not mixed | pass | All three stages use Form B: `mutations` is a list of op objects with `service`, `http`, `silent`, etc. | — |
| §8.6 | silent ops carry `silent: true` + service that matches a `mock_data/` dir | pass | SM-01 (hubspot-api), SM-02 (fedex-api), SM-03 (salesforce-api) all carry `silent: true` and reach real `mock_data/<svc>-api/` dirs. Stage2 ops are marked `silent: false` (loud refreshes) and also resolve cleanly | — |
| §8.7 | boundary `fires_after_turn: N` strictly < last_turn_index | pass | `last_turn_index = 14`; both N=4 and N=10 fire well before T14 | — |
| §9.1 | absence of `task.toml` accepted with default-note | **fail [warn]** (informational only) | No `task.toml` at task root | Harness will emit one with `_DEFAULTS` (`pass_at_k=1`); document this in README or accept the default |
| §9.2 | if `task.toml` present, `pass_at_k=1` | n/a | File absent | — |

---

## 3. Cross-file consistency (§10)

| Rule | Check | Result | Evidence | Fix |
|---|---|---|---|---|
| §10.1 | every `checker_id` referenced in `test_outputs.py` docstrings appears in MA header / rubric / inject `tested_by_checkers` | n/a (vacuously pass) | `test_outputs.py` does not reference any `checker_id`, `T<n>_MA`, or `MA_C1` token; auto-detection from MA headers will supply the canonical `T2_MA`, `T5_MA`, `T9_MA`, `T11_MA`, `T13_MA` aggregates | — |
| §10.2 | every `{<SERVICE>_API_URL}` placeholder in inject ops resolves to a `mock_data/<svc>-api/` dir | pass | Stage1: hubspot, fedex, salesforce (silent vendor-portal revision). Stage2: openweather, eventbrite, google-calendar (race-day adjacency). All referenced services have matching `mock_data/<svc>-api/` directories. (Stage0 is the seed anchor with no mutations.) | — |
| §10.3 | every `required_apis` entry has matching `mock_data/<svc>-api/` dir | pass | All 17 required services present under `mock_data/` | — |
| §10.4 | every `distractor_apis` entry has matching `mock_data/<svc>-api/` dir | pass | All 9 distractor services present under `mock_data/` | — |
| §10.5 | AGENTS.md `## Multi-Agent Turns` section names literal trigger `Multi-Agent` | pass | `persona/AGENTS.md` line 57: ``"When the turn header carries the `Multi-Agent` label (T2, T5, T9, T11, T13 …)"`` — literal token present | — |
| §10.6 | prompts.txt turn count = max 0-indexed `turn_index` in test docstrings + 1 | n/a | No test docstring or test name encodes a `turn_index`; check inapplicable for this bundle | — |
| §10.7 | inject stages cover every `Multi-Agent` boundary the narrative claims | **fail [warn]** (doc drift, not a hard violation) | `INJECT.md` says stage2 fires "between TURN_9 and TURN_10", but `STAGE2_INJECT.json` description says "between TURN_10 (Fri evening) and TURN_11" and JSON header is `fires_after_turn: 10` → effective boundary [10,11], i.e. just before T11 (Saturday 05:30 race-day briefing, a Multi-Agent turn). Effective behaviour is correct; only `INJECT.md` documentation is stale | Update `INJECT.md` row for stage2 to read "Overnight between TURN_10 and TURN_11 (Fri evening to Sat morning)" so the doc and JSON agree |

Additional consistency note (not in §10): `task.yaml` `platform.multi-agent-complex-turns: [1, 4, 8, 10, 12]` (0-indexed turn indices, i.e. T2/T5/T9/T11/T13) agrees with the 5 `Multi-Agent` headers in `prompts.txt` and the bullet in `persona/AGENTS.md`. Because the `platform:` block is silently dropped at runtime, this is purely human-facing and creates no runtime risk.

---

## 4. Suggested next steps

1. **(§4.2 warn)** Decide policy on the `task.yaml:system_prompt` / `task_description` / `platform` block. If they exist only as human documentation, add a top-of-file note clarifying that — and/or delete them — so future authors do not assume runtime delivery. The agent system prompt actually shipped at runtime lives in `persona/AGENTS.md` + `persona/IDENTITY.md`.
2. **(§4.1 warn)** Either remove the unknown top-level `id:` / `name:` keys from `task.yaml`, or accept that the harness treats them as documentation only (the runtime task id comes from the directory name `Midori _Kelley2`).
3. **(§10.7 warn)** Fix the `INJECT.md` row for `stage2/` to say "between TURN_10 and TURN_11", matching `STAGE2_INJECT.json:fires_after_turn=10`. This is doc-only drift, not a runtime defect.
4. **(§9.1 informational)** Either ship a `task.toml` (and set `pass_at_k: 1` explicitly) or note in `README.md` that the bundle relies on `_DEFAULTS` (`pass_at_k=1`).
5. **(post-fix)** Run `scripts/check_injection.py "<repo>/input/Midori _Kelley2"` for a live dry-run of the inject pipeline against the mock stack. This validator does not execute pytest or invoke any API; the live dry-run is the next gate.
6. **(optional)** Consider whether the directory name should drop the leading-space typo (`Midori _Kelley2` → `Midori_Kelley2` or `MIDORI_002_rimrock_henderson_herd_health_crunch` to match `task.yaml:id`). Not required by the spec, but the embedded space in the directory name forces all shell invocations to quote the path.

---

*End of report. Generated against `task_format.md` rev as shipped in `c:\Users\User\Downloads\task-3\TASK_FORMAT\` and the recursive listing of `Midori _Kelley2/` taken on this run.*
