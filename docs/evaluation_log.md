# Evaluation log

Known limitations and observed failures (wrong fiscal year, stale news, generic metrics, unsupported
claims). This log is a project deliverable: each entry says what went wrong or what is limited, why it
matters, and what the tool does about it.

## Known limitations (by design)

| Date | Area | Limitation | Impact | Mitigation |
|---|---|---|---|---|
| 2026-10-12 | Retrieve: 10-K | The latest 10-K can be up to about a year old late in a fiscal year. | Initiatives and risk factors may not reflect the company's current priorities. | Fiscal year and filing date are recorded in metadata. A warning is raised if the 10-K is more than 15 months old. 8-Ks from the last 12 months add recent events. |
| 2026-10-12 | Retrieve: earnings | Earnings press releases (8-K Exhibit 99.1) are used instead of call transcripts. | No analyst Q&A or unscripted management commentary; releases are prepared statements focused on results. | Documented in README. Releases are labeled as such in citations. |
| 2026-10-12 | Retrieve: news | GDELT provides headlines only, over a 90-day rolling window. | No article context; headlines can be misleading or off-topic; older news is not covered. | Headlines are weak signals: claims supported only by a headline stay `inferred` and are labeled "news headline only". Only headlines naming the company, ticker, or a configured brand alias are eligible for Extract; others are kept in the manifest for review. If GDELT fails, the run continues with 8-Ks only. |
| 2026-10-12 | Retrieve: sections | MD&A or other sections incorporated by reference from another document (e.g. an annual report exhibit) are not supported. | Retrieve stops with an error for such filers. | Error message names the section. Out of scope for v1. |
| 2026-10-12 | Citation guard | The guard proves the cited words exist in the source, not that the claim built on them is a faithful paraphrase. | A real quote could be attached to an overstated claim. | CSM validation questions; manual spot-checks of a sample of claims on each final run, recorded below. |

## Owner manual checks

| Date | Check | Result | Effect on the tool |
|---|---|---|---|
| 2026-10-12 | Owner checked public list pricing for Jira and Confluence (300 users, billed monthly). | Pricing is intentionally excluded from generated plans. Individual prices are not recorded here or anywhere in the project. | Ground rule 10 in CLAUDE.md: never state prices, discounts, or contract terms in a generated plan, because enterprise contracts are custom. The Extract and Draft design requires the guards to flag any currency amount or per-user price in a generated plan (check and test to be added when the guards module is built). |
| 2026-10-12 | Owner compared the public Jira and Confluence plan-comparison tables against the `plan_availability` lines in the capability config, and confirmed specific claims on Atlassian pages. | No discrepancies found. Confirmed all-plan availability for Jira boards, workflows, reporting, and integrations (including Slack and Microsoft Teams), and for Confluence pages/spaces/templates, whiteboards, databases, automation, and linking Confluence pages to Jira work items. Confirmed Rovo in Jira is on paid plans only (Standard, Premium, Enterprise) and summarizes inside Jira, and that the knowledge base supports project plans, policies, and meeting notes. Confirmed product-level audit logs on Standard, Premium, and Enterprise; organization-level audit logs on Enterprise or with Atlassian Guard; retention up to 180 days for both. | Recorded in the config `meta.owner_manual_check`. Affected entries carry `verified_by: owner_manual_check` and an `owner_verified` list. The audit-log claim on JIRA-ADMIN-GOVERNANCE moved from `inferred` to owner-verified. Resolved `unconfirmed` notes removed as directed. The tool still never states plan-level availability as fact in its output. |

## Run log

Entries for each run: date, company, stage, what was checked, what was wrong, and the fix.

### 2026-10-12 · Expedia · Retrieve (as-of 2026-10-12)

| Check | Result | Action |
|---|---|---|
| Section splitter, first live run | **Failed.** Expedia's body headings read "Part I. Item 1A. Risk Factors"; the heading pattern only accepted lines starting with "Item", so only the table of contents matched and Item 1 came back with 0 words. Retrieve stopped with an error instead of saving bad sections. | Fixed: headings may start with "Part I." / "Part II.", and heading text is capped at 130 characters so cross-reference sentences ("see Part I, Item 1A, Risk Factors, under the caption ...") are not treated as boundaries. Regression test added. |
| Fiscal year | FY2025 (period end 2025-12-31) from EDGAR matches the cover page. 10-K filed 2026-02-13, about 8 months before the as-of date. | None. |
| Section edges | First and last 40 words of Items 1, 1A, and 7 reviewed; all start in the body (not the table of contents) and end before the next item. Item 7 ends with the guarantor summary table, which is part of MD&A in this filing. | None. |
| GDELT relevance | 36 of 50 headlines did not name Expedia. Most were off-topic (other companies, generic "stocks to watch" lists). The relevance flag also missed headlines naming only the ticker ("EXPE") or a brand ("Vrbo"). | **Fixed.** A headline is eligible for Extract only if it names the company, the ticker (exact case, word boundary), or a brand alias. Brand aliases live in `config/company_aliases.yaml` (taken from Item 1 of the 10-K; Retrieve warns if a brand no longer appears there), not in the model. Ineligible headlines stay in the manifest for review but are not written to chunks or sources. Tests cover ticker and alias matching. |
| GDELT window coverage | The 50-headline cap kept the newest 50, which only reached back to 2026-08-24 (about 7 weeks of the 90 days). | **Fixed.** Eligible headlines are ranked first (newest first), then remaining slots are filled by date with ineligible ones. Re-run: GDELT returned 114 unique headlines; 43 are eligible and span 2026-07-15 to 2026-09-30, covering the 90-day window. |

### 2026-10-12 · Expedia · Retrieve re-run after review fixes (as-of 2026-10-12)

| Check | Result | Action |
|---|---|---|
| 8-K event dates | Added `event_date` (EDGAR report date) next to `filing_date` for every 8-K, in the manifest, chunk metadata, and the schema's citation object. Leadership-change 8-K: filed 2026-04-23, event 2026-04-17 (matches EDGAR). Material-agreement 8-K: filed 2026-03-30, event 2026-03-27. Earnings 8-Ks: same day. | Test added (offline, against a verbatim EDGAR excerpt) plus a live check. |
| GDELT rate limiting | **Observed twice.** GDELT returned HTTP 429 when called again a few seconds after the live test, and again about 4 minutes after a successful call. The run continued with 8-Ks only both times, as designed, but the manifest then had no headlines. | **Fixed.** (1) Successful GDELT responses are cached per company and as-of date in `data/_cache/gdelt/` (gitignored); re-runs for the same as-of date reuse the cache and make no GDELT request, and `--force` bypasses and refreshes it. Failed or non-JSON responses are never cached. (2) After a GDELT 429 the client waits 60, 180, then 300 seconds before retrying (other hosts keep short backoff; `Retry-After` is honored), and the CLI prints why it is waiting. Tests cover cache reuse, force refresh, no caching of failures, and both backoff schedules. |
| GDELT eligible noise | 16 of the 43 eligible headlines were automated stock-position or rating posts (tickerreport.com, dailypolitical.com, themarketsdaily.com). They name Expedia, so they passed the alias rule, but carry little or no business signal. | **Fixed.** `config/news_sources.yaml` lists low-signal domains (subdomains match too). Headlines from them are flagged `low_signal`, kept in the manifest, and excluded from chunks and from Extract. Tests cover domain matching and eligibility. |

### 2026-10-12 · Expedia · Retrieve re-run with GDELT cache, backoff, and low-signal domains (as-of 2026-10-12)

| Check | Result | Action |
|---|---|---|
| GDELT backoff in practice | First run after the change: GDELT returned 429 on all four attempts (initial plus retries after 60, 180, and 300 seconds, about 9 minutes). The run fell back to 8-Ks only and was not committed. After a further 15-minute pause, a single run succeeded and wrote the cache. | GDELT's throttle can last more than 10 minutes, so the cache matters more than the backoff. Re-runs for this as-of date now read the cache. If it recurs, consider an even longer schedule or failing fast with a clear message. |
| Low-signal headlines dropped by the cap | **Found and fixed during the re-run.** With low-signal posts now ineligible, they competed by date with headlines that name nobody for the slots left under the 50 cap, and 12 of the 16 were dropped from the manifest. That broke the rule to keep them for review. | Ranking now has three tiers: eligible, then low-signal headlines that name the company, then unmatched headlines; the cap applies after ranking. Test added. |
| Result | GDELT returned 114 unique headlines. Kept 50: 27 eligible (2026-07-15 to 2026-09-30, the full 90-day window), 16 low-signal posts naming Expedia, 7 naming neither. Chunks contain only the 27 eligible headlines and no low-signal domains. Second run read the cache (no GDELT request). | None. |

### 2026-10-12 · Expedia · Extract plumbing run (Haiku 5.5, profile `plumbing`)

Purpose: check that both calls run and parse. Output quality is not evaluated on this profile.

| Check | Result | Action |
|---|---|---|
| Calls and parsing | Both calls returned parsed output. 11 initiatives, 11 challenges, 3 changes. No exclusions, no overflow. | None. |
| Cost estimate | Actual $0.0194 vs estimate $0.0207. Input was **underestimated by about a third** (84,383 and 84,357 actual vs 63,398 and 63,150 estimated); output was overestimated (about 2,500 actual vs 8,000 assumed), which hid the input error. | Open: recalibrate the local estimate or use Anthropic's token-counting endpoint, so the $3 limit check is reliable for Opus runs. |
| Evidence length | **15 of 19 rejected citations broke the 6-14 word rule** (14 too long, 15-28 words; 1 too short). The other 4 were not found in the chunk. As a result, 11 items lost all citations and became `inferred` with `evidence_not_found` (I-01, I-06, I-07, I-09, I-10, C-01, C-02, C-05, C-07, C-08, C-10; checked against 02_extract.json). The guard worked as designed. | Open: check on the dev profile before changing the prompt. If it recurs, state the limit more prominently. |
| Duplicates across types | The CFO change appears as both a change (S-01, sourced) and a challenge (C-05, evidence rejected). | Open: decide whether a leadership change can also be a challenge. |
| Borderline initiative | I-03 "raised full-year 2026 guidance" is a financial outlook rather than an action or priority. | Open: tighten the initiative definition if it recurs on dev. |
| Rules that held | No personal names, no pricing words. Headline-only items (Layla acquisition, layoffs) stayed `inferred` with the label. The $2.5 billion credit facility amount traced to its 8-K; an untraceable $60 million was flagged for price review. | None. |

### 2026-10-12 · Expedia · Extract dev run (Sonnet 5.5, profile `dev`, prompts unchanged)

| Check | Result | Action |
|---|---|---|
| Calls, cost, counts | Both calls parsed. Input tokens counted exactly before the run (84,383 and 84,357; matched the API's billed input). Output 2,655 and 4,268 tokens vs 12,000 assumed. Cost $0.4067 vs estimate $0.5775. Initiatives 11 (9 sourced, 2 inferred); challenges 12 (12 sourced); changes 5 (3 sourced, 2 inferred). No exclusions, no overflow. | None. |
| Rejected citations | 4 (Haiku: 19): 3 slightly too long (15-16 words), 1 too short (3 words), 0 not found. Only 1 item lost all citations (I-09). The evidence-length problem is mostly a Haiku issue. | Open: the remaining 15-16 word phrases suggest the model miscounts by one or two words. |
| Guard bug found (plumbing run) | All 4 "evidence not found" rejections in the Haiku run were verbatim. Each phrase ended with a period, and normalization removes the space after punctuation, so the chunk read "2024.we also" and the word-boundary check failed. | Open: apply the boundary check only when the phrase starts or ends with a letter or digit. Not changed yet, awaiting owner approval. |
| Named individual | C-11 names the controlling shareholder with an honorific. The personal-title guard flagged it (kept for review). The roles-only rule in the prompt covers changes, not challenges. | Open: decide whether roles-only applies to all item types. |
| Financing as initiative | I-11 is a share repurchase authorization, which the prompt says is not an initiative without a stated business purpose. The model noted the purpose is not stated and marked it as inference, so it is `inferred`, but it should not be an initiative. | Open: prompt-only rule failed; consider a deterministic check or a stronger prompt. |
| Weak corroboration | S-03 combines a layoffs headline with a filing row on restructuring charges and is `sourced`, although the 58-job figure appears only in the headline. S-04 (Layla) cites a cash-flow row the model itself says is not tied to Layla; it is `inferred` only because the model set model_inference, and it lacks the "news headline only" label. The guard checks that a filing citation exists, not that it supports the headline's claim. | Open: needs a rule for when a filing citation counts as corroboration. |
| Amount from a table without "$" | S-03 states "$13 ... in millions" from a table cell that reads "13". Flagged for price review as untraceable (correct per rule, but a false alarm in substance). | Open: decide whether bare table numbers can trace. |
| Change event dates | S-03 and S-04 show event_date 2026-08-05, taken from the earnings release's 8-K report date. That is the release date, not the date of the layoffs or acquisition. | Open: take a change's event_date only from change-event 8-Ks (bug in Extract post-processing). |
| Rules that held | No pricing words. Headline-only reorganization (S-02) stayed `inferred` with the label. No analyst-rating or stock items proposed. CFO change uses roles only, event date 2026-04-17. $2.5 billion and $5 billion amounts traced to filings. | None. |

### 2026-10-12 · Extract fixes, new rules, replay, and second dev run (Sonnet 5.5)

Code since the first dev run: `6bbff3d` (evidence boundary and change event dates), `02d7d31` (financing
exclusion, headline corroboration, personal names), `14ad783` (prompts: 8-12 word evidence, no added
currency symbols, roles only everywhere, financing theme), `5be2712` (saved responses, replay, review
sheet), `ecda809` (replay fix: relative paths crashed; rebuilt responses now marked).

**Replay of the first dev run (no API call).** The first Sonnet run predates response saving, so its
response was REBUILT from that run's `02_extract.json` (lossless for guard inputs: no exclusions or
overflow, all citations kept) and marked as rebuilt. Current guards changed 5 of 28 items:

