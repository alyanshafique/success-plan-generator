"""LEGACY item-level format: the rules applied when replaying saved dev-2/dev-3 responses.

Read-only path; remove with extract_legacy.py after the sentence format has a baseline run.
"""

import json
import shutil
from datetime import date
from pathlib import Path

import pytest

from success_plan.llm import CallSettings, LLMError, LLMResult, ModelsConfig, cost_usd, load_models_config
from success_plan.models import (
    ExtractOutput, LLMCitation,
    LegacyLLMChallenge as LLMChallenge, LegacyLLMChallengesChangesOutput as LLMChallengesChangesOutput,
    LegacyLLMChange as LLMChange, LegacyLLMInitiative as LLMInitiative, LegacyLLMInitiativesOutput as LLMInitiativesOutput,
)
from success_plan.paths import CompanyPaths
from success_plan.stages import extract as ex

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"
MANIFEST = ROOT / "data" / "expedia" / "stages" / "01_retrieve.json"

# An ineligible (unmatched) headline from the manifest, planted in the chunks file to prove it is filtered out.
INELIGIBLE_NEWS_ID = next(
    h["chunk_id"] for h in json.loads(MANIFEST.read_text())["news"]["headlines"] if not h["eligible_for_extract"]
)


_HEADS = {h["headline"]: h["chunk_id"] for h in json.loads(MANIFEST.read_text())["news"]["headlines"]}
LAYLA_HEADLINE = "Expedia Acquired AI Trip - Planner Layla : Exclusive"
LAYOFFS_HEADLINE = "Expedia cuts 58 Seattle jobs in second round of layoffs this year"
LAYLA_ID, LAYOFFS_ID = _HEADS[LAYLA_HEADLINE], _HEADS[LAYOFFS_HEADLINE]

# SYNTHETIC filing chunk (not Expedia text) used only to test headline corroboration.
SYNTHETIC_RELEASE = {
    "source_id": "8k-0001324424-26-000051-ex991", "source_type": "filing",
    "title": "SYNTHETIC test earnings release", "url": "https://example.com/synthetic", "date": "2026-08-05",
    "location": "Form 8-K Exhibit 99.1", "event_date": "2026-08-05", "signal": "primary",
    "chunk_id": "8k-0001324424-26-000051-ex991-p999",
    "text": "During the quarter we completed the acquisition of Layla, an AI trip planner, and eliminated 58 roles in 2026.",
    "word_count": 19,
}
SYN = SYNTHETIC_RELEASE["chunk_id"]


@pytest.fixture
def paths(tmp_path) -> CompanyPaths:
    p = CompanyPaths(root=tmp_path, slug="expedia")
    p.stages.mkdir(parents=True)
    p.raw.mkdir(parents=True)
    shutil.copy(MANIFEST, p.stages / "01_retrieve.json")
    lines = (FIXTURES / "expe_chunks_excerpt.jsonl").read_text().splitlines()[1:]
    planted = {
        "source_id": INELIGIBLE_NEWS_ID, "source_type": "news", "title": "Hotel Stocks To Follow Now", "url": "https://x/1",
        "date": "2026-09-30", "location": "x", "event_date": None, "signal": "weak", "chunk_id": INELIGIBLE_NEWS_ID,
        "text": "Hotel Stocks To Follow Now", "word_count": 5,
    }
    manifest = json.loads(MANIFEST.read_text())
    real_heads = [h for h in manifest["news"]["headlines"] if h["chunk_id"] in (LAYLA_ID, LAYOFFS_ID)]
    heads = [
        {"source_id": h["chunk_id"], "source_type": "news", "title": h["headline"], "url": h["url"], "date": h["date"],
         "location": h["domain"], "event_date": None, "signal": "weak", "chunk_id": h["chunk_id"], "text": h["headline"],
         "word_count": len(h["headline"].split())}
        for h in real_heads
    ]
    p.chunks_file.write_text("\n".join(lines + [json.dumps(planted), json.dumps(SYNTHETIC_RELEASE)] + [json.dumps(h) for h in heads]) + "\n")
    return p


