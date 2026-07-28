"""User-defined digest sections: the vocabulary and the grouping rule.

A section is a name, an icon, the digest lists it applies to, and a predicate
over one digest entry (a FitEntry or a ReviewEntry). Sections are authored as
Python in a file outside this package (see section_config.load_sections), so a
predicate can call anything in this repo directly — `is_israel_job`,
`classify_region`, `has_relocation_evidence` — rather than through a rule
language that would have to re-implement them.

This module is pure: it never touches the filesystem and knows nothing about
where the section list came from. The helpers below exist so the common case
reads like data; a raw ``lambda entry: ...`` is equally supported.
"""
import datetime
import re
from dataclasses import dataclass

from ..models import coerce_job

# The digest lists a section may be applied to. Deferred is deliberately absent:
# those entries were never evaluated, so they carry no facts to match on.
LIST_NAMES = ("fits", "review")


@dataclass(frozen=True)
class Section:
    """One named group of job cards.

    ``match`` is any callable taking an entry and returning a bool; ``None``
    matches everything, which is how a deliberate catch-all is written last.
    """

    name: str
    icon: str = ""
    applies_to: tuple = ("fits",)
    match: object = None


def _job(entry):
    """The entry's Job, coerced so a mapping-shaped entry works too."""
    return coerce_job(getattr(entry, "job", None) or {})


def _facts(entry):
    """The LLM facts dict, or {} when the entry was decided without one."""
    evaluation = getattr(entry, "evaluation", None) or {}
    facts = evaluation.get("facts") if hasattr(evaluation, "get") else None
    return facts if isinstance(facts, dict) else {}


# ── Combinators ───────────────────────────────────────────────────────────────
def all_of(*predicates):
    def predicate(entry):
        return all(p(entry) for p in predicates)

    return predicate


def any_of(*predicates):
    def predicate(entry):
        return any(p(entry) for p in predicates)

    return predicate


def not_(inner):
    def predicate(entry):
        return not inner(entry)

    return predicate


# ── Predicates ────────────────────────────────────────────────────────────────
def is_remote(entry):
    return bool(_job(entry).is_remote)


def in_region(*regions):
    wanted = set(regions)

    def predicate(entry):
        return _job(entry).region in wanted

    return predicate


def fact(name, *values):
    """Test an LLM-extracted fact.

    With values, true when the fact equals any of them (case-insensitively).
    With none, true when the fact is known at all — i.e. present and not the
    "unknown" the extractor writes when a posting does not state it.
    """
    wanted = set(str(value).strip().lower() for value in values)

    def predicate(entry):
        value = str(_facts(entry).get(name, "")).strip().lower()
        if wanted:
            return value in wanted
        return bool(value) and value != "unknown"

    return predicate


def location_contains(*tokens):
    wanted = [str(token).strip().lower() for token in tokens if str(token).strip()]

    def predicate(entry):
        location = _job(entry).location.lower()
        return any(token in location for token in wanted)

    return predicate


def title_matches(pattern):
    """Regex against the job title. Compiled once, at section-definition time,
    so a broken pattern surfaces while the config file is being loaded."""
    compiled = re.compile(pattern, re.IGNORECASE)

    def predicate(entry):
        return bool(compiled.search(_job(entry).title))

    return predicate


def on_job(fn):
    """Adapt a job-taking function into an entry-taking predicate.

    The shortest bridge to everything already written in this repo:
    ``match=on_job(is_israel_job)``.
    """

    def predicate(entry):
        return bool(fn(_job(entry)))

    return predicate


def days_since_posted(entry):
    """Age of the posting in days, or None when the source gave no date."""
    posted = _job(entry).date_posted
    if posted is None:
        return None
    return (datetime.date.today() - posted).days


# ── Grouping ──────────────────────────────────────────────────────────────────
# Where entries that match no configured section land. Rendered only when it has
# something in it, so a config with its own trailing catch-all never shows both.
OTHER_SECTION = Section("Other", "📋")


def group_entries(entries, sections, list_name):
    """Group `entries` into (Section, entries) pairs; the first match wins.

    Section order is priority order: an entry matching several sections appears
    once, under the earliest one. That keeps the per-section counts summing to
    the list total and stops a fit's "Download CV" button from appearing twice.

    Returns ``(groups, warnings)``. An empty ``groups`` means nothing is
    configured for this list, and the caller renders it flat exactly as it did
    before sections existed. ``warnings`` names any section whose predicate
    raised — the entry simply falls through to the next section, because losing
    the grouping of one card is never worth losing the card.
    """
    active = [s for s in sections if list_name in tuple(s.applies_to or ())]
    if not active:
        return [], []

    buckets = [[] for _ in active]
    other = []
    warnings = []
    reported = set()

    for entry in entries:
        placed = False
        for index, section in enumerate(active):
            if section.match is None:
                buckets[index].append(entry)
                placed = True
                break
            try:
                matched = bool(section.match(entry))
            except Exception as exc:
                # Deduped by (section, error): a predicate broken on one field is
                # broken on every entry, and one warning per job would bury the
                # rest of the digest.
                key = (section.name, type(exc).__name__, str(exc))
                if key not in reported:
                    reported.add(key)
                    warnings.append(
                        "section {!r} predicate failed ({}: {})".format(
                            section.name, type(exc).__name__, exc
                        )
                    )
                continue
            if matched:
                buckets[index].append(entry)
                placed = True
                break
        if not placed:
            other.append(entry)

    groups = [(section, bucket) for section, bucket in zip(active, buckets) if bucket]
    if other:
        groups.append((OTHER_SECTION, other))
    return groups, warnings
