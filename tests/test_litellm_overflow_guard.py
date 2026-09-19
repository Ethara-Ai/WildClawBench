"""Unit tests for the 1P context-overflow pre-call guard
(`src/utils/litellm_overflow_guard_callback.py`).

Each test names the production invariant it guards so a failure self-explains:

| # | Invariant guarded |
|---|---|
| 1 | Token estimate counts bare-string content |
| 2 | Token estimate counts list-of-parts content (OpenAI + Anthropic blocks) |
| 3 | Token estimate counts assistant tool_calls function arguments |
| 4 | Token estimate counts tool-role results (incl. nested block content) |
| 5 | Estimate divides by 3.5 — deliberately BELOW the measured 3.695 |
|   | chars/token so the guard over-estimates and fires early |
| 6 | Over threshold -> raises, and the message carries every substring |
|   | openclaw's overflow matcher looks for |
| 7 | The raised object is a fastapi HTTPException with status_code 400 and a |
|   | {"error": {"message": ...}} detail — the ONLY shape litellm 1.88.1 |
|   | forwards to the client verbatim (see module docstring) |
| 8 | Under threshold -> returns `data` unchanged, never raises |
| 9 | Model gate: a non-matching model NEVER raises, however huge the prompt |
| 10| Model gate is env-driven (WCB_OVERFLOW_GUARD_MODELS csv of substrings) |
| 11| Threshold is env-driven (WCB_1P_PROMPT_TOKEN_LIMIT) |
| 12| Malformed / hostile payloads pass through without raising (fail OPEN) |
| 13| Module exposes a singleton that IS a CustomLogger subclass and defines |
|   | async_pre_call_hook on its LEAF class — litellm's dispatch loop skips |
|   | callbacks that only inherit it (proxy/utils.py:1486) |

Pattern follows tests/test_litellm_headroom_callback.py (sys.path shim, autouse
env-clearing fixture, asyncio.run to drive the hook).
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils import litellm_overflow_guard_callback as guard  # noqa: E402

MATCHED_MODEL = "rl-muse-spark-1-2-playground"
UNMATCHED_MODEL = "claude-opus-4-6"

# The four substrings the guard module was written to carry. Section F pins
# them against openclaw's REAL detector and shows only two of them actually
# reach a pattern: this tuple pins the message TEXT, not the match. Do not read
# it as "any of these four triggers compaction" — that claim is false.
OPENCLAW_OVERFLOW_SUBSTRINGS = (
    "context length exceeded",
    "maximum context length",
    "prompt is too long",
    "context_window_exceeded",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ("WCB_OVERFLOW_GUARD_MODELS", "WCB_1P_PROMPT_TOKEN_LIMIT"):
        monkeypatch.delenv(var, raising=False)
    yield


def _call(data, call_type="acompletion"):
    return asyncio.run(
        guard.overflow_guard_instance.async_pre_call_hook(
            user_api_key_dict=object(),
            cache=object(),
            data=data,
            call_type=call_type,
        )
    )


def _big_text(tokens: int) -> str:
    """A string whose estimate is ~`tokens` under the module's 3.5 divisor."""
    return "x" * int(tokens * guard._CHARS_PER_TOKEN)


# ===========================================================================
# Section A — token estimation across content shapes
# ===========================================================================


