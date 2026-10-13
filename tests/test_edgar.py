from datetime import date

import pytest

from success_plan.stages.retrieve import edgar

TICKERS = {
    "0": {"cik_str": 1324424, "ticker": "EXPE", "title": "Expedia Group, Inc."},
    "1": {"cik_str": 1000001, "ticker": "ACME", "title": "Acme Corp"},
    "2": {"cik_str": 1000002, "ticker": "ACMH", "title": "Acme Holdings Ltd"},
    "3": {"cik_str": 1000003, "ticker": "BRKA", "title": "Berkshire Widgets Inc"},
    "4": {"cik_str": 1000003, "ticker": "BRKB", "title": "Berkshire Widgets Inc"},
}


def test_resolve_by_normalized_name():
    c = edgar.resolve_company(TICKERS, "Expedia")
    assert (c.name, c.ticker, c.cik) == ("Expedia Group, Inc.", "EXPE", 1324424)


def test_resolve_ambiguous_lists_candidates():
    with pytest.raises(edgar.EdgarError, match="several registrants.*Acme Corp.*Acme Holdings"):
        edgar.resolve_company(TICKERS, "Acme")


def test_resolve_by_ticker_and_share_classes_collapse():
    assert edgar.resolve_company(TICKERS, "anything", ticker="acmh").name == "Acme Holdings Ltd"
    assert edgar.resolve_company(TICKERS, "Berkshire Widgets").ticker == "BRKA"


def test_resolve_partial_match_and_no_match():
    assert edgar.resolve_company(TICKERS, "Berkshire").cik == 1000003
    with pytest.raises(edgar.EdgarError, match="No SEC registrant"):
        edgar.resolve_company(TICKERS, "Nonexistent Co")


def test_display_name_drops_only_legal_suffixes():
    assert edgar.display_name("Expedia Group, Inc.") == "Expedia Group"
    assert edgar.display_name("Acme Holdings Ltd") == "Acme Holdings"


def _page(rows):
    keys = ["form", "accessionNumber", "filingDate", "reportDate", "primaryDocument", "items"]
    return {k: [r[i] for r in rows] for i, k in enumerate(keys)}


FILINGS = edgar.filings_from_page(
    _page(
        [
            ("10-K", "0001-25-000001", "2025-02-07", "2024-12-31", "a2024.htm", ""),
            ("10-K", "0001-26-000001", "2026-02-06", "2025-12-31", "a2025.htm", ""),
            ("10-K/A", "0001-26-000002", "2026-04-20", "2025-12-31", "a2025a.htm", ""),
            ("8-K", "0001-26-000010", "2026-08-01", "", "q2.htm", "2.02,9.01"),
            ("8-K", "0001-26-000011", "2026-05-01", "", "q1.htm", "2.02,9.01"),
            ("8-K", "0001-26-000012", "2026-02-06", "", "q4.htm", "2.02,9.01"),
            ("8-K", "0001-26-000013", "2026-06-15", "", "ceo.htm", "5.02"),
            ("8-K", "0001-25-000014", "2025-06-15", "", "old.htm", "5.02"),
            ("8-K", "0001-26-000015", "2026-07-01", "", "vote.htm", "5.07"),
            ("4", "0001-26-000016", "2026-07-02", "", "f4.xml", ""),
        ]
    )
)


def test_latest_10k_skips_amendment_and_warns():
    tenk, warnings = edgar.select_latest_10k(FILINGS, date(2026, 10, 12))
    assert tenk.accession == "0001-26-000001"
    assert tenk.report_date == date(2025, 12, 31)
    assert any("10-K/A" in w for w in warnings)


def test_latest_10k_respects_as_of():
    tenk, _ = edgar.select_latest_10k(FILINGS, date(2025, 12, 1))
    assert tenk.accession == "0001-25-000001"


def test_stale_10k_warning():
    _, warnings = edgar.select_latest_10k(FILINGS, date(2027, 6, 1))
    assert any("stale" in w for w in warnings)


def test_event_and_earnings_selection():
    as_of = date(2026, 10, 12)
    events = edgar.select_event_8ks(FILINGS, date(2025, 10, 12), as_of)
    assert [f.accession for f in events] == ["0001-26-000013"]  # 5.07 excluded, 2025 one outside window
    earnings = edgar.select_earnings_8ks(FILINGS, as_of, 2)
    assert [f.accession for f in earnings] == ["0001-26-000010", "0001-26-000011"]


INDEX_HTML = """
<table class="tableFile" summary="Document Format Files">
<tr><th>Seq</th><th>Description</th><th>Document</th><th>Type</th><th>Size</th></tr>
<tr><td>1</td><td>8-K</td><td><a href="/ix?doc=/Archives/edgar/data/1/000/q2.htm">q2.htm</a></td><td>8-K</td><td>1</td></tr>
<tr><td>2</td><td>PRESS RELEASE</td><td><a href="/Archives/edgar/data/1/000/ex991.htm">ex991.htm</a></td><td>EX-99.1</td><td>1</td></tr>
</table>
"""


def test_find_exhibit():
    assert edgar.find_exhibit(INDEX_HTML, "EX-99.1") == "ex991.htm"
    assert edgar.find_exhibit(INDEX_HTML, "8-K") == "q2.htm"  # iXBRL viewer link reduced to filename
    assert edgar.find_exhibit(INDEX_HTML, "EX-99.2") is None
