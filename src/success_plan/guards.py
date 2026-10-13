"""Deterministic rule checks applied to model output. No model calls here.

Each check is a small pure function so it can be tested on its own:
  - citation guard: the cited evidence must appear word for word in the cited chunk
  - price guard: currency amounts must trace to a cited filing chunk and must not sit near pricing words
  - long-quote guard: item text must not copy 15 or more consecutive words from a source
  - personal-title and personal-name guards: roles only, no honorifics or names of people
  - market-signal filter: analyst ratings and stock moves are excluded
  - financing backstop: financing and capital-return keywords exclude an initiative
  - headline corroboration: a filing must share a distinctive term with a cited headline
    (term overlap, not meaning)
  - guidance filter: guidance and outlook items are not initiatives
  - biography check: no prior employers or career history in change items
  - consequence-word check: a heuristic for fines, penalties, etc. that no cited source mentions
  - statement-support check: a heuristic for sentences whose terms the cited chunks mostly lack
"""

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

EVIDENCE_MIN_WORDS = 6
EVIDENCE_MAX_WORDS = 14
LONG_QUOTE_WORDS = 15
PRICE_CONTEXT_WORDS = 8

# ---------------------------------------------------------------- normalization

_QUOTE_MAP = str.maketrans({"‘": "'", "’": "'", "‚": "'", "′": "'", "“": '"', "”": '"', "„": '"'})
_DASHES = re.compile("[‐‑‒–—−]")


def normalize(text: str) -> str:
    """Case, spacing, quote style, dash style, table separators, and GDELT punctuation spacing only."""
    t = text.translate(_QUOTE_MAP)
    t = _DASHES.sub("-", t)
    t = t.replace("\xa0", " ").replace("|", " ")
    # Remove spaces around punctuation so "Hotels . com" == "Hotels.com" and "( EXPE )" == "(EXPE)".
    t = re.sub(r"\s*([.,:;!?()'\"/\-])\s*", r"\1", t)
    # Tables put the currency symbol in its own cell ("$ | 119,590"); match "$119,590" too.
    t = re.sub(r"([$€£¥])\s+(?=\d)", r"\1", t)
    return re.sub(r"\s+", " ", t).strip().lower()


def _words(text: str) -> list[str]:
    """Words for counting: whitespace tokens that contain a letter or digit."""
    return [t for t in text.split() if re.search(r"[A-Za-z0-9]", t)]


def _contains_phrase(haystack_norm: str, needle_norm: str) -> bool:
    """Phrase match on word boundaries, so 'cost structure' does not match 'cost structures'.

    The boundary check applies only at an end of the phrase that is a letter or digit. Normalization
    removes spaces after punctuation, so a phrase ending "2024." sits directly before the next
    sentence ("2024.we also"); requiring a non-letter after the period would wrongly reject it.
    """
    if not needle_norm:
        return False
    before = r"(?<![a-z0-9])" if needle_norm[0].isalnum() else ""
    after = r"(?![a-z0-9])" if needle_norm[-1].isalnum() else ""
    return re.search(before + re.escape(needle_norm) + after, haystack_norm) is not None


# ---------------------------------------------------------------- citation guard

_NAME_FILLER = {"inc", "group", "corp", "corporation", "co", "company", "ltd", "plc", "the", "and", "nasdaq", "nyse", "s"}


def is_name_only(evidence: str, aliases: list[str]) -> bool:
    """True if the evidence is only the company name, ticker, or brand names (plus legal filler)."""
    t = normalize(evidence)
    for alias in sorted(aliases, key=len, reverse=True):
        a = normalize(alias)
        if a:
            t = re.sub(rf"(?<![a-z0-9]){re.escape(a)}(?![a-z0-9])", " ", t)
    remaining = [w for w in re.findall(r"[a-z0-9]+", t) if w not in _NAME_FILLER]
    return not remaining


def first_words(text: str, n: int) -> str:
    """The text up to and including its n-th counted word (punctuation-only tokens are kept, not counted)."""
    out, count = [], 0
    for token in text.split():
        out.append(token)
        if re.search(r"[A-Za-z0-9]", token):
            count += 1
            if count == n:
                break
    return " ".join(out)


PLACEHOLDER_REASON = "placeholder citation"
_PLACEHOLDER = re.compile(r"placeholder|(?<![A-Za-z0-9])n/a(?![A-Za-z0-9])|\btbd\b|xxx", re.IGNORECASE)


def is_placeholder(text: str) -> bool:
    """A citation the model filled with a stand-in instead of a real chunk id or evidence phrase."""
    return _PLACEHOLDER.search(text or "") is not None


