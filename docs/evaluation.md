# Evaluation

The full write-up behind the README's evaluation table. Every figure here comes from
[evaluation_log.md](evaluation_log.md) (findings, confirmations, run details) or
`data/expedia/stages/usage_log.jsonl` (cost).

## What was measured (one company)

Each Expedia extraction run was checked against its cited sources in a review sheet, counting items with at least one
unsupported or meaning-shifted statement.

| Run | Items with a problem |
|---|---|
| Item-level citations (Sonnet) | 13 of 28 |
| First sentence-level run (Sonnet) | 3 of 28 |
| Sentence-level, after prompt fixes (Sonnet) | 0 of 25 |
| Final run (Opus) | 1 of 30 |

**Who read what.** The first two counts came from an AI-assisted read of the review sheet (the coding assistant,
Claude, reading the sheet), with some findings then confirmed individually by the owner: on the item-level run, 4 of
the 13 (C-12, I-11, I-07, C-04); on the first sentence-level run, all 3 errors. The other 9 item-level findings are the
assistant's only. The owner read the last two runs (0 of 25 and 1 of 30) in full.

**Caveats.**
- The owner wrote the rules and did the confirmations.
- The pipeline was tuned on the earlier runs, so these are not held-out results. Prompt and rule changes were made
  after reading Expedia runs, so later runs on the same input are not held-out tests.
- The measure covers sentence-level support, not omissions (two models chose different risks from the same filing).
- Three of the 13 item-level problems (C-12, I-11, I-07) were true statements cited to the wrong passage (I-07 also lost
  some wording in compression).
- Single-reviewer evaluation: the owner's judgment, with no second reviewer or inter-rater check.
- One company (Expedia) and one product family (Jira and Confluence).
- One model family (Claude): Haiku for plumbing, Sonnet for development, Opus for the final extraction.
- Support checks are sentence-level word overlap: they flag added facts and figures, not omissions. A sentence can pass
  while leaving out a qualifier that changes its meaning (I-08 below).

## What the automated checks missed

On the first sentence-level run, the word-overlap checks flagged none of the three confirmed errors (a wrong date and
two lost hedges). In the final run, a sentence turned "initiatives continued to drive efficiencies" into "initiatives
target efficiencies", and every automated check passed it. A human reading the sentence beside its source found it.

## A failure of the owner's own safeguard

A check meant to decide whether a news headline was backed by a filing matched the figure 58 in a headline about 58
layoffs to "(58)" in unrelated rows of an earnings-release table (legal reserves and interest expense). The claim was
labeled filing-backed when it was not. The owner found it by reading the cited filing passage, and the rule was
replaced with one that requires a shared named entity and ignores table rows.

## The Expedia sample

[`output/expedia/plan.md`](../output/expedia/plan.md) (and `plan.json`) is **unedited tool output: a first-draft
hypothesis** for a CSM to review and edit with the company. Nothing in it was changed by hand, and the owner's review
notes stay in [evaluation_log.md](evaluation_log.md), not in the plan. The brief
([`plan_brief.md`](../output/expedia/plan_brief.md), [`plan_brief.docx`](../output/expedia/plan_brief.docx)) is built
from the same plan.json.

- **I-08 (Restructuring and cost efficiency) is drafted** because Map rated it a partial fit, although the owner judged
  that fit stretched. Its Extract sentence "Ongoing initiatives target transactional efficiencies, particularly in
  payments and customer service" turns a result the filing reports into an aim; this is logged, and the sample is not
  edited to hide it.
- **Three sentences are omitted** from the sample: two for verbatim runs of 15 or more words from the filing (I-12 s1,
  C-06 s1) and one that points back at an omitted sentence (I-12 s2). Two validation questions that relied on them are
  CSM blanks. See `output/expedia/render_report.json`.
- **Map is a replay of a saved real run** (`map-20261012T235845Z-dev`, Sonnet): the guards were re-applied to the saved
  response with no API call. Draft is likewise a replay of its saved real run (`draft-20261013T214056Z-dev`). The plan's
  metadata names both runs.
- **Nine of twelve initiatives are no_fit by design.** Most of Expedia's initiatives are commercial or customer-facing
  (supply, loyalty, marketing, personalization), which Jira and Confluence do not do. They are listed with their reasons
  and gap ids, and get no objective.

## Cost of the sample

From `data/expedia/stages/usage_log.jsonl`; Anthropic API prices as of 2026-10-06.

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
