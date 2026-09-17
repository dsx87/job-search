from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor

import pytest

from job_search.components import CVArtifact, CandidateProfile, DefaultCVRenderer, LatexCompiler
from job_search.config import PipelineConfig, load_base_tex
from job_search.digest.model import FitEntry, ReviewEntry
from job_search.latex import compile as compile_mod
from job_search.latex.compile import CompileResult
from job_search.models import Job
from job_search.llm.tailor import tailor_resume


class SelectingLLM:
    def generate(self, prompt, **kwargs):
        return '{"jobs": []}'

    def usage_summary(self):
        return "usage"


class SuccessfulCompiler:
    executable = "fake"

    def __init__(self):
        self.sources = []

    def compile(self, llm, tex_source, max_attempts=3, *, max_pages=1):
        self.sources.append(tex_source)
        return CompileResult(True, b"PDF", "", 1, False, tex_source)


class FailFastBaseCompiler:
    executable = "fake"

    def __init__(self, tex_source):
        self.tex_source = tex_source
        self.base_calls = []

    def compile_base(self, source, *, max_pages=1):
        self.base_calls.append(source)
        return CompileResult(True, b"BASE-PDF", "", 1, False, self.tex_source)

    def compile(self, llm, tex_source, max_attempts=3, *, max_pages=1):
        raise AssertionError("base rendering must not enter repair/shrink compilation")


class LimitRecordingCompiler:
    executable = "fake"

    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def compile(self, llm, tex_source, max_attempts=3, *, max_pages=1):
        self.calls.append(("tailored", max_pages, tex_source))
        return CompileResult(True, b"PDF", "", self.pages, False, tex_source)

    def compile_base(self, tex_source, *, max_pages=1):
        self.calls.append(("base", max_pages, tex_source))
        return CompileResult(True, b"BASE", "", self.pages, False, tex_source)


class PerLimitCompiler:
    executable = "fake"

    def __init__(self):
        self.calls = []

    def compile(self, llm, tex_source, max_attempts=3, *, max_pages=1):
        self.calls.append(max_pages)
        return CompileResult(True, b"PDF", "", max_pages, False, tex_source)


class SelectionLLM:
    def generate(self, prompt, **kwargs):
        assert "Example Labs" in prompt
        return '{"jobs": [{"company": "Example Labs", "keep_bullets": [1]}]}'

    def usage_summary(self):
        return "usage"


def test_candidate_profile_drives_validation_and_private_placeholders():
    profile = CandidateProfile(
        display_name="Ada Example",
        employer_order=("First Co", "Second Co"),
        forbidden_claim_patterns=(r"forbidden claim",),
        private_placeholders={"((EMAIL))": "PRIVATE_EMAIL"},
    )
    tex = (
        "\\jobheader{First Co}\\jobheader{Second Co} "
        "Reach me at ((EMAIL))"
    )

    assert profile.validate_tex(tex) == []
    assert profile.resolve_private_placeholders(
        tex, {"PRIVATE_EMAIL": "ada@example.com"}
    ).endswith("ada@example.com")
    assert profile.validate_tex(tex + " forbidden claim") == [
        "forbidden term present: 'forbidden claim'"
    ]


def test_candidate_profile_employers_drive_deterministic_bullet_selection():
    profile = CandidateProfile(
        display_name="Ada Example",
        employer_order=("Example Labs",),
        forbidden_claim_patterns=(),
    )
    base_tex = r"""\documentclass{article}
\newcommand{\jobheader}[4]{#1 #2 #3 #4}
\begin{document}
\jobheader{Example Labs}{Engineer}{Remote}{2024--Present}
\begin{itemize}
  \item First project
  \item Second project
\end{itemize}
\end{document}
"""

    rendered = tailor_resume(
        SelectionLLM(),
        "compatibility instructions",
        base_tex,
        Job(title="Engineer", company="Acme", description="Swift role"),
        profile=profile,
    )

    assert "Second project" in rendered
    assert "First project" not in rendered
    assert profile.validate_tex(rendered) == []


