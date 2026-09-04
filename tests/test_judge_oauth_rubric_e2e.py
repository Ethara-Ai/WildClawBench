"""End-to-end integration test: grade a rubric with the Sonnet judge routed
through the Claude-Max OAuth cc-bridge pathway (NOT Bedrock).

Unlike the pure-unit `test_claude_oauth_bridge.py`, this boots the REAL bridge
as its uvicorn server subprocess (`python -m src.utils.claude_oauth`) against a
fake Anthropic upstream, then drives `grading.grade_with_rubric()` through the
full host -> cc-bridge -> upstream route over real TCP:

    grade_with_rubric()                     src/utils/grading.py
      -> _call_one_judge(family="sonnet")   KENSEI_JUDGE_USE_LITELLM=1
      -> judge_litellm.call_judge_via_litellm(...)
      -> litellm.completion(model="anthropic/...", api_base=bridge_url)
      -> POST {bridge}/v1/messages          REAL uvicorn cc-bridge subprocess
      -> POST {fake-anthropic}/v1/messages  fake upstream (this process)

The fake upstream returns an Anthropic Messages response whose text body carries
Yes/No verdict blocks in the exact `grading._VERDICT_RE` shape, so we can assert
per-criterion parsing and the weighted score. Under the OAuth auth provider only
the `sonnet` council family survives (Kimi/GLM would hit Bedrock), so this
exercises the Sonnet-only OAuth council. No real Anthropic quota is spent.

This is an INTEGRATION test: it needs `litellm` + `httpx` importable and the
bridge subprocess to boot. When any of those is unavailable the module skips
cleanly so `pytest tests/` stays green on machines without the full stack.

Run it explicitly:
    pytest tests/test_judge_oauth_rubric_e2e.py -q -s
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

pytest.importorskip("httpx")
pytest.importorskip("litellm")

import httpx  # noqa: E402

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

from src.utils import grading, judge_litellm  # noqa: E402

TOKEN_A = "sk-oat-e2e-account-A-00000000001"
BRIDGE_SECRET = "e2e-bridge-secret-judge-0001"
STUB_KEY = "sk-wcb-oauth-stub"

RUBRICS = [
    {"criterion": "The report identifies the top revenue-driving region.", "weight": 5},
    {"criterion": "The report includes a year-over-year growth chart.", "weight": 3},
    {"criterion": "The report lists at least three actionable recommendations.", "weight": 1},
]
EXPECTED_VERDICTS = [True, False, True]

TASK = "Produce a sales analysis report from the provided quarterly data."
DELIVERABLE = (
    "# Q3 Sales Analysis\n\n"
    "Top revenue-driving region: EMEA (42% of total).\n\n"
    "## Recommendations\n"
    "1. Expand EMEA headcount.\n"
    "2. Localise pricing for APAC.\n"
    "3. Bundle the analytics add-on.\n"
)
TRANSCRIPT = "user: build the report\nassistant: wrote results/report.md\n"


def _verdict_block(rubrics: list[dict], verdicts: list[bool]) -> str:
    lines = []
    for i, (r, ok) in enumerate(zip(rubrics, verdicts), start=1):
        sat = "Yes" if ok else "No"
        lines.append(
            f"{i}. {r['criterion']} "
            f"[[RATIONALE: mock judge rationale for criterion {i}]] "
            f"[[SATISFIED: {sat}]] "
            f"[[TRUNCATION_AFFECTED: No]]"
        )
    return "\n".join(lines)


_JUDGE_TEXT = _verdict_block(RUBRICS, EXPECTED_VERDICTS)


def _make_fake_anthropic(captured: list[dict]) -> type[BaseHTTPRequestHandler]:
    response_payload = {
        "id": "msg_e2e",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-5",
        "content": [{"type": "text", "text": _JUDGE_TEXT}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 128, "output_tokens": 64},
    }

    class _FakeAnthropic(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            raw_request = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            try:
                request_json = json.loads(raw_request or b"{}")
            except json.JSONDecodeError:
                request_json = {"_unparsed": raw_request.decode("utf-8", "replace")}
            captured.append({
                "path": self.path,
                "request": request_json,
                "response": response_payload,
            })
            body = json.dumps(response_payload).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    return _FakeAnthropic


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture()
def oauth_bridge(monkeypatch):
    """Boot the real cc-bridge subprocess against a fake Anthropic upstream and
    configure the judge to route Sonnet through it under the OAuth provider.

    Yields (bridge_url, captured) where `captured` is a list the fake upstream
    appends each forwarded request+response to, so tests can inspect the raw
    input prompt the judge received and the raw response it returned. Skips the
    test if the bridge never becomes healthy (e.g. uvicorn/fastapi missing)."""
    captured: list[dict] = []
    up = ThreadingHTTPServer(("127.0.0.1", 0), _make_fake_anthropic(captured))
    threading.Thread(target=up.serve_forever, daemon=True).start()
    up_url = f"http://127.0.0.1:{up.server_address[1]}"

    pool = Path(tempfile.mkdtemp(prefix="wcb_e2e_pool_"))
    account = pool / "account_a.json"
    account.write_text(json.dumps({"claudeAiOauth": {
        "accessToken": TOKEN_A,
        "refreshToken": "rt_" + TOKEN_A,
        "expiresAt": int((time.time() + 86400) * 1000),
        "scopes": ["user:inference"],
        "subscriptionType": "max",
    }}))

    bridge_port = _free_port()
    bridge_env = dict(os.environ)
    bridge_env.update({
        "WCB_CC_ACCOUNT_POOL": str(account),
        "WCB_CC_UPSTREAM": up_url,
        "WCB_CC_BRIDGE_SECRET": BRIDGE_SECRET,
        "PYTHONPATH": str(_REPO),
    })
    bridge = subprocess.Popen(
        [sys.executable, "-m", "src.utils.claude_oauth", "--port", str(bridge_port),
         "--log-level", "warning"],
        env=bridge_env, cwd=str(_REPO),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    bridge_url = f"http://127.0.0.1:{bridge_port}"

    healthy = False
    for _ in range(50):
        if bridge.poll() is not None:
            break
        try:
            if httpx.get(f"{bridge_url}/healthz", timeout=1).status_code == 200:
                healthy = True
                break
        except httpx.HTTPError:
            time.sleep(0.2)

    if not healthy:
        bridge.terminate()
        try:
            bridge.wait(timeout=5)
        except subprocess.TimeoutExpired:
            bridge.kill()
        up.shutdown()
        pytest.skip("cc-bridge subprocess did not become healthy "
                    "(uvicorn/fastapi missing?) — integration test skipped")

    for k, v in {
        "KENSEI_JUDGE_USE_LITELLM": "1",
        "KENSEI_JUDGE_OAUTH_BRIDGE_URL": bridge_url,
        "KENSEI_JUDGE_OAUTH_BRIDGE_MODEL": "anthropic/claude-sonnet-5",
        "WCB_CC_BRIDGE_SECRET": BRIDGE_SECRET,
        "WCB_CC_STUB_KEY": STUB_KEY,
        "JUDGE_COUNCIL": "1",
        "JUDGE_COUNCIL_SONNET_ARN":
            "bedrock/arn:aws:bedrock:us-east-1:000000000000:application-inference-profile/e2e",
        "WCB_AUTH_PROVIDER": "oauth",
        "WCB_USE_CLAUDE_OAUTH": "1",
        "WCB_CC_ACCOUNT_POOL": str(account),
    }.items():
        monkeypatch.setenv(k, v)
    for stale in ("JUDGE_COUNCIL_MEMBERS", "JUDGE_COUNCIL_GLM_ARN", "JUDGE_COUNCIL_KIMI_ARN"):
        monkeypatch.delenv(stale, raising=False)

    try:
        yield bridge_url, captured
    finally:
        bridge.terminate()
        try:
            bridge.wait(timeout=5)
        except subprocess.TimeoutExpired:
            bridge.kill()
        up.shutdown()


def test_preflight_judge_oauth_succeeds(oauth_bridge):
    _bridge_url, _captured = oauth_bridge
    ok, detail = judge_litellm.preflight_judge_oauth(timeout_s=30.0)
    assert ok, f"preflight failed: {detail}"


def test_council_is_sonnet_only_under_oauth(oauth_bridge):
    _bridge_url, _captured = oauth_bridge
    members = grading.council_members()
    assert [m.family for m in members] == ["sonnet"]


def test_grade_rubric_via_oauth_sonnet_judge(oauth_bridge):
    _bridge_url, captured = oauth_bridge
    workspace = Path(tempfile.mkdtemp(prefix="wcb_e2e_ws_"))
    results = workspace / "results"
    results.mkdir()
    (results / "report.md").write_text(DELIVERABLE)

    scores = grading.grade_with_rubric(
        rubrics=RUBRICS,
        task_description=TASK,
        workspace_results=workspace,
        transcript_text=TRANSCRIPT,
    )

    assert not scores.get("error"), scores.get("error")

    verdicts = [bool(c.get("passed")) for c in (scores.get("criteria") or [])]
    assert verdicts == EXPECTED_VERDICTS

    assert scores.get("criteria_total") == 3
    assert scores.get("criteria_passed") == 2
    assert scores.get("criteria_failed") == 1

    assert round(float(scores["overall_score"]), 4) == round(6 / 9, 4)
    assert scores.get("judge_model") == "council"


def test_show_raw_judge_prompt_and_response(oauth_bridge, capsys):
    """Grade the rubric, then print the RAW input prompt the Sonnet judge
    received (the Anthropic Messages request the bridge forwarded) and the RAW
    response text it returned. Run with -s to see it:

        pytest tests/test_judge_oauth_rubric_e2e.py::test_show_raw_judge_prompt_and_response -s
    """
    _bridge_url, captured = oauth_bridge
    workspace = Path(tempfile.mkdtemp(prefix="wcb_e2e_raw_ws_"))
    results = workspace / "results"
    results.mkdir()
    (results / "report.md").write_text(DELIVERABLE)

    grading.grade_with_rubric(
        rubrics=RUBRICS,
        task_description=TASK,
        workspace_results=workspace,
        transcript_text=TRANSCRIPT,
    )

    assert captured, "fake upstream received no request from the judge"
    exchange = captured[-1]
    request = exchange["request"]
    response = exchange["response"]

    system_prompt = request.get("system")
    messages = request.get("messages", [])
    response_text = response["content"][0]["text"]

    with capsys.disabled():
        print("\n" + "=" * 78)
        print(f"POST {exchange['path']}   model={request.get('model')!r}  "
              f"max_tokens={request.get('max_tokens')}")
        print("=" * 78)
        print("\n----- RAW INPUT: system prompt given to Sonnet -----")
        print(json.dumps(system_prompt, indent=2, ensure_ascii=False)
              if not isinstance(system_prompt, str) else system_prompt)
        print("\n----- RAW INPUT: messages given to Sonnet -----")
        print(json.dumps(messages, indent=2, ensure_ascii=False))
        print("\n----- RAW OUTPUT: response text returned by Sonnet -----")
        print(response_text)
        print("\n----- RAW OUTPUT: full Anthropic response envelope -----")
        print(json.dumps(response, indent=2, ensure_ascii=False))
        print("=" * 78 + "\n")

    assert messages, "no messages were forwarded to the judge"
    assert response_text.strip()
