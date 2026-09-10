"""Ensure configuration crosses public execution boundaries."""
from types import SimpleNamespace

from job_search.models import Job
from job_search.pipeline import run
from job_search.sources.health import FetchReport


def test_pipeline_fetch_threads_search_and_candidate(monkeypatch):
    search, candidate = object(), object()
    cfg = SimpleNamespace(settings_file='settings.toml', search=search, candidate=candidate,
                          setting_origins={'search_terms': {'kind': 'file'}},
                          sources_enable=(), sources_disable=(), seen_jobs_file='seen_jobs.json',
                          scrape_budget_seconds=77)
    captured = {}
    def fetch(**kwargs):
        captured.update(kwargs)
        return FetchReport((), ())
    monkeypatch.setattr(run, 'fetch_jobs_with_health', fetch)
    run._fetch_for_pipeline(cfg)
    assert captured['search'] is search
    assert captured['candidate'] is candidate
    assert captured['budget_seconds'] == 77


def test_pipeline_evaluation_threads_policy(monkeypatch):
    from job_search.llm import eval as evaluator
    captured = {}
    def evaluate(*args, **kwargs):
        captured.update(kwargs)
        return {'verdict': 'uncertain'}
    monkeypatch.setattr(evaluator, 'evaluate_job', evaluate)
    monkeypatch.setattr(run, 'ensure_job_description', lambda job: True)
    cfg = SimpleNamespace(settings_file='settings.toml', search=object(), candidate=object(), policy=object())
    run._evaluate_candidate(object(), '', Job(), settings=cfg)
    assert captured['search'] is cfg.search
    assert captured['candidate'] is cfg.candidate
    assert captured['policy'] is cfg.policy


def test_redacted_settings_handles_immutable_nested_configuration():
    import json
    from dataclasses import dataclass
    from types import MappingProxyType
    from job_search.runtime import Runtime, redacted_settings

    @dataclass(frozen=True)
    class Settings:
        llm_primary_api_key: str = 'must-not-appear'
        candidate: object = None
    cfg = Settings(candidate=MappingProxyType({'max_pages_by_country': MappingProxyType({'DE': 2})}))
    rt = Runtime(object(), object(), object(), object(), object())
    payload = redacted_settings(cfg, rt)
    assert 'must-not-appear' not in payload
    assert json.loads(payload)['candidate']['max_pages_by_country'] == {'DE': 2}


def test_scraper_cli_explicit_arguments_override_file(monkeypatch, tmp_path):
    from job_search import scraper_cli
    path = tmp_path / 'settings.toml'
    path.write_text('[settings]\nversion=1\n[search]\nsearch_terms=["Python"]\nmax_age_days=20\nrelocation_regions=["eu"]\n')
    monkeypatch.setenv('JOB_SEARCH_SETTINGS_FILE', str(path))
    captured = {}
    def scrape(**kwargs):
        captured.update(kwargs)
        return 0
    monkeypatch.setattr(scraper_cli, 'run_scraper', scrape)
    assert scraper_cli.main(['--max-age', '0', '--region', 'ca', '--sources', 'remotive']) == 0
    from job_search.models import Region
    assert captured['max_age'] == 0
    assert captured['relocation_regions'] == {Region.CA}
    assert captured['source_names'] == ['remotive']
    assert captured['search'].search_terms == ('Python',)


def test_scraper_cli_uses_file_defaults(monkeypatch, tmp_path):
    from job_search import scraper_cli
    path = tmp_path / 'settings.toml'
    path.write_text('[settings]\nversion=1\n[search]\nsearch_terms=["Python"]\nmax_age_days=20\nrelocation_regions=["eu"]\n')
    monkeypatch.setenv('JOB_SEARCH_SETTINGS_FILE', str(path))
    captured = {}
    def scrape(**kwargs):
        captured.update(kwargs)
        return 0
    monkeypatch.setattr(scraper_cli, 'run_scraper', scrape)
    assert scraper_cli.main(['--json']) == 0
    assert captured['max_age'] == 20


