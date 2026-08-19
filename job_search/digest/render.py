"""Render one run's digest as a self-contained, offline HTML dashboard.

No external assets (the page is opened locally from an extracted ZIP), so all
CSS is inlined and the page is theme-aware via ``prefers-color-scheme``. Every
interpolated value is HTML-escaped — postings are untrusted text.

The layout is a stack of cards (one per job), not a wide table: the digest is
often read on a phone, where a multi-column table forces horizontal scrolling.
Each fit card leads with the LLM one-line summary, then the "why it fits"
reason and key-fact chips, and ends with a prominent "Download CV" button and a
link to the original posting.
"""
import html

from ..models import REGION_LABELS, Region, coerce_job
from .sections import group_entries

_STYLE = """
*{box-sizing:border-box}
:root{
  --bg:#f3f4f6; --panel:#ffffff; --panel-2:#f8fafc;
  --ink:#1a1d22; --muted:#5b6472; --faint:#8b94a0;
  --line:#e4e7ec; --line-soft:#eef1f4;
  --accent:#3f5bd8; --accent-ink:#ffffff;
  --fit:#0f8f52; --fit-bg:#e7f4ec; --fit-line:#c3e6d1;
  --review:#b57705; --review-bg:#fbf1d7; --review-line:#ecd9a3;
  --defer:#b0532c; --defer-bg:#f6e9e1; --defer-line:#ecd0bf;
  --chip:#eef1f5; --chip-ink:#39424d;
  --radius:16px;
  --shadow:0 1px 2px rgba(16,22,30,.04),0 3px 8px rgba(16,22,30,.05);
}
@media (prefers-color-scheme:dark){
  :root{
    --bg:#0f1216; --panel:#191d23; --panel-2:#1f242b;
    --ink:#e8ebef; --muted:#98a2ad; --faint:#6d7681;
    --line:#2a3037; --line-soft:#232930;
    --accent:#8098ff; --accent-ink:#0f1216;
    --fit:#57c98c; --fit-bg:#14261d; --fit-line:#25523b;
    --review:#e0b45a; --review-bg:#2a2413; --review-line:#4a3f1e;
    --defer:#e09a72; --defer-bg:#2a1e17; --defer-line:#4a3527;
    --chip:#242a31; --chip-ink:#c2cbd5;
    --shadow:0 1px 2px rgba(0,0,0,.35),0 3px 10px rgba(0,0,0,.3);
  }
}
body{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  -webkit-font-smoothing:antialiased}
.wrap{max-width:860px;margin:0 auto;padding:clamp(18px,4vw,34px) clamp(14px,4vw,24px) 64px}

header.top{margin:0 0 20px}
header.top h1{font-size:clamp(21px,4.6vw,27px);margin:0;letter-spacing:-.02em;font-weight:700}
header.top .sub{color:var(--muted);margin-top:4px;font-size:14px}
header.top .date{font-variant-numeric:tabular-nums;font-weight:600;color:var(--ink)}

.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(92px,1fr));gap:10px;margin:0 0 18px}
.stat{background:var(--panel);border:1px solid var(--line);border-radius:12px;
  padding:11px 14px;box-shadow:var(--shadow)}
.stat b{display:block;font-size:22px;line-height:1.1;font-weight:700;font-variant-numeric:tabular-nums}
.stat span{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.05em;font-weight:600}
.stat.fit b{color:var(--fit)}
.stat.review b{color:var(--review)}
.stat.defer b{color:var(--defer)}

.warn{border-radius:12px;padding:11px 15px;margin:0 0 18px;font-size:13.5px;line-height:1.5;
  background:var(--review-bg);color:var(--review);border:1px solid var(--review-line)}
.warn b{color:inherit}

section{margin:0 0 26px}
.sec-head{display:flex;align-items:baseline;gap:9px;margin:0 0 12px}
.sec-head h2{font-size:15px;margin:0;font-weight:700;letter-spacing:-.01em}
.sec-head .n{color:var(--faint);font-weight:600;font-variant-numeric:tabular-nums}

.sub-head{display:flex;align-items:baseline;gap:8px;margin:18px 0 10px}
.sub-head h3{font-size:12.5px;margin:0;font-weight:700;letter-spacing:.03em;
  text-transform:uppercase;color:var(--muted)}
.sub-head .n{color:var(--faint);font-weight:600;font-variant-numeric:tabular-nums}

.job{background:var(--panel);border:1px solid var(--line);border-left:3px solid var(--line);
  border-radius:var(--radius);box-shadow:var(--shadow);padding:16px 18px;margin:0 0 12px}
.job.fit{border-left-color:var(--fit)}
.job.review{border-left-color:var(--review)}
.job-head{display:flex;justify-content:space-between;align-items:flex-start;gap:10px 14px;flex-wrap:wrap}
.job .title{font-size:16.5px;font-weight:600;letter-spacing:-.01em;margin:0;line-height:1.3}
.job .company{color:var(--muted);font-size:13.5px;margin-top:2px}
.badges{display:flex;flex-wrap:wrap;gap:5px}
.badge{border-radius:7px;padding:2px 8px;font-size:11.5px;font-weight:600;white-space:nowrap}
.badge.remote{background:var(--fit-bg);color:var(--fit)}
.badge.region{background:var(--chip);color:var(--chip-ink)}

.summary{margin:11px 0 0;font-size:15px;line-height:1.5}

.meta{display:grid;grid-template-columns:1fr 1fr;gap:12px 22px;margin-top:13px}
.meta.single{grid-template-columns:1fr}
.meta-label{font-size:10.5px;text-transform:uppercase;letter-spacing:.05em;
  color:var(--faint);font-weight:700;margin-bottom:4px}
.meta-val{font-size:14px;line-height:1.5}
.tz{display:block;margin-top:5px;color:var(--review);font-size:13px}
.chips{display:flex;flex-wrap:wrap;gap:5px}
.chip{background:var(--chip);color:var(--chip-ink);border-radius:999px;
  padding:2px 10px;font-size:12.5px;white-space:nowrap}
.muted{color:var(--faint)}

details.desc{margin-top:13px;border-top:1px solid var(--line-soft);padding-top:11px}
details.desc summary{cursor:pointer;color:var(--accent);font-size:13px;font-weight:600;
  list-style:none;user-select:none;display:inline-flex;align-items:center;gap:6px}
details.desc summary::-webkit-details-marker{display:none}
details.desc summary::before{content:"\\25B8";font-size:11px;transition:transform .15s}
details.desc[open] summary::before{transform:rotate(90deg)}
details.desc .desc-body{margin-top:9px;color:var(--ink);white-space:pre-wrap;
  font-size:13.5px;line-height:1.55;max-height:360px;overflow:auto;
  background:var(--panel-2);border:1px solid var(--line-soft);border-radius:10px;padding:11px 13px}

.actions{display:flex;flex-wrap:wrap;gap:8px;margin-top:15px}
.btn{display:inline-flex;align-items:center;gap:6px;border-radius:9px;padding:8px 14px;
  font-size:13.5px;font-weight:600;white-space:nowrap;border:1px solid transparent;
  transition:filter .15s,background .15s}
.btn.cv{background:var(--accent);color:var(--accent-ink)}
.btn.cv:hover{filter:brightness(1.07);text-decoration:none}
.btn.ghost{background:transparent;color:var(--accent);border-color:var(--line)}
.btn.ghost:hover{background:var(--chip);text-decoration:none}

a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}

ul.deferred{list-style:none;margin:0;padding:0;background:var(--panel);
  border:1px solid var(--line);border-radius:var(--radius);box-shadow:var(--shadow);overflow:hidden}
ul.deferred li{padding:11px 16px;border-bottom:1px solid var(--line-soft);
  display:flex;justify-content:space-between;align-items:baseline;gap:10px 14px;flex-wrap:wrap;font-size:14px}
ul.deferred li:last-child{border-bottom:none}
ul.deferred .company{color:var(--muted);font-weight:400}
.note{color:var(--faint);font-size:12.5px;margin:9px 2px 0}

.empty{color:var(--muted);background:var(--panel);border:1px dashed var(--line);
  border-radius:var(--radius);padding:24px;text-align:center}

footer{color:var(--faint);font-size:12px;margin-top:30px;border-top:1px solid var(--line);
  padding-top:14px;line-height:1.5}

@media (max-width:560px){
  .meta{grid-template-columns:1fr}
  .job-head{flex-direction:column;gap:6px}
  .actions .btn{flex:1 1 auto;justify-content:center}
}
@media print{
  /* Force the light palette so a dark-mode "save as PDF" stays readable
     (otherwise light text lands on the white print background). */
  :root{
    --bg:#ffffff; --panel:#ffffff; --panel-2:#f8fafc;
    --ink:#1a1d22; --muted:#5b6472; --faint:#8b94a0;
    --line:#e4e7ec; --line-soft:#eef1f4;
    --accent:#2b4bd0; --accent-ink:#ffffff;
    --fit:#0f8f52; --fit-bg:#e7f4ec; --fit-line:#c3e6d1;
    --review:#b57705; --review-bg:#fbf1d7; --review-line:#ecd9a3;
    --defer:#b0532c; --defer-bg:#f6e9e1; --defer-line:#ecd0bf;
    --chip:#eef1f5; --chip-ink:#39424d;
  }
  body{background:#fff;color:var(--ink)}
  .job,.stat,ul.deferred{box-shadow:none}
  /* Outlined, not filled: the accent fill is dropped when "Background
     graphics" is off (default when saving to PDF), which would hide white text. */
  .btn.cv{background:transparent;color:var(--accent);border-color:var(--accent)}
}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
"""

