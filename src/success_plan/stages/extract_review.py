"""Review sheet for Extract output: each item with provenance and flags, and each citation's evidence
phrase next to the full source chunk.

The sheet contains full source text, so it is written to the gitignored raw folder and never committed.
"""

import html
import re
from pathlib import Path

from .. import atomic
from ..models import ExtractItem, ExtractOutput, GuardReport

_CSS = """
body { font: 14px/1.45 system-ui, sans-serif; margin: 24px; color: #1a1a1a; }
h1 { font-size: 20px; } h2 { font-size: 17px; margin-top: 32px; border-bottom: 1px solid #ccc; }
.item { border: 1px solid #ddd; border-radius: 6px; padding: 12px 14px; margin: 16px 0; }
.meta { color: #555; font-size: 13px; }
.tag { display: inline-block; padding: 1px 7px; border-radius: 10px; font-size: 12px; margin-right: 4px; }
.sourced { background: #d9f2df; } .inferred { background: #fde8c8; } .label { background: #e6e6f5; }
.flag { background: #f8d4d4; }
table { border-collapse: collapse; width: 100%; margin-top: 8px; table-layout: fixed; }
td, th { border: 1px solid #ddd; padding: 6px 8px; vertical-align: top; text-align: left; }
th { background: #f5f5f5; font-weight: 600; } td.evidence { width: 30%; } td.chunk { white-space: pre-wrap; font-size: 13px; }
tr.rejected td { background: #fff4f4; }
mark { background: #fff2a8; }
.sentence { margin: 10px 0 10px 12px; } .sentence.dropped { color: #8a1f1f; }
"""


def _highlight(chunk_text: str, evidence: str) -> str:
    """Escape the chunk and mark the evidence where it appears verbatim (best effort, case-insensitive).

    The guard matches after normalizing quotes, dashes, spacing, and table separators, so a verified
    phrase may not be highlighted here even though it passed.
    """
    escaped = html.escape(chunk_text)
    target = html.escape(evidence.strip())
    if not target:
        return escaped
    pattern = re.compile(re.escape(target), re.IGNORECASE)
    return pattern.sub(lambda m: f"<mark>{m.group(0)}</mark>", escaped, count=1)


def _verified_row(c, chunks: dict[str, dict]) -> str:
    e = html.escape
    chunk = chunks.get(c.chunk_id, {})
    when = f"filed {c.date}" + (f", event {c.event_date}" if c.event_date else "")
    return (
        f'<tr><td class="evidence"><strong>verified{" (uncorroborated headline)" if c.uncorroborated else ""}'
        f'{" (stored phrase truncated to 14 words)" if c.evidence_truncated else ""}</strong><br>{e(c.chunk_id)}<br>'
        f'<span class="meta">{e(c.title)}{" · " + e(c.location) if c.location else ""} · {when}</span>'
        f'<br><br>“{e(c.evidence)}”</td><td class="chunk">{_highlight(chunk.get("text", ""), c.evidence)}</td></tr>'
    )


def _rejected_row(r, chunks: dict[str, dict]) -> str:
    e = html.escape
    chunk = chunks.get(r.chunk_id)
    body = _highlight(chunk["text"], r.evidence) if chunk else "<em>no such chunk</em>"
    return (
        f'<tr class="rejected"><td class="evidence"><strong>rejected:</strong> {e(r.reason)}<br>{e(r.chunk_id)}'
        f'<br><br>“{e(r.evidence)}”</td><td class="chunk">{body}</td></tr>'
    )


_TABLE_HEAD = "<table><tr><th>Evidence phrase</th><th>Full source chunk</th></tr>"


def _sentences_html(item: ExtractItem, chunks: dict[str, dict]) -> list[str]:
    """Sentence format: each kept sentence with its own citations, then dropped sentences with reasons."""
    e = html.escape
    parts = []
    for n, sr in enumerate(item.sentences, start=1):
        score = "not scored" if sr.support_score is None else f"support {sr.support_score:.0%} against its own citations"
        rows = [_verified_row(c, chunks) for c in sr.citations] + [_rejected_row(r, chunks) for r in sr.rejected_citations]
        parts.append(f'<div class="sentence"><p><strong>Sentence {n}</strong> <span class="meta">({score})</span><br>{e(sr.text)}</p>'
                     + _TABLE_HEAD + "".join(rows) + "</table></div>")
    if item.dropped_sentences:
        parts.append(f"<p><strong>Dropped sentences ({len(item.dropped_sentences)})</strong> — not part of the item text</p>")
        for d in item.dropped_sentences:
            rows = "".join(_rejected_row(r, chunks) for r in d.rejected_citations)
            parts.append(f'<div class="sentence dropped"><p><s>{e(d.text)}</s><br><span class="meta">Reason: {e(d.reason)}</span></p>'
                         + (_TABLE_HEAD + rows + "</table>" if rows else "") + "</div>")
    return parts


