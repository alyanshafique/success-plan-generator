"""`spg run` resume decisions, overwrite protection, stop conditions, the zero-fit path, personal names, and atomic
writes. No network or API calls: stage outputs come from the Render test fixture (fake models)."""

import json
import os

import pytest

from success_plan import atomic
from success_plan import run as rn
from success_plan.llm import load_models_config
from success_plan.stages import draft as dr
from success_plan.stages import map as mp
from success_plan.stages import render as rd
from test_render import paths  # noqa: F401  (fixture: stages 01-04 and raw chunks for "Test Co")

CONFIG_PATH = rn.product_config_path(rn.DEFAULT_PRODUCT)


def opts(**kw):
    kw.setdefault("company", "Test Co")
    return rn.RunOptions(**kw)


# ---------------------------------------------------------------- resume decisions

def test_everything_saved_and_consistent_is_reused(paths):
    assert rn.decide(opts(), paths, CONFIG_PATH) == {"retrieve": "reuse", "extract": "reuse", "map": "reuse",
                                                     "draft": "reuse", "render": "run"}


def test_force_reruns_every_stage(paths):
    assert set(rn.decide(opts(force=True), paths, CONFIG_PATH).values()) == {"run"}


def test_from_reruns_that_stage_and_later(paths):
    assert rn.decide(opts(from_stage="map"), paths, CONFIG_PATH) == {
        "retrieve": "reuse", "extract": "reuse", "map": "run", "draft": "run", "render": "run"}


def test_from_needs_the_earlier_outputs(paths):
    (paths.stages / mp.OUTPUT_NAME).unlink()
    with pytest.raises(rn.RunStop, match="--from draft needs a saved map output"):
        rn.decide(opts(from_stage="draft"), paths, CONFIG_PATH)


def test_a_missing_or_inconsistent_stage_reruns_it_and_everything_after(paths):
    m = json.loads((paths.stages / mp.OUTPUT_NAME).read_text())
    m["extract_generated_at"] = "2020-01-01T00:00:00Z"
    (paths.stages / mp.OUTPUT_NAME).write_text(json.dumps(m))
    assert rn.decide(opts(), paths, CONFIG_PATH) == {"retrieve": "reuse", "extract": "reuse", "map": "run",
                                                     "draft": "run", "render": "run"}
    (paths.stages / "02_extract.json").unlink()
    assert rn.decide(opts(), paths, CONFIG_PATH)["extract"] == "run"


def test_a_different_product_config_reruns_map(paths, tmp_path):
    other = tmp_path / "product_other.yaml"
    other.write_text(CONFIG_PATH.read_text())
    assert rn.decide(opts(), paths, other)["map"] == "run"


def test_as_of_change_reruns_retrieve_except_in_a_dry_run(paths):
    from datetime import date
    assert rn.decide(opts(as_of=date(2027, 1, 1)), paths, CONFIG_PATH)["retrieve"] == "run"
    assert rn.decide(opts(as_of=date(2027, 1, 1), dry_run=True), paths, CONFIG_PATH)["retrieve"] == "reuse"
    assert rn.decide(opts(refresh_sources=True), paths, CONFIG_PATH)["retrieve"] == "run"


# ---------------------------------------------------------------- overwrite protection

def test_existing_outputs_need_force_but_a_dry_run_is_allowed(paths):
    rd.render(paths)
    with pytest.raises(rn.RunStop, match="already has outputs .*Use --force to replace them"):
        rn.check_overwrite(opts(), paths)
    rn.check_overwrite(opts(dry_run=True), paths)
    rn.check_overwrite(opts(force=True), paths)


def test_committed_sample_is_named_as_such():
    expedia = rn.CompanyPaths(root=rn.PROJECT_ROOT, slug="expedia")
    with pytest.raises(rn.RunStop, match="output/expedia is a committed sample; use --force only if you mean to replace it"):
        rn.check_overwrite(opts(company="Expedia"), expedia)


# ---------------------------------------------------------------- stop conditions

def test_extract_stops_when_more_than_half_of_sentences_are_dropped(paths):
    report = {"dropped_sentences": [{"item_id": "I-01", "kind": "initiative", "title": "t", "reason": "r"}] * 50}
    (paths.stages / "02_extract_guard_report.json").write_text(json.dumps(report))
    with pytest.raises(rn.RunStop, match=r'more than half\. .*spg run "Test Co" --from map'):
        rn.extract_checks(paths, opts(), accept_drops=False)
    assert "dropped" in rn.extract_checks(paths, opts(), accept_drops=True)  # --from map continues
    assert rn.EXTRACT_DROP_LIMIT == 0.5


