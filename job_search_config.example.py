"""Optional composition example for the job-search pipeline.

Copy this file to ``job_search_config.py`` or point
``JOB_SEARCH_CONFIG_FILE`` at it. It is trusted Python, so keep credentials in
environment variables and use this module only to assemble components.

This no-op version returns the built-in graph unchanged, which makes the
file safe to import in tests and a safe starting point for local edits.
"""


def configure(defaults, settings):
    """Return the built-in component graph unchanged."""
    return defaults