def test_latex_compiler_uses_configured_executable_for_both_passes(monkeypatch, tmp_path):
    calls = []

    def run(cmd, capture_output, timeout):
        calls.append(cmd)
        out_dir = cmd[cmd.index("-output-directory") + 1]
        from pathlib import Path
        Path(out_dir, "cv.pdf").write_bytes(b"PDF")
        Path(out_dir, "cv.log").write_text(
            "Output written on cv.pdf (1 page, 3 bytes).", encoding="utf-8"
        )
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(compile_mod.subprocess, "run", run)
    compiler = LatexCompiler(executable="xelatex")

    result = compiler.compile(SelectingLLM(), "\\documentclass{x}\\begin{document}x\\end{document}")

    assert result.ok is True
    assert [command[0] for command in calls] == ["xelatex", "xelatex"]
    assert result.tex_source.startswith("\\documentclass")


def test_default_cv_renderer_returns_profile_named_artifact():
    compiler = SuccessfulCompiler()
    profile = CandidateProfile(
        display_name="Ada Example",
        base_tex_path="igor_pivnyk_cv_base_updated.tex",
        cv_filename_prefix="ada_example_cv",
    )
    renderer = DefaultCVRenderer(
        PipelineConfig(), profile, compiler=compiler
    )
    job = Job(
        title="iOS Engineer", company="Example Labs",
        description="Swift UIKit engineering role. " * 10,
    )

    artifact = renderer.render_tailored(SelectingLLM(), job)

    assert artifact == CVArtifact(
        "ada_example_cv_example_labs.pdf", "application/pdf", b"PDF"
    )
    assert compiler.sources and profile.validate_tex(compiler.sources[0]) == []


def test_default_base_renderer_uses_fail_fast_compile_and_validates_source():
    from job_search.pipeline.stages import CVPreparationError

    base_source = load_base_tex()
    compiler = FailFastBaseCompiler(base_source)
    profile = CandidateProfile()
    renderer = DefaultCVRenderer(PipelineConfig(), profile, compiler=compiler)

    artifact = renderer.render_base(SelectionLLM())

    assert artifact.content == b"BASE-PDF"
    assert compiler.base_calls == [base_source]

    unsafe = FailFastBaseCompiler(base_source + " banking")
    unsafe_renderer = DefaultCVRenderer(PipelineConfig(), profile, compiler=unsafe)
    with pytest.raises(CVPreparationError, match="validation"):
        unsafe_renderer.render_base(SelectionLLM())


def test_tailored_renderer_resolves_one_immutable_job_page_limit():
    compiler = LimitRecordingCompiler(pages=2)
    profile = CandidateProfile(max_pages_by_country={"DE": 2})
    renderer = DefaultCVRenderer(PipelineConfig(), profile, compiler=compiler)

    artifact = renderer.render_tailored(
        SelectingLLM(),
        Job(title="iOS", company="Acme", location="Germany", description="Swift role. " * 20),
    )

    assert artifact.content == b"PDF"
    assert [(kind, limit) for kind, limit, _source in compiler.calls] == [("tailored", 2)]


def test_renderer_rejects_a_successful_result_above_the_resolved_limit():
    from job_search.pipeline.stages import CVPreparationError

    renderer = DefaultCVRenderer(
        PipelineConfig(), CandidateProfile(max_pages_by_country={"DE": 2}),
        compiler=LimitRecordingCompiler(pages=3),
    )

    with pytest.raises(CVPreparationError, match="within 2 pages"):
        renderer.render_tailored(
            SelectingLLM(),
            Job(title="iOS", company="Acme", location="Germany", description="Swift role. " * 20),
        )


def test_base_renderer_uses_fallback_limit_without_repair_or_shrink():
    compiler = LimitRecordingCompiler(pages=2)
    renderer = DefaultCVRenderer(
        PipelineConfig(), CandidateProfile(max_pages=2), compiler=compiler
    )

    artifact = renderer.render_base(SelectionLLM())

    assert artifact.content == b"BASE"
    assert [(kind, limit) for kind, limit, _source in compiler.calls] == [("base", 2)]


