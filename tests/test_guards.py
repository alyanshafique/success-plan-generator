"""Guards, tested against verbatim Expedia excerpts (tests/fixtures/expe_chunks_excerpt.jsonl)."""

import json
import re
from decimal import Decimal
from pathlib import Path

import pytest

from success_plan import guards

FIXTURE = Path(__file__).parent / "fixtures" / "expe_chunks_excerpt.jsonl"
CHUNKS = {
    r["chunk_id"]: r
    for r in (json.loads(l) for l in FIXTURE.read_text().splitlines()[1:] if l.strip())
}
ALL = set(CHUNKS)
ALIASES = ["Expedia Group", "Expedia", "Hotels.com", "Vrbo", "Orbitz", "Travelocity", "ebookers", "Wotif", "trivago", "EXPE"]


def problem(chunk_id, evidence, allowed=ALL, kind=None):
    return guards.citation_problem(chunk_id, evidence, CHUNKS, allowed, ALIASES, kind=kind)


# ---------------------------------------------------------------- citation guard

def test_fixture_has_table_row_and_curly_quotes():
    assert " | " in CHUNKS["10k-item7-p025"]["text"]
    assert "’" in CHUNKS["10k-item1-p005"]["text"] and "“" in CHUNKS["10k-item7-p008"]["text"]


def test_curly_quote_sentence_matches_straight_quotes():
    assert problem("10k-item1-p005", "Expedia Group is one of the world's largest online travel companies") is None


def test_curly_double_quotes_match():
    assert problem("10k-item7-p008", 'Expedia Group ("EG") Advertising is responsible for generating advertising revenue') is None


@pytest.mark.parametrize(
    "evidence",
    [
        "Total gross bookings | $ | 119,590 | $ | 110,921 | $ | 104,079",  # as it appears in the chunk
        "Total gross bookings $119,590 $110,921 $104,079",  # as a model would usually write it
    ],
)
def test_table_row_matches_with_or_without_cell_separators(evidence):
    assert problem("10k-item7-p025", evidence) is None


def test_gdelt_spaced_punctuation_matches():
    assert problem("news-gdelt-012", "Expedia Cuts Eight Executives in AI-Focused Reorganization") is None


def test_evidence_not_found_and_partial_word():
    assert problem("10k-item7-p032", "Technology and content expense increased $37 million for 2025") == "evidence not found in chunk"
    # Word boundaries: "previously announced cost saving initiative" must not match "...initiatives"
    assert problem("10k-item7-p032", "in connection with previously announced cost saving initiative") == "evidence not found in chunk"


# The real I-06 evidence from the Haiku plumbing run (2026-10-12). It was wrongly rejected because the
# phrase ends with a period that sits directly before the next sentence once spaces are normalized.
I06_EVIDENCE = "we generated $758 million of advertising and media revenue, a 19% increase from 2024."


def test_real_i06_phrase_ending_in_period_is_verified():
    assert problem("10k-item7-p008", I06_EVIDENCE) is None


def test_phrase_with_punctuation_at_either_end():
    assert problem("10k-item7-p008", "(\u201cEG\u201d) Advertising is responsible for generating advertising revenue") is None
    assert problem("10k-item7-p008", "a 19% increase from 2024. We also generate advertising revenue") is None


def test_boundary_still_enforced_at_letter_or_digit_ends():
    # "from 202" must not match inside "from 2024"; "eg advertising is" must not match mid-word.
    assert problem("10k-item7-p008", "advertising and media revenue, a 19% increase from 202") == "evidence not found in chunk"
    assert problem("10k-item7-p008", "G\u201d) Advertising is responsible for generating advertising revenue") == "evidence not found in chunk"


@pytest.mark.parametrize(
    "evidence, reason",
    [
        ("Technology and content expense decreased", "evidence must be 6-14 words (got 5)"),
        # Over 14 words and NOT in the chunk ("increased" vs "decreased"): rejected.
        ("Technology and content expense increased $37 million for 2025 compared to 2024 primarily due to",
         "evidence must be 6-14 words (got 15) and was not found in chunk"),
    ],
)
def test_evidence_length(evidence, reason):
    assert problem("10k-item7-p032", evidence) == reason


