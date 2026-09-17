"""Configuration discovery stays usable before runtime setup on a fresh clone."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


def invoke(module, arguments, tmp_path):
    hook = tmp_path / 'explosive.py'
    hook.write_text("raise RuntimeError('trusted hook executed')\n")
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(('JOB_SEARCH_', 'CV_', 'LLM_', 'TELEGRAM_', 'GEMINI_', 'OPENAI_'))}
    env.update(PYTHONPATH=str(ROOT), JOB_SEARCH_CONFIG_FILE=str(hook),
               JOB_SEARCH_SETTINGS_FILE=str(tmp_path / 'absent.toml'))
    return subprocess.run([sys.executable, '-m', module] + arguments,
                          cwd=str(tmp_path), env=env, text=True, capture_output=True)


@pytest.mark.parametrize('module', ['job_search', 'job_search.pipeline'])
def test_describe_config_ignores_missing_selected_file_and_executable_hook(module, tmp_path):
    result = invoke(module, ['--describe-config'], tmp_path)
    assert result.returncode == 0, result.stderr
    metadata = json.loads(result.stdout)
    assert metadata['version'] == 1
    assert 'candidate.max_pages' in result.stdout
    assert 'trusted hook executed' not in result.stderr
    assert not (tmp_path / 'seen_jobs.json').exists()


@pytest.mark.parametrize('module', ['job_search', 'job_search.pipeline'])
def test_validate_explicit_path_is_data_only(module, tmp_path):
    config = tmp_path / 'valid.toml'
    config.write_text('[settings]\nversion = 1\n[candidate]\nmax_pages = 2\n')
    result = invoke(module, ['--validate-config', str(config)], tmp_path)
    assert result.returncode == 0, result.stderr
    assert 'valid' in result.stdout.lower()
    assert not (tmp_path / 'seen_jobs.json').exists()


@pytest.mark.parametrize('module', ['job_search', 'job_search.pipeline'])
def test_validate_reports_key_error_without_traceback(module, tmp_path):
    config = tmp_path / 'invalid.toml'
    config.write_text('[settings]\nversion = 1\n[candidate]\nmax_pages = true\n')
    result = invoke(module, ['--validate-config', str(config)], tmp_path)
    assert result.returncode == 2
    assert 'candidate.max_pages' in result.stderr
    assert 'Traceback' not in result.stderr


@pytest.mark.parametrize('module', ['job_search', 'job_search.pipeline'])
def test_help_does_not_load_selected_configuration(module, tmp_path):
    result = invoke(module, ['--help'], tmp_path)
    assert result.returncode == 0, result.stderr
