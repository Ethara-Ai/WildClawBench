# Task Format Validator Prompt

Use this prompt with a capable LLM to verify a new task directory conforms to
the canonical task format documented in `docs/task_format.md`. The validator
checks folder structure, per-file schemas, and cross-file consistency rules.

It does **not** call any APIs or run pytest. It only reads files and reports
gaps. After fixing reported gaps, run `scripts/check_injection.py <task_dir>`
for a live dry-run of the inject pipeline.

---

## System prompt

> You are a task-format validator for the WildClawBench harness. Your job is to
> read a task directory and a reference specification (`docs/task_format.md`)
> and report whether the task conforms. You do not modify any file. You do not
> run code. You only read what the user gives you and produce a structured
> report.
>
> When in doubt about an enum value, a header convention, or a schema, prefer
> the reference spec over your priors. The spec is authoritative.
>
> Be precise. Cite the section of `task_format.md` you are checking, the
> file/line in the task directory you are flagging, and the exact fix required.
> Do not invent issues that are not in the spec. Do not gloss over real issues.

---

## User prompt template

Fill in the bracketed sections before sending.

```
Reference spec:
<<<
[paste full contents of docs/task_format.md here, OR a link if the LLM can fetch]
>>>

Task directory listing (recursive, with sizes):
<<<
[paste output of: find "<task_dir>" -type f -printf '%P %s\n' | sort
 OR an equivalent recursive listing]
>>>

Key file contents:

# === prompt.txt OR prompts.txt ===
<<<
[full contents]
>>>

# === rubric.json ===
<<<
[full contents]
>>>

# === task.yaml (if present) ===
<<<
[full contents]
>>>

# === test_outputs.py (if present) ===
<<<
[full contents]
>>>

# === test_weights.json (if present) ===
<<<
[full contents]
>>>

# === persona/AGENTS.md (if present) ===
<<<
[full contents]
>>>

# === persona/TOOLS.md (if present) ===
<<<
[full contents]
>>>

# === mock_data/ tree ===
<<<
[paste: find "<task_dir>/mock_data" -type f | sort]
>>>

# === inject/ tree + every STAGE*_INJECT.json / mutations.json ===
<<<
[paste each stage file in full]
>>>

# === task.toml (if present) ===
<<<
[full contents]
>>>

Run the validator. Produce the report in the format below.
```

---

## Required output format

The validator must produce a single Markdown report with these sections.

### 1. Verdict

One line: `PASS` if every check passes, `PASS WITH WARNINGS` if only items
marked `[warn]` fail, `FAIL` if any item marked `[hard]` fails.

### 2. Section checklist

A Markdown table with one row per check. Columns:

| Section | Check | Result | Evidence | Fix |
|---|---|---|---|---|

`Section` is the `task_format.md` section number (`\u00a71`, `\u00a72`, ...).
`Result` is `pass`, `fail [hard]`, `fail [warn]`, or `n/a`.
`Evidence` cites the file + line/key from the task directory.
`Fix` is a one-line concrete remediation, empty if pass.

### 3. Cross-file consistency

A second table covering the 7 rules from `task_format.md \u00a710`.

### 4. Suggested next steps

A short ordered list of fixes in the order the validator recommends applying
them. Reference items from the tables above.

---

## Checks to perform

### \u00a71. Directory layout `[hard]`

1.1. Exactly one of `prompt.txt` or `prompts.txt` is present at the task root.
1.2. `rubric.json` is present at the task root.
1.3. If `mock_data/` is present, every immediate child is a directory whose
     name ends in `-api`.
1.4. If `inject/` is present, every immediate child is a directory whose name
     matches `stage[0-9]+`.
1.5. If `persona/` is present, its `.md` files are flat at `persona/*.md` and
     NOT nested under `persona/<name>/`.

### \u00a72. prompts.txt header convention `[hard]`

