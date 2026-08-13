"""AST guard: a sub-agent spawn always runs the main trajectory's model.

Deliberately AST-only (no docker, no sidecar, no network) so it is safe to run
anywhere. Pins the fix for the Bedrock 403 where a spawn fell back to the
hardcoded id 'claude-opus-4-7'. That id is not a registered sidecar model_name
(every Bedrock alias is dot-form), so LiteLLM resolved it against its own cost
map and issued a bare foundation-model invoke with no model_id, which
EtharaKenseiProductionPolicy explicitly denies.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DIRECTOR = REPO_ROOT / "src" / "utils" / "subagent_director.py"
RUNNER = REPO_ROOT / "src" / "agents" / "openclaw" / "runner.py"

ENV_KEY = "WILDCLAW_MODEL"


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _find_function(tree: ast.Module, name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name}() not found")


def _env_keys_read(fn: ast.AST) -> set[str]:
    keys: set[str] = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "get":
            continue
        value = func.value
        if not (isinstance(value, ast.Attribute) and value.attr == "environ"):
            continue
        if node.args and isinstance(node.args[0], ast.Constant):
            keys.add(node.args[0].value)
    return keys


def test_invoker_resolves_model_only_from_main_trajectory_env() -> None:
    fn = _find_function(_parse(DIRECTOR), "_make_invoker_from_env")
    assert ENV_KEY in _env_keys_read(fn)
    assert "WILDCLAW_SUBAGENT_MODEL" not in _env_keys_read(fn), (
        "a per-spawn model override reintroduces main/sub model divergence"
    )


def test_invoker_has_no_hardcoded_model_fallback() -> None:
    fn = _find_function(_parse(DIRECTOR), "_make_invoker_from_env")
    for node in ast.walk(fn):
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
            for operand in node.values:
                assert not (
                    isinstance(operand, ast.Constant)
                    and isinstance(operand.value, str)
                    and "claude" in operand.value.lower()
                ), f"hardcoded model fallback {operand.value!r} reintroduced"


def test_invoker_raises_when_model_missing() -> None:
    fn = _find_function(_parse(DIRECTOR), "_make_invoker_from_env")
    raises = [n for n in ast.walk(fn) if isinstance(n, ast.Raise)]
    assert len(raises) >= 2, "missing WILDCLAW_MODEL must fail loudly, not default"


def test_runner_sets_model_env_unconditionally() -> None:
    tree = _parse(RUNNER)
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent

    assigns = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(t, ast.Subscript)
            and isinstance(t.slice, ast.Constant)
            and t.slice.value == ENV_KEY
            for t in node.targets
        )
    ]
    assert assigns, f"runner no longer assigns {ENV_KEY}"

    for assign in assigns:
        node: ast.AST = assign
        while node in parents:
            node = parents[node]
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                break
            assert not isinstance(node, ast.If), (
                f"{ENV_KEY} moved back inside a conditional; a spawn that "
                "resolves a base URL from the image would lose the model"
            )


def test_no_dash_form_opus_model_id_literal() -> None:
    offenders = []
    for path in (REPO_ROOT / "src").rglob("*.py"):
        for node in ast.walk(_parse(path)):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value == "claude-opus-4-7"
            ):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
    assert not offenders, (
        "dash-form 'claude-opus-4-7' is not a registered sidecar model_name "
        f"(Bedrock aliases are dot-form): {offenders}"
    )
