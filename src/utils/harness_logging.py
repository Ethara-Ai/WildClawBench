"""Centralized debug logging for the whole harness pipeline.

Goal: produce a single, self-contained debug log file that traces a run end to
end — auth/connection setup (LiteLLM sidecar, OAuth bridges, mock stack), task
load, workspace staging, agent dispatch (trajectory creation), usage/output
collection, and both scoring channels (pytest reward + rubric judge). It is the
file you open to answer "where did the harness break?" and "why is the score 0 /
why didn't the rubric run?".

Design
------
Every module in this repo logs via ``logging.getLogger(__name__)`` and lets
records propagate to the *root* logger (see ``eval/run_batch.py`` basicConfig and
``src/utils/ui/console.py::install_rich_logging``). So we do NOT need to touch
each module: attaching one DEBUG-level ``FileHandler`` to the root logger
captures every existing ``logger.info/warning/error`` across the codebase
(litellm_sidecar, mock_stack, docker_utils, claude_oauth/codex_oauth bridges,
grading, testgen, test_executor, ...) into one file.

``install_rich_logging`` deliberately preserves FileHandlers and only ever
*lowers* the root level, so the handler we add here survives and keeps flowing at
DEBUG while the console stays at INFO (uncluttered).

Two granularities:
  * a SESSION file (one per process) under ``logs/harness_debug_<ts>.log`` that
    captures the whole batch, including the shared auth/connection setup that
    happens once before any task runs;
  * a per-RUN file written into each ``output_dir`` (``harness_debug.log``) so a
    single graded run carries its own focused trace next to its score.json.

Everything here is stdlib-only and defensive: logging must never crash a run.
"""

from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------
# Verbose on purpose: absolute timestamp (with date), level, logger name, and
# thread name — the last matters because drift-director / mock-health / stream
# renderer run on daemon threads and their interleaving is often the bug.
_DEBUG_FORMAT = "%(asctime)s | %(levelname)-7s | %(threadName)-14s | %(name)s | %(message)s"
_DEBUG_DATEFMT = "%Y-%m-%d %H:%M:%S"

# The stage/event helpers log under this name so they are easy to grep/filter.
STAGE_LOGGER = "harness.stage"

# Idempotency bookkeeping: map absolute path -> handler so repeated installs are
# no-ops and callers can detach a per-run handler cleanly.
_installed: dict[str, logging.Handler] = {}
_session_path: Path | None = None


def _make_file_handler(path: Path, level: int) -> logging.Handler:
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(str(path), encoding="utf-8")
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(_DEBUG_FORMAT, datefmt=_DEBUG_DATEFMT))
    return handler


def _keep_console_uncluttered(root: logging.Logger, console_level: int) -> None:
    """Pin existing stdout/stderr/Rich handlers to ``console_level`` so lowering
    the root logger to DEBUG (for the file) does not flood the terminal."""
    for h in list(root.handlers):
        # Skip our own file handlers (their stream is a file, not a tty).
        if isinstance(h, logging.FileHandler):
            continue
        try:
            h.setLevel(console_level)
        except Exception:  # pragma: no cover - defensive
            pass


def install_debug_logfile(
    path: str | os.PathLike[str] | None = None,
    *,
    level: int = logging.DEBUG,
    console_level: int = logging.INFO,
    log_dir: str | os.PathLike[str] = "logs",
) -> Path:
    """Attach a DEBUG ``FileHandler`` to the root logger (the SESSION file).

    Captures every module's log records for the lifetime of the process. Safe to
    call more than once — the same resolved path is only installed once. Returns
    the path actually being written to.
    """
    global _session_path
    if path is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = Path(log_dir) / f"harness_debug_{ts}.log"
    path = Path(path).resolve()
    key = str(path)

    root = logging.getLogger()
    if key not in _installed:
        try:
            handler = _make_file_handler(path, level)
        except Exception as exc:  # pragma: no cover - defensive
            # Never let logging setup kill the harness; fall back to a warning.
            logging.getLogger(__name__).warning(
                "could not open debug log file %s: %s", path, exc
            )
            return path
        root.addHandler(handler)
        _installed[key] = handler

    # Ensure DEBUG records actually reach the handler, but keep console at INFO.
    if root.level == logging.NOTSET or root.level > level:
        root.setLevel(level)
    _keep_console_uncluttered(root, console_level)

    if _session_path is None:
        _session_path = path
        logging.getLogger(STAGE_LOGGER).info(
            "=== harness debug session log opened: %s (pid=%s) ===", path, os.getpid()
        )
    return path