def verify_citation(chunk_id: str, evidence: str, chunks: dict[str, dict], allowed_ids: set[str],
                    aliases: list[str], *, kind: str | None = None) -> tuple[str | None, str, bool]:
    """Check one citation. Returns (problem or None, evidence to store, truncated).

    Evidence over 14 words is accepted when the whole phrase appears in the chunk; only its first 14 words
    are stored and the citation is marked truncated. Evidence under 6 words is always rejected.
    """
    if is_placeholder(chunk_id) or is_placeholder(evidence):
        return PLACEHOLDER_REASON, evidence, False
    chunk = chunks.get(chunk_id)
    if chunk is None:
        return "unknown chunk id", evidence, False
    if kind == "initiative" and chunk.get("source_id") == "10k-item1a":
        return "Risk Factors cannot support an initiative", evidence, False
    if chunk_id not in allowed_ids:
        return "chunk was not among this call's sources", evidence, False
    n_evidence = len(_words(evidence))
    n_chunk = len(_words(chunk["text"]))
    too_long = False
    if n_chunk < EVIDENCE_MIN_WORDS:
        if normalize(evidence) != normalize(chunk["text"]):
            return f"chunk has fewer than {EVIDENCE_MIN_WORDS} words; evidence must be the whole chunk", evidence, False
    elif n_evidence < EVIDENCE_MIN_WORDS:
        return f"evidence must be {EVIDENCE_MIN_WORDS}-{EVIDENCE_MAX_WORDS} words (got {n_evidence})", evidence, False
    elif n_evidence > EVIDENCE_MAX_WORDS:
        too_long = True
    if is_name_only(evidence, aliases):
        return "evidence is only a company or brand name", evidence, False
    if not _contains_phrase(normalize(chunk["text"]), normalize(evidence)):
        if too_long:
            return f"evidence must be {EVIDENCE_MIN_WORDS}-{EVIDENCE_MAX_WORDS} words (got {n_evidence}) and was not found in chunk", evidence, False
        return "evidence not found in chunk", evidence, False
    if too_long:
        return None, first_words(evidence, EVIDENCE_MAX_WORDS), True
    return None, evidence, False


def citation_problem(chunk_id: str, evidence: str, chunks: dict[str, dict], allowed_ids: set[str],
                     aliases: list[str], *, kind: str | None = None) -> str | None:
    """Return why a citation fails, or None if it is verified."""
    return verify_citation(chunk_id, evidence, chunks, allowed_ids, aliases, kind=kind)[0]


# ---------------------------------------------------------------- price guard

_NUM = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
_SCALE = r"billion|million|thousand|bn|mm|b|m|k"
_CODE = r"USD|EUR|GBP|JPY|CAD|AUD"
_PREFIXED = re.compile(rf"(?<![\w.])(?:[$€£¥]|(?:{_CODE})\s?)\s*({_NUM})(?![\d.,]*\d)(?:\s?({_SCALE})\b)?", re.IGNORECASE)
_SUFFIXED = re.compile(
    rf"(?<![\w.,$€£¥])({_NUM})(?![\d.,]*\d)(?:\s?({_SCALE})\b)?\s?(?:dollars|euros|pounds|{_CODE})\b", re.IGNORECASE
)
_BARE_SCALED = re.compile(rf"(?<![\w.,])({_NUM})(?![\d.,]*\d)\s?(billion|million|thousand)\b", re.IGNORECASE)
_SCALE_CANON = {"billion": "billion", "bn": "billion", "b": "billion", "million": "million", "mm": "million",
                "m": "million", "thousand": "thousand", "k": "thousand"}
_PRICING_WORDS = re.compile(r"\bper[\s-]?(?:user|seat)s?\b|\blicen[cs]\w*|\bsubscription\w*|\bdiscount\w*", re.IGNORECASE)


@dataclass(frozen=True)
class Amount:
    value: Decimal
    scale: str  # "", "thousand", "million", "billion"
    raw: str
    start: int
    end: int

    @property
    def key(self) -> tuple[Decimal, str]:
        return (self.value, self.scale)


def _amount(match: re.Match) -> Amount | None:
    try:
        value = Decimal(match.group(1).replace(",", ""))
    except InvalidOperation:
        return None
    scale = _SCALE_CANON.get((match.group(2) or "").lower(), "")
    return Amount(value.normalize(), scale, match.group(0).strip(), match.start(), match.end())


