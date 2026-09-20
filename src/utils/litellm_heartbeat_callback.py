"""LiteLLM proxy liveness heartbeat — the turn-stall guard's second opinion.

Mounted read-only into the sidecar at /app/litellm_heartbeat_callback.py and
registered from the LiteLLM YAML beside the usage callback:

    litellm_settings:
      callbacks: ["litellm_usage_callback.proxy_handler_instance", ...,
                  "litellm_heartbeat_callback.heartbeat_instance"]

WHY THIS EXISTS
---------------
The host-side turn-stall guard (src/agents/openclaw/runner.py
``_turn_wait_outcome``) decides "this in-flight request is silently dead" from
COMPLETION-time evidence only: rows the usage callback appends to
LITELLM_USAGE_LOG_PATH when a request succeeds or fails. A request that is
streaming happily for twenty minutes writes no row for its whole duration, so a
healthy long call is indistinguishable from a wedged one.

  cite: alpha 2026-09-19 — 3 healthy 1P reps killed at 600s+poll; koji's row
  landed 3 min AFTER the kill, proving the request had never stopped moving.

This module gives the guard a per-chunk liveness signal: a zero-byte file whose
mtime is bumped at request start and again as chunks flow. The guard ORs that
mtime against its existing row-count check, so an advancing heartbeat resets the
stall clock while a genuinely wedged request (no chunks, no rows) still trips it.

HARD RULES
----------
  * SINK SEPARATION (user m0130 lineage, mirrored from
    litellm_stream_callback.py:24-26): this is a THIRD sink. It writes ONLY
    under WCB_HEARTBEAT_DIR (default /var/litellm_usage/heartbeats — a
    SUBDIRECTORY of the usage mount, never the usage log itself). It NEVER
    writes to LITELLM_USAGE_LOG_PATH, usage_oauth.jsonl, the headroom JSONL or
    WCB_STREAM_LOG_PATH. Schemas never merge; there is no schema here at all.
  * ALWAYS-ON, DISPLAY-INDEPENDENT. The live-token display tap
    (litellm_stream_callback.StreamTap) is gated by ``enable_stream_callback``
    and, above it, run.sh's D6 single-run-only rule. The stall guard runs on
    EVERY run, so the heartbeat cannot ride that gate — this slim second
    CustomLogger is registered whenever the usage callback is (i.e. whenever
    the guard has a usage log to read at all) and is deliberately NOT the
    display feature. Turning WCB_STREAM on or off changes nothing here.
  * FAIL-SILENT. Every write is wrapped; a heartbeat failure must never break
    or delay a stream. The first failure per process is reported once on
    stderr and then the writer self-disables for good.
  * PASS-THE-ORIGINAL-OBJECT (R5). The streaming hook yields the EXACT chunk
    object it received, unconditionally, on every code path — the ``yield`` is
    never inside our try/except.

This file is standalone by design: the sidecar container only has this module
(same rule as the usage/headroom/stream callbacks), so nothing here may import
from the repo. The host-side reader is
``OpenClawAgent._heartbeat_mtime`` and the two key schemes are pinned against
each other by tests/test_stall_guard_heartbeat.py.
"""
from __future__ import annotations

import os
import sys
import time
from typing import Any, AsyncGenerator

try:
    from litellm.integrations.custom_logger import CustomLogger  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - litellm only present inside the sidecar
    class CustomLogger:  # type: ignore[no-redef]
        pass


# Sibling of the usage log, so the existing `-v <host>:/var/litellm_usage`
# bind mount carries it host-side with no second mount to forget. start_litellm
# passes the container path explicitly; the default keeps this module honest if
# it is ever loaded without one.
_DEFAULT_DIR = "/var/litellm_usage/heartbeats"

# Only `wcb::<task_id>::<uuid4hex>` bearers are ever used as a heartbeat name —
# same prefix rule litellm_usage_callback._extract_run_key enforces, so a real
# credential can never become a filename. No run key (master-key sidecar mode)
# means no heartbeat, which is exactly right: the stall guard is inert in that
# mode too (OpenClawAgent._run_key_bearer_live).
_RUN_KEY_PREFIX = "wcb::"

# Per-chunk touch throttle. A long turn emits tens of thousands of chunks and
# the guard's threshold floor is 600s, so 1s granularity is four orders of
# magnitude finer than it needs to be while costing one utime() per second
# instead of one per token. 0 disables the throttle (tests).
_MIN_INTERVAL_DEFAULT_S = 1.0

_last_touch: dict[str, float] = {}
_disabled = False


def heartbeat_dir() -> str:
    return (os.environ.get("WCB_HEARTBEAT_DIR", "").strip() or _DEFAULT_DIR)


def safe_name(run_key: str) -> str:
    """Filename for a run key. Kept byte-stable with the host-side reader
    (OpenClawAgent._heartbeat_name) — a drift here silently disables the
    heartbeat half of the guard, so the two are pinned by a shared test."""
    return "".join(
        ch if (ch.isalnum() or ch in "._-") else "_" for ch in run_key
    )[:200]


