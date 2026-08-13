"""Report per-trajectory model spend to the Odoo finance API.

Implements ``POST <base>/ethara_project/trajectory_usage/create``: one record
per completed trajectory, carrying the primary model's token/cost figures plus
a ``judge_lines`` entry for each evaluator model that scored it.

Two properties matter more than completeness here:

* **On by default, switchable.** ``config.py`` ships a staging base URL, so
  reporting is live unless ``WCB_FINANCE_API_URL`` is set empty, which turns it
  off everywhere.
* **Fail-open.** Billing telemetry must never sink an expensive trajectory that
  has already been generated and judged. Every public function in this module
  returns a result object instead of raising, and each payload is mirrored to
  disk alongside its delivery outcome, so a rejected or unreachable endpoint
  can be replayed later without re-running the batch.

Token figures come from the harness ``usage.json``, which guarantees
``total_tokens == input + output + cache_read + cache_write`` and reports
``input_tokens`` net of cached input. The ``sources.agent`` sub-tree is used
rather than the top-level aggregate: the aggregate folds in batch-wide
preflight cost that is replicated across every task, which would over-bill.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import httpx

from src.utils.oauth_pricing import estimate_cost_usd

logger = logging.getLogger(__name__)

CREATE_PATH = "ethara_project/trajectory_usage/create"
DEFAULT_API_PATH = "api/v1"

BUDGET_RFP = "RFP"
BUDGET_PRODUCTION = "Production"
BUDGET_TYPES = (BUDGET_RFP, BUDGET_PRODUCTION)

RFP_SUB_TESTING = "Testing"
RFP_SUB_SAMPLING = "Sampling"
RFP_SUB_TYPES = (RFP_SUB_TESTING, RFP_SUB_SAMPLING)

MODE_SINGLEPHASE = "Singlephase"
MODE_MULTIPHASE = "Multiphase"
PRODUCTION_MODES = (MODE_SINGLEPHASE, MODE_MULTIPHASE)

DEFAULT_TIMEOUT_S = 15.0

# Judge members whose own model id did not survive into usage.json still need a
# non-empty model_name for the finance record to be attributable.
_UNKNOWN_JUDGE_MODEL = "unknown-judge"


def _normalise_base_url(base_url: str) -> str:
    """Repair the two base-URL shapes people actually type.

    A bare host ("odoo.example.com") yields a schemeless endpoint that httpx
    rejects outright, and since posting is fail-open that rejection would be
    swallowed once per trajectory with nothing ever reaching Odoo. So add the
    scheme, and when no path was supplied fall back to the documented /api/v1
    mount. A base URL that already carries a path is left untouched.
    """
    url = base_url.strip().rstrip("/")
    if not url:
        return ""
    if "://" not in url:
        url = f"https://{url}"
    scheme, _, remainder = url.partition("://")
    host, slash, path = remainder.partition("/")
    if not (slash and path):
        return f"{scheme}://{host}/{DEFAULT_API_PATH}"
    return url


@dataclass(frozen=True)
class FinanceSettings:
    """Resolved finance-reporting configuration for one batch."""

    base_url: str = ""
    api_token: str = ""
    timeout_s: float = DEFAULT_TIMEOUT_S
    project_id: str = ""
    project_type: str = ""
    team_type: str = ""
    budget_type: str = ""
    rfp_sub_type: str = ""
    production_mode: str = ""
    subscription_id: str = ""
    sink_path: Optional[Path] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "base_url", _normalise_base_url(self.base_url))

    @property
    def enabled(self) -> bool:
        return bool(self.base_url)

    @property
    def is_phase_based(self) -> bool:
        return self.production_mode == MODE_MULTIPHASE

    @property
    def endpoint(self) -> str:
        return f"{self.base_url.rstrip('/')}/{CREATE_PATH}"

    def normalised(self) -> "FinanceSettings":
        """Apply the budget-type exclusivity rules from the API contract.

        RFP records carry no production mode; Production records carry no RFP
        sub-type. Enforcing this here keeps every emitted payload inside one of
        the four documented variations even when config and CLI disagree.
        """
        budget = self.budget_type.strip()
        rfp_sub = self.rfp_sub_type.strip()
        mode = self.production_mode.strip()

        if budget == BUDGET_RFP:
            mode = ""
        elif budget == BUDGET_PRODUCTION:
            rfp_sub = ""

        return replace(
            self,
            budget_type=budget,
            rfp_sub_type=rfp_sub,
            production_mode=mode,
        )


@dataclass(frozen=True)
class FinancePostResult:
    ok: bool
    status_code: Optional[int] = None
    detail: str = ""
    skipped: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "status_code": self.status_code,
            "detail": self.detail,
            "skipped": self.skipped,
        }


def resolve_settings(
    config: Any,
    args: Any = None,
    *,
    subscription_id: str = "",
    sink_path: Optional[Path] = None,
) -> FinanceSettings:
    """Merge env-backed ``Config`` values with CLI overrides.

    CLI wins when supplied, matching how every other env-paired flag in this
    repo resolves. ``subscription_id`` is the Claude account uuid discovered at
    batch start; an explicitly configured id takes precedence over it.
    """

    def pick(attr: str, cli_attr: Optional[str] = None) -> str:
        cli_value = getattr(args, cli_attr or f"finance_{attr}", None) if args else None
        if cli_value:
            return str(cli_value).strip()
        return str(getattr(config, f"finance_{attr}", "") or "").strip()

    configured_sub = pick("subscription_id")

    settings = FinanceSettings(
        base_url=pick("api_url"),
        api_token=str(getattr(config, "finance_api_token", "") or "").strip(),
        timeout_s=float(getattr(config, "finance_api_timeout", DEFAULT_TIMEOUT_S) or DEFAULT_TIMEOUT_S),
        project_id=pick("project_id"),
        project_type=pick("project_type"),
        team_type=pick("team_type"),
        budget_type=pick("budget_type"),
        rfp_sub_type=pick("rfp_sub_type"),
        production_mode=pick("production_mode"),
        subscription_id=configured_sub or subscription_id.strip(),
        sink_path=sink_path,
    )
    return settings.normalised()


def _as_number(value: Any) -> float:
    """Coerce a usage figure to a number. The API accepts decimals."""
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return 0.0


def _agent_usage(usage: Mapping[str, Any]) -> Mapping[str, Any]:
    sources = usage.get("sources")
    if isinstance(sources, Mapping):
        agent = sources.get("agent")
        if isinstance(agent, Mapping):
            return agent
    return usage


def build_judge_lines(usage: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Turn the judge council's per-member usage into finance judge lines.

    ``usage.json -> sources.judge.per_member`` is keyed by stable model family
    (``sonnet`` / ``glm`` / ``kimi``) so it survives model rotation. Cache
    tokens map by direction of data flow: tokens *read* from the prompt cache
    are input-side, tokens *written* to it are the request's cache output.
    When a council ran without per-member detail, a single aggregated line is
    emitted so the cost is still reported rather than silently dropped.
    """
    sources = usage.get("sources")
    judge = sources.get("judge") if isinstance(sources, Mapping) else None
    if not isinstance(judge, Mapping):
        return []

    per_member = judge.get("per_member")
    if isinstance(per_member, Mapping) and per_member:
        return [
            _judge_line(member, fallback_name=family)
            for family, member in sorted(per_member.items())
            if isinstance(member, Mapping)
        ]

    if _as_number(judge.get("total_tokens")) or _as_number(judge.get("cost_usd")):
        return [_judge_line(judge)]
    return []