def find_currency_amounts(text: str) -> list[Amount]:
    """Amounts with a currency marker ($, EUR, 'dollars', ...), with their scale words."""
    found = {}
    for pattern in (_PREFIXED, _SUFFIXED):
        for m in pattern.finditer(text):
            a = _amount(m)
            if a:
                found[(a.start, a.end)] = a
    return sorted(found.values(), key=lambda a: a.start)


def _source_amount_keys(text: str) -> set[tuple[Decimal, str]]:
    """Amounts in a source chunk: currency-marked, or a number with a scale word (tables often omit '$')."""
    flat = text.replace("|", " ")
    keys = {a.key for a in find_currency_amounts(flat)}
    for m in _BARE_SCALED.finditer(flat):
        a = _amount(m)
        if a:
            keys.add(a.key)
    return keys


def price_flags(item_text: str, cited_filing_texts: list[str]) -> list[str]:
    """Reasons to send an item to price review. Amounts are kept; nothing is deleted.

    An amount is allowed only if the same number with the same scale appears in a verified cited
    filing chunk (whole-number match: 1.3 never matches 11.3). Any amount near pricing words
    (per user, per seat, license, subscription, discount) is flagged even if it traces.
    """
    source_keys = set()
    for t in cited_filing_texts:
        source_keys |= _source_amount_keys(t)
    reasons = []
    for a in find_currency_amounts(item_text):
        before = item_text[: a.start].split()[-PRICE_CONTEXT_WORDS:]
        after = item_text[a.end:].split()[:PRICE_CONTEXT_WORDS]
        if _PRICING_WORDS.search(" ".join(before + after)):
            reasons.append(f"amount {a.raw!r} appears near product-pricing words")
        elif a.key not in source_keys:
            reasons.append(f"amount {a.raw!r} does not trace to a cited filing chunk")
    return reasons


# ---------------------------------------------------------------- other text checks

def long_quote(item_text: str, source_texts: list[str], n: int = LONG_QUOTE_WORDS) -> bool:
    """True if item text copies n or more consecutive words from any source text."""
    return bool(long_quote_spans([item_text], source_texts, n, first_only=True))


def quote_grams(text: str, n: int = LONG_QUOTE_WORDS) -> set[tuple[str, ...]]:
    """Every run of n consecutive words, as the long-quote check counts them (normalized; a hyphenated word is two)."""
    words = re.findall(r"[a-z0-9]+", normalize(text))
    return {tuple(words[i:i + n]) for i in range(len(words) - n + 1)}


def long_quote_spans(texts: list[str], source_texts: list[str], n: int = LONG_QUOTE_WORDS, *,
                     first_only: bool = False) -> list[str]:
    """Runs of n consecutive words (normalized) that any of texts copies from any source text. Each text is
    checked on its own, so a run never spans two texts."""
    grams = set()
    for text in texts:
        grams |= quote_grams(text, n)
    if not grams:
        return []
    found = []
    for src in source_texts:
        src_words = re.findall(r"[a-z0-9]+", normalize(src))
        for i in range(len(src_words) - n + 1):
            gram = tuple(src_words[i:i + n])
            if gram in grams:
                found.append(" ".join(gram))
                grams.discard(gram)
                if first_only:
                    return found
    return found


_HONORIFIC = re.compile(r"\b(?:Mr|Ms|Mrs|Mx|Dr)\.?\s+[A-Z][a-z]")


def has_personal_title(text: str) -> bool:
    return _HONORIFIC.search(text) is not None


_MARKET_SIGNAL = re.compile(
    r"\b(?:upgrad\w*|downgrad\w*|underweight|overweight|outperform|underperform|price target|"
    r"(?:buy|sell|hold) rating|analysts?\b|stock\b|stocks\b|shares (?:up|down|rise|rose|fall|fell|jump\w*|drop\w*|slide\w*|surge\w*)|"
    r"position in|stake in|shares of)",
    re.IGNORECASE,
)


def is_market_signal(headline: str) -> bool:
    """Analyst ratings, price targets, stock moves, and fund position posts."""
    return _MARKET_SIGNAL.search(re.sub(r"\s+", " ", headline)) is not None


# ---------------------------------------------------------------- financing and capital return

_FINANCING_KEYWORDS = re.compile(
    r"\b(?:share\s+)?repurchas\w*|\bbuy-?backs?\b|\bdividends?\b|\bcredit\s+facilit\w*|\bnotes?\s+offering\w*",
    re.IGNORECASE,
)


def financing_keyword(text: str) -> str | None:
    """The first financing or capital-return keyword in the text, if any (backstop for initiatives)."""
    m = _FINANCING_KEYWORDS.search(text)
    return m.group(0) if m else None