class TestEstimation:
    def test_bare_string_content(self):
        data = {"messages": [{"role": "user", "content": "a" * 3500}]}
        assert guard.estimate_prompt_tokens(data) == 1000

    def test_divisor_is_below_measured_chars_per_token(self):
        # Real content tokenizes at ~3.695 chars/token; dividing by 3.5 makes
        # the estimate run HIGH on purpose (early fire is safe, late fire
        # reproduces the incident).
        assert guard._CHARS_PER_TOKEN == 3.5
        real_tokens = 10_000
        chars = int(real_tokens * 3.695)
        estimate = guard.estimate_prompt_tokens(
            {"messages": [{"role": "user", "content": "x" * chars}]}
        )
        assert estimate > real_tokens

    def test_openai_list_of_parts_content(self):
        data = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "a" * 1750},
                        {"type": "text", "text": "b" * 1750},
                    ],
                }
            ]
        }
        assert guard.estimate_prompt_tokens(data) == 1000

    def test_anthropic_blocks_count_tool_use_input_and_nested_tool_result(self):
        data = {
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "tu_1", "name": "bash",
                         "input": {"command": "c" * 1750}},
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "tu_1",
                         "content": [{"type": "text", "text": "r" * 1750}]},
                    ],
                },
            ]
        }
        # 1750 + 1750 chars of payload plus the short "bash" name.
        assert guard.estimate_prompt_tokens(data) == pytest.approx(1001, abs=2)

    def test_structural_discriminators_are_not_counted(self):
        # type/id/tool_use_id/cache_control are fixed-size bookkeeping; counting
        # them would make the estimate drift with block COUNT rather than size.
        noisy = {
            "messages": [
                {"role": "user", "content": [
                    {"type": "text", "text": "", "cache_control": {"type": "ephemeral"}}
                ] * 100}
            ]
        }
        assert guard.estimate_prompt_tokens(noisy) == 0

    def test_tool_calls_function_arguments_counted(self):
        data = {
            "messages": [
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {"id": "call_1", "type": "function",
                         "function": {"name": "run", "arguments": "{\"x\": \"" + "y" * 3000 + "\"}"}},
                    ],
                }
            ]
        }
        assert guard.estimate_prompt_tokens(data) > 800

    def test_tool_role_result_message_counted(self):
        data = {
            "messages": [
                {"role": "tool", "tool_call_id": "call_1", "content": "z" * 3500},
            ]
        }
        assert guard.estimate_prompt_tokens(data) == 1000

    def test_system_and_tools_schema_counted(self):
        # Both are genuine prompt tokens on the wire; omitting them would eat
        # the ~7K-token margin between the 255K default and the 262,022 relay
        # ceiling.
        assert guard.estimate_prompt_tokens({"system": "s" * 3500}) == 1000
        assert guard.estimate_prompt_tokens(
            {"tools": [{"type": "function",
                        "function": {"name": "n", "description": "d" * 3499}}]}
        ) == 1000

    def test_empty_payload_is_zero(self):
        assert guard.estimate_prompt_tokens({}) == 0
        assert guard.estimate_prompt_tokens({"messages": []}) == 0


# ===========================================================================
# Section B — threshold behaviour
# ===========================================================================


