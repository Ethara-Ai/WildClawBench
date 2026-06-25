from __future__ import annotations

import json
import logging
import os
import subprocess
import time

logger = logging.getLogger(__name__)

LITELLM_IMAGE = "ghcr.io/berriai/litellm:main-stable"
LITELLM_INTERNAL_PORT = 4000
LITELLM_HEADROOM_IMAGE = "wildclawbench-litellm-headroom:v2"


def build_litellm_config_yaml(
    bedrock_arn: str = "",
    aws_region: str = "ap-south-1",
    openai_api_key: str = "",
    bedrock_sonnet_arn: str = "",
    enable_usage_callback: bool = False,
    enable_headroom_callback: bool = False,
    openai_whisper_api_key: str = "",
) -> str:
    model_blocks: list[str] = []
    # `cache_control_injection_points` MUST live inside each model's
    # `litellm_params:` block, NOT in global `litellm_settings:`. Empirically
    # verified with the LiteLLM main-stable image (probe_cache.py + GitHub
    # source litellm/litellm_core_utils/litellm_logging.py:917): the
    # `anthropic_cache_control_hook` only instantiates when the directive is
    # in the per-call `non_default_params` payload, which is fed from per-
    # model params. The global-settings form was accepted by the proxy
    # (visible in DEBUG logs as `setting litellm.cache_control_injection_points
    # =[...]`) but never produced cache_read/cache_write > 0 on any request.
    # Required on every Anthropic-on-Bedrock route. OpenAI routes auto-cache
    # server-side at >=1024 prompt tokens and do not need the directive.
    cache_marker = (
        "      cache_control_injection_points:\n"
        "        - location: message\n"
        "          role: system\n"
    )
    if bedrock_arn:
        # Extended-thinking visibility on opus-4-6/4-7 via Bedrock Converse needs the
        # EXACT pair thinking:{type:adaptive} + output_config:{effort}. Three shapes
        # were tried empirically against this LiteLLM (main-stable sha 75543fa1d739):
        #   - thinking:{type:enabled,budget_tokens} -> Bedrock 400 "thinking.type.enabled
        #     is not supported... use thinking.type.adaptive and output_config.effort".
        #   - bare thinking:{type:adaptive} (no effort) -> Bedrock accepts but returns
        #     ZERO reasoningContent -> 0 thinking blocks every turn (run_4/run_5).
        #   - additional_model_request_fields:{...} -> Bedrock 400 "Extra inputs are
        #     not permitted" (LiteLLM forwards the unknown key literally).
        # The thinking block MUST include display:"summarized". Empirically (direct
        # Bedrock /converse probes against this opus ARN, sheep-math reasoning prompt):
        #   - thinking:{type:adaptive} alone           -> reasoning text_len=0 (EMPTY),
        #     signature present. This is what reasoning_effort:high builds, and it made
        #     openclaw persist an EMPTY thinking block (run_6 1/32, text len 0).
        #   - thinking:{type:adaptive,display:summarized} -> reasoning text_len 289-665
        #     (POPULATED) + signature. THIS is the shape that yields visible reasoning.
        #   - display:"detailed" -> Bedrock 400 "display: Input should be summarized or
        #     omitted". Valid values are ONLY "summarized" or "omitted".
        # output_config:{effort} is OPTIONAL once display:summarized is present (the
        # no-output_config probe returned MORE text, 665). So we pass the explicit
        # thinking dict (same known-good shape the sonnet judge entry uses below) rather
        # than reasoning_effort:high, which strips display and yields empty text.
        #
        # CRITICAL routing/detection decoupling (still required): adaptive-thinking
        # detection keys off the `model` STRING via get_base_model()->_is_opus_4_6_model()
        # substring match. Our opus access is an opaque application-inference-profile ARN
        # (.../j6mdizxjngus); putting that ARN in `model:` makes get_base_model return
        # "j6mdizxjngus" (split('/')[-1]) -> fails the opus-4-6 substring -> Bedrock
        # 400s the legacy shape. Fix: `model:` carries the RECOGNIZABLE name
        # "anthropic.claude-opus-4-6-v1"; `model_id:` (common_utils.py:get_bedrock_model_id
        # pops it, URL-encodes into the endpoint URL) carries the real ARN for routing.
        # Do NOT collapse these into a single `model: bedrock/converse/<ARN>` line, and
        # do NOT drop display:summarized -- either re-breaks thinking visibility.
        opus_params = (
            "    litellm_params:\n"
            # NO `converse/` infix: per LiteLLM common_utils.py:873 (`if "claude"
            # in model -> AmazonAnthropicClaudeMessagesConfig`), /v1/messages routes
            # a claude model through Bedrock INVOKE (native Anthropic SSE) not
            # Converse. Direct HTTP probes proved Invoke emits parseable thinking_
            # delta+signature_delta+text_delta; Converse leaks camelCase
            # reasoningContent that pi-ai's @anthropic-ai/sdk cannot parse. The
            # "anthropic.claude-opus-4-6-v1" substring must remain so get_base_model
            # ->_is_opus_4_6_model() matches and adaptive detection fires; model_id
            # carries the real ARN. Do NOT re-add `converse/` -- re-breaks thinking.
            "      model: bedrock/anthropic.claude-opus-4-6-v1\n"
            f"      model_id: {bedrock_arn}\n"
            f"      aws_region_name: {aws_region or 'ap-south-1'}\n"
            # output_config.effort:high: probes showed bare adaptive can return an
            # empty/absent thinking block; +effort:high reliably populates it.
            "      thinking: {\"type\": \"adaptive\", \"display\": \"summarized\"}\n"
            "      output_config: {\"effort\": \"high\"}\n"
            "      stream_options:\n"
            "        include_usage: true\n"
            + cache_marker
            + "      input_cost_per_token: 0.000005\n"
            "      output_cost_per_token: 0.000025"
        )
        model_blocks.append("  - model_name: claude-opus-4.7\n" + opus_params)
        # openclaw's _set_model presents the recognized id "claude-opus-4-6" to
        # activate extended thinking (see runner.py); that id arrives here on the
        # /v1/messages route and must resolve to the SAME opus ARN as
        # claude-opus-4.7. Both names alias one ARN so the harness model arg and
        # the openclaw-facing id stay decoupled.
        model_blocks.append("  - model_name: claude-opus-4-6\n" + opus_params)
    if bedrock_sonnet_arn:
        model_blocks.append(
            "  - model_name: claude-sonnet-4-6\n"
            "    litellm_params:\n"
            f"      model: bedrock/converse/{bedrock_sonnet_arn}\n"
            f"      aws_region_name: {aws_region or 'ap-south-1'}\n"
            "      thinking: {\"type\": \"adaptive\", \"display\": \"summarized\"}\n"
            "      stream_options:\n"
            "        include_usage: true\n"
            + cache_marker
            + "      input_cost_per_token: 0.000003\n"
            "      output_cost_per_token: 0.000015"
        )
    if openai_api_key:
        # The dict `reasoning_effort: {effort, summary}` shape is a Responses
        # API feature; /v1/chat/completions rejects it as
        # `invalid_request_error: Unsupported value: 'reasoning_effort' does
        # not support {...}. Supported values are: 'none','low','medium','high'`.
        # That is exactly the silent 'Connection error.' the agent saw on
        # 2026-06-02 (alden-croft run_3 chat.jsonl 4x stopReason=error).
        # Fix: prefix the upstream model with `openai/responses/` so LiteLLM
        # bridges every chat-completions call to /v1/responses, where the
        # dict form is accepted. summary="auto" (NOT "detailed") because per
        # LiteLLM docs/providers/openai#reasoning-effort, "detailed" requires
        # OpenAI org verification and 400s otherwise; "auto" works for any
        # gpt-5.5 caller and still emits a reasoning summary. gpt-5.5 default
        # effort is "medium"; we keep "high" deliberately for hard tasks.
        model_blocks.append(
            "  - model_name: gpt-5.5\n"
            "    litellm_params:\n"
            "      model: openai/responses/gpt-5.5\n"
            "      api_key: os.environ/OPENAI_API_KEY\n"
            "      reasoning_effort: {\"effort\": \"high\", \"summary\": \"auto\"}\n"
            "      stream_options:\n"
            "        include_usage: true\n"
            "      input_cost_per_token: 0.000005\n"
            "      output_cost_per_token: 0.00003"
        )
        # Without this, /v1/audio/transcriptions returns HTTP 400 "Invalid
        # model name passed in model=whisper-1" (see failure report §6a) and
        # the agent burns its budget on broken pip-install whisper fallbacks.
        model_blocks.append(
            "  - model_name: whisper-1\n"
            "    litellm_params:\n"
            "      model: openai/whisper-1\n"
            # Dedicated transcription key (KENSEI_OPENAI_WHISPER_API_KEY); start_litellm
            # sets OPENAI_WHISPER_API_KEY = whisper key, falling back to the main key.
            "      api_key: os.environ/OPENAI_WHISPER_API_KEY"
        )
        # OpenClaw's built-in transcribeAudio runner auto-POSTs the sidecar's
        # /v1/audio/transcriptions but its OpenAI plugin defaults to model=
        # "gpt-4o-mini-transcribe" (DEFAULT_OPENAI_AUDIO_MODEL), NOT whisper-1.
        # With only whisper-1 registered, that request 400s "Invalid model
        # name" and the agent punts ("give it a listen yourself"), zeroing
        # audio-dependent criteria. Alias every audio id openclaw can emit to
        # the same openai/whisper-1 upstream (a pure sidecar rewrite, same
        # pattern as the image aliases below). whisper-1 is the correct OpenAI
        # transcription model + /v1/audio/transcriptions the correct multipart
        # endpoint per developers.openai.com/api/docs/guides/speech-to-text.
        for _audio_fallback_id in (
            "gpt-4o-mini-transcribe",
            "gpt-4o-transcribe",
        ):
            model_blocks.append(
                f"  - model_name: {_audio_fallback_id}\n"
                "    litellm_params:\n"
                "      model: openai/whisper-1\n"
                "      api_key: os.environ/OPENAI_WHISPER_API_KEY"
            )
    # OpenClaw's image tool falls back to built-in default model ids when its
    # own imageModel override isn't applied inside the container. The openclaw
    # 2026.3.11 dist (verified via grep of /usr/lib/node_modules/openclaw/dist)
    # references BOTH "gpt-4o" (32x) and "gpt-4o-mini" (81x) as defaults, and the
    # image tool emits them under the "anthropic/" provider slot. The gateway
    # doesn't expose those ids, so multimodal calls die with e.g.
    # "Unknown model: anthropic/gpt-4o-mini" (see gateway.log 2026-06-04 06:57).
    # We alias EVERY fallback id openclaw can emit to a real vision-capable model
    # that IS registered, so image tasks resolve instead of erroring. The alias
    # is a pure sidecar rewrite: a request labeled "anthropic/gpt-4o-mini" is
    # transparently served by gpt-5.5 (or the Opus profile) and NEVER reaches a
    # real OpenAI gpt-4o/gpt-4o-mini endpoint -- no extra cost, no egress, no
    # bypass of the --internal sandbox. Prefer GPT-5.5 (OpenAI), else Opus.
    if openai_api_key:
        image_alias = (
            "      model: openai/responses/gpt-5.5\n"
            "      api_key: os.environ/OPENAI_API_KEY"
        )
    elif bedrock_arn:
        image_alias = (
            f"      model: bedrock/converse/{bedrock_arn}\n"
            f"      aws_region_name: {aws_region or 'ap-south-1'}\n"
            + cache_marker.rstrip("\n")
        )
    else:
        image_alias = ""
    if image_alias:
        for _img_fallback_id in (
            "anthropic/gpt-4o",
            "anthropic/gpt-4o-mini",
            "gpt-4o",
            "gpt-4o-mini",
        ):
            model_blocks.append(
                f"  - model_name: {_img_fallback_id}\n"
                "    litellm_params:\n"
                + image_alias
            )
    if not model_blocks:
        return ""
    # Real per-call usage from the proxy itself (not the agent's chat.jsonl
    # which openclaw writes with all-zero usage). Loaded by the LiteLLM
    # callback file mounted at /app/litellm_usage_callback.py.
    #
    # When `enable_headroom_callback=True`, mount the Headroom pre-call
    # compressor AFTER the usage callback. Ordering rationale: LiteLLM iterates
    # `for cb in litellm.callbacks` for the pre-call dispatch. The usage
    # callback does NOT override `async_pre_call_hook` so LiteLLM auto-skips
    # it in that loop (litellm/proxy/utils.py skip-rule: `if cb.async_pre_call_hook
    # != CustomLogger.async_pre_call_hook`), which means the headroom callback
    # is effectively first in the pre-call phase regardless of list position.
    # Post-call, the usage logger sees `kwargs["messages"]` AS COMPRESSED, so
    # it records the post-compression token count — exactly what Bedrock/OpenAI
    # billed — preserving the existing 11-key JSONL schema unchanged.
    _cbs: list[str] = []
    if enable_usage_callback:
        _cbs.append("litellm_usage_callback.proxy_handler_instance")
    if enable_headroom_callback:
        _cbs.append("litellm_headroom_callback.headroom_callback_instance")
    if _cbs:
        callback_line = "  callbacks: [" + ", ".join(f'"{c}"' for c in _cbs) + "]\n"
    else:
        callback_line = ""
    return (
        "model_list:\n"
        + "\n".join(model_blocks)
        + "\n"
        "litellm_settings:\n"
        "  drop_params: true\n"
        "  modify_params: true\n"
        "  telemetry: false\n"
        # User policy m1386 2026-06-02: maximum extension on the LiteLLM-side
        # timeouts. 86400s = 24h is the largest value httpx will accept as a
        # positive float without overflow concerns; LiteLLM rejects null/-1/0/
        # 'infinity' so this is the de-facto 'indefinite' value. num_retries
        # bumped to 10 for non-openclaw paths (testgen, judge council) which
        # call LiteLLM directly. CAVEAT: for the openclaw agent backend, the
        # openclaw npm package has its OWN hardcoded ~22s 'LLM request timed
        # out' ceiling on /v1/messages and /chat/completions — raising these
        # numbers does NOT help openclaw runs hit by that ceiling. Do not
        # 'normalize' these values back down without rereading b66 and m1386.
        "  num_retries: 10\n"
        "  request_timeout: 86400\n"
        "  stream_timeout: 86400\n"
        "  reasoning_auto_summary: true\n"
        # Transcription response cache: LiteLLM keys on the audio BYTE hash
        # (auto-injected metadata.file_checksum), NOT the filename, so distinct
        # recordings never collide. supported_call_types is scoped to ONLY
        # (a)transcription so chat/judge/opus caching is unaffected; do not widen
        # it without re-checking judge-council determinism.
        "  cache: true\n"
        "  cache_params:\n"
        "    type: local\n"
        "    supported_call_types: [\"transcription\", \"atranscription\"]\n"
        + callback_line
        + "general_settings:\n"
        "  master_key: os.environ/LITELLM_MASTER_KEY\n"
        "  store_model_in_db: false\n"
    )


