"""Draft stage tests with a fake model. No network or API calls."""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from success_plan import guards
from success_plan.llm import LLMError, LLMResult, ModelsConfig
from success_plan.models import (
    COMPANY_MEASURE_LABEL, CRITERION_BLANK, OBJECTIVES_BLANK, TARGET_BLANK, LLMCriterion, LLMDraftOutput, LLMSkipped,
    LLMTaskPick,
)
from success_plan.stages import draft as dr
from success_plan.stages import map as mp
from test_map import CONFIG as MAP_CONFIG
from test_map import SYSTEM, TODAY, FakeLLM, base_output, write_extract

CONFIG = ModelsConfig(
    max_cost_usd_per_run=3.0,
    profiles={"test": {"draft": {"model": "claude-sonnet-5-5", "effort": "medium", "max_tokens": 16000, "expected_output_tokens": 6000}}},
    prices={"claude-sonnet-5-5": {"input": 2.0, "output": 10.0, "cache_read": 0.2, "cache_write": 2.5}},
    prices_as_of="2026-10-06",
)


def write_map(tmp_path, **kw):
    """Extract sample plus a Map output: I-01 partial (B-01), I-02 full (B-02), I-03 partial (B-03, inferred,
    news headline only), I-04 no_fit (B-04)."""
    p = write_extract(tmp_path, **kw)
    mp.run_map(p, FakeLLM(base_output()), MAP_CONFIG, "test", system=SYSTEM, today=TODAY)
    return p


@pytest.fixture
def paths(tmp_path):
    return write_map(tmp_path)


def crit(iid, text, *refs, ctype="product_usage", itype="leading", measure="number of teams using one shared view",
         direction="increase", baseline="How many teams use one shared view today?", validation="Is this worth tracking?"):
    return LLMCriterion(initiative_id=iid, text=text, refs=list(refs), criterion_type=ctype, indicator_type=itype,
                        measure=measure, direction=direction, baseline_question=baseline, validation_question=validation)


def task(iid, task_id, capability_id="", role="CSM", window="Day 0-30"):
    return LLMTaskPick(initiative_id=iid, task_id=task_id, capability_id=capability_id, responsible_role=role, window=window)


def base_draft():
    return LLMDraftOutput(
        criteria=[
            crit("I-01", "Teams use Automation rules for event-triggered work.", "B-01.s1",
                 measure="number of teams with at least one active automation rule"),
            crit("I-02", "Delivery work across teams is combined into one view.", "I-02.s1", "B-02.s1"),
            crit("I-02", "Delivery of product changes across teams is coordinated.", "I-02.s1",
                 ctype="business_outcome", itype="lagging", measure="share of planned product changes delivered on schedule"),
            crit("I-03", "Teams edit pages together with comments and version history.", "B-03.s1",
                 measure="number of teams editing shared pages each week"),
        ],
        tasks=[
            task("I-01", "T-DEMO", "JIRA-AUTOMATION", role="Customer admin", window="Day 31-60"),
            task("I-01", "T-DISCOVERY"),
            task("I-02", "T-PILOT", "JIRA-PLANNING-ROADMAPS", role="Customer engineering lead", window="Day 61-90"),
            task("I-03", "T-WORKFLOW-REVIEW", role="Customer team lead"),
        ],
    )


def group(output: LLMDraftOutput) -> dict:
    """The flat test output in the model's per-initiative format. An initiative with nothing at all is left out."""
    order = list(dict.fromkeys([c.initiative_id for c in output.criteria] + [t.initiative_id for t in output.tasks]
                               + [x.initiative_id for x in output.skipped]))
    reasons = {x.initiative_id: x.reason for x in output.skipped}
    return {"initiatives": [{
        "initiative_id": i,
        "criteria": [c.model_dump(mode="json", exclude={"initiative_id"}) for c in output.criteria if c.initiative_id == i],
        "tasks": [t.model_dump(mode="json", exclude={"initiative_id"}) for t in output.tasks if t.initiative_id == i],
        "skip_reason": reasons.get(i, ""),
    } for i in order]}