def _readable_model(raw: Any, fallback: str = "") -> str:
    """Prefer a human-readable model name over a Bedrock inference-profile ARN.

    Council members record their *effective* model, which on Bedrock is a full
    ARN embedding the AWS account id. Sending that to an external finance system
    both leaks the account id and is useless for billing, so the stable family
    key (``sonnet`` / ``glm`` / ``kimi``) wins whenever an ARN is present.
    """
    name = str(raw or "").strip()
    if name and "arn:aws:" not in name:
        return name
    return fallback.strip() or _UNKNOWN_JUDGE_MODEL


def _judge_cost_usd(
    model_name: str,
    recorded: float,
    *,
    input_tokens: float,
    output_tokens: float,
    cache_read_tokens: float,
    cache_write_tokens: float,
) -> float:
    """Recorded judge cost, or a Bedrock-rate estimate when it came back zero.

    A council member graded over the Claude Max subscription is prepaid, so
    ``grading.py::_judge_cost_usd`` deliberately records $0 for it. A Bedrock
    member always carries a real non-zero cost, so a truthy ``recorded`` short
    circuits here and that path is never repriced.
    """
    if recorded:
        return recorded

    estimated, priced_ok = estimate_cost_usd(
        model_name,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
    )
    if not priced_ok or not estimated:
        return recorded

    logger.info(
        "[finance] judge %s reported $0 (prepaid); repriced at Bedrock rates to $%s",
        model_name,
        estimated,
    )
    return estimated


