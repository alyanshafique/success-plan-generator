# AI-Powered Customer Success Plan Generator

[![pipeline](https://gitlab.com/alyanshafique/success-plan-generator/badges/main/pipeline.svg)](https://gitlab.com/alyanshafique/success-plan-generator/-/pipelines) [![coverage](https://gitlab.com/alyanshafique/success-plan-generator/badges/main/coverage.svg)](https://gitlab.com/alyanshafique/success-plan-generator/-/pipelines)

An experimental CLI that turns public company information into a structured first draft of a customer success plan.

## Why this matters for Customer Success

Preparing for strategic customer conversations often requires CSMs to research company priorities, connect them to product capabilities, and develop relevant discovery questions and success measures.

This prototype explores whether AI can help create that initial draft from public information, giving the CSM a structured starting point for research and discovery. It is designed to support preparation, not replace customer discovery or validate business outcomes automatically.

> All inputs are publicly available. This tool does not use proprietary or customer data. This output is a
> hypothesis generated from public information and has not been validated with the company.

## Example output (Expedia and Atlassian Jira and Confluence)

- [`output/expedia/csm_draft.docx`](output/expedia/csm_draft.docx) (Word) and
  [`output/expedia/csm_draft.md`](output/expedia/csm_draft.md) (Markdown): the working draft the CSM edits and takes
  into the customer conversation. It has no source links; it carries a CSM action under each section and the discovery
  questions next to what they test.
- [`output/expedia/plan.md`](output/expedia/plan.md) and [`output/expedia/plan.json`](output/expedia/plan.json): the
  detailed plan: every sentence with its source links and labels. It is the reference the draft was built from and the
  place to check a claim; the CSM does not edit it.

The sample is unedited tool output. What it gets wrong is recorded in [docs/evaluation.md](docs/evaluation.md).

## How it works

The pipeline has five stages:

1. **Retrieve:** collect public company filings (10-K and 8-K), earnings releases, and eligible news headlines.
2. **Extract:** identify potential business initiatives, challenges, and leadership or structural changes, with a
   source citation on every sentence.
3. **Map:** evaluate whether each initiative aligns with configured product capabilities, including partial and no-fit
   cases.
4. **Draft:** generate proposed objectives, success criteria, and tasks with relative timelines.
5. **Render:** assemble both the detailed plan and the CSM draft, surface validation questions, and apply final checks.
   No model call.

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
full read. None are held-out results: the pipeline was tuned on the earlier runs. A second, held-out sample (Etsy,
chosen by the pre-registered rule in [docs/evaluation_log.md](docs/evaluation_log.md)) is in progress and will be added
with its error count, whatever it is. Methodology, who confirmed what, and observed failures:
[docs/evaluation.md](docs/evaluation.md) and [docs/evaluation_log.md](docs/evaluation_log.md).

## Known limitations

- One company (Expedia) and one product (Jira and Confluence) so far.
- Some success criteria have weak or unitless measures.
- Some objective-level questions drift away from what the product does.
- The CSM draft's challenge grouping is coarse (by Extract's category fields).
- Semantic errors, such as a result restated as an aim, can pass the automated checks.

More: [docs/known_limitations.md](docs/known_limitations.md).

## Cost

The Expedia sample cost $1.23 in model calls (from `data/expedia/stages/usage_log.jsonl`): extraction on Opus $1.159,
mapping $0.045, drafting $0.028. Logged runs by profile:

| Stage | plumbing (Haiku) | dev (Sonnet) | final (Opus for Extract, Sonnet for Map and Draft) |
|---|---|---|---|
| Extract | $0.019 | $0.40-0.43 | $1.16 |
| Map | $0.003 | $0.045 | same as dev |
| Draft | $0.001 | $0.015-0.029 | same as dev |

Every model-calling stage supports `--dry-run`, and a run aborts before any call if its estimate exceeds
`max_cost_usd_per_run` in `config/models.yaml` (for `spg run`, the whole run's estimate; see below).

## CI/CD

GitLab CI runs on every push and merge request: lint, 430+ offline tests with coverage, SAST, secret detection, a dependency audit, and an offline render of the Expedia sample. A weekly schedule checks the live EDGAR/GDELT sources, and a monthly schedule runs a full model pipeline capped at $2. See .gitlab-ci.yml.

## Setup and usage

Requirements: [uv](https://docs.astral.sh/uv/getting-started/installation/) (it installs Python 3.12) and, from Extract
onward, an Anthropic API key.

```bash
uv sync                       # creates .venv with Python 3.12 and the dependencies
cp .env.example .env          # .env is gitignored; never commit it
```

In `.env`, set `SEC_USER_AGENT="Your Name you@example.com"` (required by SEC EDGAR) and `ANTHROPIC_API_KEY=...`
(needed from Extract onward; without it, `--dry-run` still estimates cost and Render needs no key).

**One command for a whole plan:**

```bash
uv run spg run "<company name>" --dry-run                # Retrieve, then the whole-run cost estimate; no model call
uv run spg run "<company name>"                          # dev profile; shows the estimate and asks before spending
uv run spg run "<company name>" --profile final --yes    # Opus for Extract, Sonnet for Map and Draft; no prompt
uv run spg run "<company name>" --ticker <TICKER>        # when the name matches several SEC registrants
uv run spg run "<company name>" --from map               # re-run Map, Draft and Render from the saved Extract output
uv run spg run "<company name>" --force                  # re-run every stage and replace existing outputs
```

`spg run` writes `output/<company>/plan.md`, `plan.json`, `csm_draft.md` and `csm_draft.docx`. Before any model call it
checks the keys and the product config (`--product`, default `atlassian_jira_confluence`), runs Retrieve, and compares
a whole-run estimate with the run limit (`--max-cost`, default `max_cost_usd_per_run`); over the limit it stops, and
otherwise it asks unless `--yes`. It resumes from saved stage outputs, and it does not replace existing outputs without
`--force` (`--dry-run` is always allowed). It stops with a message when Retrieve finds no 10-K or cannot confirm the
fiscal year, when Extract keeps no initiatives or drops more than half of its proposed sentences, or when Draft writes
no criteria. If no initiative fits the product, it writes a plan with every initiative marked no fit and exits
normally. Afterwards it lists possible personal names found in the stage outputs, to add to
`config/private_names.txt` before committing anything. Seconds and cost per stage go to the usage log. Other options:
`--as-of`, `--refresh-sources` (re-download EDGAR and GDELT), `--skip-news` (no GDELT), `--require-news` (stop before any model call if GDELT returns no headlines).

**One stage at a time:**

```bash
uv run spg retrieve --company "Expedia" --as-of 2026-10-12   # Stage 1
uv run spg extract --company "Expedia" --dry-run             # Stage 2 estimate, no API call
uv run spg extract --company "Expedia" --profile dev         # profiles: plumbing, dev, final
uv run spg map --company "Expedia" --profile dev             # Stage 3 (also: replay-map)
uv run spg draft --company "Expedia" --profile dev           # Stage 4 (also: replay-draft)
uv run spg render --company "Expedia"                        # Stage 5: plan.json, plan.md, and the CSM draft
uv run spg csm-draft --company "Expedia"                     # rebuild only the CSM draft from plan.json
uv run pytest                                                # offline tests (no network, no API calls)
```

## Scope

An independent portfolio project using public information only. It does not use proprietary customer data and is not
affiliated with or endorsed by Atlassian or Expedia. The generated plan is unedited tool output and a starting point for
research and discovery, not a finished customer success plan. Designed and reviewed by the author; code written with
Claude Code. Project rules are in [CLAUDE.md](CLAUDE.md). Licence: MIT (see [LICENSE](LICENSE)).