| Item | Change | Assessment |
|---|---|---|
| I-11 share repurchase | Excluded (financing keyword "repurchase") | Correct. |
| C-08 legal proceedings and tax disputes | sourced -> inferred, "news headline only" | Rule applied as written: an uncorroborated headline (lawsuit by Cuban exile families) downgrades an item whose main claim is supported by Risk Factors. Open design question. |
| C-11 voting control | New `personal_name` flag (surname from "Mr. ..." in the source) | Correct. |
| S-03 layoffs and restructuring charges | Event date removed (was the earnings release date) | Correct. Provenance stayed `sourced`: **false corroboration**, see below. |
| S-04 Layla acquisition | Event date removed; "news headline only" label added | Correct. |

**Second dev run (current prompts).** Input 84,617 and 84,516 tokens, counted exactly
before the run and matching billed input. Output 2,457 and 4,113. Cost $0.4040
vs estimate $0.5783. Initiatives 11 (all sourced); challenges
12 (11 sourced, 1 inferred); changes 5 (2 sourced, 3 inferred).
No exclusions, no overflow. Verified evidence phrases were 6-14 words (mean 9.9).

| Rejected citations by reason | Haiku plumbing | Sonnet dev 1 | Sonnet dev 2 |
|---|---|---|---|
| Evidence not found | 4 (guard bug) | 0 | 0 |
| Too long (15+ words) | 14 | 3 | 1 |
| Too short (under 6 words) | 1 | 1 | 4 |
| Total rejected / proposed | 19 / 34 | 4 / 51 | 5 / 49 |

New findings (open):

| Finding | Detail | Proposed action |
|---|---|---|
| **False corroboration from table figures** | S-04 (layoffs) is `sourced` because the headline figure "58" matched "(58)" in unrelated earnings-release table rows (legal reserves; interest expense). Same cause as S-03 in the replay. Financial tables are dense with numbers, so chance matches are likely. | Count figure matches only in prose lines, not table rows; replay the saved run to confirm. |
| Common words as distinctive terms | "second", "round", "year" from a sentence-case headline are candidate terms. They did not cause a match here, but "the Second Quarter" mid-sentence would. | Add time and ordinal words to the stoplist. |
| Item-level downgrade | C-08: one uncorroborated headline downgrades a Risk-Factors-backed challenge. | Owner decision: keep, or drop the uncorroborated headline citation instead of downgrading the item. |
| Evidence now too short | The 8-12 word prompt with "shorter is safer" shifted rejections from too long to too short (4 of 5 were 3-5 words). | Reword to stress the 8-word minimum. |
| Lost citations | S-01 (CFO change) lost its only citation at 15 words. C-09's true source for "$6.3 billion" was rejected at 5 words, so the amount was flagged as untraceable. | Same as above. |
| Borderline initiative | I-11 "2026 margin expansion target" is guidance. The prompt allows targets. | Owner decision on whether guidance targets count. |

Earlier open items, now closed:
- Cost estimate underestimated input (plumbing run): **closed** by exact token counting with a calibrated fallback (`e335414`).
- Evidence length (plumbing run): **closed for too-long phrases** (14 -> 1); replaced by the too-short finding above.
- Guard bug, "evidence not found" on phrases ending in a period: **closed** (`6bbff3d`); test uses the real I-06 phrase.
- Change event dates taken from earnings releases: **closed** (`6bbff3d`); confirmed in replay (S-03, S-04).
- Named individual (C-11): **closed**. Roles-only prompt for all item types plus a `personal_name` flag; no names in the second run.
- Financing as initiative (I-11 buyback): **closed** (`02d7d31`). Excluded in replay; none proposed in the second run.
- Amount from a table shown with an added "$" (S-03 "$13"): **closed** by the prompt; the second run wrote "13 in millions".
- Weak corroboration: **partly closed**. The rule now requires shared distinctive terms (S-04 Layla now labeled), but the figure match is too loose (first finding above).

Still open from earlier: CFO change appearing as both change and challenge (not observed in the second run; no rule yet); guidance as initiative (above).

### 2026-10-12 · Owner review of the dev-2 review sheet, new rules, and replay of dev-2

**Owner spot-check of the dev-2 review sheet** (`data/expedia/raw/02_extract_review.html`):

| Item | Owner finding | Status |
|---|---|---|
| S-04 layoffs and restructuring charges | The "58" is in the statement of operations, in a row unrelated to layoffs; the layoff evidence comes only from the headline. Falsely `sourced`. | **Fixed**: figures corroborate only from prose lines (`9694174`); replay shows `inferred`, "news headline only". |
| I-11 2026 margin expansion target | Guidance, not an initiative. | **Fixed**: guidance exclusion (`5ac2458`); excluded in replay as "guidance is not an initiative". |
| S-01 CFO role change | The evidence phrase was real but one word over the limit, so a correct item became `inferred`. The text also named the new CFO's prior employer. | **Fixed (evidence)**: over-14-word phrases found whole are verified and stored truncated to 14 words (`5ac2458`); replay shows `sourced`. **Open (biography)**: the prompt now forbids career history (`fe2dbd7`), but the specified keyword check ("previously served", "formerly") does not match the real wording "previously CFO of", so replay did not flag it. |
| C-04 alternative accommodations regulation | "fines" appears in neither cited chunk (owner read both). Overreach the citation guard cannot catch, because the cited phrases were real. | **Partly addressed**: `unsupported_term` flag (heuristic, documented in README) now flags it in replay. Overreach in general remains possible. |
| C-09 indebtedness and covenants | Generalizes the leverage covenant from the revolving credit facility to credit agreements. | **Open**: no guard catches this kind of generalization; relies on CSM review. In replay C-09 now carries `partial_support` (one citation rejected at 5 words). |
| C-01, I-04, C-12, C-09 | Some sentences rest only on citations the guard rejected, while the item stays `sourced`. | **Partly addressed**: all four now carry `partial_support` in replay. Provenance is unchanged; sentence-level support is not checked. |

**Rules added this round** (`5ac2458` rules, `fe2dbd7` prompt):
- Guidance and outlook items excluded from initiatives (keyword "guidance"/"outlook", or citations only to Business Outlook table rows). Excluded items stay listed.
- Evidence over 14 words is verified if the whole phrase is in the chunk; the first 14 words are stored and the citation is marked truncated. Under 6 words is still rejected.
- `partial_support` flag on any item with a rejected citation; shown in the review sheet.
- Roles only includes biography (prompt); `biography` flag for "previously served" or "formerly" in change items.
- `unsupported_term` flag for fines, penalties, lawsuit, bankruptcy, default, or liability absent from every cited chunk (heuristic, not a proof).
- Rule (e), "a filing citation supports a headline only if it names the same event or entity": the existing rule covers the **entity** case (shared proper noun), but does not check that it is the same **event**, and a **figure** shared in a prose line still corroborates without any shared entity. A strict expected-failure test records the figure gap; owner decision pending.

