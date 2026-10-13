"""Map stage with a fake model: no API calls. Uses the real capability config and synthetic initiatives."""

import copy
import json
import random
from datetime import date, datetime, timezone

import pytest

from success_plan import guards
from success_plan.llm import LLMError, LLMResult, ModelsConfig
from success_plan.models import (
    PLAN_CONFIRMATION_BLANK, ExtractItem, ExtractOutput, LLMBenefitSentence, LLMClaimCitation, LLMMapOutput, LLMMapping,
    SentenceRecord,
)
from success_plan.paths import CompanyPaths
from success_plan.stages import map as mp

CONFIG = ModelsConfig(
    max_cost_usd_per_run=3.0,
    profiles={"test": {"map": {"model": "claude-sonnet-5-5", "effort": "medium", "max_tokens": 16000, "expected_output_tokens": 6000}}},
    prices={"claude-sonnet-5-5": {"input": 2.0, "output": 10.0, "cache_read": 0.2, "cache_write": 2.5}},
    prices_as_of="2026-10-06",
)
TODAY = date(2026, 10, 12)
SYSTEM = "TEST SYSTEM PROMPT for {company}"

# Verbatim claim phrases from the real config (see build_claims).
AUTOMATION_D1 = "A no-code rule builder for if-this-then-that rules triggered by events"
PLANS_D1 = "Plans combine work items from boards, spaces, and filters into one view"
ADMIN_D2 = "audit logs of important changes, sandboxes and release tracks for controlled change management"
KB_D1 = "Collaborative pages with real-time editing, in-line comments, and version history"


def initiative(id_, title, sentences, provenance="sourced", labels=()):
    return ExtractItem(
        id=id_, kind="initiative", title=title, text=" ".join(sentences), provenance=provenance, labels=list(labels),
        flags=[], citations=[], rejected_citations=[], model_inference=False, validation_question="q",
        sentences=[SentenceRecord(text=s, citations=[]) for s in sentences],
    )


INITIATIVES = [
    initiative("I-01", "Unified technology platform", ["The company runs its brands on a shared technology platform to release changes faster."]),
    initiative("I-02", "Cross-team delivery planning", ["Management coordinates delivery of product changes across many teams."]),
    initiative("I-03", "B2B growth", ["The company plans to grow its B2B business with new partners."], provenance="inferred",
               labels=["news headline only"]),
    initiative("I-04", "Restructuring", ["The company committed to restructuring actions to recalibrate resources."]),
]


@pytest.fixture
def paths(tmp_path) -> CompanyPaths:
    return write_extract(tmp_path)


def write_extract(tmp_path, challenges=(), changes=()) -> CompanyPaths:
    """A temp company folder holding the test Extract output (also used by the Draft and Render tests)."""
    p = CompanyPaths(root=tmp_path, slug="testco")
    p.stages.mkdir(parents=True)
    p.raw.mkdir(parents=True)
    extract = ExtractOutput(
        generated_at=datetime(2026, 10, 12, tzinfo=timezone.utc), company="Test Co", profile="final",
        retrieve_generated_at=datetime(2026, 10, 12, tzinfo=timezone.utc), prices_as_of="2026-10-06",
        initiatives=INITIATIVES, challenges=list(challenges), changes=list(changes), usage=[], total_cost_usd=0.0, warnings=[],
    )
    (p.stages / mp.EXTRACT_NAME).write_text(extract.model_dump_json(indent=2))
    return p


def claim(cid, evidence):
    return LLMClaimCitation(claim_id=cid, evidence=evidence)


def sent(text, *claims):
    return LLMBenefitSentence(text=text, claims=list(claims))


def mapping(iid, fit, *sentences, gap_ids=(), no_fit_reason="", rationale=None, question="Is this a fit?"):
    return LLMMapping(initiative_id=iid, fit=fit, sentences=list(sentences), gap_ids=list(gap_ids), no_fit_reason=no_fit_reason,
                      fit_rationale=rationale or "The initiative's delivery work is the kind of work this capability supports.",
                      validation_question=question)


