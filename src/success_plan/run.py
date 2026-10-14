"""`spg run`: Retrieve, Extract, Map, Draft, and Render in one command.

The run drives the same stage functions as the single-stage commands, so every stage check stays where it is.
What the run adds: pre-flight checks, one cost gate before any model call (a whole-run estimate, the run limit, and
a confirmation), a budget that shrinks as stages spend, timing per stage, and a summary line in the usage log.
"""

import json
import time
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from pathlib import Path

from .llm import LLMError, ModelsConfig, cost_usd, estimate_tokens
from .paths import PROJECT_ROOT, CompanyPaths
from .settings import SettingsError, sec_user_agent
from .stages import draft as dr
from .stages import extract as ex
from .stages import map as mp
from .stages import render as rd
from .stages.retrieve import RetrieveError, run_retrieve
from .stages.retrieve.edgar import EdgarError
from .stages.retrieve.http import FetchError

STAGES = ["retrieve", "extract", "map", "draft", "render"]
MODEL_STAGES = ["extract", "map", "draft"]
DEFAULT_PRODUCT = "atlassian_jira_confluence"
# Upper-bound allowance per initiative for the Map and Draft input estimates (they cannot be counted before Extract
# has run): about four sentences of initiative text, plus three benefit sentences for Draft.
CHARS_PER_INITIATIVE_MAP = 4 * 200
CHARS_PER_INITIATIVE_DRAFT = 7 * 200


@dataclass
class RunOptions:
    company: str
    ticker: str | None = None
    profile: str = "dev"
    product: str = DEFAULT_PRODUCT
    as_of: date | None = None
    dry_run: bool = False
    yes: bool = False
    max_cost: float | None = None
    force: bool = False
    refresh_sources: bool = False
    from_stage: str | None = None
    skip_news: bool = False


class RunStop(Exception):
    """A stop between stages: which stage, why, and what to do next. Everything already written stays on disk."""

    def __init__(self, stage: str, message: str):
        super().__init__(f"{stage}: {message}")
        self.stage, self.message = stage, message


@dataclass
class StageReport:
    name: str
    status: str  # "ran", "reused", "skipped"
    seconds: float = 0.0
    cost_usd: float = 0.0
    detail: str = ""


@dataclass
class RunResult:
    paths: CompanyPaths
    estimate: list[dict] = field(default_factory=list)
    stages: list[StageReport] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)
    outputs: list[Path] = field(default_factory=list)
    stop: RunStop | None = None
    dry_run: bool = False

    @property
    def cost_usd(self) -> float:
        return round(sum(s.cost_usd for s in self.stages), 6)

    @property
    def estimated_cost_usd(self) -> float:
        return round(sum(r["cost_usd"] for r in self.estimate), 6)


def product_config_path(product: str) -> Path:
    return PROJECT_ROOT / "config" / f"product_{product}.yaml"


# ---------------------------------------------------------------- pre-flight

def preflight(opts: RunOptions, models_config: ModelsConfig, llm) -> tuple[str, Path, dict]:
    """Checks that need no network or model call. Returns (SEC user agent, product config path, product config)."""
    try:
        user_agent = sec_user_agent()
    except SettingsError as exc:
        raise RunStop("pre-flight", f"{exc} EDGAR requires it.") from exc
    for stage in MODEL_STAGES:
        try:
            models_config.settings(opts.profile, stage)
        except LLMError as exc:
            raise RunStop("pre-flight", f"{exc}. Use --profile plumbing, dev, or final.") from exc
    if llm is None and not opts.dry_run:
        raise RunStop("pre-flight", "ANTHROPIC_API_KEY is not set. Add it to .env, or use --dry-run.")
    if opts.from_stage and opts.from_stage not in STAGES[1:]:
        raise RunStop("pre-flight", f"--from must be one of {', '.join(STAGES[1:])}.")
    config_path = product_config_path(opts.product)
    if not config_path.exists():
        raise RunStop("pre-flight", f"No product config {config_path.relative_to(PROJECT_ROOT)}. Check --product.")
    try:
        config = mp.load_config(config_path)
        mp.build_claims(config)
    except Exception as exc:  # noqa: BLE001  (any malformed config is a pre-flight stop)
        raise RunStop("pre-flight", f"Product config {config_path.name} is not valid: {exc}") from exc
    return user_agent, config_path, config


# ---------------------------------------------------------------- cost

def _row(stage: str, call: str, model: str, n_in: int, source: str, n_out: int, cost: float) -> dict:
    return {"stage": stage, "call": call, "model": model, "input_tokens": n_in, "input_source": source,
            "output_tokens": n_out, "cost_usd": round(cost, 6)}


