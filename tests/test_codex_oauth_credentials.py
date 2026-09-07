"""Tests for src/utils/codex_oauth/credentials.py — auth.json load/refresh/pool.

Every test points ``KAIJU_CODEX_AUTH_PATH`` at a tmp_path fixture and stubs
``httpx.post``, so the real ~/.codex/auth.json is never read and the OAuth token
endpoint is never contacted.
"""
from __future__ import annotations

import base64
import json
import os
import stat
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from src.utils.codex_oauth import credentials as credmod
from src.utils.codex_oauth.credentials import (
    DEFAULT_CLIENT_ID,
    OAUTH_TOKEN_URL,
    CodexCredentials,
    CredentialProvider,
    CredentialsError,
    MultiAccountCredentialProvider,
    _decode_jwt_exp,
    _FileCredentialProvider,
    _parse_auth_json,
    load_account_pool,
    load_credentials,
    refresh_credentials,
)

_AUTH_PATH_ENV = "KAIJU_CODEX_AUTH_PATH"
_INLINE_ENV = "CODEX_CREDENTIALS"


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _jwt(claims: dict) -> str:
    """Build an unsigned JWT so the exp/claim decoder has something real to parse."""
    header = _b64url(json.dumps({"alg": "none", "typ": "JWT"}).encode())
    payload = _b64url(json.dumps(claims).encode())
    return f"{header}.{payload}.sig"


def _auth_json(access_token: str, *, account_id: str | None = "acct-1",
               refresh_token: str | None = "rt.1") -> dict:
    tokens: dict = {"id_token": _jwt({"sub": "u"}), "access_token": access_token}
    if refresh_token is not None:
        tokens["refresh_token"] = refresh_token
    if account_id is not None:
        tokens["account_id"] = account_id
    return {"auth_mode": "chatgpt", "OPENAI_API_KEY": None, "tokens": tokens,
            "last_refresh": "2026-07-03T00:00:00Z"}


def _write_auth(path: Path, access_token: str, **kw) -> Path:
    path.write_text(json.dumps(_auth_json(access_token, **kw)))
    return path


class _FakeTokenResponse:
    def __init__(self, status_code: int, payload: dict, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text or json.dumps(payload)

    def json(self) -> dict:
        return self._payload


@pytest.fixture(autouse=True)
def _isolate_credential_env(monkeypatch, tmp_path):
    monkeypatch.delenv(_INLINE_ENV, raising=False)
    monkeypatch.delenv("KAIJU_CODEX_CLIENT_ID", raising=False)
    monkeypatch.delenv("KAIJU_CODEX_POOL_STATE_PATH", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "fakehome"))
    monkeypatch.setattr(credmod.Path, "home", classmethod(lambda cls: tmp_path / "fakehome"))


# ---------------------------------------------------------------------------
# JWT exp decode + CodexCredentials
# ---------------------------------------------------------------------------


def test_decode_jwt_exp_reads_exp_claim():
    assert _decode_jwt_exp(_jwt({"exp": 1893456000})) == 1893456000.0


def test_decode_jwt_exp_none_when_claim_absent():
    assert _decode_jwt_exp(_jwt({"sub": "u"})) is None


def test_decode_jwt_exp_none_for_unparseable_token():
    assert _decode_jwt_exp("not-a-jwt") is None
    assert _decode_jwt_exp("") is None


def test_seconds_remaining_and_is_expired():
    creds = CodexCredentials(access_token="a", account_id="b",
                             expires_at=1000.0)
    assert creds.seconds_remaining(now=400.0) == 600.0
    assert not creds.is_expired(now=400.0)
    assert creds.is_expired(now=800.0)
    assert creds.is_expired(skew=0, now=1000.0)


def test_is_expired_false_when_expiry_unknown():
    creds = CodexCredentials(access_token="a", account_id="b")
    assert creds.seconds_remaining() is None
    assert not creds.is_expired()


# ---------------------------------------------------------------------------
# load_credentials / _parse_auth_json
# ---------------------------------------------------------------------------


