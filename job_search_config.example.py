"""Optional trusted escape hatch for the job-search pipeline.

Copy this file to ``job_search_config.py`` or point ``JOB_SEARCH_CONFIG_FILE``
at it. It is trusted Python, executed on every run: keep credentials in
environment variables and use this module only to adjust the runtime.

Start with ``job_search.example.toml``: provider, search, candidate, policy,
CV-page, digest, and delivery choices are declarative settings. Use this module
only for the rare reviewed behavior that no setting can express. ``configure``
is called once after the built-in object graph is built and before host
preflight. Mutate ``runtime`` in place, return a replacement, or both —
``build_runtime`` uses the returned runtime (or the unchanged one for ``None``).
Anything this file raises propagates as your own traceback, unmodified.
"""


# def _is_example_source(job):
#     return job.source == "example-source"


def configure(runtime, settings):
    """A no-op as shipped: copy this file, then uncomment what you need.

    Left inert on purpose — an active filter here discards each rejected job
    before evaluation, which can look like an empty digest. Example (with
    ``_is_example_source`` above uncommented)::

        runtime.candidate_filter = _is_example_source
    """
    return runtime
