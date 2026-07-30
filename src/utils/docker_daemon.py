"""Bring the Docker daemon up before a trajectory runs.

Every backend runs the agent in a container, so a stopped daemon fails the run —
but it fails it *confusingly*. ``docker image ls -q`` exits 0 with empty output
when it cannot reach the daemon, so :func:`src.utils.docker_utils.image_present`
reads "daemon down" as "image absent" and the operator is told to
``docker load`` a 13 GB tar that is already on their disk. Starting Docker
Desktop for them removes the failure entirely; :func:`daemon_ready` also lets the
image check tell the truth when it cannot.

Deliberately never prompts for a password: on Linux the daemon is only started
when sudo is already passwordless (the policy ``script/run.sh`` uses for its own
auto-installs). Otherwise the operator gets the exact command to run.
"""
from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
import time
from typing import Callable, NamedTuple, Optional

logger = logging.getLogger(__name__)

#: How long to wait for a cold Docker Desktop. It routinely needs 30-60s on
#: macOS, more on a busy machine, so the default is generous — the wait ends as
#: soon as the daemon answers, and only a genuinely broken install pays it.
DEFAULT_START_TIMEOUT = 180.0

_POLL_INTERVAL = 2.0
_INFO_TIMEOUT = 15.0


class DaemonStatus(NamedTuple):
    """Outcome of :func:`ensure_daemon`."""

    ok: bool
    #: "already-running" | "started" | "no-cli" | "unsupported" | "timeout" | "failed"
    action: str
    detail: str

    @property
    def started_by_us(self) -> bool:
        return self.action == "started"


def docker_cli_present() -> bool:
    return shutil.which("docker") is not None


def daemon_ready(timeout: float = _INFO_TIMEOUT) -> bool:
    """True when the daemon answers.

    ``docker info`` rather than ``docker ps``: it is the check that fails
    cleanly when the CLI is installed but the daemon socket is dead, which is
    the state this module exists to repair.
    """
    if not docker_cli_present():
        return False
    try:
        proc = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True, text=True, timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return proc.returncode == 0


def _sudo_prefix() -> Optional[list[str]]:
    """``sudo -n`` when it works without a password, [] when already root."""
    if os.geteuid() == 0:
        return []
    if not shutil.which("sudo"):
        return None
    try:
        probe = subprocess.run(["sudo", "-n", "true"], capture_output=True,
                               timeout=5)
    except (subprocess.TimeoutExpired, OSError):
        return None
    return ["sudo", "-n"] if probe.returncode == 0 else None


def start_command() -> tuple[Optional[list[str]], str]:
    """(command to start the daemon, human instruction if we cannot).

    The instruction is returned in both cases so callers can always tell the
    operator what to do if the command does not take effect.
    """
    system = platform.system()
    if system == "Darwin":
        # -g: do not steal focus. -a Docker: Docker Desktop, which starts the
        # daemon. Returns as soon as the app is launching, not when it is ready.
        return ["open", "-ga", "Docker"], "Start Docker Desktop from Applications"
    if system == "Linux":
        hint = "Start it with `sudo systemctl start docker`"
        if not shutil.which("systemctl"):
            return None, hint
        sudo = _sudo_prefix()
        if sudo is None:
            # Never prompt: a password prompt from inside a TUI launch is worse
            # than a clear message.
            return None, hint + " (passwordless sudo is not available here)"
        return sudo + ["systemctl", "start", "docker"], hint
    return None, f"Start the Docker daemon manually (unrecognised platform {system!r})"


def _timeout_from_env(default: float = DEFAULT_START_TIMEOUT) -> float:
    raw = os.environ.get("WCB_DOCKER_START_TIMEOUT", "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning("Ignoring non-numeric WCB_DOCKER_START_TIMEOUT=%r", raw)
        return default
    return value if value > 0 else default


def ensure_daemon(
    timeout: Optional[float] = None,
    on_progress: Optional[Callable[[str], None]] = None,
) -> DaemonStatus:
    """Make sure the daemon is up, starting it if it is not.

    ``on_progress`` is called with short status lines so a caller can keep the
    operator informed during what may be a 60-second wait; a silent minute reads
    as a hang.

    Set ``WCB_DOCKER_AUTOSTART=0`` to only check and report, never launch, and
    ``WCB_DOCKER_START_TIMEOUT`` to change how long the wait is.
    """
    say = on_progress or (lambda _msg: None)

    if not docker_cli_present():
        return DaemonStatus(
            False, "no-cli",
            "docker CLI not found in PATH. Install Docker Desktop: "
            "https://www.docker.com/products/docker-desktop",
        )

    if daemon_ready():
        return DaemonStatus(True, "already-running", "Docker daemon is up")

    if os.environ.get("WCB_DOCKER_AUTOSTART", "1").strip() == "0":
        return DaemonStatus(
            False, "failed",
            "Docker daemon is not running and WCB_DOCKER_AUTOSTART=0",
        )

    command, hint = start_command()
    if command is None:
        return DaemonStatus(False, "unsupported",
                            f"Docker daemon is not running. {hint}")

    say("Docker is not running — starting it…")
    logger.info("Starting Docker daemon: %s", " ".join(command))
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=60)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return DaemonStatus(False, "failed",
                            f"Could not launch Docker ({exc}). {hint}")
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()[:200]
        return DaemonStatus(
            False, "failed",
            f"Could not launch Docker: {err or 'command failed'}. {hint}",
        )

    limit = _timeout_from_env() if timeout is None else timeout
    deadline = time.monotonic() + limit
    waited = 0.0
    while time.monotonic() < deadline:
        if daemon_ready(timeout=min(_INFO_TIMEOUT, max(1.0, deadline - time.monotonic()))):
            return DaemonStatus(True, "started",
                                f"Docker daemon came up in {waited:.0f}s")
        time.sleep(_POLL_INTERVAL)
        waited += _POLL_INTERVAL
        # Only from 10s on: a daemon that wakes immediately should not produce
        # a wall of progress lines.
        if waited >= 10 and waited % 10 < _POLL_INTERVAL:
            say(f"still waiting for Docker… {waited:.0f}s")

    return DaemonStatus(
        False, "timeout",
        f"Docker did not become ready within {limit:.0f}s. {hint}, then retry. "
        f"(Raise WCB_DOCKER_START_TIMEOUT if this machine is just slow.)",
    )