# ---------------------------------------------------------------- headline corroboration
#
# This checks TERM OVERLAP, not meaning. A headline counts as corroborated by a filing chunk only when
# the chunk names the same entity: a proper noun from the headline (a place, acquired company, product)
# written with a capital letter in the middle of a sentence in the filing. A shared figure alone never
# corroborates; it is only extra support when an entity also matches (figures in prose lines only;
# table rows are dense with numbers and matched a "58 jobs" headline by chance). Company names, the
# ticker, brand aliases, and time or ordinal words do not count as entities. Whether the filing
# describes the same EVENT is not checked. A filing that shares a term with a headline may still describe something else, so
# corroborated items still go to CSM validation like every other item.

_CORROBORATION_STOP = {
    "the", "and", "for", "with", "from", "into", "over", "under", "after", "before", "its", "his", "her", "their",
    "this", "that", "what", "why", "how", "new", "news", "exclusive", "report", "reports", "says", "said",
    "inc", "group", "company", "corp", "corporation", "ltd", "plc", "nasdaq", "nyse",
    "january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
    "november", "december", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    # time and ordinal words ("second round", "the Second Quarter")
    "first", "second", "third", "fourth", "fifth", "sixth", "last", "next", "latest", "previous", "prior",
    "round", "rounds", "quarter", "quarters", "quarterly", "year", "years", "yearly", "annual", "annually",
    "half", "month", "months", "monthly", "week", "weeks", "weekly", "day", "days", "daily", "fiscal",
    "full", "today", "yesterday", "recent", "recently", "again", "ytd",
}


def _alias_words(aliases: list[str]) -> set[str]:
    words = set()
    for a in aliases:
        words |= {w.lower() for w in re.findall(r"[A-Za-z0-9]+", a)}
    return words


def distinctive_terms(headline: str, aliases: list[str]) -> tuple[set[str], set[str]]:
    """(figures, words) from a headline that could tie it to a filing."""
    alias_words = _alias_words(aliases)
    figures = set()
    for m in re.finditer(r"(?<![\d.,])\d[\d,]*(?:\.\d+)?", headline):
        digits = m.group(0).replace(",", "")
        if len(digits.split(".")[0]) < 2 or re.fullmatch(r"(19|20)\d\d", digits):
            continue
        figures.add(digits)
    words = {
        w for w in re.findall(r"[A-Za-z][A-Za-z'&-]*[A-Za-z]|[A-Za-z]", headline)
        if len(w) >= 3 and w.lower() not in _CORROBORATION_STOP and w.lower() not in alias_words
    }
    return figures, words


def shared_entities(headline: str, filing_text: str, aliases: list[str]) -> set[str]:
    """Proper nouns from the headline that the filing writes with a capital letter mid-sentence."""
    _, words = distinctive_terms(headline, aliases)
    flat = filing_text.replace("|", " ")
    found = set()
    for w in words:
        proper = w[0].upper() + w[1:]
        # Capitalized mid-sentence: preceded by a lowercase letter, digit, or comma-type mark and a space.
        if re.search(rf"(?<=[a-z0-9,;:]\s){re.escape(proper)}(?![A-Za-z])", flat):
            found.add(proper)
    return found


def shared_prose_figures(headline: str, filing_text: str, aliases: list[str]) -> set[str]:
    """Headline figures that appear in prose lines of the filing (extra support only)."""
    figures, _ = distinctive_terms(headline, aliases)
    prose = "\n".join(line for line in filing_text.split("\n") if " | " not in line)  # table rows excluded
    numbers = {n.replace(",", "") for n in re.findall(r"(?<![\d.,])\d[\d,]*(?:\.\d+)?", prose)}
    return figures & numbers


def headline_corroborated(headline: str, filing_texts: list[str], aliases: list[str]) -> bool:
    """True only if some filing chunk names an entity from the headline (term overlap, not meaning)."""
    return any(shared_entities(headline, text, aliases) for text in filing_texts)


# ---------------------------------------------------------------- personal names

_HONORIFIC_NAME = re.compile(r"\b(?:Mr|Ms|Mrs|Mx|Dr)\.?\s+([A-Z][a-zA-Z'-]+)")


def names_in_sources(source_texts: list[str]) -> set[str]:
    """Surnames that the sources introduce with an honorific (e.g. 'Mr. Smith' -> 'Smith')."""
    names = set()
    for t in source_texts:
        names |= set(_HONORIFIC_NAME.findall(t.translate(_QUOTE_MAP)))
    return names


