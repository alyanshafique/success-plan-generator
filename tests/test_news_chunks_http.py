import json
from datetime import date

import httpx
import pytest

from success_plan.stages.retrieve.chunks import MAX_WORDS, chunk_text
from success_plan.stages.retrieve.http import FetchError, RateLimitedClient
from success_plan.stages.retrieve.news import (
    REDACTED_HEADLINE, AliasMatcher, Headline, fetch_headlines, is_low_signal, names_person, parse_gdelt,
    rank_and_cap, redact_headline,
)

GDELT_JSON = json.dumps(
    {
        "articles": [
            {"url": "https://a.example/1", "title": "Expedia Group names new CFO", "seendate": "20261001T120000Z", "domain": "a.example"},
            {"url": "https://a.example/1", "title": "duplicate url", "seendate": "20261001T120000Z", "domain": "a.example"},
            {"url": "https://b.example/2", "title": "Expedia  Group names new CFO", "seendate": "20261002T120000Z", "domain": "b.example"},
            {"url": "https://c.example/3", "title": "Ten cheap beach hotels", "seendate": "20260915T120000Z", "domain": "c.example"},
        ]
    }
)


def _client(handler, **kwargs) -> RateLimitedClient:
    return RateLimitedClient("Test User test@example.com", transport=httpx.MockTransport(handler), sleep=lambda s: None, **kwargs)


MATCHER = AliasMatcher(["Expedia Group", "expedia", "Hotels.com", "Vrbo"], "EXPE")


def test_parse_gdelt_dedupes_and_flags_eligibility():
    headlines = parse_gdelt(GDELT_JSON, MATCHER)
    assert [h.headline for h in headlines] == ["Expedia Group names new CFO", "Ten cheap beach hotels"]
    assert headlines[0].date == date(2026, 10, 1)
    assert [h.eligible for h in headlines] == [True, False]


@pytest.mark.parametrize(
    "title, expected",
    [
        ("Stocks making the biggest moves midday : EXPE , NVDA", ("EXPE",)),
        ("Nykredit Takes Position in $EXPE", ("EXPE",)),
        ("Morgan Stanley Cuts Expedia ( EXPE ) to Underweight", ("expedia", "EXPE")),
        ("Vrbo to Double Commissions as It Plays Catch - Up to Airbnb", ("Vrbo",)),
        ("Hotels . com launches new app", ("Hotels.com",)),  # GDELT spacing around punctuation
        ("EXPEDITE your fall travel plans", ()),  # ticker needs a word boundary
        ("expe reports results", ()),  # ticker is matched in exact case only
        ("Ten cheap beach hotels", ()),
    ],
)
def test_alias_and_ticker_matching(title, expected):
    assert MATCHER.matches(title) == expected


def test_alias_labels_deduplicated_case_insensitively():
    matcher = AliasMatcher(["Expedia Group", "Expedia", "expedia", "Vrbo"], "EXPE")
    assert matcher.labels == ["Expedia Group", "Expedia", "Vrbo", "EXPE"]


def _h(day: int, eligible: bool) -> Headline:
    return Headline(f"h{day}", f"https://x/{day}", date(2026, 9, day), "x", ("Vrbo",) if eligible else ())


def test_rank_eligible_first_then_fill_by_date():
    headlines = [_h(1, True), _h(20, False), _h(5, True), _h(25, False), _h(15, False)]
    ranked = rank_and_cap(headlines, cap=4)
    assert [h.headline for h in ranked] == ["h5", "h1", "h25", "h20"]


def test_rank_keeps_low_signal_company_headlines_ahead_of_unmatched():
    low = Headline("low", "https://t/1", date(2026, 7, 20), "tickerreport.com", ("EXPE",), low_signal=True)
    headlines = [_h(1, True), _h(25, False), low, _h(20, False)]
    ranked = rank_and_cap(headlines, cap=3)
    assert [h.headline for h in ranked] == ["h1", "low", "h25"]  # older low-signal beats newer unmatched


def test_gdelt_failure_is_recorded_not_raised():
    client = _client(lambda req: httpx.Response(200, text="Your search contained a phrase that was too short."))
    result = fetch_headlines(client, "Expedia Group", MATCHER, date(2026, 10, 12))
    assert result.status == "failed" and result.headlines == []
    assert "JSONDecodeError" in result.error