class TestThreshold:
    def test_over_threshold_raises_with_openclaw_matchable_message(self):
        data = {"model": MATCHED_MODEL,
                "messages": [{"role": "user", "content": _big_text(300_000)}]}
        with pytest.raises(Exception) as excinfo:
            _call(data)
        text = str(getattr(excinfo.value, "detail", excinfo.value))
        lowered = text.lower()
        for needle in OPENCLAW_OVERFLOW_SUBSTRINGS:
            assert needle in lowered, f"{needle!r} missing from {text!r}"

    def test_raised_exception_shape_is_the_one_litellm_forwards(self):
        # litellm 1.88.1 only relays the message verbatim for an HTTPException
        # whose detail is {"error": {"message": ...}} (see module docstring);
        # a returned str would become a RejectedRequestError -> HTTP 200.
        data = {"model": MATCHED_MODEL,
                "messages": [{"role": "user", "content": _big_text(300_000)}]}
        with pytest.raises(guard.HTTPException) as excinfo:
            _call(data)
        exc = excinfo.value
        assert exc.status_code == 400
        assert isinstance(exc.detail, dict)
        inner = exc.detail["error"]
        assert isinstance(inner, dict)
        assert isinstance(inner["message"], str)
        assert inner["code"] == "context_length_exceeded"

    def test_message_reports_estimate_and_limit(self):
        data = {"model": MATCHED_MODEL,
                "messages": [{"role": "user", "content": _big_text(300_000)}]}
        estimated = guard.estimate_prompt_tokens(data)
        with pytest.raises(guard.HTTPException) as excinfo:
            _call(data)
        message = excinfo.value.detail["error"]["message"]
        assert str(estimated) in message
        assert str(guard._DEFAULT_PROMPT_TOKEN_LIMIT) in message

    def test_under_threshold_passes_data_through_unchanged(self):
        data = {"model": MATCHED_MODEL,
                "messages": [{"role": "user", "content": _big_text(1000)}]}
        assert _call(data) is data

    def test_exactly_at_limit_does_not_raise(self):
        # `<=` boundary: the limit itself is still an allowed prompt.
        data = {"model": MATCHED_MODEL,
                "messages": [{"role": "user",
                              "content": _big_text(guard._DEFAULT_PROMPT_TOKEN_LIMIT)}]}
        assert guard.estimate_prompt_tokens(data) == guard._DEFAULT_PROMPT_TOKEN_LIMIT
        assert _call(data) is data

    def test_limit_is_env_overridable(self, monkeypatch):
        monkeypatch.setenv("WCB_1P_PROMPT_TOKEN_LIMIT", "1000")
        data = {"model": MATCHED_MODEL,
                "messages": [{"role": "user", "content": _big_text(5000)}]}
        with pytest.raises(guard.HTTPException):
            _call(data)

    @pytest.mark.parametrize("bad", ["", "   ", "not-a-number", "0", "-5"])
    def test_invalid_limit_falls_back_to_default(self, monkeypatch, bad):
        monkeypatch.setenv("WCB_1P_PROMPT_TOKEN_LIMIT", bad)
        assert guard._prompt_token_limit() == guard._DEFAULT_PROMPT_TOKEN_LIMIT
        data = {"model": MATCHED_MODEL,
                "messages": [{"role": "user", "content": _big_text(1000)}]}
        assert _call(data) is data


# ===========================================================================
# Section C — model gate
# ===========================================================================


class TestModelGate:
    def test_non_matching_model_never_raises_even_when_huge(self):
        data = {"model": UNMATCHED_MODEL,
                "messages": [{"role": "user", "content": _big_text(5_000_000)}]}
        assert _call(data) is data

    @pytest.mark.parametrize("model", [
        "rl-muse-spark-1-2-playground",
        "openai/rl-muse-spark-1-2-playground",
        "RL-MUSE-SPARK-1-2",
    ])
    def test_default_substring_matches_the_1p_family(self, model):
        assert guard._model_matches(model)

    @pytest.mark.parametrize("model", [
        UNMATCHED_MODEL, "gpt-5.5", "", None, 123, {"a": 1},
    ])
    def test_default_substring_rejects_everything_else(self, model):
        assert not guard._model_matches(model)

    def test_model_gate_is_env_overridable_csv(self, monkeypatch):
        monkeypatch.setenv("WCB_OVERFLOW_GUARD_MODELS", "foo-model, bar-model")
        assert guard._model_matches("vendor/bar-model-v2")
        assert not guard._model_matches(MATCHED_MODEL)

    def test_blank_env_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("WCB_OVERFLOW_GUARD_MODELS", "   ")
        assert guard._model_matches(MATCHED_MODEL)

    def test_missing_model_key_passes_through(self):
        data = {"messages": [{"role": "user", "content": _big_text(500_000)}]}
        assert _call(data) is data


# ===========================================================================
# Section D — fail-open on malformed input
# ===========================================================================


