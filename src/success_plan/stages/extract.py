"""Stage 2: Extract initiatives, challenges, and leadership or structural changes with verified citations.

Two model calls, each given only the chunks its outputs may cite:
  A. initiatives  <- 10-K Item 1, Item 7, earnings releases (never Risk Factors, never news)
  B. challenges + changes <- 10-K Item 1A, change-event 8-Ks, eligible headlines, earnings releases
     (releases only to corroborate or update)

The model proposes items and citations. Code verifies every citation and sets provenance, labels, flags,
dates, and source details. Nothing the guards catch is silently deleted: it is flagged, excluded with a
reason, or moved to overflow, and recorded in the guard report.
"""

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .. import guards
from ..llm import LLM, CallSettings, LLMError, ModelsConfig, TokenCounter, cost_usd, estimate_tokens
from ..models import (
    DroppedSentence, ExtractItem, ExtractOutput, GuardEntry, GuardReport, ItemFlag, LegacyLLMChallengesChangesOutput,
    LegacyLLMInitiativesOutput, LLMChallengesChangesOutput, LLMInitiativesOutput, RejectedCitation, RetrieveManifest,
    SentenceRecord, UsageRecord, VerifiedCitation,
)
from ..paths import CompanyPaths
from .extract_review import write_review_sheet

PROMPTS = Path(__file__).resolve().parents[1] / "prompts"
OUTPUT_NAME = "02_extract.json"
USAGE_LOG_NAME = "usage_log.jsonl"  # one line per real run, including failed ones
RESPONSES_DIR = "llm"  # raw model responses, under the gitignored raw folder
REVIEW_SHEET_NAME = "02_extract_review.html"  # gitignored: shows full source chunks
GUARD_REPORT_NAME = "02_extract_guard_report.json"
CAPS = {"initiative": 12, "challenge": 12, "change": 20}
ID_PREFIX = {"initiative": "I", "challenge": "C", "change": "S"}
NEWS_ONLY_LABEL = "news headline only"
GUIDANCE_REASON = "guidance is not an initiative"
UNCORROBORATED_LABEL = "uncorroborated headline"


class ExtractError(Exception):
    pass


# ---------------------------------------------------------------- inputs

def load_inputs(paths: CompanyPaths) -> tuple[RetrieveManifest, dict[str, dict]]:
    manifest_path = paths.stages / "01_retrieve.json"
    if not manifest_path.exists() or not paths.chunks_file.exists():
        raise ExtractError("Run `spg retrieve` first: 01_retrieve.json or chunks.jsonl is missing.")
    manifest = RetrieveManifest.model_validate_json(manifest_path.read_text())
    chunks = {}
    for line in paths.chunks_file.read_text().splitlines():
        if line.strip():
            record = json.loads(line)
            chunks[record["chunk_id"]] = record
    return manifest, chunks


def is_change_event_8k(source_id: str) -> bool:
    return source_id.startswith("8k-") and not source_id.endswith("-ex991")


def _group(chunk: dict) -> str:
    sid = chunk["source_id"]
    if sid in ("10k-item1", "10k-item7"):
        return "10k-business-mdna"
    if sid == "10k-item1a":
        return "10k-risk"
    if sid.startswith("8k-") and sid.endswith("-ex991"):
        return "earnings"
    if sid.startswith("8k-"):
        return "8k-event"
    if chunk["source_type"] == "news":
        return "news"
    return "other"


def partition(manifest: RetrieveManifest, chunks: dict[str, dict]) -> tuple[list[dict], list[dict]]:
    """Chunks for call A (initiatives) and call B (challenges + changes).

    News chunks must also be marked eligible in the manifest, so a stale chunks file can never pass
    an ineligible or low-signal headline to the model.
    """
    eligible_news = {h.chunk_id for h in manifest.news.headlines if h.eligible_for_extract}
    a, b = [], []
    for c in chunks.values():
        g = _group(c)
        if g == "news" and c["chunk_id"] not in eligible_news:
            continue
        if g in ("10k-business-mdna", "earnings"):
            a.append(c)
        if g in ("10k-risk", "8k-event", "news", "earnings"):
            b.append(c)
    return a, b


