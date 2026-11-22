from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from job_search.components import CVArtifact, CandidateProfile, DefaultCVRenderer, LatexCompiler
from job_search.config import PipelineConfig
from job_search.digest.model import FitEntry, ReviewEntry
from job_search.latex import compile as compile_mod
from job_search.latex.compile import CompileResult
from job_search.models import Job
from job_search.llm.tailor import tailor_resume


FIXTURE = Path(__file__).parent / "fixtures" / "fictional_candidate.tex"


def _base_tex():
    return FIXTURE.read_text(encoding="utf-8")


def _avery_profile(**overrides):
    values = {
        "display_name": "Avery Example",
        "base_tex_path": str(FIXTURE),
        "rendered_base_path": "avery_example_base.pdf",
        "cv_filename_prefix": "avery_example_cv",
        "employer_order": ("Example Labs", "Sample Systems"),
        "forbidden_claim_patterns": (),
    }
    values.update(overrides)
    return CandidateProfile(**values)


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
    profile = _avery_profile()
    renderer = DefaultCVRenderer(
        PipelineConfig(), profile, compiler=compiler
    )
    job = Job(
        title="iOS Engineer", company="Example Labs",
        description="Swift UIKit engineering role. " * 10,
    )

    artifact = renderer.render_tailored(SelectingLLM(), job)

    assert artifact == CVArtifact(
        "avery_example_cv_example_labs.pdf", "application/pdf", b"PDF"
    )
    assert compiler.sources and profile.validate_tex(compiler.sources[0]) == []


def test_default_base_renderer_uses_fail_fast_compile_and_validates_source():
    from job_search.pipeline.stages import CVPreparationError

    base_source = _base_tex()
    compiler = FailFastBaseCompiler(base_source)
    profile = _avery_profile()
    renderer = DefaultCVRenderer(PipelineConfig(), profile, compiler=compiler)

    artifact = renderer.render_base(SelectionLLM())

    assert artifact.content == b"BASE-PDF"
    assert compiler.base_calls == [base_source]

    unsafe = FailFastBaseCompiler(base_source + " banking")
    guarded_profile = _avery_profile(forbidden_claim_patterns=(r"banking",))
    unsafe_renderer = DefaultCVRenderer(PipelineConfig(), guarded_profile, compiler=unsafe)
    with pytest.raises(CVPreparationError, match="validation"):
        unsafe_renderer.render_base(SelectionLLM())


def test_tailored_renderer_resolves_one_immutable_job_page_limit():
    compiler = LimitRecordingCompiler(pages=2)
    profile = _avery_profile(max_pages_by_country={"DE": 2})
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
        PipelineConfig(), _avery_profile(max_pages_by_country={"DE": 2}),
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
        PipelineConfig(), _avery_profile(max_pages=2), compiler=compiler
    )

    artifact = renderer.render_base(SelectionLLM())

    assert artifact.content == b"BASE"
    assert [(kind, limit) for kind, limit, _source in compiler.calls] == [("base", 2)]