class TestFailOpen:
    @pytest.mark.parametrize("data", [
        {"model": MATCHED_MODEL, "messages": None},
        {"model": MATCHED_MODEL, "messages": "not-a-list"},
        {"model": MATCHED_MODEL, "messages": [None, 42, "raw", ["nested"]]},
        {"model": MATCHED_MODEL, "messages": [{"role": "user"}]},
        {"model": MATCHED_MODEL, "messages": [{"content": {"weird": object()}}]},
        {"model": MATCHED_MODEL, "messages": [{"content": [1, 2, 3]}]},
        {"model": MATCHED_MODEL, "messages": [{"tool_calls": "nope"}]},
        {"model": MATCHED_MODEL, "messages": [{"tool_calls": [{"function": None}]}]},
        {"model": MATCHED_MODEL, "system": 5, "tools": object()},
        {"model": MATCHED_MODEL},
    ])
    def test_malformed_payloads_pass_through_without_raising(self, data):
        assert _call(data) is data

    def test_non_dict_data_passes_through(self):
        sentinel = ["not", "a", "dict"]
        assert _call(sentinel) is sentinel

    def test_self_referential_content_does_not_hang(self):
        loop: list = []
        loop.append(loop)
        data = {"model": MATCHED_MODEL, "messages": [{"content": loop}]}
        assert _call(data) is data

    def test_estimation_error_is_swallowed_and_request_passes(self, monkeypatch):
        def _boom(_data):
            raise RuntimeError("synthetic estimator failure")

        monkeypatch.setattr(guard, "estimate_prompt_tokens", _boom)
        data = {"model": MATCHED_MODEL,
                "messages": [{"role": "user", "content": _big_text(500_000)}]}
        assert _call(data) is data


# ===========================================================================
# Section E — litellm dispatch contract
# ===========================================================================


class TestDispatchContract:
    def test_singleton_is_a_customlogger_subclass(self):
        assert isinstance(guard.overflow_guard_instance, guard.CustomLogger)

    def test_hook_is_defined_on_the_leaf_class(self):
        # litellm 1.88.1 proxy/utils.py:1486 + :1640 gate dispatch on
        # `"async_pre_call_hook" in vars(type(cb))`. An inherited hook is
        # silently skipped, which would make this guard a no-op in production.
        assert "async_pre_call_hook" in vars(guard.OverflowGuard)

    def test_hook_accepts_the_canonical_4_keyword_signature(self):
        data = {"model": UNMATCHED_MODEL, "messages": []}
        result = asyncio.run(
            guard.overflow_guard_instance.async_pre_call_hook(
                user_api_key_dict=None,
                cache=None,
                data=data,
                call_type="acompletion",
            )
        )
        assert result is data

    @pytest.mark.parametrize("call_type", [
        "completion", "acompletion", "atext_completion", "anthropic_messages",
    ])
    def test_guard_fires_on_every_chat_shaped_call_type(self, call_type):
        data = {"model": MATCHED_MODEL,
                "messages": [{"role": "user", "content": _big_text(300_000)}]}
        with pytest.raises(guard.HTTPException):
            _call(data, call_type=call_type)


# ===========================================================================
# Section F — sidecar wiring (kept here, not in test_litellm_sidecar_config.py,
# so the guard's coverage lives with the guard)
# ===========================================================================

from src.utils import litellm_sidecar as sidecar  # noqa: E402

_GUARD_CB = "litellm_overflow_guard_callback.overflow_guard_instance"


def _callbacks(cfg_yaml: str) -> list[str]:
    import yaml

    doc = yaml.safe_load(cfg_yaml)
    return doc.get("litellm_settings", {}).get("callbacks", []) or []


