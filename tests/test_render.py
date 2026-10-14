"""Render stage tests. No network or API calls: Map and Draft come from fake models."""

import json
import re
from datetime import date, datetime, timezone

from pathlib import Path

import jsonschema
import pytest

from success_plan.models import (
    OBJECTIVES_BLANK, PLAN_CONFIRMATION_BLANK, ExtractItem, ExtractOutput, SentenceRecord, VerifiedCitation,
)
from success_plan.stages import draft as dr
from success_plan.stages import render as rd
from test_draft import CONFIG as DRAFT_CONFIG
from test_draft import FakeDraftLLM, base_draft, write_map
from test_map import SYSTEM

FILING_URL = "https://www.sec.gov/Archives/edgar/data/1/000000000126000001/test-10k.htm"
NEWS_URL = "https://news.example.com/test-co-reorganizes-teams"
EVIDENCE = {  # verbatim evidence phrases: must never appear in the plan
    "I-01": "brands share one technology platform to ship releases more quickly",
    "I-02": "management coordinates the delivery of changes across numerous product teams",
    "I-03": "a growing network of new partners for the B2B business segment",
    "I-04": "restructuring actions intended to recalibrate resources and reduce costs",
    "C-01": "competitors may adopt new technologies faster than the company does",
    "S-01": "the company announced a reorganization of its product teams",
}
CHUNK_TEXT = ("Raw chunk text that only exists in the filing: the board approved a multi-year technology roadmap "
              "and several other matters that are not part of any extracted sentence at all.")


def cite(key, source_type="filing", url=FILING_URL, title="Test Co Form 10-K for fiscal year 2025", location="Item 7 MD&A"):
    return VerifiedCitation(chunk_id=f"chunk-{key}", source_id="10k", evidence=EVIDENCE[key], source_type=source_type,
                            title=title, location=location, url=url, date=date(2026, 2, 13))


HEADLINE = f"Test Co says {EVIDENCE['S-01']} this week"  # a news citation's title is the headline itself


def with_citations(item: ExtractItem) -> ExtractItem:
    c = (cite(item.id, source_type="news", url=NEWS_URL, title=HEADLINE, location=None) if item.id.startswith("S-")
         else cite(item.id))
    sentences = [SentenceRecord(text=s.text, citations=[c]) for s in item.sentences]
    return item.model_copy(update={"sentences": sentences, "citations": [c]})


def challenge(id_, kind, text, labels=(), **cit):
    return with_citations(ExtractItem(
        id=id_, kind=kind, title=f"{kind} {id_}", text=text, provenance="sourced", labels=list(labels), flags=[],
        citations=[], rejected_citations=[], model_inference=False, validation_question=f"Is {id_} still relevant?",
        sentences=[SentenceRecord(text=text, citations=[])], **({"category": "competition"} if kind == "challenge" else {"change_type": "reorganization"}),
    ))


def manifest(paths):
    m = {
        "stage": "retrieve", "generated_at": "2026-10-12T00:00:00Z", "as_of": "2026-10-12",
        "company": {"input_name": "Test Co", "name": "Test Co, Inc.", "ticker": "TSTC", "cik": 999999999},
        "filing": {"form": "10-K", "accession": "0000000001-26-000001", "fiscal_year": "2025", "period_end": "2025-12-31",
                   "filing_date": "2026-02-13", "url": FILING_URL, "cover_fiscal_year_end": "2025-12-31", "fiscal_year_check": "passed"},
        "sections": [], "eight_ks": {"events_window_from": "2026-01-01", "events_window_to": "2026-10-12", "events": [], "earnings_releases": []},
        "news": {"provider": "gdelt", "status": "ok", "query": "q", "window_from": "2026-07-14", "window_to": "2026-10-12",
                 "aliases": ["Test Co"], "low_signal_domains": [], "from_cache": False, "returned_count": 1, "eligible_count": 1,
                 "low_signal_count": 0, "headlines": [{"chunk_id": "news-001", "headline": HEADLINE, "url": NEWS_URL,
                 "date": "2026-09-01", "domain": "news.example.com", "matched_aliases": ["Test Co"], "low_signal": False,
                 "eligible_for_extract": True}]},
        "sources": [], "total_chunks": 0, "request_count": 0, "warnings": [],
    }
    (paths.stages / "01_retrieve.json").write_text(json.dumps(m))