def test_page_limits_preserve_history_while_policy_changes_reopen_only_nonfits():
    import datetime
    from dataclasses import replace
    from job_search.config import SearchConfig, CandidateConfig, PolicyConfig
    from job_search.policy import evaluation_configuration_revision
    from job_search.state.seen_jobs import (criteria_fingerprint, evaluation_signature,
                                           record_evaluation, should_reevaluate)
    from job_search.identity import job_identity_keys

    job = Job(title='Platform Engineer', company='Fictional Labs', url='https://example.invalid/role')
    search, candidate, policy = SearchConfig(), CandidateConfig(), PolicyConfig()
    def signature(cand, rules):
        revision = evaluation_configuration_revision(search, cand, rules)
        return evaluation_signature('stable posting', criteria_fingerprint('criteria', revision))
    original = signature(candidate, policy)
    changed_pages = signature(replace(candidate, max_pages=3, max_pages_by_country={'DE': 2}), policy)
    assert changed_pages == original
    changed_policy = signature(candidate, replace(policy, rejected_seniority=('junior',)))
    assert changed_policy != original
    delivered = set(job_identity_keys(job))
    record_evaluation(delivered, job, original, 'fit', datetime.date(2026, 9, 10))
    snapshot = set(delivered)
    assert not should_reevaluate(delivered, job, changed_pages)
    assert not should_reevaluate(delivered, job, changed_policy)
    assert delivered == snapshot
    rejected = set(job_identity_keys(job))
    record_evaluation(rejected, job, original, 'nonfit', datetime.date(2026, 9, 10))
    assert should_reevaluate(rejected, job, changed_policy)


def test_scraper_cli_passes_file_scrape_budget(monkeypatch, tmp_path):
    from job_search import scraper_cli
    path = tmp_path / 'settings.toml'
    path.write_text('[settings]\nversion=1\nscrape_budget_seconds=17\n[search]\nsearch_terms=["Python"]\n')
    monkeypatch.setenv('JOB_SEARCH_SETTINGS_FILE', str(path))
    captured = {}
    def scrape(**kwargs):
        captured.update(kwargs)
        return 0
    monkeypatch.setattr(scraper_cli, 'run_scraper', scrape)
    assert scraper_cli.main(['--json']) == 0
    assert captured['budget_seconds'] == 17


def test_scraper_cli_explicit_all_overrides_file_source_selection(monkeypatch, tmp_path):
    from job_search import scraper_cli
    from job_search.sources.fetch import select_sources
    path = tmp_path / 'settings.toml'
    path.write_text('[settings]\nversion=1\n[search]\nsearch_terms=["Python"]\nsources_disable=["remotive"]\n')
    monkeypatch.setenv('JOB_SEARCH_SETTINGS_FILE', str(path))
    captured = {}
    def scrape(**kwargs):
        captured.update(kwargs)
        return 0
    monkeypatch.setattr(scraper_cli, 'run_scraper', scrape)
    assert scraper_cli.main(['--sources', 'all', '--json']) == 0
    assert captured['source_names'] == select_sources(generic=True)
    assert 'remotive' in captured['source_names']


def test_menu_all_overrides_configured_source_selection(monkeypatch, tmp_path):
    from job_search import scraper_cli
    path = tmp_path / 'settings.toml'
    path.write_text('[settings]\nversion=1\n[search]\nsources_disable=["remotive"]\n')
    monkeypatch.setenv('JOB_SEARCH_SETTINGS_FILE', str(path))
    replies = iter(['2', 'all', '1'])
    monkeypatch.setattr('builtins.input', lambda _: next(replies))
    captured = {}
    monkeypatch.setattr(scraper_cli, 'run_scraper', lambda **kw: captured.update(kw) or 0)
    assert scraper_cli.main(['--menu']) == 0
    assert 'remotive' in captured['source_names']


def test_explicit_cli_search_counts_with_selected_empty_toml(monkeypatch, tmp_path):
    from job_search import scraper_cli
    config = tmp_path / 'empty.toml'
    config.write_text('[settings]\nversion=1\n')
    monkeypatch.setenv('JOB_SEARCH_SETTINGS_FILE', str(config))
    calls = []
    monkeypatch.setattr(scraper_cli, 'run_scraper', lambda **kw: calls.append(kw) or 0)
    assert scraper_cli.main(['--sources', 'remotive', '--json']) == 0
    assert calls[0]['source_names'] == ['remotive']
