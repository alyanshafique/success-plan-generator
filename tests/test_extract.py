"""Extract stage, sentence-level format, with a fake model: no API calls, no network."""

import json
import random
import shutil
from datetime import date
from pathlib import Path

import pytest

from success_plan.llm import LLMError, LLMResult, ModelsConfig, cost_usd, load_models_config
from success_plan.models import (
    ExtractOutput, LegacyLLMInitiative, LegacyLLMInitiativesOutput, LLMChallenge, LLMChallengesChangesOutput, LLMChange,
    LLMCitation, LLMInitiative, LLMInitiativesOutput, LLMSentence,
)
from success_plan.paths import CompanyPaths
from success_plan.stages import extract as ex

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"
MANIFEST = ROOT / "data" / "expedia" / "stages" / "01_retrieve.json"
_HEADLINES = json.loads(MANIFEST.read_text())["news"]["headlines"]
INELIGIBLE_NEWS_ID = next(h["chunk_id"] for h in _HEADLINES if not h["eligible_for_extract"])
_HEADS = {h["headline"]: h["chunk_id"] for h in _HEADLINES}
LAYLA_HEADLINE = "Expedia Acquired AI Trip - Planner Layla : Exclusive"
LAYLA_ID = _HEADS[LAYLA_HEADLINE]

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
    extra = [{
        "source_id": INELIGIBLE_NEWS_ID, "source_type": "news", "title": "Hotel Stocks To Follow Now", "url": "https://x/1",
        "date": "2026-09-30", "location": "x", "event_date": None, "signal": "weak", "chunk_id": INELIGIBLE_NEWS_ID,
        "text": "Hotel Stocks To Follow Now", "word_count": 5,
    }, SYNTHETIC_RELEASE]
    layla = next(h for h in _HEADLINES if h["chunk_id"] == LAYLA_ID)
    extra.append({"source_id": LAYLA_ID, "source_type": "news", "title": layla["headline"], "url": layla["url"],
                  "date": layla["date"], "location": layla["domain"], "event_date": None, "signal": "weak",
                  "chunk_id": LAYLA_ID, "text": layla["headline"], "word_count": len(layla["headline"].split())})
    p.chunks_file.write_text("\n".join(lines + [json.dumps(x) for x in extra]) + "\n")
    return p


CONFIG = ModelsConfig(
    max_cost_usd_per_run=3.0,
    profiles={"test": {"extract": {"model": "claude-sonnet-5-5", "effort": "medium", "max_tokens": 32000,
                                   "expected_output_tokens": 12000}}},
    prices={"claude-sonnet-5-5": {"input": 2.0, "output": 10.0, "cache_read": 0.2, "cache_write": 2.5}},
    prices_as_of="2026-10-06",
)

# Verbatim evidence phrases from the fixture chunks.
ADS = "Expedia Group (“EG”) Advertising is responsible for generating advertising revenue"
ADS_758 = "we generated $758 million of advertising and media revenue, a 19% increase from 2024."
BOOKINGS_ROW = "Total gross bookings $119,590 $110,921 $104,079"
RELEASE_ROW = "Gross Bookings | $33,928 | $30,409 | 12%"
TECH = "Technology and content expense decreased $37 million for 2025 compared to 2024"
RISK = "These risks could have a material adverse effect on our alternative accommodations business"
REORG = "Expedia Cuts Eight Executives in AI - Focused Reorganization"
ITEM_502 = "Item 5.02. Departure of Directors or Certain Officers; Election of Directors"
OUTLOOK_ROW = "Adjusted EBITDA margin expansion** | +1 - 1.25pts | +1.5 - 1.75pts"


def cit(chunk_id, evidence):
    return LLMCitation(chunk_id=chunk_id, evidence=evidence)


def sent(text, *citations):
    return LLMSentence(text=text, citations=list(citations))


def initiative(title, *sentences, theme="growth"):
    return LLMInitiative(title=title, theme=theme, sentences=list(sentences), validation_question="Is this a priority?")


