"""Contract test: read_csv_with_ctx and read_json_with_ctx produce equivalent rows.

Loads the same logical dataset as both CSV and JSON, feeds both through
the same coercer chain, and asserts identical output.  This locks the
reader-equivalence contract so that introducing CSV seed files alongside
existing JSON ones cannot silently change mock-API behavior.
"""

from __future__ import annotations

import csv
import json
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "environment"))

from _mutable_store import (
    CoerceError,
    read_csv_with_ctx,
    read_json_with_ctx,
    read_seed_with_ctx,
    strict_int,
    strict_float,
    strict_bool,
    strict_str,
    opt_int,
    opt_float,
    opt_bool,
    opt_str,
    opt_csv_list,
    strict_csv_list,
)


_API = "test-api"
_TABLE = "widgets"

SEED_ROWS = [
    {
        "id": "1",
        "name": "Alpha",
        "price": "9.99",
        "active": "true",
        "tags": "a,b",
        "count": "10",
    },
    {
        "id": "2",
        "name": "Bravo",
        "price": "0",
        "active": "false",
        "tags": "",
        "count": "0",
    },
    {
        "id": "3",
        "name": "",
        "price": "42.5",
        "active": "true",
        "tags": "x",
        "count": "7",
    },
]

COLUMNS = list(SEED_ROWS[0].keys())


@pytest.fixture()
def seed_dir(tmp_path):
    json_path = tmp_path / "widgets.json"
    csv_path = tmp_path / "widgets.csv"

    json_rows = []
    for r in SEED_ROWS:
        row = {}
        for k, v in r.items():
            row[k] = None if v == "" else v
        json_rows.append(row)
    json_path.write_text(json.dumps(json_rows, indent=2), encoding="utf-8")

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(SEED_ROWS)

    return tmp_path, json_path, csv_path


def _strip_file_key(rows):
    out = []
    for r in rows:
        cleaned = dict(r)
        cleaned.pop("__file__", None)
        out.append(cleaned)
    return out


def test_row_count_matches(seed_dir):
    _, json_path, csv_path = seed_dir
    json_rows = read_json_with_ctx(json_path, _API, _TABLE)
    csv_rows = read_csv_with_ctx(csv_path, _API, _TABLE)
    assert len(json_rows) == len(csv_rows) == len(SEED_ROWS)


def test_context_keys_present(seed_dir):
    _, json_path, csv_path = seed_dir
    for reader, path in [
        (read_json_with_ctx, json_path),
        (read_csv_with_ctx, csv_path),
    ]:
        rows = reader(path, _API, _TABLE)
        for idx, row in enumerate(rows):
            assert row["__api__"] == _API
            assert row["__table__"] == _TABLE
            assert row["__row_index__"] == idx
            assert "__file__" in row


def test_strict_int_equivalent(seed_dir):
    _, json_path, csv_path = seed_dir
    json_rows = read_json_with_ctx(json_path, _API, _TABLE)
    csv_rows = read_csv_with_ctx(csv_path, _API, _TABLE)
    for j, c in zip(json_rows, csv_rows):
        assert strict_int(j, "count") == strict_int(c, "count")
        assert strict_int(j, "id") == strict_int(c, "id")


def test_strict_float_equivalent(seed_dir):
    _, json_path, csv_path = seed_dir
    json_rows = read_json_with_ctx(json_path, _API, _TABLE)
    csv_rows = read_csv_with_ctx(csv_path, _API, _TABLE)
    for j, c in zip(json_rows, csv_rows):
        assert strict_float(j, "price") == strict_float(c, "price")


def test_strict_bool_equivalent(seed_dir):
    _, json_path, csv_path = seed_dir
    json_rows = read_json_with_ctx(json_path, _API, _TABLE)
    csv_rows = read_csv_with_ctx(csv_path, _API, _TABLE)
    for j, c in zip(json_rows, csv_rows):
        assert strict_bool(j, "active") == strict_bool(c, "active")


def test_strict_str_equivalent(seed_dir):
    _, json_path, csv_path = seed_dir
    json_rows = read_json_with_ctx(json_path, _API, _TABLE)
    csv_rows = read_csv_with_ctx(csv_path, _API, _TABLE)
    for j, c in zip(json_rows, csv_rows):
        assert strict_str(j, "name") == strict_str(c, "name")


def test_opt_int_equivalent(seed_dir):
    _, json_path, csv_path = seed_dir
    json_rows = read_json_with_ctx(json_path, _API, _TABLE)
    csv_rows = read_csv_with_ctx(csv_path, _API, _TABLE)
    for j, c in zip(json_rows, csv_rows):
        assert opt_int(j, "count") == opt_int(c, "count")
        assert opt_int(j, "nonexistent", default=-1) == opt_int(
            c, "nonexistent", default=-1
        )


def test_opt_float_equivalent(seed_dir):
    _, json_path, csv_path = seed_dir
    json_rows = read_json_with_ctx(json_path, _API, _TABLE)
    csv_rows = read_csv_with_ctx(csv_path, _API, _TABLE)
    for j, c in zip(json_rows, csv_rows):
        assert opt_float(j, "price") == opt_float(c, "price")


def test_opt_bool_equivalent(seed_dir):
    _, json_path, csv_path = seed_dir
    json_rows = read_json_with_ctx(json_path, _API, _TABLE)
    csv_rows = read_csv_with_ctx(csv_path, _API, _TABLE)
    for j, c in zip(json_rows, csv_rows):
        assert opt_bool(j, "active") == opt_bool(c, "active")


