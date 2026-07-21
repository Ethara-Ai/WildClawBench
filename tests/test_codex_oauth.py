"""Offline unit tests for the codex ChatGPT-plan OAuth package.

No network and no docker: httpx refresh is monkeypatched, cert/credential/pool
logic is exercised against tmp files. Mirrors the claude_oauth test style.
"""

from __future__ import annotations

import base64
import json
import time

import pytest

from src.agents.codex import backend as codex_backend
from src.utils.codex_oauth import (
    CertAuthority,
    CodexOAuthProxy,
    ErrorKind,
    classify_openai_error,
    load_account_pool,
    load_codex_credentials,
)
from src.utils.codex_oauth import credentials as cx_creds
from src.utils.codex_oauth.credentials import CodexCredentials


def _jwt(exp: int) -> str:
    def b(o: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(o).encode()).rstrip(b"=").decode()

    return f"{b({'alg': 'none'})}.{b({'exp': exp})}.sig"


def _write_auth(path, account_id="acc_x", exp_offset=9999):
    payload = {
        "tokens": {
            "id_token": _jwt(int(time.time()) + exp_offset),
            "access_token": _jwt(int(time.time()) + exp_offset),
            "refresh_token": "rt-" + account_id,
            "account_id": account_id,
        },
        "last_refresh": "2030-01-01T00:00:00Z",
    }
    path.write_text(json.dumps(payload))
    return path


# --------------------------------------------------------------------------- CA
def test_ca_and_leaf_chain():
    ca = CertAuthority()
    assert ca.cert_pem.startswith(b"-----BEGIN CERTIFICATE-----")
    leaf = ca.issue_leaf("chatgpt.com")
    # leaf + CA in the chain, plus a private key
    assert leaf.cert_pem.count(b"BEGIN CERTIFICATE") == 2
    assert b"PRIVATE KEY" in leaf.key_pem


def test_leaf_cert_is_valid_now():
    """Regression: a fixed past epoch would make the leaf 'certificate expired'."""
    from cryptography import x509

    leaf = CertAuthority().issue_leaf("chatgpt.com")
    cert = x509.load_pem_x509_certificate(leaf.cert_pem)
    now = time.time()
    assert cert.not_valid_before_utc.timestamp() <= now <= cert.not_valid_after_utc.timestamp()


# ------------------------------------------------------------------- credentials
def test_load_credentials_and_expiry(tmp_path):
    p = _write_auth(tmp_path / "auth.json", "acc_1")
    c = load_codex_credentials(p)
    assert c.account_id == "acc_1"
    assert not c.is_expired()

    _write_auth(tmp_path / "auth.json", "acc_1", exp_offset=-100)
    assert load_codex_credentials(tmp_path / "auth.json").is_expired()


def test_missing_tokens_raises(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text(json.dumps({"OPENAI_API_KEY": "sk-123"}))  # API-key file, not ChatGPT
    with pytest.raises(cx_creds.CredentialsError):
        load_codex_credentials(p)


def test_refresh_rotates_token(tmp_path, monkeypatch):
    p = _write_auth(tmp_path / "auth.json", "acc_1", exp_offset=-100)  # expired -> refresh

    class _Resp:
        status_code = 200

        def json(self):
            return {"access_token": _jwt(int(time.time()) + 9999), "refresh_token": "rt-ROTATED"}

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, *a, **k):
            return _Resp()

    monkeypatch.setattr(cx_creds.httpx, "Client", _Client)
    prov = cx_creds.CodexCredentialProvider(p, label="file:test")
    creds = prov.get_credentials()
    assert not creds.is_expired()
    # rotated refresh token persisted back to the pool file
    assert json.loads(p.read_text())["tokens"]["refresh_token"] == "rt-ROTATED"


# --------------------------------------------------------------------- rotation
def test_pool_rotation_on_exhaustion(tmp_path):
    a = _write_auth(tmp_path / "a.json", "acc_a")
    b = _write_auth(tmp_path / "b.json", "acc_b")
    pool = load_account_pool(f"{a}:{b}")

    first = pool.get_credentials()
    assert first.account_id == "acc_a"  # insertion order
    pool.mark_account_exhausted(first.account_label, time.time() + 3600)
    second = pool.get_credentials()
    assert second.account_id == "acc_b"  # rotated to the next account


def test_pool_all_exhausted_raises(tmp_path):
    a = _write_auth(tmp_path / "a.json", "acc_a")
    pool = load_account_pool(str(a))
    c = pool.get_credentials()
    pool.mark_account_invalid(c.account_label)
    with pytest.raises(cx_creds.CredentialsError):
        pool.get_credentials()


