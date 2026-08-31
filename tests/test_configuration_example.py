"""The shipped composition example is executable documentation."""
import importlib.util
from pathlib import Path

from job_search.components import Components, default_components
from job_search.composition import load_components, validate_components
from job_search.config import PipelineConfig


EXAMPLE = Path(__file__).resolve().parents[1] / "job_search_config.example.py"


def _load_example():
    spec = importlib.util.spec_from_file_location("job_search_config_example", EXAMPLE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_configuration_example_imports_and_preserves_defaults(monkeypatch):
    for name in (
        "JOB_SEARCH_OUTPUT_DIR",
        "JOB_SEARCH_OUTPUT_CV_MODE",
        "JOB_SEARCH_PROMPT_DIR",
        "JOB_SEARCH_PROMPT_REVISION",
        "JOB_SEARCH_PROFILE_NAME",
        "JOB_SEARCH_CV_FILENAME_PREFIX",
        "JOB_SEARCH_LATEX_EXECUTABLE",
    ):
        monkeypatch.delenv(name, raising=False)
    settings = PipelineConfig(llm_primary_api_key="test-key")
    defaults = default_components(settings)

    configured = _load_example().configure(defaults, settings)

    assert isinstance(configured, Components)
    assert configured is defaults
    validate_components(configured, settings, command="check")


def test_noop_configuration_example_returns_the_builtin_graph(monkeypatch):
    for name in (
        "JOB_SEARCH_OUTPUT_DIR",
        "JOB_SEARCH_OUTPUT_CV_MODE",
        "JOB_SEARCH_PROMPT_DIR",
        "JOB_SEARCH_PROMPT_REVISION",
        "JOB_SEARCH_PROFILE_NAME",
        "JOB_SEARCH_CV_FILENAME_PREFIX",
        "JOB_SEARCH_LATEX_EXECUTABLE",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("JOB_SEARCH_CONFIG_FILE", str(EXAMPLE))

    configured = load_components(PipelineConfig(), command="check")

    builtin = default_components(PipelineConfig())
    assert {
        name: type(getattr(configured, name))
        for name in Components.__dataclass_fields__
    } == {
        name: type(getattr(builtin, name))
        for name in Components.__dataclass_fields__
    }


def test_configuration_example_can_select_filesystem_text_only_output(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("JOB_SEARCH_OUTPUT_DIR", str(tmp_path / "digest"))
    monkeypatch.setenv("JOB_SEARCH_OUTPUT_CV_MODE", "disabled")
    settings = PipelineConfig(llm_primary_api_key="test-key")

    configured = _load_example().configure(default_components(settings), settings)

    assert configured.output_renderer.kind == "html"
    assert configured.output_backend.cv_mode == "disabled"
    assert configured.output_backend.directory == str(tmp_path / "digest")
    validate_components(configured, settings, command="daily")
