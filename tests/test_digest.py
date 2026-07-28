"""TDD for the run digest: HTML dashboard + ZIP bundle of tailored CVs."""
import datetime
import io
import zipfile
from types import SimpleNamespace

from job_search.digest import (
    DeferredEntry,
    DigestContext,
    FitEntry,
    ReviewEntry,
    build_digest_zip,
    cv_filename_for,
    digest_filename,
    render_digest_html,
)
from job_search.models import Job, Region


def _stats(**over):
    base = dict(new_jobs=3, evaluated=2, non_fit=1, fits=1, uncertain=1, deferred=1)
    base.update(over)
    return SimpleNamespace(**base)


def _fit(title="iOS Engineer", company="Acme", url="https://x/1", reason="Fully remote iOS role.",
         summary="Senior iOS role on a Swift app.", pdf=b"PDF-A", cv="igor_pivnyk_cv_acme_ab12cd.pdf"):
    job = Job(title=title, company=company, url=url, location="Remote", is_remote=True,
              region=Region.EU, description="A long description. " * 20, matched_skills=["ios", "swift"])
    evaluation = {"fit": True, "reason": reason, "timezone_note": None,
                  "facts": {"seniority": "senior", "work_arrangement": "remote"}}
    return FitEntry(job=job, evaluation=evaluation, summary=summary, pdf_bytes=pdf, cv_filename=cv)


def _context(fits=None, review=None, deferred=None, stats=None,
             sections=(), sections_error=""):
    return DigestContext(
        date=datetime.date(2026, 7, 21),
        stats=stats or _stats(),
        source_warning="",
        usage_summary="tokens: 10",
        fits=fits if fits is not None else [_fit()],
        review=review if review is not None else [],
        deferred=deferred if deferred is not None else [],
        sections=sections,
        sections_error=sections_error,
    )


# ── render_digest_html ────────────────────────────────────────────────────────

def test_html_is_a_complete_standalone_document():
    html = render_digest_html(_context())
    assert "<!doctype html>" in html.lower()
    assert "<html" in html.lower() and "</html>" in html.lower()
    assert "<style" in html.lower()  # self-contained, no external assets


def test_html_shows_fit_role_summary_reason_and_local_cv_link():
    entry = _fit()
    html = render_digest_html(_context(fits=[entry]))
    assert "iOS Engineer" in html
    assert "Acme" in html
    assert "Senior iOS role on a Swift app." in html
    assert "Fully remote iOS role." in html
    assert f'href="cvs/{entry.cv_filename}"' in html
    assert 'href="https://x/1"' in html  # the real posting link


def test_html_shows_run_date_and_counts():
    html = render_digest_html(_context())
    assert "2026-07-21" in html
    assert ">1<" in html or "Fits" in html  # a fits count is surfaced


def test_html_escapes_fields():
    entry = _fit(title="R&D <iOS>", company="AT&T", reason="great <fit>")
    html = render_digest_html(_context(fits=[entry]))
    assert "R&amp;D &lt;iOS&gt;" in html
    assert "AT&amp;T" in html
    assert "great &lt;fit&gt;" in html
    assert "<iOS>" not in html  # never rendered as raw markup


def test_html_includes_review_and_deferred_sections():
    review = [ReviewEntry(
        job=Job(title="Maybe iOS", company="Beta", url="https://x/2", description="d"),
        evaluation={"reason": "policy could not decide", "timezone_note": None},
        summary="Ambiguous remote scope.",
    )]
    deferred = [DeferredEntry(job=Job(title="Sparse", company="Gamma", url="https://x/3", description="tiny"))]
    html = render_digest_html(_context(review=review, deferred=deferred))
    assert "Maybe iOS" in html
    assert "policy could not decide" in html
    assert "Sparse" in html
    assert 'href="https://x/3"' in html


def test_header_counts_reflect_shown_entries_not_stats():
    # stats can over-count (a fit whose CV failed to compile is dropped from the
    # bundle); the dashboard must report what it actually shows.
    stats = _stats(fits=9, uncertain=9, deferred=9)
    a = _fit(company="Acme", cv="igor_pivnyk_cv_acme_a.pdf")
    b = _fit(company="Beta", cv="igor_pivnyk_cv_beta_b.pdf")
    html = render_digest_html(_context(fits=[a, b], review=[], deferred=[], stats=stats))
    assert "<b>2</b><span>Fits</span>" in html
    assert "<b>0</b><span>Review</span>" in html
    assert "<b>0</b><span>Deferred</span>" in html


def test_html_surfaces_nonzero_failure_counts():
    stats = _stats(preparation_failed=1, evaluation_failed=2)
    html = render_digest_html(_context(stats=stats))
    assert "2 evaluation failures" in html
    assert "1 CV preparation failure" in html


def test_html_hides_failure_row_when_all_zero():
    html = render_digest_html(_context(stats=_stats()))  # base stats: no failures
    assert "evaluation failure" not in html
    assert "preparation failure" not in html


def test_html_shows_source_warning_when_present():
    ctx = _context()
    ctx.source_warning = "flaky: partial (page two failed)"
    html = render_digest_html(ctx)
    assert "flaky: partial" in html


def test_fits_render_as_cards_not_a_scrolling_table():
    # Redesigned to a responsive card layout — a phone can't read a 6-column
    # table without horizontal scrolling, so there is no <table> at all.
    html = render_digest_html(_context(fits=[_fit()]))
    assert '<article class="job fit">' in html
    assert "<table" not in html


def test_fit_cv_is_a_prominent_download_button():
    entry = _fit()
    html = render_digest_html(_context(fits=[entry]))
    assert 'class="btn cv" href="cvs/{}"'.format(entry.cv_filename) in html


