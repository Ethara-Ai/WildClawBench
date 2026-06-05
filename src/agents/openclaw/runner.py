from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
import time
from pathlib import Path

from dotenv import load_dotenv

from src.agents.base import AgentExecution, AgentTaskSpec, BaseAgent
from src.utils.docker_utils import (
    inject_api_connectors,
    inject_lobster_workspace,
    inject_openclaw_models,
    inject_subagent_tool,
    run_background,
    run_warmup,
    setup_skills,
    setup_workspace,
    snapshot_workspace_state,
    start_container,
    write_turn_marker,
)
from src.utils.grading import extract_usage_from_jsonl, extract_usage_from_litellm_log

load_dotenv()

logger = logging.getLogger(__name__)

# Logical model names used when routing through the LiteLLM sidecar.
MODEL_NAMES: dict[str, str] = {
    "claude": "claude-opus-4.7",
    "gpt": "gpt-5.5",
}

_GPT_PREFIXES = ("gpt", "o1", "o3", "o4", "llama", "mistral", "kimi", "deepseek", "gemini", "qwen")


def _normalize_openrouter_model(model: str) -> str:
    if model.startswith("openrouter/"):
        return model
    if "/" in model:
        return f"openrouter/{model}"
    if any(model.lower().startswith(p) for p in _GPT_PREFIXES):
        return f"openrouter/openai/{model}"
    return f"openrouter/anthropic/{model}"