def _judge_line(member: Mapping[str, Any], *, fallback_name: str = "") -> dict[str, Any]:
    model_name = _readable_model(member.get("model"), fallback_name)
    input_tokens = _as_number(member.get("input_tokens"))
    output_tokens = _as_number(member.get("output_tokens"))
    cache_read_tokens = _as_number(member.get("cache_read_tokens"))
    cache_write_tokens = _as_number(member.get("cache_write_tokens"))
    return {
        "model_name": model_name,
        "judge_input_tokens": input_tokens,
        "judge_output_tokens": output_tokens,
        "judge_input_cache_tokens": cache_read_tokens,
        "judge_output_cache_tokens": cache_write_tokens,
        "judge_cost_usd": _judge_cost_usd(
            model_name,
            _as_number(member.get("cost_usd")),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
        ),
    }


def _trajectory_cost_usd(
    agent: Mapping[str, Any], model_name: str, oauth_route: bool
) -> float:
    """Recorded cost, or a Bedrock-rate estimate when the run used OAuth.

    A Claude Max subscription is prepaid, so LiteLLM records ~$0 marginal cost
    and the finance record would understate the trajectory by orders of
    magnitude. On that route only, token counts are repriced at Bedrock list
    rates. ``oauth_route`` is False for every Bedrock run, so this function
    returns the recorded cost untouched there.
    """
    recorded = _as_number(agent.get("cost_usd"))
    if not oauth_route:
        return recorded

    estimated, priced_ok = estimate_cost_usd(
        model_name,
        input_tokens=_as_number(agent.get("input_tokens")),
        output_tokens=_as_number(agent.get("output_tokens")),
        cache_read_tokens=_as_number(agent.get("cache_read_tokens")),
        cache_write_tokens=_as_number(agent.get("cache_write_tokens")),
    )
    if not priced_ok:
        logger.warning(
            "[finance] OAuth route but no Bedrock rate card for model %r; "
            "reporting recorded cost $%s unchanged",
            model_name,
            recorded,
        )
        return recorded

    logger.info(
        "[finance] OAuth route: repriced %s at Bedrock rates, $%s estimated (recorded $%s)",
        model_name,
        estimated,
        recorded,
    )
    return estimated


def build_trajectory_usage_payload(
    settings: FinanceSettings,
    *,
    task_id: str,
    trajectory_id: str,
    model_name: str,
    usage: Optional[Mapping[str, Any]] = None,
    generated_at: Optional[str] = None,
    judge_lines: Optional[Sequence[Mapping[str, Any]]] = None,
    oauth_route: bool = False,
) -> dict[str, Any]:
    usage = usage or {}
    agent = _agent_usage(usage)
    lines = judge_lines if judge_lines is not None else build_judge_lines(usage)

    return {
        "project_id": settings.project_id,
        "project_type": settings.project_type,
        "task_id": task_id,
        "trajectory_id": trajectory_id,
        "team_type": settings.team_type,
        "budget_type": settings.budget_type,
        "rfp_sub_type": settings.rfp_sub_type,
        "production_mode": settings.production_mode,
        "is_phase_based": settings.is_phase_based,
        "generated_at": generated_at or _now_iso8601(),
        "model_name": model_name,
        "trajectory_input_tokens": _as_number(agent.get("input_tokens")),
        "trajectory_output_tokens": _as_number(agent.get("output_tokens")),
        "trajectory_input_cache_tokens": _as_number(agent.get("cache_read_tokens")),
        "trajectory_output_cache_tokens": _as_number(agent.get("cache_write_tokens")),
        "trajectory_cost_usd": _trajectory_cost_usd(agent, model_name, oauth_route),
        "subscription_id": settings.subscription_id,
        "judge_lines": [dict(line) for line in lines],
    }


