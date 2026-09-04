"""Ground-truth publishing in the raw-output -> published-bundle repackager.

`script/repackage_to_bundle.py` re-sources a handful of files from the ORIGINAL
input task dir (prompt, persona/, staged inputs). This suite pins the
truth-doc -> TRUTH.md feature added on top of that flow:

  1. When the input task dir ships a truth doc (TRUTH.md / GTFA.md /
     golden_steer_flow.md / ground-truth.md, resolved first-match-wins), the
     bundle gets data/solution/TRUTH.md, a VERBATIM byte-copy of the source
     (no section slicing — the whole doc is published).
  2. When it does NOT ship any such file, no TRUTH.md is emitted and the
     conversion still succeeds (the file is optional, like the prompt/persona/).
  3. The publish is keyed off the fuzzy persona-core match: if the run-output
     task dir can't be matched back to an input dir, nothing is re-sourced.

The legacy heading-slicer (`extract_ground_truth_sections`) is retained in the
module and still unit-tested below (it is no longer on the publish path, but is
kept for callers/back-compat); this suite also still pins its regex tolerance.

`script/` is not an importable package, so the module is loaded by path the
same way tests/test_connector_ssrf_guard.py loads connector scripts.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_repackager():
    path = _REPO_ROOT / "script" / "repackage_to_bundle.py"
    assert path.exists(), f"repackager missing: {path}"
    spec = importlib.util.spec_from_file_location("_test_repackage_to_bundle", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_test_repackage_to_bundle"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def rp():
    return _load_repackager()


def _mk_source_run_tree(source_root: Path, task_id: str) -> Path:
    """Minimal raw run-output tree convert_task will accept and emit from."""
    task_dir = source_root / task_id
    run_dir = task_dir / "trajectories" / "openclaw" / "run_1"
    (run_dir / "task_output" / "logs" / "verifier").mkdir(parents=True)
    (task_dir / "rubric.json").write_text("[]", encoding="utf-8")
    (run_dir / "output.json").write_text("{}", encoding="utf-8")
    (run_dir / "score.json").write_text(
        json.dumps({"combined_reward": 1.0, "criteria": []}), encoding="utf-8"
    )
    return task_dir


def _mk_input_dir(
    input_root: Path,
    name: str,
    *,
    steer: str | None,
    steer_filename: str = "golden_steer_flow.md",
) -> Path:
    """Original input task dir; optionally ships a ground-truth source file.

    `steer_filename` defaults to the legacy name for back-compat with existing
    tests; pass "GTFA.md" or "ground-truth.md" to exercise alternate sources.
    """
    d = input_root / name
    d.mkdir(parents=True)
    (d / "prompt.txt").write_text("do the thing", encoding="utf-8")
    if steer is not None:
        (d / steer_filename).write_text(steer, encoding="utf-8")
    return d


_RUN_TASK_ID = "darren_weston_2b2baa81"
_INPUT_TASK_ID = "darren_weston_01"


_CANONICAL_STEER = """\
# golden_steer_flow.md
## Task: a thing

---

## Section 1: Focal Event and Scope

**Focal event:** the championship.
**Scope:** only the current order.

---

## Section 2: Canonical Solve Path

1. Discover service.
2. Resolve value.

---

## Section 3: Value Lock

```
VALUE_LOCK:
  KEY = "value"
```

---

## Section 4: Fairness Ledger

private author notes, must not leak.

---

## Section 5: Phase-2 Fingerprint

