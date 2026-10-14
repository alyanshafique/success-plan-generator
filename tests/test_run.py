"""`spg run` pre-flight and cost gate. No network or API calls (Retrieve and the estimate are stubbed here; the
offline end-to-end test is in test_run_e2e.py)."""

import json

import pytest

from success_plan import run as rn
from success_plan.llm import load_models_config
from success_plan.paths import CompanyPaths


class RecordingLLM:
    def __init__(self):
        self.calls = []

    def complete(self, **kw):
        self.calls.append(kw)
        raise AssertionError("no model call expected")

    def count_input_tokens(self, **kw):
        return 1000


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("SEC_USER_AGENT", "Test User test@example.com")
    return CompanyPaths(root=tmp_path, slug="test-co")


def run(paths, llm=None, **opts):
    options = rn.RunOptions(company="Test Co", **opts)
    return rn.run_pipeline(options, models_config=load_models_config(), llm=llm, http_factory=None,
                           echo=lambda *a: None, confirm=lambda m: False, paths=paths)


def stub_retrieve_and_estimate(monkeypatch, rows):
    monkeypatch.setattr(rn, "_run_retrieve", lambda *a, **k: None)
    monkeypatch.setattr(rn, "estimate_run", lambda *a, **k: rows)


ROW = {"stage": "extract", "call": "initiatives", "model": "claude-sonnet-5-5", "input_tokens": 85000,
       "input_source": "counted", "output_tokens": 12000}


@pytest.mark.parametrize("change,message", [
    ({"SEC_USER_AGENT": ""}, "SEC_USER_AGENT is not set"),
    ({"SEC_USER_AGENT": "no-email"}, "must include a name and a contact email"),
])
def test_preflight_requires_sec_user_agent(env, monkeypatch, change, message):
    for k, v in change.items():
        monkeypatch.setenv(k, v)
    result = run(env, llm=RecordingLLM())
    assert result.stop.stage == "pre-flight" and message in result.stop.message


def test_preflight_requires_an_api_key_unless_dry_run(env):
    result = run(env, llm=None)
    assert result.stop.stage == "pre-flight" and "ANTHROPIC_API_KEY is not set" in result.stop.message


@pytest.mark.parametrize("opts,message", [
    ({"profile": "turbo"}, "No settings for profile 'turbo'"),
    ({"product": "nonexistent"}, "No product config config/product_nonexistent.yaml"),
    ({"from_stage": "retrieve"}, "--from must be one of extract, map, draft, render"),
])
def test_preflight_rejects_bad_options(env, opts, message):
    result = run(env, llm=RecordingLLM(), **opts)
    assert result.stop.stage == "pre-flight" and message in result.stop.message


def test_final_profile_uses_opus_for_extract_and_sonnet_for_map_and_draft():
    mc = load_models_config()
    assert mc.settings("final", "extract").model == "claude-opus-5-5"
    assert mc.settings("final", "map").model == mc.settings("dev", "map").model == "claude-sonnet-5-5"
    assert mc.settings("final", "draft").model == mc.settings("dev", "draft").model == "claude-sonnet-5-5"
    for profile in ("dev", "plumbing"):
        assert len({mc.settings(profile, s).model for s in ("extract", "map", "draft")}) == 1


def test_estimate_over_the_limit_stops_before_any_model_call(env, monkeypatch):
    stub_retrieve_and_estimate(monkeypatch, [dict(ROW, cost_usd=2.5), dict(ROW, call="map", stage="map", cost_usd=1.0)])
    llm = RecordingLLM()
    result = run(env, llm=llm, yes=True)
    assert result.stop.stage == "cost" and "Estimated $3.50 exceeds the run limit of $3.00" in result.stop.message
    assert llm.calls == []
    line = json.loads((env.stages / "usage_log.jsonl").read_text().splitlines()[-1])
    assert line["stage"] == "run" and line["status"] == "stopped" and line["stop"]["stage"] == "cost"


def test_max_cost_raises_the_limit_and_confirmation_is_required(env, monkeypatch):
    stub_retrieve_and_estimate(monkeypatch, [dict(ROW, cost_usd=3.5)])
    llm = RecordingLLM()
    result = run(env, llm=llm, max_cost=5.0)  # under the raised limit, but not confirmed
    assert result.stop.stage == "cost" and "Not confirmed" in result.stop.message and llm.calls == []


def test_dry_run_estimates_and_stops_without_a_key(env, monkeypatch):
    stub_retrieve_and_estimate(monkeypatch, [dict(ROW, cost_usd=0.4)])
    result = run(env, llm=None, dry_run=True)
    assert result.stop is None and result.dry_run and result.estimated_cost_usd == 0.4 and result.stages == []