def test_load_credentials_parses_auth_json_from_env_path(monkeypatch, tmp_path):
    token = _jwt({"exp": time.time() + 86400})
    path = _write_auth(tmp_path / "auth.json", token)
    monkeypatch.setenv(_AUTH_PATH_ENV, str(path))

    creds = load_credentials()
    assert creds.access_token == token
    assert creds.account_id == "acct-1"
    assert creds.refresh_token == "rt.1"
    assert creds.expires_at is not None
    assert not creds.is_expired()


def test_load_credentials_prefers_inline_env_over_path(monkeypatch, tmp_path):
    monkeypatch.setenv(_AUTH_PATH_ENV, str(_write_auth(tmp_path / "auth.json", "from-file")))
    monkeypatch.setenv(_INLINE_ENV, json.dumps(_auth_json("from-inline",
                                                          account_id="acct-inline")))
    creds = load_credentials()
    assert creds.access_token == "from-inline"
    assert creds.account_id == "acct-inline"


def test_load_credentials_error_when_nothing_found(monkeypatch, tmp_path):
    monkeypatch.setenv(_AUTH_PATH_ENV, str(tmp_path / "missing.json"))
    with pytest.raises(CredentialsError, match="No Codex credentials found"):
        load_credentials()


def test_load_credentials_falls_back_to_home_codex_auth_json(monkeypatch, tmp_path):
    monkeypatch.delenv(_AUTH_PATH_ENV, raising=False)
    home_auth = tmp_path / "fakehome" / ".codex" / "auth.json"
    home_auth.parent.mkdir(parents=True)
    _write_auth(home_auth, "home-token", account_id="acct-home")
    assert load_credentials().account_id == "acct-home"


def test_parse_auth_json_rejects_invalid_json():
    with pytest.raises(CredentialsError, match="not valid JSON"):
        _parse_auth_json("{not json", "src")


def test_parse_auth_json_rejects_api_key_auth_mode():
    raw = json.dumps({"auth_mode": "apikey", "tokens": {"access_token": "a",
                                                        "account_id": "b"}})
    with pytest.raises(CredentialsError, match="ChatGPT-auth mode"):
        _parse_auth_json(raw, "src")


def test_parse_auth_json_accepts_missing_auth_mode():
    raw = json.dumps({"tokens": {"access_token": "a", "account_id": "b"}})
    assert _parse_auth_json(raw, "src").account_id == "b"


def test_parse_auth_json_requires_account_id(monkeypatch, tmp_path):
    """There is no JWT-claim fallback: ``tokens.account_id`` is mandatory because
    the codex backend cross-checks ChatGPT-Account-Id against the OAuth token."""
    raw = json.dumps(_auth_json(_jwt({"exp": time.time() + 60,
                                      "https://api.openai.com/auth":
                                          {"chatgpt_account_id": "acct-from-jwt"}}),
                                account_id=None))
    with pytest.raises(CredentialsError, match="tokens.access_token/account_id"):
        _parse_auth_json(raw, "src")


def test_parse_auth_json_requires_access_token():
    raw = json.dumps({"auth_mode": "chatgpt", "tokens": {"account_id": "b"}})
    with pytest.raises(CredentialsError, match="tokens.access_token/account_id"):
        _parse_auth_json(raw, "src")


def test_parse_auth_json_refresh_token_optional():
    raw = json.dumps(_auth_json("a", refresh_token=None))
    assert _parse_auth_json(raw, "src").refresh_token is None


# ---------------------------------------------------------------------------
# refresh_credentials
# ---------------------------------------------------------------------------