def challenge(title, *sentences):
    return LLMChallenge(title=title, category="competition", sentences=list(sentences), validation_question="Is this affecting investment?")


def change(title, *sentences, change_type="leadership_change"):
    return LLMChange(title=title, change_type=change_type, sentences=list(sentences), validation_question="Has this changed decisions?")


def outputs(initiatives=(), challenges=(), changes=()):
    return {
        LLMInitiativesOutput: LLMInitiativesOutput(initiatives=list(initiatives)),
        LLMChallengesChangesOutput: LLMChallengesChangesOutput(challenges=list(challenges), changes=list(changes)),
    }


def base_outputs():
    return outputs(
        initiatives=[
            initiative("Advertising revenue growth",
                       sent("Advertising revenue reached $758 million in 2025, up 19%.", cit("10k-item7-p008", ADS_758)),
                       sent("A dedicated unit generates advertising revenue across the brands.", cit("10k-item7-p008", ADS))),
        ],
        challenges=[
            challenge("Alternative accommodations risk",
                      sent("Risks could have a material adverse effect on the alternative accommodations business.",
                           cit("10k-item1a-p040", RISK))),
        ],
        changes=[
            change("Chief Financial Officer role change",
                   sent("An 8-K reported a departure or appointment of certain officers.", cit("8k-0001324424-26-000016-main-p003", ITEM_502))),
        ],
    )


class FakeLLM:
    def __init__(self, outputs, fail_on=None):
        self.outputs, self.fail_on, self.calls = outputs, fail_on, []

    def complete(self, *, system, user, output_model, settings):
        self.calls.append({"system": system, "user": user, "output_model": output_model})
        if output_model is self.fail_on:
            raise LLMError("simulated refusal")
        return LLMResult(parsed=self.outputs[output_model], model=settings.model, stop_reason="end_turn",
                         input_tokens=40000, output_tokens=10000)


def run(paths, llm, config=CONFIG):
    return ex.run_extract(paths, llm, config, "test")


def by_title(items):
    return {i.title: i for i in items}


def report(paths):
    return json.loads((paths.stages / ex.GUARD_REPORT_NAME).read_text())


# ---------------------------------------------------------------- inputs

def test_partition_sources(paths):
    manifest, chunks = ex.load_inputs(paths)
    a, b = ex.partition(manifest, chunks)
    a_ids, b_ids = {c["chunk_id"] for c in a}, {c["chunk_id"] for c in b}
    assert "10k-item1a-p040" not in a_ids and "10k-item1a-p040" in b_ids
    assert not any(i.startswith("news-") for i in a_ids)
    assert "8k-0001324424-26-000016-main-p003" not in a_ids
    assert "8k-0001324424-26-000051-ex991-p002" in a_ids & b_ids
    assert INELIGIBLE_NEWS_ID not in a_ids | b_ids


def test_prompts_contain_only_partitioned_chunks(paths):
    llm = FakeLLM(base_outputs())
    run(paths, llm)
    init_call, cc_call = llm.calls
    assert "10k-item1a-p040" not in init_call["user"] and "news-gdelt" not in init_call["user"]
    assert "10k-item1a-p040" in cc_call["user"] and INELIGIBLE_NEWS_ID not in cc_call["user"]
    assert init_call["output_model"] is LLMInitiativesOutput


# ---------------------------------------------------------------- assembly

def test_all_sentences_verified_text_is_their_join(paths):
    _, out = run(paths, FakeLLM(base_outputs()))
    item = by_title(out.initiatives)["Advertising revenue growth"]
    assert item.text == "Advertising revenue reached $758 million in 2025, up 19%. A dedicated unit generates advertising revenue across the brands."
    assert [len(s.citations) for s in item.sentences] == [1, 1] and item.dropped_sentences == []
    assert item.provenance == "sourced" and "partial_support" not in [f.type for f in item.flags]
    assert out.response_format == "sentences" and not item.legacy


