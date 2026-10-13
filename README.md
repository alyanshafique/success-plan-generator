# Success Plan Generator

A CLI that drafts a **hypothetical** customer success plan for a company from **public information only**
(SEC filings, public news headlines, public product documentation). The output is a **first-draft hypothesis
for a CSM to review and edit**, then validate with the customer. It is never presented as fact.

> All inputs are publicly available. This tool does not use proprietary or customer data. This output is a
> hypothesis generated from public information and has not been validated with the company.

Project rules (provenance tags, CSM-owned blanks, "no fit" answers) are in [CLAUDE.md](CLAUDE.md).

**Not affiliated with Atlassian or Expedia.** This is an independent portfolio project. Product names and
company names are used only to describe public information; no company has reviewed or endorsed this output.

## What this is, and how much to trust it

A command-line pipeline that drafts a hypothetical customer success plan for a public company, using only public
sources (SEC 10-K and 8-K filings, earnings releases, news headlines) and a product capability config. It is a
first-draft hypothesis for a customer success manager to review, not a finished plan.

**Design in one paragraph.** A model proposes; code verifies. Every sentence drawn from a source carries a citation to
that source passage, and code checks that the cited words appear in that passage. Product claims must come from a
config checked against the vendor's own pages. Code, not just the prompt, checks the rules: prices and plan tiers are
blocked; outcome promises, personal names, and unsupported sentences are flagged for review; timelines are relative;
and customer-only fields are left as labeled blanks.

**Evaluation (one company).** Each Expedia extraction run was checked against its cited sources in a review sheet,
counting items with at least one unsupported or meaning-shifted statement. The first two counts came from an
AI-assisted read of the review sheet (the coding assistant, Claude, reading the sheet), with some findings then
confirmed individually by me: on the item-level run, 4 of the 13 (C-12, I-11, I-07, C-04); on the first sentence-level
run, all 3 errors. The other 9 item-level findings are the assistant's only. The last two runs (0 of 25 and 1 of 30) I
read in full myself.

| Run | Items with a problem |
|---|---|
| Item-level citations (Sonnet) | 13 of 28 |
| First sentence-level run (Sonnet) | 3 of 28 |
| Sentence-level, after prompt fixes (Sonnet) | 0 of 25 |
| Final run (Opus) | 1 of 30 |

Caveats: I wrote the rules and did the confirmations; the pipeline was tuned on the earlier runs, so these are not
held-out results; the measure covers sentence-level support, not omissions (two models chose different risks from the
same filing). Three of the 13 item-level problems (C-12, I-11, I-07) were true statements cited to the wrong passage
(I-07 also lost some wording in compression). Details are in docs/evaluation_log.md.

**What the code missed.** On the first sentence-level run, word-overlap checks caught none of the three confirmed
errors (a wrong date and two lost hedges). In the final run, a sentence turned "initiatives continued to drive
efficiencies" into "initiatives target efficiencies", and every automated check passed it. A human reading the
sentence beside its source caught it.

**A failure of my own safeguard.** A check meant to decide whether a news headline was backed by a filing matched the
figure 58 in a headline about 58 layoffs to "(58)" in unrelated rows of an earnings-release table (legal reserves and
interest expense). The claim was labeled filing-backed when it was not. I found it by reading the cited filing passage,
and replaced the rule with one that requires a shared named entity and ignores table rows.

**Cost.** The Expedia sample cost $1.23 in model calls (extraction on Opus $1.159, mapping $0.045, drafting $0.028).
All 14 logged development runs together cost $3.38. A Sonnet extraction would bring one plan to roughly $0.50
(estimate).

**Limitations.** One company tested. The sample's mapping and drafting outputs are replays of saved real runs. The
product config's plan-level details were checked by hand once, and the tool never states plan availability. Three
sentences are omitted from the sample: two for verbatim runs of 15 or more words, and one that pointed back at an
omitted sentence. Earnings releases stand in for call transcripts, the 10-K can be months old, and GDELT covers only
the last 90 days and throttles. Nine of twelve Expedia initiatives mapped to "no fit" by design, since Jira and
Confluence track work and do not run a travel business.

## Setup