def _image_present_locally(image: str) -> bool:
    return subprocess.run(
        ["docker", "image", "inspect", image],
        capture_output=True,
    ).returncode == 0


def pull_litellm_image(image: str = LITELLM_IMAGE) -> None:
    # `:main-stable` is a moving registry tag; we explicitly pull at batch
    # startup so registry/network failures surface here instead of inside the
    # first `docker run` and being misattributed to a task error.
    #
    # The pull is a registry round-trip even when the image is already cached
    # locally (moving tag), so a slow/blocked/unauthenticated ghcr.io connection
    # can hang the whole batch at startup. To stay robust:
    #   * WILDCLAW_SKIP_LITELLM_PULL=1 skips the pull entirely when the image is
    #     present locally (offline / pinned-image runs).
    #   * the pull is time-bounded (WILDCLAW_LITELLM_PULL_TIMEOUT, default 180s).
    #   * on timeout/failure we fall back to the local image if present, only
    #     raising when there is genuinely no image to run.
    if os.environ.get("WILDCLAW_SKIP_LITELLM_PULL") and _image_present_locally(image):
        logger.info("Skipping LiteLLM pull (WILDCLAW_SKIP_LITELLM_PULL set); using local %s", image)
        return

    timeout = int(os.environ.get("WILDCLAW_LITELLM_PULL_TIMEOUT", "180"))
    logger.info("Pulling LiteLLM image %s (timeout %ss)", image, timeout)
    try:
        r = subprocess.run(
            ["docker", "pull", image],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        if _image_present_locally(image):
            logger.warning(
                "LiteLLM pull timed out after %ss; falling back to the local "
                "image %s. Set WILDCLAW_SKIP_LITELLM_PULL=1 to skip the pull.",
                timeout, image,
            )
            return
        raise RuntimeError(
            f"LiteLLM pull of {image} timed out after {timeout}s and no local "
            f"copy exists. Pre-pull it or set WILDCLAW_LITELLM_PULL_TIMEOUT higher."
        )
    if r.returncode != 0:
        if _image_present_locally(image):
            logger.warning(
                "LiteLLM pull failed (%s); falling back to the local image %s.",
                (r.stderr or "").strip(), image,
            )
            return
        raise RuntimeError(
            f"Failed to pull LiteLLM image {image}: {(r.stderr or '').strip()}"
        )
    logger.info("LiteLLM image %s ready", image)


def create_network(name: str, internal: bool = True) -> None:
    # internal=True creates an --internal bridge with no NAT to the host's
    # default route, so containers attached to ONLY this network cannot
    # reach the public internet. Agent containers MUST attach to an
    # internal-only bridge to keep them sandboxed. The LiteLLM sidecar
    # needs Bedrock/OpenAI access, so it's dual-homed (this internal
    # bridge + the default bridge) via connect_default_bridge() below.
    r = subprocess.run(
        ["docker", "network", "inspect", name],
        capture_output=True,
    )
    if r.returncode == 0:
        return
    cmd = ["docker", "network", "create"]
    if internal:
        cmd.append("--internal")
    cmd.append(name)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"Failed to create network {name}: {r.stderr}")
    logger.info("Network %s created (internal=%s)", name, internal)


