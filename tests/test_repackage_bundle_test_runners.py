"""Invariant tests for script/repackage_to_bundle.py test-runner / solver staging.

Delivered bundles carry NO pytest surface (bundle-only test-surface strip): the
bundle emits only ``data/solution/solve.sh``. The full 4-file test surface
(``data/tests/{test_outputs.py,test_weights.json,test.sh}`` + solve.sh) is still
staged on the OUTPUT side via ``stage_output_data`` (``--stage-output-data``),
so ``output/<task>/data`` and the delivered bundle differ by ``data/tests/**``
as a by-design asymmetry.

The tests are static (no docker, no network). They cover:
  1. test.sh / solve.sh generators byte-equal with src/utils/harbor/.
  2. convert_task emits solve.sh but NO data/tests/ (delivered bundle).
  3. stage_output_data emits the full 4-file surface into output/<task>/data/.
  4. solve.sh contains one os.environ.get per discovered service env_var.
  5. test_outputs.py / test_weights.json sources are at input/<task>/ ROOT.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_repackage_module():
    spec = importlib.util.spec_from_file_location(
        "_rp_test", REPO_ROOT / "script" / "repackage_to_bundle.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_harbor_test_sh():
    sys.path.insert(0, str(REPO_ROOT / "src" / "utils" / "harbor"))
    try:
        from test_sh import generate_harbor_test_sh
        return generate_harbor_test_sh
    finally:
        sys.path.pop(0)


def _load_harbor_solve_sh():
    sys.path.insert(0, str(REPO_ROOT / "src" / "utils" / "harbor"))
    try:
        from solve_sh import generate_harbor_solve_sh
        return generate_harbor_solve_sh
    finally:
        sys.path.pop(0)


def test_test_sh_byte_equal_with_harbor():
    rp = _load_repackage_module()
    harbor = _load_harbor_test_sh()
    assert rp._generate_test_sh() == harbor(), (
        "test.sh has drifted from src/utils/harbor/test_sh.py::_TEST_SH. "
        "Both must stay byte-equal; update both files together."
    )


def test_solve_sh_byte_equal_with_harbor_empty():
    rp = _load_repackage_module()
    harbor = _load_harbor_solve_sh()
    assert rp._generate_solve_sh({}) == harbor({})
    assert rp._generate_solve_sh(None) == harbor(None)


def test_solve_sh_byte_equal_with_harbor_populated():
    rp = _load_repackage_module()
    harbor = _load_harbor_solve_sh()
    env_vars = {
        "GMAIL_API_URL": "http://gmail-api:8017",
        "XERO_API_URL": "http://xero-api:8088",
        "QUICKBOOKS_API_URL": "http://quickbooks-api:8041",
    }
    assert rp._generate_solve_sh(env_vars) == harbor(env_vars)


def _stage_minimal_task(tmp_path: Path, task_id: str = "ben_cox_8fc24d4b") -> tuple[Path, Path, Path]:
    src_root = tmp_path / "output_root"
    dst_root = tmp_path / "dest_root"
    inp_root = tmp_path / "input_root"
    task_src = src_root / task_id
    task_inp = inp_root / task_id

    (task_src / "trajectories" / "claude" / "run_1").mkdir(parents=True)
    (task_src / "trajectories" / "claude" / "run_1" / "output.json").write_text("{}")
    (task_src / "rubric.json").write_text('{"criteria": []}')

    env_dir = task_src / "data" / "environment"
    (env_dir / "gmail-api").mkdir(parents=True)
    (env_dir / "gmail-api" / "service.toml").write_text(
        '[service]\nname = "gmail-api"\nport = 8017\nenv_var_name = "GMAIL_API_URL"\n'
    )
    (env_dir / "xero-api").mkdir(parents=True)
    (env_dir / "xero-api" / "service.toml").write_text(
        '[service]\nname = "xero-api"\nport = 8088\nenv_var_name = "XERO_API_URL"\n'
    )

    task_inp.mkdir(parents=True)
    (task_inp / "prompt.txt").write_text("dummy prompt\n")
    (task_inp / "test_outputs.py").write_text(
        "import pytest\n\nclass TestFoo:\n    def test_bar(self):\n        assert True\n"
    )
    (task_inp / "test_weights.json").write_text(json.dumps({"test_bar": 5}))

    return src_root, dst_root, inp_root


def test_convert_task_emits_no_test_surface_only_solve_sh(tmp_path):
    rp = _load_repackage_module()
    src_root, dst_root, inp_root = _stage_minimal_task(tmp_path)
    task_id = "ben_cox_8fc24d4b"

    bundle = rp.convert_task(
        task_dir=src_root / task_id,
        dest_root=dst_root,
        input_root=inp_root,
        infer_meta=False,
        verbose=False,
    )
    assert bundle is not None, "convert_task returned None"

    solve_sh = bundle / "data" / "solution" / "solve.sh"
    assert solve_sh.is_file(), f"missing {solve_sh}"

    tests_dir = bundle / "data" / "tests"
    assert not tests_dir.exists(), (
        "delivered bundle must carry NO pytest surface — data/tests/ removed "
        "deliberately (bundle-only test-surface strip); do NOT restore"
    )


def test_stage_output_data_test_outputs_source_is_input_root(tmp_path):
    rp = _load_repackage_module()
    src_root, _dst, inp_root = _stage_minimal_task(tmp_path)
    task_id = "ben_cox_8fc24d4b"
    task_dir = src_root / task_id

    src_text = (inp_root / task_id / "test_outputs.py").read_text()
    src_weights = (inp_root / task_id / "test_weights.json").read_text()

    assert rp.stage_output_data(task_dir=task_dir, input_root=inp_root, verbose=False)

    assert (task_dir / "data" / "tests" / "test_outputs.py").read_text() == src_text
    assert (task_dir / "data" / "tests" / "test_weights.json").read_text() == src_weights


def test_stage_output_data_accepts_legacy_test_output_singular_filename(tmp_path):
    rp = _load_repackage_module()
    src_root, _dst, inp_root = _stage_minimal_task(tmp_path)
    task_id = "ben_cox_8fc24d4b"
    task_dir = src_root / task_id

    canonical = inp_root / task_id / "test_outputs.py"
    legacy = inp_root / task_id / "test_output.py"
    legacy_content = canonical.read_text()
    canonical.unlink()
    legacy.write_text(legacy_content)

    assert rp.stage_output_data(task_dir=task_dir, input_root=inp_root, verbose=False)
    dst = task_dir / "data" / "tests" / "test_outputs.py"
    assert dst.is_file(), (
        "legacy singular-typo test_output.py at input root must still produce "
        "canonical data/tests/test_outputs.py on the output side (mirrors "
        "task_parser.py:190 which accepts both names)"
    )
    assert dst.read_text() == legacy_content


def test_resolve_test_outputs_source_prefers_canonical_when_both_present(tmp_path):
    rp = _load_repackage_module()
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    (task_dir / "test_outputs.py").write_text("# canonical")
    (task_dir / "test_output.py").write_text("# legacy")
    resolved = rp._resolve_test_outputs_source(task_dir)
    assert resolved is not None
    assert resolved.name == "test_outputs.py"
    assert resolved.read_text() == "# canonical"


def test_resolve_test_outputs_source_falls_back_to_legacy(tmp_path):
    rp = _load_repackage_module()
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    (task_dir / "test_output.py").write_text("# legacy only")
    resolved = rp._resolve_test_outputs_source(task_dir)
    assert resolved is not None
    assert resolved.name == "test_output.py"


def test_resolve_test_outputs_source_returns_none_when_absent(tmp_path):
    rp = _load_repackage_module()
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    assert rp._resolve_test_outputs_source(task_dir) is None


def test_solve_sh_references_discovered_env_vars(tmp_path):
    rp = _load_repackage_module()
    src_root, dst_root, inp_root = _stage_minimal_task(tmp_path)
    task_id = "ben_cox_8fc24d4b"
    # solve.sh env is scoped to the task's declared APIs, so they must be declared.
    (inp_root / task_id / "task.yaml").write_text(
        "task_type: finance_ops\ndifficulty: hard\nrequired_apis: [gmail, xero]\n"
    )

    bundle = rp.convert_task(
        task_dir=src_root / task_id,
        dest_root=dst_root,
        input_root=inp_root,
        infer_meta=False,
        verbose=False,
    )
    solve = (bundle / "data" / "solution" / "solve.sh").read_text()
    assert "GMAIL_API_URL" in solve
    assert "XERO_API_URL" in solve
    assert "http://gmail-api:8017" in solve
    assert "http://xero-api:8088" in solve


def test_solve_sh_emits_but_no_test_surface_when_input_root_missing(tmp_path):
    rp = _load_repackage_module()
    src_root = tmp_path / "src"
    dst_root = tmp_path / "dst"
    inp_root = tmp_path / "input_does_not_match"
    inp_root.mkdir()

    task_id = "orphan_task_001"
    task_src = src_root / task_id
    (task_src / "trajectories" / "claude" / "run_1").mkdir(parents=True)
    (task_src / "trajectories" / "claude" / "run_1" / "output.json").write_text("{}")
    (task_src / "rubric.json").write_text('{"criteria": []}')

    bundle = rp.convert_task(
        task_dir=task_src,
        dest_root=dst_root,
        input_root=inp_root,
        infer_meta=False,
        verbose=False,
    )
    assert bundle is not None
    assert (bundle / "data" / "solution" / "solve.sh").is_file(), (
        "solve.sh must emit unconditionally (no input dir dependency)"
    )
    assert not (bundle / "data" / "tests").exists(), (
        "delivered bundle must carry NO pytest surface, even for orphan tasks"
    )


def test_discover_service_env_vars_returns_dict(tmp_path):
    rp = _load_repackage_module()
    env_dir = tmp_path / "environment"
    (env_dir / "alpha").mkdir(parents=True)
    (env_dir / "alpha" / "service.toml").write_text(
        '[service]\nname = "alpha"\nport = 9000\nenv_var_name = "ALPHA_URL"\n'
    )
    (env_dir / "beta-svc").mkdir(parents=True)
    (env_dir / "beta-svc" / "service.toml").write_text(
        '[service]\nname = "beta-svc"\nport = 9001\nenv_var_name = "BETA_URL"\n'
    )
    out = rp._discover_service_env_vars(env_dir)
    assert out == {
        "ALPHA_URL": "http://alpha:9000",
        "BETA_URL": "http://beta-svc:9001",
    }


def test_discover_service_env_vars_respects_enabled_filter(tmp_path):
    rp = _load_repackage_module()
    env_dir = tmp_path / "environment"
    for n, p, v in [("alpha", 9000, "ALPHA_URL"), ("beta", 9001, "BETA_URL")]:
        d = env_dir / n
        d.mkdir(parents=True)
        (d / "service.toml").write_text(
            f'[service]\nname = "{n}"\nport = {p}\nenv_var_name = "{v}"\n'
        )
    out = rp._discover_service_env_vars(env_dir, enabled_apis={"alpha"})
    assert out == {"ALPHA_URL": "http://alpha:9000"}


def test_discover_service_env_vars_skips_missing_toml(tmp_path):
    rp = _load_repackage_module()
    env_dir = tmp_path / "environment"
    (env_dir / "no-toml").mkdir(parents=True)
    out = rp._discover_service_env_vars(env_dir)
    assert out == {}


def test_per_rep_logs_verifier_has_no_fabricated_test_sources(tmp_path):
    rp = _load_repackage_module()
    src_root, dst_root, inp_root = _stage_minimal_task(tmp_path)
    task_id = "ben_cox_8fc24d4b"

    bundle = rp.convert_task(
        task_dir=src_root / task_id,
        dest_root=dst_root,
        input_root=inp_root,
        infer_meta=False,
        verbose=False,
    )
    assert bundle is not None
    verifier = bundle / "trajectories" / "Claude Opus 4.7" / "run_1" / "logs" / "verifier"
    for name in ("test.sh", "test_outputs.py", "test_weights.json"):
        assert not (verifier / name).exists(), (
            f"{name} must NOT be fabricated into the bundle per-rep logs/verifier/ "
            "for a rubric-only run (bundle-side _stage_verifier_test_sources removed)"
        )


# ---- stage_output_data parity tests ----
#
# These pin the output/<task>/data parity contract added 2026-06-27 per user
# request: "There is a mismatch between data folder in output and output_bundle"
# (m0835) and user-chosen Option 1 (m0838): "Mirror EVERYTHING into output/".
# The orchestrator stage_output_data() is invoked by eval/run_batch.py via
# subprocess (--stage-output-data flag) BEFORE the bundle subprocess call,
# so output/<task>/data/ ends up matching bundle/<task>/data/ modulo the 3
# by-design strips (_overlay_manifest.json, _meta.json, skills/*/_meta.json).
# See script/AGENTS.md "stage-output-data" invariant for the full contract.


def test_stage_output_data_emits_5_artifacts_into_source(tmp_path):
    """stage_output_data writes persona/, artifacts/, 3 harness .py, tests/, solution/
    into task_dir/data/ (the SOURCE output/<task>/, not a bundle/)."""
    rp = _load_repackage_module()
    src_root, _dst, inp_root = _stage_minimal_task(tmp_path)
    task_id = "ben_cox_8fc24d4b"
    task_dir = src_root / task_id

    # Stage persona + harness env source files so the staging helpers have inputs.
    (inp_root / task_id / "persona").mkdir(exist_ok=True)
    (inp_root / task_id / "persona" / "SOUL.md").write_text("# persona\n")
    (inp_root / task_id / "data").mkdir(exist_ok=True)
    (inp_root / task_id / "data" / "input.txt").write_text("artifact body\n")

    ok = rp.stage_output_data(task_dir=task_dir, input_root=inp_root, verbose=False)
    assert ok is True

    data = task_dir / "data"
    assert (data / "tests" / "test.sh").is_file(), "test.sh missing in output/<task>/data/tests/"
    assert (data / "tests" / "test_outputs.py").is_file(), "test_outputs.py missing"
    assert (data / "tests" / "test_weights.json").is_file(), "test_weights.json missing"
    assert (data / "solution" / "solve.sh").is_file(), "solve.sh missing"


def test_stage_output_data_still_emits_test_sh_when_input_missing(tmp_path):
    """test.sh + solve.sh are zero-dep on input/; they emit even when no input dir matches."""
    rp = _load_repackage_module()
    src_root = tmp_path / "src"
    inp_root = tmp_path / "input_does_not_match"
    inp_root.mkdir()
    task_id = "orphan_task"
    task_dir = src_root / task_id
    (task_dir / "data" / "environment").mkdir(parents=True)

    ok = rp.stage_output_data(task_dir=task_dir, input_root=inp_root, verbose=False)
    assert ok is True

    assert (task_dir / "data" / "tests" / "test.sh").is_file()
    assert (task_dir / "data" / "solution" / "solve.sh").is_file()


def test_stage_output_data_then_convert_task_parity(tmp_path):
    """End-to-end: after stage_output_data writes into output/<task>/data/, a
    subsequent convert_task produces a bundle whose data/solution/solve.sh is
    byte-equal with the output side, while data/tests/** is a by-design
    asymmetry (present in output, stripped from the delivered bundle)."""
    rp = _load_repackage_module()
    src_root, dst_root, inp_root = _stage_minimal_task(tmp_path)
    task_id = "ben_cox_8fc24d4b"
    task_dir = src_root / task_id

    (inp_root / task_id / "persona").mkdir(exist_ok=True)
    (inp_root / task_id / "persona" / "SOUL.md").write_text("# persona\n")
    (inp_root / task_id / "data").mkdir(exist_ok=True)
    (inp_root / task_id / "data" / "input.txt").write_text("artifact body\n")

    assert rp.stage_output_data(task_dir=task_dir, input_root=inp_root, verbose=False)

    bundle = rp.convert_task(
        task_dir=task_dir,
        dest_root=dst_root,
        input_root=inp_root,
        infer_meta=False,
        verbose=False,
    )
    assert bundle is not None

    # solve.sh is byte-equal on both sides.
    assert (task_dir / "data" / "solution" / "solve.sh").is_file()
    assert (bundle / "data" / "solution" / "solve.sh").is_file()
    assert (task_dir / "data" / "solution" / "solve.sh").read_bytes() == (
        bundle / "data" / "solution" / "solve.sh"
    ).read_bytes()

    # data/tests/** is the by-design asymmetry: present on the output side,
    # stripped from the delivered bundle.
    for rel in (
        "data/tests/test.sh",
        "data/tests/test_outputs.py",
        "data/tests/test_weights.json",
    ):
        assert (task_dir / rel).is_file(), f"output side missing {rel}"
    assert not (bundle / "data" / "tests").exists(), (
        "delivered bundle must carry NO data/tests/ (bundle-only test-surface strip)"
    )


def test_stage_output_data_cli_flag_in_argparse():
    """The --stage-output-data flag must be exposed via the CLI so eval/run_batch.py
    can invoke it via subprocess. AST-level check on the module source."""
    src = (REPO_ROOT / "script" / "repackage_to_bundle.py").read_text()
    assert "--stage-output-data" in src, (
        "--stage-output-data flag missing from script/repackage_to_bundle.py argparse; "
        "eval/run_batch.py subprocess invocation will fail."
    )
    assert "def stage_output_data" in src, (
        "stage_output_data() orchestrator missing from script/repackage_to_bundle.py"
    )


# ============================================================================
# Harbor-parity emitters: instruction.md, Dockerfile, docker-compose.yaml, task.toml
# Pins the 4 emissions added 2026-06-27 after user m0899 reported these files
# missing from output_bundle. All 4 emit into bundle/data/ (and into output/<task>/data/
# via --stage-output-data). Each test ensures byte-equal with Harbor source-of-truth
# generators (src/utils/harbor/{dockerfile.py,compose.py,task_toml.py}) where applicable.
# ============================================================================


def _load_harbor_dockerfile():
    spec = importlib.util.spec_from_file_location(
        "harbor_dockerfile", REPO_ROOT / "src" / "utils" / "harbor" / "dockerfile.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.generate_harbor_dockerfile


def _load_harbor_compose_gen():
    """Load Harbor compose generator via a sys.path-aware loader.

    compose.py uses package-relative imports (`from .compose import ...`),
    so we import the parent harbor package first.
    """
    import sys as _sys
    if str(REPO_ROOT) not in _sys.path:
        _sys.path.insert(0, str(REPO_ROOT))
    from src.utils.harbor.compose import generate_harbor_compose  # type: ignore
    return generate_harbor_compose


def test_dockerfile_byte_equal_with_harbor_all_combinations():
    """Dockerfile must be byte-equal with src/utils/harbor/dockerfile.py across
    all 8 (has_skills, has_persona, has_artifacts) boolean combinations."""
    import itertools
    rp = _load_repackage_module()
    harbor_gen = _load_harbor_dockerfile()
    for s, p, a in itertools.product([False, True], repeat=3):
        harbor = harbor_gen(has_skills=s, has_persona=p, has_artifacts=a)
        local = rp._generate_environment_dockerfile(has_skills=s, has_persona=p, has_artifacts=a)
        assert harbor == local, (
            f"Dockerfile drift at has_skills={s} has_persona={p} has_artifacts={a}"
        )


def test_compose_byte_equal_with_harbor(tmp_path):
    """docker-compose.yaml must be byte-equal with src/utils/harbor/compose.py
    when given the same env_dir (here: a minimal env with 2 services)."""
    rp = _load_repackage_module()
    harbor_compose = _load_harbor_compose_gen()
    env_dir = tmp_path / "env"
    env_dir.mkdir()
    for name, port, env_var in (
        ("gmail-api", 8017, "GMAIL_API_URL"),
        ("xero-api", 8087, "XERO_API_URL"),
    ):
        svc_dir = env_dir / name
        svc_dir.mkdir()
        (svc_dir / "service.toml").write_text(
            f'[service]\nname = "{name}"\nport = {port}\nenv_var_name = "{env_var}"\nhealthcheck_path = "/health"\n'
        )
    harbor = harbor_compose(env_dir)
    local = rp._generate_environment_compose(env_dir)
    assert harbor == local, "compose byte-equal drift"


def test_instruction_md_emitted_with_workspace_hint_when_attachments(tmp_path):
    """When input task has attachments (persona/home or data files),
    instruction.md = prompt.txt + workspace_hint suffix."""
    rp = _load_repackage_module()
    input_task_dir = tmp_path / "task_with_attachments"
    input_task_dir.mkdir()
    (input_task_dir / "prompt.txt").write_text("Solve this puzzle.\n")
    (input_task_dir / "data").mkdir()
    (input_task_dir / "data" / "input.txt").write_text("payload\n")

    bundle = tmp_path / "bundle_a"
    assert rp._stage_data_instruction(input_task_dir, bundle, verbose=False)
    text = (bundle / "data" / "solution" / "instruction.md").read_text()
    assert not (bundle / "data" / "instruction.md").exists()
    assert text.startswith("Solve this puzzle.")
    assert "Workspace inputs" in text
    assert "/root/workspace/" in text


def test_instruction_md_no_workspace_hint_when_no_attachments(tmp_path):
    """When the task has no persona/home and no data files, instruction.md is
    prompt.txt verbatim (no workspace hint appended)."""
    rp = _load_repackage_module()
    input_task_dir = tmp_path / "task_no_attachments"
    input_task_dir.mkdir()
    (input_task_dir / "prompt.txt").write_text("Just answer in words.\n")

    bundle = tmp_path / "bundle_b"
    assert rp._stage_data_instruction(input_task_dir, bundle, verbose=False)
    text = (bundle / "data" / "solution" / "instruction.md").read_text()
    assert text == "Just answer in words.\n"
    assert "Workspace inputs" not in text


def test_instruction_md_stage_removes_legacy_data_root_copy(tmp_path):
    """instruction.md moved from data/ into data/solution/. Re-staging a tree
    packaged before the move (or a bundle whose data/ was copytree'd from one)
    must drop the stale data/instruction.md so the file never ships twice, and
    must leave solve.sh / TRUTH.md alongside it untouched."""
    rp = _load_repackage_module()
    input_task_dir = tmp_path / "task"
    input_task_dir.mkdir()
    (input_task_dir / "prompt.txt").write_text("New prompt.\n")

    bundle = tmp_path / "bundle_legacy"
    solution = bundle / "data" / "solution"
    solution.mkdir(parents=True)
    (bundle / "data" / "instruction.md").write_text("STALE\n")
    (solution / "solve.sh").write_text("#!/bin/sh\n")
    (solution / "TRUTH.md").write_text("truth\n")

    assert rp._stage_data_instruction(input_task_dir, bundle, verbose=False)
    assert not (bundle / "data" / "instruction.md").exists()
    assert (solution / "instruction.md").read_text() == "New prompt.\n"
    assert (solution / "solve.sh").is_file() and (solution / "TRUTH.md").is_file()


def test_instruction_md_legacy_copy_kept_when_nothing_is_staged(tmp_path):
    """No readable prompt source -> nothing is emitted, so the existing legacy
    file is the only copy and must NOT be deleted."""
    rp = _load_repackage_module()
    input_task_dir = tmp_path / "task_no_prompt"
    input_task_dir.mkdir()
    bundle = tmp_path / "bundle_keep"
    (bundle / "data").mkdir(parents=True)
    (bundle / "data" / "instruction.md").write_text("ONLY COPY\n")

    assert not rp._stage_data_instruction(input_task_dir, bundle, verbose=False)
    assert not rp._stage_data_instruction(None, bundle, verbose=False)
    assert (bundle / "data" / "instruction.md").read_text() == "ONLY COPY\n"


def test_dockerfile_and_compose_emitted_from_bundle_env_dir(tmp_path):
    """Dockerfile + docker-compose.yaml emit into bundle/data/environment/ when
    env_dir is present. has_persona/has_artifacts conditionals work."""
    rp = _load_repackage_module()
    bundle = tmp_path / "bundle_c"
    env_dir = bundle / "data" / "environment"
    env_dir.mkdir(parents=True)
    (env_dir / "persona").mkdir()
    (env_dir / "persona" / "SOUL.md").write_text("# persona\n")
    svc_dir = env_dir / "gmail-api"
    svc_dir.mkdir()
    (svc_dir / "service.toml").write_text(
        '[service]\nname = "gmail-api"\nport = 8017\nenv_var_name = "GMAIL_API_URL"\n'
    )
    wrote_d, wrote_c = rp._stage_environment_dockerfile_and_compose(bundle, verbose=False)
    assert wrote_d is True and wrote_c is True
    dockerfile_text = (env_dir / "Dockerfile").read_text()
    assert "FROM ubuntu:24.04" in dockerfile_text
    assert "COPY persona /root/.openclaw/persona" in dockerfile_text
    assert "COPY artifacts/inputs/files" not in dockerfile_text
    compose_text = (env_dir / "docker-compose.yaml").read_text()
    assert "services:" in compose_text
    assert "gmail-api:" in compose_text
    assert "litellm-proxy:4000" in compose_text


_ENV_SPECS: tuple[tuple[str, int, str], ...] = (
    ("gmail-api", 8017, "GMAIL_API_URL"),
    ("xero-api", 8087, "XERO_API_URL"),
    ("github-api", 8088, "GITHUB_API_URL"),
)


def _mk_env_services(env_dir: Path, specs=_ENV_SPECS) -> None:
    env_dir.mkdir(parents=True, exist_ok=True)
    for name, port, env_var in specs:
        sd = env_dir / name
        sd.mkdir(exist_ok=True)
        (sd / "service.toml").write_text(
            f'[service]\nname = "{name}"\nport = {port}\nenv_var_name = "{env_var}"\n'
        )


def _mk_api_task(tmp_path: Path, name: str, task_yaml: str | None,
                 mock_apis=()) -> tuple[Path, Path]:
    input_task_dir = tmp_path / name
    input_task_dir.mkdir()
    (input_task_dir / "prompt.txt").write_text("Use gmail and xero.\n")
    if task_yaml is not None:
        (input_task_dir / "task.yaml").write_text(task_yaml)
    if mock_apis:
        mock_data = input_task_dir / "mock_data"
        mock_data.mkdir()
        for api in mock_apis:
            (mock_data / api).mkdir()
    bundle = tmp_path / f"bundle_{name}"
    _mk_env_services(bundle / "data" / "environment")
    return input_task_dir, bundle


def test_task_toml_required_skills_prefers_declared_required_apis(tmp_path):
    """task.yaml `required_apis` is authoritative and WINS over the mock_data
    scan: newer tasks seed the FULL mock fleet, so the scan would otherwise
    claim every connector as required. Bare declared names ('gmail') are
    normalized to the '-api' env-dir convention ('gmail-api')."""
    rp = _load_repackage_module()
    input_task_dir, bundle = _mk_api_task(
        tmp_path, "declared",
        "difficulty: hard\ntask_type: finance_ops\n"
        "required_apis: [gmail, xero]\ndistractor_apis: auto\n",
        mock_apis=[name for name, _p, _e in _ENV_SPECS],
    )

    assert rp._stage_task_toml(input_task_dir, bundle, verbose=False)
    toml_text = (bundle / "data" / "task.toml").read_text()
    assert 'required_skills = ["gmail-api-connector", "xero-api-connector"]' in toml_text
    assert 'distractor_skills = ["github-api-connector"]' in toml_text


def test_task_toml_required_skills_falls_back_to_mock_data(tmp_path):
    """With NO `required_apis` key the mock_data/<api>/ scan stays the source of
    required_skills (legacy tasks keep working)."""
    rp = _load_repackage_module()
    input_task_dir, bundle = _mk_api_task(
        tmp_path, "fallback", "distractor_apis: auto\n",
        mock_apis=("gmail-api", "xero-api"),
    )

    assert rp._stage_task_toml(input_task_dir, bundle, verbose=False)
    toml_text = (bundle / "data" / "task.toml").read_text()
    assert 'required_skills = ["gmail-api-connector", "xero-api-connector"]' in toml_text
    assert 'distractor_skills = ["github-api-connector"]' in toml_text


def test_task_toml_distractor_skills_explicit_list_minus_required(tmp_path):
    """An explicit `distractor_apis` list publishes exactly those, minus any
    overlap with required."""
    rp = _load_repackage_module()
    input_task_dir, bundle = _mk_api_task(
        tmp_path, "explicit",
        "required_apis: [gmail]\ndistractor_apis: [github, gmail]\n",
    )

    assert rp._stage_task_toml(input_task_dir, bundle, verbose=False)
    toml_text = (bundle / "data" / "task.toml").read_text()
    assert 'required_skills = ["gmail-api-connector"]' in toml_text
    assert 'distractor_skills = ["github-api-connector"]' in toml_text


@pytest.mark.parametrize("task_yaml", [
    None,
    "required_apis: [gmail]\n",
    "required_apis: [gmail]\ndistractor_apis:\n",
    "required_apis: [gmail]\ndistractor_apis: []\n",
    "required_apis: [gmail]\ndistractor_apis: null\n",
])
def test_task_toml_distractor_skills_none_when_absent_or_empty(tmp_path, task_yaml):
    """absent / empty / null `distractor_apis` publishes NO distractors."""
    rp = _load_repackage_module()
    input_task_dir, bundle = _mk_api_task(
        tmp_path, "noneish", task_yaml, mock_apis=("gmail-api",),
    )

    assert rp._stage_task_toml(input_task_dir, bundle, verbose=False)
    toml_text = (bundle / "data" / "task.toml").read_text()
    assert 'required_skills = ["gmail-api-connector"]' in toml_text
    assert "distractor_skills = []" in toml_text


def test_task_toml_category_difficulty_keywords_from_task_yaml(tmp_path):
    """[metadata] category/difficulty + [task] keywords come from task.yaml
    task_type/difficulty. These were permanently empty before _stage_task_toml
    started forwarding them to _build_task_toml."""
    rp = _load_repackage_module()
    input_task_dir, bundle = _mk_api_task(
        tmp_path, "meta",
        "difficulty: hard\ntask_type: design_review_signoff\nrequired_apis: [gmail]\n",
    )

    assert rp._stage_task_toml(input_task_dir, bundle, verbose=False)
    toml_text = (bundle / "data" / "task.toml").read_text()
    assert 'category = "design_review_signoff"' in toml_text
    assert 'difficulty = "hard"' in toml_text
    assert 'keywords = ["design_review_signoff", "hard"]' in toml_text


def test_task_toml_category_alias_and_absent_task_yaml(tmp_path):
    """`category` is accepted as a task_type alias; a missing task.yaml
    fail-softs to empty strings and empty keywords."""
    rp = _load_repackage_module()
    aliased, bundle_a = _mk_api_task(
        tmp_path, "alias", "category: ops_qa\ndifficulty: medium\n",
    )
    assert rp._stage_task_toml(aliased, bundle_a, verbose=False)
    toml_text = (bundle_a / "data" / "task.toml").read_text()
    assert 'category = "ops_qa"' in toml_text
    assert 'keywords = ["ops_qa", "medium"]' in toml_text

    bare, bundle_b = _mk_api_task(tmp_path, "bare", None)
    assert rp._stage_task_toml(bare, bundle_b, verbose=False)
    toml_text = (bundle_b / "data" / "task.toml").read_text()
    assert 'category = ""' in toml_text
    assert 'difficulty = ""' in toml_text
    assert "keywords = []" in toml_text


def test_solve_sh_env_shrinks_for_explicit_distractor_list(tmp_path):
    """solve.sh enumerates only required union distractor. With a small explicit
    distractor list the preamble genuinely shrinks: xero is in the bundle env but
    is neither required nor a declared distractor, so it must not appear."""
    rp = _load_repackage_module()
    input_task_dir, bundle = _mk_api_task(
        tmp_path, "scoped", "required_apis: [gmail]\ndistractor_apis: [github]\n",
    )

    rp._stage_test_runners_and_solver(
        input_task_dir, bundle, verbose=False, include_tests=False
    )
    solve = (bundle / "data" / "solution" / "solve.sh").read_text()
    assert "GMAIL_API_URL" in solve
    assert "GITHUB_API_URL" in solve
    assert "XERO_API_URL" not in solve, "solve.sh published a non-task service"


def test_solve_sh_env_spans_fleet_under_distractor_auto(tmp_path):
    """Under `distractor_apis: auto` the union legitimately IS the full env
    complement, so solve.sh spans every service in the bundle. That is faithful
    to what the agent saw (auto mounts the whole complement as distractors), not
    a leak — documented here so the behavior is not "fixed" by mistake."""
    rp = _load_repackage_module()
    input_task_dir, bundle = _mk_api_task(
        tmp_path, "autoscope", "required_apis: [gmail]\ndistractor_apis: auto\n",
    )

    rp._stage_test_runners_and_solver(
        input_task_dir, bundle, verbose=False, include_tests=False
    )
    solve = (bundle / "data" / "solution" / "solve.sh").read_text()
    for env_var in ("GMAIL_API_URL", "XERO_API_URL", "GITHUB_API_URL"):
        assert env_var in solve


def test_solve_sh_env_agrees_with_task_toml_environment_env(tmp_path):
    """solve.sh and task.toml [environment.env] are derived from the SAME
    resolver, so the service env vars they name must never diverge."""
    rp = _load_repackage_module()
    input_task_dir, bundle = _mk_api_task(
        tmp_path, "agree", "required_apis: [gmail]\ndistractor_apis: [github]\n",
    )

    assert rp._stage_task_toml(input_task_dir, bundle, verbose=False)
    rp._stage_test_runners_and_solver(
        input_task_dir, bundle, verbose=False, include_tests=False
    )
    toml_text = (bundle / "data" / "task.toml").read_text()
    solve = (bundle / "data" / "solution" / "solve.sh").read_text()
    for env_var in ("GMAIL_API_URL", "GITHUB_API_URL"):
        assert f"{env_var} = " in toml_text
        assert env_var in solve
    assert "XERO_API_URL" not in toml_text
    assert "XERO_API_URL" not in solve


def test_resolve_task_api_sets_explicit_list_is_pinned(tmp_path):
    """Shared-resolver contract, pinned directly: an explicit fixture must yield
    exactly (required, distractor) with declared bare names normalized to the
    '-api' env-dir convention and required/distractor disjoint and sorted."""
    rp = _load_repackage_module()
    input_task_dir, bundle = _mk_api_task(
        tmp_path, "pinned",
        "required_apis: [xero, gmail]\ndistractor_apis: [github, gmail]\n",
    )

    required, distractor = rp._resolve_task_api_sets(
        input_task_dir, bundle / "data" / "environment"
    )
    assert required == ["gmail-api", "xero-api"]
    assert distractor == ["github-api"]


def test_block_style_required_apis_falls_back_to_mock_data(tmp_path):
    """Block-style YAML must NOT be half-parsed. A newline-permitting `\\s*`
    captured only `- figma`, silently dropping the rest and emitting a phantom
    `- figma-api-connector`. Anchored to one line the key reads as absent, so
    the documented mock_data fallback takes over."""
    rp = _load_repackage_module()
    input_task_dir, bundle = _mk_api_task(
        tmp_path, "blockstyle",
        "required_apis:\n  - gmail\n  - xero\ndistractor_apis: auto\n",
        mock_apis=("gmail-api",),
    )

    assert rp._stage_task_toml(input_task_dir, bundle, verbose=False)
    toml_text = (bundle / "data" / "task.toml").read_text()
    assert 'required_skills = ["gmail-api-connector"]' in toml_text
    assert "- gmail" not in toml_text
    assert "--api-connector" not in toml_text


def test_explicit_empty_required_apis_is_an_empty_contract(tmp_path):
    """Sibling of the block-style case: a literal same-line `required_apis: []`
    is an explicit "no APIs" declaration and must NOT fall back to mock_data,
    unlike a dangling `required_apis:` with the value on following lines."""
    rp = _load_repackage_module()
    input_task_dir, bundle = _mk_api_task(
        tmp_path, "emptycontract", "required_apis: []\n", mock_apis=("gmail-api",),
    )

    required, distractor = rp._resolve_task_api_sets(
        input_task_dir, bundle / "data" / "environment"
    )
    assert required == []
    assert distractor == []


def test_block_style_authors_yields_no_phantom_author(tmp_path):
    """Same anchoring guarantee for the pre-existing authors regex."""
    rp = _load_repackage_module()
    input_task_dir, _bundle = _mk_api_task(
        tmp_path, "blockauthors", "authors:\n  - Jane Doe\n  - John Roe\n",
    )
    assert rp._resolve_authors(input_task_dir) == []


def test_declared_apis_accept_mock_apis_aliases(tmp_path):
    """`required_mock_apis` / `distractor_mock_apis` are legacy aliases accepted
    by src/utils/task_parser.py:883-888; the bundler must honor them too or the
    task falls back to the full-fleet mock_data scan."""
    rp = _load_repackage_module()
    input_task_dir, bundle = _mk_api_task(
        tmp_path, "aliases",
        "required_mock_apis: [gmail]\ndistractor_mock_apis: [github]\n",
        mock_apis=[name for name, _p, _e in _ENV_SPECS],
    )

    assert rp._stage_task_toml(input_task_dir, bundle, verbose=False)
    toml_text = (bundle / "data" / "task.toml").read_text()
    assert 'required_skills = ["gmail-api-connector"]' in toml_text
    assert 'distractor_skills = ["github-api-connector"]' in toml_text


def test_declared_api_absent_from_bundle_env_is_dropped(tmp_path, capsys):
    """A declared api with no service in bundle/data/environment/ (author typo)
    must NOT emit a phantom `<name>-connector`; it is dropped with a stderr
    warning, mirroring run_batch.py:736-745."""
    rp = _load_repackage_module()
    input_task_dir, bundle = _mk_api_task(
        tmp_path, "phantomdrop",
        "required_apis: [gmail, notreal]\ndistractor_apis: [alsofake]\n",
    )

    assert rp._stage_task_toml(input_task_dir, bundle, verbose=False)
    toml_text = (bundle / "data" / "task.toml").read_text()
    assert 'required_skills = ["gmail-api-connector"]' in toml_text
    assert "notreal" not in toml_text
    assert "alsofake" not in toml_text
    assert "distractor_skills = []" in toml_text
    assert "notreal-api" in capsys.readouterr().err


def test_declared_apis_survive_empty_bundle_env_catalog(tmp_path):
    """The catalog intersection is skipped when the bundle env dir has no
    services (nothing to validate against), matching run_batch's `if catalog:`
    guard — otherwise every declared api would be wrongly dropped."""
    rp = _load_repackage_module()
    input_task_dir = tmp_path / "nocatalog"
    input_task_dir.mkdir()
    (input_task_dir / "task.yaml").write_text("required_apis: [gmail]\n")

    required, distractor = rp._resolve_task_api_sets(
        input_task_dir, tmp_path / "nocatalog_bundle" / "data" / "environment"
    )
    assert required == ["gmail-api"]
    assert distractor == []


def test_inline_yaml_comments_are_stripped(tmp_path):
    """Trailing `# comment` must not corrupt values. `difficulty: hard # tough`
    previously produced 'hard # tough', which the validate gate then accepted as
    non-empty. A `#` without preceding whitespace stays literal per YAML."""
    rp = _load_repackage_module()
    input_task_dir, bundle = _mk_api_task(
        tmp_path, "comments",
        "task_type: design_review  # the l2 name\n"
        "difficulty: hard # tough one\n"
        "required_apis: [gmail]  # only gmail\n"
        "distractor_apis: auto  # full complement\n",
    )

    assert rp._stage_task_toml(input_task_dir, bundle, verbose=False)
    toml_text = (bundle / "data" / "task.toml").read_text()
    assert 'category = "design_review"' in toml_text
    assert 'difficulty = "hard"' in toml_text
    assert 'keywords = ["design_review", "hard"]' in toml_text
    assert 'required_skills = ["gmail-api-connector"]' in toml_text
    assert "#" not in toml_text.split("[verifier]")[0]
    distractor_line = [
        line for line in toml_text.splitlines()
        if line.startswith("distractor_skills = ")
    ][0]
    assert "github-api-connector" in distractor_line
    assert "xero-api-connector" in distractor_line


def test_strip_yaml_comment_preserves_hash_without_whitespace():
    """Per YAML, `#` opens a comment only at value start or after whitespace."""
    rp = _load_repackage_module()
    assert rp._strip_yaml_comment("hard # tough") == "hard"
    assert rp._strip_yaml_comment("tag#1") == "tag#1"
    assert rp._strip_yaml_comment("# all comment") == ""
    assert rp._strip_yaml_comment('"a # b" # c') == '"a # b"'
    assert rp._strip_yaml_comment("[a, b]\t# c") == "[a, b]"


def test_task_toml_env_vars_and_healthcheck_chain(tmp_path):
    """task.toml [environment.env] must contain per-service URL env vars +
    runtime_env_defaults; [environment.healthcheck] command must chain
    curl probes per service."""
    rp = _load_repackage_module()
    input_task_dir = tmp_path / "task_e"
    input_task_dir.mkdir()
    (input_task_dir / "prompt.txt").write_text("Use gmail.\n")
    mock_data = input_task_dir / "mock_data"
    mock_data.mkdir()
    (mock_data / "gmail-api").mkdir()

    bundle = tmp_path / "bundle_e"
    env_dir = bundle / "data" / "environment"
    env_dir.mkdir(parents=True)
    sd = env_dir / "gmail-api"
    sd.mkdir()
    (sd / "service.toml").write_text(
        '[service]\nname = "gmail-api"\nport = 8017\nenv_var_name = "GMAIL_API_URL"\n'
    )

    assert rp._stage_task_toml(input_task_dir, bundle, verbose=False)
    toml_text = (bundle / "data" / "task.toml").read_text()
    assert 'GMAIL_API_URL = "http://gmail-api:8017"' in toml_text
    assert 'LITELLM_BASE_URL = "http://litellm-proxy:4000"' in toml_text
    assert 'TEST_DIR = "/tests"' in toml_text
    assert "curl -f http://localhost:8017/health" in toml_text


def test_convert_task_emits_all_four_harbor_files(tmp_path):
    """End-to-end: convert_task emits instruction.md + Dockerfile + docker-compose.yaml + task.toml."""
    rp = _load_repackage_module()
    src_root, dst_root, inp_root = _stage_minimal_task(tmp_path)
    task_id = "ben_cox_8fc24d4b"

    bundle = rp.convert_task(
        task_dir=src_root / task_id,
        dest_root=dst_root,
        input_root=inp_root,
        infer_meta=False,
        verbose=False,
    )
    assert bundle is not None
    for rel in (
        "data/solution/instruction.md",
        "data/environment/Dockerfile",
        "data/environment/docker-compose.yaml",
        "data/task.toml",
    ):
        assert (bundle / rel).is_file(), f"convert_task did not emit {rel}"
    assert not (bundle / "data" / "instruction.md").exists()


# ============================================================================
# Bundle-only test-surface strip: delivered bundles carry NO pytest surface,
# and report.json / pass_summary.json carry NO test-scoring keys.
# ============================================================================


def test_bundle_has_no_pytest_surface_anywhere(tmp_path):
    rp = _load_repackage_module()
    src_root, dst_root, inp_root = _stage_minimal_task(tmp_path)
    task_id = "ben_cox_8fc24d4b"

    bundle = rp.convert_task(
        task_dir=src_root / task_id,
        dest_root=dst_root,
        input_root=inp_root,
        infer_meta=False,
        verbose=False,
    )
    assert bundle is not None

    forbidden = {"test.sh", "test_outputs.py", "test_weights.json", "ctrf.json", "reward.txt"}
    offenders = [str(p.relative_to(bundle)) for p in bundle.rglob("*")
                 if p.is_file() and p.name in forbidden]
    assert not offenders, f"delivered bundle must carry no pytest surface; found: {offenders}"
    assert not any(p.is_dir() and p.name == "tests" and p.parent.name == "data"
                   for p in bundle.rglob("*")), "no data/tests/ dir anywhere in the bundle"
    assert (bundle / "data" / "solution" / "solve.sh").is_file()


def test_report_json_has_no_test_keys(tmp_path):
    rp = _load_repackage_module()
    src_root, dst_root, inp_root = _stage_minimal_task(tmp_path)
    task_id = "ben_cox_8fc24d4b"
    run_dir = src_root / task_id / "trajectories" / "claude" / "run_1"
    (run_dir / "score.json").write_text(json.dumps({
        "criteria": [],
        "rubric_weights_percentage": 42.0,
        "rubric_based_reward": 0.42,
        "combined_reward": 0.42,
    }))

    bundle = rp.convert_task(
        task_dir=src_root / task_id,
        dest_root=dst_root,
        input_root=inp_root,
        infer_meta=False,
        verbose=False,
    )
    assert bundle is not None
    report = json.loads(
        (bundle / "trajectories" / "Claude Opus 4.7" / "run_1" / "report.json").read_text()
    )
    for key in ("pytest", "test_weights_percentage", "test_channel_present"):
        assert key not in report, f"report.json must not carry {key}"
    assert "final_reward" in report and "rubric_weights_percentage" in report
    assert abs(report["final_reward"] - report["rubric_weights_percentage"]) <= 0.01


def test_pass_summary_has_no_test_keys(tmp_path):
    rp = _load_repackage_module()
    src_root, dst_root, inp_root = _stage_minimal_task(tmp_path)
    task_id = "ben_cox_8fc24d4b"
    run_dir = src_root / task_id / "trajectories" / "claude" / "run_1"
    (run_dir / "score.json").write_text(json.dumps({
        "criteria": [],
        "rubric_weights_percentage": 42.0,
        "rubric_based_reward": 0.42,
        "combined_reward": 0.42,
    }))

    bundle = rp.convert_task(
        task_dir=src_root / task_id,
        dest_root=dst_root,
        input_root=inp_root,
        infer_meta=False,
        verbose=False,
    )
    assert bundle is not None
    summary = json.loads(
        (bundle / "trajectories" / "Claude Opus 4.7" / "pass_summary.json").read_text()
    )
    assert "average_test_weights_percentage" not in summary
    for rec in summary.get("per_run", []):
        assert "test_weights_percentage" not in rec
    assert "average_combined_score" in summary
    assert "average_rubric_weights_percentage" in summary


def test_task_toml_authors_from_task_yaml(tmp_path):
    """[task].authors is sourced from input/<task>/task.yaml, stdlib-only."""
    rp = _load_repackage_module()
    input_task_dir = tmp_path / "task_authors"
    input_task_dir.mkdir()
    (input_task_dir / "prompt.txt").write_text("Do the thing.\n")
    (input_task_dir / "task.yaml").write_text(
        "difficulty: hard\nauthor: Jane Doe\nl1: ops\nl2: qa\n", encoding="utf-8"
    )
    bundle = tmp_path / "bundle_authors"
    (bundle / "data" / "environment").mkdir(parents=True)

    assert rp._stage_task_toml(input_task_dir, bundle, verbose=False)
    toml_text = (bundle / "data" / "task.toml").read_text()
    assert 'authors = [{ name = "Jane Doe" }]' in toml_text
    # Harbor field order: authors sits between description and keywords.
    assert toml_text.find("description =") < toml_text.find("authors =")
    assert toml_text.find("authors =") < toml_text.find("keywords =")


def test_task_toml_authors_absent_degrades_to_empty(tmp_path):
    """No author key (today's corpus) must emit `authors = []`, never break."""
    rp = _load_repackage_module()
    input_task_dir = tmp_path / "task_noauthor"
    input_task_dir.mkdir()
    (input_task_dir / "prompt.txt").write_text("Do the thing.\n")
    (input_task_dir / "task.yaml").write_text("difficulty: hard\nl1: ops\n", encoding="utf-8")
    bundle = tmp_path / "bundle_noauthor"
    (bundle / "data" / "environment").mkdir(parents=True)

    assert rp._stage_task_toml(input_task_dir, bundle, verbose=False)
    assert "authors = []" in (bundle / "data" / "task.toml").read_text()


def test_resolve_authors_accepts_inline_sequence_and_is_fail_soft(tmp_path):
    rp = _load_repackage_module()
    d = tmp_path / "y"
    d.mkdir()
    (d / "task.yaml").write_text("authors: [Ada L, 'Grace H']\n", encoding="utf-8")
    assert rp._resolve_authors(d) == ["Ada L", "Grace H"]
    assert rp._resolve_authors(tmp_path / "missing") == []
    assert rp._resolve_authors(None) == []


def test_malformed_connector_skill_predicate_scoped_to_connectors(tmp_path):
    """Only `*-api-connector` dirs missing SKILL.md/references/ are malformed.

    Non-connector skills legitimately have no references/ and MUST pass.
    A missing scripts/ must NOT mark a connector malformed -- legacy output
    trees lack it and _backfill_skill_scripts_from_baseline repairs that.
    """
    rp = _load_repackage_module()
    skills = tmp_path / "skills"
    skills.mkdir()

    good = skills / "github-api-connector"
    (good / "references").mkdir(parents=True)
    (good / "scripts").mkdir()
    (good / "SKILL.md").write_text("---\nname: github-api-connector\n---\n")

    no_scripts = skills / "gmail-api-connector"
    (no_scripts / "references").mkdir(parents=True)
    (no_scripts / "SKILL.md").write_text("---\nname: gmail-api-connector\n---\n")

    bad = skills / "canvas-lms-api-connector"
    bad.mkdir()
    (bad / "SKILL.md").write_text("---\nname: canvas-lms-api-connector\n---\n")

    non_connector = skills / "pdf-extract"
    (non_connector / "scripts").mkdir(parents=True)
    (non_connector / "SKILL.md").write_text("---\nname: pdf-extract\n---\n")

    assert rp._is_malformed_connector_skill(bad) is True
    assert rp._is_malformed_connector_skill(good) is False
    assert rp._is_malformed_connector_skill(no_scripts) is False
    assert rp._is_malformed_connector_skill(non_connector) is False

    ignored = rp._bundle_data_ignore(str(skills), sorted(p.name for p in skills.iterdir()))
    assert "canvas-lms-api-connector" in ignored
    assert "github-api-connector" not in ignored
    assert "gmail-api-connector" not in ignored
    assert "pdf-extract" not in ignored


def test_malformed_connector_not_copied_into_bundle(tmp_path):
    """End-to-end: the copytree ignore drops the malformed connector."""
    rp = _load_repackage_module()
    src_skills = tmp_path / "src" / "environment" / "skills"
    src_skills.mkdir(parents=True)
    for name in ("github-api-connector", "canvas-lms-api-connector"):
        d = src_skills / name
        d.mkdir()
        (d / "SKILL.md").write_text(f"---\nname: {name}\n---\n")
    (src_skills / "github-api-connector" / "references").mkdir()

    import shutil as _sh
    dest = tmp_path / "dst"
    _sh.copytree(tmp_path / "src", dest, ignore=rp._bundle_data_ignore)

    out = dest / "environment" / "skills"
    assert (out / "github-api-connector").is_dir()
    assert not (out / "canvas-lms-api-connector").exists()


# ============================================================================
# CURRENT_DATE parity. repackage_to_bundle is deliberately isolated from the
# eval package, so its sim-clock port must stay byte-identical to Harbor's —
# a drift here silently ships two bundles that disagree about "today".
# ============================================================================

_PROMPTS_SCHEMAS = [
    ({"timezone": "America/Chicago",
      "turns": [{"turn": "T0", "timestamp": "2026-11-03T07:38:00-06:00"}]},
     "2026-11-03"),
    ({"window": {"start": "2026-09-14", "timezone": "America/New_York"},
      "turns": [{"turn": "T0", "day": 2, "time": "09:15"}]},
     "2026-09-15"),
    ({"turns": [{"turn": "T0"}]}, "2026-05-28"),
    ({"turns": []}, "2026-05-28"),
    ({"timezone": "Not/AZone",
      "turns": [{"turn": "T0", "day": 1, "time": "09:15"}]}, "2026-05-28"),
]


@pytest.mark.parametrize("payload,expected", _PROMPTS_SCHEMAS)
def test_current_date_parity_with_harbor(tmp_path, payload, expected):
    rp = _load_repackage_module()
    from src.utils.harbor.compose import resolve_current_date

    task_dir = tmp_path / "task"
    task_dir.mkdir()
    (task_dir / "prompts.json").write_text(json.dumps(payload), encoding="utf-8")

    local = rp._compose_current_date(task_dir)
    assert local == expected
    assert local == resolve_current_date(task_dir), "CURRENT_DATE emitter drift"


def test_current_date_falls_back_without_task_dir():
    rp = _load_repackage_module()
    from src.utils.harbor.compose import resolve_current_date

    assert rp._compose_current_date(None) == "2026-05-28"
    assert rp._compose_current_date(None) == resolve_current_date(None)


def _two_service_env(tmp_path):
    env_dir = tmp_path / "env"
    env_dir.mkdir()
    for name, port, env_var in (
        ("gmail-api", 8017, "GMAIL_API_URL"),
        ("xero-api", 8087, "XERO_API_URL"),
    ):
        svc_dir = env_dir / name
        svc_dir.mkdir()
        (svc_dir / "service.toml").write_text(
            f'[service]\nname = "{name}"\nport = {port}\n'
            f'env_var_name = "{env_var}"\nhealthcheck_path = "/health"\n'
        )
    return env_dir


def test_compose_byte_equal_with_harbor_when_task_dir_supplied(tmp_path):
    rp = _load_repackage_module()
    harbor_compose = _load_harbor_compose_gen()
    env_dir = _two_service_env(tmp_path)
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    (task_dir / "prompts.json").write_text(json.dumps({
        "timezone": "America/Chicago",
        "turns": [{"turn": "T0", "timestamp": "2026-11-03T07:38:00-06:00"}],
    }), encoding="utf-8")

    harbor = harbor_compose(env_dir, task_dir=task_dir)
    local = rp._generate_environment_compose(env_dir, task_dir=task_dir)
    assert harbor == local, "compose byte-equal drift with derived CURRENT_DATE"
    assert "      - CURRENT_DATE=2026-11-03\n" in local


def test_staged_compose_uses_input_task_narrative_date(tmp_path):
    rp = _load_repackage_module()
    bundle = tmp_path / "bundle"
    env_dir = bundle / "data" / "environment"
    env_dir.mkdir(parents=True)
    (env_dir / "gmail-api").mkdir()
    (env_dir / "gmail-api" / "service.toml").write_text(
        '[service]\nname = "gmail-api"\nport = 8017\n'
        'env_var_name = "GMAIL_API_URL"\nhealthcheck_path = "/health"\n'
    )
    input_task_dir = tmp_path / "input_task"
    input_task_dir.mkdir()
    (input_task_dir / "prompts.json").write_text(json.dumps({
        "timezone": "America/Chicago",
        "turns": [{"turn": "T0", "timestamp": "2026-11-03T07:38:00-06:00"}],
    }), encoding="utf-8")

    rp._stage_environment_dockerfile_and_compose(bundle, False, input_task_dir)
    text = (env_dir / "docker-compose.yaml").read_text()
    assert "      - CURRENT_DATE=2026-11-03\n" in text

    rp._stage_environment_dockerfile_and_compose(bundle, False)
    assert "      - CURRENT_DATE=2026-05-28\n" in (
        env_dir / "docker-compose.yaml").read_text()


# ---- _build_rubric_block abstention marker ----
#
# A criterion the judge council could not resolve is stored
# resolved_by="human_eval" and contributes 0 to the reward numerator. Bundling
# flattens it to passed=false, which for a NEGATIVE weight is arithmetically
# identical to "the violation happened" — so a whole-run council failure ships
# as a pile of penalties. The "abstained" marker keeps the two distinguishable
# (see script/check_negative_semantics.py). The key is emitted ONLY when true,
# so the report.json schema stays byte-identical for every existing run.


def _score_criterion(cid, weight, satisfied, passed, resolved_by="unanimous"):
    return {
        "id": cid,
        "weight": weight,
        "satisfied": satisfied,
        "passed": passed,
        "resolved_by": resolved_by,
        "human_eval": "required" if resolved_by == "human_eval" else "",
        "criterion": f"criterion {cid + 1}",
        "is_positive": weight >= 0,
        "rationale": f"rationale {cid + 1}",
    }


def test_build_rubric_block_marks_human_eval_criteria_abstained():
    rp = _load_repackage_module()
    score = {"criteria": [
        _score_criterion(0, 5, True, True),
        _score_criterion(1, -3, False, False, resolved_by="human_eval"),
    ]}
    rubric = rp._build_rubric_block(score, infer_meta=False)
    assert "abstained" not in rubric[0]
    assert rubric[1]["abstained"] is True
    assert rubric[1]["passed"] is False
    assert rubric[1]["score"] == -3


def test_build_rubric_block_omits_abstained_key_when_no_abstentions():
    """Schema stability: resolved criteria must not gain the key at all, so
    existing report.json consumers see byte-identical entries."""
    rp = _load_repackage_module()
    score = {"criteria": [
        _score_criterion(0, 5, True, True),
        _score_criterion(1, 3, False, False, resolved_by="sonnet"),
        _score_criterion(2, -3, False, True),
    ]}
    rubric = rp._build_rubric_block(score, infer_meta=False)
    assert all("abstained" not in item for item in rubric)
    assert [item["passed"] for item in rubric] == [True, False, True]
    assert rubric[1]["justification"] == "rationale 2"


def test_build_rubric_block_detects_abstention_via_human_eval_required():
    """grading.py writes resolved_by AND human_eval together; either alone is
    enough to identify the abstention."""
    rp = _load_repackage_module()
    score = {"criteria": [dict(_score_criterion(0, 5, False, False),
                               resolved_by="", human_eval="required")]}
    assert rp._build_rubric_block(score, infer_meta=False)[0]["abstained"] is True


def test_build_rubric_block_abstained_entry_keeps_key_order():
    rp = _load_repackage_module()
    score = {"criteria": [
        _score_criterion(0, -5, False, False, resolved_by="human_eval")]}
    item = rp._build_rubric_block(score, infer_meta=False)[0]
    assert list(item) == [
        "number", "criterion", "type", "evaluation_target", "importance",
        "score", "is_positive", "passed", "abstained", "justification",
    ]


def test_whole_run_abstention_is_marked_on_every_criterion():
    """The shipped failure mode: the council died, every criterion abstained,
    and the two negative weights read as -8 points of penalty downstream."""
    rp = _load_repackage_module()
    weights = [5, 3, -3, -5, 5]
    score = {"criteria": [
        _score_criterion(i, w, False, False, resolved_by="human_eval")
        for i, w in enumerate(weights)
    ]}
    rubric = rp._build_rubric_block(score, infer_meta=False)
    assert all(item["abstained"] is True for item in rubric)
    assert all(item["passed"] is False for item in rubric)