def attach_run_logfile(
    output_dir: str | os.PathLike[str],
    *,
    filename: str = "harness_debug.log",
    level: int = logging.DEBUG,
    console_level: int = logging.INFO,
) -> logging.Handler | None:
    """Attach a per-run DEBUG ``FileHandler`` writing into ``output_dir``.

    Returns the handler so the caller can pass it to :func:`detach_run_logfile`
    in a ``finally`` block. Returns ``None`` if the handler could not be created
    (logging must never break a run).
    """
    path = (Path(output_dir) / filename).resolve()
    root = logging.getLogger()
    try:
        handler = _make_file_handler(path, level)
    except Exception as exc:  # pragma: no cover - defensive
        logging.getLogger(__name__).warning(
            "could not open per-run debug log %s: %s", path, exc
        )
        return None
    root.addHandler(handler)
    _installed[str(path)] = handler
    if root.level == logging.NOTSET or root.level > level:
        root.setLevel(level)
    _keep_console_uncluttered(root, console_level)
    logging.getLogger(STAGE_LOGGER).info("--- per-run debug log opened: %s ---", path)
    return handler


def detach_run_logfile(handler: logging.Handler | None) -> None:
    """Remove and close a handler returned by :func:`attach_run_logfile`."""
    if handler is None:
        return
    root = logging.getLogger()
    try:
        logging.getLogger(STAGE_LOGGER).info("--- per-run debug log closed ---")
        root.removeHandler(handler)
        handler.close()
    except Exception:  # pragma: no cover - defensive
        pass
    for k, v in list(_installed.items()):
        if v is handler:
            _installed.pop(k, None)


# ---------------------------------------------------------------------------
# Structured helpers used to instrument the pipeline
# ---------------------------------------------------------------------------
def _fmt_fields(fields: dict[str, Any]) -> str:
    if not fields:
        return ""
    parts = []
    for k, v in fields.items():
        parts.append(f"{k}={v!r}" if isinstance(v, str) else f"{k}={v}")
    return " " + " ".join(parts)


def event(msg: str, /, *, logger: logging.Logger | str | None = None, **fields: Any) -> None:
    """Log a single structured line: ``msg key=val key=val``.

    Use for milestone facts (resolved model, reward value, judge verdict counts,
    container ids) that you want to grep for later.
    """
    log = _resolve(logger)
    try:
        log.info("%s%s", msg, _fmt_fields(fields))
    except Exception:  # pragma: no cover - defensive
        pass


def _resolve(logger: logging.Logger | str | None) -> logging.Logger:
    if isinstance(logger, logging.Logger):
        return logger
    if isinstance(logger, str):
        return logging.getLogger(logger)
    return logging.getLogger(STAGE_LOGGER)


@contextmanager
def stage(
    name: str,
    *,
    logger: logging.Logger | str | None = None,
    **context: Any,
) -> Iterator[dict[str, Any]]:
    """Context manager that brackets a pipeline stage with START/END/FAIL lines
    and elapsed timing.

    Yields a mutable dict; anything you put in it is appended to the END line, so
    a stage can report an outcome (e.g. ``st["reward"] = 0.0``).

    Exceptions are logged with a full traceback (as FAIL) and re-raised — the
    control flow of the harness is never altered by instrumentation.
    """
    log = _resolve(logger)
    outcome: dict[str, Any] = {}
    start = time.monotonic()
    try:
        log.info("▶ START %s%s", name, _fmt_fields(context))
    except Exception:  # pragma: no cover - defensive
        pass
    try:
        yield outcome
    except BaseException as exc:  # noqa: BLE001 - we log then re-raise everything
        elapsed = time.monotonic() - start
        try:
            log.error(
                "✖ FAIL  %s elapsed=%.2fs error=%r%s",
                name,
                elapsed,
                exc,
                _fmt_fields(outcome),
                exc_info=True,
            )
        except Exception:  # pragma: no cover - defensive
            pass
        raise
    else:
        elapsed = time.monotonic() - start
        try:
            log.info("✔ END   %s elapsed=%.2fs%s", name, elapsed, _fmt_fields(outcome))
        except Exception:  # pragma: no cover - defensive
            pass


def mask_secret(value: str | None, *, keep: int = 4) -> str:
    """Mask a token/key for safe logging: keep the last ``keep`` chars."""
    if not value:
        return "<empty>"
    if len(value) <= keep:
        return "*" * len(value)
    return "*" * (len(value) - keep) + value[-keep:]


def get_logger(name: str) -> logging.Logger:
    """Convenience wrapper so call sites can ``from harness_logging import get_logger``."""
    return logging.getLogger(name)
