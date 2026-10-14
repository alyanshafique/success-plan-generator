# Success Plan Generator

## Project goal

A CLI tool that generates a **hypothetical customer success plan** from **public information only**.

- Input: a company name and a product capability config (`config/product_<name>.yaml`).
- Output: a draft success plan (JSON validated against `schema/success_plan_schema.json`, plus a rendered Markdown version).
- Purpose: portfolio project showing AI fluency for customer success roles. The plan is a starting hypothesis a CSM would validate with the customer. It is never presented as fact.

Default sample: company = Expedia, product = Jira (plus related Atlassian products listed in the config).

## Non-negotiable ground rules

1. **Public data only.** Allowed inputs: SEC EDGAR filings, earnings call transcripts or commentary that are publicly available, news articles, the company's own website, and public product documentation. Never use, request, or reference customer account data, CRM data, internal enablement material, or any non-public document.
2. **No private files.** Do not read, copy, or reference anything outside this project folder. If a file looks like real customer data (renewal amounts, internal deal strategy, named account history), stop and tell the user. Do not process it.
3. **Every claim needs a provenance tag.** Allowed values: `sourced`, `inferred`, `csm-input`.
   - `sourced`: must include at least one citation (filing section, article URL, or date).
   - `inferred`: the model's reasoning. Must be labeled as such and must not be written as fact.
   - `csm-input`: a placeholder for information only the CSM can supply.
4. **Never fabricate CSM-owned content.** Renewal details, historical account information, customer-raised challenges, and baselines are labeled blanks, for example `[CSM to complete: renewal date, ARR, status, next steps]`. Never invent them or estimate them.
5. **"No fit" is a valid answer.** If a business initiative does not map to a capability in the product config, mark it `no_fit` and say why. Do not stretch a capability to force a match.
6. **Only use capabilities from the product config.** Never describe a product feature that is not in the config file. If the config lacks something, flag it rather than guessing.
7. **Do not imply the company is a customer of the product.** Unless the company has publicly said so and it is cited, frame the plan as a hypothetical adoption or expansion scenario.
8. **Quote sparingly.** Paraphrase sources. Direct quotes must be short (under 15 words) and cited. Never reproduce long passages from filings or articles.
9. **Include the disclaimer** in every output: "All inputs are publicly available. This tool does not use proprietary or customer data. This output is a hypothesis generated from public information and has not been validated with the company."
10. **No prices or contract terms.** Never state prices, discounts, or contract terms in a generated plan. Enterprise contracts are custom, so public list prices would mislead. Commercial details belong to the CSM and the account team.

## Section ownership and sourcing

Every section has an `owner`: `claude` (generate), `csm` (leave a labeled blank), or `shared` (Claude drafts, CSM validates).

| Section | Owner | Source / behavior |
|---|---|---|
| Company Highlights | claude (overview) + csm (account history) | Overview from the 10-K Business section and company site. Account history is a labeled blank. |
| Key Business Initiatives | shared | Pull from the 10-K **Business section, MD&A, and earnings call commentary**, plus recent news. Do **not** derive initiatives from Risk Factors. CSM selects the relevant ones. |
| Key Benefits | claude | For each initiative, state how the product could address it, using only the capability config. Mark `no_fit` where it does not. |
| Renewal | csm | Labeled blank only. |
| Success Criteria | shared | Derive from initiatives and benefits. Each gets `needs_baseline` and a discovery question for the CSM. |
| Key Challenges / Potential Blockers | shared | Draw from 10-K **Risk Factors** and recent news (leadership changes, restructurings, acquisitions, incidents, public changes that could affect investment). Add a labeled blank for challenges the customer has raised. |
| Objectives, Success Criteria, Tasks | shared | High-level objectives, each with criteria and tasks. Tasks have a proposed relative timeline. CSM validates. |

## Success criteria rules

- Prefer specific, measurable criteria over generic ones. "Improve efficiency" is not acceptable.
- Distinguish business outcomes from product usage goals, and leading from lagging indicators.
- A proposed numeric target must set `needs_baseline: true` unless the baseline is publicly sourced, and must include a `baseline_question` for the CSM.
- Every success criterion gets a `validation_question`.

## Timelines