def estimate_run(paths: CompanyPaths, models_config: ModelsConfig, profile: str, counter, config: dict,
                 stages: list[str]) -> list[dict]:
    """Whole-run estimate. Extract input is counted exactly when a counter is available; Map and Draft cannot be
    counted before Extract exists, so they get an upper bound from the real product config and the most initiatives
    Extract can return."""
    rows = []
    if "extract" in stages:
        estimates, _ = ex.run_extract(paths, None, models_config, profile, dry_run=True, counter=counter)
        rows += [_row("extract", e.name, e.model, e.input_tokens, e.input_source, e.output_tokens, e.cost_usd)
                 for e in estimates]
    n = ex.CAPS["initiative"]
    claims, _, _ = mp.build_claims(config)
    if "map" in stages:
        s = models_config.settings(profile, "map")
        text = mp.system_prompt("Company") + "\n".join(c["text"] for c in claims.values())
        text += "\n".join(str(g.get("text", "")) for g in config.get("not_covered", []))
        n_in = estimate_tokens(text, models_config.chars_per_token) + estimate_tokens("x" * (n * CHARS_PER_INITIATIVE_MAP),
                                                                                    models_config.chars_per_token)
        rows.append(_row("map", "map", s.model, n_in, "estimated (upper bound)", s.expected_output_tokens,
                         cost_usd(models_config.prices, s.model, input_tokens=n_in, output_tokens=s.expected_output_tokens)))
    if "draft" in stages:
        s = models_config.settings(profile, "draft")
        tasks = dr.load_tasks()
        text = (dr.PROMPTS / "draft.md").read_text() + json.dumps(dr.model_tasks(tasks))
        n_in = estimate_tokens(text, models_config.chars_per_token) + estimate_tokens(
            "x" * (n * CHARS_PER_INITIATIVE_DRAFT), models_config.chars_per_token)
        rows.append(_row("draft", "draft", s.model, n_in, "estimated (upper bound)", s.expected_output_tokens,
                         cost_usd(models_config.prices, s.model, input_tokens=n_in, output_tokens=s.expected_output_tokens)))
    return rows


def _budget(models_config: ModelsConfig, limit: float, spent: float) -> ModelsConfig:
    """The stages check their own estimate against max_cost_usd_per_run; give them what is left of the run limit."""
    return replace(models_config, max_cost_usd_per_run=max(0.0, limit - spent))


# ---------------------------------------------------------------- the run

def run_pipeline(opts: RunOptions, *, models_config: ModelsConfig, llm, http_factory, echo=print,
                 confirm=lambda message: False, paths: CompanyPaths | None = None) -> RunResult:
    paths = paths or CompanyPaths.for_company(opts.company)
    result = RunResult(paths=paths, dry_run=opts.dry_run)
    counter = llm if hasattr(llm, "count_input_tokens") else None
    limit = opts.max_cost if opts.max_cost is not None else models_config.max_cost_usd_per_run
    started = datetime.now(timezone.utc).replace(microsecond=0)
    try:
        user_agent, config_path, config = preflight(opts, models_config, llm)
        _run_retrieve(opts, paths, http_factory, user_agent, echo, result)
        to_run = MODEL_STAGES
        result.estimate = estimate_run(paths, models_config, opts.profile, counter, config, to_run)
        _gate(opts, result, limit, models_config, echo, confirm)
        if opts.dry_run:
            return result
        spent = 0.0
        spent += _run_model_stage("extract", lambda mc: ex.run_extract(paths, llm, mc, opts.profile, counter=counter)[1],
                                  models_config, limit, spent, echo, result, ex.ExtractError)
        spent += _run_model_stage("map", lambda mc: mp.run_map(paths, llm, mc, opts.profile, counter=counter,
                                                               config=config, config_path=config_path)[1],
                                  models_config, limit, spent, echo, result, mp.MapError)
        spent += _run_model_stage("draft", lambda mc: dr.run_draft(paths, llm, mc, opts.profile, counter=counter)[1],
                                  models_config, limit, spent, echo, result, dr.DraftError)
        _run_render(paths, echo, result)
    except RunStop as stop:
        result.stop = stop
    finally:
        if not opts.dry_run or result.stop:
            log_run(paths, opts, result, started)
    return result


def _timed(fn):
    t0 = time.monotonic()
    value = fn()
    return value, round(time.monotonic() - t0, 2)


