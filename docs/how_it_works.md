# How it works

Detail behind the README's "How it works" and "Design principles". Project rules (provenance tags, CSM-owned
blanks, "no fit" answers) are in [CLAUDE.md](../CLAUDE.md). Failures and fixes are logged in
[evaluation_log.md](evaluation_log.md).

## Design in one paragraph

A model proposes; code verifies. Every sentence drawn from a source carries a citation to that source passage, and
code checks that the cited words appear in that passage. Product claims must come from a config checked against the
vendor's own pages. Code, not just the prompt, checks the rules: prices and plan tiers are blocked; outcome promises,
personal names, and unsupported sentences are flagged for review; timelines are relative; and customer-only fields are
left as labeled blanks. The flags are heuristics: they are backstops for review, not proof that a sentence is right.

## Retrieve: sources and dates

- **10-K timing.** The plan uses the latest 10-K filed on or before the as-of date. Late in a fiscal year that filing
  can be up to about a year old. More recent events come only from 8-Ks and headlines.
- **No earnings call transcripts.** Full transcripts are generally behind paywalls or restrictive terms. "Earnings
  commentary" comes from the earnings press releases companies file with the SEC (Form 8-K Exhibit 99.1). These are
  management's prepared statements and do not include analyst Q&A.
- **News is headlines only.** Headlines come from the GDELT DOC 2.0 API, which only searches the last 90 days. Only
  the headline, URL, date, and domain are stored (no article text). A headline is eligible for Extract only if it
  names the company, its ticker, or a brand listed in [config/company_aliases.yaml](../config/company_aliases.yaml)
  (brands taken from Item 1 of the 10-K), and does not come from a low-signal domain listed in
  [config/news_sources.yaml](../config/news_sources.yaml) (automated stock-position posts). Ineligible headlines are
  kept in the manifest for review only. GDELT responses are cached per company and as-of date in
  `data/_cache/gdelt/` (gitignored), so re-runs are reproducible and avoid GDELT's rate limit; `--force` refreshes the
  cache. After a GDELT HTTP 429 the tool waits 1, 3, then 5 minutes before retrying. Headline-only claims are treated
  as weak signals: they stay `inferred` and are labeled "news headline only" unless an 8-K or the 10-K corroborates
  them. If GDELT fails, the tool continues with 8-Ks only.
- **8-K dates.** Each 8-K carries two dates: `date` (filing date) and `event_date` (EDGAR's report date, the date of
  the earliest event reported). The event date can be several days before the filing date.

Stage outputs are written to `data/<company>/stages/`. Raw source text and chunks stay in `data/<company>/raw/`, which
is gitignored so long excerpts are never committed.

## Extract: how sentences cite sources

The model returns each item as a short title plus 1-4 sentences, one claim per sentence, and every sentence carries
1-2 evidence phrases from the paragraph that states it. Code verifies each phrase and keeps a sentence only if at least
one of its citations verifies; the item text is built from the kept sentences alone, so a sentence whose citations fail
cannot appear in the plan. Dropped sentences, and items left with no sentence, are listed in the guard report and the
review sheet (`data/<company>/raw/02_extract_review.html`, gitignored). Responses saved before this format (item-level
citations) can still be replayed read-only and are marked LEGACY.

**Evidence phrases are for verification, not display.** The rendered plan shows each sentence's source links (title,
section, URL, dates) and never the evidence phrases. The stage JSON (`data/<company>/stages/02_extract.json`) keeps the
verbatim phrases so every citation can be checked against its source, and those phrases can include officers' names
quoted from 8-K filings. Whether a sample contains names is not left to chance: a test fails if any name on the
gitignored `config/private_names.txt` appears in any tracked file (and in `data/*/inputs/` and `output/`, including the
text of Word files).

### Extract rules and flags

- **Headline corroboration checks term overlap, not meaning.** A claim that cites a news headline stays `inferred`
  with the "news headline only" label unless a cited filing chunk names the same entity: a proper noun from the
  headline that the filing writes with a capital letter mid-sentence. A shared figure alone does not corroborate; it
  is only extra support alongside an entity (and only in prose, not table rows). Company names, the ticker, brand
  aliases, and time or ordinal words do not count. A shared entity does not prove the filing describes the same event,
  so these items still go to CSM validation. Exception: a challenge backed by a verified Risk Factors citation stays
  `sourced`; an uncorroborated headline on it gets the label "uncorroborated headline" and its own validation question.
  A challenge supported only by change-event 8-Ks is excluded as "covered under changes".
- **Financing is not an initiative.** Share repurchases, dividends, credit facilities, and notes offerings are excluded
  from initiatives by code (a model-chosen theme plus a keyword backstop) and listed in the guard report. Financing
  events can still appear as structural changes.
- **Guidance is not an initiative.** Initiatives that mention guidance or an outlook, or whose only citations are rows
  of a "Business Outlook" table, are excluded and listed in the guard report, so false positives stay visible.
- **Evidence length.** Evidence phrases under 6 words are rejected. A phrase over 14 words is accepted only if the
  whole phrase appears in the cited chunk; just its first 14 words are stored, and the citation is marked truncated.
  Any item with a rejected citation gets a `partial_support` flag, because some of its statements may rest only on the
  rejected citation.
- **Consequence words are a heuristic, not a proof.** If an item mentions fines, penalties, a lawsuit, bankruptcy,
  default, or liability and none of its cited chunks uses that word in any form, it gets an `unsupported_term` flag. A
  source can imply a consequence in other words, and a word can appear without supporting the item's claim, so flagged
  items stay in the output and go to review.