more private notes.
"""


def _assert_three_sections_present(body: str) -> None:
    assert "Focal Event" in body or "focal event" in body.lower()
    assert "Canonical Solve Path" in body or "canonical solve path" in body.lower()
    assert "Value Lock" in body or "VALUE_LOCK" in body or "value lock" in body.lower()


def _assert_leakage_sections_absent(body: str) -> None:
    assert "Fairness Ledger" not in body
    assert "Phase-2 Fingerprint" not in body
    assert "private author notes" not in body
    assert "more private notes" not in body


def test_ground_truth_emitted_verbatim(rp, tmp_path):
    source_root = tmp_path / "src"
    input_root = tmp_path / "input"
    dest_root = tmp_path / "out"
    task_dir = _mk_source_run_tree(source_root, _RUN_TASK_ID)
    _mk_input_dir(input_root, _INPUT_TASK_ID, steer=_CANONICAL_STEER)

    bundle = rp.convert_task(task_dir, dest_root, input_root, False, False)

    assert bundle is not None
    gt = bundle / "data" / "solution" / "TRUTH.md"
    assert gt.is_file(), "TRUTH.md not emitted under data/solution/"
    body = gt.read_text(encoding="utf-8")
    # Verbatim: the FULL author doc is published byte-for-byte, INCLUDING the
    # sections the old slicer used to drop.
    assert body == _CANONICAL_STEER
    _assert_three_sections_present(body)
    assert "Fairness Ledger" in body, "verbatim copy must keep all sections"
    assert "Phase-2 Fingerprint" in body
    assert "VALUE_LOCK:" in body
    assert not (bundle / "ground-truth.md").exists()
    assert not (bundle / "golden_steer_flow.md").exists()


def test_no_ground_truth_when_source_absent(rp, tmp_path, capsys):
    """When NONE of the accepted source filenames exist in the input task dir,
    TRUTH.md is skipped AND an unconditional stderr warning is emitted naming
    all candidates. The prompt (PROMPT.md) is still published."""
    source_root = tmp_path / "src"
    input_root = tmp_path / "input"
    dest_root = tmp_path / "out"
    task_dir = _mk_source_run_tree(source_root, _RUN_TASK_ID)
    _mk_input_dir(input_root, _INPUT_TASK_ID, steer=None)

    bundle = rp.convert_task(task_dir, dest_root, input_root, False, False)

    assert bundle is not None
    assert not (bundle / "data" / "solution" / "TRUTH.md").exists()
    assert (bundle / "prompt.txt").is_file()
    err = capsys.readouterr().err
    assert "no ground-truth source file found" in err, (
        f"missing-source warning not on stderr; got: {err!r}"
    )
    for candidate in rp.GROUND_TRUTH_SOURCE_CANDIDATES:
        assert candidate in err, f"warning must list {candidate}; got: {err!r}"


@pytest.mark.parametrize(
    "steer_filename",
    ["TRUTH.md", "GTFA.md", "golden_steer_flow.md", "ground-truth.md"],
)
def test_ground_truth_accepts_all_source_filenames(rp, tmp_path, steer_filename):
    source_root = tmp_path / "src"
    input_root = tmp_path / "input"
    dest_root = tmp_path / "out"
    task_dir = _mk_source_run_tree(source_root, _RUN_TASK_ID)
    _mk_input_dir(
        input_root,
        _INPUT_TASK_ID,
        steer=_CANONICAL_STEER,
        steer_filename=steer_filename,
    )

    bundle = rp.convert_task(task_dir, dest_root, input_root, False, False)

    gt = bundle / "data" / "solution" / "TRUTH.md"
    assert gt.is_file(), (
        f"TRUTH.md not emitted when source is {steer_filename!r}"
    )
    # Verbatim regardless of which source filename supplied it.
    assert gt.read_text(encoding="utf-8") == _CANONICAL_STEER


def test_ground_truth_source_priority_truth_wins_over_gtfa(rp, tmp_path):
    """First-match-wins resolution: when multiple candidates coexist, the
    earliest in GROUND_TRUTH_SOURCE_CANDIDATES (TRUTH.md) is selected and
    published verbatim."""
    source_root = tmp_path / "src"
    input_root = tmp_path / "input"
    dest_root = tmp_path / "out"
    task_dir = _mk_source_run_tree(source_root, _RUN_TASK_ID)
    input_task_dir = _mk_input_dir(input_root, _INPUT_TASK_ID, steer=None)
    # Marker text distinguishes which file actually got picked.
    truth_text = "## Focal Event\nfrom-truth\n"
    gtfa_text = "## Focal Event\nfrom-gtfa\n"
    legacy_text = "## Focal Event\nfrom-legacy\n"
    fallback_text = "## Focal Event\nfrom-fallback\n"
    (input_task_dir / "TRUTH.md").write_text(truth_text, encoding="utf-8")
    (input_task_dir / "GTFA.md").write_text(gtfa_text, encoding="utf-8")
    (input_task_dir / "golden_steer_flow.md").write_text(legacy_text, encoding="utf-8")
    (input_task_dir / "ground-truth.md").write_text(fallback_text, encoding="utf-8")

    bundle = rp.convert_task(task_dir, dest_root, input_root, False, False)

    body = (bundle / "data" / "solution" / "TRUTH.md").read_text(encoding="utf-8")
    assert body == truth_text, "TRUTH.md must win when all candidates coexist"


def test_ground_truth_source_priority_gtfa_wins_over_legacy(rp, tmp_path):
    source_root = tmp_path / "src"
    input_root = tmp_path / "input"
    dest_root = tmp_path / "out"
    task_dir = _mk_source_run_tree(source_root, _RUN_TASK_ID)
    input_task_dir = _mk_input_dir(input_root, _INPUT_TASK_ID, steer=None)
    gtfa_text = "## Focal Event\nfrom-gtfa\n"
    (input_task_dir / "GTFA.md").write_text(gtfa_text, encoding="utf-8")
    (input_task_dir / "golden_steer_flow.md").write_text(
        "## Focal Event\nfrom-legacy\n", encoding="utf-8"
    )
    (input_task_dir / "ground-truth.md").write_text(
        "## Focal Event\nfrom-fallback\n", encoding="utf-8"
    )

    bundle = rp.convert_task(task_dir, dest_root, input_root, False, False)

    body = (bundle / "data" / "solution" / "TRUTH.md").read_text(encoding="utf-8")
    assert body == gtfa_text, "GTFA.md must win over legacy/fallback when TRUTH.md absent"


def test_ground_truth_verbatim_from_ground_truth_md(rp, tmp_path):
    """When only ground-truth.md exists (lowest-priority fallback), it is
    published verbatim as TRUTH.md."""
    source_root = tmp_path / "src"
    input_root = tmp_path / "input"
    dest_root = tmp_path / "out"
    task_dir = _mk_source_run_tree(source_root, _RUN_TASK_ID)
    input_task_dir = _mk_input_dir(input_root, _INPUT_TASK_ID, steer=None)
    pre_sliced = (
        "## Focal Event\nbody-fe\n"
        "## Canonical Solve Path\nbody-csp\n"
        "## Value Lock\nbody-vl\n"
    )
    (input_task_dir / "ground-truth.md").write_text(pre_sliced, encoding="utf-8")

    bundle = rp.convert_task(task_dir, dest_root, input_root, False, False)

    assert (bundle / "data" / "solution" / "TRUTH.md").read_text(encoding="utf-8") == pre_sliced


def test_ground_truth_source_candidates_constant_is_exact():
    """Pins the canonical lookup list. Order matters (priority)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_rp", _REPO_ROOT / "script" / "repackage_to_bundle.py"
    )
    rp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rp)
    assert rp.GROUND_TRUTH_SOURCE_CANDIDATES == (
        "TRUTH.md",
        "GTFA.md",
        "golden_steer_flow.md",
        "ground-truth.md",
    )