**Replay of the saved dev-2 response** (real saved response, no API call), compared with the original dev-2 output:
9 of 28 items changed (C-08 and S-04 from the previous round's rules; the other 7 from this round): I-11 excluded (guidance); S-01 `inferred` -> `sourced` (truncated citation); S-04 `sourced` -> `inferred`, "news headline only"; C-08 `inferred` -> `sourced`, "uncorroborated headline"; C-04 `unsupported_term` ("fines"); I-04, C-01, C-09, C-12 `partial_support`.
Result: initiatives 10 (all sourced); challenges 12 (all sourced); changes 5 (2 sourced, 3 inferred). Flags: {'partial_support': 4, 'price_review': 1, 'unsupported_term': 1}. Rejected citations 4 (all under 6 words). Excluded 1, overflow 0.

Open after this round: biography wording beyond the two keywords (S-01); figure-only corroboration (rule e); generalization and sentence-level support (C-09, partial-support items); too-short evidence (to be checked on the next real run with the reworded prompt).

### 2026-10-12 · Wider biography check, entity-only corroboration, replay, and third dev run (Sonnet 5.5)

**Rules** (`56cd229`):
- Biography flag on change items now matches "previously", "prior to", "formerly", "served as", "most
  recently", "joined from" (review only). Test uses the real S-01 wording ("previously CFO of Snap Inc.").
- Headline corroboration requires a shared entity (proper noun capitalized mid-sentence in a verified
  filing chunk). A shared figure alone no longer corroborates; it is extra support only. The strict
  expected-failure test for rule (e) is now a normal passing test. Same-event matching is still not checked.

**Replay of the saved dev-2 response** (no API call), compared with the previous replay: only S-01 changed,
gaining a `biography` flag ("previously"). No headline-backed item changed, because none relied on a figure
alone after the prose-only fix. Exclusions, overflow, and rejected citations unchanged. Matches owner expectations.

**Third dev run** (current prompts; dry run first: estimate $0.58, under the $0.80 cap). Input
84,743 and 84,542 tokens (counted exactly; matched billed). Output 2,669 and
3,353. Cost $0.3988 vs estimate $0.5786.
Initiatives 11 (10 sourced, 1 inferred);
challenges 12 (all sourced, all from Risk Factors); changes 5
(2 sourced, 3 inferred: the reorganization, Seattle layoffs, and Layla
acquisition are headline-only). No exclusions, no overflow.

| Rejected citations / proposed | Haiku | Sonnet 1 | Sonnet 2 | Sonnet 3 |
|---|---|---|---|---|
| Evidence not found | 4 | 0 | 0 | 0 |
| Too long and not found | 14 | 3 | 1 | 0 |
| Too short (under 6 words) | 1 | 1 | 4 | 1 |
| Total | 19 / 34 | 4 / 51 | 5 / 49 | 1 / 40 |

Checks:
- Verified evidence phrases: 39; 1 under 8 words; 16 over the prompt's 12-word maximum
  (accepted by the 6-14 guard), of which 3 were over 14 words, found whole, and stored truncated.
- Flags: one `partial_support` (I-04, margin-expansion citation rejected at 5 words). No `unsupported_term`,
  `biography`, `personal_name`, `personal_title`, `price_review`, or `long_quote` flags.
- No item names a person or states a prior employer or career history; S-01 now describes roles only.
- No guidance, financing, or analyst-rating item in initiatives. I-04 states a margin goal without guidance
  figures (allowed). Figures from tables are written without an added "$" (S-04: "13 in millions").
- Fewer citations proposed (40 vs 49-51); every challenge has a single Risk Factors citation.

Closed this round: biography wording beyond two keywords (S-01); figure-only corroboration (rule e, entity
required); too-short evidence (4 -> 1 rejected).
Still open: same-event matching for headlines; generalization and sentence-level support (only flagged via
`partial_support`); the model often exceeds the 12-word evidence target (16 of 39), which the guard tolerates.

### 2026-10-12 · Reviewer findings on the third dev run, and the statement-support check

**What the guards reported vs what review found.** On the third dev run the guards reported 1 rejected
citation and 1 flag (`partial_support` on I-04). Review of the review sheet
(`data/expedia/raw/02_extract_review.html`) found **13 items with statements their verified citations do
not contain**. Every one of these citations was real and verified; the overreach is in the item text.

**Reviewer findings** (owner confirmation pending for C-12, I-07, C-04, and I-11):

| Item | Unsupported statement |
|---|---|
| I-04 | "cloud spending optimization" |
| I-07 | "faster room night growth in some geographies" (owner confirmation pending) |
| I-11 | "focusing most efforts on Expedia, Hotels.com and Vrbo" (owner confirmation pending) |
| C-02 | "algorithm changes"; "significant share of traffic" |
| C-03 | "greater resources or data"; "cost market share and margins" |
| C-04 | "add compliance costs" (owner confirmation pending) |
| C-06 | "agentic AI booking" |
| C-07 | "AI-assisted" |
| C-09 | "tax authorities are increasingly assertive" |
| C-10 | "leverage ratio covenant" |
| C-11 | "competition for AI talent" |
| C-12 | "about 32%" (owner confirmation pending) |
| S-02 | "released from senior note guarantees" |

Items judged fine by the reviewer: I-01, I-02, I-05, I-06, I-08, I-09, I-10, C-01, C-05, C-08, S-01, S-03, S-05.

**Statement-support check** (`b24acb3`, heuristic, not a proof): each sentence is scored by the share of its
content words, numbers, and proper nouns found in the item's verified cited chunks; under 75%, or any figure
absent from the cited chunks, adds an `unsupported_statement` flag naming the sentence and missing terms.

**Replay of the saved dev-3 response** (no API call):

| | Recall | Precision |
|---|---|---|
| Items (13 problem, 13 fine) | 11 of 13 problem items caught | 11 of 15 flagged items are problems (4 fine items flagged) |
| Sentences (15 problem, 22 scored fine-item sentences) | 11 of 15 caught | 11 of 17 flagged sentences are problems (6 fine-item sentences flagged) |

- Caught: I-07, C-02 (traffic share), C-03 (market share and margins), C-04, C-06, C-07, C-09, C-10, C-11,
  C-12 (by the figure rule: "32%" is in no cited chunk), S-02.
- Missed: I-04 (score 0.79: only "cloud" and "optimization" missing among 14 terms); I-11 (score 1.00: every
  word is in the source, but the source says the *marketing* effort goes to the core brands, so the
  overreach is in meaning, not vocabulary) [corrected in the next entry: mis-cited, not overreach in meaning]; C-02 "algorithm changes" (0.79) and C-03 "greater resources or
  data" (0.77), both in items caught through their other sentence.
- False flags: C-05 (paraphrase with synonyms, 0.56 and 0.67), C-01 (0.73 and 0.57), I-09 (0.58), and S-03
  (0.50: a headline-only item whose text paraphrases the headline).
- Across all 28 items the replay adds 23 `unsupported_statement` flags on 17 items, so review load rises.

**Caveat.** The 0.75 threshold, the attribution-word list, and the figure rule were chosen on these same 26
items, so these numbers are optimistic. The check needs to be measured on a new run before it is trusted.
It cannot see meaning: I-11 shows a sentence that uses only source words and still overreaches. [Corrected in the next entry: I-11 is mis-cited; its words are spread across the cited chunks.]

Open: overreach in meaning (I-11-type); synonym paraphrases flagged as unsupported (C-05-type);
measurement of this check on a run it was not tuned on.

### 2026-10-12 · Diagnoses of dev-3 statement problems (owner confirmations and mis-citation search)

Terms: **mis-cited** = true, but the item cites the wrong chunk; **conflated** = true elsewhere in the
10-K but attached to a different topic; **unsupported** = in no chunk the call was allowed to cite.

**Owner confirmations and diagnoses**

| Item | Statement | Diagnosis |
|---|---|---|
| C-12 | "about 32%" | **True; mis-cited.** Owner found it in 10k-item1-p027 (Item 1, not available to the challenges call). The Risk Factors version is 10k-item1a-p053 (Class B ownership paragraph). Dev-3 cited p054. |
| I-11 | "focusing most efforts on Expedia, Hotels.com and Vrbo" | **True; mis-cited.** In 10k-item1-p013 (owner confirmed). Dev-3 cited p004 and p003. **Correction** of the earlier "overreach in meaning" note: the statement-support score was 1.00 because its words are spread across the cited chunks, not because the claim was a misreading. |
| C-04 | "add compliance costs" | **Conflated.** The wording is in the 10-K's privacy-regulation paragraph (GDPR and state privacy laws; it also contains "fines for non-compliance"), attached to short-term rental rules. Dev-2's C-04 "fines" likely has the same origin. |
| I-07 | "faster room night growth in some geographies" | **Pending owner re-check.** The mis-citation search found the wording in 10k-item7-p006. |

**The four "not found in any allowed chunk" sentences are heuristic false negatives, not fabrication.**
Each was checked against the text of its cited chunks:

| Item | Sentence | Where the support is | Why the heuristic missed it |
|---|---|---|---|
| I-04 | "Management is pursuing wider Adjusted EBITDA margins, and the latest release reports margin expansion." | "Expanded Adj. EBITDA margins y/y" in 8k-0001324424-26-000051-ex991-p001, the chunk whose citation was rejected at 5 words | Abbreviation ("Adj." vs "Adjusted"); the supporting chunk's citation was rejected |
| C-06 | "Expedia has been hurt by fraudulent bookings and chargebacks." | Cited 10k-item1a-p016: results "have been negatively affected by our acceptance of fraudulent bookings"; "charge-back" | Paraphrase ("hurt" for "negatively affected"); hyphenation ("charge-back") |
| C-11 | "The 10-K cites recent leadership transitions that can disrupt management focus and reliance on the Chairman and Chief Executive Officer." | Cited 10k-item1a-p026: "a number of leadership transitions... diverting management's attention"; the Chairman's and CEO's contributions "are critical" | Paraphrase |
| C-07 | "Expedia faces persistent cyberattacks, including AI-assisted ones, and depends on AWS availability after migrating key platform functions." | AWS half in the cited 10k-item1a-p019; the cyberattack half in 10k-item1a-p032 ("cybersecurity-related attack... is persistent"; "some actors are using AI technology to launch more sophisticated... attacks") | Compound sentence with support split across two chunks; no single chunk lifts it to the threshold |

**The 19 "found in" matches, strong vs generic** (strong = specific terms pointing to one or two allowed chunks):
- Strong (5): I-07 -> 10k-item7-p006 (and the Q2 release); C-10 -> 10k-item1a-p052 and the credit-facility 8-K;
  C-11 -> 10k-item1a-p025; C-12 -> 10k-item1a-p053; S-03 -> news-gdelt-012 (the other reorganization headline).
- Generic (14; three or more qualifying chunks, mostly common Risk Factors vocabulary): I-09 (18), C-01 (5 and 3),
  C-02 (17), C-03 (5), C-04 (69 and 8), C-05 (3 and 11; the first has fairly specific terms), C-06 (7), C-07 (7),
  C-09 (9), S-02 (10), S-04 (24).

**Takeaways.** Most of the dev-3 problems the owner checked so far are true statements attached to the wrong
chunk (C-12, I-11) or to the wrong topic (C-04), not inventions. The four "not found" cases are limits of
term matching (abbreviations, paraphrase, split support). Sentence-level citations (approved design, being
built next) target exactly this: each sentence must cite the paragraph that states it.

### 2026-10-12 · Sentence-level format, run 1: plumbing (Haiku 5.5)

Purpose: check that the new sentence format parses with the real API. Dry run $0.02 (under the $0.80 cap).
Both calls returned parsed output in the sentence format. Input 85,022 and 84,755 tokens (counted exactly);
output 2,578 and 2,328. Cost $0.0194. 20 items, 33 kept sentences, 0 dropped, 0 excluded. Flags:
11 `unsupported_title`, 8 `unsupported_statement`. Quality not evaluated on this profile.

### 2026-10-12 · Sentence-level format, run 2: dev (Sonnet 5.5), held-out test

No threshold, rule, or prompt was changed based on dev-3 before this run. Dry run $0.58 (under the $0.80 cap).
Response saved in `data/expedia/raw/llm/extract-20261012T221928Z-dev/`.

| | Result |
|---|---|
| Tokens and cost | Input 85,022 and 84,755 (counted exactly; matched billed). Output 3,684 and 4,271. Cost $0.4191 vs estimate $0.5796. |
| Initiatives | 12 proposed, 29 sentences proposed; 12 kept (all sourced), 28 sentences kept, 1 dropped, 0 excluded |
| Challenges | 11 proposed, 24 sentences proposed; 11 kept (all sourced), 23 kept, 1 dropped, 0 excluded |
| Changes | 5 proposed, 10 sentences proposed; 5 kept (2 sourced, 3 inferred headline-only), 10 kept, 0 dropped, 0 excluded |
| Totals | 63 sentences proposed, 61 kept, 2 dropped (3.2%); 0 items excluded; 2.18 kept sentences per item |
| Stop conditions | None triggered (excluded 0 of max 3; 2.18 >= 1.5 sentences per item; 3.2% <= 25% dropped) |

| Rejected citations / proposed | Haiku | Sonnet 1 | Sonnet 2 | Sonnet 3 | Sonnet 4 (sentences) |
|---|---|---|---|---|---|
| Evidence not found | 4 | 0 | 0 | 0 | 0 |
| Too long and not found | 14 | 3 | 1 | 0 | 0 |
| Too short (under 6 words) | 1 | 1 | 4 | 1 | 2 |
| Unknown chunk id | 0 | 0 | 0 | 0 | 1 |
| Total | 19 / 34 | 4 / 51 | 5 / 49 | 1 / 40 | 3 / 64 |

**Dropped sentences** (both appear to be true; dropped correctly by the rule, but false drops in substance; owner to check):
1. I-05: "The company reported expanded adjusted EBITDA margins year over year in the first and second quarters
   of 2026." Both citations were the release headline bullet "Expanded Adj. EBITDA margins y/y", which is only
   5 words as a line, inside a longer chunk, so the 6-word minimum rejects it.
2. C-02: "Search providers have added their own travel functionality, which may keep reducing traffic to the
   company's sites." The model emitted a placeholder citation (chunk id "11k-placeholder", evidence
   "placeholder"). The claim is in 10k-item1a-p011 ("Certain search providers, such as Google, have
   increasingly added their own travel search functionality...").

**Flags** (23 on 18 items): 14 `unsupported_title`, 7 `unsupported_statement`, 2 `partial_support` (I-05, C-02).
- `unsupported_statement`: 7 sentences on 7 items, all "found in" another allowed chunk (mis-cited), 0 not found.
  Four (I-02, I-03, I-05, I-07) are sentences attributed to "the Chief Executive Officer": the role words are
  missing from the cited chunks because the model replaced the person's name with the role (roles-only rule),
  so these "found in" matches are likely spurious. C-01 is flagged only because "10-K" is read as the figure
  10. S-02 (a second headline about the memo) and S-03 ("69 in millions for the six months ended June 30,
  2026"; the table row's period header is in a different chunk) are the remaining two.
- `unsupported_title`: 14 of 28 titles. Titles are abstract labels ("Intense competition", "Talent retention"),
  so this flag is mostly noise on this run.

**Content checks:** no item names a person or states career history; roles only throughout. No guidance,
financing, or analyst-rating item reached the initiatives. I-05's kept sentence ("The Chief Executive Officer
attributed first-quarter margin expansion to disciplined execution of strategic priorities") reports a result
rather than a goal; owner to judge. Headline labels: S-02, S-03, S-04 "news headline only"; C-05
"uncorroborated headline" (Risk Factors challenge).

**Compared with dev-3 (item-level citations):** unsupported_statement flags fell from 23 on 17 items to 7 on
7 items, and none is "not found". Owner review of the review sheet is needed to confirm whether statements
in the final text are now supported; dev-3's baseline was 13 of 28 items with unsupported statements.

**Changes I would propose (not made; this was the held-out run):** treat "10-K"/"10-Q" as document names, not
figures; treat role titles ("Chief Executive Officer") as attribution words in statement support; reduce or
drop `unsupported_title` (14 of 28 on abstract labels); allow citing a whole line shorter than 6 words (such as
a release headline bullet), as is already allowed for a whole chunk; reject placeholder citations with a
specific reason.

### 2026-10-12 · Reviewer findings on dev-4 (sentence format), new checks, and replay of dev-4

**Reviewer findings on dev-4** (owner confirmations recorded 2026-10-12):

| Item | Finding | Status |
|---|---|---|
| S-01 | Sentence 2 date error. The 8-K says the successor takes over on the Effective Date (May 11, 2026); the item's "as of the same date" reads as April 23. | **Confirmed error** (owner) |
| S-03 | Sentence 2: "69 in millions for the six months ended June 30, 2026". The owner confirmed the table column header for 69 is "six months ended June 30" and that the table is in millions, so the claim is accurate. The cited chunk is a header-less table row, so the period and unit are not verifiable from that chunk alone. | **Context gap, claim accurate** (reclassified from problem) |
| I-05 | The item attributes margin expansion to disciplined execution, while the cited sentence credits discipline for the highest first-quarter profitability; the margin-expansion phrase is in the preceding sentence. It is also a results statement, not an initiative. (The earlier wording "merged quote" was a loose description; the owner searched the 10-K and 8-K and found no such word, as expected.) | **Reviewer finding, not owner-confirmed; a judgment call** |
| C-06 | Hedging lost: the 10-K says "may lack"; the item says "lack". | **Confirmed error** (owner) |
| C-08 | Hedging lost: the 10-K says "further reduced"; the item says "reduced". | **Confirmed error** (owner) |

**Corrected count:** confirmed errors on dev-4 are S-01, C-06, and C-08 (**3 of 28 items**). S-03 is a
context gap with an accurate claim; I-05 is an unconfirmed judgment call.

**Dev-3 problems fixed by the sentence format:** C-04 (conflated "add compliance costs"), C-09 and C-10
(covenant attribution), C-12's "32%" (now C-11, citing 10k-item1a-p053), I-11's core-brands sentence (now
I-12, citing 10k-item1-p013), I-04's cloud sentence, and C-07's compound sentence (now split, each part
citing its own chunk).

**How the statement-support heuristic did on dev-4:** it flagged 1 of the 5 reviewer findings (S-03), and
5 of its 7 `unsupported_statement` flags were false alarms (four "Chief Executive Officer" attributions
and C-01's "10-K" read as a figure). Date errors and lost hedging are outside what term overlap can see.
**Corrected after owner confirmation:** S-03 is a context gap with an accurate claim, so the heuristic caught
**0 of the 3 confirmed errors** (S-01, C-06, C-08); its S-03 flag correctly marked a period and unit that
the cited chunk alone cannot show.

**Plan reversed:** the proposed whole-line short-evidence rule (citing a line under 6 words, such as a
release headline bullet) was **not implemented**. Its only motivating case, I-05, is a results statement,
which is not an initiative. The 6-word minimum stays.

**Changes this round** (`ea59866` checks, `835361a` prompts):
- Statement support ignores SEC form names ("10-K", "10-Q", "8-K") as figures, and treats role titles
  (Chief Executive Officer, Chief Financial Officer, Chairman, controlling shareholder, CEO, CFO) as
  attribution words.
- `unsupported_title` is no longer emitted; the title score is kept on the item and shown in the review sheet.
- Placeholder citations ("placeholder", "n/a", "tbd", "xxx" in the chunk id or evidence) are rejected as
  "placeholder citation" (real dev-4 C-02 case tested).
- The "found in" search is skipped for sentences that cite only news headlines.
- New review-only `results_review` flag on initiative sentences with "reported", "attributed", "achieved",
  "grew", or "increased" (crude keyword check, documented in the README).
- Prompt rules: keep the source's hedging words; always write dates, never "the same date", "that date",
  or "then"; an initiative states what the company is doing or intends to do, and a result or its cause is
  not an initiative. **These prompt rules affect only new model output and are untested until the next
  real run;** the replay below cannot exercise them.

**Replay of the saved dev-4 response** (no API call), compared with the original dev-4 output: 17 of 28
items changed, all in flags; provenance, labels, text, kept and dropped sentences, exclusions, and
overflow are unchanged.
- Flags before: 14 `unsupported_title`, 7 `unsupported_statement`, 2 `partial_support`.
  Flags after: 2 `unsupported_statement`, 2 `results_review`, 2 `partial_support`.
- Removed false alarms: the four "Chief Executive Officer" attributions (I-02, I-03, I-05, I-07) and C-01's
  "10-K"; all 14 `unsupported_title` flags.
- Remaining `unsupported_statement`: S-03 sentence 2 (context gap, claim accurate per owner) and S-02 sentence 2
  (now "found-in search skipped (cites only news headlines)").
- New `results_review`: I-05 sentence 1 ("attributed"; reviewer finding, a judgment call) and I-10 sentence 1
  ("EG Advertising revenue growth was attributed to...", a results statement).
- C-02's dropped sentence is now rejected as "placeholder citation" instead of "unknown chunk id".
- Not caught by any check: S-01's date error, and C-06 and C-08's lost hedging (now addressed only in the prompt).

### 2026-10-12 · Dev run 5 (Sonnet 5.5, sentence format, new prompt rules)

**Not a fully held-out test.** The prompt rules added in `835361a` (keep hedging words, write dates, results
are not initiatives) were written after reading dev-4. This run tests those rules, but the pipeline as a
whole was tuned on dev-4. No rule, prompt, or threshold was changed based on this run.

Dry run $0.58 (under the $0.80 cap). Response saved in `data/expedia/raw/llm/extract-20261012T224059Z-dev/`.

| | Result |
|---|---|
| Tokens and cost | Input 85,188 and 84,873 (counted exactly; matched billed). Output 2,949 and 5,737. Cost $0.4270 vs estimate $0.5801. |
| Initiatives | 11 proposed (25 sentences); 10 kept (all sourced), 24 sentences kept, 1 dropped; 1 excluded |
| Challenges | 10 proposed (16 sentences); 10 kept (all sourced), 16 kept, 0 dropped, 0 excluded |
| Changes | 5 proposed (8 sentences); 5 kept (2 sourced, 3 headline-only), 8 kept, 0 dropped, 0 excluded |
| Totals | 49 sentences proposed, 48 kept, 1 dropped (2.0%); 1 item excluded; 1.92 kept sentences per item |
| Stop conditions | None triggered (1 excluded; 1.92 >= 1.5; 2.0% <= 25%) |

| Rejected citations / proposed | Haiku | Sonnet 1 | Sonnet 2 | Sonnet 3 | Sonnet 4 | Sonnet 5 |
|---|---|---|---|---|---|---|
| Evidence not found | 4 | 0 | 0 | 0 | 0 | 0 |
| Too long and not found | 14 | 3 | 1 | 0 | 0 | 0 |
| Too short (under 6 words) | 1 | 1 | 4 | 1 | 2 | 1 |
| Unknown chunk id | 0 | 0 | 0 | 0 | 1 (a placeholder; before the placeholder rule) | 0 |
| Placeholder citation | 0 | 0 | 0 | 0 | 0 | 0 |
| Total | 19 / 34 | 4 / 51 | 5 / 49 | 1 / 40 | 3 / 64 | 1 / 50 |

**Dropped sentence and excluded item:** "Adjusted EBITDA margin expansion" (initiative) had one sentence: "On August
5, 2026, the company raised its full year Adjusted EBITDA margin expansion goal and reports expanding margins
year over year." Its only citation was the 5-word release bullet, so the sentence was dropped and the item
excluded. Note: the sentence describes raised guidance as a "goal", which the guidance keyword check would not
have caught; it was excluded only because its evidence was too short.

**Flags** (4): `unsupported_statement` 3, `long_quote` 1; no `results_review`.
- I-01 sentence 3 ("On August 5, 2026, the Chief Executive Officer said..."): the written date comes from the
  chunk's filing date, not its text, so "August 5" counts as missing. A side effect of the date-writing rule.
- I-03 sentence 2 ("It also plans to introduce additional products and services for B2B partners."): found in
  10k-item1-p001/p002/p004 (mis-cited, generic match).
- S-03 sentence 1: headline paraphrase; found-in search skipped.
- C-04 sentence 1: `long_quote`; it repeats the 10-K's EU Short-Term Rental Law sentence nearly verbatim.

**Checks of the new prompt rules and the dev-4 errors:**
- Dates (dev-4 S-01): fixed in this run. S-01 sentence 2: "On April 23, 2026 the company announced a successor
  as Chief Financial Officer, effective May 11, 2026." No sentence uses "the same date", "that date", or "then".
- Hedging (dev-4 C-06): kept. C-05 sentence 2: "agentic AI booking capabilities that may lack strong consent
  controls may significantly increase fraud risks." 7 of 16 challenge sentences keep a hedge word.
- Hedging (dev-4 C-08, "further reduced"): not testable; this run's supplier-compensation item (C-03) has only
  the sentence "A substantial portion of revenue comes from compensation negotiated with travel suppliers."
- Results in initiatives: no initiative sentence uses a results word; the one results-like item was excluded
  (above). I-05 now states the restructuring actions, not charges or margins.
- No item names a person or states career history in its text. No guidance, financing, or analyst-rating item
  reached the initiatives.

**Open, for owner decision (not changed):**
- Personal names appear in stored evidence phrases: S-01's verbatim evidence includes two officers' names
  ("agreed that Mr. [name] would step down...", "announced the appointment of [name]..."). Roles-only applies
  to item text; Render must decide whether evidence phrases are shown or redacted.
- Guidance written as a "goal" can evade the guidance keyword check.
- The date-writing rule causes a statement-support flag when the date comes from chunk metadata.

### 2026-10-12 · Chunk dates in statement support; run-5 known gaps; Render requirement on evidence

**Date change** (`96b9c44`): when scoring a sentence, each of its own cited chunks' filing and event dates
count as part of that chunk's text, written out ("August 5, 2026"). Only the sentence's own cited chunks'
dates count; a sentence that writes any other date is still flagged. This removes the run-5 false alarm on
I-01 sentence 3 ("On August 5, 2026, the Chief Executive Officer said..."), which wrote the release's filing
date as the prompt now asks. Tests: the real I-01 sentence against a verbatim excerpt of its chunk, and a
wrong-date version ("September 9, 2026") that is still flagged.

Replay of the saved run-5 response (no API call): only I-01 changed (sentence 3 support 78% -> 100%; its
`unsupported_statement` flag removed). Every other item's flags and support scores, the exclusion, the dropped
sentence, and the rejected citation are unchanged.

**Run-5 findings recorded as known gaps:**
- **C-08's fix not exercised.** The dev-4 error (the 10-K's "further reduced" written as "reduced") could not
  be checked: run 5's supplier-compensation item has no sentence about reductions. The hedging prompt rule is
  untested for this case.
- **Guidance relabeled as a "goal".** Run 5's excluded initiative described raised full-year Adjusted EBITDA
  margin guidance as a "goal", which the guidance keyword check does not catch. It was excluded only because
  its evidence was too short. Guidance phrased as a goal can reach the initiatives.
- **Fewer sentences per item.** Kept sentences per item fell from 2.18 (dev-4) to 1.92 (run 5); challenges
  and changes averaged 1.60. Still above the 1.5 stop threshold. The items may be thinner, possibly from the
  stricter one-claim, one-paragraph, and hedging rules; one run is not enough to tell.

**Render requirement (for the Render stage):** the rendered plan (JSON and Markdown) shows source links (title,
section, URL, dates) for each sentence and **never shows evidence phrases**. The committed stage JSON
(`data/<company>/stages/02_extract.json`) keeps the verbatim evidence phrases so citations can be verified,
and those phrases can include officers' names taken from 8-Ks (run 5's CFO change cites two). Roles-only
applies to plan text; it is enforced in rendering by not displaying evidence.

### 2026-10-12 · Owner review of the run-5 review sheet

The owner read every run-5 item against its highlighted evidence in the review sheet
(`data/expedia/raw/02_extract_review.html`).

| Check | Result |
|---|---|
| Unsupported statements | None found. **Items with an unsupported statement: 0 of 25** (dev-3: 13 of 28; dev-4: 3 of 28). |
| Lost hedges | None found. |
| Names or career history in item text | None found. Evidence phrases can contain officers' names, as logged; they are never rendered. |
| C-04 `long_quote` | **Accepted by the owner as a known limitation.** The sentence is just over the 15-word limit and the owner judged it acceptable in this case. This is an owner exception to ground rule 8, not a fix: the rule is unchanged and the code still flags such sentences. |

**Caveats:**
- The pipeline was tuned on dev-3 and dev-4 before this run.
- The run-5 prompt rules (hedging, written dates, results are not initiatives) were written after reading dev-4.
- Kept sentences per item fell from 2.18 to 1.92; fewer sentences means fewer chances to err.
- C-08's lost-hedge fix was not exercised (its sentence did not appear in run 5).

### 2026-10-12 · Final run 1 (Opus 5.5, high effort)

No rule, prompt, or threshold was changed for this run. Dry run $1.48 (under the $2.00 cap). Response saved
in `data/expedia/raw/llm/extract-20261012T225248Z-final/`. The Opus outputs (02_extract.json, the guard
report) are not committed yet, pending owner review.

| | Result |
|---|---|
| Tokens and cost | Input 85,188 and 84,873 (counted exactly; matched billed). Output 10,671 and 13,275 (about 2-3x Sonnet, mostly thinking at high effort). Cost $1.1592 vs estimate $1.4802. |
| Initiatives | 12 proposed (42 sentences); 12 kept (all sourced), 42 sentences kept; 3.50 per item |
| Challenges | 12 proposed (31 sentences); 12 kept (all sourced), 31 kept; 2.58 per item |
| Changes | 6 proposed (11 sentences); 6 kept (3 sourced, 3 headline-only), 11 kept; 1.83 per item |
| Totals | 84 sentences proposed, 84 kept, 0 dropped; 0 items excluded; **2.80 kept sentences per item** (Sonnet run 5: 1.92) |
| Stop conditions | None triggered |

| Rejected citations / proposed | Haiku | Sonnet 1 | Sonnet 2 | Sonnet 3 | Sonnet 4 | Sonnet 5 | Opus 1 |
|---|---|---|---|---|---|---|---|
| Evidence not found | 4 | 0 | 0 | 0 | 0 | 0 | 0 |
| Too long and not found | 14 | 3 | 1 | 0 | 0 | 0 | 0 |
| Too short (under 6 words) | 1 | 1 | 4 | 1 | 2 | 1 | 0 |
| Unknown chunk id | 0 | 0 | 0 | 0 | 1 | 0 | 0 |
| Placeholder citation | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| Total | 19 / 34 | 4 / 51 | 5 / 49 | 1 / 40 | 3 / 64 | 1 / 50 | **0 / 89** |

**Dropped sentences:** none.

**Flags** (4):
- I-01 sentence 3, `unsupported_statement` (70%): "In the second quarter 2026 release, the Chief Executive Officer described
  using AI to innovate faster and operate more efficiently." Missing "second", "described", "using"; found in
  other chunks (generic).
- I-03 sentence 2, `unsupported_statement` (71%): "It also plans to grow B2B by introducing additional products and
  services." Same generic mis-citation as Sonnet run 5's I-03.
- I-12 sentence 1, `long_quote`: "In 2023, trivago adapted its marketing strategy and launched a new logo and visual identity."
- C-06 sentence 1, `long_quote`: "The company is subject to the EU Short-Term Rental Law, which comes into effect in May
  2026." The same sentence the owner accepted as an exception in run 5 (C-04 there); this is a new instance and
  goes to review.

**Items compared with Sonnet run 5** (paired by kind, shared cited chunks, and title words): 24 of Opus's 30
items pair with run-5 items.
- Opus only (6): I-04 Global lodging supply expansion; I-09 Personalization and direct customer relationships;
  C-02 Intense competition from OTAs and large platforms; C-05 Travel industry disruption and macro conditions;
  C-10 Website display and fee disclosure rules; S-06 Release of subsidiary note guarantees (the dev-3 S-02 topic,
  now cited to the credit-facility 8-K).
- Sonnet run 5 only (1): C-09 Concentrated voting control.
- Weak pairings: Opus I-12 (trivago) with run-5 I-08 (advertising and media); Opus C-07 (cybersecurity and system
  availability) with run-5 C-07 (cloud platform dependence).

**Sentences of interest:**
- CFO change (S-01, event date 2026-04-17): "On April 17, 2026, the company and the Chief Financial Officer agreed he
  would step down effective May 11, 2026." / "The filing states the departure is not the result of any disagreement
  with the company." / "On April 23, 2026, the company announced a new Chief Financial Officer, succeeding in the role
  on May 11, 2026." Dates written correctly. Sentence 1 uses "he" (from the 8-K's honorific; no name).
- Supplier compensation (C-04): "A substantial portion of revenue comes from compensation negotiated with travel
  suppliers, especially lodging suppliers, airlines and GDS partners." / "The company says there is no assurance that
  travel suppliers will elect to participate in its platform." The second sentence comes from the same 10-K sentence
  as dev-4's C-08 error; it keeps the hedge ("no assurance") but omits the "further reduced" clause, so the exact
  dev-4 C-08 case is still not exercised.
- Restructuring (I-08): "In February 2024, the company committed to restructuring actions to recalibrate resources,
  which reduced headcount." / "During 2025, the company decided to expand these restructuring actions."
- Guidance-related: none. I-11's "The goal is to increase monetization and drive growth through marketing and
  product and technology investments" is a stated management goal, not guidance.
- I-08 sentence 4, "Ongoing initiatives target transactional efficiencies, particularly in payments and customer
  service": **confirmed meaning shift (a result stated as an aim).** It cites 10k-item7-p030, whose source sentence
  reads "Cost of revenue remained relatively consistent in 2025 compared to 2024, and decreased as a percentage of
  revenue during the period as ongoing initiatives continued to drive transactional efficiencies, particularly in
  payments and customer service." The owner confirmed that p030 says initiatives "continued to drive" transactional
  efficiencies, which is a result, and that the owner's earlier reasoning was based on p036 (the restructuring
  paragraph: "continue to evaluate additional cost reduction efforts"), which this sentence does not cite. No
  guard flagged it: every content word is in the cited chunk, and "target" is not a results word.