def test_evidence_length_bounds_accepted():
    assert problem("10k-item7-p032", "Technology and content expense decreased $37 million") is None  # 6 words
    assert problem("10k-item7-p032", "Technology and content expense decreased $37 million for 2025 compared to 2024 primarily") is None  # 14


def test_short_chunk_requires_whole_chunk_and_rejects_name_only():
    chunks = {"news-x": {"chunk_id": "news-x", "source_id": "news-x", "source_type": "news", "text": "Expedia Group ( EXPE )"}}
    assert guards.citation_problem("news-x", "Expedia Group", chunks, {"news-x"}, ALIASES).startswith("chunk has fewer than 6 words")
    assert guards.citation_problem("news-x", "Expedia Group (EXPE)", chunks, {"news-x"}, ALIASES) == "evidence is only a company or brand name"


def test_name_only_detects_brand_lists_and_allows_substance():
    assert guards.is_name_only("Expedia, Hotels.com, Vrbo, Orbitz, Travelocity, trivago", ALIASES)
    assert guards.is_name_only("Expedia Group, Inc. (NASDAQ: EXPE)", ALIASES)
    assert not guards.is_name_only("Expedia Cuts Eight Executives", ALIASES)


def test_unknown_disallowed_and_risk_factor_initiative():
    assert problem("10k-item9-p001", "anything at all goes here for six words") == "unknown chunk id"
    assert problem("10k-item1a-p040", "These risks could have a material adverse effect", allowed={"10k-item1-p005"}) == \
        "chunk was not among this call's sources"
    assert problem("10k-item1a-p040", "These risks could have a material adverse effect", kind="initiative") == \
        "Risk Factors cannot support an initiative"


# ---------------------------------------------------------------- price guard

def test_amounts_parse_whole_numbers_with_units():
    amounts = guards.find_currency_amounts("We spent $1.3 billion, then $11.3 billion, $1,300 million, and 5 million dollars.")
    assert [a.key for a in amounts] == [
        (Decimal("1.3"), "billion"), (Decimal("11.3"), "billion"), (Decimal("1300"), "million"), (Decimal("5"), "million"),
    ]


def test_1_3_does_not_match_11_3():
    assert guards.price_flags("Intercompany receivables of $1.3 billion.", ["receivables of $11.3 billion"]) == [
        "amount '$1.3 billion' does not trace to a cited filing chunk"
    ]
    assert guards.price_flags("Receivables of $11.3 billion.", ["receivables of $1.3 billion"]) == [
        "amount '$11.3 billion' does not trace to a cited filing chunk"
    ]


def test_traceable_amounts_pass():
    assert guards.price_flags("Advertising and media revenue reached $758 million in 2025.", [CHUNKS["10k-item7-p008"]["text"]]) == []
    assert guards.price_flags("Technology and content expense fell $37 million.", [CHUNKS["10k-item7-p032"]["text"]]) == []
    # Table row: "$" sits in its own cell in the source.
    assert guards.price_flags("Total gross bookings were $119,590 million.", [CHUNKS["10k-item7-p025"]["text"]]) != []  # scale differs
    assert guards.price_flags("Total gross bookings were $119,590.", [CHUNKS["10k-item7-p025"]["text"]]) == []


def test_unit_must_match():
    assert guards.price_flags("Expense fell $37 billion.", [CHUNKS["10k-item7-p032"]["text"]]) == [
        "amount '$37 billion' does not trace to a cited filing chunk"
    ]


@pytest.mark.parametrize("phrase", ["per user", "per-seat", "license", "subscriptions", "a discount of"])
def test_amount_near_pricing_words_flagged_even_if_traceable(phrase):
    flags = guards.price_flags(f"Expense fell $37 million, {phrase} terms aside.", [CHUNKS["10k-item7-p032"]["text"]])
    assert flags == ["amount '$37 million' appears near product-pricing words"]


def test_untraceable_amount_without_filing_citation():
    assert guards.price_flags("Layla was acquired for $50 million.", []) == [
        "amount '$50 million' does not trace to a cited filing chunk"
    ]


# ---------------------------------------------------------------- other checks

