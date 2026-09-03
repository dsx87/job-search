"""Escape hatch used by tests that do not explicitly exercise configuration."""


def configure(runtime, settings):
    return runtime
