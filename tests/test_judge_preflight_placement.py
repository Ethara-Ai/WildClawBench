"""The judge preflights must run on the canonical script/run.sh path.

Both judge preflights used to sit inside `_setup_litellm_and_mocks`'s
self-owned-bridge branch, whose condition includes
`not (shared_mode and shared_cc_bridge)`. script/run.sh bootstraps a SHARED
bridge and exports WCB_SHARED_CC_BRIDGE, so that condition is false for the
parent and for every -P N child: on the only launch shape anybody uses, the
"fail at T+0 before you spend a trajectory" check never executed.

These tests are structural on purpose. The bug was pure placement -- the
preflight code itself was correct and had its own passing unit tests -- so the
only thing that can catch a regression is an assertion about WHERE it lives.
"""
from __future__ import annotations

import ast
import json
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils import grading, judge_litellm  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
RUN_BATCH = REPO / "eval" / "run_batch.py"

SONNET_ARN = (
    "bedrock/arn:aws:bedrock:ap-south-1:426628337772:"
    "application-inference-profile/is9bst5tfadh"
)


def _setup_fn() -> ast.FunctionDef:
    tree = ast.parse(RUN_BATCH.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_setup_litellm_and_mocks":
            return node
    raise AssertionError("_setup_litellm_and_mocks not found in eval/run_batch.py")


def _names(node: ast.AST) -> set[str]:
    return {
        n.id for n in ast.walk(node) if isinstance(n, ast.Name)
    } | {
        alias.name for n in ast.walk(node)
        if isinstance(n, ast.ImportFrom) for alias in n.names
    }


def _self_owned_bridge_block(fn: ast.FunctionDef) -> ast.If:
    src = RUN_BATCH.read_text(encoding="utf-8").splitlines()
    for node in ast.walk(fn):
        if not isinstance(node, ast.If):
            continue
        test = src[node.test.lineno - 1]
        if "shared_mode and shared_cc_bridge" in test and "not (" in test:
            return node
    raise AssertionError("the self-owned-bridge branch was not found")


@pytest.mark.parametrize("preflight", ["preflight_judge_oauth", "preflight_judge_bedrock"])
def test_preflight_is_not_trapped_in_the_self_owned_bridge_branch(preflight):
    fn = _setup_fn()
    block = _self_owned_bridge_block(fn)
    assert preflight not in _names(block), (
        f"{preflight} is inside `if bridge_needed and not (shared_mode and "
        f"shared_cc_bridge)`, which script/run.sh makes FALSE. It would never "
        f"run on the canonical path, in the parent or in any -P N child."
    )


@pytest.mark.parametrize("preflight", ["preflight_judge_oauth", "preflight_judge_bedrock"])
def test_preflight_is_reachable_from_the_function_body(preflight):
    fn = _setup_fn()
    assert preflight in _names(fn), f"{preflight} is not called from setup at all"


def test_both_preflights_are_keyed_on_the_judge_lane():
    fn = _setup_fn()
    src = RUN_BATCH.read_text(encoding="utf-8")
    assert "judge_provider_id == OAUTH" in src
    assert "judge_provider_id == BEDROCK" in src
    assert "judge_provider_id" in _names(fn)


def test_the_bridge_start_block_is_gated_on_bridge_needed_not_use_oauth():
    """Oracle REQUIRED #2: the plan widened where the bridge is NAMED (a pair of
    string assignments), not where start_bridge is CALLED."""
    block = _self_owned_bridge_block(_setup_fn())
    assert "start_bridge" in _names(block), "this is not the block that starts the bridge"
    assert "bridge_needed" in _names(block.test)
    assert "use_oauth" not in _names(block.test)


def test_opus_thinking_probe_stays_on_the_agent_lane():
    """It probes the AGENT's opus path, and the block it lives in now also runs
    for a Bedrock agent whose judge alone is on OAuth."""
    src = RUN_BATCH.read_text(encoding="utf-8")
    assert "if not _skip_thinking_pf and use_oauth:" in src


# ---------------------------------------------------------------------------
# preflight_judge_bedrock behaviour
# ---------------------------------------------------------------------------


@pytest.fixture
def council(monkeypatch):
    monkeypatch.setattr(
        grading, "council_members",
        lambda: [grading.CouncilMember(family="sonnet", model=SONNET_ARN)],
    )
    monkeypatch.setenv("KENSEI_AWS_BEARER_TOKEN", "bearer-xyz")
    monkeypatch.delenv("KENSEI_JUDGE_PREFLIGHT_TIMEOUT", raising=False)
    judge_litellm._TEMP_REJECTED_MODELS.discard(SONNET_ARN)
    return monkeypatch


class _Resp:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, _n=1):
        return b"\x00"


