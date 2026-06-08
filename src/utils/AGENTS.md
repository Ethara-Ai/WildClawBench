# src/utils — HARNESS SUPPORT LIBRARY

Shared building blocks consumed by `eval/run_batch.py` and `src/agents/*`. Flat modules
plus 3 sub-packages (`testgen/`, `harbor/`, `trajectory/`).

## WHERE TO LOOK
| Concern | Module |
|---------|--------|
| Env → `Config` dataclass (KENSEI_* keys) | `config.py` |
| CLI flag parsing | `cli_args.py` (`parse_run_batch_args`) |
| Docker lifecycle, skills install, output collection | `docker_utils.py` |
| sqlite persistence (`task`/`sandbox` tables) | `store.py` (`Store`, `Task`) |
| Grading + host-side LLM rubric judge | `grading.py` |
| LiteLLM sidecar container mgmt | `litellm_sidecar.py`, `litellm_usage_callback.py` |
| Bedrock Converse streaming | `bedrock_eventstream.py` |
| Mid-run state mutation orchestration | `drift_director.py` (host side of admin_plane) |
| Mock container fleet up/down | `mock_stack.py`, `mock_health_logger.py` |
| Task .md parsing | `task_parser.py` (`parse_task_md`, `load_task`) |
| Which APIs/skills a task needs | `skills_inference.py` (`infer_required_apis`) |
| Model-test generation pipeline | `testgen/` (LLM→pytest+weights, lint loop) |
| Reproducible task bundle export | `harbor/` (compose/dockerfile/ctrf/task_toml) |
| Build trajectory from chat.jsonl | `trajectory/builder.py` |

## CONVENTIONS
- `testgen/` is "ported from kensei2" — routes Bedrock Converse **direct** (not via sidecar);
  entry `generate_task_tests`; weights constrained to `ALLOWED_WEIGHTS`.
- `harbor/` mirrors a tests-as-bundle contract: one module per emitted artifact
  (`compose.py`, `dockerfile.py`, `solve_sh.py`, `test_sh.py`, `task_toml.py`, `ctrf.py`).
- Grading judge has a hard evidence char-cap (~450k, `JUDGE_MAX_EVIDENCE`) derived from the
  smallest judge-council context window — read the `grading.py` header before changing it.

## ANTI-PATTERNS
- Don't call the LiteLLM sidecar from host-side utils (grading/judge) — it's network-isolated
  to the batch; call OpenAI/Bedrock providers directly.
- Don't bypass `Config.from_env`'s `s()/b()/i()/f()` helpers when adding env vars — keep the
  KENSEI_*-first alias ordering.
