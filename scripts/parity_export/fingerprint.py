"""Fingerprint area: the hashes the legacy comparison needs, byte-exact (Decision 18).

``normalize-content.json``: ``_normalize_content`` on edge strings.
``evaluation-signature.json``: the engine's signature path (run.py): a raw
description, ``clean_job_description`` of it (the content), the criteria
version string passed in (fixed fake values; ``criteria_version`` itself is not
exported), ``_normalize_content`` of the content and ``evaluation_signature``.
``identity-tokens.json``: ``_identity_token`` (full SHA-256 hex of the UTF-8 key).
"""
from job_search.identity import identity_keys_from_fields
from job_search.pipeline.stages import clean_job_description
from job_search.state.seen_jobs import _identity_token, _normalize_content, evaluation_signature

NOTES = [
    "fingerprint: evaluation_signature = first 16 hex of SHA-256 over UTF-8 of "
    "_normalize_content(content) + U+0000 + criteria_version; the criteria_version values "
    "are fixed fake strings, not engine-computed.",
]

_CONTENT_CASES = [
    ("plain", "We build iOS apps in Swift."),
    ("digits_removed", "iOS 15 to iOS 16, Over 214 applicants, Reposted 3 days ago"),
    ("digits_inside_words", "Swift5 v2.3 x86_64 h264"),
    ("non_ascii_digits_removed", "٣ years, १२ months, １２３ yen"),
    ("superscript_kept", "x² ½ ① Ⅻ"),
    ("case_folding", "İSTANBUL Straße STRASSE ΟΔΟΣ"),
    ("whitespace_kinds", " a b c\x1cd\x1fe　f\tg\n"),
    ("not_python_whitespace", "a​b﻿c"),
    ("emoji_and_combining", "Café \U0001f469‍\U0001f4bb \U0001f1e9\U0001f1ea"),
    ("only_digits_and_space", " 123  456 "),
    ("empty", ""),
]

_SIGNATURE_DESCRIPTIONS = [
    ("plain_text", "We build iOS apps in Swift.\n\nRemote within Europe."),
    ("html_markup", "<p>We build <b>iOS</b> apps&nbsp;in Swift.</p><ul><li>Remote</li></ul>"),
    ("generics_kept_as_text", "Map<String, Int> in Swift 5"),
    ("entities_only", "R&amp;D team &#8212; iOS"),
    ("digit_churn_a", "Over 214 applicants. iOS 15 role."),
    ("digit_churn_b", "Over 37 applicants. iOS 16 role."),
    ("edge_unicode", "İstanbul office, Straße 5, \U0001f680 Café"),
    ("empty", ""),
]

# Fake criteria versions (16 lowercase hex like the engine's, or empty for the default).
_CRITERIA_VERSIONS = ["", "0123456789abcdef", "fedcba9876543210"]

_TOKEN_FIELDS = [
    ("url_and_key", "https://Jobs.Example.com/acme/1/", "iOS Engineer", "Acme Corp", "Berlin, Germany"),
    ("key_only", "", "iOS Engineer", "Acme Corp", ""),
    ("unicode_key", "", "İOS Mühendisi", "Straße GmbH", "Zürich  \U0001f30d"),
    ("location_only", "", "", "", "Remote"),
]


def _normalize_cases():
    return [{"name": name, "input": value, "expected": _normalize_content(value)} for name, value in _CONTENT_CASES]


def _signature_cases():
    cases = []
    for name, description in _SIGNATURE_DESCRIPTIONS:
        content = clean_job_description(description)
        for version in _CRITERIA_VERSIONS:
            cases.append({
                "name": f"{name}_{version or 'no_version'}",
                "description": description,
                "content": content,
                "criteria_version": version,
                "normalized": _normalize_content(content),
                "expected": evaluation_signature(content, version),
            })
    return cases


def _token_cases():
    cases = []
    for name, url, title, company, location in _TOKEN_FIELDS:
        for index, key in enumerate(identity_keys_from_fields(url, title, company, location)):
            cases.append({"name": f"{name}_{index}", "input": key, "expected": _identity_token(key)})
    for name, value in _CONTENT_CASES:
        cases.append({"name": f"raw_{name}", "input": value, "expected": _identity_token(value)})
    return cases


def export():
    for cases in (_CONTENT_CASES, _SIGNATURE_DESCRIPTIONS, _TOKEN_FIELDS):
        names = [case[0] for case in cases]
        assert len(names) == len(set(names)), "duplicate case name"
    return {
        "normalize-content.json": _normalize_cases(),
        "evaluation-signature.json": _signature_cases(),
        "identity-tokens.json": _token_cases(),
    }
