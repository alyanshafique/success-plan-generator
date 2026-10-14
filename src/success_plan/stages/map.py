"""Stage 3: Map each Extract initiative to config capabilities (full / partial) or no_fit.

One model call. The model sees the initiatives' kept sentences and the capabilities as numbered claims: the
config's own wording, split into clauses. It never sees plan availability, plan-tier limitation lines or
clauses, prices, or the config's `addresses` hints. Each benefit sentence cites 1-2 claims with an evidence
phrase that code verifies against the claim text, so a benefit cannot add features the config does not list.

Verification chain: benefit sentence -> config claim (verified here, against the config's wording) ->
Atlassian page (the capability's source_urls, checked when the config was verified; see verified_on).
"""

import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, create_model

from .. import atomic, guards
from ..llm import LLM, CallSettings, LLMError, ModelsConfig, TokenCounter, cost_usd, estimate_tokens
from ..models import (
    PLAN_CONFIRMATION_BLANK, BenefitSentence, DroppedBenefitSentence, ExtractOutput, GuardEntry, ItemFlag, KeyBenefit,
    LLMMapOutput, MapGuardReport, MapOutput, ProductCitation, RejectedCitation, UsageRecord, WithheldClaim,
)
from ..paths import PROJECT_ROOT, CompanyPaths

PROMPTS = Path(__file__).resolve().parents[1] / "prompts"
CONFIG_FILE = PROJECT_ROOT / "config" / "product_atlassian_jira_confluence.yaml"
EXTRACT_NAME = "02_extract.json"
OUTPUT_NAME = "03_map.json"
GUARD_REPORT_NAME = "03_map_guard_report.json"
REVIEW_SHEET_NAME = "03_map_review.html"
USAGE_LOG_NAME = "usage_log.jsonl"
RESPONSES_DIR = "llm"
MAX_CLAIMS_PER_SENTENCE = 2
STALE_AFTER_DAYS = 90
UNCONFIRMED_LABEL = "unconfirmed product detail"


class MapError(Exception):
    pass


# ---------------------------------------------------------------- inputs

def load_extract(paths: CompanyPaths) -> ExtractOutput:
    path = paths.stages / EXTRACT_NAME
    if not path.exists():
        raise MapError("Run `spg extract` first: 02_extract.json is missing.")
    return ExtractOutput.model_validate_json(path.read_text())


def company_aliases(paths: CompanyPaths, extract: ExtractOutput) -> list[str]:
    """The company name plus the names, brands, and ticker Retrieve used (01_retrieve.json), if present."""
    aliases = [extract.company]
    manifest = paths.stages / "01_retrieve.json"
    if manifest.exists():
        aliases += (json.loads(manifest.read_text()).get("news") or {}).get("aliases") or []
    return aliases


def rationale_vocabulary(paths: CompanyPaths, extract: ExtractOutput, config: dict, claims: dict[str, dict]) -> list[str]:
    """Words a fit rationale may capitalize without adding a fact: config wording the model saw (product and
    capability names, claims, gaps; never plan availability) and the company's aliases."""
    config_text = [f'{c.get("product", "")} {c.get("name", "")}' for c in config["capabilities"]]
    config_text += [c["text"] for c in claims.values()] + [str(g.get("text", "")) for g in config.get("not_covered", [])]
    return config_text + company_aliases(paths, extract)


def _clauses(description: str) -> list[str]:
    """Sentences, then clauses at ':' and ';', so a plan qualifier can be withheld without its list."""
    text = re.sub(r"\s+", " ", description.strip())
    out = []
    for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z])", text):
        out += [c.strip() for c in re.split(r"(?<=[:;])\s+", sentence) if c.strip()]
    return out


