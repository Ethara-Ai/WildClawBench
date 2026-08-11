# Multi-Turn Tasks & Interactive SFT Collection

## Format

Multi-turn tasks use the **Talos native format** — a task directory with:

| File / dir | Role |
|---|---|
| `prompts.json` | the FINALIZED turn schedule (2026-07-31): `{task_id, persona, timezone, turn_count, turns:[{turn:"T0", timestamp:"2026-10-20T09:30:00-04:00", message}]}`. **Highest priority** — beats every txt form. Loudly validated at load (contiguous T0..TN labels matching position, `turn_count` agreement, chronological timestamps); timestamps are metadata only (never shown to the agent); bundles render it to the canonical `--- TURN Tn (Day N, HH:MM) ---` text. Newer tasks ship a companion `prompts.txt` with identical turns — JSON is authoritative and the loader warns if the companion's turn count drifts |
| `prompts.txt` | the per-turn wake-up script — `--- TURN T0 (Day 1, 05:30) ---` blocks, one user message per turn (a multi-day simulation). **Takes priority over `prompt.txt`** when both exist (`prompt.txt` is only the T0 mirror); before 2026-07-27 the loader had this backwards, so every corpus task shipping both files silently ran a single turn with zero injections — treat multi-turn scores from before that fix as suspect |
| `inject/stage0..N/mutations.json` | silent environment mutations applied at turn boundaries (`applies_between_turns: ["T15","T16"]`): `filesystem` drops, `loud` visible API changes, and `silent` admin-plane drifts (e.g. a meeting moves without an email). Give each API op an explicit `admin` block (`{op: patch\|upsert\|update_where\|doc_set, table, pk, set/row}`) — it dispatches directly against the store; the bare REST form (`method`/`path`/`body`) relies on fuzzy row resolution that needs an `id`/`pk` column and silently logs `unresolved` on domain-keyed tables (e.g. doordash `orders` keys on `order_id`). Verify firings in the run's `inject_timeline.jsonl` (`status: applied`, before/after values) |
| `rubric.json` | rubric criteria for the LLM judge (Channel B) |
| `test_outputs.py` + `test_weights.json` | deterministic pytest checkers (Channel A); weights are signed (`±1/±3/±5`; negative = guardrail) |
| `persona/` | 7 OpenClaw bootstrap `.md` files (IDENTITY/SOUL/AGENTS/MEMORY/USER/TOOLS/HEARTBEAT) |
| `data/` · `mock_data/` · `task.yaml` | workspace inputs · mock-API overlays · metadata sidecar |

`task_parser.load_task` routes this as `native+yaml` and, with an `inject/` dir +
a per-task mock stack, runs the multi-turn loop (`run_batch.py`). Example task:
`input/davis_meal_calorie_check` (4 turns, 2 silent drift stages, 10 checkers).

## Authoring: the declarative compiler

Hand-writing the format above is error-prone (turn numbering,
`applies_between_turns`, weight vocab, test-name ↔ weight-key matching). The
preferred path is to author **four data files, no Python** under
`declarative/<task_id>/` — `metadata.json`, `prompt.txt`, `stages.toml`,
`rubrics.json`, plus passthrough `persona/` `data/` `mock_data/` — and compile:

```bash
python3 script/compile_declarative_task.py declarative/<task_id> [--force]
python3 script/preflight_task.py declarative/_build/<task_id>
cp -R declarative/_build/<task_id>/ input/<task_id>/     # promote to the corpus
```

The compiler generates all the bookkeeping positionally (a stage that never
fires cannot be authored), normalizes service names, emits admin-block
mutations, and compiles `rubrics.json` "checks" into a hermetic
`test_outputs.py` + `test_weights.json`. Full format reference:
`declarative/README.md`. **`input/` is gitignored** — the declarative source is
the only version-controlled representation of a compiled task, so edit the
source and recompile; never hand-edit the compiled copy in `input/`.
`input/davis_meal_calorie_check` is compiled from
`declarative/davis_meal_calorie_check/` (the pre-conversion originals are kept
in its `original_snapshot/`).

## How it runs

Turns come from `prompts.txt`; between turns the `inject/` mutations fire
silently (agent must re-verify, not trust its earlier note). Scoring is
deterministic checkers (`test_outputs.py`, signed weights + guardrails) plus the
optional 3-judge council over `rubric.json`. All on the same OpenClaw session so
context carries across turns.

### Clock scope — an authoring constraint

The simulated clock shifts the **agent's** `Date` reads only (re-anchored to each
turn's `prompts.json` timestamp; see `src/utils/sim_clock.py` +
`docker/agent_faketime_shim.js`). **Mock-API records are stamped with real host
UTC** — the mock fleet is a separate container with no clock plumbing. So:

* Do not author rubric criteria or Channel-A checkers that read a timestamp out
  of a mock record.
* Keep authored inject message dates **earlier** than the run's real wall clock,
  or the agent's own reply sorts *before* the message it is answering.

## Two modes (same pipeline; only the prompt source changes)

- **Static (default)** — the scripted `prompts.txt` turns are fed automatically.
  Reproducible → **Eval** and **RL** (K rollouts via `script/run.sh`).