@pytest.fixture
def paths(tmp_path, monkeypatch):
    challenges = [challenge("C-01", "challenge", "Competitors may adopt new technologies faster.")]
    changes = [challenge("S-01", "change", "The company reorganized its product teams.", labels=["news headline only"])]
    p = write_map(tmp_path, challenges=challenges, changes=changes)
    dr.run_draft(p, FakeDraftLLM(base_draft()), DRAFT_CONFIG, "test", system=SYSTEM)
    # Same Extract (same timestamp), now with verified citations carrying evidence phrases.
    extract = ExtractOutput.model_validate_json((p.stages / "02_extract.json").read_text())
    extract.initiatives = [with_citations(i) for i in extract.initiatives]
    (p.stages / "02_extract.json").write_text(extract.model_dump_json(indent=2))
    manifest(p)
    (p.raw / "chunks.jsonl").write_text(json.dumps({"chunk_id": "10k-p001", "text": CHUNK_TEXT}) + "\n")
    monkeypatch.setattr(rd, "load_brand_aliases", lambda cik: ["Brandly", "Testtrip"])
    return p


def rendered(paths):
    json_path, md_path, report = rd.render(paths, now=datetime(2026, 10, 13, tzinfo=timezone.utc))
    return json.loads(json_path.read_text()), md_path.read_text(), report


def test_plan_is_schema_valid_with_disclaimer_notice_and_blanks(paths):
    plan, md, _ = rendered(paths)
    jsonschema.validate(plan, json.loads(rd.SCHEMA_FILE.read_text()))
    assert plan["metadata"]["disclaimer"] == rd.DISCLAIMER and rd.DISCLAIMER in md
    assert plan["metadata"]["notice"] == rd.NOTICE and md.index(rd.NOTICE) < md.index("## Company highlights")
    for blank in rd.BLANKS.values():
        assert blank in md
    assert plan["renewal"]["placeholder"] == rd.BLANKS["renewal"]
    assert plan["company_highlights"]["account_history"]["placeholder"] == rd.BLANKS["account_history"]
    assert plan["key_challenges"]["customer_raised"]["placeholder"] == rd.BLANKS["customer_raised"]
    assert plan["objectives_csm_blank"]["placeholder"] == OBJECTIVES_BLANK and OBJECTIVES_BLANK in md


def test_overview_is_code_built_and_cited(paths):
    plan, md, _ = rendered(paths)
    o = plan["company_highlights"]["overview"]
    assert o["text"] == ("Test Co, Inc. (ticker TSTC). Latest annual report: Form 10-K for fiscal year 2025, filed 2026-02-13. "
                         "Brands named in Item 1 (Business) of that filing: Brandly, Testtrip.")
    assert o["provenance"] == "sourced" and o["citations"][0]["url"] == FILING_URL and o["citations"][0]["location"] == "Item 1 Business"


def test_no_evidence_field_or_key_in_plan(paths):
    plan, md, report = rendered(paths)
    keys = set(rd._keys(plan))
    assert not any("evidence" in k.lower() for k in keys)
    assert '"evidence' not in json.dumps(plan) and not re.search(r"\bevidence(?:_\w+)?\s*[:=]", md, re.IGNORECASE)
    assert report["evidence_checks"].startswith("passed")
    assert FILING_URL in md  # source links are shown instead
    leaky = dict(plan, key_benefits=[dict(plan["key_benefits"][0], evidence="x")])
    assert "evidence key 'evidence' in plan.json" in rd.evidence_problems(leaky, md, [], [])


def test_sentence_with_15_word_run_is_omitted_and_reported(paths):
    words = CHUNK_TEXT.split()
    copied = " ".join(words[15:30])  # exactly 15 consecutive words (no hyphenated word)
    extract = json.loads((paths.stages / "02_extract.json").read_text())
    i1 = extract["initiatives"][0]
    i1["sentences"].append({"text": f"Management said {copied}.", "citations": i1["sentences"][0]["citations"]})
    i1["text"] = " ".join(x["text"] for x in i1["sentences"])
    (paths.stages / "02_extract.json").write_text(json.dumps(extract))
    plan, md, report = rendered(paths)
    assert report["omitted_sentences"] == [{"id": "I-01.s2", "reason": "verbatim run of 15+ words from source chunk 10k-p001"}]
    assert plan["metadata"]["omitted_sentences"] == "1 sentences omitted: verbatim runs of 15+ words"
    assert "1 sentences omitted: verbatim runs of 15+ words. Details: render_report.json." in md
    item = plan["key_business_initiatives"][0]
    assert [x["text"] for x in item["sentences"]] == [item["sentences"][0]["text"], rd.OMITTED_MARKER]
    assert item["sentences"][1]["citations"]  # the source link stays with the marker
    assert item["text"].endswith(rd.OMITTED_MARKER) and rd.OMITTED_MARKER in md
    assert copied not in md and copied not in json.dumps(plan)
    on_disk = json.loads((rd.output_dir(paths) / rd.REPORT_NAME).read_text())
    assert on_disk["omitted_sentences"] == report["omitted_sentences"] and copied not in json.dumps(on_disk)
    fourteen = " ".join(words[15:29])
    assert rd.evidence_problems({"x": f"Management said {fourteen}."}, "", [CHUNK_TEXT], []) == []