class FakeDraftLLM(FakeLLM):
    def complete(self, *, system, user, output_model, settings):
        self.calls.append({"system": system, "user": user, "output_model": output_model})
        if self.fail:
            raise LLMError("simulated refusal")
        parsed = output_model.model_validate(group(self.output))  # must fit the enum-restricted format
        return LLMResult(parsed=parsed, model=settings.model, stop_reason="end_turn", input_tokens=4000, output_tokens=2000)


def run(paths, output, **kw):
    return dr.run_draft(paths, FakeDraftLLM(output), CONFIG, "test", system=SYSTEM, **kw)


def report(paths):
    return json.loads((paths.stages / dr.GUARD_REPORT_NAME).read_text())


def objective(out, iid):
    return next(o for o in out.objectives if o.linked_initiative_ids == [iid])


# ---------------------------------------------------------------- scope

def test_only_fitted_initiatives_get_objectives_and_reach_the_model(paths):
    llm = FakeDraftLLM(base_draft())
    _, out = dr.run_draft(paths, llm, CONFIG, "test", system=SYSTEM)
    assert [o.linked_initiative_ids[0] for o in out.objectives] == ["I-01", "I-02", "I-03"]
    assert out.not_drafted == ["I-04"] and out.objectives_blank == OBJECTIVES_BLANK
    user = llm.calls[0]["user"]
    assert "I-04" not in user and "restructuring" not in user.lower()
    assert 'ref="I-02.s1"' in user and 'ref="B-02.s1"' in user


def test_output_format_allows_only_real_ids_roles_and_windows(paths):
    llm = FakeDraftLLM(base_draft())
    dr.run_draft(paths, llm, CONFIG, "test", system=SYSTEM)
    schema = json.dumps(llm.calls[0]["output_model"].model_json_schema())
    assert "I-04" not in schema and "B-04" not in schema
    assert "Day 0-30" in schema and "Customer admin" in schema and "T-PILOT" in schema
    bad = base_draft()
    bad.tasks[0].window = "March 2027"
    with pytest.raises(Exception):
        llm.calls[0]["output_model"].model_validate(group(bad))


def test_draft_does_not_read_a_selection_file(paths):
    inputs = paths.stages.parent / "inputs"
    inputs.mkdir(parents=True)
    (inputs / "csm_selection.yaml").write_text("selected: [I-02]\n")
    _, out = run(paths, base_draft())
    assert len(out.objectives) == 3


def test_map_from_a_different_extract_is_refused(paths):
    m = json.loads((paths.stages / dr.MAP_NAME).read_text())
    m["extract_generated_at"] = "2026-01-01T00:00:00Z"
    (paths.stages / dr.MAP_NAME).write_text(json.dumps(m))
    with pytest.raises(dr.DraftError, match="different 02_extract.json"):
        run(paths, base_draft())


# ---------------------------------------------------------------- objectives and criteria

def test_objective_fields_titles_and_labels(paths):
    _, out = run(paths, base_draft())
    o1, o2, o3 = (objective(out, i) for i in ("I-01", "I-02", "I-03"))
    assert o1.title == "Explore support for: Unified technology platform"
    assert o1.fit == "partial" and o1.capability_ids == ["JIRA-AUTOMATION"] and o1.provenance == "inferred"
    assert "partial fit: supports the work, does not deliver the outcome" in o1.labels
    assert o2.fit == "full" and any(l.startswith("full fit") for l in o2.labels)
    assert "news headline only" in o3.labels  # carried from Extract through Map
    for o in out.objectives:
        assert not guards.outcome_promise(o.title)


