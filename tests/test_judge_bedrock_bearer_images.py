"""A Bedrock judge grades an image-bearing chunk with ONLY a bearer token.

The live finding this pins (2026-09-22, gama): attaching pixels forces the
LiteLLM transport, LiteLLM's Bedrock handler resolves boto credentials before
it looks for a bearer, and on a host whose only AWS identity is an EC2
instance role it SigV4-signs with that role. Bedrock answers "Authentication
failed" for a credential the run never meant to use, and with the judge lane
believed to be OAuth the no-fallback guard then forbade the urllib path that
would have worked. Every criterion abstained.

Two independent repairs, both pinned here, because the box that runs the judge
is the HOST and requirements.txt is explicit that litellm lives in the Docker
image, not on the host:

  1. the LiteLLM lane hands the bearer over as `api_key`, taking litellm's
     Authorization: Bearer branch instead of SigV4;
  2. the urllib Converse lane carries image blocks itself, so a host with no
     litellm at all still grades the pixels.

No network: both transports are faked.
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils import auth_provider as ap  # noqa: E402
from src.utils import grading, judge_litellm  # noqa: E402

SONNET_ARN = (
    "bedrock/arn:aws:bedrock:ap-south-1:426628337772:"
    "application-inference-profile/is9bst5tfadh"
)
IMAGES = [
    {"name": "hero.png", "media_type": "image/png", "b64": "UE5HQg=="},
    {"name": "chart.jpg", "media_type": "image/jpeg", "b64": "SlBFRw=="},
]
VERDICT = "1. c [[RATIONALE: r]] [[SATISFIED: Yes]] [[TRUNCATION_AFFECTED: No]]"

_ENV = (
    "WCB_AUTH_PROVIDER",
    "WCB_JUDGE_AUTH_PROVIDER",
    "WCB_USE_CLAUDE_OAUTH",
    "WCB_CC_ACCOUNT_POOL",
    "KENSEI_JUDGE_OAUTH_BRIDGE_URL",
    "KENSEI_JUDGE_USE_LITELLM",
    "KENSEI_AWS_BEARER_TOKEN",
    "AWS_BEARER_TOKEN_BEDROCK",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_PROFILE",
)


@pytest.fixture
def bearer_only_box(monkeypatch):
    """The gama shape: a bearer token and nothing else. No keys, no profile."""
    for k in _ENV:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("KENSEI_AWS_BEARER_TOKEN", "bedrock-bearer-abc123")
    monkeypatch.setenv("WCB_AUTH_PROVIDER", ap.OAUTH)
    monkeypatch.setenv("WCB_JUDGE_AUTH_PROVIDER", ap.BEDROCK)
    monkeypatch.setenv("KENSEI_JUDGE_OAUTH_BRIDGE_URL", "http://127.0.0.1:34567")
    monkeypatch.setenv("KENSEI_JUDGE_USE_LITELLM", "1")
    judge_litellm._registered_tails.clear()
    return monkeypatch


def _fake_litellm(seen: list[dict]):
    def _completion(**kw):
        seen.append(kw)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=VERDICT))],
            usage={"prompt_tokens": 100, "completion_tokens": 10},
        )

    return SimpleNamespace(completion=_completion, register_model=lambda *a, **k: None)


# ---------------------------------------------------------------------------
# Repair 1 -- the LiteLLM lane authenticates with the bearer, not the role
# ---------------------------------------------------------------------------


def test_litellm_bedrock_lane_sends_the_bearer_as_api_key(bearer_only_box, monkeypatch):
    seen: list[dict] = []
    monkeypatch.setitem(sys.modules, "litellm", _fake_litellm(seen))
    judge_litellm.call_judge_via_litellm(
        model=SONNET_ARN, system="s", user="u", max_output_tokens=64,
        cost_fn=lambda *a, **k: (0.5, True), family="sonnet", images=IMAGES,
    )
    assert len(seen) == 1
    kw = seen[0]
    assert kw["api_key"] == "bedrock-bearer-abc123"
    assert kw["aws_region_name"] == "ap-south-1"
    assert "api_base" not in kw, "a Bedrock judge must never carry the bridge base"


def test_bearer_is_also_promoted_into_the_env_var(bearer_only_box, monkeypatch):
    import os

    seen: list[dict] = []
    monkeypatch.setitem(sys.modules, "litellm", _fake_litellm(seen))
    judge_litellm.call_judge_via_litellm(
        model=SONNET_ARN, system="s", user="u", max_output_tokens=64,
        cost_fn=lambda *a, **k: (0.5, True), family="sonnet",
    )
    assert os.environ["AWS_BEARER_TOKEN_BEDROCK"] == "bedrock-bearer-abc123"


def test_oauth_bridge_lane_does_not_get_an_aws_api_key(monkeypatch):
    for k in _ENV:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("KENSEI_AWS_BEARER_TOKEN", "bedrock-bearer-abc123")
    monkeypatch.setenv("WCB_AUTH_PROVIDER", ap.BEDROCK)
    monkeypatch.setenv("WCB_JUDGE_AUTH_PROVIDER", ap.OAUTH)
    monkeypatch.setenv("KENSEI_JUDGE_OAUTH_BRIDGE_URL", "http://127.0.0.1:34567")
    monkeypatch.setenv("WCB_CC_STUB_KEY", "sk-wcb-oauth-stub")
    judge_litellm._registered_tails.clear()
    seen: list[dict] = []
    monkeypatch.setitem(sys.modules, "litellm", _fake_litellm(seen))
    judge_litellm.call_judge_via_litellm(
        model=SONNET_ARN, system="s", user="u", max_output_tokens=64,
        cost_fn=lambda *a, **k: (0.5, True), family="sonnet", images=IMAGES,
    )
    kw = seen[0]
    assert kw["api_base"] == "http://127.0.0.1:34567"
    assert kw["api_key"] == "sk-wcb-oauth-stub"
    assert "aws_region_name" not in kw


# ---------------------------------------------------------------------------
# Repair 2 -- the urllib Converse lane carries the pixels
# ---------------------------------------------------------------------------


def test_converse_user_content_puts_images_before_the_text():
    blocks = grading._converse_user_content("grade this", IMAGES)
    assert blocks == [
        {"image": {"format": "png", "source": {"bytes": "UE5HQg=="}}},
        {"image": {"format": "jpeg", "source": {"bytes": "SlBFRw=="}}},
        {"text": "grade this"},
    ]


def test_converse_user_content_is_unchanged_without_images():
    assert grading._converse_user_content("grade this", None) == [{"text": "grade this"}]
    assert grading._converse_user_content("grade this", []) == [{"text": "grade this"}]


def test_converse_user_content_drops_unsupported_media():
    blocks = grading._converse_user_content(
        "t", [{"media_type": "image/tiff", "b64": "x"}, {"media_type": "image/png", "b64": ""}]
    )
    assert blocks == [{"text": "t"}]


def test_bedrock_judge_with_images_grades_on_a_bearer_only_box(
    bearer_only_box, monkeypatch
):
    """THE acceptance case. litellm is not importable on this host, so the
    dispatcher falls through to urllib -- which must now authenticate with the
    bearer AND carry the pixels."""
    fake = types.ModuleType("src.utils.judge_litellm")

    def _no_litellm(**kwargs):
        raise ModuleNotFoundError("No module named 'litellm'")

    fake.call_judge_via_litellm = _no_litellm
    import src.utils as _su

    monkeypatch.setitem(sys.modules, "src.utils.judge_litellm", fake)
    monkeypatch.setattr(_su, "judge_litellm", fake, raising=False)

    posted: dict = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, _n=8192):
            return b""

    def _urlopen(req, timeout=120):
        posted["url"] = req.full_url
        posted["headers"] = dict(req.headers)
        posted["body"] = json.loads(req.data.decode())
        return _Resp()

    monkeypatch.setattr("urllib.request.urlopen", _urlopen)
    monkeypatch.setattr(grading, "_member_max_output_tokens", lambda *a, **k: 8192)
    monkeypatch.setattr(
        grading, "_judge_cost_usd", lambda *a, **k: (0.25, True)
    )
    monkeypatch.setattr(grading, "iter_eventstream", None, raising=False)
    monkeypatch.setitem(
        sys.modules, "src.utils.bedrock_eventstream",
        SimpleNamespace(iter_eventstream=lambda chunks: [
            ("contentBlockDelta", {"delta": {"text": VERDICT}}),
            ("metadata", {"usage": {"inputTokens": 120, "outputTokens": 9}}),
        ]),
    )

    raw, usage = grading._call_one_judge(SONNET_ARN, "sys", "user", "sonnet", IMAGES)

    assert raw == VERDICT
    assert usage["input_tokens"] == 120
    assert posted["headers"]["Authorization"] == "Bearer bedrock-bearer-abc123"
    content = posted["body"]["messages"][0]["content"]
    assert [b for b in content if "image" in b], "the pixels never reached Bedrock"
    assert content[-1] == {"text": "user"}
    assert len([b for b in content if "image" in b]) == 2