def test_gdelt_ok():
    client = _client(lambda req: httpx.Response(200, text=GDELT_JSON))
    result = fetch_headlines(client, "Expedia Group", MATCHER, date(2026, 10, 12))
    assert result.status == "ok" and len(result.headlines) == 2 and result.returned_count == 2
    assert result.window_from == date(2026, 7, 14)


def test_chunks_respect_size_and_ids():
    text = "\n".join(["word " * 60] * 20 + ["x " * 700])
    chunks = chunk_text(text, "10k-item1", "10k-item1")
    assert chunks[0].id == "10k-item1-p001"
    assert all(c.word_count <= MAX_WORDS for c in chunks)
    assert sum(c.word_count for c in chunks) == 60 * 20 + 700


def test_rate_limiter_spaces_sec_requests():
    now = [0.0]
    slept = []

    def sleep(s):
        slept.append(s)
        now[0] += s

    client = RateLimitedClient(
        "Test User test@example.com",
        transport=httpx.MockTransport(lambda req: httpx.Response(200, text="{}")),
        sleep=sleep, clock=lambda: now[0],
    )
    client.get("https://www.sec.gov/a")
    client.get("https://data.sec.gov/b")  # same budget as www.sec.gov
    assert slept == [pytest.approx(0.25)]
    assert client.request_count == 2


def test_retry_then_success_and_403_message():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(503) if len(calls) == 1 else httpx.Response(200, text="ok")

    assert _client(handler).get("https://www.sec.gov/x").text == "ok"
    with pytest.raises(FetchError, match="SEC_USER_AGENT"):
        _client(lambda req: httpx.Response(403)).get("https://www.sec.gov/x")


def test_user_agent_header_sent():
    seen = {}

    def handler(req):
        seen["ua"] = req.headers["User-Agent"]
        return httpx.Response(200, text="ok")

    _client(handler).get("https://www.sec.gov/x")
    assert seen["ua"] == "Test User test@example.com"


# --- GDELT response cache (per as-of date) ---

def _counting_client(responses):
    calls = []

    def handler(req):
        calls.append(req)
        status, text = responses[min(len(calls) - 1, len(responses) - 1)]
        return httpx.Response(status, text=text)

    return _client(handler, max_retries=0), calls


def test_gdelt_cache_reused_then_force_refreshes(tmp_path):
    cache = tmp_path / "gdelt" / "cik1_2026-10-12.json"
    client, calls = _counting_client([(200, GDELT_JSON)])
    first = fetch_headlines(client, "Expedia Group", MATCHER, date(2026, 10, 12), cache_path=cache)
    assert first.status == "ok" and not first.from_cache and cache.exists() and len(calls) == 1

    second = fetch_headlines(client, "Expedia Group", MATCHER, date(2026, 10, 12), cache_path=cache)
    assert second.from_cache and len(calls) == 1  # no network call
    assert [h.headline for h in second.headlines] == [h.headline for h in first.headlines]

    third = fetch_headlines(client, "Expedia Group", MATCHER, date(2026, 10, 12), cache_path=cache, force=True)
    assert not third.from_cache and len(calls) == 2  # --force bypasses and refreshes


def test_gdelt_failure_not_cached(tmp_path):
    cache = tmp_path / "gdelt" / "cik1_2026-10-12.json"
    client, _ = _counting_client([(429, "")])
    result = fetch_headlines(client, "Expedia Group", MATCHER, date(2026, 10, 12), cache_path=cache)
    assert result.status == "failed" and not cache.exists()

    client, _ = _counting_client([(200, "Your search contained a phrase that was too short.")])
    result = fetch_headlines(client, "Expedia Group", MATCHER, date(2026, 10, 12), cache_path=cache)
    assert result.status == "failed" and not cache.exists()  # non-JSON error text is not cached


# --- 429 backoff ---

def _backoff_client(handler):
    slept, messages = [], []
    client = RateLimitedClient(
        "Test User test@example.com", transport=httpx.MockTransport(handler),
        sleep=slept.append, clock=lambda: 0.0, min_intervals={"sec": 0.0, "gdelt": 0.0, "other": 0.0},
        on_backoff=messages.append,
    )
    return client, slept, messages