def base_output():
    return LLMMapOutput(mappings=[
        mapping("I-01", "partial", sent("Jira Automation offers a no-code rule builder for rules triggered by events.",
                                        claim("JIRA-AUTOMATION#d1", AUTOMATION_D1))),
        mapping("I-02", "full", sent("Plans combine work items from boards, spaces, and filters into one view.",
                                     claim("JIRA-PLANNING-ROADMAPS#d1", PLANS_D1)),
                rationale="Coordinating delivery across many teams is what Plans combine work items into one view for."),
        mapping("I-03", "partial", sent("Pages support real-time editing, in-line comments, and version history.",
                                        claim("CONF-KNOWLEDGE-BASE#d1", KB_D1))),
        mapping("I-04", "no_fit", gap_ids=["GAP-FINANCIAL-PLANNING"], no_fit_reason="Restructuring is an organizational and financial matter."),
    ])


class FakeLLM:
    def __init__(self, output, fail=False):
        self.output, self.fail, self.calls = output, fail, []

    def complete(self, *, system, user, output_model, settings):
        self.calls.append({"system": system, "user": user, "output_model": output_model})
        if self.fail:
            raise LLMError("simulated refusal")
        parsed = output_model.model_validate(self.output.model_dump(mode="json"))  # must fit the enum-restricted format
        return LLMResult(parsed=parsed, model=settings.model, stop_reason="end_turn", input_tokens=12000, output_tokens=5000)


def run(paths, output, **kw):
    return mp.run_map(paths, FakeLLM(output), CONFIG, "test", system=SYSTEM, today=TODAY, **kw)


def report(paths):
    return json.loads((paths.stages / mp.GUARD_REPORT_NAME).read_text())


def by_initiative(out):
    return {b.initiative_id: b for b in out.key_benefits}


# ---------------------------------------------------------------- claims from the config

def test_plan_tier_lines_and_clauses_withheld_lists_kept():
    claims, withheld, _ = mp.build_claims(mp.load_config())
    texts = " ".join(c["text"] for c in claims.values())
    assert "by plan" not in texts and "Premium" not in texts and "Enterprise allows" not in texts
    assert "JIRA-ADMIN-GOVERNANCE#d2" in claims and "audit logs" in claims["JIRA-ADMIN-GOVERNANCE#d2"]["text"]
    assert "Plans combine work items" in claims["JIRA-PLANNING-ROADMAPS#d1"]["text"]  # the feature name is not a tier
    assert any(w.text.startswith("Lower plans") for w in withheld)


def test_model_never_sees_plan_availability_addresses_or_withheld_text(paths):
    llm = FakeLLM(base_output())
    mp.run_map(paths, llm, CONFIG, "test", system=SYSTEM, today=TODAY)
    user = llm.calls[0]["user"]
    cfg = mp.load_config()
    for cap in cfg["capabilities"]:
        assert cap["plan_availability"].split(".")[0] not in user
        for hint in cap.get("addresses", []):
            assert hint not in user
    assert "Lower plans have basic roadmaps only" not in user and "Step allowances depend on plan tier" not in user
    assert "<gap id=\"GAP-FINANCIAL-PLANNING\">" in user and '<initiative id="I-01"' in user


def test_output_format_allows_only_real_ids(paths):
    llm = FakeLLM(base_output())
    mp.run_map(paths, llm, CONFIG, "test", system=SYSTEM, today=TODAY)
    model = llm.calls[0]["output_model"]
    bad = base_output().model_dump(mode="json")
    bad["mappings"][0]["sentences"][0]["claims"][0]["claim_id"] = "JIRA-TELEPORTATION#d1"
    with pytest.raises(Exception):
        model.model_validate(bad)
    bad = base_output().model_dump(mode="json")
    bad["mappings"][3]["gap_ids"] = ["GAP-MADE-UP"]
    with pytest.raises(Exception):
        model.model_validate(bad)


# ---------------------------------------------------------------- assembly