_FACT_LABELS = {
    "seniority": {"senior": "Senior", "mid": "Mid-level", "junior": "Junior"},
    "work_arrangement": {"remote": "Remote", "hybrid": "Hybrid", "onsite": "On-site"},
    "remote_geo_scope": {"worldwide": "Worldwide", "restricted": "Geo-restricted"},
    "employment_type": {"full_time": "Full-time", "part_time": "Part-time",
                        "contract": "Contract", "freelance": "Freelance", "internship": "Internship"},
    "offers_sponsorship": {"yes": "Sponsorship"},
}


def _esc(value) -> str:
    return html.escape(str(value if value is not None else ""))


def _attr(value) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _stat(stats, name) -> int:
    return int(getattr(stats, name, 0) or 0)


def _is_http(url) -> bool:
    u = str(url or "").strip().lower()
    return u.startswith("http://") or u.startswith("https://")


def _posting_link(job, label="View posting") -> str:
    """A plain inline posting link (used in the compact deferred list)."""
    url = str(job.get("url", "") or "").strip()
    if _is_http(url):
        return '<a href="{}" target="_blank" rel="noopener">{}</a>'.format(_attr(url), _esc(label))
    return '<span class="muted">—</span>'


def _posting_button(job, label="View posting ↗") -> str:
    """The posting as a secondary action button (used on job cards)."""
    url = str(job.get("url", "") or "").strip()
    if _is_http(url):
        return '<a class="btn ghost" href="{}" target="_blank" rel="noopener">{}</a>'.format(
            _attr(url), _esc(label))
    return ""


