"""`spg run`: Retrieve, Extract, Map, Draft, and Render in one command.

The run drives the same stage functions as the single-stage commands, so every stage check stays where it is.
What the run adds: pre-flight checks, one cost gate before any model call (a whole-run estimate, the run limit, and
a confirmation), a budget that shrinks as stages spend, timing per stage, and a summary line in the usage log.
"""

import json
import re
import subprocess
import time
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from pathlib import Path

from .config import load_private_names
from .llm import LLMError, ModelsConfig, cost_usd, estimate_tokens
from .models import DraftOutput, ExtractOutput, MapOutput, RetrieveManifest
from .paths import PROJECT_ROOT, CompanyPaths
from .settings import SettingsError, sec_user_agent
from .stages import csm_draft as cdr
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
    personal_names: list[str] = field(default_factory=list)

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


# ---------------------------------------------------------------- resume, overwrite protection, stops

OUTPUT_FILES = [rd.PLAN_JSON, rd.PLAN_MD, rd.REPORT_NAME, cdr.DRAFT_MD, cdr.DRAFT_DOCX]
# New proposal (2026-10-13), not taken from an earlier run: stop when more than half of Extract's proposed sentences
# are dropped by the citation checks, since that suggests a broken response. The sentence-level Extract evaluation
# runs used a stricter stop condition, at most 25% dropped (docs/evaluation_log.md).
EXTRACT_DROP_LIMIT = 0.5
STAGE_FILES = {"retrieve": "01_retrieve.json", "extract": ex.OUTPUT_NAME, "map": mp.OUTPUT_NAME, "draft": dr.OUTPUT_NAME}


def existing_outputs(paths: CompanyPaths) -> list[Path]:
    out = rd.output_dir(paths)
    return [out / name for name in OUTPUT_FILES if (out / name).exists()]