def test_concurrent_tailoring_keeps_each_jobs_resolved_limit():
    compiler = PerLimitCompiler()
    renderer = DefaultCVRenderer(
        PipelineConfig(),
        _avery_profile(max_pages_by_country={"DE": 2, "FR": 3}),
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
    out = tmp_path / "nested" / "configured-base.pdf"
    manifest = tmp_path / "rendered-path.txt"
    monkeypatch.setenv("JOB_SEARCH_RENDER_BASE_MANIFEST", str(manifest))
    settings = PipelineConfig(base_tex_file="configured.tex", rendered_base_file=str(out))
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


def test_render_base_command_rejects_missing_cv_paths(monkeypatch, capsys):
    from job_search.latex import render_base

    monkeypatch.setattr(
        render_base,
        "build_runtime",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not build runtime")),
    )

    assert render_base.main(PipelineConfig(base_tex_file="", rendered_base_file="")) == 1
    assert "base_tex_file" in capsys.readouterr().err


@pytest.mark.parametrize("failure", ("parent", "manifest"))
def test_render_base_command_reports_output_write_failures(monkeypatch, tmp_path, capsys, failure):
    from job_search.latex import render_base

    class Renderer:
        def render_base(self, llm=None):
            return CVArtifact("base.pdf", "application/pdf", b"PDF")

    monkeypatch.setattr(
        render_base,
        "build_runtime",
        lambda *_args, **_kwargs: SimpleNamespace(cv_renderer=Renderer(), llm=object()),
    )
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory", encoding="utf-8")
    output = blocked / "base.pdf" if failure == "parent" else tmp_path / "base.pdf"
    if failure == "manifest":
        manifest_directory = tmp_path / "manifest-directory"
        manifest_directory.mkdir()
        monkeypatch.setenv("JOB_SEARCH_RENDER_BASE_MANIFEST", str(manifest_directory))

    assert render_base.main(
        PipelineConfig(base_tex_file=str(FIXTURE), rendered_base_file=str(output))
    ) == 1
    assert "output write failed" in capsys.readouterr().err


@pytest.mark.parametrize(
    "configuration_error",
    ("base_tex_path does not name a readable file: missing.tex", "LaTeX executable is unavailable"),
)
def test_render_base_command_reports_runtime_configuration_errors(
    monkeypatch, tmp_path, capsys, configuration_error
):
    from job_search.config import ConfigurationError
    from job_search.latex import render_base

    monkeypatch.setattr(
        render_base,
        "build_runtime",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ConfigurationError(configuration_error)),
    )

    assert render_base.main(
        PipelineConfig(base_tex_file=str(FIXTURE), rendered_base_file=str(tmp_path / "base.pdf"))
    ) == 1
    error = capsys.readouterr().err
    assert "ERROR: base CV rendering failed" in error
    assert configuration_error in error


def test_render_base_command_output_path_overrides_default_renderers_profile(
    monkeypatch, tmp_path
):
    # Same regression guard, for the built-in DefaultCVRenderer: the settings
    # value wins even when the profile's own rendered_base_path disagrees.
    from job_search.latex import render_base

    out = tmp_path / "ada-base.pdf"
    settings = PipelineConfig(base_tex_file=str(FIXTURE), rendered_base_file=str(out))
    profile = _avery_profile(
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

    assert render_base.main(
        PipelineConfig(base_tex_file=str(FIXTURE), rendered_base_file=str(output))
    ) == 0

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
        PipelineConfig(), _avery_profile(), compiler=FailingCompiler()
    )

    with pytest.raises(CVPreparationError):
        renderer.render_tailored(SelectingLLM(), _tailorable_job())


def test_cv_renderer_revalidates_compiler_repair_output():
    """A repair pass that adds a fabricated claim must not reach delivery."""
    from job_search.pipeline.stages import CVPreparationError

    profile = _avery_profile(forbidden_claim_patterns=(r"banking",))
    clean = _base_tex()
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
        PipelineConfig(), _avery_profile(), compiler=compiler
    )

    artifact = renderer.render_tailored(
        SelectingLLM(),
        {"title": "iOS", "company": "Example Labs", "description": "Swift role. " * 40},
    )

    assert artifact.filename == "avery_example_cv_example_labs.pdf"
    assert compiler.sources and _avery_profile().validate_tex(compiler.sources[0]) == []

    # A posting with no company still yields a usable name, and the name does
    # not depend on the URL (it used to carry a hash of it).
    unnamed = renderer.render_tailored(
        SelectingLLM(),
        {"title": "iOS", "company": "", "url": "https://x/1?ref=2",
         "description": "Swift role. " * 40},
    )
    assert unnamed.filename == "avery_example_cv_unknown.pdf"


