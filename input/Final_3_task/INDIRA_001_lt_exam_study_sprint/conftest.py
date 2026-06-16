"""
conftest.py - State fixture for INDIRA_001 test_outputs.py.

The OpenClaw harness writes the final per-task agent state to
`tests/agent_state.json` at the end of the simulation. This fixture
loads it for every test function. If no state file exists (e.g. during
schema-only validation runs), the fixture returns an empty dict and the
checker lambdas degrade to "no evidence" failures, which is the
expected behavior pre-harness.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def state() -> dict:
    """Return the live agent state from the harness output, or {} if absent."""
    state_path = Path(__file__).resolve().parent / "tests" / "agent_state.json"
    if not state_path.exists():
        # Fallback: bundle-root agent_state.json (legacy layout)
        legacy = Path(__file__).resolve().parent / "agent_state.json"
        if legacy.exists():
            return json.loads(legacy.read_text())
        return {}
    return json.loads(state_path.read_text())