**Checks:** no "the same date", "that date", or "then"; no names or career history in item text; no guidance,
financing, or analyst-rating item in the initiatives; no initiative sentence uses a results word; 11 of 31
challenge sentences keep a hedge word.

### 2026-10-12 · Owner review of final run 1 (Opus 5.5)

Update to the final run 1 entry above. On 2026-10-12 the owner read every sentence of all 30 items against
its cited chunk. One sentence turns a result into an aim (I-08, below); the owner found no other sentence that
its cited chunk does not support.

**Items with an unsupported or meaning-shifted statement: 1 of 30 (I-08), owner-reported.**

| Run | Items with an unsupported or meaning-shifted statement |
|---|---|
| dev-3 (Sonnet, item-level citations) | 13 of 28 |
| dev-4 (Sonnet, sentence format) | 3 of 28 |
| Run 5 (Sonnet, sentence format, new prompt rules) | 0 of 25 |
| Final run 1 (Opus 5.5, high effort) | 1 of 30 |

This is the owner's first statement that every item of the Opus run was read; the final run 1 entry above
recorded the run's results only.

**I-08** ("Ongoing initiatives target transactional efficiencies, particularly in payments and customer service";
cites 10k-item7-p030): **CONFIRMED minor meaning shift (a result stated as an aim).** The chunk says ongoing
initiatives "continued to drive transactional efficiencies"; the sentence says they "target" them. Owner
decision, 2026-10-12, after reading the sentence next to its chunk. This reverses the "owner-judged supported"
status set in commit 66dfdfd and reinstates the classification from commit 7739a2a.