def test_sentence_with_rejected_citation_is_dropped(paths):
    o = base_outputs()
    o[LLMInitiativesOutput].initiatives[0].sentences.append(
        sent("Advertising will triple by 2030.", cit("10k-item7-p008", "advertising will triple by the end of the decade for sure")))
    _, out = run(paths, FakeLLM(o))
    item = by_title(out.initiatives)["Advertising revenue growth"]
    assert "triple" not in item.text
    assert [d.text for d in item.dropped_sentences] == ["Advertising will triple by 2030."]
    assert item.dropped_sentences[0].reason.startswith("no citation verified: evidence not found in chunk")
    assert [f.detail for f in item.flags if f.type == "partial_support"] == ["1 of 3 sentences dropped because no citation verified"]
    dropped = report(paths)["dropped_sentences"]
    assert len(dropped) == 1 and "Advertising will triple by 2030." in dropped[0]["reason"]


def test_uncited_sentence_dropped_and_all_dropped_excludes_item(paths):
    o = base_outputs()
    o[LLMInitiativesOutput].initiatives.append(initiative("Nothing verified", sent("An uncited claim about strategy."),
                                                          sent("A wrong claim.", cit("10k-item9-p001", "no such chunk exists in the sources at all"))))
    _, out = run(paths, FakeLLM(o))
    assert "Nothing verified" not in by_title(out.initiatives)
    r = report(paths)
    assert {"title": "Nothing verified", "reason": "no sentence has a verified citation"}.items() <= next(
        e for e in r["excluded"] if e["title"] == "Nothing verified").items()
    reasons = [e["reason"] for e in r["dropped_sentences"] if e["title"] == "Nothing verified"]
    assert any("(uncited sentence)" in x for x in reasons) and any("unknown chunk id" in x for x in reasons)


def test_two_citations_allowed_third_rejected(paths):
    o = base_outputs()
    o[LLMInitiativesOutput].initiatives.append(initiative("Bookings",
        sent("Gross bookings grew in 2025 and again in the second quarter of 2026.",
             cit("10k-item7-p025", BOOKINGS_ROW), cit("8k-0001324424-26-000051-ex991-p002", RELEASE_ROW),
             cit("10k-item7-p008", ADS))))
    _, out = run(paths, FakeLLM(o))
    item = by_title(out.initiatives)["Bookings"]
    (s,) = item.sentences
    assert [c.source_id for c in s.citations] == ["8k-0001324424-26-000051-ex991", "10k-item7"]  # newest first
    assert [r.reason for r in s.rejected_citations] == ["more than 2 citations in one sentence"]
    assert item.sources_note.startswith("Newer source preferred: earnings release filed 2026-08-05")


def test_property_text_is_always_join_of_kept_sentences(paths):
    """Random fake outputs: assembled text equals the join of kept sentences and never contains a dropped one."""
    valid = [("10k-item7-p008", ADS), ("10k-item7-p008", ADS_758), ("10k-item7-p025", BOOKINGS_ROW),
             ("10k-item7-p032", TECH), ("8k-0001324424-26-000051-ex991-p002", RELEASE_ROW)]
    invalid = [("10k-item7-p008", "this phrase is not in the advertising chunk at all"),
               ("10k-item1a-p040", RISK), ("10k-item7-p032", "short")]
    rng = random.Random(20261012)
    for trial in range(60):
        items = []
        for i in range(rng.randint(1, 5)):
            sentences = []
            for j in range(rng.randint(1, 4)):
                pool = [cit(*rng.choice(valid if rng.random() < 0.6 else invalid)) for _ in range(rng.randint(0, 3))]
                sentences.append(sent(f"Claim t{trial} i{i} s{j} zq{rng.randint(1000, 9999)} about operations.", *pool))
            items.append(initiative(f"Item t{trial} i{i}", *sentences))
        trial_paths = CompanyPaths(root=paths.root / f"t{trial}", slug="expedia")
        trial_paths.stages.mkdir(parents=True)
        trial_paths.raw.mkdir(parents=True)
        shutil.copy(paths.stages / "01_retrieve.json", trial_paths.stages / "01_retrieve.json")
        shutil.copy(paths.chunks_file, trial_paths.chunks_file)
        _, out = run(trial_paths, FakeLLM(outputs(initiatives=items)))
        for item in out.initiatives:
            assert item.text == " ".join(s.text for s in item.sentences)
            assert item.sentences and all(s.citations for s in item.sentences)
            for d in item.dropped_sentences:
                assert d.text not in item.text
        # Every proposed sentence is either kept in an item, dropped from a kept item, or in the guard report.
        kept = {s.text for i in out.initiatives for s in i.sentences}
        reported = "\n".join(e["reason"] for e in report(trial_paths)["dropped_sentences"])
        for item in items:
            for s in item.sentences:
                assert s.text in kept or s.text in reported