CONFIG = ModelsConfig(
    max_cost_usd_per_run=3.0,
    profiles={"test": {"extract": {"model": "claude-sonnet-5-5", "effort": "medium", "max_tokens": 32000,
                                   "expected_output_tokens": 12000}}},
    prices={"claude-sonnet-5-5": {"input": 2.0, "output": 10.0, "cache_read": 0.2, "cache_write": 2.5}},
    prices_as_of="2026-10-06",
)


def cit(chunk_id, evidence):
    return LLMCitation(chunk_id=chunk_id, evidence=evidence)


def initiative(title, citations, text="Paraphrased description.", model_inference=False):
    return LLMInitiative(title=title, text=text, theme="growth", citations=citations, model_inference=model_inference,
                         validation_question="Is this still a priority?")


def challenge(title, citations, text="Paraphrased challenge."):
    return LLMChallenge(title=title, text=text, category="competition", citations=citations, model_inference=False,
                        validation_question="Is this affecting investment?")


def change(title, citations, text="The Chief Financial Officer role changed."):
    return LLMChange(title=title, text=text, change_type="leadership_change", citations=citations, model_inference=False,
                     validation_question="Has this changed decision-making?")


ADS = "Expedia Group (“EG”) Advertising is responsible for generating advertising revenue"
BOOKINGS_ROW = "Total gross bookings $119,590 $110,921 $104,079"
RELEASE_ROW = "Gross Bookings | $33,928 | $30,409 | 12%"
RISK = "These risks could have a material adverse effect on our alternative accommodations business"
REORG = "Expedia Cuts Eight Executives in AI - Focused Reorganization"
ITEM_502 = "Item 5.02. Departure of Directors or Certain Officers; Election of Directors"


def good_outputs(n_initiatives: int = 0):
    initiatives = [
        initiative("Grow advertising revenue", [cit("10k-item7-p008", ADS)],
                   text="Advertising and media revenue reached $758 million in 2025."),
        initiative("Gross bookings growth", [cit("10k-item7-p025", BOOKINGS_ROW), cit("8k-0001324424-26-000051-ex991-p002", RELEASE_ROW)]),
        initiative("Risk-based initiative", [cit("10k-item1a-p040", RISK)]),
        initiative("Invented source", [cit("10k-item9-p001", "this chunk id does not exist anywhere at all")]),
        initiative("Untraceable amount", [cit("10k-item7-p008", ADS)], text="The company plans to spend $2 billion on ads."),
    ]
    initiatives += [initiative(f"Extra {i}", [cit("10k-item7-p008", ADS)]) for i in range(n_initiatives)]
    challenges = [
        challenge("Executive reorganization", [cit("news-gdelt-012", REORG)]),
        challenge("Executive reorganization with filing", [cit("news-gdelt-012", REORG), cit("8k-0001324424-26-000016-main-p003", ITEM_502)]),
        challenge("Analyst downgrade", [cit("news-gdelt-004", "Morgan Stanley Cuts Expedia ( EXPE ) to Underweight as Rivals Pull Ahead on Users")]),
        challenge("Release only", [cit("8k-0001324424-26-000051-ex991-p002", RELEASE_ROW)]),
        challenge("Alternative accommodations risk", [cit("10k-item1a-p040", RISK)]),
        challenge("Ineligible headline", [cit(INELIGIBLE_NEWS_ID, "Hotel Stocks To Follow Now")]),
    ]
    changes = [
        change("CFO role change", [cit("8k-0001324424-26-000016-main-p003", ITEM_502)]),
        change("Named person", [cit("8k-0001324424-26-000016-main-p003", ITEM_502)], text="Mr. Smith will step down."),
    ]
    return {
        LLMInitiativesOutput: LLMInitiativesOutput(initiatives=initiatives),
        LLMChallengesChangesOutput: LLMChallengesChangesOutput(challenges=challenges, changes=changes),
    }


