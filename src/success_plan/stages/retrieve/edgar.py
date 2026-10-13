"""SEC EDGAR lookups: company resolution, filing selection, and exhibit discovery.

Selection logic is written as pure functions over EDGAR's JSON so it can be tested offline.
"""

import re
from dataclasses import dataclass
from datetime import date

from bs4 import BeautifulSoup

from .http import RateLimitedClient

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
SUBMISSIONS_PAGE_URL = "https://data.sec.gov/submissions/{name}"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc_nodash}/{document}"
INDEX_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc_nodash}/{acc}-index.htm"

# 8-K items treated as public signals of change (CLAUDE.md: leadership changes, acquisitions,
# restructurings, and other public changes that could affect investment).
EVENT_ITEMS = {
    "1.01": "Entry into a material definitive agreement",
    "2.01": "Completion of acquisition or disposition of assets",
    "2.05": "Costs associated with exit or disposal activities",
    "5.02": "Departure or appointment of directors or certain officers",
}
EARNINGS_ITEM = "2.02"

_LEGAL_SUFFIXES = {
    "inc", "incorporated", "corp", "corporation", "co", "company", "group", "holdings",
    "holding", "ltd", "limited", "plc", "llc", "lp", "sa", "nv", "ag", "the",
}
_DISPLAY_SUFFIXES = {"inc", "incorporated", "corp", "corporation", "co", "ltd", "limited", "plc", "llc", "lp"}


class EdgarError(Exception):
    pass


@dataclass(frozen=True)
class Company:
    name: str
    ticker: str
    cik: int


@dataclass(frozen=True)
class Filing:
    form: str
    accession: str
    filing_date: date
    report_date: date | None
    primary_document: str
    items: tuple[str, ...]

    def url(self, cik: int) -> str:
        return ARCHIVE_URL.format(cik=cik, acc_nodash=self.accession.replace("-", ""), document=self.primary_document)

    def index_url(self, cik: int) -> str:
        return INDEX_URL.format(cik=cik, acc_nodash=self.accession.replace("-", ""), acc=self.accession)


def normalize_name(name: str) -> str:
    text = name.lower().replace("&", " and ")
    tokens = re.sub(r"[^a-z0-9 ]+", " ", text).split()
    return " ".join(t for t in tokens if t not in _LEGAL_SUFFIXES)


def display_name(name: str) -> str:
    """'Expedia Group, Inc.' -> 'Expedia Group' (used as the news search phrase)."""
    tokens = re.sub(r"[,\.]", " ", name).split()
    while tokens and tokens[-1].lower() in _DISPLAY_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def _companies_from_tickers(tickers_json: dict) -> list[Company]:
    seen: dict[int, Company] = {}
    for row in tickers_json.values():
        cik = int(row["cik_str"])
        # The first ticker listed for a CIK is its primary listing; later rows are other share classes.
        seen.setdefault(cik, Company(name=row["title"], ticker=row["ticker"], cik=cik))
    return list(seen.values())


def resolve_company(tickers_json: dict, company: str, ticker: str | None = None) -> Company:
    companies = _companies_from_tickers(tickers_json)
    if ticker:
        for row in tickers_json.values():
            if row["ticker"].upper() == ticker.upper():
                return Company(name=row["title"], ticker=row["ticker"], cik=int(row["cik_str"]))
        raise EdgarError(f"Ticker {ticker!r} was not found in SEC's company list.")

    query = normalize_name(company)
    if not query:
        raise EdgarError(f"Company name {company!r} is empty after normalization.")
    exact = [c for c in companies if normalize_name(c.name) == query]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        _raise_ambiguous(company, exact)

    pattern = re.compile(rf"(^| ){re.escape(query)}( |$)")
    partial = [c for c in companies if pattern.search(normalize_name(c.name))]
    if len(partial) == 1:
        return partial[0]
    if not partial:
        raise EdgarError(f"No SEC registrant matches {company!r}. Try --ticker.")
    _raise_ambiguous(company, partial)


def _raise_ambiguous(company: str, candidates: list[Company]) -> None:
    listed = "; ".join(f"{c.name} ({c.ticker})" for c in candidates[:5])
    more = f" and {len(candidates) - 5} more" if len(candidates) > 5 else ""
    raise EdgarError(f"{company!r} matches several registrants: {listed}{more}. Re-run with --ticker.")


