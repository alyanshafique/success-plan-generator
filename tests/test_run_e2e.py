"""Offline end-to-end test of `spg run`: fake SEC EDGAR and GDELT (httpx MockTransport) and a fake model.

No network and no API call. The command runs through the real CLI (Typer's CliRunner) with SPG_ROOT pointing at a
temporary folder, so it exercises pre-flight, Retrieve, the cost gate, Extract, Map, Draft, Render, resume,
overwrite protection, the zero-fit path, the Extract drop stop, and the personal-names list.
"""

import json

import httpx
import jsonschema
import pytest
from docx import Document
from typer.testing import CliRunner

from success_plan import cli, guards
from success_plan.llm import LLMResult
from success_plan.models import LLMChallengesChangesOutput, LLMInitiativesOutput
from success_plan.stages import render as rd
from success_plan.stages.retrieve.http import RateLimitedClient
from test_private_names import file_text, untracked_scope
from test_sections import make_10k

CIK, TICKER = 999999999, "TSTC"
TENK_ACC, EIGHTK_ACC = "0000999999-26-000001", "0000999999-26-000002"
AS_OF = "2026-10-12"
AUTOMATION_D1 = "A no-code rule builder for if-this-then-that rules triggered by events"
OFFICER_8K = """<html><body>
<p>Item 5.02 Departure of Directors or Certain Officers.</p>
<p>On April 17, 2026, the Board appointed Jordan Q. Sample as Chief Financial Officer of Test Co, effective May 11, 2026.</p>
<p>The Board of Directors approved the appointment of a new Chief Financial Officer for the company effective next month.</p>
</body></html>"""


# ---------------------------------------------------------------- fake SEC EDGAR and GDELT

def submissions():
    return {"filings": {"recent": {
        "form": ["8-K", "10-K"], "accessionNumber": [EIGHTK_ACC, TENK_ACC],
        "filingDate": ["2026-04-23", "2026-02-13"], "reportDate": ["2026-04-17", "2025-12-31"],
        "primaryDocument": ["testco-8k.htm", "testco-20251231.htm"], "items": ["5.02,9.01", ""],
    }, "files": []}}


def gdelt_ok():
    return httpx.Response(200, json={"articles": [
        {"url": "https://news.example.com/test-co-platform", "title": "Test Co expands its platform teams",
         "seendate": "20260901T120000Z", "domain": "news.example.com"}]})


GDELT = {"response": gdelt_ok}  # tests swap this for a throttled GDELT


def fake_http(requests: list):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        requests.append(url)
        if url.startswith("https://www.sec.gov/files/company_tickers.json"):
            return httpx.Response(200, json={"0": {"cik_str": CIK, "ticker": TICKER, "title": "Test Co Inc"}})
        if url.startswith(f"https://data.sec.gov/submissions/CIK{CIK:010d}.json"):
            return httpx.Response(200, json=submissions())
        if url.endswith("/testco-20251231.htm"):
            return httpx.Response(200, text=make_10k())
        if url.endswith("/testco-8k.htm"):
            return httpx.Response(200, text=OFFICER_8K)
        if url.startswith("https://api.gdeltproject.org/"):
            return GDELT["response"]()
        return httpx.Response(404)

    def factory(user_agent, on_backoff=None):
        return RateLimitedClient(user_agent, transport=httpx.MockTransport(handler), sleep=lambda s: None,
                                 min_intervals={"sec": 0.0, "gdelt": 0.0, "other": 0.0})
    return factory


# ---------------------------------------------------------------- fake model

def cite(chunk_id, evidence):
    return {"chunk_id": chunk_id, "evidence": evidence}


