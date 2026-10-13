"""Command-line entry point: `spg <stage>`."""

from datetime import date
from typing import Optional

import typer

from .llm import AnthropicLLM, LLMError, has_api_key, load_models_config
from .paths import CompanyPaths
from .settings import SettingsError, load_env, sec_user_agent
from .stages.extract import GUARD_REPORT_NAME, OUTPUT_NAME, REVIEW_SHEET_NAME, ExtractError, replay_extract, run_extract
from .stages.retrieve import RetrieveError, run_retrieve
from .stages.retrieve.edgar import EdgarError
from .stages.retrieve.http import FetchError, RateLimitedClient

app = typer.Typer(add_completion=False, help="Generate a hypothetical customer success plan from public information.")


@app.callback()
def main() -> None:
    load_env()


@app.command()
def retrieve(
    company: str = typer.Option(..., help="Company name, e.g. 'Expedia'"),
    ticker: Optional[str] = typer.Option(None, help="Ticker, to resolve an ambiguous name"),
    as_of: Optional[str] = typer.Option(None, help="As-of date (YYYY-MM-DD). Defaults to today."),
    force: bool = typer.Option(False, help="Re-download cached EDGAR documents and refresh the GDELT cache"),
) -> None:
    """Stage 1: fetch the latest 10-K, recent 8-Ks, and GDELT headlines; split and chunk them."""
    try:
        user_agent = sec_user_agent()
        as_of_date = date.fromisoformat(as_of) if as_of else None
        on_backoff = lambda msg: typer.secho(msg, fg=typer.colors.YELLOW, err=True)
        with RateLimitedClient(user_agent, on_backoff=on_backoff) as client:
            m = run_retrieve(client, CompanyPaths.for_company(company), company, ticker=ticker, as_of=as_of_date, force=force)
    except (SettingsError, EdgarError, FetchError, RetrieveError, ValueError) as exc:
        typer.secho(f"Retrieve failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    typer.echo(f"{m.company.name} ({m.company.ticker}, CIK {m.company.cik})")
    typer.echo(
        f"10-K FY{m.filing.fiscal_year}: period end {m.filing.period_end}, filed {m.filing.filing_date}, "
        f"fiscal-year check {m.filing.fiscal_year_check}"
    )
    for s in m.sections:
        typer.echo(f"  {s.title}: {s.word_count:,} words, {s.chunk_count} chunks")
    typer.echo(f"8-K events: {len(m.eight_ks.events)}; earnings releases: {len(m.eight_ks.earnings_releases)}")
    cached = " (from cache)" if m.news.from_cache else ""
    typer.echo(
        f"GDELT: {m.news.status}{cached}, {len(m.news.headlines)} headlines kept, "
        f"{m.news.eligible_count} eligible for Extract, {m.news.low_signal_count} low-signal"
    )
    typer.echo(f"Chunks: {m.total_chunks}; requests: {m.request_count}")
    for w in m.warnings:
        typer.secho(f"Warning: {w}", fg=typer.colors.YELLOW)


@app.command()
def extract(
    company: str = typer.Option(..., help="Company name used for retrieve, e.g. 'Expedia'"),
    profile: str = typer.Option("dev", help="Model profile from config/models.yaml: plumbing, dev, or final"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print estimated tokens and cost; make no API call"),
) -> None:
    """Stage 2: extract initiatives, challenges, and changes with verified citations."""
    try:
        config = load_models_config()
        client = AnthropicLLM() if has_api_key() else None  # counting needs a key; dry run works without one
        if client is None and not dry_run:
            raise LLMError("ANTHROPIC_API_KEY is not set. Add it to .env, or use --dry-run.")
        estimates, output = run_extract(
            CompanyPaths.for_company(company), None if dry_run else client, config, profile, dry_run=dry_run, counter=client,
        )
    except (ExtractError, LLMError) as exc:
        typer.secho(f"Extract failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    total = sum(e.cost_usd for e in estimates)
    typer.echo(f"Profile {profile} (prices as of {config.prices_as_of}; verify on Anthropic's pricing page)")
    for e in estimates:
        typer.echo(
            f"  {e.name}: {e.model}, {e.input_tokens:,} input tokens ({e.input_source}), "
            f"~{e.output_tokens:,} output tokens (assumed), ~${e.cost_usd:.2f}"
        )
        if e.note:
            typer.secho(f"    {e.note}", fg=typer.colors.YELLOW)
    typer.echo(f"  Estimated total: ${total:.2f} (limit ${config.max_cost_usd_per_run:.2f})")
    if dry_run:
        counted = any(e.input_source == "counted" for e in estimates)
        typer.echo(
            "Dry run: no model call was made"
            + (" (input tokens counted with Anthropic's free token-counting endpoint)." if counted else ".")
        )
        return
    typer.echo(
        f"Initiatives: {len(output.initiatives)}; challenges: {len(output.challenges)}; changes: {len(output.changes)}"
    )
    typer.echo(f"Actual cost: ${output.total_cost_usd:.4f}")
    typer.echo(f"Wrote {OUTPUT_NAME}, {GUARD_REPORT_NAME}, and raw/{REVIEW_SHEET_NAME} (gitignored)")
    for w in output.warnings:
        typer.secho(f"Warning: {w}", fg=typer.colors.YELLOW)


@app.command("replay-extract")
def replay_extract_cmd(
    company: str = typer.Option(..., help="Company name used for retrieve, e.g. 'Expedia'"),
    response: Optional[str] = typer.Option(None, help="Saved response folder (default: the most recent)"),
) -> None:
    """Re-apply the current guards to saved Extract responses. Makes no API call."""
    from pathlib import Path

    try:
        out = replay_extract(CompanyPaths.for_company(company), load_models_config(), Path(response) if response else None)
    except (ExtractError, LLMError) as exc:
        typer.secho(f"Replay failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    typer.echo(f"Replayed {out.replayed_from} (no API call)")
    typer.echo(f"Initiatives: {len(out.initiatives)}; challenges: {len(out.challenges)}; changes: {len(out.changes)}")
    typer.echo(f"Wrote {OUTPUT_NAME}, {GUARD_REPORT_NAME}, and raw/{REVIEW_SHEET_NAME} (gitignored)")
    for w in out.warnings:
        typer.secho(f"Warning: {w}", fg=typer.colors.YELLOW)


@app.command("map")
def map_cmd(
    company: str = typer.Option(..., help="Company name used for retrieve, e.g. 'Expedia'"),
    profile: str = typer.Option("dev", help="Model profile from config/models.yaml: plumbing, dev, or final"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print estimated tokens and cost; make no model call"),
) -> None:
    """Stage 3: map each initiative to config capabilities or no_fit, with verified claim citations."""
    from .stages import map as mp

    try:
        config = load_models_config()
        client = AnthropicLLM() if has_api_key() else None
        if client is None and not dry_run:
            raise LLMError("ANTHROPIC_API_KEY is not set. Add it to .env, or use --dry-run.")
        estimate, out = mp.run_map(CompanyPaths.for_company(company), None if dry_run else client, config, profile,
                                   dry_run=dry_run, counter=client)
    except (mp.MapError, LLMError) as exc:
        typer.secho(f"Map failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    typer.echo(f"Profile {profile} (prices as of {config.prices_as_of}; verify on Anthropic's pricing page)")
    typer.echo(f"  map: {estimate['model']}, {estimate['input_tokens']:,} input tokens ({estimate['input_source']}), "
               f"~{estimate['output_tokens']:,} output tokens (assumed), ~${estimate['cost_usd']:.2f} "
               f"(limit ${config.max_cost_usd_per_run:.2f})")
    if estimate["note"]:
        typer.secho(f"    {estimate['note']}", fg=typer.colors.YELLOW)
    if dry_run:
        typer.echo("Dry run: no model call was made.")
        return
    typer.echo(f"Key benefits: {len(out.key_benefits)}; fit: {out.fit_distribution}; actual cost: ${out.total_cost_usd:.4f}")
    typer.echo(f"Wrote {mp.OUTPUT_NAME} and {mp.GUARD_REPORT_NAME}")
    for w in out.warnings:
        typer.secho(f"Warning: {w}", fg=typer.colors.YELLOW)


@app.command("replay-map")
def replay_map_cmd(
    company: str = typer.Option(..., help="Company name used for retrieve, e.g. 'Expedia'"),
    response: Optional[str] = typer.Option(None, help="Saved Map response folder (default: the most recent)"),
) -> None:
    """Re-apply the current Map guards to a saved Map response. Makes no API call."""
    from pathlib import Path
    from .stages import map as mp

    try:
        out = mp.replay_map(CompanyPaths.for_company(company), load_models_config(), Path(response) if response else None)
    except (mp.MapError, LLMError) as exc:
        typer.secho(f"Replay failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    typer.echo(f"Replayed {out.replayed_from} (no API call). Key benefits: {len(out.key_benefits)}; fit: {out.fit_distribution}")
    for w in out.warnings:
        typer.secho(f"Warning: {w}", fg=typer.colors.YELLOW)


@app.command("draft")
def draft_cmd(
    company: str = typer.Option(..., help="Company name used for retrieve, e.g. 'Expedia'"),
    profile: str = typer.Option("dev", help="Model profile from config/models.yaml: plumbing, dev, or final"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print estimated tokens and cost; make no model call"),
) -> None:
    """Stage 4: draft objectives, success criteria, and tasks for initiatives Map fitted (full or partial)."""
    from .stages import draft as dr

    try:
        config = load_models_config()
        client = AnthropicLLM() if has_api_key() else None
        if client is None and not dry_run:
            raise LLMError("ANTHROPIC_API_KEY is not set. Add it to .env, or use --dry-run.")
        estimate, out = dr.run_draft(CompanyPaths.for_company(company), None if dry_run else client, config, profile,
                                     dry_run=dry_run, counter=client)
    except (dr.DraftError, LLMError) as exc:
        typer.secho(f"Draft failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    typer.echo(f"Profile {profile} (prices as of {config.prices_as_of}; verify on Anthropic's pricing page)")
    typer.echo(f"  draft: {estimate['model']}, {estimate['input_tokens']:,} input tokens ({estimate['input_source']}), "
               f"~{estimate['output_tokens']:,} output tokens (assumed), ~${estimate['cost_usd']:.2f} "
               f"(limit ${config.max_cost_usd_per_run:.2f})")
    if estimate["note"]:
        typer.secho(f"    {estimate['note']}", fg=typer.colors.YELLOW)
    if dry_run:
        typer.echo("Dry run: no model call was made.")
        return
    n_criteria = sum(len(o.success_criteria) for o in out.objectives)
    typer.echo(f"Objectives: {len(out.objectives)}; criteria: {n_criteria}; actual cost: ${out.total_cost_usd:.4f}")
    typer.echo(f"Wrote {dr.OUTPUT_NAME} and {dr.GUARD_REPORT_NAME}")
    for w in out.warnings:
        typer.secho(f"Warning: {w}", fg=typer.colors.YELLOW)


@app.command("replay-draft")
def replay_draft_cmd(
    company: str = typer.Option(..., help="Company name used for retrieve, e.g. 'Expedia'"),
    response: Optional[str] = typer.Option(None, help="Saved Draft response folder (default: the most recent)"),
) -> None:
    """Re-apply the current Draft checks to a saved Draft response. Makes no API call."""
    from pathlib import Path
    from .stages import draft as dr

    try:
        out = dr.replay_draft(CompanyPaths.for_company(company), load_models_config(), Path(response) if response else None)
    except (dr.DraftError, LLMError) as exc:
        typer.secho(f"Replay failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    n_criteria = sum(len(o.success_criteria) for o in out.objectives)
    typer.echo(f"Replayed {out.replayed_from} (no API call). Objectives: {len(out.objectives)}; criteria: {n_criteria}")
    for w in out.warnings:
        typer.secho(f"Warning: {w}", fg=typer.colors.YELLOW)


@app.command("render")
def render_cmd(
    company: str = typer.Option(..., help="Company name used for retrieve, e.g. 'Expedia'"),
) -> None:
    """Stage 5: render the plan as schema-validated JSON and Markdown, with items to validate. No model call."""
    from .stages import render as rd

    try:
        json_path, md_path, report = rd.render(CompanyPaths.for_company(company))
    except rd.RenderError as exc:
        typer.secho(f"Render failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    typer.echo(f"Wrote {json_path} (schema-valid) and {md_path}")
    typer.echo(f"Wrote the brief: {', '.join(report['brief'])} (same folder)")
    for w in report["warnings"]:
        typer.secho(f"Warning: {w}", fg=typer.colors.YELLOW)


@app.command("brief")
def brief_cmd(
    company: str = typer.Option(..., help="Company name used for retrieve, e.g. 'Expedia'"),
) -> None:
    """Rebuild only the brief (plan_brief.md and .docx) from the existing plan.json. No model call."""
    from .models import RetrieveManifest
    from .stages import brief as bf
    from .stages import render as rd

    paths = CompanyPaths.for_company(company)
    manifest = paths.stages / "01_retrieve.json"
    headlines = ([h.headline for h in RetrieveManifest.model_validate_json(manifest.read_text()).news.headlines]
                 if manifest.exists() else [])
    try:
        md_path, docx_path = bf.write_brief(paths, headlines)
    except rd.RenderError as exc:
        typer.secho(f"Brief failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    typer.echo(f"Wrote {md_path} and {docx_path}")