class FakeLLM:
    def __init__(self, outputs, fail_on=None):
        self.outputs, self.fail_on, self.calls = outputs, fail_on, []

    def complete(self, *, system, user, output_model, settings):
        self.calls.append({"system": system, "user": user, "output_model": output_model})
        if output_model is self.fail_on:
            raise LLMError("simulated refusal")
        return LLMResult(parsed=self.outputs[output_model], model=settings.model, stop_reason="end_turn",
                         input_tokens=40000, output_tokens=10000)


_RUN_COUNTER = [0]


def run(paths, llm, config=CONFIG):
    """Legacy outputs are only reachable through replay: save them as a legacy response, then replay."""
    _RUN_COUNTER[0] += 1
    run_dir = paths.raw / ex.RESPONSES_DIR / f"extract-20260101T0000{_RUN_COUNTER[0]:02d}Z-legacytest"
    run_dir.mkdir(parents=True, exist_ok=True)
    for name, model in (("initiatives", LLMInitiativesOutput), ("challenges_changes", LLMChallengesChangesOutput)):
        record = {"call": name, "profile": "test", "model": "claude-sonnet-5-5", "effort": "medium",
                  "usage": {"input_tokens": 40000, "output_tokens": 10000}, "output": llm.outputs[model].model_dump(mode="json")}
        (run_dir / f"{name}.json").write_text(json.dumps(record))
    out = ex.replay_extract(paths, config, run_dir)
    assert out.response_format == "legacy" and all(i.legacy for i in out.initiatives + out.challenges + out.changes)
    return None, out


def by_title(items):
    return {i.title: i for i in items}


# ---------------------------------------------------------------- inputs





# ---------------------------------------------------------------- post-processing

def test_initiatives(paths):
    _, out = run(paths, FakeLLM(good_outputs()))
    items = by_title(out.initiatives)

    ads = items["Grow advertising revenue"]
    assert ads.provenance == "sourced" and ads.flags == [] and ads.citations[0].url.startswith("https://www.sec.gov/")

    growth = items["Gross bookings growth"]  # 10-K and newer earnings release: newest first, both noted
    assert [c.source_id for c in growth.citations] == ["8k-0001324424-26-000051-ex991", "10k-item7"]
    assert growth.sources_note.startswith("Newer source preferred: earnings release filed 2026-08-05")

    risk = items["Risk-based initiative"]
    assert risk.provenance == "inferred" and risk.rejected_citations[0].reason == "Risk Factors cannot support an initiative"
    assert [f.type for f in risk.flags] == ["evidence_not_found", "partial_support"]

    invented = items["Invented source"]
    assert invented.provenance == "inferred" and invented.rejected_citations[0].reason == "unknown chunk id"

    money = items["Untraceable amount"]
    assert "price_review" in [f.type for f in money.flags]  # kept and flagged, not deleted


def test_challenges_headline_only_and_corroboration(paths):
    _, out = run(paths, FakeLLM(good_outputs()))
    items = by_title(out.challenges)

    headline = items["Executive reorganization"]
    assert headline.provenance == "inferred" and headline.labels == ["news headline only"] and headline.origin == "public_signal"

    # The CFO 8-K's Item 5.02 heading shares no distinctive term with the reorganization headline,
    # so citing it does not corroborate the headline.
    weak = items["Executive reorganization with filing"]
    assert weak.provenance == "inferred" and weak.labels == ["news headline only"]

    assert items["Alternative accommodations risk"].origin == "risk_factor"
    assert "Analyst downgrade" not in items and "Release only" not in items

    ineligible = items["Ineligible headline"]  # cited chunk was not in this call's sources
    assert ineligible.provenance == "inferred" and ineligible.rejected_citations[0].reason == "chunk was not among this call's sources"


