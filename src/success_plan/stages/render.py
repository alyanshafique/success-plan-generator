"""Stage 5: Render the plan as schema-validated JSON and Markdown. No model call.

Render also does what was planned as a separate Flag stage: it gathers every validation, baseline, and
plan-confirmation question into items_to_validate, adds the CSM blanks, runs plan-wide guards, and validates the
JSON against schema/success_plan_schema.json before writing anything.

Inputs: 01_retrieve.json, 02_extract.json, 03_map.json, 04_draft.json, the product config, the brands in
config/company_aliases.yaml, the usage log (real cost of replayed stages), and an optional CSM selection file
data/<company>/inputs/csm_selection.yaml. The plan shows source links only, never evidence phrases.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import jsonschema
import yaml

from .. import guards
from ..config import load_brand_aliases
from ..models import (
    OBJECTIVES_BLANK, PLAN_CONFIRMATION_BLANK, DraftOutput, ExtractItem, ExtractOutput, MapOutput, RetrieveManifest,
)
from ..paths import PROJECT_ROOT, CompanyPaths
from .map import load_config

SCHEMA_FILE = PROJECT_ROOT / "schema" / "success_plan_schema.json"
SELECTION_NAME = "csm_selection.yaml"
PLAN_JSON, PLAN_MD, REPORT_NAME = "plan.json", "plan.md", "render_report.json"
DISCLAIMER = ("All inputs are publicly available. This tool does not use proprietary or customer data. This output is a "
              "hypothesis generated from public information and has not been validated with the company.")
NOTICE = "First-draft hypothesis for a CSM to review and edit."
BLANKS = {
    "renewal": "[CSM to complete: renewal date, ARR, status, next steps]",
    "account_history": "[CSM to complete: account history]",
    "customer_raised": "[CSM to complete: challenges the customer has raised]",
}
OMITTED_ITEM_TEXT = "Source passages omitted (verbatim text from the filing); see source link"
OMITTED_MARKER = "[sentence omitted: verbatim passage from the filing]"
VALIDATION_BLANK = "[CSM to complete: validation question]"
BACK_REFERENCE = re.compile(r"(?:this|that|these|those|such|it|its|they|their)\b", re.IGNORECASE)
NOT_MAPPED_HEADING = "Optional discovery for initiatives the tool did not map"
STATUS = {None: "Proposed, awaiting CSM review", True: "Selected by CSM", False: "Proposed, not selected"}


class RenderError(Exception):
    pass


# ---------------------------------------------------------------- inputs

def inputs_dir(paths: CompanyPaths) -> Path:
    return paths.stages.parent / "inputs"


def output_dir(paths: CompanyPaths) -> Path:
    return paths.root / "output" / paths.slug


def load_selection(paths: CompanyPaths, extract: ExtractOutput) -> dict[str, bool | None]:
    """csm_selected per initiative: null for all without a selection file; true or false with one."""
    path = inputs_dir(paths) / SELECTION_NAME
    ids = [i.id for i in extract.initiatives]
    if not path.exists():
        return {i: None for i in ids}
    data = yaml.safe_load(path.read_text()) or {}
    stamp = data.get("extract_generated_at")
    if stamp is None or datetime.fromisoformat(str(stamp).replace("Z", "+00:00")) != extract.generated_at:
        raise RenderError(f"{SELECTION_NAME} was made for a different 02_extract.json (extract_generated_at "
                          f"{stamp!r}, expected {extract.generated_at.isoformat()}).")
    selected = [str(x) for x in data.get("selected") or []]
    unknown = sorted(set(selected) - set(ids))
    if unknown:
        raise RenderError(f"{SELECTION_NAME} lists unknown initiative ids: {', '.join(unknown)}")
    return {i: i in selected for i in ids}


def load_stages(paths: CompanyPaths):
    files = {"01_retrieve.json": RetrieveManifest, "02_extract.json": ExtractOutput, "03_map.json": MapOutput,
             "04_draft.json": DraftOutput}
    missing = [f for f in files if not (paths.stages / f).exists()]
    if missing:
        raise RenderError(f"Missing stage output(s): {', '.join(missing)}. Run the earlier stages first.")
    out = [cls.model_validate_json((paths.stages / f).read_text()) for f, cls in files.items()]
    _, extract, mapping, draft = out
    if mapping.extract_generated_at != extract.generated_at or draft.extract_generated_at != extract.generated_at:
        raise RenderError("03_map.json or 04_draft.json was made from a different 02_extract.json.")
    if draft.map_generated_at != mapping.generated_at:
        raise RenderError("04_draft.json was made from a different 03_map.json.")
    return out


def usage_log(paths: CompanyPaths) -> list[dict]:
    path = paths.stages / "usage_log.jsonl"
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()] if path.exists() else []


def stage_record(stage: str, output, log: list[dict]) -> dict:
    model = output.usage[0].model if output.usage else "unknown"
    if output.replayed_from:
        real = [r for r in log if r.get("stage") == stage and r.get("responses") == output.replayed_from and r.get("status") == "ok"]
        note = (f"Replay of a saved real run ({output.replayed_from}); no API call at replay. "
                + ("Real cost from usage_log.jsonl." if real else "Real run not found in usage_log.jsonl."))
        return {"profile": output.profile, "model": model, "replayed_from": output.replayed_from,
                "cost_usd": real[-1]["cost_usd"] if real else None, "cost_note": note}
    return {"profile": output.profile, "model": model, "replayed_from": None, "cost_usd": output.total_cost_usd,
            "cost_note": "Cost of this run."}


# ---------------------------------------------------------------- building blocks

def _citation(c) -> dict:
    # A news citation's title is the headline, and its evidence phrase is part of that headline: show the domain.
    title = f"News article ({urlparse(c.url).netloc.removeprefix('www.')})" if c.source_type == "news" else c.title
    d = {"source_type": c.source_type, "title": title, "url": c.url, "date": c.date.isoformat()}
    if c.location:
        d["location"] = c.location
    if c.event_date:
        d["event_date"] = c.event_date.isoformat()
    return d


def _product_citations(cits) -> list[dict]:
    """One link per cited capability: its first source URL (the capability's main page), verified_on as the date."""
    out = {}
    for c in cits:
        if not c.source_urls:
            continue
        d = {"source_type": "product_docs", "title": c.capability_name, "url": c.source_urls[0]}
        if c.verified_on:
            d["date"] = c.verified_on.isoformat()
        out[d["url"]] = d
    return list(out.values())


def _dedupe(cits: list[dict]) -> list[dict]:
    """One citation per source and location (first one kept)."""
    out: dict[tuple, dict] = {}
    for c in cits:
        out.setdefault((c["url"], c.get("location")), c)
    return list(out.values())


def _item(item: ExtractItem, owner: str) -> dict:
    d = {"id": item.id, "title": item.title, "text": item.text, "owner": owner, "provenance": item.provenance,
         "citations": _dedupe([_citation(c) for c in item.citations]),
         "sentences": [{"text": s.text, "citations": _dedupe([_citation(c) for c in s.citations])} for s in item.sentences],
         "validation_question": item.validation_question, "labels": list(item.labels)}
    if not d["sentences"]:
        del d["sentences"]
    return d


def overview(manifest: RetrieveManifest, extract: ExtractOutput) -> dict:
    """Code-built: identifiers and brands only, cited to the 10-K. No descriptive claims about the business."""
    f, c = manifest.filing, manifest.company
    brands = load_brand_aliases(c.cik) or []
    text = (f"{c.name} (ticker {c.ticker}). Latest annual report: Form {f.form} for fiscal year {f.fiscal_year}, "
            f"filed {f.filing_date.isoformat()}.")
    if brands:
        text += f" Brands named in Item 1 (Business) of that filing: {', '.join(brands)}."
    return {"id": "CH-01", "text": text, "owner": "claude", "provenance": "sourced",
            "citations": [{"source_type": "filing", "title": f"{c.name} Form {f.form} for fiscal year {f.fiscal_year}",
                           "location": "Item 1 Business", "url": f.url, "date": f.filing_date.isoformat()}]}


def _benefit(b) -> dict:
    d = {"id": b.id, "initiative_id": b.initiative_id, "owner": b.owner, "provenance": b.provenance, "fit": b.fit,
         "capability_ids": list(b.capability_ids), "fit_rationale": b.fit_rationale,
         "validation_question": b.validation_question, "labels": list(b.labels)}
    if b.fit == "no_fit":
        d.update(text="No fit with the capabilities in the product config.", no_fit_reason=b.no_fit_reason or "Named by gap ids.",
                 gap_ids=list(b.gap_ids))
    else:
        cits = [c for s in b.sentences for c in s.citations]
        d.update(text=b.text, citations=_product_citations(cits), plan_confirmation=PLAN_CONFIRMATION_BLANK,
                 sentences=[{"text": s.text, "citations": _product_citations(s.citations)} for s in b.sentences])
    return d


def _criterion(c) -> dict:
    d = {"id": c.id, "text": c.text, "owner": c.owner, "provenance": c.provenance,
         "needs_baseline": c.needs_baseline, "validation_question": c.validation_question, "labels": list(c.labels)}
    cits = _dedupe([_citation(x) for x in c.citations] + _product_citations(c.product_citations))
    if cits:
        d["citations"] = cits
    for k in ("criterion_type", "indicator_type", "measure", "proposed_target", "baseline_question"):
        if getattr(c, k):
            d[k] = getattr(c, k)
    return d


def _objective(o) -> dict:
    return {"id": o.id, "title": o.title, "owner": o.owner, "provenance": o.provenance,
            "linked_initiative_ids": list(o.linked_initiative_ids), "capability_ids": list(o.capability_ids), "fit": o.fit,
            "labels": list(o.labels), "success_criteria": [_criterion(c) for c in o.success_criteria],
            "tasks": [{"id": t.id, "text": t.text, "responsible_role": t.responsible_role,
                       "proposed_timeline": {"window": t.window, "is_proposed": True}, "provenance": t.provenance,
                       "validation_question": t.validation_question} for t in o.tasks]}


def validation_groups(plan: dict) -> dict[str, list[dict]]:
    """Questions by plan section, each question once (its ref ids joined). Questions about initiatives Map did not
    fit go under NOT_MAPPED_HEADING."""
    not_mapped = {b["initiative_id"] for b in plan["key_benefits"] if b["fit"] == "no_fit"}
    groups: dict[str, list[dict]] = {"Key business initiatives": [], "Key benefits": [], "Success criteria": [],
                                     "Key challenges and potential blockers": [], NOT_MAPPED_HEADING: []}
    seen: dict[str, dict] = {}

    def add(section, ref, question):
        if not question:
            return
        # A CSM blank is merged only within its own section, so a blank for a challenge never lands elsewhere.
        key = (section, question) if question.startswith("[CSM to") else question
        if key in seen:
            if ref not in seen[key]["ref_id"].split(", "):
                seen[key]["ref_id"] += f", {ref}"
            return
        seen[key] = {"ref_id": ref, "question": question}
        groups[section].append(seen[key])

    for i in plan["key_business_initiatives"]:
        add(NOT_MAPPED_HEADING if i["id"] in not_mapped else "Key business initiatives", i["id"], i.get("validation_question"))
    for b in plan["key_benefits"]:
        section = NOT_MAPPED_HEADING if b["fit"] == "no_fit" else "Key benefits"
        add(section, b["id"], b.get("validation_question"))
        add(section, b["id"], b.get("plan_confirmation"))
    for o in plan["objectives"]:
        for c in o["success_criteria"]:
            add("Success criteria", c["id"], c.get("baseline_question"))
            add("Success criteria", c["id"], c.get("validation_question"))
    for c in plan["key_challenges"]["public_signals"]:
        add("Key challenges and potential blockers", c["id"], c.get("validation_question"))
    return groups


def items_to_validate(plan: dict) -> list[dict]:
    return [x for group in validation_groups(plan).values() for x in group]


# ---------------------------------------------------------------- guards

def _strings(node, path=""):
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _strings(v, f"{path}.{k}" if path else k)
    elif isinstance(node, list):
        for n, v in enumerate(node):
            yield from _strings(v, f"{path}[{n}]")
    elif isinstance(node, str):
        yield path, node


def plan_guards(plan: dict) -> tuple[list[str], list[str]]:
    """(errors, warnings). A price or plan tier in drafted or mapped text is an error (ground rule 10); in text
    from public sources it is a warning (company financials). Customer framing anywhere is a warning."""
    errors, warnings = [], []
    fixed = set(BLANKS.values()) | {PLAN_CONFIRMATION_BLANK, OBJECTIVES_BLANK, DISCLAIMER, NOTICE}
    for path, text in _strings({k: v for k, v in plan.items() if k != "metadata"}):
        if text in fixed or ".citations" in path or path.endswith((".url", ".id")):
            continue
        generated = path.startswith(("key_benefits", "objectives", "success_criteria"))
        hit = guards.find_currency_amounts(text) or guards.plan_tier_phrase(text)
        if hit and generated:
            errors.append(f"{path}: states a price or plan tier")
        elif hit:
            warnings.append(f"{path}: currency amount or plan wording in source-derived text (check it is not a price)")
        if guards.implies_customer(text):
            warnings.append(f"{path}: wording implies the company already uses the product")
    return errors, warnings


# ---------------------------------------------------------------- evidence checks

NEWS_TITLE = re.compile(r"News article \([^()\s]+\)")


def _keys(node):
    if isinstance(node, dict):
        for k, v in node.items():
            yield k
            yield from _keys(v)
    elif isinstance(node, list):
        for v in node:
            yield from _keys(v)


def load_chunks(paths: CompanyPaths) -> list[dict] | None:
    path = paths.raw / "chunks.jsonl"
    if not path.exists():
        return None
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def load_chunk_texts(paths: CompanyPaths) -> list[str] | None:
    chunks = load_chunks(paths)
    return None if chunks is None else [c["text"] for c in chunks]


def omit_long_runs(plan: dict, chunks: list[dict]) -> list[dict]:
    """Replace every sentence that copies 15+ consecutive words from a source chunk (the long-quote count) with an
    inline marker, keeping its source link. A following sentence that opens by pointing back at it ("This was ...")
    is replaced too, and a validation question that relies on omitted content becomes a CSM blank. Returns
    [{id, reason}] for the render report; the copied words are not repeated there."""
    index: dict[tuple, str] = {}
    for c in chunks:
        for gram in guards.quote_grams(c["text"]):
            index.setdefault(gram, c.get("chunk_id", "unknown chunk"))
    omitted = []

    def hit(text):
        found = sorted({index[g] for g in guards.quote_grams(text) if g in index})
        return f"verbatim run of {guards.LONG_QUOTE_WORDS}+ words from source chunk {', '.join(found)}" if found else None

    items = plan["key_business_initiatives"] + plan["key_challenges"]["public_signals"] + plan["key_benefits"]
    for item in items:
        hidden = []
        if item.get("sentences"):
            previous_omitted = False
            for n, sentence in enumerate(item["sentences"], start=1):
                reason = hit(sentence["text"])
                if not reason and previous_omitted and BACK_REFERENCE.match(sentence["text"]):
                    reason = "refers back to the omitted sentence before it"
                if reason:
                    omitted.append({"id": f"{item['id']}.s{n}", "reason": reason})
                    hidden.append(sentence["text"])
                    sentence["text"] = OMITTED_MARKER
                previous_omitted = bool(reason)
            if hidden and all(x["text"] == OMITTED_MARKER for x in item["sentences"]):
                item["text"] = OMITTED_ITEM_TEXT  # one line with the source link, no row of blank markers
                item["citations"] = _dedupe(list(item.get("citations") or []) + [c for x in item["sentences"] for c in x["citations"]])
                del item["sentences"]
            elif hidden:
                item["text"] = " ".join(x["text"] for x in item["sentences"])
        else:
            reason = hit(item["text"])
            if reason:
                omitted.append({"id": item["id"], "reason": reason})
                hidden.append(item["text"])
                item["text"] = OMITTED_ITEM_TEXT
        question = item.get("validation_question")
        if hidden and question:
            shown = guards._terms(" ".join([item.get("title", ""), item["text"]]))
            missing = guards._terms(" ".join(hidden))
            relies = [surface for key, surface in guards._terms(question).items() if key in missing and key not in shown]
            if relies:
                item["validation_question"] = VALIDATION_BLANK
                omitted.append({"id": f"{item['id']} validation question",
                                "reason": f"relied on omitted content ({', '.join(relies)}); replaced with a CSM blank"})
    return omitted


def evidence_problems(plan: dict, md: str, chunk_texts: list[str] | None, headlines: list[str]) -> list[str]:
    """(a) no evidence field or key; (b) no run of 15+ consecutive words from a source chunk; (c) news citations
    show "News article (domain)", never a headline."""
    problems = [f"evidence key {k!r} in plan.json" for k in sorted({k for k in _keys(plan) if "evidence" in k.lower()})]
    if re.search(r"\bevidence(?:_\w+)?\s*[:=]", md, re.IGNORECASE) or '"evidence' in md:
        problems.append("evidence field in plan.md")
    if chunk_texts is not None:
        texts = [t for path, t in _strings(plan) if not path.endswith(".url")] + md.splitlines()
        problems += [f"run of {guards.LONG_QUOTE_WORDS}+ words copied from a source chunk: {span!r}"
                     for span in guards.long_quote_spans(texts, chunk_texts)]
    for path, text in _strings(plan):
        if path.endswith(".source_type") and text == "news":
            citation = _at(plan, path.rsplit(".", 1)[0])
            if not NEWS_TITLE.fullmatch(citation.get("title", "")):
                problems.append(f"{path}: news citation title is not 'News article (domain)'")
    body = json.dumps(plan, ensure_ascii=False) + md
    problems += [f"news headline appears in the plan: {h!r}" for h in headlines if h and h in body]
    return problems


def _at(node, path: str):
    for part in re.findall(r"[^.\[\]]+|\[\d+\]", path):
        node = node[int(part[1:-1])] if part.startswith("[") else node[part]
    return node


# ---------------------------------------------------------------- render

def build_plan(paths: CompanyPaths, now: datetime | None = None) -> tuple[dict, dict]:
    manifest, extract, mapping, draft = load_stages(paths)
    selection = load_selection(paths, extract)
    config = load_config()
    log = usage_log(paths)
    now = now or datetime.now(timezone.utc).replace(microsecond=0)
    products = (config.get("meta") or {}).get("products_in_scope") or []
    f = manifest.filing
    metadata = {
        "company": extract.company, "ticker": manifest.company.ticker,
        "product": f"Atlassian {' and '.join(products)}".strip(), "generated_at": now.isoformat(),
        "filing": {"form": f.form, "fiscal_year": f.fiscal_year, "filing_date": f.filing_date.isoformat(), "url": f.url},
        "news_window": {"from": manifest.news.window_from.isoformat(), "to": manifest.news.window_to.isoformat()},
        "notice": NOTICE,
        "stages": {"extract": stage_record("extract", extract, log), "map": stage_record("map", mapping, log),
                   "draft": stage_record("draft", draft, log)},
        "omitted_sentences": "",
        "csm_selection": "applied from inputs/csm_selection.yaml" if any(v is not None for v in selection.values())
                         else "none: every initiative awaits CSM review",
        "disclaimer": DISCLAIMER,
    }
    initiatives = []
    for item in extract.initiatives:
        d = _item(item, "shared")
        d["csm_selected"] = selection[item.id]
        initiatives.append(d)
    objectives = [_objective(o) for o in draft.objectives]
    plan = {
        "metadata": metadata,
        "company_highlights": {"overview": overview(manifest, extract),
                               "account_history": {"owner": "csm", "provenance": "csm-input", "placeholder": BLANKS["account_history"]}},
        "key_business_initiatives": initiatives,
        "key_benefits": [_benefit(b) for b in mapping.key_benefits],
        "renewal": {"owner": "csm", "provenance": "csm-input", "placeholder": BLANKS["renewal"]},
        "success_criteria": [c for o in objectives for c in o["success_criteria"]],
        "key_challenges": {"public_signals": [_item(i, "shared") for i in extract.challenges + extract.changes],
                           "customer_raised": {"owner": "csm", "provenance": "csm-input", "placeholder": BLANKS["customer_raised"]}},
        "objectives": objectives,
        "objectives_csm_blank": {"owner": "csm", "provenance": "csm-input", "placeholder": OBJECTIVES_BLANK},
        "items_to_validate": [],
    }
    chunks = load_chunks(paths)
    omitted = omit_long_runs(plan, chunks) if chunks is not None else []
    n_sentences = sum(1 for o in omitted if not o["id"].endswith("validation question"))
    metadata["omitted_sentences"] = (f"{n_sentences} sentences omitted: verbatim runs of {guards.LONG_QUOTE_WORDS}+ words"
                                     if chunks is not None else "Long-run check not run: raw/chunks.jsonl not found")
    plan["items_to_validate"] = items_to_validate(plan)
    errors, warnings = plan_guards(plan)
    if errors:
        raise RenderError("Plan-wide guard failed (nothing written):\n" + "\n".join(errors))
    try:
        jsonschema.validate(plan, json.loads(SCHEMA_FILE.read_text()))
    except jsonschema.ValidationError as exc:
        raise RenderError(f"Plan does not match the schema (nothing written): {exc.message} at {list(exc.absolute_path)}") from exc
    report = {"stage": "render", "generated_at": now.isoformat(), "warnings": warnings, "omitted_sentences": omitted,
              "unmapped_objectives_note": OBJECTIVES_BLANK, "not_drafted": draft.not_drafted}
    return plan, report


def render(paths: CompanyPaths, now: datetime | None = None) -> tuple[Path, Path, dict]:
    plan, report = build_plan(paths, now)
    md = markdown(plan)
    chunks = load_chunk_texts(paths)
    headlines = [h.headline for h in RetrieveManifest.model_validate_json((paths.stages / "01_retrieve.json").read_text()).news.headlines]
    problems = evidence_problems(plan, md, chunks, headlines)
    if problems:
        raise RenderError("Evidence check failed (nothing written):\n" + "\n".join(problems))
    report["evidence_checks"] = ("passed: no evidence field; no news headline; no run of "
                                 f"{guards.LONG_QUOTE_WORDS}+ words from {len(chunks)} source chunks" if chunks is not None
                                 else "passed except the long-run check: raw/chunks.jsonl not found")
    out = output_dir(paths)
    out.mkdir(parents=True, exist_ok=True)
    (out / PLAN_JSON).write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n")
    (out / PLAN_MD).write_text(md)
    (out / REPORT_NAME).write_text(json.dumps(report, indent=2) + "\n")
    from .csm_draft import write_csm_draft  # the CSM draft is built from the plan.json just written

    draft_md, draft_docx = write_csm_draft(paths, headlines)
    report["csm_draft"] = [draft_md.name, draft_docx.name]
    return out / PLAN_JSON, out / PLAN_MD, report


# ---------------------------------------------------------------- Markdown

def _links(cits: list[dict]) -> str:
    parts = []
    for c in cits:
        location = c.get("location")
        label = c["title"] + (f", {location}" if location and location.lower() not in c["title"].lower() else "")
        when = f" ({c['date']})" if c.get("date") else ""
        parts.append(f"[{label}]({c['url']}){when}" if c.get("url") else f"{label}{when}")
    return "; ".join(parts)


def _labels(d: dict) -> str:
    return f" · _{', '.join(d['labels'])}_" if d.get("labels") else ""


def _sentences(d: dict, lines: list[str]) -> None:
    if d.get("sentences"):
        for s in d["sentences"]:
            lines.append(f"- {s['text']} — {_links(s['citations'])}")
    elif d["text"] == OMITTED_ITEM_TEXT:
        lines.append(f"{d['text']}: {_links(d.get('citations') or [])}")
    else:
        lines.append(f"- {d['text']}" + (f" — {_links(d['citations'])}" if d.get("citations") else ""))


def markdown(plan: dict) -> str:
    m = plan["metadata"]
    status = {i["id"]: STATUS[i["csm_selected"]] for i in plan["key_business_initiatives"]}
    titles = {i["id"]: i["title"] for i in plan["key_business_initiatives"]}
    L = [f"# Hypothetical success plan: {m['company']} and {m['product']}", "",
         f"> **{m['notice']}**", ">", f"> {m['disclaimer']}", "",
         f"Filing: Form {m['filing']['form']} for fiscal year {m['filing']['fiscal_year']}, filed {m['filing']['filing_date']} "
         f"([EDGAR]({m['filing']['url']})). News window: {m['news_window']['from']} to {m['news_window']['to']}. "
         f"Generated {m['generated_at']}. CSM selection: {m['csm_selection']}.", "",
         f"{m['omitted_sentences']}. Details: {REPORT_NAME}.", ""]
    for name, s in m["stages"].items():
        cost = "unknown" if s["cost_usd"] is None else f"${s['cost_usd']:.4f}"
        L.append(f"- {name}: profile {s['profile']}, {s['model']}, cost {cost}. {s['cost_note']}")
    L += ["", "## Company highlights", "", plan["company_highlights"]["overview"]["text"],
          f"Source: {_links(plan['company_highlights']['overview']['citations'])}", "",
          f"**Account history:** {plan['company_highlights']['account_history']['placeholder']}", "",
          "## Key business initiatives", ""]
    for i in plan["key_business_initiatives"]:
        L.append(f"### {i['id']} {i['title']}")
        L.append(f"**{status[i['id']]}** · {i['provenance']}{_labels(i)}")
        L.append("")
        _sentences(i, L)
        L += ["", f"_Validate:_ {i['validation_question']}", ""]
    L += ["## Key benefits", "",
          "Fit: **full** = the initiative is about what the capability does; **partial** = the capability supports the "
          "work but does not deliver the outcome; **no_fit** = neither.", ""]
    for b in (x for x in plan["key_benefits"] if x["fit"] != "no_fit"):
        L.append(f"### {titles.get(b['initiative_id'], '')} ({b['initiative_id']}): {b['fit']} fit")
        L.append(f"Benefit {b['id']} · _{status.get(b['initiative_id'], '')}_{_labels(b)}")
        L.append("")
        _sentences(b, L)
        L += ["", f"Capabilities: {', '.join(b['capability_ids'])}. {b['plan_confirmation']}", "",
              f"_Fit rationale (inference):_ {b['fit_rationale']}", ""]
    no_fit = [x for x in plan["key_benefits"] if x["fit"] == "no_fit"]
    if no_fit:
        L += ["### Initiatives the tool did not map (no_fit)", "", "| Initiative | Reason | Gap IDs |", "|---|---|---|"]
        for b in no_fit:
            L.append(f"| {titles.get(b['initiative_id'], '')} ({b['initiative_id']}) | {b['no_fit_reason']} | "
                     f"{', '.join(b.get('gap_ids') or []) or '—'} |")
        L.append("")
    L += ["## Renewal", "", plan["renewal"]["placeholder"], "", "## Key challenges and potential blockers", ""]
    for c in plan["key_challenges"]["public_signals"]:
        L.append(f"### {c['id']} {c['title']}")
        L.append(f"{c['provenance']}{_labels(c)}")
        L.append("")
        _sentences(c, L)
        L.append("")
    L += [f"**Raised by the customer:** {plan['key_challenges']['customer_raised']['placeholder']}", "",
          "## Objectives, success criteria, and tasks", "",
          "All timelines are proposed and relative. Targets are set with the company after a baseline.", ""]
    for o in plan["objectives"]:
        linked = ", ".join(f"{x} ({status.get(x, '')})" for x in o["linked_initiative_ids"])
        L += [f"### {o['id']} {o['title']}", f"Initiative: {linked} · fit: {o['fit']}{_labels(o)}", "", "**Success criteria**", ""]
        for c in o["success_criteria"]:
            kind = " · ".join(x for x in (c.get("criterion_type"), c.get("indicator_type")) if x)
            L.append(f"- **{c['id']}** {c['text']}" + (f" ({kind})" if kind else "") + _labels(c))
            if c.get("proposed_target"):
                L.append(f"  - Proposed target: {c['proposed_target']}")
            if c.get("needs_baseline"):
                L.append(f"  - Needs baseline. {c['baseline_question']}")
            L.append(f"  - Validate: {c['validation_question']}")
            if c.get("citations"):
                L.append(f"  - Based on: {_links(c['citations'])}")
        L += ["", "**Tasks (proposed)**", "", "| Window | Role | Task | Validate |", "|---|---|---|---|"]
        for t in o["tasks"]:
            L.append(f"| {t['proposed_timeline']['window']} | {t.get('responsible_role', '')} | {t['text']} | {t['validation_question']} |")
        L.append("")
    L += [plan["objectives_csm_blank"]["placeholder"], "", "## Items to validate with the customer", ""]
    for section, questions in validation_groups(plan).items():
        if questions:
            L += [f"### {section}", ""] + [f"- {x['ref_id']}: {x['question']}" for x in questions] + [""]
    L += ["", "---", "", f"_{m['disclaimer']}_", ""]
    return "\n".join(L)
