"""Deterministic tailored-CV rendering from the trusted base template.

Instead of asking the model to write full LaTeX (which risks fabrication,
truncation, and compile failures), it returns a structured SELECTION of existing
base bullets, and this module rebuilds the CV from the base. Every emitted line
is verbatim base content, so a tailored CV is always a compilable subset of the
base and can never introduce fabricated claims (audit Finding 19).

A base may also mark optional blocks, each holding exactly one itemize::

    %% OPTIONAL-SECTION-BEGIN: Side Project
    ...
    %% OPTIONAL-SECTION-END

Optional blocks are dropped unless the selection explicitly keeps at least one
of their bullets (under ``OPTIONAL_SELECTION_KEY``); marker lines never reach
the rendered output.
"""
import re

from ..config import ConfigurationError
from ..profile import EXPECTED_JOB_ORDER

_ITEMIZE_RE = re.compile(r"\\begin\{itemize\}(.*?)\\end\{itemize\}", re.DOTALL)
_JOBHEADER_RE = re.compile(r"\\jobheader\{([^}]*)\}")
# Case-insensitive so a mis-cased marker fails as malformed instead of being
# silently compiled as an ordinary comment.
_MARKER_LINE_RE = re.compile(
    r"^[ \t]*%+[ \t]*OPTIONAL-SECTION-[^\n]*(?:\n|$)", re.MULTILINE | re.IGNORECASE
)
_MARKER_BEGIN_RE = re.compile(r"%+\s*OPTIONAL-SECTION-BEGIN\s*:\s*(\S(?:.*\S)?)")
_MARKER_END_RE = re.compile(r"%+\s*OPTIONAL-SECTION-END")

# Selection key holding {optional section name (lower-cased): [indices]}; it
# rides in the company selection dict so plain {company: [idx]} callers work.
OPTIONAL_SELECTION_KEY = "__optional_sections__"


def _company_for(header_company, employer_order=None):
    employers = EXPECTED_JOB_ORDER if employer_order is None else employer_order
    for key in employers:
        if key.lower() in str(header_company or "").lower():
            return key
    return None


def _split_items(body):
    """Verbatim bullet texts (without the leading \\item) from an itemize body."""
    return [part.strip() for part in re.split(r"\\item\b", body)[1:] if part.strip()]


def _inside(pos, blocks):
    return any(block["start"] <= pos < block["end"] for block in blocks)


def _company_before(base_tex, pos, employer_order=None, blocks=()):
    """The configured employer of the last ``\\jobheader`` before ``pos``."""
    company = None
    for match in _JOBHEADER_RE.finditer(base_tex):
        if match.start() >= pos:
            break
        if _inside(match.start(), blocks):
            continue
        key = _company_for(match.group(1), employer_order)
        if key:
            company = key
    return company


def _optional_blocks(base_tex):
    """Parse the optional-section markers; raise ConfigurationError if malformed.

    Each block is a dict with its ``name``, ``bullets``, the marker-inclusive
    span ``start``/``end``, the ``drop_end`` that also swallows one doubled
    blank line when the block is removed, and the ``inner`` tex between markers.
    """
    blocks = []
    opened = None
    for match in _MARKER_LINE_RE.finditer(base_tex):
        line = match.group(0).strip()
        begin = _MARKER_BEGIN_RE.fullmatch(line)
        if begin:
            if opened is not None:
                raise ConfigurationError(
                    "Base CV optional section {!r} is not closed before the next "
                    "OPTIONAL-SECTION-BEGIN".format(opened[0])
                )
            before = base_tex[:match.start()]
            if before.count("\\begin{itemize}") != before.count("\\end{itemize}"):
                raise ConfigurationError(
                    "Base CV optional section {!r} must not start inside an "
                    "itemize".format(begin.group(1))
                )
            opened = (begin.group(1), match.start(), match.end())
        elif _MARKER_END_RE.fullmatch(line):
            if opened is None:
                raise ConfigurationError(
                    "Base CV has OPTIONAL-SECTION-END without a matching OPTIONAL-SECTION-BEGIN"
                )
            name, start, inner_start = opened
            opened = None
            inner = base_tex[inner_start:match.start()]
            itemize = _ITEMIZE_RE.search(inner)
            bullets = _split_items(itemize.group(1)) if itemize else []
            if (
                inner.count("\\begin{itemize}") != 1
                or inner.count("\\end{itemize}") != 1
                or not bullets
            ):
                raise ConfigurationError(
                    "Base CV optional section {!r} must contain exactly one "
                    "\\begin{{itemize}}...\\end{{itemize}} with \\item bullets".format(name)
                )
            end = match.end()
            drop_end = end
            if base_tex[:start].endswith("\n\n") and base_tex.startswith("\n", end):
                drop_end += 1
            blocks.append({
                "name": name, "bullets": bullets, "inner": inner,
                "start": start, "end": end, "drop_end": drop_end,
            })
        else:
            raise ConfigurationError(
                "Base CV has a malformed optional-section marker: {!r}".format(line)
            )
    if opened is not None:
        raise ConfigurationError(
            "Base CV optional section {!r} has no OPTIONAL-SECTION-END".format(opened[0])
        )
    names = [block["name"].lower() for block in blocks]
    for name in names:
        if names.count(name) > 1:
            raise ConfigurationError(
                "Base CV has more than one optional section named {!r}".format(name)
            )
    return blocks


