from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("httpx")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.utils.claude_oauth import profile as profile_mod  # noqa: E402
from src.utils.claude_oauth.credentials import (  # noqa: E402
    CredentialsError,
    TransientCredentialsError,
)
from src.utils.claude_oauth.profile import (  # noqa: E402
    PROFILE_ENDPOINT,
    AccountProfile,
    ProfileError,
    fetch_account_profile,
    get_claude_account_info,
    reset_account_info_cache,
)

SAMPLE_PAYLOAD = {
    "account": {
        "uuid": "c5802e58-1111-2222-3333-444455556666",
        "full_name": "Madhur",
        "display_name": "Madhur B",
        "email": "madhur@example.com",
        "has_claude_max": True,
        "has_claude_pro": False,
    },
    "organization": {
        "uuid": "org-9999",
        "name": "Kensei",
        "subscription_status": "active",
        "rate_limit_tier": "default_claude_max_20x",
    },
    "application": {"name": "Claude Code", "slug": "claude-code"},
}


class _FakeResp:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json body")
        return self._payload


class _FakeClient:
    responses: list = []
    calls: list = []

    def __init__(self, *a, **k):
        self.kwargs = k
        _FakeClient.calls.append({"init": k})

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, headers=None, **k):
        _FakeClient.calls.append({"url": url, "headers": headers or {}})
        item = _FakeClient.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class _StubSource:
    def __init__(self, token="sk-ant-oat01-test", error=None):
        self._token = token
        self._error = error

    def get_access_token(self):
        if self._error is not None:
            raise self._error
        return self._token


@pytest.fixture
def patch_httpx(monkeypatch):
    _FakeClient.responses = []
    _FakeClient.calls = []
    monkeypatch.setattr(profile_mod.httpx, "Client", _FakeClient)
    return _FakeClient


@pytest.fixture(autouse=True)
def clear_cache():
    reset_account_info_cache()
    yield
    reset_account_info_cache()


def test_from_payload_maps_documented_shape():
    prof = AccountProfile.from_payload(SAMPLE_PAYLOAD)
    assert prof.account_uuid == "c5802e58-1111-2222-3333-444455556666"
    assert prof.name == "Madhur"
    assert prof.email == "madhur@example.com"
    assert prof.organization_uuid == "org-9999"
    assert prof.subscription_status == "active"
    assert prof.rate_limit_tier == "default_claude_max_20x"
    assert prof.has_claude_max is True
    assert prof.has_claude_pro is False


def test_from_payload_falls_back_to_display_name():
    payload = {"account": {"uuid": "u1", "display_name": "Only Display"}}
    assert AccountProfile.from_payload(payload).name == "Only Display"


def test_from_payload_tolerates_missing_sections():
    prof = AccountProfile.from_payload({"account": {"uuid": "u1"}})
    assert prof.account_uuid == "u1"
    assert prof.organization_uuid == ""
    assert prof.rate_limit_tier == ""


def test_from_payload_rejects_non_object():
    with pytest.raises(ProfileError):
        AccountProfile.from_payload(["not", "an", "object"])


def test_to_dict_hides_raw_by_default():
    prof = AccountProfile.from_payload(SAMPLE_PAYLOAD)
    assert "raw" not in prof.to_dict()
    assert prof.to_dict(include_raw=True)["raw"] == SAMPLE_PAYLOAD


def test_fetch_sends_oauth_headers_to_documented_endpoint(patch_httpx):
    patch_httpx.responses = [_FakeResp(200, SAMPLE_PAYLOAD)]
    prof = fetch_account_profile(_StubSource("sk-ant-oat01-abc"))

    assert prof.email == "madhur@example.com"
    call = [c for c in patch_httpx.calls if "url" in c][0]
    assert call["url"] == PROFILE_ENDPOINT
    assert call["headers"]["authorization"] == "Bearer sk-ant-oat01-abc"
    assert call["headers"]["anthropic-beta"] == profile_mod.OAUTH_BETA
    assert call["headers"]["anthropic-version"] == profile_mod.ANTHROPIC_VERSION


@pytest.mark.parametrize("status", [401, 403])
def test_fetch_maps_auth_failures_to_actionable_error(patch_httpx, status):
    patch_httpx.responses = [_FakeResp(status, None, "denied")]
    with pytest.raises(ProfileError) as exc:
        fetch_account_profile(_StubSource())
    assert "claude login" in str(exc.value)


