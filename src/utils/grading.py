from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

load_dotenv()
TMP_WORKSPACE = os.environ.get("TMP_WORKSPACE", "/tmp_workspace")


# ---------------------------------------------------------------------------
# LLM rubric judge (for native prompt.txt + rubric.json tasks)
#
# Native tasks have no `automated_checks` to exec, so without this they score a
# degenerate reward:0.0 / tests_total:0 no matter how well the agent did. This
# judge scores each rubric criterion 0..1 against the agent's deliverables +
# transcript, then weights them into an overall_score and per-criterion test
# counts. It calls the judge model DIRECTLY from the host (OpenAI or Bedrock) —
# the per-batch LiteLLM sidecar is not host-reachable, and the host can reach
# both providers directly (verified). Fully best-effort: any failure returns a
# structured error and never raises into the run loop.
# ---------------------------------------------------------------------------

JUDGE_MODEL = os.environ.get("JUDGE_MODEL", "openai/gpt-5.4")  # may be bedrock/<arn>; falls back to JUDGE_MODEL_FALLBACK
# Smallest-member-governs evidence cap, derived from AWS Bedrock official
# context-window numbers (2026-06-02 web-confirmed, no longer hit-and-trial):
#   * Claude Sonnet 4.6 (is9bst5tfadh) — 1,000,000 input tokens
#   * Kimi K2.5         (p532c9fzmeed) —   256,000 input tokens
#   * GLM 5             (xx5msvho23iq) —   200,000 input tokens  <-- smallest
# Bedrock enforces input_tokens + max_tokens <= context_window (see LiteLLM
# PR #22479 / issue #22478). Our maxTokens request is 4,000. Empirical chars
# /token ratio on real WildClawBench payloads is ~2.515 (500k chars produced
# 198,753 input tokens for GLM in the m1037 probe). Budget:
#   200,000 ctx  − 4,000 output  − ~5,000 scaffold (TASK + 25-rubric criteria
#   + JSON schema + system prompt) = ~191,000 tokens for evidence
#   ÷ 2.515 chars/token = ~75,944 evidence tokens worth of chars ≈ 191,000
# Converting back: 191,000 tokens × 2.515 chars/token ≈ 480,365 chars. Round
# down with safety margin for tokenizer drift and rubric-block variance:
# 450,000 chars. Operators with a single high-context judge (Sonnet's 1M
# budget) can lift this by exporting JUDGE_MAX_EVIDENCE=<chars>. Setting it
# to 0 (or anything falsy after int()) restores the unbounded behavior we
# briefly defaulted to between b31 and now — known to 400 every council
# member on real WildClawBench runs.
_DEFAULT_JUDGE_MAX_EVIDENCE = 450_000


def _resolve_judge_max_evidence() -> int | None:
    raw = os.environ.get("JUDGE_MAX_EVIDENCE")
    if raw is None or raw.strip() == "":
        return _DEFAULT_JUDGE_MAX_EVIDENCE
    try:
        n = int(raw)
    except ValueError:
        return _DEFAULT_JUDGE_MAX_EVIDENCE
    return n if n > 0 else None


_JUDGE_MAX_EVIDENCE = _resolve_judge_max_evidence()

# Per-member evidence budgets (chars). Council members have different context
# windows, so each gets a payload sized to its own ceiling instead of all three
# sharing the smallest-member cap. Numbers derived from the same arithmetic as
# _DEFAULT_JUDGE_MAX_EVIDENCE:
#   budget_chars = (ctx_window − 4,000 maxTokens − ~5,500 scaffold) × 2.515 chars/token
# Rounded down with safety margin for tokenizer drift and rubric-block variance.
# Match patterns are checked against the model identifier as-passed, so an
# operator override via JUDGE_COUNCIL_*_ARN still maps to the correct budget so
# long as the inference-profile ID stays in the string.
# AWS edge body cap (~25 MB) observed in (b40) probes B2/B3 caps every Bedrock
# request regardless of context window; we cap at 24,000,000 chars to leave
# headroom for JSON envelope + scaffold + base64 overhead.
_AWS_EDGE_BODY_CAP = 24_000_000

# Pattern → budget. First match wins. The default (None match) returns the flat
# _resolve_judge_max_evidence() value so single-judge calls behave identically
# to pre-Fix-14 and OpenAI fallback gets the same conservative cap.
_MEMBER_EVIDENCE_BUDGETS: tuple[tuple[str, int], ...] = (
    # Sonnet 4.6 — 1,000,000 ctx, generous but bounded by AWS body cap.
    ("is9bst5tfadh", 2_400_000),
    # Kimi K2.5 — 256,000 ctx.
    ("p532c9fzmeed", 600_000),
    # GLM 5 — 200,000 ctx.
    ("xx5msvho23iq", 450_000),
)


def _member_evidence_budget(model: str) -> int | None:
    """Per-member evidence char budget. Returns None to mean 'use the flat
    JUDGE_MAX_EVIDENCE default' (so single-judge calls and unknown council
    members stay on the safe conservative cap)."""
    # Operator-supplied env override wins outright over the per-member table:
    # JUDGE_MAX_EVIDENCE applies to everyone, single or council, so the
    # operator stays in control of the maximum cost any judge call can incur.
    env_raw = os.environ.get("JUDGE_MAX_EVIDENCE")
    if env_raw is not None and env_raw.strip() != "":
        return _resolve_judge_max_evidence()
    for needle, chars in _MEMBER_EVIDENCE_BUDGETS:
        if needle in model:
            # Never exceed the AWS edge body cap, even if the table grows.
            return min(chars, _AWS_EDGE_BODY_CAP)
    return _DEFAULT_JUDGE_MAX_EVIDENCE

# LLM council (opt-in). When enabled the rubric is scored by THREE judges in
# parallel and aggregated by per-criterion mean with stddev-based disagreement
# flags. Quorum policy: any 2 surviving judges produce a valid council score;
# < 2 surviving judges falls through to the single-judge code path. Member
# identifiers follow the same "<provider>/<rest>" form as JUDGE_MODEL. The
# default roster matches the team's chosen models (Sonnet 4.6 + GLM 5 + Kimi
# k2.5) and is overridable end-to-end via env:
#   JUDGE_COUNCIL=1
#   JUDGE_COUNCIL_MEMBERS=bedrock/<arn1>,bedrock/<arn2>,bedrock/<arn3>
#   JUDGE_COUNCIL_DISAGREEMENT_THRESHOLD=0.30   (stddev above which a criterion
#                                                is flagged as contested)
_DEFAULT_COUNCIL_MEMBERS = (
    # Sonnet 4.6 anchor — points to the operator-supplied inference profile in
    # ap-south-1. The prior default xv71vnlzm71s is denied by EtharaKenseiPolicy
    # at the IAM layer; is9bst5tfadh replaces it as the current working anchor.
    # Override via JUDGE_COUNCIL_SONNET_ARN if you need a different profile.
    os.environ.get(
        "JUDGE_COUNCIL_SONNET_ARN",
        "bedrock/arn:aws:bedrock:ap-south-1:426628337772:application-inference-profile/is9bst5tfadh",
    ),
    # GLM 5 — us-east-1 profile.
    os.environ.get(
        "JUDGE_COUNCIL_GLM_ARN",
        "bedrock/arn:aws:bedrock:us-east-1:426628337772:application-inference-profile/xx5msvho23iq",
    ),
    # Kimi k2.5 — ap-south-1 profile.
    os.environ.get(
        "JUDGE_COUNCIL_KIMI_ARN",
        "bedrock/arn:aws:bedrock:ap-south-1:426628337772:application-inference-profile/p532c9fzmeed",
    ),
)
_COUNCIL_DISAGREEMENT_THRESHOLD = float(
    os.environ.get("JUDGE_COUNCIL_DISAGREEMENT_THRESHOLD", "0.30") or "0.30"
)