def connect_default_bridge(container_name: str) -> None:
    # Attach a second NIC on the default bridge so this container can reach
    # the public internet (needed for the LiteLLM sidecar to talk to
    # Bedrock/OpenAI). Idempotent: ignores the 'already exists' error.
    r = subprocess.run(
        ["docker", "network", "connect", "bridge", container_name],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0 and "already exists" not in (r.stderr or ""):
        raise RuntimeError(
            f"Failed to attach {container_name} to default bridge: {r.stderr}"
        )


def remove_network(name: str) -> None:
    subprocess.run(["docker", "network", "rm", name], capture_output=True)


def start_litellm(
    container_name: str,
    network: str,
    host_config_path: str,
    master_key: str,
    aws_bearer_token: str = "",
    aws_region: str = "ap-south-1",
    openai_api_key: str = "",
    port: int = LITELLM_INTERNAL_PORT,
    usage_callback_host_path: str = "",
    usage_log_host_dir: str = "",
    headroom_callback_host_path: str = "",
    headroom_log_host_dir: str = "",
    enable_headroom: bool = False,
    openai_whisper_api_key: str = "",
) -> None:
    env_args: list[str] = ["-e", f"LITELLM_MASTER_KEY={master_key}"]
    _litellm_log = os.environ.get("LITELLM_LOG", "").strip()
    if _litellm_log:
        env_args += ["-e", f"LITELLM_LOG={_litellm_log}"]
    if aws_bearer_token:
        env_args += [
            "-e", f"AWS_BEARER_TOKEN_BEDROCK={aws_bearer_token}",
            "-e", f"AWS_REGION={aws_region}",
        ]
    if openai_api_key:
        env_args += ["-e", f"OPENAI_API_KEY={openai_api_key}"]
    # Transcription key: dedicated whisper key if provided, else reuse the main
    # key. The whisper/audio model blocks read os.environ/OPENAI_WHISPER_API_KEY.
    _whisper_key = openai_whisper_api_key or openai_api_key
    if _whisper_key:
        env_args += ["-e", f"OPENAI_WHISPER_API_KEY={_whisper_key}"]

    # Mount the callback module + writable log dir so UsageWriter can write
    # real provider-side usage rows from inside the sidecar. The env var name
    # is also read by litellm_usage_callback.py:_write_row.
    callback_args: list[str] = []
    if usage_callback_host_path and usage_log_host_dir:
        callback_args = [
            "-v", f"{usage_callback_host_path}:/app/litellm_usage_callback.py:ro",
            "-v", f"{usage_log_host_dir}:/var/litellm_usage",
            "-e", "LITELLM_USAGE_LOG_PATH=/var/litellm_usage/usage.jsonl",
        ]

    # Headroom pre-call compressor: writes to a SEPARATE JSONL sink
    # (/var/litellm_headroom/headroom.jsonl). Must never collide with
    # LITELLM_USAGE_LOG_PATH — token-tracking invariant (user m0130).
    # LITELLM_HEADROOM_IMAGE has `headroom-ai` baked in; the stock image
    # cannot `import headroom` at proxy startup (no egress at that point).
    headroom_args: list[str] = []
    image_to_run = LITELLM_IMAGE
    if enable_headroom and headroom_callback_host_path and headroom_log_host_dir:
        image_to_run = LITELLM_HEADROOM_IMAGE
        headroom_args = [
            "-v", f"{headroom_callback_host_path}:/app/litellm_headroom_callback.py:ro",
            "-v", f"{headroom_log_host_dir}:/var/litellm_headroom",
            "-e", "KENSEI_AGENT_HEADROOM_LOG_PATH=/var/litellm_headroom/headroom.jsonl",
            "-e", f"KENSEI_AGENT_HEADROOM_ENABLED={os.environ.get('KENSEI_AGENT_HEADROOM_ENABLED', 'true')}",
        ]
        for _k in ("KENSEI_AGENT_HEADROOM_TARGET_RATIO",
                   "KENSEI_AGENT_HEADROOM_PROTECT_RECENT",
                   "KENSEI_AGENT_HEADROOM_MIN_TOKENS"):
            _v = os.environ.get(_k)
            if _v:
                headroom_args += ["-e", f"{_k}={_v}"]

    cmd = [
        "docker", "run", "-d",
        "--name", container_name,
        "--network", network,
        *env_args,
        *callback_args,
        *headroom_args,
        "-v", f"{host_config_path}:/app/config.yaml:ro",
        image_to_run,
        "--config", "/app/config.yaml",
        "--port", str(port),
    ]
    logger.info("[%s] Starting LiteLLM sidecar on network %s", container_name, network)
    subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"LiteLLM container start failed:\n{r.stderr}")
    connect_default_bridge(container_name)
    logger.info("[%s] LiteLLM sidecar dual-homed (internal + default bridge)", container_name)