2.1. Every turn header matches the regex
     `^---\s*(TURN\s+T?(\d+)\b.*?)\s*---\s*$` (case-insensitive). Flag any
     header line that starts with `---` but does not match.
2.2. Turn numbers are 1-indexed and strictly monotonically increasing from
     `TURN 1`. Gaps and duplicates are `fail [hard]`.
2.3. Each turn header carries one of the labels `Light` or `Multi-Agent` in
     its parenthetical. Missing label is `fail [warn]`.
2.4. `Multi-Agent` is spelled exactly that way (hyphenated, both words
     capitalised). Any variant (`multi agent`, `MultiAgent`, etc.) is
     `fail [hard]` because auto-detection will miss it.

### \u00a73. rubric.json `[hard]`

3.1. Top level is a JSON list. Each entry is an object with keys
     `number, criterion, is_positive, type, evaluation_target, importance, score`.
3.2. `type` is one of `task completion`, `factuality and hallucination`,
     `safety & boundaries`, `instruction following`.
3.3. `evaluation_target` is one of the values listed in `task_format.md \u00a73`.
     If every criterion uses the same `evaluation_target`, flag as
     `fail [warn]` with the note that the rubric is likely missing variants
     such as `tool_call`, `artifact`, or `workspace_state`.
3.4. `importance` is one of `critically_important`, `important`.
3.5. `score` is an integer.
3.6. `is_positive` is a boolean.
3.7. `number` is unique across criteria.

### \u00a74. task.yaml `[warn]`

4.1. If present, top-level keys are limited to the spec's whitelist plus the
     ignored-but-documented set. Flag unknown keys as `fail [warn]`.
4.2. If the file ships `system_prompt`, `task_description`, or `platform`, add
     a `warn` note that these are silently dropped by `_overlay_yaml_metadata`.
4.3. `required_apis` and `distractor_apis` should be disjoint. Overlap is
     `fail [hard]`.

### \u00a75. test_outputs.py + test_weights.json `[warn]`

5.1. If `test_outputs.py` is present, `test_weights.json` must be present too,
     and vice versa.
5.2. Every key in `test_weights.json` resolves to a real test in
     `test_outputs.py`. Use full FQN, class-qualified, and bare matching per
     `task_format.md \u00a75`. Unresolved keys are `fail [hard]`.
5.3. Imports must not include `subprocess` or `sqlite3` unless actually used.
     Flag unused imports as `fail [warn]`.
5.4. Imports must not include a `try: import pytest / except ImportError`
     block. `pytest` is always available in the runner. Flag as `fail [warn]`.
5.5. No test method name carries a prefix `test_behavioral_` or
     `test_negative_weight_`. Flag as `fail [warn]`.
5.6. Negative weights must correspond to violation-detector tests whose body
     asserts a violation is **present**, not absent. Sanity-check the
     docstring or the assertion language. Heuristic only; mark `fail [warn]`
     if uncertain.

### \u00a76. persona/ `[warn]`

6.1. All seven canonical MDs are present: `AGENTS.md`, `HEARTBEAT.md`,
     `IDENTITY.md`, `MEMORY.md`, `SOUL.md`, `TOOLS.md`, `USER.md`. Missing
     files are `fail [warn]`.
6.2. If `prompts.txt` contains any `Multi-Agent` header, `persona/AGENTS.md`
     must contain a `## Multi-Agent Turns` section. Missing section is
     `fail [hard]`.
6.3. `persona/TOOLS.md` mentions only services that appear in `task.yaml`'s
     `required_apis` + `distractor_apis` (if `task.yaml` present) OR
     `mock_data/` subdirectories (if no `task.yaml`). Extra services are
     `fail [warn]`.

### \u00a77. mock_data/ `[hard]`

7.1. Every API directory contains at least one of `*.csv` or `*.json`.
7.2. Every CSV parses cleanly with `csv.reader` and has a header row plus
     consistent column counts. Malformed CSVs are `fail [hard]`.
