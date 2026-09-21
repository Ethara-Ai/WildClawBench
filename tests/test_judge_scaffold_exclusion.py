"""Locks for harness-scaffold exclusion in judge evidence collection.

`inject_persona_into_workspace` (src/utils/docker_utils.py:2554) docker-cp's the
task's `persona/` directory onto the container workspace ROOT, and
`collect_task_output` (:2266) mirrors that root into `workspace_full/`. The
loose-root recovery pass in `_collect_deliverable_files` then swept those 7-8
files as agent deliverables: measured across 96 archived runs that is 16.5% of
every evidence character the judge receives, and 55-81 KB per task — 32-47% of
the GLM member's entire 175 KB budget spent on prompt-side text presented as
agent output. In the koji_holder_1f210ca9 E2E it helped evict
`bench_run_packet.pdf`, the single most-graded deliverable, producing 13/68
abstentions.

The rule these tests pin is PROVENANCE-FIRST, not name-first:
  * the harness's own `artifacts_excluded.json` record wins outright;
  * a scaffold-NAMED file the agent genuinely authored (so it appears in the
    agent-produced `artifacts/` diff) is still collected and still graded;
  * only when there is no diff at all (legacy collection) does the canonical
    name list decide;
  * whatever is excluded is NAMED in the omission manifest, because silence
    reads to a judge as "the agent never produced it".

Offline/deterministic: tmp_path trees only, no docker, no network, no LLM call.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils import grading  # noqa: E402

_SCAFFOLD = (
    "AGENTS.md", "BOOTSTRAP.md", "HEARTBEAT.md", "IDENTITY.md",
    "MEMORY.md", "SOUL.md", "TOOLS.md", "USER.md",
)


def _persona_workspace(
    tmp_path: Path,
    *,
    with_diff: bool = True,
    diff_extra: dict[str, str] | None = None,
    excluded_manifest: dict | None = None,
) -> Path:
    """Reproduce the real collected layout and return the evidence dir.

      <tmp>/task_output/workspace_full/<8 persona .md>   <- harness-injected
      <tmp>/task_output/workspace_full/results/report.md <- agent deliverable
      <tmp>/task_output/workspace_full/loose_notes.csv   <- root-written deliverable
      <tmp>/task_output/artifacts/...                    <- agent-produced diff

    `_pick_evidence_dir` (eval/run_batch.py:2982) prefers `artifacts/` when it
    is non-empty, so that is what the judge is handed on a modern run.
    """
    task_output = tmp_path / "task_output"
    wf = task_output / "workspace_full"
    (wf / "results").mkdir(parents=True)
    for name in _SCAFFOLD:
        (wf / name).write_text(f"harness persona body for {name}\n" * 40,
                               encoding="utf-8")
    (wf / "results" / "report.md").write_text("THE GRADED REPORT", encoding="utf-8")
    (wf / "loose_notes.csv").write_text("a,b,c\n1,2,3", encoding="utf-8")

    if excluded_manifest is not None:
        (task_output / "artifacts_excluded.json").write_text(
            json.dumps(excluded_manifest), encoding="utf-8")

    if not with_diff:
        return wf

    artifacts = task_output / "artifacts"
    (artifacts / "results").mkdir(parents=True)
    (artifacts / "results" / "report.md").write_text(
        "THE GRADED REPORT", encoding="utf-8")
    for rel, body in (diff_extra or {}).items():
        target = artifacts / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    return artifacts


def _names(files) -> list[str]:
    return sorted(f.name for f in files)


def test_injected_persona_scaffold_is_not_collected_as_a_deliverable(tmp_path):
    evidence = _persona_workspace(tmp_path)
    files = grading._collect_deliverable_files(evidence)
    collected = _names(files)
    for name in _SCAFFOLD:
        assert name not in collected, f"{name} is harness input, not agent output"
    # The recovery behaviour the loose-root pass exists for is untouched.
    assert "loose_notes.csv" in collected
    assert "report.md" in collected


def test_scaffold_bodies_never_reach_the_judge(tmp_path):
    evidence = _persona_workspace(tmp_path)
    blob = grading._gather_evidence(evidence, "TRANSCRIPT", budget=None)
    assert "harness persona body" not in blob
    assert "THE GRADED REPORT" in blob


def test_excluded_scaffold_is_named_in_the_omission_manifest(tmp_path):
    # Silence is indistinguishable from "never produced" to the judge, which is
    # the exact hallucination the omission manifest exists to prevent.
    evidence = _persona_workspace(tmp_path)
    blob = grading._gather_evidence(evidence, "TRANSCRIPT", budget=None)
    for name in _SCAFFOLD:
        assert f"{name} (harness scaffold, excluded)" in blob


def test_scaffold_out_parameter_reports_every_skipped_name_once(tmp_path):
    evidence = _persona_workspace(tmp_path)
    scaffold: list[str] = []
    grading._collect_deliverable_files(evidence, frozenset(), None, scaffold)
    assert scaffold == sorted(_SCAFFOLD)


def test_agent_authored_scaffold_name_survives_on_provenance(tmp_path):
    # The coincidence case: the agent really did write AGENTS.md as its
    # deliverable, so the harness diff carries it. Provenance beats the name and
    # the file is graded; the seven it did NOT write are still excluded.
    evidence = _persona_workspace(
        tmp_path, diff_extra={"AGENTS.md": "AGENT AUTHORED DELIVERABLE"})
    scaffold: list[str] = []
    files = grading._collect_deliverable_files(evidence, frozenset(), None, scaffold)
    assert "AGENTS.md" in _names(files)
    assert "AGENTS.md" not in scaffold
    assert sorted(n for n in _SCAFFOLD if n != "AGENTS.md") == scaffold

    blob = grading._gather_evidence(evidence, "TRANSCRIPT", budget=None)
    assert "AGENT AUTHORED DELIVERABLE" in blob


def test_scaffold_name_inside_a_deliverable_subdir_is_always_graded(tmp_path):
    # The harness stages persona at the workspace ROOT only, so results/MEMORY.md
    # is an agent deliverable no matter what the provenance records say.
    evidence = _persona_workspace(tmp_path)
    wf = tmp_path / "task_output" / "workspace_full"
    (wf / "results" / "MEMORY.md").write_text("AGENT SUBDIR MEMORY", encoding="utf-8")

    files = grading._collect_deliverable_files(evidence)
    assert any(f.name == "MEMORY.md" for f in files)
    assert "AGENT SUBDIR MEMORY" in grading._gather_evidence(evidence, "T", budget=None)


def test_legacy_run_without_a_diff_tree_falls_back_to_the_name_list(tmp_path):
    # No artifacts/ means no provenance was ever recorded, so the canonical
    # persona name list is the only signal the run has.
    evidence = _persona_workspace(tmp_path, with_diff=False)
    assert grading._agent_produced_rel_paths(tmp_path / "task_output") is None
    scaffold: list[str] = []
    files = grading._collect_deliverable_files(evidence, frozenset(), None, scaffold)
    assert scaffold == sorted(_SCAFFOLD)
    assert "loose_notes.csv" in _names(files)
    assert "report.md" in _names(files)


def test_harness_manifest_excludes_a_file_the_name_list_does_not_know(tmp_path):
    # artifacts_excluded.json is the harness's OWN record of what it put under
    # the workspace. It outranks the name list in both directions.
    evidence = _persona_workspace(
        tmp_path,
        excluded_manifest={
            "injected_by_harness": ["loose_notes.csv"],
            "harness_bookkeeping": [],
        },
    )
    scaffold: list[str] = []
    files = grading._collect_deliverable_files(evidence, frozenset(), None, scaffold)
    assert "loose_notes.csv" not in _names(files)
    assert "loose_notes.csv" in scaffold


def test_harness_bookkeeping_list_is_honoured_not_just_the_injected_list(tmp_path):
    # Both manifest keys are provenance. `harness_bookkeeping` is where a
    # harness-authored file lands when the harness wrote it itself rather than
    # copying it in (docker_utils.py:2476 moves AGENTS.md there under spawn
    # steering), so reading only `injected_by_harness` would miss it.
    evidence = _persona_workspace(
        tmp_path,
        excluded_manifest={
            "injected_by_harness": [],
            "harness_bookkeeping": ["loose_notes.csv", ".wildclaw_spawn_steering.md"],
        },
    )
    scaffold: list[str] = []
    files = grading._collect_deliverable_files(evidence, frozenset(), None, scaffold)
    assert "loose_notes.csv" not in _names(files)
    assert "loose_notes.csv" in scaffold


def test_malformed_or_missing_manifest_degrades_instead_of_raising(tmp_path):
    root = tmp_path / "task_output"
    root.mkdir()
    assert grading._harness_excluded_rel_paths(root) == frozenset()
    (root / "artifacts_excluded.json").write_text("{not json", encoding="utf-8")
    assert grading._harness_excluded_rel_paths(root) == frozenset()
    (root / "artifacts_excluded.json").write_text("[1, 2, 3]", encoding="utf-8")
    assert grading._harness_excluded_rel_paths(root) == frozenset()


def test_a_workspace_with_no_scaffold_is_byte_identical_to_the_old_behaviour(tmp_path):
    # Budget/manifest math must not move for inputs that carry no scaffold: no
    # skipped names means no manifest line and no change in assembled chars.
    results = tmp_path / "task_output" / "artifacts" / "results"
    results.mkdir(parents=True)
    (results / "report.md").write_text("ONLY REAL OUTPUT", encoding="utf-8")
    scaffold: list[str] = []
    grading._collect_deliverable_files(
        tmp_path / "task_output" / "artifacts", frozenset(), None, scaffold)
    assert scaffold == []
    blob = grading._gather_evidence(
        tmp_path / "task_output" / "artifacts", "TRANSCRIPT", budget=None)
    assert "EVIDENCE BUDGET NOTE" not in blob
    assert blob == (
        "\n----- DELIVERABLE: report.md -----\nONLY REAL OUTPUT"
        "\n----- TRANSCRIPT (condensed) -----\nTRANSCRIPT"
    )


def test_scaffold_exclusion_frees_the_budget_for_a_rubric_named_deliverable(tmp_path):
    # The koji_holder_1f210ca9 shape in miniature: a rubric-named deliverable
    # that does not fit until the scaffold stops spending the budget.
    task_output = tmp_path / "task_output"
    wf = task_output / "workspace_full"
    (wf / "results").mkdir(parents=True)
    for name in _SCAFFOLD:
        (wf / name).write_text("S" * 6_000, encoding="utf-8")
    (wf / "results" / "packet.md").write_text("P" * 20_000, encoding="utf-8")
    artifacts = task_output / "artifacts"
    (artifacts / "results").mkdir(parents=True)
    (artifacts / "results" / "packet.md").write_text("P" * 20_000, encoding="utf-8")

    blob = grading._gather_evidence(
        artifacts, "TRANSCRIPT", budget=40_000,
        rubric_names=frozenset({"packet.md"}))
    assert "P" * 20_000 in blob
    assert "S" * 6_000 not in blob