Use relative windows only (for example `Day 0-30`, `Day 31-60`, `Day 61-90`, or `Week N`). Mark all timelines as proposed. No calendar dates unless sourced.

## Pipeline (each stage is its own module, testable alone)

1. **Retrieve**: fetch the latest 10-K from SEC EDGAR, split into sections (Business, Risk Factors, MD&A), save locally. Gather recent news. Save raw inputs under `data/<company>/raw/`.
2. **Extract**: pull initiatives, challenges, and leadership or structural changes into JSON with citations. No drafting here. The guards flag any currency amount or per-user price in extracted text that would flow into the plan (ground rule 10).
3. **Map**: map each initiative to config capabilities or `no_fit`.
4. **Draft**: generate objectives, success criteria, and tasks per the schema, for initiatives Map fitted (full or partial). The model writes only success criteria, each tied to Extract or Map sentences by reference; code builds objective titles, tasks (from `config/draft_tasks.yaml`), and targets (the number is a CSM blank). Any currency amount (e.g. "$12", "USD 5,000", "€8"), per-user price (e.g. "per user per month"), or plan tier in a criterion drops it (ground rule 10), as does a date: a month name, a specific day, a quarter label, or a year its referenced texts do not contain.
5. **Render** (includes what was planned as a separate Flag stage): set baseline flags, gather discovery and validation questions into the "things to validate with the customer" list, add the CSM blanks, apply the optional CSM selection (`data/<company>/inputs/csm_selection.yaml`), and output JSON and Markdown. Validate JSON against the schema before writing. The plan shows source links only, never evidence phrases, and omits (and reports) any sentence that copies 15 or more consecutive words from a source. It also writes the CSM draft from the same plan.json (`csm_draft.md` and `csm_draft.docx`): the working draft the CSM edits and takes into the customer conversation.

Intermediate outputs are saved per stage so any stage can be inspected or re-run.

## SEC EDGAR notes

- EDGAR requires a descriptive `User-Agent` header with a name and contact email. Read it from an environment variable, never hardcode it.
- Respect rate limits (no more than 10 requests per second; keep it well below).
- Always confirm the filing's fiscal year and filing date and record them in the output metadata.

## Working style

- **Plan before coding.** For each stage, propose the design and wait for approval before writing code.
- **One stage at a time.** Build, run, and show results before starting the next stage.
- **Explain after building.** After each stage, give a short walkthrough of what the code does and why, so the owner can describe it in interviews.
- **Commit after each working stage** with a clear message.
- **Secrets.** API keys come from environment variables. Never commit them. Keep `.env` in `.gitignore`.
- **Log failures.** When output is wrong (wrong fiscal year, stale news, generic metrics, unsupported claims), record it in `docs/evaluation_log.md`. These notes are a deliverable.

## Repo structure (approved)

```
CLAUDE.md
README.md
pyproject.toml                         # Python 3.12, managed with uv
.env.example                           # env var names only (SEC_USER_AGENT, ANTHROPIC_API_KEY)
config/product_atlassian_jira_confluence.yaml
schema/success_plan_schema.json
src/success_plan/
  cli.py                               # spg run | retrieve | extract | map | draft | render | csm-draft (plus replay-* commands)
  config.py                            # loads capability config; model view strips plan_availability
  models.py                            # pydantic models for each stage's output
  llm.py                               # Anthropic client wrapper; per-stage model; token and cost logging
  guards.py                            # deterministic rule checks (citations, quotes, plan wording, dates)
  stages/retrieve/                     # edgar, sections, news, chunks
  stages/extract.py, map.py, draft.py, render.py   # render.py also builds the Markdown
  stages/csm_draft.py                  # CSM draft (Markdown and Word) from plan.json
  prompts/                             # prompt files, version-controlled
tests/                                 # per-stage tests; no network or API calls in unit tests
data/<company>/raw/                    # gitignored: filings, section text, chunks
data/<company>/stages/                 # committed: per-stage JSON (short evidence only, no long excerpts)
data/<company>/inputs/                 # optional CSM inputs, e.g. csm_selection.yaml (applied at Render)
output/<company>/
docs/evaluation_log.md
```

- Keep long source excerpts out of committed files. Stored evidence spans are short (under 15 words).
- Models are configurable per stage. Use a cheaper model while developing and Opus 5.5 for final runs. Log token usage and cost for every run.