def test_next_reset_at(tmp_path):
    a = _write_auth(tmp_path / "a.json", "acc_a")
    pool = load_account_pool(str(a))
    assert pool.next_reset_at() is None  # available
    c = pool.get_credentials()
    when = time.time() + 500
    pool.mark_account_exhausted(c.account_label, when)
    assert pool.next_reset_at() == pytest.approx(when, abs=1)


# ------------------------------------------------------------------- error kinds
@pytest.mark.parametrize(
    "status,headers,expected",
    [
        (200, {}, ErrorKind.OK),
        (101, {}, ErrorKind.OK),
        (429, {"Retry-After": "3600"}, ErrorKind.SUBSCRIPTION_CAP),
        (429, {"Retry-After": "5"}, ErrorKind.TRANSIENT_THROTTLE),
        (401, {}, ErrorKind.OAUTH_TOKEN_INVALID),
        (403, {}, ErrorKind.ACCOUNT_RESTRICTED),
        (402, {}, ErrorKind.BILLING_ERROR),
        (500, {}, ErrorKind.UPSTREAM_5XX),
    ],
)
def test_classify(status, headers, expected):
    assert classify_openai_error(status, None, headers).kind == expected


def test_error_kind_policies():
    assert ErrorKind.SUBSCRIPTION_CAP.is_account_problem
    assert ErrorKind.OAUTH_TOKEN_INVALID.is_account_problem
    assert ErrorKind.TRANSIENT_THROTTLE.is_retryable
    assert not ErrorKind.SUBSCRIPTION_CAP.is_retryable


# --------------------------------------------------------------------- proxy hdr
def test_proxy_rewrites_auth_headers():
    px = CodexOAuthProxy.__new__(CodexOAuthProxy)  # no sockets/ca
    req = (
        b"GET /backend-api/codex/responses HTTP/1.1\r\n"
        b"host: chatgpt.com\r\n"
        b"authorization: Bearer STUBTOKEN\r\n"
        b"chatgpt-account-id: STUBACC\r\n"
        b"upgrade: websocket"
    )
    creds = CodexCredentials(access_token="REALTOKEN", refresh_token="x", account_id="REALACC")
    out = CodexOAuthProxy._rewrite_auth(px, req, creds)
    assert b"authorization: Bearer REALTOKEN" in out
    assert b"chatgpt-account-id: REALACC" in out
    assert b"STUBTOKEN" not in out and b"STUBACC" not in out
    assert b"upgrade: websocket" in out  # untouched
    assert out.split(b"\r\n", 1)[0] == b"GET /backend-api/codex/responses HTTP/1.1"


def test_proxy_injects_missing_auth_header():
    px = CodexOAuthProxy.__new__(CodexOAuthProxy)
    req = b"GET /x HTTP/1.1\r\nhost: chatgpt.com"
    creds = CodexCredentials(access_token="T", refresh_token="x", account_id="A")
    out = CodexOAuthProxy._rewrite_auth(px, req, creds)
    assert b"authorization: Bearer T" in out
    assert b"chatgpt-account-id: A" in out


class _RecordingProvider:
    def __init__(self):
        self.exhausted = []
        self.invalidated = []

    def mark_account_exhausted(self, label, until):
        self.exhausted.append(label)

    def mark_account_invalid(self, label):
        self.invalidated.append(label)


def _classify(px, status, headers=None):
    lines = [f"HTTP/1.1 {status} X"] + [f"{k}: {v}" for k, v in (headers or {}).items()]
    raw = ("\r\n".join(lines) + "\r\n\r\n").encode()
    return CodexOAuthProxy._classify_and_react(px, raw, "acct")


def test_react_marks_only_on_cap_and_token_invalid():
    px = CodexOAuthProxy.__new__(CodexOAuthProxy)
    px.provider = _RecordingProvider()
    _classify(px, 429, {"Retry-After": "3600"})  # cap -> exhausted
    _classify(px, 401)  # token invalid -> invalid
    _classify(px, 403)  # account_restricted -> NOT a rotation trigger
    _classify(px, 402)  # billing -> NOT a rotation trigger
    _classify(px, 200)  # ok
    assert px.provider.exhausted == ["acct"]
    assert px.provider.invalidated == ["acct"]  # exactly one (the 401), NOT the 403/402


# ------------------------------------------------------------- backend stub auth
def test_stub_auth_json_shape():
    stub = json.loads(codex_backend.build_codex_stub_auth_json("acc-stub"))
    assert set(stub["tokens"]) == {"id_token", "access_token", "refresh_token", "account_id"}
    assert stub["tokens"]["account_id"] == "acc-stub"
    # far-future exp so codex never self-refreshes the stub
    payload = stub["tokens"]["access_token"].split(".")[1]
    payload += "=" * (-len(payload) % 4)
    assert json.loads(base64.urlsafe_b64decode(payload))["exp"] > time.time() + 10 * 365 * 86400