INITIATIVES = {"initiatives": [
    {"title": "Platform modernization", "theme": "technology_ai", "validation_question": "Which platform work comes first?",
     "sentences": [{"text": "The company describes its business in ordinary terms.",
                    "citations": [cite("10k-item1-p001", "Business body sentence with several ordinary words in it")]}]},
    {"title": "Market expansion", "theme": "market_expansion", "validation_question": "Which markets come next?",
     "sentences": [{"text": "Management discusses its plans in ordinary words.",
                    "citations": [cite("10k-item7-p001", "MDNA body sentence with several ordinary words in it")]}]},
]}
CHALLENGES = {
    "challenges": [{"title": "Competitive pressure", "category": "competition", "validation_question": "Which rivals matter most?",
                    "sentences": [{"text": "The company lists competitive risks in ordinary words.",
                                   "citations": [cite("10k-item1a-p001", "Risk body sentence with several ordinary words in it")]}]}],
    "changes": [{"title": "Chief Financial Officer change", "change_type": "leadership_change",
                 "validation_question": "What are the new finance priorities?",
                 "sentences": [{"text": "The company appointed a new Chief Financial Officer.",
                                "citations": [cite(f"8k-{EIGHTK_ACC}-main-p001",
                                                   "approved the appointment of a new Chief Financial Officer for the company")]}]}],
}


def mapping(i01_fit="partial"):
    first = ({"initiative_id": "I-01", "fit": "partial", "gap_ids": [], "no_fit_reason": "",
              "sentences": [{"text": "Automation offers a no-code rule builder for rules triggered by events.",
                             "claims": [{"claim_id": "JIRA-AUTOMATION#d1", "evidence": AUTOMATION_D1}]}],
              "fit_rationale": "Automation supports the platform work but does not deliver it.",
              "validation_question": "Which platform steps are manual today?"}
             if i01_fit == "partial" else
             {"initiative_id": "I-01", "fit": "no_fit", "gap_ids": ["GAP-CUSTOMER-FACING"], "sentences": [],
              "no_fit_reason": "Not something these products do.", "fit_rationale": "No capability fits.",
              "validation_question": "Is there internal work to track?"})
    return {"mappings": [first, {
        "initiative_id": "I-02", "fit": "no_fit", "gap_ids": ["GAP-CUSTOMER-FACING"], "sentences": [],
        "no_fit_reason": "Market expansion is commercial work.", "fit_rationale": "No capability performs it.",
        "validation_question": "Is there launch work to coordinate?"}]}


DRAFT = {"initiatives": [{"initiative_id": "I-01", "skip_reason": "", "criteria": [
    {"text": "Track how many teams use automation rules triggered by events.", "refs": ["B-01.s1"],
     "criterion_type": "product_usage", "indicator_type": "leading", "measure": "number of teams with active automation rules",
     "direction": "increase", "baseline_question": "How many teams use automation rules today?",
     "validation_question": "Does manual routing slow the teams down?"},
    {"text": "Track how quickly the company releases business changes.", "refs": ["I-01.s1"],
     "criterion_type": "business_outcome", "indicator_type": "lagging", "measure": "time to release business changes",
     "direction": "decrease", "baseline_question": "How long does a release take today?",
     "validation_question": "Is release time reviewed by leadership?"}],
    "tasks": [{"task_id": "T-DISCOVERY", "capability_id": "", "responsible_role": "CSM", "window": "Day 0-30"},
              {"task_id": "T-DEMO", "capability_id": "JIRA-AUTOMATION", "responsible_role": "Customer admin",
               "window": "Day 31-60"}]}]}


class FakeModel:
    def __init__(self, initiatives=INITIATIVES, challenges=CHALLENGES, map_output=None, draft=DRAFT):
        self.outputs = {"initiatives": initiatives, "challenges": challenges, "map": map_output or mapping(),
                        "draft": draft}
        self.calls: list[str] = []

    def count_input_tokens(self, *, system, user, output_model, model):
        return 1000

    def complete(self, *, system, user, output_model, settings):
        if output_model is LLMInitiativesOutput:
            key = "initiatives"
        elif output_model is LLMChallengesChangesOutput:
            key = "challenges"
        elif output_model.__name__ == "MapOutputOut":
            key = "map"
        else:
            key = "draft"
        self.calls.append(key)
        parsed = output_model.model_validate(self.outputs[key])  # must fit the real (enum-restricted) format
        return LLMResult(parsed=parsed, model=settings.model, stop_reason="end_turn", input_tokens=1000, output_tokens=500)


# ---------------------------------------------------------------- harness

