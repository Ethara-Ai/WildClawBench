# WildClawBench — Fixes Changelog (skoll-delivery)

**Window:** 2026-06-16 morning → 2026-06-17
**Branch:** `skoll-delivery`
**Commits:** `7d9c4cc`, `8ba33ec`, `bf51744`, `fbcf07c`, `9216664`, `7b9b65c`
**Scope:** 21 source/test files, +2,615 lines. All committed.

Theme: make the harness's generated trajectory artifacts (`output.json`,
`delivery.json`, snapshots, golden) **correct, complete, and faithful**, plus a new
golden-trajectory pipeline.

---

## A. Trajectory / delivery correctness (the "6 bundle fixes")

1. **Trim harness scaffolding from user messages** — `src/utils/trajectory/builder.py`
   - Problem: user turns in output.json/delivery.json carried machine junk —
     the turn-0 boilerplate (`"You are an expert in a restricted…"`) + `[<…> UTC]`
     + `[TURN N (…)]` headers — instead of the exact prompt.
   - Fix: `_strip_user_turn_prefix()` (anchored regex, only known tokens) applied to
     user messages in `build_trajectory_from_jsonl`. One chokepoint → fixes BOTH
     output.json and delivery.json. Negatives (text that merely starts with `[`) preserved.
   - Also `_strip_user_prefix_from_message()` helper: handles both `str` content and
     `list`-of-blocks (multimodal) content, trimming **only the first text block**.

2. **delivery.json now carries `artifacts` + `input_files`** — `src/utils/delivery_schema.py`
   - Problem: delivery.json had no artifacts list.
   - Fix: surface `traj["output_artifacts"]` / `input_files` into the delivery dict.

3. **Sub-agent costing** — `src/utils/subagent_director.py`, `src/agents/openclaw/runner.py`
   - Problem: sub-agents had no `cost_usd` anywhere; spawn rows logged `model: None`.
   - Fix: capture the resolved model; add `cost_usd` to `SubagentResult` / `to_log_row`
     (spawn_tree.jsonl) / summaries / `write_subagent_delivery` (spw delivery
     meta_info.usage); fold subagent cost into parent `usage` in runner.py (summary
     rows skipped → no double-count).
   - Pricing (`_subagent_cost_usd`): **primary path** = the static `_AGENT_COST_RATES`
     table (substring-keyed on model name — opus/sonnet/haiku, as (input, output,
     cache_read, cache_write) per-token rates) which works in the bare container with
     **no litellm**; **fallback** = `litellm.cost_per_token(...)` (host-only, lazy import)
     for models that match no table key; else `0.0`.

4. **score.json real pytest counts** — `eval/run_batch.py` (`_overlay_pytest_counts`)
   - Problem: score.json showed `tests_total:32, passed:0, failed:0` — it leaked the
     rubric criteria counts (32, all abstained) into the pytest fields.
   - Fix: overlay the real pytest counts from `result["test_result"]` (17/8/9) onto
     score.json AFTER reward math; rubric stays under `criteria_*`.

5. **prompt.txt holds all 15 turns** — `src/utils/harbor/bundle.py` (`_resolve_bundle_prompt`)
   - Problem: bundle prompt.txt saved only TURN 1 (820 B) for multi-turn tasks.
   - Fix: prefer the full `prompts.txt` from the task dir; else fall back to initial prompt.

6. **instruction.md decoupled (self-found audit bug)** — `src/utils/harbor/bundle.py`
   - Problem: fix #5 made `data/instruction.md` ALSO go multi-turn (shared variable).
   - Fix: keep prompt.txt = all turns, but `instruction.md` = single-turn initial prompt
     (matches reference). Found during a post-implementation self-audit.

Tests: `tests/test_skoll_delivery_fixes.py`, `tests/test_delivery_schema.py`,
`tests/test_subagent_director.py`.

---

