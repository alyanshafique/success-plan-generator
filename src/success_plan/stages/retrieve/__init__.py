"""Stage 1: Retrieve public sources (latest 10-K, 8-Ks, GDELT headlines) and chunk them for citation."""

import json
from datetime import date, datetime, timedelta, timezone

from ...config import load_brand_aliases, load_low_signal_domains, load_private_names
from ...models import (
    CompanyInfo, EightKInfo, EightKSet, FilingInfo, HeadlineInfo, NewsInfo, RetrieveManifest, SectionInfo, SourceInfo,
)
from ...paths import CompanyPaths
from . import edgar
from .chunks import Chunk, SourceMeta, chunk_text
from .http import RateLimitedClient
from .news import AliasMatcher, fetch_headlines, redact_headline
from .sections import SectionError, cover_fiscal_year_end, html_to_text, split_sections

EVENTS_WINDOW_DAYS = 365
EARNINGS_RELEASES = 2
MANIFEST_NAME = "01_retrieve.json"

SECTION_LOCATIONS = {"1": "Item 1 Business", "1a": "Item 1A Risk Factors", "7": "Item 7 MD&A"}


class RetrieveError(Exception):
    pass


def eightk_info(
    filing: edgar.Filing, cik: int, labels: list[str], chunk_count: int, exhibit_url: str | None = None
) -> EightKInfo:
    """Manifest entry for an 8-K. event_date is EDGAR's report date (earliest event reported)."""
    return EightKInfo(
        accession=filing.accession, filing_date=filing.filing_date, event_date=filing.report_date,
        items=list(filing.items), item_labels=labels, url=filing.url(cik), exhibit_99_1_url=exhibit_url,
        chunk_count=chunk_count,
    )


def eightk_meta(filing: edgar.Filing, source_id: str, title: str, url: str, location: str) -> SourceMeta:
    return SourceMeta(
        source_id=source_id, source_type="filing", title=title, url=url, date=filing.filing_date.isoformat(),
        location=location, event_date=filing.report_date.isoformat() if filing.report_date else None,
    )


