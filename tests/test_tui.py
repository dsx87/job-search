from job_search import tui
from job_search.models import Job
from job_search.sources.health import FetchReport, SourceHealth, SourceStatus


class ImmediateThread:
    def __init__(self, target, daemon=True):
        self.target = target

    def start(self):
        self.target()


class RecordingStore:
    def __init__(self):
        self.calls = []

    def merge(self, jobs, incomplete_sources=()):
        self.calls.append((tuple(jobs), tuple(incomplete_sources)))


def test_tui_partial_refresh_preserves_incomplete_sources_and_sets_warning(monkeypatch):
    job = Job(title="iOS", company="Acme", source="healthy")
    report = FetchReport((job,), (
        SourceHealth("healthy", SourceStatus.SUCCESS, 1, 1),
        SourceHealth("flaky", SourceStatus.PARTIAL, 0, 2, "page failed"),
    ))
    monkeypatch.setattr(tui, "fetch_jobs_with_health", lambda **_kwargs: report)
    monkeypatch.setattr(tui.threading, "Thread", ImmediateThread)
    screen = tui.JobTUI.__new__(tui.JobTUI)
    screen.store = RecordingStore()
    screen.loading = False
    screen.loading_message = ""
    screen.needs_redraw = False
    screen.refresh_done = False
    screen.refresh_error = None
    screen.refresh_warning = None

    screen._start_refresh()

    assert screen.store.calls == [((job,), ("flaky",))]
    assert "flaky: partial" in screen.refresh_warning
    assert screen.refresh_done is True


def test_tui_total_outage_does_not_merge_store(monkeypatch):
    report = FetchReport((), (
        SourceHealth("down", SourceStatus.FAILED, failure_detail="offline"),
    ))
    monkeypatch.setattr(tui, "fetch_jobs_with_health", lambda **_kwargs: report)
    monkeypatch.setattr(tui.threading, "Thread", ImmediateThread)
    screen = tui.JobTUI.__new__(tui.JobTUI)
    screen.store = RecordingStore()
    screen.loading = False
    screen.loading_message = ""
    screen.needs_redraw = False
    screen.refresh_done = False
    screen.refresh_error = None
    screen.refresh_warning = None

    screen._start_refresh()

    assert screen.store.calls == []
    assert "No usable job source" in screen.refresh_error