def test_item_with_every_sentence_omitted_keeps_a_labeled_placeholder(paths):
    extract = json.loads((paths.stages / "02_extract.json").read_text())
    c = extract["challenges"][0]
    c["sentences"][0]["text"] = c["text"] = CHUNK_TEXT
    (paths.stages / "02_extract.json").write_text(json.dumps(extract))
    plan, md, report = rendered(paths)
    c1 = plan["key_challenges"]["public_signals"][0]
    assert c1["text"] == rd.OMITTED_ITEM_TEXT and "sentences" not in c1 and c1["citations"]
    assert f"{rd.OMITTED_ITEM_TEXT}: [Test Co Form 10-K" in md and f"- {rd.OMITTED_MARKER}" not in md
    assert {"id": "C-01.s1", "reason": "verbatim run of 15+ words from source chunk 10k-p001"} in report["omitted_sentences"]


def test_drafted_criterion_with_15_word_run_still_blocks_render(paths):
    out = json.loads((paths.stages / dr.OUTPUT_NAME).read_text())
    out["objectives"][0]["success_criteria"][0]["text"] = " ".join(CHUNK_TEXT.split()[15:30])
    (paths.stages / dr.OUTPUT_NAME).write_text(json.dumps(out))
    with pytest.raises(rd.RenderError, match="run of 15\\+ words copied from a source chunk"):
        rd.render(paths)
    assert not (rd.output_dir(paths) / rd.PLAN_MD).exists()


REAL_EXTRACT = Path(__file__).resolve().parents[1] / "data" / "expedia" / "stages" / "02_extract.json"
REAL_CHUNKS = REAL_EXTRACT.parents[1] / "raw" / "chunks.jsonl"


def real_sentence(item_id, n):
    data = json.loads(REAL_EXTRACT.read_text())
    item = next(i for k in ("initiatives", "challenges") for i in data[k] if i["id"] == item_id)
    return item["sentences"][n - 1]


@pytest.mark.parametrize("item_id", ["I-12", "C-06"])
def test_real_sentences_i12_s1_and_c06_s1_are_omitted(item_id):
    s = real_sentence(item_id, 1)
    chunk_ids = {c["chunk_id"] for c in s["citations"]}
    if REAL_CHUNKS.exists():  # the committed sample's own source chunks (gitignored raw folder)
        chunks = [c for c in map(json.loads, REAL_CHUNKS.read_text().splitlines()) if c["chunk_id"] in chunk_ids]
    else:  # without the raw folder: the sentence inside a chunk of other text
        chunks = [{"chunk_id": sorted(chunk_ids)[0], "text": f"Earlier text. {s['text']} Later text."}]
    plan = {"key_business_initiatives": [], "key_benefits": [],
            "key_challenges": {"public_signals": [{"id": item_id, "text": s["text"] + " Kept.",
                                                   "sentences": [{"text": s["text"]}, {"text": "Kept."}]}]}}
    omitted = rd.omit_long_runs(plan, chunks)
    assert [o["id"] for o in omitted] == [f"{item_id}.s1"]
    assert plan["key_challenges"]["public_signals"][0]["text"] == f"{rd.OMITTED_MARKER} Kept."


