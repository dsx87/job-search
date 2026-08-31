"""Public structural contracts for configuring the job-search pipeline.

Custom composition modules implement these protocols by shape; inheritance and
package discovery are deliberately unnecessary.  The concrete defaults live
here too so a configuration can selectively replace one component with
``dataclasses.replace(defaults, ...)``.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from string import Template
from typing import Mapping, Protocol, Sequence, Tuple, runtime_checkable

from .profile import EXPECTED_JOB_ORDER, FORBIDDEN_TERM_PATTERNS
from .latex.compile import LatexCompiler


@runtime_checkable
class PromptSet(Protocol):
    revision: str

    def fact_extraction(self, job: object) -> str: ...
    def job_summary(self, job: object) -> str: ...
    def cv_bullet_selection(
        self, base_tex: str, job: object, profile: object = None
    ) -> str: ...
    def compiler_repair(self, tex_source: str, error_excerpt: str) -> str: ...


@runtime_checkable
class LLMProvider(Protocol):
    scheme: str
    model: str

    def generate(
        self, prompt: str, temperature: float = 0.0, json_mode: bool = False,
        response_schema: object = None,
    ) -> str: ...


@runtime_checkable
class LLMService(Protocol):
    def generate(
        self, prompt: str, temperature: float = 0.0, json_mode: bool = False,
        response_schema: object = None,
    ) -> str: ...

    def usage_summary(self) -> str: ...


@runtime_checkable
class CandidateFilter(Protocol):
    revision: str

    def include(self, job: object) -> bool: ...


@runtime_checkable
class JobEvaluator(Protocol):
    revision: str

    def evaluate(self, llm: LLMService, criteria: str, job: object) -> dict: ...
    def fingerprint(self, criteria: str) -> str: ...


@dataclass(frozen=True)
class CandidateProfile:
    """Public candidate identity and CV-policy configuration."""

    display_name: str = "Igor Pivnyk"
    base_tex_path: str = "igor_pivnyk_cv_base_updated.tex"
    rendered_base_path: str = "igor_pivnyk_cv_base_updated.pdf"
    cv_filename_prefix: str = "igor_pivnyk_cv"
    employer_order: Tuple[str, ...] = tuple(EXPECTED_JOB_ORDER)
    forbidden_claim_patterns: Tuple[str, ...] = tuple(FORBIDDEN_TERM_PATTERNS)
    private_placeholders: Mapping[str, str] = field(
        default_factory=lambda: {"((PHONE))": "CV_PHONE"}
    )
    revision: str = "igor-profile-v1"

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


@runtime_checkable
class CVCompiler(Protocol):
    executable: str

    def compile(
        self, llm: LLMService, tex_source: str, max_attempts: int = 3
    ) -> object: ...


@runtime_checkable
class CVRenderer(Protocol):
    media_types: Sequence[str]

    def render_tailored(
        self, llm: LLMService, job: object, evaluation: object = None
    ) -> CVArtifact: ...
    def render_base(self, llm: LLMService = None) -> CVArtifact: ...


@runtime_checkable
class SectionProvider(Protocol):
    def load(self) -> tuple: ...


@runtime_checkable
class OutputRenderer(Protocol):
    kind: str

    def render_notice(self, notice: object, **context: object) -> object: ...
    def render_fit(self, job: object, evaluation: object) -> object: ...
    def render_digest(self, context: object) -> object: ...


@dataclass(frozen=True)
class DeliveryOutcome:
    """Completion receipt for one delivered fit.

    Lives here, beside DigestOutcome, because it is half of the OutputBackend
    contract: a third-party backend has to return one, and reaching into
    ``pipeline.stages`` for it made a public contract depend on an internal
    module. ``pipeline.stages`` re-exports it for existing callers.
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


@runtime_checkable
class OutputBackend(Protocol):
    accepted_renderer_kinds: Sequence[str]
    accepted_media_types: Sequence[str]
    cv_mode: str

    def deliver_notice(self, rendered: object) -> object: ...
    def deliver_fit(
        self, rendered: object, artifact: CVArtifact = None,
        notification_already_sent: bool = False, *, job: object = None,
    ) -> DeliveryOutcome: ...
    def deliver_digest(
        self, rendered: object, artifacts: Sequence[CVArtifact] = (),
        *, context: object = None, date: object = None,
    ) -> DigestOutcome: ...


@dataclass
class Components:
    """The pipeline's object graph.

    Deliberately mutable: a ``configure`` function may either return
    ``dataclasses.replace(defaults, ...)`` or assign fields on ``defaults`` in
    place, and both are supported. Nothing in this package mutates a graph it
    was handed — ``composition.rebind_defaults`` builds a new one with
    ``replace`` — so an author's object is never modified behind their back.
    """

    prompts: PromptSet
    llm: LLMService
    candidate_filter: CandidateFilter
    evaluator: JobEvaluator
    profile: CandidateProfile
    cv_renderer: CVRenderer
    section_provider: SectionProvider
    output_renderer: OutputRenderer
    output_backend: OutputBackend


class AllowAllCandidates:
    revision = "allow-all-v1"

    def include(self, job: object) -> bool:
        return True