def personal_names_used(item_text: str, source_texts: list[str]) -> list[str]:
    """Names of people from the cited sources that the item text repeats (roles only, no names)."""
    return sorted(n for n in names_in_sources(source_texts) if re.search(rf"\b{re.escape(n)}\b", item_text))


# ---------------------------------------------------------------- guidance and outlook

_GUIDANCE_WORDS = re.compile(r"\b(guidance|outlook)\b", re.IGNORECASE)
_OUTLOOK_HEADING = re.compile(r"^\s*(?:business\s+)?outlook\s*$", re.IGNORECASE)


def guidance_keyword(text: str) -> str | None:
    m = _GUIDANCE_WORDS.search(text)
    return m.group(0) if m else None


def is_outlook_row(chunk_text: str, evidence: str) -> bool:
    """True if the evidence sits in a table block that follows an "Outlook" or "Business Outlook" heading."""
    lines = chunk_text.split("\n")
    target = normalize(evidence)
    for i, line in enumerate(lines):
        if not _OUTLOOK_HEADING.match(line):
            continue
        block = []
        for row in lines[i + 1:]:
            if " | " not in row:
                break
            block.append(row)
        if block and _contains_phrase(normalize("\n".join(block)), target):
            return True
    return False


# ---------------------------------------------------------------- biography (roles only)

_BIOGRAPHY = re.compile(
    r"\bpreviously\b|\bprior\s+to\b|\bformerly\b|\bserved\s+as\b|\bmost\s+recently\b|\bjoined\s+from\b",
    re.IGNORECASE,
)


def biography_phrase(text: str) -> str | None:
    """Career-history wording in a change item (roles only: no prior employers or career history).

    Broad on purpose: it also matches ordinary uses such as "previously announced" or "prior to the
    acquisition". It only flags for review; it never removes an item.
    """
    m = _BIOGRAPHY.search(text)
    return m.group(0) if m else None


# ---------------------------------------------------------------- consequence words
#
# A HEURISTIC, NOT A PROOF. If an item states a legal or financial consequence (fines, penalties, a
# lawsuit, bankruptcy, default, liability) and none of its verified cited chunks uses that word (or a
# form of it), the item may be overreaching beyond its sources. A source can imply a consequence in other
# words, and a word can appear in a chunk without supporting the item's sentence, so flagged items go to
# review; nothing is removed.

_CONSEQUENCE = re.compile(r"\b(fines?|penalty|penalties|lawsuits?|bankruptcy|default|liability)\b", re.IGNORECASE)
_CONSEQUENCE_STEM = {
    "fine": "fine", "fines": "fine", "penalty": "penalt", "penalties": "penalt", "lawsuit": "lawsuit",
    "lawsuits": "lawsuit", "bankruptcy": "bankrupt", "default": "default", "liability": "liabilit",
}


def unsupported_consequence_words(item_text: str, cited_texts: list[str]) -> list[str]:
    found = []
    for m in _CONSEQUENCE.finditer(item_text):
        word = m.group(0).lower()
        stem = _CONSEQUENCE_STEM[word]
        if not any(re.search(rf"\b{stem}\w*", t, re.IGNORECASE) for t in cited_texts) and word not in found:
            found.append(word)
    return found


# ---------------------------------------------------------------- statement support
#
# A HEURISTIC, NOT A PROOF. Each sentence of an item's text is reduced to its terms: content words
# (lowercased, stopwords dropped, lightly stemmed), numbers, and proper nouns. The score is the share of
# those terms found anywhere in the union of the item's verified cited chunks. A low score means the
# sentence says things its citations do not contain, which is how unsupported additions and
# generalizations show up. It cannot see meaning: a sentence can reuse source words and still misstate
# them (scores high), or paraphrase a source faithfully with different words (scores low). Flagged
# sentences go to review; nothing is removed. A sentence is also flagged when it states a figure that none
# of the cited chunks contains, whatever its score: a figure is a precise claim.

STATEMENT_SUPPORT_THRESHOLD = 0.75  # flag a sentence when under 75% of its terms are in its cited chunks
STATEMENT_MIN_TERMS = 3  # sentences with fewer terms are not scored

_STOPWORDS = set("""
a about above after again against all also although am among an and any are as at be because been before
being below between both but by can could did do does doing down during each either etc few for from further
had has have having he her here hers him his how however i if in into is it its itself just less like may
might more most much must my no nor not now of off on once only or other our ours out over own per rather
same she should since so some such than that the their theirs them then there these they this those through
thus to too under until up upon us very via was we were what when where whether which while who whom whose
why will with within without would yet you your
company companys expedia group inc including include includes included says said say notes note reports
report reported according also new
news headline headlines filing filings release releases management latest cited confirmed states stated
shows show source sources
""".split())  # last two lines: attribution words that describe the source, not the claim