def test_no_ground_truth_when_input_dir_unmatched(rp, tmp_path):
    source_root = tmp_path / "src"
    input_root = tmp_path / "input"
    dest_root = tmp_path / "out"
    task_dir = _mk_source_run_tree(source_root, _RUN_TASK_ID)
    _mk_input_dir(input_root, "someone_else_99", steer=_CANONICAL_STEER)

    bundle = rp.convert_task(task_dir, dest_root, input_root, False, False)

    assert bundle is not None
    assert not (bundle / "data" / "solution" / "TRUTH.md").exists()
    assert not (bundle / "prompt.txt").exists()


def test_ground_truth_emitted_even_without_target_sections(rp, tmp_path, capsys):
    """Verbatim publish is unconditional on content: a truth doc that contains
    NONE of the old target sections is still copied to TRUTH.md as-is (the old
    'no target sections' skip no longer applies), and no skip warning fires."""
    source_root = tmp_path / "src"
    input_root = tmp_path / "input"
    dest_root = tmp_path / "out"
    task_dir = _mk_source_run_tree(source_root, _RUN_TASK_ID)
    steer = "# golden\n## Fairness Ledger\nnothing relevant.\n"
    _mk_input_dir(input_root, _INPUT_TASK_ID, steer=steer)

    bundle = rp.convert_task(task_dir, dest_root, input_root, False, False)

    assert bundle is not None
    assert (bundle / "data" / "solution" / "TRUTH.md").read_text(encoding="utf-8") == steer
    assert (bundle / "prompt.txt").is_file()
    assert (bundle / "rubric.json").is_file()
    err = capsys.readouterr().err
    assert "no ground-truth source file found" not in err, (
        f"unexpected missing-source warning when source present; got: {err!r}"
    )