class DefaultJobEvaluator:
    revision = "default-policy-v1"
    requires_criteria = True

    def __init__(self, prompts: PromptSet = None):
        self.prompts = prompts

    def evaluate(self, llm: LLMService, criteria: str, job: object) -> dict:
        from .llm.eval import evaluate_job
        return evaluate_job(llm, criteria, job, prompts=self.prompts)

    def fingerprint(self, criteria: str) -> str:
        from .state.seen_jobs import criteria_version
        prompt_revision = getattr(self.prompts, "revision", DefaultPromptSet.revision)
        if self.revision == "default-policy-v1" and prompt_revision == DefaultPromptSet.revision:
            return criteria_version(criteria)
        return criteria_version(
            "{}\n[evaluator:{}]\n[prompts:{}]".format(
                criteria, self.revision, prompt_revision
            )
        )


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
        self, base_tex: str, job: object, profile: object = None
    ) -> str:
        from .llm.cv_edits import build_cv_bullet_selection_prompt
        return build_cv_bullet_selection_prompt(
            base_tex,
            job,
            employer_order=getattr(profile, "employer_order", None),
            candidate_name=getattr(profile, "display_name", "Igor Pivnyk"),
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
        fallback: PromptSet = None,
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
        self, base_tex: str, job: object, profile: object = None
    ) -> str:
        from .latex.tailor_render import extract_job_bullets
        from .llm.cv_edits import _configured_selection_prompt

        lines = []
        for entry in extract_job_bullets(
            base_tex, getattr(profile, "employer_order", None)
        ):
            lines.append("{}:".format(entry["company"]))
            lines.extend(
                "  [{}] {}".format(index, " ".join(str(bullet).split()))
                for index, bullet in enumerate(entry["bullets"])
            )
        values = self._job_values(job, 7000)
        values["resume_bullets"] = "\n".join(lines)
        values["candidate_name"] = getattr(
            profile, "display_name", "Igor Pivnyk"
        )
        return self._render(
            "cv_bullet_selection", values,
            lambda: _configured_selection_prompt(
                self.fallback, base_tex, job, profile
            ),
        )

    def compiler_repair(self, tex_source: str, error_excerpt: str) -> str:
        return self._render(
            "compiler_repair",
            {"tex_source": tex_source, "compiler_errors": error_excerpt},
            lambda: self.fallback.compiler_repair(tex_source, error_excerpt),
        )


class DefaultSectionProvider:
    def __init__(self, path: str):
        self.path = path

    def load(self) -> tuple:
        from .digest.section_config import load_sections
        return load_sections(self.path)


class DefaultOutputRenderer:
    kind = "telegram"

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
        *, prompts: PromptSet = None, compiler: CVCompiler = None,
    ):
        self.settings = settings
        self.profile = profile
        self.prompts = prompts or DefaultPromptSet()
        self.compiler = compiler or LatexCompiler(
            prompts=self.prompts, profile=self.profile
        )

    def render_tailored(self, llm: LLMService, job: object, evaluation: object = None) -> CVArtifact:
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
    accepted_renderer_kinds = ("telegram",)
    accepted_media_types = ("application/pdf", "application/zip")
    cv_mode = "required"
    requires_telegram_credentials = True

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


def default_components(
    settings: object,
    *,
    prompts: PromptSet = None,
    llm: LLMService = None,
    evaluator: JobEvaluator = None,
    telegram: object = None,
) -> Components:
    """Construct the side-effect-free built-in object graph for ``settings``."""
    from .llm.clients import LLMClient
    from .notify.telegram import TelegramClient

    prompts = prompts or DefaultPromptSet()
    llm = llm or LLMClient.from_config(settings)
    telegram = telegram or TelegramClient(
        settings.telegram_bot_token, settings.telegram_chat_id
    )
    profile = CandidateProfile(
        base_tex_path=getattr(settings, "base_tex_file", "igor_pivnyk_cv_base_updated.tex"),
        rendered_base_path=getattr(
            settings, "rendered_base_file", "igor_pivnyk_cv_base_updated.pdf"
        ),
    )
    return Components(
        prompts=prompts,
        llm=llm,
        candidate_filter=AllowAllCandidates(),
        evaluator=evaluator or DefaultJobEvaluator(prompts),
        profile=profile,
        cv_renderer=DefaultCVRenderer(settings, profile, prompts=prompts),
        section_provider=DefaultSectionProvider(getattr(settings, "sections_file", "sections.py")),
        output_renderer=DefaultOutputRenderer(),
        output_backend=DefaultOutputBackend(
            telegram, getattr(settings, "telegraph_access_token", "")
        ),
    )


__all__ = [
    "CVArtifact", "CVCompiler", "CVRenderer", "CandidateFilter",
    "CandidateProfile", "Components", "DefaultCVRenderer", "DefaultPromptSet",
    "DeliveryOutcome", "DigestOutcome", "FilePromptSet", "LatexCompiler",
    "JobEvaluator", "LLMProvider", "LLMService", "OutputBackend",
    "OutputRenderer", "PromptSet", "SectionProvider", "default_components",
]