_SUFFIXES = ("ational", "ations", "ation", "nesses", "ness", "ments", "ment", "ities", "ity", "ings", "ing",
             "ies", "ied", "ers", "er", "ed", "es", "ly", "al", "s", "e")


def stem(word: str) -> str:
    """Light suffix stripping so 'optimization', 'optimize', and 'optimizing' share a stem."""
    w = word.lower().strip("'’")
    if w.endswith("’s") or w.endswith("'s"):
        w = w[:-2]
    for suffix in _SUFFIXES:
        if w.endswith(suffix) and len(w) - len(suffix) >= 3:
            w = w[: -len(suffix)] + ("y" if suffix in ("ies", "ied") else "")
            break
    return w


# Removed before scoring: SEC form names are document names, not figures ("10-K" is not the number 10), and
# role titles are attribution (roles-only writing puts "the Chief Executive Officer" where a source has a name).
_DOCUMENT_NAMES = re.compile(r"\b(?:10-K|10-Q|8-K)(?:/A)?\b", re.IGNORECASE)
_ROLE_TITLES = re.compile(
    r"\b(?:chief\s+executive\s+officer|chief\s+financial\s+officer|chairman|controlling\s+shareholder|ceo|cfo)s?\b",
    re.IGNORECASE,
)


def _terms(text: str) -> dict[str, str]:
    """Stemmed content words and normalized numbers (proper nouns included, lowercased) -> surface form."""
    t = text.translate(_QUOTE_MAP).replace("|", " ")
    t = _ROLE_TITLES.sub(" ", _DOCUMENT_NAMES.sub(" ", t))
    terms: dict[str, str] = {}
    for token in re.findall(r"\d[\d,]*(?:\.\d+)?%?|[A-Za-z][A-Za-z'’]*", t):
        if token[0].isdigit():
            terms.setdefault("#" + token.rstrip("%").replace(",", ""), token)
        elif token.lower().strip("'’") not in _STOPWORDS and len(token) > 1:
            terms.setdefault(stem(token), token)
    return terms


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"“(])", text.strip())
    return [p.strip() for p in parts if p.strip()]


@dataclass(frozen=True)
class SentenceSupport:
    sentence: str
    score: float | None  # None when the sentence has too few terms to score
    missing: tuple[str, ...]


def statement_support(item_text: str, cited_texts: list[str]) -> list[SentenceSupport]:
    source: set[str] = set()
    for t in cited_texts:
        source |= set(_terms(t))
    out = []
    for sentence in split_sentences(item_text):
        terms = _terms(sentence)
        if len(terms) < STATEMENT_MIN_TERMS:
            out.append(SentenceSupport(sentence, None, ()))
            continue
        missing = [surface for key, surface in terms.items() if key not in source]
        out.append(SentenceSupport(sentence, round(1 - len(missing) / len(terms), 3), tuple(missing)))
    return out


def unsupported_statements(item_text: str, cited_texts: list[str],
                           threshold: float = STATEMENT_SUPPORT_THRESHOLD) -> list[SentenceSupport]:
    """Sentences scoring under the threshold, or stating a figure no cited chunk contains."""
    return [
        s for s in statement_support(item_text, cited_texts)
        if s.score is not None and (s.score < threshold or any(m[0].isdigit() for m in s.missing))
    ]


def _terms_figures_from_prose(text: str) -> set[str]:
    """Terms of a chunk, but with figures taken only from prose lines (table rows match figures by chance)."""
    prose = "\n".join(line for line in text.split("\n") if " | " not in line)
    words = {k for k in _terms(text) if not k.startswith("#")}
    figures = {k for k in _terms(prose) if k.startswith("#")}
    return words | figures


def find_supporting_chunks(sentence: str, cited_texts: list[str], candidates: dict[str, str],
                           threshold: float = STATEMENT_SUPPORT_THRESHOLD, limit: int = 3) -> list[str]:
    """Allowed but uncited chunks that would support a flagged sentence (mis-citation search).

    A candidate qualifies if adding it to the item's cited chunks lifts the sentence to the threshold and
    leaves no figure missing. Candidates are ranked by how many missing terms they supply. Uses the same
    threshold as the statement-support check; same heuristic limits apply.
    """
    terms = _terms(sentence)
    if len(terms) < STATEMENT_MIN_TERMS:
        return []
    cited: set[str] = set()
    for t in cited_texts:
        cited |= set(_terms(t))
    missing = {k for k in terms if k not in cited}
    if not missing:
        return []
    ranked = []
    for chunk_id, text in candidates.items():
        supplied = missing & _terms_figures_from_prose(text)
        still_missing = missing - supplied
        score = 1 - len(still_missing) / len(terms)
        if supplied and score >= threshold and not any(k.startswith("#") for k in still_missing):
            ranked.append((-len(supplied), chunk_id))
    return [chunk_id for _, chunk_id in sorted(ranked)[:limit]]