def test_long_quote():
    src = CHUNKS["10k-item1a-p040"]["text"]
    copied = " ".join(src.split()[:15])
    assert guards.long_quote(f"Summary: {copied} and more.", [src])
    assert not guards.long_quote(" ".join(src.split()[:14]), [src])


def test_personal_title():
    assert guards.has_personal_title("Mr. Smith will step down as chief financial officer.")
    assert not guards.has_personal_title("The Chief Financial Officer role changed in April 2026.")


def test_market_signal():
    assert guards.is_market_signal(CHUNKS["news-gdelt-004"]["text"])
    assert guards.is_market_signal("Expedia Group ( NASDAQ : EXPE ) Shares Down 7 % – Here What Happened")
    assert not guards.is_market_signal(CHUNKS["news-gdelt-012"]["text"])


# ---------------------------------------------------------------- rules: financing, corroboration, names

@pytest.mark.parametrize(
    "text, keyword",
    [("New $5 billion share repurchase authorization", "share repurchase"), ("Stock buy-back program", "buy-back"),
     ("Reinstated quarterly dividends", "dividends"), ("New revolving credit facility", "credit facility"),
     ("Senior notes offering", "notes offering"), ("Grow the B2B business", None)],
)
def test_financing_keyword(text, keyword):
    assert guards.financing_keyword(text) == keyword


def test_distinctive_terms_skip_aliases_years_and_small_numbers():
    figures, words = guards.distinctive_terms("Expedia Group cuts 58 Seattle jobs in 2026 , down 7 %", ALIASES)
    assert figures == {"58"} and "Seattle" in words
    assert not {"Expedia", "Group", "2026"} & (figures | words)


def test_corroboration_is_term_overlap():
    headline = "Expedia Acquired AI Trip - Planner Layla : Exclusive"
    assert guards.headline_corroborated(headline, ["we completed the acquisition of Layla, an AI trip planner"], ALIASES)
    assert not guards.headline_corroborated(headline, ["Acquisitions and other, net | (400) | 163"], ALIASES)
    # A common word capitalized only at a sentence start does not count.
    assert not guards.headline_corroborated("Expedia Trip Planner News", ["Trip volumes rose. Planner tools improved."], ALIASES)
    # Real case from the Sonnet dev run: restructuring-charges row does not corroborate the Seattle layoffs headline.
    assert not guards.headline_corroborated(
        "Expedia cuts 58 Seattle jobs in second round of layoffs this year",
        ["Restructuring and related reorganization charges (1) | 13 | 44 | 69 | 70"], ALIASES,
    )


def test_personal_names_from_sources():
    src = ["Mr. Smith is in a position to influence, and potentially control, significant corporate actions"]
    assert guards.personal_names_used("Smith may be deemed to hold about 32% of voting power.", src) == ["Smith"]
    assert guards.personal_names_used("The chairman may be deemed to hold about 32% of voting power.", src) == []


# Real rows from the Q2 2026 earnings release (8k-0001324424-26-000051-ex991-p014), copied verbatim.
# The layoffs headline's "58" matched the "(58)" in these unrelated rows in the second dev run.
LAYOFFS_HEADLINE = "Expedia cuts 58 Seattle jobs in second round of layoffs this year"
RELEASE_TABLE_ROWS = (
    "Legal reserves, occupancy tax and other | 6 | 2 | (58) | 2\n"
    "Interest expense | (61) | (58) | (172) | (116)"
)


def test_layoffs_headline_not_corroborated_by_table_rows():
    assert not guards.headline_corroborated(LAYOFFS_HEADLINE, [RELEASE_TABLE_ROWS], ALIASES)


def test_entity_corroborates_and_figure_is_extra_support():
    prose = "During the quarter we eliminated 58 roles in our Seattle headquarters."
    assert guards.headline_corroborated(LAYOFFS_HEADLINE, [RELEASE_TABLE_ROWS + "\n" + prose], ALIASES)
    assert guards.shared_entities(LAYOFFS_HEADLINE, prose, ALIASES) == {"Seattle"}
    assert guards.shared_prose_figures(LAYOFFS_HEADLINE, prose, ALIASES) == {"58"}
    assert guards.shared_prose_figures(LAYOFFS_HEADLINE, RELEASE_TABLE_ROWS, ALIASES) == set()  # table rows ignored