def render_chunks(chunks: list[dict]) -> str:
    parts = []
    for c in chunks:
        attrs = f'id="{c["chunk_id"]}" source="{c["title"]}"'
        if c.get("location"):
            attrs += f' location="{c["location"]}"'
        attrs += f' date="{c["date"]}"'
        if c.get("event_date"):
            attrs += f' event_date="{c["event_date"]}"'
        parts.append(f"<chunk {attrs}>\n{c['text']}\n</chunk>")
    return "\n\n".join(parts)


@dataclass
class CallSpec:
    name: str
    system: str
    user: str
    output_model: type
    chunks: list[dict]

    @property
    def allowed_ids(self) -> set[str]:
        return {c["chunk_id"] for c in self.chunks}


def build_calls(manifest: RetrieveManifest, chunks: dict[str, dict]) -> list[CallSpec]:
    a_chunks, b_chunks = partition(manifest, chunks)
    fill = lambda s: s.replace("{company}", manifest.company.name).replace("{fiscal_year}", manifest.filing.fiscal_year)
    common = fill((PROMPTS / "extract_common.md").read_text())
    return [
        CallSpec(
            "initiatives", common + "\n\n" + fill((PROMPTS / "extract_initiatives.md").read_text()),
            render_chunks(a_chunks), LLMInitiativesOutput, a_chunks,
        ),
        CallSpec(
            "challenges_changes", common + "\n\n" + fill((PROMPTS / "extract_challenges_changes.md").read_text()),
            render_chunks(b_chunks), LLMChallengesChangesOutput, b_chunks,
        ),
    ]


# ---------------------------------------------------------------- estimates

@dataclass
class CallEstimate:
    name: str
    model: str
    input_tokens: int
    input_source: str  # "counted" (token-counting endpoint) or "estimated" (calibrated fallback)
    output_tokens: int
    cost_usd: float
    note: str | None = None


def estimate_calls(
    calls: list[CallSpec], settings: CallSettings, config: ModelsConfig, counter: TokenCounter | None = None,
) -> list[CallEstimate]:
    """Input tokens are counted exactly when a counter is available, else estimated from characters."""
    out = []
    for call in calls:
        n_in, source, note = None, "estimated", None
        if counter is not None:
            try:
                n_in = counter.count_input_tokens(
                    system=call.system, user=call.user, output_model=call.output_model, model=settings.model
                )
                source = "counted"
            except LLMError as exc:
                note = f"exact count unavailable ({exc}); used calibrated estimate"
        if n_in is None:
            n_in = estimate_tokens(call.system + call.user, config.chars_per_token)
        out.append(
            CallEstimate(
                call.name, settings.model, n_in, source, settings.expected_output_tokens,
                cost_usd(config.prices, settings.model, input_tokens=n_in, output_tokens=settings.expected_output_tokens),
                note,
            )
        )
    return out


# ---------------------------------------------------------------- post-processing

def _verified(chunk: dict, evidence: str) -> VerifiedCitation:
    return VerifiedCitation(
        chunk_id=chunk["chunk_id"], source_id=chunk["source_id"], evidence=evidence, source_type=chunk["source_type"],
        title=chunk["title"], location=chunk.get("location"), url=chunk["url"], date=chunk["date"],
        event_date=chunk.get("event_date"),
    )


MAX_CITATIONS_PER_SENTENCE = 2


def assemble_text(sentences: list[SentenceRecord]) -> str:
    """Item text is exactly the kept sentences, in the model's order. Dropped sentences cannot appear."""
    return " ".join(s.text.strip() for s in sentences)


def _initiative_excluded(raw, report: GuardReport) -> bool:
    """Financing and guidance exclusions, judged on the title and every proposed sentence."""
    proposed = " ".join([raw.title] + [s.text for s in raw.sentences])
    if raw.theme == "financing_capital_return":
        report.excluded.append(GuardEntry(kind="initiative", title=raw.title, reason="financing or capital return is not an initiative (theme)"))
        return True
    keyword = guards.financing_keyword(proposed)
    if keyword:
        report.excluded.append(GuardEntry(
            kind="initiative", title=raw.title, reason=f"financing or capital return is not an initiative (keyword {keyword!r})",
        ))
        return True
    if guards.guidance_keyword(proposed):
        report.excluded.append(GuardEntry(kind="initiative", title=raw.title, reason=GUIDANCE_REASON))
        return True
    return False