@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "load_env", lambda: None)  # never read the developer's .env in tests
    monkeypatch.setenv("SPG_ROOT", str(tmp_path))
    monkeypatch.setenv("SEC_USER_AGENT", "Test User test@example.com")
    requests: list[str] = []
    monkeypatch.setattr(cli, "make_http_client", fake_http(requests))
    monkeypatch.setitem(GDELT, "response", gdelt_ok)
    state = {"model": FakeModel(), "requests": requests, "root": tmp_path}
    monkeypatch.setattr(cli, "make_model_client", lambda: state["model"])
    return state


def spg(*args):
    return CliRunner().invoke(cli.app, ["run", "Test Co", "--as-of", AS_OF, *args])


def out_dir(env):
    return env["root"] / "output" / "test-co"


# ---------------------------------------------------------------- tests

def test_full_run_writes_all_four_outputs_offline(env):
    result = spg("--yes")
    assert result.exit_code == 0, result.output
    out = out_dir(env)
    for name in ("plan.json", "plan.md", "csm_draft.md", "csm_draft.docx"):
        assert (out / name).exists(), name
    assert env["model"].calls == ["initiatives", "challenges", "map", "draft"]
    assert all(u.startswith(("https://www.sec.gov/", "https://data.sec.gov/", "https://api.gdeltproject.org/"))
               for u in env["requests"])  # only the fake hosts, through the mock transport
    plan = json.loads((out / "plan.json").read_text())
    jsonschema.validate(plan, json.loads(rd.SCHEMA_FILE.read_text()))
    assert [o["linked_initiative_ids"] for o in plan["objectives"]] == [["I-01"]]
    md, draft_text = (out / "plan.md").read_text(), file_text(out / "csm_draft.docx")
    body = md.split("## Company highlights")[1]  # the header lists each stage's API cost, which is not a price
    for text in (body, draft_text, (out / "csm_draft.md").read_text()):
        assert not guards.find_currency_amounts(text) and not guards.has_personal_title(text)
        assert "Jordan" not in text  # the officer's name never reaches the plan or the CSM draft
    assert Document(out / "csm_draft.docx").paragraphs[0].text == "Success Plan Strategy and Initiatives"
    assert "Summary:" in result.output and "total cost $" in result.output


def test_run_lists_personal_names_for_the_private_list(env):
    result = spg("--yes")
    assert "Possible personal names" in result.output and "Jordan Q. Sample" in result.output
    assert "config/private_names.txt" in result.output
    scanned = {p.name for p in untracked_scope(env["root"])}
    assert {"csm_draft.docx", "csm_draft.md", "plan.md", "plan.json"} <= scanned  # the private-names scan covers them


def test_timing_and_cost_in_the_usage_log(env):
    spg("--yes")
    lines = [json.loads(l) for l in (env["root"] / "data" / "test-co" / "stages" / "usage_log.jsonl").read_text().splitlines()]
    run_line = lines[-1]
    assert run_line["stage"] == "run" and run_line["status"] == "ok"
    assert [s["name"] for s in run_line["stages"]] == ["retrieve", "extract", "map", "draft", "render"]
    assert all(isinstance(s["seconds"], float) for s in run_line["stages"]) and run_line["cost_usd"] > 0


def test_second_run_refuses_to_overwrite_and_dry_run_is_allowed(env):
    assert spg("--yes").exit_code == 0
    again = spg("--yes")
    assert again.exit_code == 1 and "already has outputs" in again.output and "Use --force" in again.output
    calls = len(env["model"].calls)
    dry = spg("--dry-run")
    assert dry.exit_code == 0 and "Dry run: no model call was made" in dry.output
    assert len(env["model"].calls) == calls


def test_resume_reuses_saved_stages_without_model_calls(env):
    assert spg("--yes").exit_code == 0
    for f in out_dir(env).iterdir():
        f.unlink()
    env["model"].calls.clear()
    result = spg("--yes")
    assert result.exit_code == 0, result.output
    assert env["model"].calls == [] and "reused 02_extract.json" in result.output
    assert (out_dir(env) / "csm_draft.docx").exists()


def test_force_reruns_every_stage(env):
    assert spg("--yes").exit_code == 0
    env["model"].calls.clear()
    assert spg("--yes", "--force").exit_code == 0
    assert env["model"].calls == ["initiatives", "challenges", "map", "draft"]