def test_criterion_target_baseline_and_citations(paths):
    _, out = run(paths, base_draft())
    c = objective(out, "I-02").success_criteria[0]
    assert c.id == "O-02-C1" and c.refs == ["I-02.s1", "B-02.s1"] and c.provenance == "inferred"
    assert c.needs_baseline and c.baseline_question
    assert c.proposed_target == f"Increase: number of teams using one shared view. Target: {TARGET_BLANK}"
    assert not re.search(r"\d", c.proposed_target.replace(TARGET_BLANK, ""))
    assert c.product_citations[0].claim_id == "JIRA-PLANNING-ROADMAPS#d1"
    outcome = objective(out, "I-02").success_criteria[1]
    assert outcome.criterion_type == "business_outcome" and COMPANY_MEASURE_LABEL in outcome.labels


def test_reference_to_other_initiative_rejected(paths):
    o = base_draft()
    o.criteria[0].refs = ["B-02.s1"]  # I-01 criterion citing I-02's benefit
    _, out = run(paths, o)
    assert objective(out, "I-01").success_criteria[0].text == CRITERION_BLANK
    r = report(paths)
    assert "B-02.s1: belongs to I-02" in r["rejected_refs"][0]["reason"]
    assert "no valid reference" in r["dropped_criteria"][0]["reason"]


def test_type_rules(paths):
    o = base_draft()
    o.criteria[1].refs = ["I-02.s1"]  # product_usage without a benefit sentence
    o.criteria[2].indicator_type = "leading"  # business outcome must be lagging
    o.criteria[0].measure = "  "
    _, out = run(paths, o)
    reasons = " | ".join(d["reason"] for d in report(paths)["dropped_criteria"])
    assert "does not reference a benefit sentence" in reasons and "must be lagging" in reasons and "no measure" in reasons
    assert objective(out, "I-02").success_criteria[0].text == CRITERION_BLANK


@pytest.mark.parametrize("field,value,reason", [
    ("text", "Teams pay $8 per user for one shared view.", "price"),
    ("measure", "number of Premium teams using one view", "plan tier"),
    ("text", "By March, delivery work across teams is in one view.", "states a month ('March')"),
    ("measure", "number of teams using one view since Sept. launch", "states a month"),
    ("validation_question", "Could the first team be live by the 15th?", "states a day ('15th')"),
    ("baseline_question", "How many teams used one view on 2026-03-01?", "states a day"),
    ("text", "Delivery work across teams is in one view by Q3.", "states a quarter ('Q3')"),
    ("validation_question", "Can this be live in the second quarter?", "states a quarter"),
    ("text", "Delivery work across teams is in one view in 2027.", "a year its references do not contain ('2027')"),
    ("text", "Delivery work for 40 teams is combined into one view.", "figure its references do not contain"),
])
def test_hard_drops(paths, field, value, reason):
    o = base_draft()
    setattr(o.criteria[1], field, value)
    _, out = run(paths, o)
    assert reason in report(paths)["dropped_criteria"][0]["reason"]
    assert [c.criterion_type for c in objective(out, "I-02").success_criteria] == ["business_outcome"]


def test_year_in_references_is_allowed(paths):
    o = base_draft()
    o.criteria[2].text = "Delivery of product changes across teams since 2024 is coordinated."
    o.criteria[2].refs = ["I-02.s1"]
    # I-02.s1 has no year: dropped. Then give the initiative sentence a year: kept.
    _, out = run(paths, o)
    assert "a year its references do not contain ('2024')" in report(paths)["dropped_criteria"][0]["reason"]
    extract = json.loads((paths.stages / dr.EXTRACT_NAME).read_text())
    extract["initiatives"][1]["sentences"][0]["text"] = "Since 2024, management coordinates delivery of product changes across many teams."
    (paths.stages / dr.EXTRACT_NAME).write_text(json.dumps(extract))
    _, out = run(paths, o)
    assert report(paths)["dropped_criteria"] == []
    assert "since 2024" in objective(out, "I-02").success_criteria[1].text


