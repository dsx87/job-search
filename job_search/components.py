"""The concrete object graph the pipeline runs on: profile, prompts, CV
rendering, and output delivery.

``job_search.runtime`` builds these into a ``Runtime`` from settings; nothing
here is a Protocol or a swappable slot registry any more (see runtime.py).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from string import Template
from typing import Mapping, Sequence, Tuple

from .config import BASE_TEX_FILE, CV_DISPLAY_NAME, CV_FILENAME_PREFIX, OUT_PDF_FILE
from .profile import EXPECTED_JOB_ORDER, FORBIDDEN_TERM_PATTERNS
from .latex.compile import LatexCompiler


@dataclass(frozen=True)
class CandidateProfile:
    """Public candidate identity and CV-policy configuration."""

    display_name: str = CV_DISPLAY_NAME
    base_tex_path: str = BASE_TEX_FILE
    rendered_base_path: str = OUT_PDF_FILE
    cv_filename_prefix: str = CV_FILENAME_PREFIX
    employer_order: Tuple[str, ...] = tuple(EXPECTED_JOB_ORDER)
    forbidden_claim_patterns: Tuple[str, ...] = tuple(FORBIDDEN_TERM_PATTERNS)
    private_placeholders: Mapping[str, str] = field(
        default_factory=lambda: {"((PHONE))": "CV_PHONE"}
    )

    @classmethod
    def from_settings(cls, settings: object) -> "CandidateProfile":
        return cls(
            display_name=getattr(settings, "cv_display_name", CV_DISPLAY_NAME),
            base_tex_path=getattr(settings, "base_tex_file", BASE_TEX_FILE),
            rendered_base_path=getattr(settings, "rendered_base_file", OUT_PDF_FILE),
            cv_filename_prefix=getattr(
                settings, "cv_filename_prefix", CV_FILENAME_PREFIX
            ),
        )

    def resolve_private_placeholders(
        self, text: str, environ: Mapping[str, str] = None
    ) -> str:
        values = os.environ if environ is None else environ
        rendered = text
        for placeholder, env_name in self.private_placeholders.items():
            value = str(values.get(env_name, "")).strip()
            if placeholder == "((PHONE))":
                value = "\\enspace\\textbar\\enspace {}".format(value) if value else ""
            rendered = rendered.replace(placeholder, value)
        return rendered

    def validate_tex(self, tex: str) -> list:
        from .profile import validate_tailored_cv

        return validate_tailored_cv(
            tex,
            expected_job_order=self.employer_order,
            forbidden_term_patterns=self.forbidden_claim_patterns,
        )


@dataclass(frozen=True)
class CVArtifact:
    filename: str
    media_type: str
    content: bytes

    @property
    def bytes(self) -> bytes:
        """Compatibility spelling for adapters that call the payload bytes."""
        return self.content


@dataclass(frozen=True)
class DeliveryOutcome:
    """Completion receipt for one delivered fit.

    Lives here, beside DigestOutcome, because it is half of the output
    backend's duck-typed contract: an adapter has to return one, and reaching
    into ``pipeline.stages`` for it made a public contract depend on an
    internal module. ``pipeline.stages`` re-exports it for existing callers.
    """

    notification_sent: bool = False
    cv_sent: bool = False
    error: object = None
    notification_satisfied: bool = False
    cv_required: bool = True

    @property
    def complete(self) -> bool:
        return (
            self.notification_satisfied
            and (self.cv_sent or not self.cv_required)
            and self.error is None
        )


@dataclass(frozen=True)
class DigestOutcome:
    """Completion receipt for one delivered digest."""

    delivered: bool
    notification_sent: bool = False
    cv_sent: int = 0
    error: object = None


class DefaultPromptSet:
    """Default prompt facade; prompt bodies are supplied by the LLM modules."""

    revision = "default-prompts-v1"

    def fact_extraction(self, job: object) -> str:
        from .llm.facts import build_fact_extraction_prompt
        return build_fact_extraction_prompt(job)

    def job_summary(self, job: object) -> str:
        from .llm.summarize import build_job_summary_prompt
        return build_job_summary_prompt(job)

    def cv_bullet_selection(
        self, base_tex: str, job: object, profile: object
    ) -> str:
        from .llm.cv_edits import build_cv_bullet_selection_prompt
        return build_cv_bullet_selection_prompt(
            base_tex,
            job,
            employer_order=profile.employer_order,
            candidate_name=profile.display_name,
        )

    def compiler_repair(self, tex_source: str, error_excerpt: str) -> str:
        from .latex.compile import build_compiler_repair_prompt
        return build_compiler_repair_prompt(tex_source, error_excerpt)


class FilePromptSet:
    """File-backed prompt overrides using explicit ``$placeholder`` fields.

    Omitted files fall back to :class:`DefaultPromptSet`. A nonempty revision is
    mandatory because prompt behavior participates in evaluation reopening.
    """

    _ALLOWED_PLACEHOLDERS = {
        "fact_extraction": {
            "title", "company", "location", "is_remote", "description",
        },
        "job_summary": {
            "title", "company", "location", "is_remote", "description",
        },
        "cv_bullet_selection": {
            "title", "company", "location", "is_remote", "description",
            "resume_bullets", "candidate_name",
        },
        "compiler_repair": {"tex_source", "compiler_errors"},
    }

    def __init__(
        self,
        *,
        revision: str,
        fact_extraction_file: str = "",
        job_summary_file: str = "",
        cv_bullet_selection_file: str = "",
        compiler_repair_file: str = "",
        fallback: object = None,
    ):
        revision = str(revision or "").strip()
        if not revision:
            raise ValueError("FilePromptSet revision must be a nonempty string")
        self.revision = revision
        self.fallback = fallback or DefaultPromptSet()
        self._templates = {}
        for name, path in (
            ("fact_extraction", fact_extraction_file),
            ("job_summary", job_summary_file),
            ("cv_bullet_selection", cv_bullet_selection_file),
            ("compiler_repair", compiler_repair_file),
        ):
            if path:
                with open(path, encoding="utf-8") as handle:
                    template = Template(handle.read())
                identifiers = set()
                for match in template.pattern.finditer(template.template):
                    identifier = match.group("named") or match.group("braced")
                    if identifier:
                        identifiers.add(identifier)
                    elif match.group("invalid") is not None:
                        raise ValueError(
                            "{} prompt template has an invalid placeholder".format(name)
                        )
                unknown = identifiers - self._ALLOWED_PLACEHOLDERS[name]
                if unknown:
                    raise ValueError(
                        "{} prompt template has unknown placeholder(s): {}".format(
                            name, ", ".join(sorted(unknown))
                        )
                    )
                self._templates[name] = template

    def _render(self, name: str, values: Mapping[str, object], fallback) -> str:
        template = self._templates.get(name)
        if template is None:
            return fallback()
        try:
            return template.substitute({key: str(value) for key, value in values.items()})
        except (KeyError, ValueError) as exc:
            raise ValueError("{} prompt template is invalid: {}".format(name, exc)) from exc

    @staticmethod
    def _job_values(job: object, limit: int) -> dict:
        from .models import coerce_job
        from .text import section_aware_excerpt

        value = coerce_job(job)
        return {
            "title": value.get("title", ""),
            "company": value.get("company", ""),
            "location": value.get("location", ""),
            "is_remote": value.get("is_remote", ""),
            "description": section_aware_excerpt(value.get("description", ""), limit),
        }

    def fact_extraction(self, job: object) -> str:
        return self._render(
            "fact_extraction", self._job_values(job, 5000),
            lambda: self.fallback.fact_extraction(job),
        )

    def job_summary(self, job: object) -> str:
        return self._render(
            "job_summary", self._job_values(job, 4000),
            lambda: self.fallback.job_summary(job),
        )

    def cv_bullet_selection(
        self, base_tex: str, job: object, profile: object
    ) -> str:
        from .latex.tailor_render import extract_job_bullets

        lines = []
        for entry in extract_job_bullets(base_tex, profile.employer_order):
            lines.append("{}:".format(entry["company"]))
            lines.extend(
                "  [{}] {}".format(index, " ".join(str(bullet).split()))
                for index, bullet in enumerate(entry["bullets"])
            )
        values = self._job_values(job, 7000)
        values["resume_bullets"] = "\n".join(lines)
        values["candidate_name"] = profile.display_name
        return self._render(
            "cv_bullet_selection", values,
            lambda: self.fallback.cv_bullet_selection(base_tex, job, profile),
        )

    def compiler_repair(self, tex_source: str, error_excerpt: str) -> str:
        return self._render(
            "compiler_repair",
            {"tex_source": tex_source, "compiler_errors": error_excerpt},
            lambda: self.fallback.compiler_repair(tex_source, error_excerpt),
        )


class DefaultOutputRenderer:
    def render_notice(self, notice: object, **context: object) -> str:
        if context.get("level") == "error":
            import html
            title = html.escape(str(context.get("title", "Pipeline error")))
            icon = html.escape(str(context.get("icon", "⚠️")))
            detail = html.escape(str(notice))
            if context.get("code", False):
                detail = "<code>{}</code>".format(detail)
                separator = "\n\n"
            else:
                separator = "\n"
            return "{} <b>{}</b>{}{}".format(
                icon, title, separator, detail
            )
        return str(notice)

    def render_fit(self, job: object, evaluation: object) -> str:
        from .pipeline.stages import _format_notification
        return _format_notification(job, evaluation or {})

    def render_digest(self, context: object) -> str:
        from .digest.render import render_digest_html
        return render_digest_html(context)


class DefaultCVRenderer:
    media_types = ("application/pdf",)

    def __init__(
        self, settings: object, profile: CandidateProfile,
        *, prompts: object = None, compiler: object = None,
    ):
        self.settings = settings
        self.profile = profile
        self.prompts = prompts or DefaultPromptSet()
        self.compiler = compiler or LatexCompiler(
            getattr(settings, "latex_engine", "pdflatex") or "pdflatex",
            prompts=self.prompts, profile=self.profile,
        )

    def render_tailored(self, llm: object, job: object, evaluation: object = None) -> CVArtifact:
        from .config import load_base_tex, load_tailoring_instructions
        from .llm.tailor import tailor_resume
        from .pipeline.stages import CVPreparationError, _company_slug
        base = load_base_tex(self.profile.base_tex_path)
        instructions = load_tailoring_instructions(
            getattr(self.settings, "cv_tailoring_prompt_file", "cv_tailoring_prompt.md")
        )
        try:
            tex_source = tailor_resume(
                llm, instructions, base, job,
                prompts=self.prompts, profile=self.profile,
            )
            result = self.compiler.compile(llm, tex_source)
        except Exception as exc:
            raise CVPreparationError("CV rendering failed: {}".format(exc)) from exc
        if not result.ok or not result.pdf_bytes or result.page_count != 1:
            raise CVPreparationError(
                result.error_excerpt or "CV compilation did not produce a verified one-page PDF"
            )
        final_tex = getattr(result, "tex_source", "") or tex_source
        violations = self.profile.validate_tex(final_tex)
        if violations:
            raise CVPreparationError(
                "Final CV validation failed after compilation repair: " + "; ".join(violations)
            )
        company = getattr(job, "company", "") or (
            job.get("company", "") if hasattr(job, "get") else ""
        )
        return CVArtifact(
            "{}_{}.pdf".format(self.profile.cv_filename_prefix, _company_slug(company)),
            "application/pdf",
            result.pdf_bytes,
        )

    def render_base(self, llm=None) -> CVArtifact:
        from .config import load_base_tex
        from .pipeline.stages import CVPreparationError

        source = load_base_tex(self.profile.base_tex_path)
        compile_base = getattr(self.compiler, "compile_base", None)
        result = (
            compile_base(source)
            if callable(compile_base)
            else self.compiler.compile(llm, source)
        )
        if not result.ok or not result.pdf_bytes or result.page_count != 1:
            raise CVPreparationError(
                result.error_excerpt or "base CV did not compile to exactly one page"
            )
        final_tex = getattr(result, "tex_source", "") or source
        violations = self.profile.validate_tex(final_tex)
        if violations:
            raise CVPreparationError(
                "Base CV validation failed: " + "; ".join(violations)
            )
        return CVArtifact(
            os.path.basename(self.profile.rendered_base_path),
            "application/pdf",
            result.pdf_bytes,
        )


class DefaultOutputBackend:
    def __init__(self, telegram: object, telegraph_token: str = ""):
        self.telegram = telegram
        self.telegraph_token = str(telegraph_token or "")

    def deliver_notice(self, rendered: object) -> object:
        return self.telegram.send_message(str(rendered))

    def deliver_fit(
        self, rendered: object, artifact: CVArtifact = None,
        notification_already_sent: bool = False, *, job: object = None,
    ) -> DeliveryOutcome:
        from .models import coerce_job
        from .pipeline.stages import send_fit

        # ``job`` only supplies the document caption ("Tailored CV — <title> at
        # <company>"); the filename comes from the artifact. Backends that do
        # not caption anything are free to ignore it.
        described = coerce_job(job) if job is not None else None
        payload = {
            "title": described.get("title", "") if described is not None else "",
            "company": described.get("company", "") if described is not None else "",
            "message": rendered,
        }
        if artifact is not None:
            payload["artifact"] = artifact
            payload["pdf_bytes"] = artifact.content
        return send_fit(payload, self.telegram, notification_already_sent)

    def deliver_digest(
        self, rendered: object, artifacts: Sequence[CVArtifact] = (),
        *, context: object = None, date: object = None,
    ) -> DigestOutcome:
        """Publish the run as a telegra.ph page, or send it as one ZIP.

        The whole routing decision lives in ``digest.delivery`` — including
        which route a missing token forces — so this backend behaves like any
        other one: the pipeline hands it a rendered digest and gets back a
        completion receipt. ``context`` is the ``DigestContext`` the page
        renderer needs; ``artifacts`` is accepted for contract parity and is
        already reachable through the context's entries.
        """
        from .digest.delivery import deliver_telegram_digest

        if context is None:
            # No context means no page and no ZIP to build; the rendered notice
            # is all there is to send.
            try:
                self.telegram.send_message(str(rendered))
            except Exception as exc:
                return DigestOutcome(False, error=exc)
            return DigestOutcome(True, notification_sent=True, cv_sent=0)
        return deliver_telegram_digest(
            self.telegram, self.telegraph_token, context, rendered, date
        )


def _default_output_pair(settings: object, telegram: object):
    """Choose the built-in renderer/backend pair for ``settings.output_mode``.

    The pair is chosen together rather than as two independently swappable
    slots, so the kind-compatibility question ("does this renderer's output
    make sense to this backend?") is structurally impossible to get wrong for
    the built-in graph — there is no seam left where the two could disagree.
    """
    from .notify.telegram import TelegramClient

    mode = getattr(settings, "output_mode", "telegram")
    if mode == "telegram":
        telegram = telegram or TelegramClient(
            settings.telegram_bot_token, settings.telegram_chat_id
        )
        return DefaultOutputRenderer(), DefaultOutputBackend(
            telegram, getattr(settings, "telegraph_access_token", "")
        )

    from .output import FilesystemOutputBackend, HtmlOutputRenderer, PlainTextOutputRenderer

    if mode == "html":
        renderer = HtmlOutputRenderer()
    elif mode == "plain":
        renderer = PlainTextOutputRenderer()
    else:
        raise ValueError("Unknown OUTPUT_MODE: {!r}".format(mode))
    backend = FilesystemOutputBackend(
        getattr(settings, "output_dir", "") or ".",
        require_artifact=getattr(settings, "output_cv_mode", "required") == "required",
    )
    return renderer, backend


def _default_prompts(settings: object) -> object:
    """Choose the built-in prompt set for ``settings.prompt_dir``.

    Deliberately tolerant of a set ``prompt_dir`` with an empty
    ``prompt_revision`` (falls back to :class:`DefaultPromptSet` rather than
    raising here): the combination is a *policy* error, not a construction
    error, so it is rejected once with a named message by
    ``runtime.preflight`` rather than by an incidental exception from this
    constructor.

    A conventional filename absent from the directory is passed through as
    "" so :class:`FilePromptSet` applies its own per-prompt fallback, exactly
    as if that one file had never been named at all.
    """
    prompt_dir = str(getattr(settings, "prompt_dir", "") or "").strip()
    revision = str(getattr(settings, "prompt_revision", "") or "").strip()
    if not prompt_dir or not revision:
        return DefaultPromptSet()

    def _conventional_file(name):
        path = os.path.join(prompt_dir, name)
        return path if os.path.isfile(path) else ""

    return FilePromptSet(
        revision=revision,
        fact_extraction_file=_conventional_file("fact_extraction.txt"),
        job_summary_file=_conventional_file("job_summary.txt"),
        cv_bullet_selection_file=_conventional_file("cv_bullet_selection.txt"),
        compiler_repair_file=_conventional_file("compiler_repair.txt"),
    )


__all__ = [
    "CVArtifact",
    "CandidateProfile", "DefaultCVRenderer", "DefaultOutputBackend",
    "DefaultOutputRenderer", "DefaultPromptSet", "DeliveryOutcome",
    "DigestOutcome", "FilePromptSet", "LatexCompiler",
]
