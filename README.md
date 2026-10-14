# AI-Powered Customer Success Plan Generator

An experimental CLI that turns public company information into a structured first draft of a customer success plan.

## Why this matters for Customer Success

Preparing for strategic customer conversations often requires CSMs to research company priorities, connect them to product capabilities, and develop relevant discovery questions and success measures.

This prototype explores whether AI can help create that initial draft from public information, giving the CSM a structured starting point for research and discovery. It is designed to support preparation, not replace customer discovery or validate business outcomes automatically.

> All inputs are publicly available. This tool does not use proprietary or customer data. This output is a
> hypothesis generated from public information and has not been validated with the company.

## Example output (Expedia and Atlassian Jira and Confluence)

- [`output/expedia/plan_brief.docx`](output/expedia/plan_brief.docx): the short brief a CSM reads before a call, in Word.
- [`output/expedia/plan_brief.md`](output/expedia/plan_brief.md): the same brief in Markdown.
- [`output/expedia/plan.md`](output/expedia/plan.md): the detailed plan, with every sentence, source link, and label.

The sample is unedited tool output. What it gets wrong is recorded in [docs/evaluation.md](docs/evaluation.md).

## How it works

The pipeline has five stages:

1. **Retrieve:** collect public company filings (10-K and 8-K), earnings releases, and eligible news headlines.
2. **Extract:** identify potential business initiatives, challenges, and leadership or structural changes, with a
   source citation on every sentence.
3. **Map:** evaluate whether each initiative aligns with configured product capabilities, including partial and no-fit
   cases.
4. **Draft:** generate proposed objectives, success criteria, and tasks with relative timelines.
5. **Render:** assemble the detailed plan and the brief, surface validation questions, and apply final checks. No model
   call.

The model proposes, code verifies: each stage's output passes deterministic checks before the next stage uses it.
Details: [docs/how_it_works.md](docs/how_it_works.md).

## Design principles

- **Citations are verified by code.** Code checks that every cited passage exists and contains the quoted evidence;
  a sentence whose citations fail does not reach the plan.
- **Flags are backstops, not proof.** Heuristic flags (unsupported statement, partial support, and others) mark
  sentences for human review. One meaning-shift error (I-08) passed every automated check.
- **No prices, ever; roles, not personal names.** Prices, discounts, contract terms, and plan tiers never appear in a
  plan. People are referred to by role.
- **Honest product fit.** Initiatives that do not align with the configured capabilities are marked no fit with a
  reason, not forced into a recommendation.
- **CSM-owned decisions.** Baselines, targets, plan availability, renewal, and account history are labeled blanks for
  the CSM; generated plans are hypotheses for CSM review and customer validation.

## Evaluation

One public company and one product family. Items with at least one unsupported or meaning-shifted statement, in the
Expedia extraction runs:

| Run | Items with a problem |
|---|---|
| Item-level citations (Sonnet) | 13 of 28 |
| First sentence-level run (Sonnet) | 3 of 28 |
| Sentence-level, after prompt fixes (Sonnet) | 0 of 25 |
| Final run (Opus) | 1 of 30 |

The first two counts were an AI-assisted read with some findings confirmed by the owner; the last two are the owner's
full read. None are held-out results: the pipeline was tuned on the earlier runs. Methodology, who confirmed what, and
observed failures: [docs/evaluation.md](docs/evaluation.md) and [docs/evaluation_log.md](docs/evaluation_log.md).

## Known limitations

- One company (Expedia) and one product (Jira and Confluence) so far.
- Some success criteria have weak or unitless measures.
- Some objective-level questions drift away from what the product does.
- The brief's challenge grouping is coarse (by Extract's category fields).
- Semantic errors, such as a result restated as an aim, can pass the automated checks.

More: [docs/known_limitations.md](docs/known_limitations.md).

## Cost

The Expedia sample cost $1.23 in model calls (from `data/expedia/stages/usage_log.jsonl`): extraction on Opus $1.159,
mapping $0.045, drafting $0.028. Logged runs by profile:

| Stage | plumbing (Haiku) | dev (Sonnet) | final (Opus) |
|---|---|---|---|
| Extract | $0.019 | $0.40-0.43 | $1.16 |
| Map | $0.003 | $0.045 | not run |
| Draft | $0.001 | $0.015-0.029 | not run |

Every model-calling stage supports `--dry-run`, and a run aborts before any call if its estimate exceeds
`max_cost_usd_per_run` in `config/models.yaml`.

## Setup and usage

Requirements: [uv](https://docs.astral.sh/uv/getting-started/installation/) (it installs Python 3.12) and, from Extract
onward, an Anthropic API key.

```bash
uv sync                       # creates .venv with Python 3.12 and the dependencies
cp .env.example .env          # .env is gitignored; never commit it
```

In `.env`, set `SEC_USER_AGENT="Your Name you@example.com"` (required by SEC EDGAR) and `ANTHROPIC_API_KEY=...`
(needed from Extract onward; without it, `--dry-run` still estimates cost and Render needs no key).

```bash
uv run spg retrieve --company "Expedia" --as-of 2026-10-12   # Stage 1
uv run spg extract --company "Expedia" --dry-run             # Stage 2 estimate, no API call
uv run spg extract --company "Expedia" --profile dev         # profiles: plumbing, dev, final
uv run spg map --company "Expedia" --profile dev             # Stage 3 (also: replay-map)
uv run spg draft --company "Expedia" --profile dev           # Stage 4 (also: replay-draft)
uv run spg render --company "Expedia"                        # Stage 5: plan.json, plan.md, and the brief
uv run spg brief --company "Expedia"                         # rebuild only the brief from plan.json
uv run pytest                                                # offline tests (no network, no API calls)
```

## Scope

An independent portfolio project using public information only. It does not use proprietary customer data and is not
affiliated with or endorsed by Atlassian or Expedia. The generated plan is unedited tool output and a starting point for
research and discovery, not a finished customer success plan. Designed and reviewed by the author; code written with
Claude Code. Project rules are in [CLAUDE.md](CLAUDE.md). Licence: MIT (see [LICENSE](LICENSE)).