def test_may_as_a_verb_is_not_a_month():
    assert guards.date_mentions("Which teams may adopt one shared view first?") == []
    assert guards.date_mentions("May teams adopt one shared view?") == []
    assert guards.date_mentions("Launched in May with the first team.") == [("month", "May")]
    assert guards.date_mentions("Live by May 2027.") == [("month", "May"), ("year", "2027")]


def test_review_flags(paths):
    o = base_draft()
    o.criteria[0].text = "Automation will reduce manual work and improve team efficiency at Lisbon offices."
    o.criteria[3].validation_question = "Which team uses their existing Confluence site most?"
    _, out = run(paths, o)
    types = {f.type for f in objective(out, "I-01").success_criteria[0].flags}
    assert {"outcome_promise", "generic_criterion", "adds_facts", "unsupported_statement"} <= types
    assert "proper noun 'Lisbon'" in " ".join(f.detail for f in objective(out, "I-01").success_criteria[0].flags)
    assert any(f.type == "implies_customer" for f in objective(out, "I-03").success_criteria[0].flags)


@pytest.mark.parametrize("word", ["supporting", "enabling", "Driving"])
def test_goal_clause_flagged_review_only(paths, word):
    o = base_draft()
    o.criteria[1].text = f"Delivery work across teams is combined into one view, {word} faster releases."
    _, out = run(paths, o)
    c = objective(out, "I-02").success_criteria[0]
    assert c.text.startswith("Delivery work") and any(f.type == "goal_clause" and word in f.detail for f in c.flags)
    assert report(paths)["dropped_criteria"] == []


def test_skipped_initiative_reported_with_reason(paths):
    o = base_draft()
    o.criteria = [c for c in o.criteria if c.initiative_id != "I-03"]
    o.skipped = [LLMSkipped(initiative_id="I-03", reason="The benefit lines do not support a measurable criterion.")]
    _, out = run(paths, o)
    entries = report(paths)["skipped_initiatives"]
    assert [(e["item_id"], e["reason"]) for e in entries] == [
        ("I-03", "initiative skipped by model: The benefit lines do not support a measurable criterion.")]
    assert objective(out, "I-03").success_criteria[0].text == CRITERION_BLANK
    assert any("initiatives skipped by model" in w for w in out.warnings)


def test_initiative_missing_from_response_reported(paths):
    o = base_draft()
    o.criteria = [c for c in o.criteria if c.initiative_id != "I-03"]
    o.tasks = [t for t in o.tasks if t.initiative_id != "I-03"]
    _, out = run(paths, o)
    assert [e["reason"] for e in report(paths)["skipped_initiatives"]] == ["initiative skipped by model: no entry in the response"]


def test_all_criteria_dropped_is_not_a_skip(paths):
    o = base_draft()
    o.criteria[3].refs = ["B-01.s1"]  # I-03 criterion citing I-01: dropped by code, not skipped by the model
    run(paths, o)
    assert report(paths)["skipped_initiatives"] == []


def test_saved_response_is_per_initiative_and_old_flat_responses_replay(paths):
    run(paths, base_draft())
    saved = json.loads((dr.latest_response_dir(paths) / "draft.json").read_text())
    assert [e["initiative_id"] for e in saved["output"]["initiatives"]] == ["I-01", "I-02", "I-03"]
    flat = base_draft().model_dump(mode="json")
    flat.pop("skipped")
    assert dr.flatten(flat).criteria == base_draft().criteria  # a run 1-3 response (flat lists) still replays


def test_fallback_blank_criterion_never_empty(paths):
    o = base_draft()
    o.criteria = [c for c in o.criteria if c.initiative_id != "I-03"]
    _, out = run(paths, o)
    c = objective(out, "I-03").success_criteria
    assert len(c) == 1 and c[0].text == CRITERION_BLANK and c[0].owner == "csm" and c[0].provenance == "csm-input"
    assert c[0].needs_baseline is False and c[0].validation_question
    assert any("CSM blank criterion" in f["reason"] for f in report(paths)["fallbacks"])