def test_basic_mapping_and_fields(paths):
    _, out = run(paths, base_output())
    b = by_initiative(out)
    assert out.fit_distribution == {"full": 1, "partial": 2, "no_fit": 1}
    i01 = b["I-01"]
    assert i01.fit == "partial" and i01.capability_ids == ["JIRA-AUTOMATION"] and i01.provenance == "inferred"
    assert i01.text == "Jira Automation offers a no-code rule builder for rules triggered by events."
    cit = i01.sentences[0].citations[0]
    assert cit.source_urls and cit.verified_on == date(2026, 10, 12) and cit.verified_by is None
    assert i01.plan_confirmation == PLAN_CONFIRMATION_BLANK == "[CSM to confirm which plan includes these capabilities]"
    assert all(x.plan_confirmation == PLAN_CONFIRMATION_BLANK for x in out.key_benefits)
    assert i01.fit_rationale_label == "inference"
    i04 = b["I-04"]
    assert i04.fit == "no_fit" and i04.capability_ids == [] and i04.gap_ids == ["GAP-FINANCIAL-PLANNING"] and i04.text == ""


def test_extract_labels_and_provenance_carry_through(paths):
    _, out = run(paths, base_output())
    i03 = by_initiative(out)["I-03"]
    assert i03.labels == ["news headline only"] and i03.initiative_provenance == "inferred" and i03.provenance == "inferred"


def test_unverified_claim_drops_sentence_and_downgrades(paths):
    o = base_output()
    o.mappings[0].sentences = [sent("Automation writes release notes from commit messages.",
                                    claim("JIRA-AUTOMATION#d1", "automation writes release notes from commit messages always"))]
    _, out = run(paths, o)
    i01 = by_initiative(out)["I-01"]
    assert i01.fit == "no_fit" and i01.no_fit_reason == "no verified capability claim" and i01.text == ""
    assert i01.dropped_sentences[0].reason.startswith("no claim verified: evidence not found")
    r = report(paths)
    assert r["downgraded"][0]["reason"] == "partial with no verified capability claim; recorded as no_fit"


def test_no_fit_without_gap_or_reason_excluded(paths):
    o = base_output()
    o.mappings[3].gap_ids, o.mappings[3].no_fit_reason = [], ""
    _, out = run(paths, o)
    assert "I-04" not in by_initiative(out)
    assert report(paths)["excluded"][0]["reason"] == "no_fit without a GAP-* reason or an explanation"


def test_no_fit_with_explanation_only_is_kept(paths):
    o = base_output()
    o.mappings[3].gap_ids = []
    _, out = run(paths, o)
    assert by_initiative(out)["I-04"].no_fit_reason.startswith("Restructuring is")


def test_plan_and_price_sentences_dropped(paths):
    o = base_output()
    o.mappings[1].sentences += [
        sent("Plans are available on the Premium plan for cross-team planning.", claim("JIRA-PLANNING-ROADMAPS#d1", PLANS_D1)),
        sent("Plans cost $15 per user for cross-team planning.", claim("JIRA-PLANNING-ROADMAPS#d1", PLANS_D1)),
    ]
    _, out = run(paths, o)
    i02 = by_initiative(out)["I-02"]
    assert "Premium" not in i02.text and "$15" not in i02.text
    reasons = [d.reason for d in i02.dropped_sentences]
    assert reasons[0].startswith("states plan-level availability") and reasons[1] == "states a price or currency amount"


def test_feature_not_in_config_flagged(paths):
    o = base_output()
    o.mappings[0].sentences = [sent("Jira Automation generates release notes and predicts sprint risk from events.",
                                    claim("JIRA-AUTOMATION#d1", AUTOMATION_D1))]
    _, out = run(paths, o)
    weak = [f.detail for f in by_initiative(out)["I-01"].flags if f.type == "unsupported_statement"]
    assert len(weak) == 1 and "release" in weak[0]


