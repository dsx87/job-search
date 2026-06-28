"""Shared fixtures for the offline characterization suite.

Everything here is location-independent (no import of the modules under test),
so it does not need repointing as code migrates into the job_search package.
The modules under test are imported at the top of each individual test file —
those import lines are the only thing that changes per migration step.
"""
import io
import json as _json

import pytest


class FakeLLM:
    """Minimal stand-in for LLMClient / GeminiClient with a .generate method.

    Either return canned responses in order, or raise queued exceptions. Records
    every prompt it was given so tests can assert on prompt contents.
    """

    def __init__(self, responses=None):
        self._responses = list(responses or [])
        self.prompts = []

    def generate(self, prompt, temperature=0.0, json_mode=False):
        self.prompts.append(prompt)
        if not self._responses:
            raise AssertionError("FakeLLM ran out of canned responses")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def fake_llm():
    return FakeLLM


class FakeHTTPResponse:
    """Context-manager mimic of urllib's response object for monkeypatching
    urllib.request.urlopen. Supports .read(), .status, .headers.get(...)."""

    def __init__(self, body=b"", status=200, headers=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self._body = body
        self.status = status
        self._headers = headers or {"content-type": "application/json; charset=utf-8"}

    @property
    def headers(self):
        store = self._headers

        class _H:
            def get(self, key, default=""):
                for k, v in store.items():
                    if k.lower() == key.lower():
                        return v
                return default

            def get_content_charset(self, default=None):
                ct = self.get("content-type", "")
                import re as _re

                m = _re.search(r"charset=([^;\s]+)", ct, _re.IGNORECASE)
                return m.group(1) if m else default

        return _H()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def fake_http_response():
    return FakeHTTPResponse
