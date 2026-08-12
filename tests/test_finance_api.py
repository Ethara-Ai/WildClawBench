from __future__ import annotations

import json
import sys
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("httpx")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.utils import finance_api  # noqa: E402
from src.utils.finance_api import (  # noqa: E402
    CREATE_PATH,
    FinanceSettings,
    append_to_sink,
    build_judge_lines,
    build_trajectory_usage_payload,
    post_trajectory_usage,
    record_trajectory_usage,
    resolve_settings,
)
from src.utils.oauth_pricing import estimate_cost_usd  # noqa: E402

PAYLOAD_KEYS = {
    "project_id",
    "project_type",
    "task_id",
    "trajectory_id",
    "team_type",
    "budget_type",
    "rfp_sub_type",
    "production_mode",
    "is_phase_based",
    "generated_at",
    "model_name",
    "trajectory_input_tokens",
    "trajectory_output_tokens",
    "trajectory_input_cache_tokens",
    "trajectory_output_cache_tokens",
    "trajectory_cost_usd",
    "subscription_id",
    "judge_lines",
}

JUDGE_LINE_KEYS = {
    "model_name",
    "judge_input_tokens",
    "judge_output_tokens",
    "judge_input_cache_tokens",
    "judge_output_cache_tokens",
    "judge_cost_usd",
}

USAGE = {
    "input_tokens": 999999,
    "output_tokens": 999999,
    "sources": {
        "agent": {
            "input_tokens": 1200,
            "output_tokens": 340,
            "cache_read_tokens": 88,
            "cache_write_tokens": 12,
            "cost_usd": 1.2345,
        },
        "judge": {
            "input_tokens": 500,
            "output_tokens": 60,
            "cost_usd": 0.75,
            "per_member": {
                "sonnet": {
                    "model": "claude-sonnet-4-6",
                    "input_tokens": 300,
                    "output_tokens": 40,
                    "cache_read_tokens": 10,
                    "cache_write_tokens": 5,
                    "cost_usd": 0.5,
                },
                "glm": {
                    "model": "glm-4.6",
                    "input_tokens": 200,
                    "output_tokens": 20,
                    "cost_usd": 0.25,
                },
            },
        },
    },
}


class _FakeResp:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text


class _FakeClient:
    responses: list = []
    posted: list = []

    def __init__(self, *a, **k):
        _FakeClient.posted.append({"init": k})

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, url, json=None, headers=None, **k):
        _FakeClient.posted.append({"url": url, "json": json, "headers": headers or {}})
        item = _FakeClient.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def patch_httpx(monkeypatch):
    _FakeClient.responses = []
    _FakeClient.posted = []
    monkeypatch.setattr(finance_api.httpx, "Client", _FakeClient)
    return _FakeClient


