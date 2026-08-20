"""Optional composition example for the job-search pipeline.

Copy this file to ``job_search_config.py`` or point
``JOB_SEARCH_CONFIG_FILE`` at it. It is trusted Python, so keep credentials in
environment variables and use this module only to assemble components.

With none of the example-specific variables set, ``configure`` returns the
built-in graph unchanged. This makes the file safe to import in tests and easy
to adopt one feature at a time.
"""
from dataclasses import replace
import os
from pathlib import Path

from job_search.components import (
    DefaultCVRenderer,
    DefaultJobEvaluator,
    FilePromptSet,
    LatexCompiler,
)
from job_search.output import FilesystemOutputBackend, HtmlOutputRenderer


def _optional(value):
    return str(value or "").strip()


def configure(defaults, settings):
    """Overlay only the example features explicitly selected in the environment."""
    changes = {}

    prompt_dir = _optional(os.environ.get("JOB_SEARCH_PROMPT_DIR"))
    prompts = defaults.prompts
    if prompt_dir:
        revision = _optional(os.environ.get("JOB_SEARCH_PROMPT_REVISION"))
        if not revision:
            raise ValueError(
                "JOB_SEARCH_PROMPT_REVISION is required with JOB_SEARCH_PROMPT_DIR"
            )
        directory = Path(prompt_dir)
        prompts = FilePromptSet(
            revision=revision,
            fact_extraction_file=str(directory / "fact_extraction.txt"),
            job_summary_file=str(directory / "job_summary.txt"),
            cv_bullet_selection_file=str(directory / "cv_bullet_selection.txt"),
            compiler_repair_file=str(directory / "compiler_repair.txt"),
        )
        changes["prompts"] = prompts
        changes["evaluator"] = DefaultJobEvaluator(prompts)

    profile = defaults.profile
    profile_name = _optional(os.environ.get("JOB_SEARCH_PROFILE_NAME"))
    filename_prefix = _optional(os.environ.get("JOB_SEARCH_CV_FILENAME_PREFIX"))
    if profile_name or filename_prefix:
        profile = replace(
            profile,
            display_name=profile_name or profile.display_name,
            cv_filename_prefix=filename_prefix or profile.cv_filename_prefix,
            revision="example-profile-v1",
        )
        changes["profile"] = profile

    latex_executable = _optional(os.environ.get("JOB_SEARCH_LATEX_EXECUTABLE"))
    if prompts is not defaults.prompts or profile is not defaults.profile or latex_executable:
        compiler = LatexCompiler(
            executable=latex_executable or "pdflatex",
            prompts=prompts,
            profile=profile,
        )
        changes["cv_renderer"] = DefaultCVRenderer(
            settings,
            profile,
            prompts=prompts,
            compiler=compiler,
        )

    output_dir = _optional(os.environ.get("JOB_SEARCH_OUTPUT_DIR"))
    if output_dir:
        cv_mode = _optional(os.environ.get("JOB_SEARCH_OUTPUT_CV_MODE")) or "required"
        changes["output_renderer"] = HtmlOutputRenderer()
        changes["output_backend"] = FilesystemOutputBackend(output_dir, cv_mode=cv_mode)

    return replace(defaults, **changes) if changes else defaults