def test_criteria_cap(paths):
    o = base_draft()
    o.criteria += [crit("I-02", f"Delivery work across teams is combined into one {w} view.", "B-02.s1")
                   for w in ("shared", "single", "common", "joint", "team")]
    _, out = run(paths, o)
    assert len(objective(out, "I-02").success_criteria) == dr.MAX_CRITERIA_PER_OBJECTIVE


# ---------------------------------------------------------------- tasks

def test_tasks_are_filled_from_the_catalog(paths):
    _, out = run(paths, base_draft())
    t = objective(out, "I-01").tasks
    assert [x.task_id for x in t] == ["T-DISCOVERY", "T-DEMO", "T-BASELINES"]  # sorted by window
    assert t[1].text == "Demo automation rules to the team." and t[1].responsible_role == "Customer admin"
    assert t[1].validation_question == "How closely would automation rules fit the way the team works today?"
    assert t[0].text == "Run a discovery call on the work behind 'Unified technology platform'." and t[0].responsible_role == "CSM"
    assert all(x.is_proposed and x.window in ("Day 0-30", "Day 31-60", "Day 61-90") for x in t)
    catalog_texts = {x["text"] for x in dr.load_tasks()["tasks"]}
    for o in out.objectives:
        for x in o.tasks:
            spec = next(c for c in dr.load_tasks()["tasks"] if c["id"] == x.task_id)
            pattern = re.escape(spec["text"]).replace(r"\{initiative\}", ".+").replace(r"\{capability\}", ".+")
            assert re.fullmatch(pattern, x.text) and spec["text"] in catalog_texts


def test_task_capability_must_come_from_the_mapping(paths):
    o = base_draft()
    o.tasks.append(task("I-01", "T-ENABLEMENT", "JIRA-PLANNING-ROADMAPS"))  # not mapped to I-01
    o.tasks.append(task("I-02", "T-PILOT", "JIRA-PLANNING-ROADMAPS"))  # duplicate
    _, out = run(paths, o)
    reasons = " | ".join(r["reason"] for r in report(paths)["rejected_tasks"])
    assert "T-ENABLEMENT needs a capability from this mapping" in reasons and "duplicate T-PILOT" in reasons
    assert [x.task_id for x in objective(out, "I-01").tasks] == ["T-DISCOVERY", "T-DEMO", "T-BASELINES"]


def test_default_task_when_none_valid(paths):
    o = base_draft()
    o.tasks = [t for t in o.tasks if t.initiative_id != "I-03"]
    _, out = run(paths, o)
    t = objective(out, "I-03").tasks
    assert [x.task_id for x in t] == ["T-DISCOVERY", "T-BASELINES"]
    assert t[0].responsible_role == "CSM" and t[0].window == "Day 0-30"


def test_task_catalog_is_free_of_prices_plans_dates_and_promises():
    data = yaml.safe_load(dr.TASKS_FILE.read_text())
    for t in data["tasks"]:
        for text in (t["text"], t["question"]):
            assert not guards.find_currency_amounts(text) and not guards.plan_tier_phrase(text)
            assert not guards.date_mentions(text) and not guards.outcome_promise(text) and not guards.implies_customer(text)
            assert not re.search(r"\d", text)
            assert set(re.findall(r"\{(\w+)\}", text)) <= {"initiative", "capability"}


def test_baselines_task_added_by_code_to_every_objective(paths):
    o = base_draft()
    o.tasks = []  # even with no model tasks at all
    _, out = run(paths, base_draft())
    _, empty = run(paths, o)
    for result in (out, empty):
        for ob in result.objectives:
            b = [t for t in ob.tasks if t.task_id == "T-BASELINES"]
            assert len(b) == 1 and b[0].text == "Agree baselines for this objective's success criteria."
            assert b[0].window == "Day 31-60" and b[0].responsible_role == "CSM"