def test_extract_stops_with_no_initiatives(paths):
    e = json.loads((paths.stages / "02_extract.json").read_text())
    e["initiatives"] = []
    (paths.stages / "02_extract.json").write_text(json.dumps(e))
    with pytest.raises(rn.RunStop, match="nothing to map"):
        rn.extract_checks(paths, opts(), accept_drops=False)


def test_draft_stops_when_every_criterion_is_the_csm_blank(paths):
    d = json.loads((paths.stages / dr.OUTPUT_NAME).read_text())
    for o in d["objectives"]:
        o["success_criteria"] = [{"id": f"{o['id']}-C1", "text": "[CSM to complete: x]", "owner": "csm",
                                  "provenance": "csm-input", "needs_baseline": False, "validation_question": "q"}]
    (paths.stages / dr.OUTPUT_NAME).write_text(json.dumps(d))
    with pytest.raises(rn.RunStop, match="Draft produced no success criteria"):
        rn.draft_checks(paths)


def test_zero_fits_write_a_draft_with_no_objectives_and_render_the_plan(paths):
    m = json.loads((paths.stages / mp.OUTPUT_NAME).read_text())
    for b in m["key_benefits"]:
        b.update(fit="no_fit", capability_ids=[], sentences=[], text="", no_fit_reason="Nothing fits.", gap_ids=[])
    (paths.stages / mp.OUTPUT_NAME).write_text(json.dumps(m))
    assert rn.fit_count(paths) == 0
    out = dr.write_without_fits(paths, load_models_config())
    assert out.objectives == [] and out.no_objectives_reason == dr.NO_FIT_REASON and out.usage == []
    assert len(out.not_drafted) == len(m["key_benefits"])
    plan_json, plan_md, report = rd.render(paths)
    plan = json.loads(plan_json.read_text())
    assert plan["objectives"] == [] and "add objectives for initiatives the tool did not map" in plan_md.read_text()
    assert all(b["fit"] == "no_fit" for b in plan["key_benefits"])


# ---------------------------------------------------------------- personal names, atomic writes, timing

def test_personal_name_candidates_from_flags_and_officer_8ks(paths, monkeypatch):
    monkeypatch.setattr(rn, "load_private_names", lambda: ["Known Person"])
    e = json.loads((paths.stages / "02_extract.json").read_text())
    e["changes"][0]["flags"] = [{"type": "personal_name", "detail": "surname from 'Mr. Example'"}]
    (paths.stages / "02_extract.json").write_text(json.dumps(e))
    m = json.loads((paths.stages / "01_retrieve.json").read_text())
    m["eight_ks"]["events"] = [{"accession": "0000000001-26-000009", "filing_date": "2026-04-23",
                                "event_date": "2026-04-17", "items": ["5.02"], "item_labels": ["x"], "url": "u",
                                "chunk_count": 1}]
    (paths.stages / "01_retrieve.json").write_text(json.dumps(m))
    with paths.chunks_file.open("a") as fh:
        fh.write(json.dumps({"chunk_id": "8k-0000000001-26-000009-main-p001", "source_id": "8k-0000000001-26-000009",
                             "text": "The Board appointed Jordan Q. Sample as Chief Financial Officer. Known Person, "
                                     "age 50, resigned. Mr. Sample will serve from May 11."}) + "\n")
    names = rn.personal_name_candidates(paths)
    assert "S-01 flag personal_name: surname from 'Mr. Example'" in names
    assert "Jordan Q. Sample" in names and "Mr. Sample" in names
    assert not any("Known Person" in n for n in names)  # already on the private list
    assert not any("Chief Financial" in n for n in names)


def test_atomic_writes_leave_no_temp_files(tmp_path):
    target = tmp_path / "sub" / "out.json"
    atomic.write_text(target, "one")
    atomic.write_text(target, "two")
    atomic.write_bytes(tmp_path / "b.bin", b"x")
    atomic.save_with(tmp_path / "c.txt", lambda p: p.write_text("saved"))
    assert target.read_text() == "two" and (tmp_path / "c.txt").read_text() == "saved"
    assert not [f for f in tmp_path.rglob("*") if ".tmp-" in f.name]


def test_an_interrupted_write_keeps_the_previous_file(tmp_path, monkeypatch):
    target = tmp_path / "out.json"
    atomic.write_text(target, "previous")

    def fail(*a):
        raise OSError("disk full")
    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError):
        atomic.write_text(target, "new")
    assert target.read_text() == "previous"