def _config(**overrides):
    base = {
        "finance_api_url": "",
        "finance_api_token": "",
        "finance_api_timeout": 15.0,
        "finance_project_id": "",
        "finance_project_type": "",
        "finance_team_type": "",
        "finance_budget_type": "",
        "finance_rfp_sub_type": "",
        "finance_production_mode": "",
        "finance_subscription_id": "",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _enabled(**overrides):
    base = {
        "base_url": "https://odoo.example.com/api/v1",
        "project_id": "PRJ-512",
        "project_type": "Technical",
        "team_type": "Projects",
        "budget_type": "RFP",
        "rfp_sub_type": "Testing",
        "subscription_id": "SUB-98765",
    }
    base.update(overrides)
    return FinanceSettings(**base)


def test_reporting_is_off_when_no_url_is_configured():
    assert FinanceSettings().enabled is False
    assert resolve_settings(_config()).enabled is False


def test_endpoint_matches_documented_path():
    settings = _enabled(base_url="https://odoo.example.com/api/v1/")
    assert settings.endpoint == f"https://odoo.example.com/api/v1/{CREATE_PATH}"


@pytest.mark.parametrize(
    "raw",
    [
        "projects-stage.ethara.ai",
        "https://projects-stage.ethara.ai",
        "https://projects-stage.ethara.ai/api/v1",
        "https://projects-stage.ethara.ai/api/v1/",
        "  projects-stage.ethara.ai  ",
    ],
)
def test_bare_hostname_is_normalised_to_a_postable_url(raw):
    # httpx rejects a scheme-less URL outright, and because posting is
    # fail-open that rejection would silently disable reporting.
    settings = _enabled(base_url=raw)
    assert settings.endpoint == (
        f"https://projects-stage.ethara.ai/api/v1/{CREATE_PATH}"
    )


def test_normalisation_preserves_an_explicit_mount_path():
    settings = _enabled(base_url="http://localhost:8069/custom/mount")
    assert settings.endpoint == f"http://localhost:8069/custom/mount/{CREATE_PATH}"


def test_normalisation_leaves_an_empty_url_disabled():
    assert _enabled(base_url="   ").enabled is False


def test_cli_overrides_env_config():
    config = _config(finance_api_url="https://from-env/api/v1", finance_project_id="PRJ-ENV")
    args = Namespace(finance_api_url="https://from-cli/api/v1", finance_project_id="PRJ-CLI")
    settings = resolve_settings(config, args)
    assert settings.base_url == "https://from-cli/api/v1"
    assert settings.project_id == "PRJ-CLI"


def test_env_config_used_when_cli_absent():
    config = _config(finance_api_url="https://from-env/api/v1", finance_project_id="PRJ-ENV")
    args = Namespace(finance_api_url=None, finance_project_id=None)
    settings = resolve_settings(config, args)
    assert settings.base_url == "https://from-env/api/v1"
    assert settings.project_id == "PRJ-ENV"


def test_account_uuid_fills_blank_subscription_id():
    settings = resolve_settings(
        _config(finance_api_url="https://x"), subscription_id="c5802e58-uuid"
    )
    assert settings.subscription_id == "c5802e58-uuid"


def test_configured_subscription_id_beats_account_uuid():
    settings = resolve_settings(
        _config(finance_api_url="https://x", finance_subscription_id="SUB-1"),
        subscription_id="c5802e58-uuid",
    )
    assert settings.subscription_id == "SUB-1"


def test_rfp_budget_clears_production_mode():
    settings = FinanceSettings(
        budget_type="RFP", rfp_sub_type="Sampling", production_mode="Multiphase"
    ).normalised()
    assert settings.rfp_sub_type == "Sampling"
    assert settings.production_mode == ""
    assert settings.is_phase_based is False


def test_production_budget_clears_rfp_sub_type():
    settings = FinanceSettings(
        budget_type="Production", rfp_sub_type="Testing", production_mode="Singlephase"
    ).normalised()
    assert settings.rfp_sub_type == ""
    assert settings.production_mode == "Singlephase"
    assert settings.is_phase_based is False


def test_multiphase_is_the_only_phase_based_variation():
    settings = FinanceSettings(budget_type="Production", production_mode="Multiphase").normalised()
    assert settings.is_phase_based is True


def test_payload_has_exactly_the_documented_keys():
    payload = build_trajectory_usage_payload(
        _enabled(), task_id="TASK-1024", trajectory_id="TRAJ-2048", model_name="claude-opus-4.8"
    )
    assert set(payload) == PAYLOAD_KEYS


def test_payload_uses_agent_source_not_the_batch_aggregate():
    payload = build_trajectory_usage_payload(
        _enabled(),
        task_id="t",
        trajectory_id="tr",
        model_name="claude-opus-4.8",
        usage=USAGE,
    )
    assert payload["trajectory_input_tokens"] == 1200
    assert payload["trajectory_output_tokens"] == 340
    assert payload["trajectory_input_cache_tokens"] == 88
    assert payload["trajectory_output_cache_tokens"] == 12
    assert payload["trajectory_cost_usd"] == pytest.approx(1.2345)


# The user's constraint on OAuth repricing was "DO NOT interfere with the bedrock
# side of calculations". These two tests are that constraint made executable.
@pytest.mark.parametrize("model", ["claude-opus-4.8", "claude-sonnet-4-6", "gpt-5.5"])
def test_bedrock_route_cost_is_reported_verbatim(model):
    payload = build_trajectory_usage_payload(
        _enabled(),
        task_id="t",
        trajectory_id="tr",
        model_name=model,
        usage=USAGE,
        oauth_route=False,
    )
    assert payload["trajectory_cost_usd"] == pytest.approx(1.2345)


def test_oauth_route_reprices_from_token_counts():
    expected, priced_ok = estimate_cost_usd(
        "claude-opus-4.7",
        input_tokens=1200,
        output_tokens=340,
        cache_read_tokens=88,
        cache_write_tokens=12,
    )
    assert priced_ok is True

    payload = build_trajectory_usage_payload(
        _enabled(),
        task_id="t",
        trajectory_id="tr",
        model_name="claude-opus-4.7",
        usage=USAGE,
        oauth_route=True,
    )
    assert payload["trajectory_cost_usd"] == pytest.approx(expected)
    assert payload["trajectory_cost_usd"] != pytest.approx(1.2345)


def test_oauth_route_keeps_recorded_cost_when_the_model_has_no_rate_card():
    payload = build_trajectory_usage_payload(
        _enabled(),
        task_id="t",
        trajectory_id="tr",
        model_name="glassy_lagoon",
        usage=USAGE,
        oauth_route=True,
    )
    assert payload["trajectory_cost_usd"] == pytest.approx(1.2345)


def test_payload_falls_back_to_flat_usage_without_sources():
    payload = build_trajectory_usage_payload(
        _enabled(),
        task_id="t",
        trajectory_id="tr",
        model_name="m",
        usage={"input_tokens": 7, "output_tokens": 3},
    )
    assert payload["trajectory_input_tokens"] == 7
    assert payload["trajectory_output_tokens"] == 3


def test_payload_tolerates_missing_usage():
    payload = build_trajectory_usage_payload(
        _enabled(), task_id="t", trajectory_id="tr", model_name="m"
    )
    assert payload["trajectory_input_tokens"] == 0.0
    assert payload["judge_lines"] == []


def test_generated_at_carries_a_timezone_offset():
    payload = build_trajectory_usage_payload(
        _enabled(), task_id="t", trajectory_id="tr", model_name="m"
    )
    stamp = payload["generated_at"]
    assert "T" in stamp
    assert stamp.endswith("Z") or "+" in stamp[10:] or "-" in stamp[10:]


def test_judge_lines_are_built_per_council_member_and_sorted():
    lines = build_judge_lines(USAGE)
    assert [line["model_name"] for line in lines] == ["glm-4.6", "claude-sonnet-4-6"]
    assert set(lines[0]) == JUDGE_LINE_KEYS


def test_judge_cache_tokens_map_by_direction():
    lines = build_judge_lines(USAGE)
    sonnet = [line for line in lines if line["model_name"] == "claude-sonnet-4-6"][0]
    assert sonnet["judge_input_cache_tokens"] == 10
    assert sonnet["judge_output_cache_tokens"] == 5
    assert sonnet["judge_cost_usd"] == pytest.approx(0.5)


def test_judge_model_arn_is_replaced_by_the_council_family():
    # A Bedrock inference-profile ARN embeds the AWS account id. It must never
    # reach the finance API; the stable family key is sent instead.
    arn = "bedrock/arn:aws:bedrock:ap-south-1:426628337772:application-inference-profile/urg0zifsjiga"
    usage = {"sources": {"judge": {"per_member": {"sonnet": {"model": arn, "cost_usd": 0.42}}}}}
    lines = build_judge_lines(usage)
    assert lines[0]["model_name"] == "sonnet"
    assert "426628337772" not in json.dumps(lines)


def test_judge_model_keeps_a_readable_name():
    usage = {"sources": {"judge": {"per_member": {"sonnet": {"model": "claude-sonnet-4-6"}}}}}
    assert build_judge_lines(usage)[0]["model_name"] == "claude-sonnet-4-6"


def test_judge_model_falls_back_when_nothing_is_readable():
    usage = {"sources": {"judge": {"per_member": {"": {"model": "arn:aws:bedrock:x", "cost_usd": 1}}}}}
    assert build_judge_lines(usage)[0]["model_name"] == "unknown-judge"


def test_judge_lines_fall_back_to_one_aggregate_line():
    usage = {"sources": {"judge": {"total_tokens": 120, "cost_usd": 0.4}}}
    lines = build_judge_lines(usage)
    assert len(lines) == 1
    assert lines[0]["judge_cost_usd"] == pytest.approx(0.4)


def test_judge_lines_empty_when_no_judge_ran():
    assert build_judge_lines({"sources": {"agent": {"input_tokens": 1}}}) == []
    assert build_judge_lines({}) == []


def test_post_sends_bearer_token_when_configured(patch_httpx):
    patch_httpx.responses = [_FakeResp(200)]
    settings = _enabled(api_token="secret-token")
    result = post_trajectory_usage(settings, {"project_id": "PRJ-512"})

    assert result.ok is True
    call = [c for c in patch_httpx.posted if "url" in c][0]
    assert call["url"] == settings.endpoint
    assert call["headers"]["Authorization"] == "Bearer secret-token"
    assert call["headers"]["Content-Type"] == "application/json"
    assert call["json"] == {"project_id": "PRJ-512"}


def test_post_omits_auth_header_when_no_token(patch_httpx):
    patch_httpx.responses = [_FakeResp(201)]
    post_trajectory_usage(_enabled(), {"a": 1})
    call = [c for c in patch_httpx.posted if "url" in c][0]
    assert "Authorization" not in call["headers"]


def test_post_is_skipped_when_disabled(patch_httpx):
    result = post_trajectory_usage(FinanceSettings(), {"a": 1})
    assert result.skipped is True
    assert result.ok is False
    assert patch_httpx.posted == []


def test_post_never_raises_on_transport_failure(patch_httpx):
    patch_httpx.responses = [RuntimeError("connection refused")]
    result = post_trajectory_usage(_enabled(), {"a": 1})
    assert result.ok is False
    assert "connection refused" in result.detail


def test_post_reports_http_error_status(patch_httpx):
    patch_httpx.responses = [_FakeResp(422, "validation failed")]
    result = post_trajectory_usage(_enabled(), {"a": 1})
    assert result.ok is False
    assert result.status_code == 422
    assert "validation failed" in result.detail


def test_record_writes_artifact_and_sink_on_success(patch_httpx, tmp_path):
    patch_httpx.responses = [_FakeResp(200)]
    sink = tmp_path / "sink" / "finance_usage.jsonl"
    output_dir = tmp_path / "run_1"
    output_dir.mkdir()
    settings = _enabled(sink_path=sink)

    result = record_trajectory_usage(
        settings,
        task_id="TASK-1024",
        trajectory_id="TRAJ-2048",
        model_name="claude-opus-4.8",
        usage=USAGE,
        output_dir=output_dir,
    )

    assert result.ok is True
    record = json.loads((output_dir / "finance_usage.json").read_text())
    assert record["payload"]["task_id"] == "TASK-1024"
    assert record["payload"]["trajectory_id"] == "TRAJ-2048"
    assert record["post"]["ok"] is True
    assert record["endpoint"] == settings.endpoint

    lines = sink.read_text().strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["payload"]["model_name"] == "claude-opus-4.8"


def test_record_persists_replayable_artifact_even_when_post_fails(patch_httpx, tmp_path):
    patch_httpx.responses = [RuntimeError("odoo down")]
    sink = tmp_path / "finance_usage.jsonl"
    output_dir = tmp_path / "run_1"
    output_dir.mkdir()

    result = record_trajectory_usage(
        _enabled(sink_path=sink),
        task_id="t",
        trajectory_id="tr",
        model_name="m",
        usage=USAGE,
        output_dir=output_dir,
    )

    assert result.ok is False
    record = json.loads((output_dir / "finance_usage.json").read_text())
    assert record["post"]["ok"] is False
    assert set(record["payload"]) == PAYLOAD_KEYS
    assert len(sink.read_text().strip().splitlines()) == 1


def test_record_is_a_noop_when_disabled(patch_httpx, tmp_path):
    result = record_trajectory_usage(
        FinanceSettings(), task_id="t", trajectory_id="tr", model_name="m", output_dir=tmp_path
    )
    assert result.skipped is True
    assert not (tmp_path / "finance_usage.json").exists()
    assert patch_httpx.posted == []


def test_record_survives_an_unwritable_output_dir(patch_httpx, tmp_path):
    patch_httpx.responses = [_FakeResp(200)]
    result = record_trajectory_usage(
        _enabled(),
        task_id="t",
        trajectory_id="tr",
        model_name="m",
        output_dir=tmp_path / "does" / "not" / "exist",
    )
    assert result.ok is True


def test_sink_appends_one_line_per_record(tmp_path):
    sink = tmp_path / "nested" / "finance_usage.jsonl"
    append_to_sink(sink, {"b": 2, "a": 1})
    append_to_sink(sink, {"a": 3})

    lines = sink.read_text().strip().splitlines()
    assert len(lines) == 2
    assert lines[0] == '{"a": 1, "b": 2}'


def test_sink_never_raises_on_bad_path(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    append_to_sink(blocker / "finance_usage.jsonl", {"a": 1})