def test_news_citations_show_domain_never_headline(paths):
    plan, md, _ = rendered(paths)
    news = [c for s in plan["key_challenges"]["public_signals"] for c in s["citations"] if c["source_type"] == "news"]
    assert news and all(c["title"] == "News article (news.example.com)" for c in news)
    assert f"[News article (news.example.com)]({NEWS_URL})" in md
    assert HEADLINE not in json.dumps(plan) and HEADLINE not in md
    bad = json.loads(json.dumps(plan))
    bad["key_challenges"]["public_signals"][1]["citations"][0]["title"] = HEADLINE
    problems = rd.evidence_problems(bad, md, [], [HEADLINE])
    assert any("not 'News article (domain)'" in p for p in problems) and any("news headline appears" in p for p in problems)


def test_without_selection_every_initiative_awaits_review(paths):
    plan, md, _ = rendered(paths)
    assert {i["csm_selected"] for i in plan["key_business_initiatives"]} == {None}
    assert md.count("Proposed, awaiting CSM review") >= 4 and "Selected by CSM" not in md
    assert "none: every initiative awaits CSM review" in md


def test_optional_selection_applied_at_render(paths):
    (rd.inputs_dir(paths)).mkdir(parents=True)
    (rd.inputs_dir(paths) / rd.SELECTION_NAME).write_text("extract_generated_at: 2026-10-12T00:00:00Z\nselected: [I-01, I-03]\n")
    plan, md, _ = rendered(paths)
    sel = {i["id"]: i["csm_selected"] for i in plan["key_business_initiatives"]}
    assert sel == {"I-01": True, "I-02": False, "I-03": True, "I-04": False}
    assert "### I-02 Cross-team delivery planning\n**Proposed, not selected**" in md
    assert "### I-01 Unified technology platform\n**Selected by CSM**" in md
    assert len(plan["objectives"]) == 3  # selection never removes drafted objectives; it only labels them


@pytest.mark.parametrize("content,match", [
    ("extract_generated_at: 2026-01-01T00:00:00Z\nselected: [I-01]\n", "different 02_extract.json"),
    ("selected: [I-01]\n", "different 02_extract.json"),
    ("extract_generated_at: 2026-10-12T00:00:00Z\nselected: [I-99]\n", "unknown initiative ids: I-99"),
])
def test_bad_selection_refused(paths, content, match):
    rd.inputs_dir(paths).mkdir(parents=True)
    (rd.inputs_dir(paths) / rd.SELECTION_NAME).write_text(content)
    with pytest.raises(rd.RenderError, match=match):
        rd.render(paths)
    assert not (rd.output_dir(paths) / rd.PLAN_JSON).exists()


def test_no_fit_listed_with_reason_and_gaps_but_no_objective(paths):
    plan, md, _ = rendered(paths)
    b4 = next(b for b in plan["key_benefits"] if b["initiative_id"] == "I-04")
    assert b4["fit"] == "no_fit" and b4["gap_ids"] == ["GAP-FINANCIAL-PLANNING"] and b4["capability_ids"] == []
    assert "gaps:" not in b4["no_fit_reason"]
    assert "plan_confirmation" not in b4
    assert any(i["id"] == "I-04" for i in plan["key_business_initiatives"])
    assert not any("I-04" in o["linked_initiative_ids"] for o in plan["objectives"])
    assert ("| Restructuring (I-04) | Restructuring is an organizational and financial matter. | GAP-FINANCIAL-PLANNING |"
            in md)


def test_key_benefits_show_plan_blank_and_product_links(paths):
    plan, md, _ = rendered(paths)
    b2 = next(b for b in plan["key_benefits"] if b["initiative_id"] == "I-02")
    assert b2["plan_confirmation"] == PLAN_CONFIRMATION_BLANK
    assert all(c["source_type"] == "product_docs" and c["url"].startswith("https://") for c in b2["citations"])
    assert "_Fit rationale (inference):_" in md


def test_challenges_and_changes_with_source_links_and_labels(paths):
    plan, md, _ = rendered(paths)
    ids = [c["id"] for c in plan["key_challenges"]["public_signals"]]
    assert ids == ["C-01", "S-01"]
    assert "### S-01 change S-01" in md and "news headline only" in md
    assert f"]({FILING_URL})" in md.split("## Key challenges")[1]