class TestSidecarWiring:
    def test_callback_absent_by_default(self):
        cfg = sidecar.build_litellm_config_yaml(meta_api_key="k", meta_model=MATCHED_MODEL)
        assert _GUARD_CB not in _callbacks(cfg)

    def test_callback_registered_when_enabled(self):
        cfg = sidecar.build_litellm_config_yaml(
            meta_api_key="k", meta_model=MATCHED_MODEL,
            enable_overflow_guard_callback=True,
        )
        assert _GUARD_CB in _callbacks(cfg)

    def test_guard_is_registered_after_headroom(self):
        # Ordering is load-bearing: headroom SHRINKS the prompt, so the guard
        # must see the post-compression payload or it would reject requests
        # compression would have made fit.
        cbs = _callbacks(sidecar.build_litellm_config_yaml(
            meta_api_key="k", meta_model=MATCHED_MODEL,
            enable_usage_callback=True,
            enable_headroom_callback=True,
            enable_overflow_guard_callback=True,
        ))
        assert cbs.index(_GUARD_CB) > cbs.index(
            "litellm_headroom_callback.headroom_callback_instance"
        )

    def test_enabled_by_default_for_1p_routes(self, monkeypatch):
        monkeypatch.delenv("WCB_OVERFLOW_GUARD", raising=False)
        assert sidecar.overflow_guard_enabled("key", MATCHED_MODEL)

    def test_disabled_without_a_1p_route(self, monkeypatch):
        monkeypatch.delenv("WCB_OVERFLOW_GUARD", raising=False)
        assert not sidecar.overflow_guard_enabled("", MATCHED_MODEL)
        assert not sidecar.overflow_guard_enabled("key", "")

    @pytest.mark.parametrize("kill", ["0", "false", "no", "off", "OFF"])
    def test_kill_switch(self, monkeypatch, kill):
        monkeypatch.setenv("WCB_OVERFLOW_GUARD", kill)
        assert not sidecar.overflow_guard_enabled("key", MATCHED_MODEL)


class TestStartLitellmMount:
    def _argv(self, monkeypatch, **kwargs) -> list[str]:
        captured: list[list[str]] = []

        class _R:
            returncode = 0
            stderr = ""
            stdout = ""

        def _fake_run(cmd, *a, **kw):
            captured.append(list(cmd))
            return _R()

        monkeypatch.setattr(sidecar.subprocess, "run", _fake_run)
        monkeypatch.setattr(sidecar, "connect_default_bridge", lambda *a, **k: None)
        sidecar.start_litellm(
            container_name="c", network="n", host_config_path="/tmp/cfg.yaml",
            master_key="mk", **kwargs,
        )
        return next(c for c in captured if "run" in c and "-d" in c)

    def test_module_mounted_readonly_at_app_path(self, monkeypatch):
        argv = self._argv(monkeypatch, overflow_guard_callback_host_path="/host/guard.py")
        assert "/host/guard.py:/app/litellm_overflow_guard_callback.py:ro" in argv

    def test_no_mount_when_path_empty(self, monkeypatch):
        argv = self._argv(monkeypatch)
        assert not any("litellm_overflow_guard_callback" in a for a in argv)

    def test_knobs_forwarded_only_when_set(self, monkeypatch):
        monkeypatch.delenv("WCB_OVERFLOW_GUARD_MODELS", raising=False)
        monkeypatch.delenv("WCB_1P_PROMPT_TOKEN_LIMIT", raising=False)
        argv = self._argv(monkeypatch, overflow_guard_callback_host_path="/host/guard.py")
        assert not any(a.startswith("WCB_1P_PROMPT_TOKEN_LIMIT") for a in argv)

        monkeypatch.setenv("WCB_1P_PROMPT_TOKEN_LIMIT", "250000")
        monkeypatch.setenv("WCB_OVERFLOW_GUARD_MODELS", "rl-muse,other")
        argv = self._argv(monkeypatch, overflow_guard_callback_host_path="/host/guard.py")
        assert "WCB_1P_PROMPT_TOKEN_LIMIT=250000" in argv
        assert "WCB_OVERFLOW_GUARD_MODELS=rl-muse,other" in argv