def test_ground_truth_skip_warning_is_unconditional(rp, tmp_path, capsys):
    """The missing-source skip warning must fire even when verbose=False so
    operators always see it during auto-bundle runs (script/run.sh and
    deliver.sh both call convert_task with verbose=False by default)."""
    source_root = tmp_path / "src"
    input_root = tmp_path / "input"
    dest_root = tmp_path / "out"
    task_dir = _mk_source_run_tree(source_root, _RUN_TASK_ID)
    _mk_input_dir(input_root, _INPUT_TASK_ID, steer=None)

    rp.convert_task(task_dir, dest_root, input_root, False, False)

    captured = capsys.readouterr()
    assert "no ground-truth source file found" in captured.err, (
        "unconditional skip warning must go to stderr, "
        f"not stdout; stdout={captured.out!r} stderr={captured.err!r}"
    )


def test_ground_truth_no_warning_when_source_present(rp, tmp_path, capsys):
    """Conversely, a present source must NOT print the missing-source warning."""
    source_root = tmp_path / "src"
    input_root = tmp_path / "input"
    dest_root = tmp_path / "out"
    task_dir = _mk_source_run_tree(source_root, _RUN_TASK_ID)
    _mk_input_dir(input_root, _INPUT_TASK_ID, steer=_CANONICAL_STEER)

    bundle = rp.convert_task(task_dir, dest_root, input_root, False, False)
    err = capsys.readouterr().err
    assert (bundle / "data" / "solution" / "TRUTH.md").is_file()
    assert "no ground-truth source file found" not in err, (
        f"unexpected warning on clean run: {err!r}"
    )


def test_no_golden_trajectory_dir(rp, tmp_path):
    """Delivered bundles emit no golden-trajectory/ dir at all."""
    source_root = tmp_path / "src"
    input_root = tmp_path / "input"
    dest_root = tmp_path / "out"
    task_dir = _mk_source_run_tree(source_root, _RUN_TASK_ID)
    _mk_input_dir(input_root, _INPUT_TASK_ID, steer=_CANONICAL_STEER)

    bundle = rp.convert_task(task_dir, dest_root, input_root, False, False)

    assert not (bundle / "golden-trajectory").exists()


def test_no_per_run_logs_dir(rp, tmp_path):
    """No run_N/logs/ is published. The old logs/verifier/ mirror shipped
    test_outputs.py / test_weights.json / test.sh to graded recipients, which
    defeated the data/tests/ strip."""
    source_root = tmp_path / "src"
    input_root = tmp_path / "input"
    dest_root = tmp_path / "out"
    task_dir = _mk_source_run_tree(source_root, _RUN_TASK_ID)
    _mk_input_dir(input_root, _INPUT_TASK_ID, steer=_CANONICAL_STEER)

    verifier = (
        task_dir / "trajectories" / "claude" / "run_1"
        / "task_output" / "logs" / "verifier"
    )
    verifier.mkdir(parents=True, exist_ok=True)
    (verifier / "test_outputs.py").write_text("def test_x(): pass\n", encoding="utf-8")
    (verifier / "test_weights.json").write_text('{"test_x": 5}', encoding="utf-8")
    (verifier / "ctrf.json").write_text("{}", encoding="utf-8")

    bundle = rp.convert_task(task_dir, dest_root, input_root, False, False)

    assert list(bundle.rglob("logs")) == []
    for leaked in ("test_outputs.py", "test_weights.json", "test.sh", "ctrf.json"):
        assert list(bundle.rglob(leaked)) == [], f"{leaked} leaked into the bundle"


