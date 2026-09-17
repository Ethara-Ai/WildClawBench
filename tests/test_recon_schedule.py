"""Turn-instant recovery: the trajectory, then the labels, then refusal.

The label path is the one worth pinning. willie's published run woke the agent
on 2026-10-19 for the turns its prompt labels call Day 5, because the narrative
skipped a calendar day the labels counted straight through. The miniature here
reproduces that skip so the drift stays visible.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from script.lib.recon import schedule as S  # noqa: E402
from script.lib.recon.prompts import Turn  # noqa: E402
from src.utils.inject_director import parse_prompts_json  # noqa: E402
from src.utils.task_standard import TaskWindow  # noqa: E402

TURNS = [
    Turn(0, "morning.", day=1, time="08:12"),
    Turn(1, "quick one.", day=2, time="09:40"),
    Turn(2, "last look.", day=3, time="16:45"),
]
WINDOW = TaskWindow(date(2026, 10, 14), date(2026, 10, 16), "Africa/Accra", "test")
TZ = "Africa/Accra"

#: The run's own stamps, carrying the dispatch latency a real run carries and
#: skipping a calendar day between T1 and T2 exactly as willie's run does.
RUN_STAMPS = [
    "2026-10-14T08:12:04.777Z",
    "2026-10-15T09:40:00.810Z",
    "2026-10-17T16:45:01.224Z",
]


def _bundle(tmp_path: Path, stamps=RUN_STAMPS, model="claude-opus-5",
            run="run_1", system_prompt="") -> Path:
    d = tmp_path / "bundle" / "trajectories" / model / run
    d.mkdir(parents=True)
    messages = []
    for i, stamp in enumerate(stamps):
        messages.append({"type": "message", "id": f"u{i}", "timestamp": stamp,
                         "message": {"role": "user",
                                     "content": [{"type": "text", "text": "x"}]}})
        messages.append({"type": "message", "id": f"a{i}", "timestamp": stamp,
                         "message": {"role": "assistant", "content": []}})
    (d / "output.json").write_text(json.dumps(
        {"meta_info": {"system_prompt": system_prompt}, "messages": messages}),
        encoding="utf-8")
    return tmp_path / "bundle"


def test_trajectory_instants_win_and_are_exact(tmp_path):
    got = S.resolve(_bundle(tmp_path), TURNS, WINDOW, TZ)
    assert got.fidelity == "exact"
    assert got.source == "trajectory:claude-opus-5/run_1"
    assert [v.isoformat() for v in got.values] == [
        "2026-10-14T08:12:00+00:00",
        "2026-10-15T09:40:00+00:00",
        "2026-10-17T16:45:00+00:00",
    ]


def test_dispatch_latency_is_truncated_and_reported(tmp_path):
    got = S.resolve(_bundle(tmp_path), TURNS, WINDOW, TZ)
    assert got.jitter_ms == 4777
    assert all(v.second == 0 and v.microsecond == 0 for v in got.values)


def test_labels_are_used_only_when_no_run_covers_the_turns(tmp_path):
    got = S.resolve(_bundle(tmp_path, stamps=RUN_STAMPS[:2]), TURNS, WINDOW, TZ)
    assert got.fidelity == "degraded"
    assert got.source == "labels+window-start"


def test_label_instants_drift_from_the_run_they_replace(tmp_path):
    """The failure mode the degraded stamp exists for."""
    exact = S.resolve(_bundle(tmp_path), TURNS, WINDOW, TZ)
    labels = S.from_labels(TURNS, WINDOW, TZ)
    assert exact.values[:2] == labels.values[:2]
    assert exact.values[2] != labels.values[2]
    assert any("skips" in n for n in labels.notes)


def test_a_bundle_with_no_instants_at_all_is_refused(tmp_path):
    bare = tmp_path / "bare"
    bare.mkdir()
    unlabelled = [Turn(0, "a"), Turn(1, "b")]
    assert S.resolve(bare, unlabelled, WINDOW, TZ).values == []


def test_labels_without_a_window_are_refused(tmp_path):
    bare = tmp_path / "bare"
    bare.mkdir()
    assert S.resolve(bare, TURNS, None, TZ).values == []


def test_an_unknown_timezone_refuses_rather_than_writing_naive_stamps(tmp_path):
    bare = tmp_path / "bare"
    bare.mkdir()
    got = S.resolve(bare, TURNS, WINDOW, "")
    assert got.values == []
    assert any("offset-aware" in n for n in got.notes)


def test_named_run_is_honoured(tmp_path):
    bundle = _bundle(tmp_path)
    second = bundle / "trajectories" / "claude-opus-5" / "run_2"
    second.mkdir()
    (second / "output.json").write_text(json.dumps({"messages": [
        {"timestamp": "2027-01-01T01:00:00Z", "message": {"role": "user"}},
        {"timestamp": "2027-01-01T02:00:00Z", "message": {"role": "user"}},
        {"timestamp": "2027-01-01T03:00:00Z", "message": {"role": "user"}},
    ]}), encoding="utf-8")
    got = S.resolve(bundle, TURNS, WINDOW, TZ, run="run_2")
    assert got.source == "trajectory:claude-opus-5/run_2"
    assert got.values[0].isoformat() == "2027-01-01T01:00:00+00:00"


def test_a_short_run_is_reported_not_padded(tmp_path):
    got = S.from_trajectory(_bundle(tmp_path, stamps=RUN_STAMPS[:2]), 3, TZ)
    assert len(got.values) == 2
    assert any("no published run covers them all" in n for n in got.notes)


def test_emitted_json_is_what_the_loader_accepts(tmp_path):
    instants = S.resolve(_bundle(tmp_path), TURNS, WINDOW, TZ)
    payload = S.build("t", "Willie Prince", TZ, TURNS, instants)
    out = tmp_path / "prompts.json"
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    messages, meta = parse_prompts_json(out)
    assert messages == [t.text for t in TURNS]
    assert meta["turn_count"] == 3
    assert "fidelity" not in meta


def test_a_degraded_schedule_says_so_in_the_file(tmp_path):
    payload = S.build("t", "p", TZ, TURNS, S.from_labels(TURNS, WINDOW, TZ))
    assert payload["fidelity"] == "degraded"
    assert "degraded" in payload["source"]


def test_turn_text_comes_from_the_prompt_never_the_trajectory(tmp_path):
    """The trajectory's T0 carries the workspace footer the loader re-appends."""
    payload = S.build("t", "p", TZ, TURNS, S.resolve(_bundle(tmp_path), TURNS,
                                                     WINDOW, TZ))
    assert [t["message"] for t in payload["turns"]] == [t.text for t in TURNS]
    assert all("Workspace inputs" not in t["message"] for t in payload["turns"])


def test_system_prompt_is_read_off_the_run(tmp_path):
    bundle = _bundle(tmp_path, system_prompt="be terse")
    label, path = S.find_runs(bundle)[0]
    assert S.system_prompt(path) == "be terse"


@pytest.mark.parametrize("stamp", ["not-a-date", "2026-10-14T08:12:00", "", None])
def test_unusable_stamps_are_dropped_not_guessed(tmp_path, stamp):
    bundle = tmp_path / "b"
    d = bundle / "trajectories" / "m" / "run_1"
    d.mkdir(parents=True)
    (d / "output.json").write_text(json.dumps({"messages": [
        {"timestamp": stamp, "message": {"role": "user"}}]}), encoding="utf-8")
    assert S.user_instants(d / "output.json") == []
