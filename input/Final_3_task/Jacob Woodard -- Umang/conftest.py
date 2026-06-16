"""
conftest.py — Pytest fixtures for JACOB_001_krasicki_delivery_crisis
====================================================================

The OpenClaw harness writes the agent's final state to
``tests/agent_state.json`` (or ``agent_state.json`` at the bundle root
as a legacy fallback).  The ``state`` fixture loads that file once per
session so every test_outputs.py test can query it without re-reading.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any, Dict

import pytest


@pytest.fixture(scope="session")
def state() -> Dict[str, Any]:
    """Load agent state produced by the OpenClaw harness."""
    here = pathlib.Path(__file__).resolve().parent
    candidates = [
        here / "tests" / "agent_state.json",
        here / "agent_state.json",
    ]
    for path in candidates:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    return {}
