"""Optional escape hatch for the job-search pipeline.

Copy this file to ``job_search_config.py`` or point ``JOB_SEARCH_CONFIG_FILE``
at it. It is trusted Python, executed on every run: keep credentials in
environment variables and use this module only to adjust the runtime.

Every realistic knob is an environment variable (see docs/configuration.md);
reach for this file only for the rare thing that genuinely needs code — a
candidate filter, say, which has no setting of its own. ``configure`` is
called once, after the built-in object graph is built from settings and
before preflight checks it. Mutate ``runtime`` in place, return a replacement,
or both — ``build_runtime`` uses whatever comes back (or the runtime
unchanged if you return ``None``). Anything this file raises propagates as
your own traceback, unmodified.
"""


def _wants_remote_ios(job):
    return job.is_remote and "ios" in job.title.lower()


def configure(runtime, settings):
    """Only consider remote iOS roles; leave everything else untouched."""
    runtime.candidate_filter = _wants_remote_ios
    return runtime
