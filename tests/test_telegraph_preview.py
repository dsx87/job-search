"""TDD for the mock-page preview tool."""
import importlib
import json

import pytest

preview = importlib.import_module("scripts.telegraph_preview")


class FakeClient:
    def __init__(self):
        self.pages = []
        self.created = []
        self.edited = []

    def create_account(self, short_name):
        return "minted-token"

    def get_page_list(self, _token, offset=0):
        return list(self.pages)

    def create_page(self, _token, title, nodes):
        self.created.append((title, nodes))
        page = {"path": title.replace(" ", "-"), "title": title,
                "url": "https://telegra.ph/" + title.replace(" ", "-")}
        self.pages.insert(0, page)
        return page

    def edit_page(self, _token, path, title, nodes):
        self.edited.append((path, title, nodes))
        return {"path": path, "title": title, "url": "https://telegra.ph/" + path}


def test_publishes_one_page_per_day_plus_the_index(monkeypatch):
    monkeypatch.setenv("TELEGRAPH_PREVIEW_TOKEN", "preview-tok")
    client = FakeClient()

    assert preview.main(["--days", "3"], client=client) == 0

    titles = [title for title, _nodes in client.created]
    assert titles[0] == "Job Search Digests"          # the index, created first
    assert sum(1 for t in titles if t.startswith("Job Digest ")) == 3
    assert len(client.edited) == 3                     # index refreshed per digest


def test_second_invocation_grows_the_index(monkeypatch):
    monkeypatch.setenv("TELEGRAPH_PREVIEW_TOKEN", "preview-tok")
    client = FakeClient()

    preview.main(["--days", "2"], client=client)
    preview.main(["--days", "2"], client=client)

    _path, _title, nodes = client.edited[-1]
    links = json.dumps(nodes).count('"href"')
    assert links == 4


def test_refuses_the_production_token(monkeypatch, capsys):
    monkeypatch.delenv("TELEGRAPH_PREVIEW_TOKEN", raising=False)
    monkeypatch.setenv("TELEGRAPH_ACCESS_TOKEN", "production-tok")
    client = FakeClient()

    assert preview.main([], client=client) == 2
    assert client.created == []
    assert "TELEGRAPH_PREVIEW_TOKEN" in capsys.readouterr().err


def test_force_allows_the_production_token(monkeypatch):
    monkeypatch.delenv("TELEGRAPH_PREVIEW_TOKEN", raising=False)
    monkeypatch.setenv("TELEGRAPH_ACCESS_TOKEN", "production-tok")
    client = FakeClient()

    assert preview.main(["--force", "--days", "1"], client=client) == 0
    assert client.created


def test_missing_token_mints_one_and_prints_it(monkeypatch, capsys):
    monkeypatch.delenv("TELEGRAPH_PREVIEW_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAPH_ACCESS_TOKEN", raising=False)
    client = FakeClient()

    assert preview.main(["--days", "1"], client=client) == 0
    out = capsys.readouterr().out
    assert "minted-token" in out
    assert "TELEGRAPH_PREVIEW_TOKEN" in out


def test_dump_writes_node_json_without_publishing(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAPH_PREVIEW_TOKEN", "preview-tok")
    client = FakeClient()
    target = tmp_path / "nodes.json"

    assert preview.main(["--dump", str(target)], client=client) == 0

    assert client.created == []
    payload = json.loads(target.read_text())
    assert "digest" in payload and "index" in payload
    assert payload["digest"][0]["tag"] == "h3"