def corroboration_outputs(model_inference=False):
    outputs = good_outputs()
    outputs[LLMChallengesChangesOutput].changes = [
        LLMChange(title="Layla acquisition", text="The company acquired an AI trip planner.", change_type="acquisition",
                  citations=[cit(LAYLA_ID, LAYLA_HEADLINE), cit(SYN, "we completed the acquisition of Layla, an AI trip planner")],
                  model_inference=model_inference, validation_question="q"),
        LLMChange(title="Seattle layoffs", text="Roles were eliminated.", change_type="restructuring_layoffs",
                  citations=[cit(LAYOFFS_ID, LAYOFFS_HEADLINE), cit(SYN, "an AI trip planner, and eliminated 58 roles in 2026")],
                  model_inference=False, validation_question="q"),
        LLMChange(title="Layoffs with unrelated filing", text="Roles were eliminated.", change_type="restructuring_layoffs",
                  citations=[cit(LAYOFFS_ID, LAYOFFS_HEADLINE), cit("8k-0001324424-26-000016-main-p003", ITEM_502)],
                  model_inference=False, validation_question="q"),
    ]
    return outputs


def test_headline_corroborated_by_named_entity_and_figure(paths):
    _, out = run(paths, FakeLLM(corroboration_outputs()))
    items = by_title(out.changes)
    assert items["Layla acquisition"].provenance == "sourced" and items["Layla acquisition"].labels == []  # "Layla"
    # "58" is shared but no entity is: a figure alone no longer corroborates.
    assert items["Seattle layoffs"].provenance == "inferred" and items["Seattle layoffs"].labels == ["news headline only"]
    unrelated = items["Layoffs with unrelated filing"]  # a filing exists but shares no distinctive term
    assert unrelated.provenance == "inferred" and unrelated.labels == ["news headline only"]


def test_headline_label_applies_regardless_of_model_inference(paths):
    outputs = corroboration_outputs(model_inference=True)
    _, out = run(paths, FakeLLM(outputs))
    assert by_title(out.changes)["Layla acquisition"].labels == ["model inference"]  # corroborated: no headline label
    assert by_title(out.changes)["Layoffs with unrelated filing"].labels == ["news headline only"]
    outputs[LLMChallengesChangesOutput].changes[2].model_inference = True
    _, out = run(paths, FakeLLM(outputs))
    assert by_title(out.changes)["Layoffs with unrelated filing"].labels == ["news headline only", "model inference"]


def test_financing_initiatives_excluded(paths):
    outputs = good_outputs()
    extra = [
        LLMInitiative(title="Capital return", text="Return cash to shareholders.", theme="financing_capital_return",
                      citations=[cit("10k-item7-p008", ADS)], model_inference=False, validation_question="q"),
        initiative("New $5 billion share repurchase authorization", [cit("10k-item7-p008", ADS)]),
        initiative("Refinance the revolving credit facility", [cit("10k-item7-p008", ADS)]),
        initiative("Reinstate the quarterly dividend", [cit("10k-item7-p008", ADS)]),
        initiative("Growth", [cit("10k-item7-p008", ADS)], text="Fund growth from a senior notes offering."),
        initiative("Stock buyback", [cit("10k-item7-p008", ADS)]),
    ]
    outputs[LLMInitiativesOutput].initiatives += extra
    outputs[LLMChallengesChangesOutput].changes.append(
        LLMChange(title="New revolving credit facility", text="A new credit facility was entered.", change_type="financing",
                  citations=[cit("8k-0001324424-26-000016-main-p003", ITEM_502)], model_inference=False, validation_question="q")
    )
    _, out = run(paths, FakeLLM(outputs))
    titles = {i.title for i in out.initiatives}
    assert not titles & {e.title for e in extra}
    report = json.loads((paths.stages / ex.GUARD_REPORT_NAME).read_text())
    reasons = {e["title"]: e["reason"] for e in report["excluded"]}
    assert reasons["Capital return"] == "financing or capital return is not an initiative (theme)"
    assert reasons["New $5 billion share repurchase authorization"].endswith("(keyword 'share repurchase')")
    assert reasons["Refinance the revolving credit facility"].endswith("(keyword 'credit facility')")
    assert reasons["Reinstate the quarterly dividend"].endswith("(keyword 'dividend')")
    assert reasons["Growth"].endswith("(keyword 'notes offering')")
    assert reasons["Stock buyback"].endswith("(keyword 'buyback')")
    assert "New revolving credit facility" in {c.title for c in out.changes}  # financing changes are allowed