def tracked_by_git(root: Path, files: list[Path]) -> bool:
    try:
        rel = [str(f.relative_to(root)) for f in files]
        res = subprocess.run(["git", "-C", str(root), "ls-files", "--", *rel], capture_output=True, text=True, timeout=10)
        return res.returncode == 0 and bool(res.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def check_overwrite(opts: RunOptions, paths: CompanyPaths) -> None:
    """Existing outputs are never replaced without --force. A dry run writes no outputs, so it is always allowed."""
    if opts.dry_run or opts.force:
        return
    existing = existing_outputs(paths)
    if not existing:
        return
    where = rd.output_dir(paths).relative_to(paths.root)
    sample = (f" {where} is a committed sample; use --force only if you mean to replace it."
              if tracked_by_git(paths.root, existing) else " Use --force to replace them.")
    raise RunStop("pre-flight", f"{where} already has outputs ({', '.join(f.name for f in existing)}).{sample}")


def _load(path: Path, model):
    try:
        return model.model_validate_json(path.read_text()) if path.exists() else None
    except Exception:  # noqa: BLE001  (an unreadable stage output is simply re-run)
        return None


def decide(opts: RunOptions, paths: CompanyPaths, config_path: Path) -> dict[str, str]:
    """Which stages run and which reuse their saved output: "run" or "reuse"."""
    s = paths.stages
    manifest = _load(s / STAGE_FILES["retrieve"], RetrieveManifest) if paths.chunks_file.exists() else None
    extract = _load(s / STAGE_FILES["extract"], ExtractOutput)
    mapping = _load(s / STAGE_FILES["map"], MapOutput)
    draft = _load(s / STAGE_FILES["draft"], DraftOutput)
    start = STAGES.index(opts.from_stage) if opts.from_stage else None
    plan: dict[str, str] = {}
    if opts.force and start is None:
        return {st: "run" for st in STAGES}
    if start is not None:
        for st, saved in (("retrieve", manifest), ("extract", extract), ("map", mapping), ("draft", draft)):
            if STAGES.index(st) < start:
                if saved is None:
                    raise RunStop("pre-flight", f"--from {opts.from_stage} needs a saved {st} output "
                                                f"({STAGE_FILES[st]}); run without --from first.")
                plan[st] = "reuse"
            else:
                plan[st] = "run"
        plan["render"] = "run"
        return plan
    stale_retrieve = (manifest is None or opts.refresh_sources
                      or (opts.as_of is not None and manifest.as_of != opts.as_of))
    if stale_retrieve and manifest is not None and opts.dry_run and not opts.force:
        stale_retrieve = False  # a dry run never replaces a saved Retrieve; the estimate uses it (noted below)
    plan["retrieve"] = "run" if stale_retrieve else "reuse"
    upstream = stale_retrieve
    ok = (not upstream and extract is not None and extract.retrieve_generated_at == manifest.generated_at)
    plan["extract"] = "reuse" if ok else "run"
    upstream = upstream or not ok
    ok = (not upstream and mapping is not None and mapping.extract_generated_at == extract.generated_at
          and mapping.config_file == mp.config_label(config_path))
    plan["map"] = "reuse" if ok else "run"
    upstream = upstream or not ok
    ok = not upstream and draft is not None and draft.map_generated_at == mapping.generated_at
    plan["draft"] = "reuse" if ok else "run"
    plan["render"] = "run"
    return plan


def extract_checks(paths: CompanyPaths, opts: RunOptions, accept_drops: bool) -> str:
    output = ExtractOutput.model_validate_json((paths.stages / ex.OUTPUT_NAME).read_text())
    if not output.initiatives:
        raise RunStop("extract", "No initiatives were kept after the citation checks, so there is nothing to map. "
                                 f"See {ex.GUARD_REPORT_NAME}.")
    report_path = paths.stages / ex.GUARD_REPORT_NAME
    report = json.loads(report_path.read_text()) if report_path.exists() else {}
    kept = sum(len(i.sentences) for i in output.initiatives + output.challenges + output.changes)
    dropped = len(report.get("dropped_sentences") or [])
    proposed = kept + dropped
    detail = f"{len(output.initiatives)} initiatives; {dropped} of {proposed} proposed sentences dropped"
    if proposed and dropped / proposed > EXTRACT_DROP_LIMIT and not accept_drops:
        raise RunStop("extract", f"{dropped} of {proposed} proposed sentences ({dropped / proposed:.0%}) were dropped by "
                                 f"the citation checks, more than half. Review {ex.GUARD_REPORT_NAME}. To continue with "
                                 f'this extraction anyway, run: spg run "{opts.company}" --from map')
    return detail


def fit_count(paths: CompanyPaths) -> int:
    mapping = MapOutput.model_validate_json((paths.stages / mp.OUTPUT_NAME).read_text())
    return sum(1 for b in mapping.key_benefits if b.fit != "no_fit")


def draft_checks(paths: CompanyPaths) -> str:
    draft = DraftOutput.model_validate_json((paths.stages / dr.OUTPUT_NAME).read_text())
    criteria = [c for o in draft.objectives for c in o.success_criteria]
    if draft.objectives and all(c.owner == "csm" for c in criteria):
        raise RunStop("draft", "Every objective fell back to the CSM blank criterion: Draft produced no success "
                               f"criteria. See {dr.GUARD_REPORT_NAME}.")
    return f"{len(draft.objectives)} objectives; {sum(1 for c in criteria if c.owner != 'csm')} criteria"


# ---------------------------------------------------------------- personal names (for config/private_names.txt)

_HONORIFIC = re.compile(r"\b(?:Mr|Ms|Mrs|Dr)\.\s+[A-Z][A-Za-z'-]+(?:\s+[A-Z][A-Za-z'-]+)?")
_NAME_BEFORE_ROLE = re.compile(
    r"\b([A-Z][a-z]+(?:\s+[A-Z]\.)?\s+[A-Z][a-z]+(?:-[A-Z][a-z]+)?)"
    r"(?=,\s+(?:age|aged|who|the\s+Company|our|\d)|\s+(?:as|has\s+been|will\s+serve|was\s+appointed|resigned|to\s+serve))")
_NOT_NAME_WORDS = {
    "chief", "officer", "executive", "financial", "operating", "president", "board", "directors", "director",
    "company", "group", "inc", "corporation", "committee", "chairman", "vice", "senior", "general", "counsel",
    "january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november",
    "december", "item", "form", "the", "our", "effective", "exhibit",
}


def personal_name_candidates(paths: CompanyPaths) -> list[str]:
    """Possible people named in this company's stage outputs (heuristic): Extract's personal-name, personal-title, and
    biography flags, and names in evidence phrases and in 8-K Item 5.02 text (officer changes). Names already on
    config/private_names.txt are left out."""
    private = {n.lower() for n in load_private_names()}
    found: dict[str, str] = {}
    extract_path = paths.stages / ex.OUTPUT_NAME
    texts: list[str] = []
    if extract_path.exists():
        data = json.loads(extract_path.read_text())
        for kind in ("initiatives", "challenges", "changes"):
            for item in data.get(kind) or []:
                for f in item.get("flags") or []:
                    if f["type"] in ("personal_name", "personal_title", "biography"):
                        found.setdefault(f"{item['id']} flag {f['type']}: {f['detail']}", "flag")
                texts += [c["evidence"] for s in item.get("sentences") or [] for c in s.get("citations") or []]
    manifest_path = paths.stages / STAGE_FILES["retrieve"]
    if manifest_path.exists() and paths.chunks_file.exists():
        manifest = RetrieveManifest.model_validate_json(manifest_path.read_text())
        officer_8ks = {f"8k-{e.accession}" for e in manifest.eight_ks.events if "5.02" in e.items}
        for line in paths.chunks_file.read_text().splitlines():
            record = json.loads(line)
            if record.get("source_id") in officer_8ks:
                texts.append(record["text"])
    for text in texts:
        for m in list(_HONORIFIC.finditer(text)) + list(_NAME_BEFORE_ROLE.finditer(text)):
            name = (m.group(1) if m.re is _NAME_BEFORE_ROLE else m.group(0)).strip()
            words = {w.strip(".").lower() for w in name.split()}
            if words & _NOT_NAME_WORDS or name.lower() in private:
                continue
            if any(name.lower().endswith(p) or p in name.lower() for p in private):
                continue
            found.setdefault(name, "name")
    return sorted(found)


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
        check_overwrite(opts, paths)
        plan = decide(opts, paths, config_path)
        if plan["retrieve"] == "run":
            _run_retrieve(opts, paths, http_factory, user_agent, echo, result)
        else:
            _reused(paths, "retrieve", RetrieveManifest, result, echo, opts)
            if opts.dry_run and opts.as_of and not opts.force:
                result.notices.append("Dry run: the estimate uses the saved Retrieve output; --force refreshes it.")
        to_run = [st for st in MODEL_STAGES if plan[st] == "run"]
        result.estimate = estimate_run(paths, models_config, opts.profile, counter, config, to_run) if to_run else []
        _gate(opts, result, limit, models_config, echo, confirm)
        if opts.dry_run:
            return result
        spent = 0.0
        if plan["extract"] == "run":
            spent += _run_model_stage("extract", lambda mc: ex.run_extract(paths, llm, mc, opts.profile, counter=counter)[1],
                                      models_config, limit, spent, echo, result, ex.ExtractError)
        else:
            _reused(paths, "extract", ExtractOutput, result, echo, opts)
        result.stages[-1].detail = extract_checks(paths, opts, accept_drops=opts.from_stage in ("map", "draft", "render"))
        if plan["map"] == "run":
            spent += _run_model_stage("map", lambda mc: mp.run_map(paths, llm, mc, opts.profile, counter=counter,
                                                                   config=config, config_path=config_path)[1],
                                      models_config, limit, spent, echo, result, mp.MapError)
        else:
            _reused(paths, "map", MapOutput, result, echo, opts)
        fits = fit_count(paths)
        result.stages[-1].detail = f"{fits} initiatives with a full or partial fit"
        if fits == 0:
            notice = ("No product fit: Map found no full or partial fit, so Draft made no model call and the plan lists "
                      "every initiative as no fit, with the 'add objectives' blank.")
            result.notices.append(notice)
            echo(notice)
            (_, seconds) = _timed(lambda: dr.write_without_fits(paths, models_config))
            result.stages.append(StageReport("draft", "skipped", seconds, 0.0, "no fit: no objectives"))
        else:
            if plan["draft"] == "run":
                spent += _run_model_stage("draft", lambda mc: dr.run_draft(paths, llm, mc, opts.profile, counter=counter)[1],
                                          models_config, limit, spent, echo, result, dr.DraftError)
            else:
                _reused(paths, "draft", DraftOutput, result, echo, opts)
            result.stages[-1].detail = draft_checks(paths)
        _run_render(paths, echo, result)
    except RunStop as stop:
        result.stop = stop
    finally:
        result.personal_names = personal_name_candidates(paths)
        if not opts.dry_run or result.stop:
            log_run(paths, opts, result, started)
    return result


def _reused(paths: CompanyPaths, stage: str, model, result: RunResult, echo, opts: RunOptions) -> None:
    saved = model.model_validate_json((paths.stages / STAGE_FILES[stage]).read_text())
    profile = getattr(saved, "profile", None)
    note = f" (profile {profile})" if profile else ""
    if profile and stage in MODEL_STAGES and profile not in (opts.profile, "none"):
        result.notices.append(f"{stage.capitalize()} reused from profile {profile}, not {opts.profile}; "
                              f"use --from {stage} to re-run it.")
    result.stages.append(StageReport(stage, "reused", 0.0, 0.0, f"reused {STAGE_FILES[stage]}{note}"))
    echo(f"{stage.capitalize()}: reused {STAGE_FILES[stage]}{note}")


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
        raise RunStop("retrieve", str(exc)) from exc
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
    if not result.estimate:
        echo("  no model calls needed (every model stage reuses its saved output)")
    total = result.estimated_cost_usd
    echo(f"  total ${total:.2f} (run limit ${limit:.2f})")
    if total > limit:
        raise RunStop("cost", f"Estimated ${total:.2f} exceeds the run limit of ${limit:.2f}. No model call was made. "
                              "Use a cheaper --profile, or raise --max-cost.")
    if opts.dry_run:
        echo("Dry run: no model call was made.")
        return
    if result.estimate and not opts.yes and not confirm(f"Estimated ${total:.2f} (limit ${limit:.2f}). Proceed?"):
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
        out = rd.output_dir(paths)
        kept = [f.name for f in (out / rd.PLAN_JSON, out / rd.PLAN_MD) if f.exists()]
        raise RunStop("render", str(exc) + (f" Kept: {', '.join(kept)}." if kept else "")) from exc
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
