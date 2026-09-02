"""Tests for the two 2026-08 harness fixes.

Fix 1 — usage attribution: per-attempt ``wcb::`` run keys tagged by the usage
callback and matched exactly by extract_usage_from_litellm_log, with the
legacy time window ONLY as fallback.

Fix 2 — turn completion: ``run_incomplete`` computed from planned-vs-executed
turns, stamped into score.json, and excluded from every averaging consumer.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.utils.grading import extract_usage_from_litellm_log
from src.utils import litellm_usage_callback as cb
from src.agents.base import AgentExecution


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, REPO / "script" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _row(ts: datetime, run_key: str | None = None, **over):
    row = {
        "ts": ts.isoformat(),
        "model": "bedrock/opus",
        "kind": "agent",
        "input_tokens": 10,
        "output_tokens": 20,
        "cache_read_tokens": 1000,
        "cache_write_tokens": 100,
        "total_tokens": 1130,
        "audio_seconds": 0.0,
        "cost_usd": 0.5,
        "duration_s": 1.0,
    }
    if run_key:
        row["run_key"] = run_key
    row.update(over)
    return row


NOW = datetime(2026, 8, 20, 12, 0, 0, tzinfo=timezone.utc)


def _write_log(tmp_path, rows):
    p = tmp_path / "usage.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return p


class TestRunKeyExtraction:
    KEY = "wcb::task_a::abc123"

    def test_tagged_rows_win_over_window(self, tmp_path):
        rows = [
            _row(NOW, run_key=self.KEY),
            _row(NOW, run_key="wcb::task_b::other"),
            _row(NOW),
        ]
        log = _write_log(tmp_path, rows)
        out = extract_usage_from_litellm_log(
            log, NOW.timestamp() - 10, NOW.timestamp() + 10, run_key=self.KEY)
        assert out["request_count"] == 1
        assert out["usage_source"] == "litellm_run_key"
        assert out["cost_usd"] == 0.5

    def test_tagged_match_ignores_window_bounds(self, tmp_path):
        far = NOW + timedelta(hours=3)
        log = _write_log(tmp_path, [_row(far, run_key=self.KEY)])
        out = extract_usage_from_litellm_log(
            log, NOW.timestamp(), NOW.timestamp() + 1, run_key=self.KEY)
        assert out["request_count"] == 1
        assert out["usage_source"] == "litellm_run_key"

    def test_window_fallback_when_no_tagged_rows(self, tmp_path):
        rows = [_row(NOW), _row(NOW + timedelta(seconds=1))]
        log = _write_log(tmp_path, rows)
        out = extract_usage_from_litellm_log(
            log, NOW.timestamp() - 5, NOW.timestamp() + 5, run_key=self.KEY)
        assert out["request_count"] == 2
        assert out["usage_source"] == "litellm"

    def test_legacy_call_without_run_key_unchanged(self, tmp_path):
        rows = [
            _row(NOW),
            _row(NOW + timedelta(hours=2)),
            _row(NOW, kind="preflight"),
            _row(NOW, kind="failure"),
        ]
        log = _write_log(tmp_path, rows)
        out = extract_usage_from_litellm_log(
            log, NOW.timestamp() - 5, NOW.timestamp() + 5)
        assert out["request_count"] == 1
        assert out["usage_source"] == "litellm"

    def test_preflight_and_failure_rows_never_counted_even_tagged(self, tmp_path):
        rows = [
            _row(NOW, run_key=self.KEY, kind="preflight"),
            _row(NOW, run_key=self.KEY, kind="failure"),
            _row(NOW, run_key=self.KEY),
        ]
        log = _write_log(tmp_path, rows)
        out = extract_usage_from_litellm_log(
            log, NOW.timestamp() - 5, NOW.timestamp() + 5, run_key=self.KEY)
        assert out["request_count"] == 1


class TestCallbackRunKeyTagging:
    def _kwargs(self, headers):
        return {"model": "m", "secret_fields": {"raw_headers": headers}}

    def test_bearer_wcb_key_extracted(self):
        k = self._kwargs({"authorization": "Bearer wcb::t::123"})
        assert cb._extract_run_key(k) == "wcb::t::123"

    def test_x_api_key_wcb_extracted(self):
        k = self._kwargs({"x-api-key": "wcb::t::456"})
        assert cb._extract_run_key(k) == "wcb::t::456"

    def test_explicit_header_wins(self):
        k = self._kwargs({"x-wcb-run-key": "wcb::t::explicit",
                          "authorization": "Bearer wcb::t::bearer"})
        assert cb._extract_run_key(k) == "wcb::t::explicit"

    def test_real_credentials_never_returned(self):
        for headers in (
            {"authorization": "Bearer sk-litellm"},
            {"authorization": "Bearer sk-real-master-key"},
            {"x-api-key": "sk-ant-secret"},
            {},
        ):
            assert cb._extract_run_key(self._kwargs(headers)) == ""

    def test_missing_secret_fields_safe(self):
        assert cb._extract_run_key({}) == ""
        assert cb._extract_run_key({"secret_fields": None}) == ""

    def test_litellm_1_88_user_api_key_channel(self):
        """secret_fields is None on the pinned image (litellm 1.88.1); in
        keyless mode the raw bearer passes through metadata.user_api_key."""
        k = {"model": "m", "secret_fields": None,
             "litellm_params": {"metadata": {"user_api_key": "wcb::t::raw"}}}
        assert cb._extract_run_key(k) == "wcb::t::raw"
        k = {"model": "m",
             "litellm_params": {"metadata": {"user_api_key_hash": "wcb::t::h"}}}
        assert cb._extract_run_key(k) == "wcb::t::h"

    def test_litellm_1_88_metadata_headers_channel(self):
        k = {"model": "m",
             "litellm_params": {"metadata": {"headers": {
                 "x-wcb-run-key": "wcb::t::hdr", "user-agent": "curl"}}}}
        assert cb._extract_run_key(k) == "wcb::t::hdr"

    def test_litellm_1_88_proxy_server_request_channel(self):
        k = {"model": "m",
             "litellm_params": {"proxy_server_request": {"headers": {
                 "x-wcb-run-key": "wcb::t::psr"}}}}
        assert cb._extract_run_key(k) == "wcb::t::psr"

    def test_hashed_master_key_never_returned(self):
        k = {"model": "m",
             "litellm_params": {"metadata": {
                 "user_api_key": "88a145080d3b3b6d7a33a668f5d3b1a6",
                 "user_api_key_hash": "88a145080d3b3b6d7a33a668f5d3b1a6",
                 "headers": {"user-agent": "curl"}}}}
        assert cb._extract_run_key(k) == ""

    def test_write_row_includes_run_key(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cb, "_PATH", str(tmp_path / "u.jsonl"))
        kwargs = {
            "model": "m",
            "secret_fields": {"raw_headers": {"authorization": "Bearer wcb::t::x"}},
        }
        cb._write_row(kwargs, {"usage": {"prompt_tokens": 5, "completion_tokens": 7}},
                      None, None)
        row = json.loads((tmp_path / "u.jsonl").read_text().strip())
        assert row["run_key"] == "wcb::t::x"

    def test_failure_row_records_error_and_run_key(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cb, "_PATH", str(tmp_path / "u.jsonl"))
        kwargs = {
            "model": "m",
            "exception": ValueError("boom " + "x" * 400),
            "litellm_params": {"metadata": {"user_api_key": "wcb::t::fk"}},
        }
        cb._write_failure_row(kwargs, None, None)
        row = json.loads((tmp_path / "u.jsonl").read_text().strip())
        assert row["kind"] == "failure"
        assert row["error_class"] == "ValueError"
        assert row["error"].startswith("boom ") and len(row["error"]) <= 300
        assert row["run_key"] == "wcb::t::fk"

    def test_write_row_omits_run_key_when_untagged(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cb, "_PATH", str(tmp_path / "u.jsonl"))
        cb._write_row({"model": "m"},
                      {"usage": {"prompt_tokens": 5, "completion_tokens": 7}},
                      None, None)
        row = json.loads((tmp_path / "u.jsonl").read_text().strip())
        assert "run_key" not in row


class TestAgentExecutionFields:
    def test_new_fields_default_backcompat(self):
        ex = AgentExecution(elapsed_time=1.0)
        assert ex.turns_planned is None
        assert ex.recovery_turn_fired is False
        assert ex.turns_completed == 1

    def test_incomplete_shape(self):
        ex = AgentExecution(elapsed_time=1.0, turns_completed=5, turns_planned=9)
        assert ex.turns_completed < ex.turns_planned


class TestPassSummaryExclusion:
    @pytest.fixture()
    def rb(self):
        sys.path.insert(0, str(REPO / "eval"))
        import run_batch
        return run_batch

    def _entries(self, rb):
        good = rb._pass_summary_entry(1, {"overall_score": 0.8,
                                          "rubric_weights_percentage": 80.0}, None)
        bad_scores = {"overall_score": 0.2, "rubric_weights_percentage": 20.0,
                      "run_incomplete": True, "turns_planned": 9,
                      "turns_completed": 3}
        bad = rb._pass_summary_entry(2, bad_scores, None)
        return good, bad

    def test_entry_carries_flag(self, rb):
        good, bad = self._entries(rb)
        assert "run_incomplete" not in good
        assert bad["run_incomplete"] is True
        assert bad["turns_planned"] == 9

    def test_doc_excludes_incomplete(self, rb, monkeypatch):
        monkeypatch.delenv("WCB_INCLUDE_INCOMPLETE_RUNS", raising=False)
        good, bad = self._entries(rb)
        doc = rb._pass_summary_doc("claude", [good, bad])
        assert doc["runs"] == 2
        assert doc["runs_used"] == 1
        assert doc["runs_excluded_incomplete"] == 1
        assert doc["average_reward"] == pytest.approx(0.8)
        assert len(doc["per_run"]) == 2

    def test_doc_override_env_includes_all(self, rb, monkeypatch):
        monkeypatch.setenv("WCB_INCLUDE_INCOMPLETE_RUNS", "1")
        good, bad = self._entries(rb)
        doc = rb._pass_summary_doc("claude", [good, bad])
        assert "runs_excluded_incomplete" not in doc
        assert doc["average_reward"] == pytest.approx(0.5)

    def test_doc_no_flags_identical_to_legacy(self, rb, monkeypatch):
        monkeypatch.delenv("WCB_INCLUDE_INCOMPLETE_RUNS", raising=False)
        good, _ = self._entries(rb)
        doc = rb._pass_summary_doc("claude", [good])
        assert "runs_used" not in doc
        assert "runs_excluded_incomplete" not in doc
        assert doc["average_reward"] == pytest.approx(0.8)


class TestRebuildScriptParity:
    def test_rebuild_port_excludes_incomplete(self, monkeypatch):
        monkeypatch.delenv("WCB_INCLUDE_INCOMPLETE_RUNS", raising=False)
        rps = _load_script("rebuild_pass_summary")
        good = rps._pass_summary_entry(1, {"overall_score": 0.8,
                                           "rubric_weights_percentage": 80.0}, None)
        bad = rps._pass_summary_entry(2, {"overall_score": 0.2,
                                          "rubric_weights_percentage": 20.0,
                                          "run_incomplete": True,
                                          "turns_planned": 9,
                                          "turns_completed": 3}, None)
        doc = rps._pass_summary_doc("claude", [good, bad])
        assert doc["runs_excluded_incomplete"] == 1
        assert doc["average_reward"] == pytest.approx(0.8)


class TestEvalSkipGate:
    """A broken/incomplete trajectory must never reach the eval phase: pytest
    grading and the LLM judge both cost money and the score is excluded from
    averages anyway."""

    @pytest.fixture()
    def rb(self):
        sys.path.insert(0, str(REPO / "eval"))
        import run_batch
        return run_batch

    def test_reason_for_incomplete_run(self, rb, monkeypatch):
        monkeypatch.delenv("WCB_GRADE_INCOMPLETE_RUNS", raising=False)
        r = {"run_incomplete": True, "turns_planned": 9, "turns_completed": 3}
        assert "3 of 9" in rb._eval_skip_reason(r)

    def test_no_reason_for_complete_run(self, rb, monkeypatch):
        monkeypatch.delenv("WCB_GRADE_INCOMPLETE_RUNS", raising=False)
        r = {"run_incomplete": False, "turns_planned": 9, "turns_completed": 9}
        msgs = [{"message": {"role": "assistant", "content": []}}]
        assert rb._eval_skip_reason(r, msgs) is None

    def test_reason_for_empty_trajectory(self, rb, monkeypatch):
        monkeypatch.delenv("WCB_GRADE_INCOMPLETE_RUNS", raising=False)
        assert "no assistant messages" in rb._eval_skip_reason({}, [])
        user_only = [{"message": {"role": "user", "content": []}}]
        assert "no assistant messages" in rb._eval_skip_reason({}, user_only)

    def test_override_env_disables_gate(self, rb, monkeypatch):
        monkeypatch.setenv("WCB_GRADE_INCOMPLETE_RUNS", "1")
        r = {"run_incomplete": True, "turns_planned": 9, "turns_completed": 3}
        assert rb._eval_skip_reason(r, []) is None

    def _grade(self, rb, monkeypatch, tmp_path, result):
        called = {"n": 0}

        def fake_run_grading(**kwargs):
            called["n"] += 1
            return {"overall_score": 1.0}

        monkeypatch.setattr(rb, "run_grading", fake_run_grading)
        task = {"task_id": "t1", "automated_checks": "assert True"}
        out = rb.grade_the_task(
            "t1", str(tmp_path / "ws"), tmp_path, task, result)
        return called["n"], out

    def test_grading_skipped_for_incomplete(self, rb, monkeypatch, tmp_path):
        monkeypatch.delenv("WCB_GRADE_INCOMPLETE_RUNS", raising=False)
        n, out = self._grade(rb, monkeypatch, tmp_path,
                             {"run_incomplete": True, "turns_planned": 9,
                              "turns_completed": 3})
        assert n == 0
        assert "run incomplete" in out["eval_skipped"]
        assert "eval skipped" in out["scores"]["error"]
        assert out["scores"]["overall_score"] is None
        assert out["scores"]["run_incomplete"] is True
        assert out["scores"]["turns_planned"] == 9
        on_disk = json.loads((tmp_path / "score.json").read_text())
        assert on_disk["run_incomplete"] is True

    def test_grading_runs_for_complete(self, rb, monkeypatch, tmp_path):
        monkeypatch.delenv("WCB_GRADE_INCOMPLETE_RUNS", raising=False)
        n, out = self._grade(rb, monkeypatch, tmp_path,
                             {"run_incomplete": False, "turns_planned": 9,
                              "turns_completed": 9})
        assert n == 1
        assert "eval_skipped" not in out

    def test_grading_runs_for_incomplete_with_override(
            self, rb, monkeypatch, tmp_path):
        monkeypatch.setenv("WCB_GRADE_INCOMPLETE_RUNS", "1")
        n, out = self._grade(rb, monkeypatch, tmp_path,
                             {"run_incomplete": True, "turns_planned": 9,
                              "turns_completed": 3})
        assert n == 1


class TestMergeExclusion:
    def test_merge_stats_skip_incomplete(self, tmp_path, monkeypatch):
        monkeypatch.delenv("WCB_INCLUDE_INCOMPLETE_RUNS", raising=False)
        mps = _load_script("merge_pass_summaries")
        docs = []
        for i, (pct, incomplete) in enumerate([(80.0, False), (20.0, True)], 1):
            per_run = [{"run_index": 1, "include_multimodal": True,
                        "test_weights_percentage": pct,
                        "rubric_weights_percentage": pct}]
            if incomplete:
                per_run[0]["run_incomplete"] = True
            p = tmp_path / f"ps{i}.json"
            p.write_text(json.dumps({"model": "claude", "runs": 1,
                                     "average_test_weights_percentage": pct,
                                     "average_rubric_weights_percentage": pct,
                                     "per_run": per_run}))
            docs.append(p)
        merged = mps.merge_pass_summaries(docs, dedup=False, extended=False)
        assert merged["runs"] == 2
        assert merged["runs_excluded_incomplete"] == 1
        assert merged["average_rubric_weights_percentage"] == pytest.approx(80.0)
        assert merged["per_run"][1]["run_incomplete"] is True


class TestAgentBearerWiring:
    """The wire-level test whose absence let the inert-fix bug through: proves
    the run key actually rides the bearer (or deliberately does not)."""

    def _agent(self, master_key):
        from src.agents.openclaw.runner import OpenClawAgent
        a = OpenClawAgent(gateway_port=1, litellm_master_key=master_key)
        a._run_keys["t1"] = "wcb::t1::deadbeef"
        return a

    def test_production_default_master_key_keeps_master_key(self, monkeypatch):
        monkeypatch.delenv("WCB_SIDECAR_NO_MASTER_KEY", raising=False)
        a = self._agent("sk-talos-litellm")
        assert a._agent_bearer("t1") == "sk-talos-litellm"
        assert a._run_key_bearer_live() is False

    def test_empty_master_key_sends_run_key(self, monkeypatch):
        monkeypatch.delenv("WCB_SIDECAR_NO_MASTER_KEY", raising=False)
        a = self._agent("")
        assert a._agent_bearer("t1") == "wcb::t1::deadbeef"
        assert a._run_key_bearer_live() is True

    def test_keyless_switch_overrides_master_key(self, monkeypatch):
        monkeypatch.setenv("WCB_SIDECAR_NO_MASTER_KEY", "1")
        a = self._agent("sk-talos-litellm")
        assert a._agent_bearer("t1") == "wcb::t1::deadbeef"
        assert a._run_key_bearer_live() is True

    def test_collect_usage_passes_key_only_when_bearer_live(
            self, monkeypatch, tmp_path):
        from src.agents.openclaw import runner as ocr
        captured = {}

        def fake_extract(p, s, e, run_key=""):
            captured["run_key"] = run_key
            return {"request_count": 0}

        monkeypatch.setattr(ocr, "extract_usage_from_litellm_log", fake_extract)
        monkeypatch.setattr(
            ocr, "extract_preflight_usage_from_litellm_log",
            lambda p: {"request_count": 0})
        monkeypatch.setattr(ocr, "extract_usage_from_jsonl",
                            lambda p: {"request_count": 0})
        monkeypatch.setattr(
            ocr.subprocess, "run",
            lambda *a, **k: type("R", (), {"returncode": 1, "stdout": "",
                                           "stderr": ""})())

        monkeypatch.setenv("WCB_SIDECAR_NO_MASTER_KEY", "1")
        a = self._agent("sk-talos-litellm")
        a.litellm_usage_log = str(tmp_path / "u.jsonl")
        a._task_windows["t1"] = (1.0, 2.0)
        a.collect_usage("t1", tmp_path / "od", 1.0)
        assert captured["run_key"] == "wcb::t1::deadbeef"

        monkeypatch.delenv("WCB_SIDECAR_NO_MASTER_KEY", raising=False)
        b = self._agent("sk-talos-litellm")
        b.litellm_usage_log = str(tmp_path / "u.jsonl")
        b._task_windows["t1"] = (1.0, 2.0)
        b.collect_usage("t1", tmp_path / "od", 1.0)
        assert captured["run_key"] == ""

    def test_collect_usage_pops_run_key(self, monkeypatch, tmp_path):
        from src.agents.openclaw import runner as ocr
        monkeypatch.setattr(ocr, "extract_usage_from_litellm_log",
                            lambda p, s, e, run_key="": {"request_count": 0})
        monkeypatch.setattr(
            ocr, "extract_preflight_usage_from_litellm_log",
            lambda p: {"request_count": 0})
        monkeypatch.setattr(ocr, "extract_usage_from_jsonl",
                            lambda p: {"request_count": 0})
        monkeypatch.setattr(
            ocr.subprocess, "run",
            lambda *a, **k: type("R", (), {"returncode": 1, "stdout": "",
                                           "stderr": ""})())
        a = self._agent("")
        a.litellm_usage_log = str(tmp_path / "u.jsonl")
        a._task_windows["t1"] = (1.0, 2.0)
        a.collect_usage("t1", tmp_path / "od", 1.0)
        assert "t1" not in a._run_keys


class TestSpawnTreeFold:
    """Director spawn traffic already flows through the sidecar, so folding the
    spawn ledger into litellm-sourced totals double-counts it."""

    def _collect(self, monkeypatch, tmp_path, litellm_usage, ledger_rows):
        from src.agents.openclaw import runner as ocr
        from src.agents.openclaw.runner import OpenClawAgent
        monkeypatch.setattr(
            ocr, "extract_usage_from_litellm_log",
            lambda p, s, e, run_key="": dict(litellm_usage))
        monkeypatch.setattr(
            ocr, "extract_preflight_usage_from_litellm_log",
            lambda p: {"request_count": 0})
        monkeypatch.setattr(
            ocr, "extract_usage_from_jsonl",
            lambda p: {"input_tokens": 100, "output_tokens": 50,
                       "total_tokens": 150, "cost_usd": 1.0,
                       "request_count": 4, "usage_source": "openclaw"})
        monkeypatch.setattr(
            ocr.subprocess, "run",
            lambda *a, **k: type("R", (), {"returncode": 1, "stdout": "",
                                           "stderr": ""})())
        a = OpenClawAgent(gateway_port=1, litellm_master_key="mk")
        a.litellm_usage_log = str(tmp_path / "u.jsonl") if litellm_usage else None
        a._task_windows["t1"] = (1.0, 2.0)
        out = tmp_path / "od"
        ledger = out / "task_output" / "workspace_full" / "spawn_tree.jsonl"
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.write_text("\n".join(json.dumps(r) for r in ledger_rows) + "\n")
        if not litellm_usage:
            (out / "chat.jsonl").write_text("")
            monkeypatch.setattr(
                ocr.subprocess, "run",
                lambda *a, **k: type("R", (), {"returncode": 0, "stdout": "",
                                               "stderr": ""})())
        return a.collect_usage("t1", out, 1.0)

    SPAWNS = [
        {"spawn_id": "spw_1", "tokens_in": 10, "tokens_out": 5,
         "cost_usd": 0.5},
        {"spawn_id": "spw_2", "tokens_in": 20, "tokens_out": 10,
         "cost_usd": 0.25},
        {"kind": "summary", "scope": "batch", "n_spawns": 2,
         "input_tokens": 30, "output_tokens": 15, "cost_usd": 0.75},
    ]

    def test_summary_rows_do_not_inflate_count(self, monkeypatch, tmp_path):
        u = self._collect(monkeypatch, tmp_path,
                          {"input_tokens": 100, "output_tokens": 50,
                           "total_tokens": 150, "cost_usd": 1.0,
                           "request_count": 4, "usage_source": "litellm"},
                          self.SPAWNS)
        assert u["subagent_count"] == 2
        assert u["subagent_tokens_in"] == 30
        assert u["subagent_cost_usd"] == pytest.approx(0.75)

    def test_no_fold_when_litellm_already_counted(self, monkeypatch, tmp_path):
        for source in ("litellm", "litellm_run_key", "litellm_oauth"):
            u = self._collect(monkeypatch, tmp_path,
                              {"input_tokens": 100, "output_tokens": 50,
                               "total_tokens": 150, "cost_usd": 1.0,
                               "request_count": 4, "usage_source": source},
                              self.SPAWNS)
            assert u["subagent_usage_folded"] is False, source
            assert u["input_tokens"] == 100, source
            assert u["cost_usd"] == pytest.approx(1.0), source
            assert u["subagent_cost_usd"] == pytest.approx(0.75), source

    def test_fold_when_transcript_source_misses_spawns(
            self, monkeypatch, tmp_path):
        u = self._collect(monkeypatch, tmp_path, None, self.SPAWNS)
        assert u["usage_source"] == "openclaw"
        assert u["subagent_usage_folded"] is True
        assert u["input_tokens"] == 130
        assert u["output_tokens"] == 65
        assert u["total_tokens"] == 195
        assert u["cost_usd"] == pytest.approx(1.75)

    def test_no_subagent_fields_without_spawns(self, monkeypatch, tmp_path):
        u = self._collect(monkeypatch, tmp_path,
                          {"input_tokens": 100, "output_tokens": 50,
                           "total_tokens": 150, "cost_usd": 1.0,
                           "request_count": 4, "usage_source": "litellm"},
                          [{"kind": "summary", "n_spawns": 0}])
        assert "subagent_count" not in u
        assert "subagent_usage_folded" not in u


class _FakeProc:
    def __init__(self, waits_before_exit=None):
        self.waits_before_exit = waits_before_exit
        self.calls = 0

    def wait(self, timeout=None):
        self.calls += 1
        if (self.waits_before_exit is not None
                and self.calls > self.waits_before_exit):
            return 0
        raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout or 0)


class TestStallGuard:
    def _agent(self, tmp_path, run_key="wcb::t1::k"):
        from src.agents.openclaw.runner import OpenClawAgent
        a = OpenClawAgent(gateway_port=1, litellm_master_key="")
        a.litellm_usage_log = str(tmp_path / "usage.jsonl")
        (tmp_path / "usage.jsonl").write_text("")
        a._run_keys["t1"] = run_key
        return a

    def _spec(self):
        from src.agents.base import AgentTaskSpec
        return AgentTaskSpec(task_id="t1", task={}, workspace_path="/tmp",
                             prompt="p", timeout_seconds=30,
                             output_dir=Path("/tmp"), model="m")

    def test_stall_seconds_floor_and_disable(self, monkeypatch):
        from src.agents.openclaw.runner import OpenClawAgent
        monkeypatch.delenv("WCB_TURN_STALL_SECONDS", raising=False)
        assert OpenClawAgent._stall_seconds() == 0.0
        monkeypatch.setenv("WCB_TURN_STALL_SECONDS", "0")
        assert OpenClawAgent._stall_seconds() == 0.0
        monkeypatch.setenv("WCB_TURN_STALL_SECONDS", "120")
        assert OpenClawAgent._stall_seconds() == 600.0
        monkeypatch.setenv("WCB_TURN_STALL_SECONDS", "900")
        assert OpenClawAgent._stall_seconds() == 900.0
        monkeypatch.setenv("WCB_TURN_STALL_SECONDS", "garbage")
        assert OpenClawAgent._stall_seconds() == 0.0

    def test_disabled_guard_ok_and_timeout(self, tmp_path, monkeypatch):
        monkeypatch.delenv("WCB_TURN_STALL_SECONDS", raising=False)
        a = self._agent(tmp_path)
        assert a._turn_wait_outcome(_FakeProc(waits_before_exit=0),
                                    self._spec(),
                                    time.time() + 30) == "ok"
        assert a._turn_wait_outcome(_FakeProc(), self._spec(),
                                    time.time() - 1) == "timeout"

    def test_guarded_stall_detected(self, tmp_path, monkeypatch):
        from src.agents.openclaw import runner as ocr
        monkeypatch.setenv("WCB_SIDECAR_NO_MASTER_KEY", "1")
        monkeypatch.setenv("WCB_TURN_STALL_SECONDS", "1")
        monkeypatch.setattr(ocr.OpenClawAgent, "_STALL_FLOOR_S", 0.2)
        a = self._agent(tmp_path)
        assert a._turn_wait_outcome(_FakeProc(), self._spec(),
                                    time.time() + 30) == "stalled"

    def test_guarded_progress_prevents_stall(self, tmp_path, monkeypatch):
        from src.agents.openclaw import runner as ocr
        monkeypatch.setenv("WCB_SIDECAR_NO_MASTER_KEY", "1")
        monkeypatch.setenv("WCB_TURN_STALL_SECONDS", "1")
        monkeypatch.setattr(ocr.OpenClawAgent, "_STALL_FLOOR_S", 5.0)
        a = self._agent(tmp_path)
        log = Path(a.litellm_usage_log)

        class _ProgressProc(_FakeProc):
            def wait(self, timeout=None):
                with open(log, "a") as fh:
                    fh.write('{"run_key": "wcb::t1::k"}\n')
                return super().wait(timeout)

        assert a._turn_wait_outcome(_ProgressProc(waits_before_exit=3),
                                    self._spec(),
                                    time.time() + 30) == "ok"

    def test_log_truncation_counts_as_progress(self, tmp_path, monkeypatch):
        """m6 regression: rows count DECREASING (rotation) must not read as
        an instant stall — any change resets the progress clock."""
        from src.agents.openclaw import runner as ocr
        monkeypatch.setenv("WCB_SIDECAR_NO_MASTER_KEY", "1")
        monkeypatch.setenv("WCB_TURN_STALL_SECONDS", "1")
        monkeypatch.setattr(ocr.OpenClawAgent, "_STALL_FLOOR_S", 5.0)
        a = self._agent(tmp_path)
        log = Path(a.litellm_usage_log)
        log.write_text('{"run_key": "wcb::t1::k"}\n' * 5)

        class _TruncProc(_FakeProc):
            def wait(self, timeout=None):
                if self.calls == 1:
                    log.write_text('{"run_key": "wcb::t1::k"}\n')
                return super().wait(timeout)

        assert a._turn_wait_outcome(_TruncProc(waits_before_exit=2),
                                    self._spec(),
                                    time.time() + 30) == "ok"

    def test_terminate_agent_invocations_scoped(self, monkeypatch):
        from src.agents.openclaw import runner as ocr
        captured = {}

        def fake_run(cmd, **kw):
            captured["cmd"] = cmd
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        monkeypatch.setattr(ocr.subprocess, "run", fake_run)
        ocr.OpenClawAgent._terminate_agent_invocations("task-x")
        joined = " ".join(captured["cmd"])
        assert "docker exec task-x" in joined
        assert "pkill -TERM -f 'openclaw agent'" in joined
        assert "pkill -KILL -f 'openclaw agent'" in joined
        assert "openclaw gateway" not in joined

    def test_break_connections_honest_when_ss_missing(self, tmp_path,
                                                      monkeypatch, caplog):
        import logging
        from src.agents.openclaw import runner as ocr

        def fake_run(cmd, **kw):
            return type("R", (), {"returncode": 0,
                                  "stdout": "WCB_SS_UNAVAILABLE\n",
                                  "stderr": ""})()

        monkeypatch.setattr(ocr.subprocess, "run", fake_run)
        a = self._agent(tmp_path)
        with caplog.at_level(logging.WARNING):
            a._break_stuck_llm_connections("t1")
        assert any("ss unavailable" in r.message for r in caplog.records)
        assert not any("reset sidecar connections" in r.message
                       for r in caplog.records)


class TestSidecarKeylessSwitch:
    def test_yaml_omits_master_key_when_switch_on(self, monkeypatch):
        from src.utils import litellm_sidecar as sc
        monkeypatch.setenv("WCB_SIDECAR_NO_MASTER_KEY", "1")
        yaml_on = sc.build_litellm_config_yaml(bedrock_sonnet_arn="")
        monkeypatch.delenv("WCB_SIDECAR_NO_MASTER_KEY")
        yaml_off = sc.build_litellm_config_yaml(bedrock_sonnet_arn="")
        assert "master_key" not in yaml_on
        assert "master_key: os.environ/LITELLM_MASTER_KEY" in yaml_off
        assert "store_model_in_db" in yaml_on


class TestBackfillPortParity:
    def test_backfill_pass_summary_excludes_incomplete(self, monkeypatch):
        monkeypatch.delenv("WCB_INCLUDE_INCOMPLETE_RUNS", raising=False)
        bps = _load_script("backfill_pass_summary")
        good = bps._entry(1, {"overall_score": 0.8,
                              "rubric_weights_percentage": 80.0}, None)
        bad = bps._entry(2, {"overall_score": 0.2,
                             "rubric_weights_percentage": 20.0,
                             "run_incomplete": True,
                             "turns_planned": 9, "turns_completed": 3}, None)
        doc = bps._doc("claude", [good, bad])
        assert doc["runs_excluded_incomplete"] == 1
        assert doc["average_reward"] == pytest.approx(0.8)
        assert len(doc["per_run"]) == 2


class TestAggregateExclusion:
    def test_incomplete_score_json_skipped_but_counted(self, tmp_path, monkeypatch):
        monkeypatch.delenv("WCB_INCLUDE_INCOMPLETE_RUNS", raising=False)
        agg = _load_script("aggregate_runs")
        root = tmp_path / "output"
        for run, (pct, incomplete) in enumerate([(80.0, False), (20.0, True)], 1):
            d = root / "openclaw" / "task_x" / "trajectories" / "claude" / f"run_{run}"
            d.mkdir(parents=True)
            score = {"rubric_weights_percentage": pct, "criteria_total": 10,
                     "criteria_passed": 8, "criteria_failed": 2}
            if incomplete:
                score["run_incomplete"] = True
            (d / "score.json").write_text(json.dumps(score))
        summary = agg.aggregate(root)
        entry = summary["by_task_model"][0]
        assert entry["run_count"] == 1
        assert entry["runs_excluded_incomplete"] == 1
        assert entry["average_rubric_weights_percentage"] == pytest.approx(80.0)

    def test_all_incomplete_task_still_visible(self, tmp_path, monkeypatch):
        monkeypatch.delenv("WCB_INCLUDE_INCOMPLETE_RUNS", raising=False)
        agg = _load_script("aggregate_runs")
        root = tmp_path / "output"
        d = root / "openclaw" / "task_y" / "trajectories" / "claude" / "run_1"
        d.mkdir(parents=True)
        (d / "score.json").write_text(json.dumps(
            {"rubric_weights_percentage": 10.0, "run_incomplete": True}))
        summary = agg.aggregate(root)
        entry = summary["by_task_model"][0]
        assert entry["run_count"] == 0
        assert entry["runs_excluded_incomplete"] == 1