- **Statement support is a heuristic, not a proof.** Each sentence of an item's text is reduced to its content words
  (lowercased, stopwords and attribution words such as "headline" or "filing" dropped, lightly stemmed), numbers, and
  proper nouns. Form names ("10-K", "10-Q", "8-K") are not figures, and role titles ("Chief Executive Officer",
  "Chairman") are treated as attribution, because roles-only writing puts a role where the source has a name. A cited
  chunk's own filing and event dates, written out ("August 5, 2026"), count as part of its text; any other date in the
  sentence is still flagged. If under 75% of those terms appear in the item's verified cited chunks, or the sentence
  states a figure no cited chunk contains, the item gets an `unsupported_statement` flag naming the sentence and the
  missing terms. It cannot see meaning: a faithful paraphrase in different words can be flagged, and a sentence that
  reuses source words while misstating them is not. On 26 reviewer-labeled items from one run it flagged 11 of 13
  problem items and 4 of 13 fine ones; the threshold was chosen on those same items. Flagged items go to review. Each
  flag also says whether the missing terms were "found in" other chunks the call was allowed to cite (likely
  mis-cited; up to three chunk IDs) or "not found in any allowed chunk" (likely unsupported). This is term coverage
  too: common words are found in many chunks, so "found in" is weak evidence unless the terms are specific, and
  abbreviations ("Adj." for "Adjusted") can hide a source.
- **Results wording in initiatives is a crude keyword check.** An initiative sentence that uses "reported",
  "attributed", "achieved", "grew", or "increased" gets a `results_review` flag, because an initiative should state
  what the company is doing or intends to do, not a result. It misses results phrased other ways and flags legitimate
  uses; the item is never dropped.
- **Placeholder citations are rejected.** A citation whose chunk id or evidence contains "placeholder", "n/a", "tbd",
  or "xxx" is rejected as a placeholder, so its sentence is dropped unless another citation verifies.
- **Near-verbatim sentences can pass review.** The `long_quote` flag marks any sentence that copies 15 or more
  consecutive words from its source (ground rule 8). In run 5 the owner accepted one such sentence, just over the
  limit, as an exception; the rule and the flag are unchanged, so these still go to review. That exception applied to
  Extract output only; the rendered plan never contains such a run (see Render below).

## Map: how initiatives tie to capabilities

Map assigns each initiative a fit: **full** (the initiative is itself about the activity a capability performs),
**partial** (the capability supports the work needed to execute the initiative but does not deliver its outcome), or
**no_fit** (neither; named by a `GAP-*` reason from the config or explained). Every benefit sentence cites 1-2
capability claims, and code verifies each citation. The verification chain is:

1. **Benefit sentence → config claim.** The evidence phrase must appear word for word in the cited claim, which is a
   clause of the capability config's own description or limitations. Claims verify against the **config's wording,
   not Atlassian's text**.
2. **Config claim → Atlassian page.** The config's wording was checked against the capability's `source_urls` when the
   config was verified (`verified_on`, and `verified_by` where the owner checked it by hand). Map warns when a cited
   capability's verification is older than 90 days.

The model never sees plan availability, plan-tier lines or clauses, prices, or the config's `addresses` hints, and any
benefit sentence that states a plan or a price is dropped. Every key benefit carries the labeled blank "[CSM to confirm
which plan includes these capabilities]". The one-sentence fit rationale is always an inference. The Map review sheet
is `data/<company>/raw/03_map_review.html` (gitignored).

## Draft: objectives, success criteria, tasks

Draft writes objectives only for initiatives Map fitted (full or partial); `no_fit` initiatives are listed with their
reason and gap ids, and the plan leaves "[CSM to complete: add objectives for initiatives the tool did not map]". The
model returns one entry per initiative and writes only success criteria. Each criterion cites reference ids of its own
initiative's Extract sentences or Map benefit sentences, and code checks those references and scores the criterion
against them. Criteria name a measure and a direction; the target number is always a CSM blank after a baseline.
Objective titles are neutral templates, and tasks are filled from a fixed catalog
([config/draft_tasks.yaml](../config/draft_tasks.yaml)): the model only picks task ids, roles, and relative windows
(Day 0-30, 31-60, 61-90). Code adds the "agree baselines" task to every objective. An initiative the model returns no
criteria for is reported as "initiative skipped by model", and its objective gets a CSM blank criterion.

## Render: the plan and the brief

Render (no model call) assembles the plan, adds the CSM blanks (account history, renewal, customer-raised challenges,
unmapped objectives), gathers every validation and baseline question into items to validate, runs plan-wide price and
plan-tier checks, and validates against the schema before writing anything. The plan shows source links, never
evidence phrases; news links show the site, not the headline. The rendered plan contains no verbatim run of 15 or more
words from any source: Render replaces any sentence that has one with the marker "[sentence omitted: verbatim passage
from the filing]" (keeping its source link), also replaces a following sentence that points back at it and any
validation question that relied on it, lists each in `render_report.json`, and states the count in the plan's
metadata. Shorter verbatim spans remain, cited by source link.

CSM selection is optional: put `selected: [I-01, ...]` and the Extract timestamp (`extract_generated_at`) in
`data/<company>/inputs/csm_selection.yaml`. Without it every initiative shows "Proposed, awaiting CSM review"; with it,
unlisted initiatives show "Proposed, not selected". The committed sample has no selection file: it is unedited tool
output.

**The brief** (`plan_brief.md` and `plan_brief.docx`) is a short summary of the same plan.json for a CSM to read
before a customer call. It has a CSM action line under each section, puts discovery questions next to what they test,
and has no source links; the detailed plan has every sentence, source, and citation. Key challenges in the brief are
limited to the categories and change types in [config/brief_rules.yaml](../config/brief_rules.yaml); the rest are
counted and left to the detailed plan. Render runs the same leakage checks on the brief before keeping it.