@pytest.mark.parametrize("filing", [
    "Results for the Second Quarter improved.",  # "second" is a time/ordinal word
    "Bookings in the Fiscal Year rose; the Round of financing closed.",
])
def test_time_and_ordinal_words_do_not_corroborate(filing):
    assert not guards.headline_corroborated(LAYOFFS_HEADLINE.replace("58 Seattle", "many"), [filing], ALIASES)


def test_time_words_ignored_in_distinctive_terms():
    _, words = guards.distinctive_terms(LAYOFFS_HEADLINE, ALIASES)
    assert not {"second", "round", "year"} & {w.lower() for w in words}
    assert "Seattle" in words


# ---------------------------------------------------------------- long evidence found whole: verified, truncated

def verify(chunk_id, evidence, chunks=CHUNKS):
    return guards.verify_citation(chunk_id, evidence, chunks, set(chunks), ALIASES)


def test_long_evidence_found_whole_is_verified_and_truncated():
    long = "Technology and content expense decreased $37 million for 2025 compared to 2024 primarily due to lower"
    problem, stored, truncated = verify("10k-item7-p032", long)
    assert problem is None and truncated
    assert stored == "Technology and content expense decreased $37 million for 2025 compared to 2024 primarily due"


# Real S-01 (Sonnet dev 2): a correct phrase one word over the limit. The chunk text below is a verbatim,
# name-free excerpt of 8k-0001324424-26-000016-main-p003 (checked against the raw chunk in
# test_real_excerpts_match_raw_chunks when the raw data is present).
S01_EVIDENCE = "would step down as the Company\u2019s Chief Financial Officer, effective as of May 11, 2026"
S01_CHUNK_EXCERPT = ("would step down as the Company\u2019s Chief Financial Officer, effective as of May 11, 2026 "
                     "(the \u201cEffective Date\u201d)")


def test_real_s01_phrase_verified_and_truncated():
    chunks = {"8k-s01": {"chunk_id": "8k-s01", "source_id": "8k-x", "source_type": "filing", "text": S01_CHUNK_EXCERPT}}
    problem, stored, truncated = verify("8k-s01", S01_EVIDENCE, chunks)
    assert problem is None and truncated
    assert stored == "would step down as the Company\u2019s Chief Financial Officer, effective as of May 11,"


def test_short_evidence_still_rejected_even_if_found():
    assert verify("10k-item7-p032", "Technology and content expense")[0] == "evidence must be 6-14 words (got 4)"


# ---------------------------------------------------------------- guidance

REAL_I11_TEXT = ("In its August 2026 release, the company raised full-year 2026 guidance, now expecting Adjusted EBITDA "
                 "margin expansion of 1.5 to 1.75 points, up from 1 to 1.25 points.")
REAL_I11_EVIDENCE = "Adjusted EBITDA margin expansion** | +1 - 1.25pts | +1.5 - 1.75pts"


def test_guidance_keyword_and_outlook_rows():
    assert guards.guidance_keyword("2026 margin expansion target " + REAL_I11_TEXT) == "guidance"
    assert guards.guidance_keyword("Raised full-year outlook") == "outlook"
    assert guards.guidance_keyword("Grow the B2B business") is None
    outlook_chunk = CHUNKS["8k-0001324424-26-000051-ex991-p003"]["text"]
    assert guards.is_outlook_row(outlook_chunk, REAL_I11_EVIDENCE)
    assert not guards.is_outlook_row(CHUNKS["8k-0001324424-26-000051-ex991-p002"]["text"], "Gross Bookings | $33,928 | $30,409 | 12%")


# ---------------------------------------------------------------- biography

@pytest.mark.parametrize("text, phrase", [
    ("The new Chief Financial Officer previously served as CFO of another company.", "previously"),
    ("The incoming officer was formerly at a technology company.", "formerly"),
    ("Prior to joining, the officer led finance elsewhere.", "Prior to"),
    ("The officer served as treasurer at a retailer.", "served as"),
    ("The officer was most recently a finance chief.", "most recently"),
    ("The officer joined from a rival.", "joined from"),
    ("The Chief Financial Officer role changed effective May 11, 2026.", None),
])
def test_biography_phrase(text, phrase):
    assert guards.biography_phrase(text) == phrase