def council_enabled() -> bool:
    return os.environ.get("JUDGE_COUNCIL", "").strip() in {"1", "true", "yes", "on"}


def council_members() -> list[str]:
    raw = os.environ.get("JUDGE_COUNCIL_MEMBERS", "").strip()
    if raw:
        return [m.strip() for m in raw.split(",") if m.strip()]
    return [m for m in _DEFAULT_COUNCIL_MEMBERS if m]


def _judge_system_prompt() -> str:
    # 2026-06-02 judge rewrite — the prompt body lives in
    # system_prompts/judge_system.md (b78 walkthrough_2026_05_27 spec, b96
    # centralization). It encodes four operationally-essential rules whose
    # absence produces graders that drift: the EXACT verdict format the
    # parser regex depends on (deviation → ParseError → council quorum
    # collapse), truncation handling (do not penalize beyond visible
    # evidence), negative-rubric polarity (Satisfied=Yes means the forbidden
    # behavior OCCURRED, not that the guardrail held), and the Yes/No-only
    # emission (no N/A, no Maybe, no markdown tables). All four are
    # referenced in the matching parser at _VERDICT_RE and in the aggregator
    # at _grade_council; changing the prompt without updating both is a
    # known-bad refactor pattern.
    from src.utils.prompt_loader import load_prompt
    return load_prompt("judge_system")


# Rubric schemas in this repo store the weight under either `weight` or
# `score` (kensei2-style rubrics use `score`, with the SIGN encoding polarity
# — negative for guardrail / forbidden-behavior criteria). The judge prompt's
# polarity semantics live entirely in the weight sign, so missing this fallback
# silently flattens all guardrail criteria to positive weight=1.0 and inverts
# the pass-count for any criterion the agent CORRECTLY refrained from.
def _extract_weight(r: dict) -> float:
    w = r.get("weight")
    if w is None:
        w = r.get("score")
    if w is None:
        return 1.0
    try:
        return float(w)
    except (TypeError, ValueError):
        return 1.0


def _split_evidence(evidence: str) -> tuple[str, str]:
    marker = "\n----- TRANSCRIPT (condensed) -----\n"
    if marker in evidence:
        files_part, _, transcript_part = evidence.partition(marker)
        return files_part, transcript_part
    return evidence, ""


def _judge_user_prompt(task_description: str, rubrics: list, evidence: str) -> str:
    from src.utils.prompt_loader import load_prompt
    output_files, transcript = _split_evidence(evidence)
    crit_lines = []
    for i, r in enumerate(rubrics):
        crit = r.get("criterion") if isinstance(r, dict) else str(r)
        wt = _extract_weight(r) if isinstance(r, dict) else 1.0
        crit_lines.append(f"{i + 1}. {crit}  [points: {wt}]")
    return load_prompt(
        "judge_user",
        task_description=task_description,
        transcript=transcript or "(no transcript captured)",
        output_files=output_files or "(no deliverable files were collected)",
        rubrics_block="\n".join(crit_lines),
        n_criteria=len(rubrics),
    )


# Per real-task forensics, agents intuit several different deliverable-root
# names (results, deliverables, output, out, artifacts). Hard-coding only
# results/ silently zeros out otherwise-correct runs (see Claude run in the
# trajectory failure report — wrote to deliverables/, scored 0/18).
_DELIVERABLE_DIR_NAMES = ("results", "deliverables", "output", "out", "artifacts")

# Text-readable deliverable formats. Used only for the workspace-ROOT scan
# below (files saved directly under /tmp_workspace rather than in a named
# subdir). Binary formats (pdf/docx/xlsx/png) would inject decode garbage into
# the judge evidence and are almost always supplied inputs, not agent output.
_DELIVERABLE_EXTS = {
    ".csv", ".tsv", ".md", ".markdown", ".json", ".txt", ".text",
    ".yaml", ".yml", ".html", ".htm", ".xml", ".log",
}
_ROOT_SCAN_MAX_FILE_BYTES = 512_000   # skip oversized files in the root scan


def _looks_like_deliverable(path: Path, root: Path) -> bool:
    """True if a workspace-root file looks like agent output (text-readable
    extension) rather than an oversized blob or binary input."""
    if path.suffix.lower() not in _DELIVERABLE_EXTS:
        return False
    try:
        return path.stat().st_size <= _ROOT_SCAN_MAX_FILE_BYTES
    except OSError:
        return False


def _collect_deliverable_files(workspace_results: Path) -> list[Path]:
    files: list[Path] = []
    seen: set[Path] = set()

    def _add_from(root: Path) -> None:
        if not root.is_dir():
            return
        for f in sorted(root.rglob("*")):
            if f.is_file() and f not in seen:
                seen.add(f)
                files.append(f)

    if workspace_results:
        results_path = Path(workspace_results)
        _add_from(results_path)
        # Sibling sweep: workspace_full/<deliverable-name>/ written by the agent
        # outside results/ — collect_output_from_container always preserves the
        # full /tmp_workspace tree under workspace_full/ for exactly this case.
        workspace_root = results_path.parent.parent if results_path.name == "results" else results_path.parent
        for sibling in (workspace_root / "workspace_full", workspace_root):
            if not sibling.is_dir():
                continue
            for name in _DELIVERABLE_DIR_NAMES:
                _add_from(sibling / name)
            # Some agents save deliverables at the workspace ROOT (e.g.
            # /tmp_workspace/foo.csv) rather than in a named subdir. Recover
            # text-like deliverable files sitting directly under the sweep root,
            # without recursing into input/scaffold subtrees.
            for f in sorted(sibling.glob("*")):
                if f.is_file() and f not in seen and _looks_like_deliverable(f, sibling):
                    seen.add(f)
                    files.append(f)
    return files