def _run_retrieve(opts, paths, http_factory, user_agent, echo, result: RunResult) -> None:
    echo("Retrieve: SEC EDGAR filings and news headlines ...")
    on_backoff = lambda msg: echo(f"  waiting: {msg}")  # noqa: E731
    try:
        with http_factory(user_agent, on_backoff) as client:
            manifest, seconds = _timed(lambda: run_retrieve(
                client, paths, opts.company, ticker=opts.ticker, as_of=opts.as_of, force=opts.refresh_sources,
                skip_news=opts.skip_news))
    except (EdgarError, FetchError, RetrieveError, ValueError) as exc:
        raise RunStop("retrieve", str(exc) + (" Re-run with --ticker." if "--ticker" not in str(exc) and
                                              "matches several" in str(exc) else "")) from exc
    detail = (f"{manifest.company.name} ({manifest.company.ticker}); 10-K FY{manifest.filing.fiscal_year} filed "
              f"{manifest.filing.filing_date}; {len(manifest.eight_ks.events)} 8-K events; news {manifest.news.status}")
    result.stages.append(StageReport("retrieve", "ran", seconds, 0.0, detail))
    result.notices += [f"Retrieve: {w}" for w in manifest.warnings]
    echo(f"  {detail} ({seconds:.0f}s)")


def _gate(opts, result: RunResult, limit: float, models_config: ModelsConfig, echo, confirm) -> None:
    echo(f"Estimated cost (profile {opts.profile}; prices as of {models_config.prices_as_of}):")
    for r in result.estimate:
        echo(f"  {r['stage']:<8} {r['call']:<20} {r['model']:<20} {r['input_tokens']:>8,} in ({r['input_source']}), "
             f"~{r['output_tokens']:,} out  ${r['cost_usd']:.2f}")
    total = result.estimated_cost_usd
    echo(f"  total ${total:.2f} (run limit ${limit:.2f})")
    if total > limit:
        raise RunStop("cost", f"Estimated ${total:.2f} exceeds the run limit of ${limit:.2f}. No model call was made. "
                              "Use a cheaper --profile, or raise --max-cost.")
    if opts.dry_run:
        echo("Dry run: no model call was made.")
        return
    if not opts.yes and not confirm(f"Estimated ${total:.2f} (limit ${limit:.2f}). Proceed?"):
        raise RunStop("cost", "Not confirmed. No model call was made.")


def _run_model_stage(name, call, models_config, limit, spent, echo, result: RunResult, error) -> float:
    echo(f"{name.capitalize()} ...")
    try:
        output, seconds = _timed(lambda: call(_budget(models_config, limit, spent)))
    except (error, LLMError) as exc:
        raise RunStop(name, f"{exc} Saved stage outputs are kept; re-run to resume.") from exc
    cost = float(getattr(output, "total_cost_usd", 0.0) or 0.0)
    result.stages.append(StageReport(name, "ran", seconds, cost))
    result.notices += [f"{name.capitalize()}: {w}" for w in getattr(output, "warnings", [])]
    echo(f"  done: ${cost:.4f} ({seconds:.0f}s)")
    return cost


def _run_render(paths, echo, result: RunResult) -> None:
    echo("Render ...")
    try:
        (json_path, md_path, report), seconds = _timed(lambda: rd.render(paths))
    except rd.RenderError as exc:
        raise RunStop("render", str(exc)) from exc
    out = rd.output_dir(paths)
    result.outputs = [json_path, md_path] + [out / name for name in report.get("csm_draft", [])]
    result.stages.append(StageReport("render", "ran", seconds, 0.0))
    result.notices += [f"Render: {w}" for w in report.get("warnings", [])]
    echo(f"  done ({seconds:.0f}s)")


def log_run(paths: CompanyPaths, opts: RunOptions, result: RunResult, started: datetime) -> None:
    """One summary line per run in the usage log: stages, seconds, cost, estimate, and the stop reason if any."""
    paths.stages.mkdir(parents=True, exist_ok=True)
    line = {
        "stage": "run", "at": started.isoformat(), "profile": opts.profile, "product": opts.product,
        "status": "stopped" if result.stop else ("dry_run" if opts.dry_run else "ok"),
        "stop": {"stage": result.stop.stage, "message": result.stop.message} if result.stop else None,
        "stages": [{"name": s.name, "status": s.status, "seconds": s.seconds, "cost_usd": round(s.cost_usd, 6)}
                   for s in result.stages],
        "estimated_cost_usd": result.estimated_cost_usd, "cost_usd": result.cost_usd,
    }
    with (paths.stages / ex.USAGE_LOG_NAME).open("a") as fh:
        fh.write(json.dumps(line) + "\n")