# ===========================================================================
# Section F — the synthetic message vs openclaw's REAL overflow detector
# ===========================================================================
#
# PROVENANCE OF THE MIRRORED LIST BELOW — re-verify before editing it
# ---------------------------------------------------------------------------
#   Source file : /lib/node_modules/@mariozechner/pi-ai/dist/utils/overflow.js
#   Package     : @mariozechner/pi-ai 0.57.1 (a dependency of openclaw)
#   Consumer    : openclaw 2026.3.11
#   Image       : wildclawbench-ubuntu:v1.4 (id 631bcdbe91c8, built 2026-09-15)
#   Extracted   : docker create + docker cp of the image's /lib/node_modules
#   Count       : 17 patterns, mirrored below in source order
#
# To re-extract:
#   cid=$(docker create wildclawbench-ubuntu:v1.4) \
#     && docker cp "$cid":/lib/node_modules /tmp/openclaw-src \
#     && docker rm "$cid" \
#     && sed -n '/^const OVERFLOW_PATTERNS/,/^];/p' \
#          /tmp/openclaw-src/node_modules/@mariozechner/pi-ai/dist/utils/overflow.js
#
# WHY THIS SECTION EXISTS
# ---------------------------------------------------------------------------
# src/utils/litellm_overflow_guard_callback.py exists for exactly one reason:
# the 1P relay's own 400 is generic prose that matches NONE of these 17
# patterns, so openclaw never compacts and the run dies. The guard therefore
# hand-crafts a replacement message shaped to trip the detector. Until this
# section landed, nothing anywhere pinned that message against the real list —
# the only assertion was that the message contains four substrings the guard's
# own comment nominated, which is circular: the test read the message and the
# message was written from the comment, and neither had ever been checked
# against openclaw. Trimming or rewording the message could have dropped the
# detector to zero matches with a fully green suite.
#
# It also caught the comment being wrong. Of those four nominated substrings
# only TWO reach a real pattern; see test_the_two_decoy_substrings_match_nothing
# for why the other two miss by a preposition and a prefix.
#
# Matching semantics, from isContextOverflow() in the same file: each entry is
# a JS RegExp with the /i flag, applied with .test() (unanchored search) over
# `message.errorMessage`, combined with .some() — so ONE hit is sufficient.
# re.search(..., re.IGNORECASE) is the faithful Python equivalent; every
# pattern below is plain enough that JS and Python regex syntax agree.

OPENCLAW_OVERFLOW_PATTERNS = (
    (r"prompt is too long", "Anthropic"),
    (r"input is too long for requested model", "Amazon Bedrock"),
    (r"exceeds the context window", "OpenAI (Completions & Responses)"),
    (r"input token count.*exceeds the maximum", "Google (Gemini)"),
    (r"maximum prompt length is \d+", "xAI (Grok)"),
    (r"reduce the length of the messages", "Groq"),
    (r"maximum context length is \d+ tokens", "OpenRouter (all backends)"),
    (r"exceeds the limit of \d+", "GitHub Copilot"),
    (r"exceeds the available context size", "llama.cpp server"),
    (r"greater than the context length", "LM Studio"),
    (r"context window exceeds limit", "MiniMax"),
    (r"exceeded model token limit", "Kimi For Coding"),
    (r"too large for model with \d+ maximum context length", "Mistral"),
    (r"model_context_window_exceeded", "z.ai"),
    (r"context[_ ]length[_ ]exceeded", "generic fallback"),
    (r"too many tokens", "generic fallback"),
    (r"token limit exceeded", "generic fallback"),
)

# The patterns the guard's message is actually load-bearing on, by index into
# the tuple above. Asserted exactly — a message that starts matching MORE
# patterns is fine in production but means this pin is stale, and a message
# that matches FEWER is the silent-death regression this section exists to
# catch.
EXPECTED_MATCHING_PATTERNS = {
    0: r"prompt is too long",
    14: r"context[_ ]length[_ ]exceeded",
}

# sha256 over the mirrored patterns, joined by \n. Not a security control: it
# is a tripwire, so that editing the list cannot be done casually without
# reading the provenance block above and re-running the extraction.
OPENCLAW_PATTERNS_FINGERPRINT = (
    "6f27b025c8be1576d24a323f3bf922169a52666f260b484c944a4ed74fd92285"
)


def _matching_patterns(message: str) -> dict:
    return {
        i: pat
        for i, (pat, _vendor) in enumerate(OPENCLAW_OVERFLOW_PATTERNS)
        if re.search(pat, message, re.IGNORECASE)
    }