def _title(job) -> str:
    return '<h3 class="title">{}</h3>'.format(_esc(job.get("title", "") or "Untitled role"))


def _company_line(job) -> str:
    sub = " · ".join(p for p in (_esc(job.get("company", "")), _esc(job.get("location", ""))) if p)
    return '<div class="company">{}</div>'.format(sub) if sub else ""


def _badges(job) -> str:
    badges = []
    if job.get("is_remote"):
        badges.append('<span class="badge remote">Remote</span>')
    region = job.get("region")
    region = region if isinstance(region, Region) else None
    if region and region != Region.UNKNOWN:
        badges.append('<span class="badge region">{}</span>'.format(
            _esc(REGION_LABELS.get(region, region.value))))
    return '<div class="badges">{}</div>'.format("".join(badges)) if badges else ""


def _card_head(job) -> str:
    return '<div class="job-head"><div>{}{}</div>{}</div>'.format(
        _title(job), _company_line(job), _badges(job))


def _summary_line(text) -> str:
    return '<p class="summary">{}</p>'.format(_esc(text)) if text else ""


def _meta_block(label, inner) -> str:
    return '<div class="meta-block"><div class="meta-label">{}</div><div class="meta-val">{}</div></div>'.format(
        _esc(label), inner)


def _fact_chips(evaluation, job) -> str:
    facts = (evaluation or {}).get("facts") or {}
    chips = []
    for field_name, mapping in _FACT_LABELS.items():
        label = mapping.get(str(facts.get(field_name, "")).lower())
        if label:
            chips.append('<span class="chip">{}</span>'.format(_esc(label)))
    for skill in list(job.get("matched_skills", []) or [])[:4]:
        chips.append('<span class="chip">{}</span>'.format(_esc(skill)))
    return '<div class="chips">{}</div>'.format("".join(chips)) if chips else '<span class="muted">—</span>'