# ---------------------------------------------------------------- results wording in initiatives
#
# A CRUDE KEYWORD CHECK. An initiative states what the company is doing or intends to do; reporting a
# result, or the cause of a result, is not an initiative. Sentences using result words are flagged for
# review only; the item is never dropped. It misses results phrased other ways and flags legitimate uses.

_RESULT_WORDS = re.compile(r"\b(reported|attributed|achieved|grew|increased)\b", re.IGNORECASE)


def result_word(sentence: str) -> str | None:
    m = _RESULT_WORDS.search(sentence)
    return m.group(0) if m else None



def written_dates(iso_dates: list) -> str:
    """A chunk's own filing and event dates in written form ("August 5, 2026"), to score against.

    The prompt asks the model to write dates instead of "the same date"; those dates often come from the
    chunk's metadata (the filing or event date) rather than its text. Only the cited chunk's own dates are
    added, so a sentence that writes any other date is still flagged.
    """
    from datetime import date as _date

    out = []
    for d in iso_dates:
        if not d:
            continue
        value = d if isinstance(d, _date) else _date.fromisoformat(str(d))
        out.append(f"{value.strftime('%B')} {value.day}, {value.year}")
    return ". ".join(dict.fromkeys(out))


# ---------------------------------------------------------------- Map: plan tiers, outcome promises, customer framing

# Plan-level availability. Used to withhold config lines and clauses from the model, and to drop any benefit
# sentence that states a plan or tier. Tier names count only when capitalized ("Premium"), so ordinary words
# ("standard practice") and the Jira feature name "Plans" are not caught.
PLAN_TIER = re.compile(
    r"\bby plan\b|\bplan[- ]?tiers?\b|\bplan comparison\b|\bin one plan\b|\bpaid add-?on\b|\bpaid plans?\b"
    r"|\b(?:lower|higher|other|all|every|each)\s+plans\b"
    r"|\b(?:free|standard|premium|enterprise)[\s-](?:plans?|tiers?|editions?|subscriptions?|customers?)\b"
    r"|(?-i:\b(?:Free|Standard|Premium|Enterprise)\b)",
    re.IGNORECASE,
)


def plan_tier_phrase(text: str) -> str | None:
    m = PLAN_TIER.search(text)
    return m.group(0) if m else None


# Fit rationale checks. A rationale is a judgment, so word overlap with its sources is not a useful test
# (2026-10-12 dev run: every rationale scored low on overlap). It is flagged only when it adds a figure, a proper
# noun, or a plan or price term that its sources do not contain.
_INFERENCE_PREFIX = re.compile(r"^\s*[*_]*inference[*_]*\s*[:\-\u2013\u2014]\s*", re.IGNORECASE)
_FIGURE = re.compile(r"(?<![\w.,])\d[\d,]*(?:\.\d+)?")
_PLAN_TERM = re.compile(
    r"\b(?:plans?|tiers?|editions?|pric(?:e|es|ed|ing)|licen[cs](?:e|es|ed|ing)|discounts?|subscriptions?)\b",
    re.IGNORECASE,
)
# A capitalized word preceded by a lowercase letter, digit, or comma-type mark and a space: mid-sentence.
_MID_SENTENCE_PROPER = re.compile(r"(?<=[a-z0-9,;:)]\s)[A-Z][A-Za-z0-9'&-]*")


def strip_inference_prefix(text: str) -> str:
    """Remove a leading "Inference:" label; code applies the inference label itself."""
    return _INFERENCE_PREFIX.sub("", text, count=1).strip()