def wait_for_litellm_healthy(container_name: str, port: int = LITELLM_INTERNAL_PORT,
                             timeout: float | None = None) -> bool:
    # `KENSEI_LITELLM_HEALTH_TIMEOUT` env override exists so slower hosts
    # (cold Docker pulls, qemu-emulated arches) can extend the budget
    # without code edits. Default raised from 60s to 120s after the
    # openclaw.log 2026-06-02 incident where the sidecar booted fine but
    # the agent's first call still produced a bare "Connection error." at
    # the 4-retry/22s mark — the proxy was up, upstream Bedrock was the
    # actual problem (see verify_litellm_upstream_reachable below).
    if timeout is None:
        try:
            timeout = float(os.environ.get("KENSEI_LITELLM_HEALTH_TIMEOUT", "120"))
        except ValueError:
            timeout = 120.0
    probe = (
        "import sys, urllib.request; "
        "urllib.request.urlopen("
        f"'http://localhost:{port}/health/liveliness', timeout=2"
        ")"
    )
    deadline = time.time() + timeout
    interval = 2.0
    while time.time() < deadline:
        r = subprocess.run(
            ["docker", "exec", container_name, "python3", "-c", probe],
            capture_output=True,
        )
        if r.returncode == 0:
            logger.info("[%s] LiteLLM healthy", container_name)
            return True
        time.sleep(interval)
    logger.warning(
        "[%s] LiteLLM did not become healthy within %.0fs", container_name, timeout
    )
    return False


