"""Render one run's digest as a telegra.ph Node array.

A second renderer rather than a conversion of render.py's HTML: Telegraph
allows only ``a aside b blockquote br code em figcaption figure h3 h4 hr i
iframe img li ol p pre s strong u ul video`` — no div, table, style, details,
h1 or h2 — and the dashboard is built almost entirely from the forbidden set.

Two rules this module lives by:

* **Text is plain text.** Telegraph escapes on render, so nothing here may go
  through html.escape / render._esc: that would publish a literal ``&amp;``.
* **No full job descriptions.** They were free in the ZIP behind ``<details>``,
  which Telegraph forbids; inline they are walls of text and the only realistic
  threat to the 64 KB content cap. The posting link carries them instead.
"""
import json
import secrets

from ..models import REGION_LABELS, Region, coerce_job
# Reused verbatim so the two renderers cannot disagree about what a fact chip
# says or which URLs are safe to link.
from .render import _FACT_LABELS, _is_http
from .sections import group_entries

# Telegraph's hard cap is 64 KB; the margin absorbs the JSON the API wraps the
# content in and leaves room for the "… and N more" lines a trim adds.
CONTENT_LIMIT_BYTES = 60 * 1024

# How many review / deferred entries survive successive trims. The last entry is
# 0: fits are never trimmed, because they are the payload.
_TRIM_LADDER = (10, 3, 0)


def content_size(nodes) -> int:
    """Bytes the node array serializes to, which is what Telegraph measures."""
    return len(json.dumps(nodes, ensure_ascii=False).encode("utf-8"))


def digest_page_title(date, token=None) -> str:
    """``Job Digest 2026-08-01 k3f9d2x1``.

    Telegraph derives the public path from the title, and pages are readable by
    anyone who knows the path — so the title carries 8 random hex characters
    that make it unguessable. The title is kept short on purpose: Telegraph
    truncates long slugs, and a truncation that dropped the token would hand
    back the guessable URL the token exists to prevent.
    """
    iso = date.isoformat() if hasattr(date, "isoformat") else str(date)
    return "Job Digest {} {}".format(iso, token or secrets.token_hex(4))


# ── node helpers ──────────────────────────────────────────────────────────────
def _node(tag, children=None, **attrs):
    node = {"tag": tag}
    if attrs:
        node["attrs"] = attrs
    if children:
        node["children"] = list(children)
    return node


def _link(url, children):
    """A link when the URL is safe to link, the bare children when it is not."""
    return _node("a", children, href=url) if _is_http(url) else list(children)[0]


def _text(value) -> str:
    return str(value if value is not None else "")


# ── job rendering ─────────────────────────────────────────────────────────────
def _meta_line(job) -> str:
    bits = [job.company, job.location]
    if job.is_remote:
        bits.append("Remote")
    region = job.region if isinstance(job.region, Region) else None
    if region is not None and region != Region.UNKNOWN:
        bits.append(REGION_LABELS.get(region, region.value))
    return " · ".join(bit for bit in bits if bit)


def _fact_labels(evaluation, job) -> str:
    facts = (evaluation or {}).get("facts") or {}
    labels = []
    for field_name, mapping in _FACT_LABELS.items():
        label = mapping.get(str(facts.get(field_name, "")).lower())
        if label:
            labels.append(label)
    labels.extend(_text(skill) for skill in list(job.matched_skills or [])[:4])
    return " · ".join(labels)


def _reason(evaluation) -> str:
    evaluation = evaluation or {}
    reason = _text(evaluation.get("reason")).strip() or "Previously matched."
    note = _text(evaluation.get("timezone_note")).strip()
    return "{} ⚠ {}".format(reason, note) if note else reason


def _job_node(entry, *, cv_note=False):
    """One job as a single ``p``, lines separated by ``br``."""
    job = coerce_job(entry.job)
    children = [_node("b", [_link(job.url, [job.title or "Untitled role"])])]

    def line(*parts):
        children.append(_node("br"))
        children.extend(parts)

    meta = _meta_line(job)
    if meta:
        line(_node("i", [meta]))
    summary = _text(getattr(entry, "summary", "")).strip()
    if summary:
        line(summary)
    line(_node("em", ["Why: " + _reason(getattr(entry, "evaluation", None))]))
    facts = _fact_labels(getattr(entry, "evaluation", None), job)
    if facts:
        line(_node("i", [facts]))
    if cv_note:
        line(_node("i", ["CV sent as a document below"]))
    return _node("p", children)


def _deferred_node(deferred):
    items = []
    for entry in deferred:
        job = coerce_job(entry.job)
        label = job.title or "Untitled role"
        if job.company:
            label = "{} — {}".format(label, job.company)
        items.append(_node("li", [_link(job.url, [label])]))
    return _node("ul", items)