Requirements: [uv](https://docs.astral.sh/uv/getting-started/installation/) (it installs Python 3.12 for you) and, for
Extract onward, an Anthropic API key.

```bash
uv sync                       # creates .venv with Python 3.12 and the dependencies
cp .env.example .env          # .env is gitignored; never commit it
```

Then fill in `.env`:

- `SEC_USER_AGENT="Your Name you@example.com"`: required by SEC EDGAR (a descriptive name and a contact email).
- `ANTHROPIC_API_KEY=...`: needed from the Extract stage onward. Without it, `--dry-run` still estimates tokens and
  cost with a calibrated character count, and Render needs no key.

Every stage that calls a model supports `--dry-run`, and a run aborts before any call if its estimate exceeds
`max_cost_usd_per_run` in `config/models.yaml`. The Expedia sample cost $1.23 end to end (see below).

## Usage

```bash
uv run spg retrieve --company "Expedia"            # Stage 1
uv run spg retrieve --company "Expedia" --as-of 2026-10-12
uv run spg extract --company "Expedia" --dry-run   # Stage 2 token and cost estimate, no API call
uv run spg extract --company "Expedia" --profile dev   # profiles: plumbing (Haiku), dev (Sonnet), final (Opus)
uv run spg map --company "Expedia" --dry-run       # Stage 3 (also: --profile, replay-map)
uv run spg draft --company "Expedia" --dry-run     # Stage 4 (also: --profile, replay-draft)
uv run spg render --company "Expedia"              # Stage 5: output/<company>/plan.json and plan.md (no model call)
uv run pytest                                      # offline tests (no network, no API calls)
uv run pytest -m live                              # live EDGAR/GDELT smoke test
```

Stage outputs are written to `data/<company>/stages/`. Raw source text and chunks stay in
`data/<company>/raw/`, which is gitignored so long excerpts are never committed.

## How Extract cites sources

The model returns each item as a short title plus 1-4 sentences, one claim per sentence, and every
sentence carries 1-2 evidence phrases from the paragraph that states it. Code verifies each phrase and
keeps a sentence only if at least one of its citations verifies; the item text is built from the kept
sentences alone, so a sentence whose citations fail cannot appear in the plan. Dropped sentences, and
items left with no sentence, are listed in the guard report and the review sheet
(`data/<company>/raw/02_extract_review.html`, gitignored). Responses saved before this format
(item-level citations) can still be replayed read-only and are marked LEGACY.

**Evidence phrases are for verification, not display.** The rendered plan shows each sentence's source
links (title, section, URL, dates) and never the evidence phrases. The stage JSON
(`data/<company>/stages/02_extract.json`) keeps the verbatim phrases so every citation can be checked
against its source, and those phrases can include officers' names quoted from 8-K filings. Whether a sample contains
names is not left to chance: a test fails if any name on the gitignored `config/private_names.txt`
appears in any tracked file.

## How Map ties initiatives to capabilities

Map assigns each initiative a fit: **full** (the initiative is itself about the activity a capability performs),
**partial** (the capability supports the work needed to execute the initiative but does not deliver its outcome),
or **no_fit** (neither; named by a `GAP-*` reason from the config or explained). Every benefit sentence cites 1-2
capability claims, and code verifies each citation. The verification chain is:

1. **Benefit sentence → config claim.** The evidence phrase must appear word for word in the cited claim, which is
   a clause of the capability config's own description or limitations. Claims verify against the **config's
   wording, not Atlassian's text**.
2. **Config claim → Atlassian page.** The config's wording was checked against the capability's `source_urls`
   when the config was verified (`verified_on`, and `verified_by` where the owner checked it by hand). Map warns
   when a cited capability's verification is older than 90 days.

The model never sees plan availability, plan-tier lines or clauses, prices, or the config's `addresses` hints, and
any benefit sentence that states a plan or a price is dropped. Every key benefit carries the labeled blank
"[CSM to confirm which plan includes these capabilities]". The one-sentence fit rationale is always an inference.
The Map review sheet is `data/<company>/raw/03_map_review.html` (gitignored).

## How Draft and Render build the plan

**Draft** writes objectives only for initiatives Map fitted (full or partial); `no_fit` initiatives are listed with
their reason and gap ids, and the plan leaves "[CSM to complete: add objectives for initiatives the tool did not
map]". The model writes only success criteria. Each criterion cites reference ids of its own initiative's Extract
sentences or Map benefit sentences, and code checks those references and scores the criterion against them.
Criteria name a measure and a direction; the target number is always a CSM blank after a baseline. Objective titles
are neutral templates, and tasks are filled from a fixed catalog (`config/draft_tasks.yaml`): the model only picks
task ids, roles, and relative windows (Day 0-30, 31-60, 61-90).

**Render** (no model call) assembles the plan, adds the CSM blanks (account history, renewal, customer-raised
challenges, unmapped objectives), gathers every validation and baseline question into items to validate, runs
plan-wide price and plan-tier checks, and validates against the schema before writing anything. The plan shows
source links, never evidence phrases; news links show the site, not the headline. The rendered plan contains no
verbatim run of 15 or more words from any source: Render replaces any sentence that has one with the marker
"[sentence omitted: verbatim passage from the filing]" (keeping its source link), also replaces a following sentence
that points back at it and any validation question that relied on it, lists each in `render_report.json`, and states
the count in the plan's metadata. Shorter verbatim spans remain, cited by source
link. The owner's earlier exception for sentences with verbatim spans applied to Extract output only. CSM selection is optional: put
`selected: [I-01, ...]` and the Extract timestamp (`extract_generated_at`) in
`data/<company>/inputs/csm_selection.yaml`. Without it every initiative shows "Proposed, awaiting CSM review"; with
it, unlisted initiatives show "Proposed, not selected". The committed sample has no selection file: it is unedited
tool output.

