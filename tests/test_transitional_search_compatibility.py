"""Checked search-filter compatibility corpus for the transitional profile.

The configured filter deliberately evaluates only advertised locations.  A
candidate-specific India restriction stated in prose is left for grounded policy
evaluation, so that legacy source-filter heuristic is an intentional difference
and is not hidden by this corpus.  Likewise, a title such as "Android Developer"
with configured Apple terms in its body remains an explicit data choice rather
than inheriting the legacy non-Apple title predicate.
"""
from pathlib import Path

from job_search.config import PipelineConfig
from job_search.filters import run_pipeline
from job_search.models import Job


_FIXTURE = Path(__file__).parent / "fixtures" / "transitional_search.toml"


def _jobs():
    # The first six are the recorded legacy run_pipeline golden corpus.  The
    # final two cover title-led UIKit and description-led mobile Apple signals.
    return [
        Job(title="iOS Engineer", company="Acme", location="Berlin, Germany", url="fixture:eu", description="Swift UIKit, fully remote role", is_remote=True),
        Job(title="QA Engineer", company="Acme", location="Berlin", url="fixture:qa", description="ios swift", is_remote=True),
        Job(title="iOS Developer", company="Globex", location="Bangalore, India", url="fixture:india", description="Swift UIKit remote", is_remote=True),
        Job(title="Android Developer", company="Initech", location="Remote", url="fixture:android", description="Swift Kotlin iOS Android remote", is_remote=True),
        Job(title="iOS Dev", company="Onsite Co", location="Berlin", url="fixture:onsite", description="strictly onsite, local candidates only", is_remote=False),
        Job(title="macOS Engineer", company="Cupertino", location="Remote", url="fixture:mac", description="Swift, remote position", is_remote=True),
        Job(title="iOS Engineer", company="UIKit", location="Remote", url="fixture:uikit", description="UIKit remote position", is_remote=True),
        Job(title="Mobile Engineer", company="Signals", location="Remote", url="fixture:mobile", description="iOS UIKit remote position", is_remote=True),
    ]


def _ids(jobs):
    return {job.url for job in jobs}


def test_transitional_search_toml_records_legacy_filter_coverage(monkeypatch):
    monkeypatch.setenv("JOB_SEARCH_SETTINGS_FILE", str(_FIXTURE))
    config = PipelineConfig.from_env()

    legacy = _ids(run_pipeline(_jobs(), max_age_days=0))
    configured = _ids(run_pipeline(
        _jobs(), max_age_days=0, search=config.search, candidate=config.candidate,
    ))

    # The old acceptance/rejection corpus remains visible and non-empty.
    assert legacy == {"fixture:eu", "fixture:mac", "fixture:uikit", "fixture:mobile"}
    assert {"fixture:eu", "fixture:mac", "fixture:uikit", "fixture:mobile"} <= configured
    assert {"fixture:qa", "fixture:india", "fixture:onsite"}.isdisjoint(configured)

    # Intentional explicit-data difference: the generic TOML has no implicit
    # non-Apple title predicate, so an Android title with configured Apple
    # signals is retained for later policy review.
    assert configured - legacy == {"fixture:android"}