def build_claims(config: dict) -> tuple[dict[str, dict], list[WithheldClaim], dict[str, dict]]:
    """Citable claims keyed by claim id, the withheld lines and clauses, and capabilities by id."""
    claims, withheld, caps = {}, [], {}
    for cap in config["capabilities"]:
        cid = cap["id"]
        caps[cid] = cap
        groups = [("d", _clauses(cap.get("description", "")), False),
                  ("l", list(cap.get("limitations") or []), False),
                  ("u", list(cap.get("unconfirmed") or []), True),
                  ("i", list(cap.get("inferred") or []), True)]
        for prefix, lines, unconfirmed in groups:
            for n, line in enumerate(lines, start=1):
                text = str(line).strip()
                phrase = guards.plan_tier_phrase(text)
                if phrase:
                    withheld.append(WithheldClaim(capability_id=cid, text=text, reason=f"plan-tier wording ({phrase!r})"))
                    continue
                claim_id = f"{cid}#{prefix}{n}"
                claims[claim_id] = {
                    "chunk_id": claim_id, "source_id": cid, "source_type": "product_docs", "text": text,
                    "unconfirmed": unconfirmed, "kind": {"d": "description", "l": "limitation"}.get(prefix, "unconfirmed"),
                }
    return claims, withheld, caps


def render_user(extract: ExtractOutput, claims: dict[str, dict], caps: dict[str, dict], gaps: list[dict]) -> str:
    parts = ["<initiatives>"]
    for item in extract.initiatives:
        labels = f' labels="{", ".join(item.labels)}"' if item.labels else ""
        parts.append(f'<initiative id="{item.id}" title="{item.title}"{labels}>')
        parts += [f"- {s.text}" for s in item.sentences] or [f"- {item.text}"]
        parts.append("</initiative>")
    parts += ["</initiatives>", "", "<capabilities>"]
    for cid, cap in caps.items():
        own = [c for c in claims.values() if c["source_id"] == cid]
        if not own:
            continue
        parts.append(f'<capability id="{cid}" product="{cap.get("product", "")}" name="{cap.get("name", "")}">')
        for c in own:
            marker = " (unconfirmed)" if c["unconfirmed"] else (" (limitation)" if c["kind"] == "limitation" else "")
            parts.append(f'<claim id="{c["chunk_id"]}"{marker}>{c["text"]}</claim>')
        parts.append("</capability>")
    parts += ["</capabilities>", "", "<not_covered>"]
    parts += [f'<gap id="{g["id"]}">{g["text"]}</gap>' for g in gaps]
    parts.append("</not_covered>")
    return "\n".join(parts)


def output_model(claim_ids: list[str], gap_ids: list[str], initiative_ids: list[str]) -> type[BaseModel]:
    """Structured-output model whose enums allow only real initiative, claim, and gap ids."""
    Claim = create_model("ClaimCitation", claim_id=(Literal[tuple(claim_ids)], ...), evidence=(str, ...))
    Sentence = create_model("BenefitSentenceOut", text=(str, ...), claims=(list[Claim], ...))
    Mapping = create_model(
        "MappingOut",
        initiative_id=(Literal[tuple(initiative_ids)], ...),
        fit=(Literal["full", "partial", "no_fit"], ...),
        sentences=(list[Sentence], ...),
        gap_ids=(list[Literal[tuple(gap_ids)]], ...),
        no_fit_reason=(str, ...),
        fit_rationale=(str, ...),
        validation_question=(str, ...),
    )
    return create_model("MapOutputOut", mappings=(list[Mapping], ...))


def system_prompt(company: str) -> str:
    path = PROMPTS / "map.md"
    if not path.exists():
        raise MapError("prompts/map.md is missing.")
    return path.read_text().replace("{company}", company)


# ---------------------------------------------------------------- processing

def _citation(claim: dict, cap: dict, evidence: str, truncated: bool) -> ProductCitation:
    vo = cap.get("verified_on")
    return ProductCitation(
        claim_id=claim["chunk_id"], capability_id=claim["source_id"], capability_name=cap.get("name", ""),
        evidence=evidence, source_urls=list(cap.get("source_urls") or []),
        verified_on=date.fromisoformat(str(vo)) if vo else None, verified_by=cap.get("verified_by"),
        unconfirmed=claim["unconfirmed"], evidence_truncated=truncated,
    )


