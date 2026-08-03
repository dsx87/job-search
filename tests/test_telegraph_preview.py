"""TDD for the mock-page preview tool."""
import importlib
import json

import pytest

preview = importlib.import_module("scripts.telegraph_preview")


class FakeClient:
    def __init__(self):
        self.pages = []
        self.created = []
        self.create_page_tokens = []  # the token passed per create_page call, same order
        self.account_calls = []       # short_name args passed to create_account
        self.edited = []

    def create_account(self, short_name):
        self.account_calls.append(short_name)
        return "minted-token"

    def get_page_list(self, _token, offset=0):
        return list(self.pages)

    def create_page(self, token, title, nodes):
        self.created.append((title, nodes))
        self.create_page_tokens.append(token)
        page = {"path": title.replace(" ", "-"), "title": title,
                "url": "https://telegra.ph/" + title.replace(" ", "-")}
        self.pages.insert(0, page)
        return page

    def edit_page(self, _token, path, title, nodes):
        self.edited.append((path, title, nodes))
        return {"path": path, "title": title, "url": "https://telegra.ph/" + path}


def test_publishes_one_page_per_day_plus_the_index(monkeypatch):
    monkeypatch.setenv("TELEGRAPH_PREVIEW_TOKEN", "preview-tok")
    monkeypatch.delenv("TELEGRAPH_ACCESS_TOKEN", raising=False)
    client = FakeClient()

    assert preview.main(["--days", "3"], client=client) == 0

    titles = [title for title, _nodes in client.created]
    assert titles[0].startswith("Job Search Digests")  # the index, created first
    assert titles[0] != "Job Search Digests"            # random suffix (finding 1)
    assert sum(1 for t in titles if t.startswith("Job Digest ")) == 3
    assert len(client.edited) == 3                     # index refreshed per digest


def test_second_invocation_grows_the_index(monkeypatch):
    monkeypatch.setenv("TELEGRAPH_PREVIEW_TOKEN", "preview-tok")
    monkeypatch.delenv("TELEGRAPH_ACCESS_TOKEN", raising=False)
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


def test_force_proceeds_without_touching_the_production_token(monkeypatch):
    """--force suppresses the refusal but must never publish with the real token.

    Regression guard for the finding that --force's old help text ("allow
    running against TELEGRAPH_ACCESS_TOKEN") did not match what the code
    does: with TELEGRAPH_PREVIEW_TOKEN unset, --force falls through to
    minting a fresh preview account, so the token that reaches create_page
    must be the minted one, never "production-tok".
    """
    monkeypatch.delenv("TELEGRAPH_PREVIEW_TOKEN", raising=False)
    monkeypatch.setenv("TELEGRAPH_ACCESS_TOKEN", "production-tok")
    client = FakeClient()

    assert preview.main(["--force", "--days", "1"], client=client) == 0

    assert client.created
    assert client.create_page_tokens  # something was actually published
    assert "production-tok" not in client.create_page_tokens
    assert all(token == "minted-token" for token in client.create_page_tokens)


def test_refuses_when_preview_token_equals_access_token(monkeypatch, capsys):
    """finding 7: both variables set to the SAME value, no --force. This is
    the branch _resolve_token's first `if token == ... and not args.force`
    guards -- distinct from test_refuses_the_production_token, which covers
    TELEGRAPH_PREVIEW_TOKEN being unset entirely."""
    monkeypatch.setenv("TELEGRAPH_PREVIEW_TOKEN", "shared-tok")
    monkeypatch.setenv("TELEGRAPH_ACCESS_TOKEN", "shared-tok")
    client = FakeClient()

    assert preview.main([], client=client) == 2

    assert client.created == []
    assert "TELEGRAPH_PREVIEW_TOKEN" in capsys.readouterr().err


def test_distinct_preview_and_access_tokens_uses_the_preview_one(monkeypatch):
    """Both tokens set, to different values, no --force: the preview one wins.

    This is the case the earlier review flagged as untested and dependent on
    the ambient environment. The tool must proceed (no refusal — the tokens
    are legitimately different accounts) and publish with the preview token,
    never the access one.
    """
    monkeypatch.setenv("TELEGRAPH_PREVIEW_TOKEN", "preview-tok")
    monkeypatch.setenv("TELEGRAPH_ACCESS_TOKEN", "production-tok")
    client = FakeClient()

    assert preview.main(["--days", "1"], client=client) == 0

    assert client.create_page_tokens
    assert "production-tok" not in client.create_page_tokens
    assert all(token == "preview-tok" for token in client.create_page_tokens)


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


def test_empty_minted_token_is_reported_and_publishes_nothing(monkeypatch, capsys):
    monkeypatch.delenv("TELEGRAPH_PREVIEW_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAPH_ACCESS_TOKEN", raising=False)
    client = FakeClient()
    client.create_account = lambda _short_name: ""

    assert preview.main(["--days", "1"], client=client) == 2

    assert client.created == []
    assert "no access token" in capsys.readouterr().err


def test_dump_writes_utf8_regardless_of_locale(tmp_path):
    # The nodes carry emoji and "·"; the locale default would raise under LANG=C.
    target = tmp_path / "nodes.json"

    assert preview.main(["--dump", str(target)], client=FakeClient()) == 0

    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["digest"][0]["tag"] == "h3"