def _now_iso8601() -> str:
    """Local time with a UTC offset, as the API requires."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def post_trajectory_usage(
    settings: FinanceSettings,
    payload: Mapping[str, Any],
) -> FinancePostResult:
    """POST one usage record. Never raises."""
    if not settings.enabled:
        return FinancePostResult(ok=False, skipped=True, detail="finance API not configured")

    headers = {"Content-Type": "application/json"}
    if settings.api_token:
        headers["Authorization"] = f"Bearer {settings.api_token}"

    logger.info(
        "[finance] POST %s (auth=%s, timeout=%ss, judge_lines=%d)",
        settings.endpoint,
        "bearer" if settings.api_token else "none",
        settings.timeout_s,
        len(payload.get("judge_lines") or []),
    )
    logger.debug("[finance] request body: %s", json.dumps(dict(payload), sort_keys=True))

    started = time.monotonic()
    try:
        with httpx.Client(timeout=settings.timeout_s) as client:
            resp = client.post(settings.endpoint, json=dict(payload), headers=headers)
    except Exception as exc:
        logger.warning(
            "[finance] POST transport error after %.2fs: %s: %s",
            time.monotonic() - started,
            type(exc).__name__,
            exc,
        )
        return FinancePostResult(ok=False, detail=f"{type(exc).__name__}: {exc}")

    elapsed = time.monotonic() - started
    status = getattr(resp, "status_code", None)

    body = ""
    try:
        body = str(getattr(resp, "text", ""))[:300]
    except Exception:
        pass

    if isinstance(status, int) and 200 <= status < 300:
        logger.info("[finance] POST -> HTTP %s in %.2fs", status, elapsed)
        logger.debug("[finance] response body: %s", body)
        return FinancePostResult(ok=True, status_code=status)

    logger.warning("[finance] POST -> HTTP %s in %.2fs: %s", status, elapsed, body)
    return FinancePostResult(ok=False, status_code=status, detail=body)


def append_to_sink(sink_path: Path, record: Mapping[str, Any]) -> None:
    """Append one JSON line to the replay sink. Never raises."""
    try:
        sink_path.parent.mkdir(parents=True, exist_ok=True)
        with sink_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")
    except Exception as exc:
        logger.warning("[finance] could not write sink %s: %s", sink_path, exc)


def record_trajectory_usage(
    settings: FinanceSettings,
    *,
    task_id: str,
    trajectory_id: str,
    model_name: str,
    usage: Optional[Mapping[str, Any]] = None,
    output_dir: Optional[Path] = None,
    generated_at: Optional[str] = None,
    oauth_route: bool = False,
) -> FinancePostResult:
    """Build, POST, and persist one trajectory usage record. Never raises.

    The payload is written to disk together with the delivery outcome, so a
    rejected POST still leaves a replayable artifact next to the trajectory.
    """
    if not settings.enabled:
        logger.debug("[finance] skip %s: reporting not configured", trajectory_id)
        return FinancePostResult(ok=False, skipped=True, detail="finance API not configured")

    logger.info(
        "[finance] step 1/4 build payload: task=%s trajectory=%s model=%s",
        task_id,
        trajectory_id,
        model_name,
    )
    try:
        payload = build_trajectory_usage_payload(
            settings,
            task_id=task_id,
            trajectory_id=trajectory_id,
            model_name=model_name,
            usage=usage,
            generated_at=generated_at,
            oauth_route=oauth_route,
        )
    except Exception as exc:
        logger.warning("[finance] could not build payload for %s: %s", trajectory_id, exc)
        return FinancePostResult(ok=False, detail=f"payload build failed: {exc}")

    logger.info(
        "[finance] payload built: budget=%s%s tokens(in/out/cr/cw)=%s/%s/%s/%s cost=$%s judges=%d",
        payload.get("budget_type") or "unset",
        f"/{payload.get('rfp_sub_type')}" if payload.get("rfp_sub_type") else
        (f"/{payload.get('production_mode')}" if payload.get("production_mode") else ""),
        payload.get("trajectory_input_tokens"),
        payload.get("trajectory_output_tokens"),
        payload.get("trajectory_input_cache_tokens"),
        payload.get("trajectory_output_cache_tokens"),
        payload.get("trajectory_cost_usd"),
        len(payload.get("judge_lines") or []),
    )

    logger.info("[finance] step 2/4 send")
    result = post_trajectory_usage(settings, payload)
    record = {"payload": payload, "post": result.to_dict(), "endpoint": settings.endpoint}

    logger.info("[finance] step 3/4 persist artifact")
    if output_dir is not None:
        artifact = output_dir / "finance_usage.json"
        try:
            artifact.write_text(
                json.dumps(record, indent=2, sort_keys=True), encoding="utf-8"
            )
            logger.info("[finance] wrote %s", artifact)
        except Exception as exc:
            logger.warning("[finance] could not write finance_usage.json: %s", exc)

    logger.info("[finance] step 4/4 append replay sink")
    if settings.sink_path is not None:
        append_to_sink(settings.sink_path, record)
        logger.info("[finance] appended to %s", settings.sink_path)

    if result.ok:
        logger.info("[finance] reported %s (HTTP %s)", trajectory_id, result.status_code)
    else:
        logger.warning(
            "[finance] report failed for %s (HTTP %s): %s -- payload retained for replay",
            trajectory_id,
            result.status_code,
            result.detail,
        )
    return result