7.3. Every JSON parses cleanly with `json.load`. Malformed JSON is
     `fail [hard]`.
7.4. Filename convention: table data lives in `records_<table>.csv`;
     documents live in `<feature>.json`. Mark deviations as `fail [warn]`.

### \u00a78. inject/ `[hard]`

8.1. `inject/stage0/` is present whenever any `stage1+/` is present. (Loader
     allows stage0 to be empty; it is the convention anchor.)
8.2. Each stage directory contains either `mutations.json` or
     `STAGE{N}_INJECT.json`. Both is `fail [warn]`; neither is `fail [hard]`.
8.3. Each stage file is valid JSON.
8.4. Each non-zero stage establishes a boundary by **one** of:
     `applies_between_turns: [N, M]`, `applied_between: [N, M]`, top-level
     `fires_after_turn: N`, or per-op `fires_after_turn` on every op in the
     `mutations`/`injections` list. Stages with none of these are
     `fail [hard]` (loader will silently treat them as seed).
8.5. Form B (Talos) `mutations` is a list of op objects. Form A (LAYLA)
     `mutations` is an object with `silent`/`loud`/`filesystem` keys. Mixing
     the two shapes in one stage is `fail [hard]`.
8.6. Silent ops must include `silent: true` AND a `service` reachable through
     the per-task admin plane. Service names should match a directory in
     `mock_data/`. Mismatched services are `fail [warn]` (the loader will log
     `unresolved`).
8.7. Boundary values reference real turn indices in `prompts.txt`. A
     `fires_after_turn: N` whose `N >= last_turn_index` is `fail [warn]`
     (stage will never fire).

### \u00a79. task.toml `[warn]`

9.1. If absent, the harness will emit one with `_DEFAULTS`. That is fine, but
     note in the report that the task is using the default `pass_at_k = 1`.
9.2. If present, `pass_at_k` should be `1` for single-Claude runs. Flag any
     other value as `fail [warn]`.

### \u00a710. Cross-file consistency `[hard]`

10.1. Every `checker_id` referenced in `test_outputs.py` docstrings appears
      in at least one of: a `Multi-Agent` header (auto-synthesised
      `T<n>_MA` + `MA_C1`), `rubric.json`, or a `tested_by_checkers` field
      on an inject op.
10.2. Every `service` named in an inject op's `url` placeholder
      (`{<SERVICE>_API_URL}`) corresponds to a directory in `mock_data/`.
10.3. Every `required_apis` entry in `task.yaml` has a matching
      `mock_data/<service>-api/` directory.
10.4. Every `distractor_apis` entry in `task.yaml` has a matching
      `mock_data/<service>-api/` directory.
10.5. `persona/AGENTS.md`'s `## Multi-Agent Turns` section names the literal
      trigger token `Multi-Agent` (matching auto-detect).
10.6. `prompts.txt`'s turn count equals the maximum 0-indexed `turn_index` in
      `test_weights.json` test docstrings + 1 (no test references a turn the
      prompt does not have).
10.7. `inject/` stages cover every `Multi-Agent` boundary the task narrative
      claims (cross-check `golden_steer_flow.md` or `README.md` if present).
      Missing inject coverage is `fail [warn]`.

---

## Reference exemplars

When in doubt, compare the task against the conforming exemplars under
`input/`:

- `input/Ruth Armstrong/` \u2014 Talos Form B inject (`mutations:[list]` with
  per-op `fires_after_turn`), HTTP-probe `test_outputs.py`.
- `input/GLORIA/` \u2014 Form A LAYLA inject (`mutations:{silent,loud,filesystem}`)
  with state-fixture `test_outputs.py`.
- `docs/task_template/` \u2014 minimal demo: Form B inject, HTTP-probe tests,
  flat persona, 3-turn prompts.txt with one `Multi-Agent` boundary.

These are the canonical examples for resolving ambiguity in the spec.