def test_outcome_promise_and_customer_framing_flagged(paths):
    o = base_output()
    o.mappings[0].sentences = [sent("A no-code rule builder for event-triggered rules ensures faster releases.",
                                    claim("JIRA-AUTOMATION#d1", AUTOMATION_D1))]
    o.mappings[1].validation_question = "How does the team use its existing Jira today?"
    _, out = run(paths, o)
    b = by_initiative(out)
    assert any(f.type == "outcome_promise" and "ensures" in f.detail for f in b["I-01"].flags)
    assert any(f.type == "implies_customer" for f in b["I-02"].flags)


def test_rationale_adding_company_facts_flagged(paths):
    o = base_output()
    o.mappings[1].fit_rationale = "The company has 400 engineers in Lisbon who missed three launches last year."
    _, out = run(paths, o)
    detail = next(f.detail for f in by_initiative(out)["I-02"].flags if f.type == "rationale_adds_facts")
    assert "figure '400'" in detail and "proper noun 'Lisbon'" in detail


def rationale_flags(paths, rationale, iid="I-02", index=1):
    o = base_output()
    o.mappings[index].fit_rationale = rationale
    _, out = run(paths, o)
    return [f.detail for f in by_initiative(out)[iid].flags if f.type == "rationale_adds_facts"], by_initiative(out)[iid]


def test_rationale_judgment_wording_not_flagged(paths):
    # Abstract judgment words that are in neither the initiative nor the claims are not added facts.
    details, b = rationale_flags(paths, "Plans support the coordination activity behind this commercial initiative, "
                                        "which sits outside the capabilities' core purpose.")
    assert details == []
    assert b.fit_rationale_support is not None and b.fit_rationale_support < 0.6  # old score kept for review


def test_rationale_inference_prefix_stripped(paths):
    details, b = rationale_flags(paths, "Inference: Plans combine work items into one view for this coordination work.")
    assert b.fit_rationale == "Plans combine work items into one view for this coordination work."
    assert b.fit_rationale_label == "inference" and details == []
    _, b = rationale_flags(paths, "**Inference** - Plans combine work items into one view.")
    assert b.fit_rationale == "Plans combine work items into one view."


def test_rationale_plan_and_price_terms_flagged_unless_in_sources(paths):
    details, _ = rationale_flags(paths, "This fit depends on the license and the edition the company buys.")
    assert "plan or price term 'license'" in details[0] and "plan or price term 'edition'" in details[0]
    details, _ = rationale_flags(paths, "The coordination work fits, though this depends on the Premium plan.")
    assert "plan tier 'Premium plan'" in details[0] and "proper noun 'Premium'" in details[0]
    details, _ = rationale_flags(paths, "The coordination work fits at $8 a month for each team.")
    assert any("price" in d for d in details) and "figure '8'" in details[0]
    # "Plans" is the cited claim's own feature name, so it is not an added plan term.
    details, _ = rationale_flags(paths, "Coordinating delivery across teams is what plans combine work items into one view for.")
    assert details == []


def test_rationale_proper_nouns_from_config_or_aliases_not_flagged(paths):
    (paths.stages / "01_retrieve.json").write_text(json.dumps({"news": {"aliases": ["Test Co", "TSTC", "Brandly"]}}))
    details, _ = rationale_flags(paths, "Shared work across the Brandly and TSTC teams fits what Jira and Confluence plans combine.")
    assert details == []
    details, _ = rationale_flags(paths, "Shared work across the Brandly teams fits, as Madrid does elsewhere.")
    assert details and "proper noun 'Madrid'" in details[0] and "Brandly" not in details[0].split("(")[0]


def test_unconfirmed_claim_marks_benefit(paths):
    cfg = copy.deepcopy(mp.load_config())
    cap = next(c for c in cfg["capabilities"] if c["id"] == "JIRA-AUTOMATION")
    cap["unconfirmed"] = ["Rules can call an external webhook to notify other systems when work changes."]
    o = base_output()
    o.mappings[0].sentences = [sent("Rules can call an external webhook to notify other systems.",
                                    claim("JIRA-AUTOMATION#u1", "Rules can call an external webhook to notify other systems"))]
    _, out = mp.run_map(paths, FakeLLM(o), CONFIG, "test", system=SYSTEM, today=TODAY, config=cfg)
    i01 = by_initiative(out)["I-01"]
    assert i01.sentences[0].citations[0].unconfirmed and "unconfirmed product detail" in i01.labels