# ---------------------------------------------------------------- rules on the sentence format

def test_headline_sentence_rules(paths):
    o = base_outputs()
    o[LLMChallengesChangesOutput].changes += [
        change("Layla corroborated",
               sent("A headline reports the acquisition of Layla.", cit(LAYLA_ID, LAYLA_HEADLINE)),
               sent("A release states the acquisition of Layla was completed.", cit(SYN, "we completed the acquisition of Layla, an AI trip planner")),
               change_type="acquisition"),
        change("Reorganization headline only",
               sent("A headline reports eight executives cut in a reorganization.", cit("news-gdelt-012", REORG)),
               change_type="reorganization"),
    ]
    o[LLMChallengesChangesOutput].challenges.append(challenge("Risk plus headline",
        sent("Risks could have a material adverse effect on the alternative accommodations business.", cit("10k-item1a-p040", RISK)),
        sent("A headline reports eight executives cut in a reorganization.", cit("news-gdelt-012", REORG))))
    _, out = run(paths, FakeLLM(o))
    changes = by_title(out.changes)
    assert changes["Layla corroborated"].provenance == "sourced" and changes["Layla corroborated"].labels == []
    assert changes["Reorganization headline only"].provenance == "inferred"
    assert changes["Reorganization headline only"].labels == ["news headline only"]
    risk = by_title(out.challenges)["Risk plus headline"]
    assert risk.provenance == "sourced" and risk.labels == ["uncorroborated headline"]
    assert risk.sentences[1].citations[0].uncorroborated and len(risk.extra_validation_questions) == 1


def test_exclusions(paths):
    o = base_outputs()
    o[LLMInitiativesOutput].initiatives += [
        initiative("Capital return", sent("Cash goes to shareholders.", cit("10k-item7-p008", ADS)), theme="financing_capital_return"),
        initiative("Buyback", sent("The company runs a share buyback program.", cit("10k-item7-p008", ADS))),
        initiative("Raised outlook", sent("Management raised its full-year outlook.", cit("10k-item7-p008", ADS))),
        initiative("Margin goal", sent("Margins should expand.", cit("8k-0001324424-26-000051-ex991-p003", OUTLOOK_ROW))),
    ]
    o[LLMChallengesChangesOutput].challenges += [
        challenge("Analyst downgrade", sent("An analyst cut the rating.",
                  cit("news-gdelt-004", "Morgan Stanley Cuts Expedia ( EXPE ) to Underweight as Rivals Pull Ahead on Users"))),
        challenge("Release only", sent("Bookings grew.", cit("8k-0001324424-26-000051-ex991-p002", RELEASE_ROW))),
        challenge("CFO transition", sent("Officers changed.", cit("8k-0001324424-26-000016-main-p003", ITEM_502))),
    ]
    _, out = run(paths, FakeLLM(o))
    reasons = {e["title"]: e["reason"] for e in report(paths)["excluded"]}
    assert reasons["Capital return"] == "financing or capital return is not an initiative (theme)"
    assert reasons["Buyback"].lower().endswith("(keyword 'buyback')")  # matched in the title first
    assert reasons["Raised outlook"] == reasons["Margin goal"] == "guidance is not an initiative"
    assert reasons["Analyst downgrade"] == "supported only by analyst ratings or stock moves"
    assert reasons["Release only"].startswith("challenge not supported by Risk Factors")
    assert reasons["CFO transition"] == "covered under changes"