def _why_cell(evaluation) -> str:
    evaluation = evaluation or {}
    reason = _esc(evaluation.get("reason", "") or "Previously matched.")
    note = evaluation.get("timezone_note")
    tz = '<span class="tz">⚠ {}</span>'.format(_esc(note)) if note else ""
    return '<span class="reason">{}</span>{}'.format(reason, tz)


def _desc_details(job) -> str:
    desc = str(job.get("description", "") or "").strip()
    if not desc:
        return ""
    return (
        '<details class="desc"><summary>Full job description</summary>'
        '<div class="desc-body">{}</div></details>'
    ).format(_esc(desc))


def _fit_card(entry) -> str:
    job = coerce_job(entry.job)
    meta = _meta_block("Why it fits", _why_cell(entry.evaluation)) + \
        _meta_block("Key facts", _fact_chips(entry.evaluation, job))
    cv = '<a class="btn cv" href="cvs/{}">↓ Download CV</a>'.format(_attr(entry.cv_filename))
    return (
        '<article class="job fit">{head}{summary}'
        '<div class="meta">{meta}</div>{desc}'
        '<div class="actions">{cv}{posting}</div></article>'
    ).format(
        head=_card_head(job),
        summary=_summary_line(entry.summary),
        meta=meta,
        desc=_desc_details(job),
        cv=cv,
        posting=_posting_button(job),
    )


def _review_card(entry) -> str:
    job = coerce_job(entry.job)
    cv_filename = getattr(entry, "cv_filename", "")
    cv = (
        '<a class="btn cv" href="cvs/{}">↓ Download CV</a>'.format(_attr(cv_filename))
        if cv_filename else ""
    )
    posting = _posting_button(job)
    actions = '<div class="actions">{}{}</div>'.format(cv, posting) if cv or posting else ""
    return (
        '<article class="job review">{head}{summary}'
        '<div class="meta single">{why}</div>{desc}{actions}</article>'
    ).format(
        head=_card_head(job),
        summary=_summary_line(entry.summary),
        why=_meta_block("Why uncertain", _why_cell(entry.evaluation)),
        desc=_desc_details(job),
        actions=actions,
    )


def _section(icon, name, count, body) -> str:
    return (
        '<section><div class="sec-head"><h2>{icon} {name}</h2>'
        '<span class="n">{count}</span></div>{body}</section>'
    ).format(icon=icon, name=_esc(name), count=count, body=body)


def _subsection(section, count, body) -> str:
    """One user-defined group inside a top-level list."""
    label = " ".join(
        part for part in (str(section.icon or "").strip(), str(section.name or "")) if part
    )
    return (
        '<div class="sub-head"><h3>{label}</h3><span class="n">{count}</span></div>{body}'
    ).format(label=_esc(label), count=count, body=body)


def _grouped_body(entries, ctx, list_name, card, warnings) -> str:
    """Cards for `entries`, split into sub-headed groups when sections apply.

    With nothing configured for this list, `group_entries` returns no groups and
    the body is the same flat run of cards the digest has always rendered.
    """
    groups, group_warnings = group_entries(entries, ctx.sections, list_name)
    warnings.extend(group_warnings)
    if not groups:
        return "".join(card(entry) for entry in entries)
    return "".join(
        _subsection(section, len(bucket), "".join(card(entry) for entry in bucket))
        for section, bucket in groups
    )


def _fits_section(ctx, warnings) -> str:
    if not ctx.fits:
        return ""
    return _section(
        "✅", "Fits", len(ctx.fits),
        _grouped_body(ctx.fits, ctx, "fits", _fit_card, warnings),
    )


def _review_section(ctx, warnings) -> str:
    if not ctx.review:
        return ""
    return _section(
        "\U0001f50d", "Needs review", len(ctx.review),
        _grouped_body(ctx.review, ctx, "review", _review_card, warnings),
    )


def _sections_warning(ctx, warnings) -> str:
    """One strip for both kinds of section problem.

    They arrive by different routes: `ctx.sections_error` is set by the loader
    before the run is rendered (and is the one that also fires a Telegram
    alert), while `warnings` is collected during rendering, after the alert
    opportunity has passed.
    """
    # Deduplicated because a section applying to both "fits" and "review" is
    # grouped twice, and group_entries dedupes only within a single call — a
    # predicate that raises would otherwise say the same thing twice.
    messages = []
    for message in [str(ctx.sections_error or "").strip()] + list(warnings):
        if message and message not in messages:
            messages.append(message)
    if not messages:
        return ""
    return '<div class="warn"><b>Sections:</b> {}</div>'.format(_esc(" · ".join(messages)))


