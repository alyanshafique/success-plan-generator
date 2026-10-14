"""Stage 4: Draft objectives, success criteria, and tasks for initiatives Map fitted (full or partial).

One model call. The model writes only success criteria. Each criterion cites reference ids from a table code
builds: the initiative's kept Extract sentences (I-01.s2) and its Map benefit sentences (B-01.s1). Code checks
that every reference belongs to the criterion's own initiative and scores the criterion against the referenced
text, so a criterion cannot add company facts. Code builds everything else: objective titles (neutral
template), tasks (filled from config/draft_tasks.yaml; the model only picks ids, roles, and windows), targets
(the number is always a CSM blank), and the fallback blank when no criterion survives.

no_fit initiatives get no objective and are never sent to the model. CSM selection is not read here; it is an
optional input to Render.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, create_model

from .. import atomic, guards
from ..llm import LLM, LLMError, ModelsConfig, TokenCounter, cost_usd, estimate_tokens
from ..models import (
    COMPANY_MEASURE_LABEL, CRITERION_BLANK, TARGET_BLANK, WINDOWS, DraftGuardReport, DraftOutput, DraftTask,
    ExtractOutput, GuardEntry, ItemFlag, KeyBenefit, LLMCriterion, LLMDraftOutput, LLMSkipped, LLMTaskPick, MapOutput,
    Objective, SuccessCriterion, UsageRecord,
)
from ..paths import PROJECT_ROOT, CompanyPaths

PROMPTS = Path(__file__).resolve().parents[1] / "prompts"
TASKS_FILE = PROJECT_ROOT / "config" / "draft_tasks.yaml"
EXTRACT_NAME = "02_extract.json"
MAP_NAME = "03_map.json"
OUTPUT_NAME = "04_draft.json"
GUARD_REPORT_NAME = "04_draft_guard_report.json"
USAGE_LOG_NAME = "usage_log.jsonl"
RESPONSES_DIR = "llm"
MAX_CRITERIA_PER_OBJECTIVE = 4
MAX_TASKS_PER_OBJECTIVE = 5
FIT_LABELS = {"full": "full fit: the initiative is about what the capability does",
              "partial": "partial fit: supports the work, does not deliver the outcome"}
DEFAULT_TASK = ("T-DISCOVERY", "CSM", "Day 0-30")
BLANK_VALIDATION_QUESTION = "What would success look like for this initiative, and how is it measured today?"
SLOT = re.compile(r"\{(\w+)\}")


class DraftError(Exception):
    pass


# ---------------------------------------------------------------- inputs

def load_inputs(paths: CompanyPaths) -> tuple[ExtractOutput, MapOutput]:
    e_path, m_path = paths.stages / EXTRACT_NAME, paths.stages / MAP_NAME
    if not e_path.exists() or not m_path.exists():
        raise DraftError("Run `spg extract` and `spg map` first: 02_extract.json or 03_map.json is missing.")
    extract = ExtractOutput.model_validate_json(e_path.read_text())
    mapping = MapOutput.model_validate_json(m_path.read_text())
    if mapping.extract_generated_at != extract.generated_at:
        raise DraftError("03_map.json was made from a different 02_extract.json; re-run or replay Map first.")
    return extract, mapping


def load_tasks(path: Path = TASKS_FILE) -> dict:
    data = yaml.safe_load(path.read_text())
    for t in data["tasks"]:
        bad = set(SLOT.findall(t["text"] + t["question"])) - {"initiative", "capability"}
        if bad:
            raise DraftError(f"{path.name}: task {t['id']} uses unknown slot(s) {sorted(bad)}")
    return data


def model_tasks(tasks: dict) -> list[dict]:
    """Catalog tasks the model may pick (tasks added by code are not offered)."""
    return [t for t in tasks["tasks"] if not t.get("added_by_code")]


def eligible(mapping: MapOutput) -> list[KeyBenefit]:
    return [b for b in mapping.key_benefits if b.fit in ("full", "partial")]


def reference_table(extract: ExtractOutput, benefits: list[KeyBenefit]) -> dict[str, dict]:
    """Reference id -> {initiative_id, kind, text, citations}. Only fitted initiatives appear."""
    by_id = {i.id: i for i in extract.initiatives}
    table = {}
    for b in benefits:
        initiative = by_id[b.initiative_id]
        for n, s in enumerate(initiative.sentences, start=1):
            table[f"{initiative.id}.s{n}"] = {"initiative_id": initiative.id, "kind": "initiative", "text": s.text,
                                              "citations": s.citations, "labels": list(initiative.labels)}
        for n, s in enumerate(b.sentences, start=1):
            table[f"{b.id}.s{n}"] = {"initiative_id": b.initiative_id, "kind": "benefit", "text": s.text,
                                     "citations": s.citations,
                                     "labels": ["unconfirmed product detail"] if any(c.unconfirmed for c in s.citations) else []}
    return table


def capability_names(benefits: list[KeyBenefit]) -> dict[str, str]:
    return {c.capability_id: c.capability_name for b in benefits for s in b.sentences for c in s.citations}


def render_user(benefits: list[KeyBenefit], table: dict[str, dict], tasks: dict) -> str:
    names = capability_names(benefits)
    parts = ["<initiatives>"]
    for b in benefits:
        parts.append(f'<initiative id="{b.initiative_id}" title="{b.initiative_title}" fit="{b.fit}">')
        parts += [f'<sentence ref="{rid}">{r["text"]}</sentence>' for rid, r in table.items()
                  if r["initiative_id"] == b.initiative_id and r["kind"] == "initiative"]
        parts += [f'<benefit ref="{rid}">{r["text"]}</benefit>' for rid, r in table.items()
                  if r["initiative_id"] == b.initiative_id and r["kind"] == "benefit"]
        parts += [f'<capability id="{c}">{names.get(c, c)}</capability>' for c in b.capability_ids]
        parts.append("</initiative>")
    parts += ["</initiatives>", "", "<tasks>"]
    parts += [f'<task id="{t["id"]}">{t["text"]}</task>' for t in model_tasks(tasks)]
    parts += ["</tasks>", f"<roles>{', '.join(tasks['roles'])}</roles>", f"<windows>{', '.join(WINDOWS)}</windows>"]
    return "\n".join(parts)


def output_model(initiative_ids: list[str], ref_ids: list[str], task_ids: list[str], capability_ids: list[str],
                 roles: list[str]) -> type[BaseModel]:
    """Structured-output model whose enums allow only real ids, roles, and windows: one entry per initiative, with
    its criteria and tasks, or an empty criteria list and a skip_reason."""
    Criterion = create_model(
        "CriterionOut",
        text=(str, ...),
        refs=(list[Literal[tuple(ref_ids)]], ...),
        criterion_type=(Literal["business_outcome", "product_usage"], ...),
        indicator_type=(Literal["leading", "lagging"], ...),
        measure=(str, ...),
        direction=(Literal["increase", "decrease", "reach"], ...),
        baseline_question=(str, ...),
        validation_question=(str, ...),
    )
    Task = create_model(
        "TaskOut",
        task_id=(Literal[tuple(task_ids)], ...),
        capability_id=(Literal[tuple([""] + capability_ids)], ...),
        responsible_role=(Literal[tuple(roles)], ...),
        window=(Literal[WINDOWS], ...),
    )
    Entry = create_model(
        "InitiativeDraftOut",
        initiative_id=(Literal[tuple(initiative_ids)], ...),
        criteria=(list[Criterion], ...),
        tasks=(list[Task], ...),
        skip_reason=(str, ...),
    )
    return create_model("DraftOutputOut", initiatives=(list[Entry], ...))


def flatten(output: dict) -> LLMDraftOutput:
    """The model's per-initiative entries as flat lists. A saved response from before per-initiative entries
    (flat criteria and tasks) is read as is."""
    if "initiatives" not in output:
        return LLMDraftOutput.model_validate(output)
    criteria, tasks, skipped = [], [], []
    for entry in output["initiatives"]:
        iid = entry["initiative_id"]
        criteria += [LLMCriterion(initiative_id=iid, **c) for c in entry["criteria"]]
        tasks += [LLMTaskPick(initiative_id=iid, **t) for t in entry["tasks"]]
        if not entry["criteria"]:
            skipped.append(LLMSkipped(initiative_id=iid, reason=entry.get("skip_reason", "").strip()))
    return LLMDraftOutput(criteria=criteria, tasks=tasks, skipped=skipped)


def system_prompt(company: str) -> str:
    path = PROMPTS / "draft.md"
    if not path.exists():
        raise DraftError("prompts/draft.md is missing.")
    return path.read_text().replace("{company}", company)


# ---------------------------------------------------------------- checks

def _hard_drop(fields: dict[str, str], ref_texts: list[str]) -> str | None:
    """Reason to drop a criterion: a price, a plan tier, a date (a month, a specific day, a quarter label, or a year
    its references do not contain), or a figure its references lack."""
    ref_years = {m for t in ref_texts for kind, m in guards.date_mentions(t) if kind == "year"}
    for name, value in fields.items():
        if guards.find_currency_amounts(value):
            return f"{name} states a price or currency amount"
        tier = guards.plan_tier_phrase(value)
        if tier:
            return f"{name} states a plan tier ({tier!r})"
        for kind, when in guards.date_mentions(value):
            if kind == "year" and when in ref_years:
                continue
            what = "a year its references do not contain" if kind == "year" else f"a {kind}"
            return f"{name} states {what} ({when!r}); timelines are relative only"
    for name in ("text", "measure"):
        figures = guards.added_figures(fields[name], ref_texts)
        if figures:
            return f"{name} states a figure its references do not contain ({', '.join(figures)})"
    return None


def process_criterion(raw, objective_id: str, k: int, table: dict[str, dict], vocabulary: list[str],
                      report: DraftGuardReport, title: str) -> SuccessCriterion | None:
    iid = raw.initiative_id

    def drop(reason):
        report.dropped_criteria.append(GuardEntry(item_id=iid, kind="criterion", title=title, reason=f'"{raw.text}" ({reason})'))

    refs = []
    for r in dict.fromkeys(raw.refs):
        ref = table.get(r)
        if ref is None or ref["initiative_id"] != iid:
            report.rejected_refs.append(GuardEntry(item_id=iid, kind="criterion", title=title,
                                                   reason=f"{r}: {'unknown reference' if ref is None else 'belongs to ' + ref['initiative_id']}"))
        else:
            refs.append(r)
    kinds = {table[r]["kind"] for r in refs}
    measure = raw.measure.strip()
    if not refs:
        return drop("no valid reference")
    if not measure:
        return drop("no measure")
    if raw.criterion_type == "product_usage" and "benefit" not in kinds:
        return drop("product_usage criterion does not reference a benefit sentence")
    if raw.criterion_type == "business_outcome" and "initiative" not in kinds:
        return drop("business_outcome criterion does not reference an initiative sentence")
    if raw.criterion_type == "business_outcome" and raw.indicator_type != "lagging":
        return drop("business_outcome criterion must be lagging (the product does not deliver the outcome)")
    ref_texts = [table[r]["text"] for r in refs]
    fields = {"text": raw.text, "measure": measure, "baseline_question": raw.baseline_question,
              "validation_question": raw.validation_question}
    reason = _hard_drop(fields, ref_texts)
    if reason:
        return drop(reason)

    flags = []
    support = guards.statement_support(raw.text, ref_texts)
    scored = [x.score for x in support if x.score is not None]
    for weak in guards.unsupported_statements(raw.text, ref_texts):
        flags.append(ItemFlag(type="unsupported_statement",
                              detail=f'support {weak.score:.0%}: not in its references: {", ".join(weak.missing)}'))
    for name in ("text", "measure", "baseline_question", "validation_question"):
        added = [a for a in guards.rationale_additions(fields[name], ref_texts, vocabulary) if not a.startswith("figure")]
        if added:
            flags.append(ItemFlag(type="adds_facts", detail=f"{name} adds {', '.join(added)}"))
        promise = guards.outcome_promise(fields[name])
        if promise:
            flags.append(ItemFlag(type="outcome_promise", detail=f"{name}: {promise!r}"))
        customer = guards.implies_customer(fields[name])
        if customer:
            flags.append(ItemFlag(type="implies_customer", detail=f"{name}: {customer!r}; the plan is hypothetical"))
    clause = guards.goal_clause(raw.text)
    if clause:
        flags.append(ItemFlag(type="goal_clause", detail=f"text: {clause!r}; state what the product does or what is measured, "
                                                         "without tying it to a company goal"))
    for name in ("text", "measure"):
        generic = guards.generic_criterion(fields[name])
        if generic:
            flags.append(ItemFlag(type="generic_criterion", detail=f"{name}: {generic!r}; name something countable"))
    for f in flags:
        report.flagged.append(GuardEntry(item_id=iid, kind="criterion", title=title, reason=f"{f.type}: {f.detail}"))

    labels = list(dict.fromkeys(l for r in refs for l in table[r]["labels"]))
    if raw.criterion_type == "business_outcome":
        labels.append(COMPANY_MEASURE_LABEL)
    citations = list({c.chunk_id: c for r in refs if table[r]["kind"] == "initiative" for c in table[r]["citations"]}.values())
    product = list({c.claim_id: c for r in refs if table[r]["kind"] == "benefit" for c in table[r]["citations"]}.values())
    return SuccessCriterion(
        id=f"{objective_id}-C{k}", text=raw.text.strip(), refs=refs, citations=citations, product_citations=product,
        criterion_type=raw.criterion_type, indicator_type=raw.indicator_type, measure=measure, direction=raw.direction,
        proposed_target=f"{raw.direction.capitalize()}: {measure}. Target: {TARGET_BLANK}",
        needs_baseline=True, baseline_question=raw.baseline_question.strip(),
        validation_question=raw.validation_question.strip(), support_score=min(scored) if scored else None,
        labels=labels, flags=flags,
    )


def blank_criterion(objective_id: str) -> SuccessCriterion:
    return SuccessCriterion(id=f"{objective_id}-C1", text=CRITERION_BLANK, owner="csm", provenance="csm-input",
                            needs_baseline=False, validation_question=BLANK_VALIDATION_QUESTION)


def join_names(names: list[str]) -> str:
    if len(names) <= 2:
        return " and ".join(names)
    return ", ".join(names[:-1]) + ", and " + names[-1]


def display_name(capability_id: str, tasks: dict, names: dict[str, str]) -> str:
    return (tasks.get("display_names") or {}).get(capability_id) or names.get(capability_id, capability_id)


def fill(template: str, initiative: str, capability: str) -> str:
    return template.replace("{initiative}", initiative).replace("{capability}", capability)


def build_task(task_id: str, role: str, window: str, capability_id: str, b: KeyBenefit, tasks: dict,
               names: dict[str, str], n: int, objective_id: str) -> DraftTask:
    t = next(t for t in tasks["tasks"] if t["id"] == task_id)
    if t.get("covers_all_capabilities"):
        capability = join_names([display_name(c, tasks, names) for c in b.capability_ids])
    else:
        capability = display_name(capability_id, tasks, names) if capability_id else ""
    return DraftTask(id=f"{objective_id}-T{n}", task_id=task_id, text=fill(t["text"], b.initiative_title, capability),
                     responsible_role=role, window=window,
                     validation_question=fill(t["question"], b.initiative_title, capability))


def needs_capability(task: dict) -> bool:
    return "{capability}" in task["text"] or "{capability}" in task["question"]


# ---------------------------------------------------------------- assembly

def assemble(paths: CompanyPaths, extract: ExtractOutput, mapping: MapOutput, tasks: dict, parsed: LLMDraftOutput,
             usage: list[UsageRecord], profile: str, models_config: ModelsConfig, now: datetime, *,
             replayed_from: str | None = None, no_objectives_reason: str | None = None) -> DraftOutput:
    benefits = eligible(mapping)
    table = reference_table(extract, benefits)
    names = capability_names(benefits)
    vocabulary = [extract.company] + list(names.values()) + [b.initiative_title for b in benefits]
    catalog = {t["id"]: t for t in model_tasks(tasks)}
    code_tasks = [t for t in tasks["tasks"] if t.get("added_by_code")]
    report = DraftGuardReport(generated_at=now, dropped_criteria=[], rejected_refs=[], flagged=[], rejected_tasks=[], fallbacks=[])
    fitted = {b.initiative_id for b in benefits}
    for raw in parsed.criteria:
        if raw.initiative_id not in fitted:
            report.dropped_criteria.append(GuardEntry(item_id=raw.initiative_id, kind="criterion", title=raw.initiative_id,
                                                      reason="initiative has no full or partial fit"))
    for raw in parsed.tasks:
        if raw.initiative_id not in fitted:
            report.rejected_tasks.append(GuardEntry(item_id=raw.initiative_id, kind="task", title=raw.initiative_id,
                                                    reason="initiative has no full or partial fit"))

    reasons = {x.initiative_id: x.reason for x in parsed.skipped}
    for b in benefits:
        if not any(c.initiative_id == b.initiative_id for c in parsed.criteria):
            why = reasons.get(b.initiative_id)
            detail = (f": {why}" if why else ": no reason given") if b.initiative_id in reasons else ": no entry in the response"
            report.skipped_initiatives.append(GuardEntry(item_id=b.initiative_id, kind="objective", title=b.initiative_title,
                                                         reason=f"initiative skipped by model{detail}"))

    objectives = []
    for n, b in enumerate(benefits, start=1):
        oid, title = f"O-{n:02d}", b.initiative_title
        criteria = []
        for raw in (c for c in parsed.criteria if c.initiative_id == b.initiative_id):
            if len(criteria) >= MAX_CRITERIA_PER_OBJECTIVE:
                report.dropped_criteria.append(GuardEntry(item_id=b.initiative_id, kind="criterion", title=title,
                                                          reason=f'"{raw.text}" (more than {MAX_CRITERIA_PER_OBJECTIVE} criteria)'))
                continue
            c = process_criterion(raw, oid, len(criteria) + 1, table, vocabulary, report, title)
            if c:
                criteria.append(c)
        if not criteria:
            criteria = [blank_criterion(oid)]
            report.fallbacks.append(GuardEntry(item_id=b.initiative_id, kind="objective", title=title,
                                               reason="no criterion survived; CSM blank criterion used"))

        built, seen = [], set()
        for raw in (t for t in parsed.tasks if t.initiative_id == b.initiative_id):
            spec = catalog.get(raw.task_id)
            if spec is None:
                report.rejected_tasks.append(GuardEntry(item_id=b.initiative_id, kind="task", title=title,
                                                        reason=f"{raw.task_id} is added by code, not chosen by the model"))
                continue
            per_capability = needs_capability(spec) and not spec.get("covers_all_capabilities")
            cap = raw.capability_id if per_capability else ""
            reason = None
            if per_capability and cap not in b.capability_ids:
                reason = f"{raw.task_id} needs a capability from this mapping ({', '.join(b.capability_ids)}); got {raw.capability_id or 'none'}"
            elif (raw.task_id, cap) in seen:
                reason = f"duplicate {raw.task_id}"
            elif len(built) >= MAX_TASKS_PER_OBJECTIVE:
                reason = f"more than {MAX_TASKS_PER_OBJECTIVE} tasks"
            if reason:
                report.rejected_tasks.append(GuardEntry(item_id=b.initiative_id, kind="task", title=title, reason=reason))
                continue
            seen.add((raw.task_id, cap))
            built.append(build_task(raw.task_id, raw.responsible_role, raw.window, cap, b, tasks, names, len(built) + 1, oid))
        if not built:
            built = [build_task(*DEFAULT_TASK, "", b, tasks, names, 1, oid)]
            report.fallbacks.append(GuardEntry(item_id=b.initiative_id, kind="objective", title=title,
                                               reason="no valid task; default discovery task used"))
        for t in code_tasks:  # e.g. agree baselines, in every objective
            built.append(build_task(t["id"], t["added_by_code"]["role"], t["added_by_code"]["window"], "", b, tasks, names,
                                    len(built) + 1, oid))
        built.sort(key=lambda t: WINDOWS.index(t.window))
        for k, t in enumerate(built, start=1):
            t.id = f"{oid}-T{k}"

        objectives.append(Objective(
            id=oid, title=f"Explore support for: {title}", linked_initiative_ids=[b.initiative_id], benefit_id=b.id,
            capability_ids=list(b.capability_ids), fit=b.fit,
            labels=list(dict.fromkeys(list(b.labels) + [FIT_LABELS[b.fit]])), success_criteria=criteria, tasks=built,
        ))

    warnings = []
    for name, count in (("initiatives skipped by model", len(report.skipped_initiatives)),
                        ("dropped criteria", len(report.dropped_criteria)), ("rejected references", len(report.rejected_refs)),
                        ("flags", len(report.flagged)), ("rejected tasks", len(report.rejected_tasks)),
                        ("fallbacks", len(report.fallbacks))):
        if count:
            warnings.append(f"{count} {name}; see {GUARD_REPORT_NAME}.")
    output = DraftOutput(
        generated_at=now, company=extract.company, profile=profile, extract_generated_at=extract.generated_at,
        map_generated_at=mapping.generated_at, map_profile=mapping.profile, map_replayed_from=mapping.replayed_from,
        objectives=objectives, not_drafted=[b.initiative_id for b in mapping.key_benefits if b.fit == "no_fit"],
        usage=usage, total_cost_usd=round(sum(u.cost_usd for u in usage), 6), prices_as_of=models_config.prices_as_of,
        warnings=warnings, replayed_from=replayed_from, no_objectives_reason=no_objectives_reason,
    )
    paths.stages.mkdir(parents=True, exist_ok=True)
    atomic.write_text(paths.stages / OUTPUT_NAME, output.model_dump_json(indent=2) + "\n")
    atomic.write_text(paths.stages / GUARD_REPORT_NAME, report.model_dump_json(indent=2) + "\n")
    return output


# ---------------------------------------------------------------- run and replay

def run_draft(paths: CompanyPaths, llm: LLM | None, models_config: ModelsConfig, profile: str, *, dry_run: bool = False,
              counter: TokenCounter | None = None, system: str | None = None, tasks: dict | None = None):
    extract, mapping = load_inputs(paths)
    tasks = tasks or load_tasks()
    benefits = eligible(mapping)
    if not benefits:
        raise DraftError("Map found no full or partial fit; there is nothing to draft. No API call was made.")
    table = reference_table(extract, benefits)
    system = system or system_prompt(extract.company)
    user = render_user(benefits, table, tasks)
    caps = sorted({c for b in benefits for c in b.capability_ids})
    model_cls = output_model([b.initiative_id for b in benefits], list(table), [t["id"] for t in model_tasks(tasks)], caps,
                             list(tasks["roles"]))
    settings = models_config.settings(profile, "draft")

    source, note, n_in = "estimated", None, None
    if counter is not None:
        try:
            n_in = counter.count_input_tokens(system=system, user=user, output_model=model_cls, model=settings.model)
            source = "counted"
        except LLMError as exc:
            note = f"exact count unavailable ({exc}); used calibrated estimate"
    if n_in is None:
        n_in = estimate_tokens(system + user, models_config.chars_per_token)
    estimate = {"model": settings.model, "input_tokens": n_in, "input_source": source, "note": note,
                "output_tokens": settings.expected_output_tokens,
                "cost_usd": cost_usd(models_config.prices, settings.model, input_tokens=n_in, output_tokens=settings.expected_output_tokens)}
    if dry_run:
        return estimate, None
    if estimate["cost_usd"] > models_config.max_cost_usd_per_run:
        raise DraftError(f"Estimated cost ${estimate['cost_usd']:.2f} exceeds the limit of ${models_config.max_cost_usd_per_run:.2f} "
                         "(max_cost_usd_per_run in config/models.yaml). No API call was made.")
    if llm is None:
        raise DraftError("No model client provided.")

    now = datetime.now(timezone.utc).replace(microsecond=0)
    run_dir = paths.raw / RESPONSES_DIR / f"draft-{now:%Y%m%dT%H%M%SZ}-{profile}"

    def log_run(status, usage, error=None):
        paths.stages.mkdir(parents=True, exist_ok=True)
        with (paths.stages / USAGE_LOG_NAME).open("a") as fh:
            fh.write(json.dumps({"stage": "draft", "at": now.isoformat(), "profile": profile, "status": status, "error": error,
                                 "estimated_cost_usd": round(estimate["cost_usd"], 6),
                                 "cost_usd": round(sum(u.cost_usd for u in usage), 6),
                                 "responses": str(run_dir.relative_to(paths.root)), "calls": [u.model_dump() for u in usage]}) + "\n")

    try:
        result = llm.complete(system=system, user=user, output_model=model_cls, settings=settings)
    except LLMError as exc:
        log_run("failed", [], f"draft: {exc}")
        raise DraftError(f"Model call failed: {exc}. Nothing was saved except the usage log.") from exc
    raw_output = result.parsed.model_dump(mode="json")
    parsed = flatten(raw_output)
    run_dir.mkdir(parents=True, exist_ok=True)
    atomic.write_text(run_dir / "draft.json", json.dumps({
        "call": "draft", "profile": profile, "model": result.model, "effort": settings.effort, "stop_reason": result.stop_reason,
        "usage": {"input_tokens": result.input_tokens, "output_tokens": result.output_tokens,
                  "cache_read_input_tokens": result.cache_read_input_tokens,
                  "cache_creation_input_tokens": result.cache_creation_input_tokens},
        "output": raw_output,
    }, indent=2, ensure_ascii=False) + "\n")
    usage = [UsageRecord(
        call="draft", model=result.model, effort=settings.effort, input_tokens=result.input_tokens, output_tokens=result.output_tokens,
        cache_read_input_tokens=result.cache_read_input_tokens, cache_creation_input_tokens=result.cache_creation_input_tokens,
        cost_usd=cost_usd(models_config.prices, settings.model, input_tokens=result.input_tokens, output_tokens=result.output_tokens,
                          cache_read_input_tokens=result.cache_read_input_tokens, cache_creation_input_tokens=result.cache_creation_input_tokens),
        estimated_input_tokens=estimate["input_tokens"], estimated_cost_usd=estimate["cost_usd"],
    )]
    log_run("ok", usage)
    return estimate, assemble(paths, extract, mapping, tasks, parsed, usage, profile, models_config, now)


NO_FIT_REASON = "Map found no full or partial fit, so no objectives were drafted and no model call was made."


def write_without_fits(paths: CompanyPaths, models_config: ModelsConfig, tasks: dict | None = None) -> DraftOutput:
    """04_draft.json with no objectives, for a plan where every initiative is no_fit (no model call)."""
    extract, mapping = load_inputs(paths)
    if eligible(mapping):
        raise DraftError("Map found fits; run Draft normally.")
    now = datetime.now(timezone.utc).replace(microsecond=0)
    return assemble(paths, extract, mapping, tasks or load_tasks(), LLMDraftOutput(criteria=[], tasks=[]), [], "none",
                    models_config, now, no_objectives_reason=NO_FIT_REASON)


def latest_response_dir(paths: CompanyPaths) -> Path | None:
    root = paths.raw / RESPONSES_DIR
    dirs = sorted(d for d in root.glob("draft-*") if d.is_dir()) if root.exists() else []
    return dirs[-1] if dirs else None


def replay_draft(paths: CompanyPaths, models_config: ModelsConfig, response_dir: Path | None = None, *,
                 tasks: dict | None = None) -> DraftOutput:
    """Re-apply the current checks to a saved Draft response. No API call of any kind."""
    response_dir = response_dir or latest_response_dir(paths)
    if response_dir is None or not response_dir.resolve().exists():
        raise DraftError(f"No saved Draft responses found under {paths.raw / RESPONSES_DIR}.")
    response_dir = response_dir.resolve()
    record = json.loads((response_dir / "draft.json").read_text())
    parsed = flatten(record["output"])
    u = record.get("usage", {})
    usage = [UsageRecord(call="draft", model=record.get("model", "unknown"), effort=record.get("effort", "unknown"),
                         input_tokens=u.get("input_tokens", 0), output_tokens=u.get("output_tokens", 0),
                         cost_usd=0.0, estimated_input_tokens=0, estimated_cost_usd=0.0)]
    extract, mapping = load_inputs(paths)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    return assemble(paths, extract, mapping, tasks or load_tasks(), parsed, usage, record.get("profile", "unknown"),
                    models_config, now, replayed_from=str(response_dir.relative_to(paths.root.resolve())))