def test_change_event_date_only_from_change_event_8ks(paths):
    o = base_outputs()
    o[LLMChallengesChangesOutput].changes.append(
        change("From release", sent("Bookings grew in the quarter.", cit("8k-0001324424-26-000051-ex991-p002", RELEASE_ROW)),
               change_type="other"))
    _, out = run(paths, FakeLLM(o))
    assert by_title(out.changes)["From release"].event_date is None
    assert by_title(out.changes)["Chief Financial Officer role change"].event_date == date(2026, 4, 17)


# ---------------------------------------------------------------- per-sentence checks (own citations only)

def test_padding_flagged_per_sentence_and_kept(paths):
    o = base_outputs()
    o[LLMChallengesChangesOutput].challenges.append(challenge("Accommodation rules",
        sent("Rules on alternative accommodations could bring fines and penalties for platforms.", cit("10k-item1a-p040", RISK))))
    _, out = run(paths, FakeLLM(o))
    item = by_title(out.challenges)["Accommodation rules"]
    assert item.text.startswith("Rules on alternative accommodations")  # flagged, not dropped
    types = [f.type for f in item.flags]
    assert "unsupported_term" in types and "unsupported_statement" in types
    assert all(f.detail.startswith("sentence 1") for f in item.flags if f.type in ("unsupported_term", "unsupported_statement"))
    assert item.sentences[0].support_score is not None and item.sentences[0].support_score < 0.75


def test_support_scored_against_own_citations_only(paths):
    o = base_outputs()
    # Sentence 2 states the $758 million figure but cites the technology chunk; sentence 1 cites the right chunk.
    o[LLMInitiativesOutput].initiatives.append(initiative("Mixed",
        sent("Advertising revenue reached $758 million in 2025, up 19%.", cit("10k-item7-p008", ADS_758)),
        sent("Advertising revenue reached $758 million in 2025.", cit("10k-item7-p032", TECH))))
    _, out = run(paths, FakeLLM(o))
    item = by_title(out.initiatives)["Mixed"]
    weak = [f.detail for f in item.flags if f.type == "unsupported_statement"]
    assert len(weak) == 1 and weak[0].startswith("sentence 2") and "found in 10k-item7-p008 (mis-cited)" in weak[0]
    prices = [f.detail for f in item.flags if f.type == "price_review"]
    assert prices == ["sentence 2: amount '$758 million' does not trace to a cited filing chunk"]


def test_title_is_a_label_scored_not_flagged(paths):
    o = base_outputs()
    o[LLMInitiativesOutput].initiatives.append(initiative("Quantum satellite metaverse expansion",
        sent("Advertising revenue reached $758 million in 2025, up 19%.", cit("10k-item7-p008", ADS_758))))
    _, out = run(paths, FakeLLM(o))
    item = by_title(out.initiatives)["Quantum satellite metaverse expansion"]
    assert item.title_support_score == 0.0 and not item.flags  # scored, never flagged or dropped
    assert "title support 0% against the kept sentences" in (paths.raw / ex.REVIEW_SHEET_NAME).read_text()


def test_roles_only_flags(paths):
    o = base_outputs()
    o[LLMChallengesChangesOutput].changes.append(change("CFO with history",
        sent("Mr. Smith previously served as finance chief at a retailer.", cit("8k-0001324424-26-000016-main-p003", ITEM_502))))
    _, out = run(paths, FakeLLM(o))
    types = [f.type for f in by_title(out.changes)["CFO with history"].flags]
    assert "personal_title" in types and "biography" in types


def test_overflow_over_cap(paths):
    o = base_outputs()
    o[LLMInitiativesOutput].initiatives += [
        initiative(f"Extra {i}", sent("A unit generates advertising revenue across the brands.", cit("10k-item7-p008", ADS)))
        for i in range(13)
    ]
    _, out = run(paths, FakeLLM(o))
    assert len(out.initiatives) == 12
    assert [e["title"] for e in report(paths)["overflow"]] == ["Extra 11", "Extra 12"]


# ---------------------------------------------------------------- run mechanics

