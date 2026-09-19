from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    from dotenv import dotenv_values
except ImportError:
    def dotenv_values(path):  # type: ignore[misc]
        out: dict = {}
        if not os.path.isfile(path):
            return out
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
        return out


# src/utils/config.py -> parents[2] == repo root (Wildclow_forked_image)
ROOT_DIR = Path(__file__).resolve().parents[2]
ENVIRONMENT_DIR = ROOT_DIR / "environment"

DEFAULT_FINANCE_API_URL = "https://projects-stage.ethara.ai/api/v1"
DEFAULT_FINANCE_PROJECT_ID = "KEN-123"
DEFAULT_FINANCE_PROJECT_TYPE = "Technical"
DEFAULT_FINANCE_TEAM_TYPE = "Projects"
DEFAULT_FINANCE_BUDGET_TYPE = "Production"
DEFAULT_FINANCE_PRODUCTION_MODE = "Singlephase"


@dataclass
class Config:
    # ---- AWS Bedrock (Claude via bearer token) ----
    bedrock_inference_arn: str = ""
    bedrock_sonnet_arn: str = ""
    bedrock_region: str = "ap-south-1"
    aws_bearer_token: str = ""
    # Region for the OpenAI-family Bedrock route (bedrock_mantle / gpt-5.6-sol).
    # DELIBERATELY SEPARATE from bedrock_region: `bedrock_region` is the Anthropic
    # (Converse/Invoke) region for the opus + sonnet inference profiles and is
    # ap-south-1 here, but per the AWS model card the BARE `openai.gpt-5.6-sol` id
    # is only served in-region in us-east-1 / us-east-2 -- ap-south-1 offers it via
    # Global CRIS only, and the CRIS id (`global.openai...`) silently degrades off
    # the /openai/v1/responses base path. So this route needs its own us-* region.
    # Same bearer token (aws_bearer_token -> AWS_BEARER_TOKEN_BEDROCK); no new creds.
    gpt56_bedrock_region: str = "us-east-2"

    # ---- S3 (trajectory media upload) ----
    s3_bucket: str = ""
    s3_prefix: str = "WildClaw"
    s3_region: str = "us-east-1"
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""

    # ---- OpenAI (direct / via-LiteLLM) ----
    openai_api_key: str = ""
    # Optional dedicated key for Whisper / audio transcription; falls back to
    # openai_api_key at the call site when empty.
    openai_whisper_api_key: str = ""

    # ---- GPT rubric judge (Channel B, opt-in) ----
    # Dedicated OpenAI key + model id for using GPT (e.g. gpt-5.6) as a rubric
    # judge, independent of openai_api_key (which is the trajectory/agent key).
    # Consumed LIVE by grading.py (_judge_gpt_api_key / _judge_gpt_model): when
    # both are set (and JUDGE_GPT_PRIMARY is not off) a single GPT judge grades
    # ahead of the council, with the council as the no-signal fallback. Leaving
    # both empty is a no-op and the council remains the sole Channel-B path.
    judge_gpt_api_key: str = ""
    judge_gpt_model: str = ""

    # ---- GPT rubric judge via ChatGPT/Codex subscription (Channel B, opt-in) ----
    # Alternative to the metered judge_gpt_api_key: route the GPT judge through
    # the codex OAuth bridge (the same ChatGPT subscription used for the codex
    # trajectory backend) instead of a metered key. run_batch.py publishes the
    # bridge on a host loopback port and sets KENSEI_JUDGE_CODEX_BRIDGE_URL when
    # --use-codex-oauth is active; the judge then bills $0 (flat subscription).
    # The model defaults to gpt-5.6-sol; the evidence cap is the tunable safety
    # valve for the undocumented subscription context window. All read LIVE from
    # the environment by grading.py, mirroring the Sonnet-OAuth judge route.
    judge_codex_bridge_url: str = ""
    judge_codex_bridge_model: str = ""
    judge_codex_max_evidence: int = 500_000

    # ---- Anthropic direct (alternative upstream for opus when Bedrock unavailable) ----
    # Used by litellm_sidecar.py to emit an `anthropic/claude-opus-4-20250514`
    # model entry for the `claude-opus-4.7` / `claude-opus-4-6` aliases when
    # no Bedrock bearer token is available. This keeps the harness usable on
    # machines where the Bedrock IAM access has been rotated/revoked.
    anthropic_api_key: str = ""

    # ---- First-party vendor (OpenAI-compatible relay, routed via LiteLLM) ----
    # An internal OpenAI-compatible relay (base URL supplied via config). The
    # vendor onboarding guide is explicit: keep ALL inference params at their
    # defaults (never override reasoning_effort/temperature/top_p/top_k), so the
    # sidecar model block for this provider carries only routing fields. The
    # harness-facing model id equals `meta_model`, so `--model <meta_model>`
    # routes here. Registered only when both key and model id are present.
    meta_api_key: str = ""
    meta_base_url: str = "https://api.ai.meta.com/v1"
    meta_model: str = ""

    # ---- OpenRouter (fallback LLM routing) ----
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # ---- Brave Search (passed to container; placeholder prevents gateway crash) ----
    brave_api_key: str = "placeholder"

    # ---- Container image ----
    # Default is the distributed v1.3 image (HarnessV2). v1.4 = v1.3 +
    # openai-whisper, opt-in via DOCKER_IMAGE; built by preflight_agent_image()
    # in script/run.sh. Keep in lockstep with AGENT_IMAGE_DEFAULT there.
    docker_image: str = "wildclawbench-ubuntu:v1.3"

    # ---- LiteLLM proxy (shared sidecar container) ----
    litellm_master_key: str = "sk-talos-litellm"
    litellm_port: int = 4000

    # ---- Claude Code OAuth trajectory path (opt-in) ----
    # When enabled, opus traffic routes through a sibling `wcbsh-cc-bridge-*`
    # sidecar that forwards to https://api.anthropic.com under a Claude Code
    # OAuth subscription instead of Bedrock. Gated by --use-claude-oauth (or
    # WCB_USE_CLAUDE_OAUTH=1). Bedrock path remains the default; the OAuth
    # path is an approved deviation from litellm_sidecar.py:276 (see
    # src/utils/AGENTS.md) for the opus trajectory route only.
    use_claude_oauth: bool = False
    # Colon-separated list of OAuth credential JSON files (kaiju-format:
    # {"claudeAiOauth": {"accessToken", "refreshToken", "expiresAt", ...}}).
    # Host paths are mounted read-write into the bridge container at
    # /oauth_pool/ so fcntl-protected refresh_token rotation can persist.
    cc_account_pool: str = ""
    # Shared secret required on the `x-wcb-bridge-secret` header of every
    # bridge request. LiteLLM injects it via extra_headers. Prevents
    # co-tenant containers on wcbsh-net-<suffix> from draining the
    # subscription. Auto-generated by bootstrap_sidecar.py if empty.
    cc_bridge_secret: str = ""
    # Value LiteLLM sends as api_key on the OAuth block. The bridge ignores
    # it (Authorization: Bearer <oauth-token> is stamped bridge-side).
    cc_stub_key: str = "sk-wcb-oauth-stub"

    # ---- ChatGPT/Codex OAuth trajectory path (opt-in) ----
    # Sibling of use_claude_oauth for the GPT side: when enabled, the gpt-5.6
    # trajectory routes through a sibling `wcbsh-codex-bridge-*` sidecar that
    # forwards to the ChatGPT/Codex backend under a Codex OAuth subscription
    # instead of a metered OpenAI API key. Gated by --use-codex-oauth (or
    # WCB_USE_CODEX_OAUTH=1).
    use_codex_oauth: bool = False
    # Sidecar model id exposed on that route. `gpt-5.6-sol` because the Codex
    # backend rejects the bare `gpt-5.6`; the family it serves is the
    # -luna/-sol/-terra variants. Override via WCB_CODEX_MODEL and pass the
    # same id to --model.
    codex_model: str = "gpt-5.6-sol"

    # ---- Odoo finance API (per-trajectory usage reporting) ----
    # Base URL of the Odoo instance exposing
    # POST <base>/ethara_project/trajectory_usage/create. This ships pointing at
    # staging, so reporting is ON by default; set WCB_FINANCE_API_URL="" to turn
    # it off. The remaining fields are the project/budget taxonomy the finance
    # side keys records by; each has a matching --finance-* CLI override, and
    # CLI wins (resolution lives in src/utils/finance_api.py::resolve_settings).
    finance_api_url: str = DEFAULT_FINANCE_API_URL
    finance_api_token: str = ""
    finance_api_timeout: float = 15.0
    finance_project_id: str = DEFAULT_FINANCE_PROJECT_ID
    finance_project_type: str = DEFAULT_FINANCE_PROJECT_TYPE
    finance_team_type: str = DEFAULT_FINANCE_TEAM_TYPE
    # "RFP" or "Production". rfp_sub_type ("Testing"/"Sampling") applies only to
    # the former, production_mode ("Singlephase"/"Multiphase") only to the
    # latter; finance_api normalises whichever pair does not apply back to "".
    finance_budget_type: str = DEFAULT_FINANCE_BUDGET_TYPE
    finance_rfp_sub_type: str = ""
    finance_production_mode: str = DEFAULT_FINANCE_PRODUCTION_MODE
    # Defaults to the Claude account uuid resolved at batch start; set this
    # only when finance needs a billing id that is not the Claude account.
    finance_subscription_id: str = ""

    # ---- Sandbox runtime ----
    tmp_workspace: str = "/tmp_workspace"
    gateway_port: int = 18789

    # ---- Harbor quality gate ----
    min_harbor_score: float | None = None

    # ---- File paths ----
    environment_dir: Path = field(default_factory=lambda: ENVIRONMENT_DIR)
    state_db: Path = field(default_factory=lambda: ROOT_DIR / "state.db")
    work_dir: Path = field(default_factory=lambda: ROOT_DIR / "work")
    output_dir: Path = field(default_factory=lambda: ROOT_DIR / "output")

    # ---- WildClawBench skills ----
    wildclaw_skills_dir: Path | None = None
    default_skills: list = field(default_factory=list)

    # ---- Behaviour ----
    upload_media_to_s3: bool = False

    @classmethod
    def from_env(cls, env_file: Optional[Path] = None) -> "Config":
        env: dict = {}
        if env_file is None:
            for candidate in (ROOT_DIR / ".env", Path.cwd() / ".env"):
                if candidate.is_file():
                    env_file = candidate
                    break
        if env_file and Path(env_file).is_file():
            env.update(dotenv_values(env_file))
        env.update(os.environ)

        def s(*keys: str, default: str = "") -> str:
            for k in keys:
                v = env.get(k)
                if v not in (None, ""):
                    return str(v).strip()
            return default

        def b(key: str, default: bool) -> bool:
            v = env.get(key)
            if v is None:
                return default
            return str(v).strip().lower() in ("1", "true", "yes", "on")

        def i(key: str, default: int) -> int:
            v = env.get(key)
            if v is None:
                return default
            try:
                return int(v)
            except ValueError:
                return default

        def f(key: str, default: float | None = None) -> float | None:
            v = env.get(key)
            if v in (None, ""):
                return default
            try:
                return float(v)
            except ValueError:
                return default

        _wcsd = s("WILDCLAW_SKILLS_DIR")
        # Media-processing skills injected into every task by default (e.g.
        # video-frames = ffmpeg frame/clip extraction for multimodal inputs).
        _ds = s("WILDCLAW_DEFAULT_SKILLS", "KENSEI3_DEFAULT_SKILLS",
                default="video-frames,pdf-extract,audio-extract")
        return cls(
            bedrock_inference_arn=s("KENSEI_BEDROCK_MODEL_ARN", "KENSEI2_BEDROCK_MODEL_ARN", "BEDROCK_MODEL_ARN"),
            bedrock_sonnet_arn=s("KENSEI_BEDROCK_SONNET_ARN", "BEDROCK_SONNET_ARN"),
            bedrock_region=s("KENSEI_AWS_REGION", "AWS_REGION", default="ap-south-1"),
            gpt56_bedrock_region=s(
                "KENSEI_GPT56_BEDROCK_REGION", "KENSEI_BEDROCK_GPT_REGION", default="us-east-2"
            ),
            aws_bearer_token=s("KENSEI_AWS_BEARER_TOKEN", "AWS_BEARER_TOKEN_BEDROCK"),
            s3_bucket=s("S3_BUCKET"),
            s3_prefix=s("S3_PREFIX", default="WildClaw"),
            s3_region=s("S3_REGION", default="us-east-1"),
            s3_access_key_id=s("KENSEI_S3_ACCESS_KEY_ID", "AWS_ACCESS_KEY_ID"),
            s3_secret_access_key=s("KENSEI_S3_SECRET_ACCESS_KEY", "AWS_SECRET_ACCESS_KEY"),
            openai_api_key=s("KENSEI_OPENAI_API_KEY", "OPENAI_API_KEY"),
            openai_whisper_api_key=s("KENSEI_OPENAI_WHISPER_API_KEY", "OPENAI_WHISPER_API_KEY"),
            judge_gpt_api_key=s("KENSEI_JUDGE_GPT_API_KEY", "JUDGE_GPT_API_KEY"),
            judge_gpt_model=s("KENSEI_JUDGE_GPT_MODEL", "JUDGE_GPT_MODEL"),
            judge_codex_bridge_url=s("KENSEI_JUDGE_CODEX_BRIDGE_URL"),
            judge_codex_bridge_model=s("KENSEI_JUDGE_CODEX_BRIDGE_MODEL"),
            judge_codex_max_evidence=i("KENSEI_JUDGE_CODEX_MAX_EVIDENCE", 500_000),
            anthropic_api_key=s("KENSEI_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"),
            meta_api_key=s("KENSEI_1P_API_KEY", "ONEP_API_KEY"),
            meta_base_url=s("KENSEI_1P_BASE_URL", "ONEP_API_BASE_URL", default="https://api.ai.meta.com/v1"),
            meta_model=s("KENSEI_1P_MODEL", "ONEP_MODEL"),
            openrouter_api_key=s("OPENROUTER_API_KEY"),
            openrouter_base_url=s("OPENROUTER_BASE_URL", default="https://openrouter.ai/api/v1"),
            brave_api_key=s("BRAVE_API_KEY", default="placeholder"),
            docker_image=s("DOCKER_IMAGE", default="wildclawbench-ubuntu:v1.3"),
            tmp_workspace=s("TMP_WORKSPACE", default="/tmp_workspace"),
            gateway_port=i("GATEWAY_PORT", 18789),
            upload_media_to_s3=b("UPLOAD_MEDIA_TO_S3", False),
            # Skills live in the environment folder (the mock-API connectors).
            # Default the harness skill catalog to <root>/environment/skills.
            wildclaw_skills_dir=Path(_wcsd) if _wcsd else (ENVIRONMENT_DIR / "skills"),
            default_skills=[x.strip() for x in _ds.split(",") if x.strip()],
            litellm_master_key=s("KENSEI_LITELLM_MASTER_KEY", "KENSEI3_LITELLM_MASTER_KEY", "LITELLM_MASTER_KEY", default="sk-talos-litellm"),
            litellm_port=i("KENSEI3_LITELLM_PORT", i("LITELLM_PORT", 4000)),
            min_harbor_score=f("MIN_HARBOR_SCORE"),
            use_claude_oauth=b("WCB_USE_CLAUDE_OAUTH", False),
            cc_account_pool=s("WCB_CC_ACCOUNT_POOL"),
            cc_bridge_secret=s("WCB_CC_BRIDGE_SECRET"),
            cc_stub_key=s("WCB_CC_STUB_KEY", default="sk-wcb-oauth-stub"),
            use_codex_oauth=b("WCB_USE_CODEX_OAUTH", False),
            codex_model=s("WCB_CODEX_MODEL", default="gpt-5.6-sol"),
            finance_api_url=s("WCB_FINANCE_API_URL", default=DEFAULT_FINANCE_API_URL),
            finance_api_token=s("WCB_FINANCE_API_TOKEN"),
            finance_api_timeout=f("WCB_FINANCE_API_TIMEOUT", 15.0) or 15.0,
            finance_project_id=s("WCB_FINANCE_PROJECT_ID", default=DEFAULT_FINANCE_PROJECT_ID),
            finance_project_type=s("WCB_FINANCE_PROJECT_TYPE", default=DEFAULT_FINANCE_PROJECT_TYPE),
            finance_team_type=s("WCB_FINANCE_TEAM_TYPE", default=DEFAULT_FINANCE_TEAM_TYPE),
            finance_budget_type=s("WCB_FINANCE_BUDGET_TYPE", default=DEFAULT_FINANCE_BUDGET_TYPE),
            finance_rfp_sub_type=s("WCB_FINANCE_RFP_SUB_TYPE"),
            finance_production_mode=s(
                "WCB_FINANCE_PRODUCTION_MODE", default=DEFAULT_FINANCE_PRODUCTION_MODE
            ),
            finance_subscription_id=s("WCB_FINANCE_SUBSCRIPTION_ID"),
        )

    def litellm_enabled(self) -> bool:
        """LiteLLM/Bedrock routing is active when Bedrock (arn+bearer token)
        OR a direct OpenAI key is configured. Otherwise OpenRouter is used.

        The Claude Code OAuth path also counts as LiteLLM-enabled — the
        LiteLLM sidecar still fronts the traffic, just with a different
        upstream (bridge → api.anthropic.com under OAuth) for the opus
        model block."""
        if self.bedrock_inference_arn and self.aws_bearer_token:
            return True
        if self.openai_api_key:
            return True
        if self.anthropic_api_key:
            return True
        if self.meta_api_key and self.meta_model:
            return True
        if self.use_claude_oauth and self.cc_account_pool:
            return True
        return False

    def ensure_dirs(self) -> None:
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
