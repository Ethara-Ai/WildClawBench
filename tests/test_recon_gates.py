"""The acceptance gates, each shown failing for the reason it exists."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from script.lib.recon import environment as E  # noqa: E402
from script.lib.recon import gates as G  # noqa: E402
from script.lib.recon import metadata as M  # noqa: E402
from script.lib.recon import prompts as P  # noqa: E402
from script.lib.recon import schedule as S  # noqa: E402
from script.lib.recon import sources as SRC  # noqa: E402

HEADER = ("# task_id: t\n# persona: Ada\n# timezone: UTC\n"
          "# window: 2026-10-14 to 2026-10-15 (2 days)\n# turn_count: 2\n")
BODY = ("--- TURN T0 (Day 1, 08:00) ---\nfirst.\n\n"
        "--- TURN T1 (Day 2, 09:00) ---\nsecond.\n")
STAMPS = ["2026-10-14T08:00:00Z", "2026-10-15T09:00:00Z"]
RUBRIC = '[{"criterion": "does the thing"}]'


def _bundle(tmp_path: Path) -> Path:
    b = tmp_path / "bundle"
    (b / "data").mkdir(parents=True)
    (b / "prompt.txt").write_text(HEADER + "\n" + BODY, encoding="utf-8")
    (b / "rubric.json").write_text(RUBRIC, encoding="utf-8")
    files = b.joinpath(*SRC.ARTIFACTS_SUBPATH)
    (files / "home" / "Desktop").mkdir(parents=True)
    (files / "home" / "Desktop" / "a.png").write_bytes(b"png")
    persona = b.joinpath(*SRC.PERSONA_SUBPATH)
    persona.mkdir(parents=True)
    for name in ("AGENTS.md", "HEARTBEAT.md", "IDENTITY.md", "MEMORY.md",
                 "SOUL.md", "TOOLS.md", "USER.md"):
        (persona / name).write_text(name, encoding="utf-8")
    stage = b / "inject" / "stage1"
    stage.mkdir(parents=True)
    (stage / "mutations.json").write_text(json.dumps(
        {"stage_name": "s1", "applies_between_turns": ["T0", "T1"],
         "mutations": {"filesystem": [], "loud": [], "silent": []}}), encoding="utf-8")
    run = b / "trajectories" / "m" / "run_1"
    run.mkdir(parents=True)
    (run / "output.json").write_text(json.dumps({"meta_info": {"system_prompt": ""},
        "messages": [{"timestamp": s, "message": {"role": "user"}} for s in STAMPS]}),
        encoding="utf-8")
    (b / "data" / "task.toml").write_text(
        '[metadata]\nrequired_skills = ["gmail-api-connector"]\n'
        'distractor_skills = ["slack-api-connector"]\n'
        '[multimodal]\ndependency_tags = ["a", "b"]\n', encoding="utf-8")
    return b


def _reconstruct(tmp_path: Path) -> G.GateContext:
    bundle = _bundle(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    source = P.locate_prompt_file(bundle)
    rec = P.normalise(source, task_id="t")
    (out / "prompts.txt").write_text(rec.text, encoding="utf-8")
    window = P.window_from_header(rec.header["window"], "UTC")
    instants = S.resolve(bundle, rec.turns, window, "UTC")
    (out / "prompts.json").write_text(json.dumps(
        S.build("t", "Ada", "UTC", rec.turns, instants), indent=2), encoding="utf-8")
    SRC.recover_all(bundle, out)
    meta = M.derive(bundle, out)
    M.write(meta, out)
    env = bundle / "data" / "environment"
    mock = E.extract(env, env, out / "mock_data", meta.scoped_apis)
    drift = E.module_drift(env, env, "t")
    return G.GateContext(bundle=bundle, out_dir=out, prompts=rec, instants=instants,
                         meta=meta, mock=mock, drift=drift)


def _by_id(results):
    return {g.id: g for g in results}


def test_a_faithful_reconstruction_clears_every_gate_but_preflight(tmp_path):
    ctx = _reconstruct(tmp_path)
    checks = [g for g in G.ALL_GATES if g is not G.g3_preflight]
    results = _by_id([g(ctx) for g in checks])
    assert [g.status for g in results.values()] == [G.PASS] * len(checks)


def test_g1_catches_a_turn_count_the_loader_disagrees_with(tmp_path):
    ctx = _reconstruct(tmp_path)
    assert G.g1_turn_count(ctx).status == G.PASS
    (ctx.out_dir / "prompts.json").unlink()
    (ctx.out_dir / "prompts.txt").write_text(HEADER, encoding="utf-8")
    assert G.g1_turn_count(ctx).status == G.FAIL


def test_g2_requires_every_turn_to_resolve_a_clock(tmp_path):
    ctx = _reconstruct(tmp_path)
    assert G.g2_sim_clock(ctx).status == G.PASS
    (ctx.out_dir / "prompts.json").unlink()
    assert G.g2_sim_clock(ctx).status == G.FAIL


def test_g2_catches_an_instant_that_is_not_the_runs(tmp_path):
    ctx = _reconstruct(tmp_path)
    payload = json.loads((ctx.out_dir / "prompts.json").read_text(encoding="utf-8"))
    payload["turns"][1]["timestamp"] = "2026-10-15T23:59:00+00:00"
    (ctx.out_dir / "prompts.json").write_text(json.dumps(payload), encoding="utf-8")
    gate = G.g2_sim_clock(ctx)
    assert gate.status == G.FAIL
    assert "other than" in gate.detail


def test_g2_only_warns_when_the_schedule_was_reconstructed(tmp_path):
    ctx = _reconstruct(tmp_path)
    window = P.window_from_header(ctx.prompts.header["window"], "UTC")
    ctx.instants = S.from_labels(ctx.prompts.turns, window, "UTC")
    (ctx.out_dir / "prompts.json").write_text(json.dumps(
        S.build("t", "Ada", "UTC", ctx.prompts.turns, ctx.instants)), encoding="utf-8")
    assert G.g2_sim_clock(ctx).status == G.WARN


def test_g4_fails_on_a_module_that_differs_from_the_baseline(tmp_path):
    ctx = _reconstruct(tmp_path)
    ctx.drift = {"stale": ["gmail-api/gmail_data.py"], "module_count": 3}
    assert G.g4_module_drift(ctx).status == G.FAIL


def test_g5_fails_when_the_overlay_escapes_the_declared_surface(tmp_path):
    ctx = _reconstruct(tmp_path)
    ctx.mock.overlays["stripe-api"] = []
    assert G.g5_overlay_containment(ctx).status == G.FAIL


def test_g5_fails_when_the_task_declares_no_surface_at_all(tmp_path):
    ctx = _reconstruct(tmp_path)
    ctx.mock.scope_proven = False
    gate = G.g5_overlay_containment(ctx)
    assert gate.status == G.FAIL
    assert "containment cannot be shown" in gate.detail


def test_g6_catches_a_reworded_turn(tmp_path):
    ctx = _reconstruct(tmp_path)
    (ctx.out_dir / "prompts.txt").write_text(
        HEADER + "\n" + BODY.replace("second.", "reworded."), encoding="utf-8")
    assert G.g6_turn_fidelity(ctx).status == G.FAIL


def test_g7_catches_a_rubric_that_is_not_the_bundles(tmp_path):
    ctx = _reconstruct(tmp_path)
    assert G.g7_rubric(ctx).status == G.PASS
    (ctx.out_dir / "rubric.json").write_text("[]", encoding="utf-8")
    assert G.g7_rubric(ctx).status == G.FAIL


def test_g7_reports_a_deliberate_rubric_substitution(tmp_path):
    ctx = _reconstruct(tmp_path)
    ctx.rubric_override = "some/other/rubric.json"
    gate = G.g7_rubric(ctx)
    assert gate.status == G.WARN
    assert "on request" in gate.detail


def test_g8_catches_an_attachment_left_behind(tmp_path):
    ctx = _reconstruct(tmp_path)
    (ctx.out_dir / "data" / "home" / "Desktop" / "a.png").unlink()
    assert G.g8_attachment_parity(ctx).status == G.FAIL


def test_g9_catches_a_stage_boundary_that_moved(tmp_path):
    ctx = _reconstruct(tmp_path)
    moved = ctx.out_dir / "inject" / "stage1" / "mutations.json"
    payload = json.loads(moved.read_text(encoding="utf-8"))
    payload["applies_between_turns"] = ["T1", "T2"]
    moved.write_text(json.dumps(payload), encoding="utf-8")
    gate = G.g9_inject_parity(ctx)
    assert gate.status == G.FAIL
    assert "stage boundaries differ" in gate.detail


def test_g10_only_ever_advises(tmp_path):
    ctx = _reconstruct(tmp_path)
    (ctx.out_dir / "persona" / "TOOLS.md").unlink()
    assert G.g10_persona(ctx).status == G.WARN
    results = _by_id(G.run(ctx, G.STRICT))
    assert results["G10"].status == G.WARN


@pytest.mark.parametrize("mode,expected", [(G.STRICT, G.FAIL), (G.WARN_ONLY, G.WARN)])
def test_gate_mode_decides_whether_a_failure_blocks(tmp_path, mode, expected):
    ctx = _reconstruct(tmp_path)
    ctx.drift = {"stale": ["gmail-api/gmail_data.py"], "module_count": 1}
    assert _by_id(G.run(ctx, mode))["G4"].status == expected


def test_gates_off_runs_nothing(tmp_path):
    assert G.run(_reconstruct(tmp_path), G.OFF) == []


def test_failures_lists_only_the_blocking_gates(tmp_path):
    ctx = _reconstruct(tmp_path)
    ctx.drift = {"stale": ["x.py"], "module_count": 1}
    ids = [g.id for g in G.failures(G.run(ctx, G.STRICT))]
    assert "G4" in ids and "G10" not in ids


def test_legacy_waives_only_the_retired_test_channel(tmp_path, monkeypatch):
    """preflight still demands files the retired generated-test channel wrote."""
    ctx = _reconstruct(tmp_path)

    class _Done:
        returncode = 1
        stdout = ("  \x1b[31m✘\x1b[0m test_outputs.py MISSING\n"
                  "  \x1b[31m✘\x1b[0m test_weights.json MISSING\n")
        stderr = ""

    monkeypatch.setattr(G.subprocess, "run", lambda *a, **k: _Done())
    assert G.g3_preflight(ctx).status == G.FAIL
    ctx.legacy = True
    assert G.g3_preflight(ctx).status == G.PASS


def test_legacy_does_not_waive_a_real_preflight_failure(tmp_path, monkeypatch):
    ctx = _reconstruct(tmp_path)
    ctx.legacy = True

    class _Done:
        returncode = 1
        stdout = ("  \x1b[31m✘\x1b[0m test_outputs.py MISSING\n"
                  "  \x1b[31m✘\x1b[0m environment/gmail-api MISSING\n")
        stderr = ""

    monkeypatch.setattr(G.subprocess, "run", lambda *a, **k: _Done())
    gate = G.g3_preflight(ctx)
    assert gate.status == G.FAIL
    assert "environment/gmail-api MISSING" in gate.detail
    assert "test_outputs.py" not in gate.detail


def test_a_gate_that_raises_is_reported_not_swallowed(tmp_path, monkeypatch):
    ctx = _reconstruct(tmp_path)
    monkeypatch.setattr(G, "ALL_GATES", (G.g4_module_drift,))
    ctx.drift = None
    assert G.run(ctx, G.STRICT)[0].status == G.FAIL