def test_opt_csv_list_equivalent(seed_dir):
    _, json_path, csv_path = seed_dir
    json_rows = read_json_with_ctx(json_path, _API, _TABLE)
    csv_rows = read_csv_with_ctx(csv_path, _API, _TABLE)
    for j, c in zip(json_rows, csv_rows):
        assert opt_csv_list(j, "tags") == opt_csv_list(c, "tags")


def test_opt_str_default_empty(seed_dir):
    _, json_path, csv_path = seed_dir
    json_rows = read_json_with_ctx(json_path, _API, _TABLE)
    csv_rows = read_csv_with_ctx(csv_path, _API, _TABLE)
    for j, c in zip(json_rows, csv_rows):
        assert opt_str(j, "name") == opt_str(c, "name")


def test_opt_str_custom_default_equivalent(seed_dir):
    _, json_path, csv_path = seed_dir
    json_rows = read_json_with_ctx(json_path, _API, _TABLE)
    csv_rows = read_csv_with_ctx(csv_path, _API, _TABLE)

    row_with_empty_name_json = json_rows[2]
    row_with_empty_name_csv = csv_rows[2]

    json_val = opt_str(row_with_empty_name_json, "name", default="FALLBACK")
    csv_val = opt_str(row_with_empty_name_csv, "name", default="FALLBACK")

    assert json_val == csv_val


def test_seed_dispatcher_json(seed_dir):
    tmp_path, json_path, _ = seed_dir
    rows_direct = read_json_with_ctx(json_path, _API, _TABLE)
    rows_dispatch = read_seed_with_ctx(json_path, _API, _TABLE)
    assert _strip_file_key(rows_direct) == _strip_file_key(rows_dispatch)


def test_seed_dispatcher_csv(seed_dir):
    tmp_path, _, csv_path = seed_dir
    rows_direct = read_csv_with_ctx(csv_path, _API, _TABLE)
    rows_dispatch = read_seed_with_ctx(csv_path, _API, _TABLE)
    assert _strip_file_key(rows_direct) == _strip_file_key(rows_dispatch)


def test_seed_dispatcher_no_extension_probes_csv_first(seed_dir):
    tmp_path, json_path, csv_path = seed_dir
    no_ext = tmp_path / "widgets"
    rows_csv = read_csv_with_ctx(csv_path, _API, _TABLE)
    rows_probe = read_seed_with_ctx(no_ext, _API, _TABLE)
    assert _strip_file_key(rows_csv) == _strip_file_key(rows_probe)


def test_seed_dispatcher_json_with_csv_sibling_prefers_csv(seed_dir):
    """CSV-overlay-wins: explicit .json path with sibling .csv returns CSV rows."""
    tmp_path, json_path, csv_path = seed_dir
    rows_csv = read_csv_with_ctx(csv_path, _API, _TABLE)
    rows_dispatch = read_seed_with_ctx(json_path, _API, _TABLE)
    assert _strip_file_key(rows_csv) == _strip_file_key(rows_dispatch)


def test_seed_dispatcher_no_extension_falls_back_to_csv(tmp_path):
    csv_path = tmp_path / "items.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "val"])
        writer.writeheader()
        writer.writerow({"id": "1", "val": "x"})

    no_ext = tmp_path / "items"
    rows = read_seed_with_ctx(no_ext, _API, _TABLE)
    assert len(rows) == 1
    assert rows[0]["id"] == "1"


def test_seed_dispatcher_missing_file_raises(tmp_path):
    # CoerceError is imported at module top so its identity is stable across
    # sys.modules eviction performed by tests/mocks/_helpers.py and
    # tests/test_drift_plane_smoke.py during the same pytest session.
    with pytest.raises(CoerceError, match="seed file not found"):
        read_seed_with_ctx(tmp_path / "nope", _API, _TABLE)


def test_real_api_csv_overlay_shadows_baked_json(tmp_path, monkeypatch):
    import importlib
    import shutil
    env_dir = Path(__file__).resolve().parents[1] / "environment"
    sandbox = tmp_path / "figma-api"
    shutil.copytree(env_dir / "figma-api", sandbox)
    overlay_row = {
        "comment_id": "E2E_OVERLAY_001", "file_key": "FK_E2E", "user_id": "u-e2e",
        "user_handle": "E2E", "message": "from CSV", "node_id": "0:0",
        "resolved": "false", "created_at": "2026-06-17T00:00:00Z",
    }
    with open(sandbox / "comments.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(overlay_row.keys()))
        w.writeheader()
        w.writerow(overlay_row)

    # Self-sufficient overlay isolation - symmetric setup/teardown:
    #  - Pop _STORES["figma-api"] so Store.register doesn't short-circuit on a
    #    stale registration from tests/mocks/ (would otherwise return baseline).
    #  - Pop sys.modules["figma_data"] so importlib re-binds against the sandbox.
    #  - DO NOT evict _mutable_store; CoerceError class identity must stay
    #    stable for test_seed_dispatcher_missing_file_raises.
    #  - finally-block pops the sandbox-bound entries we just created so this
    #    test does not leak a tmp_path-bound figma_data into the session.
    import _mutable_store as _ms
    _ms._STORES.pop("figma-api", None)
    sys.modules.pop("figma_data", None)
    monkeypatch.syspath_prepend(str(env_dir))
    monkeypatch.syspath_prepend(str(sandbox))
    try:
        figma_data = importlib.import_module("figma_data")
        rows = figma_data._store.table("comments").rows()
        assert len(rows) == 1
        assert rows[0]["comment_id"] == "E2E_OVERLAY_001"
    finally:
        _ms._STORES.pop("figma-api", None)
        sys.modules.pop("figma_data", None)
