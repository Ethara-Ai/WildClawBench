# src/agents — HARNESS BACKENDS

One sub-package per agent scaffold; all implement the `BaseAgent` ABC in `base.py`.

## STRUCTURE
```
base.py            # AgentTaskSpec / AgentExecution dataclasses + BaseAgent(ABC)
openclaw/runner.py # reference harness (long-running gateway; chat.jsonl native)
claudecode/        # runner.py + transcript.py (converts CC chat → openclaw jsonl)
codex/             # Codex CLI harness
hermesagent/       # Hermes Agent harness
```

## CONTRACT (BaseAgent)
Every harness MUST implement:
- `expects_gateway` (property) — does it spawn a persistent gateway proc?
- `transcript_container_path` (property) — in-container chat transcript path
- `run_task(spec: AgentTaskSpec) -> AgentExecution`
- `collect_usage(task_id, output_dir, elapsed_time) -> dict`
- optionally override `prepare_grading_transcript(task_id)`

## CONVENTIONS
- Each harness has its **own Docker image** + env override chain, e.g. ClaudeCode:
  `DOCKER_IMAGE_CLAUDECODE` → `CLAUDECODE_DOCKER_IMAGE` → `wildclawbench-claudecode-ubuntu:v0.2`.
- Non-OpenClaw harnesses convert their native logs into OpenClaw `chat.jsonl` format so
  grading/trajectory code is harness-agnostic (see `claudecode/transcript.py`).
- API key falls back to `OPENROUTER_API_KEY` when no explicit key passed.
- New harness = new sub-package implementing BaseAgent + import/dispatch in
  `eval/run_batch.py` and a case in `script/run.sh`.

## ANTI-PATTERNS
- Don't grade off a harness-native transcript — always normalize to openclaw jsonl first.
- Don't hardcode image tags; honor the per-backend env override chain.