def process_item(raw, kind: str, call: CallSpec, chunks: dict[str, dict], aliases: list[str],
                 report: GuardReport) -> ExtractItem | None:
    """Verify each sentence's citations, keep sentences with at least one verified citation, and build the
    item from them. Returns None if a rule excludes the item or no sentence survives."""
    if kind == "initiative" and _initiative_excluded(raw, report):
        return None

    kept: list[SentenceRecord] = []
    dropped: list[DroppedSentence] = []
    market_signal_only = True
    saw_market_signal = False
    for sentence in raw.sentences:
        verified: list[VerifiedCitation] = []
        rejected: list[RejectedCitation] = []
        for n, cit in enumerate(sentence.citations):
            if n >= MAX_CITATIONS_PER_SENTENCE:
                rejected.append(RejectedCitation(chunk_id=cit.chunk_id, evidence=cit.evidence,
                                                 reason=f"more than {MAX_CITATIONS_PER_SENTENCE} citations in one sentence"))
                continue
            problem, stored, truncated = guards.verify_citation(
                cit.chunk_id, cit.evidence, chunks, call.allowed_ids, aliases, kind=kind
            )
            chunk = chunks.get(cit.chunk_id)
            if problem is None and chunk["source_type"] == "news" and guards.is_market_signal(chunk["text"]):
                problem = "analyst rating or stock move (excluded)"
                saw_market_signal = True
            if problem:
                rejected.append(RejectedCitation(chunk_id=cit.chunk_id, evidence=cit.evidence, reason=problem))
                report.rejected_citations.append(GuardEntry(kind=kind, title=raw.title, reason=f"{cit.chunk_id}: {problem}"))
            else:
                v = _verified(chunk, stored)
                v.evidence_truncated = truncated
                verified.append(v)
        if verified:
            market_signal_only = False
            verified.sort(key=lambda c: c.date, reverse=True)  # newest source first
            kept.append(SentenceRecord(text=sentence.text.strip(), citations=verified, rejected_citations=rejected))
        else:
            reason = "uncited sentence" if not sentence.citations else "no citation verified: " + "; ".join(r.reason for r in rejected)
            dropped.append(DroppedSentence(text=sentence.text.strip(), rejected_citations=rejected, reason=reason))
            report.dropped_sentences.append(GuardEntry(kind=kind, title=raw.title, reason=f'"{sentence.text.strip()}" ({reason})'))

    if not kept:
        reason = ("supported only by analyst ratings or stock moves" if saw_market_signal and market_signal_only
                  else "no sentence has a verified citation")
        report.excluded.append(GuardEntry(kind=kind, title=raw.title, reason=reason))
        return None

    # Union of the kept sentences' citations (deduplicated) drives the item-level rules.
    union: dict[tuple[str, str], VerifiedCitation] = {}
    for sr in kept:
        for c in sr.citations:
            union.setdefault((c.chunk_id, c.evidence), c)
    verified_all = sorted(union.values(), key=lambda c: c.date, reverse=True)

    if kind == "initiative" and all(guards.is_outlook_row(chunks[c.chunk_id]["text"], c.evidence) for c in verified_all):
        report.excluded.append(GuardEntry(kind=kind, title=raw.title, reason=GUIDANCE_REASON))
        return None
    if kind == "challenge" and all(is_change_event_8k(c.source_id) for c in verified_all):
        report.excluded.append(GuardEntry(kind=kind, title=raw.title, reason="covered under changes"))
        return None

    origin = None
    if kind == "challenge":
        sids = {c.source_id for c in verified_all}
        if "10k-item1a" in sids:
            origin = "risk_factor"
        elif any(c.source_type == "news" or is_change_event_8k(c.source_id) for c in verified_all):
            origin = "public_signal"
        else:
            report.excluded.append(GuardEntry(
                kind=kind, title=raw.title,
                reason="challenge not supported by Risk Factors, an 8-K, or a news headline (earnings release alone)",
            ))
            return None

    labels, flags, extra_questions = [], [], []
    news = [c for c in verified_all if c.source_type == "news"]
    filings = [c for c in verified_all if c.source_type != "news"]
    filing_texts = [chunks[c.chunk_id]["text"] for c in filings]
    uncorroborated = [c for c in news if not guards.headline_corroborated(chunks[c.chunk_id]["text"], filing_texts, aliases)]
    risk_backed = kind == "challenge" and origin == "risk_factor"
    if uncorroborated and not risk_backed:
        provenance = "inferred"
        labels.append(NEWS_ONLY_LABEL)
    else:
        provenance = "sourced"
    if uncorroborated and risk_backed:
        labels.append(UNCORROBORATED_LABEL)
        weak_ids = {c.chunk_id for c in uncorroborated}
        for sr in kept:
            for c in sr.citations:
                if c.chunk_id in weak_ids:
                    c.uncorroborated = True
        for c in uncorroborated:
            c.uncorroborated = True
            extra_questions.append(
                f'Confirm the news headline "{chunks[c.chunk_id]["text"]}" ({c.location}, {c.date}); '
                "no filing in the sources corroborates it."
            )

    sources_note = None
    tenk = [c for c in verified_all if c.source_id.startswith("10k-")]
    release = [c for c in verified_all if c.source_id.endswith("-ex991")]
    if tenk and release:
        newest = max(release, key=lambda c: c.date)
        sources_note = (
            f"Newer source preferred: earnings release filed {newest.date}; also covered in the 10-K filed {tenk[0].date}."
        )

    # Per-sentence checks, each against that sentence's own citations only.
    text = assemble_text(kept)
    for n, sr in enumerate(kept, start=1):
        own_texts = [chunks[c.chunk_id]["text"] for c in sr.citations]
        own_filing_texts = [chunks[c.chunk_id]["text"] for c in sr.citations if c.source_type != "news"]
        tag = f"sentence {n}"
        # Statement support also counts each cited chunk's own filing and event dates, written out.
        dates = guards.written_dates([d for c in sr.citations for d in (c.date, c.event_date)])
        support_texts = own_texts + ([dates] if dates else [])
        support = guards.statement_support(sr.text, support_texts)
        scored = [x.score for x in support if x.score is not None]
        sr.support_score = min(scored) if scored else None
        for reason in guards.price_flags(sr.text, own_filing_texts):
            flags.append(ItemFlag(type="price_review", detail=f"{tag}: {reason}"))
        if guards.long_quote(sr.text, own_texts):
            flags.append(ItemFlag(type="long_quote", detail=f"{tag}: copies {guards.LONG_QUOTE_WORDS}+ consecutive words from a source"))
        for word in guards.unsupported_consequence_words(sr.text, own_texts):
            flags.append(ItemFlag(type="unsupported_term", detail=f"{tag}: {word!r} appears in none of its cited chunks"))
        own_ids = {c.chunk_id for c in sr.citations}
        others = {c["chunk_id"]: c["text"] for c in call.chunks if c["chunk_id"] not in own_ids}
        headline_only = all(c.source_type == "news" for c in sr.citations)
        for weak in guards.unsupported_statements(sr.text, support_texts):
            if headline_only:  # a headline paraphrase; searching filings for its words finds unrelated matches
                where = "found-in search skipped (cites only news headlines)"
            else:
                found = guards.find_supporting_chunks(weak.sentence, support_texts, others)
                where = f"found in {', '.join(found)} (mis-cited)" if found else "not found in any allowed chunk (unsupported)"
            flags.append(ItemFlag(
                type="unsupported_statement",
                detail=f'{tag}, support {weak.score:.0%}: "{weak.sentence}" (not in its cited chunks: {", ".join(weak.missing)}); {where}',
            ))
        if kind == "initiative":
            word = guards.result_word(sr.text)
            if word:
                flags.append(ItemFlag(
                    type="results_review",
                    detail=f"{tag}: uses {word!r}; an initiative states what the company is doing or intends to do, not a result",
                ))

    # Item-level checks on the assembled text.
    all_texts = [chunks[c.chunk_id]["text"] for c in verified_all]
    if guards.has_personal_title(text) or guards.has_personal_title(raw.title):
        flags.append(ItemFlag(type="personal_title", detail="uses an honorific; describe roles, not people"))
    names = guards.personal_names_used(f"{raw.title}. {text}", all_texts)
    if names:
        flags.append(ItemFlag(type="personal_name", detail=f"names a person from the source ({', '.join(names)}); describe roles, not people"))
    if kind == "change":
        phrase = guards.biography_phrase(f"{raw.title}. {text}")
        if phrase:
            flags.append(ItemFlag(type="biography", detail=f"career history ({phrase!r}); roles only, no prior employers"))
    title_scores = [x.score for x in guards.statement_support(raw.title, [text]) if x.score is not None]
    title_support_score = min(title_scores) if title_scores else None  # shown in the review sheet, never flagged
    if dropped:
        flags.append(ItemFlag(
            type="partial_support",
            detail=f"{len(dropped)} of {len(dropped) + len(kept)} sentences dropped because no citation verified",
        ))
    for f in flags:
        report.flagged.append(GuardEntry(kind=kind, title=raw.title, reason=f"{f.type}: {f.detail}"))

    event_dates = [c.event_date for c in verified_all if c.event_date and is_change_event_8k(c.source_id)]
    return ExtractItem(
        id="", kind=kind, title=raw.title, text=text, provenance=provenance, labels=labels, flags=flags,
        citations=verified_all, rejected_citations=[r for sr in kept for r in sr.rejected_citations]
        + [r for d in dropped for r in d.rejected_citations],
        model_inference=False, validation_question=raw.validation_question, extra_validation_questions=extra_questions,
        theme=getattr(raw, "theme", None), category=getattr(raw, "category", None), origin=origin,
        change_type=getattr(raw, "change_type", None),
        event_date=min(event_dates) if kind == "change" and event_dates else None,
        sources_note=sources_note, sentences=kept, dropped_sentences=dropped, title_support_score=title_support_score,
    )


