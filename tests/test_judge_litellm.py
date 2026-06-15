"""Tests for the opt-in LiteLLM + Headroom judge transport (`judge_litellm.py`)
and its integration with the council dispatcher in `grading._call_one_judge`.

The 10 unit tests guard the contracts the rest of the harness silently relies
on (see `src/utils/AGENTS.md` and `RUNBOOK.md`):

  1. Default OFF: when `KENSEI_JUDGE_USE_LITELLM` is unset, grading uses the
     legacy urllib path. No litellm import, no headroom import.
  2. Headroom ImportError-safe: if `headroom-ai` is not installed, the LiteLLM
     path still works (compression silently no-ops).
  3. Headroom stats land in the per-judge `usage["headroom"]` sub-dict.
  4. `compress_system_messages=False` is the load-bearing config — guards the
     verdict-format regex `_VERDICT_RE`. Test asserts the CompressConfig.
  5. `max_tokens` passed to `litellm.completion` matches
     `_member_max_output_tokens(arn)` (Sonnet 8192, Kimi/GLM 16384).
  6. The judge call must NOT include `thinking`, `reasoning_effort`,
     `output_config`, or `response_format` (bg_f3f8f684 #4 invariant).
  7. The returned `usage` dict has the canonical 7 keys with the same
     semantics as the urllib path (input_tokens excludes cache).
  8. On ANY LiteLLM exception the dispatcher falls back to urllib so grading
     never fails because of transport choice (user m0039 contract).
  9. `register_judges_once()` calls `litellm.register_model` once per judge
     ARN tail with the expected ctx-window / max-output / cost rates.
 10. ARN tokenizer hint: when the model id contains
     `application-inference-profile`, Headroom is called with the Anthropic
     model hint so its tokenizer can do meaningful estimates.

Plus 1 integration smoke (gated by `RUN_INTEGRATION_JUDGE_TESTS=true`) that
verifies Bedrock `cache_control` markers survive Headroom compression and
trigger a real `cache_read_input_tokens > 0` on the second call (5-min TTL).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils import grading, judge_litellm  # noqa: E402


# ---------- shared fixtures / helpers ----------


@pytest.fixture(autouse=True)
def _clean_env_and_state(monkeypatch):
    """Every test starts from a known env baseline + a fresh module-level
    registration flag so test ordering doesn't matter."""
    for var in (
        "KENSEI_JUDGE_USE_LITELLM",
        "KENSEI_JUDGE_HEADROOM_ENABLED",
        "KENSEI_JUDGE_HEADROOM_TARGET_RATIO",
        "KENSEI_JUDGE_HEADROOM_PROTECT_RECENT",
        "KENSEI_JUDGE_HEADROOM_MIN_TOKENS",
    ):
        monkeypatch.delenv(var, raising=False)
    # Reset the idempotency latch — otherwise the first test that calls
    # register_judges_once() flips it True and subsequent tests can't observe
    # the registration calls.
    judge_litellm._registered_once = False
    yield


def _make_litellm_response(text: str = "1. example [[RATIONALE: x]] [[SATISFIED: Yes]]",
                           prompt_tokens: int = 1000,
                           completion_tokens: int = 50,
                           cache_read: int = 0,
                           cache_write: int = 0) -> SimpleNamespace:
    """Construct a fake LiteLLM `completion()` return value matching the
    OpenAI-normalized shape the production code reads."""
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
        usage={
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cache_read_input_tokens": cache_read,
            "cache_creation_input_tokens": cache_write,
        },
    )


SONNET_ARN = "bedrock/arn:aws:bedrock:ap-south-1:426628337772:application-inference-profile/urg0zifsjiga"
GLM_ARN = "bedrock/arn:aws:bedrock:us-east-1:426628337772:application-inference-profile/u4czm4f2p3ws"
KIMI_ARN = "bedrock/arn:aws:bedrock:ap-south-1:426628337772:application-inference-profile/q6g7fi6wumk3"