def _gather_evidence(
    workspace_results: Path,
    transcript_text: str,
    budget: int | None = None,
) -> str:
    parts: list[str] = []
    deliverables = _collect_deliverable_files(workspace_results)
    for f in deliverables:
        try:
            body = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        parts.append(f"\n----- DELIVERABLE: {f.name} -----\n{body}")
    if not parts:
        parts.append(
            "\n(no deliverable files were collected under any of: "
            + ", ".join(f"{n}/" for n in _DELIVERABLE_DIR_NAMES)
            + ")\n"
        )
    if transcript_text:
        parts.append(f"\n----- TRANSCRIPT (condensed) -----\n{transcript_text}")
    blob = "".join(parts)
    effective = _JUDGE_MAX_EVIDENCE if budget is None else budget
    return blob if effective is None else blob[:effective]


_ZERO_USAGE = {
    "input_tokens": 0,
    "output_tokens": 0,
    "cache_read_tokens": 0,
    "cache_write_tokens": 0,
    "total_tokens": 0,
    "request_count": 0,
}


def _call_judge_openai(model: str, system: str, user: str) -> tuple[str, dict]:
    import urllib.request
    key = os.environ.get("KENSEI_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
    if not key:
        raise RuntimeError("no OpenAI key for judge")
    # Yes/No verdict format (judge_walkthrough_2026_05_27.html §1.1 EXACT FORMAT):
    # judge emits free-form text with `[[RATIONALE:]] [[SATISFIED:Yes|No]]` blocks
    # wrapped in `<judgment>...</judgment>`. We MUST NOT pass
    # `response_format={"type":"json_object"}` here — it forces OpenAI to wrap
    # the entire response in a JSON envelope, which breaks `_VERDICT_RE` and
    # collapses every council vote into a parse error. max_completion_tokens
    # raised 4000→8000 so 25-criterion rubrics (≈1.2k verdict tokens) leave
    # ample headroom for reasoning.
    body = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "max_completion_tokens": 8000,
        "stream": True,
        "stream_options": {"include_usage": True},
    }).encode()
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions", data=body, method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                 "Accept": "text/event-stream"},
    )
    text_parts: list[str] = []
    u: dict = {}
    with urllib.request.urlopen(req, timeout=120) as r:
        for raw_line in r:
            line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                obj = json.loads(payload)
            except json.JSONDecodeError:
                continue
            choices = obj.get("choices") or []
            if choices:
                delta = choices[0].get("delta") or {}
                content = delta.get("content")
                if isinstance(content, str):
                    text_parts.append(content)
            usage_obj = obj.get("usage")
            if isinstance(usage_obj, dict):
                u = usage_obj
    text = "".join(text_parts)
    details = u.get("prompt_tokens_details", {}) or {}
    prompt_tok = int(u.get("prompt_tokens", 0) or 0)
    comp_tok = int(u.get("completion_tokens", 0) or 0)
    cached_tok = int(details.get("cached_tokens", 0) or 0)
    usage = {
        "input_tokens": prompt_tok,
        "output_tokens": comp_tok,
        "cache_read_tokens": cached_tok,
        "cache_write_tokens": 0,
        "total_tokens": int(u.get("total_tokens", 0) or (prompt_tok + comp_tok)),
        "request_count": 1,
    }
    return text, usage


_ARN_REGION_RE = re.compile(r"^arn:aws:bedrock:([a-z0-9-]+):")

# Bedrock prompt-caching support is per-model. Anthropic Claude on Bedrock
# accepts `cachePoint` blocks; Kimi (`p532c9fzmeed`) and GLM (`xx5msvho23iq`)
# do NOT and return HTTP 403 "You invoked an unsupported model or your request
# did not allow prompt caching." Observed in alden-croft 2026-06-02T20:20:04Z
# gateway.log: 2-of-3 council members 403'd, quorum fell back to single-judge.
# Identifiers below are the application-inference-profile IDs known to map to
# Anthropic Claude family models. Add a new ID here only after empirically
# confirming the underlying model is Anthropic.
_CACHE_SUPPORTED_PROFILE_IDS = (
    "is9bst5tfadh",  # Sonnet 4.6 (council default, single-judge primary)
    "xv71vnlzm71s",  # Sonnet 4.6 (alternate; IAM-denied per b34 but caches when permitted)
    "96j5zamnqlci",  # Opus (.env KENSEI_BEDROCK_MODEL_ARN)
)


def _supports_prompt_caching(arn: str) -> bool:
    return any(pid in (arn or "") for pid in _CACHE_SUPPORTED_PROFILE_IDS)


def _bedrock_region_for(arn: str) -> str:
    m = _ARN_REGION_RE.match(arn or "")
    if m:
        return m.group(1)
    return os.environ.get("KENSEI_AWS_REGION") or os.environ.get("AWS_REGION", "ap-south-1")


def _call_judge_bedrock(arn: str, system: str, user: str) -> tuple[str, dict]:
    import urllib.request, urllib.parse, urllib.error
    from src.utils.bedrock_eventstream import iter_eventstream
    tok = os.environ.get("KENSEI_AWS_BEARER_TOKEN") or os.environ.get("AWS_BEARER_TOKEN_BEDROCK", "")
    if not tok:
        raise RuntimeError("no Bedrock bearer token for judge")
    reg = _bedrock_region_for(arn)
    mid = urllib.parse.quote(arn, safe="")
    url = f"https://bedrock-runtime.{reg}.amazonaws.com/model/{mid}/converse-stream"

    def _do_post(include_temperature: bool) -> tuple[str, dict]:
        infer = {"maxTokens": 8000}
        if include_temperature:
            infer["temperature"] = 0
        # Bedrock prompt-caching: a `cachePoint` block marks the preceding
        # blocks as cacheable for ~5 min on Anthropic models. Kimi K2.5 and
        # GLM 5 on Bedrock return 403 "your request did not allow prompt
        # caching" if cachePoint is present (see _CACHE_SUPPORTED_PROFILE_IDS
        # above). Gate emission on per-ARN allowlist; preserve b49 caching
        # win on Sonnet/Opus without re-triggering the 2-of-3 council quorum
        # collapse observed in alden-croft 2026-06-02T20:20Z gateway.log.
        if _supports_prompt_caching(arn):
            system_blocks = [{"text": system}, {"cachePoint": {"type": "default"}}]
        else:
            system_blocks = [{"text": system}]
        body = json.dumps({
            "system": system_blocks,
            "messages": [{"role": "user", "content": [{"text": user}]}],
            "inferenceConfig": infer,
        }).encode()
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json",
                     "Accept": "application/vnd.amazon.eventstream"},
        )
        return _consume(req)

    def _consume(req) -> tuple[str, dict]:
        text_parts: list[str] = []
        u: dict = {}
        try:
            resp = urllib.request.urlopen(req, timeout=120)
        except urllib.error.HTTPError as e:
            err_body = ""
            try:
                err_body = e.read().decode("utf-8", "replace")[:2000]
            except Exception:
                pass
            raise RuntimeError(f"Bedrock HTTP {e.code} at {url}: {err_body}") from None
        with resp as r:
            def _chunks():
                while True:
                    chunk = r.read(8192)
                    if not chunk:
                        return
                    yield chunk
            for evt_type, evt_payload in iter_eventstream(_chunks()):
                if not isinstance(evt_payload, dict):
                    continue
                if evt_type and evt_type.endswith("Exception"):
                    err = evt_payload.get("Message") or evt_payload.get("message") or ""
                    raise RuntimeError(f"Bedrock judge error ({evt_type}): {err}")
                if evt_type == "contentBlockDelta":
                    delta = evt_payload.get("delta") or {}
                    txt = delta.get("text")
                    if isinstance(txt, str):
                        text_parts.append(txt)
                elif evt_type == "metadata":
                    usage_obj = evt_payload.get("usage")
                    if isinstance(usage_obj, dict):
                        u = usage_obj
        text = "".join(text_parts)
        in_tok = int(u.get("inputTokens", 0) or 0)
        out_tok = int(u.get("outputTokens", 0) or 0)
        c_read = int(
            u.get("cacheReadInputTokens")
            or u.get("cacheReadTokens")
            or u.get("cache_read_input_tokens")
            or 0
        )
        c_write = int(
            u.get("cacheWriteInputTokens")
            or u.get("cacheCreationInputTokens")
            or u.get("cache_creation_input_tokens")
            or 0
        )
        usage = {
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "cache_read_tokens": c_read,
            "cache_write_tokens": c_write,
            "total_tokens": int(u.get("totalTokens", 0) or (in_tok + out_tok + c_read + c_write)),
            "request_count": 1,
        }
        return text, usage

    try:
        return _do_post(include_temperature=True)
    except RuntimeError as exc:
        msg = str(exc).lower()
        if "temperature" in msg and ("deprecated" in msg or "not supported" in msg or "unsupported" in msg):
            return _do_post(include_temperature=False)
        raise