def test_roles_only_for_challenges(paths):
    outputs = good_outputs()
    outputs[LLMChallengesChangesOutput].challenges.append(
        challenge("Controlling shareholder", [cit("10k-item1a-p040", RISK)], text="Mr. Smith controls voting power.")
    )
    _, out = run(paths, FakeLLM(outputs))
    assert "personal_title" in [f.type for f in by_title(out.challenges)["Controlling shareholder"].flags]


def test_exclusions_recorded_in_guard_report(paths):
    run(paths, FakeLLM(good_outputs()))
    report = json.loads((paths.stages / ex.GUARD_REPORT_NAME).read_text())
    excluded = {e["title"]: e["reason"] for e in report["excluded"]}
    assert excluded["Analyst downgrade"] == "supported only by analyst ratings or stock moves"
    assert excluded["Release only"].startswith("challenge not supported by Risk Factors")
    flagged = {(e["item_id"], e["reason"].split(":")[0]) for e in report["flagged"]}
    assert ("S-02", "personal_title") in flagged


def test_changes_event_date_and_roles(paths):
    _, out = run(paths, FakeLLM(good_outputs()))
    items = by_title(out.changes)
    cfo = items["CFO role change"]
    assert cfo.event_date == date(2026, 4, 17) and cfo.citations[0].date == date(2026, 4, 23)
    assert [f.type for f in items["Named person"].flags] == ["personal_title"]


def test_change_event_date_only_from_change_event_8ks(paths):
    outputs = good_outputs()
    outputs[LLMChallengesChangesOutput].changes.append(
        change("Restructuring from release", [cit("8k-0001324424-26-000051-ex991-p002", RELEASE_ROW)])
    )
    _, out = run(paths, FakeLLM(outputs))
    items = by_title(out.changes)
    assert items["Restructuring from release"].event_date is None  # release date is not an event date
    assert items["Restructuring from release"].citations[0].event_date == date(2026, 8, 5)  # still on the citation
    assert items["CFO role change"].event_date == date(2026, 4, 17)


def test_overflow_over_cap(paths):
    _, out = run(paths, FakeLLM(good_outputs(n_initiatives=10)))  # 5 + 10 = 15 initiatives
    assert len(out.initiatives) == 12 and out.initiatives[-1].id == "I-12"
    report = json.loads((paths.stages / ex.GUARD_REPORT_NAME).read_text())
    assert [e["title"] for e in report["overflow"]] == ["Extra 7", "Extra 8", "Extra 9"]
    assert all(e["reason"] == "over the cap of 12 initiatives" for e in report["overflow"])




# ---------------------------------------------------------------- dry run, spending limit, failures







# ---------------------------------------------------------------- config and cost





# ---------------------------------------------------------------- token counting and fallback











# ---------------------------------------------------------------- saved responses, replay, review sheet





def test_review_sheet_shows_evidence_next_to_chunk(paths):
    run(paths, FakeLLM(good_outputs()))
    sheet = (paths.raw / ex.REVIEW_SHEET_NAME).read_text()
    assert "Grow advertising revenue" in sheet and "sourced" in sheet
    assert "<mark>Expedia Group (&ldquo;EG&rdquo;) Advertising" not in sheet  # html.escape keeps curly quotes as-is
    assert "<mark>Expedia Group (\u201cEG\u201d) Advertising is responsible for generating advertising revenue</mark>" in sheet
    assert "rejected:</strong> unknown chunk id" in sheet and "no such chunk" in sheet
    assert "Excluded by rule" in sheet and "Analyst downgrade" in sheet
    assert "Gitignored" in sheet





# ---------------------------------------------------------------- challenge corroboration and exclusions

