"""Deterministic tailored-CV rendering from the trusted base template.

Instead of asking the model to write full LaTeX (which risks fabrication,
truncation, and compile failures), it returns a structured SELECTION of existing
base bullets, and this module rebuilds the CV from the base. Every emitted line
is verbatim base content, so a tailored CV is always a compilable subset of the
base and can never introduce fabricated claims (audit Finding 19).
"""
import re

from ..profile import EXPECTED_JOB_ORDER

_ITEMIZE_RE = re.compile(r"\\begin\{itemize\}(.*?)\\end\{itemize\}", re.DOTALL)
_JOBHEADER_RE = re.compile(r"\\jobheader\{([^}]*)\}")


def _company_for(header_company, employer_order=None):
    employers = EXPECTED_JOB_ORDER if employer_order is None else employer_order
    for key in employers:
        if key.lower() in str(header_company or "").lower():
            return key
    return None


def _split_items(body):
    """Verbatim bullet texts (without the leading \\item) from an itemize body."""
    return [part.strip() for part in re.split(r"\\item\b", body)[1:] if part.strip()]


def _company_before(base_tex, pos, employer_order=None):
    """The configured employer of the last ``\\jobheader`` before ``pos``."""
    company = None
    for match in _JOBHEADER_RE.finditer(base_tex):
        if match.start() >= pos:
            break
        key = _company_for(match.group(1), employer_order)
        if key:
            company = key
    return company


def extract_job_bullets(base_tex, employer_order=None):
    """Return [{"company", "bullets": [...]}] for each experience itemize block."""
    jobs = []
    for match in _ITEMIZE_RE.finditer(base_tex):
        company = _company_before(base_tex, match.start(), employer_order)
        if company is None:
            continue
        jobs.append({"company": company, "bullets": _split_items(match.group(1))})
    return jobs


def _selected(bullets, indices):
    """Keep the chosen bullets in order, de-duplicated; never return empty."""
    if not indices:
        return bullets
    seen = set()
    chosen = []
    for i in indices:
        if isinstance(i, int) and not isinstance(i, bool) and 0 <= i < len(bullets) and i not in seen:
            seen.add(i)
            chosen.append(bullets[i])
    return chosen if chosen else bullets


def render_tailored(base_tex, selection, employer_order=None):
    """Rebuild the base CV keeping only the selected bullets per company.

    `selection` maps company -> ordered 0-based bullet indices. A company that is
    missing, empty, or all-invalid keeps all its bullets. Everything outside the
    experience itemize blocks is byte-identical to `base_tex`.
    """
    selection = selection or {}
    out = []
    last = 0
    for match in _ITEMIZE_RE.finditer(base_tex):
        company = _company_before(base_tex, match.start(), employer_order)
        bullets = _split_items(match.group(1))
        if company is None or not bullets:
            continue  # leave non-experience/empty itemize blocks verbatim
        chosen = _selected(bullets, selection.get(company))
        body = "\n" + "\n".join(f"  \\item {bullet}" for bullet in chosen) + "\n"
        out.append(base_tex[last:match.start()])
        out.append("\\begin{itemize}" + body + "\\end{itemize}")
        last = match.end()
    out.append(base_tex[last:])
    return "".join(out)