def test_not_configured_returns_ok(monkeypatch):
    monkeypatch.setattr(grading, "council_members", lambda: [])
    ok, detail = judge_litellm.preflight_judge_bedrock()
    assert ok is True
    assert "not configured" in detail


def test_missing_bearer_is_named(council):
    council.delenv("KENSEI_AWS_BEARER_TOKEN", raising=False)
    council.delenv("AWS_BEARER_TOKEN_BEDROCK", raising=False)
    ok, detail = judge_litellm.preflight_judge_bedrock()
    assert ok is False
    assert "KENSEI_AWS_BEARER_TOKEN" in detail


def test_happy_path_uses_bearer_auth_and_max_tokens_1(council, monkeypatch):
    seen: dict = {}

    def _urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["headers"] = dict(req.headers)
        seen["body"] = json.loads(req.data.decode())
        seen["timeout"] = timeout
        return _Resp()

    monkeypatch.setattr("urllib.request.urlopen", _urlopen)
    ok, detail = judge_litellm.preflight_judge_bedrock()
    assert ok is True, detail
    assert seen["headers"]["Authorization"] == "Bearer bearer-xyz"
    assert seen["body"]["inferenceConfig"]["maxTokens"] == 1
    assert seen["body"]["inferenceConfig"]["temperature"] == 0
    assert "ap-south-1" in seen["url"]
    assert seen["timeout"] == 90.0


def test_auth_failure_is_not_retried(council, monkeypatch):
    calls = {"n": 0}

    def _urlopen(req, timeout=None):
        calls["n"] += 1
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", _urlopen)
    ok, detail = judge_litellm.preflight_judge_bedrock()
    assert ok is False
    assert "403" in detail
    assert calls["n"] == 1


def test_timeout_is_retried_once_then_names_the_budget(council, monkeypatch):
    calls = {"n": 0}

    def _urlopen(req, timeout=None):
        calls["n"] += 1
        raise TimeoutError("timed out")

    monkeypatch.setattr("urllib.request.urlopen", _urlopen)
    ok, detail = judge_litellm.preflight_judge_bedrock()
    assert ok is False
    assert calls["n"] == 2
    assert "KENSEI_JUDGE_PREFLIGHT_TIMEOUT" in detail


def test_temperature_rejection_is_learned_before_grade_time(council, monkeypatch):
    import io

    bodies: list[dict] = []
    calls = {"n": 0}

    def _urlopen(req, timeout=None):
        calls["n"] += 1
        bodies.append(json.loads(req.data.decode()))
        if calls["n"] == 1:
            raise urllib.error.HTTPError(
                req.full_url, 400, "Bad Request", {},
                io.BytesIO(b"temperature is not supported"),
            )
        return _Resp()

    monkeypatch.setattr("urllib.request.urlopen", _urlopen)
    ok, _ = judge_litellm.preflight_judge_bedrock()
    assert ok is True
    assert calls["n"] == 2
    assert "temperature" in bodies[0]["inferenceConfig"]
    assert "temperature" not in bodies[1]["inferenceConfig"]
    assert SONNET_ARN in judge_litellm._TEMP_REJECTED_MODELS
    judge_litellm._TEMP_REJECTED_MODELS.discard(SONNET_ARN)


def test_a_broken_roster_is_reported_not_swallowed(monkeypatch):
    def _boom():
        raise RuntimeError("no usable judge remains")

    monkeypatch.setattr(grading, "council_members", _boom)
    ok, detail = judge_litellm.preflight_judge_bedrock()
    assert ok is False
    assert "no usable judge remains" in detail
