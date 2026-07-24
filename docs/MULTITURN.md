# Multi-Turn Tasks & Interactive SFT Collection

## Format

Multi-turn tasks use the **Talos native format** — a task directory with:

| File / dir | Role |
|---|---|
| `prompts.txt` | the per-turn wake-up script — `--- TURN T0 (Day 1, 05:30) ---` blocks, one user message per turn (a multi-day simulation) |
| `inject/stage0..N/mutations.json` | silent environment mutations applied at turn boundaries (`applies_between_turns: ["T15","T16"]`): `filesystem` drops, `loud` visible API changes, and `silent` admin-plane drifts (e.g. a meeting moves without an email) |
| `rubric.json` | rubric criteria for the LLM judge (Channel B) |
| `test_outputs.py` + `test_weights.json` | deterministic pytest checkers (Channel A); weights are signed (`±1/±3/±5`; negative = guardrail) |
| `persona/` | 7 OpenClaw bootstrap `.md` files (IDENTITY/SOUL/AGENTS/MEMORY/USER/TOOLS/HEARTBEAT) |
| `data/` · `mock_data/` · `task.yaml` | workspace inputs · mock-API overlays · metadata sidecar |

`task_parser.load_task` routes this as `native+yaml` and, with an `inject/` dir +
a per-task mock stack, runs the multi-turn loop (`run_batch.py`). Example task:
`input/input`.

## How it runs

Turns come from `prompts.txt`; between turns the `inject/` mutations fire
silently (agent must re-verify, not trust its earlier note). Scoring is
deterministic checkers (`test_outputs.py`, signed weights + guardrails) plus the
optional 3-judge council over `rubric.json`. All on the same OpenClaw session so
context carries across turns.

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
point elsewhere — the override below wins).

**Eval — static multi-turn, one run** (checkers + judge):

```bash
WCB_CC_ACCOUNT_POOL=$HOME/.wcb/oauth_pool/account_a.json \
.venv/bin/python eval/run_batch.py --task input/input \
  --agent-backend openclaw --model claude-opus-4.7 \
  --litellm --use-claude-oauth --mock-stack --parallel 1 --judge-council
```

**RL — static, K rollouts (pass@K):**

```bash
bash script/run.sh input/input claude-opus-4.7 4   # per-rollout reward.txt + pass_summary.json
```

**SFT — interactive human session (real terminal only, not run.sh):**

```bash
WCB_CC_ACCOUNT_POOL=$HOME/.wcb/oauth_pool/account_a.json \
.venv/bin/python eval/run_batch.py --task input/input \
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