def apply_caps(items: list[ExtractItem], kind: str, report: GuardReport) -> list[ExtractItem]:
    cap = CAPS[kind]
    for extra in items[cap:]:
        report.overflow.append(GuardEntry(kind=kind, title=extra.title, reason=f"over the cap of {cap} {kind}s"))
    kept = items[:cap]
    for i, item in enumerate(kept, start=1):
        item.id = f"{ID_PREFIX[kind]}-{i:02d}"
    # Fill in item ids on guard entries recorded before ids existed.
    by_title = {(it.kind, it.title): it.id for it in kept}
    for entry in report.rejected_citations + report.flagged:
        if entry.item_id is None and (entry.kind, entry.title) in by_title:
            entry.item_id = by_title[(entry.kind, entry.title)]
    return kept


# ---------------------------------------------------------------- run

def _save_response(run_dir: Path, call: CallSpec, profile: str, settings: CallSettings, result) -> None:
    """Raw model output for one call, saved in the gitignored raw folder so guards can be replayed."""
    run_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "call": call.name, "profile": profile, "model": result.model, "effort": settings.effort,
        "stop_reason": result.stop_reason,
        "usage": {
            "input_tokens": result.input_tokens, "output_tokens": result.output_tokens,
            "cache_read_input_tokens": result.cache_read_input_tokens,
            "cache_creation_input_tokens": result.cache_creation_input_tokens,
        },
        "output": result.parsed.model_dump(mode="json"),
    }
    (run_dir / f"{call.name}.json").write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")