def test_review_renders_as_a_card():
    review = [ReviewEntry(
        job=Job(title="Maybe iOS", company="Beta", url="https://x/2", description="d"),
        evaluation={"reason": "policy could not decide", "timezone_note": None},
        summary="Ambiguous remote scope.",
    )]
    html = render_digest_html(_context(review=review))
    assert '<article class="job review">' in html


# ── build_digest_zip ──────────────────────────────────────────────────────────

def test_zip_contains_index_and_one_pdf_per_fit():
    a = _fit(company="Acme", pdf=b"PDF-A", cv="igor_pivnyk_cv_acme_a1.pdf")
    b = _fit(company="Beta", pdf=b"PDF-B", cv="igor_pivnyk_cv_beta_b2.pdf")
    data = build_digest_zip(_context(fits=[a, b]))

    zf = zipfile.ZipFile(io.BytesIO(data))
    names = set(zf.namelist())
    assert "index.html" in names
    assert f"cvs/{a.cv_filename}" in names
    assert f"cvs/{b.cv_filename}" in names
    assert zf.read(f"cvs/{a.cv_filename}") == b"PDF-A"
    assert zf.read(f"cvs/{b.cv_filename}") == b"PDF-B"


def test_zip_html_links_resolve_to_bundled_pdfs():
    entry = _fit()
    data = build_digest_zip(_context(fits=[entry]))
    zf = zipfile.ZipFile(io.BytesIO(data))
    index = zf.read("index.html").decode("utf-8")
    assert f'href="cvs/{entry.cv_filename}"' in index
    assert f"cvs/{entry.cv_filename}" in zf.namelist()


def test_zip_with_no_fits_still_has_index():
    data = build_digest_zip(_context(fits=[], deferred=[DeferredEntry(job=Job(title="S", company="C"))]))
    zf = zipfile.ZipFile(io.BytesIO(data))
    assert zf.namelist() == ["index.html"]


# ── filename helpers ──────────────────────────────────────────────────────────

def test_digest_filename_uses_iso_date():
    assert digest_filename(datetime.date(2026, 7, 21)) == "job-digest-2026-07-21.zip"


def test_cv_filenames_are_unique_even_for_same_company():
    taken = set()
    job = Job(title="iOS", company="Acme", url="https://x/1")
    job2 = Job(title="iOS Senior", company="Acme", url="https://x/2")
    n1 = cv_filename_for(job, taken); taken.add(n1)
    n2 = cv_filename_for(job2, taken); taken.add(n2)
    assert n1 != n2
    assert n1.startswith("igor_pivnyk_cv_acme") and n1.endswith(".pdf")
    assert n2.startswith("igor_pivnyk_cv_acme") and n2.endswith(".pdf")


# ── user-defined sections ─────────────────────────────────────────────────────

from job_search.digest.sections import Section, is_remote, on_job  # noqa: E402


def _review(title="Maybe iOS", company="Globex", reason="Unclear on remote."):
    job = Job(title=title, company=company, url="https://x/2", location="Berlin",
              description="Another description. " * 20)
    evaluation = {"fit": None, "reason": reason, "timezone_note": None, "facts": {}}
    return ReviewEntry(job=job, evaluation=evaluation, summary="A maybe.")


def test_without_sections_the_fits_render_flat_exactly_as_before():
    html = render_digest_html(_context(fits=[_fit()]))
    assert 'class="sub-head"' not in html


def test_sections_group_the_fits_under_sub_headings_with_counts():
    remote = _fit(title="Remote iOS", url="https://x/r")
    onsite_job = Job(title="Onsite iOS", company="Globex", url="https://x/o",
                     location="Berlin", is_remote=False,
                     description="A long description. " * 20)
    onsite = FitEntry(job=onsite_job, evaluation={"fit": True, "reason": "r",
                                                 "timezone_note": None, "facts": {}},
                      summary="", pdf_bytes=b"PDF-B", cv_filename="cv_b.pdf")
    sections = (Section("Remote roles", "🌍", match=is_remote),)

    html = render_digest_html(_context(fits=[remote, onsite], sections=sections))

    assert "Remote roles" in html
    assert html.count('class="sub-head"') == 2
    assert "Other" in html            # the un-matched onsite fit
    assert "Remote iOS" in html and "Onsite iOS" in html


def test_a_section_applies_only_to_the_lists_it_names():
    sections = (Section("Berlin", "🏙", applies_to=("review",),
                        match=on_job(lambda job: "berlin" in job.location.lower())),)

    html = render_digest_html(_context(fits=[_fit()], review=[_review()], sections=sections))

    # The review list is grouped; the fits list, which the section does not
    # apply to, stays flat and produces no Other bucket.
    assert "Berlin" in html
    assert "Other" not in html


def test_empty_sections_are_not_rendered():
    sections = (Section("Never matches", "🚫", match=lambda _entry: False),)
    html = render_digest_html(_context(fits=[_fit()], sections=sections))
    assert "Never matches" not in html


def test_section_names_and_icons_are_html_escaped():
    sections = (Section("<script>x</script>", "<img>", match=is_remote),)
    html = render_digest_html(_context(fits=[_fit()], sections=sections))
    assert "<script>x</script>" not in html
    assert "&lt;script&gt;" in html


def test_a_sections_error_is_shown_as_a_warning_strip():
    html = render_digest_html(_context(sections_error="sections.py is invalid: bad"))
    assert "sections.py is invalid: bad" in html
    assert "Sections:" in html


def test_a_render_time_predicate_failure_is_shown_as_a_warning_strip():
    def boom(_entry):
        raise AttributeError("no such field")

    sections = (Section("Broken", "💥", match=boom),)
    html = render_digest_html(_context(fits=[_fit()], sections=sections))

    assert "Broken" in html
    assert "no such field" in html
    assert "Sections:" in html
