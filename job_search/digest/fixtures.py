"""Synthetic digests shared by the tests and scripts/telegraph_preview.py.

One source of truth on purpose: the preview tool publishes real telegra.ph
pages from these exact contexts, so what a human eyeballs is what the unit
tests assert on. Everything here is deterministic — no randomness, no clock —
so a golden-node comparison stays stable.
"""
import datetime
from types import SimpleNamespace

from ..models import Job, Region
from .model import DeferredEntry, DigestContext, FitEntry, ReviewEntry
from .sections import Section, all_of, in_region, is_remote

_DEFAULT_DATE = datetime.date(2026, 8, 1)


def sample_stats(**over):
    base = dict(
        new_jobs=12, evaluated=8, non_fit=3, fits=3, uncertain=2, deferred=2,
        evaluation_failed=0, preparation_failed=0, delivery_failed=0,
        retries_waiting=0, newly_blocked=0, notification_sent=0, cv_sent=0,
    )
    base.update(over)
    return SimpleNamespace(**base)


def sample_fit(
    title="Senior iOS Engineer",
    company="Acme",
    url="https://jobs.example.com/1",
    location="Berlin, Germany",
    *,
    evaluation=True,
    is_remote=True,
    region=Region.EU,
    summary="Swift and SwiftUI team, fully remote across the EU.",
    reason="Remote-EU iOS role matching Swift and SwiftUI.",
    timezone_note=None,
    cv_filename="igor_pivnyk_cv_acme.pdf",
    description="Long job description sentence. " * 40,
):
    job = Job(
        title=title, company=company, url=url, location=location,
        is_remote=is_remote, region=region, description=description,
        matched_skills=["swift", "swiftui", "ios"],
    )
    facts = {"seniority": "senior", "work_arrangement": "remote", "employment_type": "full_time"}
    ev = None
    if evaluation:
        ev = {"fit": True, "reason": reason, "timezone_note": timezone_note, "facts": facts}
    return FitEntry(
        job=job, evaluation=ev, summary=summary,
        pdf_bytes=b"%PDF-1.4 mock cv", cv_filename=cv_filename,
    )


def sample_review(
    title="Staff Engineer",
    company="Beta GmbH",
    url="https://jobs.example.com/2",
    location="Munich, Germany",
    *,
    is_remote=False,
    region=Region.EU,
    summary="Platform role, relocation unclear.",
    reason="Could not confirm remote eligibility.",
    description="Another long description sentence. " * 40,
):
    job = Job(
        title=title, company=company, url=url, location=location,
        is_remote=is_remote, region=region, description=description,
        matched_skills=["python"],
    )
    evaluation = {
        "fit": None, "reason": reason, "timezone_note": None,
        "facts": {"work_arrangement": "hybrid"},
    }
    return ReviewEntry(job=job, evaluation=evaluation, summary=summary)


def sample_deferred(title="Mobile Engineer", company="Gamma", url="https://jobs.example.com/3"):
    return DeferredEntry(job=Job(title=title, company=company, url=url, description="too short"))


def sample_sections():
    """A grouping config that puts the sample fits in more than one bucket."""
    return (
        Section("Remote EU", "\U0001f30d", ("fits", "review"),
                all_of(is_remote, in_region(Region.EU))),
        Section("On-site", "\U0001f3e2", ("fits", "review"), None),
    )


def sample_context(date=None, *, grouped=True, **over):
    """A realistic run: fits in two groups, two review entries, two deferred.

    Deliberately includes a fit with no evaluation, a fit with no URL, a fit
    with a non-http URL, and a title carrying ``&``, angle brackets and an
    emoji — the four shapes that break renderers.
    """
    fits = [
        sample_fit(),
        sample_fit(title="R&D <Lead> \U0001f680", company="Delta & Sons",
                   url="", is_remote=False, region=Region.UNKNOWN,
                   cv_filename="igor_pivnyk_cv_delta_sons.pdf"),
        sample_fit(title="Very " + "long " * 30 + "title", company="Epsilon",
                   url="mailto:jobs@example.com", evaluation=False,
                   cv_filename="igor_pivnyk_cv_epsilon.pdf"),
    ]
    values = dict(
        date=date or _DEFAULT_DATE,
        stats=sample_stats(),
        source_warning="",
        usage_summary="tokens in 12,400 / out 3,100 · state 84 KB",
        fits=fits,
        review=[sample_review(), sample_review(title="Backend Engineer", company="Zeta",
                                               url="https://jobs.example.com/4")],
        deferred=[sample_deferred(), sample_deferred(title="QA Engineer", company="Eta",
                                                     url="https://jobs.example.com/5")],
        sections=sample_sections() if grouped else (),
        sections_error="",
    )
    values.update(over)
    return DigestContext(**values)


def oversized_context(date=None):
    """A run whose review list alone blows past the 60 KB node budget."""
    review = [
        sample_review(title="Role {}".format(index), company="Company {}".format(index),
                      url="https://jobs.example.com/{}".format(100 + index),
                      summary="Summary sentence. " * 200,
                      reason="Reason sentence. " * 200,
                      description="Long description sentence. " * 200)
        for index in range(40)
    ]
    return sample_context(date, grouped=False, review=review)