def test_real_s01_wording_flagged():
    # Real S-01 text from the second Sonnet dev run.
    assert guards.biography_phrase("A new Chief Financial Officer, previously CFO of Snap Inc., succeeds in the role") == "previously"


# ---------------------------------------------------------------- consequence words (heuristic)

# Real C-04 (Sonnet dev 2) item text, and the sentences around its two evidence phrases, verbatim from
# 10k-item1a-p037 and 10k-item1a-p041. Neither full chunk contains "fine" in any form (see
# test_real_excerpts_match_raw_chunks).
REAL_C04_TEXT = ("Short-term rental rules, such as the EU Short-Term Rental Law effective May 2026, and price and "
                 "information display rules can reduce listings and add compliance cost and fines.")
C04_P037 = ("For example, we are subject to the EU Short-Term Rental Law, which comes into effect in May 2026, and will "
            "require platforms such as ours to obtain registration numbers for all alternative accommodations in the "
            "European Union where a government has implemented a registration system.")
C04_P041_START = ("Recent examples of new website display laws or regulations include, (i) in the United States, the "
                  "Federal Trade Commission Rule on Unfair or Deceptive Fees, effective May 2025, relating to price "
                  "display for accommodation and live events")


def test_real_c04_fines_unsupported():
    assert guards.unsupported_consequence_words(REAL_C04_TEXT, [C04_P037, C04_P041_START]) == ["fines"]


def test_consequence_word_supported_by_any_form():
    assert guards.unsupported_consequence_words("Could face penalties and liability.", ["civil penalty and liabilities"]) == []
    assert guards.unsupported_consequence_words("Risk of default.", ["no event of default occurred"]) == []
    assert guards.unsupported_consequence_words("A lawsuit is pending.", ["litigation is pending"]) == ["lawsuit"]
    assert guards.unsupported_consequence_words("Fines may apply.", ["we define fees"]) == ["fines"]  # 'define' is not 'fine'


def test_real_excerpts_match_raw_chunks():
    """Checks the verbatim excerpts above against the gitignored raw chunks, when present."""
    raw = Path(__file__).resolve().parents[1] / "data" / "expedia" / "raw" / "chunks.jsonl"
    if not raw.exists():
        pytest.skip("raw Expedia chunks not present")
    real = {json.loads(l)["chunk_id"]: json.loads(l)["text"] for l in raw.read_text().splitlines() if l.strip()}
    assert S01_CHUNK_EXCERPT in real["8k-0001324424-26-000016-main-p003"]
    assert C04_P037 in real["10k-item1a-p037"] and C04_P041_START in real["10k-item1a-p041"]
    for cid in ("10k-item1a-p037", "10k-item1a-p041"):
        assert not re.search(r"(?i)\bfine", real[cid])


# ---------------------------------------------------------------- rule (e): same event or entity

def test_corroboration_requires_shared_entity_not_just_same_topic():
    headline = "Expedia Acquired AI Trip - Planner Layla : Exclusive"
    assert guards.headline_corroborated(headline, ["we completed the acquisition of Layla during the quarter"], ALIASES)
    # Same topic (an acquisition), different entity: not corroborated.
    assert not guards.headline_corroborated(headline, ["we completed the acquisition of Tiqets during the quarter"], ALIASES)


def test_figure_only_overlap_does_not_corroborate():
    headline = "Expedia cuts 58 Seattle jobs in second round of layoffs this year"
    assert not guards.headline_corroborated(headline, ["The board approved 58 new store openings."], ALIASES)



# ---------------------------------------------------------------- statement support (heuristic)

def test_stem_groups_word_forms():
    assert guards.stem("optimization") == guards.stem("optimize") == guards.stem("optimizing")
    assert guards.stem("penalties") == guards.stem("penalty")


