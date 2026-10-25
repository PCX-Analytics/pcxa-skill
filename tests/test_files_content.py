"""Tests for `files list` display and the `files search` clamp notice.

`files list --content` was retired with pcxa#3850; see test_files_query.py.
"""

import json
from types import SimpleNamespace

from pcxa.commands.files import SEMANTIC_SEARCH_CAP, cmd_files_list, cmd_files_search
from tests.conftest import FakeResponse

LIST_URL = "https://api.example.com/api/companies/1/projects/2/files/"
SEARCH_URL = "https://api.example.com/api/companies/1/projects/2/semantic-search/search/"


def _args(**kwargs):
    defaults = {
        "limit": 25, "offset": 0, "ext": None, "tags": None, "tags_mode": None,
        "folder": None, "category": None, "search": None, "exact": False,
        "content": None, "index_status": None, "sort": None, "count_only": False,
        "format": "json",
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _search_args(**kwargs):
    defaults = {"query": "IOCC-387", "page_size": 25, "scope": None, "ext": None, "format": "json"}
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_deferred_count_display_is_not_none(client, capsys):
    # A filter whose count the server defers (null) must not print "of None".
    client.session.default = FakeResponse(200, {
        "count": None, "count_state": "deferred",
        "results": [{"id": 1, "title": "doc", "file_type": "PDF"}],
    })
    cmd_files_list(client, _args(tags="urgent", format="table"))
    out = capsys.readouterr().out
    assert "of None" not in out
    assert "--count-only" in out  # points the user at the exact total


# ── files search clamp (secondary ask) ─────────────────────────────────────

def test_search_clamps_limit_and_warns(client, capsys):
    client.session.default = FakeResponse(200, {"total_results": 50, "results": []})
    cmd_files_search(client, _search_args(page_size=200))
    call = client.session.calls[-1]
    assert call["url"] == SEARCH_URL
    assert call["params"]["limit"] == SEMANTIC_SEARCH_CAP  # clamped, not 200
    err = capsys.readouterr().err
    assert "ranked" in err.lower()
    assert "not a count" in err.lower()


def test_search_no_warning_under_cap(client, capsys):
    client.session.default = FakeResponse(200, {"total_results": 3, "results": []})
    cmd_files_search(client, _search_args(page_size=25))
    assert client.session.calls[-1]["params"]["limit"] == 25
    assert capsys.readouterr().err == ""


def test_search_header_flags_capped_result(client, capsys):
    client.session.default = FakeResponse(200, {"total_results": 50, "results": [], "hybrid_enabled": True})
    cmd_files_search(client, _search_args(page_size=25, format="table"))
    out = capsys.readouterr().out
    assert "not a count" in out  # header no longer reads as "there are 50 matches"