def _synthetic_message() -> str:
    return guard._overflow_message("rl-muse-spark-1-2-playground", 300_000, 255_000)


class TestSyntheticMessageAgainstRealOpenclawPatterns:
    def test_mirror_has_the_seventeen_patterns_that_were_extracted(self):
        assert len(OPENCLAW_OVERFLOW_PATTERNS) == 17

    def test_mirrored_list_is_unchanged_since_provenance_was_verified(self):
        # Tripwire. If this fails you edited OPENCLAW_OVERFLOW_PATTERNS: re-run
        # the docker extraction in the provenance block above, confirm the
        # image tag and package version still match what is recorded there,
        # update BOTH the comment block and this digest, and re-check
        # EXPECTED_MATCHING_PATTERNS — the match set is what actually protects
        # overflow recovery, and it does not follow from the count.
        digest = hashlib.sha256(
            "\n".join(p for p, _ in OPENCLAW_OVERFLOW_PATTERNS).encode()
        ).hexdigest()
        assert digest == OPENCLAW_PATTERNS_FINGERPRINT, (
            "openclaw overflow pattern mirror changed; re-verify provenance "
            "(wildclawbench-ubuntu:v1.4 -> @mariozechner/pi-ai 0.57.1 "
            "dist/utils/overflow.js) and update the recorded digest"
        )

    def test_synthetic_message_matches_at_least_one_real_pattern(self):
        # The whole mechanism in one assertion: zero matches means openclaw
        # never compacts and the run dies at the relay ceiling.
        hits = _matching_patterns(_synthetic_message())
        assert hits, (
            "guard message matches NONE of openclaw's 17 overflow patterns - "
            "native compaction will never fire"
        )

    def test_synthetic_message_matches_exactly_the_expected_patterns(self):
        assert _matching_patterns(_synthetic_message()) == EXPECTED_MATCHING_PATTERNS

    @pytest.mark.parametrize("index,pattern", sorted(EXPECTED_MATCHING_PATTERNS.items()))
    def test_each_expected_pattern_individually_matches(self, index, pattern):
        assert OPENCLAW_OVERFLOW_PATTERNS[index][0] == pattern
        assert re.search(pattern, _synthetic_message(), re.IGNORECASE)

    def test_the_two_decoy_substrings_match_nothing(self):
        # The guard's comment nominated four substrings as "openclaw's overflow
        # matcher". Two of them are decorative: they are real text in the
        # message but no real pattern can see them.
        #
        #   "maximum context length"  - the real pattern is
        #       /maximum context length is \d+ tokens/i
        #     and the message says "maximum context length OF 255000 tokens".
        #     One preposition away from matching.
        #
        #   "context_window_exceeded" - the real pattern is
        #       /model_context_window_exceeded/i
        #     and the message emits the bare token in parentheses, with no
        #     "model_" prefix.
        #
        # Pinned as a NEGATIVE so the failure mode is legible: if a future
        # openclaw adds a pattern these do reach, this test fails and tells the
        # reader to promote them into EXPECTED_MATCHING_PATTERNS rather than
        # leaving the message's true coverage a mystery.
        for decoy in ("maximum context length", "context_window_exceeded"):
            assert decoy in _synthetic_message()
            assert not _matching_patterns(decoy), (
                f"{decoy!r} now reaches a real pattern - update "
                "EXPECTED_MATCHING_PATTERNS and the guard module comment"
            )

    def test_the_generic_relay_400_still_matches_nothing(self):
        # The premise of the entire module. If this ever starts matching, the
        # guard is redundant and should be reconsidered rather than maintained.
        relay_400 = (
            "The request contains invalid parameters. Check the request body "
            "for any errors or inconsistencies."
        )
        assert not _matching_patterns(relay_400)

    def test_match_is_case_insensitive_like_the_js_flag(self):
        # Every mirrored pattern carries /i upstream; a message that only
        # matched in one casing would be a mirror bug, not a real match.
        assert _matching_patterns(_synthetic_message().upper()) == EXPECTED_MATCHING_PATTERNS
