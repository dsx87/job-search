"""job_search — autonomous iOS/macOS job-search agent.

Light, lazy re-exports of the most-used names. Imports are deferred via
``__getattr__`` so importing the package never pulls in the whole dependency
graph (and never triggers the lazy optional jobspy/playwright imports).
"""

__all__ = ["Job", "Region", "fetch_jobs"]


def __getattr__(name):
    if name in ("Job", "Region"):
        from . import models

        return getattr(models, name)
    if name == "fetch_jobs":
        from .sources.fetch import fetch_jobs

        return fetch_jobs
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
