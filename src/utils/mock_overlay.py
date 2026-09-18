"""Lay one task's ``mock_data/`` over a baseline service, in a throwaway tree.

At runtime a task's ``mock_data/<api>/*`` files are bind-mounted read-only over
``environment/<api>/``, so the rows the agent sees are the task's, not the
fleet's. Anything that wants to know what a task's world actually looks like
has to reproduce that overlay before it looks — checking against the pristine
seeds answers a question nobody asked, and (the fleet-audit lesson) reports
not-founds for rows the task does seed.

``script/coerce_dryrun.py`` built this tree privately; the pre-trajectory task
gate needs the same one, plus the ``server.py`` half for
``service_probe.load_service``. So the tree-building lives here once, and both
entry points are context managers that clean up after themselves:

* :func:`overlaid_tree` — the directory, for a caller that wants the probe.
* :func:`overlaid_data_module` — the imported ``<api>_data`` module, for a
  caller that wants the store and the service getters.

Mutating a module imported this way cannot touch ``environment/`` or any other
task: it was imported out of a temp copy that is deleted on exit.
"""
from __future__ import annotations

import importlib
import shutil
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

__all__ = ["ENVIRONMENT_DIR", "OverlayError", "overlaid_data_module", "overlaid_tree"]

ENVIRONMENT_DIR = Path(__file__).resolve().parents[2] / "environment"

# Flat siblings every ``environment/<api>/`` module imports by bare name.
_INFRA = ("_mutable_store.py", "admin_plane.py", "tracking_middleware.py")

# Evicted before the data module is imported so a previously-loaded service
# cannot hand back its already-populated store (register/eager_load are both
# idempotent, so a stale store would run neither the loader nor the coercer).
_SHARED_MODULES = ("server", "_mutable_store", "admin_plane", "tracking_middleware")


class OverlayError(Exception):
    """The baseline service an overlay names does not exist on disk."""


@contextmanager
def overlaid_tree(api: str, overlay_dir: Optional[Path],
                  environment_dir: Optional[Path] = None) -> Iterator[Path]:
    """Yield a temp copy of ``environment/<api>/`` with ``overlay_dir`` laid over it.

    The yielded path is the SERVICE dir (``<tmp>/<api>``); its parent holds the
    infra siblings, mirroring the ``environment/`` layout both the data module
    and ``server.py`` import against. ``overlay_dir`` may be None — a service a
    task names but does not seed runs on the fleet's own rows, and that is the
    world the agent gets.
    """
    env_dir = Path(environment_dir) if environment_dir else ENVIRONMENT_DIR
    src_api = env_dir / api
    if not src_api.is_dir():
        raise OverlayError(f"no such baseline api dir: {src_api}")
    tmp = Path(tempfile.mkdtemp(prefix=f"overlay-{api}-"))
    try:
        shutil.copytree(src_api, tmp / api)
        for infra in _INFRA:
            infra_path = env_dir / infra
            if infra_path.is_file():
                shutil.copy(infra_path, tmp)
        if overlay_dir is not None and Path(overlay_dir).is_dir():
            for seed in sorted(Path(overlay_dir).iterdir()):
                if seed.is_file():
                    shutil.copy(seed, tmp / api / seed.name)
        yield tmp / api
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _data_module_name(service_dir: Path, api: str) -> str:
    """The ``<api>_data`` module a service ships, by convention then by glob."""
    conventional = f"{api.replace('-', '_')}_data"
    if (service_dir / f"{conventional}.py").is_file():
        return conventional
    candidates = sorted(service_dir.glob("*_data.py"))
    if not candidates:
        raise OverlayError(f"no *_data.py in {service_dir}")
    return candidates[0].stem


@contextmanager
def overlaid_data_module(api: str, overlay_dir: Optional[Path],
                         environment_dir: Optional[Path] = None) -> Iterator[Any]:
    """Yield the task-overlaid ``<api>_data`` module, imported for real.

    Importing it runs ``_store.eager_load()``, which is the same coercion the
    live mock performs, so a schema the task's CSVs do not match raises here
    rather than at seed time inside a container. The module's getters are then
    the real serving projection — what the agent can actually read.
    """
    with overlaid_tree(api, overlay_dir, environment_dir) as service_dir:
        module_name = _data_module_name(service_dir, api)
        saved_path = list(sys.path)
        saved_modules = set(sys.modules)
        sys.path[:0] = [str(service_dir.parent), str(service_dir)]
        for cached in list(sys.modules):
            if cached == module_name or cached in _SHARED_MODULES:
                del sys.modules[cached]
        try:
            yield importlib.import_module(module_name)
        finally:
            sys.path[:] = saved_path
            for name in list(sys.modules):
                if name not in saved_modules:
                    del sys.modules[name]
