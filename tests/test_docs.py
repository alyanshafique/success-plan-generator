"""Committed documentation must not contain unfilled placeholders."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCS = sorted((ROOT / "docs").rglob("*.md")) + [ROOT / "README.md"]

# A "[" followed by placeholder instruction text, e.g. "[I-08: write the status here ...]", "[fill in]", "[TODO".
PLACEHOLDER = re.compile(r"\[[^\]\n]{0,80}?(write the status here|fill in|TODO)", re.IGNORECASE)


def find_placeholders(text: str) -> list[str]:
    return [m.group(0) for m in PLACEHOLDER.finditer(text)]


@pytest.mark.parametrize("path", DOCS, ids=lambda p: str(p.relative_to(ROOT)))
def test_no_unfilled_placeholders(path):
    lines = [f"line {n}: {line.strip()}" for n, line in enumerate(path.read_text().splitlines(), 1) if find_placeholders(line)]
    assert not lines, f"Unfilled placeholder text in {path.relative_to(ROOT)}:\n" + "\n".join(lines)


@pytest.mark.parametrize("text", [
    "[I-08: write the status here, based on the chunk]",
    "Status: [fill in after review]",
    "[TODO: add the date]",
    "[todo]",
])
def test_pattern_catches_placeholders(text):
    assert find_placeholders(text)


@pytest.mark.parametrize("text", [
    "[corrected in the next entry: mis-cited, not overreach in meaning]",  # real bracketed note in the log
    "Owners fill in the renewal date later.",  # 'fill in' without a bracket
    "See the [README](README.md).",
])
def test_pattern_ignores_ordinary_text(text):
    assert not find_placeholders(text)
