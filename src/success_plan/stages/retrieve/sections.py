"""Convert filing HTML to plain text and split a 10-K into Item 1, Item 1A, and Item 7."""

import re
import warnings
from dataclasses import dataclass
from datetime import date, datetime

from bs4 import BeautifulSoup, NavigableString, XMLParsedAsHTMLWarning

# Inline XBRL filings are XHTML; the HTML parser handles them and tolerates malformed markup.
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

_BLOCK_TAGS = ["p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "section"]

# Every 10-K item heading we use either as a target or as a boundary, with words that must
# follow the item number. The title words stop "Item 7" in running text from matching.
ITEM_TITLES = {
    "1": r"business",
    "1a": r"risk\s+factors",
    "1b": r"unresolved\s+staff\s+comments",
    "1c": r"cybersecurity",
    "2": r"properties",
    "3": r"legal\s+proceedings",
    "4": r"mine\s+safety",
    "5": r"market\s+for",
    "6": r"(\[?\s*reserved|selected)",
    "7": r"management.?s\s+discussion",
    "7a": r"quantitative\s+and\s+qualitative",
    "8": r"financial\s+statements",
    "9": r"changes\s+in\s+and\s+disagreements",
}

TARGET_ITEMS = {
    "1": ("Item 1. Business", 1500),
    "1a": ("Item 1A. Risk Factors", 3000),
    "7": ("Item 7. Management's Discussion and Analysis", 3000),
}

# Body headings are often prefixed with the Part, e.g. "Part I. Item 1A. Risk Factors".
_HEADING = re.compile(
    r"^(?:part\s+[ivx]+\s*[\.,:\-–—|]?\s*)?item\s*(1a|1b|1c|7a|1|2|3|4|5|6|7|8|9)\b[\s\.\:\-–—|]*(.*)$"
)
# A heading's text after the item number is just the title (plus a page number in the table of
# contents). Longer text means a cross-reference sentence, not a heading.
_MAX_TITLE_CHARS = 130
# Cover pages often put the date in its own table cell, which html_to_text joins with " | "
# ("For the fiscal year ended | December 31 , 2025"), so "|" may separate the words of the line and the date.
_COVER_FY = re.compile(
    r"for\s+the\s+(?:fiscal\s+)?year\s+ended[\s:|]*([a-z]+\.?[\s|]+\d{1,2}[\s|]*,?[\s|]*\d{4})", re.IGNORECASE
)
_INCORPORATED = re.compile(r"incorporated\s+(herein\s+)?by\s+reference", re.IGNORECASE)


class SectionError(Exception):
    pass


@dataclass
class Section:
    item: str
    title: str
    text: str

    @property
    def word_count(self) -> int:
        return len(self.text.split())


def html_to_text(html: str | bytes) -> str:
    """Plain text with one block (paragraph, table row, list item) per line."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all(["script", "style", "ix:header"]):
        tag.decompose()
    for tag in soup.find_all(style=re.compile(r"display\s*:\s*none", re.IGNORECASE)):
        tag.decompose()

    for table in soup.find_all("table"):
        rows = []
        for tr in table.find_all("tr"):
            cells = [_clean(td.get_text(" ")) for td in tr.find_all(["td", "th"])]
            cells = [c for c in cells if c]
            if cells:
                rows.append(" | ".join(cells))
        table.replace_with(NavigableString("\n" + "\n".join(rows) + "\n"))

    for tag in soup.find_all(_BLOCK_TAGS):
        tag.insert_before("\n")
        tag.insert_after("\n")

    lines = []
    for raw in soup.get_text().split("\n"):
        line = _clean(raw)
        if not line or _is_page_furniture(line):
            continue
        lines.append(line)
    return "\n".join(lines)


def _clean(text: str) -> str:
    text = text.replace("\xa0", " ").replace("​", "")
    return re.sub(r"\s+", " ", text).strip()


def _is_page_furniture(line: str) -> bool:
    """Page numbers and repeated 'Table of Contents' back-links."""
    return bool(re.fullmatch(r"(page\s+)?\d{1,3}|table of contents|index", line, re.IGNORECASE))


def _norm(line: str) -> str:
    return line.lower().replace("’", "'").replace("‘", "'")


def cover_fiscal_year_end(text: str) -> date:
    """The 'For the fiscal year ended <date>' line on the 10-K cover page."""
    match = _COVER_FY.search(text[:20000])
    if not match:
        raise SectionError("Could not find 'For the fiscal year ended ...' on the 10-K cover page.")
    raw = re.sub(r"[\s|]+", " ", match.group(1).replace(".", "")).replace(" ,", ",")
    for fmt in ("%B %d, %Y", "%B %d %Y", "%b %d, %Y", "%b %d %Y"):
        try:
            return datetime.strptime(raw.title(), fmt).date()
        except ValueError:
            continue
    raise SectionError(f"Could not parse the cover-page fiscal year end {match.group(1)!r}.")


def find_headings(lines: list[str]) -> list[tuple[int, str]]:
    """(line index, item) for every line that looks like a 10-K item heading."""
    headings = []
    for i, line in enumerate(lines):
        norm = _norm(line)
        if len(norm) > 200:
            continue
        match = _HEADING.match(norm)
        if not match:
            continue
        item, rest = match.group(1), match.group(2).strip(" .|")
        if not rest and i + 1 < len(lines):
            rest = _norm(lines[i + 1])  # heading split across two lines: "Item 7." / "Management's..."
        if re.match(ITEM_TITLES[item], rest) and len(rest) <= _MAX_TITLE_CHARS:
            headings.append((i, item))
    return headings


def split_sections(text: str) -> dict[str, Section]:
    """Find the body (not the table of contents) of each target item.

    Each item heading appears at least twice: once in the table of contents and once in the
    body. For every occurrence we take the span up to the next item heading, and keep the
    longest span. Table-of-contents spans are a few words long, so the body wins.
    """
    lines = text.split("\n")
    headings = find_headings(lines)
    sections = {}
    for item, (title, min_words) in TARGET_ITEMS.items():
        best: list[str] = []
        for pos, (line_idx, found_item) in enumerate(headings):
            if found_item != item:
                continue
            end = headings[pos + 1][0] if pos + 1 < len(headings) else len(lines)
            start = line_idx + 1
            if start < len(lines) and not _HEADING.match(_norm(lines[line_idx]))[2].strip(" .|"):
                start += 1  # skip the title line of a two-line heading
            body = lines[start:end]
            if sum(len(l.split()) for l in body) > sum(len(l.split()) for l in best):
                best = body
        section = Section(item=item, title=title, text="\n".join(best))
        if section.word_count < min_words:
            if _INCORPORATED.search(section.text):
                raise SectionError(
                    f"{title} appears to be incorporated by reference from another document "
                    f"({section.word_count} words found). Not supported in v1."
                )
            raise SectionError(
                f"{title}: found only {section.word_count} words (minimum {min_words}). "
                "The splitter may have matched the table of contents or missed the heading."
            )
        sections[item] = section
    return sections