def test_no_bundle_side_verifier_log_copier(rp):
    """The bundle-side logs/verifier copier is gone. Do NOT restore it."""
    assert not hasattr(rp, "copy_verifier_logs")


def test_filename_constants_wired(rp):
    assert rp.GOLDEN_STEER_FILENAME == "golden_steer_flow.md"
    assert rp.GROUND_TRUTH_FILENAME == "TRUTH.md"
    assert rp.PROMPT_FILENAME == "prompt.txt"


@pytest.mark.parametrize(
    "heading_line",
    [
        "## Section 1: Focal Event and Scope",
        "# Focal Event",
        "### Focal Event",
        "## section 2 - Focal Event",
        "## 1. Focal Event",
        "## **Focal Event**",
        "##    Focal Event   ",
        "Focal Event\n===========",
        "Focal Event\n-----------",
        "## Chapter 1: Focal Event",
        "## Chapter II - Focal Event",
        "## Step 1: Focal Event",
        "## Phase 1: Focal Event",
        "## Sec 1: Focal Event",
        "## Sec. 1: Focal Event",
        "## S. 1: Focal Event",
        "## Appendix A: Focal Event",
        "## Appendix: Focal Event",
        "## (1) Focal Event",
        "## [1] Focal Event",
        "## 2.1 Focal Event",
        "## 2.1.3 Focal Event",
        "## I. Focal Event",
        "## II) Focal Event",
        "## III - Focal Event",
        "## 1) Focal Event",
        "## 1: Focal Event",
        "## Focal.Event",
        "## Focal/Event",
        "## Focal\u2014Event",
        "## Focal\u2013Event",
        "## Focal\u00a0Event",
        "## Focal\tEvent",
        "## ***Focal Event***",
        "## **_Focal Event_**",
        "## __Focal Event__",
        "## `Focal Event`",
        "## ~~Focal Event~~",
        "## <b>Focal Event</b>",
        "## Focal Event {#focal-event}",
        "## Focal Event (scope)",
        "## Focal Event [draft]",
        "## Focal Event:",
        "## Focal Event.",
        "## Focal Event \u2014 Definition",
        "## Section 1 \u2014 Focal Event",
        "## Section 1 \u2013 Focal Event",
        "## Section\u00a01: Focal Event",
        "## **Section 1**: Focal Event",
        "## \U0001F7E2 Focal Event",
        "## \u00a7 1 Focal Event",
        "## #1 Focal Event",
        "## The Focal Event",
        "## Focal Events",
        "###### Focal Event",
        "## focal\u00a0event",
    ],
)
def test_focal_event_heading_variants(rp, heading_line):
    text = f"{heading_line}\n\nbody-focal\n\n## Canonical Solve Path\n\nbody-csp\n"
    out = rp.extract_ground_truth_sections(text)
    assert "body-focal" in out, f"focal body missed for heading: {heading_line!r}"
    assert "body-csp" in out


