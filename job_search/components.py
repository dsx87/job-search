"""Public structural contracts for configuring the job-search pipeline.

Custom composition modules implement these protocols by shape; inheritance and
package discovery are deliberately unnecessary.  The concrete defaults live
here too so a configuration can selectively replace one component with
``dataclasses.replace(defaults, ...)``.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence, Tuple, runtime_checkable

from .profile import EXPECTED_JOB_ORDER, FORBIDDEN_TERM_PATTERNS


@runtime_checkable
class PromptSet(Protocol):
    revision: str

    def fact_extraction(self, job: object) -> str: ...
    def job_summary(self, job: object) -> str: ...
    def cv_bullet_selection(self, base_tex: str, job: object) -> str: ...
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
    def render_base(self) -> CVArtifact: ...


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
class DigestOutcome:
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
        notification_already_sent: bool = False,
    ) -> object: ...
    def deliver_digest(
        self, rendered: object, artifacts: Sequence[CVArtifact] = (), **context: object
    ) -> DigestOutcome: ...


@dataclass
class Components:
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

    def __init__(self, prompts: PromptSet = None):
        self.prompts = prompts

    def evaluate(self, llm: LLMService, criteria: str, job: object) -> dict:
        from .llm.eval import evaluate_job
        return evaluate_job(llm, criteria, job)

    def fingerprint(self, criteria: str) -> str:
        from .state.seen_jobs import criteria_version
        prompt_revision = getattr(self.prompts, "revision", "default-prompts-v1")
        return criteria_version("{}\n{}\n{}".format(criteria, self.revision, prompt_revision))


class DefaultPromptSet:
    """Default prompt facade; prompt bodies are supplied by the LLM modules."""

    revision = "default-prompts-v1"

    def fact_extraction(self, job: object) -> str:
        from .llm.facts import build_fact_extraction_prompt
        return build_fact_extraction_prompt(job)

    def job_summary(self, job: object) -> str:
        from .llm.summarize import build_job_summary_prompt
        return build_job_summary_prompt(job)

    def cv_bullet_selection(self, base_tex: str, job: object) -> str:
        from .llm.cv_edits import build_cv_bullet_selection_prompt
        return build_cv_bullet_selection_prompt(base_tex, job)

    def compiler_repair(self, tex_source: str, error_excerpt: str) -> str:
        from .latex.compile import build_compiler_repair_prompt
        return build_compiler_repair_prompt(tex_source, error_excerpt)


class DefaultSectionProvider:
    def __init__(self, path: str):
        self.path = path

    def load(self) -> tuple:
        from .digest.section_config import load_sections
        return load_sections(self.path)


class DefaultOutputRenderer:
    kind = "telegram"

    def render_notice(self, notice: object, **context: object) -> str:
        return str(notice)

    def render_fit(self, job: object, evaluation: object) -> str:
        from .pipeline.stages import _format_notification
        return _format_notification(job, evaluation or {})

    def render_digest(self, context: object) -> str:
        from .digest.render import render_digest_html
        return render_digest_html(context)


class DefaultCVRenderer:
    media_types = ("application/pdf",)

    def __init__(self, settings: object, profile: CandidateProfile):
        self.settings = settings
        self.profile = profile

    def render_tailored(self, llm: LLMService, job: object, evaluation: object = None) -> CVArtifact:
        from .config import load_base_tex, load_tailoring_instructions
        from .pipeline.stages import _company_slug, _prepare_verified_pdf
        base = load_base_tex(self.profile.base_tex_path)
        instructions = load_tailoring_instructions(self.settings.cv_tailoring_prompt_file)
        content = _prepare_verified_pdf(llm, instructions, base, job)
        company = getattr(job, "company", "") or (
            job.get("company", "") if hasattr(job, "get") else ""
        )
        return CVArtifact(
            "{}_{}.pdf".format(self.profile.cv_filename_prefix, _company_slug(company)),
            "application/pdf",
            content,
        )

    def render_base(self) -> CVArtifact:
        path = self.profile.rendered_base_path
        with open(path, "rb") as handle:
            return CVArtifact(os.path.basename(path), "application/pdf", handle.read())


class DefaultOutputBackend:
    accepted_renderer_kinds = ("telegram",)
    accepted_media_types = ("application/pdf", "application/zip")
    cv_mode = "required"
    requires_telegram_credentials = True

    def __init__(self, telegram: object):
        self.telegram = telegram

    def deliver_notice(self, rendered: object) -> object:
        return self.telegram.send_message(str(rendered))

    def deliver_fit(
        self, rendered: object, artifact: CVArtifact = None,
        notification_already_sent: bool = False,
    ) -> object:
        from .pipeline.stages import send_fit
        payload = {"title": "", "company": "", "message": rendered}
        if artifact is not None:
            payload["pdf_bytes"] = artifact.content
        return send_fit(payload, self.telegram, notification_already_sent)

    def deliver_digest(
        self, rendered: object, artifacts: Sequence[CVArtifact] = (), **context: object
    ) -> DigestOutcome:
        try:
            self.telegram.send_message(str(rendered))
        except Exception as exc:
            return DigestOutcome(False, error=exc)
        return DigestOutcome(True, notification_sent=True, cv_sent=len(artifacts))


def default_components(settings: object) -> Components:
    """Construct the side-effect-free built-in object graph for ``settings``."""
    from .llm.clients import LLMClient
    from .notify.telegram import TelegramClient

    prompts = DefaultPromptSet()
    profile = CandidateProfile(
        base_tex_path=getattr(settings, "base_tex_file", "igor_pivnyk_cv_base_updated.tex"),
        rendered_base_path=getattr(
            settings, "rendered_base_file", "igor_pivnyk_cv_base_updated.pdf"
        ),
    )
    return Components(
        prompts=prompts,
        llm=LLMClient.from_config(settings),
        candidate_filter=AllowAllCandidates(),
        evaluator=DefaultJobEvaluator(prompts),
        profile=profile,
        cv_renderer=DefaultCVRenderer(settings, profile),
        section_provider=DefaultSectionProvider(getattr(settings, "sections_file", "sections.py")),
        output_renderer=DefaultOutputRenderer(),
        output_backend=DefaultOutputBackend(
            TelegramClient(settings.telegram_bot_token, settings.telegram_chat_id)
        ),
    )


__all__ = [
    "CVArtifact", "CVCompiler", "CVRenderer", "CandidateFilter",
    "CandidateProfile", "Components", "DefaultPromptSet", "DigestOutcome",
    "JobEvaluator", "LLMProvider", "LLMService", "OutputBackend",
    "OutputRenderer", "PromptSet", "SectionProvider", "default_components",
]