def test_gdelt_429_waits_minutes_before_retry():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(429) if len(calls) <= 2 else httpx.Response(200, text="{}")

    client, slept, messages = _backoff_client(handler)
    assert client.get("https://api.gdeltproject.org/api/v2/doc/doc").text == "{}"
    assert slept == [60.0, 180.0]
    assert "waiting 60s" in messages[0] and "waiting 180s" in messages[1]


def test_sec_429_keeps_short_backoff_and_honors_retry_after():
    calls = []

    def handler(req):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429)
        if len(calls) == 2:
            return httpx.Response(429, headers={"Retry-After": "7"})
        return httpx.Response(200, text="ok")

    client, slept, messages = _backoff_client(handler)
    assert client.get("https://www.sec.gov/x").text == "ok"
    assert slept == [1.0, 7.0]
    assert messages == []  # short waits are not announced


# --- low-signal domains ---

LOW_SIGNAL = ["tickerreport.com", "dailypolitical.com", "themarketsdaily.com"]


@pytest.mark.parametrize(
    "domain, expected",
    [
        ("tickerreport.com", True),
        ("www.tickerreport.com", True),  # subdomain
        ("TheMarketsDaily.com", True),
        ("notdailypolitical.com", False),  # suffix match must be on a dot boundary
        ("skift.com", False),
    ],
)
def test_low_signal_domain_matching(domain, expected):
    assert is_low_signal(domain, LOW_SIGNAL) is expected


def test_low_signal_headlines_flagged_and_not_eligible():
    payload = json.dumps({"articles": [
        {"url": "https://tickerreport.com/1", "title": "Expedia Group ( NASDAQ : EXPE ) Shares Down 7 %", "seendate": "20260908T000000Z", "domain": "tickerreport.com"},
        {"url": "https://skift.com/2", "title": "Expedia Cuts Eight Executives", "seendate": "20260821T000000Z", "domain": "skift.com"},
    ]})
    by_domain = {h.domain: h for h in parse_gdelt(payload, MATCHER, LOW_SIGNAL)}
    ticker_post = by_domain["tickerreport.com"]
    assert ticker_post.matched_aliases and ticker_post.low_signal and not ticker_post.eligible
    assert by_domain["skift.com"].eligible and not by_domain["skift.com"].low_signal


def test_low_signal_domains_config(tmp_path):
    from success_plan.config import NEWS_SOURCES_FILE, load_low_signal_domains

    assert set(LOW_SIGNAL) <= set(load_low_signal_domains(NEWS_SOURCES_FILE))
    assert load_low_signal_domains(tmp_path / "missing.yaml") == []



# ---------------------------------------------------------------- post-fetch redaction (synthetic names only)

SYNTHETIC_NAMES = ["Pat Example", "Example"]


def test_redact_headline_naming_listed_person():
    text, url, redacted = redact_headline("Pat Example Used Expedia To Beat The Market", "https://x.example/blog/pat-example-used-expedia/",
                                          "x.example", SYNTHETIC_NAMES)
    assert redacted and text == REDACTED_HEADLINE and url == "https://x.example/"


def test_redaction_matches_url_slug_and_case():
    assert names_person("https://x.example/blog/pat-example-on-travel/", ["Pat Example"])
    assert names_person("PAT EXAMPLE talks travel", ["Pat Example"])
    assert not names_person("Examples of travel deals", ["Example"])  # whole words only


def test_headline_without_listed_name_unchanged():
    assert redact_headline("Expedia Cuts Eight Executives", "https://a.example/1", "a.example", SYNTHETIC_NAMES) == (
        "Expedia Cuts Eight Executives", "https://a.example/1", False)
    assert redact_headline("Pat Example news", "https://a.example/1", "a.example", []) == ("Pat Example news", "https://a.example/1", False)
    # A listed surname inside the domain alone does not trigger redaction.
    assert redact_headline("Travel deals roundup", "https://example.com/deals", "example.com", SYNTHETIC_NAMES)[2] is False


def test_load_private_names(tmp_path):
    from success_plan.config import load_private_names
    f = tmp_path / "names.txt"
    f.write_text("# comment\nPat Example\n\nExample\n")
    assert load_private_names(f) == ["Pat Example", "Example"]
    assert load_private_names(tmp_path / "missing.txt") == []
