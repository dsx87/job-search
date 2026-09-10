"""A non-personal candidate traverses configured search, evaluation, and CV work."""
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from job_search.components import CandidateProfile, DefaultCVRenderer
from job_search.config import CandidateConfig, PipelineConfig, SearchConfig
from job_search.filters import run_pipeline
from job_search.llm.eval import evaluate_job
from job_search.llm.tailor import tailor_resume
from job_search.models import Job


FIXTURE = Path(__file__).parent / "fixtures" / "fictional_candidate.tex"


class BoundedFakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []

    def generate(self, prompt, **kwargs):
        self.prompts.append((prompt, kwargs))
        if not self.responses:
            raise AssertionError("fictional flow made an unexpected LLM request")
        return self.responses.pop(0)


def _search():
    return SearchConfig(
        role_include_terms=("platform engineer",),
        skill_include_groups=(("python",), ("terraform",)),
        remote_allowed=True,
        relocation_allowed=False,
    )


def _candidate(base_tex_path):
    return CandidateProfile(
        display_name="Avery Example",
        base_tex_path=str(base_tex_path),
        cv_filename_prefix="avery_example_cv",
        employer_order=("Example Labs", "Sample Systems"),
        forbidden_claim_patterns=(),
        max_pages=1,
    )


def _job():
    return Job(
        title="Platform Engineer",
        company="Harbor Works",
        location="Remote",
        is_remote=True,
        description=(
            "Harbor Works is hiring a fully remote Platform Engineer. "
            "You will build reliable Python services and Terraform infrastructure "
            "for a worldwide team."
        ),
    )


def _facts_response():
    return json.dumps(
        {
            "role_match": "yes",
            "matched_role_terms": ["platform engineer"],
            "matched_required_skills": ["python", "terraform"],
            "work_arrangement": "remote",
            "remote_geo_scope": "worldwide",
            "description_language": "english",
            "evidence": [
                {"field": "role_match", "snippet": "Platform Engineer"},
                {"field": "work_arrangement", "snippet": "fully remote"},
            ],
        }
    )


def _selection_response():
    return json.dumps(
        {
            "jobs": [
                {"company": "Example Labs", "keep_bullets": [0]},
                {"company": "Sample Systems", "keep_bullets": [1]},
            ]
        }
    )


def _filtered_and_evaluated_job():
    job = _job()
    filtered = run_pipeline([job], search=_search())
    assert filtered == [job]

    config = PipelineConfig.from_env()
    client = BoundedFakeLLM([_facts_response()])
    evaluation = evaluate_job(
        client,
        "unused compatibility criteria",
        job,
        search=_search(),
        candidate=CandidateConfig(residency_countries=("CA",)),
        policy=config.policy,
    )
    assert evaluation["verdict"] == "fit"
    assert evaluation["facts"]["matched_required_skills"] == ["python", "terraform"]
    assert client.responses == []
    return job


def test_fictional_candidate_flow_is_independent_of_the_personal_cv():
    job = _filtered_and_evaluated_job()
    profile = _candidate(FIXTURE)
    client = BoundedFakeLLM([_selection_response()])

    tailored = tailor_resume(
        client,
        "compatibility instructions",
        FIXTURE.read_text(encoding="utf-8"),
        job,
        profile,
    )

    assert profile.validate_tex(tailored) == []
    assert "Example Labs" in tailored
    assert "Sample Systems" in tailored
    assert "Implemented release automation" in tailored
    assert "Introduced reproducible infrastructure" in tailored
    assert "Legacy monitoring cleanup" not in tailored
    assert client.responses == []


@pytest.mark.requires_pdflatex
@pytest.mark.skipif(shutil.which("pdflatex") is None, reason="pdflatex is not installed")
def test_fictional_candidate_flow_renders_a_real_pdf(tmp_path):
    job = _filtered_and_evaluated_job()
    instructions = tmp_path / "instructions.md"
    instructions.write_text("## STEP 3\nunused\n## BASE LaTeX TEMPLATE\n", encoding="utf-8")
    profile = _candidate(FIXTURE)
    renderer = DefaultCVRenderer(
        SimpleNamespace(cv_tailoring_prompt_file=str(instructions), latex_engine="pdflatex"),
        profile,
    )

    artifact = renderer.render_tailored(BoundedFakeLLM([_selection_response()]), job)

    assert artifact.filename == "avery_example_cv_harbor_works.pdf"
    assert artifact.content.startswith(b"%PDF")