class OpenClawAgent(BaseAgent):
    """OpenClaw backend with dual routing:

    - LiteLLM/Bedrock mode (when ``litellm_config_yaml`` is set): writes a
      ``models.providers.litellm`` block into openclaw.json pointing at the
      shared LiteLLM sidecar container, and skips OpenRouter auth.
    - OpenRouter mode (default fallback): injects the OpenRouter key into
      auth-profiles.json and sets the model via the normalized model string.
    """

    def __init__(
        self,
        gateway_port: int,
        openrouter_api_key: str = "",
        openrouter_base_url: str = "https://openrouter.ai/api/v1",
        openai_api_key: str = "",
        litellm_master_key: str = "",
        litellm_port: int = 4000,
        litellm_config_yaml: str = "",
        litellm_container_name: str = "",
        litellm_network: str = "",
        image_model: str | None = None,
        litellm_usage_log: str = "",
    ) -> None:
        self.gateway_port = gateway_port
        self.openrouter_api_key = openrouter_api_key
        self.openrouter_base_url = openrouter_base_url
        self.openai_api_key = openai_api_key
        self.litellm_master_key = litellm_master_key
        self.litellm_port = litellm_port
        self.litellm_config_yaml = litellm_config_yaml
        self.litellm_container_name = litellm_container_name
        self.litellm_network = litellm_network
        self.image_model = (
            image_model
            if image_model is not None
            else os.environ.get("OPENCLAW_IMAGE_MODEL", "").strip()
        )
        self.litellm_usage_log = litellm_usage_log
        self._task_windows: dict[str, tuple[float, float]] = {}

    @property
    def expects_gateway(self) -> bool:
        return True

    @property
    def transcript_container_path(self) -> str:
        return "/root/.openclaw/agents/main/sessions/chat.jsonl"

    def prepare_grading_transcript(self, task_id: str) -> str:
        # Snapshot chat.jsonl from the agent container to host BEFORE grading so
        # the grader reads a frozen byte-stream the agent can no longer mutate.
        # Without this the agent (which runs as root in its container with rw
        # access to /root/.openclaw/agents/main/sessions/chat.jsonl) could
        # append fabricated assistant messages claiming the task is done
        # between agent_proc.wait() and grade_the_task. See b54 Issue 6.
        try:
            host_snap = Path(tempfile.gettempdir()) / f"chat-snap-{task_id}.jsonl"
            r = subprocess.run(
                ["docker", "cp", f"{task_id}:{self.transcript_container_path}", str(host_snap)],
                capture_output=True, text=True, timeout=30,
            )
            if r.returncode == 0 and host_snap.exists() and host_snap.stat().st_size > 0:
                return str(host_snap)
        except (subprocess.SubprocessError, OSError) as exc:
            logger.warning("[%s] chat.jsonl host snapshot failed: %s", task_id, exc)
        return self.transcript_container_path

    def run_task(self, spec: AgentTaskSpec) -> AgentExecution:
        gateway_proc = None
        agent_proc = None
        elapsed_time = float(spec.timeout_seconds)

        try:
            exec_path = os.path.join(spec.workspace_path, "exec")
            tmp_path = os.path.join(spec.workspace_path, "tmp")
            os.makedirs(exec_path, exist_ok=True)

            start_container(
                spec.task_id,
                exec_path,
                extra_env=spec.task.get("env", ""),
                tmp_path=tmp_path,
                lobster_env=spec.lobster.get("env") if spec.lobster else None,
                extra_env_dict=spec.task.get("env_dict") or None,
                network=self.litellm_network,
            )

            # Raise openclaw binary's bootstrap-file caps before the gateway
            # starts. Default is 20k chars/file + 150k total, which truncates
            # MEMORY.md and persona files for any non-trivial task.
            self._set_bootstrap_limits(spec.task_id)

            if spec.lobster:
                inject_lobster_workspace(spec.task_id, spec.lobster["workspace"])
                self._index_memory(spec.task_id)

            # Native (kensei2-style) task: inject the task-provided persona
            # (SOUL.md / MEMORY.md / AGENT(S).md, sent at runtime) into /root/ and
            # index it so the agent recalls it. Lives at <task_dir>/persona/.
            persona_dir = spec.task.get("persona_dir") if isinstance(spec.task, dict) else ""
            if persona_dir:
                inject_lobster_workspace(spec.task_id, persona_dir)
                self._index_memory(spec.task_id)

            setup_workspace(spec.task_id, thinking=spec.thinking)
            setup_skills(
                spec.task_id,
                spec.task.get("skills", ""),
                spec.task.get("skills_path", ""),
            )
            # Inject both required AND distractor connector skills so the
            # agent sees plausible-but-unneeded API surfaces alongside the
            # ones it actually needs. Without this, distractors only existed
            # in task.toml + testgen negative-weight tests, so the agent could
            # not realistically be tempted by them at runtime. The two lists
            # are deduplicated; if a distractor was already required (catalog
            # overlap edge case) the connector is only copied once.
            _required = spec.task.get("required_apis", []) or []
            _distractors = spec.task.get("distractor_apis", []) or []
            inject_api_connectors(
                spec.task_id,
                spec.task.get("env_dir", ""),
                list(dict.fromkeys(list(_required) + list(_distractors))),
            )

            run_warmup(spec.task_id, spec.task.get("warmup", ""))

            # Capture workspace state RIGHT BEFORE the agent runs so the
            # post-run diff (see collect_output_from_container) can isolate
            # agent-produced artifacts from the staged input set (data/,
            # persona/, openclaw scratch). Everything written or modified
            # under /tmp_workspace/ between this call and collect time is
            # by definition agent-generated. Codex+claudecode runners already
            # do this; without it openclaw runs land an empty artifacts/ dir.
            snapshot_workspace_state(spec.task_id)

            if spec.models_config:
                inject_openclaw_models(spec.task_id, spec.models_config)

            if spec.multi_agent_enabled:
                inject_subagent_tool(spec.task_id, spec.multi_agent_config)

            self._set_model(spec.task_id, spec.model, thinking=spec.thinking)
            self._inject_auth(spec.task_id)
            image_model = self.image_model or spec.model
            self._set_image_model(spec.task_id, image_model)

            gateway_cmd = f"openclaw gateway --port {self.gateway_port}"
            if self.openrouter_api_key and not self.litellm_config_yaml:
                gateway_cmd = (
                    f"export OPENROUTER_API_KEY='{self.openrouter_api_key}' && "
                    f"export OPENROUTER_BASE_URL='{self.openrouter_base_url}' && "
                    + gateway_cmd
                )
            if self.openai_api_key and not self.litellm_config_yaml:
                gateway_cmd = f"export OPENAI_API_KEY='{self.openai_api_key}' && " + gateway_cmd

            gateway_proc = run_background(
                spec.task_id,
                bash_cmd=gateway_cmd,
                log_path=spec.output_dir / "gateway.log",
            )
            logger.info("[%s] Waiting for gateway (2s)...", spec.task_id)
            time.sleep(2)

            # Multi-turn / staged injection: invoke the agent once per turn on
            # the SAME session ("chat") so context carries across turns. Turn 0
            # is the task prompt; each later turn is a follow-up message, and
            # before each later turn the agent is idle while before_turn(i)
            # applies that stage's silent mock-data injection. Single-turn runs
            # (spec.turns is None) execute exactly one iteration with spec.prompt,
            # behaviour-identical to the prior single-shot path.
            turn_messages: tuple[str, ...] = spec.turns or (spec.prompt,)
            start_time = time.perf_counter()
            wall_start = time.time()
            agent_proc = None
            for turn_index, message in enumerate(turn_messages):
                if turn_index > 0 and spec.before_turn is not None:
                    # Agent is idle here -> apply this stage's injection.
                    try:
                        spec.before_turn(turn_index)
                    except Exception as exc:
                        logger.error("[%s] before_turn(%d) hook failed: %s",
                                     spec.task_id, turn_index, exc)
                if spec.multi_agent_enabled:
                    write_turn_marker(spec.task_id, turn_index)
                safe_msg = message.replace("'", "'\\''")
                if len(turn_messages) > 1:
                    logger.info("[%s] Agent turn %d/%d starting",
                                spec.task_id, turn_index + 1, len(turn_messages))
                agent_proc = run_background(
                    spec.task_id,
                    bash_cmd=(
                        f"openclaw agent --session-id chat "
                        f"--timeout {spec.timeout_seconds} "
                        f"--message '{safe_msg}'"
                    ),
                    log_path=spec.output_dir / "agent.log",
                )
                logger.info("[%s] Waiting for agent to finish...", spec.task_id)
                try:
                    agent_proc.wait(timeout=spec.timeout_seconds)
                    logger.info("[%s] Agent turn %d finished", spec.task_id, turn_index + 1)
                except subprocess.TimeoutExpired:
                    logger.warning("[%s] Agent turn %d timed out", spec.task_id, turn_index + 1)
                    agent_proc.kill()
                    agent_proc.wait()
                    break
            elapsed_time = time.perf_counter() - start_time
            self._task_windows[spec.task_id] = (wall_start, time.time())
            logger.info("[%s] Agent finished (%.2fs, %d turn(s))",
                        spec.task_id, elapsed_time, len(turn_messages))

            logger.info("[%s] Agent exit code: %s", spec.task_id,
                        agent_proc.returncode if agent_proc else "n/a")
            return AgentExecution(
                elapsed_time=elapsed_time,
                error=None,
                gateway_proc=gateway_proc,
                agent_proc=agent_proc,
            )

        except Exception as exc:
            logger.error("[%s] Execution error: %s", spec.task_id, exc)
            return AgentExecution(
                elapsed_time=float(spec.timeout_seconds),
                error=str(exc),
                gateway_proc=gateway_proc,
                agent_proc=agent_proc,
            )

    def collect_usage(self, task_id: str, output_dir: Path, elapsed_time: float) -> dict:
        transcript_host = output_dir / "chat.jsonl"
        output_dir.mkdir(parents=True, exist_ok=True)
        r_cp = subprocess.run(
            ["docker", "cp", f"{task_id}:{self.transcript_container_path}", str(transcript_host)],
            capture_output=True,
            text=True,
        )

        usage: dict
        if self.litellm_usage_log:
            window = self._task_windows.get(task_id)
            if window is None:
                window = (time.time() - max(elapsed_time, 1.0), time.time())
            usage = extract_usage_from_litellm_log(Path(self.litellm_usage_log), window[0], window[1])
        else:
            usage = {"request_count": 0}

        if usage.get("request_count", 0) == 0:
            if r_cp.returncode == 0 and transcript_host.exists():
                usage = extract_usage_from_jsonl(transcript_host)
            else:
                logger.warning("[%s] Transcript copy failed: %s", task_id, r_cp.stderr.strip())
                usage = {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cache_read_tokens": 0,
                    "cache_write_tokens": 0,
                    "total_tokens": 0,
                    "cost_usd": 0.0,
                    "request_count": 0,
                    "usage_source": "none",
                }

        # Fold sub-agent token totals from spawn_tree.jsonl into parent usage so
        # leaderboard cost math reflects the full call graph (not just the
        # parent's LiteLLM hits). Missing/malformed file is silently treated as
        # zero spawns — single-agent tasks must remain byte-identical.
        spawn_tree = output_dir / "task_output" / "workspace_full" / "spawn_tree.jsonl"
        sub_in = sub_out = sub_count = 0
        if spawn_tree.is_file():
            for line in spawn_tree.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                sub_count += 1
                try:
                    sub_in += int(row.get("tokens_in") or 0)
                except (TypeError, ValueError):
                    pass
                try:
                    sub_out += int(row.get("tokens_out") or 0)
                except (TypeError, ValueError):
                    pass
        if sub_count:
            usage["subagent_count"] = sub_count
            usage["subagent_tokens_in"] = sub_in
            usage["subagent_tokens_out"] = sub_out
            usage["input_tokens"] = int(usage.get("input_tokens") or 0) + sub_in
            usage["output_tokens"] = int(usage.get("output_tokens") or 0) + sub_out
            usage["total_tokens"] = int(usage.get("total_tokens") or 0) + sub_in + sub_out

        self._task_windows.pop(task_id, None)
        usage["elapsed_time"] = round(elapsed_time, 2)
        return usage

    def _set_model(self, task_id: str, model: str, thinking: str | None = None) -> None:
        if self.litellm_config_yaml:
            model_id = model[len("litellm/"):] if model.startswith("litellm/") else model
            # Anthropic models use api="anthropic-messages" so openclaw posts via
            # the bundled Anthropic SDK to {baseUrl}/v1/messages and round-trips
            # thinking_blocks[].signature on every turn. The OpenAI Chat
            # Completions wire format has no field for signed thinking blocks
            # and silently drops them, so a single api="openai-completions"
            # provider for all models lost reasoning visibility after turn 0
            # (empirical: alden-croft claude/run_2 had 1/30 thinking_blocks
            # across all assistant turns despite --thinking xhigh). LiteLLM's
            # /v1/messages handler bridges Anthropic <-> Bedrock Converse and
            # preserves thinking_blocks bidirectionally
            # (litellm/litellm_core_utils/prompt_templates/factory.py:4798-4815).
            # gpt-5.5 stays on openai-completions because it has no native
            # thinking blocks to preserve (OpenAI reasoning is internal). The
            # baseUrl suffix differs by SDK: Anthropic SDK appends /v1/messages
            # to model.baseUrl; OpenAI SDK appends /chat/completions and
            # requires baseUrl to already end with /v1.
            is_anthropic_model = "claude" in model_id.lower()
            # openclaw 2026.3.11 gates extended thinking on a HARDCODED model
            # allowlist (config-mlcrIFGX.js): the registry tops out at
            # claude-opus-4-6 / claude-sonnet-4-6 (dash form). supportsXHighThinking
            # does an exact Set.has on `${provider}/${model}` and mapThinkingLevel
            # only emits a thinkingBudget for a recognized id. Our harness model id
            # "claude-opus-4.7" (dot, version 4.7) is NOT in that allowlist, so
            # openclaw never requests reasoning and the trajectory persists 0
            # thinking blocks (empirical: amanda_hayes_01 claude/run_2 had 0/23
            # despite --thinking xhigh). We present a RECOGNIZED id to openclaw so
            # thinking activates; the actual inference still hits the real opus ARN
            # via LiteLLM (litellm_sidecar.py routes both names to the same
            # bedrock/converse ARN). self.model_id stays "claude-opus-4.7" so the
            # output dir + usage threading are unaffected.
            openclaw_model_id = "claude-opus-4-6" if is_anthropic_model else model_id
            # openclaw 2026.3.11 gates thinking-capability on the PROVIDER KEY too,
            # not only the model id. supportsXHighThinking does an exact Set.has on
            # `${provider}/${model}` and XHIGH_MODEL_SET contains only `anthropic/...`
            # refs, and mapThinkingLevel only emits a thinkingBudget for recognized
            # provider+model pairs. Under the custom provider key "litellm" the agent
            # still produced 0 thinking blocks even with the recognized id
            # claude-opus-4-6 (empirical: amanda_hayes_01 claude/run_3 0/29). We
            # therefore register the sidecar provider under the key "anthropic" so
            # `anthropic/claude-opus-4-6` matches the allowlist and thinking
            # activates. Our providers["anthropic"] override carries baseUrl pointing
            # at the LiteLLM sidecar and api="anthropic-messages", so it shadows
            # openclaw's built-in anthropic provider (api.anthropic.com) and all
            # traffic still goes to the sidecar /v1/messages (verified via
            # ll_stream.log POST /v1/messages, never api.anthropic.com).
            provider_key = "anthropic" if is_anthropic_model else "litellm"
            primary = f"{provider_key}/{openclaw_model_id}"
            base_url_root = f"http://{self.litellm_container_name}:{self.litellm_port}"
            base_url_v1 = f"{base_url_root}/v1"
            if is_anthropic_model:
                litellm_provider = {
                    "baseUrl": base_url_root,
                    "apiKey": self.litellm_master_key or "sk-litellm",
                    "api": "anthropic-messages",
                    "models": [
                        {"id": openclaw_model_id, "name": openclaw_model_id,
                         "input": ["text", "image"], "reasoning": True,
                         "contextWindow": 200000, "maxTokens": 128000},
                    ],
                }
            else:
                litellm_provider = {
                    "baseUrl": base_url_v1,
                    "apiKey": self.litellm_master_key or "sk-litellm",
                    "auth": "api-key",
                    "api": "openai-completions",
                    "models": [
                        {"id": "gpt-5.5", "name": "gpt-5.5",
                         "input": ["text", "image"], "reasoning": True,
                         "contextWindow": 1050000, "maxTokens": 128000},
                    ],
                }
            thinking_default = (thinking or "").strip()
            set_thinking_line = (
                f'defaults["thinkingDefault"] = {json.dumps(thinking_default)}\n'
                if thinking_default and thinking_default.lower() not in {"off", "none", "disabled"}
                else ""
            )
            script = f"""\
import json, pathlib
p = pathlib.Path("/root/.openclaw/openclaw.json")
d = json.loads(p.read_text()) if p.exists() else {{}}
models = d.setdefault("models", {{}})
providers = models.setdefault("providers", {{}})
providers[{json.dumps(provider_key)}] = json.loads({json.dumps(json.dumps(litellm_provider))})
agents = d.setdefault("agents", {{}})
defaults = agents.setdefault("defaults", {{}})
defaults["model"] = {{"primary": {json.dumps(primary)}}}
defaults["imageModel"] = {{"primary": {json.dumps(primary)}}}
defaults.pop("models", None)
{set_thinking_line}d["browser"] = {{"enabled": False}}
# Defense-in-depth against headless-browser tools is handled via the
# schema-validated tools.deny list below. Earlier Fix 10 also wrote root
# keys d["chrome"], d["chromium"], d["playwright"], d["puppeteer"],
# d["selenium"], d["webdriver"] = {{"enabled": False}}; the openclaw config
# validator rejected all six on 2026-06-02 ('Unrecognized keys: "chrome",
# "chromium", "playwright", "puppeteer", "selenium", "webdriver"') and
# refused to load the config. tools.deny is the only legal layer for
# extra tool blocks. Image lacks every browser binary (verified
# 2026-06-02) and --internal network blocks egress, so the two remaining
# defense layers are sufficient.
tools = d.setdefault("tools", {{}})
tools["deny"] = [
    "browser", "duckduckgo",
    "chrome", "chromium", "playwright", "puppeteer",
    "selenium", "webdriver", "headless_browser",
    "browser_navigate", "browser_screenshot", "browser_eval",
]
# Exec runs in the openclaw gateway process inside this agent container
# (host='gateway'). The container itself is the sandbox (network-isolated
# via --internal bridge). Two other host values are wrong here:
#   * 'sandbox' spawns a nested Docker container per exec, requires the
#     docker CLI inside this container (wildclawbench-ubuntu:v1.3 has
#     none); seen 2026-06-02 06:43 'Sandbox mode requires Docker'.
#   * 'node' routes exec to a paired companion app over WebSocket, which
#     does not exist in headless benchmark runs; seen 2026-06-02 07:17
#     'exec host=node requires a paired node (none available)'.
exec_cfg = tools.setdefault("exec", {{}})
exec_cfg["host"] = "gateway"
# Bypass exec denial in headless benchmark runs. openclaw's config
# validator (seen 2026-06-02 megan-davis run) accepts exactly three
# values for tools.exec.security: "deny"|"allowlist"|"full". "full"
# disables the per-command human-approval check entirely; without it,
# every exec call waits 120s for an approval channel that does not
# exist in the harness, then fails with
#   'exec denied: host=gateway security=deny'
#   'Channel is required (no configured channels detected)'
# (~25x in 2026-06-02 07:43 gateway.log). tools.exec.approval is NOT
# a recognized key per the same validator; do not add it.
exec_cfg["security"] = "full"
sandbox_cfg = defaults.setdefault("sandbox", {{}})
sandbox_cfg["mode"] = "off"
web = tools.setdefault("web", {{}})
web["search"] = {{"enabled": False}}
web["fetch"] = {{"enabled": False}}
p.write_text(json.dumps(d, indent=2))
"""
        else:
            normalized = _normalize_openrouter_model(model)
            primary = normalized
            script = f"""\
import json, pathlib
p = pathlib.Path("/root/.openclaw/openclaw.json")
d = json.loads(p.read_text()) if p.exists() else {{}}
agents = d.setdefault("agents", {{}})
defaults = agents.setdefault("defaults", {{}})
defaults["model"] = {{"primary": {json.dumps(normalized)}}}
defaults["imageModel"] = {{"primary": {json.dumps(normalized)}}}
defaults.setdefault("models", {{}})[{json.dumps(normalized)}] = {{}}
d["browser"] = {{"enabled": False}}
tools = d.setdefault("tools", {{}})
tools["deny"] = [
    "browser", "duckduckgo",
    "chrome", "chromium", "playwright", "puppeteer",
    "selenium", "webdriver", "headless_browser",
    "browser_navigate", "browser_screenshot", "browser_eval",
]
# Mirror the LiteLLM branch: see comments there for the full rationale,
# including why the chrome/chromium/etc. root-key writes were removed.
exec_cfg = tools.setdefault("exec", {{}})
exec_cfg["host"] = "gateway"
exec_cfg["security"] = "full"
sandbox_cfg = defaults.setdefault("sandbox", {{}})
sandbox_cfg["mode"] = "off"
web = tools.setdefault("web", {{}})
web["search"] = {{"enabled": False}}
web["fetch"] = {{"enabled": False}}
p.write_text(json.dumps(d, indent=2))
"""
        r = subprocess.run(
            ["docker", "exec", "-i", task_id, "python3", "-"],
            input=script,
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            raise RuntimeError(f"Model setup failed:\n{r.stderr}")
        logger.info("[%s] Model set in openclaw.json: %s", task_id, primary)

    def _inject_auth(self, task_id: str) -> None:
        # LiteLLM holds Bedrock/OpenAI creds via its own env; no agent-side key.
        if self.litellm_config_yaml:
            return
        if self.openai_api_key and not self.openrouter_api_key:
            key = self.openai_api_key
            profile_id = "openai:default"
            provider = "openai"
        elif self.openrouter_api_key:
            key = self.openrouter_api_key
            profile_id = "openrouter:default"
            provider = "openrouter"
        else:
            return

        auth_profile_path = "/root/.openclaw/agents/main/agent/auth-profiles.json"
        script = f"""\
import json, pathlib
p = pathlib.Path({json.dumps(auth_profile_path)})
d = json.loads(p.read_text()) if p.exists() else {{"version": 1, "profiles": {{}}}}
d.setdefault("profiles", {{}})[{json.dumps(profile_id)}] = {{
    "type": "api_key",
    "provider": {json.dumps(provider)},
    "key": {json.dumps(key)}
}}
p.write_text(json.dumps(d, indent=2))
"""
        subprocess.run(
            ["docker", "exec", "-i", task_id, "python3", "-"],
            input=script,
            capture_output=True,
            text=True,
        )
        logger.info("[%s] Injected %s key into auth-profiles.json", task_id, provider)

    def _set_image_model(self, task_id: str, model: str) -> None:
        if self.litellm_config_yaml:
            logger.info("[%s] imageModel already set via _set_model (litellm mode)", task_id)
            return
        subprocess.run(
            ["docker", "exec", task_id, "/bin/bash", "-c",
             f"openclaw config set agents.defaults.imageModel.primary '{model}'"],
            capture_output=True,
            text=True,
        )
        logger.info("[%s] imageModel set: %s", task_id, model)

    def _set_bootstrap_limits(
        self,
        task_id: str,
        *,
        per_file_chars: int = 1_000_000_000,
        total_chars: int = 1_000_000_000,
    ) -> None:
        # Round-trip the set with a get-back-and-compare. Silent failure here
        # silently truncates MEMORY.md to 20k chars (binary default) and the
        # agent has no in-context signal it lost its tail. We MUST NOT let a
        # timeout/error propagate — gateway-start downstream is critical and
        # must run regardless of whether this verification succeeded.
        cmd = (
            f"openclaw config set agents.defaults.bootstrapMaxChars {per_file_chars} >/dev/null 2>&1 && "
            f"openclaw config set agents.defaults.bootstrapTotalMaxChars {total_chars} >/dev/null 2>&1 && "
            f"echo -n 'per='; openclaw config get agents.defaults.bootstrapMaxChars; "
            f"echo -n 'total='; openclaw config get agents.defaults.bootstrapTotalMaxChars"
        )
        # Best-effort: raising the bootstrap caps is an optimization (it prevents
        # MEMORY.md truncation), NOT a precondition for the run. A slow/hung
        # `openclaw config` call must never abort the task — under qemu x86
        # emulation the CLI can take far longer than a native invocation, so the
        # timeout is generous and any failure (timeout, non-zero rc) is swallowed.
        try:
            result = subprocess.run(
                ["docker", "exec", task_id, "/bin/bash", "-c", cmd],
                capture_output=True,
                text=True,
                timeout=90,
            )
        except subprocess.TimeoutExpired:
            logger.warning(
                "[%s] Bootstrap-limit tuning timed out; continuing with binary "
                "defaults (MEMORY.md may be truncated at 20k chars)", task_id,
            )
            return
        except OSError as exc:
            logger.warning("[%s] Bootstrap-limit tuning failed to exec (%s); "
                           "continuing", task_id, exc)
            return

        applied_per = f"per={per_file_chars}" in result.stdout
        applied_total = f"total={total_chars}" in result.stdout
        if result.returncode == 0 and applied_per and applied_total:
            logger.info(
                "[%s] Bootstrap limits raised: per_file=%d total=%d (verified)",
                task_id,
                per_file_chars,
                total_chars,
            )
        else:
            logger.warning(
                "[%s] Failed to raise bootstrap limits (rc=%d): %s",
                task_id,
                result.returncode,
                (result.stderr or result.stdout)[:200],
            )

    def _index_memory(self, task_id: str) -> None:
        # openclaw's memory tool searches /root/memory/<YYYY-MM-DD>.md for
        # today's and yesterday's notes on every session bootstrap. Without
        # these files the agent surfaces ENOENT errors (see gateway.log:
        # 'read failed: ENOENT ... /root/memory/<date>.md') and the persona
        # bootstrap silently falls back to a generic LLM with the prompt
        # only. Seed both with MEMORY.md so the daily-memory layer resolves.
        # Bootstrap-file allowlist widened 2026-06-03 to all 7 files openclaw
        # reads on every turn (docs.openclaw.ai/concepts/agent-workspace):
        # AGENTS/AGENT (instructions), SOUL (personality), MEMORY (long-term),
        # IDENTITY (name/vibe), USER (user profile), TOOLS (tool notes),
        # HEARTBEAT (scheduled tasks). Files absent from the task's persona
        # dir are silently skipped — alden-croft ships all 7, renata-voss
        # ships only AGENTS/MEMORY/SOUL. See `inject_lobster_workspace`
        # (docker_utils.py:762) which already does the /root/ surface copy.
        # Bash emits MD:<name>:<state> tokens parsed by the harness. Token grammar
        # is load-bearing (Option A per user m1721 'option a'): each token represents
        # one verified post-copy state. States: present|missing|copy_failed|verified.
        # 'verified' is emitted only after `test -f /root/memory/<name>` succeeds,
        # closing the b89 'is it really there' gap that opaque success logs left open.
        cmd = (
            "mkdir -p /root/memory && "
            "for f in MEMORY.md SOUL.md AGENT.md AGENTS.md "
            "IDENTITY.md USER.md TOOLS.md HEARTBEAT.md; do "
            '  if [ -f "/root/$f" ]; then '
            '    if cp "/root/$f" /root/memory/ 2>/dev/null && [ -f "/root/memory/$f" ]; then '
            '      echo "MD:$f:verified"; '
            "    else "
            '      echo "MD:$f:copy_failed"; '
            "    fi; "
            "  else "
            '    echo "MD:$f:missing"; '
            "  fi; "
            "done; "
            "if [ -f /root/MEMORY.md ]; then "
            '  today=$(date -u +%Y-%m-%d); '
            '  yesterday=$(date -u -d "yesterday" +%Y-%m-%d 2>/dev/null || date -u -v-1d +%Y-%m-%d); '
            '  cp /root/MEMORY.md "/root/memory/${today}.md" && echo "MD:${today}.md:verified" || echo "MD:${today}.md:copy_failed"; '
            '  cp /root/MEMORY.md "/root/memory/${yesterday}.md" && echo "MD:${yesterday}.md:verified" || echo "MD:${yesterday}.md:copy_failed"; '
            "fi; "
            "echo '---INDEX---'; "
            "openclaw memory index --force 2>&1 | tail -3"
        )
        result = subprocess.run(
            ["docker", "exec", task_id, "/bin/bash", "-c", cmd],
            capture_output=True,
            text=True,
        )

        stdout = result.stdout or ""
        index_marker = "---INDEX---"
        md_section, _, index_section = stdout.partition(index_marker)

        verified: list[str] = []
        missing: list[str] = []
        failed: list[str] = []
        for line in md_section.splitlines():
            if not line.startswith("MD:"):
                continue
            _, _, rest = line.partition("MD:")
            name, _, state = rest.partition(":")
            if state == "verified":
                verified.append(name)
            elif state == "missing":
                missing.append(name)
            elif state == "copy_failed":
                failed.append(name)

        logger.info(
            "[%s] Bootstrap MDs indexed: verified=%s missing=%s",
            task_id,
            verified or "[]",
            missing or "[]",
        )
        if failed:
            logger.warning("[%s] Bootstrap MD copy failures: %s", task_id, failed)

        if result.returncode != 0:
            logger.warning("[%s] memory index failed (rc=%d): %s", task_id, result.returncode, (result.stderr or "")[:200])
        elif index_section.strip():
            logger.info("[%s] openclaw memory index: %s", task_id, index_section.strip()[:200])