- **Interactive (`--interactive`)** — a human paces each turn: the scripted
  message is shown as an overridable suggestion (Enter = send it, type =
  override, `/exit` = end), and the agent's prior reply is echoed. The `inject/`
  silent mutations STILL fire at boundaries; scoring is unchanged. Used for
  **SFT** collection. OpenClaw backend only; direct invocation only (not via
  `run.sh` — it tees stdout and backgrounds runs). Gate: any multi-turn task
  (`prompts.txt` >1 turn / `inject/` / `stages.yaml`), single `--task`,
  `--parallel 1`.

  Runs inside the **same unified TUI** as static runs. `--interactive`
  auto-enables the full-screen Textual dashboard (`src/utils/ui/tui.py`) when a
  real terminal + `textual` are available: the scripted suggestion, the agent's
  prior reply, and your own turns render in a dedicated **Conversation** pane,
  and an **input bar** appears at each turn boundary (Enter/type/`/exit` as
  above). When the dashboard can't run (piped output, `NO_COLOR`, or `textual`
  missing) it falls back to the original `/dev/tty` REPL — same behaviour, no
  dashboard. Either way `HumanTurnSource`'s turn logic and the recorded
  `turn_timeline.jsonl` are identical; only the input/echo backend changes.

`script/session_to_prompts.py` promotes an interactive session's
`turn_timeline.jsonl` back into a static `prompts.txt` (one HITL session → SFT
trajectory + a reusable static task).

## Commands

Credentials: the OAuth bridge uses the pool file `~/.wcb/oauth_pool/account_a.json`
(export from the Claude Code Keychain entry; `.env`'s `WCB_CC_ACCOUNT_POOL` may
point elsewhere — the override below wins). If bootstrap aborts with `rc=7`
(bridge not healthy) and the bridge log shows `HTTP 400` on the token refresh,
the pool's refresh token has been burned by a rotation elsewhere — re-export the
Keychain pair: `security find-generic-password -s "Claude Code-credentials" -w
> ~/.wcb/oauth_pool/account_a.json` (back the old file up first).

**Eval — static multi-turn, one run** (checkers + judge):

```bash
WCB_CC_ACCOUNT_POOL=$HOME/.wcb/oauth_pool/account_a.json \
.venv/bin/python eval/run_batch.py --task input/davis_meal_calorie_check \
  --agent-backend openclaw --model claude-opus-4.7 \
  --litellm --use-claude-oauth --mock-stack --parallel 1 --judge-council
```

> `--model claude-opus-4.7` above is a valid Opus alias on the OAuth bridge. To
> run the current Opus, use `--model claude-opus-5` (OAuth upstreams
> `anthropic/claude-opus-5`); both resolve through the same cc-bridge. The full
> model list + per-model routing is in `RUNBOOK.md` §1.

**RL — static, K rollouts (pass@K):**

```bash
WCB_USE_CLAUDE_OAUTH=1 WCB_CC_ACCOUNT_POOL=$HOME/.wcb/oauth_pool/account_a.json \
bash script/run.sh input/davis_meal_calorie_check claude-opus-4.7 4 --use-claude-oauth
# per-rollout reward.txt + pass_summary.json; drop the OAuth env/flag to use Bedrock
```

**SFT — interactive human session (real terminal only, not run.sh):**

```bash
WCB_CC_ACCOUNT_POOL=$HOME/.wcb/oauth_pool/account_a.json \
.venv/bin/python eval/run_batch.py --task input/davis_meal_calorie_check \
  --agent-backend openclaw --model claude-opus-4.7 \
  --litellm --use-claude-oauth --mock-stack --parallel 1 --interactive
```

**Promote an interactive session into a static task:**

```bash
RUN=$(ls -dt output/openclaw/*/trajectories/*/run_* | head -1)
.venv/bin/python script/session_to_prompts.py --run "$RUN"   # writes $RUN/prompts.txt
```

## Where the interactive engine lives

| Piece | File |
|---|---|
| Pull-based runner loop (`while (msg := source.next_message(i))`) | `src/agents/openclaw/runner.py` (`AgentTaskSpec.turn_source` field in `src/agents/base.py`) |
| Turn sources | `src/utils/turn_source.py` — `StaticTurnSource` (the schedule) + `HumanTurnSource` (Mode-2, io-injectable: dashboard or `/dev/tty`) |
| Unified TUI (Conversation pane + input bar) | `src/utils/ui/tui.py` (`HarnessDashboard`), `src/utils/ui/input_bridge.py` (worker↔UI channel), `src/utils/ui/interactive.py` (`tui_io` → HumanTurnSource `io`) |
| Interactive wiring (io selection, reply echo, turn record, gate) | `eval/run_batch.py` (`_make_reply_fn` / `_make_record_fn`, `tui_io` vs `/dev/tty` by `lifecycle.is_dashboard_active()`, `--interactive` gate) |
| Session → static task | `script/session_to_prompts.py` |

The inject silent-mutation system, the deterministic checkers, and the judge are
the harness's existing native+inject pipeline — interactive mode only swaps the
prompt source; everything else is identical to a static run.
