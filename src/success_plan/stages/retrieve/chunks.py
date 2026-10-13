"""Split source text into numbered chunks that later stages cite by ID."""

import re
from dataclasses import asdict, dataclass

MIN_WORDS = 150
MAX_WORDS = 300


@dataclass(frozen=True)
class SourceMeta:
    source_id: str
    source_type: str  # schema citation enum: filing | earnings_call | news | company_site | product_docs
    title: str
    url: str
    date: str  # ISO filing or publication date
    location: str | None = None  # e.g. "Item 7 MD&A"
    event_date: str | None = None  # 8-Ks only: EDGAR report date (earliest event reported)
    signal: str = "primary"  # "primary" | "weak" (GDELT headlines)


@dataclass(frozen=True)
class Chunk:
    id: str
    source_id: str
    text: str
    word_count: int

    def record(self, meta: SourceMeta) -> dict:
        return {**asdict(meta), "chunk_id": self.id, "text": self.text, "word_count": self.word_count}


def _split_long(paragraph: str) -> list[str]:
    """Break a paragraph over MAX_WORDS at sentence boundaries (or hard-wrap if it has none)."""
    sentences = re.split(r"(?<=[.!?])\s+", paragraph)
    pieces, current = [], []
    for sentence in sentences:
        words = sentence.split()
        while len(words) > MAX_WORDS:
            if current:
                pieces.append(" ".join(current))
                current = []
            pieces.append(" ".join(words[:MAX_WORDS]))
            words = words[MAX_WORDS:]
        if current and len(current) + len(words) > MAX_WORDS:
            pieces.append(" ".join(current))
            current = []
        current.extend(words)
    if current:
        pieces.append(" ".join(current))
    return pieces


def chunk_text(text: str, id_prefix: str, source_id: str) -> list[Chunk]:
    """Group consecutive paragraphs into chunks of about MIN_WORDS to MAX_WORDS words."""
    paragraphs: list[str] = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        paragraphs.extend(_split_long(line) if len(line.split()) > MAX_WORDS else [line])

    groups: list[list[str]] = []
    current: list[str] = []
    count = 0
    for para in paragraphs:
        n = len(para.split())
        if current and count + n > MAX_WORDS:
            groups.append(current)
            current, count = [], 0
        current.append(para)
        count += n
        if count >= MIN_WORDS:
            groups.append(current)
            current, count = [], 0
    if current:
        if groups and count < MIN_WORDS // 3 and sum(len(p.split()) for p in groups[-1]) + count <= MAX_WORDS:
            groups[-1].extend(current)  # fold a short tail into the previous chunk
        else:
            groups.append(current)

    chunks = []
    for i, group in enumerate(groups, start=1):
        body = "\n".join(group)
        chunks.append(Chunk(id=f"{id_prefix}-p{i:03d}", source_id=source_id, text=body, word_count=len(body.split())))
    return chunks
