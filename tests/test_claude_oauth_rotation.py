"""Regression tests for OAuth rotation hardening (issues C1/H1/H2,
docs/OAUTH_ROTATION_ISSUES.md).

C1: transient refresh failures back a pool slot off instead of permanently
    invalidating it; genuine 4xx refresh failures still invalidate.
H1: after the flock re-read, refresh uses the NEWEST credential pair (on-disk
    when another process rotated it; in-memory when our write-back failed).
H2: pool error-attribution matches the exact erred token against each
    provider's recent-token history, surviving mid-flight rotation.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils.claude_oauth import credentials
from src.utils.claude_oauth.credentials import (
    TRANSIENT_REFRESH_BACKOFF_SECONDS,
    OAuthCredentials,
    TransientCredentialsError,
    _FileCredentialProvider,
    load_account_pool,
)


def _ms(offset_seconds: float) -> int:
    return int((time.time() + offset_seconds) * 1000)


def _write_creds(path: Path, expires_at_ms: int, access: str, refresh: str | None = None) -> Path:
    payload = {
        "claudeAiOauth": {
            "accessToken": access,
            "refreshToken": refresh or ("rt_" + access),
            "expiresAt": expires_at_ms,
            "scopes": ["user:inference"],
            "subscriptionType": "max",
        }
    }
    Path(path).write_text(json.dumps(payload))
    return Path(path)


class _FakeResp:
    def __init__(self, status_code: int, body=None, text: str = ""):
        self.status_code = status_code
        self._body = body
        self.text = text or (json.dumps(body) if body is not None else "")

    def json(self):
        if self._body is None:
            raise ValueError("no json body")
        return self._body


class _FakeClient:
    """Stands in for httpx.Client inside credentials.refresh_credentials.

    ``responses`` is a queue of _FakeResp objects or exceptions to raise;
    ``posted`` captures each request's JSON payload.
    """

    posted: list = []
    responses: list = []

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, json=None, headers=None):
        _FakeClient.posted.append(json)
        if not _FakeClient.responses:
            raise AssertionError("unexpected refresh call: no fake response queued")
        item = _FakeClient.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _ok_refresh(access: str, refresh: str) -> _FakeResp:
    return _FakeResp(200, {"access_token": access, "refresh_token": refresh, "expires_in": 3600})


@pytest.fixture
def fake_refresh(monkeypatch):
    _FakeClient.posted = []
    _FakeClient.responses = []
    monkeypatch.setattr(credentials.httpx, "Client", _FakeClient)
    # No-op the retry backoff so network-failure paths don't sleep for real.
    monkeypatch.setattr(credentials.time, "sleep", lambda s: None)
    return _FakeClient


# ── C1: transient vs permanent refresh failures ─────────────────────────────


def test_transient_refresh_backs_off_slot_instead_of_invalidating(tmp_path, fake_refresh):
    p1 = _write_creds(tmp_path / "a1.json", _ms(-3600), "sk-oat-a")  # expired -> refresh path
    p2 = _write_creds(tmp_path / "a2.json", _ms(3600), "sk-oat-b")
    pool = load_account_pool(f"{p1}:{p2}")
    fake_refresh.responses = [httpx.ConnectError("boom")] * 3  # exhaust all attempts

    before = time.time()
    token = pool.get_access_token()

    assert token == "sk-oat-b"  # failed over
    slot1 = pool._slots[0]
    assert not slot1.invalid  # NOT permanently killed
    assert slot1.exhausted_until >= before + TRANSIENT_REFRESH_BACKOFF_SECONDS - 1
    assert not slot1.is_available(before + 1)
    assert slot1.is_available(before + TRANSIENT_REFRESH_BACKOFF_SECONDS + 1)


def test_refresh_4xx_still_invalidates_slot(tmp_path, fake_refresh):
    p1 = _write_creds(tmp_path / "a1.json", _ms(-3600), "sk-oat-a")
    p2 = _write_creds(tmp_path / "a2.json", _ms(3600), "sk-oat-b")
    pool = load_account_pool(f"{p1}:{p2}")
    fake_refresh.responses = [_FakeResp(401, text="refresh token revoked")]

    token = pool.get_access_token()

    assert token == "sk-oat-b"
    assert pool._slots[0].invalid  # permanent path preserved


def test_transient_error_type_from_network_failure(fake_refresh):
    creds = OAuthCredentials("a", "r", _ms(-10), [], "max")
    fake_refresh.responses = [httpx.ConnectError("no route")] * 3
    with pytest.raises(TransientCredentialsError):
        credentials.refresh_credentials(creds)


def test_all_transient_failures_terminate_when_backoff_expires_midcall(
    tmp_path, fake_refresh, monkeypatch
):
    # Ping-pong scenario: each time.time() call advances the clock 40s, so a
    # slot's 60s transient backoff has always "expired" again by the time the
    # other slot finishes failing. The per-call tried-set must terminate the
    # call with CredentialsError instead of retrying slots forever.
    p1 = _write_creds(tmp_path / "a1.json", _ms(-3600), "sk-oat-a")
    p2 = _write_creds(tmp_path / "a2.json", _ms(-3600), "sk-oat-b")
    pool = load_account_pool(f"{p1}:{p2}")
    fake_refresh.responses = [httpx.ConnectError("boom")] * 12

    state = {"now": time.time()}

    def warped_time():
        state["now"] += 40.0
        return state["now"]

    monkeypatch.setattr(credentials.time, "time", warped_time)

    with pytest.raises(credentials.CredentialsError):
        pool.get_access_token()
    # Transient failures must still not permanently invalidate either slot.
    assert not pool._slots[0].invalid
    assert not pool._slots[1].invalid


def test_missing_file_slot_still_invalidated(tmp_path):
    bad = tmp_path / "nonexistent.json"
    p2 = _write_creds(tmp_path / "a2.json", _ms(3600), "sk-oat-b")
    pool = load_account_pool(f"{bad}:{p2}")

    assert pool.get_access_token() == "sk-oat-b"
    assert pool._slots[0].invalid  # config errors remain permanent


# ── H1: refresh from the newest credential pair ─────────────────────────────


def test_refresh_uses_rotated_on_disk_refresh_token(tmp_path, fake_refresh):
    p = _write_creds(tmp_path / "a.json", _ms(-7200), "sk-oat-old", refresh="rt_OLD")
    prov = _FileCredentialProvider(str(p))
    # Stale pre-rotation pair in memory; another process has since rotated the
    # file to a newer (but also expired) pair.
    prov._creds = OAuthCredentials("sk-oat-old", "rt_OLD", _ms(-7200), [], "max")
    _write_creds(p, _ms(-120), "sk-oat-mid", refresh="rt_DISK")
    fake_refresh.responses = [_ok_refresh("sk-oat-new", "rt_NEW")]

    token = prov.get_access_token()

    assert token == "sk-oat-new"
    # The refresh grant must have used the on-disk (newest) refresh token.
    assert fake_refresh.posted[0]["refresh_token"] == "rt_DISK"
    on_disk = json.loads(p.read_text())["claudeAiOauth"]
    assert on_disk["refreshToken"] == "rt_NEW"
    assert on_disk["accessToken"] == "sk-oat-new"


def test_refresh_keeps_in_memory_pair_when_disk_is_older(tmp_path, fake_refresh):
    # Failed-write-back scenario: memory holds the rotated (live) pair, disk
    # holds the consumed one. Refresh must use the IN-MEMORY refresh token.
    p = _write_creds(tmp_path / "a.json", _ms(-7200), "sk-oat-disk", refresh="rt_DISK_STALE")
    prov = _FileCredentialProvider(str(p))
    prov._creds = OAuthCredentials("sk-oat-mem", "rt_MEM_LIVE", _ms(-120), [], "max")
    fake_refresh.responses = [_ok_refresh("sk-oat-new", "rt_NEW")]

    token = prov.get_access_token()

    assert token == "sk-oat-new"
    assert fake_refresh.posted[0]["refresh_token"] == "rt_MEM_LIVE"


def test_concurrent_refresher_pair_adopted_without_second_refresh(tmp_path, fake_refresh):
    # While we waited on the flock another process wrote a NON-expired pair:
    # adopt it and do not hit the refresh endpoint at all.
    p = _write_creds(tmp_path / "a.json", _ms(-3600), "sk-oat-old")
    prov = _FileCredentialProvider(str(p))
    prov._creds = OAuthCredentials("sk-oat-old", "rt_OLD", _ms(-3600), [], "max")
    _write_creds(p, _ms(3600), "sk-oat-fresh", refresh="rt_FRESH")

    token = prov.get_access_token()

    assert token == "sk-oat-fresh"
    assert fake_refresh.posted == []  # no refresh call made


# ── H2: exact-token attribution across rotation ─────────────────────────────


def test_cap_attribution_survives_token_rotation(tmp_path, fake_refresh):
    p1 = _write_creds(tmp_path / "a1.json", _ms(3600), "sk-oat-t1")
    pool = load_account_pool(str(p1))
    slot = pool._slots[0]

    t1 = pool.get_access_token()
    assert t1 == "sk-oat-t1"

    # Rotate: expire both the file and the in-memory pair, refresh vends t2.
    _write_creds(p1, _ms(-3600), "sk-oat-t1")
    slot.provider._creds.expires_at_ms = _ms(-3600)
    fake_refresh.responses = [_ok_refresh("sk-oat-t2", "rt2")]
    assert pool.get_access_token() == "sk-oat-t2"

    # A 429 for the OLD token arrives now. Current-token prefix matching can
    # no longer attribute it; only the recent-token history can.
    reset_at = time.time() + 500
    pool.mark_account_exhausted("sk-oat-t1", reset_at)
    assert slot.exhausted_until == pytest.approx(reset_at, abs=1)


def test_token_history_dedupes_repeat_vends(tmp_path, fake_refresh):
    p = _write_creds(tmp_path / "a.json", _ms(3600), "sk-oat-first")
    prov = _FileCredentialProvider(str(p))
    for _ in range(10):  # would flush a non-deduped deque(maxlen=8)
        assert prov.get_access_token() == "sk-oat-first"

    _write_creds(p, _ms(-3600), "sk-oat-first")
    prov._creds.expires_at_ms = _ms(-3600)
    fake_refresh.responses = [_ok_refresh("sk-oat-second", "rt2")]
    assert prov.get_access_token() == "sk-oat-second"

    assert prov.knows_token("sk-oat-first")
    assert prov.knows_token("sk-oat-second")
    assert not prov.knows_token("sk-oat-never-vended")