def _min_interval() -> float:
    raw = os.environ.get("WCB_HEARTBEAT_MIN_INTERVAL_S", "").strip()
    if not raw:
        return _MIN_INTERVAL_DEFAULT_S
    try:
        v = float(raw)
    except ValueError:
        return _MIN_INTERVAL_DEFAULT_S
    return v if v >= 0 else _MIN_INTERVAL_DEFAULT_S


def touch(run_key: str) -> None:
    """Bump the run's heartbeat mtime. Never raises, never blocks a stream."""
    global _disabled
    if _disabled or not run_key or not run_key.startswith(_RUN_KEY_PREFIX):
        return
    now = time.time()
    gap = _min_interval()
    if gap and (now - _last_touch.get(run_key, 0.0)) < gap:
        return
    try:
        directory = heartbeat_dir()
        path = os.path.join(directory, safe_name(run_key))
        try:
            os.utime(path, None)
        except OSError:
            # First touch of this run (or the dir vanished): create, then set
            # the mtime explicitly — reopening an existing file in append mode
            # without writing does NOT move mtime on every filesystem.
            os.makedirs(directory, exist_ok=True)
            with open(path, "a", encoding="utf-8"):
                pass
            os.utime(path, None)
        _last_touch[run_key] = now
    except Exception as exc:  # noqa: BLE001 - liveness must never propagate
        _disabled = True
        try:
            sys.stderr.write(
                f"[litellm_heartbeat_callback] DEBUG heartbeat disabled for "
                f"this process ({exc!r}); the stall guard falls back to "
                f"usage-row counting\n"
            )
        except Exception:  # noqa: BLE001 - even the warning is best-effort
            pass


def _run_key_of(user_api_key_dict: Any, request_data: Any) -> str:
    """The attempt's run key, or "" when this request carries none.

    Channel order mirrors litellm_usage_callback._extract_run_key, minus the
    post-call-only kwargs shapes this hook never sees:

    1. ``user_api_key_dict.api_key`` — under run-key auth the accepted bearer
       IS the run key and litellm_run_key_auth echoes it back onto the token
       (src/utils/litellm_run_key_auth.py:107-119). THE channel for main-agent
       traffic.
    2. ``metadata.user_api_key`` — the same value as litellm puts it on the
       request dict.
    3. ``x-wcb-run-key`` / ``authorization`` / ``x-api-key`` headers — the
       explicit attribution channel in-container helpers (subagent director,
       audio-extract skill) use; survives litellm's header redaction.
    """
    try:
        candidates = [getattr(user_api_key_dict, "api_key", None)]
        data = request_data if isinstance(request_data, dict) else {}
        md = data.get("metadata")
        if isinstance(md, dict):
            candidates.append(md.get("user_api_key"))
        psr = data.get("proxy_server_request")
        header_dicts = [
            md.get("headers") if isinstance(md, dict) else None,
            psr.get("headers") if isinstance(psr, dict) else None,
        ]
        for raw in header_dicts:
            if not isinstance(raw, dict):
                continue
            for header in ("x-wcb-run-key", "authorization", "x-api-key"):
                candidates.append(raw.get(header))
        for value in candidates:
            if not isinstance(value, str):
                continue
            if value.startswith("Bearer "):
                value = value[7:]
            if value.startswith(_RUN_KEY_PREFIX):
                return value
    except Exception:  # noqa: BLE001 - extraction never breaks a request
        pass
    return ""


class HeartbeatTap(CustomLogger):
    """Liveness-only observer. Writes no rows and shapes no requests."""

    async def async_pre_call_hook(
        self,
        user_api_key_dict: Any,
        cache: Any,
        data: dict,
        call_type: str,
    ) -> None:
        """Request-start touch.

        Returns None ALWAYS: litellm replaces the request dict only when a
        pre-call hook returns one, so this hook is a strict no-op on the
        request. It is registered last, after the headroom compressor and the
        overflow guard, both of which DO shape ``data`` — order is irrelevant
        to us but staying last keeps their documented ordering untouched.

        The start touch is what covers a bridge-internal buffered retry: a
        re-issue means bytes stopped flowing, and the reissued attempt's own
        start touch re-arms the clock.
        """
        try:
            touch(_run_key_of(user_api_key_dict, data))
        except Exception:  # noqa: BLE001 - defence in depth; touch() is already safe
            pass
        return None

    async def async_post_call_streaming_iterator_hook(
        self,
        user_api_key_dict: Any,
        response: Any,
        request_data: dict,
    ) -> AsyncGenerator[Any, None]:
        """Per-chunk touch. Pure pass-through (R5)."""
        try:
            run_key = _run_key_of(user_api_key_dict, request_data)
        except Exception:  # noqa: BLE001
            run_key = ""
        if run_key:
            try:
                touch(run_key)
            except Exception:  # noqa: BLE001
                run_key = ""
        async for chunk in response:
            if run_key:
                try:
                    touch(run_key)
                except Exception:  # noqa: BLE001 - stop trying, keep streaming
                    run_key = ""
            # R5: forward the ORIGINAL object, unconditionally.
            yield chunk


# Name referenced from the LiteLLM YAML:
#   callbacks: [..., "litellm_heartbeat_callback.heartbeat_instance"]
heartbeat_instance = HeartbeatTap()
