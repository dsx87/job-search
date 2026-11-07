"""Legacy area: seen_jobs.json state + postings -> the answer per posting.

The answer (new | suppressed | reopened), signature and matched flag come from
``commands.legacy_answers``, the same function the ``legacy-check`` command uses.
Seen sets are built with the engine's own writers (``record_evaluation``), so
the marker strings are the real on-disk format.
"""
import datetime

from job_search.config import CandidateConfig, PolicyConfig, SearchConfig
from job_search.identity import job_identity_keys, normalize_url, title_company_key
from job_search.models import Job
from job_search.pipeline.stages import clean_job_description
from job_search.state.seen_jobs import SeenSet, evaluation_signature, record_evaluation

from .commands import legacy_answers, legacy_criteria_version

TODAY = datetime.date(2026, 11, 1)

NOTES = [
    "Seen sets are written with the engine's record_evaluation on 2026-11-01, so eval:first and "
    "eval:last markers carry that date.",
    "matched is true when the posting's signature is among the signatures recorded for its "
    "identity; one identity can hold several signatures. It is read from the eval markers "
    "apart from the answer, so a posting can be new and matched (markers_without_identity_new).",
    "criteria_version is computed as run_daily does, from synthetic criteria text and the "
    "engine's default search, candidate and policy settings.",
]

_CRITERIA_A = "Native iOS roles at product companies.\nRemote or relocation.\n"
_CRITERIA_B = "Native iOS roles at product companies.\nRemote only.\n"
VERSION_A = legacy_criteria_version(_CRITERIA_A, SearchConfig(), CandidateConfig(), PolicyConfig())
VERSION_B = legacy_criteria_version(_CRITERIA_B, SearchConfig(), CandidateConfig(), PolicyConfig())

_TEXT = "Build and ship a native iOS app in Swift. 5 years of experience. Remote within Europe."
_TEXT_CHANGED = _TEXT + " US residents only."
_TEXT_DIGITS = _TEXT.replace("5 years", "7 years")
_TEXT_HTML = "<p>Build and ship a native iOS app in Swift.</p><ul><li>5 years of experience.</li></ul>" \
             "<p>Remote within Europe.</p>"


def _job(n, description=_TEXT, **fields):
    values = {
        "title": "iOS Engineer",
        "company": f"Acme Corp {n}",
        "location": "Remote",
        "url": f"https://jobs.example.com/acme/{n}",
        "source": "remotive",
        "date_posted": datetime.date(2026, 10, 30),
        "description": description,
        "is_remote": True,
    }
    values.update(fields)
    return Job(**values)


def _signature(job, version):
    return evaluation_signature(clean_job_description(job.description), version)


def _seen(*entries):
    """Entries: a plain string key, or (job, version, verdict) recorded as run_daily records
    a verdict: the identity keys, then record_evaluation's markers."""
    seen = SeenSet()
    for entry in entries:
        if isinstance(entry, str):
            seen.add(entry)
        else:
            job, version, verdict = entry
            seen.update(job_identity_keys(job))
            record_evaluation(seen, job, _signature(job, version), verdict, TODAY)
    return sorted(seen)


def _case(name, seen_jobs, postings, version=VERSION_A):
    return {
        "name": name,
        "criteria_version": version,
        "seen_jobs": seen_jobs,
        "postings": [job.to_dict() for job in postings],
        "expected": legacy_answers(seen_jobs, postings, version),
    }


def _cases():
    p1 = _job(1)
    p1_changed = _job(1, _TEXT_CHANGED)
    cases = [
        _case("empty_seen_new", [], [p1]),
        _case("other_job_seen_new", _seen((_job(2), VERSION_A, "nonfit")), [p1]),
        _case("legacy_url_only_suppressed", _seen(normalize_url(p1.url)), [p1_changed]),
        _case(
            "legacy_title_company_only_suppressed",
            _seen(title_company_key(p1.title, p1.company, p1.location)),
            [p1_changed],
        ),
        _case(
            "legacy_url_normalized_match",
            _seen(normalize_url(p1.url)),
            [_job(1, url="HTTPS://Jobs.Example.com/acme/1/")],
        ),
    ]
    for verdict in ("fit", "nonfit", "deferred", "review", "uncertain", "maybe"):
        cases.append(_case(f"{verdict}_same_signature", _seen((p1, VERSION_A, verdict)), [p1]))
        cases.append(_case(
            f"{verdict}_changed_signature", _seen((p1, VERSION_A, verdict)), [p1_changed]
        ))
    cases += [
        _case("nonfit_digit_only_change", _seen((p1, VERSION_A, "nonfit")), [_job(1, _TEXT_DIGITS)]),
        _case("nonfit_html_same_text", _seen((p1, VERSION_A, "nonfit")), [_job(1, _TEXT_HTML)]),
        _case(
            "nonfit_two_signatures_old_matches",
            _seen((p1, VERSION_A, "nonfit"), (p1_changed, VERSION_A, "nonfit")),
            [p1],
        ),
        _case(
            "fit_then_nonfit_changed_signature",
            _seen((p1, VERSION_A, "nonfit"), (_job(1, _TEXT_DIGITS + " Hybrid."), VERSION_A, "fit")),
            [p1_changed],
        ),
        _case("nonfit_criteria_changed", _seen((p1, VERSION_A, "nonfit")), [p1], VERSION_B),
        _case("fit_criteria_changed", _seen((p1, VERSION_A, "fit")), [p1], VERSION_B),
        _case(
            "title_company_alias_new_url_changed",
            _seen((p1, VERSION_A, "nonfit")),
            [_job(1, _TEXT_CHANGED, url="https://boards.example.org/acme-1")],
        ),
        _case(
            "title_company_alias_new_url_same",
            _seen((p1, VERSION_A, "nonfit")),
            [_job(1, url="https://boards.example.org/acme-1")],
        ),
        _case(
            "known_without_signature_reopens",
            _seen(p1.url, *[
                key for key in _seen((p1, VERSION_A, "nonfit")) if key.startswith("eval:first:")
            ]),
            [p1],
        ),
        _case(
            "markers_without_identity_new",
            [key for key in _seen((p1, VERSION_A, "nonfit")) if key.startswith("eval:")],
            [p1],
        ),
        _case(
            "legacy_only_entries_and_batch",
            _seen(
                "https://jobs.example.com/acme/9",
                title_company_key("Android Engineer", "Example Ltd", "Berlin"),
                (_job(8), VERSION_A, "nonfit"),
                (p1, VERSION_A, "nonfit"),
                (_job(3), VERSION_A, "fit"),
            ),
            [_job(4), p1_changed, _job(3, _TEXT_CHANGED), _job(5), p1],
        ),
    ]
    return cases


def export():
    return {"cases.json": _cases()}