def test_tailor_resume_canonicalizes_its_job_argument(monkeypatch):
    """The Job-shape guarantee prepare_fit used to provide now lives here."""
    received = []
    monkeypatch.setattr(
        "job_search.llm.tailor.select_cv_bullets",
        lambda llm, base, job, profile, prompts=None: received.append(job) or {},
    )

    tailor_resume(
        SelectingLLM(), "instr", _base_tex(),
        {"title": "iOS", "company": "Acme", "description": "Swift role. " * 40},
        _avery_profile(),
    )

    assert isinstance(received[0], Job)


# --- optional CV sections -------------------------------------------------

OPTIONAL_BLOCK = (
    "%% OPTIONAL-SECTION-BEGIN: Side Project\n"
    "\\cvsection{Side Project}\n"
    "\\begin{itemize}\n"
    "  \\item Built Recipe Finder search with ranked ingredient matching.\n"
    "  \\item Published Recipe Finder as an open-source command line tool.\n"
    "\\end{itemize}\n"
    "%% OPTIONAL-SECTION-END\n"
)
PICK_OPTIONAL = (
    '{"jobs": [{"company": "Example Labs", "keep_bullets": [0]}], '
    '"optional_sections": [{"name": "Side Project", "keep_bullets": [1, 0]}]}'
)


def _optional_profile(tmp_path, **overrides):
    path = tmp_path / "base_with_optional.tex"
    path.write_text(
        _base_tex().replace("\\cvsection{Skills}", OPTIONAL_BLOCK + "\n\\cvsection{Skills}"),
        encoding="utf-8",
    )
    return _avery_profile(base_tex_path=str(path), **overrides)


class _FixedReplyLLM:
    def __init__(self, reply):
        self.reply = reply

    def generate(self, prompt, **kwargs):
        return self.reply

    def usage_summary(self):
        return "usage"


class _ScriptedCompiler:
    executable = "fake"

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def compile(self, llm, tex_source, max_attempts=3, *, max_pages=1):
        self.calls.append((tex_source, max_pages))
        make = self.outcomes.pop(0)
        return make(tex_source)


def _overflow(pages=2):
    return lambda src: CompileResult(False, None, "too long", pages, False, src)


def _failure(pages=None):
    return lambda src: CompileResult(False, None, "latex error", pages, True, src)


def _success(content=b"PDF", pages=1):
    return lambda src: CompileResult(True, content, "", pages, False, src)


_JOB = Job(title="Engineer", company="Example Labs", description="Python role. " * 10)


def test_overflow_with_the_optional_section_retries_without_it_and_delivers_that(tmp_path):
    compiler = _ScriptedCompiler(_overflow(2), _success(b"WITHOUT"))
    renderer = DefaultCVRenderer(PipelineConfig(), _optional_profile(tmp_path), compiler=compiler)

    artifact = renderer.render_tailored(_FixedReplyLLM(PICK_OPTIONAL), _JOB)

    assert artifact.content == b"WITHOUT"
    assert len(compiler.calls) == 2
    with_optional, without = (source for source, _limit in compiler.calls)
    assert "Published Recipe Finder" in with_optional
    assert "Recipe Finder" not in without
    assert "OPTIONAL-SECTION" not in with_optional + without
    assert [limit for _source, limit in compiler.calls] == [1, 1]


def test_optional_section_that_fits_is_delivered_after_a_single_compile(tmp_path):
    compiler = _ScriptedCompiler(_success(b"WITH"))
    renderer = DefaultCVRenderer(PipelineConfig(), _optional_profile(tmp_path), compiler=compiler)

    artifact = renderer.render_tailored(_FixedReplyLLM(PICK_OPTIONAL), _JOB)

    assert artifact.content == b"WITH"
    assert len(compiler.calls) == 1
    assert "Published Recipe Finder" in compiler.calls[0][0]