## The Expedia sample

[`output/expedia/plan.md`](output/expedia/plan.md) (and `plan.json`) is **unedited tool output: a first-draft
hypothesis** for a CSM to review and edit with the company. Nothing in it was changed by hand, and the owner's review
notes stay in [docs/evaluation_log.md](docs/evaluation_log.md), not in the plan.

**Brief:** [`output/expedia/plan_brief.md`](output/expedia/plan_brief.md) (also as
[`plan_brief.docx`](output/expedia/plan_brief.docx)) is a short summary of the same plan.json for a CSM to read before a
customer call, with a CSM action under each section and the discovery questions next to what they test. It has no
source links; the detailed plan above has every sentence, source, and citation.

- **I-08 (Restructuring and cost efficiency) is drafted** because Map rated it a partial fit, although the owner judged
  that fit stretched. Its Extract sentence "Ongoing initiatives target transactional efficiencies, particularly in
  payments and customer service" turns a result the filing reports into an aim; this is logged, and the sample is not
  edited to hide it.
- **Three sentences are omitted** for verbatim runs of 15 or more words from the filing (I-12 s1, I-12 s2, which points
  back at s1, and C-06 s1); two validation questions that relied on them are CSM blanks. See
  `output/expedia/render_report.json`.
- **Map is a replay of a saved real run** (`map-20261012T235845Z-dev`, Sonnet): the guards were re-applied to the saved
  response with no API call. Draft is likewise a replay of its saved real run (`draft-20261013T214056Z-dev`). The plan's
  metadata names both runs.
- **Nine of twelve initiatives are no_fit by design.** Most of Expedia's initiatives are commercial or customer-facing
  (supply, loyalty, marketing, personalization), which Jira and Confluence do not do. They are listed with their reasons
  and gap ids, and get no objective.

**Cost of the sample** (from `data/expedia/stages/usage_log.jsonl`; Anthropic API prices as of 2026-10-06):

| Stage | Profile and model | Cost |
|---|---|---|
| Extract | final, Opus 5.5 (high effort) | $1.1592 |
| Map | dev, Sonnet 5.5 | $0.0455 |
| Draft | dev, Sonnet 5.5 | $0.0279 |
| **Total** | | **$1.2325** |

*Estimate, not measured:* the same plan with a Sonnet 5.5 extraction would cost about **$0.50** (the latest Sonnet
Extract run on the same inputs cost $0.4270, plus the same Map and Draft runs). That is a cost estimate only; a Sonnet
extraction produces different initiatives, so the rest of the plan would differ too. Development runs (plumbing, dev,
and re-runs across all stages, 14 logged runs including the three above) cost $3.38 in total.

**Limitations of this evaluation:**
- One company (Expedia) and one product family (Jira and Confluence).
- One model family (Claude): Haiku for plumbing, Sonnet for development, Opus for the final extraction.
- Single-reviewer evaluation: the owner's judgment, with no second reviewer or inter-rater check.
- Prompt and rule changes were made after reading Expedia runs, so later runs on the same input are not held-out tests.
- Support checks are sentence-level word overlap: they catch added facts and figures, not omissions. A sentence can
  pass while leaving out a qualifier that changes its meaning (I-08 above).

## Sources and known limitations

- **10-K timing.** The plan uses the latest 10-K filed on or before the as-of date. Late in a fiscal year
  that filing can be up to about a year old. More recent events come only from 8-Ks and headlines.
- **No earnings call transcripts.** Full transcripts are generally behind paywalls or restrictive terms.
  "Earnings commentary" comes from the earnings press releases companies file with the SEC
  (Form 8-K Exhibit 99.1). These are management's prepared statements and do not include analyst Q&A.
- **News is headlines only.** Headlines come from the GDELT DOC 2.0 API, which only searches the last
  90 days. Only the headline, URL, date, and domain are stored (no article text). A headline is eligible
  for Extract only if it names the company, its ticker, or a brand listed in
  [config/company_aliases.yaml](config/company_aliases.yaml) (brands taken from Item 1 of the 10-K),
  and does not come from a low-signal domain listed in [config/news_sources.yaml](config/news_sources.yaml)
  (automated stock-position posts). Ineligible headlines are kept in the manifest for review only.
  GDELT responses are cached per company and as-of date in `data/_cache/gdelt/` (gitignored), so
  re-runs are reproducible and avoid GDELT's rate limit; `--force` refreshes the cache. After a GDELT
  HTTP 429 the tool waits 1, 3, then 5 minutes before retrying. Headline-only claims are treated as weak signals: they stay `inferred` and are labeled "news headline
  only" unless an 8-K or the 10-K corroborates them. If GDELT fails, the tool continues with 8-Ks only.
