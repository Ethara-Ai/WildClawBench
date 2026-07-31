"""prompts.json turn-schedule format: parser validation, loader priority,
bundle rendering, and the inject/ passthrough copiers.

Static tests only (no docker, no network). Covers:
  1. parse_prompts_json: valid multi-turn parse, metadata preservation,
     label-gap / turn_count-mismatch / empty-message rejection, missing
     turn-label tolerance, single-turn schedules.
  2. task_parser.load_task: prompts.json wins over prompts.txt/prompt.txt;
     turn_schedule metadata lands in the task dict.
  3. repackage_to_bundle: _read_prompt_text renders JSON to TURN-delimited
     text (never raw JSON); copy_inject stages inject/ verbatim and no-ops
     when the task ships none.
"""
from __future__ import annotations

import importlib.util
import json
import logging
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.utils.inject_director import parse_prompts_json  # noqa: E402
from src.utils.task_parser import load_task  # noqa: E402


def _load_repackage_module():
    spec = importlib.util.spec_from_file_location(
        "_rp_prompts_json_test", REPO_ROOT / "script" / "repackage_to_bundle.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _schedule(turns, **extra):
    doc = {
        "task_id": "amanda_martinez_98b3953b",
        "persona": "amanda-martinez",
        "window": {"start": "2026-10-14", "end": "2026-10-18",
                   "timezone": "America/New_York", "day_count": 5},
        "turn_count": len(turns),
        "source": "static authored multi-turn user schedule",
        "turns": turns,
    }
    doc.update(extra)
    return doc


def _turn(i, msg, day=1, time="08:20", label=None):
    return {"turn": f"T{i}" if label is None else label,
            "day": day, "time": time, "message": msg}


def _write(tmp_path, doc):
    p = tmp_path / "prompts.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    return p


# --------------------------------------------------------------------------- #
# 1. parse_prompts_json
# --------------------------------------------------------------------------- #
def test_parse_valid_six_turns(tmp_path):
    turns = [_turn(i, f"message for turn {i}", day=1 + i // 2) for i in range(6)]
    messages, meta = parse_prompts_json(_write(tmp_path, _schedule(turns)))
    assert messages == [f"message for turn {i}" for i in range(6)]
    assert meta["task_id"] == "amanda_martinez_98b3953b"
    assert meta["persona"] == "amanda-martinez"
    assert meta["window"]["timezone"] == "America/New_York"
    assert len(meta["turns"]) == 6
    # per-turn metadata preserved, message excluded
    assert meta["turns"][2] == {"turn": "T2", "day": 2, "time": "08:20"}
    assert all("message" not in t for t in meta["turns"])


def test_parse_label_gap_raises(tmp_path):
    turns = [_turn(0, "a"), _turn(1, "b"), _turn(3, "c")]  # T2 skipped
    with pytest.raises(ValueError, match="contiguous"):
        parse_prompts_json(_write(tmp_path, _schedule(turns)))


def test_parse_out_of_order_labels_raise(tmp_path):
    turns = [_turn(1, "a"), _turn(0, "b")]
    with pytest.raises(ValueError, match="contiguous"):
        parse_prompts_json(_write(tmp_path, _schedule(turns)))


def test_parse_turn_count_mismatch_raises(tmp_path):
    turns = [_turn(0, "a"), _turn(1, "b")]
    doc = _schedule(turns, turn_count=5)
    with pytest.raises(ValueError, match="turn_count"):
        parse_prompts_json(_write(tmp_path, doc))


def test_parse_empty_message_raises(tmp_path):
    turns = [_turn(0, "a"), _turn(1, "   ")]
    with pytest.raises(ValueError, match="message"):
        parse_prompts_json(_write(tmp_path, _schedule(turns)))


def test_parse_unparseable_label_raises(tmp_path):
    turns = [_turn(0, "a", label="Day 2")]
    with pytest.raises(ValueError, match="unparseable"):
        parse_prompts_json(_write(tmp_path, _schedule(turns)))


def test_parse_missing_label_tolerated(tmp_path):
    turns = [{"day": 1, "time": "09:00", "message": "no label here"},
             {"day": 1, "time": "10:00", "message": "second"}]
    messages, _ = parse_prompts_json(_write(tmp_path, _schedule(turns)))
    assert messages == ["no label here", "second"]


def test_parse_single_turn(tmp_path):
    messages, meta = parse_prompts_json(
        _write(tmp_path, _schedule([_turn(0, "only turn")])))
    assert messages == ["only turn"]
    assert meta["turn_count"] == 1


def test_parse_empty_turns_raises(tmp_path):
    with pytest.raises(ValueError, match="turns"):
        parse_prompts_json(_write(tmp_path, _schedule([])))


def test_parse_invalid_json_raises(tmp_path):
    p = tmp_path / "prompts.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid JSON"):
        parse_prompts_json(p)


# --------------------------------------------------------------------------- #
# 2. load_task priority
# --------------------------------------------------------------------------- #
def _make_task_dir(tmp_path):
    task = tmp_path / "amanda_martinez_98b3953b"
    task.mkdir()
    (task / "rubric.json").write_text(json.dumps(
        [{"criterion": "did the thing", "weight": 5}]), encoding="utf-8")
    return task


def test_load_task_prompts_json_wins(tmp_path):
    task_dir = _make_task_dir(tmp_path)
    turns = [_turn(0, "json turn zero"), _turn(1, "json turn one")]
    _write(task_dir, _schedule(turns))
    (task_dir / "prompts.txt").write_text(
        "--- TURN T0 ---\ntxt turn zero\n", encoding="utf-8")
    (task_dir / "prompt.txt").write_text("txt mirror", encoding="utf-8")

    task = load_task(task_dir)
    assert task["turn_messages"] == ["json turn zero", "json turn one"]
    assert task["prompt"].strip().endswith("json turn zero") or \
        "json turn zero" in task["prompt"]
    assert task["turn_schedule"]["persona"] == "amanda-martinez"
    assert task["turn_schedule"]["turns"][1]["time"] == "08:20"


def test_load_task_txt_unaffected(tmp_path):
    task_dir = _make_task_dir(tmp_path)
    (task_dir / "prompts.txt").write_text(
        "--- TURN T0 ---\ntxt turn zero\n--- TURN T1 ---\ntxt turn one\n",
        encoding="utf-8")
    task = load_task(task_dir)
    assert task["turn_messages"] == ["txt turn zero", "txt turn one"]
    assert task["turn_schedule"] is None


def test_load_task_json_without_companion_txt_fails(tmp_path):
    # Finalized pair convention: prompts.json REQUIRES a companion prompts.txt
    # (json drives the trajectory, txt ships in the output bundle).
    task_dir = _make_task_dir(tmp_path)
    _write(task_dir, _schedule([_turn(0, "solo")]))
    with pytest.raises(FileNotFoundError, match="companion prompts.txt"):
        load_task(task_dir)


# --------------------------------------------------------------------------- #
# 3. bundler: JSON rendering + inject passthrough
# --------------------------------------------------------------------------- #
def test_read_prompt_text_renders_json_not_raw(tmp_path):
    rp = _load_repackage_module()
    task_dir = tmp_path / "t"
    task_dir.mkdir()
    turns = [_turn(0, "hello world", day=1, time="08:20"),
             _turn(1, "follow up", day=2, time="09:45")]
    _write(task_dir, _schedule(turns))
    text = rp._read_prompt_text(task_dir)
    assert text is not None
    assert "--- TURN T0 (Day 1, 08:20) ---" in text
    assert "--- TURN T1 (Day 2, 09:45) ---" in text
    assert "hello world" in text and "follow up" in text
    assert not text.lstrip().startswith("{")  # never raw JSON


def test_read_prompt_text_txt_verbatim(tmp_path):
    rp = _load_repackage_module()
    task_dir = tmp_path / "t"
    task_dir.mkdir()
    (task_dir / "prompt.txt").write_text("plain prompt\n", encoding="utf-8")
    assert rp._read_prompt_text(task_dir) == "plain prompt\n"


def test_copy_inject_stages_verbatim(tmp_path):
    rp = _load_repackage_module()
    task_dir = tmp_path / "input_task"
    stage = task_dir / "inject" / "stage1"
    stage.mkdir(parents=True)
    (stage / "mutations.json").write_text('{"silent": []}', encoding="utf-8")
    (stage / "verify.sh").write_text("#!/bin/sh\ntrue\n", encoding="utf-8")
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    n = rp.copy_inject(task_dir, bundle, verbose=False)
    assert n == 2
    assert (bundle / "inject" / "stage1" / "mutations.json").read_text() == '{"silent": []}'
    assert (bundle / "inject" / "stage1" / "verify.sh").is_file()


def test_copy_inject_noop_without_inject(tmp_path):
    rp = _load_repackage_module()
    task_dir = tmp_path / "input_task"
    task_dir.mkdir()
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    assert rp.copy_inject(task_dir, bundle, verbose=False) == 0
    assert not (bundle / "inject").exists()


def test_copy_inject_none_input_dir(tmp_path):
    rp = _load_repackage_module()
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    assert rp.copy_inject(None, bundle, verbose=False) == 0


# --------------------------------------------------------------------------- #
# 4. FINALIZED schema (2026-07-31): top-level timezone + per-turn timestamp
# --------------------------------------------------------------------------- #
_FINAL_DOC = {
    "task_id": "amanda_martinez_679bc6cb",
    "persona": "amanda-martinez",
    "timezone": "America/New_York",
    "turn_count": 3,
    "source": "static authored multi-turn user schedule",
    "turns": [
        {"turn": "T0", "timestamp": "2026-10-20T09:30:00-04:00",
         "message": "quick worry check before i lose sleep on this."},
        {"turn": "T1", "timestamp": "2026-10-21T22:15:00-04:00",
         "message": "yo one small thing before i turn in."},
        {"turn": "T2", "timestamp": "2026-10-22T11:00:00-04:00",
         "message": "cool. one more check while we're here."},
    ],
}


def test_parse_finalized_schema(tmp_path):
    p = tmp_path / "prompts.json"
    p.write_text(json.dumps(_FINAL_DOC), encoding="utf-8")
    messages, meta = parse_prompts_json(p)
    assert len(messages) == 3
    assert messages[0].startswith("quick worry check")
    assert meta["timezone"] == "America/New_York"
    assert meta["turns"][1] == {"turn": "T1",
                                "timestamp": "2026-10-21T22:15:00-04:00"}


def test_parse_out_of_order_timestamps_raise(tmp_path):
    doc = json.loads(json.dumps(_FINAL_DOC))
    doc["turns"][2]["timestamp"] = "2026-10-19T08:00:00-04:00"  # before T0
    p = tmp_path / "prompts.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ValueError, match="out of order"):
        parse_prompts_json(p)


def test_parse_garbage_timestamp_raises(tmp_path):
    doc = json.loads(json.dumps(_FINAL_DOC))
    doc["turns"][1]["timestamp"] = "Day 2, 22:15"
    p = tmp_path / "prompts.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ValueError, match="unparseable timestamp"):
        parse_prompts_json(p)


def test_parse_mixed_naive_aware_timestamps_raise(tmp_path):
    doc = json.loads(json.dumps(_FINAL_DOC))
    doc["turns"][1]["timestamp"] = "2026-10-21T22:15:00"  # naive vs aware T0
    p = tmp_path / "prompts.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ValueError, match="offset-aware and naive"):
        parse_prompts_json(p)


def test_render_finalized_schema_derives_day_time_headers(tmp_path):
    rp = _load_repackage_module()
    task_dir = tmp_path / "t"
    task_dir.mkdir()
    (task_dir / "prompts.json").write_text(json.dumps(_FINAL_DOC), encoding="utf-8")
    text = rp._read_prompt_text(task_dir)
    # Byte-match with the companion prompts.txt convention the task team ships.
    assert "--- TURN T0 (Day 1, 09:30) ---" in text
    assert "--- TURN T1 (Day 2, 22:15) ---" in text
    assert "--- TURN T2 (Day 3, 11:00) ---" in text
    assert "quick worry check" in text


def test_render_unparseable_timestamp_degrades_to_no_label(tmp_path):
    rp = _load_repackage_module()
    task_dir = tmp_path / "t"
    task_dir.mkdir()
    doc = {"turns": [{"turn": "T0", "timestamp": "not-a-date", "message": "hi"}]}
    (task_dir / "prompts.json").write_text(json.dumps(doc), encoding="utf-8")
    text = rp._read_prompt_text(task_dir)
    assert "--- TURN T0 ---" in text and "hi" in text


def test_load_task_warns_on_stale_companion(tmp_path, caplog):
    task_dir = _make_task_dir(tmp_path)
    _write(task_dir, _schedule([_turn(0, "json zero"), _turn(1, "json one")]))
    # companion with a DIFFERENT turn count (stale)
    (task_dir / "prompts.txt").write_text(
        "--- TURN T0 ---\nonly one turn here\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        task = load_task(task_dir)
    assert task["turn_messages"] == ["json zero", "json one"]  # JSON wins
    assert any("companion prompts.txt" in r.getMessage() and "stale" in r.getMessage()
               for r in caplog.records)


# --------------------------------------------------------------------------- #
# 5. Pair convention: json = trajectory, txt = bundle
# --------------------------------------------------------------------------- #
def test_bundle_publishes_authored_txt_verbatim_when_pair_present(tmp_path):
    rp = _load_repackage_module()
    task_dir = tmp_path / "t"
    task_dir.mkdir()
    (task_dir / "prompts.json").write_text(json.dumps(_FINAL_DOC), encoding="utf-8")
    authored = ("# banner comment the task team wrote\n"
                "--- TURN T0 (Day 1, 09:30) ---\nauthored zero\n"
                "--- TURN T1 (Day 2, 22:15) ---\nauthored one\n"
                "--- TURN T2 (Day 3, 11:00) ---\nauthored two\n")
    (task_dir / "prompts.txt").write_text(authored, encoding="utf-8")
    text = rp._read_prompt_text(task_dir)
    # VERBATIM authored txt (banner included), NOT the json render.
    assert text == authored
    assert "quick worry check" not in text


def test_companion_text_drift_warns_but_loads(tmp_path, caplog):
    task_dir = _make_task_dir(tmp_path)
    _write(task_dir, _schedule([_turn(0, "json zero"), _turn(1, "json one")]))
    (task_dir / "prompts.txt").write_text(
        "--- TURN T0 ---\njson zero\n--- TURN T1 ---\nEDITED text\n",
        encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        task = load_task(task_dir)
    assert task["turn_messages"] == ["json zero", "json one"]  # JSON still wins
    assert any("differs from prompts.json" in r.getMessage() for r in caplog.records)
