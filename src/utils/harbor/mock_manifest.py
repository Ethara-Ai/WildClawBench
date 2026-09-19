"""Generation-time integrity manifest for the mock modules a bundle ships.

megan's bundle was cut from a stale `notion-api/notion_data.py` — the
write-discard bug was already fixed upstream and four sibling bundles shipped
the corrected module, but nothing compared what landed in the bundle against
what the harness actually had. The staleness was found by hand, months later,
by md5-ing the same file across seven deliveries.

This records the digest of every `environment/<svc>-api/*.py` module staged
into a bundle, next to the digest of the harness source it was copied from, and
fails the build when they disagree. `script/validate_bundle.py --mock-source`
re-checks an already-written bundle the same way, so a stale module cannot
silently ship even if it is packaged by a path that skips the writer.

Deliberately NOT written inside `data/environment/`: the manifest describes
that tree, so living in it would change the very bytes being checksummed and
make the bundle differ from the harness source it is supposed to mirror. It
sits at the bundle root next to `rubric.json` as delivery metadata.

stdlib-only; imported by the bundle writer and by a script that runs without
the docker stack.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

__all__ = [
    "MANIFEST_NAME",
    "MANIFEST_VERSION",
    "DIGEST_ALGO",
    "StaleMockModule",
    "module_digest",
    "collect_modules",
    "build_manifest",
    "write_manifest",
    "verify_manifest",
]

MANIFEST_NAME = "mock_modules.json"
MANIFEST_VERSION = 1
DIGEST_ALGO = "sha256"

# Mock service dirs are `environment/<name>-api/`; only their python modules
# carry behavior. Seed .json/.csv are per-task overlays and legitimately differ
# from the harness baseline, so checksumming them would be all false positives.
_SERVICE_SUFFIX = "-api"
_MODULE_GLOB = "*.py"

# Shared infra that ships alongside the services and is just as capable of going
# stale (the admin plane and the store are what the data modules are written
# against). `environment/` top level, not inside a service dir.
_SHARED_MODULES = ("_mutable_store.py", "admin_plane.py", "tracking_middleware.py")


class StaleMockModule(RuntimeError):
    """A module staged into a bundle does not match the harness source."""


def module_digest(path: Path) -> str:
    h = hashlib.new(DIGEST_ALGO)
    h.update(Path(path).read_bytes())
    return h.hexdigest()


def collect_modules(env_dir: Path) -> Dict[str, Path]:
    """Map `<svc>-api/<module>.py` (and the shared infra modules) to its path.

    Keys are POSIX-relative to `env_dir` so a manifest built from the harness
    source and one built from a bundle use the same names.
    """
    env_dir = Path(env_dir)
    found: Dict[str, Path] = {}
    if not env_dir.is_dir():
        return found
    for name in _SHARED_MODULES:
        shared = env_dir / name
        if shared.is_file():
            found[name] = shared
    for service in sorted(env_dir.iterdir()):
        if not service.is_dir() or not service.name.endswith(_SERVICE_SUFFIX):
            continue
        for module in sorted(service.glob(_MODULE_GLOB)):
            if module.is_file():
                found[f"{service.name}/{module.name}"] = module
    return found


def build_manifest(bundle_env_dir: Path, source_env_dir: Path,
                   *, task_id: str = "") -> Dict[str, Any]:
    """Digest every module staged in `bundle_env_dir` against `source_env_dir`.

    `source_digest` is None when the harness has no such module (a bundle-only
    overlay module); `stale` names the modules that exist in both and differ —
    exactly the megan `notion_data.py` signature.
    """
    shipped = collect_modules(bundle_env_dir)
    source = collect_modules(source_env_dir)
    modules: Dict[str, Dict[str, Any]] = {}
    stale: List[str] = []
    for rel in sorted(shipped):
        shipped_digest = module_digest(shipped[rel])
        src = source.get(rel)
        source_digest = module_digest(src) if src is not None else None
        modules[rel] = {"digest": shipped_digest, "source_digest": source_digest}
        if source_digest is not None and source_digest != shipped_digest:
            stale.append(rel)
    return {
        "version": MANIFEST_VERSION,
        "algorithm": DIGEST_ALGO,
        "task_id": task_id,
        "source_environment": str(source_env_dir),
        "module_count": len(modules),
        "modules": modules,
        "stale": stale,
    }


def write_manifest(out_dir: Path, bundle_env_dir: Path, source_env_dir: Path,
                   *, task_id: str = "", strict: bool = True) -> Dict[str, Any]:
    """Build the manifest, write it to `<out_dir>/mock_modules.json`, return it.

    Raises `StaleMockModule` when `strict` and any shipped module diverges from
    the harness source. Failing the build is the whole point: a stale module is
    a silent wrong-behavior delivery, and the alternative — shipping it with a
    warning — is what happened last time.
    """
    manifest = build_manifest(bundle_env_dir, source_env_dir, task_id=task_id)
    path = Path(out_dir) / MANIFEST_NAME
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    if strict and manifest["stale"]:
        raise StaleMockModule(
            f"{len(manifest['stale'])} mock module(s) differ from "
            f"{source_env_dir}: {', '.join(manifest['stale'])}"
        )
    return manifest


def verify_manifest(bundle_dir: Path, source_env_dir: Optional[Path] = None
                    ) -> Tuple[List[str], List[str]]:
    """Re-check an already-written bundle. Returns (errors, warnings).

    Two independent checks, because they fail for different reasons:
      * recorded digest vs the module ON DISK in the bundle -> the bundle was
        edited after packaging (or the manifest is from a different build)
      * bundle module vs the harness source -> the megan staleness case
    `source_env_dir` None skips the second check (validating a delivered bundle
    on a machine without the harness tree).
    """
    bundle_dir = Path(bundle_dir)
    errors: List[str] = []
    warnings: List[str] = []

    manifest_path = bundle_dir / MANIFEST_NAME
    bundle_env = bundle_dir / "data" / "environment"
    if not manifest_path.is_file():
        if bundle_env.is_dir():
            errors.append(f"{MANIFEST_NAME} missing (bundle ships mock modules "
                          "with no integrity manifest)")
        return errors, warnings
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"{MANIFEST_NAME} unreadable: {exc}"], warnings
    recorded = manifest.get("modules")
    if not isinstance(recorded, dict):
        return [f"{MANIFEST_NAME} has no modules block"], warnings

    on_disk = collect_modules(bundle_env)
    for rel, entry in sorted(recorded.items()):
        path = on_disk.get(rel)
        if path is None:
            errors.append(f"{rel}: in manifest, absent from the bundle")
            continue
        actual = module_digest(path)
        if actual != (entry or {}).get("digest"):
            errors.append(f"{rel}: bundle module does not match its manifest "
                          f"digest (bundle edited after packaging?)")
    for rel in sorted(set(on_disk) - set(recorded)):
        warnings.append(f"{rel}: shipped but not recorded in {MANIFEST_NAME}")

    if source_env_dir is not None:
        source = collect_modules(Path(source_env_dir))
        for rel, path in sorted(on_disk.items()):
            src = source.get(rel)
            if src is None:
                warnings.append(f"{rel}: no counterpart in {source_env_dir}")
                continue
            if module_digest(path) != module_digest(src):
                errors.append(f"{rel}: STALE — differs from the harness source "
                              f"at {source_env_dir}")
    return errors, warnings
