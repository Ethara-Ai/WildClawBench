"""Rebuild task.yaml and task.json from what the bundle's task.toml declares.

Every shipped task.yaml carries the same seven keys, so the question is only
which of them the bundle still supports. Measured across the 126 bundles:

``l1``/``l2``
    ``[multimodal].dependency_tags`` — its first two entries, which reproduce
    task.yaml's l1/l2 exactly on all 71 delivery-1 pairs. (Not
    ``[dimensions]``: that section holds the complexity flags and carries no
    tags in any bundle.)
``required_apis``
    ``[metadata].required_skills``, minus the ``-connector`` suffix, but only
    when ``distractor_skills`` is non-empty. When it is empty the required list
    is the entire shipped fleet — 101 entries on every batch-1, batch-rework
    and delivery-1 bundle — which says what the image contains, not what the
    task needs. Believing it is how a three-API task acquires fifty.
``task_type``/``difficulty``
    ``[metadata].category``/``difficulty``, which only five of the 126 bundles
    actually populate; the rest are left to the loader's own derivation.
``modalities``
    Scanned off the recovered attachments, and narrowed to the vocabulary
    preflight can verify (image/audio/video must each be backed by a real MIME
    hit), so a reconstruction cannot declare a modality preflight will reject.

task.json is written alongside because it is the file the loader reads at run
time for API overrides. Both get the same values, so their precedence — yaml
overlays json — never has to be reasoned about.
"""
from __future__ import annotations

import json
import mimetypes
from dataclasses import dataclass, field
from pathlib import Path

CONNECTOR_SUFFIX = "-connector"
API_SUFFIX = "-api"

#: Non-text modalities preflight will verify, and the MIME prefix each needs.
MODALITY_MIME_PREFIX = {"image": "image/", "audio": "audio/", "video": "video/"}

#: Written when the bundle cannot say which APIs were distractors.
AUTO = "auto"

TASK_YAML_KEYS = ("difficulty", "modalities", "l1", "l2", "task_type",
                  "required_apis", "distractor_apis")


@dataclass
class Metadata:
    """The task's declared shape, and what had to be left to the loader."""

    required_apis: list = field(default_factory=list)
    distractor_apis: object = AUTO
    l1: str = ""
    l2: str = ""
    task_type: str = ""
    difficulty: str = ""
    modalities: list = field(default_factory=list)
    system_prompt: str = ""
    notes: list = field(default_factory=list)

    @property
    def scoped_apis(self) -> set:
        """required u distractor — the only APIs an overlay may touch."""
        declared = set(self.required_apis)
        if isinstance(self.distractor_apis, list):
            declared |= set(self.distractor_apis)
        return declared


def load_toml(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        try:
            import tomllib
        except ModuleNotFoundError:
            import tomli as tomllib
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, ModuleNotFoundError):
        return {}


def _strip_connector(names) -> list:
    out = set()
    for raw in names or ():
        name = str(raw).strip()
        if name.endswith(CONNECTOR_SUFFIX):
            name = name[: -len(CONNECTOR_SUFFIX)]
        if name:
            out.add(name if name.endswith(API_SUFFIX) else name + API_SUFFIX)
    return sorted(out)


def bare(names) -> list:
    """task.yaml writes API names without the -api suffix the loader adds."""
    return [n[: -len(API_SUFFIX)] if n.endswith(API_SUFFIX) else n for n in names]


def scan_modalities(data_dir: Path) -> list:
    """Modalities the recovered attachments actually back, text always first."""
    if not data_dir.is_dir():
        return []
    mimes = set()
    for f in data_dir.rglob("*"):
        if f.is_file():
            guessed = mimetypes.guess_type(f.name)[0]
            if guessed:
                mimes.add(guessed)
    found = [name for name, prefix in sorted(MODALITY_MIME_PREFIX.items())
             if any(m.startswith(prefix) for m in mimes)]
    return ["text"] + found if mimes else []


def derive(bundle: Path, out_dir: Path, system_prompt: str = "") -> Metadata:
    """Read every field task.toml still supports; leave the rest to the loader."""
    toml = load_toml(bundle / "data" / "task.toml")
    md = toml.get("metadata") or {}
    tags = (toml.get("multimodal") or {}).get("dependency_tags") \
        or (toml.get("dimensions") or {}).get("dependency_tags") or []
    meta = Metadata(
        l1=str(tags[0]) if len(tags) > 0 else "",
        l2=str(tags[1]) if len(tags) > 1 else "",
        task_type=str(md.get("category") or ""),
        difficulty=str(md.get("difficulty") or ""),
        modalities=scan_modalities(out_dir / "data"),
        system_prompt=system_prompt,
    )
    required = _strip_connector(md.get("required_skills"))
    distractor = _strip_connector(md.get("distractor_skills"))
    if distractor:
        meta.required_apis, meta.distractor_apis = required, distractor
    else:
        meta.notes.append(
            f"required_apis: task.toml names {len(required)} required skill(s) "
            f"and no distractors, which is the whole shipped fleet rather than "
            f"this task's APIs — left unset and distractors left '{AUTO}'")
    if not meta.l1:
        meta.notes.append("l1/l2: task.toml carries no dependency_tags; the "
                          "loader derives them from the rubric instead")
    if not meta.task_type:
        meta.notes.append("task_type: task.toml's [metadata].category is empty")
    if not meta.difficulty:
        meta.notes.append("difficulty: task.toml's [metadata].difficulty is empty")
    return meta


def _yaml_list(values) -> str:
    return "[" + ", ".join(values) + "]"


def render_task_yaml(meta: Metadata) -> str:
    lines = []
    if meta.difficulty:
        lines.append(f"difficulty: {meta.difficulty}")
    if meta.modalities:
        lines.append(f"modalities: {_yaml_list(meta.modalities)}")
    if meta.l1:
        lines.append(f"l1: {meta.l1}")
    if meta.l2:
        lines.append(f"l2: {meta.l2}")
    if meta.task_type:
        lines.append(f"task_type: {meta.task_type}")
    if meta.required_apis:
        lines.append(f"required_apis: {_yaml_list(bare(meta.required_apis))}")
    lines.append(
        f"distractor_apis: {_yaml_list(bare(meta.distractor_apis))}"
        if isinstance(meta.distractor_apis, list)
        else f"distractor_apis: {AUTO}")
    if meta.system_prompt:
        lines.append(f"system_prompt: {json.dumps(meta.system_prompt)}")
    return "\n".join(lines) + "\n"


def render_task_json(meta: Metadata) -> str:
    payload = {"required_apis": meta.required_apis}
    payload["distractor_apis"] = (meta.distractor_apis
                                  if isinstance(meta.distractor_apis, list)
                                  else AUTO)
    return json.dumps(payload, indent=2) + "\n"


def write(meta: Metadata, out_dir: Path) -> list:
    (out_dir / "task.yaml").write_text(render_task_yaml(meta), encoding="utf-8")
    (out_dir / "task.json").write_text(render_task_json(meta), encoding="utf-8")
    return ["task.yaml", "task.json"]