def run_retrieve(
    client: RateLimitedClient,
    paths: CompanyPaths,
    company_input: str,
    *,
    ticker: str | None = None,
    as_of: date | None = None,
    force: bool = False,
) -> RetrieveManifest:
    as_of = as_of or date.today()
    warnings: list[str] = []
    records: list[dict] = []
    sources: list[SourceInfo] = []

    def add_source(meta: SourceMeta, chunks: list[Chunk]) -> None:
        records.extend(c.record(meta) for c in chunks)
        sources.append(
            SourceInfo(
                source_id=meta.source_id, source_type=meta.source_type, title=meta.title, url=meta.url,
                date=date.fromisoformat(meta.date),
                event_date=date.fromisoformat(meta.event_date) if meta.event_date else None,
                location=meta.location, signal=meta.signal, chunk_count=len(chunks),
            )
        )

    # 1. Resolve the company. The ticker list changes over time, so it is refetched each run.
    tickers = client.get(edgar.TICKERS_URL).json()
    company = edgar.resolve_company(tickers, company_input, ticker)

    # 2. Filings. Load enough history to cover the 8-K events window.
    events_from = as_of - timedelta(days=EVENTS_WINDOW_DAYS)
    filings = edgar.load_filings(client, company.cik, since=events_from)

    # 3. Latest 10-K, with the fiscal-year cross-check against the cover page.
    tenk, tenk_warnings = edgar.select_latest_10k(filings, as_of)
    warnings.extend(tenk_warnings)
    tenk_url = tenk.url(company.cik)
    html = client.get_cached(tenk_url, paths.raw / "10k" / f"{tenk.accession}.htm", force=force)
    text = html_to_text(html)
    try:
        cover_end = cover_fiscal_year_end(text)
    except SectionError as exc:
        raise RetrieveError(str(exc)) from exc
    if cover_end != tenk.report_date:
        raise RetrieveError(
            f"Fiscal year mismatch: EDGAR report period is {tenk.report_date}, "
            f"but the cover page says fiscal year ended {cover_end}."
        )
    fiscal_year = str(tenk.report_date.year)

    try:
        sections = split_sections(text)
    except SectionError as exc:
        raise RetrieveError(str(exc)) from exc

    section_infos = []
    sections_dir = paths.raw / "10k" / "sections"
    sections_dir.mkdir(parents=True, exist_ok=True)
    for item, section in sections.items():
        (sections_dir / f"item{item}.txt").write_text(section.text)
        source_id = f"10k-item{item}"
        chunks = chunk_text(section.text, source_id, source_id)
        add_source(
            SourceMeta(
                source_id=source_id, source_type="filing",
                title=f"{company.name} Form 10-K for fiscal year {fiscal_year}",
                url=tenk_url, date=tenk.filing_date.isoformat(), location=SECTION_LOCATIONS[item],
            ),
            chunks,
        )
        section_infos.append(
            SectionInfo(
                item=item, title=section.title, word_count=section.word_count, chunk_count=len(chunks),
                first_chunk_id=chunks[0].id, last_chunk_id=chunks[-1].id,
            )
        )

    # 4. 8-Ks: change events in the last 12 months, plus the two latest earnings releases (Ex. 99.1).
    event_infos = []
    for filing in edgar.select_event_8ks(filings, events_from, as_of):
        url = filing.url(company.cik)
        body = html_to_text(client.get_cached(url, paths.raw / "8k" / filing.accession / filing.primary_document, force=force))
        source_id = f"8k-{filing.accession}"
        labels = [edgar.EVENT_ITEMS[i] for i in filing.items if i in edgar.EVENT_ITEMS]
        chunks = chunk_text(body, f"{source_id}-main", source_id)
        add_source(
            eightk_meta(
                filing, source_id, f"{company.name} Form 8-K filed {filing.filing_date} ({'; '.join(labels)})",
                url, f"Form 8-K Items {', '.join(filing.items)}",
            ),
            chunks,
        )
        event_infos.append(eightk_info(filing, company.cik, labels, len(chunks)))

    earnings_infos = []
    for filing in edgar.select_earnings_8ks(filings, as_of, EARNINGS_RELEASES):
        index_html = client.get_cached(
            filing.index_url(company.cik), paths.raw / "8k" / filing.accession / "index.htm", force=force
        ).decode("utf-8", errors="replace")
        exhibit = edgar.find_exhibit(index_html, "EX-99.1")
        if not exhibit:
            warnings.append(f"8-K {filing.accession} (Item 2.02, filed {filing.filing_date}) has no EX-99.1 exhibit.")
            continue
        exhibit_url = edgar.ARCHIVE_URL.format(
            cik=company.cik, acc_nodash=filing.accession.replace("-", ""), document=exhibit
        )
        body = html_to_text(client.get_cached(exhibit_url, paths.raw / "8k" / filing.accession / exhibit, force=force))
        source_id = f"8k-{filing.accession}-ex991"
        chunks = chunk_text(body, source_id, source_id)
        add_source(
            eightk_meta(
                filing, source_id, f"{company.name} earnings release (Form 8-K Ex. 99.1) filed {filing.filing_date}",
                exhibit_url, "Form 8-K Exhibit 99.1",
            ),
            chunks,
        )
        earnings_infos.append(
            eightk_info(filing, company.cik, ["Results of operations and financial condition"], len(chunks), exhibit_url)
        )
    if len(earnings_infos) < EARNINGS_RELEASES:
        warnings.append(f"Found {len(earnings_infos)} earnings release(s); expected {EARNINGS_RELEASES}.")
    for info in event_infos + earnings_infos:
        if info.event_date is None:
            warnings.append(f"8-K {info.accession} has no report date in EDGAR; event_date is unknown.")

    # 5. GDELT headlines (weak signals). Only headlines naming the company, ticker, or a brand alias
    #    from config are eligible for Extract. A GDELT failure is a warning, not an error.
    brands = load_brand_aliases(company.cik)
    if brands is None:
        brands = []
        warnings.append(
            f"No brand aliases for CIK {company.cik} in config/company_aliases.yaml; "
            "headline matching uses the company name and ticker only."
        )
    item1_text = sections["1"].text.lower()
    for brand in brands:
        if brand.lower() not in item1_text:
            warnings.append(f"Brand alias {brand!r} does not appear in Item 1 of this 10-K; check config/company_aliases.yaml.")
    phrase = edgar.display_name(company.name)
    core = edgar.normalize_name(company.name)
    # Brands come before the bare core name so a brand's casing wins when they coincide ("Expedia").
    names = [phrase] + brands + ([core.title()] if core else [])
    matcher = AliasMatcher(names, company.ticker)  # de-duplicates names case-insensitively
    alias_labels = matcher.labels

    low_signal_domains = load_low_signal_domains()
    gdelt_cache = paths.shared_cache / "gdelt" / f"cik{company.cik}_{as_of.isoformat()}.json"
    news = fetch_headlines(
        client, phrase, matcher, as_of, cache_path=gdelt_cache, force=force, low_signal_domains=low_signal_domains
    )
    if news.status == "failed":
        warnings.append(f"GDELT request failed ({news.error}). Continuing with 8-Ks only.")
    elif news.status == "empty":
        warnings.append(f"GDELT returned no headlines for {news.query} in the last 90 days.")
    # Post-fetch: headlines naming a person on the private list (config/private_names.txt, gitignored) are
    # replaced, after eligibility and alias matching, so names never reach the manifest or the chunks.
    private_names = load_private_names()
    redacted_count = 0
    headline_infos = []
    for i, h in enumerate(news.headlines, start=1):
        chunk_id = f"news-gdelt-{i:03d}"
        text, url, redacted = redact_headline(h.headline, h.url, h.domain, private_names)
        redacted_count += redacted
        if h.eligible:
            meta = SourceMeta(
                source_id=chunk_id, source_type="news", title=text, url=url, date=h.date.isoformat(),
                location=h.domain, signal="weak",
            )
            add_source(meta, [Chunk(id=chunk_id, source_id=chunk_id, text=text, word_count=len(text.split()))])
        headline_infos.append(
            HeadlineInfo(chunk_id=chunk_id, headline=text, url=url, date=h.date, domain=h.domain,
                         matched_aliases=list(h.matched_aliases), low_signal=h.low_signal,
                         eligible_for_extract=h.eligible)
        )
    if redacted_count:
        warnings.append(f"{redacted_count} GDELT headline(s) name a listed person; headline and URL path removed.")
    eligible_count = sum(1 for h in headline_infos if h.eligible_for_extract)
    low_signal_count = sum(1 for h in headline_infos if h.low_signal and h.matched_aliases)
    unmatched_count = sum(1 for h in headline_infos if not h.matched_aliases)
    if unmatched_count:
        warnings.append(
            f"{unmatched_count} GDELT headline(s) name neither the company nor an alias; "
            "kept in the manifest for review but not eligible for Extract."
        )
    if low_signal_count:
        warnings.append(
            f"{low_signal_count} GDELT headline(s) from low-signal domains (config/news_sources.yaml); "
            "kept in the manifest for review but not eligible for Extract."
        )

    # 6. Save chunks (gitignored, full text) and the manifest (committed, short facts only).
    paths.raw.mkdir(parents=True, exist_ok=True)
    with paths.chunks_file.open("w") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    manifest = RetrieveManifest(
        generated_at=datetime.now(timezone.utc).replace(microsecond=0),
        as_of=as_of,
        company=CompanyInfo(input_name=company_input, name=company.name, ticker=company.ticker, cik=company.cik),
        filing=FilingInfo(
            form=tenk.form, accession=tenk.accession, fiscal_year=fiscal_year, period_end=tenk.report_date,
            filing_date=tenk.filing_date, url=tenk_url, cover_fiscal_year_end=cover_end, fiscal_year_check="passed",
        ),
        sections=section_infos,
        eight_ks=EightKSet(
            events_window_from=events_from, events_window_to=as_of, events=event_infos, earnings_releases=earnings_infos,
        ),
        news=NewsInfo(
            provider="gdelt", status=news.status, query=news.query, window_from=news.window_from,
            window_to=news.window_to, error=news.error, aliases=alias_labels, low_signal_domains=low_signal_domains,
            from_cache=news.from_cache, returned_count=news.returned_count, eligible_count=eligible_count,
            low_signal_count=low_signal_count, headlines=headline_infos,
        ),
        sources=sources,
        total_chunks=len(records),
        request_count=client.request_count,
        warnings=warnings,
    )
    paths.stages.mkdir(parents=True, exist_ok=True)
    (paths.stages / MANIFEST_NAME).write_text(manifest.model_dump_json(indent=2) + "\n")
    return manifest
