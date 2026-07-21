"""Codex ChatGPT-plan OAuth credentials: read ``auth.json``, refresh, pool.

The ``codex`` CLI stores credentials at ``$CODEX_HOME/auth.json`` (default
``~/.codex/auth.json``) with this shape::

    {"tokens": {"id_token": "<jwt>", "access_token": "<jwt>",
                "refresh_token": "<opaque>", "account_id": "<uuid>"},
     "last_refresh": "2026-07-21T00:00:00Z",
     "OPENAI_API_KEY": null}

The OAuth refresh grant goes to ``https://auth.openai.com/oauth/token`` with the
public codex client id. The ``refresh_token`` may rotate on refresh, so the
provider persists the rotated token back into the pool file under an
``fcntl.flock`` (same rotation-safety concern as the Claude bridge).

A pooled account = one ``auth.json`` file. The MITM proxy asks the pool for the
currently-available account's ``(access_token, account_id)`` on every request
and stamps them onto the outbound ChatGPT request, giving per-request rotation.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx

_LOG = logging.getLogger(__name__)

# Public codex CLI OAuth client id (ships in every release; required for the
# refresh-token grant). Overridable for staging via env, mirroring codex.
CODEX_CLIENT_ID = os.environ.get("CODEX_APP_SERVER_LOGIN_CLIENT_ID", "app_EMoamEEZ73f0CkXaXp7hrann")
REFRESH_ENDPOINT = os.environ.get("CODEX_REFRESH_TOKEN_URL_OVERRIDE", "https://auth.openai.com/oauth/token")
REFRESH_LEEWAY_SECONDS = 300  # codex refreshes ~5 min before expiry
_DEFAULT_TTL_SECONDS = 3600


class CredentialsError(RuntimeError):
    """Raised when codex credentials cannot be loaded or refreshed."""


def _jwt_exp(token: str) -> Optional[float]:
    """Best-effort ``exp`` (unix seconds) from a JWT access token; None if absent."""
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        exp = payload.get("exp")
        return float(exp) if exp is not None else None
    except Exception:  # noqa: BLE001 - any malformed token -> unknown expiry
        return None


@dataclass
class CodexCredentials:
    access_token: str
    refresh_token: str
    account_id: Optional[str] = None
    id_token: Optional[str] = None
    expires_at: float = 0.0
    account_label: str = ""
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_auth_json(cls, payload: dict, label: str = "") -> "CodexCredentials":
        tokens = payload.get("tokens") if isinstance(payload, dict) else None
        if not isinstance(tokens, dict):
            raise CredentialsError("codex auth.json missing 'tokens' object (not a ChatGPT-plan credential)")
        access = tokens.get("access_token")
        if not access:
            raise CredentialsError("codex auth.json missing tokens.access_token")
        exp = _jwt_exp(access) or (time.time() + _DEFAULT_TTL_SECONDS)
        return cls(
            access_token=access,
            refresh_token=tokens.get("refresh_token") or "",
            account_id=tokens.get("account_id"),
            id_token=tokens.get("id_token"),
            expires_at=exp,
            account_label=label,
            raw=dict(payload),
        )

    def to_auth_json(self) -> dict:
        out = dict(self.raw) if self.raw else {}
        out["tokens"] = {
            "id_token": self.id_token,
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "account_id": self.account_id,
        }
        out["last_refresh"] = datetime.now(timezone.utc).isoformat()
        return out

    def is_expired(self, leeway_seconds: int = REFRESH_LEEWAY_SECONDS) -> bool:
        return time.time() >= self.expires_at - leeway_seconds


def load_codex_credentials(path: str | Path, label: str = "") -> CodexCredentials:
    p = Path(path).expanduser()
    if not p.is_file():
        raise CredentialsError(f"codex credentials file not found: {p}")
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise CredentialsError(f"could not read codex credentials {p}: {e}") from e
    return CodexCredentials.from_auth_json(payload, label=label or f"file:{p}")


def refresh_codex_credentials(
    creds: CodexCredentials,
    *,
    timeout: float = 30.0,
    max_attempts: int = 3,
    backoff_base: float = 1.0,
) -> CodexCredentials:
    """Exchange ``refresh_token`` for a fresh access token (refresh may rotate)."""
    if not creds.refresh_token:
        raise CredentialsError("cannot refresh: codex credential has no refresh_token")
    body = {
        "client_id": CODEX_CLIENT_ID,
        "grant_type": "refresh_token",
        "refresh_token": creds.refresh_token,
        "scope": "openid profile email",
    }
    last_error: Optional[Exception] = None
    r = None
    for attempt in range(1, max_attempts + 1):
        try:
            with httpx.Client(timeout=timeout) as client:
                r = client.post(REFRESH_ENDPOINT, json=body, headers={"content-type": "application/json"})
        except (httpx.HTTPError, OSError) as e:
            last_error = e
            if attempt >= max_attempts:
                raise CredentialsError(f"codex OAuth refresh network error after {attempt} tries: {e}") from e
            time.sleep(backoff_base * (2 ** (attempt - 1)))
            continue
        if r.status_code == 200:
            break
        if 400 <= r.status_code < 500:
            raise CredentialsError(
                f"codex OAuth refresh failed (non-retryable): HTTP {r.status_code} {r.text[:200]}"
            )
        last_error = CredentialsError(f"codex OAuth refresh HTTP {r.status_code} {r.text[:200]}")
        if attempt >= max_attempts:
            raise last_error
        time.sleep(backoff_base * (2 ** (attempt - 1)))

    if r is None or r.status_code != 200:
        raise CredentialsError(f"codex OAuth refresh failed: {last_error}")
    try:
        data = r.json()
    except ValueError as e:
        raise CredentialsError(f"codex OAuth refresh returned non-JSON: {e}") from e

    access = data.get("access_token")
    if not access:
        raise CredentialsError(f"codex OAuth refresh missing access_token: {data}")
    expires_in = data.get("expires_in")
    exp = _jwt_exp(access)
    if exp is None:
        exp = time.time() + (int(expires_in) if expires_in else _DEFAULT_TTL_SECONDS)
    return CodexCredentials(
        access_token=access,
        refresh_token=data.get("refresh_token") or creds.refresh_token,
        account_id=creds.account_id,
        id_token=data.get("id_token") or creds.id_token,
        expires_at=exp,
        account_label=creds.account_label,
        raw=creds.raw,
    )


class CodexCredentialProvider:
    """Single-account provider: lazy load from ``auth.json`` + auto-refresh.

    ``get_credentials()`` returns a live ``CodexCredentials`` (refreshing when
    within the leeway window). Refreshes persist back into the pool file under
    an ``fcntl.flock`` so concurrent processes don't lose the rotated token.
    """

    def __init__(self, path: str | Path, label: str = "") -> None:
        self._path = Path(path).expanduser()
        self._label = label or f"file:{self._path}"
        self._lock = threading.Lock()
        self._creds: Optional[CodexCredentials] = None

    @property
    def label(self) -> str:
        return self._label

    def token_prefix(self) -> Optional[str]:
        with self._lock:
            return self._creds.access_token[:16] if self._creds else None

    def force_reload(self) -> None:
        with self._lock:
            self._creds = None

    def get_credentials(self) -> CodexCredentials:
        with self._lock:
            if self._creds is None:
                self._creds = load_codex_credentials(self._path, label=self._label)
            if not self._creds.is_expired():
                return self._creds
            self._creds = self._refresh_locked()
            return self._creds

    def _refresh_locked(self) -> CodexCredentials:
        import fcntl

        lock_path = self._path.with_suffix(self._path.suffix + ".lock")
        try:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        with open(lock_path, "w") as lock_fh:
            try:
                fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
            except OSError as e:
                _LOG.warning("flock failed on %s: %s; proceeding unlocked", lock_path, e)
            # Another process may have refreshed while we waited for the lock.
            try:
                fresh = load_codex_credentials(self._path, label=self._label)
                if not fresh.is_expired():
                    return fresh
            except CredentialsError:
                fresh = self._creds  # fall back to in-memory
            _LOG.info("refreshing codex OAuth token for %s", self._label)
            refreshed = refresh_codex_credentials(fresh or self._creds)
            try:
                self._path.write_text(json.dumps(refreshed.to_auth_json()), encoding="utf-8")
                os.chmod(self._path, 0o600)
            except OSError as e:
                _LOG.warning("could not persist refreshed codex creds to %s: %s", self._path, e)
            return refreshed


@dataclass
class _AccountSlot:
    provider: CodexCredentialProvider
    label: str
    exhausted_until: float = 0.0
    invalid: bool = False

    def is_available(self, now: Optional[float] = None) -> bool:
        if self.invalid:
            return False
        now = now if now is not None else time.time()
        return now >= self.exhausted_until


class MultiAccountCredentialProvider:
    """Pool of codex accounts with per-request rotation and cap tracking.

    ``get_credentials()`` returns the first available account's live
    credentials (with ``account_label`` set). The proxy calls
    ``mark_account_exhausted(label, until)`` on a 429 cap so the next request
    rotates to another account.
    """

    def __init__(self, slots: list[_AccountSlot]) -> None:
        if not slots:
            raise CredentialsError("MultiAccountCredentialProvider needs >= 1 slot")
        self._slots = slots
        self._lock = threading.Lock()

    def get_credentials(self) -> CodexCredentials:
        with self._lock:
            slot = self._select_slot_locked()
        try:
            creds = slot.provider.get_credentials()
        except CredentialsError:
            with self._lock:
                slot.invalid = True
            return self.get_credentials()
        creds.account_label = slot.label
        return creds

    def _select_slot_locked(self) -> _AccountSlot:
        now = time.time()
        for slot in self._slots:
            if slot.is_available(now):
                return slot
        soonest = min((s.exhausted_until for s in self._slots if not s.invalid), default=0.0)
        delta = max(0.0, soonest - now)
        raise CredentialsError(
            f"all {len(self._slots)} codex accounts exhausted; soonest reset in {delta:.0f}s"
        )

    def force_reload(self) -> None:
        with self._lock:
            for slot in self._slots:
                slot.provider.force_reload()
                slot.exhausted_until = 0.0
                slot.invalid = False

    def mark_account_exhausted(self, label: str, until_unix: float) -> None:
        with self._lock:
            for slot in self._slots:
                if slot.label == label:
                    slot.exhausted_until = max(slot.exhausted_until, until_unix)
                    _LOG.info("codex account %s exhausted until +%.0fs", label, max(0.0, until_unix - time.time()))
                    return

    def mark_account_invalid(self, label: str) -> None:
        with self._lock:
            for slot in self._slots:
                if slot.label == label:
                    slot.invalid = True
                    _LOG.warning("codex account %s marked invalid", label)
                    return

    def next_reset_at(self) -> Optional[float]:
        with self._lock:
            now = time.time()
            if any(s.is_available(now) for s in self._slots):
                return None
            future = [s.exhausted_until for s in self._slots if not s.invalid]
            return min(future) if future else None

    def snapshot(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "label": s.label,
                    "token_prefix": s.provider.token_prefix(),
                    "invalid": s.invalid,
                    "exhausted_until": s.exhausted_until,
                    "available": s.is_available(),
                }
                for s in self._slots
            ]


def load_account_pool(spec: str) -> Optional[MultiAccountCredentialProvider]:
    """Parse a ``WCB_CX_ACCOUNT_POOL`` spec (colon-separated ``auth.json`` paths)."""
    if not spec:
        return None
    slots: list[_AccountSlot] = []
    for raw in spec.split(":"):
        entry = raw.strip()
        if not entry:
            continue
        label = f"file:{entry}"
        slots.append(_AccountSlot(provider=CodexCredentialProvider(Path(entry), label=label), label=label))
    if not slots:
        return None
    return MultiAccountCredentialProvider(slots)