def test_output_written_and_usage_logged(paths):
    _, out = run(paths, FakeLLM(base_outputs()))
    saved = ExtractOutput.model_validate_json((paths.stages / ex.OUTPUT_NAME).read_text())
    assert saved.total_cost_usd == pytest.approx(2 * (40000 * 2.0 + 10000 * 10.0) / 1e6)
    log = [json.loads(l) for l in (paths.stages / ex.USAGE_LOG_NAME).read_text().splitlines()]
    assert log[-1]["status"] == "ok" and log[-1]["cost_usd"] == pytest.approx(saved.total_cost_usd)


def test_dry_run_makes_no_calls(paths):
    llm = FakeLLM(base_outputs())
    estimates, out = ex.run_extract(paths, llm, CONFIG, "test", dry_run=True)
    assert out is None and llm.calls == [] and not (paths.stages / ex.OUTPUT_NAME).exists()
    assert all(e.input_tokens > 0 for e in estimates)


def test_run_aborts_over_spending_limit(paths):
    llm = FakeLLM(base_outputs())
    with pytest.raises(ex.ExtractError, match="exceeds the limit of \\$0.01.*No API call was made"):
        ex.run_extract(paths, llm, ModelsConfig(0.01, CONFIG.profiles, CONFIG.prices, CONFIG.prices_as_of), "test")
    assert llm.calls == []


def test_failed_call_logs_usage_and_saves_nothing(paths):
    with pytest.raises(ex.ExtractError, match="challenges_changes.*simulated refusal"):
        run(paths, FakeLLM(base_outputs(), fail_on=LLMChallengesChangesOutput))
    assert not (paths.stages / ex.OUTPUT_NAME).exists()
    log = [json.loads(l) for l in (paths.stages / ex.USAGE_LOG_NAME).read_text().splitlines()]
    assert log[-1]["status"] == "failed" and [c["call"] for c in log[-1]["calls"]] == ["initiatives"]


def test_models_config_profiles_and_limit():
    config = load_models_config()
    assert config.max_cost_usd_per_run == 3.0 and config.chars_per_token == 2.35
    assert config.settings("dev", "extract").model == "claude-sonnet-5-5"
    assert (config.settings("final", "extract").model, config.settings("final", "extract").effort) == ("claude-opus-5-5", "high")


def test_cost_uses_long_context_rate():
    prices = load_models_config().prices
    assert cost_usd(prices, "claude-haiku-5-5", input_tokens=150_000, output_tokens=1_000) == pytest.approx((150_000 * 0.50 + 1_000 * 2.50) / 1e6)


class FakeCounter:
    def __init__(self, fail=False):
        self.fail, self.calls = fail, []

    def count_input_tokens(self, *, system, user, output_model, model):
        self.calls.append((model, output_model))
        if self.fail:
            raise LLMError("simulated counting outage")
        return 90_000


def test_exact_count_and_fallback(paths):
    counter = FakeCounter()
    estimates, _ = ex.run_extract(paths, None, CONFIG, "test", dry_run=True, counter=counter)
    assert [e.input_source for e in estimates] == ["counted", "counted"]
    assert [m for _, m in counter.calls] == [LLMInitiativesOutput, LLMChallengesChangesOutput]  # counted with the new format
    estimates, _ = ex.run_extract(paths, None, CONFIG, "test", dry_run=True, counter=FakeCounter(fail=True))
    assert [e.input_source for e in estimates] == ["estimated", "estimated"]


def test_responses_saved_and_replay_matches_without_api(paths):
    _, original = run(paths, FakeLLM(base_outputs()))
    run_dir = ex.latest_response_dir(paths)
    saved = json.loads((run_dir / "initiatives.json").read_text())
    assert "sentences" in saved["output"]["initiatives"][0]
    replayed = ex.replay_extract(paths, CONFIG)
    strip = lambda o: [(i.id, i.text, i.provenance, i.labels, [f.type for f in i.flags]) for i in o.initiatives + o.challenges + o.changes]
    assert strip(replayed) == strip(original) and replayed.response_format == "sentences"