def test_refresh_credentials_posts_refresh_grant_and_keeps_account_id(monkeypatch):
    new_token = _jwt({"exp": 1893456000})
    calls: list[tuple[str, dict]] = []

    def fake_post(url: str, json: dict | None = None, timeout: float | None = None):
        calls.append((url, dict(json or {})))
        return _FakeTokenResponse(200, {"access_token": new_token, "refresh_token": "rt.2"})

    monkeypatch.setattr(credmod.httpx, "post", fake_post)

    old = CodexCredentials(access_token="old", account_id="acct-1", refresh_token="rt.1")
    new = refresh_credentials(old)

    assert calls[0][0] == OAUTH_TOKEN_URL
    assert calls[0][1] == {"grant_type": "refresh_token",
                           "refresh_token": "rt.1",
                           "client_id": DEFAULT_CLIENT_ID}
    assert new.access_token == new_token
    assert new.refresh_token == "rt.2"
    assert new.account_id == "acct-1"
    assert new.expires_at == 1893456000.0


def test_refresh_credentials_keeps_old_refresh_token_when_not_rotated(monkeypatch):
    monkeypatch.setattr(credmod.httpx, "post",
                        lambda *a, **kw: _FakeTokenResponse(200, {"access_token": "new"}))
    new = refresh_credentials(
        CodexCredentials(access_token="old", account_id="a", refresh_token="rt.1"))
    assert new.refresh_token == "rt.1"


def test_refresh_credentials_honours_client_id_override(monkeypatch):
    seen: dict = {}
    monkeypatch.setenv("KAIJU_CODEX_CLIENT_ID", "app_custom")

    def fake_post(url: str, json: dict | None = None, timeout: float | None = None):
        seen.update(json or {})
        return _FakeTokenResponse(200, {"access_token": "n"})

    monkeypatch.setattr(credmod.httpx, "post", fake_post)
    refresh_credentials(CodexCredentials(access_token="o", account_id="a", refresh_token="r"))
    assert seen["client_id"] == "app_custom"


def test_refresh_credentials_without_refresh_token_raises(monkeypatch):
    monkeypatch.setattr(credmod.httpx, "post",
                        lambda *a, **kw: pytest.fail("must not contact the token endpoint"))
    with pytest.raises(CredentialsError, match="no refresh_token present"):
        refresh_credentials(CodexCredentials(access_token="o", account_id="a"))


def test_refresh_credentials_non_200_raises(monkeypatch):
    monkeypatch.setattr(credmod.httpx, "post",
                        lambda *a, **kw: _FakeTokenResponse(400, {}, text="bad grant"))
    with pytest.raises(CredentialsError, match="token refresh returned 400"):
        refresh_credentials(CodexCredentials(access_token="o", account_id="a",
                                             refresh_token="r"))


def test_refresh_credentials_missing_access_token_raises(monkeypatch):
    monkeypatch.setattr(credmod.httpx, "post",
                        lambda *a, **kw: _FakeTokenResponse(200, {"token_type": "Bearer"}))
    with pytest.raises(CredentialsError, match="no access_token"):
        refresh_credentials(CodexCredentials(access_token="o", account_id="a",
                                             refresh_token="r"))


def test_refresh_credentials_transport_error_raises(monkeypatch):
    def boom(*a, **kw):
        raise credmod.httpx.HTTPError("connection refused")

    monkeypatch.setattr(credmod.httpx, "post", boom)
    with pytest.raises(CredentialsError, match="token refresh request failed"):
        refresh_credentials(CodexCredentials(access_token="o", account_id="a",
                                             refresh_token="r"))


# ---------------------------------------------------------------------------
# CredentialProvider — lazy refresh + atomic persist
# ---------------------------------------------------------------------------


def test_provider_returns_cached_token_without_refreshing(monkeypatch, tmp_path):
    token = _jwt({"exp": time.time() + 86400})
    monkeypatch.setenv(_AUTH_PATH_ENV, str(_write_auth(tmp_path / "auth.json", token)))
    monkeypatch.setattr(credmod.httpx, "post",
                        lambda *a, **kw: pytest.fail("valid token must not refresh"))

    provider = CredentialProvider()
    assert provider.get_access_token() == token
    assert provider.account_id == "acct-1"
    assert provider.get_token_and_account() == (token, "acct-1")