# ── list sections ─────────────────────────────────────────────────────────────
def _list_nodes(ctx, list_name, heading, entries, warnings, *, cv_note, total=None):
    """Cards for one list, headed by its true count even when trimmed empty.

    ``total`` is what the heading reports; it defaults to ``len(entries)`` but
    a caller that trimmed ``entries`` for size passes the pre-trim count, so
    the heading agrees with the top counts line instead of reporting however
    many cards survived the trim. When ``total`` is nonzero but ``entries`` is
    empty (trimmed all the way down), the heading still renders alone — the
    "... and N more" line that follows needs a heading to sit under.
    """
    if total is None:
        total = len(entries)
    if not total:
        return []
    nodes = [_node("h3", ["{} ({})".format(heading, total)])]
    if not entries:
        return nodes
    groups, group_warnings = group_entries(entries, ctx.sections, list_name)
    warnings.extend(group_warnings)
    if not groups:
        nodes.extend(_job_node(entry, cv_note=cv_note) for entry in entries)
        return nodes
    for section, bucket in groups:
        label = " ".join(
            part for part in (_text(section.icon).strip(), _text(section.name)) if part
        )
        nodes.append(_node("h4", ["{} ({})".format(label, len(bucket))]))
        nodes.extend(_job_node(entry, cv_note=cv_note) for entry in bucket)
    return nodes


def _warning_nodes(ctx, warnings):
    nodes = []
    source = _text(ctx.source_warning).strip()
    if source:
        nodes.append(_node("blockquote", ["⚠️ Source health: " + source]))
    # Deduplicated: a section applying to both lists is grouped twice, and
    # group_entries dedupes only within one call.
    messages = []
    for message in [_text(ctx.sections_error).strip()] + list(warnings):
        if message and message not in messages:
            messages.append(message)
    if messages:
        nodes.append(_node("blockquote", ["⚠️ Sections: " + " · ".join(messages)]))
    return nodes


def _counts_line(ctx) -> str:
    return " · ".join([
        "{} new".format(int(getattr(ctx.stats, "new_jobs", 0) or 0)),
        "{} evaluated".format(int(getattr(ctx.stats, "evaluated", 0) or 0)),
        "{} fits".format(len(ctx.fits)),
        "{} to review".format(len(ctx.review)),
        "{} deferred".format(len(ctx.deferred)),
    ])


def _render(ctx, index_url, keep_review, keep_deferred):
    review = list(ctx.review)
    deferred = list(ctx.deferred)
    dropped_review = dropped_deferred = 0
    if keep_review is not None and len(review) > keep_review:
        dropped_review = len(review) - keep_review
        review = review[:keep_review]
    if keep_deferred is not None and len(deferred) > keep_deferred:
        dropped_deferred = len(deferred) - keep_deferred
        deferred = deferred[:keep_deferred]

    # Body first: grouping is what discovers a predicate that raises, and that
    # warning belongs above the body it happened in.
    warnings = []
    body = _list_nodes(ctx, "fits", "✅ Fits", ctx.fits, warnings, cv_note=True)
    body.extend(_list_nodes(
        ctx, "review", "🔍 Needs review", review, warnings, cv_note=False, total=len(ctx.review)
    ))
    if dropped_review:
        body.append(_node("p", [_node("i", ["… and {} more to review".format(dropped_review)])]))
    # Headed by the true total (ctx.deferred), not the possibly-trimmed
    # `deferred` list, so a trim to zero still leaves the "... and N more"
    # line under a heading instead of orphaned. The <ul> itself is only
    # emitted when something survived the trim — an empty <ul> is a node
    # shape worth avoiding.
    if ctx.deferred:
        body.append(_node("h3", ["⚠️ Deferred ({})".format(len(ctx.deferred))]))
        if deferred:
            body.append(_deferred_node(deferred))
        body.append(_node("p", [_node("i", [
            "Not enough job-description text for a reliable decision — "
            "these retry automatically next run."
        ])]))
    if dropped_deferred:
        body.append(_node("p", [_node("i", ["… and {} more deferred".format(dropped_deferred)])]))
    if not body:
        body = [_node("p", ["No evaluated jobs matched your criteria this run."])]

    date_str = ctx.date.isoformat() if hasattr(ctx.date, "isoformat") else _text(ctx.date)
    nodes = [
        _node("h3", ["Job Search Digest — " + date_str]),
        _node("p", [_counts_line(ctx)]),
    ]
    nodes.extend(_warning_nodes(ctx, warnings))
    nodes.extend(body)
    nodes.append(_node("hr"))
    footer = [_node("i", [_text(ctx.usage_summary)])]
    if index_url:
        footer.append(_node("br"))
        footer.append(_node("a", ["← All digests"], href=index_url))
    nodes.append(_node("p", footer))
    return nodes


def render_digest_nodes(ctx, index_url="") -> list:
    """The run as Telegraph nodes, trimmed to fit if it somehow runs long.

    Trimming drops review and deferred entries only. Fits are never trimmed —
    they are the payload — so a run whose fits alone exceed the budget comes
    back over-size on purpose: create_page will reject it and the caller falls
    back to the ZIP, which has no such limit.
    """
    nodes = _render(ctx, index_url, None, None)
    if content_size(nodes) <= CONTENT_LIMIT_BYTES:
        return nodes
    for keep in _TRIM_LADDER:
        nodes = _render(ctx, index_url, keep, keep)
        if content_size(nodes) <= CONTENT_LIMIT_BYTES:
            return nodes
    return nodes
