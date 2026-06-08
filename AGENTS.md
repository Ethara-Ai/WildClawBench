# WILDCLAWBENCH — PROJECT KNOWLEDGE BASE

**Branch:** main · **Commit:** ec949fc

## OVERVIEW
Agent-evaluation benchmark (60 tasks, 6 categories) that runs four agent harnesses
(OpenClaw, Claude Code, Codex, Hermes) inside Docker against 101 mock REST APIs.
Pure Python 3 (no package manager file — flat `requirements.txt`, run via `python3`).

> This checkout is a **LiteLLM/Bedrock fork** of upstream `internlm/WildClawBench`.
> The fork adds Bedrock routing, a LiteLLM sidecar, drift-plane, testgen, and Harbor
> bundles — none documented in README.md. **Trust the code, not the README, for harness/env behavior.**

## STRUCTURE
```
src/             # all harness code (agents/ + utils/) — see src/agents, src/utils AGENTS.md
environment/     # 101 mock-API services + admin/drift plane — see environment/AGENTS.md
eval/run_batch.py# single 1998-line orchestrator: parse→spin docker→run agent→grade→summarize
script/          # user entrypoints: run.sh (dispatch backend), prepare.sh (download data)
scripts/         # one-off maintenance: regrade, aggregate_runs, migrate_to_drift_plane
system_prompts/  # per-harness system prompt text
tests/           # pytest: mock-API integrity + harness invariants — see tests/AGENTS.md
input/  output/  logs/   # DATA, not code: personas, run trajectories, logs (do not edit)
debhouse/ wheelhouse/    # vendored skill-deps / offline wheels
state.db         # sqlite Store (task/sandbox/run tables); state.db-shm/-wal are sqlite sidecars
tasks/           # NOT in repo — downloaded from HuggingFace via prepare.sh
```

## WHERE TO LOOK
| Task | Location |
|------|----------|
| Add/change a harness | `src/agents/<name>/runner.py` + register in `eval/run_batch.py` imports |
| Run config / env vars | `src/utils/config.py` (`Config.from_env`) + `.env.example` |
| CLI flags | `src/utils/cli_args.py` (`parse_run_batch_args`) |
| Grading / LLM judge | `src/utils/grading.py` |
| Mock API behavior | `environment/<api>-api/server.py` + `*_data.py` |
| Run a benchmark | `bash script/run.sh <openclaw\|claudecode\|codex\|hermesagent> [args]` |

## CONVENTIONS (fork-specific)
- **`(bN)` / `mNNNN` in comments** = compressed-session block / message IDs used as
  changelog anchors (e.g. "see b54 Issue 3", "the m1037 probe"). Treat as historical
  rationale markers, not code refs.
- **`KENSEI_*` env vars are primary**, generic aliases secondary. `Config.from_env`
  reads them in priority order via the `s(...)` helper. Add new keys there.
- `from __future__ import annotations` at top of every module.
- Imports use `from src.agents...` / `from src.utils...` — there is **no `src/__init__.py`**;
  `run_batch.py` injects repo root onto `sys.path` (line 19). Run from repo root.
- `ROOT_DIR = Path(__file__).resolve().parents[N]` for path anchoring (never `os.getcwd`).
- Config dataclasses (`@dataclass`), `field(default_factory=...)` for paths/lists.

## ANTI-PATTERNS (THIS PROJECT)
- Don't edit `input/`, `output/`, `logs/` — generated data / fixtures (incl. their
  stray `persona/AGENTS.md` files, which are TASK content, not knowledge files).
- Don't trust README model-name rules over `cli_args.py` — fork normalizes differently
  per backend (OpenClaw/Codex want `openrouter/<p>/<m>`; Claude Code/Hermes want `<p>/<m>`).
- Don't commit `.env` or real `KENSEI_*` secrets (`.env.example` is the tracked template).
- LiteLLM sidecar is **per-batch, not host-reachable**; host-side code (grading judge)
  must call providers directly — see `grading.py` header.

## COMMANDS
```bash
pip install -r requirements.txt          # flat pinned deps + python-dotenv
bash script/prepare.sh                    # fetch videos/weights/git archives into tasks/
bash script/run.sh openclaw --category all --parallel 4 --model openrouter/openai/gpt-5.5
python3 eval/run_batch.py --agent-backend claudecode --task tasks/.../x.md --model openai/gpt-5.5
pytest tests/                             # harness + mock invariants
pytest tests/mocks                        # parametrized per-API integrity (xdist-grouped)
```

## NOTES
- `litellm_enabled()` auto-routes to Bedrock/OpenAI when those creds exist, else OpenRouter.
- Output layout: `output/<harness>/<category>/<task>/<short_model>_<ts>_<runid>/`.
- Default-injected skills every task: `video-frames,pdf-extract,audio-extract`.