def test_unknown_duplicate_and_unmapped(paths):
    o = base_output()
    o.mappings.append(copy.deepcopy(o.mappings[0]))  # duplicate I-01
    o.mappings = [m for m in o.mappings if m.initiative_id != "I-03"]  # I-03 unmapped
    _, out = run(paths, o)
    r = report(paths)
    assert [e["reason"] for e in r["excluded"]] == ["duplicate mapping for this initiative"]
    assert r["unmapped_initiatives"] == ["I-03"]


def test_stale_verification_warning(paths):
    _, out = mp.run_map(paths, FakeLLM(base_output()), CONFIG, "test", system=SYSTEM, today=date(2027, 3, 1))
    assert any("older than 90 days" in w for w in out.warnings)
    _, out = run(paths, base_output())
    assert not any("older than 90 days" in w for w in out.warnings)


def test_property_text_is_join_of_kept_sentences(paths):
    valid = [("JIRA-AUTOMATION#d1", AUTOMATION_D1), ("JIRA-PLANNING-ROADMAPS#d1", PLANS_D1),
             ("JIRA-ADMIN-GOVERNANCE#d2", ADMIN_D2), ("CONF-KNOWLEDGE-BASE#d1", KB_D1)]
    invalid = [("JIRA-AUTOMATION#d1", "this evidence is not in the automation claim text"),
               ("JIRA-AUTOMATION#d1", "short")]
    rng = random.Random(20261012)
    for trial in range(60):
        mappings = []
        for iid in ("I-01", "I-02", "I-03", "I-04"):
            sentences = []
            for j in range(rng.randint(0, 3)):
                cits = [claim(*rng.choice(valid if rng.random() < 0.6 else invalid)) for _ in range(rng.randint(0, 3))]
                text = f"Capability statement t{trial} {iid} s{j} zq{rng.randint(1000, 9999)}"
                if rng.random() < 0.1:
                    text += " on the Enterprise plan"
                sentences.append(sent(text + ".", *cits))
            fit = rng.choice(["full", "partial", "no_fit"])
            mappings.append(mapping(iid, fit, *sentences, gap_ids=["GAP-CICD"] if fit == "no_fit" else []))
        trial_paths = CompanyPaths(root=paths.root / f"t{trial}", slug="testco")
        trial_paths.stages.mkdir(parents=True)
        trial_paths.raw.mkdir(parents=True)
        (trial_paths.stages / mp.EXTRACT_NAME).write_text((paths.stages / mp.EXTRACT_NAME).read_text())
        _, out = run(trial_paths, LLMMapOutput(mappings=mappings))
        for b in out.key_benefits:
            assert b.text == " ".join(s.text for s in b.sentences)
            assert all(s.citations for s in b.sentences)
            assert not guards.plan_tier_phrase(b.text)
            for d in b.dropped_sentences:
                assert d.text not in b.text
            if b.fit == "no_fit":
                assert b.text == "" and b.capability_ids == []
            else:
                assert b.capability_ids and b.sentences


# ---------------------------------------------------------------- run mechanics

def test_dry_run_makes_no_call(paths):
    llm = FakeLLM(base_output())
    estimate, out = mp.run_map(paths, llm, CONFIG, "test", dry_run=True, system=SYSTEM)
    assert out is None and llm.calls == [] and estimate["input_tokens"] > 0
    assert not (paths.stages / mp.OUTPUT_NAME).exists()


def test_spending_limit(paths):
    llm = FakeLLM(base_output())
    with pytest.raises(mp.MapError, match="No API call was made"):
        mp.run_map(paths, llm, ModelsConfig(0.01, CONFIG.profiles, CONFIG.prices, CONFIG.prices_as_of), "test", system=SYSTEM)
    assert llm.calls == []