def test_sentence_split_and_scores():
    src = ["Technology and content expense decreased $37 million for 2025 compared to 2024 primarily due to lower "
           "personnel costs in connection with previously announced cost saving initiatives as well as initiatives "
           "to optimize cloud spending."]
    text = ("Technology and content expense decreased $37 million on lower personnel costs. "
            "The company is shifting workloads to a sovereign European data center.")
    supported, unsupported = guards.statement_support(text, src)
    assert supported.score == 1.0 and unsupported.score < 0.75
    assert {"sovereign", "European", "data", "center"} <= set(unsupported.missing)
    assert [w.sentence for w in guards.unsupported_statements(text, src)] == [unsupported.sentence]


def test_missing_figure_flags_even_with_high_score():
    src = ["The Chairman may be deemed to beneficially own shares representing voting power over the company."]
    text = "The Chairman may be deemed to beneficially own shares representing about 32% of voting power."
    (only,) = guards.unsupported_statements(text, src)
    assert only.score >= 0.75 and "32%" in only.missing


def test_attribution_words_not_counted():
    src = ["Expedia acquired the AI trip planner Layla."]
    assert guards.unsupported_statements("A news headline reports the company acquired AI trip planner Layla.", src) == []


def test_short_sentences_not_scored():
    assert guards.statement_support("Yes. No.", ["x"])[0].score is None



# ---------------------------------------------------------------- mis-citation search

# Real C-12 (third dev run) and verbatim, name-free excerpts: the chunk it cited (10k-item1a-p054), the
# Class B ownership paragraph that holds the 32% (10k-item1a-p053), and an unrelated earnings-release table
# row that also contains "32%" (8k-0001324424-26-000051-ex991-p003). Checked against the raw chunks below.
C12_TEXT = ("The Chairman may be deemed to hold about 32% of voting power and shares a role at IAC, which could "
            "create conflicts and discourage change of control transactions.")
C12_CITED_P054 = (  # the four name-free sentences of 10k-item1a-p054, verbatim
    "Additionally, in the future, another holder of the Class B Shares might have such a position of influence by "
    "virtue of ownership interests in the Class B Shares. This concentrated ownership position could discourage "
    "others from initiating any potential merger, takeover or other change of control transaction that may "
    "otherwise be beneficial to Expedia Group stockholders. Actual or potential conflicts of interest may develop "
    "between Expedia Group management and directors, on the one hand, and the management and directors of IAC, on "
    "the other. These overlapping relationships could create, or appear to create, potential conflicts of interest "
    "for the directors or officers when facing decisions that may affect both IAC and Expedia Group."
)
CLASS_B_P053 = ("may be deemed to have beneficially owned 100% of Expedia Group\u2019s outstanding Class B common stock, "
                "representing approximately 32% of the total voting power of all shares of Expedia Group common stock "
                "and Class B common stock outstanding.")
RELEASE_ROW_32 = "Net cash provided by operating activities | $1,478 | $1,121 | 32%"


def test_real_c12_32_percent_found_in_class_b_paragraph():
    (weak,) = guards.unsupported_statements(C12_TEXT, [C12_CITED_P054])
    assert "32%" in weak.missing
    found = guards.find_supporting_chunks(
        weak.sentence, [C12_CITED_P054],
        {"10k-item1a-p053": CLASS_B_P053, "8k-0001324424-26-000051-ex991-p003": RELEASE_ROW_32},
    )
    assert found == ["10k-item1a-p053"]  # the table row's 32% is not counted


def test_unsupported_when_no_allowed_chunk_has_the_terms():
    text = "The company plans satellite broadband sponsorships across airline lounges."
    assert guards.find_supporting_chunks(text, ["Advertising revenue grew."], {"x": CLASS_B_P053, "y": RELEASE_ROW_32}) == []


def test_mis_citation_excerpts_match_raw_chunks():
    raw = Path(__file__).resolve().parents[1] / "data" / "expedia" / "raw" / "chunks.jsonl"
    if not raw.exists():
        pytest.skip("raw Expedia chunks not present")
    real = {json.loads(l)["chunk_id"]: json.loads(l)["text"] for l in raw.read_text().splitlines() if l.strip()}
    p054 = real["10k-item1a-p054"].replace("\n", " ")
    import re as _re
    for sentence in _re.split(r"(?<=[.])\s+(?=[A-Z])", C12_CITED_P054):
        assert sentence in p054
    assert CLASS_B_P053 in real["10k-item1a-p053"]
    assert RELEASE_ROW_32 in real["8k-0001324424-26-000051-ex991-p003"].split("\n")