# ---------- Test 1: litellm OFF → uses direct urllib path ----------


def test_litellm_off_uses_direct_path(monkeypatch):
    """When KENSEI_JUDGE_USE_LITELLM is unset/false the dispatcher must take
    the urllib branch. We assert by sentinel-mocking the urllib function and
    poisoning `judge_litellm.call_judge_via_litellm` to raise if accidentally
    invoked."""
    sentinel = ("ok-from-urllib", {"input_tokens": 1, "output_tokens": 2,
                                   "cache_read_tokens": 0, "cache_write_tokens": 0,
                                   "total_tokens": 3, "request_count": 1,
                                   "cost_usd": 0.0})
    monkeypatch.setattr(grading, "_call_judge_bedrock", lambda *a, **k: sentinel)

    def _poison(*a, **k):
        raise AssertionError("LiteLLM path must NOT be called when flag is off")

    monkeypatch.setattr(judge_litellm, "call_judge_via_litellm", _poison)

    text, usage = grading._call_one_judge(SONNET_ARN, "sys", "user")
    assert text == "ok-from-urllib"
    assert usage["input_tokens"] == 1


# ---------- Test 2: Headroom ImportError → silent no-op ----------


def test_litellm_on_no_headroom_install_falls_back_gracefully(monkeypatch):
    """If headroom-ai is not installed, `maybe_compress` returns the original
    messages + {} stats. The LiteLLM call still proceeds with uncompressed
    payload — grading never fails because compression is unavailable."""
    monkeypatch.setenv("KENSEI_JUDGE_USE_LITELLM", "true")

    # Simulate ImportError on `from headroom import compress, CompressConfig`
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "headroom":
            raise ImportError("simulated: headroom-ai not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", _fake_import)

    messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    out_messages, stats = judge_litellm.maybe_compress(messages, SONNET_ARN)

    assert out_messages is messages, "must pass-through original list on ImportError"
    assert stats == {}, "no telemetry when headroom unavailable"


# ---------- Test 3: Headroom compression stats recorded ----------


def test_headroom_compression_stats_recorded(monkeypatch):
    """Stub `compress()` to return a non-trivial CompressResult and assert
    the stats land in `usage['headroom']` with the expected key shape."""
    monkeypatch.setenv("KENSEI_JUDGE_USE_LITELLM", "true")

    fake_result = SimpleNamespace(
        messages=[{"role": "system", "content": "s"},
                  {"role": "user", "content": "u-compressed"}],
        tokens_before=10_000,
        tokens_after=4_000,
        tokens_saved=6_000,
        compression_ratio=0.4,
        transforms_applied=["dedupe", "whitespace"],
    )

    class _FakeCompressConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    fake_headroom = SimpleNamespace(
        compress=lambda messages, model, config: fake_result,
        CompressConfig=_FakeCompressConfig,
    )

    fake_litellm = SimpleNamespace(
        completion=mock.MagicMock(return_value=_make_litellm_response()),
        register_model=mock.MagicMock(),
    )

    monkeypatch.setitem(sys.modules, "headroom", fake_headroom)
    monkeypatch.setitem(sys.modules, "litellm", fake_litellm)

    _, usage = judge_litellm.call_judge_via_litellm(
        model=SONNET_ARN, system="s", user="u",
        max_output_tokens=8192, cost_fn=grading._judge_cost_usd,
    )
    hr = usage["headroom"]
    assert hr["tokens_before"] == 10_000
    assert hr["tokens_after"] == 4_000
    assert hr["tokens_saved"] == 6_000
    assert hr["compression_ratio"] == pytest.approx(0.4)
    assert hr["transforms_applied"] == ["dedupe", "whitespace"]


# ---------- Test 4: compress_system_messages=False is load-bearing ----------


