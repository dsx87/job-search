"""Filter pipeline: compose the pure rules into run_pipeline."""
from ..location.classify import apply_region
from .rules import (
    dedup,
    filter_by_age,
    india_exclusion_filter,
    opportunity_filter,
    role_filter,
    skills_filter,
    sort_jobs,
)


def run_pipeline(jobs, max_age_days=30, relocation_regions=None):
    jobs = [job for job in jobs if india_exclusion_filter(job)]
    jobs = [job for job in jobs if role_filter(job)]
    jobs = [job for job in jobs if skills_filter(job)]
    jobs = [apply_region(job) for job in jobs]
    jobs = [job for job in jobs if opportunity_filter(job, relocation_regions)]
    jobs = filter_by_age(jobs, max_age_days)
    jobs = dedup(jobs)
    jobs = sort_jobs(jobs)
    return jobs
