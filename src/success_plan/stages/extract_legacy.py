"""Legacy item-level Extract processing (one free-text block per item, item-level citations).

READ-ONLY. Kept only to replay the saved dev-2 and dev-3 responses until the sentence-level format has a
baseline run; everything produced here is marked legacy. Remove after that run (record the removal in
docs/evaluation_log.md). The rules are the same as at commit 7c30527.
"""

from .. import guards
from ..models import ExtractItem, GuardEntry, GuardReport, ItemFlag, RejectedCitation, VerifiedCitation
from .extract import (
    GUIDANCE_REASON, NEWS_ONLY_LABEL, UNCORROBORATED_LABEL, CallSpec, _verified, is_change_event_8k,
)


def process_legacy_item(raw, kind: str, call: CallSpec, chunks: dict[str, dict], aliases: list[str],
                 report: GuardReport) -> ExtractItem | None:
    """Verify citations and set provenance, labels, and flags. Returns None if a rule excludes the item."""
    if kind == "initiative":
        if raw.theme == "financing_capital_return":
            report.excluded.append(GuardEntry(kind=kind, title=raw.title, reason="financing or capital return is not an initiative (theme)"))
            return None
        keyword = guards.financing_keyword(f"{raw.title} {raw.text}")
        if keyword:
            report.excluded.append(GuardEntry(
                kind=kind, title=raw.title, reason=f"financing or capital return is not an initiative (keyword {keyword!r})",
            ))
            return None
        if guards.guidance_keyword(f"{raw.title} {raw.text}"):
            report.excluded.append(GuardEntry(kind=kind, title=raw.title, reason=GUIDANCE_REASON))
            return None

    verified: list[VerifiedCitation] = []
    rejected: list[RejectedCitation] = []
    market_signal_dropped = False
    for cit in raw.citations:
        problem, stored, truncated = guards.verify_citation(
            cit.chunk_id, cit.evidence, chunks, call.allowed_ids, aliases, kind=kind
        )
        chunk = chunks.get(cit.chunk_id)
        if problem is None and chunk["source_type"] == "news" and guards.is_market_signal(chunk["text"]):
            problem = "analyst rating or stock move (excluded)"
            market_signal_dropped = True
        if problem:
            rejected.append(RejectedCitation(chunk_id=cit.chunk_id, evidence=cit.evidence, reason=problem))
            report.rejected_citations.append(GuardEntry(kind=kind, title=raw.title, reason=f"{cit.chunk_id}: {problem}"))
        else:
            v = _verified(chunk, stored)
            v.evidence_truncated = truncated
            verified.append(v)

    if market_signal_dropped and not verified:
        report.excluded.append(GuardEntry(kind=kind, title=raw.title, reason="supported only by analyst ratings or stock moves"))
        return None

    if kind == "initiative" and verified and all(
        guards.is_outlook_row(chunks[c.chunk_id]["text"], c.evidence) for c in verified
    ):
        report.excluded.append(GuardEntry(kind=kind, title=raw.title, reason=GUIDANCE_REASON))
        return None

    origin = None
    if kind == "challenge" and verified and all(is_change_event_8k(c.source_id) for c in verified):
        report.excluded.append(GuardEntry(kind=kind, title=raw.title, reason="covered under changes"))
        return None
    if kind == "challenge" and verified:
        sids = {c.source_id for c in verified}
        if "10k-item1a" in sids:
            origin = "risk_factor"
        elif any(c.source_type == "news" or is_change_event_8k(c.source_id) for c in verified):
            origin = "public_signal"
        else:
            report.excluded.append(GuardEntry(
                kind=kind, title=raw.title,
                reason="challenge not supported by Risk Factors, an 8-K, or a news headline (earnings release alone)",
            ))
            return None

    verified.sort(key=lambda c: c.date, reverse=True)  # newest source first
    labels, flags = [], []
    news = [c for c in verified if c.source_type == "news"]
    filings = [c for c in verified if c.source_type != "news"]
    filing_texts = [chunks[c.chunk_id]["text"] for c in filings]
    # A cited headline counts as corroborated only if a verified filing chunk shares a distinctive term
    # with it (term overlap, not meaning; see guards.headline_corroborated).
    uncorroborated = [c for c in news if not guards.headline_corroborated(chunks[c.chunk_id]["text"], filing_texts, aliases)]
    # A challenge backed by a verified Risk Factors citation stays sourced; an uncorroborated headline on it
    # is labeled and gets its own validation question instead of downgrading the item.
    risk_backed = kind == "challenge" and any(c.source_id == "10k-item1a" for c in verified)
    extra_questions = []
    if not verified:
        provenance = "inferred"
        flags.append(ItemFlag(type="evidence_not_found", detail="no citation could be verified against its chunk"))
    elif uncorroborated and not risk_backed:
        provenance = "inferred"
        labels.append(NEWS_ONLY_LABEL)
    else:
        provenance = "inferred" if raw.model_inference else "sourced"
    if uncorroborated and risk_backed:
        labels.append(UNCORROBORATED_LABEL)
        for c in uncorroborated:
            c.uncorroborated = True
            extra_questions.append(
                f'Confirm the news headline "{chunks[c.chunk_id]["text"]}" ({c.location}, {c.date}); '
                "no filing in the sources corroborates it."
            )
    if raw.model_inference:
        labels.append("model inference")

    sources_note = None
    tenk = [c for c in verified if c.source_id.startswith("10k-")]
    release = [c for c in verified if c.source_id.endswith("-ex991")]
    if tenk and release:
        newest = max(release, key=lambda c: c.date)
        sources_note = (
            f"Newer source preferred: earnings release filed {newest.date}; also covered in the 10-K filed {tenk[0].date}."
        )

    text = f"{raw.title}. {raw.text}"
    source_texts = [chunks[c.chunk_id]["text"] for c in verified]
    for reason in guards.price_flags(text, filing_texts):
        flags.append(ItemFlag(type="price_review", detail=reason))
    if guards.long_quote(text, source_texts):
        flags.append(ItemFlag(type="long_quote", detail=f"copies {guards.LONG_QUOTE_WORDS}+ consecutive words from a source"))
    if guards.has_personal_title(text):
        flags.append(ItemFlag(type="personal_title", detail="uses an honorific; describe roles, not people"))
    names = guards.personal_names_used(text, source_texts)
    if names:
        flags.append(ItemFlag(type="personal_name", detail=f"names a person from the source ({', '.join(names)}); describe roles, not people"))
    if kind == "change":
        phrase = guards.biography_phrase(text)
        if phrase:
            flags.append(ItemFlag(type="biography", detail=f"career history ({phrase!r}); roles only, no prior employers"))
    cited_ids = {c.chunk_id for c in verified}
    uncited = {c["chunk_id"]: c["text"] for c in call.chunks if c["chunk_id"] not in cited_ids}
    for weak in guards.unsupported_statements(raw.text, source_texts):
        found = guards.find_supporting_chunks(weak.sentence, source_texts, uncited)
        where = f"found in {', '.join(found)} (mis-cited)" if found else "not found in any allowed chunk (unsupported)"
        flags.append(ItemFlag(
            type="unsupported_statement",
            detail=f'support {weak.score:.0%}: "{weak.sentence}" (not in cited chunks: {", ".join(weak.missing)}); {where}',
        ))
    for word in guards.unsupported_consequence_words(text, source_texts):
        flags.append(ItemFlag(type="unsupported_term", detail=f"{word!r} appears in none of the cited chunks"))
    if rejected:
        flags.append(ItemFlag(
            type="partial_support",
            detail=f"{len(rejected)} of {len(rejected) + len(verified)} citations rejected; some statements may rest only on rejected citations",
        ))
    for f in flags:
        report.flagged.append(GuardEntry(kind=kind, title=raw.title, reason=f"{f.type}: {f.detail}"))

    # A change's event date comes only from change-event 8-Ks. An earnings release's 8-K report date is
    # the release date, not the date of the layoffs or acquisition it mentions.
    event_dates = [c.event_date for c in verified if c.event_date and is_change_event_8k(c.source_id)]
    return ExtractItem(
        id="", kind=kind, title=raw.title, text=raw.text, provenance=provenance, labels=labels, flags=flags,
        citations=verified, rejected_citations=rejected, model_inference=raw.model_inference,
        validation_question=raw.validation_question, extra_validation_questions=extra_questions,
        theme=getattr(raw, "theme", None), category=getattr(raw, "category", None), origin=origin,
        change_type=getattr(raw, "change_type", None),
        event_date=min(event_dates) if kind == "change" and event_dates else None,
        sources_note=sources_note, legacy=True,
    )