def assemble(
    paths: CompanyPaths, manifest: RetrieveManifest, chunks: dict[str, dict], calls: list[CallSpec],
    parsed_by_call: dict[str, object], usage: list[UsageRecord], profile: str, config: ModelsConfig,
    now: datetime, replayed_from: str | None = None, legacy: bool = False,
) -> ExtractOutput:
    """Apply the guards to parsed model output and write the stage output, guard report, and review sheet."""
    report = GuardReport(generated_at=now, rejected_citations=[], flagged=[], excluded=[], overflow=[])
    aliases = manifest.news.aliases
    by_name = {c.name: c for c in calls}
    if legacy:
        from .extract_legacy import process_legacy_item as processor
    else:
        processor = process_item

    def collect(call_name: str, field: str, kind: str) -> list[ExtractItem]:
        call, parsed = by_name[call_name], parsed_by_call[call_name]
        items = [processor(raw, kind, call, chunks, aliases, report) for raw in getattr(parsed, field)]
        return apply_caps([i for i in items if i is not None], kind, report)

    initiatives = collect("initiatives", "initiatives", "initiative")
    challenges = collect("challenges_changes", "challenges", "challenge")
    changes = collect("challenges_changes", "changes", "change")

    warnings = []
    for name, count in (("rejected citations", len(report.rejected_citations)),
                        ("dropped sentences", len(report.dropped_sentences)), ("flagged items", len(report.flagged)),
                        ("excluded items", len(report.excluded)), ("overflow items", len(report.overflow))):
        if count:
            warnings.append(f"{count} {name}; see {GUARD_REPORT_NAME}.")

    output = ExtractOutput(
        generated_at=now, company=manifest.company.name, profile=profile, retrieve_generated_at=manifest.generated_at,
        prices_as_of=config.prices_as_of, initiatives=initiatives, challenges=challenges, changes=changes,
        usage=usage, total_cost_usd=round(sum(u.cost_usd for u in usage), 6), warnings=warnings,
        replayed_from=replayed_from, response_format="legacy" if legacy else "sentences",
    )
    if legacy:
        output.warnings.insert(0, "LEGACY response format (item-level citations), replayed read-only; not the current format.")
    paths.stages.mkdir(parents=True, exist_ok=True)
    (paths.stages / OUTPUT_NAME).write_text(output.model_dump_json(indent=2) + "\n")
    (paths.stages / GUARD_REPORT_NAME).write_text(report.model_dump_json(indent=2) + "\n")
    write_review_sheet(paths.raw / REVIEW_SHEET_NAME, output, report, chunks)
    return output