def test_failed_call_logs_and_saves_nothing(paths):
    with pytest.raises(mp.MapError, match="simulated refusal"):
        mp.run_map(paths, FakeLLM(base_output(), fail=True), CONFIG, "test", system=SYSTEM)
    assert not (paths.stages / mp.OUTPUT_NAME).exists()
    log = [json.loads(l) for l in (paths.stages / mp.USAGE_LOG_NAME).read_text().splitlines()]
    assert log[-1]["stage"] == "map" and log[-1]["status"] == "failed"


def test_saved_response_and_replay(paths):
    _, original = run(paths, base_output())
    assert (mp.latest_response_dir(paths) / "map.json").exists()
    replayed = mp.replay_map(paths, CONFIG, today=TODAY)
    strip = lambda o: [(b.initiative_id, b.fit, b.text, [f.type for f in b.flags]) for b in o.key_benefits]
    assert strip(replayed) == strip(original) and replayed.total_cost_usd == 0.0


# ---------------------------------------------------------------- prompt

def test_map_prompt_rules():
    prompt = " ".join(mp.system_prompt("Test Co").split())
    assert "full: the initiative is itself about the activity the capability performs." in prompt
    assert ("partial: the capability supports the work needed to execute the initiative but does not deliver its outcome."
            in prompt)
    assert "no_fit: neither." in prompt and "Do not stretch a capability" in prompt
    assert "Never say it will deliver a result" in prompt and '"ensures"' in prompt
    assert "Never mention plans, tiers, editions" in prompt and "at least 8 words, aim for 10" in prompt
    assert "fit_rationale: one sentence" in prompt and "labeled as an inference" in prompt
    assert 'Do not start it with "Inference:"' in prompt
    assert "Test Co is not assumed to use these products" in prompt and "{company}" not in prompt


def test_run_map_uses_prompt_file_by_default(paths):
    llm = FakeLLM(base_output())
    mp.run_map(paths, llm, CONFIG, "test", today=TODAY)
    assert llm.calls[0]["system"].startswith("You map Test Co's business initiatives")



# ---------------------------------------------------------------- review sheet

def test_map_review_sheet(paths):
    o = base_output()
    o.mappings[1].sentences.append(sent("Plans are on the Premium plan.", claim("JIRA-PLANNING-ROADMAPS#d1", PLANS_D1)))
    run(paths, o)
    sheet = (paths.raw / mp.REVIEW_SHEET_NAME).read_text()
    assert "Fit rationale (inference)" in sheet and "against the initiative and cited claims" in sheet
    assert "<mark>A no-code rule builder for if-this-then-that rules triggered by events</mark>" in sheet
    assert "Full config claim" in sheet and "verified 2026-10-12" in sheet and "https://www.atlassian.com/" in sheet
    assert "<s>Plans are on the Premium plan.</s>" in sheet and "Reason: states plan-level availability" in sheet
    assert "Claims withheld from the model (12)" in sheet and "Lower plans have basic roadmaps only" in sheet
    assert "[CSM to confirm which plan includes these capabilities]" in sheet
    assert "Verification chain" in sheet and "No-fit reason: Restructuring is" in sheet


# Capabilities whose config lists no Jira-specific source page; the first URL stays as verified.
NO_JIRA_SPECIFIC_SOURCE = {"JIRA-ANALYTICS-ENTERPRISE"}


def test_jira_capabilities_list_a_jira_specific_source_first():
    """Render links each capability to its first source URL, so a Jira capability should not point at a Confluence page."""
    config = mp.load_config()
    for cap in config["capabilities"]:
        if cap["product"] != "Jira" or cap["id"] in NO_JIRA_SPECIFIC_SOURCE:
            continue
        first = cap["source_urls"][0]
        assert "/jira" in first and "confluence" not in first, f"{cap['id']}: {first}"
    rovo = next(c for c in config["capabilities"] if c["id"] == "JIRA-AI-ROVO")
    assert rovo["source_urls"][0] == "https://www.atlassian.com/software/jira"
