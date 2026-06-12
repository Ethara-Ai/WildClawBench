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
| LiteLLM sidecar container mgmt | `litellm_sidecar.py`, `litellm_usage_callback.py`, `litellm_headroom_callback.py` |
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
- Don't call the per-batch LiteLLM **sidecar** from host-side utils — it's network-isolated
  to the batch (no published port, `--internal` bridge). Grading MAY use LiteLLM in
  **library mode** (in-process `litellm.completion()`) when `KENSEI_JUDGE_USE_LITELLM=true`;
  default OFF and falls back to the urllib direct-provider path on any LiteLLM error.
  Both transports MUST produce the same 7-key per-judge `usage` dict shape — see the
  `grading.py` header and `judge_litellm.py` module docstring.
- When LiteLLM library mode is on, judges MUST use Headroom with `compress_system_messages=False`
  (the system prompt is verdict-format-load-bearing — compressing it can break `_VERDICT_RE`).
  See `judge_litellm.maybe_compress` for the locked config.
- The agent-path sidecar Headroom compressor (`litellm_headroom_callback.py`) MUST keep
  `compress_system_messages=False` (openclaw's system prompt carries tool schemas + verbatim
  instructions) and MUST write telemetry to a SEPARATE JSONL via `KENSEI_AGENT_HEADROOM_LOG_PATH`
  — it is FORBIDDEN to write into the 11-key `LITELLM_USAGE_LOG_PATH` JSONL that
  `litellm_usage_callback.py` owns. The two callbacks coexist via LiteLLM's pre-call dispatch
  skip-rule: only headroom overrides `async_pre_call_hook`, only the usage writer overrides
  `async_log_success_event`, so they cannot fight. Headroom mutates the request; the usage
  writer observes the response — order is fixed by LiteLLM's lifecycle, not YAML list position.
- Don't bypass `Config.from_env`'s `s()/b()/i()/f()` helpers when adding env vars — keep the
  KENSEI_*-first alias ordering. (Exception: per-feature judge toggles like
  `KENSEI_JUDGE_USE_LITELLM` and the `KENSEI_JUDGE_HEADROOM_*` family read `os.environ`
  directly, matching the existing `JUDGE_MAX_EVIDENCE` / `JUDGE_COUNCIL_MEMBERS` precedent
  in `grading.py`. Same exception applies to `KENSEI_AGENT_HEADROOM_*` — the sidecar reads
  them at container-start time and forwards them as `-e` flags, not via the host Config
  dataclass. Config is for system-wide infra (Bedrock ARNs, S3 keys), not per-call feature
  flags.)