def _parse_judge_json(text: str) -> dict:
    text = (text or "").strip()
    # Strip a leading ```json / ``` fence if present.
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Decode the FIRST balanced JSON object, ignoring any trailing data (some
    # judge models append a second object or prose after the first — that was
    # the "Extra data: line 2 column 1" failure).
    start = text.find("{")
    if start != -1:
        try:
            obj, _ = json.JSONDecoder().raw_decode(text[start:])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    raise ValueError("judge returned no parseable JSON")


# Parser for the Yes/No verdict format mandated by _judge_system_prompt. Three
# load-bearing properties: (1) DOTALL so rationale text can span newlines,
# (2) the leading 'N.' anchor disambiguates each verdict block when judges
# emit Markdown-style bold/italic markers around the criterion sentence,
# (3) TRUNCATION_AFFECTED is OPTIONAL so a judge that omits it (older models,
# rare miss-emission) does not collapse the whole verdict count and trigger
# quorum failure. Defaults to No when absent. Match this regex's structure
# against any change to the system prompt's verdict template; deviation here
# silently zeros all judge votes — see 2026-06-02 alden-croft regression
# context referenced in _judge_system_prompt.
_VERDICT_RE = re.compile(
    r"\d+\.\s.*?"
    r"\[\[\s*RATIONALE:\s*(.*?)\s*\]\]\s*"
    r"\[\[\s*SATISFIED:\s*(Yes|No)\s*\]\]"
    r"(?:\s*\[\[\s*TRUNCATION_AFFECTED:\s*(Yes|No)\s*\]\])?",
    re.DOTALL | re.IGNORECASE,
)


def _parse_verdict_text(response: str, n_criteria: int) -> list[dict]:
    if not response:
        raise ValueError(f"empty judge response (expected up to {n_criteria} verdicts)")
    # Tolerate judges that wrap the entire block in <judgment>...</judgment> or
    # emit it bare; either way the verdict items themselves are what we match.
    matches = _VERDICT_RE.findall(response)
    # Partial-coverage contract per user m1543: smaller-context judges
    # (Kimi K2.5 = 256k input, GLM 5 = 200k input) truncate output before
    # reaching all rubric items on long rubrics (Sonnet 4.6 = 1M handles 69+
    # comfortably). Renata-voss 2026-06-03 produced GLM=59 / Kimi=54 / Sonnet=69
    # for a 69-criterion rubric. Returning the partial list lets the council
    # aggregator vote per-criterion using only judges that actually covered
    # that index; abstentions never invent No-votes the model did not cast.
    if not matches:
        raise ValueError(
            f"no verdicts parsed (expected up to {n_criteria}); response shape does not match _VERDICT_RE"
        )
    out: list[dict] = []
    # Cap at n_criteria in case a judge emits stray numbered items beyond the
    # rubric (defensive — verdict list is ordered, criteria N+1.. would be junk).
    for rationale, satisfied, truncation in matches[:n_criteria]:
        out.append({
            "rationale": (rationale or "").strip(),
            "satisfied": (satisfied or "No").strip().lower() == "yes",
            "truncation_affected": (truncation or "No").strip().lower() == "yes",
        })
    return out


def _call_one_judge(model: str, system: str, user: str) -> tuple[str, dict]:
    provider, _, rest = model.partition("/")
    if provider == "bedrock":
        return _call_judge_bedrock(rest, system, user)
    return _call_judge_openai(rest or model, system, user)


def _run_single_judge(
    primary: str, fallback: str, system: str, user: str
) -> tuple[dict | None, dict, str | None, Exception | None]:
    candidates = [m for m in (primary, fallback) if m]
    last_err: Exception | None = None
    for model in candidates:
        try:
            raw, usage = _call_one_judge(model, system, user)
        except Exception as exc:
            last_err = exc
            logger.warning("Rubric judge model %s failed (%s); trying next", model, exc)
            continue
        try:
            parsed = _parse_judge_json(raw)
        except Exception as exc:
            last_err = exc
            logger.warning("Rubric judge %s parse failed (%s); trying next", model, exc)
            continue
        return parsed, usage, model, None
    return None, dict(_ZERO_USAGE), None, last_err


def _run_council(
    members: list[str],
    system: str,
    user_for_member: "dict[str, str] | str",
    n_criteria: int,
) -> list[dict]:
    """Run every member judge in parallel and return one result dict per member:
    {model, ok, verdicts?, usage, error?, user_chars, raw_response?}. Never raises.

    `user_for_member` may be a single shared string (legacy) or a
    {model: user_prompt} dict so each member receives a payload sized to its
    own context window. `n_criteria` is the expected verdict count; parses
    failing this count return `ok=False, error='parse: ...'` rather than raise."""
    from concurrent.futures import ThreadPoolExecutor

    def _resolve_user(model: str) -> str:
        if isinstance(user_for_member, dict):
            return user_for_member.get(model, "")
        return user_for_member

    def _one(model: str) -> dict:
        user = _resolve_user(model)
        try:
            raw, usage = _call_one_judge(model, system, user)
        except Exception as exc:
            logger.warning("Council member %s call failed: %s", model, exc)
            return {
                "model": model, "ok": False,
                "error": f"call: {exc}", "usage": dict(_ZERO_USAGE),
                "user_chars": len(user),
            }
        try:
            verdicts = _parse_verdict_text(raw, n_criteria)
        except Exception as exc:
            logger.warning("Council member %s parse failed: %s", model, exc)
            return {
                "model": model, "ok": False,
                "error": f"parse: {exc}", "usage": usage,
                "user_chars": len(user),
                "raw_response": raw[:2000] if isinstance(raw, str) else "",
            }
        return {
            "model": model, "ok": True, "verdicts": verdicts, "usage": usage,
            "user_chars": len(user),
        }

    with ThreadPoolExecutor(max_workers=max(1, len(members))) as pool:
        return list(pool.map(_one, members))


def _quantize_binary(score: float) -> float:
    return 1.0 if score >= 0.5 else 0.0


def _stddev(values: list[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / n
    return var ** 0.5


def _criterion_pass(score: float, weight: float) -> bool:
    triggered = score >= 0.5
    return (not triggered) if weight < 0 else triggered


def _criterion_pass_from_satisfied(satisfied: bool, weight: float) -> bool:
    # Walkthrough §2 polarity rule: SATISFIED reflects the literal criterion
    # text, NOT the point sign. Aggregator applies sign here.
    #  positive weight + satisfied=True  → passed (desired thing done)
    #  positive weight + satisfied=False → failed
    #  negative weight + satisfied=False → passed (guardrail held)
    #  negative weight + satisfied=True  → failed (forbidden behavior occurred)
    return (not satisfied) if weight < 0 else satisfied


def _short_judge_label(model: str) -> str:
    """Shorten 'bedrock/arn:aws:bedrock:<region>:<acct>:application-inference-profile/<id>'
    to '<id>' for compact per-criterion arrays in score.json; preserves the
    'openai/<model>' shape verbatim."""
    if model.startswith("bedrock/"):
        return model.rsplit("/", 1)[-1]
    return model


def _grade_council(
    rubrics: list,
    system: str,
    user_for_member: "dict[str, str] | str",
    members: list[str],
) -> dict | None:
    """Council aggregation. Returns scores dict (with `judge_council` block) if
    quorum >= 2 surviving members; returns None to signal 'fall through to
    single-judge'."""
    results = _run_council(members, system, user_for_member, len(rubrics))
    surviving = [r for r in results if r.get("ok") and isinstance(r.get("verdicts"), list)]
    if len(surviving) < 2:
        failed_summary = "; ".join(
            f"{_short_judge_label(r.get('model', '?'))}={(r.get('error') or 'unknown').strip()[:160]}"
            for r in results if not r.get("ok")
        ) or "(no failure detail captured)"
        logger.warning(
            "Judge council quorum failed: only %d/%d members succeeded; failed: %s; falling back to single-judge",
            len(surviving), len(members), failed_summary,
        )
        return None

    verdicts_per_member: list[list[dict]] = [r["verdicts"] for r in surviving]
    n_surv = len(surviving)

    crit_out: list[dict] = []
    truncation_flags: list[int] = []
    abstention_flags: list[int] = []
    weighted = 0.0
    passed = 0
    # Denominator is the sum of POSITIVE weights only — walkthrough §4 verbatim:
    # 'Always use sum(positive_points). Do NOT use sum(all_points) — that
    # overshoots 1 whenever penalties exist.' Mirrors test_executor._compute_reward
    # pos_total. See alden-croft 2026-06-02 (23/23 passed → overall 0.4986 was
    # bug; positive-only denom gives 0.983 correctly).
    total_w = sum(_extract_weight(r) for r in rubrics
                  if isinstance(r, dict) and _extract_weight(r) > 0) or 1.0

    for i, r in enumerate(rubrics):
        wt = _extract_weight(r) if isinstance(r, dict) else 1.0
        # Partial-coverage per-criterion voter pool (user m1543): only judges
        # that actually emitted a verdict for index i vote on this criterion.
        # Abstentions (truncated output past index i) are NOT counted as votes
        # in either direction. Threshold = ceil(voters/2): 2-of-3, 1-of-2,
        # 1-of-1; tie-on-2 → Yes (b75 recommendation). 0 voters → abstain.
        per_satisfied: list[bool] = []
        per_rationale: list[str] = []
        per_truncation: list[bool] = []
        per_label: list[str] = []
        per_voted: list[bool] = []
        for member_result, verdicts in zip(surviving, verdicts_per_member):
            label = _short_judge_label(member_result["model"])
            per_label.append(label)
            if i < len(verdicts):
                v = verdicts[i]
                per_voted.append(True)
                per_satisfied.append(bool(v.get("satisfied", False)))
                per_rationale.append(str(v.get("rationale", "") or ""))
                per_truncation.append(bool(v.get("truncation_affected", False)))
            else:
                per_voted.append(False)
                per_satisfied.append(False)
                per_rationale.append("(abstained — output truncated before this criterion)")
                per_truncation.append(False)

        voters = sum(1 for vd in per_voted if vd)
        if voters == 0:
            abstention_flags.append(i)
            satisfied_majority = False
            criterion_passed = False
        else:
            crit_threshold = (voters + 1) // 2
            yes_votes = sum(1 for s, vd in zip(per_satisfied, per_voted) if vd and s)
            satisfied_majority = yes_votes >= crit_threshold
            criterion_passed = _criterion_pass_from_satisfied(satisfied_majority, wt)
            if criterion_passed:
                passed += 1
            # Walkthrough §4 reward: contribute weight ONLY if majority says Yes.
            # No fractions. Yes/No is binary by construction — b51 leak impossible.
            if satisfied_majority:
                weighted += wt
        if any(per_truncation):
            truncation_flags.append(i)
        crit_out.append({
            "id": i,
            "weight": wt,
            "satisfied": satisfied_majority,
            "passed": criterion_passed,
            "voters": voters,
            "criterion": (r.get("criterion") if isinstance(r, dict) else str(r)),
            "votes": "/".join(
                ("Yes" if s else "No") if vd else "Abstain"
                for s, vd in zip(per_satisfied, per_voted)
            ),
            "satisfied_by_judge": per_satisfied,
            "voted_by_judge": per_voted,
            "rationales_by_judge": per_rationale,
            "truncation_affected_by_judge": per_truncation,
            "judges": per_label,
            "is_positive": wt >= 0,
        })

    overall = max(0.0, min(1.0, weighted / total_w))
    council_usage = _ZERO_USAGE.copy()
    for r in results:
        u = r.get("usage") or {}
        for k in council_usage.keys():
            council_usage[k] = council_usage.get(k, 0) + int(u.get(k, 0) or 0)

    n = len(rubrics)
    n_abstained = len(abstention_flags)
    failed = n - passed - n_abstained
    # Schema contract — score.json scores RUBRIC CRITERIA, not pytest tests.
    # Canonical keys: criteria_total/_passed/_failed/_abstained and
    # rubric_weights_percentage (= overall_score * 100, per user formula m1420).
    # criteria_total = passed + failed + abstained (b82 invariant). The tests_*
    # keys are deprecated aliases retained ONLY because run_batch.py:706-714
    # forwards these counts into the harbor pytest channel (test_result / SQLite
    # store / ctrf.json) when no real pytest result exists. Deleting the aliases
    # here without first migrating that adapter will silently zero the harbor
    # bundle's test counts. See NOMENCLATURE.md for the channel boundary.
    return {
        "overall_score": round(overall, 4),
        "rubric_weights_percentage": round(overall * 100.0, 2),
        "criteria_total": n,
        "criteria_passed": passed,
        "criteria_failed": failed,
        "criteria_abstained": n_abstained,
        "tests_total": n,
        "tests_passed": passed,
        "tests_failed": failed,
        "criteria": crit_out,
        "judge_model": "council",
        "judge_council": {
            "members": [r["model"] for r in results],
            "surviving": [r["model"] for r in surviving],
            "failed": [
                {"model": r["model"], "error": r.get("error", "")}
                for r in results if not r.get("ok")
            ],
            "aggregation": "majority_vote_partial_coverage",
            "per_member_user_chars": {
                r["model"]: int(r.get("user_chars", 0) or 0) for r in results
            },
            "per_member_verdict_count": {
                r["model"]: len(r["verdicts"]) for r in surviving
            },
        },
        "truncation_flags": truncation_flags,
        "abstention_flags": abstention_flags,
        "usage": council_usage,
    }


def grade_with_rubric(
    rubrics: list,
    task_description: str,
    workspace_results: Path,
    transcript_text: str = "",
    judge_model: str | None = None,
    use_council: bool | None = None,
) -> dict:
    """Score `rubrics` with an LLM judge or judge council.

    `use_council` overrides the JUDGE_COUNCIL env flag when not None. Council
    mode runs the members from `council_members()` in parallel, aggregates by
    per-criterion mean, and falls through to single-judge if quorum (>=2
    surviving members) is not met. Returns a scores dict:
    {overall_score, rubric_weights_percentage,
     criteria_total, criteria_passed, criteria_failed,
     tests_total, tests_passed, tests_failed,   # deprecated aliases
     criteria:[...], judge_model, [judge_council, disagreement_flags]}
    or {overall_score:0.0, error:...} on failure (never raises)."""
    if not rubrics:
        return {"overall_score": 0.0, "error": "no rubric criteria"}
    system = _judge_system_prompt()

    want_council = council_enabled() if use_council is None else bool(use_council)
    if want_council:
        members = council_members()
        if len(members) >= 2:
            user_for_member: dict[str, str] = {}
            for m in members:
                budget = _member_evidence_budget(m)
                ev = _gather_evidence(workspace_results, transcript_text, budget=budget)
                user_for_member[m] = _judge_user_prompt(task_description, rubrics, ev)
            council_scores = _grade_council(rubrics, system, user_for_member, members)
            if council_scores is not None:
                return council_scores
        else:
            logger.warning(
                "Judge council requested but only %d members configured; falling back to single-judge",
                len(members),
            )

    user = _judge_user_prompt(task_description, rubrics, _gather_evidence(workspace_results, transcript_text))
    primary = judge_model or JUDGE_MODEL
    fallback = os.environ.get("JUDGE_MODEL_FALLBACK", "openai/gpt-5.4")
    n_criteria = len(rubrics)
    verdicts: list[dict] | None = None
    judge_usage: dict = dict(_ZERO_USAGE)
    used_model: str | None = None
    last_err: Exception | None = None
    for model in [m for m in (primary, fallback) if m]:
        try:
            raw, judge_usage = _call_one_judge(model, system, user)
        except Exception as exc:
            last_err = exc
            logger.warning("Rubric judge model %s failed (%s); trying next", model, exc)
            continue
        try:
            verdicts = _parse_verdict_text(raw, n_criteria)
            used_model = model
            break
        except Exception as exc:
            last_err = exc
            logger.warning("Rubric judge %s parse failed (%s); trying next", model, exc)
            continue
    if verdicts is None or used_model is None:
        logger.error("All rubric judge models failed: %s", last_err)
        return {"overall_score": 0.0, "error": f"judge failed: {last_err}", "usage": dict(_ZERO_USAGE)}

    total_w = sum(_extract_weight(r) for r in rubrics
                  if isinstance(r, dict) and _extract_weight(r) > 0) or 1.0
    weighted = 0.0
    passed = 0
    truncation_flags: list[int] = []
    # Single-judge mirror of b82 partial-coverage policy (user m1543 2026-06-03):
    # if the judge truncated mid-rubric, indices i >= len(verdicts) are abstentions
    # (NOT silent No-votes). Abstained criteria contribute 0 to weighted AND are
    # NOT counted toward criteria_passed/criteria_failed. The walkthrough §4
    # denominator (sum positive weights) is unchanged so abstentions reduce the
    # achievable ceiling, mirroring how a missing vote loses winnable points.
    abstention_flags: list[int] = []
    crit_out = []
    for i, r in enumerate(rubrics):
        wt = _extract_weight(r) if isinstance(r, dict) else 1.0
        voted = i < len(verdicts)
        v = verdicts[i] if voted else {}
        satisfied = bool(v.get("satisfied", False)) if voted else False
        truncation_affected = bool(v.get("truncation_affected", False)) if voted else False
        if truncation_affected:
            truncation_flags.append(i)
        if not voted:
            abstention_flags.append(i)
            criterion_passed = False
            rationale_text = "(abstained — judge output truncated before this criterion)"
        else:
            criterion_passed = _criterion_pass_from_satisfied(satisfied, wt)
            if criterion_passed:
                passed += 1
            if satisfied:
                weighted += wt
            rationale_text = str(v.get("rationale", "") or "")
        crit_out.append({
            "id": i, "weight": wt,
            "voted": voted,
            "satisfied": satisfied,
            "passed": criterion_passed,
            "criterion": (r.get("criterion") if isinstance(r, dict) else str(r)),
            "rationale": rationale_text,
            "truncation_affected": truncation_affected,
            "is_positive": wt >= 0,
        })
    overall = max(0.0, min(1.0, weighted / total_w))
    n = len(rubrics)
    n_abstained = len(abstention_flags)
    failed = n - passed - n_abstained
    return {
        "overall_score": round(overall, 4),
        "rubric_weights_percentage": round(overall * 100.0, 2),
        "criteria_total": n,
        "criteria_passed": passed,
        "criteria_failed": failed,
        "criteria_abstained": n_abstained,
        "tests_total": n,
        "tests_passed": passed,
        "tests_failed": failed,
        "criteria": crit_out,
        "judge_model": used_model,
        "truncation_flags": truncation_flags,
        "abstention_flags": abstention_flags,
        "usage": judge_usage,
    }

def _write_score(output_dir: Path, task_id: str, scores: dict) -> None:
    score_path = output_dir / "score.json"
    score_path.parent.mkdir(parents=True, exist_ok=True)
    score_path.write_text(
        json.dumps(scores, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    logger.info("[%s] Grading results written to → %s", task_id, score_path)


def _error_score(output_dir: Path, task_id: str, message: str) -> dict:
    scores = {"overall_score": 0.0, "error": message}
    _write_score(output_dir, task_id, scores)
    return scores


def _grading_error(
    output_dir: Path,
    task_id: str,
    message: str,
    write_error_score: bool,
) -> dict:
    if write_error_score:
        return _error_score(output_dir, task_id, message)
    return {"error": message}


def write_error_score(output_dir: Path, task_id: str, message: str) -> dict:
    return _error_score(output_dir, task_id, message)


def run_grading(
    task_id: str,
    automated_checks: str,
    output_dir: Path,
    extra_env: str = "",
    lobster_env: list[str] | None = None,
    transcript_container_path: str = "",
    write_error_score: bool = False,
) -> dict:
    logger.info("[%s] Starting in-container grading...", task_id)

    loader_src = Path(__file__).with_name("transcript_loader.py")
    if not loader_src.exists():
        logger.error("[%s] transcript loader module not found: %s", task_id, loader_src)
        return _grading_error(
            output_dir,
            task_id,
            f"transcript loader module not found: {loader_src}",
            write_error_score,
        )

    runner_code = "\n".join([
        "import json",
        "from _transcript_loader import load_transcript",
        f"_transcript = load_transcript({json.dumps(transcript_container_path)})",
        "",
        automated_checks,
        "",
        f'result = grade(transcript=_transcript, workspace_path="{TMP_WORKSPACE}")',
        "print(json.dumps(result))",
    ]) + "\n"

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    ) as f:
        f.write(runner_code)
        runner_host = f.name

    try:
        r_loader = subprocess.run(
            ["docker", "cp", str(loader_src), f"{task_id}:/tmp/_transcript_loader.py"],
            capture_output=True, text=True,
        )
        if r_loader.returncode != 0:
            logger.error("[%s] docker cp transcript loader failed: %s", task_id, r_loader.stderr)
            return _grading_error(
                output_dir,
                task_id,
                f"docker cp transcript loader failed: {r_loader.stderr}",
                write_error_score,
            )

        r = subprocess.run(
            ["docker", "cp", runner_host, f"{task_id}:/tmp/_grade_runner.py"],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            logger.error("[%s] docker cp failed: %s", task_id, r.stderr)
            return _grading_error(
                output_dir,
                task_id,
                f"docker cp failed: {r.stderr}",
                write_error_score,
            )

        env_args: list[str] = []
        for line in extra_env.splitlines():
            key = line.strip()
            if not key or key.startswith("#"):
                continue
            value = os.environ.get(key, "")
            env_args += ["-e", f"{key}={value}"]
            masked = (value[:4] + "***") if value else "(empty)"
            logger.info("[%s] Injecting grading env: %s=%s", task_id, key, masked)

        for key in (lobster_env or []):
            value = os.environ.get(key, "")
            if not value:
                logger.warning("[%s] Grading lobster env key %s not found, skipping", task_id, key)
                continue
            env_args += ["-e", f"{key}={value}"]
            masked = value[:4] + "***"
            logger.info("[%s] Injecting grading lobster env: %s=%s", task_id, key, masked)

        r = subprocess.run(
            ["docker", "exec", *env_args, task_id, "python3", "/tmp/_grade_runner.py"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if r.returncode != 0:
            logger.error("[%s] Grading script execution failed: %s", task_id, r.stderr)
            return _grading_error(
                output_dir,
                task_id,
                f"grade script failed: {r.stderr}",
                write_error_score,
            )

        try:
            scores = json.loads(r.stdout.strip())
        except json.JSONDecodeError:
            scores = None
            for line in reversed(r.stdout.strip().splitlines()):
                line = line.strip()
                if line.startswith("{"):
                    try:
                        scores = json.loads(line)
                        break
                    except json.JSONDecodeError:
                        continue
            if scores is None:
                logger.error("[%s] Failed to parse grading result, no valid JSON found in stdout\nstdout: %s", task_id, r.stdout[:500])
                return _grading_error(
                    output_dir,
                    task_id,
                    "json parse failed: no valid JSON in stdout",
                    write_error_score,
                )

    finally:
        Path(runner_host).unlink(missing_ok=True)

    _write_score(output_dir, task_id, scores)
    return scores


def format_scores(task_id: str, scores: dict) -> str:
    if "error" in scores and not any(
        isinstance(v, (int, float)) for v in scores.values()
    ):
        return f"[{task_id}] Grading error: {scores['error']}"
    lines = [f"\n{'='*60}", f"  {task_id}", f"{'='*60}"]

    for k, v in scores.items():
        if isinstance(v, (int, float)):
            bar = "█" * int(v * 10) + "░" * (10 - int(v * 10))
            lines.append(f"  {bar} {v:.2f}  {k}")

    lines.append("=" * 60)
    return "\n".join(lines)

def print_summary(results: list[dict], category: str, output_dir: Path, model_name: str) -> None:
    print(f"\n{'#'*60}")
    print(f"  Summary Report — {category}")
    print(f"{'#'*60}")

    all_scores: dict[str, float] = {}
    for r in results:
        task_id = r["task_id"]
        scores = r['scores']
        if not scores:
            if r.get("error"):
                print(f"  ✗ {task_id}: {r['error']}")
            else:
                print(f"  - {task_id}: No scores")
            continue
        numeric_dict = {k: v for k, v in scores.items() if isinstance(v, (int, float))}
        
        if not numeric_dict:
            if "error" in scores:
                print(f"  ✗ {task_id}: Grading error {scores['error']}")
            else:
                print(f"  - {task_id}: No valid numeric scores")
            continue

        avg = sum(numeric_dict.values()) / len(numeric_dict)
        status = "!" if r.get("error") or scores.get("error") else "✓"
        note = ""
        if r.get("error"):
            note = f" agent_error={r['error']}"
        elif scores.get("error"):
            note = f" grading_error={scores['error']}"
        print(f"  {status} {task_id}: avg {avg:.2f}  ({len(numeric_dict)} items){note}")

        final_score_val = numeric_dict.get('overall_score', avg)
        all_scores[task_id] = final_score_val

    if all_scores:
        print(f"\n  Final scores per task:")
        for k, score in sorted(all_scores.items()):
            bar = "█" * int(score * 10) + "░" * (10 - int(score * 10))
            print(f"    {bar} {score:.2f}  {k}")

    print(f"\n  Token usage and cost per task:")
    print(f"    {'Task ID':<55} {'Output Tokens':>12} {'Cost(USD)':>12}")
    print(f"    {'-'*55} {'-'*12} {'-'*12}")
    total_output_tokens = 0
    total_cost_usd = 0.0
    for r in sorted(results, key=lambda x: x["task_id"]):
        usage = r.get("usage", {})
        out_tok = usage.get("output_tokens", 0)
        cost = usage.get("cost_usd", 0.0)
        total_output_tokens += out_tok
        total_cost_usd += cost
        print(f"    {r['task_id']:<55} {out_tok:>12} {cost:>11.4f}$")
    print(f"    {'Total':<55} {total_output_tokens:>12} {total_cost_usd:>11.4f}$")

    summary_path = output_dir / category / f"summary_{model_name}.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(results, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    print(f"\n  Summary written to → {summary_path}")
    print("#" * 60)

_MODEL_COST_PER_TOKEN: dict[str, tuple[float, float]] = {
    "gpt-5.5":          (0.000005,  0.00003),
    "gpt-4o":           (0.0000025, 0.00001),
    "claude-opus-4.7":  (0.000005,  0.000025),
}


def _extract_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for c in content:
            if isinstance(c, str):
                parts.append(c)
            elif isinstance(c, dict):
                parts.append(str(c.get("text") or c.get("content") or ""))
        return "\n".join(parts)
    return ""


def _estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


def extract_usage_from_litellm_log(
    log_path: Path, window_start: float, window_end: float
) -> dict:
    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "total_tokens": 0,
        "cost_usd": 0.0,
        "request_count": 0,
        "usage_source": "litellm",
    }
    if not log_path or not log_path.exists():
        return totals

    from datetime import datetime as _dt

    pad = 2.0
    lo = window_start - pad
    hi = window_end + pad

    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return totals

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts_str = row.get("ts", "")
        try:
            ts = _dt.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp()
        except (ValueError, AttributeError):
            continue
        if ts < lo or ts > hi:
            continue
        totals["request_count"] += 1
        totals["input_tokens"]       += int(row.get("input_tokens", 0) or 0)
        totals["output_tokens"]      += int(row.get("output_tokens", 0) or 0)
        totals["cache_read_tokens"]  += int(row.get("cache_read_tokens", 0) or 0)
        totals["cache_write_tokens"] += int(row.get("cache_write_tokens", 0) or 0)
        totals["total_tokens"]       += int(row.get("total_tokens", 0) or 0)
        totals["cost_usd"]           += float(row.get("cost_usd", 0.0) or 0.0)

    totals["cost_usd"] = round(totals["cost_usd"], 6)
    return totals


def extract_usage_from_jsonl(jsonl_path: Path) -> dict:
    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "total_tokens": 0,
        "cost_usd": 0.0,
        "request_count": 0,
        "usage_source": "openclaw",
    }
    if not jsonl_path.exists():
        return totals

    entries: list[dict] = []
    for line in jsonl_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    openclaw_total = 0
    last_model = ""
    for entry in entries:
        if entry.get("type") != "message":
            continue
        msg = entry.get("message", {})
        if msg.get("role") != "assistant":
            continue
        totals["request_count"] += 1
        if msg.get("model"):
            last_model = msg["model"]
        usage = msg.get("usage", {})
        totals["input_tokens"]       += usage.get("input",       0)
        totals["output_tokens"]      += usage.get("output",      0)
        totals["cache_read_tokens"]  += usage.get("cacheRead",   0)
        totals["cache_write_tokens"] += usage.get("cacheWrite",  0)
        totals["total_tokens"]       += usage.get("totalTokens", 0)
        cost = usage.get("cost", {})
        totals["cost_usd"] += cost.get("total", 0.0)
        openclaw_total += usage.get("input", 0) + usage.get("output", 0)

    # Fallback: openclaw reported no usage but there were requests. Estimate
    # tokens (~len/4) with a running-context model and apply per-model rates.
    if openclaw_total == 0 and totals["request_count"] > 0:
        totals["usage_source"] = "estimated"
        running_context_tokens = 0
        for entry in entries:
            if entry.get("type") != "message":
                continue
            msg = entry.get("message", {})
            text = _extract_text(msg.get("content", ""))
            tokens = _estimate_tokens(text)
            role = msg.get("role")
            if role in ("user", "system", "toolResult"):
                running_context_tokens += tokens
            elif role == "assistant":
                totals["input_tokens"]  += running_context_tokens
                totals["output_tokens"] += tokens
                running_context_tokens += tokens
                if msg.get("model"):
                    last_model = msg["model"]
        totals["total_tokens"] = totals["input_tokens"] + totals["output_tokens"]

        model_id = last_model.split("/")[-1] if last_model else ""
        rates = _MODEL_COST_PER_TOKEN.get(model_id, (0.0, 0.0))
        totals["cost_usd"] = (
            totals["input_tokens"]  * rates[0]
            + totals["output_tokens"] * rates[1]
        )

    totals["cost_usd"] = round(totals["cost_usd"], 6)
    return totals

def print_global_summary(results: list[dict], output_dir: Path, model_name: str) -> None:
    print(f"\n{'#'*60}")
    print(f"  Global Summary Report — ALL CATEGORIES")
    print(f"{'#'*60}")

    total_tasks = len(results)
    scored_tasks = 0
    missing_score_tasks = 0
    total_score = 0.0
    for r in results:
        scores = r.get("scores", {})
        numeric = {
            k: v
            for k, v in scores.items()
            if isinstance(v, (int, float))
        } if scores else {}
        if not numeric:
            missing_score_tasks += 1
            continue
        final = numeric.get("overall_score", sum(numeric.values()) / len(numeric))
        total_score += final
        scored_tasks += 1

    global_avg = 0.0
    if total_tasks > 0:
        global_avg = total_score / total_tasks
        bar = "█" * int(global_avg * 10) + "░" * (10 - int(global_avg * 10))
        print(f"\n  Completed tasks: {scored_tasks} / {total_tasks}")
        print(f"  Tasks without a valid score.json: {missing_score_tasks}")
        if missing_score_tasks > 0:
            print("  Possible causes: task execution failed, such as OOM, or grading failed.")
        print(f"  Global average: {bar} {global_avg:.4f}")
    else:
        print("  No tasks found")

    total_out_tok = sum(r.get("usage", {}).get("output_tokens", 0) for r in results)
    total_cost    = sum(r.get("usage", {}).get("cost_usd",      0.0) for r in results)
    print(f"  Total output tokens: {total_out_tok}   Total cost: ${total_cost:.4f}")

    summary_path = output_dir / f"summary_all_{model_name}.json"
    summary_path.write_text(
        json.dumps(
            {"global_avg": global_avg if total_tasks else None,
             "task_count": total_tasks,
             "scored_task_count": scored_tasks,
             "missing_score_task_count": missing_score_tasks,
             "results": results},
            indent=2, ensure_ascii=False, default=str,
        ),
        encoding="utf-8",
    )
    print(f"\n  Global summary written to → {summary_path}")
    print("#" * 60)