def test_cost_limit_stops_before_any_model_call(env):
    result = spg("--yes", "--max-cost", "0.0001")
    assert result.exit_code == 1 and "exceeds the run limit" in result.output
    assert env["model"].calls == [] and not out_dir(env).exists()


def test_without_confirmation_nothing_is_spent(env):
    result = CliRunner().invoke(cli.app, ["run", "Test Co", "--as-of", AS_OF], input="n\n")
    assert result.exit_code == 1 and "Not confirmed" in result.output and env["model"].calls == []


def test_zero_fits_render_a_no_fit_plan_and_exit_0(env):
    env["model"] = FakeModel(map_output=mapping(i01_fit="no_fit"))
    result = spg("--yes")
    assert result.exit_code == 0, result.output
    assert "No product fit" in result.output and env["model"].calls == ["initiatives", "challenges", "map"]
    plan = json.loads((out_dir(env) / "plan.json").read_text())
    assert plan["objectives"] == [] and all(b["fit"] == "no_fit" for b in plan["key_benefits"])
    assert "add objectives for initiatives the tool did not map" in (out_dir(env) / "plan.md").read_text()
    stages = json.loads((env["root"] / "data" / "test-co" / "stages" / "04_draft.json").read_text())
    assert stages["no_objectives_reason"] and stages["objectives"] == []


def test_extract_drop_stop_then_continue_with_from_map(env):
    bad = json.loads(json.dumps(INITIATIVES))
    for item in bad["initiatives"]:
        item["sentences"] += [{"text": f"Unsupported claim {n}.", "citations": [cite("10k-item1-p001", "words that are not in the chunk at all here")]}
                              for n in range(4)]
    env["model"] = FakeModel(initiatives=bad)
    stopped = spg("--yes")
    assert stopped.exit_code == 1 and "more than half" in stopped.output and '--from map' in stopped.output
    assert env["model"].calls == ["initiatives", "challenges"] and not out_dir(env).exists()
    env["model"].calls.clear()
    resumed = spg("--yes", "--from", "map")
    assert resumed.exit_code == 0, resumed.output
    assert env["model"].calls == ["map", "draft"]


@pytest.mark.parametrize("setup,message", [
    (lambda mp: mp.delenv("SEC_USER_AGENT"), "SEC_USER_AGENT is not set"),
    (lambda mp: mp.setattr(cli, "make_model_client", lambda: None), "ANTHROPIC_API_KEY is not set"),
])
def test_preflight_stops_before_any_network_call(env, monkeypatch, setup, message):
    setup(monkeypatch)
    result = spg("--yes")
    assert result.exit_code == 1 and message in result.output and env["requests"] == []


def test_no_10k_stops_at_retrieve(env, monkeypatch):
    monkeypatch.setattr(__import__(__name__), "submissions", lambda: {"filings": {"recent": {
        "form": ["8-K"], "accessionNumber": [EIGHTK_ACC], "filingDate": ["2026-04-23"], "reportDate": ["2026-04-17"],
        "primaryDocument": ["testco-8k.htm"], "items": ["5.02"]}, "files": []}})
    result = spg("--yes")
    assert result.exit_code == 1 and "Stopped at retrieve" in result.output and "No 10-K found" in result.output
    assert env["model"].calls == []


# ---------------------------------------------------------------- the Retrieve line in the run summary

def retrieve_line(output: str) -> str:
    return next(l for l in output.splitlines() if l.strip().startswith("retrieve "))


def test_summary_shows_as_of_headlines_eligible_and_gdelt_ok(env):
    result = spg("--yes")
    assert "as of 2026-10-12; 1 headlines retrieved, 1 eligible; GDELT ok" in retrieve_line(result.output)


def test_dry_run_summary_shows_the_saved_retrieve(env):
    spg("--yes")
    dry = spg("--dry-run")
    line = retrieve_line(dry.output)
    assert "reused" in line and "reused 01_retrieve.json" in line and "as of 2026-10-12" in line and "GDELT ok" in line


def test_gdelt_status_cache_hit(env):
    spg("--yes")
    rerun = spg("--yes", "--force")  # Retrieve runs again; the GDELT response for this as-of date is cached
    assert "GDELT cache hit" in retrieve_line(rerun.output)


