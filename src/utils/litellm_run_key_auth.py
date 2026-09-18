"""Inbound auth for the LiteLLM sidecar: accept per-run keys, nothing else.

Mounted into the sidecar container at /app/litellm_run_key_auth.py and named
from the proxy YAML as:

    general_settings:
      custom_auth: litellm_run_key_auth.user_api_key_auth

`get_instance_fn` resolves that value relative to the config file, which
`start_litellm` mounts at /app/config.yaml, so the module has to sit beside it
in /app — the same placement the usage, headroom, stream and overflow-guard
callbacks already use.

Why a hook at all. The runner mints `wcb::<task_id>::<uuid4hex>` per attempt and
sends it as the sidecar bearer; the usage callback reads it back off the request
and stamps every row with it, which is what makes per-run cost attribution exact
under parallelism. That only worked while the sidecar enforced no inbound auth,
because a master key is the only bearer a master-key proxy accepts. So the
harness had a choice between attribution and authentication, and picked
attribution. This hook removes the choice: the run key becomes the credential,
so the bearer that identifies a run is also the bearer that admits it.

What is checked. The `wcb::` prefix, and a 32-char lowercase hex tail — that tail
is `uuid.uuid4().hex` from the runner's single mint site, and it is the part an
outsider cannot produce. The task-id segment in the middle is deliberately
matched loosely: it is not a secret, it never gates anything, and pinning a
charset here would turn an unusual task id into a hard 401 on a live run. A
bearer that fails the check raises, which litellm's auth exception handler
renders as a 401 naming the rejected prefix.

What is NOT checked, stated plainly: this validates the SHAPE of a run key, not
membership in the set of keys the current batch actually minted. A container
already on the sidecar's network can therefore mint a well-formed key of its own
and spend against it, and can name another attempt's task id while doing so. Two
things bound that. The network is per-batch and internal, so the only members
are that batch's own agent containers, its mocks and its bridges. And the
32-hex tail is not guessable, so forged traffic lands on a run key that matches
no attempt and is visible as such in usage.jsonl rather than silently joining a
real run's totals. Narrowing to the exact minted set was considered and left
out: under the shared-sidecar bootstrap the proxy is started by bash before any
run key exists, so the set would have to arrive through a file the sidecar
re-reads per request, and a stale or unreadable file fails closed on live
traffic. The shape check is the part that is correct without that machinery.

`x-wcb-run-key` is NOT accepted as a credential here. It stays what it has always
been: an attribution-only channel for callers that already got past auth.
"""

from __future__ import annotations

import re
from typing import Any

try:
    from litellm.proxy._types import (  # type: ignore[import-not-found]
        LitellmUserRoles,
        UserAPIKeyAuth,
    )
except Exception:  # pragma: no cover - litellm only present inside the sidecar
    LitellmUserRoles = None  # type: ignore[assignment]
    UserAPIKeyAuth = None  # type: ignore[assignment]


# `wcb::<task_id>::<uuid4().hex>` — see OpenClawAgent.run_task, the single mint
# site. The tail is anchored to exactly 32 lowercase hex because that is what
# uuid4().hex is; the middle segment is whatever the task is called.
RUN_KEY_PATTERN = re.compile(r"^wcb::[^\s:][^\s]{0,200}::[0-9a-f]{32}$")


def is_run_key(candidate: str) -> bool:
    """Whether a bearer matches the minted run-key contract.

    Split out from the hook so the rule can be exercised without litellm
    installed — the host test environment has no litellm, only the sidecar
    image does.
    """
    if not isinstance(candidate, str):
        return False
    key = candidate.strip()
    if key.startswith("Bearer "):
        key = key[7:].strip()
    return bool(RUN_KEY_PATTERN.match(key))


def normalize_bearer(candidate: str) -> str:
    """The bare key, with any `Bearer ` prefix removed."""
    key = (candidate or "").strip()
    if key.startswith("Bearer "):
        key = key[7:].strip()
    return key


async def user_api_key_auth(request: Any, api_key: str) -> Any:
    """litellm `custom_auth` hook. Returns a token, or raises to 401.

    Called by `_user_api_key_auth_builder` before every other auth path — the
    master-key comparison, the key DB and the JWT reader all sit behind it — and
    its return value is final. Both call sites are covered by this signature:
    the OSS branch passes `request=`/`api_key=` by keyword, the enterprise
    wrapper passes them positionally.

    The accepted key is echoed back as the token's `api_key`, which is what puts
    it on `metadata.user_api_key` for the usage callback to read. The role
    matches what the proxy hands out when no master key is configured, so
    everything downstream of auth behaves as it did in keyless mode.
    """
    key = normalize_bearer(api_key)
    if not is_run_key(key):
        shown = key[:12] if key else "<none>"
        raise Exception(
            f"sidecar accepts per-run keys only; rejected bearer {shown!r}. "
            "This sidecar is in run-key mode: the bearer must be the "
            "wcb::<task_id>::<uuid4> key the runner minted for this attempt. "
            "Set WCB_SIDECAR_MASTER_KEY=1 to go back to master-key auth."
        )
    if UserAPIKeyAuth is None:  # pragma: no cover - host-side import guard
        raise RuntimeError("litellm is not importable; this hook runs in the sidecar")
    if LitellmUserRoles is not None:
        return UserAPIKeyAuth(api_key=key, user_role=LitellmUserRoles.INTERNAL_USER)
    return UserAPIKeyAuth(api_key=key)
