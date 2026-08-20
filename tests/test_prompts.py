import hashlib

import pytest

from job_search.components import (
    CandidateProfile,
    DefaultJobEvaluator,
    DefaultPromptSet,
    FilePromptSet,
)
from job_search.config import load_base_tex
from job_search.models import Job
from job_search.state.seen_jobs import criteria_version


def _job():
    return Job(
        title="Senior iOS Engineer",
        company="Acme",
        location="Berlin",
        description="Swift UIKit remote role. " * 20,
        is_remote=True,
    )


def _sha(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def test_default_prompt_text_is_byte_compatible_with_the_legacy_builders():
    prompts = DefaultPromptSet()
    job = _job()

    assert _sha(prompts.fact_extraction(job)) == (
        "9806ade23ae91d02676719ffbd6c777bb0816bee0dd392580a3345320ec9c099"
    )
    assert _sha(prompts.job_summary(job)) == (
        "d3a245365e747e426b44859319052e519abcd48b17dd9f6199825df506b45236"
    )
    assert _sha(prompts.cv_bullet_selection(load_base_tex(), job)) == (
        "d4fd277bb774b637c91dc8339bbbaa5f00392890f909ff3fa66ad8b05b5e14fe"
    )
    assert _sha(prompts.compiler_repair("\\documentclass{article}", "! Error")) == (
        "edfb0cdb1577e48e97aa638b13515d44c6dc048e2fa60a489db545009f817290"
    )


def test_file_prompt_set_substitutes_documented_placeholders(tmp_path):
    files = {}
    templates = {
        "fact_extraction": "$title|$company|$location|$is_remote|$description",
        "job_summary": "$title|$description",
        "cv_bullet_selection": "$resume_bullets|$company|$description",
        "compiler_repair": "$compiler_errors|$tex_source",
    }
    for name, template in templates.items():
        path = tmp_path / "{}.txt".format(name)
        path.write_text(template, encoding="utf-8")
        files[name + "_file"] = str(path)
    prompts = FilePromptSet(revision="my-prompts-v2", **files)

    assert prompts.fact_extraction(_job()).startswith("Senior iOS Engineer|Acme|Berlin|True|")
    assert prompts.job_summary(_job()).startswith("Senior iOS Engineer|")
    assert "Check Point" in prompts.cv_bullet_selection(load_base_tex(), _job())
    assert prompts.compiler_repair("TEX", "ERROR") == "ERROR|TEX"
    assert prompts.revision == "my-prompts-v2"


def test_file_prompt_set_requires_a_nonempty_revision(tmp_path):
    with pytest.raises(ValueError, match="revision"):
        FilePromptSet(revision="")


@pytest.mark.parametrize(
    "template, message",
    [
        ("Broken $! placeholder", "invalid"),
        ("Unknown $verdict placeholder", "unknown placeholder"),
    ],
)
def test_file_prompt_set_rejects_invalid_placeholders_at_load(
    tmp_path, template, message
):
    path = tmp_path / "facts.txt"
    path.write_text(template, encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        FilePromptSet(revision="bad-v1", fact_extraction_file=str(path))


def test_file_prompt_set_accepts_legacy_two_argument_cv_fallback():
    class LegacyFallback(DefaultPromptSet):
        def cv_bullet_selection(self, base_tex, job):
            return "legacy:{}:{}".format(base_tex, job.title)

    prompts = FilePromptSet(revision="wrapper-v1", fallback=LegacyFallback())

    assert prompts.cv_bullet_selection("BASE", _job(), CandidateProfile()) == (
        "legacy:BASE:Senior iOS Engineer"
    )


def test_default_evaluator_fingerprint_is_legacy_compatible_until_prompts_change():
    criteria = "Senior native Apple roles"

    assert DefaultJobEvaluator(DefaultPromptSet()).fingerprint(criteria) == criteria_version(criteria)

    custom = type("Prompts", (DefaultPromptSet,), {"revision": "prompts-v2"})()
    assert DefaultJobEvaluator(custom).fingerprint(criteria) != criteria_version(criteria)
