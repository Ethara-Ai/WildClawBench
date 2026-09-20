"""Before/after mock-service state diff, rendered as compact judge evidence.

Every run with a per-task mock stack already dumps the live mock store twice
(eval/run_batch.py via InjectApplier.snapshot_state):

    <run>/snapshot/workspace_before/mock_data/<api>/<table>.csv   (pre-run)
    <run>/snapshot/workspace_after/mock_data/<api>/<table>.csv    (post-run)

That pair is the authoritative record of what changed in the world, and it does
not depend on the transcript. Long runs have their transcript middle-cut to fit
the judge's evidence budget, and a state-change criterion ("the agent closed
#502", "the agent posted the handover to the channel") then abstains because
the tool call that did it was in the cut. This module turns the snapshot pair
into a short, budget-capped block the judge always receives.

Changes are attributed with the run's inject_timeline.jsonl: a row the harness
itself upserted or patched mid-run (an `inject.api` record) is labelled
`harness`, everything else `agent`. Stdlib only; never raises.
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

STATE_CHANGES_HEADER = "----- MOCK STATE CHANGES (before -> after) -----"
_DEFAULT_MAX_CHARS = 16_000
_MAX_ROWS_PER_TABLE = 15
_CELL_CHARS = 90
_ROW_CELL_CHARS = 60
# Cell width for rows the AGENT wrote, widest first. What the agent posted is
# the graded content ("the comment marks the figure verified"), and at 60 chars
# the judge saw a clipped body and abstained as truncation-affected (2026-09-20
# ariadne_kostas_8c8579bb criterion 16: 2,839-char comment, 60 shown). The
# whole block is ~3-5K on real runs, so the widest step that still fits
# max_chars is used; None is the legacy width every [harness] row keeps.
_AGENT_CELL_STEPS: Tuple[Optional[int], ...] = (4000, 1200, 400, 150, None)

# csv module default field limit (128 KB) is smaller than some mock cells.
csv.field_size_limit(16 * 1024 * 1024)


def _short(value: object, limit: int = _CELL_CHARS) -> str:
    s = " ".join(str(value if value is not None else "").split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _changed_pair(old: object, new: object, limit: int = _CELL_CHARS) -> Tuple[str, str]:
    """Both values, windowed on the first differing character so a long text
    edit shows WHAT changed rather than two identical prefixes."""
    o = " ".join(str(old if old is not None else "").split())
    n = " ".join(str(new if new is not None else "").split())
    if len(o) <= limit and len(n) <= limit:
        return o, n
    i = 0
    while i < min(len(o), len(n)) and o[i] == n[i]:
        i += 1
    start = max(0, i - 20)
    lead = "…" if start else ""
    return (_short(lead + o[start:], limit), _short(lead + n[start:], limit))


_TIMESTAMP_COL_RE = re.compile(
    r"(updated|modified|synced|edited)[_ ]?(at|time|on)$|^(updatedat|lastmodified|modifiedat)$",
    re.IGNORECASE)
_IDENTITY_COLS = ("number", "key", "name", "title", "subject", "slug", "email", "display_name")


def _read_table(path: Path) -> Tuple[List[str], List[Dict[str, str]]]:
    """(header, rows) exactly as the snapshot writer emitted them."""
    try:
        with open(path, encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            header = list(reader.fieldnames or [])
            rows = [dict(r) for r in reader]
    except (OSError, csv.Error, UnicodeDecodeError):
        return [], []
    return header, rows


def _key_columns(header: List[str], *row_sets: List[Dict[str, str]]) -> Optional[List[str]]:
    """Smallest leading key that is unique on every side: `id` alone when it is,
    else the first 1..3 columns. None when no such key exists (a join table with
    no unique prefix), in which case rows are diffed by content."""
    candidates: List[List[str]] = []
    if "id" in header:
        candidates.append(["id"])
    candidates += [header[:n] for n in (1, 2, 3) if len(header) >= n]
    for cols in candidates:
        if all(len({tuple(r.get(c) for c in cols) for r in rows}) == len(rows)
               for rows in row_sets):
            return cols
    return None


def _key_of(row: Dict[str, str], cols: List[str]) -> str:
    return "/".join(str(row.get(c) or "") for c in cols)


def _injected_rows(timeline: Optional[Path]) -> Dict[Tuple[str, str, str], Dict[str, object]]:
    """(service, table, pk) -> the fields the harness wrote, merged in timeline
    order (an upsert carries the whole row, a patch only the patched fields)."""
    out: Dict[Tuple[str, str, str], Dict[str, object]] = {}
    if not timeline or not Path(timeline).is_file():
        return out
    try:
        with open(timeline, encoding="utf-8") as fh:
            for line in fh:
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if ev.get("type") != "inject.api" or ev.get("ok") is False:
                    continue
                svc, tbl, pk = ev.get("service"), ev.get("table"), ev.get("pk")
                if not (svc and tbl and pk is not None):
                    continue
                fields = ev.get("after") if isinstance(ev.get("after"), dict) else {}
                out.setdefault((str(svc), str(tbl), str(pk)), {}).update(fields)
    except OSError:
        pass
    return out


def _injected_keys(timeline: Optional[Path]) -> Set[Tuple[str, str, str]]:
    """(service, table, pk) for every row the harness wrote mid-run."""
    return set(_injected_rows(timeline))


def _cell(v: object) -> str:
    if v is None:
        return ""
    if isinstance(v, (dict, list)):
        return json.dumps(v, sort_keys=True)
    return str(v)


def _summarise_row(row: Dict[str, str], header: Iterable[str],
                   limit: int = _ROW_CELL_CHARS) -> str:
    parts = []
    for col in header:
        val = row.get(col)
        if val in (None, ""):
            continue
        parts.append(f"{col}={_short(val, limit)}")
        if len(parts) >= 5:
            break
    return ", ".join(parts)


def _identity(row: Dict[str, str], header: List[str], skip: Iterable[str]) -> str:
    skip = set(skip)
    bits = [f"{c}={_short(row.get(c), 50)}" for c in _IDENTITY_COLS
            if c in header and c not in skip and row.get(c)]
    return f" ({', '.join(bits[:2])})" if bits else ""


def _who(api: str, table: str, key: str, before: Optional[Dict[str, str]],
         after: Optional[Dict[str, str]], header: List[str],
         injected: Dict[Tuple[str, str, str], Dict[str, object]]) -> str:
    """`harness` when the change is exactly what the injections wrote, `agent`
    when the harness never touched the row, `harness+agent` when the harness
    wrote the row and the final state still differs from what it wrote (the
    agent edited it afterwards). Timestamp columns are ignored for that test:
    a store stamps them on every write, whoever made it."""
    fields = injected.get((api, table, key))
    if fields is None:
        return "agent"
    if after is None or not fields:
        # Removed by the harness, or a timeline record that does not say what it
        # wrote: there is nothing to compare against, so credit the harness.
        return "harness"
    expected = dict(before or {})
    expected.update({k: _cell(v) for k, v in fields.items()})
    for col in header:
        if _TIMESTAMP_COL_RE.search(col):
            continue
        if col not in expected and before is None:
            continue  # upsert that did not name the column: store default
        if (after.get(col) or "") != (expected.get(col) or ""):
            return "harness+agent"
    return "harness"


def _diff_table(api: str, table: str, before: Path, after: Path,
                injected: Dict[Tuple[str, str, str], Dict[str, object]],
                agent_cell: Optional[int] = None) -> List[str]:
    def row_w(who: str) -> int:
        return _ROW_CELL_CHARS if agent_cell is None or who == "harness" else agent_cell

    def pair_w(who: str) -> int:
        return _CELL_CHARS if agent_cell is None or who == "harness" else agent_cell

    b_header, b_list = _read_table(before) if before.is_file() else ([], [])
    a_header, a_list = _read_table(after) if after.is_file() else ([], [])
    header = a_header or b_header
    lines: List[str] = []
    if b_list and not a_list:
        # Every row gone. The snapshot writer emits an empty file both for a
        # genuinely emptied table AND for a table it failed to read (mock down,
        # admin call failed), so this cannot be pinned on the agent.
        return [f"  ? {table}: all {len(b_list)} row(s) are absent from the end-of-run "
                f"snapshot — either the table was emptied or the snapshot could not "
                f"read it; NOT evidence of an agent action"]
    cols = _key_columns(header, b_list, a_list) if header else None

    if cols is None:
        # No unique key (join table): diff the rows as a multiset of contents.
        def sig(r: Dict[str, str]) -> Tuple[str, ...]:
            return tuple(r.get(c) or "" for c in header)
        b_count: Dict[Tuple[str, ...], int] = {}
        for r in b_list:
            b_count[sig(r)] = b_count.get(sig(r), 0) + 1
        a_rest = []
        for r in a_list:
            k = sig(r)
            if b_count.get(k):
                b_count[k] -= 1
            else:
                a_rest.append(r)
        first = header[0] if header else ""
        for r in a_rest:
            who = "harness" if (api, table, str(r.get(first))) in injected else "agent"
            lines.append(f"  + [{who}] {table} row: {_summarise_row(r, header, row_w(who))}")
        for r in b_list:
            k = sig(r)
            if b_count.get(k):
                b_count[k] -= 1
                who = "harness" if (api, table, str(r.get(first))) in injected else "agent"
                lines.append(f"  - [{who}] {table} row: {_summarise_row(r, header, row_w(who))}")
    else:
        b_rows = {_key_of(r, cols): r for r in b_list}
        a_rows = {_key_of(r, cols): r for r in a_list}
        for key, arow in a_rows.items():
            brow = b_rows.get(key)
            who = _who(api, table, key, brow, arow, header, injected)
            if brow is None:
                lines.append(f"  + [{who}] {table} {key}: "
                             f"{_summarise_row(arow, header, row_w(who))}")
                continue
            changed = [c for c in header if (brow.get(c) or "") != (arow.get(c) or "")]
            if changed:
                detail = "; ".join(
                    "{}: {!r} -> {!r}".format(
                        c, *_changed_pair(brow.get(c), arow.get(c), pair_w(who)))
                    for c in changed[:6])
                more = f" (+{len(changed) - 6} more fields)" if len(changed) > 6 else ""
                lines.append(f"  ~ [{who}] {table} {key}{_identity(arow, header, changed)}: "
                             f"{detail}{more}")
        for key, brow in b_rows.items():
            if key not in a_rows:
                who = _who(api, table, key, brow, None, header, injected)
                lines.append(f"  - [{who}] {table} {key}: "
                             f"{_summarise_row(brow, b_header, row_w(who))}")
    if len(lines) > _MAX_ROWS_PER_TABLE:
        extra = len(lines) - _MAX_ROWS_PER_TABLE
        lines = lines[:_MAX_ROWS_PER_TABLE] + [f"  … +{extra} more change(s) in {table}"]
    return lines


def _diff_document(api: str, name: str, before: Path, after: Path) -> List[str]:
    try:
        b = before.read_text(encoding="utf-8") if before.is_file() else None
        a = after.read_text(encoding="utf-8") if after.is_file() else None
    except OSError:
        return []
    if b == a:
        return []
    if b is None:
        return [f"  + document {name} created"]
    if a is None:
        return [f"  - document {name} removed"]
    return [f"  ~ document {name} changed"]


def build_state_changes(snapshot_dir: Path, inject_timeline: Optional[Path] = None,
                        max_chars: int = _DEFAULT_MAX_CHARS) -> str:
    """Render the before/after diff of the mock services, or "" when the run
    has no mock snapshot pair (no per-task mock stack). Never raises; the
    result is always <= max_chars."""
    try:
        before_root = Path(snapshot_dir) / "workspace_before" / "mock_data"
        after_root = Path(snapshot_dir) / "workspace_after" / "mock_data"
        if not (before_root.is_dir() and after_root.is_dir()):
            return ""
        injected = _injected_rows(inject_timeline)
        # Only services captured on BOTH sides are compared: a service missing
        # from one snapshot was not observed, which is not the same as changed.
        apis = sorted({p.name for p in before_root.iterdir() if p.is_dir()}
                      & {p.name for p in after_root.iterdir() if p.is_dir()})
        def _body(agent_cell: Optional[int]) -> List[str]:
            body: List[str] = []
            for api in apis:
                body.extend(_api_block(api, agent_cell))
            return body

        def _api_block(api: str, agent_cell: Optional[int]) -> List[str]:
            b_dir, a_dir = before_root / api, after_root / api
            names = sorted({p.name for p in b_dir.iterdir() if p.is_file()}
                           & {p.name for p in a_dir.iterdir() if p.is_file()})
            api_lines: List[str] = []
            for name in names:
                if name.endswith(".csv"):
                    api_lines += _diff_table(api, name[:-4], b_dir / name, a_dir / name,
                                             injected, agent_cell)
                elif name.endswith(".json"):
                    api_lines += _diff_document(api, name[:-5], b_dir / name, a_dir / name)
            if not api_lines:
                return []
            n_agent = sum(1 for ln in api_lines if "[agent]" in ln or "[harness+agent]" in ln)
            return [f"{api}: {len(api_lines)} change(s), {n_agent} by the agent"] + api_lines

        covered = sorted({p.name for p in before_root.iterdir() if p.is_dir()}
                         & {p.name for p in after_root.iterdir() if p.is_dir()})
        intro = (
            "Authoritative diff of the mock services' stored data between the "
            "start and the end of the run (+ added, ~ changed, - removed, "
            "? not comparable). "
            "[harness] rows were written by the environment's scripted "
            "injections, NOT by the agent; [agent] rows are the agent's own "
            "writes; [harness+agent] rows were injected and then changed again "
            "by the agent. A covered service that does not appear below was not "
            "changed; a service outside the covered list is not observed here.\n"
            f"Covered services ({len(covered)}): {', '.join(covered)}"
        )
        text = ""
        for agent_cell in _AGENT_CELL_STEPS:
            body = _body(agent_cell)
            if not body:
                text = (f"\n{STATE_CHANGES_HEADER}\n{intro}\n"
                        "(no mock service data changed during the run)\n-----\n")
                break
            text = f"\n{STATE_CHANGES_HEADER}\n{intro}\n" + "\n".join(body) + "\n-----\n"
            if len(text) <= max_chars:
                break
        if len(text) > max_chars:
            cut = text[: max(0, max_chars - 80)]
            cut = cut[: cut.rfind("\n") + 1] if "\n" in cut else cut
            text = cut + "… [state diff truncated to fit the evidence budget]\n-----\n"
        return text[:max_chars]
    except Exception:
        return ""


def build_state_changes_for_run(run_dir: Path, max_chars: int = _DEFAULT_MAX_CHARS) -> str:
    """Convenience wrapper for a run directory laid out by eval/run_batch.py."""
    run_dir = Path(run_dir)
    return build_state_changes(run_dir / "snapshot", run_dir / "inject_timeline.jsonl",
                               max_chars=max_chars)
