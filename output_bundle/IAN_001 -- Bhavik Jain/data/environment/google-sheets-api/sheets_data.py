"""Data module for the Google Sheets API (Mock) mock service.

Schema-agnostic: rows are served as generic dicts with no column-specific
coercion, so a task's overlaid CSV (any columns) loads cleanly under the admin
plane's baseline capture. A synthetic primary key is generated when the source
row lacks one.
"""

import csv
import uuid
from pathlib import Path

DATA_DIR = Path(__file__).parent

import sys as _sys
_sys.path.insert(0, str(DATA_DIR.parent))
from _mutable_store import get_store  # noqa: E402

_store = get_store("google-sheets-api")


def _load(filename):
    p = DATA_DIR / filename
    if not p.is_file():
        return []
    with open(p, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _ensure_pk(rows, pk, synth=None):
    out = []
    for r in rows:
        r = dict(r)
        if not r.get(pk):
            r[pk] = synth(r) if synth else uuid.uuid4().hex[:12]
        out.append(r)
    return out


_store.register("spreadsheets", primary_key="id",
                initial_loader=lambda: _ensure_pk(_load("spreadsheets.csv"), "id"))
def _load_sheet_data():
    return _ensure_pk(_load("sheet_data.csv"), "id", synth=lambda r: r.get("sheet_id", "") + "!" + r.get("cell", ""))

_store.register("sheet_data", primary_key="id", initial_loader=_load_sheet_data)

_PK = {"spreadsheets": "id", "sheet_data": "id"}

_TABLES = ["spreadsheets", "sheet_data"]


def list_table(name):
    return _store.table(name).rows()


def get_row(name, pk_value):
    for r in _store.table(name).rows():
        if str(r.get(_PK[name])) == str(pk_value):
            return r
    return None


def create_row(name, body):
    rows = _store.table(name).rows()
    r = dict(body)
    if not r.get(_PK[name]):
        r[_PK[name]] = uuid.uuid4().hex[:12]
    rows.append(r)
    return r


def update_row(name, pk_value, patch):
    rows = _store.table(name).rows()
    for i, r in enumerate(rows):
        if str(r.get(_PK[name])) == str(pk_value):
            rows[i].update({k: v for k, v in patch.items() if v is not None})
            return rows[i]
    return None
