"""Tests for scripts/convert_engine.py (the Job Hunter Desktop engine-import converter)."""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
SPEC = importlib.util.spec_from_file_location("convert_engine", ROOT / "scripts" / "convert_engine.py")
convert_engine = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(convert_engine)

FAKE_SECRET = "fictional-secret-0123456789"


def _full(**kwargs):
    return convert_engine.convert(
        ROOT / "job_search.example.toml",
        seen=FIXTURES / "engine_import_seen_jobs.json",
        state=FIXTURES / "engine_import_job_state.json",
        **kwargs,
    )


def test_example_config_maps_values_and_lists_the_rest():
    result = _full()
    assert result["format"] == "jobhunter-engine-import"
    assert result["version"] == 1
    assert result["name"] == "job_search.example"
    assert result["values"]["search"]["search_terms"] == ["platform engineer", "backend engineer"]
    assert result["values"]["candidate"]["max_pages_by_country"] == {"DE": 2, "FR": 2}
    assert result["values"]["policy"]["check_order"][0] == "language"
    assert result["values"]["search"]["skill_include_groups"] == [["python", "go"], ["kubernetes"]]
    assert "jobspy" not in result["sources"] and "source.jobspy" in result["unsupported"]
    assert "settings.sections_file" in result["unsupported"]
    assert "settings.version" not in result["unsupported"]
    assert result["unsupported"] == sorted(result["unsupported"])
    assert result["criteria"].startswith("# ")
    assert result["tex"].lstrip().startswith("\\documentclass")


def test_history_drops_markers_and_jobs_keep_read_state():
    result = _full()
    assert result["history"]["format"] == "jobhunter-history"
    assert not any(key.startswith(("eval:", "delivery:", "deferred:")) for key in result["history"]["seen"])
    assert len(result["history"]["seen"]) == 3
    by_url = {job["url"]: job for job in result["jobs"]}
    assert by_url["https://jobs.example.test/listing/101"]["seen"] is True
    assert by_url["https://careers.sample.test/jobs/backend-7"]["seen"] is False
    assert all("description" not in job for job in result["jobs"])


def test_minimal_config_uses_engine_defaults_and_skips_missing_cv():
    result = convert_engine.convert(FIXTURES / "engine_import_minimal.toml")
    assert result["values"]["search"]["max_age_days"] == 30
    assert result["values"]["settings"]["llm_primary_scheme"] == "anthropic"
    assert result["tex"] is None and result["criteria"] is None
    assert result["history"] is None and result["jobs"] == []
    assert result["unsupported"] == ["settings.sections_file", "source.jobspy", "source.linkedin-global"]


def test_output_never_holds_a_credential(tmp_path, monkeypatch):
    config = tmp_path / "job_search.toml"
    config.write_text(
        "[settings]\nversion = 1\n"
        f'telegram_bot_token = "{FAKE_SECRET}"\n'
        f'llm_primary_api_key = "{FAKE_SECRET}"\n'
        "[search]\nsearch_terms = [\"x\"]\n"
        f'[extra]\nanything = "{FAKE_SECRET}"\n',
        encoding="utf-8",
    )
    for name in ("LLM_PRIMARY_API_KEY", "ANTHROPIC_API_KEY", "JEV_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        monkeypatch.setenv(name, FAKE_SECRET)
    out = tmp_path / "out.json"
    assert convert_engine.main(["--config", str(config), "--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert FAKE_SECRET not in text
    result = json.loads(text)
    assert "settings.telegram_bot_token" in result["unsupported"]
    assert "settings.llm_primary_api_key" in result["unsupported"]
    assert "extra.anything" in result["unsupported"]


def test_named_files_must_exist(tmp_path):
    with pytest.raises(convert_engine.ConvertError):
        convert_engine.convert(FIXTURES / "engine_import_minimal.toml", tex=tmp_path / "missing.tex")


def test_bad_settings_value_is_an_error(tmp_path):
    config = tmp_path / "job_search.toml"
    config.write_text("[settings]\nversion = 1\n[search]\nmax_age_days = \"soon\"\n", encoding="utf-8")
    assert convert_engine.main(["--config", str(config), "--out", str(tmp_path / "out.json")]) == 1
    assert not (tmp_path / "out.json").exists()


def test_output_is_deterministic(tmp_path):
    first, second = tmp_path / "a.json", tmp_path / "b.json"
    args = ["--config", str(ROOT / "job_search.example.toml"),
            "--seen", str(FIXTURES / "engine_import_seen_jobs.json"),
            "--state", str(FIXTURES / "engine_import_job_state.json")]
    assert convert_engine.main(args + ["--out", str(first)]) == 0
    assert convert_engine.main(args + ["--out", str(second)]) == 0
    assert first.read_bytes() == second.read_bytes()
