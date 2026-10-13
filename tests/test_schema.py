"""The plan schema's optional sentence-level citations on $defs/item."""

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

SCHEMA = json.loads((Path(__file__).resolve().parents[1] / "schema" / "success_plan_schema.json").read_text())
ITEM = Draft202012Validator({"$schema": SCHEMA["$schema"], "$defs": SCHEMA["$defs"], "$ref": "#/$defs/item"})

CITATION = {"source_type": "filing", "title": "Expedia Group, Inc. Form 10-K", "location": "Item 7 MD&A",
            "url": "https://www.sec.gov/x", "date": "2026-02-13"}


def item(**extra):
    base = {"id": "I-01", "text": "Advertising grew. A unit sells ads.", "owner": "shared", "provenance": "sourced",
            "citations": [CITATION]}
    return {**base, **extra}


def test_schema_itself_is_valid():
    Draft202012Validator.check_schema(SCHEMA)


def test_item_without_sentences_still_valid():
    assert not list(ITEM.iter_errors(item()))


def test_item_with_sentences_valid():
    sentences = [{"text": "Advertising grew.", "citations": [CITATION]},
                 {"text": "A unit sells ads.", "citations": [CITATION, {**CITATION, "location": "Item 1 Business"}]}]
    assert not list(ITEM.iter_errors(item(sentences=sentences)))


@pytest.mark.parametrize("bad", [
    [{"text": "No citation.", "citations": []}],  # every sentence needs a citation
    [{"text": "Three.", "citations": [CITATION] * 3}],  # at most two
    [{"text": "Extra field.", "citations": [CITATION], "support_score": 0.9}],  # text and citations only
    [{"citations": [CITATION]}],  # text required
])
def test_bad_sentences_rejected(bad):
    assert list(ITEM.iter_errors(item(sentences=bad)))


# ---------------------------------------------------------------- key benefits (Map)

from success_plan.models import PLAN_CONFIRMATION_BLANK  # noqa: E402

KEY_BENEFIT = Draft202012Validator({"$schema": SCHEMA["$schema"], "$defs": SCHEMA["$defs"],
                                    **SCHEMA["properties"]["key_benefits"]["items"]})


def benefit(**extra):
    base = {"id": "B-01", "text": "Plans combine work items into one view.", "owner": "claude", "provenance": "inferred",
            "initiative_id": "I-02", "fit": "partial", "capability_ids": ["JIRA-PLANNING-ROADMAPS"]}
    return {**base, **extra}


def test_key_benefit_with_rationale_and_plan_blank_valid():
    assert not list(KEY_BENEFIT.iter_errors(benefit(
        fit_rationale="Coordinating delivery across teams is the work Plans support.", plan_confirmation=PLAN_CONFIRMATION_BLANK)))
    assert not list(KEY_BENEFIT.iter_errors(benefit()))  # both optional


def test_plan_blank_must_be_exact():
    assert list(KEY_BENEFIT.iter_errors(benefit(plan_confirmation="Available on Premium")))


def test_no_fit_still_requires_reason():
    assert list(KEY_BENEFIT.iter_errors(benefit(fit="no_fit", capability_ids=[])))
    assert not list(KEY_BENEFIT.iter_errors(benefit(fit="no_fit", capability_ids=[], no_fit_reason="GAP-CICD")))
