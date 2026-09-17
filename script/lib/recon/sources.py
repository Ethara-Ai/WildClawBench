"""Carry the files a bundle publishes verbatim back into an input tree.

Nothing here rewrites content; the only decisions are where each file lives on
each side and which files belong at all.

The one that mattered: input attachments are staged at
``data/environment/artifacts/inputs/files/**`` and belong at ``data/**`` under
the same relative path. Every one of the 126 bundles surveyed puts a single
``home/`` directory at the top of ``files/``, and across all 71 delivery-1
input/bundle pairs the two relative-path sets are identical, so the copy is
recursive and path-preserving. Flattening it recovered nothing at all (the only
top-level entry is a directory), and stripping the ``home/`` segment would be
worse than nothing: ``task_parser`` builds each attachment's ``storedAs`` as
``home/<path relative to data/>``, so the staged paths the agent is told about
(``home/home/Desktop/...``) only line up when ``home/`` is kept.

Test files are deliberately not recovered. ``data/tests/test_outputs.py`` and
``test_weights.json`` are still published, but the generated-test channel they
feed is retired; writing them back would reinstate a scoring channel the task
no longer runs.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from src.utils.task_standard import TRUTH_FILENAMES

ARTIFACTS_SUBPATH = ("data", "environment", "artifacts", "inputs", "files")
PERSONA_SUBPATH = ("data", "environment", "persona")

#: Where a bundle may keep the grader truth doc, most authoritative first. The
#: pilot shipped it under data/solution/; everything since puts it at the root.
TRUTH_LOCATIONS = ((), ("data", "solution"))

#: Persona is published as a fixed seven-file set; fewer means a lossy bundle.
PERSONA_FILE_COUNT = 7

_JUNK = {".DS_Store", "Thumbs.db"}


@dataclass
class Carried:
    """What one verbatim-carry step moved, and from where."""

    label: str
    names: list = field(default_factory=list)
    source: str = ""

    def __len__(self) -> int:
        return len(self.names)


def copy_tree(src: Path, dst: Path) -> list:
    """Copy every file under ``src`` to ``dst``, relative paths preserved."""
    names: list = []
    if not src.is_dir():
        return names
    for f in sorted(src.rglob("*")):
        if not f.is_file() or f.name in _JUNK:
            continue
        rel = f.relative_to(src)
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, target)
        names.append(rel.as_posix())
    return names


def copy_file(src: Path, dst: Path) -> bool:
    if not src.is_file():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return True


def recover_data(bundle: Path, out_dir: Path) -> Carried:
    """Input attachments, kept at the relative paths the agent is told about."""
    src = bundle.joinpath(*ARTIFACTS_SUBPATH)
    return Carried("data/", copy_tree(src, out_dir / "data"),
                   "/".join(ARTIFACTS_SUBPATH))


def recover_persona(bundle: Path, out_dir: Path) -> Carried:
    """The persona set, which the bundle publishes flat and complete."""
    src = bundle.joinpath(*PERSONA_SUBPATH)
    return Carried("persona/", copy_tree(src, out_dir / "persona"),
                   "/".join(PERSONA_SUBPATH))


def find_truth(bundle: Path):
    """The grader truth doc under either published name, in either location."""
    for parts in TRUTH_LOCATIONS:
        for name in TRUTH_FILENAMES:
            candidate = bundle.joinpath(*parts, name)
            if candidate.is_file():
                return candidate
    return None


def recover_truth(bundle: Path, out_dir: Path) -> Carried:
    src = find_truth(bundle)
    if src is None:
        return Carried("TRUTH.md")
    copy_file(src, out_dir / src.name)
    return Carried("TRUTH.md", [src.name],
                   src.relative_to(bundle).as_posix())


def recover_rubric(bundle: Path, out_dir: Path) -> Carried:
    src = bundle / "rubric.json"
    if not copy_file(src, out_dir / "rubric.json"):
        return Carried("rubric.json")
    return Carried("rubric.json", ["rubric.json"], "rubric.json")


def recover_inject(bundle: Path, out_dir: Path) -> Carried:
    """The inject spec, staged verbatim by the repackager and kept that way."""
    return Carried("inject/", copy_tree(bundle / "inject", out_dir / "inject"),
                   "inject")


#: How each shipped layout identifies itself, most specific first. The order is
#: load-bearing: three of the five carry a prompt.txt and two carry a
#: prompts.json, so each must be recognised by the file that sets it apart
#: before the file they share is reached.
VARIANTS = (
    ("pilot_rework", ("data/solution/TRUTH.md",)),
    ("prompts_json_mirror", ("prompts.json", "golden_trajectory.json")),
    ("prompts_json", ("prompts.json",)),
    ("golden_trajectory", ("PROMPT.md", "golden-trajectory")),
    ("prompt_txt", ("prompt.txt", "TRUTH.md")),
)


def detect_variant(bundle: Path) -> str:
    """Name the layout a bundle was published in, or 'unknown'.

    Nothing branches on the answer — every recovery step probes for what it
    needs — but it is recorded, because knowing which of the five shapes a
    bundle is makes an unexpected gap explicable rather than mysterious.
    """
    for name, markers in VARIANTS:
        if all(bundle.joinpath(*m.split("/")).exists() for m in markers):
            return name
    return "unknown"


def recover_all(bundle: Path, out_dir: Path) -> list:
    return [
        recover_rubric(bundle, out_dir),
        recover_truth(bundle, out_dir),
        recover_persona(bundle, out_dir),
        recover_data(bundle, out_dir),
        recover_inject(bundle, out_dir),
    ]