def test_objectives_criteria_tasks_and_items_to_validate(paths):
    plan, md, _ = rendered(paths)
    o = plan["objectives"][1]
    assert o["title"] == "Explore support for: Cross-team delivery planning" and o["fit"] == "full"
    c = o["success_criteria"][0]
    assert c["needs_baseline"] and c["baseline_question"] and "[CSM to complete: set after baseline]" in c["proposed_target"]
    assert plan["success_criteria"] == [x for ob in plan["objectives"] for x in ob["success_criteria"]]
    assert [t["proposed_timeline"]["window"] for t in o["tasks"]] == ["Day 31-60", "Day 61-90"]
    assert o["tasks"][0]["text"] == "Agree baselines for this objective's success criteria."
    assert all(t["proposed_timeline"]["is_proposed"] for t in o["tasks"])
    refs = {r for x in plan["items_to_validate"] for r in x["ref_id"].split(", ")}
    assert {"I-01", "B-02", c["id"], "C-01", "S-01"} <= refs
    assert {"ref_id": "B-01, B-02, B-03", "question": PLAN_CONFIRMATION_BLANK} in plan["items_to_validate"]
    assert "| Day 61-90 | Customer engineering lead | Propose a pilot of" in md and "with the CSM" not in md
    drafted = [l for l in md.split("## Objectives")[1].split("## Items to validate")[0].splitlines() if "Based on:" not in l]
    assert not re.search(r"\b20\d\d\b", "\n".join(drafted))  # no calendar dates outside source links


def test_metadata_records_profiles_replay_and_real_cost(paths):
    # Replay Map from its saved real run: metadata must name the run and take the real cost from the usage log.
    from success_plan.stages import map as mp
    from test_map import CONFIG as MAP_CONFIG
    mp.replay_map(paths, MAP_CONFIG, mp.latest_response_dir(paths))
    dr.replay_draft(paths, DRAFT_CONFIG, dr.latest_response_dir(paths))
    plan, md, _ = rendered(paths)
    s = plan["metadata"]["stages"]["map"]
    assert s["profile"] == "test" and s["replayed_from"].startswith("data/testco/raw/llm/map-")
    real = [json.loads(l) for l in (paths.stages / "usage_log.jsonl").read_text().splitlines() if json.loads(l)["stage"] == "map"][-1]
    assert s["cost_usd"] == real["cost_usd"] > 0 and "Real cost from usage_log.jsonl" in s["cost_note"]
    assert "Replay of a saved real run" in md


def test_price_in_drafted_text_fails_render(paths):
    out = json.loads((paths.stages / dr.OUTPUT_NAME).read_text())
    out["objectives"][0]["success_criteria"][0]["text"] = "Teams pay $8 per user each month."
    (paths.stages / dr.OUTPUT_NAME).write_text(json.dumps(out))
    with pytest.raises(rd.RenderError, match="price or plan tier"):
        rd.render(paths)
    assert not (rd.output_dir(paths) / rd.PLAN_MD).exists()


def test_missing_stage_refused(paths):
    (paths.stages / dr.OUTPUT_NAME).unlink()
    with pytest.raises(rd.RenderError, match="04_draft.json"):
        rd.render(paths)


# ---------------------------------------------------------------- readability fixes

def test_back_reference_and_validation_question_follow_an_omitted_sentence():
    plan = {"key_business_initiatives": [{
        "id": "I-12", "title": "trivago brand rejuvenation", "validation_question": "What comes after the 2023 relaunch?",
        "text": "", "sentences": [{"text": "In 2023, trivago adapted its marketing strategy and launched a new logo and visual identity.",
                                   "citations": [{"url": "u"}]},
                                  {"text": "This was part of a push to rejuvenate the brand.", "citations": [{"url": "u"}]},
                                  {"text": "Brand spending rose.", "citations": [{"url": "u"}]}]}],
        "key_benefits": [], "key_challenges": {"public_signals": []}}
    chunk = {"chunk_id": "k1", "text": "Earlier. In 2023, trivago adapted its marketing strategy and launched a new logo and visual identity. Later."}
    omitted = rd.omit_long_runs(plan, [chunk])
    item = plan["key_business_initiatives"][0]
    assert [x["text"] for x in item["sentences"]] == [rd.OMITTED_MARKER, rd.OMITTED_MARKER, "Brand spending rose."]
    assert item["text"] == f"{rd.OMITTED_MARKER} {rd.OMITTED_MARKER} Brand spending rose."
    assert [o["id"] for o in omitted] == ["I-12.s1", "I-12.s2", "I-12 validation question"]
    assert omitted[1]["reason"] == "refers back to the omitted sentence before it"
    assert item["validation_question"] == rd.VALIDATION_BLANK and "2023" in omitted[2]["reason"]


