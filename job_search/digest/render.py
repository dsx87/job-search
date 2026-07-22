"""Render one run's digest as a self-contained, offline HTML dashboard.

No external assets (the page is opened locally from an extracted ZIP), so all
CSS is inlined and the page is theme-aware via ``prefers-color-scheme``. Every
interpolated value is HTML-escaped — postings are untrusted text.
"""
import html

from ..models import REGION_LABELS, Region, coerce_job

_STYLE = """
:root{
  --bg:#f6f7f9; --panel:#ffffff; --ink:#1a1d21; --muted:#5b6470; --line:#e4e7eb;
  --accent:#2b6cb0; --fit:#1a7f52; --fit-bg:#e6f4ec; --review:#9a6a00; --review-bg:#fbf1d9;
  --defer:#8a4b2b; --defer-bg:#f6e9e0; --chip:#eef1f4; --chip-ink:#3a424c;
}
@media (prefers-color-scheme:dark){
  :root{
    --bg:#14171a; --panel:#1d2126; --ink:#e7ebef; --muted:#9aa4af; --line:#2c323a;
    --accent:#77aee6; --fit:#7fd6a6; --fit-bg:#173026; --review:#e6c063; --review-bg:#2e2716;
    --defer:#e0a37e; --defer-bg:#2e2018; --chip:#262c33; --chip-ink:#c3ccd6;
  }
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.55 system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}
.wrap{max-width:1120px;margin:0 auto;padding:28px 20px 60px}
header.top{display:flex;flex-wrap:wrap;align-items:baseline;gap:10px 16px;margin-bottom:18px}
header.top h1{font-size:22px;margin:0;letter-spacing:-.01em}
.date{color:var(--muted);font-variant-numeric:tabular-nums}
.stats{display:flex;flex-wrap:wrap;gap:8px;margin:6px 0 22px}
.stat{background:var(--panel);border:1px solid var(--line);border-radius:10px;
  padding:8px 12px;min-width:78px}
.stat b{display:block;font-size:20px;line-height:1.1;font-variant-numeric:tabular-nums}
.stat span{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.04em}
.warn{background:var(--review-bg);color:var(--review);border:1px solid var(--line);
  border-radius:10px;padding:10px 14px;margin:0 0 22px;font-size:14px}
section{margin:0 0 30px}
h2{font-size:16px;margin:0 0 12px;display:flex;align-items:center;gap:8px}
h2 .n{color:var(--muted);font-weight:500}
.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;overflow:hidden}
.table-wrap{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:14px}
th{text-align:left;font-size:12px;text-transform:uppercase;letter-spacing:.04em;
  color:var(--muted);font-weight:600;padding:11px 14px;border-bottom:1px solid var(--line)}
td{padding:13px 14px;border-bottom:1px solid var(--line);vertical-align:top}
tbody.entry:last-child td{border-bottom:none}
tr.detail td{padding-top:0;border-bottom:1px solid var(--line)}
tbody.entry:last-child tr.detail td{border-bottom:none}
.role .title{font-weight:600}
.role .company{color:var(--muted)}
.reason{color:var(--ink)}
.tz{display:block;margin-top:6px;color:var(--review);font-size:13px}
.chips{display:flex;flex-wrap:wrap;gap:5px;margin-top:2px}
.chip{background:var(--chip);color:var(--chip-ink);border-radius:999px;
  padding:2px 9px;font-size:12px;white-space:nowrap}
.badge{border-radius:6px;padding:1px 7px;font-size:12px;font-weight:600}
.badge.remote{background:var(--fit-bg);color:var(--fit)}
.badge.region{background:var(--chip);color:var(--chip-ink)}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
a.cv{display:inline-block;background:var(--accent);color:#fff;border-radius:8px;
  padding:6px 12px;font-size:13px;font-weight:600;white-space:nowrap}
a.cv:hover{text-decoration:none;filter:brightness(1.06)}
details{margin-top:2px}
details summary{cursor:pointer;color:var(--muted);font-size:13px;user-select:none}
details .desc{margin-top:8px;color:var(--ink);white-space:pre-wrap;font-size:13.5px;
  max-height:340px;overflow:auto}
ul.deferred{list-style:none;margin:0;padding:0}
ul.deferred li{padding:11px 14px;border-bottom:1px solid var(--line)}
ul.deferred li:last-child{border-bottom:none}
ul.deferred .company{color:var(--muted)}
footer{color:var(--muted);font-size:12.5px;margin-top:34px;border-top:1px solid var(--line);
  padding-top:14px}
.empty{color:var(--muted);padding:16px 14px}
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
    url = str(job.get("url", "") or "").strip()
    if _is_http(url):
        return '<a href="{}" target="_blank" rel="noopener">{}</a>'.format(_attr(url), _esc(label))
    return '<span class="muted">—</span>'


def _role_cell(job) -> str:
    parts = ['<div class="title">{}</div>'.format(_esc(job.get("title", "") or "Untitled role"))]
    company = job.get("company", "")
    location = job.get("location", "")
    sub = " · ".join(p for p in (_esc(company), _esc(location)) if p)
    if sub:
        parts.append('<div class="company">{}</div>'.format(sub))
    badges = []
    if job.get("is_remote"):
        badges.append('<span class="badge remote">Remote</span>')
    region = job.get("region")
    region = region if isinstance(region, Region) else None
    if region and region != Region.UNKNOWN:
        badges.append('<span class="badge region">{}</span>'.format(_esc(REGION_LABELS.get(region, region.value))))
    if badges:
        parts.append('<div class="chips">{}</div>'.format("".join(badges)))
    return "".join(parts)


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


def _description_row(job, colspan) -> str:
    desc = str(job.get("description", "") or "").strip()
    if not desc:
        return ""
    return (
        '<tr class="detail"><td colspan="{}">'
        "<details><summary>Full job description</summary>"
        '<div class="desc">{}</div></details></td></tr>'
    ).format(colspan, _esc(desc))


def _fits_section(fits) -> str:
    if not fits:
        return ""
    rows = []
    for entry in fits:
        job = coerce_job(entry.job)
        summary = _esc(entry.summary) if entry.summary else '<span class="muted">—</span>'
        cv = '<a class="cv" href="cvs/{}">Download CV</a>'.format(_attr(entry.cv_filename))
        rows.append(
            "<tbody class=\"entry\"><tr>"
            '<td class="role">{role}</td>'
            "<td>{summary}</td>"
            "<td>{why}</td>"
            "<td>{facts}</td>"
            "<td>{cv}</td>"
            "<td>{link}</td>"
            "</tr>{detail}</tbody>".format(
                role=_role_cell(job),
                summary=summary,
                why=_why_cell(entry.evaluation),
                facts=_fact_chips(entry.evaluation, job),
                cv=cv,
                link=_posting_link(job),
                detail=_description_row(job, 6),
            )
        )
    return (
        '<section><h2>✅ Fits <span class="n">({n})</span></h2>'
        '<div class="card"><div class="table-wrap"><table>'
        "<thead><tr><th>Role</th><th>Summary</th><th>Why it fits</th>"
        "<th>Key facts</th><th>CV</th><th>Posting</th></tr></thead>"
        "{rows}</table></div></div></section>"
    ).format(n=len(fits), rows="".join(rows))


def _review_section(review) -> str:
    if not review:
        return ""
    rows = []
    for entry in review:
        job = coerce_job(entry.job)
        summary = _esc(entry.summary) if entry.summary else '<span class="muted">—</span>'
        rows.append(
            "<tbody class=\"entry\"><tr>"
            '<td class="role">{role}</td>'
            "<td>{summary}</td>"
            "<td>{why}</td>"
            "<td>{link}</td>"
            "</tr>{detail}</tbody>".format(
                role=_role_cell(job),
                summary=summary,
                why=_why_cell(entry.evaluation),
                link=_posting_link(job),
                detail=_description_row(job, 4),
            )
        )
    return (
        '<section><h2>🔍 Needs review <span class="n">({n})</span></h2>'
        '<div class="card"><div class="table-wrap"><table>'
        "<thead><tr><th>Role</th><th>Summary</th><th>Why uncertain</th><th>Posting</th></tr></thead>"
        "{rows}</table></div></div></section>"
    ).format(n=len(review), rows="".join(rows))


def _deferred_section(deferred) -> str:
    if not deferred:
        return ""
    items = []
    for entry in deferred:
        job = coerce_job(entry.job)
        title = _esc(job.get("title", "") or "Untitled role")
        company = _esc(job.get("company", ""))
        sub = ' <span class="company">— {}</span>'.format(company) if company else ""
        items.append("<li><b>{}</b>{} · {}</li>".format(title, sub, _posting_link(job)))
    return (
        '<section><h2>⚠️ Deferred <span class="n">({n})</span></h2>'
        '<div class="card"><ul class="deferred">{items}</ul></div>'
        '<p class="empty">Not enough job-description text for reliable evaluation — these retry next run.</p>'
        "</section>"
    ).format(n=len(deferred), items="".join(items))


def _stats_bar(ctx) -> str:
    # New/Evaluated are run-level context; Fits/Review/Deferred are counted from
    # what the dashboard actually shows (a fit whose CV failed to compile is
    # dropped from the bundle, so stats.fits would over-count).
    cells = [
        ("New", _stat(ctx.stats, "new_jobs")),
        ("Evaluated", _stat(ctx.stats, "evaluated")),
        ("Fits", len(ctx.fits)),
        ("Review", len(ctx.review)),
        ("Deferred", len(ctx.deferred)),
    ]
    chips = "".join(
        '<div class="stat"><b>{}</b><span>{}</span></div>'.format(value, _esc(label))
        for label, value in cells
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
    body_sections = "".join(
        s for s in (
            _fits_section(ctx.fits),
            _review_section(ctx.review),
            _deferred_section(ctx.deferred),
        ) if s
    )
    if not body_sections:
        body_sections = '<p class="empty">No evaluated jobs matched your criteria this run.</p>'

    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>Job Search Digest — {date}</title>"
        "<style>{style}</style></head><body><div class=\"wrap\">"
        '<header class="top"><h1>Job Search Digest</h1>'
        '<span class="date">{date}</span></header>'
        "{stats}{issues}{warn}{body}"
        "<footer>Generated for {date} · {usage}</footer>"
        "</div></body></html>"
    ).format(
        date=_esc(date_str),
        style=_STYLE,
        stats=_stats_bar(ctx),
        issues=_issues_bar(ctx.stats),
        warn=warn,
        body=body_sections,
        usage=_esc(ctx.usage_summary),
    )