def test_risk_factor_challenge_keeps_sourced_with_uncorroborated_headline(paths):
    outputs = good_outputs()
    outputs[LLMChallengesChangesOutput].challenges.append(
        challenge("Risk plus headline", [cit("10k-item1a-p040", RISK), cit("news-gdelt-012", REORG)])
    )
    _, out = run(paths, FakeLLM(outputs))
    item = by_title(out.challenges)["Risk plus headline"]
    assert item.provenance == "sourced" and item.labels == ["uncorroborated headline"]
    news = [c for c in item.citations if c.source_type == "news"]
    assert len(news) == 1 and news[0].uncorroborated
    assert len(item.extra_validation_questions) == 1 and "Expedia Cuts Eight Executives" in item.extra_validation_questions[0]
    sheet = (paths.raw / ex.REVIEW_SHEET_NAME).read_text()
    assert "verified (uncorroborated headline)" in sheet and "Validation question (added by code)" in sheet


def test_risk_factor_challenge_with_corroborated_headline_has_no_label(paths):
    outputs = corroboration_outputs()
    outputs[LLMChallengesChangesOutput].challenges.append(
        challenge("Risk plus Layla", [cit("10k-item1a-p040", RISK), cit(LAYLA_ID, LAYLA_HEADLINE),
                                     cit(SYN, "we completed the acquisition of Layla, an AI trip planner")])
    )
    _, out = run(paths, FakeLLM(outputs))
    item = by_title(out.challenges)["Risk plus Layla"]
    assert item.provenance == "sourced" and item.labels == [] and item.extra_validation_questions == []


def test_non_risk_challenge_keeps_headline_rule(paths):
    _, out = run(paths, FakeLLM(good_outputs()))
    item = by_title(out.challenges)["Executive reorganization with filing"]  # news + 8-K, no Item 1A
    assert item.provenance == "inferred" and item.labels == ["news headline only"]


def test_changes_and_initiatives_keep_headline_rule(paths):
    _, out = run(paths, FakeLLM(corroboration_outputs()))
    assert by_title(out.changes)["Layoffs with unrelated filing"].labels == ["news headline only"]


def test_challenge_from_change_event_8ks_only_is_excluded(paths):
    outputs = good_outputs()
    outputs[LLMChallengesChangesOutput].challenges.append(
        challenge("CFO transition", [cit("8k-0001324424-26-000016-main-p003", ITEM_502)])
    )
    _, out = run(paths, FakeLLM(outputs))
    assert "CFO transition" not in by_title(out.challenges)
    report = json.loads((paths.stages / ex.GUARD_REPORT_NAME).read_text())
    assert {"title": "CFO transition", "reason": "covered under changes"}.items() <= next(
        e for e in report["excluded"] if e["title"] == "CFO transition").items()
    assert "CFO role change" in by_title(out.changes)  # the change itself stays




# ---------------------------------------------------------------- guidance, truncation, partial support, biography, consequences

OUTLOOK_ROW = "Adjusted EBITDA margin expansion** | +1 - 1.25pts | +1.5 - 1.75pts"


def test_real_i11_guidance_excluded(paths):
    outputs = good_outputs()
    real_i11 = LLMInitiative(
        title="2026 margin expansion target",
        text=("In its August 2026 release, the company raised full-year 2026 guidance, now expecting Adjusted EBITDA "
              "margin expansion of 1.5 to 1.75 points, up from 1 to 1.25 points."),
        theme="efficiency_cost", citations=[cit("8k-0001324424-26-000051-ex991-p003", OUTLOOK_ROW)],
        model_inference=False, validation_question="Which operating levers are expected to deliver the 2026 margin expansion?",
    )
    outlook_only = initiative("Margin expansion", [cit("8k-0001324424-26-000051-ex991-p003", OUTLOOK_ROW)],
                              text="Management expects margins to expand.")  # no keyword; only outlook rows
    outputs[LLMInitiativesOutput].initiatives += [real_i11, outlook_only]
    _, out = run(paths, FakeLLM(outputs))
    assert not {"2026 margin expansion target", "Margin expansion"} & set(by_title(out.initiatives))
    report = json.loads((paths.stages / ex.GUARD_REPORT_NAME).read_text())
    reasons = {e["title"]: e["reason"] for e in report["excluded"]}
    assert reasons["2026 margin expansion target"] == reasons["Margin expansion"] == "guidance is not an initiative"
    assert "Gross bookings growth" in by_title(out.initiatives)  # a non-outlook release row is unaffected