def process_mapping(raw, n: int, initiative, claims: dict[str, dict], caps: dict[str, dict], gap_ids: set[str],
                    report: MapGuardReport, vocabulary: list[str] = ()) -> KeyBenefit | None:
    title = initiative.title
    kept: list[BenefitSentence] = []
    dropped: list[DroppedBenefitSentence] = []
    flags: list[ItemFlag] = []

    for sentence in raw.sentences:
        text = sentence.text.strip()
        verified, rejected = [], []
        for k, cit in enumerate(sentence.claims):
            if k >= MAX_CLAIMS_PER_SENTENCE:
                rejected.append(RejectedCitation(chunk_id=cit.claim_id, evidence=cit.evidence,
                                                 reason=f"more than {MAX_CLAIMS_PER_SENTENCE} claims in one sentence"))
                continue
            problem, stored, truncated = guards.verify_citation(cit.claim_id, cit.evidence, claims, set(claims), [])
            if problem:
                rejected.append(RejectedCitation(chunk_id=cit.claim_id, evidence=cit.evidence, reason=problem))
                report.rejected_claims.append(GuardEntry(item_id=initiative.id, kind="mapping", title=title, reason=f"{cit.claim_id}: {problem}"))
            else:
                claim = claims[cit.claim_id]
                verified.append(_citation(claim, caps[claim["source_id"]], stored, truncated))
        reason = None
        if raw.fit == "no_fit":
            reason = "a no_fit mapping has no benefit sentences"
        elif guards.plan_tier_phrase(text):
            reason = f"states plan-level availability ({guards.plan_tier_phrase(text)!r})"
        elif guards.find_currency_amounts(text):
            reason = "states a price or currency amount"
        elif not verified:
            reason = ("uncited sentence" if not sentence.claims
                      else "no claim verified: " + "; ".join(r.reason for r in rejected))
        if reason:
            dropped.append(DroppedBenefitSentence(text=text, rejected_claims=rejected, reason=reason))
            report.dropped_sentences.append(GuardEntry(item_id=initiative.id, kind="mapping", title=title, reason=f'"{text}" ({reason})'))
        else:
            kept.append(BenefitSentence(text=text, citations=verified, rejected_claims=rejected))

    fit, no_fit_reason, gaps = raw.fit, (raw.no_fit_reason or "").strip(), [g for g in raw.gap_ids]
    bad_gaps = [g for g in gaps if g not in gap_ids]
    gaps = [g for g in gaps if g in gap_ids]
    if bad_gaps:
        report.rejected_claims.append(GuardEntry(item_id=initiative.id, kind="mapping", title=title,
                                                 reason=f"unknown gap id(s) removed: {', '.join(bad_gaps)}"))
    if fit in ("full", "partial") and not kept:
        report.downgraded.append(GuardEntry(item_id=initiative.id, kind="mapping", title=title,
                                            reason=f"{fit} with no verified capability claim; recorded as no_fit"))
        fit, no_fit_reason = "no_fit", "no verified capability claim"
    if fit == "no_fit" and not gaps and not no_fit_reason:
        report.excluded.append(GuardEntry(item_id=initiative.id, kind="mapping", title=title,
                                          reason="no_fit without a GAP-* reason or an explanation"))
        return None

    # Per-sentence checks against each sentence's own claims.
    for k, sr in enumerate(kept, start=1):
        own = [claims[c.claim_id]["text"] for c in sr.citations]
        support = guards.statement_support(sr.text, own)
        scored = [x.score for x in support if x.score is not None]
        sr.support_score = min(scored) if scored else None
        own_ids = {c.claim_id for c in sr.citations}
        others = {cid: c["text"] for cid, c in claims.items() if cid not in own_ids}
        for weak in guards.unsupported_statements(sr.text, own):
            found = guards.find_supporting_chunks(weak.sentence, own, others)
            where = f"found in {', '.join(found)} (mis-cited)" if found else "not found in any claim (adds a feature the config does not list)"
            flags.append(ItemFlag(type="unsupported_statement",
                                  detail=f'sentence {k}, support {weak.score:.0%}: "{weak.sentence}" (not in its cited claims: {", ".join(weak.missing)}); {where}'))
        promise = guards.outcome_promise(sr.text)
        if promise:
            flags.append(ItemFlag(type="outcome_promise", detail=f"sentence {k}: {promise!r}; say what the capability does, not a result it will deliver"))
    text = " ".join(s.text for s in kept)
    rationale = guards.strip_inference_prefix(raw.fit_rationale)
    for field, value in (("text", text), ("fit_rationale", rationale), ("validation_question", raw.validation_question)):
        phrase = guards.implies_customer(value)
        if phrase:
            flags.append(ItemFlag(type="implies_customer", detail=f"{field}: {phrase!r}; the plan is hypothetical, not about an existing customer"))
        tier = guards.plan_tier_phrase(value) if field != "text" else None
        if tier or (field != "text" and guards.find_currency_amounts(value)):
            flags.append(ItemFlag(type="price_review", detail=f"{field}: mentions a plan tier or price"))

    # The fit rationale is a labeled inference. Flagged only if it adds a figure, a proper noun, or a plan or
    # price term its sources lack; the word-overlap score is kept for the review sheet only.
    rationale_sources = [s.text for s in initiative.sentences] + [claims[c.claim_id]["text"] for s in kept for c in s.citations]
    rationale_support = guards.statement_support(rationale, rationale_sources)
    scored = [x.score for x in rationale_support if x.score is not None]
    added = guards.rationale_additions(rationale, rationale_sources, vocabulary)
    if added:
        flags.append(ItemFlag(type="rationale_adds_facts",
                              detail=f'adds {", ".join(added)} (not in the initiative or cited claims): "{rationale}"'))
    if dropped:
        flags.append(ItemFlag(type="partial_support", detail=f"{len(dropped)} of {len(dropped) + len(kept)} sentences dropped"))
    for f in flags:
        report.flagged.append(GuardEntry(item_id=initiative.id, kind="mapping", title=title, reason=f"{f.type}: {f.detail}"))

    labels = list(initiative.labels)
    if any(c.unconfirmed for s in kept for c in s.citations):
        labels.append(UNCONFIRMED_LABEL)
    capability_ids = list(dict.fromkeys(c.capability_id for s in kept for c in s.citations))
    return KeyBenefit(
        id=f"B-{n:02d}", initiative_id=initiative.id, initiative_title=title, initiative_provenance=initiative.provenance,
        fit=fit, capability_ids=capability_ids if fit != "no_fit" else [], text=text, sentences=kept, dropped_sentences=dropped,
        no_fit_reason=(no_fit_reason or None) if fit == "no_fit" else None, gap_ids=gaps if fit == "no_fit" else [],
        fit_rationale=rationale, fit_rationale_support=min(scored) if scored else None,
        labels=labels, flags=flags, validation_question=raw.validation_question, plan_confirmation=PLAN_CONFIRMATION_BLANK,
    )