def run_extract(
    paths: CompanyPaths, llm: LLM | None, config: ModelsConfig, profile: str, *, dry_run: bool = False,
    counter: TokenCounter | None = None,
) -> tuple[list[CallEstimate], ExtractOutput | None]:
    manifest, chunks = load_inputs(paths)
    calls = build_calls(manifest, chunks)
    settings = config.settings(profile, "extract")
    estimates = estimate_calls(calls, settings, config, counter)
    total_estimate = sum(e.cost_usd for e in estimates)
    if dry_run:
        return estimates, None
    if total_estimate > config.max_cost_usd_per_run:
        raise ExtractError(
            f"Estimated cost ${total_estimate:.2f} exceeds the limit of ${config.max_cost_usd_per_run:.2f} "
            "(max_cost_usd_per_run in config/models.yaml). No API call was made."
        )
    if llm is None:
        raise ExtractError("No model client provided.")

    now = datetime.now(timezone.utc).replace(microsecond=0)
    run_dir = paths.raw / RESPONSES_DIR / f"extract-{now:%Y%m%dT%H%M%SZ}-{profile}"
    usage, parsed_by_call = [], {}

    def log_run(status: str, error: str | None = None) -> None:
        paths.stages.mkdir(parents=True, exist_ok=True)
        entry = {
            "stage": "extract", "at": now.isoformat(), "profile": profile, "status": status, "error": error,
            "estimated_cost_usd": round(total_estimate, 6), "cost_usd": round(sum(u.cost_usd for u in usage), 6),
            "responses": str(run_dir.relative_to(paths.root)), "calls": [u.model_dump() for u in usage],
        }
        with (paths.stages / USAGE_LOG_NAME).open("a") as fh:
            fh.write(json.dumps(entry) + "\n")

    for call, est in zip(calls, estimates):
        try:
            result = llm.complete(system=call.system, user=call.user, output_model=call.output_model, settings=settings)
        except LLMError as exc:
            log_run("failed", f"{call.name}: {exc}")
            raise ExtractError(f"Model call {call.name!r} failed: {exc}. Nothing was saved except the usage log.") from exc
        _save_response(run_dir, call, profile, settings, result)
        parsed_by_call[call.name] = result.parsed
        usage.append(
            UsageRecord(
                call=call.name, model=result.model, effort=settings.effort, input_tokens=result.input_tokens,
                output_tokens=result.output_tokens, cache_read_input_tokens=result.cache_read_input_tokens,
                cache_creation_input_tokens=result.cache_creation_input_tokens,
                cost_usd=cost_usd(
                    config.prices, settings.model, input_tokens=result.input_tokens, output_tokens=result.output_tokens,
                    cache_read_input_tokens=result.cache_read_input_tokens,
                    cache_creation_input_tokens=result.cache_creation_input_tokens,
                ),
                estimated_input_tokens=est.input_tokens, estimated_cost_usd=est.cost_usd,
            )
        )

    log_run("ok")
    return estimates, assemble(paths, manifest, chunks, calls, parsed_by_call, usage, profile, config, now)