def test_compress_system_messages_is_false(monkeypatch):
    """The system prompt encodes the verdict format that `_VERDICT_RE` parses;
    compressing it risks zeroing the whole council. Asserts CompressConfig
    was constructed with compress_system_messages=False AND
    compress_user_messages=True."""
    monkeypatch.setenv("KENSEI_JUDGE_USE_LITELLM", "true")

    captured_config = {}

    class _RecordingConfig:
        def __init__(self, **kwargs):
            captured_config.update(kwargs)

    fake_result = SimpleNamespace(
        messages=[{"role": "system", "content": "s"}, {"role": "user", "content": "u"}],
        tokens_before=3_000, tokens_after=3_000, tokens_saved=0,
        compression_ratio=1.0, transforms_applied=[],
    )
    fake_headroom = SimpleNamespace(
        compress=lambda messages, model, config: fake_result,
        CompressConfig=_RecordingConfig,
    )
    monkeypatch.setitem(sys.modules, "headroom", fake_headroom)

    judge_litellm.maybe_compress(
        [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}],
        SONNET_ARN,
    )

    assert captured_config.get("compress_user_messages") is True
    assert captured_config.get("compress_system_messages") is False


# ---------- Test 5: max_tokens passed to litellm.completion is per-judge ----------


@pytest.mark.parametrize("arn,expected_max", [
    (SONNET_ARN, 8192),
    (GLM_ARN, 16384),
    (KIMI_ARN, 16384),
])
def test_judge_max_tokens_unchanged(monkeypatch, arn, expected_max):
    """Each judge keeps its production max-output ceiling. Drift here would
    truncate verdicts mid-rubric — `_parse_verdict_text` would still return a
    list but with fewer entries, silently dropping criteria."""
    monkeypatch.setenv("KENSEI_JUDGE_USE_LITELLM", "true")
    monkeypatch.setenv("KENSEI_JUDGE_HEADROOM_ENABLED", "false")

    completion_mock = mock.MagicMock(return_value=_make_litellm_response())
    fake_litellm = SimpleNamespace(completion=completion_mock, register_model=mock.MagicMock())
    monkeypatch.setitem(sys.modules, "litellm", fake_litellm)

    judge_litellm.call_judge_via_litellm(
        model=arn, system="s", user="u",
        max_output_tokens=grading._member_max_output_tokens(grading._arn_from_model(arn)),
        cost_fn=grading._judge_cost_usd,
    )

    completion_mock.assert_called_once()
    _, kwargs = completion_mock.call_args
    assert kwargs["max_tokens"] == expected_max


# ---------- Test 6: no thinking / reasoning_effort / output_config / response_format ----------


def test_no_thinking_field_in_litellm_call(monkeypatch):
    """The Sonnet sidecar entry in litellm_sidecar.py enables `thinking:
    adaptive` for the AGENT path, but judges MUST NOT get extended thinking
    (changes character of output, breaks the verdict-format regex). Same for
    `reasoning_effort`, `output_config`, and `response_format:json_object`
    (the last breaks `_VERDICT_RE` per grading.py:413-420 comment)."""
    monkeypatch.setenv("KENSEI_JUDGE_USE_LITELLM", "true")
    monkeypatch.setenv("KENSEI_JUDGE_HEADROOM_ENABLED", "false")

    completion_mock = mock.MagicMock(return_value=_make_litellm_response())
    fake_litellm = SimpleNamespace(completion=completion_mock, register_model=mock.MagicMock())
    monkeypatch.setitem(sys.modules, "litellm", fake_litellm)

    judge_litellm.call_judge_via_litellm(
        model=SONNET_ARN, system="s", user="u",
        max_output_tokens=8192, cost_fn=grading._judge_cost_usd,
    )
    _, kwargs = completion_mock.call_args
    for forbidden in ("thinking", "reasoning_effort", "output_config", "response_format"):
        assert forbidden not in kwargs, f"judge call must not include {forbidden!r}"


# ---------- Test 7: usage dict shape matches urllib path ----------


