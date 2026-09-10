"""Filter pipeline: compose the pure rules into run_pipeline."""
from ..location.classify import apply_region
from .rules import (
    dedup,
    configured_location_filter,
    configured_opportunity_filter,
    configured_role_filter,
    configured_skills_filter,
    filter_by_age,
    india_exclusion_filter,
    opportunity_filter,
    role_filter,
    skills_filter,
    sort_jobs,
)


def run_pipeline(jobs, max_age_days=None, relocation_regions=None, search=None, candidate=None):
    """Filter source results using legacy rules or one supplied generic search.

    ``search=None`` is deliberately the old iOS/Israel behavior.  Passing a
    search object selects only its declarative rules, preventing profile
    constants from silently affecting a different candidate.
    """
    if search is not None:
        jobs = [job for job in jobs if configured_location_filter(job, search)]
        jobs = [job for job in jobs if configured_role_filter(job, search)]
        jobs = [job for job in jobs if configured_skills_filter(job, search)]
        jobs = [apply_region(job) for job in jobs]
        jobs = [
            job for job in jobs
            if configured_opportunity_filter(job, search, candidate, relocation_regions)
        ]
        if max_age_days is None:
            max_age_days = getattr(search, "max_age_days", 30)
        jobs = filter_by_age(jobs, max_age_days)
        return sort_jobs(dedup(jobs))
    jobs = [job for job in jobs if india_exclusion_filter(job)]
    jobs = [job for job in jobs if role_filter(job)]
    jobs = [job for job in jobs if skills_filter(job)]
    jobs = [apply_region(job) for job in jobs]
    jobs = [job for job in jobs if opportunity_filter(job, relocation_regions)]
    jobs = filter_by_age(jobs, 30 if max_age_days is None else max_age_days)
    jobs = dedup(jobs)
    jobs = sort_jobs(jobs)
    return jobs