def assemble(paths: CompanyPaths, extract: ExtractOutput, config: dict, parsed: LLMMapOutput, usage: list[UsageRecord],
             profile: str, models_config: ModelsConfig, now: datetime, *, today: date | None = None,
             replayed_from: str | None = None, config_path: Path | None = None) -> MapOutput:
    config_path = config_path or CONFIG_FILE
    claims, withheld, caps = build_claims(config)
    gap_ids = {g["id"] for g in config.get("not_covered", [])}
    vocabulary = rationale_vocabulary(paths, extract, config, claims)
    report = MapGuardReport(generated_at=now, rejected_claims=[], dropped_sentences=[], flagged=[], excluded=[],
                            downgraded=[], unmapped_initiatives=[])
    by_id = {i.id: i for i in extract.initiatives}
    seen, benefits = set(), []
    for raw in parsed.mappings:
        initiative = by_id.get(raw.initiative_id)
        if initiative is None:
            report.excluded.append(GuardEntry(item_id=raw.initiative_id, kind="mapping", title=raw.initiative_id, reason="unknown initiative id"))
            continue
        if raw.initiative_id in seen:
            report.excluded.append(GuardEntry(item_id=raw.initiative_id, kind="mapping", title=initiative.title, reason="duplicate mapping for this initiative"))
            continue
        seen.add(raw.initiative_id)
        benefit = process_mapping(raw, len(benefits) + 1, initiative, claims, caps, gap_ids, report, vocabulary)
        if benefit:
            benefits.append(benefit)
    report.unmapped_initiatives = [i.id for i in extract.initiatives if i.id not in seen]

    warnings = []
    today = today or date.today()
    stale = sorted({c.capability_id for b in benefits for s in b.sentences for c in s.citations
                    if c.verified_on and (today - c.verified_on).days > STALE_AFTER_DAYS})
    if stale:
        warnings.append(f"Capability verification older than {STALE_AFTER_DAYS} days for: {', '.join(stale)}. Re-verify the config.")
    for name, count in (("rejected claim citations", len(report.rejected_claims)), ("dropped sentences", len(report.dropped_sentences)),
                        ("flags", len(report.flagged)), ("excluded mappings", len(report.excluded)),
                        ("downgraded to no_fit", len(report.downgraded)), ("unmapped initiatives", len(report.unmapped_initiatives))):
        if count:
            warnings.append(f"{count} {name}; see {GUARD_REPORT_NAME}.")
    distribution = {f: sum(1 for b in benefits if b.fit == f) for f in ("full", "partial", "no_fit")}
    output = MapOutput(
        generated_at=now, company=extract.company, profile=profile, extract_generated_at=extract.generated_at,
        config_file=config_label(config_path), withheld_claims=withheld, key_benefits=benefits,
        fit_distribution=distribution, usage=usage, total_cost_usd=round(sum(u.cost_usd for u in usage), 6),
        prices_as_of=models_config.prices_as_of, warnings=warnings, replayed_from=replayed_from,
    )
    paths.stages.mkdir(parents=True, exist_ok=True)
    atomic.write_text(paths.stages / OUTPUT_NAME, output.model_dump_json(indent=2) + "\n")
    atomic.write_text(paths.stages / GUARD_REPORT_NAME, report.model_dump_json(indent=2) + "\n")
    from .map_review import write_map_review_sheet

    write_map_review_sheet(paths.raw / REVIEW_SHEET_NAME, output, report, extract, claims)
    return output