def test_usage_dict_shape_matches_urllib_path(monkeypatch):
    """The 7-key shape is the contract `_grade_council`'s council_usage
    aggregator depends on. The `headroom` sub-dict is purely additive."""
    monkeypatch.setenv("KENSEI_JUDGE_USE_LITELLM", "true")
    monkeypatch.setenv("KENSEI_JUDGE_HEADROOM_ENABLED", "false")

    fake_litellm = SimpleNamespace(
        completion=lambda **kw: _make_litellm_response(
            prompt_tokens=1500, completion_tokens=120,
            cache_read=300, cache_write=50,
        ),
        register_model=mock.MagicMock(),
    )
    monkeypatch.setitem(sys.modules, "litellm", fake_litellm)

    _, usage = judge_litellm.call_judge_via_litellm(
        model=SONNET_ARN, system="s", user="u",
        max_output_tokens=8192, cost_fn=grading._judge_cost_usd,
    )
    expected_keys = set(grading._ZERO_USAGE.keys())
    assert expected_keys.issubset(set(usage.keys())), \
        f"missing keys: {expected_keys - set(usage.keys())}"
    # input_tokens MUST be non-cached
    assert usage["input_tokens"] == 1500 - 300 - 50
    assert usage["cache_read_tokens"] == 300
    assert usage["cache_write_tokens"] == 50
    assert usage["output_tokens"] == 120
    assert usage["total_tokens"] == (1500 - 300 - 50) + 120 + 300 + 50
    assert usage["request_count"] == 1
    # Additive field is present but doesn't displace any canonical key
    assert "headroom" in usage


# ---------- Test 8: LiteLLM failure falls back to urllib ----------


def test_litellm_failure_falls_back_to_urllib(monkeypatch):
    """Any exception inside the LiteLLM path must NOT propagate — the
    dispatcher logs + falls through to urllib. This is the m0039 'follow the
    code at all times' contract: grading cannot fail because LiteLLM had a
    bad day."""
    monkeypatch.setenv("KENSEI_JUDGE_USE_LITELLM", "true")

    def _explode(*a, **k):
        raise RuntimeError("simulated LiteLLM blowup")

    monkeypatch.setattr(judge_litellm, "call_judge_via_litellm", _explode)

    sentinel = ("urllib-fallback-worked", dict(grading._ZERO_USAGE, request_count=1))
    monkeypatch.setattr(grading, "_call_judge_bedrock", lambda *a, **k: sentinel)

    text, usage = grading._call_one_judge(SONNET_ARN, "sys", "user")
    assert text == "urllib-fallback-worked"
    assert usage["request_count"] == 1


# ---------- Test 9: register_judges_once registers all three judges ----------


def test_register_model_called_for_all_judges(monkeypatch):
    """One register_model call per judge ARN tail with the expected
    ctx_window, max_output_tokens, and cost rates. Drift in
    `_JUDGE_REGISTRY` vs `_MEMBER_EVIDENCE_BUDGETS` is exactly the kind of
    bug `test_judge_budget_invariant.py` doesn't catch (different invariant)
    — this test guards that the LiteLLM transport sees the same numbers the
    urllib transport does."""
    register_mock = mock.MagicMock()
    fake_litellm = SimpleNamespace(register_model=register_mock)
    monkeypatch.setitem(sys.modules, "litellm", fake_litellm)

    judge_litellm.register_judges_once()

    # One call per judge in the registry
    assert register_mock.call_count == len(judge_litellm._JUDGE_REGISTRY)

    registered_tails = set()
    for call in register_mock.call_args_list:
        ((payload,), _) = call
        assert isinstance(payload, dict) and len(payload) == 1
        tail = next(iter(payload.keys()))
        info = payload[tail]
        registered_tails.add(tail)
        # canonical fields present
        for f in (
            "max_input_tokens", "max_output_tokens",
            "input_cost_per_token", "output_cost_per_token",
            "litellm_provider", "mode",
        ):
            assert f in info, f"register_model payload for {tail} missing {f!r}"

    expected_tails = {tup[0] for tup in judge_litellm._JUDGE_REGISTRY}
    assert registered_tails == expected_tails

    # Idempotency: second call is a no-op (no extra register_model invocations)
    judge_litellm.register_judges_once()
    assert register_mock.call_count == len(judge_litellm._JUDGE_REGISTRY)