def test_provider_refresh_persists_atomically_and_keeps_account_id(monkeypatch, tmp_path):
    path = _write_auth(tmp_path / "auth.json", _jwt({"exp": time.time() - 100}))
    monkeypatch.setenv(_AUTH_PATH_ENV, str(path))
    fresh = _jwt({"exp": time.time() + 86400})
    monkeypatch.setattr(credmod.httpx, "post",
                        lambda *a, **kw: _FakeTokenResponse(
                            200, {"access_token": fresh, "refresh_token": "rt.2"}))

    provider = CredentialProvider()
    assert provider.get_access_token() == fresh

    on_disk = json.loads(path.read_text())
    assert on_disk["tokens"]["access_token"] == fresh
    assert on_disk["tokens"]["refresh_token"] == "rt.2"
    assert on_disk["tokens"]["account_id"] == "acct-1"
    assert on_disk["auth_mode"] == "chatgpt"
    assert on_disk["last_refresh"] != "2026-07-03T00:00:00Z"
    # The file carries OAuth secrets, so it must land 0600 with no temp leftovers.
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert list(tmp_path.glob("*.tmp")) == []


def test_provider_refresh_failure_reloads_from_disk(monkeypatch, tmp_path):
    path = _write_auth(tmp_path / "auth.json", _jwt({"exp": time.time() - 100}))
    monkeypatch.setenv(_AUTH_PATH_ENV, str(path))
    monkeypatch.setattr(credmod.httpx, "post",
                        lambda *a, **kw: _FakeTokenResponse(400, {}, text="revoked"))

    provider = CredentialProvider()
    out_of_band = _jwt({"exp": time.time() + 86400})
    _write_auth(path, out_of_band, account_id="acct-1")

    assert provider.get_access_token() == out_of_band


def test_provider_refresh_failure_with_still_expired_disk_token_raises(monkeypatch, tmp_path):
    path = _write_auth(tmp_path / "auth.json", _jwt({"exp": time.time() - 100}))
    monkeypatch.setenv(_AUTH_PATH_ENV, str(path))
    monkeypatch.setattr(credmod.httpx, "post",
                        lambda *a, **kw: _FakeTokenResponse(400, {}, text="revoked"))
    with pytest.raises(CredentialsError):
        CredentialProvider().get_access_token()


def test_provider_expired_without_refresh_token_raises(monkeypatch, tmp_path):
    path = _write_auth(tmp_path / "auth.json", _jwt({"exp": time.time() - 100}),
                       refresh_token=None)
    monkeypatch.setenv(_AUTH_PATH_ENV, str(path))
    with pytest.raises(CredentialsError, match="no refresh token available"):
        CredentialProvider().get_access_token()


def test_provider_persist_disabled_leaves_file_untouched(monkeypatch, tmp_path):
    path = _write_auth(tmp_path / "auth.json", _jwt({"exp": time.time() - 100}))
    monkeypatch.setenv(_AUTH_PATH_ENV, str(path))
    before = path.read_text()
    fresh = _jwt({"exp": time.time() + 86400})
    monkeypatch.setattr(credmod.httpx, "post",
                        lambda *a, **kw: _FakeTokenResponse(200, {"access_token": fresh}))

    provider = CredentialProvider(persist_path="")
    assert provider.get_access_token() == fresh
    assert path.read_text() == before


def test_provider_default_persist_path_is_none_for_inline_creds(monkeypatch):
    monkeypatch.setenv(_INLINE_ENV, json.dumps(_auth_json("inline")))
    assert CredentialProvider._default_persist_path() is None


def test_provider_reload_picks_up_hot_swapped_account(monkeypatch, tmp_path):
    path = _write_auth(tmp_path / "auth.json", _jwt({"exp": time.time() + 86400}),
                       account_id="acct-1")
    monkeypatch.setenv(_AUTH_PATH_ENV, str(path))
    provider = CredentialProvider()
    assert provider.account_id == "acct-1"

    swapped = _jwt({"exp": time.time() + 86400})
    _write_auth(path, swapped, account_id="acct-2")
    provider.reload()

    assert provider.account_id == "acct-2"
    assert provider.get_access_token() == swapped


