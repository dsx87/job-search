"""Source base class and the @register decorator backing the source registry.

@register records each source class into insertion-ordered SOURCE_REGISTRY and
its human description into SOURCE_DESCRIPTIONS, and stamps the optional-dependency
marker (only) on sources that need one — so importing the registry never imports
the optional packages themselves.
"""


class BaseSource(object):
    name = "base"

    def fetch(self, verbose=False):
        raise NotImplementedError


# name -> source class, in registration order.
SOURCE_REGISTRY = {}
# name -> human-readable description.
SOURCE_DESCRIPTIONS = {}


def register(description, optional_dependency=None):
    def decorator(cls):
        if optional_dependency is not None:
            cls.optional_dependency = optional_dependency
        SOURCE_REGISTRY[cls.name] = cls
        SOURCE_DESCRIPTIONS[cls.name] = description
        return cls

    return decorator