def test_validation_question_kept_when_it_relies_only_on_shown_text():
    plan = {"key_business_initiatives": [{
        "id": "I-01", "title": "Brand work", "validation_question": "How is brand spending tracked?", "text": "",
        "sentences": [{"text": " ".join(f"w{n}" for n in range(16)), "citations": [{"url": "u"}]},
                      {"text": "Brand spending rose.", "citations": [{"url": "u"}]}]}],
        "key_benefits": [], "key_challenges": {"public_signals": []}}
    rd.omit_long_runs(plan, [{"chunk_id": "k", "text": " ".join(f"w{n}" for n in range(16))}])
    assert plan["key_business_initiatives"][0]["validation_question"] == "How is brand spending tracked?"


def test_metadata_count_excludes_replaced_questions(paths):
    extract = json.loads((paths.stages / "02_extract.json").read_text())
    i1 = extract["initiatives"][0]
    i1["sentences"][0]["text"] = i1["text"] = CHUNK_TEXT
    i1["validation_question"] = "Has the multi-year technology roadmap changed?"
    (paths.stages / "02_extract.json").write_text(json.dumps(extract))
    plan, md, report = rendered(paths)
    assert plan["metadata"]["omitted_sentences"].startswith("1 sentences omitted")
    assert plan["key_business_initiatives"][0]["validation_question"] == rd.VALIDATION_BLANK
    assert "roadmap" not in md.split("## Items to validate")[1]


def test_repeated_citations_to_the_same_source_and_location_shown_once(paths):
    extract = json.loads((paths.stages / "02_extract.json").read_text())
    s1 = extract["initiatives"][0]["sentences"][0]
    s1["citations"] = [s1["citations"][0], dict(s1["citations"][0], chunk_id="chunk-other")]
    (paths.stages / "02_extract.json").write_text(json.dumps(extract))
    plan, md, _ = rendered(paths)
    assert len(plan["key_business_initiatives"][0]["sentences"][0]["citations"]) == 1
    line = next(l for l in md.splitlines() if l.startswith("- The company runs its brands"))
    assert line.count(FILING_URL) == 1


def test_news_link_does_not_repeat_the_domain(paths):
    extract = json.loads((paths.stages / "02_extract.json").read_text())
    c = extract["changes"][0]["sentences"][0]["citations"][0]
    c["location"] = "news.example.com"  # Retrieve stores the domain as the news location
    (paths.stages / "02_extract.json").write_text(json.dumps(extract))
    _, md, _ = rendered(paths)
    assert f"[News article (news.example.com)]({NEWS_URL})" in md and "news.example.com), news.example.com" not in md


def test_benefits_use_plain_wording_and_a_compact_no_fit_table(paths):
    _, md, _ = rendered(paths)
    section = md.split("## Key benefits")[1].split("## Renewal")[0]
    assert "→" not in md
    assert "### Cross-team delivery planning (I-02): full fit" in section and "Benefit B-02" in section
    assert "### Initiatives the tool did not map (no_fit)" in section and "| Initiative | Reason | Gap IDs |" in section
    assert "### Restructuring" not in section  # no_fit initiatives get a table row, not a full entry


def test_items_to_validate_grouped_once_each(paths):
    extract = json.loads((paths.stages / "02_extract.json").read_text())
    for i in extract["initiatives"]:
        i["validation_question"] = f"What is the status of {i['id']}?"
    (paths.stages / "02_extract.json").write_text(json.dumps(extract))
    mapping = json.loads((paths.stages / "03_map.json").read_text())
    for b in mapping["key_benefits"]:
        b["validation_question"] = f"Does {b['id']} fit?"
    (paths.stages / "03_map.json").write_text(json.dumps(mapping))
    plan, md, _ = rendered(paths)
    items = md.split("## Items to validate with the customer")[1].split("---")[0]
    questions = [l.split(": ", 1)[1] for l in items.splitlines() if l.startswith("- ")]
    assert len(questions) == len(set(questions))
    assert [h for h in re.findall(r"^### (.+)$", items, re.M)] == [
        "Key business initiatives", "Key benefits", "Success criteria", "Key challenges and potential blockers",
        rd.NOT_MAPPED_HEADING]
    optional = items.split(f"### {rd.NOT_MAPPED_HEADING}")[1]
    assert "- I-04:" in optional and "- B-04:" in optional and "- I-04:" not in items.split(f"### {rd.NOT_MAPPED_HEADING}")[0]
    plan_blank = [x for x in plan["items_to_validate"] if x["question"] == PLAN_CONFIRMATION_BLANK]
    assert len(plan_blank) == 1 and plan_blank[0]["ref_id"] == "B-01, B-02, B-03"


