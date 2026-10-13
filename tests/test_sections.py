from datetime import date

import pytest

from success_plan.stages.retrieve.sections import (
    SectionError, cover_fiscal_year_end, html_to_text, split_sections,
)


def _paras(label: str, n_words: int) -> str:
    sentence = f"{label} body sentence with several ordinary words in it."
    count = n_words // len(sentence.split()) + 1
    per_para = 20
    return "".join(f"<p>{' '.join([sentence] * per_para)}</p>" for _ in range(count // per_para + 1))


TOC = """
<table>
<tr><td>Item 1.</td><td>Business</td><td>4</td></tr>
<tr><td>Item 1A.</td><td>Risk Factors</td><td>15</td></tr>
<tr><td>Item 1B.</td><td>Unresolved Staff Comments</td><td>30</td></tr>
<tr><td>Item 7.</td><td>Management&#8217;s Discussion and Analysis</td><td>40</td></tr>
<tr><td>Item 7A.</td><td>Quantitative and Qualitative Disclosures</td><td>60</td></tr>
</table>
"""


def make_10k(business_words=2000, risk_words=4000, mdna_words=4000, two_line_item7=False) -> str:
    item7 = (
        "<div>ITEM 7.</div><div>MANAGEMENT&#8217;S DISCUSSION AND ANALYSIS</div>"
        if two_line_item7
        else "<div><b>Item 7. Management&#8217;s Discussion and Analysis</b></div>"
    )
    return f"""<html><body>
    <div style="display:none"><ix:header>hidden xbrl facts</ix:header></div>
    <p>FORM 10-K</p><p>For the fiscal year ended December&#160;31, 2025</p>
    {TOC}
    <div>Item 1. Business</div>{_paras("Business", business_words)}
    <p>See Item 7 for a discussion of results.</p>
    <div>Item 1A. Risk Factors</div>{_paras("Risk", risk_words)}
    <div>Item 1B. Unresolved Staff Comments</div><p>None.</p>
    <p>34</p><p>Table of Contents</p>
    {item7}{_paras("MDNA", mdna_words)}
    <div>Item 7A. Quantitative and Qualitative Disclosures About Market Risk</div><p>Rates.</p>
    </body></html>"""


def test_html_to_text_drops_hidden_xbrl_and_page_furniture():
    text = html_to_text(make_10k())
    assert "hidden xbrl facts" not in text
    assert "\n34\n" not in text
    assert "Item 1. | Business | 4" in text  # table rows flattened with separators


def test_cover_fiscal_year_end():
    assert cover_fiscal_year_end(html_to_text(make_10k())) == date(2025, 12, 31)


def test_cover_fiscal_year_missing():
    with pytest.raises(SectionError, match="fiscal year ended"):
        cover_fiscal_year_end("FORM 10-K\nAnnual report")


def test_split_picks_body_not_toc():
    sections = split_sections(html_to_text(make_10k()))
    assert set(sections) == {"1", "1a", "7"}
    assert sections["1"].text.startswith("Business body sentence")
    assert sections["1"].text.rstrip().endswith("See Item 7 for a discussion of results.")
    assert sections["1a"].text.startswith("Risk body sentence")
    assert sections["7"].text.startswith("MDNA body sentence")
    assert "Rates." not in sections["7"].text
    assert sections["1a"].word_count >= 3000


def test_two_line_heading():
    sections = split_sections(html_to_text(make_10k(two_line_item7=True)))
    assert sections["7"].text.startswith("MDNA body sentence")


def test_part_prefixed_heading_and_cross_reference_line():
    cross_ref = (
        "<p>Part I, Item 1A, Risk Factors, under the caption &#8220;System interruption&#8221; describes how "
        "outages could affect bookings and why we invest in redundant systems across regions.</p>"
    )
    html = make_10k().replace(
        "<div>Item 1A. Risk Factors</div>", cross_ref + "<div>Part I. Item 1A. Risk Factors</div>"
    )
    sections = split_sections(html_to_text(html))
    assert sections["1a"].text.startswith("Risk body sentence")
    assert "under the caption" in sections["1"].text  # cross-reference stays inside Item 1, not a boundary


def test_short_section_fails_loudly():
    with pytest.raises(SectionError, match="Item 1A. Risk Factors: found only"):
        split_sections(html_to_text(make_10k(risk_words=200)))


def test_incorporated_by_reference_detected():
    html = make_10k(mdna_words=0).replace(
        "<div><b>Item 7. Management&#8217;s Discussion and Analysis</b></div>",
        "<div><b>Item 7. Management&#8217;s Discussion and Analysis</b></div>"
        "<p>The information is incorporated herein by reference to the Annual Report.</p>",
    )
    with pytest.raises(SectionError, match="incorporated by reference"):
        split_sections(html_to_text(html))
