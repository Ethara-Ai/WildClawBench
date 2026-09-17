"""Isolate the task's mock_data overlay from the baked defaults around it.

A bundle flattens "baked default + task overlay" into one ``data/environment/``
tree, copying the overlay last so it wins at the same path. The overlay bytes
are therefore present but unlabelled, and the only way to tell them apart is to
diff against the pristine ``environment/`` the bundle was built from.

Two rules keep that diff honest:

**Scope.** Only the APIs the task declares — required plus distractors — are
searched. The old loop walked every ``*-api`` directory in the bundle, which is
the whole shipped fleet, so a three-API task reported an overlay spanning fifty
APIs and 259 files.

**Proof.** An API with no baseline to diff against cannot be classified at all;
every seed under it merely *looks* new. That used to be a warning, and the
warning is what let the fifty-API result through. It is an error now, and
``--unverified-overlays`` is the way to say the guess is wanted anyway.

Baselines drift: the fleet was cut from 101 APIs to 50, so today's
``environment/`` covers only 24 of the 50 APIs willie's bundle ships.
:func:`baseline_from_ref` reads a pristine tree straight out of any git ref so
a bundle can be diffed against the fleet it was actually built on.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tarfile
from dataclasses import dataclass, field
from pathlib import Path

SEED_EXTS = {".json", ".csv"}
API_SUFFIX = "-api"

VERIFIED = "verified"
UNVERIFIED = "unverified"
NEW = "new-not-in-default"
DIFFERS = "differs-from-default"


@dataclass
class Overlay:
    """One seed file the task shipped over a baked default."""

    api: str
    rel: str
    reason: str
    confidence: str

    def __str__(self) -> str:
        return f"{self.rel} ({self.reason}, {self.confidence})"


@dataclass
class OverlayResult:
    overlays: dict = field(default_factory=dict)
    unverified_apis: list = field(default_factory=list)
    out_of_scope: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    #: False when the task declared no APIs, so the search fell back to every
    #: staged directory and containment cannot be shown.
    scope_proven: bool = True

    @property
    def file_count(self) -> int:
        return sum(len(v) for v in self.overlays.values())


def baseline_from_ref(ref: str, repo_root: Path, dest: Path) -> Path:
    """Extract ``<ref>:environment`` into ``dest`` without touching the repo.

    git archive streams the tree straight out of object storage, so no branch,
    index or working copy is disturbed.
    """
    dest.mkdir(parents=True, exist_ok=True)
    archive = dest / "environment.tar"
    with archive.open("wb") as fh:
        result = subprocess.run(
            ["git", "archive", "--format=tar", f"{ref}:environment"],
            cwd=str(repo_root), stdout=fh, stderr=subprocess.PIPE)
    if result.returncode != 0:
        archive.unlink(missing_ok=True)
        raise ValueError(
            f"could not read environment/ at {ref!r}: "
            f"{result.stderr.decode('utf-8', 'replace').strip()}")
    tree = dest / "environment"
    tree.mkdir(exist_ok=True)
    with tarfile.open(archive) as tf:
        tf.extractall(tree)
    archive.unlink()
    return tree


def bundle_apis(env_dir: Path) -> list:
    if not env_dir.is_dir():
        return []
    return sorted(d.name for d in env_dir.iterdir()
                  if d.is_dir() and d.name.endswith(API_SUFFIX))


def _read(p: Path):
    try:
        return p.read_bytes()
    except OSError:
        return None


def extract(env_dir: Path, baseline_env: Path, out_mock: Path, scoped_apis,
            allow_unverified: bool = False) -> OverlayResult:
    """Copy out the seeds that differ from the baked default, within scope."""
    result = OverlayResult()
    if not env_dir.is_dir():
        result.warnings.append(
            f"no data/environment under the bundle ({env_dir}); no overlay to isolate")
        return result

    present = bundle_apis(env_dir)
    scope = set(scoped_apis or ())
    if not scope:
        result.scope_proven = False
        scope = set(present)
        result.errors.append(
            f"the task declares no required or distractor APIs, so the search "
            f"fell back to all {len(present)} staged *-api directories: that is "
            f"the shipped fleet, not a proven task surface, and any overlay "
            f"found under it is unattributed")
    result.out_of_scope = [a for a in present if a not in scope]
    for api in sorted(scope & set(present)):
        api_dir = env_dir / api
        base_api = baseline_env / api
        verified = base_api.is_dir()
        if not verified:
            result.unverified_apis.append(api)
        for f in sorted(api_dir.rglob("*")):
            if not f.is_file() or f.suffix.lower() not in SEED_EXTS:
                continue
            rel = f.relative_to(api_dir)
            base_f = base_api / rel
            if verified and base_f.is_file():
                if _read(f) == _read(base_f):
                    continue
                reason = DIFFERS
            else:
                reason = NEW
            confidence = VERIFIED if verified else UNVERIFIED
            dest = out_mock / api / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dest)
            result.overlays.setdefault(api, []).append(
                Overlay(api, rel.as_posix(), reason, confidence))

    missing = sorted(scope - set(present))
    if missing:
        result.warnings.append(
            f"{len(missing)} declared API(s) are not staged in the bundle: "
            f"{', '.join(missing[:5])}{'...' if len(missing) > 5 else ''}")
    if result.unverified_apis:
        detail = (f"{len(result.unverified_apis)} declared API(s) are absent "
                  f"from the baseline environment, so their seeds cannot be "
                  f"told from the baked defaults: "
                  f"{', '.join(result.unverified_apis[:5])}"
                  f"{'...' if len(result.unverified_apis) > 5 else ''}")
        if allow_unverified:
            result.warnings.append(
                detail + " — kept as UNVERIFIED overlay on request")
        else:
            result.errors.append(
                detail + " — pass --baseline-ref <ref> to diff against the "
                "fleet the bundle was built on, or --unverified-overlays to "
                "keep them anyway")
    return result


def prune_unverified(result: OverlayResult, out_mock: Path) -> None:
    """Drop the seeds that could not be verified, leaving only proven overlay."""
    for api in list(result.unverified_apis):
        shutil.rmtree(out_mock / api, ignore_errors=True)
        result.overlays.pop(api, None)


# --------------------------------------------------------------------------- #
# mock-module drift
# --------------------------------------------------------------------------- #
MANIFEST_NAME = "RECONSTRUCTION_MANIFEST.json"


def module_drift(env_dir: Path, baseline_env: Path, task_id: str = "") -> dict:
    """Digest the behaviour modules the bundle ships against the baseline.

    Seeds are per-task overlays and legitimately differ, so only the ``*.py``
    under each ``<svc>-api/`` plus the shared infra modules are compared — the
    same set ``harbor.mock_manifest`` records at generation time, reusing its
    collector so the two cannot drift apart. Anything listed under ``stale`` is
    a module whose behaviour no longer matches the harness, which is the defect
    that shipped a write-discarding notion mock through four sibling bundles.
    """
    from src.utils.harbor.mock_manifest import build_manifest

    return build_manifest(env_dir, baseline_env, task_id=task_id)


def write_manifest(out_dir: Path, drift: dict, result: OverlayResult,
                   provenance: dict) -> Path:
    """Record the reconstruction in the shape validate_bundle already reads."""
    payload = dict(drift)
    payload["reconstruction"] = {
        **provenance,
        "overlay_apis": sorted(result.overlays),
        "overlay_file_count": result.file_count,
        "overlay_scope_proven": result.scope_proven,
        "unverified_apis": sorted(result.unverified_apis),
        "out_of_scope_apis": sorted(result.out_of_scope),
        "overlays": {api: [str(o) for o in files]
                     for api, files in sorted(result.overlays.items())},
    }
    path = out_dir / MANIFEST_NAME
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    return path