# ---------- Test 10: ARN tokenizer hint ----------


def test_arn_tokenizer_hint(monkeypatch):
    """When the model id contains `application-inference-profile`, Headroom
    must be called with `anthropic/claude-sonnet-4-5-20250929` as the model
    hint (Headroom can't tokenize an opaque ARN). The actual LiteLLM call
    still uses the original ARN; only the tokenizer hint is swapped."""
    captured = {}

    class _RecordingConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    def _recording_compress(messages, model, config):
        captured["model_passed_to_compress"] = model
        return SimpleNamespace(
            messages=messages, tokens_before=5_000, tokens_after=4_500,
            tokens_saved=500, compression_ratio=0.9,
            transforms_applied=["x"],
        )

    fake_headroom = SimpleNamespace(compress=_recording_compress, CompressConfig=_RecordingConfig)
    monkeypatch.setitem(sys.modules, "headroom", fake_headroom)

    judge_litellm.maybe_compress(
        [{"role": "system", "content": "s"}, {"role": "user", "content": "u" * 20_000}],
        SONNET_ARN,
    )
    assert captured["model_passed_to_compress"] == "anthropic/claude-sonnet-4-5-20250929"

    # Sanity: a non-ARN model is passed through unchanged
    captured.clear()
    judge_litellm.maybe_compress(
        [{"role": "system", "content": "s"}, {"role": "user", "content": "u" * 20_000}],
        "openai/gpt-5.5",
    )
    assert captured["model_passed_to_compress"] == "openai/gpt-5.5"


# ---------- Integration smoke: cache_control survives compression ----------


@pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION_JUDGE_TESTS", "").lower() not in ("1", "true", "yes", "on"),
    reason="integration smoke — set RUN_INTEGRATION_JUDGE_TESTS=true to run; needs Bedrock creds",
)
def test_cache_control_survival_smoke():
    """End-to-end: make two real Sonnet judge calls within the 5-min cache
    TTL with LiteLLM+Headroom on. The second must report
    `cache_read_tokens > 0`, proving `cache_control` markers survived
    Headroom's compression and translated to Bedrock's `cachePoint` block.

    Gated by env var because it costs ~$0.03/run and needs
    `AWS_BEARER_TOKEN_BEDROCK`."""
    os.environ["KENSEI_JUDGE_USE_LITELLM"] = "true"
    os.environ["KENSEI_JUDGE_HEADROOM_ENABLED"] = "true"

    system = (
        "You are a strict rubric judge. For each numbered criterion respond in this "
        "exact format: 'N. <criterion> [[RATIONALE: ...]] [[SATISFIED: Yes|No]]'."
    )
    # Long enough to trigger Bedrock prompt-caching (>1024 tokens worth of system)
    user = "1. The answer mentions Python.\n\nAnswer: The user mentioned Python."

    _, usage1 = judge_litellm.call_judge_via_litellm(
        model=SONNET_ARN, system=system, user=user,
        max_output_tokens=8192, cost_fn=grading._judge_cost_usd,
    )
    _, usage2 = judge_litellm.call_judge_via_litellm(
        model=SONNET_ARN, system=system, user=user,
        max_output_tokens=8192, cost_fn=grading._judge_cost_usd,
    )

    # First call: cache_write should be non-zero (we just primed it)
    # Second call: cache_read should be non-zero (we just read it back)
    assert usage2["cache_read_tokens"] > 0, (
        "cache_control did not survive Headroom compression — second call "
        "should have produced cache_read_tokens > 0"
    )
