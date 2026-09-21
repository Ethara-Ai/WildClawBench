"""The three task-format standards, generator side and validator side.

Each standard is covered twice: once proving the generator EMITS a compliant
artifact, once proving the validator REJECTS a non-compliant one. A standard
enforced on only one side drifts, which is how a hard-coded CURRENT_DATE
reached delivery in the first place.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_DIR = REPO_ROOT / "script"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.utils import task_standard as ts  # noqa: E402

CANONICAL_HEADER = (
    "# task_id: eric_lambert_7f3a9c2e\n"
    "# persona: Eric Isabel Lambert\n"
    "# timezone: America/Chicago\n"
    "# window: 2026-10-06 to 2026-10-11 (6 days)\n"
    "# turn_count: 20\n"
)


def _load_script(filename: str, mod_alias: str):
    path = SCRIPT_DIR / filename
    spec = importlib.util.spec_from_file_location(mod_alias, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _window(start="2026-10-06", end="2026-10-11") -> ts.TaskWindow:
    return ts.TaskWindow(date.fromisoformat(start), date.fromisoformat(end))


def _task(tmp_path: Path, *, prompts_json=None, prompts_txt=None, truth=None,
          task_yaml=None, name="TASK") -> Path:
    task = tmp_path / name
    task.mkdir(parents=True, exist_ok=True)
    if prompts_json is not None:
        (task / "prompts.json").write_text(json.dumps(prompts_json), encoding="utf-8")
    if prompts_txt is not None:
        (task / "prompts.txt").write_text(prompts_txt, encoding="utf-8")
    if truth is not None:
        (task / "TRUTH.md").write_text(truth, encoding="utf-8")
    if task_yaml is not None:
        (task / "task.yaml").write_text(task_yaml, encoding="utf-8")
    return task


# ======================================================================
# Standard A — the date derives from the task's own window
# ======================================================================


def test_window_days_is_inclusive_on_both_ends():
    assert _window("2026-10-06", "2026-10-11").days == 6
    assert _window("2026-10-06", "2026-10-06").days == 1


def test_current_date_rule_is_the_window_start():
    assert _window("2026-10-06", "2026-10-11").current_date == "2026-10-06"


@pytest.mark.parametrize("payload,expected_source,expected_start", [
    ({"window": {"start": "2026-10-06", "end": "2026-10-11"}},
     "prompts.json:window", "2026-10-06"),
    ({"window": "2026-10-06 to 2026-10-11"}, "prompts.json:window", "2026-10-06"),
    ({"turns": [{"timestamp": "2026-10-06T09:00:00-05:00"},
                {"timestamp": "2026-10-11T18:00:00-05:00"}]},
     "prompts.json:turns", "2026-10-06"),
])
def test_resolve_window_reads_every_declaration_shape(
        tmp_path, payload, expected_source, expected_start):
    window = ts.resolve_window(_task(tmp_path, prompts_json=payload))
    assert window is not None
    assert window.source == expected_source
    assert window.start.isoformat() == expected_start
    assert window.end.isoformat() == "2026-10-11"


def test_resolve_window_falls_back_to_task_yaml(tmp_path):
    task = _task(tmp_path, task_yaml="window: 2026-03-01 to 2026-03-04\n"
                                     "timezone: Europe/Berlin\n")
    window = ts.resolve_window(task)
    assert window is not None
    assert (window.start.isoformat(), window.days) == ("2026-03-01", 4)
    assert window.timezone == "Europe/Berlin"


def test_resolve_window_is_none_when_the_task_declares_nothing(tmp_path):
    assert ts.resolve_window(_task(tmp_path, prompts_json={"turns": [{"turn": "T0"}]})) is None


def test_check_current_date_accepts_any_day_inside_the_window():
    assert ts.check_current_date("2026-10-08", _window()) is None


@pytest.mark.parametrize("value,fragment", [
    ("2026-05-28", "outside the task window"),
    ("", "absent or not a YYYY-MM-DD date"),
    (None, "absent or not a YYYY-MM-DD date"),
    ("not-a-date", "absent or not a YYYY-MM-DD date"),
])
def test_check_current_date_rejects_hardcoded_and_unparseable(value, fragment):
    err = ts.check_current_date(value, _window())
    assert err and fragment in err


def test_check_current_date_rejects_a_task_with_no_window():
    assert "no resolvable window" in (ts.check_current_date("2026-10-06", None) or "")


def test_resolve_current_date_prefers_the_window_over_the_static_default(tmp_path):
    """A schedule with no resolvable T0 must still date itself from its window."""
    from src.utils.harbor.compose import DEFAULT_CURRENT_DATE, resolve_current_date

    task = _task(tmp_path, prompts_json={
        "window": {"start": "2026-10-06", "end": "2026-10-11"},
        "turns": [{"turn": "T0"}],
    })
    assert resolve_current_date(task) == "2026-10-06" != DEFAULT_CURRENT_DATE


def test_resolve_current_date_still_prefers_the_sim_clock(tmp_path):
    from src.utils.harbor.compose import resolve_current_date

    task = _task(tmp_path, prompts_json={
        "timezone": "America/Chicago",
        "window": {"start": "2026-10-06", "end": "2026-10-11"},
        "turns": [{"turn": "T0", "timestamp": "2026-10-07T07:38:00-05:00"}],
    })
    assert resolve_current_date(task) == "2026-10-07"


def test_resolve_current_date_keeps_the_static_default_without_a_window(tmp_path):
    from src.utils.harbor.compose import DEFAULT_CURRENT_DATE, resolve_current_date

    task = _task(tmp_path, prompts_json={"turns": [{"turn": "T0"}]})
    assert resolve_current_date(task) == DEFAULT_CURRENT_DATE


# ======================================================================
# Standard B — TRUTH.md carries exactly three sections
# ======================================================================

def _truth_doc(sections=None) -> str:
    """A TRUTH.md carrying `sections` (the mandated skeleton by default)."""
    names = list(ts.TRUTH_SECTIONS) if sections is None else list(sections)
    body = "".join(f"## {i}. {name}\n\nbody\n\n"
                   for i, name in enumerate(names, start=1))
    return "# TRUTH\n\n" + body


_GOOD_TRUTH = _truth_doc()


def test_truth_sections_ignores_numbering_and_the_document_title():
    assert ts.truth_sections(_GOOD_TRUTH) == list(ts.TRUTH_SECTIONS)


def test_truth_sections_accepts_the_shipped_qualified_headings():
    # golden_steer_flow qualifies several headings in parentheses; the
    # canonicaliser folds a trailing qualifier, so they stay canonical.
    text = (_GOOD_TRUTH
            .replace("## 1. Focal Event", "## 1. Focal Event and Scope")
            .replace("## 4. Fairness Ledger", "## 4. Fairness Ledger (feeds G18)")
            .replace("## 7. Grader Notes",
                     "## 7. Grader Notes (CONSTANTS + coverage map)"))
    assert ts.check_truth_sections(text) is None


def test_check_truth_sections_accepts_the_generators_eight_sections():
    # The trailing five are intentional golden_steer_flow output, not drift:
    # demanding only the leading three refused every task in the corpus.
    assert ts.TRUTH_SECTIONS[3:] == ("Fairness Ledger", "Signal Set and Noise Purity",
                                     "Poison-Pill Record", "Grader Notes",
                                     "BUILD_FINGERPRINT")
    assert ts.check_truth_sections(_GOOD_TRUTH) is None


def test_every_mandated_section_can_be_rendered():
    # render_truth_skeleton indexes _TRUTH_PROMPTS by name, so a section added
    # to the tuple without a prompt is a KeyError at generation time.
    assert set(ts.TRUTH_SECTIONS) <= set(ts._TRUTH_PROMPTS)


def test_check_truth_sections_rejects_extra_sections():
    err = ts.check_truth_sections(_GOOD_TRUTH + "\n## 9. Appendix\n\nq\n")
    assert err and "unexpected ['Appendix']" in err


def test_check_truth_sections_rejects_a_missing_section():
    err = ts.check_truth_sections(_truth_doc(
        [s for s in ts.TRUTH_SECTIONS if s != "Value Lock"]))
    assert err and "missing ['Value Lock']" in err


def test_check_truth_sections_rejects_reordering():
    swapped = list(ts.TRUTH_SECTIONS)
    swapped[0], swapped[2] = swapped[2], swapped[0]
    err = ts.check_truth_sections(_truth_doc(swapped))
    assert err and "out of order" in err


def test_render_truth_skeleton_satisfies_its_own_validator():
    assert ts.check_truth_sections(ts.render_truth_skeleton("t1", _window())) is None


# ======================================================================
# Standard C — the five-line prompt header block
# ======================================================================


def test_render_prompt_header_matches_the_canonical_block():
    assert ts.render_prompt_header(
        "eric_lambert_7f3a9c2e", "Eric Isabel Lambert", "America/Chicago",
        _window(), 20) == CANONICAL_HEADER


def test_render_prompt_header_round_trips_through_its_validator():
    text = ts.render_prompt_header("t1", "P", "UTC", _window(), 4) + "\n--- TURN T0 ---\nhi\n"
    assert ts.check_prompt_header(text, task_id="t1", persona="P", timezone="UTC",
                                  window=_window(), turn_count=4) == []


def test_check_prompt_header_accepts_trailing_extra_comment_lines():
    text = CANONICAL_HEADER + "# note: extra provenance\n\n--- TURN T0 ---\n"
    assert ts.check_prompt_header(text) == []


@pytest.mark.parametrize("text,fragment", [
    ("--- TURN T0 ---\nhi\n", "must open with exactly"),
    ("task_id: x\npersona: P\ntimezone: UTC\n"
     "window: 2026-10-06 to 2026-10-11 (6 days)\nturn_count: 20\n",
     "must open with exactly"),
    (CANONICAL_HEADER.replace("# turn_count:", "# turns:"), "must open with exactly"),
    (CANONICAL_HEADER.replace("# persona: Eric Isabel Lambert\n", ""),
     "must open with exactly"),
])
def test_check_prompt_header_rejects_a_broken_key_block(text, fragment):
    errors = ts.check_prompt_header(text)
    assert errors and fragment in errors[0]


def test_check_prompt_header_rejects_a_window_without_the_day_count():
    errors = ts.check_prompt_header(
        CANONICAL_HEADER.replace(" (6 days)", ""))
    assert any("<YYYY-MM-DD> to <YYYY-MM-DD> (<N> days)" in e for e in errors)


def test_check_prompt_header_rejects_a_miscounted_day_span():
    errors = ts.check_prompt_header(CANONICAL_HEADER.replace("(6 days)", "(5 days)"))
    assert any("spans 6" in e for e in errors)


def test_check_prompt_header_rejects_a_window_disagreeing_with_the_task():
    errors = ts.check_prompt_header(CANONICAL_HEADER,
                                    window=_window("2026-01-01", "2026-01-06"))
    assert any("!= task's 2026-01-01..2026-01-06" in e for e in errors)


def test_check_prompt_header_rejects_a_turn_count_disagreeing_with_prompts_json():
    errors = ts.check_prompt_header(CANONICAL_HEADER, turn_count=9)
    assert any("turn_count 20 != 9 actual turns" in e for e in errors)


def test_check_prompt_header_rejects_a_non_integer_turn_count():
    errors = ts.check_prompt_header(
        CANONICAL_HEADER.replace("# turn_count: 20", "# turn_count: 9 across 4 days"))
    assert any("not a bare integer" in e for e in errors)


@pytest.mark.parametrize("key,bad", [
    ("task_id", "# task_id: other\n"),
    ("persona", "# persona: Someone Else\n"),
    ("timezone", "# timezone: Europe/Berlin\n"),
])
def test_check_prompt_header_cross_checks_identity_against_the_task(key, bad):
    original = [line for line in CANONICAL_HEADER.splitlines(True)
                if line.startswith(f"# {key}:")][0]
    errors = ts.check_prompt_header(
        CANONICAL_HEADER.replace(original, bad),
        task_id="eric_lambert_7f3a9c2e", persona="Eric Isabel Lambert",
        timezone="America/Chicago")
    assert any(f"header {key}" in e for e in errors)


# ======================================================================
# Validator wiring — script/preflight_task.py section 6
# ======================================================================


@pytest.fixture()
def pf(monkeypatch):
    mod = _load_script("preflight_task.py", "_t_preflight_standards")
    monkeypatch.setattr(mod, "_counts", {"PASS": 0, "WARN": 0, "FAIL": 0})
    monkeypatch.setattr(mod, "LEGACY", False)
    return mod


def _compliant_task(tmp_path: Path, name="GOOD") -> Path:
    payload = {
        "task_id": "t1", "persona": "P", "timezone": "America/Chicago",
        "window": {"start": "2026-10-06", "end": "2026-10-11"},
        "turns": [{"turn": f"T{i}", "timestamp": f"2026-10-0{6 + i}T09:00:00-05:00"}
                  for i in range(3)],
    }
    header = ts.render_prompt_header("t1", "P", "America/Chicago", _window(), 3)
    return _task(tmp_path, name=name, prompts_json=payload,
                 prompts_txt=header + "\n--- TURN T0 ---\nhi\n", truth=_GOOD_TRUTH)


def test_preflight_section6_passes_a_compliant_task(pf, tmp_path, capsys):
    pf.check_task_standards(_compliant_task(tmp_path))
    out = capsys.readouterr().out
    assert pf._counts["FAIL"] == 0
    assert "CURRENT_DATE 2026-10-06 derives from the task window" in out
    assert "TRUTH.md has exactly" in out
    assert "prompts.txt opens with the 5-line header block" in out


def test_preflight_section6_fails_each_standard_independently(pf, tmp_path, capsys):
    task = _compliant_task(tmp_path, name="BAD")
    (task / "TRUTH.md").write_text(_GOOD_TRUTH + "\n## 9. Appendix\n\nq\n",
                                   encoding="utf-8")
    (task / "prompts.txt").write_text("--- TURN T0 ---\nhi\n", encoding="utf-8")
    pf.check_task_standards(task)
    out = capsys.readouterr().out
    assert pf._counts["FAIL"] >= 1
    assert "unexpected ['Appendix']" in out
    assert "must open with exactly" in out


def test_a_header_nit_is_a_note_while_prompts_json_carries_the_turns(pf, tmp_path,
                                                                    capsys):
    # The runtime builds every turn from prompts.json and parse_prompts_file
    # drops `#` lines, so `turns:` where the standard says `turn_count:` is a
    # convention nit on decorative text — not a reason to refuse the bundle.
    task = _compliant_task(tmp_path, name="HEADERNIT")
    (task / "prompts.txt").write_text(
        (task / "prompts.txt").read_text(encoding="utf-8")
        .replace("# turn_count: 3", "# turns: 3"), encoding="utf-8")
    pf.check_task_standards(task)
    out = capsys.readouterr().out
    assert pf._counts["FAIL"] == 0
    assert pf._counts["WARN"] >= 1
    assert "decorative" in out


def test_the_same_header_nit_is_a_failure_when_prompts_txt_is_the_trajectory(
        pf, tmp_path, capsys):
    task = _compliant_task(tmp_path, name="TXTONLY")
    (task / "prompts.json").unlink()
    (task / "prompts.txt").write_text(
        (task / "prompts.txt").read_text(encoding="utf-8")
        .replace("# turn_count: 3", "# turns: 3"), encoding="utf-8")
    pf.check_task_standards(task)
    assert pf._counts["FAIL"] >= 1
    assert "decorative" not in capsys.readouterr().out


def test_preflight_section6_fails_a_task_with_no_window(pf, tmp_path, capsys):
    task = _task(tmp_path, prompts_json={"turns": [{"turn": "T0"}]},
                 prompts_txt=CANONICAL_HEADER, truth=_GOOD_TRUTH)
    pf.check_task_standards(task)
    assert pf._counts["FAIL"] >= 1
    assert "task declares no window" in capsys.readouterr().out


def test_preflight_section6_fails_an_out_of_window_task_toml(pf, tmp_path, capsys):
    task = _compliant_task(tmp_path, name="TOML")
    (task / "task.toml").write_text(
        '[environment.env]\nCURRENT_DATE = "2026-05-28"\n', encoding="utf-8")
    pf.check_task_standards(task)
    out = capsys.readouterr().out
    assert pf._counts["FAIL"] >= 1
    assert "task.toml CURRENT_DATE 2026-05-28 is outside the task window" in out


def test_preflight_section6_accepts_an_in_window_task_toml(pf, tmp_path, capsys):
    task = _compliant_task(tmp_path, name="TOML_OK")
    (task / "task.toml").write_text(
        '[environment.env]\nCURRENT_DATE = "2026-10-06"\n', encoding="utf-8")
    pf.check_task_standards(task)
    assert pf._counts["FAIL"] == 0
    assert "task.toml CURRENT_DATE 2026-10-06 inside window" in capsys.readouterr().out


def test_preflight_section6_accepts_the_legacy_truth_filename(pf, tmp_path, capsys):
    task = _compliant_task(tmp_path, name="LEGACYNAME")
    (task / "TRUTH.md").unlink()
    (task / "golden_steer_flow.md").write_text(_GOOD_TRUTH, encoding="utf-8")
    pf.check_task_standards(task)
    assert pf._counts["FAIL"] == 0
    assert "golden_steer_flow.md has exactly" in capsys.readouterr().out


def test_preflight_legacy_mode_downgrades_standard_fails_to_warns(pf, tmp_path, monkeypatch):
    monkeypatch.setattr(pf, "LEGACY", True)
    task = _task(tmp_path, name="OLD", prompts_json={"turns": [{"turn": "T0"}]},
                 prompts_txt="--- TURN T0 ---\nhi\n")
    pf.check_task_standards(task)
    assert pf._counts["FAIL"] == 0
    assert pf._counts["WARN"] >= 3


def test_preflight_legacy_flag_is_parsed_from_the_command_line(pf, tmp_path, capsys):
    task = _task(tmp_path, name="CLI", prompts_json={"turns": [{"turn": "T0"}]},
                 prompts_txt="--- TURN T0 ---\nhi\n")
    pf.main(["--legacy", str(task)])
    assert pf.LEGACY is True
    assert "task declares no window" in capsys.readouterr().out


# ======================================================================
# Generator wiring — script/compile_declarative_task.py
# ======================================================================

@pytest.fixture()
def compiler():
    pytest.importorskip("tomllib",
                        reason="compile_declarative_task.py needs tomllib")
    return _load_script("compile_declarative_task.py", "_t_compile_standards")


def _source(tmp_path: Path, metadata: dict, stages_extra="") -> Path:
    src = tmp_path / "src_task"
    src.mkdir(parents=True, exist_ok=True)
    (src / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    (src / "prompt.txt").write_text("do the thing\n", encoding="utf-8")
    (src / "stages.toml").write_text(
        '[[stages]]\nname = "seed"\n' + stages_extra +
        '\n[[stages]]\nturn = "second message"\nname = "s1"\n', encoding="utf-8")
    (src / "rubrics.json").write_text(json.dumps(
        {"judge": [{"criterion": "did the thing", "weight": 5}], "checks": []}),
        encoding="utf-8")
    return src


_META = {
    "task_id": "t1", "difficulty": "medium", "modalities": ["text"],
    "l1": "a", "l2": "b", "task_type": "c", "required_apis": [],
    "persona": "Eric Isabel Lambert", "timezone": "America/Chicago",
}


def test_compiler_emits_a_bundle_that_passes_every_standard(compiler, tmp_path):
    meta = dict(_META, window={"start": "2026-10-06", "end": "2026-10-11"})
    out = tmp_path / "out"
    compiler.compile_task(_source(tmp_path, meta), out, force=True)

    assert (out / "prompts.txt").read_text(encoding="utf-8").startswith(
        ts.render_prompt_header("t1", "Eric Isabel Lambert", "America/Chicago",
                                _window(), 2))
    assert ts.check_truth_sections((out / "TRUTH.md").read_text(encoding="utf-8")) is None
    window = ts.resolve_window(out)
    assert window is not None and window.current_date == "2026-10-06"
    assert "window: 2026-10-06 to 2026-10-11" in (out / "task.yaml").read_text(encoding="utf-8")


def test_compiler_derives_the_window_from_stage_timestamps(compiler, tmp_path):
    src = _source(tmp_path, dict(_META),
                  stages_extra='applied_at = "2026-10-06T08:30:00-05:00"\n')
    window = compiler.resolve_source_window(
        dict(_META), [{"applied_at": "2026-10-06T08:30:00-05:00"},
                      {"applied_at": "2026-10-09T14:00:00-05:00"}])
    assert (window.start.isoformat(), window.end.isoformat()) == ("2026-10-06", "2026-10-09")
    assert src.is_dir()


def test_compiler_refuses_a_task_it_cannot_date(compiler, tmp_path):
    with pytest.raises(SystemExit) as exc:
        compiler.compile_task(_source(tmp_path, dict(_META)), tmp_path / "o", force=True)
    assert "cannot date this task" in str(exc.value)


def test_compiler_rejects_an_undated_window_declaration(compiler):
    with pytest.raises(SystemExit) as exc:
        compiler.resolve_source_window(dict(_META, window="Day 1 through Day 2"), [])
    assert "names no YYYY-MM-DD date" in str(exc.value)


def test_compiler_carries_an_authored_truth_md_through(compiler, tmp_path):
    meta = dict(_META, window={"start": "2026-10-06", "end": "2026-10-11"})
    src = _source(tmp_path, meta)
    authored = _GOOD_TRUTH.replace("x\n", "the real focal event\n")
    (src / "TRUTH.md").write_text(authored, encoding="utf-8")
    out = tmp_path / "out2"
    compiler.compile_task(src, out, force=True)
    assert (out / "TRUTH.md").read_text(encoding="utf-8") == authored


def test_compiler_header_turn_count_matches_the_emitted_turns(compiler, tmp_path):
    meta = dict(_META, window={"start": "2026-10-06", "end": "2026-10-11"})
    out = tmp_path / "out3"
    compiler.compile_task(_source(tmp_path, meta), out, force=True)
    text = (out / "prompts.txt").read_text(encoding="utf-8")
    declared = int(ts.parse_prompt_header(text)["turn_count"])
    assert declared == text.count("--- TURN T")
