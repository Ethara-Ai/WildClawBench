from __future__ import annotations

import json
from pathlib import Path

import pytest

from ._helpers import (
    ENV_DIR,
    csv_bad_rows,
    discover_api_dirs,
    iter_data_files,
)


def _collect_data_files(suffix: str) -> list[tuple[Path, Path]]:
    out: list[tuple[Path, Path]] = []
    for api_dir in discover_api_dirs():
        for f in iter_data_files(api_dir, (suffix,)):
            out.append((api_dir, f))
    return out


CSV_FILES = _collect_data_files(".csv")
JSON_FILES = _collect_data_files(".json")


def test_seed_discovery_is_not_silently_empty():
    """Both suites below are parametrized off discovery, so a broken
    `discover_api_dirs` / `iter_data_files` empties them and pytest reports the
    result as a skip rather than a failure -- the JSON guard over every seed
    file in the fleet would vanish and the run would still read green.

    Only JSON is asserted non-empty. The converged fleet ships no `.csv` seed
    at all (figma and instagram, the last two, left in the newreq
    convergence), so `CSV_FILES` is legitimately empty; it refills on its own
    the moment a service ships one, because discovery is by glob rather than
    by name.
    """
    assert JSON_FILES, "no JSON seed files discovered across the fleet"


@pytest.mark.parametrize(
    "api_dir,csv_file",
    CSV_FILES,
    ids=[f"{a.name}/{f.name}" for a, f in CSV_FILES],
)
def test_csv_columns_match_header(api_dir: Path, csv_file: Path):
    bad = csv_bad_rows(csv_file)
    assert not bad, (
        f"{csv_file.relative_to(ENV_DIR)} has {len(bad)} row(s) with "
        "column count != header:\n"
        + "\n".join(f"  row {n}: expected {e} cols, got {a}" for n, e, a in bad[:10])
    )


@pytest.mark.parametrize(
    "api_dir,json_file",
    JSON_FILES,
    ids=[f"{a.name}/{f.name}" for a, f in JSON_FILES],
)
def test_json_files_parse(api_dir: Path, json_file: Path):
    try:
        json.loads(json_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        pytest.fail(f"{json_file.relative_to(ENV_DIR)} invalid: {e}")


def test_tracking_middleware_present():
    f = ENV_DIR / "tracking_middleware.py"
    assert f.is_file(), f"missing shared {f}"
    src = f.read_text(encoding="utf-8")
    for symbol in ("install_tracker", "/audit/requests", "/audit/summary", "/health"):
        assert symbol in src, f"tracking_middleware.py missing {symbol!r}"