- **8-K dates.** Each 8-K carries two dates: `date` (filing date) and `event_date` (EDGAR's report date,
  the date of the earliest event reported). The event date can be several days before the filing date.

- **Headline corroboration checks term overlap, not meaning.** A claim that cites a news headline stays
  `inferred` with the "news headline only" label unless a cited filing chunk names the same entity: a
  proper noun from the headline that the filing writes with a capital letter mid-sentence. A shared
  figure alone does not corroborate; it is only extra support alongside an entity (and only in prose,
  not table rows). Company names, the ticker, brand aliases, and time or ordinal words do not count.
  A shared entity does not prove the filing describes the same event, so these items still go to CSM
  validation. Exception: a challenge
  backed by a verified Risk Factors citation stays `sourced`; an uncorroborated headline on it gets the
  label "uncorroborated headline" and its own validation question. A challenge supported only by
  change-event 8-Ks is excluded as "covered under changes".
- **Financing is not an initiative.** Share repurchases, dividends, credit facilities, and notes
  offerings are excluded from initiatives by code (a model-chosen theme plus a keyword backstop) and
  listed in the guard report. Financing events can still appear as structural changes.

- **Guidance is not an initiative.** Initiatives that mention guidance or an outlook, or whose only
  citations are rows of a "Business Outlook" table, are excluded and listed in the guard report, so false
  positives stay visible.
- **Evidence length.** Evidence phrases under 6 words are rejected. A phrase over 14 words is accepted only
  if the whole phrase appears in the cited chunk; just its first 14 words are stored, and the citation is
  marked truncated. Any item with a rejected citation gets a `partial_support` flag, because some of its
  statements may rest only on the rejected citation.
- **Consequence words are a heuristic, not a proof.** If an item mentions fines, penalties, a lawsuit,
  bankruptcy, default, or liability and none of its cited chunks uses that word in any form, it gets an
  `unsupported_term` flag. A source can imply a consequence in other words, and a word can appear without
  supporting the item's claim, so flagged items stay in the output and go to review.

- **Statement support is a heuristic, not a proof.** Each sentence of an item's text is reduced to its
  content words (lowercased, stopwords and attribution words such as "headline" or "filing" dropped,
  lightly stemmed), numbers, and proper nouns. Form names ("10-K", "10-Q", "8-K") are not figures, and role
  titles ("Chief Executive Officer", "Chairman") are treated as attribution, because roles-only writing
  puts a role where the source has a name. A cited chunk's own filing and event dates, written out
  ("August 5, 2026"), count as part of its text; any other date in the sentence is still flagged. If under 75% of those terms appear in the item's verified
  cited chunks, or the sentence states a figure no cited chunk contains, the item gets an
  `unsupported_statement` flag naming the sentence and the missing terms. It cannot see meaning: a
  faithful paraphrase in different words can be flagged, and a sentence that reuses source words while
  misstating them is not. On 26 reviewer-labeled items from one run it caught 11 of 13 problem items and
  flagged 4 of 13 fine ones; the threshold was chosen on those same items. Flagged items go to review.
  Each flag also says whether the missing terms were "found in" other chunks the call was allowed to
  cite (likely mis-cited; up to three chunk IDs) or "not found in any allowed chunk" (likely
  unsupported). This is term coverage too: common words are found in many chunks, so "found in" is weak
  evidence unless the terms are specific, and abbreviations ("Adj." for "Adjusted") can hide a source.

- **Results wording in initiatives is a crude keyword check.** An initiative sentence that uses "reported",
  "attributed", "achieved", "grew", or "increased" gets a `results_review` flag, because an initiative should
  state what the company is doing or intends to do, not a result. It misses results phrased other ways and
  flags legitimate uses; the item is never dropped.
- **Placeholder citations are rejected.** A citation whose chunk id or evidence contains "placeholder",
  "n/a", "tbd", or "xxx" is rejected as a placeholder, so its sentence is dropped unless another citation
  verifies.

- **Near-verbatim sentences can pass review.** The `long_quote` flag marks any sentence that copies 15 or
  more consecutive words from its source (ground rule 8). In run 5 the owner accepted one such sentence,
  just over the limit, as an exception; the rule and the flag are unchanged, so these still go to review.

Known limitations and observed failures are tracked in [docs/evaluation_log.md](docs/evaluation_log.md).

## License

MIT. See [LICENSE](LICENSE).
