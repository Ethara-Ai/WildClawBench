"""
WildClawBench SubagentDirector: harness-provided sub-agent spawn primitive.

The openclaw harness exposes a ``spawn_subagent`` tool when a task opts-in via
``task_config.yaml`` ``multi_agent.enabled: true``. The parent agent calls the
tool with a role, instructions, and an allowed-tool list; this module runs the
child as a short, bounded LLM session against the same LiteLLM sidecar, then
returns the child's final text to the parent.

Two-layer split so the module is testable without HTTP / Docker / a real model:

    pure layer  -> ``run_with_invoker(spec, invoker, ...) -> SubagentResult``
                   ``invoker`` is any callable matching ``InvokerCallable``.
                   Tests pass a scripted ``FakeInvoker``.

    runtime layer -> ``LiteLLMInvoker`` (HTTP) + ``main()`` (CLI / skill entry
                     point). Reads stdin JSON spec, writes one NDJSON row to
                     ``$SPAWN_TREE_PATH`` (default
                     ``/tmp_workspace/spawn_tree.jsonl``), writes the full
                     child transcript to
                     ``/tmp_workspace/subagents/{spawn_id}.jsonl``, prints the
                     final assistant text to stdout for the parent skill.

Hard rules
----------
* No nested spawning. ``allowed_tools`` MUST NOT contain ``spawn_subagent``;
  the runtime layer enforces this and rejects with ``status='blocked'``.
* One NDJSON row per spawn. The spawn_tree row carries a *preview* of the
  child output (first 240 chars) plus a SHA-256 of the full output; the full
  transcript lives in the per-spawn file so the spawn_tree stays small.
* Bounded. ``max_tool_calls`` and ``max_tokens`` are upper bounds; the runtime
  layer also enforces ``timeout_seconds`` wall-clock.
* Turn-correlated. The spawn row carries ``turn_index`` read from
  ``/tmp_workspace/.wildclaw_current_turn`` (written by the openclaw runner
  between turns). Missing / unreadable -> ``turn_index = -1`` (still logged).
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import logging
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

LOG = logging.getLogger(__name__)

# Default container-side paths. Host-side tests override via kwargs.
_DEFAULT_SPAWN_TREE_PATH = Path("/tmp_workspace/spawn_tree.jsonl")
_DEFAULT_TRANSCRIPT_DIR = Path("/tmp_workspace/subagents")
_DEFAULT_TURN_MARKER = Path("/tmp_workspace/.wildclaw_current_turn")

# Hard ceilings - even if a task_config.yaml asks for more, we clamp here so a
# rogue parent cannot blow the budget.
_MAX_TOOL_CALLS_CEILING = 50
_MAX_TOKENS_CEILING = 16384
_MAX_TIMEOUT_CEILING = 600  # seconds
_PREVIEW_CHARS = 240

_BLOCKED_TOOLS = frozenset({"spawn_subagent"})


@dataclass(frozen=True)
class SubagentSpec:
    """Parent-supplied request to spawn a single sub-agent."""

    role: str
    instructions: str
    allowed_tools: tuple[str, ...] = ()
    context: str = ""
    model: str | None = None
    max_tool_calls: int = 20
    max_tokens: int = 4096
    timeout_seconds: int = 120

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> SubagentSpec:
        # Allowed tools accepted as list/tuple; coerce to tuple of str.
        tools_raw = raw.get("allowed_tools") or ()
        if not isinstance(tools_raw, (list, tuple)):
            raise ValueError("allowed_tools must be a list of strings")
        allowed_tools = tuple(str(t) for t in tools_raw)

        return cls(
            role=str(raw.get("role", "")).strip(),
            instructions=str(raw.get("instructions", "")),
            allowed_tools=allowed_tools,
            context=str(raw.get("context", "")),
            model=raw.get("model"),
            max_tool_calls=int(raw.get("max_tool_calls", 20)),
            max_tokens=int(raw.get("max_tokens", 4096)),
            timeout_seconds=int(raw.get("timeout_seconds", 120)),
        )


@dataclass
class SubagentResult:
    """Outcome of one sub-agent run."""

    spawn_id: str
    role: str
    output: str
    tool_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    elapsed_seconds: float = 0.0
    # ok | timeout | error | blocked
    status: str = "ok"
    error: str | None = None

    def to_log_row(
        self,
        *,
        spec: SubagentSpec,
        turn_index: int,
        parent_session_id: str | None,
    ) -> dict[str, Any]:
        out = self.output or ""
        digest = hashlib.sha256(out.encode("utf-8", "replace")).hexdigest()
        return {
            "ts": time.time(),
            "spawn_id": self.spawn_id,
            "parent_session_id": parent_session_id,
            "turn_index": turn_index,
            "role": self.role,
            "status": self.status,
            "error": self.error,
            "allowed_tools": list(spec.allowed_tools),
            "model": spec.model,
            "max_tool_calls": spec.max_tool_calls,
            "max_tokens": spec.max_tokens,
            "timeout_seconds": spec.timeout_seconds,
            "tool_calls": self.tool_calls,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "output_preview": out[:_PREVIEW_CHARS],
            "output_sha256": digest,
            "output_chars": len(out),
        }


# An invoker receives a system prompt + a user prompt and returns a dict:
#   {"output": str, "tool_calls": int, "tokens_in": int, "tokens_out": int}
# Tests use a FakeInvoker; production uses LiteLLMInvoker.
InvokerCallable = Callable[[str, str, SubagentSpec], Mapping[str, Any]]


def _build_system_prompt(spec: SubagentSpec) -> str:
    """Construct the constrained system prompt for the sub-agent.

    The system prompt names the role, restates the allow-list as a hard
    constraint, and forbids nested spawning even if the model has learned
    about the tool elsewhere.
    """
    tools_line = (
        ", ".join(spec.allowed_tools) if spec.allowed_tools else "(none — text-only)"
    )
    return (
        f"You are a sub-agent in role: {spec.role}.\n"
        "You were spawned by a parent agent to perform one bounded task and "
        "return a concise textual answer.\n"
        f"You may only use these tools: {tools_line}.\n"
        "You MUST NOT spawn further sub-agents. The spawn_subagent tool is "
        "unavailable to you even if you have seen it before.\n"
        f"Stop after at most {spec.max_tool_calls} tool calls and return your "
        "final answer as plain text."
    )


def _build_user_prompt(spec: SubagentSpec) -> str:
    parts = [spec.instructions.strip()]
    ctx = spec.context.strip()
    if ctx:
        parts.append("\n\n--- Additional context ---\n" + ctx)
    return "\n".join(p for p in parts if p)


def _validate_spec(spec: SubagentSpec) -> str | None:
    if not spec.role:
        return "role is required"
    if not spec.instructions.strip():
        return "instructions are required"
    blocked = sorted(set(spec.allowed_tools) & _BLOCKED_TOOLS)
    if blocked:
        return (
            "nested spawning is forbidden; allowed_tools contains: "
            + ", ".join(blocked)
        )
    if spec.max_tool_calls < 0 or spec.max_tool_calls > _MAX_TOOL_CALLS_CEILING:
        return f"max_tool_calls must be in [0, {_MAX_TOOL_CALLS_CEILING}]"
    if spec.max_tokens <= 0 or spec.max_tokens > _MAX_TOKENS_CEILING:
        return f"max_tokens must be in (0, {_MAX_TOKENS_CEILING}]"
    if spec.timeout_seconds <= 0 or spec.timeout_seconds > _MAX_TIMEOUT_CEILING:
        return f"timeout_seconds must be in (0, {_MAX_TIMEOUT_CEILING}]"
    return None


def run_with_invoker(
    spec: SubagentSpec,
    invoker: InvokerCallable,
    *,
    spawn_id: str | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> SubagentResult:
    """Run one sub-agent using ``invoker`` (pure, no I/O).

    ``invoker`` is responsible for HTTP / model interaction; this function
    only validates, builds prompts, dispatches once, and packages the result.
    A ``TimeoutError`` raised by the invoker becomes ``status='timeout'``;
    any other exception becomes ``status='error'``.
    """
    sid = spawn_id or _new_spawn_id()
    err = _validate_spec(spec)
    if err is not None:
        return SubagentResult(
            spawn_id=sid,
            role=spec.role or "<unspecified>",
            output="",
            status="blocked",
            error=err,
        )

    sys_prompt = _build_system_prompt(spec)
    usr_prompt = _build_user_prompt(spec)

    t0 = clock()
    try:
        raw = invoker(sys_prompt, usr_prompt, spec)
    except TimeoutError as exc:
        return SubagentResult(
            spawn_id=sid,
            role=spec.role,
            output="",
            status="timeout",
            error=str(exc) or "invoker timed out",
            elapsed_seconds=clock() - t0,
        )
    except Exception as exc:  # noqa: BLE001 — surface any invoker failure
        return SubagentResult(
            spawn_id=sid,
            role=spec.role,
            output="",
            status="error",
            error=f"{type(exc).__name__}: {exc}",
            elapsed_seconds=clock() - t0,
        )
    elapsed = clock() - t0

    if not isinstance(raw, Mapping):
        return SubagentResult(
            spawn_id=sid,
            role=spec.role,
            output="",
            status="error",
            error=f"invoker returned non-mapping: {type(raw).__name__}",
            elapsed_seconds=elapsed,
        )

    return SubagentResult(
        spawn_id=sid,
        role=spec.role,
        output=str(raw.get("output", "")),
        tool_calls=int(raw.get("tool_calls", 0) or 0),
        tokens_in=int(raw.get("tokens_in", 0) or 0),
        tokens_out=int(raw.get("tokens_out", 0) or 0),
        elapsed_seconds=elapsed,
        status="ok",
    )


def _new_spawn_id() -> str:
    return "spw_" + uuid.uuid4().hex[:12]


def read_current_turn(marker_path: Path = _DEFAULT_TURN_MARKER) -> int:
    """Read the 0-indexed current turn the openclaw runner is on.

    Returns -1 if the marker is missing or unparseable; callers should still
    log the row (the spawn happened; the absent turn just means the runner
    has not started turn tracking yet, e.g. tests).
    """
    try:
        return int(marker_path.read_text().strip())
    except (OSError, ValueError):
        return -1


def append_spawn_row(
    row: Mapping[str, Any],
    *,
    spawn_tree_path: Path = _DEFAULT_SPAWN_TREE_PATH,
) -> None:
    spawn_tree_path.parent.mkdir(parents=True, exist_ok=True)
    with spawn_tree_path.open("a", encoding="utf-8") as fp:
        fp.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_full_transcript(
    spawn_id: str,
    *,
    spec: SubagentSpec,
    result: SubagentResult,
    sys_prompt: str,
    usr_prompt: str,
    transcript_dir: Path = _DEFAULT_TRANSCRIPT_DIR,
) -> Path:
    transcript_dir.mkdir(parents=True, exist_ok=True)
    path = transcript_dir / f"{spawn_id}.jsonl"
    rows = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": usr_prompt},
        {
            "role": "assistant",
            "content": result.output,
            "status": result.status,
            "error": result.error,
            "tokens_in": result.tokens_in,
            "tokens_out": result.tokens_out,
            "tool_calls": result.tool_calls,
            "elapsed_seconds": result.elapsed_seconds,
            "spec": dataclasses.asdict(spec),
        },
    ]
    with path.open("w", encoding="utf-8") as fp:
        for r in rows:
            fp.write(json.dumps(r, ensure_ascii=False) + "\n")
    return path


class LiteLLMInvoker:
    """HTTP invoker that talks to the LiteLLM sidecar.

    Uses the OpenAI-compatible ``/v1/chat/completions`` endpoint because that
    is what the WildClawBench sidecar always exposes (Bedrock/Anthropic/etc.
    are all proxied behind it). We do not pass a tool spec on this round —
    v1 sub-agents return text only; tools enter in a later phase.
    """

    def __init__(
        self,
        *,
        base_url: str,
        default_model: str,
        api_key: str | None = None,
    ):
        if not base_url:
            raise ValueError("LiteLLMInvoker requires base_url")
        self.base_url = base_url.rstrip("/")
        self.default_model = default_model
        self.api_key = api_key

    def __call__(
        self, sys_prompt: str, usr_prompt: str, spec: SubagentSpec
    ) -> Mapping[str, Any]:
        # Import locally so the host-side `import subagent_director` does not
        # require `requests` to be installed for tests / static analysis.
        import requests  # type: ignore[import-not-found]

        model = spec.model or self.default_model
        headers: dict[str, str] = {"content-type": "application/json"}
        if self.api_key:
            headers["authorization"] = f"Bearer {self.api_key}"

        payload = {
            "model": model,
            "max_tokens": spec.max_tokens,
            "messages": [
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": usr_prompt},
            ],
        }
        url = f"{self.base_url}/v1/chat/completions"
        try:
            resp = requests.post(
                url, headers=headers, json=payload, timeout=spec.timeout_seconds
            )
        except requests.Timeout as exc:  # type: ignore[attr-defined]
            raise TimeoutError(str(exc)) from exc
        if resp.status_code >= 500:
            raise RuntimeError(f"LiteLLM {resp.status_code}: {resp.text[:200]}")
        if resp.status_code >= 400:
            raise RuntimeError(f"LiteLLM {resp.status_code}: {resp.text[:200]}")
        body = resp.json()
        choice = (body.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        output = msg.get("content") or ""
        usage = body.get("usage") or {}
        return {
            "output": output,
            "tool_calls": 0,
            "tokens_in": int(usage.get("prompt_tokens", 0) or 0),
            "tokens_out": int(usage.get("completion_tokens", 0) or 0),
        }


def _make_invoker_from_env() -> LiteLLMInvoker:
    base = os.environ.get("LITELLM_BASE_URL") or os.environ.get("LITELLM_URL")
    if not base:
        raise RuntimeError(
            "LITELLM_BASE_URL is not set in the sub-agent environment"
        )
    model = (
        os.environ.get("WILDCLAW_SUBAGENT_MODEL")
        or os.environ.get("WILDCLAW_MODEL")
        or "claude-opus-4-7"
    )
    return LiteLLMInvoker(
        base_url=base,
        default_model=model,
        api_key=os.environ.get("LITELLM_API_KEY") or os.environ.get("OPENAI_API_KEY"),
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Skill entry point. Reads spec JSON from stdin, prints text to stdout."""
    parser = argparse.ArgumentParser(
        description="Run one WildClawBench sub-agent."
    )
    parser.add_argument(
        "--spawn-tree",
        type=Path,
        default=_DEFAULT_SPAWN_TREE_PATH,
        help="Path to the spawn_tree.jsonl ledger (one NDJSON row appended).",
    )
    parser.add_argument(
        "--transcript-dir",
        type=Path,
        default=_DEFAULT_TRANSCRIPT_DIR,
        help="Directory for per-spawn full transcripts.",
    )
    parser.add_argument(
        "--turn-marker",
        type=Path,
        default=_DEFAULT_TURN_MARKER,
        help="File holding the openclaw runner's current 0-indexed turn.",
    )
    args = parser.parse_args(argv)

    try:
        raw = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError as exc:
        print(f"subagent_director: invalid JSON on stdin: {exc}", file=sys.stderr)
        return 2

    # Batch mode: stdin is either a bare list of specs or {"specs": [...]}.
    # Hard concurrency cap of _MAX_PARALLEL_SPAWNS (=5) keeps a single parent
    # turn from saturating the sidecar; results are emitted in completion order
    # as one JSON object per line, so the parent skill can stream them back.
    batch: list | None = None
    if isinstance(raw, list):
        batch = raw
    elif isinstance(raw, dict) and isinstance(raw.get("specs"), list):
        batch = raw["specs"]

    try:
        invoker = _make_invoker_from_env()
    except Exception as exc:  # noqa: BLE001
        print(f"subagent_director: cannot build invoker: {exc}", file=sys.stderr)
        return 2

    if batch is not None:
        return _run_batch_main(
            batch,
            invoker=invoker,
            spawn_tree=args.spawn_tree,
            transcript_dir=args.transcript_dir,
            turn_marker=args.turn_marker,
        )

    try:
        spec = SubagentSpec.from_dict(raw)
    except Exception as exc:  # noqa: BLE001
        print(f"subagent_director: bad spec: {exc}", file=sys.stderr)
        return 2

    sys_prompt = _build_system_prompt(spec)
    usr_prompt = _build_user_prompt(spec)
    result = run_with_invoker(spec, invoker)

    turn_index = read_current_turn(args.turn_marker)
    parent_session = os.environ.get("WILDCLAW_PARENT_SESSION_ID")

    row = result.to_log_row(
        spec=spec, turn_index=turn_index, parent_session_id=parent_session
    )
    try:
        append_spawn_row(row, spawn_tree_path=args.spawn_tree)
        write_full_transcript(
            result.spawn_id,
            spec=spec,
            result=result,
            sys_prompt=sys_prompt,
            usr_prompt=usr_prompt,
            transcript_dir=args.transcript_dir,
        )
    except OSError as exc:
        # Logging failed but the spawn itself ran; surface to parent on stderr
        # and still return the output so the parent isn't stuck.
        print(
            f"subagent_director: spawn_tree write failed: {exc}", file=sys.stderr
        )

    if result.status != "ok":
        # Parent skill sees the error in stderr; stdout still carries whatever
        # text the model produced (often empty for blocked/timeout/error).
        print(
            f"subagent_director: status={result.status} error={result.error}",
            file=sys.stderr,
        )
    sys.stdout.write(result.output)
    return 0 if result.status == "ok" else 1