def test_file_credential_provider_binds_to_its_own_path(tmp_path):
    token = _jwt({"exp": time.time() + 86400})
    path = _write_auth(tmp_path / "slot.json", token, account_id="acct-slot")
    provider = _FileCredentialProvider(str(path))
    assert provider.get_access_token() == token
    assert provider.account_id == "acct-slot"

    swapped = _jwt({"exp": time.time() + 86400})
    _write_auth(path, swapped, account_id="acct-slot-2")
    provider.reload()
    assert provider.account_id == "acct-slot-2"


def test_file_credential_provider_missing_file_raises(tmp_path):
    with pytest.raises(CredentialsError, match="account auth.json not found"):
        _FileCredentialProvider(str(tmp_path / "nope.json"))


# ---------------------------------------------------------------------------
# MultiAccountCredentialProvider — rotation pool
# ---------------------------------------------------------------------------


def _pool(tmp_path, state_path=None):
    t_a = _jwt({"exp": time.time() + 86400, "slot": "a"})
    t_b = _jwt({"exp": time.time() + 86400, "slot": "b"})
    a = _write_auth(tmp_path / "acct_a.json", t_a, account_id="acct-aaaaaaaa")
    b = _write_auth(tmp_path / "acct_b.json", t_b, account_id="acct-bbbbbbbb")
    pool = load_account_pool(f"{a}:{b}", state_path=state_path)
    assert pool is not None
    return pool, t_a, t_b


def test_load_account_pool_builds_two_slots(tmp_path):
    pool, t_a, _ = _pool(tmp_path)
    assert isinstance(pool, MultiAccountCredentialProvider)
    assert pool.get_token_and_account() == (t_a, "acct-aaaaaaaa")
    assert pool.account_id == "acct-aaaaaaaa"


def test_load_account_pool_empty_spec_is_none():
    assert load_account_pool("") is None


def test_load_account_pool_skips_unusable_entries(tmp_path):
    token = _jwt({"exp": time.time() + 86400})
    good = _write_auth(tmp_path / "good.json", token, account_id="acct-good")
    pool = load_account_pool(f"{tmp_path / 'missing.json'}:{good}")
    assert pool is not None
    assert pool.get_token_and_account() == (token, "acct-good")


def test_load_account_pool_all_entries_unusable_is_none(tmp_path):
    assert load_account_pool(str(tmp_path / "missing.json")) is None


def test_load_account_pool_default_entry_uses_env_auth_path(monkeypatch, tmp_path):
    token = _jwt({"exp": time.time() + 86400})
    monkeypatch.setenv(_AUTH_PATH_ENV,
                       str(_write_auth(tmp_path / "auth.json", token, account_id="acct-def")))
    pool = load_account_pool("default")
    assert pool is not None
    assert pool.get_token_and_account() == (token, "acct-def")


def test_pool_requires_at_least_one_provider():
    with pytest.raises(CredentialsError, match="needs >=1 provider"):
        MultiAccountCredentialProvider([])


def test_mark_exhausted_skips_the_cooled_slot(tmp_path):
    pool, t_a, t_b = _pool(tmp_path)

    token, account = pool.get_token_and_account()
    assert (token, account) == (t_a, "acct-aaaaaaaa")

    pool.mark_exhausted(token, 600)
    assert pool.get_token_and_account() == (t_b, "acct-bbbbbbbb")
    assert pool.account_id == "acct-bbbbbbbb"


def test_mark_exhausted_unknown_token_is_a_noop(tmp_path):
    pool, t_a, _ = _pool(tmp_path)
    pool.get_token_and_account()
    pool.mark_exhausted("some-other-token", 600)
    assert pool.get_token_and_account()[0] == t_a