@pytest.mark.parametrize(
    "heading_line",
    [
        "## Section 2: Canonical Solve Path",
        "# canonical solve path",
        "## CANONICAL SOLVE PATH",
        "### **Canonical Solve Path**",
        "## 2) Canonical Solve Path",
        "## Part 2: Canonical Solve Path",
        "Canonical Solve Path\n====",
        "## Canonical-Solve-Path",
        "## Canonical_Solve_Path",
        "## CANONICAL_SOLVE_PATH",
        "## canonical/solve/path",
        "## canonical.solve.path",
        "## Canonical\u2014Solve\u2014Path",
        "## Canonical\u2013Solve\u2013Path",
        "## Canonical  Solve  Path",
        "## Canonical\u00a0Solve\u00a0Path",
        "## Chapter 2: Canonical Solve Path",
        "## Step 2: Canonical Solve Path",
        "## Phase 2: Canonical Solve Path",
        "## Pt 2: Canonical Solve Path",
        "## Pt. 2: Canonical Solve Path",
        "## Ch. 2: Canonical Solve Path",
        "## Chap 2: Canonical Solve Path",
        "## 2.2 Canonical Solve Path",
        "## II. Canonical Solve Path",
        "## [2] Canonical Solve Path",
        "## (2) Canonical Solve Path",
        "## ***Canonical Solve Path***",
        "## **_Canonical Solve Path_**",
        "## `Canonical Solve Path`",
        "## ~~Canonical Solve Path~~",
        "## <b>Canonical Solve Path</b>",
        "## Canonical Solve Path {#csp}",
        "## Canonical Solve Path (overview)",
        "## Canonical Solve Path \u2014 Steps",
        "## Section 2 \u2014 Canonical Solve Path",
        "## Section 2 \u2013 Canonical Solve Path",
        "## **Section 2**: Canonical Solve Path",
        "## \U0001F7E2 Canonical Solve Path",
        "###### Canonical Solve Path",
    ],
)
def test_canonical_solve_path_heading_variants(rp, heading_line):
    text = f"# Focal Event\nf\n{heading_line}\n\nbody-csp\n"
    out = rp.extract_ground_truth_sections(text)
    assert "body-csp" in out, f"csp body missed for heading: {heading_line!r}"


@pytest.mark.parametrize(
    "heading_line",
    [
        "## Section 3: Value Lock",
        "## Value Lock",
        "## VALUE_LOCK",
        "## VALUE LOCK",
        "## Value-Lock",
        "## value_lock",
        "## **VALUE_LOCK**",
        "### Value   Lock",
        "Value Lock\n----",
        "## value-lock",
        "## VALUE-LOCK",
        "## Value/Lock",
        "## Value.Lock",
        "## Value\u2014Lock",
        "## Value\u2013Lock",
        "## Value\u00a0Lock",
        "## Value\tLock",
        "## Value--Lock",
        "## value___lock",
        "## Chapter 3: Value Lock",
        "## Step 3: Value Lock",
        "## Phase 3: VALUE_LOCK",
        "## Sec 3: Value Lock",
        "## Sec. 3: Value Lock",
        "## Appendix C: Value Lock",
        "## Appendix: VALUE_LOCK",
        "## (3) Value Lock",
        "## [3] Value Lock",
        "## 3.1 Value Lock",
        "## III. Value Lock",
        "## ***VALUE_LOCK***",
        "## **_VALUE_LOCK_**",
        "## __VALUE_LOCK__",
        "## `VALUE_LOCK`",
        "## ~~VALUE_LOCK~~",
        "## <b>VALUE_LOCK</b>",
        "## VALUE_LOCK {#vl}",
        "## Value Lock (canonical)",
        "## Value Lock [final]",
        "## Value Lock:",
        "## VALUE_LOCK \U0001F512",
        "## \U0001F7E2 Value Lock",
        "## Section 3 \u2014 Value Lock",
        "## Section 3 \u2013 VALUE_LOCK",
        "## **Section 3**: Value Lock",
        "###### Value Lock",
    ],
)
def test_value_lock_heading_variants(rp, heading_line):
    text = f"# Focal Event\nf\n{heading_line}\n\nbody-vl\n"
    out = rp.extract_ground_truth_sections(text)
    assert "body-vl" in out, f"value-lock body missed for heading: {heading_line!r}"