def test_replay_legacy_response_is_marked_legacy(paths):
    run_dir = paths.raw / ex.RESPONSES_DIR / "extract-20261012T000000Z-dev"
    run_dir.mkdir(parents=True)
    legacy_init = LegacyLLMInitiativesOutput(initiatives=[LegacyLLMInitiative(
        title="Ads", text="Advertising revenue reached $758 million.", theme="growth",
        citations=[cit("10k-item7-p008", ADS_758)], model_inference=False, validation_question="q")])
    for name, output in (("initiatives", legacy_init.model_dump(mode="json")),
                         ("challenges_changes", {"challenges": [], "changes": []})):
        (run_dir / f"{name}.json").write_text(json.dumps({"call": name, "profile": "dev", "model": "m", "effort": "e",
                                                          "usage": {}, "output": output}))
    out = ex.replay_extract(paths, CONFIG, run_dir)
    assert out.response_format == "legacy" and out.replayed_from.endswith("(LEGACY format)")
    assert out.warnings[0].startswith("LEGACY response format") and out.initiatives[0].legacy


def test_replay_without_saved_responses_fails_clearly(paths):
    with pytest.raises(ex.ExtractError, match="No saved model responses"):
        ex.replay_extract(paths, CONFIG)


def test_replay_relative_path_and_rebuilt_marker(paths, monkeypatch):
    run(paths, FakeLLM(base_outputs()))
    run_dir = ex.latest_response_dir(paths)
    for f in run_dir.iterdir():
        rec = json.loads(f.read_text())
        rec.update(rebuilt=True, note="REBUILT for test")
        f.write_text(json.dumps(rec))
    monkeypatch.chdir(paths.root)
    out = ex.replay_extract(paths, CONFIG, run_dir.relative_to(paths.root))
    assert out.replayed_from.endswith("(REBUILT, not a saved model response)")
    assert out.warnings[0].startswith("Replayed a REBUILT response")


# ---------------------------------------------------------------- prompts

def test_prompts_state_evidence_length_and_guidance_rule():
    common = (ex.PROMPTS / "extract_common.md").read_text()
    assert "at least 8 words, aim for 10" in common and "never fewer than 8, and no more than 12" in common
    initiatives = (ex.PROMPTS / "extract_initiatives.md").read_text()
    assert "Forecasts and guidance figures" in initiatives and "a goal management says it is pursuing" in initiatives


def test_prompt_states_roles_only_includes_biography():
    assert "never state a person's prior employer, previous roles, or career" in (ex.PROMPTS / "extract_common.md").read_text()



def test_prompt_states_sentence_rules():
    common = " ".join((ex.PROMPTS / "extract_common.md").read_text().split())
    for phrase in ("One claim per sentence", "Keep sentences short", "Do not combine facts from different paragraphs",
                   "taken from the paragraph that states that sentence's claim", "Never write a sentence without a citation"):
        assert phrase in common, phrase
    assert "model_inference" not in common and "60 words" not in common



# ---------------------------------------------------------------- review sheet

def test_review_sheet_shows_sentences_and_dropped(paths):
    o = base_outputs()
    o[LLMInitiativesOutput].initiatives[0].sentences.append(
        sent("Advertising will triple by 2030.", cit("10k-item7-p008", "advertising will triple by the end of the decade for sure")))
    o[LLMInitiativesOutput].initiatives.append(initiative("Nothing verified", sent("An uncited claim about strategy.")))
    run(paths, FakeLLM(o))
    sheet = (paths.raw / ex.REVIEW_SHEET_NAME).read_text()
    assert "<strong>Sentence 1</strong>" in sheet and "<strong>Sentence 2</strong>" in sheet
    assert "against its own citations" in sheet
    assert "<mark>we generated $758 million of advertising and media revenue, a 19% increase from 2024.</mark>" in sheet
    assert "Dropped sentences (1)" in sheet and "<s>Advertising will triple by 2030.</s>" in sheet
    assert "Reason: no citation verified: evidence not found in chunk" in sheet
    assert "All dropped sentences (including those of excluded items)" in sheet and "An uncited claim about strategy." in sheet
    assert "LEGACY" not in sheet


