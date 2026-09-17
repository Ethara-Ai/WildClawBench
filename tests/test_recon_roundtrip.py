"""End-to-end: build a bundle, reconstruct it, load the result back.

The fixtures are synthetic miniatures, deliberately not copies of delivered
bundles. Each reproduces one of the five shapes found across the 126 shipped
bundles — which prompt file it publishes, where its truth doc lives, which
header dialect it uses — at three turns and two APIs, so a failure names the
shape that broke rather than pointing into a 250MB tree.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from script.lib.recon import gates as G  # noqa: E402
from script.lib.recon.sources import detect_variant  # noqa: E402
from script.reconstruct_input_from_bundle import reconstruct  # noqa: E402
from src.utils.sim_clock import compute_sim_clock_for_turn  # noqa: E402
from src.utils.task_parser import load_task  # noqa: E402

TURNS = ("morning. work the batch through.",
         "one more before standup.",
         "last look before she reads it.")
STAMPS = ("2026-10-14T08:12:00Z", "2026-10-15T09:40:00Z", "2026-10-16T16:45:00Z")
LABELS = ((1, "08:12"), (2, "09:40"), (3, "16:45"))

BODY = "\n".join(
    f"--- TURN T{i} (Day {d}, {t}) ---\n{msg}\n"
    for i, ((d, t), msg) in enumerate(zip(LABELS, TURNS)))

#: One per shipped header dialect, so every variant exercises a different one.
HEADERS = {
    "canonical": "# task_id: {tid}\n# persona: Ada Lovelace\n# timezone: UTC\n"
                 "# window: 2026-10-14 to 2026-10-16 (3 days)\n# turn_count: 3\n",
    "spaced": "# task_id: {tid}\n# persona: Ada Lovelace\n# timezone: UTC\n"
              "# window: 2026-10-14 to 2026-10-16 (9 days)\n# turn count: 3\n",
    "old_key": "# task_id: {tid}\n# persona: Ada Lovelace\n# timezone: UTC\n"
               "# window: 2026-10-14 through 2026-10-16 (3 simulated days)\n"
               "# turns: 3 across 3 days\n",
    "bare": "task_id: {tid}\npersona: Ada Lovelace\ntimezone: UTC\n"
            "window: 2026-10-14 to 2026-10-16\nturns: 3\n",
}

TASK_TOML = """\
[metadata]
category = "conformance_audit"
difficulty = "hard"
required_skills = ["gmail-api-connector"]
distractor_skills = ["slack-api-connector"]