def real_item(item_id):
    data = json.loads(REAL_EXTRACT.read_text())
    return next(i for k in ("initiatives", "challenges") for i in data[k] if i["id"] == item_id)


def real_chunks_for(*items):
    ids = {c["chunk_id"] for i in items for s in i["sentences"] for c in s["citations"]}
    if REAL_CHUNKS.exists():
        return [c for c in map(json.loads, REAL_CHUNKS.read_text().splitlines()) if c["chunk_id"] in ids]
    return [{"chunk_id": "synthetic", "text": f"Earlier text. {i['sentences'][0]['text']} Later text."} for i in items]


def test_real_i12_and_c06_omission_layout_and_question_placement():
    i12, c06 = real_item("I-12"), real_item("C-06")
    as_plan = lambda i: {"id": i["id"], "title": i["title"], "text": i["text"], "validation_question": i["validation_question"],  # noqa: E731
                         "citations": [{"url": c["url"], "title": c["title"], "location": c.get("location")} for c in i["citations"]],
                         "sentences": [{"text": s["text"], "citations": [{"url": c["url"], "title": c["title"],
                                                                         "location": c.get("location")} for c in s["citations"]]}
                                       for s in i["sentences"]]}
    plan = {"key_business_initiatives": [as_plan(i12)], "key_challenges": {"public_signals": [as_plan(c06)]},
            "key_benefits": [{"id": "B-12", "initiative_id": "I-12", "fit": "no_fit", "text": "No fit.", "validation_question": "Fit?"}],
            "objectives": []}
    omitted = rd.omit_long_runs(plan, real_chunks_for(i12, c06))
    item = plan["key_business_initiatives"][0]
    # I-12: both sentences omitted (s2 points back at s1): one line with the source link, no blank bullets.
    assert item["text"] == rd.OMITTED_ITEM_TEXT and "sentences" not in item and item["citations"]
    lines = []
    rd._sentences(item, lines)
    assert lines == [f"{rd.OMITTED_ITEM_TEXT}: {rd._links(item['citations'])}"] and not lines[0].startswith("- ")
    # C-06: one sentence omitted, one shown; its question is a blank listed under challenges.
    c = plan["key_challenges"]["public_signals"][0]
    assert c["sentences"][0]["text"] == rd.OMITTED_MARKER and c["sentences"][1]["text"] != rd.OMITTED_MARKER
    groups = rd.validation_groups(plan)
    assert {"ref_id": "C-06", "question": rd.VALIDATION_BLANK} in groups["Key challenges and potential blockers"]
    assert {"ref_id": "I-12", "question": rd.VALIDATION_BLANK} in groups[rd.NOT_MAPPED_HEADING]
    assert {o["id"] for o in omitted} >= {"I-12.s1", "I-12.s2", "C-06.s1", "I-12 validation question", "C-06 validation question"}


def test_validation_blank_wording():
    assert rd.VALIDATION_BLANK == "[CSM to complete: validation question]"
    assert rd.VALIDATION_BLANK.startswith("[CSM to complete:")  # merged only within its own section


# ---------------------------------------------------------------- header and review note

def test_header_and_default_review_note(paths):
    plan, md, _ = rendered(paths)
    assert rd.NOTICE == ("Generated automatically from public information; not validated with the company; the CSM "
                         "reviews and edits before use.")
    assert plan["metadata"]["review_note"] == "No human has reviewed this output."
    lines = md.splitlines()
    assert lines[2] == f"> **{rd.NOTICE}**" and lines[4] == "> Review: No human has reviewed this output."
    assert md.index("> Review:") < md.index(rd.DISCLAIMER)


def test_review_note_read_from_inputs(paths):
    rd.inputs_dir(paths).mkdir(parents=True, exist_ok=True)
    (rd.inputs_dir(paths) / rd.REVIEW_NOTE_NAME).write_text("Extraction read in full\nby the author.\n")
    plan, md, _ = rendered(paths)
    assert plan["metadata"]["review_note"] == "Extraction read in full by the author."
    assert "> Review: Extraction read in full by the author." in md
    jsonschema.validate(plan, json.loads(rd.SCHEMA_FILE.read_text()))