def test_model_cannot_choose_the_baselines_task(paths):
    llm = FakeDraftLLM(base_draft())
    dr.run_draft(paths, llm, CONFIG, "test", system=SYSTEM)
    assert "T-BASELINES" not in llm.calls[0]["user"]
    assert "T-BASELINES" not in json.dumps(llm.calls[0]["output_model"].model_json_schema())
    o = base_draft()
    o.tasks.append(task("I-02", "T-BASELINES"))  # e.g. a saved response from before the change
    out = dr.assemble(paths, *dr.load_inputs(paths), dr.load_tasks(), o, [], "test", CONFIG,
                      datetime(2026, 10, 13, tzinfo=timezone.utc))
    assert "T-BASELINES is added by code" in report(paths)["rejected_tasks"][0]["reason"]
    assert [t.task_id for t in objective(out, "I-02").tasks].count("T-BASELINES") == 1


@pytest.mark.parametrize("role", ["CSM", "Customer admin", "Services"])
def test_task_text_never_names_the_role(paths, role):
    o = base_draft()
    o.tasks = [task(i, t, c, role=role) for i, t, c in (("I-01", "T-DISCOVERY", ""), ("I-01", "T-WORKFLOW-REVIEW", ""),
               ("I-01", "T-DEMO", "JIRA-AUTOMATION"), ("I-02", "T-PILOT", "JIRA-PLANNING-ROADMAPS"),
               ("I-02", "T-ENABLEMENT", "JIRA-PLANNING-ROADMAPS"), ("I-03", "T-PROGRESS-REVIEW", ""))]
    _, out = run(paths, o)
    for ob in out.objectives:
        for t in ob.tasks:
            assert "with the CSM" not in t.text and f"the {role}" not in t.text and t.responsible_role in (role, "CSM")


def test_unknown_slot_in_catalog_refused(tmp_path):
    f = tmp_path / "tasks.yaml"
    f.write_text("roles: [CSM]\ntasks:\n  - {id: T-X, text: 'Discuss {revenue}', question: 'q'}\n")
    with pytest.raises(dr.DraftError, match="unknown slot"):
        dr.load_tasks(f)


# ---------------------------------------------------------------- run, cost, replay

def test_dry_run_makes_no_call(paths):
    llm = FakeDraftLLM(base_draft())
    estimate, out = dr.run_draft(paths, llm, CONFIG, "test", dry_run=True, system=SYSTEM)
    assert out is None and llm.calls == [] and estimate["cost_usd"] > 0
    assert not (paths.stages / dr.OUTPUT_NAME).exists()


def test_spending_limit(paths):
    tight = ModelsConfig(max_cost_usd_per_run=0.0001, profiles=CONFIG.profiles, prices=CONFIG.prices, prices_as_of="x")
    llm = FakeDraftLLM(base_draft())
    with pytest.raises(dr.DraftError, match="exceeds the limit"):
        dr.run_draft(paths, llm, tight, "test", system=SYSTEM)
    assert llm.calls == []


def test_failed_call_logs_and_saves_nothing(paths):
    with pytest.raises(dr.DraftError):
        dr.run_draft(paths, FakeDraftLLM(base_draft(), fail=True), CONFIG, "test", system=SYSTEM)
    assert not (paths.stages / dr.OUTPUT_NAME).exists()
    last = (paths.stages / dr.USAGE_LOG_NAME).read_text().strip().splitlines()[-1]
    assert json.loads(last)["stage"] == "draft" and json.loads(last)["status"] == "failed"


def test_saved_response_and_replay(paths):
    _, first = run(paths, base_draft())
    saved = dr.latest_response_dir(paths)
    assert (saved / "draft.json").exists()
    again = dr.replay_draft(paths, CONFIG, saved)
    assert again.replayed_from and again.total_cost_usd == 0.0
    strip = lambda o: [x.model_dump(exclude={"id"}) for x in o.objectives]  # noqa: E731
    assert [o.title for o in again.objectives] == [o.title for o in first.objectives]
    assert strip(again) == strip(first)


