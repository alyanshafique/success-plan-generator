"""Live smoke test against EDGAR and GDELT. Run with: uv run pytest -m live"""

import os
from datetime import date

import pytest

from success_plan.paths import CompanyPaths
from success_plan.settings import load_env, sec_user_agent
from success_plan.stages.retrieve import run_retrieve
from success_plan.stages.retrieve.http import RateLimitedClient

pytestmark = pytest.mark.live


def test_retrieve_expedia(tmp_path):
    load_env()
    if not os.environ.get("SEC_USER_AGENT"):
        pytest.skip("SEC_USER_AGENT not set")
    with RateLimitedClient(sec_user_agent()) as client:
        m = run_retrieve(client, CompanyPaths(root=tmp_path, slug="expedia"), "Expedia", as_of=date(2026, 10, 12))
    assert m.company.ticker == "EXPE"
    assert m.filing.fiscal_year_check == "passed"
    assert {s.item for s in m.sections} == {"1", "1a", "7"}
    leadership = next(e for e in m.eight_ks.events if e.accession == "0001324424-26-000016")
    assert str(leadership.filing_date) == "2026-04-23"
    assert str(leadership.event_date) == "2026-04-17"