## B. Workspace snapshots → directories  (commit `7d9c4cc`)
`src/utils/workspace_snapshot.py`, `eval/run_batch.py`
- Problem: snapshots were flat `workspace_snapshot_{initial,final}.json`.
- Fix: emit directory trees instead — `workspace_initial/{data,mock_data,persona}`
  (verbatim copy of task input) and `workspace_after/{data,mock_data,persona}` (post-run:
  data copied from input; mock_data rendered as per-API CSV + `_doc:*`→JSON; persona→.md).
  New helpers: `write_workspace_initial`, `write_workspace_after`, `capture_workspace_after`,
  `_render_mock_data_dir`, `_render_persona_dir`, plus internals `_csv_cell`,
  `_write_table_csv`, `_copy_input_subdir`, `_safe_name`. The two flat JSON files dropped.
  Subtle behaviors: CSV header = **first-seen union of all row keys** (walks every row,
  not just row 0); nested dict/list cells JSON-encoded; a non-list / non-`_doc:` table
  payload is preserved as `<table>.json`; an empty list still writes an empty file (records
  the table's existence); `_safe_name()` sanitizes names against path traversal.
  Tests: `tests/test_workspace_snapshot.py`.

---

## C. Harness-report items #10 + #11  (commit `bf51744`)

7. **#10 — delivery embeds ALL sub-agents (was 1 of N)** — `src/utils/delivery_schema.py`
   - Problem: `_load_sub_agent_trajectories` only embedded spawns detected via parent
     `exec` toolCalls; wrapper-script (`run_all*.sh`) spawns were missed → 1 of N.
   - Fix: embed EVERY `spw_*.delivery.json` on disk (ordered by spawn_tree). Verified on a
     real run: 1 → 29 (and later 31).

8. **#11 — dedupe gateway re-issued final turn** — `src/utils/trajectory/builder.py`, `scripts/gen_golden.py`
   - Problem: a gateway recovery re-sent turn 15 → 16 identical user turns.
   - Fix: `_dedupe_reissued_turns()` collapses ADJACENT byte-identical user-turn segments
     (keeping the later/completed re-run). Applied in builder (output/delivery) + gen_golden
     (golden pack). Verified: 16 → 15, keeping the right turn id.

---

## D. meta_info sourced from task.yaml  (commit `bf51744`)

9. **delivery (+ golden) meta_info from task.yaml** — `src/utils/task_parser.py`,
   `eval/run_batch.py`, `src/utils/delivery_schema.py`, `scripts/gen_golden.py`
   - Problem: delivery meta_info had `task_type`="<task id>", `task_description`=boilerplate,
     `system_prompt`="" — task_parser silently ignored task.yaml's `system_prompt` /
     `task_description`, and run_batch passed `task_id` as `task_type`.
   - Fix: task_parser now loads `system_prompt` + `task_description` from task.yaml and
     joins the `task_type` list into a string; run_batch passes the right values; delivery
     accepts a `task_description` param (prefers it over the derived one); gen_golden's
     `metadata.json` uses task.yaml `system_prompt` (replacing the 7-persona fallback).
   - Tests: `tests/test_task_parser_yaml.py`, `tests/test_delivery_schema.py`.

---

## E. Golden-trajectory pipeline (new)  (commits `bf51744`/`7b9b65c`)

10. **`scripts/gen_golden.py`** (NEW, 515 lines) + `system_prompts/golden_oddo_generator_RFP3.md`
    - A 3-stage pipeline adapting our run + task into the RFP3 generator's 5-input contract:
      - **assemble** — run+task → `golden_input/` pack (events.jsonl←chat.jsonl,
        session-branch.json←spawn_tree.jsonl, clean prompts.json, artifacts manifest,
        metadata.json with task.yaml system_prompt).
      - **run** — `litellm.completion` (Bedrock) with the generator prompt; truncation-aware.
      - **parse** — split `=== PARENT/CHILD ===` → `golden_trajectory.json` +
        `golden_subagents/*.json`; validate (em/en-dash reject, JSON, d/c ID chains,
        toolCall↔toolResult balance, ends-on-assistant).
    - Tests: `tests/test_gen_golden.py`.

---

## F. Bug 1 + Bug 2  (commit `7b9b65c`) — verified on a fresh Opus run

11. **Bug 1 — parent thinking TEXT captured** — `src/utils/litellm_thinking_callback.py` (NEW),
    `src/utils/litellm_sidecar.py`, `eval/run_batch.py` (commit `7b9b65c`); plus the
    headroom-image LiteLLM-version fix in `docker/litellm-headroom.Dockerfile` (commit `8ba33ec`).
    - Problem: parent thinking blocks had a signature but EMPTY text (33/33 empty). There are
      TWO independent layers that can blank the reasoning text:
      - **(a) request shape** — openclaw's `reasoning_effort:high` OVERRIDES the configured
        `thinking` dict and strips `display:summarized`, so Bedrock returns signature-only
        (sub-agents send no reasoning_effort → keep text).
      - **(b) LiteLLM version** — `headroom-ai==0.24.0` hard-pins `litellm==1.82.3`, whose
        Bedrock Converse reasoning passthrough round-trips the thinking *signature* but drops
        the *text* — independently reintroducing the empty-text bug on the headroom image.
    - Fix (a): sidecar pre-call hook (`ThinkingForcer`) that, on Claude requests, drops
      `reasoning_effort`, forces `thinking:{type:adaptive,display:summarized}`, and
      `setdefault`s `output_config:{effort:"high"}`; mounted always-on (`litellm_sidecar`
      config flag `enable_thinking_callback` + `-v` mount; wired in run_batch).
    - Fix (b): `docker/litellm-headroom.Dockerfile` now pins the base by digest and
      **snapshots the base LiteLLM (1.88.1) before the headroom install, then
      `--force-reinstall --no-deps` restores it** — undoing the silent 1.82.3 downgrade so the
      headroom image keeps reasoning text. (See also §I for the matching callback-level
      thinking-block protection during compression.)
    - Verified (fresh Opus run): parent thinking 9/9 **populated** (80–2719 chars), flowing
      into chat.jsonl → output.json → delivery.json. Tests: `tests/test_litellm_thinking_callback.py`.

12. **Bug 2 — output.json top-level meta_info** — `eval/run_batch.py` (`_inject_output_meta_info`)
    - Problem: output.json had no meta_info block (only delivery.json did).
    - Fix: inject `meta_info` (task_type/task_description/task_completion_status/system_prompt/
      platform from task.yaml) **right after `output_artifacts`**. Completion status from the
      pytest reward (rubric not yet scored at write time).
    - Verified (fresh Opus run): present, correct position, real task.yaml values.

---

## I. Headroom agent-path compression  (commit `8ba33ec`)
`src/utils/litellm_headroom_callback.py` (+143), `docker/litellm-headroom.Dockerfile`
- Problem: the headroom pre-call compressor never actually engaged on the agent path
  (empty `headroom.jsonl`, unchanged Bedrock cache prefix) — multiple independent blockers.
- Fixes (4 real ones, NOT "tweaks"):
  1. **`min_tokens_to_compress` 2000 → 500** (`_min_tokens`): the old 2000 per-message gate
     marked every openclaw message "small" → 0 tokens ever saved.
  2. **`compress_user_messages=True`** on the agent `_CompressConfig`: tool-result blocks
     arrive as `role=user`; with the library default (False) they were `protected` → no
     compression even on large JSON tool results.
  3. **`anthropic_messages` added to the call_type allowlist**: openclaw's `/v1/messages`
     route maps to `CallTypes.anthropic_messages`; without it every real agent request was
     skipped uncompressed.
  4. **`_flatten_text_block_content()` + `_has_thinking_block()`**: Anthropic block-shaped
     content no-oped the string compressors; flattening makes them engage, while
     thinking-bearing turns are **detached and spliced back verbatim** so compression never
     strips reasoning text or invalidates the Bedrock `thinkingSignature` (→ downstream 400s).
     This is also what keeps Bug-1 thinking text intact under compression.
- Dockerfile: digest-pinned base + LiteLLM snapshot/restore (see §F11 fix (b)).
- Tests: `tests/test_litellm_headroom_callback.py` (asserts pure-text blocks flatten but
  tool_use/cache_control blocks don't; asserts `compress_user_messages is True`).

---

## G. Minute / supporting changes
- Added the Co-Authored-By trailer convention to commits.
- `src/utils/task_parser.py`: loads `.env` via dotenv (import-time); loads `system_prompt`
  + `task_description` from task.yaml; joins `task_type` list → comma string; and
  **`category` now prefers its own task.yaml `category` key**, only falling back to the
  joined `task_type` when no `category` is present (previously category was a straight alias
  of `task_type`).
- `system_prompts/golden_oddo_generator_RFP3.md` stored in-repo (the generator prompt).
- New tasks/input added (`fbcf07c`, `9216664`) — task data, not code fixes.
- Tests added across the board: `test_skoll_delivery_fixes.py`, `test_gen_golden.py`,
  `test_litellm_thinking_callback.py`, `test_litellm_headroom_callback.py`,
  `test_task_parser_yaml.py`, plus extensions to `test_delivery_schema.py`,
  `test_workspace_snapshot.py`, `test_subagent_director.py`.

## H. Verification status
- All code-level fixes unit-tested (~200+ tests green in the affected suites).
- #10, #11, Bug 1, Bug 2 also verified on **real runs** (delivery 1→31 subagents;
  16→15 turns; thinking 0→9 populated; output.json meta_info present).
- Known unrelated env issue (NOT a code bug): judge council fails locally on
  `SSL: CERTIFICATE_VERIFY_FAILED` reaching Bedrock from the host.

---

## Out of scope (flagged, not done)
- Harness-report CRITICALs: dead judge ARN, date anchoring, auth/tenant mismatch,
  mock persistence — separate infra workstream.
- Recovering thinking text for pre-fix runs (it was never captured).