@pytest.mark.parametrize("pages", [None, 1])
def test_non_overflow_failure_does_not_trigger_the_retry(tmp_path, pages):
    from job_search.pipeline.stages import CVPreparationError

    compiler = _ScriptedCompiler(_failure(pages), _success())
    renderer = DefaultCVRenderer(PipelineConfig(), _optional_profile(tmp_path), compiler=compiler)

    with pytest.raises(CVPreparationError, match="latex error"):
        renderer.render_tailored(_FixedReplyLLM(PICK_OPTIONAL), _JOB)

    assert len(compiler.calls) == 1


def test_overflow_is_judged_against_the_jobs_resolved_page_limit(tmp_path):
    profile = _optional_profile(tmp_path, max_pages=2)
    at_limit = _ScriptedCompiler(_failure(2), _success())
    above_limit = _ScriptedCompiler(_overflow(3), _success(b"WITHOUT"))

    from job_search.pipeline.stages import CVPreparationError

    with pytest.raises(CVPreparationError):
        DefaultCVRenderer(PipelineConfig(), profile, compiler=at_limit).render_tailored(
            _FixedReplyLLM(PICK_OPTIONAL), _JOB
        )
    artifact = DefaultCVRenderer(PipelineConfig(), profile, compiler=above_limit).render_tailored(
        _FixedReplyLLM(PICK_OPTIONAL), _JOB
    )

    assert len(at_limit.calls) == 1
    assert artifact.content == b"WITHOUT"
    assert [limit for _source, limit in above_limit.calls] == [2, 2]


def test_overflow_of_the_fallback_candidate_too_fails_after_two_compiles(tmp_path):
    from job_search.pipeline.stages import CVPreparationError

    compiler = _ScriptedCompiler(_overflow(2), _overflow(2))
    renderer = DefaultCVRenderer(PipelineConfig(), _optional_profile(tmp_path), compiler=compiler)

    with pytest.raises(CVPreparationError):
        renderer.render_tailored(_FixedReplyLLM(PICK_OPTIONAL), _JOB)

    assert len(compiler.calls) == 2


@pytest.mark.parametrize(
    "reply",
    [
        '{"jobs": [{"company": "Example Labs", "keep_bullets": [0]}]}',
        '{"jobs": [{"company": "Example Labs", "keep_bullets": [0]}], '
        '"optional_sections": [{"name": "Side Project", "keep_bullets": []}]}',
        "garbage",
    ],
)
def test_no_optional_selection_means_exactly_one_compile_even_on_overflow(tmp_path, reply):
    from job_search.pipeline.stages import CVPreparationError

    compiler = _ScriptedCompiler(_overflow(2), _success())
    renderer = DefaultCVRenderer(PipelineConfig(), _optional_profile(tmp_path), compiler=compiler)

    with pytest.raises(CVPreparationError):
        renderer.render_tailored(_FixedReplyLLM(reply), _JOB)

    assert len(compiler.calls) == 1
    assert "Recipe Finder" not in compiler.calls[0][0]


def test_base_without_markers_compiles_once_per_tailoring():
    compiler = _ScriptedCompiler(_success())
    renderer = DefaultCVRenderer(PipelineConfig(), _avery_profile(), compiler=compiler)

    renderer.render_tailored(_FixedReplyLLM(PICK_OPTIONAL), _JOB)

    assert len(compiler.calls) == 1


def test_render_base_compiles_the_base_with_optional_sections_stripped(tmp_path):
    compiler = FailFastBaseCompiler(_base_tex())
    renderer = DefaultCVRenderer(PipelineConfig(), _optional_profile(tmp_path), compiler=compiler)

    artifact = renderer.render_base(SelectionLLM())

    assert artifact.content == b"BASE-PDF"
    assert compiler.base_calls == [_base_tex()]


def test_render_base_strips_optional_sections_on_the_plain_compile_path_too(tmp_path):
    compiler = SuccessfulCompiler()
    renderer = DefaultCVRenderer(PipelineConfig(), _optional_profile(tmp_path), compiler=compiler)

    renderer.render_base(SelectionLLM())

    assert compiler.sources == [_base_tex()]