def test_review_sheet_marks_legacy(paths):
    test_replay_legacy_response_is_marked_legacy(paths)
    sheet = (paths.raw / ex.REVIEW_SHEET_NAME).read_text()
    assert "LEGACY format" in sheet and "LEGACY response format" in sheet



# ---------------------------------------------------------------- round: placeholders, headline skip, results review

C02_PLACEHOLDER = ("Search providers have added their own travel functionality, which may keep reducing traffic to the "
                   "company's sites.")  # real dev-4 C-02 sentence, cited with chunk "11k-placeholder", evidence "placeholder"


def test_real_c02_placeholder_citation_rejected(paths):
    o = base_outputs()
    o[LLMChallengesChangesOutput].challenges[0].sentences.append(sent(C02_PLACEHOLDER, cit("11k-placeholder", "placeholder")))
    _, out = run(paths, FakeLLM(o))
    item = by_title(out.challenges)["Alternative accommodations risk"]
    (d,) = item.dropped_sentences
    assert d.text == C02_PLACEHOLDER and d.reason == "no citation verified: placeholder citation"


def test_headline_only_sentence_skips_found_in_search(paths):
    o = base_outputs()
    o[LLMChallengesChangesOutput].changes.append(change("Reorganization",
        sent("Another headline cites an internal memo about executives leaving in a shakeup.", cit("news-gdelt-012", REORG)),
        change_type="reorganization"))
    _, out = run(paths, FakeLLM(o))
    weak = [f.detail for f in by_title(out.changes)["Reorganization"].flags if f.type == "unsupported_statement"]
    assert weak and all(d.endswith("found-in search skipped (cites only news headlines)") for d in weak)


def test_results_review_on_initiatives_only(paths):
    o = base_outputs()
    o[LLMInitiativesOutput].initiatives.append(initiative("Ads result",
        sent("Advertising and media revenue grew 19% in 2025 to $758 million.", cit("10k-item7-p008", ADS_758))))
    o[LLMChallengesChangesOutput].challenges.append(challenge("Risk grew",
        sent("Risks to the alternative accommodations business increased, with a material adverse effect possible.",
             cit("10k-item1a-p040", RISK))))
    _, out = run(paths, FakeLLM(o))
    ads = by_title(out.initiatives)["Ads result"]
    assert [f.detail for f in ads.flags if f.type == "results_review"] == [
        "sentence 1: uses 'grew'; an initiative states what the company is doing or intends to do, not a result"]
    assert ads.text  # kept, never dropped
    assert "results_review" not in [f.type for f in by_title(out.challenges)["Risk grew"].flags]


def test_prompt_rules_hedging_dates_and_results():
    common = " ".join((ex.PROMPTS / "extract_common.md").read_text().split())
    assert "Keep the source's hedging words" in common and "no assurance" in common
    assert '"the same date"' in common and "Always write the date itself" in common
    initiatives = " ".join((ex.PROMPTS / "extract_initiatives.md").read_text().split())
    assert "Reporting a result, or the cause of a result, is not an initiative" in initiatives



def test_sentence_dates_scored_against_own_chunk_dates_only(paths):
    o = base_outputs()
    o[LLMInitiativesOutput].initiatives.append(initiative("Dated",
        # Release row filed 2026-08-05: its own date supports "August 5, 2026".
        sent("On August 5, 2026, gross bookings were $33,928.", cit("8k-0001324424-26-000051-ex991-p002", RELEASE_ROW)),
        # The 10-K (filed 2026-02-13) does not support a different date.
        sent("On August 5, 2026, advertising revenue reached $758 million.", cit("10k-item7-p008", ADS_758))))
    _, out = run(paths, FakeLLM(o))
    weak = [f.detail for f in by_title(out.initiatives)["Dated"].flags if f.type == "unsupported_statement"]
    assert len(weak) == 1 and weak[0].startswith("sentence 2") and "August" in weak[0]
