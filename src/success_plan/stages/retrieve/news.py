"""GDELT headline search. Headlines are weak signals: we keep headline, URL, date, and domain only.

A headline is eligible for Extract only if it names the company, its ticker, or a configured brand
alias, and does not come from a configured low-signal domain. Eligible headlines are ranked first,
then low-signal headlines that name the company, then headlines that name neither; the cap applies
after ranking. Ineligible headlines are kept in the manifest for review but never passed to Extract.

Successful GDELT responses are cached per company and as-of date, so re-runs are reproducible and do
not hit GDELT's rate limit. Failed responses are never cached.
"""

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from ... import atomic
from .http import FetchError, RateLimitedClient

GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
GDELT_MAX_DAYS = 90  # The DOC 2.0 API only searches a rolling three-month window.
MAX_HEADLINES = 50


@dataclass(frozen=True)
class Headline:
    headline: str
    url: str
    date: date
    domain: str
    matched_aliases: tuple[str, ...]
    low_signal: bool = False  # from a configured low-signal domain (automated stock posts)

    @property
    def eligible(self) -> bool:
        return bool(self.matched_aliases) and not self.low_signal


@dataclass
class NewsResult:
    status: str  # "ok" | "empty" | "failed" | "skipped"
    query: str
    window_from: date
    window_to: date
    headlines: list[Headline]
    returned_count: int = 0  # unique headlines GDELT returned before the cap
    error: str | None = None
    from_cache: bool = False


class AliasMatcher:
    """Matches company names, brands (case-insensitive), and the ticker (exact case)."""

    def __init__(self, names: list[str], ticker: str | None):
        self._patterns: list[tuple[str, re.Pattern]] = []
        seen = set()
        for name in names:
            key = name.strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            # GDELT often spaces out punctuation ("Hotels . com"), so allow spaces around dots.
            body = r"\s*\.\s*".join(re.escape(part) for part in name.strip().split("."))
            self._patterns.append((name.strip(), re.compile(rf"(?<![A-Za-z0-9]){body}(?![A-Za-z0-9])", re.IGNORECASE)))
        if ticker:
            # Exact case so a ticker like "ON" or "EXPE" does not match ordinary words.
            self._patterns.append((ticker, re.compile(rf"(?<![A-Za-z0-9]){re.escape(ticker)}(?![A-Za-z0-9])")))

    @property
    def labels(self) -> list[str]:
        return [label for label, _ in self._patterns]

    def matches(self, text: str) -> tuple[str, ...]:
        return tuple(label for label, pattern in self._patterns if pattern.search(text))


def gdelt_query(company_phrase: str) -> str:
    return f'"{company_phrase}" sourcelang:english'


def is_low_signal(domain: str, low_signal_domains: list[str] | tuple[str, ...]) -> bool:
    domain = domain.lower().strip(".")
    return any(domain == d or domain.endswith("." + d) for d in low_signal_domains)


def parse_gdelt(payload: str, matcher: AliasMatcher, low_signal_domains: list[str] | tuple[str, ...] = ()) -> list[Headline]:
    """Parse a GDELT artlist JSON response into unique headlines. Raises ValueError on non-JSON error text."""
    data = json.loads(payload)
    seen_urls, seen_titles, out = set(), set(), []
    for art in data.get("articles", []):
        title = re.sub(r"\s+", " ", art.get("title", "")).strip()
        url = art.get("url", "")
        title_key = re.sub(r"[^a-z0-9]", "", title.lower())
        if not title or not url or url in seen_urls or title_key in seen_titles:
            continue
        seen_urls.add(url)
        seen_titles.add(title_key)
        out.append(
            Headline(
                headline=title,
                url=url,
                date=datetime.strptime(art["seendate"][:8], "%Y%m%d").date(),
                domain=art.get("domain", ""),
                matched_aliases=matcher.matches(title),
                low_signal=is_low_signal(art.get("domain", ""), low_signal_domains),
            )
        )
    return out


def rank_and_cap(headlines: list[Headline], cap: int = MAX_HEADLINES) -> list[Headline]:
    """Rank in three tiers, newest first within each, then apply the cap:
    1. eligible (names the company or an alias, not low-signal)
    2. low-signal headlines that name the company or an alias (kept for review, not eligible)
    3. headlines that name neither (kept for review if room remains)
    """
    def tier(h: Headline) -> int:
        if h.eligible:
            return 0
        return 1 if h.matched_aliases else 2

    return sorted(headlines, key=lambda h: (tier(h), -h.date.toordinal()))[:cap]


def fetch_headlines(
    client: RateLimitedClient,
    company_phrase: str,
    matcher: AliasMatcher,
    as_of: date,
    *,
    cache_path: Path | None = None,
    force: bool = False,
    low_signal_domains: list[str] | tuple[str, ...] = (),
) -> NewsResult:
    """Never raises: a GDELT failure is recorded and the pipeline continues with 8-Ks only.

    With cache_path, a cached response for this as-of date is reused unless force is set; a fresh
    successful response (valid JSON) is written to the cache.
    """
    window_from = as_of - timedelta(days=GDELT_MAX_DAYS)
    query = gdelt_query(company_phrase)
    from_cache = cache_path is not None and cache_path.exists() and not force
    try:
        if from_cache:
            payload = cache_path.read_text()
        else:
            params = {
                "query": query,
                "mode": "artlist",
                "format": "json",
                "maxrecords": "250",
                "sort": "datedesc",
                "startdatetime": window_from.strftime("%Y%m%d000000"),
                "enddatetime": as_of.strftime("%Y%m%d235959"),
            }
            payload = client.get(GDELT_URL, params=params).text
        parsed = parse_gdelt(payload, matcher, low_signal_domains)
    except (FetchError, ValueError, KeyError) as exc:
        return NewsResult("failed", query, window_from, as_of, [], error=f"{type(exc).__name__}: {exc}"[:300])
    if cache_path is not None and not from_cache:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        atomic.write_text(cache_path, payload)  # only reached when the payload parsed as valid GDELT JSON
    kept = rank_and_cap(parsed)
    return NewsResult(
        "ok" if kept else "empty", query, window_from, as_of, kept, returned_count=len(parsed), from_cache=from_cache
    )


# ---------------------------------------------------------------- redaction of named people (post-fetch)

REDACTED_HEADLINE = "[headline removed: names a person]"


def name_pattern(name: str) -> re.Pattern:
    """Case-insensitive; spaces in a name also match hyphens or underscores (URL slugs: "pat-example")."""
    parts = [re.escape(p) for p in name.split()]
    return re.compile(r"(?<![A-Za-z0-9])" + r"[\s\-_]+".join(parts) + r"(?![A-Za-z0-9])", re.IGNORECASE)


def names_person(text: str, names: list[str]) -> bool:
    return any(name_pattern(n).search(text or "") for n in names)


def redact_headline(headline: str, url: str, domain: str, names: list[str]) -> tuple[str, str, bool]:
    """Replace a headline that names a listed person, and reduce its URL to the domain root (slugs repeat
    the headline). Returns (headline, url, redacted). Eligibility and alias matches are computed before
    this step and are kept."""
    path = urlparse(url or "").path  # check the path only; a name can collide with a domain
    if not names or not (names_person(headline, names) or names_person(path, names)):
        return headline, url, False
    return REDACTED_HEADLINE, f"https://{domain}/" if domain else "", True