**Heuristic result for I-08:** the prompt rule (results are not initiatives) did not prevent it, and the
`results_review` keyword flag did not catch it (the sentence uses no flagged result word; "target" reads as an
aim). This is recorded as a known gap, not a rule to patch.

**Caveats:**
- Single reviewer, the same person who defined the rules.
- The measure is sentence-level support only. It does not measure omissions: Opus and Sonnet run 5 chose
  different items (6 Opus-only; 1 Sonnet-only: concentrated voting control), so the plan is a sample of the
  filing's risks, not an exhaustive list.
- The pipeline was tuned on dev-3, dev-4, and run 5 before this run.
- Opus used 2.80 sentences per item against Sonnet's 1.92, so more text was reviewed.

### 2026-10-12 · Personal-name scan before committing the Opus sample

A scan of the raw chunks found candidate personal names: six people (officers and directors) named in the
10-K, the 8-K Item 5.02 filing, and the earnings-release quotes. A scan of the stored GDELT headlines found
one more person, a market commentator, in `news-gdelt-015`. The names are kept only in the gitignored
`config/private_names.txt`, never in tracked files or this log.

| Where | Result |
|---|---|
| Opus final-run sample (`02_extract.json`, guard report) | **No personal names** in evidence phrases, item text (titles, sentences, validation questions), or flags. |
| Sonnet run 5's stored evidence phrases (untracked copy, never committed) | Contained two officers' names, quoted verbatim from the 8-K. |
| Tracked files | One test (the personal-name guard test) used a real surname; now synthetic. The Retrieve manifest stored the commentator's name in a headline and its URL slug. |
| Git history | The real surname is in the commit that added that test; the commentator's name is in the commits that stored the headline and, briefly, a docstring example. History was not rewritten. |
| Code that can print a name | The `personal_name` flag detail (surname, by design), the code-added headline validation question (full headline), `unsupported_statement` and dropped-sentence details (full sentence), and stored evidence (verbatim). |

**Fixes:** the test uses a synthetic name; the manifest headline is replaced with "[headline removed: names a
person]" and its URL reduced to the domain root (date, domain, eligibility, alias matches, counts, and
ranking unchanged); Retrieve now applies the same replacement after fetching, for names on the private
list; and a test fails if any listed name appears in any tracked file, reporting file and line only. On its
first run that test caught a real URL slug used as a docstring example, which was then replaced.

**The point:** the Opus sample is clean, but not by design: evidence phrases are verbatim source text and
can contain officers' names (Sonnet run 5's did). The private-names check is the safeguard, not luck.

### 2026-10-12 · Map run 1: plumbing (Haiku 5.5)

Purpose: check that the Map output format parses with the real API. Dry run: 10,313 input tokens (counted
exactly), estimate $0.003 (under the $0.15 cap). The call returned parsed output. Output 3,941 tokens; cost
$0.0030. 12 key benefits (0 full, 2 partial, 10 no_fit); 3 kept sentences, 0 dropped, 0 excluded, 0 downgraded,
0 unmapped; 12 `rationale_adds_facts` flags. Quality not evaluated on this profile.

### 2026-10-12 · Map run 2: dev (Sonnet 5.5), first real test of Map

No rule, prompt, or threshold was changed for this run. Input: the committed Opus Extract sample (12 initiatives).
Dry run: 10,313 input tokens (counted exactly), estimate $0.1006 (under the $0.80 cap). Response saved in
`data/expedia/raw/llm/map-20261012T235845Z-dev/`.

| | Result |
|---|---|
| Tokens and cost | Input 10,313 (matched the count). Output 2,483. Cost $0.0455 vs estimate $0.1006. |
| Fit distribution | **0 full, 3 partial, 9 no_fit** (red-flag threshold was fewer than 3 no_fit: not triggered) |
| Benefit sentences | 6 proposed, 6 kept, 0 dropped |
| Mappings | 0 excluded, 0 downgraded, 0 unmapped |
| Plan or price wording in output | None (benefit sentences, rationales, validation questions). 12 config lines or clauses withheld from the model. |
| Stale-verification warnings | None (config verified 2026-10-12) |
| Stop conditions | None triggered |

**Fits:**
- partial: I-01 AI across products and operations (JIRA-AI-ROVO); I-02 Unified technology platform
  (JIRA-WORKFLOWS, JIRA-PLANNING-ROADMAPS); I-08 Restructuring and cost efficiency (JIRA-AUTOMATION, JIRA-WORKFLOWS).
- no_fit: I-03 B2B segment growth, I-04 Global lodging supply expansion, I-05 One Key loyalty program, I-06 Core brand
  focus and marketing management, I-07 International market expansion, I-09 Personalization and direct customer
  relationships, I-11 Vrbo transactional model, I-12 trivago brand rejuvenation (all GAP-CUSTOMER-FACING); I-10
  Customer service technology (GAP-ITSM, GAP-CUSTOMER-FACING).

**Capabilities cited:** JIRA-WORKFLOWS (2 initiatives), JIRA-AI-ROVO, JIRA-PLANNING-ROADMAPS, JIRA-AUTOMATION (1
each). No Confluence capability was cited.

**Benefit sentences** (all verified against config claims; support 0.75-1.0 against their own claims):
- B-01 (I-01): "Rovo offers AI search across Jira, Confluence, and connected apps." / "Content can be turned into Jira
  work items with AI."
- B-02 (I-02): "Workflow schemes can be shared across multiple spaces so many teams follow one process." / "Plans
  combine work items from boards, spaces, and filters into one view across teams."
- B-08 (I-08): "Automation rules can close parent work items and auto-assign work." / "Workflow schemes let many teams
  follow one process from a single configuration."

**Flags:** 12 `rationale_adds_facts` (every mapping), no others (no `unsupported_statement`, `outcome_promise`,
`implies_customer`, or plan or price flags). The rationale flag is noise on this run: fit rationales are judgments
and necessarily use words that are in neither the initiative nor the claims ("commercial", "outside", "activity",
"capabilities"), and the model also prefixed each rationale with "Inference:", although code applies that label.
Support scores ranged from 11% to 58%.

**For owner review (not changed):** whether I-08 (restructuring and cost efficiency -> automation and workflows) is a
stretched partial fit; whether GAP-CUSTOMER-FACING is applied too broadly (8 of 9 no_fit); and whether the
`rationale_adds_facts` check should be dropped or narrowed, and the "Inference:" prefix stripped by code.

### 2026-10-12 · Map: fit-rationale check narrowed (replay of run 2, no API call)

The `rationale_adds_facts` check flagged all 12 rationales in Map run 2 (see above). It now flags a rationale only if it
adds a figure, a mid-sentence proper noun absent from its sources, the config's wording, and the company's aliases, or a
plan, tier, edition, or price term absent from the initiative's kept sentences and cited claims. The word-overlap score
stays on the review sheet, marked review-only. Code strips a leading "Inference:", and the prompt now asks the model not
to add it (the prompt change takes effect on the next real run). Replay of the saved dev response
(`map-20261012T235845Z-dev`): fits and sentences unchanged (0 full, 3 partial, 9 no_fit; 6 sentences kept), 0 flags
(was 12), no prefixes left. Overlap scores now range from 12% to 64% (was 11% to 58%; the removed prefix accounts for
the shift).

### 2026-10-12 · Map run 2 (Sonnet): owner review

| | |
|---|---|
| Tool output | 0 full, 3 partial (I-01, I-02, I-08), 9 no_fit |
| Partial fits, owner view | I-01 weak. I-02 reasonable but under-mapped. I-08 stretched; the owner would remove it. |
| no_fit, owner view | 8 of 9 agreed. Disagreement on I-03 (B2B growth). |
| Benefit sentences checked against their claims | Not reviewed (the owner did not say) |

