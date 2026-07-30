"""Tests for bringing the Docker daemon up before a trajectory runs.

The motivating failure: with the daemon stopped, ``docker image ls -q`` exits 0
with empty output, so the harness reported the agent image as *missing* and told
the operator to ``docker load`` a 13 GB tar already sitting on their disk. These
cover both halves of the fix — starting the daemon, and never telling that lie
again when we cannot.

No test starts, stops, or talks to a real daemon; subprocess is faked throughout.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils import docker_daemon as dd  # noqa: E402


class _Proc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ("WCB_DOCKER_AUTOSTART", "WCB_DOCKER_START_TIMEOUT"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(dd.shutil, "which", lambda name: f"/usr/bin/{name}")


@pytest.fixture
def no_sleep(monkeypatch):
    """Collapse the poll loop's wall-clock so timeout tests stay instant."""
    clock = {"t": 0.0}
    monkeypatch.setattr(dd.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(dd.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    return clock


# ------------------------------------------------------------- daemon_ready

def test_daemon_ready_true_when_info_succeeds(monkeypatch):
    monkeypatch.setattr(dd.subprocess, "run",
                        lambda *a, **k: _Proc(0, "29.3.1\n"))
    assert dd.daemon_ready() is True


def test_daemon_ready_false_when_info_fails(monkeypatch):
    monkeypatch.setattr(dd.subprocess, "run",
                        lambda *a, **k: _Proc(1, "", "Cannot connect to the Docker daemon"))
    assert dd.daemon_ready() is False


def test_daemon_ready_false_when_docker_hangs(monkeypatch):
    """A wedged daemon must not hang the launcher — `docker info` can block
    indefinitely against a half-dead socket, so the call is time-boxed."""
    def _hang(*a, **k):
        raise subprocess.TimeoutExpired(cmd="docker info", timeout=15)

    monkeypatch.setattr(dd.subprocess, "run", _hang)
    assert dd.daemon_ready() is False


def test_daemon_ready_false_without_the_cli(monkeypatch):
    monkeypatch.setattr(dd.shutil, "which", lambda name: None)
    assert dd.daemon_ready() is False


# ------------------------------------------------------------ start_command

def test_start_command_on_macos(monkeypatch):
    monkeypatch.setattr(dd.platform, "system", lambda: "Darwin")
    cmd, hint = dd.start_command()
    assert cmd == ["open", "-ga", "Docker"], cmd
    assert "Docker Desktop" in hint


def test_start_command_on_linux_uses_passwordless_sudo(monkeypatch):
    monkeypatch.setattr(dd.platform, "system", lambda: "Linux")
    monkeypatch.setattr(dd.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(dd.subprocess, "run", lambda *a, **k: _Proc(0))
    cmd, _ = dd.start_command()
    assert cmd == ["sudo", "-n", "systemctl", "start", "docker"], cmd


def test_start_command_on_linux_never_prompts_for_a_password(monkeypatch):
    """A sudo password prompt from inside a TUI launch is worse than a message:
    it is invisible behind the full-screen dashboard and looks like a hang."""
    monkeypatch.setattr(dd.platform, "system", lambda: "Linux")
    monkeypatch.setattr(dd.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(dd.subprocess, "run", lambda *a, **k: _Proc(1))
    cmd, hint = dd.start_command()
    assert cmd is None
    assert "systemctl start docker" in hint


def test_start_command_on_linux_as_root_skips_sudo(monkeypatch):
    monkeypatch.setattr(dd.platform, "system", lambda: "Linux")
    monkeypatch.setattr(dd.os, "geteuid", lambda: 0)
    cmd, _ = dd.start_command()
    assert cmd == ["systemctl", "start", "docker"], cmd


def test_start_command_on_an_unknown_platform(monkeypatch):
    monkeypatch.setattr(dd.platform, "system", lambda: "Plan9")
    cmd, hint = dd.start_command()
    assert cmd is None and "Plan9" in hint


# ------------------------------------------------------------ ensure_daemon

def test_ensure_daemon_noop_when_already_running(monkeypatch):
    monkeypatch.setattr(dd, "daemon_ready", lambda **k: True)
    launched = []
    monkeypatch.setattr(dd.subprocess, "run",
                        lambda *a, **k: launched.append(a) or _Proc(0))
    status = dd.ensure_daemon()
    assert status.ok and status.action == "already-running"
    assert not launched, "started Docker when it was already up"


def test_ensure_daemon_starts_and_waits(monkeypatch, no_sleep):
    """The daemon is not ready the instant `open -ga Docker` returns — Docker
    Desktop takes tens of seconds — so the launch must be followed by a poll."""
    calls = {"n": 0}

    def _ready(**_kw):
        calls["n"] += 1
        return calls["n"] > 4       # not ready until the 5th check

    monkeypatch.setattr(dd, "daemon_ready", _ready)
    monkeypatch.setattr(dd, "start_command",
                        lambda: (["open", "-ga", "Docker"], "hint"))
    launched = []
    monkeypatch.setattr(dd.subprocess, "run",
                        lambda cmd, **k: launched.append(cmd) or _Proc(0))

    progress = []
    status = dd.ensure_daemon(on_progress=progress.append)

    assert status.ok and status.action == "started", status
    assert launched == [["open", "-ga", "Docker"]]
    assert progress and "starting it" in progress[0]


def test_ensure_daemon_times_out_with_an_actionable_message(monkeypatch, no_sleep):
    monkeypatch.setattr(dd, "daemon_ready", lambda **k: False)
    monkeypatch.setattr(dd, "start_command", lambda: (["open", "-ga", "Docker"], "Start Docker Desktop"))
    monkeypatch.setattr(dd.subprocess, "run", lambda *a, **k: _Proc(0))

    status = dd.ensure_daemon(timeout=30)
    assert not status.ok and status.action == "timeout"
    assert "Start Docker Desktop" in status.detail
    assert "WCB_DOCKER_START_TIMEOUT" in status.detail, (
        "an operator on a slow machine needs to know the wait is adjustable"
    )


def test_ensure_daemon_reports_a_missing_cli(monkeypatch):
    monkeypatch.setattr(dd.shutil, "which", lambda name: None)
    status = dd.ensure_daemon()
    assert not status.ok and status.action == "no-cli"
    assert "docker.com" in status.detail, "no install link"


def test_ensure_daemon_honours_the_autostart_opt_out(monkeypatch):
    monkeypatch.setattr(dd, "daemon_ready", lambda **k: False)
    monkeypatch.setenv("WCB_DOCKER_AUTOSTART", "0")
    launched = []
    monkeypatch.setattr(dd.subprocess, "run",
                        lambda *a, **k: launched.append(a) or _Proc(0))
    status = dd.ensure_daemon()
    assert not status.ok and not launched, "opt-out did not prevent the launch"


def test_ensure_daemon_reports_a_failed_launch(monkeypatch):
    monkeypatch.setattr(dd, "daemon_ready", lambda **k: False)
    monkeypatch.setattr(dd, "start_command", lambda: (["open", "-ga", "Docker"], "hint"))
    monkeypatch.setattr(dd.subprocess, "run",
                        lambda *a, **k: _Proc(1, "", "Unable to find application named 'Docker'"))
    status = dd.ensure_daemon()
    assert not status.ok and status.action == "failed"
    assert "Unable to find application" in status.detail


def test_ensure_daemon_ignores_a_junk_timeout_env(monkeypatch, no_sleep):
    """A typo in the env must not make the timeout zero (instant give-up) or
    raise — fall back to the default."""
    monkeypatch.setenv("WCB_DOCKER_START_TIMEOUT", "soon-ish")
    monkeypatch.setattr(dd, "daemon_ready", lambda **k: False)
    monkeypatch.setattr(dd, "start_command", lambda: (["open", "-ga", "Docker"], "hint"))
    monkeypatch.setattr(dd.subprocess, "run", lambda *a, **k: _Proc(0))
    status = dd.ensure_daemon()
    assert status.action == "timeout"
    assert f"{dd.DEFAULT_START_TIMEOUT:.0f}s" in status.detail


# ------------------------------------------- the error the operator actually saw

def test_image_check_blames_the_daemon_not_a_missing_image(monkeypatch):
    """The original bug, pinned. `docker image ls -q` exits 0 with empty output
    when the daemon is down, so an absent daemon was indistinguishable from an
    absent image — and the advice given was to load a 13 GB tar for nothing."""
    from src.utils import docker_utils

    monkeypatch.setattr(docker_utils, "image_present", lambda image: False)
    monkeypatch.setattr(dd, "daemon_ready", lambda **k: False)
    monkeypatch.setattr(dd, "start_command", lambda: (None, "Start Docker Desktop"))

    with pytest.raises(RuntimeError) as excinfo:
        docker_utils.require_image_present("wildclawbench-ubuntu:v1.3")

    message = str(excinfo.value)
    assert "daemon is not reachable" in message, message
    assert "docker load" not in message, (
        "still tells the operator to load a tar they may already have"
    )


def test_image_check_still_reports_a_genuinely_missing_image(monkeypatch):
    """The daemon check must not swallow the real case."""
    from src.utils import docker_utils

    monkeypatch.setattr(docker_utils, "image_present", lambda image: False)
    monkeypatch.setattr(dd, "daemon_ready", lambda **k: True)

    with pytest.raises(RuntimeError) as excinfo:
        docker_utils.require_image_present("wildclawbench-ubuntu:v1.3")

    message = str(excinfo.value)
    assert "not present locally" in message and "docker load" in message, message


# ------------------------------------------------- it is wired into `wcb run`

def test_wcb_starts_docker_before_it_starts_the_run():
    """Static check on the launcher path.

    ``eval/wcb.py`` cannot be driven headlessly (it needs a tty and puts up the
    config form), so this pins the two properties that matter: the daemon check
    exists, and it happens BEFORE ``run_batch.main`` — a check after the run has
    begun is a check that never fires.
    """
    source = (Path(__file__).resolve().parents[1] / "eval" / "wcb.py").read_text()

    assert "ensure_daemon" in source, (
        "the Docker preflight is gone from the wcb launcher path"
    )
    assert source.index("ensure_daemon(") < source.index("run_batch.main("), (
        "Docker is checked after the run starts, which is too late"
    )
    # A failed start must stop the run, not warn and carry on into the
    # misleading "image not present" error further down.
    tail = source[source.index("ensure_daemon("):source.index("run_batch.main(")]
    assert "SystemExit" in tail, "a failed Docker start does not abort the run"