# ---------------------------------------------------------------- document names, role titles, placeholders, results

def test_form_names_are_not_figures():
    src = ["We operate in an intensely competitive global environment."]
    assert guards.unsupported_statements("The 10-K says the company operates in an intensely competitive global environment.", src) == []
    for form in ("10-Q", "8-K", "10-K/A"):
        assert "#10" not in guards._terms(f"The {form} reports results.") and "#8" not in guards._terms(f"The {form} reports.")


def test_role_titles_are_attribution():
    src = ["We are using AI as a force multiplier to innovate faster and operate more efficiently."]
    text = "The Chief Executive Officer said the company is using AI as a force multiplier to innovate faster."
    assert guards.unsupported_statements(text, src) == []
    assert guards.unsupported_statements("The CFO and the Chairman said the company is using AI as a force multiplier.", src) == []
    assert "financial" in guards._terms("Our financial condition could suffer.").values()  # "financial" alone still counts


@pytest.mark.parametrize("chunk_id, evidence", [
    ("11k-placeholder", "placeholder"),  # real dev-4 C-02
    ("10k-item7-p008", "n/a"), ("TBD", "advertising is responsible for generating advertising revenue"),
    ("10k-item7-p008", "xxx xxx xxx xxx xxx xxx"),
])
def test_placeholder_citations_rejected(chunk_id, evidence):
    problem, _, _ = guards.verify_citation(chunk_id, evidence, CHUNKS, set(CHUNKS) | {chunk_id}, ALIASES)
    assert problem == "placeholder citation"


def test_real_evidence_not_mistaken_for_placeholder():
    assert not guards.is_placeholder("Expedia Group (\u201cEG\u201d) Advertising is responsible for generating advertising revenue")
    assert not guards.is_placeholder("financial") and not guards.is_placeholder("canal")


@pytest.mark.parametrize("sentence, word", [
    ("The company reported expanded margins.", "reported"), ("Revenue grew 14%.", "grew"),
    ("The CEO attributed margin gains to execution.", "attributed"), ("The company is expanding One Key.", None),
])
def test_result_word(sentence, word):
    assert guards.result_word(sentence) == word



# ---------------------------------------------------------------- chunk dates count toward statement support

# Real I-01 sentence 3 from dev run 5, and a verbatim, name-free excerpt of its cited chunk
# (8k-0001324424-26-000051-ex991-p002, filed and dated 2026-08-05).
I01_S3 = ("On August 5, 2026, the Chief Executive Officer said the company is leveraging AI to innovate faster and "
          "operate more efficiently.")
I01_CHUNK = ("We continued to strengthen our marketplace through more personalized consumer product experiences and "
             "expanded supply across our business, while leveraging AI as a force multiplier to innovate faster and "
             "operate more efficiently.")


def test_written_dates():
    assert guards.written_dates(["2026-08-05", "2026-04-17", None, "2026-08-05"]) == "August 5, 2026. April 17, 2026"


def test_real_i01_date_from_chunk_metadata_is_supported():
    assert guards.unsupported_statements(I01_S3, [I01_CHUNK])  # without the chunk's date: flagged (the run-5 alarm)
    assert guards.unsupported_statements(I01_S3, [I01_CHUNK, guards.written_dates(["2026-08-05"])]) == []


def test_wrong_date_still_flagged():
    wrong = I01_S3.replace("August 5, 2026", "September 9, 2026")
    (flag,) = guards.unsupported_statements(wrong, [I01_CHUNK, guards.written_dates(["2026-08-05"])])
    assert "September" in flag.missing and "9," in flag.missing


def test_i01_excerpt_matches_raw_chunk():
    raw = Path(__file__).resolve().parents[1] / "data" / "expedia" / "raw" / "chunks.jsonl"
    if not raw.exists():
        pytest.skip("raw Expedia chunks not present")
    real = {json.loads(l)["chunk_id"]: json.loads(l) for l in raw.read_text().splitlines() if l.strip()}
    chunk = real["8k-0001324424-26-000051-ex991-p002"]
    assert I01_CHUNK in chunk["text"] and chunk["date"] == "2026-08-05"
