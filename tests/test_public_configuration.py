"""Fresh-clone fetch commands require deliberate search configuration."""
import pytest
from pathlib import Path

from job_search import scraper_cli, tui
from job_search.config import ConfigurationError, PipelineConfig
from job_search.pipeline import run
from job_search.runtime import Runtime, preflight


def unexpected(*args, **kwargs):
    raise AssertionError('must stop before runtime, network, or state work')


def test_scraper_refuses_unconfigured_fetch(monkeypatch, capsys):
    monkeypatch.delenv('JOB_SEARCH_SETTINGS_FILE', raising=False)
    monkeypatch.setattr(scraper_cli, 'run_scraper', unexpected)
    assert scraper_cli.main(['--json']) == 2
    assert 'JOB_SEARCH_SETTINGS_FILE' in capsys.readouterr().err


@pytest.mark.parametrize('command', [run.run_daily, run.run_list, run.run_seed])
def test_pipeline_refuses_unconfigured_search_before_runtime_or_state(command, monkeypatch):
    monkeypatch.setattr(run, '_build_runtime', unexpected)
    monkeypatch.setattr(run, 'pull_state', unexpected)
    monkeypatch.setattr(run, 'fetch_jobs_with_health', unexpected)
    with pytest.raises(ValueError, match='JOB_SEARCH_SETTINGS_FILE'):
        command(PipelineConfig(state_sync=True))


def test_tui_refuses_unconfigured_refresh_without_touching_store(monkeypatch):
    from tests.test_tui import ImmediateThread, RecordingStore
    monkeypatch.setattr(tui, 'fetch_jobs_with_health', unexpected)
    monkeypatch.setattr(tui.threading, 'Thread', ImmediateThread)
    screen = tui.JobTUI.__new__(tui.JobTUI)
    screen.settings = PipelineConfig()
    screen.store = RecordingStore()
    screen.loading = False
    screen._start_refresh()
    assert screen.store.calls == []
    assert 'JOB_SEARCH_SETTINGS_FILE' in screen.refresh_error


def test_cv_settings_are_not_required_for_text_only_daily(tmp_path):
    criteria = tmp_path / 'criteria.md'
    criteria.write_text('Fictional policy notes')
    cfg = PipelineConfig(output_mode='plain', output_cv_mode='disabled',
                         criteria_file=str(criteria), output_dir=str(tmp_path / 'out'))
    rt = Runtime(object(), object(), object(), object(), object(),
                 cv_required=False, needs_telegram=False, needs_base_tex=False)
    preflight(cfg, rt, command='daily')


def test_public_source_listing_bypasses_configuration(monkeypatch, capsys):
    monkeypatch.setenv('JOB_SEARCH_SETTINGS_FILE', '/missing/private.toml')
    assert scraper_cli.main(['--list-sources']) == 0
    assert 'remotive' in capsys.readouterr().out


def test_state_deployment_uses_neutral_commit_identity():
    script = (Path(__file__).resolve().parents[1] / 'scripts' / 'setup-state-sync.sh').read_text()
    identity_lines = [line.strip() for line in script.splitlines()
                      if 'config user.name' in line or 'config user.email' in line]
    assert identity_lines == [
        'git -C "$REPO/.state" config user.name "Job Search State"',
        'git -C "$REPO/.state" config user.email "job-search-state@users.noreply.github.com"',
    ]


def test_standalone_base_requires_cv_input_even_with_text_only_delivery(tmp_path):
    from job_search.runtime import build_runtime
    cfg = PipelineConfig(output_mode='plain', output_cv_mode='disabled',
                         output_dir=str(tmp_path / 'output'))
    with pytest.raises(ValueError, match='base_tex_path'):
        build_runtime(cfg, command='base')


def test_public_listing_labels_specialized_sources_as_opt_in(capsys):
    assert scraper_cli.main(['--list-sources']) == 0
    lines = capsys.readouterr().out.splitlines()
    for name in ('arc', 'mobile.career', 'jobscroller', 'secrettelaviv', 'linkedin-israel'):
        line = next(line for line in lines if line.strip().startswith(name + ' '))
        assert '(default: off)' in line


@pytest.mark.parametrize('flag,value', [('--sources', ''), ('--region', ' '),
                                       ('--sources', ',,'), ('--region', 'unknown'),
                                       ('--max-age', '-1')])
def test_invalid_cli_search_cannot_satisfy_explicit_search(flag, value, tmp_path, monkeypatch):
    config = tmp_path / 'empty.toml'
    config.write_text('[settings]\nversion=1\n')
    monkeypatch.setenv('JOB_SEARCH_SETTINGS_FILE', str(config))
    monkeypatch.setattr(scraper_cli, 'run_scraper', unexpected)
    assert scraper_cli.main([flag, value, '--json']) == 2


def test_empty_configured_relocation_regions_stay_empty(tmp_path, monkeypatch):
    config = tmp_path / 'search.toml'
    config.write_text('[settings]\nversion=1\n[search]\nrelocation_regions=[]\n')
    monkeypatch.setenv('JOB_SEARCH_SETTINGS_FILE', str(config))
    calls = []
    monkeypatch.setattr(scraper_cli, 'run_scraper', lambda **kw: calls.append(kw) or 0)
    assert scraper_cli.main(['--json']) == 0
    assert calls[0]['relocation_regions'] == set()