@pytest.mark.parametrize("response", [lambda: httpx.Response(429), lambda: httpx.Response(200, text="Please limit requests")])
def test_gdelt_status_throttled(env, monkeypatch, response):
    monkeypatch.setitem(GDELT, "response", response)
    result = spg("--yes")
    assert "0 headlines retrieved, 0 eligible; GDELT throttled" in retrieve_line(result.output)
    assert result.exit_code == 0  # without --require-news, missing news stays a warning


def test_gdelt_status_skipped(env):
    result = spg("--yes", "--skip-news")
    assert "GDELT skipped" in retrieve_line(result.output)


# ---------------------------------------------------------------- saved Retrieve with a different as-of date

def test_dry_run_reports_both_as_of_dates_and_keeps_the_saved_retrieve(env):
    spg("--yes")
    requests_before = len(env["requests"])
    dry = CliRunner().invoke(cli.app, ["run", "Test Co", "--as-of", "2026-10-14", "--dry-run"])
    assert dry.exit_code == 0, dry.output
    assert ("The saved Retrieve output is as of 2026-10-12, but --as-of is 2026-10-14. A real run will re-run "
            "Retrieve as of 2026-10-14; this dry-run estimate uses the saved output.") in dry.output
    assert len(env["requests"]) == requests_before  # no SEC or GDELT request in the dry run
    saved = json.loads((env["root"] / "data" / "test-co" / "stages" / "01_retrieve.json").read_text())
    assert saved["as_of"] == "2026-10-12"


def test_real_run_with_a_different_as_of_reruns_retrieve_and_everything_after(env):
    spg("--yes")
    for f in out_dir(env).iterdir():
        f.unlink()  # no --force: only the as-of change makes Retrieve, and so every later stage, run again
    env["model"].calls.clear()
    result = CliRunner().invoke(cli.app, ["run", "Test Co", "--as-of", "2026-10-14", "--yes"])
    assert result.exit_code == 0, result.output
    line = retrieve_line(result.output)
    assert " ran " in line and "as of 2026-10-14" in line
    assert env["model"].calls == ["initiatives", "challenges", "map", "draft"]
    saved = json.loads((env["root"] / "data" / "test-co" / "stages" / "01_retrieve.json").read_text())
    assert saved["as_of"] == "2026-10-14"


def test_same_as_of_dry_run_has_no_date_notice(env):
    spg("--yes")
    dry = spg("--dry-run")
    assert "The saved Retrieve output is as of" not in dry.output


# ---------------------------------------------------------------- --require-news

def test_require_news_stops_before_any_model_call_when_gdelt_returns_nothing(env, monkeypatch):
    monkeypatch.setitem(GDELT, "response", lambda: httpx.Response(429))
    result = spg("--yes", "--require-news")
    assert result.exit_code == 1 and "Stopped at retrieve" in result.output
    assert "GDELT returned no headlines (GDELT throttled) and --require-news is set, so no model call was made" in result.output
    assert 'spg run "Test Co" --as-of 2026-10-12 --refresh-sources --require-news' in result.output
    assert env["model"].calls == [] and not out_dir(env).exists()
    monkeypatch.setitem(GDELT, "response", gdelt_ok)  # GDELT responds: the resume command completes the run
    resumed = CliRunner().invoke(cli.app, ["run", "Test Co", "--as-of", AS_OF, "--refresh-sources", "--require-news", "--yes"])
    assert resumed.exit_code == 0, resumed.output
    assert "1 headlines retrieved" in retrieve_line(resumed.output)


def test_require_news_also_stops_a_dry_run(env, monkeypatch):
    monkeypatch.setitem(GDELT, "response", lambda: httpx.Response(429))
    result = spg("--dry-run", "--require-news")
    assert result.exit_code == 1 and "--require-news is set" in result.output and "Estimated cost" not in result.output


def test_require_news_is_ignored_with_skip_news(env):
    result = spg("--yes", "--require-news", "--skip-news")
    assert result.exit_code == 0, result.output
    assert "GDELT skipped" in retrieve_line(result.output)


def test_without_require_news_a_gdelt_failure_is_a_warning(env, monkeypatch):
    monkeypatch.setitem(GDELT, "response", lambda: httpx.Response(429))
    result = spg("--yes")
    assert result.exit_code == 0 and "GDELT request failed" in result.output