def _is_legacy_output(output: dict) -> bool | None:
    """Legacy responses put free text and citations on each item; current ones put them on sentences.
    None when the response has no items (compatible with either format)."""
    for items in output.values():
        for item in items:
            return "sentences" not in item
    return None


def latest_response_dir(paths: CompanyPaths) -> Path | None:
    root = paths.raw / RESPONSES_DIR
    dirs = sorted(d for d in root.glob("extract-*") if d.is_dir()) if root.exists() else []
    return dirs[-1] if dirs else None


def replay_extract(paths: CompanyPaths, config: ModelsConfig, response_dir: Path | None = None) -> ExtractOutput:
    """Re-apply the current guards to saved model responses. No API call of any kind."""
    response_dir = response_dir or latest_response_dir(paths)
    if response_dir is not None:
        response_dir = response_dir.resolve()
    if response_dir is None or not response_dir.exists():
        raise ExtractError(f"No saved model responses found under {paths.raw / RESPONSES_DIR}.")
    manifest, chunks = load_inputs(paths)
    calls = build_calls(manifest, chunks)
    parsed_by_call, usage, profile, rebuilt_notes = {}, [], None, []
    legacy_models = {"initiatives": LegacyLLMInitiativesOutput, "challenges_changes": LegacyLLMChallengesChangesOutput}
    legacy = None
    raw_outputs: dict[str, dict] = {}
    for call in calls:
        path = response_dir / f"{call.name}.json"
        if not path.exists():
            raise ExtractError(f"Saved response for call {call.name!r} is missing: {path}")
        record = json.loads(path.read_text())
        if record.get("rebuilt"):
            rebuilt_notes.append(record.get("note", "rebuilt response"))
        is_legacy = _is_legacy_output(record["output"])
        if is_legacy is not None:
            if legacy is not None and is_legacy != legacy:
                raise ExtractError(f"Saved responses in {response_dir} mix legacy and sentence formats.")
            legacy = is_legacy
        raw_outputs[call.name] = record
    for call in calls:
        record = raw_outputs[call.name]
        model = legacy_models[call.name] if legacy else call.output_model
        parsed_by_call[call.name] = model.model_validate(record["output"])
        profile = record.get("profile", profile)
        u = record.get("usage", {})
        usage.append(
            UsageRecord(
                call=call.name, model=record.get("model", "unknown"), effort=record.get("effort", "unknown"),
                input_tokens=u.get("input_tokens", 0), output_tokens=u.get("output_tokens", 0),
                cache_read_input_tokens=u.get("cache_read_input_tokens", 0),
                cache_creation_input_tokens=u.get("cache_creation_input_tokens", 0),
                cost_usd=0.0, estimated_input_tokens=0, estimated_cost_usd=0.0,  # replay costs nothing
            )
        )
    now = datetime.now(timezone.utc).replace(microsecond=0)
    source = str(response_dir.relative_to(paths.root.resolve()))
    if rebuilt_notes:
        source += " (REBUILT, not a saved model response)"
    if legacy:
        source += " (LEGACY format)"
    output = assemble(paths, manifest, chunks, calls, parsed_by_call, usage, profile or "unknown", config, now,
                      replayed_from=source, legacy=bool(legacy))
    if rebuilt_notes:
        output.warnings.insert(0, f"Replayed a REBUILT response, not a saved one: {rebuilt_notes[0]}")
        (paths.stages / OUTPUT_NAME).write_text(output.model_dump_json(indent=2) + "\n")
    return output