# ---------------------------------------------------------------- run and replay

def config_label(path: Path) -> str:
    """The product config path as recorded in 03_map.json (relative to the repository when possible)."""
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def load_config(path: Path = CONFIG_FILE) -> dict:
    return yaml.safe_load(path.read_text())


def run_map(paths: CompanyPaths, llm: LLM | None, models_config: ModelsConfig, profile: str, *, dry_run: bool = False,
            counter: TokenCounter | None = None, config: dict | None = None, system: str | None = None,
            today: date | None = None, config_path: Path | None = None):
    extract = load_extract(paths)
    config_path = config_path or CONFIG_FILE
    config = config or load_config(config_path)
    claims, withheld, caps = build_claims(config)
    gaps = config.get("not_covered", [])
    system = system or system_prompt(extract.company)
    user = render_user(extract, claims, caps, gaps)
    model_cls = output_model(sorted(claims), sorted(g["id"] for g in gaps), [i.id for i in extract.initiatives])
    settings = models_config.settings(profile, "map")

    source, note, n_in = "estimated", None, None
    if counter is not None:
        try:
            n_in = counter.count_input_tokens(system=system, user=user, output_model=model_cls, model=settings.model)
            source = "counted"
        except LLMError as exc:
            note = f"exact count unavailable ({exc}); used calibrated estimate"
    if n_in is None:
        n_in = estimate_tokens(system + user, models_config.chars_per_token)
    estimate = {"model": settings.model, "input_tokens": n_in, "input_source": source, "note": note,
                "output_tokens": settings.expected_output_tokens,
                "cost_usd": cost_usd(models_config.prices, settings.model, input_tokens=n_in, output_tokens=settings.expected_output_tokens)}
    if dry_run:
        return estimate, None
    if estimate["cost_usd"] > models_config.max_cost_usd_per_run:
        raise MapError(f"Estimated cost ${estimate['cost_usd']:.2f} exceeds the limit of ${models_config.max_cost_usd_per_run:.2f} "
                       "(max_cost_usd_per_run in config/models.yaml). No API call was made.")
    if llm is None:
        raise MapError("No model client provided.")

    now = datetime.now(timezone.utc).replace(microsecond=0)
    run_dir = paths.raw / RESPONSES_DIR / f"map-{now:%Y%m%dT%H%M%SZ}-{profile}"

    def log_run(status, usage, error=None):
        paths.stages.mkdir(parents=True, exist_ok=True)
        with (paths.stages / USAGE_LOG_NAME).open("a") as fh:
            fh.write(json.dumps({"stage": "map", "at": now.isoformat(), "profile": profile, "status": status, "error": error,
                                 "estimated_cost_usd": round(estimate["cost_usd"], 6),
                                 "cost_usd": round(sum(u.cost_usd for u in usage), 6),
                                 "responses": str(run_dir.relative_to(paths.root)), "calls": [u.model_dump() for u in usage]}) + "\n")

    try:
        result = llm.complete(system=system, user=user, output_model=model_cls, settings=settings)
    except LLMError as exc:
        log_run("failed", [], f"map: {exc}")
        raise MapError(f"Model call failed: {exc}. Nothing was saved except the usage log.") from exc
    parsed = LLMMapOutput.model_validate(result.parsed.model_dump(mode="json"))
    run_dir.mkdir(parents=True, exist_ok=True)
    atomic.write_text(run_dir / "map.json", json.dumps({
        "call": "map", "profile": profile, "model": result.model, "effort": settings.effort, "stop_reason": result.stop_reason,
        "usage": {"input_tokens": result.input_tokens, "output_tokens": result.output_tokens,
                  "cache_read_input_tokens": result.cache_read_input_tokens,
                  "cache_creation_input_tokens": result.cache_creation_input_tokens},
        "output": parsed.model_dump(mode="json"),
    }, indent=2, ensure_ascii=False) + "\n")
    usage = [UsageRecord(
        call="map", model=result.model, effort=settings.effort, input_tokens=result.input_tokens, output_tokens=result.output_tokens,
        cache_read_input_tokens=result.cache_read_input_tokens, cache_creation_input_tokens=result.cache_creation_input_tokens,
        cost_usd=cost_usd(models_config.prices, settings.model, input_tokens=result.input_tokens, output_tokens=result.output_tokens,
                          cache_read_input_tokens=result.cache_read_input_tokens, cache_creation_input_tokens=result.cache_creation_input_tokens),
        estimated_input_tokens=estimate["input_tokens"], estimated_cost_usd=estimate["cost_usd"],
    )]
    log_run("ok", usage)
    return estimate, assemble(paths, extract, config, parsed, usage, profile, models_config, now, today=today,
                              config_path=config_path)


