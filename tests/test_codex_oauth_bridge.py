"""Tests for src/utils/codex_oauth/bridge.py — secret gate + upstream forwarding.

The upstream ``httpx.AsyncClient`` is replaced with an in-process fake, so no
request ever leaves the machine: chatgpt.com is never contacted.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402
from starlette.requests import Request  # noqa: E402

from src.utils.codex_oauth import bridge as bridgemod  # noqa: E402
from src.utils.codex_oauth.bridge import (  # noqa: E402
    DEFAULT_USER_AGENT,
    OAUTH_BETA,
    ORIGINATOR,
    RESPONSES_PATH,
    UPSTREAM_DEFAULT,
    _client_authorized,
    _forward_headers,
    _secret_eq,
    _strip_unsupported,
    build_app,
)
from src.utils.codex_oauth.credentials import CredentialsError  # noqa: E402

_SECRET_ENV = "KAIJU_CODEX_BRIDGE_SECRET"
_SECRET = "s3cret-bridge-key"
_TOKEN = "tok-live-oauth-abcdef"
_ACCOUNT = "acct-12345678-abcd"

_CODEX_ENV_VARS = (
    _SECRET_ENV, "KAIJU_CODEX_UPSTREAM", "KAIJU_CODEX_USER_AGENT",
    "KAIJU_CODEX_MODEL", "KAIJU_CODEX_STRIP_PARAMS", "KAIJU_CODEX_FORCE_STORE_FALSE",
    "KAIJU_CODEX_CAP_WAIT_SEC", "KAIJU_CODEX_CAP_MAX_WAITS",
    "KAIJU_CODEX_BUFFER_AND_RETRY", "KAIJU_CODEX_MAX_INLINE_RETRIES",
    "KAIJU_CODEX_ACCOUNT_POOL", "KAIJU_CODEX_KEEPALIVE_SEC",
)

_SSE_OK = (
    b'data: {"type":"response.created","response":{"id":"resp_1"}}\n\n'
    b'data: {"type":"response.output_item.done","item":{"type":"message",'
    b'"content":[{"type":"output_text","text":"pong"}]}}\n\n'
    b'data: {"type":"response.completed","response":{"id":"resp_1",'
    b'"status":"completed","output":[],"usage":{"input_tokens":3,'
    b'"output_tokens":1,"total_tokens":4}}}\n\n'
)


@pytest.fixture(autouse=True)
def _clean_codex_env(monkeypatch):
    for name in _CODEX_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


class _FakeProvider:
    def __init__(self, token: str = _TOKEN, account: str = _ACCOUNT) -> None:
        self._token = token
        self.account_id = account

    def get_access_token(self) -> str:
        return self._token

    def get_token_and_account(self) -> tuple[str, str]:
        return self._token, self.account_id

    def reload(self) -> None:
        return None


class _BrokenProvider(_FakeProvider):
    def get_access_token(self) -> str:
        raise CredentialsError("no auth.json")


class _FakeUpstream:
    def __init__(self, status_code: int = 200, body: bytes = _SSE_OK,
                 headers: dict | None = None) -> None:
        self.status_code = status_code
        self._body = body
        self.headers = headers or {"content-type": "text/event-stream"}
        self.closed = False

    async def aread(self) -> bytes:
        return self._body

    async def aclose(self) -> None:
        self.closed = True

    async def aiter_raw(self):
        yield self._body

    async def aiter_lines(self):
        for line in self._body.split(b"\n"):
            yield line.decode()


class _FakeAsyncClient:
    instances: list[_FakeAsyncClient] = []

    def __init__(self, **kwargs) -> None:
        self.requests: list[SimpleNamespace] = []
        self.upstreams: list[_FakeUpstream] = []
        self.next_upstream = lambda: _FakeUpstream()
        _FakeAsyncClient.instances.append(self)

    def build_request(self, method, url, content=None, headers=None):
        return SimpleNamespace(method=method, url=url, content=content,
                               headers=dict(headers or {}))

    async def send(self, request, stream=False):
        self.requests.append(request)
        upstream = self.next_upstream()
        self.upstreams.append(upstream)
        return upstream

    async def aclose(self) -> None:
        return None


def _client(monkeypatch, provider=None, *, upstream_factory=None, secret=_SECRET):
    """Build the app with a faked upstream client; returns (TestClient, fake)."""
    _FakeAsyncClient.instances.clear()
    monkeypatch.setattr(bridgemod.httpx, "AsyncClient", _FakeAsyncClient)
    if secret is not None:
        monkeypatch.setenv(_SECRET_ENV, secret)
    app = build_app(provider or _FakeProvider())
    fake = _FakeAsyncClient.instances[-1]
    if upstream_factory is not None:
        fake.next_upstream = upstream_factory
    return TestClient(app), fake


def _auth() -> dict:
    return {"Authorization": f"Bearer {_SECRET}"}


def _request(headers: dict) -> Request:
    return Request({
        "type": "http", "method": "POST", "path": RESPONSES_PATH,
        "raw_path": RESPONSES_PATH.encode(), "query_string": b"",
        "root_path": "", "scheme": "http", "http_version": "1.1",
        "server": ("testserver", 80), "client": ("127.0.0.1", 5000),
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
    })


# ---------------------------------------------------------------------------
# Secret gate (anti-SSRF) — pure helpers
# ---------------------------------------------------------------------------


def test_secret_eq_matches_only_exact_secret():
    assert _secret_eq(_SECRET, _SECRET)
    assert not _secret_eq(_SECRET + "x", _SECRET)
    assert not _secret_eq("", _SECRET)
    assert not _secret_eq(_SECRET.upper(), _SECRET)


def test_client_authorized_accepts_bearer(monkeypatch):
    monkeypatch.setenv(_SECRET_ENV, _SECRET)
    assert _client_authorized(_request({"authorization": f"Bearer {_SECRET}"}))
    assert _client_authorized(_request({"authorization": f"bearer  {_SECRET} "}))


def test_client_authorized_accepts_x_api_key(monkeypatch):
    monkeypatch.setenv(_SECRET_ENV, _SECRET)
    assert _client_authorized(_request({"x-api-key": _SECRET}))


def test_client_authorized_rejects_wrong_and_missing_secret(monkeypatch):
    monkeypatch.setenv(_SECRET_ENV, _SECRET)
    assert not _client_authorized(_request({}))
    assert not _client_authorized(_request({"authorization": "Bearer wrong"}))
    assert not _client_authorized(_request({"x-api-key": "wrong"}))
    assert not _client_authorized(_request({"authorization": _SECRET}))


def test_client_authorized_open_when_secret_unset(monkeypatch):
    """Documented (and warned-about) fallback: with no secret configured the
    bridge runs UNAUTHENTICATED rather than refusing requests."""
    monkeypatch.delenv(_SECRET_ENV, raising=False)
    assert _client_authorized(_request({}))


def test_build_app_warns_loudly_when_secret_unset(monkeypatch, caplog):
    monkeypatch.setattr(bridgemod.httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.delenv(_SECRET_ENV, raising=False)
    with caplog.at_level("WARNING", logger=bridgemod.__name__):
        build_app(_FakeProvider())
    assert "UNAUTHENTICATED" in caplog.text
    assert _SECRET_ENV in caplog.text


def test_main_refuses_to_serve_without_the_secret(monkeypatch, capsys):
    """AGENTS.md HARD invariant / anti-SSRF gate: the DEPLOYABLE bridge
    (`python -m codex_oauth`) must exit 2 when KAIJU_CODEX_BRIDGE_SECRET is
    unset, refusing to serve unauthenticated. The gate fires BEFORE any
    credential load — proven by the stderr message AND a tripwire on the
    credential provider that must never run."""
    from src.utils.codex_oauth import __main__ as codexmain

    monkeypatch.delenv(_SECRET_ENV, raising=False)

    def _boom(*_a, **_kw):
        raise AssertionError("credentials must NOT be reached when the secret is unset")

    # Patch on the DEFINING module: main() imports CredentialProvider
    # function-locally from .credentials, so patching codexmain.* is a no-op.
    monkeypatch.setattr("src.utils.codex_oauth.credentials.CredentialProvider", _boom)
    assert codexmain.main([]) == 2
    # The exit-2 MUST be attributable to the GATE, not the credential path:
    err = capsys.readouterr().err
    assert "refusing to start" in err
    assert _SECRET_ENV in err


def test_main_with_secret_reaches_credential_load(monkeypatch, capsys):
    """Positive control: the gate is CONDITIONAL. With the secret SET, main()
    passes the gate and proceeds to credential load (here forced to fail), so
    the exit-2 comes from the credential path, not the gate."""
    from src.utils.codex_oauth import __main__ as codexmain

    monkeypatch.setenv(_SECRET_ENV, _SECRET)

    class _FailingProvider:
        def __init__(self, *_a, **_kw):
            raise CredentialsError("no auth.json")

    monkeypatch.setattr(
        "src.utils.codex_oauth.credentials.CredentialProvider", _FailingProvider
    )
    assert codexmain.main([]) == 2
    err = capsys.readouterr().err
    assert "credentials error" in err
    assert "refusing to start" not in err


def test_main_check_is_exempt_from_the_secret_gate(monkeypatch, capsys):
    """`--check` is a local credential probe that never serves traffic, so it is
    exempt from the secret gate — it proceeds to (and fails on) credential load,
    not on the missing secret."""
    from src.utils.codex_oauth import __main__ as codexmain

    monkeypatch.delenv(_SECRET_ENV, raising=False)

    class _FailingProvider:
        def __init__(self, *_a, **_kw):
            raise CredentialsError("no auth.json")

    monkeypatch.setattr(
        "src.utils.codex_oauth.credentials.CredentialProvider", _FailingProvider
    )
    # Exits 2 on the CREDENTIAL error (having passed the secret gate), not the
    # secret gate itself — proving --check bypasses the secret requirement.
    assert codexmain.main(["--check"]) == 2
    err = capsys.readouterr().err
    assert "credentials error" in err
    assert "refusing to start" not in err


# ---------------------------------------------------------------------------
# Secret gate over HTTP
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/responses", "/v1/responses",
                                  "/backend-api/codex/responses",
                                  "/chat/completions", "/v1/chat/completions"])
def test_post_without_secret_is_rejected_401(monkeypatch, path):
    client, fake = _client(monkeypatch)
    resp = client.post(path, json={"model": "gpt-5.6-sol", "input": "hi"})
    assert resp.status_code == 401
    assert resp.json()["error"]["type"] == "authentication_error"
    assert fake.requests == [], "an unauthorized request must never reach upstream"


def test_post_with_wrong_secret_is_rejected_401(monkeypatch):
    client, fake = _client(monkeypatch)
    resp = client.post("/responses", json={"model": "m", "input": "hi"},
                       headers={"Authorization": "Bearer not-the-secret"})
    assert resp.status_code == 401
    assert fake.requests == []


def test_post_with_x_api_key_secret_is_accepted(monkeypatch):
    client, fake = _client(monkeypatch)
    resp = client.post("/responses", json={"model": "m", "input": "hi"},
                       headers={"x-api-key": _SECRET})
    assert resp.status_code == 200
    assert len(fake.requests) == 1


# ---------------------------------------------------------------------------
# healthz / quota
# ---------------------------------------------------------------------------


def test_healthz_reports_masked_token_and_account(monkeypatch):
    client, _ = _client(monkeypatch)
    resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["token_prefix"] == _TOKEN[:12] + "..."
    assert body["account_prefix"] == _ACCOUNT[:8] + "..."
    assert _TOKEN not in resp.text


def test_healthz_needs_no_secret(monkeypatch):
    client, _ = _client(monkeypatch)
    assert client.get("/healthz").status_code == 200


def test_healthz_503_on_credentials_error(monkeypatch):
    client, _ = _client(monkeypatch, _BrokenProvider())
    resp = client.get("/healthz")
    assert resp.status_code == 503
    assert resp.json()["ok"] is False
    assert "no auth.json" in resp.json()["error"]


def test_quota_reports_single_account(monkeypatch):
    client, _ = _client(monkeypatch)
    body = client.get("/quota").json()
    assert body["multi_account"] is False
    assert body["account_prefix"] == _ACCOUNT[:8] + "..."


# ---------------------------------------------------------------------------
# Required upstream headers
# ---------------------------------------------------------------------------


def test_forward_headers_sets_every_required_upstream_header():
    out = _forward_headers(_request({"x-trace": "keep-me"}), _TOKEN, _ACCOUNT)
    assert out["Authorization"] == f"Bearer {_TOKEN}"
    assert out["ChatGPT-Account-Id"] == _ACCOUNT
    assert out["OpenAI-Beta"] == OAUTH_BETA
    assert out["originator"] == ORIGINATOR
    assert out["User-Agent"] == DEFAULT_USER_AGENT
    assert out["User-Agent"].startswith("codex_cli_rs/")
    assert out["Content-Type"] == "application/json"
    assert out["Accept"] == "text/event-stream"
    assert len(out["session_id"]) == 36
    assert out["x-trace"] == "keep-me"


def test_forward_headers_strips_client_auth_and_forced_headers():
    incoming = {
        "host": "127.0.0.1:8788",
        "authorization": f"Bearer {_SECRET}",
        "x-api-key": _SECRET,
        "content-length": "12",
        "connection": "keep-alive",
        "accept-encoding": "gzip",
        "accept": "*/*",
        "content-type": "text/plain",
        "openai-organization": "org-1",
        "openai-beta": "stale=1",
        "chatgpt-account-id": "acct-stale",
        "originator": "curl",
        "session_id": "stale-session",
        "user-agent": "python-httpx/0.28.1",
    }
    out = _forward_headers(_request(incoming), _TOKEN, _ACCOUNT)
    assert out["Authorization"] == f"Bearer {_TOKEN}"
    assert _SECRET not in json.dumps(out)
    assert "x-api-key" not in {k.lower() for k in out}
    assert "host" not in {k.lower() for k in out}
    assert "content-length" not in {k.lower() for k in out}
    assert out["ChatGPT-Account-Id"] == _ACCOUNT
    assert out["originator"] == ORIGINATOR
    assert out["session_id"] != "stale-session"
    assert out["User-Agent"] == DEFAULT_USER_AGENT
    assert out["Accept"] == "text/event-stream"
    assert out["Content-Type"] == "application/json"


def test_forward_headers_session_id_is_fresh_per_request():
    a = _forward_headers(_request({}), _TOKEN, _ACCOUNT)["session_id"]
    b = _forward_headers(_request({}), _TOKEN, _ACCOUNT)["session_id"]
    assert a != b


def test_forward_headers_user_agent_override(monkeypatch):
    monkeypatch.setenv("KAIJU_CODEX_USER_AGENT", "codex_cli_rs/9.9.9")
    assert _forward_headers(_request({}), _TOKEN, _ACCOUNT)["User-Agent"] == \
        "codex_cli_rs/9.9.9"


def test_proxied_request_carries_required_headers_and_url(monkeypatch):
    client, fake = _client(monkeypatch)
    resp = client.post("/v1/responses", json={"model": "gpt-5.6-sol", "input": "hi"},
                       headers=_auth())
    assert resp.status_code == 200

    sent = fake.requests[0]
    assert sent.method == "POST"
    assert sent.url == UPSTREAM_DEFAULT + RESPONSES_PATH
    assert sent.headers["Authorization"] == f"Bearer {_TOKEN}"
    assert sent.headers["ChatGPT-Account-Id"] == _ACCOUNT
    assert sent.headers["OpenAI-Beta"] == OAUTH_BETA
    assert sent.headers["originator"] == ORIGINATOR
    assert sent.headers["User-Agent"].startswith("codex_cli_rs/")
    assert sent.headers["session_id"]


def test_two_proxied_requests_get_distinct_session_ids(monkeypatch):
    client, fake = _client(monkeypatch)
    for _ in range(2):
        client.post("/responses", json={"model": "m", "input": "hi"}, headers=_auth())
    assert fake.requests[0].headers["session_id"] != fake.requests[1].headers["session_id"]


def test_upstream_base_override_is_honoured(monkeypatch):
    monkeypatch.setenv("KAIJU_CODEX_UPSTREAM", "http://127.0.0.1:9/fake-codex/")
    client, fake = _client(monkeypatch)
    client.post("/responses", json={"model": "m", "input": "hi"}, headers=_auth())
    assert fake.requests[0].url == "http://127.0.0.1:9/fake-codex" + RESPONSES_PATH


# ---------------------------------------------------------------------------
# Body preparation
# ---------------------------------------------------------------------------


def test_strip_unsupported_removes_sampling_params():
    body = {"model": "m", "temperature": 0.5, "top_p": 0.9, "seed": 1,
            "max_output_tokens": 10, "instructions": "keep"}
    _strip_unsupported(body)
    assert body == {"model": "m", "instructions": "keep"}


def test_strip_unsupported_honours_env_extension(monkeypatch):
    monkeypatch.setenv("KAIJU_CODEX_STRIP_PARAMS", "custom_field, other ")
    body = {"model": "m", "custom_field": 1, "other": 2, "keep": 3}
    _strip_unsupported(body)
    assert body == {"model": "m", "keep": 3}


def test_proxied_body_forces_stream_and_store_false_and_wraps_input(monkeypatch):
    client, fake = _client(monkeypatch)
    client.post("/responses",
                json={"model": "gpt-5.6-sol-2026-04-23", "input": "hi",
                      "temperature": 0.7},
                headers=_auth())
    sent = json.loads(fake.requests[0].content)
    assert sent["stream"] is True
    assert sent["store"] is False
    assert sent["model"] == "gpt-5.6-sol"
    assert sent["input"] == [{"type": "message", "role": "user",
                              "content": [{"type": "input_text", "text": "hi"}]}]
    assert "temperature" not in sent


def test_proxied_body_model_override(monkeypatch):
    monkeypatch.setenv("KAIJU_CODEX_MODEL", "gpt-5.6-sol")
    client, fake = _client(monkeypatch)
    client.post("/responses", json={"model": "whatever", "input": "hi"}, headers=_auth())
    assert json.loads(fake.requests[0].content)["model"] == "gpt-5.6-sol"


# ---------------------------------------------------------------------------
# Responses path (unary + streaming)
# ---------------------------------------------------------------------------


def test_unary_responses_aggregates_sse_into_response_object(monkeypatch):
    client, fake = _client(monkeypatch)
    resp = client.post("/responses", json={"model": "gpt-5.6-sol", "input": "hi"},
                       headers=_auth())
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "resp_1"
    assert body["output"][0]["content"][0]["text"] == "pong"
    assert body["usage"]["input_tokens"] == 3
    assert fake.upstreams[0].closed


def test_unary_responses_502_when_no_terminal_event(monkeypatch):
    client, _ = _client(
        monkeypatch,
        upstream_factory=lambda: _FakeUpstream(
            body=b'data: {"type":"response.created","response":{}}\n\n'),
    )
    resp = client.post("/responses", json={"model": "m", "input": "hi"}, headers=_auth())
    assert resp.status_code == 502
    assert resp.json()["error"]["type"] == "upstream_error"


def test_upstream_error_status_is_passed_through(monkeypatch):
    client, _ = _client(
        monkeypatch,
        upstream_factory=lambda: _FakeUpstream(
            status_code=400, body=b'{"error":{"message":"Input must be a list"}}',
            headers={"content-type": "application/json"}),
    )
    resp = client.post("/responses", json={"model": "m", "input": "hi"}, headers=_auth())
    assert resp.status_code == 400
    assert "Input must be a list" in resp.text


def test_streaming_responses_forwards_sse_bytes_verbatim(monkeypatch):
    client, _ = _client(monkeypatch)
    resp = client.post("/responses",
                       json={"model": "m", "input": "hi", "stream": True},
                       headers=_auth())
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert resp.content == _SSE_OK


def test_streaming_responses_injects_failure_on_truncated_upstream(monkeypatch):
    client, _ = _client(
        monkeypatch,
        upstream_factory=lambda: _FakeUpstream(
            body=b'data: {"type":"response.output_text.delta","delta":"partial"}\n\n'),
    )
    resp = client.post("/responses",
                       json={"model": "m", "input": "hi", "stream": True},
                       headers=_auth())
    assert b"event: response.failed" in resp.content
    assert b"truncated" in resp.content


# ---------------------------------------------------------------------------
# Chat Completions shim
# ---------------------------------------------------------------------------


def test_unary_chat_completions_translates_both_directions(monkeypatch):
    client, fake = _client(monkeypatch)
    resp = client.post("/v1/chat/completions", json={
        "model": "gpt-5.6-sol",
        "messages": [{"role": "system", "content": "be terse"},
                     {"role": "user", "content": "ping"}],
        "temperature": 0.4,
    }, headers=_auth())
    assert resp.status_code == 200

    sent = json.loads(fake.requests[0].content)
    assert sent["instructions"] == "be terse"
    assert sent["input"][0]["content"][0]["text"] == "ping"
    assert sent["stream"] is True
    assert "temperature" not in sent

    body = resp.json()
    assert body["object"] == "chat.completion"
    assert body["model"] == "gpt-5.6-sol"
    assert body["choices"][0]["message"]["content"] == "pong"
    assert body["choices"][0]["finish_reason"] == "stop"
    assert body["usage"]["prompt_tokens"] == 3


def test_streaming_chat_completions_emits_chat_chunks(monkeypatch):
    sse = (b'data: {"type":"response.output_text.delta","delta":"po"}\n\n'
           b'data: {"type":"response.output_text.delta","delta":"ng"}\n\n'
           b'data: {"type":"response.completed","response":{"usage":'
           b'{"input_tokens":3,"output_tokens":1,"total_tokens":4}}}\n\n')
    client, _ = _client(monkeypatch, upstream_factory=lambda: _FakeUpstream(body=sse))
    resp = client.post("/chat/completions", json={
        "model": "gpt-5.6-sol",
        "messages": [{"role": "user", "content": "ping"}],
        "stream": True,
    }, headers=_auth())

    assert resp.status_code == 200
    chunks = [json.loads(part[6:]) for part in resp.text.split("\n\n")
              if part.startswith("data: ") and not part.endswith("[DONE]")]
    assert chunks[0]["choices"][0]["delta"] == {"role": "assistant"}
    assert [c["choices"][0]["delta"].get("content") for c in chunks[1:3]] == ["po", "ng"]
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"
    assert chunks[-1]["usage"]["prompt_tokens"] == 3
    assert resp.text.endswith("data: [DONE]\n\n")


def test_chat_completions_invalid_json_body_is_400(monkeypatch):
    client, fake = _client(monkeypatch)
    resp = client.post("/chat/completions", content=b"{not json",
                       headers={**_auth(), "Content-Type": "application/json"})
    assert resp.status_code == 400
    assert resp.json()["error"]["type"] == "invalid_request_error"
    assert fake.requests == []
