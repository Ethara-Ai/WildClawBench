"""script/run.sh must carry the judge lane to everything it launches.

R1, the sharpest edge in this feature: a -P N worker is a fresh `bash $SELF`
that re-runs parse_args, and JUDGE_PROVIDER is a shell global, not an exported
variable. Omit one line from the wargs array and the parent grades on one
provider while all N children silently grade on the agent's -- a whole batch
mis-laned, with no error anywhere, which is exactly the class of failure the
evidence-budget bug was.

Two mechanisms carry it (the flag in wargs, and the exported env var picked up
by the child's own parse), and both are asserted, because relying on
inheritance alone breaks the moment anything scrubs the environment.

These are source-level assertions. Running run.sh needs Docker, a sidecar and
real credentials; the thing that must not regress is textual, so it is checked
textually -- plus `bash -n`, which proves the file still parses.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
RUN_SH = REPO / "script" / "run.sh"
SRC = RUN_SH.read_text(encoding="utf-8")


def _block(header_re: str, *, lines: int = 60) -> str:
    m = re.search(header_re, SRC, re.M)
    assert m, f"anchor not found: {header_re}"
    start = SRC[: m.start()].count("\n")
    return "\n".join(SRC.splitlines()[start:start + lines])


def test_run_sh_parses():
    assert subprocess.run(["bash", "-n", str(RUN_SH)]).returncode == 0


def test_flag_is_parsed_and_validated():
    block = _block(r"^\s+--judge-provider\)")
    assert "oauth|bedrock) JUDGE_PROVIDER=" in block
    assert "must be oauth or bedrock" in block
    assert "exit 2" in block


def test_wargs_forwards_the_judge_provider():
    """THE R1 test. Without this line a -P N batch is silently mis-laned."""
    block = _block(r"local -a wargs=\(")
    assert re.search(
        r'\[\[ -n "\$JUDGE_PROVIDER" \]\] && wargs\+=\(--judge-provider "\$JUDGE_PROVIDER"\)',
        block,
    ), "wargs does not forward --judge-provider to parallel workers"


def test_per_task_cmd_forwards_the_python_flag():
    block = _block(r"local cmd=\(\s*\n\s*python3 eval/run_batch\.py")
    assert re.search(
        r'\[\[ -n "\$JUDGE_PROVIDER" \]\] && cmd\+=\(--judge-auth-provider "\$JUDGE_PROVIDER"\)',
        block,
    )


def test_env_var_is_exported_before_bootstrap_and_before_the_fan_out():
    """bootstrap_sidecar.py is a SUBPROCESS and reads the lane from the
    environment to decide whether the cc-bridge is needed for a judge the agent
    does not share, so the export must precede it."""
    export_at = SRC.index('export WCB_JUDGE_AUTH_PROVIDER="$JUDGE_PROVIDER"')
    main_at = SRC.index("main() {")
    bootstrap_at = SRC.index("bootstrap_shared_sidecar || exit 1")
    fanout_at = SRC.index("        run_parallel_tasks\n        exit $?")
    assert main_at < export_at < bootstrap_at
    assert export_at < fanout_at


def test_an_exported_judge_lane_is_honoured_and_logged():
    block = _block(r'if \[\[ -n "\$JUDGE_PROVIDER" \]\]; then\s*\n\s*export WCB_JUDGE_AUTH_PROVIDER')
    assert 'elif [[ -n "${WCB_JUDGE_AUTH_PROVIDER:-}" ]]' in block
    assert "judge lane from environment" in block


def test_bridge_url_is_exported_when_either_lane_is_oauth():
    block = _block(r'if \[\[ -n "\$WCB_SHARED_CC_BRIDGE_HOST_URL" \]\]')
    assert '"${USE_CLAUDE_OAUTH:-0}" == "1"' in block
    assert '"$JUDGE_PROVIDER" == "oauth"' in block
    assert "export KENSEI_JUDGE_OAUTH_BRIDGE_URL" in block
    assert "Sonnet judge -> Bedrock" in block, (
        "a mixed run must say which lane the judge is on, not imply the bridge"
    )


def test_regrade_forwards_the_judge_flag_instead_of_rejecting_it():
    """run_regrade parses its own argument list and rejects anything it does not
    know, so `--regrade DIR --judge-provider bedrock` used to exit 2 -- the
    literal first validation command for this whole feature."""
    block = _block(r"^run_regrade\(\) \{", lines=80)
    assert "--judge-auth-provider)" in block
    assert 'judge_override="${2:-}"' in block
    assert 'cmd+=(--judge-auth-provider "$judge_override")' in block
    assert 'REGRADE_EXTRA+=(--judge-auth-provider "$JUDGE_PROVIDER")' in SRC


def test_preflight_checks_both_lanes():
    block = _block(r"^preflight_env_file\(\) \{", lines=50)
    assert "JUDGE_COUNCIL_SONNET_ARN" in block
    assert "WCB_CC_BRIDGE_SECRET" in block
    assert "JUDGE_MAX_EVIDENCE" in block, (
        "JUDGE_MAX_EVIDENCE short-circuits the family budget on both lanes and "
        "would silently defeat the oversize-transcript rescue"
    )


def test_help_documents_the_flag():
    assert "--judge-provider P" in SRC
    assert "WCB_JUDGE_AUTH_PROVIDER" in SRC


def test_teardown_does_not_unset_the_lane_variable():
    """It is a run-scoped SELECTION, not bridge state. Unsetting it in the
    sidecar teardown would conflate a lane choice with an infra lifetime."""
    block = _block(r"^teardown_shared_sidecar\(\) \{", lines=45)
    assert "unset WCB_JUDGE_AUTH_PROVIDER" not in block


# ---------------------------------------------------------------------------
# The python flag exists and matches
# ---------------------------------------------------------------------------


def test_run_batch_accepts_the_flag_run_sh_sends():
    sys.path.insert(0, str(REPO))
    from src.utils.cli_args import build_run_batch_parser

    parser = build_run_batch_parser("claude-opus-4.7", 1)
    args = parser.parse_args(["--task", "x", "--judge-auth-provider", "bedrock"])
    assert args.judge_auth_provider == "bedrock"
    args = parser.parse_args(["--task", "x"])
    assert args.judge_auth_provider is None


def test_run_batch_rejects_a_typo():
    sys.path.insert(0, str(REPO))
    from src.utils.cli_args import build_run_batch_parser

    with pytest.raises(SystemExit):
        build_run_batch_parser("claude-opus-4.7", 1).parse_args(
            ["--task", "x", "--judge-auth-provider", "bedrok"])


def test_tui_clears_a_stale_judge_lane():
    """Oracle RECOMMENDED R-f: the TUI has no dual-provider affordance, so an
    exported WCB_JUDGE_AUTH_PROVIDER would split every launch from it silently."""
    sys.path.insert(0, str(REPO))
    from src.utils.ui.launcher import provider_env_overrides

    env = provider_env_overrides({"auth_provider": "bedrock"})
    assert env["WCB_JUDGE_AUTH_PROVIDER"] == ""
    env = provider_env_overrides(
        {"auth_provider": "oauth", "oauth_account": "/pool/a.json",
         "judge_auth_provider": "bedrock"}
    )
    assert env["WCB_JUDGE_AUTH_PROVIDER"] == "bedrock"