def test_next_reset_at_none_while_a_healthy_slot_exists(tmp_path):
    pool, _, _ = _pool(tmp_path)
    assert pool.next_reset_at() is None
    token, _ = pool.get_token_and_account()
    pool.mark_exhausted(token, 600)
    assert pool.next_reset_at() is None


def test_next_reset_at_is_soonest_cooldown_when_all_cooled(tmp_path):
    pool, _, _ = _pool(tmp_path)
    first, _ = pool.get_token_and_account()
    pool.mark_exhausted(first, 600)
    second, _ = pool.get_token_and_account()
    pool.mark_exhausted(second, 60)

    reset = pool.next_reset_at()
    assert reset is not None
    assert 0 < reset - time.time() <= 60


def test_mark_invalid_drops_slot_permanently(tmp_path):
    pool, t_a, t_b = _pool(tmp_path)
    token, _ = pool.get_token_and_account()
    pool.mark_invalid(token)

    assert pool.get_token_and_account() == (t_b, "acct-bbbbbbbb")
    assert pool.status()["accounts"][0]["invalid"] is True


def test_all_slots_invalid_raises(tmp_path):
    pool, _, _ = _pool(tmp_path)
    first, _ = pool.get_token_and_account()
    pool.mark_invalid(first)
    second, _ = pool.get_token_and_account()
    pool.mark_invalid(second)

    with pytest.raises(CredentialsError, match="are invalid"):
        pool.get_token_and_account()
    assert pool.next_reset_at() is None


def test_penalize_cools_active_slot_and_advances_cursor(tmp_path):
    pool, _, t_b = _pool(tmp_path)
    pool.get_token_and_account()
    pool.penalize(600)
    assert pool.get_token_and_account() == (t_b, "acct-bbbbbbbb")


def test_reload_clears_cooldowns_and_invalids(tmp_path):
    pool, t_a, _ = _pool(tmp_path)
    token, _ = pool.get_token_and_account()
    pool.mark_exhausted(token, 600)
    pool.reload()

    assert pool.status()["accounts"][0]["cooldown_remaining"] == 0
    assert pool.get_token_and_account() == (t_a, "acct-aaaaaaaa")


def test_status_reports_masked_prefixes_and_cooldowns(tmp_path):
    pool, _, _ = _pool(tmp_path)
    token, _ = pool.get_token_and_account()
    pool.mark_exhausted(token, 600)

    status = pool.status()
    assert status["active"] == 0
    assert [a["account_prefix"] for a in status["accounts"]] == [
        "acct-aaa...", "acct-bbb..."]
    assert status["accounts"][0]["cooldown_remaining"] > 0
    assert status["accounts"][1]["cooldown_remaining"] == 0
    assert status["accounts"][1]["invalid"] is False


def test_cooldown_state_persists_across_pool_instances(tmp_path):
    state = tmp_path / "pool_state.json"
    pool, _, _ = _pool(tmp_path, state_path=str(state))
    token, _ = pool.get_token_and_account()
    pool.mark_exhausted(token, 600)

    assert json.loads(state.read_text())["cooldown_until"][0] > time.time()
    assert stat.S_IMODE(os.stat(state).st_mode) == 0o600

    revived, _, revived_t_b = _pool(tmp_path, state_path=str(state))
    assert revived.get_token_and_account() == (revived_t_b, "acct-bbbbbbbb")


def test_pool_state_path_read_from_env(monkeypatch, tmp_path):
    state = tmp_path / "env_state.json"
    monkeypatch.setenv("KAIJU_CODEX_POOL_STATE_PATH", str(state))
    pool, _, _ = _pool(tmp_path)
    token, _ = pool.get_token_and_account()
    pool.mark_exhausted(token, 600)
    assert state.is_file()


def test_pool_corrupt_state_file_is_ignored(tmp_path):
    state = tmp_path / "corrupt.json"
    state.write_text("{not json")
    pool, t_a, _ = _pool(tmp_path, state_path=str(state))
    assert pool.get_token_and_account() == (t_a, "acct-aaaaaaaa")