@pytest.mark.parametrize(
    "heading_line,expected_alias",
    [
        ("## Unrelated Section", False),
        ("## Conclusion", False),
        ("## Notes", False),
        ("## Lock the Value", False),
        ("## Path Solve Canonical", False),
        ("## Focal", False),
        ("## Solve Path", False),
        ("## Value", False),
        ("## Lock", False),
        ("## Section 9: Random Topic", False),
        ("## Phase-2 Fingerprint", False),
        ("## Poison-Pill Record", False),
        ("## Signal Set Declaration and Noise-Purity", False),
        ("## Fairness Ledger", False),
        ("## Task.py Authoring Notes", False),
    ],
)
def test_non_target_headings_are_not_matched(rp, heading_line, expected_alias):
    """Headings that LOOK structured but do not contain any canonical alias
    must not trigger extraction. Pins the substring-match contract: aliases
    are anchored phrases, not loose token bags."""
    text = f"{heading_line}\n\nshould-not-leak\n"
    out = rp.extract_ground_truth_sections(text)
    assert out == "", f"non-target heading erroneously matched: {heading_line!r}"


# Author-supplied prefix shapes seen in real golden_steer files. Each row is one
# of the 4 canonical prefix templates the upstream authoring tools emit. They
# must all reduce to the same canonical alias for ANY of the 3 target sections.
# Heading word ("Focal Event" / "Canonical Solve Path" / "Value Lock") here is
# a placeholder for the prefix shape under test — that's the user contract.
@pytest.mark.parametrize(
    "target,body_marker",
    [
        ("Focal Event", "fe-body"),
        ("Canonical Solve Path", "csp-body"),
        ("Value Lock", "vl-body"),
    ],
)
@pytest.mark.parametrize(
    "prefix_template",
    [
        "4. {title}",
        "GS-4 {title}",
        "GS-4: {title}",
        "Section 4 - {title}",
    ],
)
def test_author_prefix_shapes_extract(rp, prefix_template, target, body_marker):
    heading = "## " + prefix_template.format(title=target)
    text = f"{heading}\n{body_marker}\n## Unrelated Tail\nshould-not-leak\n"
    out = rp.extract_ground_truth_sections(text)
    assert body_marker in out, f"body missed for heading: {heading!r}"
    assert "should-not-leak" not in out, f"unrelated tail leaked for: {heading!r}"


@pytest.mark.parametrize(
    "prefix_template",
    [
        "4. {title}",
        "GS-4 {title}",
        "GS-4: {title}",
        "Section 4 - {title}",
    ],
)
def test_author_prefix_shapes_normalize_to_canonical_alias(rp, prefix_template):
    for target, alias in [
        ("Focal Event", "focal event"),
        ("Canonical Solve Path", "canonical solve path"),
        ("Value Lock", "value lock"),
    ]:
        normalized = rp._normalize_heading_text(prefix_template.format(title=target))
        assert alias in normalized, (
            f"normalized {normalized!r} from template {prefix_template!r} does "
            f"not contain canonical alias {alias!r}"
        )


def test_normalize_helper_matches_canonical_aliases(rp):
    """Direct probe of the normalizer + alias matcher across pathological
    decoration combinations. If this regresses, every heading-variant
    parametrize above will too — this gives a faster signal."""
    cases = {
        "focal event": [
            "Focal Event",
            "**Focal Event**",
            "***Focal Event***",
            "**_Focal Event_**",
            "`Focal Event`",
            "FOCAL EVENT",
            "Focal\u2014Event",
            "Focal\u2013Event",
            "Focal/Event",
            "Focal.Event",
            "Focal\u00a0Event",
            "Section 1: Focal Event",
            "Chapter II - Focal Event",
            "Step 3 \u2014 Focal Event",
            "(1) Focal Event",
            "[1] Focal Event",
            "1. Focal Event",
            "2.1.3 Focal Event",
        ],
        "canonical solve path": [
            "Canonical Solve Path",
            "CANONICAL_SOLVE_PATH",
            "Canonical-Solve-Path",
            "canonical/solve/path",
            "Canonical\u2014Solve\u2014Path",
            "Part 2: Canonical Solve Path",
            "Pt. 2: Canonical Solve Path",
        ],
        "value lock": [
            "Value Lock",
            "VALUE_LOCK",
            "VALUE-LOCK",
            "Value/Lock",
            "Value.Lock",
            "Value\u00a0Lock",
            "value_lock",
            "**VALUE_LOCK**",
            "Section 3: Value Lock",
            "Appendix C: VALUE_LOCK",
        ],
    }
    for alias, headings in cases.items():
        for h in headings:
            norm = rp._normalize_heading_text(h)
            assert alias in norm, (
                f"alias {alias!r} not found in normalized {norm!r} from {h!r}"
            )