def _word_set(texts: list[str]) -> set[str]:
    return {w.lower() for t in texts for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9'&-]*", t)}


def added_figures(text: str, sources: list[str]) -> list[str]:
    """Figures in text that no source contains."""
    source_figures = {f.replace(",", "") for t in sources for f in _FIGURE.findall(t)}
    return [f for f in _FIGURE.findall(text) if f.replace(",", "") not in source_figures]


def rationale_additions(rationale: str, sources: list[str], vocabulary: list[str]) -> list[str]:
    """What a fit rationale adds: figures and plan or price terms absent from its sources (the initiative's
    sentences and the cited claims), and mid-sentence proper nouns absent from the sources and the vocabulary
    (config wording and the company's aliases). Empty if it adds none of these."""
    source_words = _word_set(sources)
    known = source_words | _word_set(vocabulary)
    out = [f"figure {f!r}" for f in added_figures(rationale, sources)]
    for m in _MID_SENTENCE_PROPER.finditer(rationale):
        word = m.group(0).rstrip("'")
        if word.lower() not in known:
            out.append(f"proper noun {word!r}")
    for m in _PLAN_TERM.finditer(rationale):
        if m.group(0).lower() not in source_words:
            out.append(f"plan or price term {m.group(0)!r}")
    tier = plan_tier_phrase(rationale)
    if tier and not any(plan_tier_phrase(s) for s in sources):
        out.append(f"plan tier {tier!r}")
    for amount in find_currency_amounts(rationale):
        out.append(f"price {amount.raw!r}")
    return list(dict.fromkeys(out))


# A benefit sentence says what a capability does, never that it will deliver a result (review-only flag).
_OUTCOME_PROMISE = re.compile(r"\bwill\s+(?:reduce|increase)\b|\bensures?\b|\bguarantees?\b", re.IGNORECASE)


def outcome_promise(text: str) -> str | None:
    m = _OUTCOME_PROMISE.search(text)
    return m.group(0) if m else None


# Wording that implies the company already uses the product (ground rule 7); review-only flag.
_IMPLIES_CUSTOMER = re.compile(
    r"\b(?:your|their|its)\s+(?:existing\s+)?(?:jira|confluence|atlassian)\b|\bcurrently\s+uses?\b|\balready\s+uses?\b"
    r"|\bexisting\s+(?:jira|confluence|atlassian)\b|\b(?:jira|confluence)\s+(?:instance|site|deployment|license)s?\b",
    re.IGNORECASE,
)


def implies_customer(text: str) -> str | None:
    m = _IMPLIES_CUSTOMER.search(text)
    return m.group(0) if m else None


# ---------------------------------------------------------------- Draft: dates and generic criteria

# Timelines are relative only (Day 0-30). Date mentions in drafted text: a month name, a specific day, a quarter
# label, or a four-digit year. Draft drops a criterion for any of these except a year its references contain.
_MONTH = re.compile(
    r"\b(?:january|february|march|april|june|july|august|september|october|november|december)\b"
    r"|(?-i:(?<=[a-z0-9,;:]\s)May\b)|\bmay\s+(?=\d)"  # "May" only mid-sentence or before a number (not the verb)
    r"|\b(?:jan|feb|mar|apr|jun|jul|aug|sept?|oct|nov|dec)(?:\.|\s+(?=\d))",
    re.IGNORECASE,
)
_SPECIFIC_DAY = re.compile(
    r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b|\b\d{1,2}(?:st|nd|rd|th)\b"
    r"|\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
    re.IGNORECASE,
)
_QUARTER = re.compile(r"\bq[1-4]\b|\b(?:first|second|third|fourth|1st|2nd|3rd|4th)\s+(?:fiscal\s+)?quarter\b", re.IGNORECASE)
_YEAR = re.compile(r"(?<!\d)(?:19|20)\d\d(?!\d)")


def date_mentions(text: str) -> list[tuple[str, str]]:
    """(kind, text) for each month name, specific day, quarter label, and four-digit year in text."""
    out = []
    for kind, pattern in (("month", _MONTH), ("day", _SPECIFIC_DAY), ("quarter", _QUARTER), ("year", _YEAR)):
        out += [(kind, m.group(0).strip()) for m in pattern.finditer(text)]
    return out


# Success criteria must name something countable. These phrases are too generic to measure (review flag).
_GENERIC_CRITERION = re.compile(
    r"\b(?:improv\w*|increas\w*|better|enhanc\w*|boost\w*|greater|more)\s+"
    r"(?:\w+\s+)?(?:efficiency|productivity|collaboration|communication|visibility|alignment|agility)\b"
    r"|\bstreamlin\w*\b|\bbest practices?\b",
    re.IGNORECASE,
)


def generic_criterion(text: str) -> str | None:
    m = _GENERIC_CRITERION.search(text)
    return m.group(0) if m else None


# A criterion states what the product does or what is measured; a clause tying it to a company goal overreaches.
_GOAL_CLAUSE = re.compile(r"\b(?:supporting|enabling|driving)\b", re.IGNORECASE)


def goal_clause(text: str) -> str | None:
    m = _GOAL_CLAUSE.search(text)
    return m.group(0) if m else None