def test_fetch_reports_other_http_errors(patch_httpx):
    patch_httpx.responses = [_FakeResp(500, None, "upstream boom")]
    with pytest.raises(ProfileError) as exc:
        fetch_account_profile(_StubSource())
    assert "500" in str(exc.value)
    assert "upstream boom" in str(exc.value)


def test_fetch_rejects_non_json_body(patch_httpx):
    patch_httpx.responses = [_FakeResp(200, None, "<html>")]
    with pytest.raises(ProfileError) as exc:
        fetch_account_profile(_StubSource())
    assert "not JSON" in str(exc.value)


def test_fetch_requires_account_uuid(patch_httpx):
    patch_httpx.responses = [_FakeResp(200, {"account": {"email": "x@y.z"}})]
    with pytest.raises(ProfileError) as exc:
        fetch_account_profile(_StubSource())
    assert "account.uuid" in str(exc.value)


def test_fetch_wraps_transport_failure(patch_httpx):
    patch_httpx.responses = [RuntimeError("connection reset")]
    with pytest.raises(ProfileError) as exc:
        fetch_account_profile(_StubSource())
    assert "connection reset" in str(exc.value)


def test_fetch_wraps_transient_credentials_error(patch_httpx):
    source = _StubSource(error=TransientCredentialsError("refresh 503"))
    with pytest.raises(ProfileError) as exc:
        fetch_account_profile(source)
    assert "could not refresh" in str(exc.value)


def test_fetch_wraps_permanent_credentials_error(patch_httpx):
    source = _StubSource(error=CredentialsError("no creds anywhere"))
    with pytest.raises(ProfileError) as exc:
        fetch_account_profile(source)
    assert "no usable Claude OAuth credentials" in str(exc.value)


def test_get_account_info_returns_none_on_failure(patch_httpx):
    patch_httpx.responses = [_FakeResp(401, None, "nope")]
    assert get_claude_account_info(_StubSource()) is None


def test_get_account_info_memoises_success(patch_httpx):
    patch_httpx.responses = [_FakeResp(200, SAMPLE_PAYLOAD)]
    first = get_claude_account_info(_StubSource())
    second = get_claude_account_info(_StubSource())

    assert first is second
    assert len([c for c in patch_httpx.calls if "url" in c]) == 1


def test_get_account_info_does_not_retry_after_failure(patch_httpx):
    patch_httpx.responses = [_FakeResp(500, None, "boom")]
    assert get_claude_account_info(_StubSource()) is None
    assert get_claude_account_info(_StubSource()) is None
    assert len([c for c in patch_httpx.calls if "url" in c]) == 1


def test_refresh_forces_a_new_lookup(patch_httpx):
    patch_httpx.responses = [_FakeResp(200, SAMPLE_PAYLOAD), _FakeResp(200, SAMPLE_PAYLOAD)]
    get_claude_account_info(_StubSource())
    get_claude_account_info(_StubSource(), refresh=True)
    assert len([c for c in patch_httpx.calls if "url" in c]) == 2


def test_credentials_env_blob_feeds_the_default_provider(monkeypatch, patch_httpx):
    monkeypatch.delenv("WCB_CC_ACCOUNT_POOL", raising=False)
    monkeypatch.delenv("WCB_CC_CREDS_PATH", raising=False)
    monkeypatch.setenv(
        "CLAUDE_CODE_CREDENTIALS",
        json.dumps(
            {
                "claudeAiOauth": {
                    "accessToken": "sk-ant-oat01-fromenv",
                    "refreshToken": "sk-ant-ort01-fromenv",
                    "expiresAt": 4102444800000,
                    "scopes": ["user:profile", "user:inference"],
                    "subscriptionType": "max",
                }
            }
        ),
    )
    patch_httpx.responses = [_FakeResp(200, SAMPLE_PAYLOAD)]

    prof = fetch_account_profile()

    assert prof.account_uuid == "c5802e58-1111-2222-3333-444455556666"
    call = [c for c in patch_httpx.calls if "url" in c][0]
    assert call["headers"]["authorization"] == "Bearer sk-ant-oat01-fromenv"


def test_describe_is_log_safe():
    prof = AccountProfile.from_payload(SAMPLE_PAYLOAD)
    described = prof.describe()
    assert "madhur@example.com" in described
    assert "active" in described