def test_extractor_returns_empty_on_no_match(rp):
    assert rp.extract_ground_truth_sections("# Unrelated\n\ntext\n") == ""
    assert rp.extract_ground_truth_sections("") == ""


def test_extractor_preserves_section_order(rp):
    text = (
        "## Section 3: Value Lock\nvl-body\n"
        "## Section 1: Focal Event\nfe-body\n"
        "## Section 2: Canonical Solve Path\ncsp-body\n"
    )
    out = rp.extract_ground_truth_sections(text)
    i_fe = out.find("fe-body")
    i_csp = out.find("csp-body")
    i_vl = out.find("vl-body")
    assert -1 < i_fe < i_csp < i_vl, f"order wrong: fe={i_fe} csp={i_csp} vl={i_vl}"


def test_extractor_ignores_headings_inside_code_fences(rp):
    text = (
        "## Unrelated Preamble\npreamble-body\n"
        "```\n## Focal Event\nfake-fe-in-fence\n```\n"
        "## Canonical Solve Path\nreal-csp-body\n"
        "## Value Lock\nreal-vl-body\n"
    )
    out = rp.extract_ground_truth_sections(text)
    assert "real-csp-body" in out
    assert "real-vl-body" in out
    assert "preamble-body" not in out, "non-target heading leaked"
    assert "fake-fe-in-fence" not in out, "fenced fake heading was treated as real"


def test_extractor_keeps_fenced_block_inside_target_section_body(rp):
    text = (
        "## Focal Event\nreal-fe-body\n"
        "```\ncode-with-hash-line\n## not a heading inside fence\n```\n"
        "## Canonical Solve Path\nreal-csp-body\n"
    )
    out = rp.extract_ground_truth_sections(text)
    assert "real-fe-body" in out
    assert "code-with-hash-line" in out, "fenced body inside target section dropped"
    assert "## not a heading inside fence" in out
    assert "real-csp-body" in out


def test_extractor_section_body_stops_at_same_level_heading(rp):
    text = (
        "## Focal Event\nfe-body\n"
        "## Unrelated Section\nleaked-body\n"
        "## Canonical Solve Path\ncsp-body\n"
    )
    out = rp.extract_ground_truth_sections(text)
    assert "fe-body" in out
    assert "csp-body" in out
    assert "leaked-body" not in out
    assert "Unrelated Section" not in out


def test_extractor_keeps_subsection_inside_target(rp):
    text = (
        "## Focal Event\nfe-body\n"
        "### Sub-heading inside focal\nsub-body\n"
        "## Canonical Solve Path\ncsp-body\n"
    )
    out = rp.extract_ground_truth_sections(text)
    assert "fe-body" in out
    assert "Sub-heading inside focal" in out
    assert "sub-body" in out
    assert "csp-body" in out


def test_extractor_real_world_canonical_steer(rp):
    out = rp.extract_ground_truth_sections(_CANONICAL_STEER)
    _assert_three_sections_present(out)
    _assert_leakage_sections_absent(out)
    assert "VALUE_LOCK:" in out
    assert "Discover service." in out
    assert "the championship" in out


def test_extractor_handles_real_darren_weston_file_if_present():
    real_path = (
        _REPO_ROOT
        / "input"
        / "darren_weston_2b2baa81-fa14-4fb6-8cd3-f15749563e77"
        / "golden_steer_flow.md"
    )
    if not real_path.is_file():
        pytest.skip("real golden_steer_flow.md fixture not present in checkout")
    rp = _load_repackager()
    text = real_path.read_text(encoding="utf-8")
    out = rp.extract_ground_truth_sections(text)
    assert "Focal event:" in out
    assert "Canonical Solve Path" in out or "canonical solve path" in out.lower()
    assert "VALUE_LOCK:" in out
    assert "Fairness Ledger" not in out
    assert "Phase-2 Fingerprint" not in out
    assert "Poison-Pill Record" not in out