def latest_response_dir(paths: CompanyPaths) -> Path | None:
    root = paths.raw / RESPONSES_DIR
    dirs = sorted(d for d in root.glob("map-*") if d.is_dir()) if root.exists() else []
    return dirs[-1] if dirs else None


def replay_map(paths: CompanyPaths, models_config: ModelsConfig, response_dir: Path | None = None, *,
               config: dict | None = None, today: date | None = None, config_path: Path | None = None) -> MapOutput:
    """Re-apply the current guards to a saved Map response. No API call of any kind."""
    response_dir = (response_dir or latest_response_dir(paths))
    if response_dir is None or not response_dir.resolve().exists():
        raise MapError(f"No saved Map responses found under {paths.raw / RESPONSES_DIR}.")
    response_dir = response_dir.resolve()
    record = json.loads((response_dir / "map.json").read_text())
    parsed = LLMMapOutput.model_validate(record["output"])
    u = record.get("usage", {})
    usage = [UsageRecord(call="map", model=record.get("model", "unknown"), effort=record.get("effort", "unknown"),
                         input_tokens=u.get("input_tokens", 0), output_tokens=u.get("output_tokens", 0),
                         cost_usd=0.0, estimated_input_tokens=0, estimated_cost_usd=0.0)]
    now = datetime.now(timezone.utc).replace(microsecond=0)
    config_path = config_path or CONFIG_FILE
    return assemble(paths, load_extract(paths), config or load_config(config_path), parsed, usage,
                    record.get("profile", "unknown"), models_config, now, today=today,
                    replayed_from=str(response_dir.relative_to(paths.root.resolve())), config_path=config_path)