def _apply_edits(base_tex, edits):
    """Replace non-overlapping (start, end, text) spans of ``base_tex``."""
    out = []
    last = 0
    for start, end, text in sorted(edits, key=lambda edit: edit[0]):
        out.append(base_tex[last:start])
        out.append(text)
        last = end
    out.append(base_tex[last:])
    return "".join(out)


def strip_optional_sections(base_tex):
    """``base_tex`` with every optional block removed (default-off render)."""
    blocks = _optional_blocks(base_tex)
    if not blocks:
        return base_tex
    return _apply_edits(base_tex, [(b["start"], b["drop_end"], "") for b in blocks])


def optional_section_bullets(base_tex):
    """Return [{"name", "bullets": [...]}] for each optional block."""
    return [
        {"name": block["name"], "bullets": block["bullets"]}
        for block in _optional_blocks(base_tex)
    ]


def extract_job_bullets(base_tex, employer_order=None):
    """Return [{"company", "bullets": [...]}] for each experience itemize block."""
    blocks = _optional_blocks(base_tex)
    jobs = []
    for match in _ITEMIZE_RE.finditer(base_tex):
        if _inside(match.start(), blocks):
            continue
        company = _company_before(base_tex, match.start(), employer_order, blocks)
        if company is None:
            continue
        jobs.append({"company": company, "bullets": _split_items(match.group(1))})
    return jobs


def _selected(bullets, indices):
    """Keep the chosen bullets in order, de-duplicated; never return empty."""
    return _chosen(bullets, indices) or bullets


def _chosen(bullets, indices):
    """The valid chosen bullets in order, de-duplicated; may be empty."""
    seen = set()
    chosen = []
    for i in indices or ():
        if isinstance(i, int) and not isinstance(i, bool) and 0 <= i < len(bullets) and i not in seen:
            seen.add(i)
            chosen.append(bullets[i])
    return chosen


def _itemize(bullets):
    body = "\n" + "\n".join(f"  \\item {bullet}" for bullet in bullets) + "\n"
    return "\\begin{itemize}" + body + "\\end{itemize}"


def render_tailored(base_tex, selection, employer_order=None):
    """Rebuild the base CV keeping only the selected bullets per company.

    `selection` maps company -> ordered 0-based bullet indices. A company that is
    missing, empty, or all-invalid keeps all its bullets. Optional blocks are
    kept only when ``selection[OPTIONAL_SELECTION_KEY]`` picks at least one valid
    bullet for them. Everything outside the experience itemize blocks and the
    optional blocks is byte-identical to `base_tex`.
    """
    selection = selection or {}
    blocks = _optional_blocks(base_tex)
    edits = []
    for match in _ITEMIZE_RE.finditer(base_tex):
        if _inside(match.start(), blocks):
            continue
        company = _company_before(base_tex, match.start(), employer_order, blocks)
        bullets = _split_items(match.group(1))
        if company is None or not bullets:
            continue  # leave non-experience/empty itemize blocks verbatim
        chosen = _selected(bullets, selection.get(company))
        edits.append((match.start(), match.end(), _itemize(chosen)))
    optional = selection.get(OPTIONAL_SELECTION_KEY)
    optional = optional if isinstance(optional, dict) else {}
    for block in blocks:
        chosen = _chosen(block["bullets"], optional.get(block["name"].lower()))
        if not chosen:
            edits.append((block["start"], block["drop_end"], ""))
            continue
        inner = block["inner"]
        itemize = _ITEMIZE_RE.search(inner)
        kept = inner[:itemize.start()] + _itemize(chosen) + inner[itemize.end():]
        edits.append((block["start"], block["end"], kept))
    return _apply_edits(base_tex, edits)