def _parse_date(value: str) -> date | None:
    return date.fromisoformat(value) if value else None


def filings_from_page(page: dict) -> list[Filing]:
    """Convert EDGAR's column-oriented filing arrays into Filing records."""
    filings = []
    for i, form in enumerate(page["form"]):
        items = tuple(x.strip() for x in (page.get("items", [""] * len(page["form"]))[i] or "").split(",") if x.strip())
        filings.append(
            Filing(
                form=form,
                accession=page["accessionNumber"][i],
                filing_date=date.fromisoformat(page["filingDate"][i]),
                report_date=_parse_date(page["reportDate"][i]),
                primary_document=page["primaryDocument"][i],
                items=items,
            )
        )
    return filings


def load_filings(client: RateLimitedClient, cik: int, since: date) -> list[Filing]:
    """All filings in the submissions feed, paging into older files only when needed to reach `since`."""
    submissions = client.get(SUBMISSIONS_URL.format(cik=cik)).json()
    filings = filings_from_page(submissions["filings"]["recent"])
    oldest = min((f.filing_date for f in filings), default=None)
    if oldest is None or oldest > since:
        for extra in submissions["filings"].get("files", []):
            if date.fromisoformat(extra["filingTo"]) >= since:
                page = client.get(SUBMISSIONS_PAGE_URL.format(name=extra["name"])).json()
                filings.extend(filings_from_page(page))
    return filings


def select_latest_10k(filings: list[Filing], as_of: date) -> tuple[Filing, list[str]]:
    """Latest original 10-K filed on or before as_of. Amendments are skipped but reported."""
    warnings = []
    originals = sorted((f for f in filings if f.form == "10-K" and f.filing_date <= as_of), key=lambda f: f.filing_date)
    if not originals:
        raise EdgarError("No 10-K found on or before the as-of date.")
    latest = originals[-1]
    amendments = [f for f in filings if f.form == "10-K/A" and latest.filing_date <= f.filing_date <= as_of]
    for a in amendments:
        warnings.append(
            f"A 10-K/A ({a.accession}, filed {a.filing_date}) amends the selected 10-K. "
            "It was not used; amendments often contain only Part III."
        )
    if latest.report_date is None:
        raise EdgarError(f"10-K {latest.accession} has no report period in EDGAR; cannot confirm fiscal year.")
    age_days = (as_of - latest.filing_date).days
    if age_days > 456:  # about 15 months
        warnings.append(
            f"The latest 10-K was filed {latest.filing_date} ({age_days} days before the as-of date). "
            "It may be stale or the company may have stopped filing."
        )
    return latest, warnings


def select_event_8ks(filings: list[Filing], window_start: date, as_of: date) -> list[Filing]:
    hits = [
        f for f in filings
        if f.form == "8-K" and window_start <= f.filing_date <= as_of and set(f.items) & EVENT_ITEMS.keys()
    ]
    return sorted(hits, key=lambda f: f.filing_date, reverse=True)


def select_earnings_8ks(filings: list[Filing], as_of: date, count: int = 2) -> list[Filing]:
    hits = [f for f in filings if f.form == "8-K" and f.filing_date <= as_of and EARNINGS_ITEM in f.items]
    return sorted(hits, key=lambda f: f.filing_date, reverse=True)[:count]


def find_exhibit(index_html: str, exhibit_type: str = "EX-99.1") -> str | None:
    """Return the document filename for an exhibit type from a filing's -index.htm page."""
    soup = BeautifulSoup(index_html, "lxml")
    for table in soup.find_all("table", class_="tableFile"):
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 4:
                continue
            if cells[3].get_text(strip=True).upper() == exhibit_type:
                link = cells[2].find("a")
                if link and link.get("href"):
                    href = link["href"]
                    # iXBRL viewer links look like /ix?doc=/Archives/.../file.htm; keep only the filename.
                    if "doc=" in href:
                        href = href.split("doc=", 1)[1]
                    return href.split("?")[0].rsplit("/", 1)[-1]
                text = cells[2].get_text(strip=True)
                return text.split()[0] if text else None
    return None