def test_map_provenance_recorded(paths):
    _, out = run(paths, base_draft())
    assert out.map_profile == "test" and out.map_replayed_from is None


# ---------------------------------------------------------------- prompt

def test_draft_prompt_rules():
    prompt = " ".join(dr.system_prompt("Test Co").split())
    assert "Test Co is not assumed to use these products" in prompt and "{company}" not in prompt.replace("{capability}", "")
    assert "you never write task text" in prompt
    assert '"Improve efficiency"' in prompt and "Do not write a target number or any other number" in prompt
    assert "It must cite at least one `<benefit>` ref" in prompt and "be lagging" in prompt
    assert "Never mention plans, tiers, editions, prices" in prompt and "No dates" in prompt
    assert "Do not add facts about the company" in prompt and '"will reduce"' in prompt
    assert ("State only what the referenced benefit line says the product does and what will be measured. Do not add a "
            'clause that ties it to a company goal (no "supporting ...", "enabling ...", or "driving ...").') in prompt
    assert "State only the measure drawn from the referenced sentence." in prompt
    assert "one entry in `initiatives` for every initiative in `<initiatives>`" in prompt
    assert ("Every initiative gets 2 to 3 criteria, including at least one product_usage criterion tied to a `<benefit>` "
            "line of that initiative.") in prompt
    assert "return an empty criteria list and say why in skip_reason" in prompt


def test_run_draft_uses_prompt_file_by_default(paths):
    llm = FakeDraftLLM(base_draft())
    dr.run_draft(paths, llm, CONFIG, "test")
    assert llm.calls[0]["system"].startswith("You draft success criteria and pick tasks for a hypothetical")


def test_every_config_capability_has_a_short_display_name():
    config = yaml.safe_load((dr.PROJECT_ROOT / "config" / "product_atlassian_jira_confluence.yaml").read_text())
    names = dr.load_tasks()["display_names"]
    assert {c["id"] for c in config["capabilities"]} == set(names)
    for cid, name in names.items():
        assert len(name.split()) <= 6 and not guards.plan_tier_phrase(name) and not guards.find_currency_amounts(name)


def test_demo_and_pilot_cover_every_mapped_capability(paths):
    m = json.loads((paths.stages / dr.MAP_NAME).read_text())
    b2 = next(b for b in m["key_benefits"] if b["initiative_id"] == "I-02")
    b2["capability_ids"] = ["JIRA-PLANNING-ROADMAPS", "JIRA-WORKFLOWS", "JIRA-AUTOMATION"]
    (paths.stages / dr.MAP_NAME).write_text(json.dumps(m))
    o = base_draft()
    o.tasks.append(task("I-02", "T-DEMO", "JIRA-WORKFLOWS", window="Day 31-60"))
    o.tasks.append(task("I-02", "T-DEMO", "JIRA-AUTOMATION", window="Day 31-60"))  # a second demo pick is a duplicate
    _, out = run(paths, o)
    tasks = {t.task_id: t for t in objective(out, "I-02").tasks}
    assert tasks["T-DEMO"].text == "Demo cross-team plans, shared workflow schemes, and automation rules to the team."
    assert tasks["T-PILOT"].text == "Propose a pilot of cross-team plans, shared workflow schemes, and automation rules with one team."
    assert [t.task_id for t in objective(out, "I-02").tasks].count("T-DEMO") == 1


@pytest.mark.parametrize("names,joined", [(["a"], "a"), (["a", "b"], "a and b"), (["a", "b", "c"], "a, b, and c")])
def test_join_names(names, joined):
    assert dr.join_names(names) == joined


def test_templates_avoid_verb_agreement_with_the_capability():
    for t in dr.load_tasks()["tasks"]:
        for text in (t["text"], t["question"]):
            assert not re.search(r"\{capability\}\s+(?:is|are|does|do|has|have|matches|match)\b", text)
            assert not re.search(r"\b(?:Does|Do|Is|Are)\s+\{capability\}", text)