def verify_litellm_upstream_reachable(
    container_name: str,
    master_key: str,
    model_name: str,
    port: int = LITELLM_INTERNAL_PORT,
    timeout: float = 30.0,
) -> tuple[bool, str]:
    # Synthetic 1-token round-trip via the proxy's /v1/chat/completions to
    # confirm that the upstream provider (Bedrock/OpenAI) is actually
    # reachable from inside the sidecar — not just that the proxy's own
    # liveliness endpoint answers. This catches the "Connection error." +
    # "LLM request timed out." failure mode seen in openclaw.log on
    # 2026-06-02T10:36:42: 4 retries within 22s, all failing before any
    # token streamed, fallbackConfigured=false. wait_for_litellm_healthy
    # returned True for that batch because /health/liveliness was up; the
    # real problem was Bedrock egress. Surfacing it here as a precise
    # batch-startup RuntimeError beats a misattributed agent timeout.
    body_bytes = json.dumps({
        "model": model_name,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
        "stream": False,
    }).encode()
    # Run the probe INSIDE the sidecar so we use the same network namespace
    # and hostname resolution path that openclaw will use when it calls the
    # proxy. Catches DNS/routing failures specific to the internal bridge.
    probe = (
        "import sys, urllib.request, urllib.error\n"
        f"req = urllib.request.Request('http://localhost:{port}/v1/chat/completions', "
        f"data={body_bytes!r}, "
        f"headers={{'Authorization': 'Bearer {master_key}', "
        "'Content-Type': 'application/json'}, method='POST')\n"
        "try:\n"
        f"    r = urllib.request.urlopen(req, timeout={int(timeout)})\n"
        "    sys.stdout.write('OK status=' + str(r.status))\n"
        "except urllib.error.HTTPError as e:\n"
        "    detail = e.read().decode('utf-8', errors='ignore')[:400]\n"
        "    sys.stdout.write('HTTP ' + str(e.code) + ': ' + detail)\n"
        "    sys.exit(1)\n"
        "except Exception as e:\n"
        "    sys.stdout.write('ERR: ' + repr(e))\n"
        "    sys.exit(2)\n"
    )
    r = subprocess.run(
        ["docker", "exec", container_name, "python3", "-c", probe],
        capture_output=True,
        text=True,
        timeout=timeout + 10.0,
    )
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    if r.returncode == 0:
        logger.info("[%s] LiteLLM upstream reachable (%s)", container_name, out)
        return True, out
    logger.warning(
        "[%s] LiteLLM upstream UNREACHABLE rc=%s out=%s",
        container_name, r.returncode, out,
    )
    return False, out


def stop_litellm(container_name: str) -> None:
    subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)
