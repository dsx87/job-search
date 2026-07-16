"""Immutable source-health results for one scraper run."""
from dataclasses import dataclass
from enum import Enum
from typing import Tuple

from ..models import Job


class SourceStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    SKIPPED = "skipped"
    TIMED_OUT = "timed_out"


@dataclass(frozen=True)
class SourceHealth:
    source: str
    status: SourceStatus
    raw_job_count: int = 0
    attempt_count: int = 0
    failure_detail: str = ""


@dataclass(frozen=True)
class FetchReport:
    jobs: Tuple[Job, ...]
    outcomes: Tuple[SourceHealth, ...]

    @property
    def has_usable_source(self):
        return any(
            outcome.status in (SourceStatus.SUCCESS, SourceStatus.PARTIAL)
            for outcome in self.outcomes
        )

    @property
    def incomplete_sources(self):
        return tuple(
            outcome.source
            for outcome in self.outcomes
            if outcome.status is not SourceStatus.SUCCESS
        )


def format_source_health(report, unhealthy_only=False):
    outcomes = report.outcomes
    if unhealthy_only:
        outcomes = tuple(item for item in outcomes if item.status is not SourceStatus.SUCCESS)
    if not outcomes:
        return ""
    parts = []
    for item in outcomes:
        detail = " — {}".format(item.failure_detail) if item.failure_detail else ""
        parts.append("{}: {} ({} raw, {} attempt(s)){}".format(
            item.source, item.status.value, item.raw_job_count, item.attempt_count, detail,
        ))
    return "Source health: " + "; ".join(parts)