def test_truncated_citation_and_partial_support(paths):
    outputs = good_outputs()
    long = "Technology and content expense decreased $37 million for 2025 compared to 2024 primarily due to lower"
    outputs[LLMInitiativesOutput].initiatives.append(
        initiative("Cloud cost savings", [cit("10k-item7-p032", long), cit("10k-item7-p032", "Technology costs")])
    )
    _, out = run(paths, FakeLLM(outputs))
    item = by_title(out.initiatives)["Cloud cost savings"]
    assert item.provenance == "sourced" and item.citations[0].evidence_truncated
    assert len(item.citations[0].evidence.split()) == 14
    partial = [f for f in item.flags if f.type == "partial_support"]
    assert len(partial) == 1 and partial[0].detail.startswith("1 of 2 citations rejected")
    sheet = (paths.raw / ex.REVIEW_SHEET_NAME).read_text()
    assert "stored phrase truncated to 14 words" in sheet and "partial_support" in sheet


def test_biography_flag_on_changes_only(paths):
    outputs = good_outputs()
    outputs[LLMChallengesChangesOutput].changes.append(
        change("CFO with history", [cit("8k-0001324424-26-000016-main-p003", ITEM_502)],
               text="The new Chief Financial Officer previously served at another company.")
    )
    outputs[LLMChallengesChangesOutput].challenges.append(
        challenge("Talent", [cit("10k-item1a-p040", RISK)], text="Leaders who formerly ran rivals may leave.")
    )
    _, out = run(paths, FakeLLM(outputs))
    assert "biography" in [f.type for f in by_title(out.changes)["CFO with history"].flags]
    assert "biography" not in [f.type for f in by_title(out.challenges)["Talent"].flags]


def test_unsupported_consequence_flag_item_stays(paths):
    outputs = good_outputs()
    outputs[LLMChallengesChangesOutput].challenges.append(
        challenge("Accommodation rules", [cit("10k-item1a-p040", RISK)],
                  text="Rules on alternative accommodations could bring fines and reduce listings.")
    )
    _, out = run(paths, FakeLLM(outputs))
    item = by_title(out.challenges)["Accommodation rules"]
    assert item.provenance == "sourced"
    assert [f.detail for f in item.flags if f.type == "unsupported_term"] == ["'fines' appears in none of the cited chunks"]





def test_unsupported_statement_flag_item_stays(paths):
    outputs = good_outputs()
    outputs[LLMInitiativesOutput].initiatives.append(
        initiative("Ads plus extra claim", [cit("10k-item7-p008", ADS)],
                   text=("Advertising revenue grew through digital marketing solutions. "
                         "The company plans satellite broadband sponsorships across airline lounges."))
    )
    _, out = run(paths, FakeLLM(outputs))
    item = by_title(out.initiatives)["Ads plus extra claim"]
    weak = [f for f in item.flags if f.type == "unsupported_statement"]
    assert item.provenance == "sourced" and len(weak) == 1
    assert "satellite broadband sponsorships" in weak[0].detail and weak[0].detail.startswith("support ")
    assert weak[0].detail.endswith("not found in any allowed chunk (unsupported)")


def test_unsupported_statement_reports_mis_citation(paths):
    outputs = good_outputs()
    # Cites the gross-bookings row but states the advertising figure from 10k-item7-p008, which call A could cite.
    outputs[LLMInitiativesOutput].initiatives.append(
        initiative("Ads miscited", [cit("10k-item7-p025", BOOKINGS_ROW)],
                   text="Advertising and media revenue reached $758 million, a 19% increase from 2024.")
    )
    _, out = run(paths, FakeLLM(outputs))
    weak = [f for f in by_title(out.initiatives)["Ads miscited"].flags if f.type == "unsupported_statement"]
    assert len(weak) == 1 and weak[0].detail.endswith("found in 10k-item7-p008 (mis-cited)")
