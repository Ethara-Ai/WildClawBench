"""Run-key-scoped sidecar auth: the mode table, the wiring, and the hook.

The sidecar's inbound auth and the harness's per-run cost attribution are the
same mechanism seen from two sides. The main agent tags its usage rows by
carrying its run key as the bearer, so whatever the sidecar is willing to accept
decides whether those rows carry a run key at all. These tests pin that the
default is the run-key-scoped mode, that the legacy master-key proxy survives as
an explicit opt-in, and that the settings an existing .env already has on disk
keep meaning what they meant.

The accept/reject behaviour of the hook itself was also verified against the
pinned image (litellm 1.88.1) end to end: a minted key is admitted on both
/v1/chat/completions and /v1/messages, the master key and an unshaped bearer are
refused with 401, /health/liveliness stays public, and the admitted key arrives
on metadata.user_api_key for the usage callback to read. What is unit-testable
without docker is everything below.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.utils import litellm_run_key_auth as hook  # noqa: E402
from src.utils import litellm_sidecar as sidecar  # noqa: E402


def _run_key(task_id: str = "aaron_garcia", tail: str = "0" * 32) -> str:
    return f"wcb::{task_id}::{tail}"


@pytest.fixture(autouse=True)
def _clean_auth_env(monkeypatch):
    monkeypatch.delenv("WCB_SIDECAR_NO_MASTER_KEY", raising=False)
    monkeypatch.delenv("WCB_SIDECAR_MASTER_KEY", raising=False)


class _FakeCompleted:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture
def capture_run(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(cmd, *args, **kwargs):
        calls.append(list(cmd))
        return _FakeCompleted()

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(sidecar.subprocess, "run", fake_run)
    return calls


# ---------------------------------------------------------------------------
# Section A — the mode table
# ---------------------------------------------------------------------------


class TestAuthModeResolution:
    def test_default_is_run_key_scoped(self):
        assert sidecar.sidecar_auth_mode() == sidecar.AUTH_MODE_RUN_KEY
        assert sidecar.run_key_auth_enforced() is True

    def test_no_master_key_switch_is_a_no_op_alias(self, monkeypatch):
        # The line production already ships. It used to buy attribution by
        # removing auth entirely; the default now provides the attribution, so
        # the switch has to resolve to exactly the same mode and nothing else.
        monkeypatch.setenv("WCB_SIDECAR_NO_MASTER_KEY", "1")
        assert sidecar.sidecar_auth_mode() == sidecar.AUTH_MODE_RUN_KEY

    def test_master_key_is_an_explicit_opt_in(self, monkeypatch):
        monkeypatch.setenv("WCB_SIDECAR_MASTER_KEY", "1")
        assert sidecar.sidecar_auth_mode() == sidecar.AUTH_MODE_MASTER_KEY
        assert sidecar.run_key_auth_enforced() is False

    def test_no_master_key_wins_when_both_are_set(self, monkeypatch):
        monkeypatch.setenv("WCB_SIDECAR_NO_MASTER_KEY", "1")
        monkeypatch.setenv("WCB_SIDECAR_MASTER_KEY", "1")
        assert sidecar.sidecar_auth_mode() == sidecar.AUTH_MODE_RUN_KEY

    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", " 1 "])
    def test_truthy_spellings_opt_in(self, monkeypatch, value):
        monkeypatch.setenv("WCB_SIDECAR_MASTER_KEY", value)
        assert sidecar.sidecar_auth_mode() == sidecar.AUTH_MODE_MASTER_KEY

    @pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "maybe"])
    def test_untruthy_spellings_do_not_opt_in(self, monkeypatch, value):
        monkeypatch.setenv("WCB_SIDECAR_MASTER_KEY", value)
        assert sidecar.sidecar_auth_mode() == sidecar.AUTH_MODE_RUN_KEY

    def test_master_key_env_alone_does_not_flip_the_mode(self, monkeypatch):
        # .env.example ships KENSEI_LITELLM_MASTER_KEY uncommented, so treating
        # a present master key as an opt-in would hand every existing operator
        # the legacy mode back.
        monkeypatch.setenv("KENSEI_LITELLM_MASTER_KEY", "sk-talos-litellm")
        monkeypatch.setenv("LITELLM_MASTER_KEY", "sk-talos-litellm")
        assert sidecar.sidecar_auth_mode() == sidecar.AUTH_MODE_RUN_KEY


# ---------------------------------------------------------------------------
# Section B — the hook's own rule
# ---------------------------------------------------------------------------


class TestRunKeyShape:
    @pytest.mark.parametrize("task_id", [
        "aaron_garcia", "ruth_flynn", "task-1", "task.a", "t", "a" * 120,
    ])
    def test_accepts_every_shape_the_runner_mints(self, task_id):
        assert hook.is_run_key(_run_key(task_id, "a5277b7b108349cda74467c48609a0c1"))

    def test_accepts_a_bearer_prefixed_value(self):
        assert hook.is_run_key(f"Bearer {_run_key()}")
        assert hook.normalize_bearer(f"Bearer {_run_key()}") == _run_key()

    @pytest.mark.parametrize("bad", [
        "sk-talos-litellm",
        "sk-litellm",
        "sk-wcb-oauth-stub",
        "",
        "   ",
        "wcb::",
        "wcb::t::",
        "wcb::t::abc",
        "wcb::t::" + "a" * 31,
        "wcb::t::" + "a" * 33,
        "wcb::t::" + "A" * 32,
        "wcb::t::" + "g" * 32,
        "wcb::" + "0" * 32,
        "WCB::t::" + "a" * 32,
        " wcb::t::" + "a" * 32 + " extra",
        "prefixwcb::t::" + "a" * 32,
    ])
    def test_rejects_everything_else(self, bad):
        assert hook.is_run_key(bad) is False

    @pytest.mark.parametrize("bad", [None, 1, object(), b"wcb::t::x"])
    def test_rejects_non_strings(self, bad):
        assert hook.is_run_key(bad) is False

    def test_the_master_key_is_not_a_run_key(self):
        # The whole point: the credential every concurrent run used to share is
        # the one this mode must refuse.
        assert hook.is_run_key("sk-talos-litellm") is False


def _auth(api_key):
    return asyncio.run(hook.user_api_key_auth(request=None, api_key=api_key))


class TestHookContract:
    def test_rejection_raises_and_names_the_way_back(self):
        with pytest.raises(Exception) as excinfo:
            _auth("sk-talos-litellm")
        message = str(excinfo.value)
        assert "per-run keys only" in message
        assert "WCB_SIDECAR_MASTER_KEY=1" in message

    def test_rejection_does_not_echo_the_whole_bearer(self):
        # The sidecar's stderr lands in gateway.log, which ships in the bundle.
        secret = "sk-super-secret-value-do-not-log-in-full"
        with pytest.raises(Exception) as excinfo:
            _auth(secret)
        assert secret not in str(excinfo.value)

    def test_accepted_key_is_echoed_back_as_the_token(self, monkeypatch):
        # metadata.user_api_key is where the usage callback reads the run key
        # from, so the hook has to hand the key back rather than invent one.
        captured = {}

        class _Token:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        monkeypatch.setattr(hook, "UserAPIKeyAuth", _Token)
        monkeypatch.setattr(hook, "LitellmUserRoles", None)
        key = _run_key()
        _auth(f"Bearer {key}")
        assert captured["api_key"] == key

    def test_signature_takes_request_and_api_key(self):
        # litellm calls this by keyword on the OSS path and positionally through
        # the enterprise wrapper, so the parameter names are part of the
        # contract, not an implementation detail.
        import inspect
        params = list(inspect.signature(hook.user_api_key_auth).parameters)
        assert params == ["request", "api_key"]


# ---------------------------------------------------------------------------
# Section C — yaml and container wiring
# ---------------------------------------------------------------------------


class TestConfigWiring:
    def test_default_yaml_names_the_hook_and_carries_no_master_key(self):
        yaml = sidecar.build_litellm_config_yaml(bedrock_arn="arn:x")
        assert f"  custom_auth: {sidecar.RUN_KEY_AUTH_HOOK}\n" in yaml
        assert "master_key" not in yaml

    def test_master_key_mode_yaml_has_no_hook(self, monkeypatch):
        monkeypatch.setenv("WCB_SIDECAR_MASTER_KEY", "1")
        yaml = sidecar.build_litellm_config_yaml(bedrock_arn="arn:x")
        assert "  master_key: os.environ/LITELLM_MASTER_KEY\n" in yaml
        assert "custom_auth" not in yaml

    def test_the_named_module_is_the_file_that_gets_mounted(self):
        # litellm resolves custom_auth relative to the config file's directory,
        # so the module name in the yaml and the basename mounted beside
        # config.yaml are one fact expressed twice. If they drift the proxy
        # boots into a config naming a module that is not there.
        path = Path(sidecar.run_key_auth_module_path())
        assert path.is_file()
        assert path.stem == sidecar.RUN_KEY_AUTH_MODULE
        assert sidecar.RUN_KEY_AUTH_HOOK == f"{path.stem}.user_api_key_auth"
        assert hasattr(hook, "user_api_key_auth")


class TestStartLitellmWiring:
    def _start(self, tmp_path, monkeypatch):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("model_list: []\n")
        monkeypatch.setattr(sidecar, "wait_for_litellm_healthy", lambda *a, **k: True)
        sidecar.start_litellm(
            container_name="litellm-test",
            network="kensei-net",
            host_config_path=str(cfg),
            master_key="sk-talos-litellm",
        )

    def test_default_mounts_the_hook_and_withholds_the_master_key(
            self, tmp_path, monkeypatch, capture_run):
        self._start(tmp_path, monkeypatch)
        argv = capture_run[1] if capture_run[0][:3] == ["docker", "rm", "-f"] else capture_run[0]
        joined = " ".join(argv)
        assert f":/app/{sidecar.RUN_KEY_AUTH_MODULE}.py:ro" in joined
        assert sidecar.run_key_auth_module_path() in joined
        # Present in the container, LITELLM_MASTER_KEY turns litellm's own
        # master-key auth back on alongside the hook — a second accepted
        # credential that belongs to no run.
        assert "LITELLM_MASTER_KEY" not in joined
        assert "sk-talos-litellm" not in joined

    def test_master_key_mode_sets_the_env_and_mounts_no_hook(
            self, tmp_path, monkeypatch, capture_run):
        monkeypatch.setenv("WCB_SIDECAR_MASTER_KEY", "1")
        self._start(tmp_path, monkeypatch)
        argv = capture_run[1] if capture_run[0][:3] == ["docker", "rm", "-f"] else capture_run[0]
        joined = " ".join(argv)
        assert "LITELLM_MASTER_KEY=sk-talos-litellm" in joined
        assert f"/app/{sidecar.RUN_KEY_AUTH_MODULE}.py" not in joined

    def test_argv_injection_through_the_master_key_is_refused_in_both_modes(
            self, tmp_path, monkeypatch, capture_run):
        # The guard predates the modes; run-key mode does not wire the value in,
        # but it must not become the reason the check stopped running either.
        cfg = tmp_path / "config.yaml"
        cfg.write_text("model_list: []\n")
        monkeypatch.setattr(sidecar, "wait_for_litellm_healthy", lambda *a, **k: True)
        for mode_env in (None, "WCB_SIDECAR_MASTER_KEY"):
            if mode_env:
                monkeypatch.setenv(mode_env, "1")
            with pytest.raises(ValueError, match="argv-flag injection"):
                sidecar.start_litellm(
                    container_name="litellm-test",
                    network="kensei-net",
                    host_config_path=str(cfg),
                    master_key="--rm",
                )


class TestProbeBearer:
    def test_run_key_mode_probes_with_a_key_the_sidecar_accepts(self):
        bearer = sidecar.sidecar_probe_bearer("sk-talos-litellm")
        assert bearer != "sk-talos-litellm"
        assert hook.is_run_key(bearer)

    def test_probe_key_is_tagged_so_it_joins_no_run(self):
        assert sidecar.sidecar_probe_bearer("mk").startswith("wcb::__probe__::")

    def test_each_probe_mints_a_fresh_key(self):
        assert sidecar.sidecar_probe_bearer("mk") != sidecar.sidecar_probe_bearer("mk")

    def test_master_key_mode_probes_with_the_master_key(self, monkeypatch):
        monkeypatch.setenv("WCB_SIDECAR_MASTER_KEY", "1")
        assert sidecar.sidecar_probe_bearer("sk-talos-litellm") == "sk-talos-litellm"


# ---------------------------------------------------------------------------
# Section D — the batch-start warning
# ---------------------------------------------------------------------------


class TestParallelAttributionWarning:
    def _warn(self, caplog, parallel):
        from eval.run_batch import _warn_if_master_key_auth_degrades_attribution
        args = types.SimpleNamespace(parallel=parallel)
        with caplog.at_level(logging.WARNING):
            _warn_if_master_key_auth_degrades_attribution(args)
        return " ".join(r.getMessage() for r in caplog.records)

    def test_fires_on_master_key_mode_with_parallelism(self, monkeypatch, caplog):
        monkeypatch.setenv("WCB_SIDECAR_MASTER_KEY", "1")
        message = self._warn(caplog, 4)
        assert "master-key mode is ON" in message
        assert "--parallel 4" in message
        assert "faketime" in message
        assert "WCB_SIDECAR_MASTER_KEY" in message

    def test_silent_in_the_default_mode(self, caplog):
        assert self._warn(caplog, 8) == ""

    def test_silent_under_the_no_master_key_alias(self, monkeypatch, caplog):
        monkeypatch.setenv("WCB_SIDECAR_NO_MASTER_KEY", "1")
        monkeypatch.setenv("WCB_SIDECAR_MASTER_KEY", "1")
        assert self._warn(caplog, 8) == ""

    def test_fires_at_parallel_one_because_the_co_tenant_is_another_process(
            self, monkeypatch, caplog):
        # Was test_silent_when_nothing_overlaps, which assumed --parallel is a
        # census of who shares the log. It is not: script/run.sh:716 hardcodes
        # `--parallel 1` on every eval/run_batch.py it launches and gets its
        # concurrency by fanning processes out (run_k_for_model_bg,
        # run_parallel_tasks) onto the single WCB_SHARED_SIDECAR_USAGE_LOG that
        # bootstrap_shared_sidecar exported. Under the old gate the warning was
        # therefore unreachable from the canonical entry point at any fan-out
        # width, and equally silent for two operators on two terminals. Nothing
        # a single process can read tells it whether it is alone.
        monkeypatch.setenv("WCB_SIDECAR_MASTER_KEY", "1")
        message = self._warn(caplog, 1)
        assert "master-key mode is ON" in message
        assert "--parallel 1" in message
        assert "UNCONDITIONAL" in message

    @pytest.mark.parametrize("parallel", [None, 0])
    def test_tolerates_a_missing_or_zero_parallelism(self, monkeypatch, caplog, parallel):
        # The subject is tolerance, not silence: an absent or zero --parallel
        # must not raise and must not suppress the warning. It reads as 1, the
        # value the canonical entry point always passes anyway.
        monkeypatch.setenv("WCB_SIDECAR_MASTER_KEY", "1")
        assert "--parallel 1" in self._warn(caplog, parallel)
