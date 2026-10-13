import json
from datetime import date
from pathlib import Path

from success_plan.config import ALIASES_FILE, load_brand_aliases
from success_plan.stages.retrieve import edgar, eightk_info, eightk_meta
from success_plan.stages.retrieve.chunks import Chunk

FIXTURE = Path(__file__).parent / "fixtures" / "expe_submissions_excerpt.json"
EXPE_CIK = 1324424
AS_OF = date(2026, 10, 12)


def _expe_filings():
    return edgar.filings_from_page(json.loads(FIXTURE.read_text())["recent"])


def test_leadership_8k_filing_and_event_dates():
    """The 2026-04-23 Item 5.02 8-K: filed 2026-04-23, reporting an event on 2026-04-17 (EDGAR report date)."""
    events = edgar.select_event_8ks(_expe_filings(), date(2025, 10, 12), AS_OF)
    leadership = [f for f in events if "5.02" in f.items]
    assert len(leadership) == 1
    info = eightk_info(leadership[0], EXPE_CIK, ["Departure or appointment of directors or certain officers"], 1)
    assert info.filing_date == date(2026, 4, 23)
    assert info.event_date == date(2026, 4, 17)


def test_event_date_carried_into_chunk_metadata():
    filing = next(f for f in _expe_filings() if f.accession == "0001324424-26-000016")
    meta = eightk_meta(filing, "8k-x", "title", filing.url(EXPE_CIK), "Form 8-K Items 5.02")
    record = Chunk(id="8k-x-main-p001", source_id="8k-x", text="body", word_count=1).record(meta)
    assert record["date"] == "2026-04-23"
    assert record["event_date"] == "2026-04-17"


def test_events_window_selection_on_real_filings():
    events = edgar.select_event_8ks(_expe_filings(), date(2025, 10, 12), AS_OF)
    assert [(f.filing_date, f.report_date) for f in events] == [
        (date(2026, 4, 23), date(2026, 4, 17)),
        (date(2026, 3, 30), date(2026, 3, 27)),
    ]


def test_brand_aliases_loaded_from_config_and_missing_company(tmp_path):
    brands = load_brand_aliases(EXPE_CIK, ALIASES_FILE)
    assert {"Expedia", "Hotels.com", "Vrbo", "trivago"} <= set(brands)
    assert load_brand_aliases(999, ALIASES_FILE) is None
    assert load_brand_aliases(EXPE_CIK, tmp_path / "missing.yaml") is None