def _item_html(item: ExtractItem, chunks: dict[str, dict]) -> str:
    e = html.escape
    tags = [f'<span class="tag {item.provenance}">{item.provenance}</span>']
    if item.legacy:
        tags.append('<span class="tag flag">LEGACY format</span>')
    tags += [f'<span class="tag label">{e(l)}</span>' for l in item.labels]
    tags += [f'<span class="tag flag">{e(f.type)}</span>' for f in item.flags]
    detail = item.theme or item.category or item.change_type or ""
    meta = [e(detail)]
    if item.origin:
        meta.append(f"origin: {e(item.origin)}")
    if item.event_date:
        meta.append(f"event date: {item.event_date}")
    if item.title_support_score is not None:
        meta.append(f"title support {item.title_support_score:.0%} against the kept sentences")
    parts = [
        f'<div class="item" id="{e(item.id)}"><strong>{e(item.id)} {e(item.title)}</strong> {"".join(tags)}',
        f'<div class="meta">{" · ".join(meta)}</div>',
        f"<p>{e(item.text)}</p>",
        f'<p class="meta">Validation question: {e(item.validation_question)}</p>',
    ]
    parts += [f'<p class="meta">Validation question (added by code): {e(q)}</p>' for q in item.extra_validation_questions]
    if item.sources_note:
        parts.append(f'<p class="meta">{e(item.sources_note)}</p>')
    for f in item.flags:
        parts.append(f'<p class="meta">Flag {e(f.type)}: {e(f.detail)}</p>')
    if item.sentences:
        parts.extend(_sentences_html(item, chunks))
    else:  # legacy item-level citations
        rows = [_verified_row(c, chunks) for c in item.citations] + [_rejected_row(r, chunks) for r in item.rejected_citations]
        parts.append(_TABLE_HEAD + "".join(rows) + "</table>" if rows else '<p class="meta">No citations.</p>')
    parts.append("</div>")
    return "\n".join(parts)


def write_review_sheet(path: Path, output: ExtractOutput, report: GuardReport, chunks: dict[str, dict]) -> None:
    e = html.escape
    title = f"Extract review: {output.company}"
    head = [
        f"<h1>{e(title)}</h1>",
        f'<p class="meta">Profile {e(output.profile)} · generated {output.generated_at.isoformat()}'
        + (f" · replayed from {e(output.replayed_from)} (no API call)" if output.replayed_from else "")
        + f" · {len(output.initiatives)} initiatives, {len(output.challenges)} challenges, {len(output.changes)} changes"
        + (" · LEGACY response format" if output.response_format == "legacy" else "") + "</p>",
        '<p class="meta">Contains full source text. Gitignored; do not commit or share outside the project.</p>',
    ]
    body = []
    for heading, items in (("Initiatives", output.initiatives), ("Challenges", output.challenges), ("Changes", output.changes)):
        body.append(f"<h2>{heading} ({len(items)})</h2>")
        body.extend(_item_html(i, chunks) for i in items)
    for heading, entries in (("Excluded by rule", report.excluded), ("Overflow (over the cap)", report.overflow),
                             ("All dropped sentences (including those of excluded items)", report.dropped_sentences)):
        body.append(f"<h2>{heading} ({len(entries)})</h2>")
        if entries:
            body.append("<ul>" + "".join(f"<li>{e(x.kind)}: {e(x.title)} — {e(x.reason)}</li>" for x in entries) + "</ul>")
        else:
            body.append('<p class="meta">None.</p>')
    doc = (
        f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{e(title)}</title>'
        f"<style>{_CSS}</style></head><body>" + "\n".join(head + body) + "</body></html>\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic.write_text(path, doc)
