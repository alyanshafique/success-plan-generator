"""Review sheet for Map output (gitignored: shows initiative text and config claims side by side).

For each key benefit: the initiative's sentences, the fit and the fit rationale (an inference), each benefit
sentence with its claim citations (evidence next to the full claim text), dropped sentences with reasons, flags,
labels, and the plan blank. Then the claims withheld from the model, exclusions, downgrades, and unmapped
initiatives.
"""

import html
from pathlib import Path

from .. import atomic
from ..models import ExtractOutput, KeyBenefit, MapGuardReport, MapOutput
from .extract_review import _CSS, _highlight

_EXTRA_CSS = ".fit { font-weight: 600; } .rationale { background: #f4f4fb; padding: 6px 8px; border-radius: 4px; }"


def _benefit_html(b: KeyBenefit, extract: ExtractOutput, claims: dict[str, dict]) -> str:
    e = html.escape
    initiative = next((i for i in extract.initiatives if i.id == b.initiative_id), None)
    tags = [f'<span class="tag inferred">{e(b.provenance)}</span>', f'<span class="tag label">fit: {e(b.fit)}</span>']
    tags += [f'<span class="tag label">{e(l)}</span>' for l in b.labels]
    tags += [f'<span class="tag flag">{e(f.type)}</span>' for f in b.flags]
    parts = [f'<div class="item" id="{e(b.id)}"><strong>{e(b.id)} → {e(b.initiative_id)} {e(b.initiative_title)}</strong> {"".join(tags)}']
    if initiative:
        parts.append(f'<p class="meta">Initiative ({e(initiative.provenance)}):</p><ul>'
                     + "".join(f"<li>{e(s.text)}</li>" for s in initiative.sentences) + "</ul>")
    caps = ", ".join(b.capability_ids) or "none"
    parts.append(f'<p><span class="fit">Fit: {e(b.fit)}</span> · capabilities: {e(caps)}</p>')
    if b.fit == "no_fit":
        parts.append(f'<p class="meta">No-fit reason: {e(b.no_fit_reason or "")}{" · gaps: " + e(", ".join(b.gap_ids)) if b.gap_ids else ""}</p>')
    support = ("not scored" if b.fit_rationale_support is None
               else f"word overlap {b.fit_rationale_support:.0%} against the initiative and cited claims; review only, not a flag")
    parts.append(f'<p class="rationale"><strong>Fit rationale (inference)</strong> <span class="meta">({support})</span><br>{e(b.fit_rationale)}</p>')
    for n, s in enumerate(b.sentences, start=1):
        score = "not scored" if s.support_score is None else f"support {s.support_score:.0%} against its own claims"
        rows = []
        for c in s.citations:
            claim = claims.get(c.claim_id, {})
            urls = "<br>".join(f'<a href="{e(u)}">{e(u)}</a>' for u in c.source_urls)
            rows.append(
                f'<tr><td class="evidence"><strong>verified{" (unconfirmed claim)" if c.unconfirmed else ""}'
                f'{" (stored phrase truncated to 14 words)" if c.evidence_truncated else ""}</strong><br>{e(c.claim_id)} · {e(c.capability_name)}'
                f'<br><span class="meta">verified {c.verified_on or "unknown"}{" by " + e(c.verified_by) if c.verified_by else ""}</span>'
                f'<br><br>“{e(c.evidence)}”<br><br><span class="meta">{urls}</span></td>'
                f'<td class="chunk">{_highlight(claim.get("text", ""), c.evidence)}</td></tr>'
            )
        for r in s.rejected_claims:
            rows.append(f'<tr class="rejected"><td class="evidence"><strong>rejected:</strong> {e(r.reason)}<br>{e(r.chunk_id)}'
                        f'<br><br>“{e(r.evidence)}”</td><td class="chunk">{e(claims.get(r.chunk_id, {}).get("text", "(no such claim)"))}</td></tr>')
        parts.append(f'<div class="sentence"><p><strong>Sentence {n}</strong> <span class="meta">({score})</span><br>{e(s.text)}</p>'
                     '<table><tr><th>Evidence phrase</th><th>Full config claim</th></tr>' + "".join(rows) + "</table></div>")
    if b.dropped_sentences:
        parts.append(f"<p><strong>Dropped sentences ({len(b.dropped_sentences)})</strong> — not part of the benefit text</p>")
        for d in b.dropped_sentences:
            parts.append(f'<div class="sentence dropped"><p><s>{e(d.text)}</s><br><span class="meta">Reason: {e(d.reason)}</span></p></div>')
    for f in b.flags:
        parts.append(f'<p class="meta">Flag {e(f.type)}: {e(f.detail)}</p>')
    parts.append(f'<p class="meta">Validation question: {e(b.validation_question)}</p>')
    parts.append(f'<p class="meta">{e(b.plan_confirmation)}</p></div>')
    return "\n".join(parts)


def write_map_review_sheet(path: Path, output: MapOutput, report: MapGuardReport, extract: ExtractOutput,
                           claims: dict[str, dict]) -> None:
    e = html.escape
    title = f"Map review: {output.company}"
    head = [
        f"<h1>{e(title)}</h1>",
        f'<p class="meta">Profile {e(output.profile)} · generated {output.generated_at.isoformat()}'
        + (f" · replayed from {e(output.replayed_from)} (no API call)" if output.replayed_from else "")
        + f" · fit: {e(str(output.fit_distribution))}</p>",
        '<p class="meta">Verification chain: benefit sentence → config claim (checked here, against the config\'s wording) '
        '→ Atlassian page (the source URLs, checked when the config was verified). Gitignored; do not commit.</p>',
    ]
    body = [f"<h2>Key benefits ({len(output.key_benefits)})</h2>"] + [_benefit_html(b, extract, claims) for b in output.key_benefits]
    body.append(f"<h2>Claims withheld from the model ({len(output.withheld_claims)})</h2><ul>"
                + "".join(f"<li>{e(w.capability_id)}: {e(w.text)} — {e(w.reason)}</li>" for w in output.withheld_claims) + "</ul>")
    for heading, entries in (("Excluded mappings", report.excluded), ("Downgraded to no_fit", report.downgraded),
                             ("All dropped sentences", report.dropped_sentences)):
        body.append(f"<h2>{heading} ({len(entries)})</h2>")
        body.append("<ul>" + "".join(f"<li>{e(x.item_id or '')} {e(x.title)} — {e(x.reason)}</li>" for x in entries) + "</ul>"
                    if entries else '<p class="meta">None.</p>')
    body.append(f"<h2>Unmapped initiatives ({len(report.unmapped_initiatives)})</h2>"
                + (f"<p>{e(', '.join(report.unmapped_initiatives))}</p>" if report.unmapped_initiatives else '<p class="meta">None.</p>'))
    doc = (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{e(title)}</title>'
           f"<style>{_CSS}{_EXTRA_CSS}</style></head><body>" + "\n".join(head + body) + "</body></html>\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic.write_text(path, doc)