**Partial fits:**
- **I-01 (AI across products and operations): weak.** The initiative is about developer productivity and
  customer-service speed. The cited Rovo claims cover search, summaries, and creating work items, not developer
  productivity.
- **I-02 (Unified technology platform): reasonable but under-mapped.** No Confluence claim was cited. The owner sees
  Confluence as helping teams get up to speed on the stack.
- **I-08 (Restructuring and cost efficiency): stretched.** The owner would remove it.

**I-03 (B2B growth), tool no_fit (GAP-CUSTOMER-FACING):** the owner sees a fit, because shipping products faster with
Jira and Confluence supports B2B growth. Recorded as an **owner hypothesis, not a tool result**: it is a causal outcome
claim the config does not make, and the tool must not promise outcomes.

**Owner selection** (the CSM's choice among proposed initiatives): I-01, I-02, and I-03 selected; I-08 deselected. The
Map output file is not edited to match: the selection is a separate CSM input.

**Decision:** no prompt or rule change to produce more fits, because that would tune on the reviewed run.

**Finding: Confluence under-mapping.** No Confluence capability was cited for any initiative. To be tested with a
comparison run that shows the model the config's `addresses` hints, and with a second company whose initiatives are
more about internal operations.

### 2026-10-13 · Known limitation: Extract sentences with short verbatim spans

Found while building Render's evidence checks. In the committed Opus Extract sample (`02_extract.json`), 7 kept
sentences contain their own evidence phrase word for word. Render prints sentence text (never the evidence field),
so these spans reach the plan, cited by source link. All are under 15 words, within ground rule 8; none is in
quotation marks. Extract is not changed for this; Render refuses any plan that copies 15 or more consecutive words
from a source chunk.

| Sentence | Source | Verbatim span | Sentence length |
|---|---|---|---|
| I-02 s1 | 10-K Item 1 Business | 10 words | 16 words |
| I-12 s1 | 10-K Item 7 MD&A | 12 words | 15 words |
| C-02 s2 | 10-K Item 1A Risk Factors | 11 words | 17 words |
| C-02 s3 | 10-K Item 1A Risk Factors | 9 words | 13 words |
| C-06 s1 | 10-K Item 1A Risk Factors | 11 words | 17 words |
| C-09 s2 | 10-K Item 1A Risk Factors | 10 words | 17 words |
| C-10 s1 | 10-K Item 1A Risk Factors | 12 words | 21 words |

A related leak was fixed in Render: a news citation's title is the headline, and its evidence phrase is part of that
headline, so news links now read "News article (domain)".

### 2026-10-13 · Draft run 1: plumbing (Haiku 5.5)

Purpose: check that the Draft output format parses with the real API. Input: the committed Opus Extract sample and the
committed Map output (replay of the reviewed Sonnet run); 3 fitted initiatives (I-01, I-02, I-08), 18 reference ids.
Dry run: 4,068 input tokens (counted exactly), estimate $0.0024 (under the $0.15 cap). The call returned parsed output.
Output 1,599 tokens; cost $0.0012. 6 criteria proposed, 6 kept, 0 dropped; 9 task picks, 0 rejected; 0 fallbacks.
Flags: 3 `unsupported_statement`, 2 `adds_facts`, 1 `implies_customer`. Quality not evaluated on this profile.

### 2026-10-13 · Draft run 2: dev (Sonnet 5.5), first real test of Draft

No rule, prompt, or threshold was changed for this run. Same input as run 1. Response saved in
`data/expedia/raw/llm/draft-20261013T211954Z-dev/`.

| | Result |
|---|---|
| Tokens and cost | Input 4,068 (matched the count). Output 2,127. Cost $0.0294 vs estimate $0.0681. |
| Objectives | 3 (O-01 I-01, O-02 I-02, O-03 I-08), each with 3 criteria and 3 tasks |
| Criteria | 9 proposed, 9 kept, 0 dropped; 0 rejected references; 0 CSM-blank fallbacks |
| Criterion types | Per objective: 2 product_usage (leading) and 1 business_outcome (lagging) |
| Tasks | 9 picked, 0 rejected; every objective has Day 0-30, Day 31-60, and Day 61-90 tasks |
| Flags | 3 `unsupported_statement` (O-01-C1 67%, O-01-C2 70%, O-02-C3 33%); no other flags |
| Plan, tier, price, date, customer framing | None in the objectives section or key benefits (checked on the assembled plan) |
| Stop conditions | None triggered by the Draft output |

Observations for owner review (not changed):
- The product_usage criteria tie a capability to the initiative with "supporting ..." clauses that restate the
  initiative (for example O-01-C1 "... supporting the use of AI to speed up resolution in customer service teams").
  These are the sentences the support check flagged.
- O-02-C3 ("Speed of shipping new capabilities and features on the shared platform") scored 33% against its one
  reference, which says management believes the strategy will let it ship faster.
- Task text "Run a discovery call ... with the CSM." reads oddly when the model picks CSM as the role for T-DISCOVERY:
  a catalog wording issue.
- I-08 was drafted (Map rated it partial); the owner deselected it in review, but Draft does not read selection.

**Render: refused (nothing written).** The plan assembled and passed the schema, but the new evidence check found two
runs of 15 or more consecutive words copied from source chunks, both in committed Extract sentences that Extract had
already flagged `long_quote`: I-12 s1 (the whole 15-word sentence matches the 10-K) and C-06 s1 (a 15-token run; the
check counts a hyphenated word as two words, as the long_quote logic does). Both are among the 7 sentences in the
known-limitation entry above, which measured only the evidence phrase (12 and 11 words); the sentences copy longer
runs from the same chunks, so that entry understated them. plan.md was not produced. Extract and the check were not
changed.

### 2026-10-13 · Draft run 2 (Sonnet): owner findings and the changes made after reading it

Owner findings on Draft run 2 (`draft-20261013T211954Z-dev`):
- **"supporting" clauses overreach.** Product-usage criteria tie a capability to a company goal the references do not
  connect it to. Examples: O-02-C1 ("... supporting standard architecture across travel products"), O-01-C1
  ("... supporting the use of AI to speed up resolution in customer service teams"), O-01-C2 ("... supporting faster
  operation described by leadership").
- **O-01-C3 measures customer-service resolution time,** which Map found out of scope for these products
  (GAP-CUSTOMER-FACING and GAP-ITSM on I-10). It carried the "company measure; not attributable to the product" label.
- **O-03 inherits the stretched I-08 fit** (owner review of Map run 2).
- **The baselines task was inconsistent:** only O-03 had "Agree baselines", although every criterion needs a baseline.

Changes made in response (each its own commit): Render omits any sentence with a verbatim run of 15+ words and reports
it; the Draft prompt now says a product_usage criterion states only what the product does and what is measured, with
no goal clause, and a business_outcome criterion states only the measure from its reference; a review-only
`goal_clause` flag fires on "supporting", "enabling", or "driving"; code adds the baselines task (CSM, Day 31-60) to
every objective; task text no longer names the role.

**These prompt and rule changes were made after reading run 2, so the re-run on the same Expedia input is not a fully
held-out test.** A held-out check needs a second company.

### 2026-10-13 · Draft run 3: dev (Sonnet 5.5) re-run after the changes, and the first rendered plan

Not a held-out test (see the entry above). Dry run: 4,072 input tokens (counted), estimate $0.0681 (under the $0.40
cap). Response saved in `data/expedia/raw/llm/draft-20261013T212841Z-dev/`.

| | Result |
|---|---|
| Tokens and cost | Input 4,072 (matched the count). Output 685. Cost $0.0150 vs estimate $0.0681. |
| Coverage | **The model returned criteria and tasks for I-01 only.** It proposed nothing for I-02 or I-08 (stop reason end_turn). |
| Criteria | 3 proposed, 3 kept, 0 dropped; 0 rejected references |
| Fallbacks | O-02 and O-03: CSM blank criterion and the default discovery task (4 fallbacks) |
| Flags | 2 `unsupported_statement` (O-01-C1 56%, O-01-C2 64%; the words "Track", "many" are not in the references); 0 `goal_clause` |
| Tasks | O-01: discovery (Day 0-30), demo of Rovo (Day 31-60), baselines (Day 31-60, by code), pilot of Rovo (Day 61-90). O-02 and O-03: discovery and baselines. |
| Stop conditions | None triggered: 0 of 3 dropped; no hard-drop category in the rendered plan; every objective has criteria or the blank; schema valid |

The "supporting" clauses are gone: O-01-C1 "Track how many Jira work items are created from content using AI.",
O-01-C2 "Track how many people use AI search across Jira, Confluence, and connected apps.", O-01-C3 "Track the speed of
resolution in customer service teams." (business_outcome, still the customer-service measure, labeled not
attributable). The loss of coverage for I-02 and I-08 is the main regression against run 2 (9 criteria across all 3
objectives); one run cannot say whether the prompt change caused it.

**Render: plan written** to `output/expedia/plan.json` and `plan.md` (not committed). Schema valid; evidence checks
passed against 277 source chunks; 2 sentences omitted (I-12 s1, C-06 s1). Render warnings: currency amounts in C-11
and S-05 (debt and credit-facility figures from filings, not product prices). Observations: I-12 now starts with
"This was part of a push ...", whose referent was the omitted sentence; news links read "News article (domain),
domain" because the citation's location is also the domain; news URLs still carry headline words in the slug.

### 2026-10-13 · Draft run 3 findings, render fixes, and the per-initiative output format

**Draft run 3 (`draft-20261013T212841Z-dev`):** two of three objectives (O-02 for I-02, O-03 for I-08) had no criteria
because the model skipped those initiatives: the saved response holds 3 criteria and 3 tasks, all for I-01, and no
entry of any kind for I-02 or I-08. The flat output format (one list of criteria, one of tasks) had no place to return
an empty entry or a reason, so the omission was silent. The checks dropped nothing; the blanks came from the fallback.

**Render fixes (no model call):** an omitted sentence now shows "[sentence omitted: verbatim passage from the filing]"
with its source link; a following sentence that points back at it ("This was part of a push ...", I-12 s2) and a
validation question that relies on omitted content (I-12, C-06) are replaced too, and all are listed in the render
report. Repeated citations to the same source and location show once; news links no longer repeat the domain; no_fit
benefits are one compact table; benefit headings use plain words; items to validate are grouped by section, each
question once, with unmapped initiatives under "Optional discovery for initiatives the tool did not map".

**Draft change:** one entry per initiative with criteria, tasks, and a skip_reason; the prompt requires 2 to 3
criteria per initiative including a product_usage criterion tied to a benefit line; code reports any eligible
initiative with no criteria as "initiative skipped by model".

**The prompt change was made after reading run 3, so the re-run (run 4) is not a held-out test.**

### 2026-10-13 · Draft run 4: dev (Sonnet 5.5), per-initiative format, and the rendered plan

Not a held-out test (see the entry above). Dry run: 4,332 input tokens (counted), estimate $0.0687 (under the $0.40
cap). Response saved in `data/expedia/raw/llm/draft-20261013T214056Z-dev/`.

| | Result |
|---|---|
| Tokens and cost | Input 4,332 (matched the count). Output 1,924. Cost $0.0279 vs estimate $0.0687. |
| Coverage | One entry each for I-01, I-02, I-08; 3 criteria and 3 tasks each; skip_reason empty; 0 skipped |
| Criteria | 9 proposed, 9 kept, 0 dropped; 0 rejected references; 0 fallbacks |
| Criterion types | Per objective: 2 product_usage (leading), 1 business_outcome (lagging) |
| Flags | 3 `unsupported_statement` (O-01-C1 56%, O-01-C2 64%, O-03-C1 73%; the missing words are "Track", "many", "active", ...); 0 `goal_clause` |
| Stop conditions | None: no skipped initiative, 0 of 9 dropped, no hard-drop category in the rendered plan, every objective has criteria, schema valid |

Every criterion now states only a product use or a measure ("Track how many teams follow one shared workflow scheme
across multiple spaces."). O-01-C3 still measures customer-service resolution speed (labeled not attributable), and
O-03-C3 "transactional efficiency in payments and customer service" is not something a CSM can count as written; the
generic-criterion check did not catch it.

**Render:** plan written to `output/expedia/` (not committed). Schema valid; evidence checks passed against 277 source
chunks; 3 sentences omitted (I-12 s1, I-12 s2 back-reference, C-06 s1) and 2 validation questions replaced with CSM
blanks (I-12, C-06).

**Defect found in the new grouping:** the two replaced validation questions have identical text, so "each question
once" merged them into one line, "I-12, C-06: [CSM to complete: ...]", under "Optional discovery for initiatives the
tool did not map". C-06 is a challenge, not an unmapped initiative. Not fixed yet. Also noted: catalog grammar "Does
Automation rules match how the team works today?" when a capability name is plural.

### 2026-10-13 · Final fixes before the sample, and a summary of Draft runs 1-4

**Fixes (no API call, each its own commit with tests):**
- Render: when every sentence of an item is omitted (I-12), the plan shows one line, "Source passages omitted (verbatim
  text from the filing); see source link", with the link, instead of a row of markers. CSM-blank questions are merged
  only within their own section, which fixes the run 4 defect: C-06's blank is now listed under challenges and I-12's
  under optional discovery.
- Task catalog: short display names for all 17 capabilities (for example "Rovo AI search", "shared workflow schemes",
  "cross-team plans", "automation rules"); templates avoid verb agreement ("How closely would automation rules fit the
  way the team works today?"); demo and pilot tasks name every capability mapped to the objective.
- Config: JIRA-AI-ROVO now lists the Jira product page first (it listed the Confluence AI page first, and Render links
  the first URL). No URL added or removed. JIRA-ANALYTICS-ENTERPRISE still has no Jira-specific source (only the
  Atlassian Analytics platform page). XPROD-REQUIREMENTS-TO-WORK (Jira + Confluence) lists a Confluence page first;
  left as is since it is a cross-product capability.
- Map and Draft replayed from their saved real runs (no API call) so the reordered URL reaches the plan; the replayed
  Map output differs from the committed one only in that URL order and its timestamp. Plan re-rendered: schema valid,
  evidence checks passed against 277 source chunks.

**Draft runs 1-4 (details in the entries above):**

| Run | Profile | Cost | Criteria kept / proposed | Notes |
|---|---|---|---|---|
| 1 | plumbing (Haiku) | $0.0012 | 6 / 6 | Parse check only |
| 2 | dev (Sonnet) | $0.0294 | 9 / 9 | "supporting ..." clauses overreached; baselines task inconsistent; Render refused (two 15+ word runs in Extract sentences) |
| 3 | dev (Sonnet) | $0.0150 | 3 / 3 | Model silently skipped I-02 and I-08 (flat format); two objectives fell back to CSM blanks |
| 4 | dev (Sonnet) | $0.0279 | 9 / 9 | Per-initiative format; no skips; the sample uses this run (replayed) |

Runs 3 and 4 followed prompt and rule changes made after reading earlier runs on the same input, so they are not
held-out tests.

### 2026-10-13 · I-07 (dev-3): owner re-check, superseding the pending note

This entry supersedes the "Pending owner re-check" status for dev-3 item I-07 in "Diagnoses of dev-3 statement
problems" above (the older entries are not rewritten).

**I-07** ("faster room night growth in some geographies"): Owner confirmed 2026-10-13: the phrase 'overall room
night' appears in the 10-K chunk identified by the mis-citation search (10k-item7-p006); the sentence 'faster room
night growth in some geographies' is a loose compression of the source ('faster overall room night growth', next to
a sentence about reduced economics in various geographies). Diagnosis: mis-cited (true, cited to the wrong chunks),
with some loss of wording.

### 2026-10-13 · C-04 (dev-3) owner confirmation, and who produced the dev-3 and dev-4 findings

Recorded 2026-10-13; supersedes the "owner confirmation pending" note on C-04 in "Reviewer findings on the third dev
run" (older entries are not rewritten).

**C-04** ("add compliance costs", item-level run dev-3): on 2026-10-12 the owner searched both cited chunks for
"fines" (present in neither) and supplied the privacy-regulation paragraph from the 10-K that contains the wording
("fines for non-compliance", "increased compliance costs"). **Owner confirmed the diagnosis: conflated** (true
elsewhere in the 10-K, attached to a different topic). Cross-reference: the earlier entry "Owner review of the dev-2
review sheet" records the same "fines" search on dev-2's C-04; the paragraph the owner supplied covers both runs'
wording.

**Who produced the findings.** The dev-3 and dev-4 reviewer findings in this log were produced by an AI-assisted read
of the review sheets (Claude, the coding assistant, reading the sheet). Specific items were then confirmed
individually by the owner, as recorded: on dev-3, C-12, I-11, I-07, and C-04 (4 of the 13); on dev-4, S-01, C-06, and
C-08 (all 3 confirmed errors). The other 9 dev-3 findings are the assistant's only. Run 5 and final run 1 were read in
full by the owner.

### 2026-10-13 · Correction: evidence for the dev-3 C-04 confirmation

Corrects the evidence description in the entry "C-04 (dev-3) owner confirmation, and who produced the dev-3 and dev-4
findings" above, which this entry supersedes on that point (older entries are not rewritten).

- The owner's search of both cited chunks for "fines" was on **dev-2's** C-04 only, as already recorded in "Owner
  review of the dev-2 review sheet". It was not a check of dev-3's C-04.
- For **dev-3's C-04** ("add compliance costs"), the owner's confirmation is this: the owner supplied the 10-K
  privacy-regulation paragraph containing "increased compliance costs" and "fines for non-compliance" and judged the
  sentence **conflated** (true in that paragraph, attached to short-term rental rules).

The confirmation itself stands, so the README's "4 of the 13" (C-12, I-11, I-07, C-04) is unchanged.

### 2026-10-13 · Brief: challenge grouping rule and its limits

The brief now splits Key Challenges / Potential Blockers into "Could affect this engagement" (items that keep a
validation question) and "Company context" (no question). The rule is in `config/brief_rules.yaml`: challenge
categories organizational and security_technology, and change types leadership_change, reorganization,
restructuring_layoffs, and acquisition, keep a question; anything else is context. On the Expedia sample, 7 items keep
a question (C-07, C-08, C-12, S-01, S-02, S-03, S-04) and 11 are context.

Limits, recorded so the grouping is not mistaken for a relevance judgment:
- The grouping uses Extract's `category` and `change_type` fields, which the model assigns at Extract with no check.
- C-08 (consumer booking fraud) is kept only because it shares the security_technology category with C-07; it is not
  a security requirement a work-management tool would have to meet.
- C-11 (indebtedness and covenants) is categorised "other" although it is a financing risk.
- The rule is a coarse filter, not a judgment about relevance. The CSM decides relevance; the brief's action line asks
  the CSM to move context items up if the customer treats them as concerns, and every item's question stays in
  plan.md.

### 2026-10-13 · Brief: show only what bears on the product or on buying and adoption (owner decision)

The owner decided the brief should show only items related to what the product can provide, plus change events that
could affect buying or adoption.

- The 11 context-only challenges (C-01 to C-06, C-09 to C-11, S-05, S-06) are omitted from the brief, which states the
  count and points to plan.md; they and their questions remain in plan.md and plan.json.
- The per-criterion validation questions are omitted from the brief; each criterion keeps its baseline question. The
  removed questions remain in plan.md and plan.json.
- C-08 (consumer fraud) remains in the brief because the rule uses categories (it shares security_technology with C-07).
- Weak objective-level validation questions and criterion O-03-C3 ("transactional efficiency in payments and customer
  service", not countable as written) remain until a Draft prompt change and rerun.

### 2026-10-13 · Rename: the "brief" is now the "CSM draft"

The owner decided the document is the CSM's working draft, not a summary: the CSM edits it and takes it into the
customer conversation, while the detailed plan (plan.md, plan.json) is the reference it was built from and the place
to check a claim. Renamed accordingly: `plan_brief.md` and `plan_brief.docx` are now `csm_draft.md` and
`csm_draft.docx`; `stages/brief.py` and `tests/test_brief.py` are `stages/csm_draft.py` and `tests/test_csm_draft.py`;
`config/brief_rules.yaml` is `config/csm_draft_rules.yaml`; `spg brief` is `spg csm-draft`; the render report field
`brief` is `csm_draft`. Earlier entries in this log keep the old names as written.

### 2026-10-13 · `spg run`: decisions recorded with the build

Built offline: no real API or network call was made; the end-to-end test uses a fake SEC, a fake GDELT, and a fake
model. Owner decisions applied:
- `--profile final` is Opus for Extract and the dev settings (Sonnet) for Map and Draft, defined in
  `config/models.yaml`; dev and plumbing use one model for all stages. Default profile: dev.
- No fit for any initiative is not a stop: Draft makes no model call, `04_draft.json` records no objectives with
  `no_objectives_reason`, the plan marks every initiative no fit with the "add objectives" blank, and the run exits 0.
- Extract stops when more than half of its proposed sentences are dropped. This threshold is a new proposal, not taken
  from an earlier run: the sentence-level Extract evaluation runs used a stricter stop condition (at most 25% dropped;
  see the dev-4 and run 5 entries). The message names `--from map` as the way to continue.
- plan.md and the CSM draft open with "Generated automatically from public information; not validated with the
  company; the CSM reviews and edits before use." and a review note from `data/<company>/inputs/review_note.txt`
  (default: "No human has reviewed this output."). The Expedia note says the extraction was read in full and Map,
  Draft, and the documents were not checked sentence by sentence.
- After a run, possible personal names in the stage outputs are listed (heuristic) for `config/private_names.txt`.
- Stage outputs are written atomically; seconds per stage go to the usage log and the run summary.

Not yet done: no run of `spg run` against a real company other than the committed Expedia sample.

### 2026-10-14 · Second sample: pre-screen method (recorded before any result)

Purpose: choose a second company for a sample plan. The choice is made for **fit with the product's capabilities**
(Jira and Confluence: work tracking, workflows, planning, automation, documentation), **not for output quality**; no
model stage is run in this pre-screen.

- **Candidates:** Etsy, Zillow, Uber, DoorDash.
- **Data:** `spg retrieve` for each (SEC EDGAR and GDELT only; no model call). Counts come from the saved Item 1
  (Business) and Item 7 (MD&A) text of the latest 10-K.
- **Fixed keyword list** (case-insensitive substring counts; the list is not changed after seeing results):
  "technology platform", "moderniz", "migrat", "engineering", "automat", "artificial intelligence", "developer",
  "agile", "productivity", "cloud", "integrat", "workflow".
- **Measure:** each keyword's count per 10,000 words of Items 1 and 7 combined, and the total per 10,000 words.
- **Also reported:** fiscal year end, 10-K filing date, whether the name could be ambiguous in news headlines, and the
  number of 8-Ks retrieved.
- **Limits:** keyword density is a proxy for how much the filing talks about internal technology work, not a measure
  of fit; a substring such as "integrat" also matches commercial integrations. The final choice remains the owner's.

**Results (2026-10-14; `spg retrieve --as-of 2026-10-14`, no model call).** Keyword counts per 10,000 words of Items 1
and 7 combined, using the fixed list above:

| Company | Words (Items 1+7) | technology platform | moderniz | migrat | engineering | automat | artificial intelligence | developer | agile | productivity | cloud | integrat | workflow | Total per 10k |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Etsy | 15,874 | 0.6 | 0.0 | 0.0 | 6.9 | 0.6 | 0.0 | 0.0 | 0.0 | 0.0 | 6.9 | 4.4 | 0.0 | **19.5** |
| Zillow | 15,413 | 0.6 | 0.6 | 0.6 | 0.0 | 1.3 | 0.0 | 0.6 | 0.0 | 0.0 | 0.6 | 7.8 | 0.0 | **12.3** |
| Uber | 16,957 | 1.8 | 0.6 | 0.0 | 1.2 | 0.6 | 0.0 | 0.0 | 0.0 | 0.6 | 3.5 | 0.0 | 0.0 | **8.3** |
| DoorDash | 12,971 | 1.5 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 3.9 | 0.0 | **5.4** |

| Company | Fiscal year end | 10-K filed | Name ambiguous in headlines? | 8-Ks retrieved | GDELT |
|---|---|---|---|---|---|
| Etsy | 2025-12-31 (cover page) | not recorded (Retrieve stopped); the 10-K is signed 2026-02-19 | Unlikely (a distinctive name) | not retrieved | not reached |
| Zillow | 2025-12-31 | 2026-02-11 | Unlikely; resolved to Zillow Group (ticker ZG) | 5 (3 events, 2 earnings releases) | failed (HTTP 429 after three retries) |
| Uber | 2025-12-31 | 2026-02-13 | Yes, possibly: "uber" is also an English and German word; 7 of 50 headlines named neither the company nor an alias | 6 (4 events, 2 earnings releases) | ok (16 of 50 eligible) |
| DoorDash | 2025-12-31 | 2026-02-18 | Unlikely | 3 (1 event, 2 earnings releases) | ok (29 of 50 eligible) |

Notes:
- **Etsy: Retrieve stopped at the fiscal-year check.** The cover page reads "For the fiscal year ended | December 31 ,
  2025": the date is in a separate table cell, and the cover-page pattern does not allow the "|" cell separator. Its
  keyword counts above come from the cached 10-K, split with the same section splitter. Not fixed in this step; the
  proposed fix is to allow "|" between "ended" and the date in that one pattern, with a test using this cover line.
- **Zillow:** GDELT was rate-limited, so a plan would rest on 8-Ks and the 10-K only unless Retrieve is re-run later.
- **DoorDash:** one GDELT headline named a person on the private list and was redacted by Retrieve; a 10-K/A filed
  2026-05-06 amends the 10-K and was not used.
- None of the four has an entry in `config/company_aliases.yaml`, so headline matching used the name and ticker only.
- "integrat" mostly counts commercial integrations (partner and product integrations), and "engineering" and "cloud"
  can describe cost lines; the density is a proxy, as stated above. No candidate uses "workflow", "agile", or
  "artificial intelligence" in Items 1 and 7.

### 2026-10-14 · Addendum: section check for the first pre-screen

Checked after the results above (the earlier entry is not edited). For each 10-K, every Item heading was located in
the raw filing text, and the span the splitter uses for Item 1 and Item 7 was compared with the next heading.

| Company | Item 1 words | Item 1 ends at | Item 7 words | Item 7 ends at | Truncated? |
|---|---|---|---|---|---|
| Etsy | 9,481 | Item 1A heading ("Item 1A. Risk Factors.") | 6,393 | Item 7A heading | No |
| Zillow | 5,274 | Item 1A heading | 10,139 | Item 7A heading | No |
| Uber | 4,498 | Item 1A heading ("ITEM 1A. RISK FACTORS") | 12,459 | Item 7A heading | No |
| DoorDash | 2,569 | Item 1A heading | 10,402 | Item 7A heading | No |

In every case the section ends exactly at the next Item's body heading, with no other heading-like line inside the
span. Uber's and DoorDash's Item 1 are short because the filings are: the tables of contents put Item 1 on pages 4-9
(Uber) and 6-10 (DoorDash), and both sections end with the usual website and investor-relations paragraphs. Etsy's
figures come from the cached 10-K (its Retrieve stopped at the cover-page check).

**Supplementary counts, not used for ranking** (Items 1 and 7; "AI" whole-word and case-sensitive; "machine
learning" whole-phrase, any case):

| Company | AI per 10k | machine learning per 10k |
|---|---|---|
| Etsy | 7.6 (12) | 1.9 (3) |
| Zillow | 4.5 (7) | 0.6 (1) |
| Uber | 0.0 (0) | 0.0 (0) |
| DoorDash | 0.0 (0) | 0.0 (0) |

### 2026-10-14 · Fix found by the pre-screen: cover-page date split across table cells

The first pre-screen found that Retrieve stopped on Etsy's FY2025 10-K because the cover page reads "For the fiscal
year ended | December 31 , 2025" (the date in its own table cell). The cover-page pattern now allows "|" between
"ended" and the date and inside the date (commit "Retrieve: cover-page fiscal year can be split by "|" across table
cells"), with regression tests on synthetic cover text. The fiscal-year check itself is unchanged: the cover date must
still equal EDGAR's report period. Etsy's Retrieve has not been re-run yet.

### 2026-10-14 · Second pre-screen: method (recorded before any retrieval)

- **Candidates:** Cigna Group, CVS Health, ExxonMobil, Wells Fargo, Ford Motor Company, McDonald's, Etsy.
- **Why they were chosen:** large US 10-K filers with December fiscal years and sizeable IT and engineering
  organizations, drawn from third-party lists of companies reported to use Jira (unverified estimates, not used by the
  tool and not a statement about any company's customer status), filtered to exclude foreign filers, private companies,
  IT services partners, and non-December fiscal years. The first list (above) was chosen for name clarity, not fit.
  **Etsy is carried over from the first list** so that both lists are compared under the same keyword list; it can win
  under the choice rule.
- **Fixed keyword list** (case-insensitive substring counts unless stated; not changed after seeing results). It
  differs from the first list because that list over-counted commercial "integrat" mentions: "digital
  transformation", "legacy", "moderniz", "migrat", "agile", "automat", "artificial intelligence", "machine learning",
  "developer", "engineering", "productivity", "operating model", "simplif", "cloud", "workflow", "technology
  investment"; plus whole-word, case-sensitive "AI".
- **Measure:** each keyword's count per 10,000 words of Items 1 and 7 combined, and the total per 10,000 words.
- **Exclusions from ranking:**
  - a company whose name is ambiguous in headlines: more than 10% of the first 50 retrieved headlines do not name the
    company; if GDELT stays throttled, ambiguity is "not assessed" and the company stays in the ranking, flagged;
  - a company whose Item 1 or Item 7 is a stub: under 2,000 words, or text saying it is incorporated by reference;
  - a company where Retrieve fails.
- **Choice rule:** the highest total per 10,000 words among the companies not excluded. The pre-screen states which
  company the rule selects; it does not run it.
- **Procedure:** `spg retrieve` for each candidate (SEC EDGAR and GDELT only, no model call); if GDELT stays throttled
  after its retries, Retrieve continues without news. The step A section check (each Item 1 and Item 7 ends at the next
  Item's body heading) is applied to each candidate.

### 2026-10-14 · Second pre-screen: results and the company the choice rule selects

`spg retrieve --as-of 2026-10-14` for each candidate (no model call). Counts per 10,000 words of Items 1 and 7, fixed
second keyword list (case-insensitive substrings; "AI" whole-word and case-sensitive):

| Company | Words (Items 1+7) | digital transformation | legacy | moderniz | migrat | agile | automat | artificial intelligence | machine learning | developer | engineering | productivity | operating model | simplif | cloud | workflow | technology investment | AI | Total per 10k |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Cigna Group | 23,595 | 0.0 | 0.0 | 0.4 | 0.0 | 0.0 | 0.4 | 0.8 | 0.4 | 0.0 | 0.0 | 0.0 | 0.4 | 0.4 | 0.0 | 0.0 | 0.0 | 10.2 | **13.1** |
| CVS Health | 39,843 | 0.0 | 1.3 | 0.5 | 0.0 | 0.0 | 1.5 | 1.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 2.3 | 0.5 | 1.3 | 0.0 | 1.8 | **10.0** |
| Etsy | 15,874 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.6 | 0.0 | 1.9 | 0.0 | 6.9 | 0.0 | 1.3 | 0.0 | 6.9 | 0.0 | 0.6 | 7.6 | **25.8** |

| Company | Fiscal year end | 10-K filed | 10-K/A | Headline ambiguity | 8-Ks retrieved | Section check | Ranking |
|---|---|---|---|---|---|---|---|
| Cigna Group | 2025-12-31 | 2026-02-26 | none | not assessed (GDELT HTTP 429 after retries) | 5 (3 events, 2 earnings) | Item 1 12,858 and Item 7 10,737 words; each ends at the next Item heading | ranked, flagged |
| CVS Health | 2025-12-31 | 2026-02-10 | none | 0 of 50 headlines (0%) do not name the company | 5 (3 events, 2 earnings) | Item 1 23,752 and Item 7 16,091 words; each ends at the next Item heading | ranked |
| Etsy | 2025-12-31 | 2026-02-19 | none | not assessed (GDELT HTTP 429 after retries) | 8 (6 events, 2 earnings) | Item 1 9,481 and Item 7 6,393 words; each ends at the next Item heading | ranked, flagged |
| ExxonMobil | - | - | - | - | - | - | **excluded: Retrieve failed** |
| Wells Fargo | - | - | - | - | - | - | **excluded: Retrieve failed (stub)** |
| Ford Motor Company | - | - | - | - | - | - | **excluded: Retrieve failed** |
| McDonald's | - | - | - | - | - | - | **excluded: Retrieve failed** |

Why Retrieve failed (diagnosed from cached files and SEC data; nothing was changed):
- **ExxonMobil:** "No 10-K found". SEC's ticker list now maps both the name and the ticker XOM to a newly registered
  "ExxonMobil Holdings Corp" (CIK 2115436), which has no 10-K. The FY2025 10-K (filed 2026-02-18) is under Exxon Mobil
  Corp (CIK 34088), which the ticker list no longer maps; Retrieve has no option to choose a CIK.
- **Wells Fargo:** Item 1A has 39 words and Item 7 has 29: the 10-K says the information is in the 2025 Annual Report
  to Shareholders and "is incorporated into this item by reference". A stub under the method's rule. The tool's
  incorporated-by-reference check matches "incorporated (herein) by reference", not this wording.
- **Ford Motor Company:** "Item 1. Business: found only 844 words". Ford repeats "Item 1. Business (Continued)" (and
  likewise for Items 1A and 7) at the top of each page; the splitter treats each repeat as a new heading and keeps the
  longest single page. A splitter limitation, not a short filing.
- **McDonald's:** the name with an apostrophe matched no registrant; with `--ticker MCD` it resolved to McDonald's
  Corp, but the splitter found 0 words for Item 1. The 10-K's body uses its own section titles ("Business Summary",
  "Description of the Business") and lists the Item numbers only in a cross-reference index near the end.

Other warnings: no candidate has a `config/company_aliases.yaml` entry (headline matching used the name and ticker);
CVS Health's GDELT results included one headline naming a person on the private list, which Retrieve redacted.

**Choice rule result: Etsy** (25.8 per 10,000 words, the highest among the three companies not excluded). Etsy is
flagged: its headline ambiguity is not assessed because GDELT stayed rate-limited. Caveat for the owner: most of
Etsy's total comes from "engineering" (11), "cloud" (11), and "AI" (12); in a 10-K, "engineering" and "cloud" can
describe cost lines rather than initiatives, as noted for the first pre-screen. Not run.

### 2026-10-14 · Etsy: expectations and review standard (recorded before any Etsy run)

Etsy is the company the second pre-screen's choice rule selected. Recorded before running any model stage on it:

- **This is the first run on a company the pipeline was not tuned on.** Every prompt and rule change so far was made
  after reading Expedia runs.
- **Expected issue: Depop staleness.** Item 1 of Etsy's FY2025 10-K (filed 2026-02-19) describes Depop as part of
  Etsy, with the sale to eBay pending (expected to close in the second quarter of 2026). The sale completed on
  2026-07-30 (Form 8-K Item 2.01, filed 2026-07-30). Any Etsy sentence about Depop must be checked for staleness: an
  initiative, challenge, benefit, or criterion that treats Depop as a current business is a finding.
- **Review standard:** the owner will read every Extract item against its cited passage, as for the Opus run. The
  count of items with an unsupported or meaning-shifted statement will be recorded here, whatever it is.
- **Drop rate:** the share of proposed sentences dropped by the guards will be recorded. `spg run` stops above 50%
  (a new threshold); the Expedia sentence-level evaluation runs used a stricter stop condition of at most 25% dropped,
  and the result will be compared with that too.
- **Pipeline changes since the Expedia sample that could affect the result:** the cover-page fix in Retrieve (the
  fiscal-year date split across table cells, found by this pre-screen), and the `spg run` changes (orchestration,
  cost gate, resume, stop conditions, the zero-fit path, atomic writes, the Retrieve line in the summary, the dry-run
  as-of notice, and `--require-news`).
- **Aliases:** `config/company_aliases.yaml` has an Etsy entry with no brand aliases (Depop, Reverb, and Elo7 are
  sold; Etsy Ads, Etsy Insider, and the Etsy app are already matched by "Etsy"), so headline matching uses the name and
  ticker only.
- **Dates:** every Etsy command uses `--as-of 2026-10-14`.

### 2026-10-15 · Etsy: run without news (decided before any Etsy run)

GDELT returned HTTP 429 on every attempt from 2026-10-14 to 2026-10-15, including `--require-news` retries and manual
checks (owner-reported; the 2026-10-14 pre-screen runs in this log also show GDELT throttled or failing for Etsy).

Decision: the Etsy sample will be run with `--skip-news`, using the 10-K and 8-Ks only. It is therefore less
comparable to the Expedia sample, whose extraction also drew on news headlines. This was decided before the run and
does not depend on any result. The README will say that the Etsy sample has no news.

### 2026-10-15 · Etsy: first held-out run stopped at the CSM draft check

First held-out Etsy run: `spg run "Etsy" --skip-news --profile final --as-of 2026-10-15` (Opus for Extract, Sonnet
for Map and Draft).

- **Retrieve, Extract, Map, and Draft completed.** Extract: 0 of 94 proposed sentences dropped (12 initiatives, 12
  challenges, 7 changes). Map: 2 of 12 initiatives with a full or partial fit (both partial). Draft: 2 objectives
  (O-01 for I-07, O-02 for I-12), 6 criteria.
- **Render stopped at the CSM draft check** because two Draft questions for criterion O-02-C2 imply the company is a
  customer ("your Atlassian Cloud apps"):
  - baseline question: "How many people today query data from your Atlassian Cloud apps, and with what tools?"
  - validation question: "Would querying data from your Atlassian Cloud apps be useful to your product, engineering,
    and operations teams?"
- The Draft guard flagged both as `implies_customer` in `04_draft_guard_report.json` (a review flag, so Draft kept
  them).
- **No CSM draft was written.** Render had already written `output/etsy/plan.json` and `plan.md` before the CSM draft
  check, and kept them (the stop message says so); `plan.md` contains the same two questions, because the plan-wide
  check treats customer framing as a warning, not a stop. These outputs are not committed.
- **Next step:** re-run the Draft stage only, with no code or prompt change. The result will be logged either way.