[multimodal]
dependency_tags = ["creative_media", "content_audit"]
"""

PERSONA_FILES = ("AGENTS.md", "HEARTBEAT.md", "IDENTITY.md", "MEMORY.md",
                 "SOUL.md", "TOOLS.md", "USER.md")

#: (prompt file, truth doc location, extra marker files, header dialect)
VARIANT_SPECS = {
    "pilot_rework": ("prompt.txt", "data/solution/TRUTH.md", (), "spaced"),
    "prompt_txt": ("prompt.txt", "TRUTH.md", (), "canonical"),
    "golden_trajectory": ("PROMPT.md", "TRUTH.md",
                          ("golden-trajectory/steps.json",), "bare"),
    "prompts_json": ("data/instruction.md", None, (), "old_key"),
    "prompts_json_mirror": ("data/instruction.md", None,
                            ("golden_trajectory.json",), "canonical"),
}


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def build_bundle(root: Path, variant: str, tid: str = "ada_lovelace_0001") -> Path:
    prompt_file, truth_at, extras, dialect = VARIANT_SPECS[variant]
    b = root / tid
    _write(b / prompt_file, HEADERS[dialect].format(tid=tid) + "\n" + BODY)
    if variant.startswith("prompts_json"):
        _write(b / "prompts.json", json.dumps({
            "task_id": tid, "persona": "Ada Lovelace", "timezone": "UTC",
            "turn_count": 3, "source": "authored",
            "turns": [{"turn": f"T{i}", "timestamp": s, "message": m}
                      for i, (s, m) in enumerate(zip(STAMPS, TURNS))]}, indent=2))
    _write(b / "rubric.json", json.dumps([{"criterion": "does the thing"}]))
    if truth_at:
        _write(b / truth_at, "# TRUTH\n\n## 1. Focal Event\n\nx\n")
    for extra in extras:
        _write(b / extra, "{}")
    _write(b / "data" / "task.toml", TASK_TOML)

    env = b / "data" / "environment"
    for name in PERSONA_FILES:
        _write(env / "persona" / name, f"# {name}\n")
    files = env / "artifacts" / "inputs" / "files" / "home"
    _write(files / "Desktop" / "brief.txt", "brief")
    (files / "Pictures").mkdir(parents=True, exist_ok=True)
    (files / "Pictures" / "shot.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    _write(env / "gmail-api" / "messages.json", '[{"id": "1", "subject": "task"}]')
    _write(env / "gmail-api" / "gmail_data.py", "STORE = {}\n")
    _write(env / "slack-api" / "channels.json", '[{"id": "1", "name": "general"}]')
    _write(env / "slack-api" / "slack_data.py", "STORE = {}\n")

    _write(b / "inject" / "stage1" / "mutations.json", json.dumps({
        "stage_name": "s1", "applies_between_turns": ["T0", "T1"],
        "mutations": {"filesystem": [], "loud": [], "silent": []}}))
    run = b / "trajectories" / "claude-opus-5" / "run_1"
    _write(run / "output.json", json.dumps({
        "meta_info": {"system_prompt": "", "task_type": "conformance_audit"},
        "messages": [{"type": "message", "id": f"u{i}", "timestamp": s,
                      "message": {"role": "user",
                                  "content": [{"type": "text", "text": m}]}}
                     for i, (s, m) in enumerate(zip(STAMPS, TURNS))]}))
    return b


def build_baseline(root: Path) -> Path:
    """The pristine harness tree: slack untouched, gmail carrying the default."""
    base = root / "environment"
    _write(base / "gmail-api" / "messages.json", '[{"id": "1", "subject": "default"}]')
    _write(base / "gmail-api" / "gmail_data.py", "STORE = {}\n")
    _write(base / "slack-api" / "channels.json", '[{"id": "1", "name": "general"}]')
    _write(base / "slack-api" / "slack_data.py", "STORE = {}\n")
    return base


def _run(tmp_path: Path, variant: str, **kw) -> tuple:
    bundle = build_bundle(tmp_path / "bundles", variant)
    baseline = build_baseline(tmp_path / "base")
    out = tmp_path / "out" / bundle.name
    summary = reconstruct(bundle, out, baseline, verbose=False,
                          gate_mode=G.OFF, **kw)
    return bundle, out, summary


@pytest.mark.parametrize("variant", sorted(VARIANT_SPECS))
def test_every_shipped_layout_is_recognised(tmp_path, variant):
    assert detect_variant(build_bundle(tmp_path, variant)) == variant


@pytest.mark.parametrize("variant", sorted(VARIANT_SPECS))
def test_every_layout_reconstructs_into_a_tree_the_loader_reads(tmp_path, variant):
    _, out, summary = _run(tmp_path, variant)
    assert summary["turns"] == 3
    task = load_task(out)
    assert task["turn_messages"] == list(TURNS)


@pytest.mark.parametrize("variant", sorted(VARIANT_SPECS))
def test_every_layout_dates_every_turn_from_its_run(tmp_path, variant):
    _, out, summary = _run(tmp_path, variant)
    assert summary["clock"] == "exact"
    task = load_task(out)
    resolved = [compute_sim_clock_for_turn(task, i) for i in range(3)]
    assert all(c is not None for c in resolved)
    assert [c.iso for c in resolved] == [
        "2026-10-14T08:12:00+00:00",
        "2026-10-15T09:40:00+00:00",
        "2026-10-16T16:45:00+00:00",
    ]


@pytest.mark.parametrize("variant", sorted(VARIANT_SPECS))
def test_every_layout_declares_the_same_api_surface(tmp_path, variant):
    _, out, _ = _run(tmp_path, variant)
    task = load_task(out)
    assert task["required_apis_declared"] == ["gmail-api"]
    assert task["distractor_apis_declared"] == ["slack-api"]


@pytest.mark.parametrize("variant", sorted(VARIANT_SPECS))
def test_every_layout_clears_every_gate_it_can_be_held_to(tmp_path, variant):
    """G3 is excluded: preflight boots the real harness mock modules, so no
    synthetic seed can satisfy it. Every other gate passes on every layout."""
    bundle = build_bundle(tmp_path / "bundles", variant)
    baseline = build_baseline(tmp_path / "base")
    out = tmp_path / "out" / bundle.name
    reconstruct(bundle, out, baseline, verbose=False, gate_mode=G.OFF)
    summary = reconstruct(bundle, out, baseline, verbose=False, gate_mode=G.STRICT,
                          legacy=True)
    graded = {g.id: g.status for g in summary["gates"]}
    assert len(graded) == len(G.ALL_GATES)
    assert [gid for gid, st in graded.items() if st == G.FAIL] == ["G3"]


def test_the_overlay_is_only_the_seed_that_differs(tmp_path):
    _, out, summary = _run(tmp_path, "prompt_txt")
    assert summary["mock_data_apis"] == 1
    assert (out / "mock_data" / "gmail-api" / "messages.json").is_file()
    assert not (out / "mock_data" / "slack-api").exists()


def test_the_header_is_normalised_and_the_day_count_recomputed(tmp_path):
    """The 'spaced' dialect declares nine days for a window spanning three."""
    _, out, _ = _run(tmp_path, "pilot_rework")
    text = (out / "prompts.txt").read_text(encoding="utf-8")
    assert text.startswith(
        "# task_id: ada_lovelace_0001\n# persona: Ada Lovelace\n# timezone: UTC\n"
        "# window: 2026-10-14 to 2026-10-16 (3 days)\n# turn_count: 3\n")


def test_attachments_come_back_at_their_published_paths(tmp_path):
    _, out, _ = _run(tmp_path, "prompt_txt")
    task = load_task(out)
    assert sorted(a["storedAs"] for a in task["attachments"]) == [
        "home/home/Desktop/brief.txt", "home/home/Pictures/shot.png"]


def test_the_truth_doc_is_found_wherever_the_layout_puts_it(tmp_path):
    for variant in ("pilot_rework", "prompt_txt"):
        _, out, _ = _run(tmp_path / variant, variant)
        assert (out / "TRUTH.md").is_file()


def test_a_layout_that_ships_no_truth_doc_says_so(tmp_path):
    _, out, _ = _run(tmp_path, "prompts_json")
    assert not (out / "TRUTH.md").exists()
    notes = (out / "RECONSTRUCTION_NOTES.md").read_text(encoding="utf-8")
    assert "TRUTH" in notes


def test_the_manifest_records_the_layout_and_the_provenance(tmp_path):
    _, out, _ = _run(tmp_path, "golden_trajectory")
    payload = json.loads(
        (out / "RECONSTRUCTION_MANIFEST.json").read_text(encoding="utf-8"))
    recon = payload["reconstruction"]
    assert recon["bundle_variant"] == "golden_trajectory"
    assert recon["turns"] == 3
    assert recon["clock_fidelity"] == "exact"
    assert recon["overlay_scope_proven"] is True


def test_no_generated_test_files_are_written_back(tmp_path):
    _, out, _ = _run(tmp_path, "prompt_txt")
    assert not (out / "test_outputs.py").exists()
    assert not (out / "test_weights.json").exists()


def test_a_run_short_of_turns_degrades_to_the_labels(tmp_path):
    bundle = build_bundle(tmp_path / "bundles", "prompt_txt")
    run = bundle / "trajectories" / "claude-opus-5" / "run_1" / "output.json"
    payload = json.loads(run.read_text(encoding="utf-8"))
    payload["messages"] = payload["messages"][:2]
    run.write_text(json.dumps(payload), encoding="utf-8")
    out = tmp_path / "out"
    summary = reconstruct(bundle, out, build_baseline(tmp_path / "base"),
                          verbose=False, gate_mode=G.OFF)
    assert summary["clock"] == "degraded"
    payload = json.loads((out / "prompts.json").read_text(encoding="utf-8"))
    assert payload["fidelity"] == "degraded"