def test_concurrent_tailoring_keeps_each_jobs_resolved_limit():
    compiler = PerLimitCompiler()
    renderer = DefaultCVRenderer(
        PipelineConfig(),
        CandidateProfile(max_pages_by_country={"DE": 2, "FR": 3}),
        compiler=compiler,
    )
    jobs = (
        Job(title="iOS", company="Berlin", location="Germany", description="Swift role. " * 20),
        Job(title="iOS", company="Paris", location="France", description="Swift role. " * 20),
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        artifacts = list(pool.map(lambda job: renderer.render_tailored(SelectingLLM(), job), jobs))

    assert [artifact.content for artifact in artifacts] == [b"PDF", b"PDF"]
    assert sorted(compiler.calls) == [2, 3]


def test_digest_entries_accept_generic_artifacts_and_keep_pdf_compatibility():
    artifact = CVArtifact("candidate.txt", "text/plain", b"hello")

    fit = FitEntry(job=Job(), evaluation=None, summary="", artifact=artifact)
    legacy = ReviewEntry(
        job=Job(), evaluation={}, pdf_bytes=b"PDF", cv_filename="legacy.pdf"
    )

    assert fit.artifact is artifact
    assert fit.cv_filename == "candidate.txt"
    assert fit.pdf_bytes == b"hello"
    assert legacy.artifact == CVArtifact("legacy.pdf", "application/pdf", b"PDF")


def test_render_base_command_writes_to_configured_output_path(monkeypatch, tmp_path):
    # Bug #4/#5 regression guard: OUT_PDF_FILE / rendered_base_file must be
    # honored regardless of the cv_renderer's type — the old code forked on
    # `type(...) is DefaultCVRenderer` and silently ignored the setting for
    # any other renderer, including this fully custom one.
    from job_search.latex import render_base

    monkeypatch.chdir(tmp_path)
    out = tmp_path / "configured-base.pdf"
    manifest = tmp_path / "rendered-path.txt"
    monkeypatch.setenv("JOB_SEARCH_RENDER_BASE_MANIFEST", str(manifest))
    settings = PipelineConfig(rendered_base_file=str(out))
    calls = []

    class Renderer:
        def render_base(self, llm=None):
            calls.append(llm)
            return CVArtifact("renderer-base.txt", "text/plain", b"CONFIGURED")

    llm = object()
    components = SimpleNamespace(cv_renderer=Renderer(), llm=llm)
    monkeypatch.setattr(render_base, "build_runtime", lambda *_a, **_k: components)

    assert render_base.main(settings) == 0
    assert out.read_bytes() == b"CONFIGURED"
    assert manifest.read_text(encoding="utf-8") == str(out) + "\n"
    assert calls == [llm]


def test_render_base_command_output_path_overrides_default_renderers_profile(
    monkeypatch, tmp_path
):
    # Same regression guard, for the built-in DefaultCVRenderer: the settings
    # value wins even when the profile's own rendered_base_path disagrees.
    from job_search.latex import render_base

    out = tmp_path / "ada-base.pdf"
    settings = PipelineConfig(rendered_base_file=str(out))
    profile = CandidateProfile(
        rendered_base_path=str(tmp_path / "ignored-profile-path.pdf")
    )
    renderer = DefaultCVRenderer(settings, profile, compiler=SuccessfulCompiler())
    renderer.render_base = lambda llm=None: CVArtifact(
        "ada-base.pdf", "application/pdf", b"ADA"
    )
    components = SimpleNamespace(cv_renderer=renderer, llm=object())
    monkeypatch.setattr(render_base, "build_runtime", lambda *_a, **_k: components)

    assert render_base.main(settings) == 0
    assert out.read_bytes() == b"ADA"


def test_render_base_reports_a_verified_pdf_without_claiming_one_page(monkeypatch, tmp_path, capsys):
    from job_search.latex import render_base

    output = tmp_path / "base.pdf"

    class Renderer:
        def render_base(self, llm=None):
            return CVArtifact("base.pdf", "application/pdf", b"PDF")

    monkeypatch.setattr(
        render_base,
        "build_runtime",
        lambda *_args, **_kwargs: SimpleNamespace(cv_renderer=Renderer(), llm=object()),
    )

    assert render_base.main(PipelineConfig(rendered_base_file=str(output))) == 0

    message = capsys.readouterr().out
    assert "1 page" not in message
    assert "verified PDF" in message


# --- behaviors that used to live on stages.prepare_fit -----------------------
# The tailoring/compilation/validation guarantees moved onto DefaultCVRenderer
# when the pipeline lost its second, legacy preparation path. They are asserted
# here against the renderer that now owns them.


class FailingCompiler:
    executable = "fake"

    def compile(self, llm, tex_source, max_attempts=3, *, max_pages=1):
        return CompileResult(False, None, "compilation failed", 0, False, tex_source)


class RepairingCompiler:
    """A compiler whose repair pass silently introduces a forbidden claim."""

    executable = "fake"

    def __init__(self, repaired):
        self.repaired = repaired

    def compile(self, llm, tex_source, max_attempts=3, *, max_pages=1):
        return CompileResult(True, b"PDF", "", 1, False, self.repaired)


def _tailorable_job():
    return Job(
        title="iOS Engineer",
        company="Acme",
        description="Swift UIKit engineering role. " * 10,
    )


def test_cv_renderer_requires_verified_pdf():
    from job_search.pipeline.stages import CVPreparationError

    renderer = DefaultCVRenderer(
        PipelineConfig(), CandidateProfile(), compiler=FailingCompiler()
    )

    with pytest.raises(CVPreparationError):
        renderer.render_tailored(SelectingLLM(), _tailorable_job())


def test_cv_renderer_revalidates_compiler_repair_output():
    """A repair pass that adds a fabricated claim must not reach delivery."""
    from job_search.pipeline.stages import CVPreparationError

    profile = CandidateProfile()
    clean = load_base_tex()
    repaired_with_false_claim = clean.replace(
        "\\end{document}", "Built consumer banking systems.\\end{document}"
    )
    renderer = DefaultCVRenderer(
        PipelineConfig(), profile,
        compiler=RepairingCompiler(repaired_with_false_claim),
    )

    with pytest.raises(CVPreparationError) as raised:
        renderer.render_tailored(SelectingLLM(), _tailorable_job())

    assert "banking" in str(raised.value)


def test_cv_renderer_accepts_a_raw_scraped_mapping():
    """The renderer takes whatever the scraper produced; canonicalization is
    tailor_resume's job (it calls coerce_job), so a plain dict must still yield
    a correctly named artifact rather than an attribute error."""
    compiler = SuccessfulCompiler()
    renderer = DefaultCVRenderer(
        PipelineConfig(), CandidateProfile(), compiler=compiler
    )

    artifact = renderer.render_tailored(
        SelectingLLM(),
        {"title": "iOS", "company": "Example Labs", "description": "Swift role. " * 40},
    )

    assert artifact.filename == "igor_pivnyk_cv_example_labs.pdf"
    assert compiler.sources and CandidateProfile().validate_tex(compiler.sources[0]) == []

    # A posting with no company still yields a usable name, and the name does
    # not depend on the URL (it used to carry a hash of it).
    unnamed = renderer.render_tailored(
        SelectingLLM(),
        {"title": "iOS", "company": "", "url": "https://x/1?ref=2",
         "description": "Swift role. " * 40},
    )
    assert unnamed.filename == "igor_pivnyk_cv_unknown.pdf"


def test_tailor_resume_canonicalizes_its_job_argument(monkeypatch):
    """The Job-shape guarantee prepare_fit used to provide now lives here."""
    received = []
    monkeypatch.setattr(
        "job_search.llm.tailor.select_cv_bullets",
        lambda llm, base, job, profile, prompts=None: received.append(job) or {},
    )

    tailor_resume(
        SelectingLLM(), "instr", load_base_tex(),
        {"title": "iOS", "company": "Acme", "description": "Swift role. " * 40},
        CandidateProfile(),
    )

    assert isinstance(received[0], Job)