_MAX_PARALLEL_SPAWNS = 5


def run_batch_parallel(
    specs: Sequence[SubagentSpec],
    invoker: "InvokerCallable",
    *,
    max_concurrency: int = _MAX_PARALLEL_SPAWNS,
) -> list[SubagentResult]:
    """Run multiple SubagentSpec in parallel via a thread pool.

    Returns results in *completion* order (not input order) so a slow spawn
    cannot block the parent from acting on faster siblings. Each result still
    carries its own ``spawn_id`` for downstream correlation. Concurrency is
    capped at ``min(len(specs), max_concurrency, _MAX_PARALLEL_SPAWNS)``.
    """
    import concurrent.futures
    cap = max(1, min(len(specs) or 1, int(max_concurrency), _MAX_PARALLEL_SPAWNS))
    if not specs:
        return []
    results: list[SubagentResult] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=cap) as pool:
        futures = [pool.submit(run_with_invoker, s, invoker) for s in specs]
        for fut in concurrent.futures.as_completed(futures):
            try:
                results.append(fut.result())
            except Exception as exc:  # noqa: BLE001 - surface as error row
                results.append(SubagentResult(
                    spawn_id=_new_spawn_id(),
                    role="<unknown>",
                    output="",
                    tool_calls=0,
                    tokens_in=0,
                    tokens_out=0,
                    elapsed_seconds=0.0,
                    status="error",
                    error=f"{type(exc).__name__}: {exc}",
                ))
    return results


