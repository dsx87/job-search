"""The shared synthetic digests, used by the renderer tests and the preview tool."""
import datetime

from job_search.digest import DigestContext
from job_search.digest.fixtures import (
    oversized_context,
    sample_context,
    sample_fit,
    sample_sections,
)
from job_search.digest.sections import group_entries


def test_sample_context_has_all_three_lists_and_a_date():
    ctx = sample_context(datetime.date(2026, 8, 1))
    assert isinstance(ctx, DigestContext)
    assert ctx.date == datetime.date(2026, 8, 1)
    assert ctx.fits and ctx.review and ctx.deferred


def test_sample_context_grouped_by_default_and_ungrouped_on_request():
    assert sample_context().sections
    assert sample_context(grouped=False).sections == ()


def test_sample_sections_actually_group_the_sample_fits():
    ctx = sample_context()
    groups, warnings = group_entries(ctx.fits, ctx.sections, "fits")
    assert warnings == []
    # More than one bucket, or the fixture is not exercising grouping at all.
    assert len(groups) > 1


def test_sample_fit_covers_the_shapes_that_break_renderers():
    assert sample_fit(evaluation=False).evaluation is None
    assert sample_fit(url="").job.url == ""
    assert sample_fit(url="mailto:jobs@example.com").job.url == "mailto:jobs@example.com"
    assert "&" in sample_fit(title="R&D <Lead> 🚀").job.title


def test_oversized_context_is_far_past_the_60kb_budget():
    ctx = oversized_context()
    total = sum(len(e.job.description) for e in ctx.review)
    assert total > 100_000
