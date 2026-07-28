"""Load the user's digest section definitions from a Python file.

The file lives outside this package (``sections.py`` at the repo root by
default, overridable via the SECTIONS_FILE env var) and is plain Python, so a
predicate can call anything in this repo directly. It is loaded by path under a
private module name, so a root-level ``sections.py`` can never shadow or collide
with ``job_search.digest.sections``.

``load_sections`` never raises. Sections are opt-in, so a missing file yields no
sections and no message; every other problem comes back as a string the caller
prints, shows in the digest, and alerts on. The daily run is unattended, and a
typo in a presentation config must never cost a day of jobs — or the LLM and
pdflatex spend already paid for them.
"""
import importlib.util
import os

from ..models import Job
from .model import FitEntry, ReviewEntry
from .sections import LIST_NAMES, OTHER_SECTION, Section

# Deliberately not "sections": this module object is never registered in
# sys.modules, and the distinctive name keeps tracebacks unambiguous.
_MODULE_NAME = "job_search_user_sections"

# How many validation problems to name before truncating. The message rides in a
# Telegram alert and a warning strip; the first few identify the file well enough.
_MAX_REPORTED = 3


def _blank_entries():
    """Representative empty entries for the load-time smoke check.

    Three shapes, because they exercise different predicate assumptions: a fit
    with facts, a fit with NO evaluation at all (FitEntry.evaluation is
    Optional), and a review entry.
    """
    return (
        FitEntry(job=Job(), evaluation={"facts": {}}, summary="",
                 pdf_bytes=b"", cv_filename=""),
        FitEntry(job=Job(), evaluation=None, summary="",
                 pdf_bytes=b"", cv_filename=""),
        ReviewEntry(job=Job(), evaluation={"facts": {}}, summary=""),
    )


def _validate(sections):
    """Return a list of problem strings; empty when the sections are usable."""
    if not isinstance(sections, (list, tuple)):
        return [
            "SECTIONS must be a list of Section objects, got {}".format(
                type(sections).__name__
            )
        ]
    problems = []
    seen_names = set()
    for index, section in enumerate(sections):
        where = "SECTIONS[{}]".format(index)
        if not isinstance(section, Section):
            problems.append(
                "{} is a {}, not a Section".format(where, type(section).__name__)
            )
            continue
        name = str(section.name or "").strip()
        if not name:
            problems.append("{} has an empty name".format(where))
        elif name.lower() == OTHER_SECTION.name.lower():
            problems.append(
                "{} ({!r}) is reserved for the automatic catch-all; omit match= "
                "on a section to have it own the leftovers instead".format(
                    where, name
                )
            )
        elif name.lower() in seen_names:
            problems.append("{} repeats the name {!r}".format(where, name))
        else:
            seen_names.add(name.lower())
        try:
            applies = tuple(section.applies_to or ())
        except TypeError:
            problems.append(
                "{} ({!r}) has an applies_to that is not iterable "
                "(expected a list of list names)".format(where, name)
            )
            continue
        if not applies:
            problems.append("{} ({!r}) has an empty applies_to".format(where, name))
        for list_name in applies:
            if list_name not in LIST_NAMES:
                problems.append(
                    "{} ({!r}) applies_to {!r}; allowed: {}".format(
                        where, name, list_name, ", ".join(LIST_NAMES)
                    )
                )
        if section.match is not None and not callable(section.match):
            problems.append("{} ({!r}) match is not callable".format(where, name))
    return problems


def _summarize_problems(problems):
    """Join the first `_MAX_REPORTED` problems, noting how many were dropped."""
    shown = "; ".join(problems[:_MAX_REPORTED])
    remainder = len(problems) - _MAX_REPORTED
    if remainder > 0:
        shown += " (+{} more)".format(remainder)
    return shown


def _smoke_problems(sections):
    """Call every predicate once against blank entries, reporting any raise.

    This is what buys back the load-time typo-catching a declarative rule
    language would have given for free: ``lambda e: e.job.is_remot`` becomes a
    message naming the section, instead of a surprise during render. It is
    non-fatal because a legitimate predicate may reasonably dislike a degenerate
    entry.
    """
    messages = []
    blanks = _blank_entries()
    for section in sections:
        if section.match is None:
            continue
        for entry in blanks:
            try:
                section.match(entry)
            except Exception as exc:
                messages.append(
                    "section {!r} predicate raised on a blank entry ({}: {})".format(
                        section.name, type(exc).__name__, exc
                    )
                )
                break
    return messages


def load_sections(path):
    """Return ``(sections, error)`` for the config at ``path``. Never raises.

    An empty or missing path disables sections entirely and is not an error.
    A non-empty ``error`` with a non-empty ``sections`` is the smoke-check case:
    the sections are used and the message is still surfaced.
    """
    path = str(path or "").strip()
    if not path:
        return (), ""
    if not os.path.exists(path):
        # Sections are opt-in: no file at all is silent, not an error.
        return (), ""
    if not os.path.isfile(path):
        return (), "{} is not a file. Showing ungrouped lists.".format(path)

    try:
        spec = importlib.util.spec_from_file_location(_MODULE_NAME, path)
        if spec is None or spec.loader is None:
            raise ImportError("not an importable Python file")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    # A user file's top-level code runs here, and a stray `sys.exit()` raises
    # SystemExit rather than Exception. Catching it too keeps the never-raises
    # guarantee; KeyboardInterrupt is deliberately left alone so Ctrl-C still
    # stops a run.
    except (Exception, SystemExit) as exc:
        return (), "{} could not be loaded ({}: {}). Showing ungrouped lists.".format(
            path, type(exc).__name__, exc
        )

    try:
        if not hasattr(module, "SECTIONS"):
            return (), "{} defines no SECTIONS list. Showing ungrouped lists.".format(
                path
            )

        sections = module.SECTIONS
        problems = _validate(sections)
        if problems:
            return (), "{} is invalid: {}. Showing ungrouped lists.".format(
                path, _summarize_problems(problems)
            )

        return tuple(sections), "; ".join(_smoke_problems(sections))
    except (Exception, SystemExit) as exc:
        # Belt-and-braces: no future addition to validation or the smoke check
        # should be able to re-breach the never-raises guarantee. hasattr()
        # above only swallows AttributeError, so a property-like SECTIONS that
        # raises something else is caught here too, and SystemExit (e.g. a
        # predicate or module body calling sys.exit()) is caught alongside it.
        return (), "{} raised while validating SECTIONS ({}: {}). Showing ungrouped lists.".format(
            path, type(exc).__name__, exc
        )