def _run_batch_main(
    raw_specs: list,
    *,
    invoker: "InvokerCallable",
    spawn_tree: Path,
    transcript_dir: Path,
    turn_marker: Path,
) -> int:
    specs: list[SubagentSpec] = []
    for i, raw in enumerate(raw_specs):
        try:
            specs.append(SubagentSpec.from_dict(raw))
        except Exception as exc:  # noqa: BLE001
            print(
                f"subagent_director: batch[{i}] bad spec: {exc}", file=sys.stderr
            )
            return 2

    results = run_batch_parallel(specs, invoker)
    turn_index = read_current_turn(turn_marker)
    parent_session = os.environ.get("WILDCLAW_PARENT_SESSION_ID")

    by_spawn_id = {r.spawn_id: r for r in results}
    spec_by_role = {s.role: s for s in specs}

    any_error = False
    for result in results:
        spec = spec_by_role.get(result.role)
        if spec is None:
            spec = specs[0]
        row = result.to_log_row(
            spec=spec, turn_index=turn_index, parent_session_id=parent_session
        )
        try:
            append_spawn_row(row, spawn_tree_path=spawn_tree)
            write_full_transcript(
                result.spawn_id,
                spec=spec,
                result=result,
                sys_prompt=_build_system_prompt(spec),
                usr_prompt=_build_user_prompt(spec),
                transcript_dir=transcript_dir,
            )
        except OSError as exc:
            print(
                f"subagent_director: batch spawn_tree write failed: {exc}",
                file=sys.stderr,
            )
        if result.status != "ok":
            any_error = True
        sys.stdout.write(json.dumps({
            "spawn_id": result.spawn_id,
            "role": result.role,
            "status": result.status,
            "output": result.output,
            "error": result.error,
            "tokens_in": result.tokens_in,
            "tokens_out": result.tokens_out,
            "tool_calls": result.tool_calls,
        }) + "\n")
    return 1 if any_error else 0


if __name__ == "__main__":  # pragma: no cover - CLI dispatch
    raise SystemExit(main())
