"""Source registry.

Importing this package imports every source module, which registers each source
class (via @register) into base.SOURCE_REGISTRY. We then expose the registry in
the canonical, load-bearing order below (the order --list-sources prints and the
default fetch order). A drift guard fails loudly if a source is added/removed
without updating the order — the order is part of the public behavior.

Optional sources (jobspy/playwright) register without importing their optional
dependency: those imports happen lazily inside each source's fetch().
"""
from collections import OrderedDict

from .base import SOURCE_DESCRIPTIONS as _RAW_DESCRIPTIONS
from .base import SOURCE_REGISTRY as _RAW_REGISTRY

# Importing the modules triggers @register for every source class.
from . import api_sources  # noqa: E402,F401
from . import html_sources  # noqa: E402,F401
from . import jobspy_sources  # noqa: E402,F401
from . import playwright_sources  # noqa: E402,F401
from . import rss_sources  # noqa: E402,F401

# Canonical source order (load-bearing): the exact sequence the flat scraper's
# ALL_SOURCES dict used.
SOURCE_ORDER = [
    "jobspy",
    "linkedin-global",
    "linkedin-israel",
    "arc",
    "mobile.career",
    "jobscroller",
    "arbeitnow",
    "jobicy",
    "remoteok",
    "weworkremotely",
    "remotefirstjobs",
    "remotevibe",
    "himalayas",
    "remotive",
    "workingnomads",
    "themuse",
    "swissdevjobs",
    "relocate.me",
    "secrettelaviv",
]

# Drift guard: every registered source must appear in SOURCE_ORDER and vice versa.
_registered = set(_RAW_REGISTRY)
_ordered = set(SOURCE_ORDER)
if _registered != _ordered:
    missing = _ordered - _registered
    extra = _registered - _ordered
    raise RuntimeError(
        "source registry/order mismatch — "
        f"in order but not registered: {sorted(missing)}; "
        f"registered but not ordered: {sorted(extra)}"
    )

# Public, ordered views of the registry.
SOURCE_REGISTRY = OrderedDict((name, _RAW_REGISTRY[name]) for name in SOURCE_ORDER)
SOURCE_DESCRIPTIONS = OrderedDict((name, _RAW_DESCRIPTIONS[name]) for name in SOURCE_ORDER)
# Backwards-compatible alias used by the fetch layer and CLIs.
ALL_SOURCES = SOURCE_REGISTRY