def _deferred_section(deferred) -> str:
    if not deferred:
        return ""
    items = []
    for entry in deferred:
        job = coerce_job(entry.job)
        title = _esc(job.get("title", "") or "Untitled role")
        company = _esc(job.get("company", ""))
        comp = ' <span class="company">{}</span>'.format(company) if company else ""
        items.append("<li><span><b>{}</b>{}</span>{}</li>".format(title, comp, _posting_link(job)))
    body = (
        '<ul class="deferred">{items}</ul>'
        '<p class="note">Not enough job-description text for a reliable decision — '
        "these retry automatically next run.</p>"
    ).format(items="".join(items))
    return _section("⚠️", "Deferred", len(deferred), body)


def _stats_bar(ctx) -> str:
    # New/Evaluated are run-level context; Fits/Review/Deferred are counted from
    # what the dashboard actually shows (a fit whose CV failed to compile is
    # dropped from the bundle, so stats.fits would over-count).
    cells = [
        ("New", _stat(ctx.stats, "new_jobs"), ""),
        ("Evaluated", _stat(ctx.stats, "evaluated"), ""),
        ("Fits", len(ctx.fits), "fit"),
        ("Review", len(ctx.review), "review"),
        ("Deferred", len(ctx.deferred), "defer"),
    ]
    chips = "".join(
        '<div class="{cls}"><b>{value}</b><span>{label}</span></div>'.format(
            cls=("stat " + tone).strip(), value=value, label=_esc(label))
        for label, value, tone in cells
    )
    return '<div class="stats">{}</div>'.format(chips)


def _issues_bar(stats) -> str:
    """A warning strip when a run had problems, so the digest never hides them.

    (Preparation and delivery failures also trigger their own instant Telegram
    alerts, but evaluation failures would otherwise be invisible in the report.)
    """
    problems = [
        ("evaluation failure", _stat(stats, "evaluation_failed")),
        ("CV preparation failure", _stat(stats, "preparation_failed")),
        ("delivery failure", _stat(stats, "delivery_failed")),
        ("awaiting retry", _stat(stats, "retries_waiting")),
        ("newly blocked", _stat(stats, "newly_blocked")),
    ]
    active = [
        "{} {}{}".format(count, label, "" if count == 1 or label == "awaiting retry" else "s")
        for label, count in problems
        if count
    ]
    if not active:
        return ""
    return '<div class="warn">⚠️ {}</div>'.format(_esc(" · ".join(active)))


def render_digest_html(ctx) -> str:
    """Return a complete, standalone HTML document for the run."""
    date_str = ctx.date.isoformat() if hasattr(ctx.date, "isoformat") else str(ctx.date)
    warn = (
        '<div class="warn"><b>Source health:</b> {}</div>'.format(_esc(ctx.source_warning))
        if ctx.source_warning
        else ""
    )
    # Built before the warning strip: grouping is what discovers a predicate that
    # raises, and that has to reach the strip above the body it happened in.
    warnings = []
    body_sections = "".join(
        s for s in (
            _fits_section(ctx, warnings),
            _review_section(ctx, warnings),
            _deferred_section(ctx.deferred),
        ) if s
    )
    if not body_sections:
        body_sections = '<p class="empty">No evaluated jobs matched your criteria this run.</p>'
    sections_warn = _sections_warning(ctx, warnings)

    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>Job Search Digest — {date}</title>"
        "<style>{style}</style></head><body><div class=\"wrap\">"
        '<header class="top"><h1>Job Search Digest</h1>'
        '<div class="sub">Your matches for <span class="date">{date}</span></div></header>'
        "{stats}{issues}{warn}{sections_warn}{body}"
        "<footer>Generated for {date} · {usage}</footer>"
        "</div></body></html>"
    ).format(
        date=_esc(date_str),
        style=_STYLE,
        stats=_stats_bar(ctx),
        issues=_issues_bar(ctx.stats),
        warn=warn,
        sections_warn=sections_warn,
        body=body_sections,
        usage=_esc(ctx.usage_summary),
    )
