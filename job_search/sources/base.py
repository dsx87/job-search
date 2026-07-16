"""Source base class and the @register decorator backing the source registry.

@register records each source class into insertion-ordered SOURCE_REGISTRY and
its human description into SOURCE_DESCRIPTIONS, and stamps the optional-dependency
marker (only) on sources that need one — so importing the registry never imports
the optional packages themselves.
"""


class BaseSource(object):
    name = "base"
    # Whether this source runs in the default set. Default-off sources are still
    # registered (the drift guard counts them) but only run when explicitly
    # enabled via SOURCES_ENABLE / --sources <name>. See sources.fetch.select_sources.
    default_enabled = True

    def __init__(self):
        self._attempts = 0
        self._failures = []
        self._skip_detail = ""
        self._timeout_detail = ""

    def _attempt_succeeded(self):
        self._attempts += 1

    def _attempt_failed(self, error):
        self._attempts += 1
        detail = " ".join(str(error or "request failed").split())
        if detail:
            self._failures.append(detail[:240])

    def _attempt_http(self, status):
        if 200 <= status < 300:
            self._attempt_succeeded()
        else:
            self._attempt_failed("HTTP {}".format(status))

    def _skip(self, detail):
        self._skip_detail = " ".join(str(detail).split())[:240]

    def _timed_out(self, detail):
        self._timeout_detail = " ".join(str(detail).split())[:240]

    def fetch(self, verbose=False):
        raise NotImplementedError


# name -> source class, in registration order.
SOURCE_REGISTRY = {}
# name -> human-readable description.
SOURCE_DESCRIPTIONS = {}


def register(description, optional_dependency=None, default_enabled=True):
    def decorator(cls):
        if optional_dependency is not None:
            cls.optional_dependency = optional_dependency
        # Stamp the class only to turn default-off ON→OFF, mirroring the
        # optional_dependency pattern (leave the class attr untouched otherwise).
        if not default_enabled:
            cls.default_enabled = False
        SOURCE_REGISTRY[cls.name] = cls
        SOURCE_DESCRIPTIONS[cls.name] = description
        return cls

    return decorator
