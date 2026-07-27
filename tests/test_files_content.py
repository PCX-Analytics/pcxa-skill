"""Tests for `files list --content` — full-text search of file BODY on the
paginated + countable list (the gap: title-only `--search` can't find files
by their contents, and `files search` is capped at 50 with no count).
"""

import json
from types import SimpleNamespace

from pcxa.commands.files import cmd_files_list
from tests.conftest import FakeResponse

LIST_URL = "https://api.example.com/api/companies/1/projects/2/files/"
TOTAL_URL = "https://api.example.com/api/companies/1/projects/2/files/total-size/"


def _args(**kwargs):
    defaults = {
        "limit": 25, "offset": 0, "ext": None, "tags": None, "tags_mode": None,
        "folder": None, "category": None, "search": None, "exact": False,
        "content": None, "index_status": None, "sort": None, "count_only": False,
        "format": "json",
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_content_passes_param_to_list(client):
    client.session.default = FakeResponse(200, {"count": None, "count_state": "deferred", "results": []})
    cmd_files_list(client, _args(content="IOCC-387"))
    call = client.session.calls[-1]
    assert call["method"] == "GET"
    assert call["url"] == LIST_URL
    assert call["params"]["content"] == "IOCC-387"
    # A title search must NOT be sent when only --content is given.
    assert "search" not in call["params"]


def test_content_count_only_uses_total_size(client, capsys):
    # The list defers the count; --count-only must read the exact total from
    # the /total-size/ action, carrying the content filter.
    client.session.default = FakeResponse(200, {"total_size": 123, "count": 5})
    cmd_files_list(client, _args(content="IOCC-387", count_only=True))
    call = client.session.calls[-1]
    assert call["url"] == TOTAL_URL
    assert call["params"]["content"] == "IOCC-387"
    assert json.loads(capsys.readouterr().out) == {"count": 5}


def test_content_composes_with_title_search(client):
    client.session.default = FakeResponse(200, {"count": None, "results": []})
    cmd_files_list(client, _args(content="IOCC-387", search="report"))
    params = client.session.calls[-1]["params"]
    assert params["content"] == "IOCC-387"
    assert params["search"] == "report"  # both filters ride along (title AND body)


def test_deferred_count_display_is_not_none(client, capsys):
    # Table format with a deferred (null) count must not print "of None".
    client.session.default = FakeResponse(200, {
        "count": None, "count_state": "deferred",
        "results": [{"id": 1, "title": "YATES002119058", "file_type": "EML"}],
    })
    cmd_files_list(client, _args(content="IOCC-387", format="table"))
    out = capsys.readouterr().out
    assert "of None" not in out
    assert "--count-only" in out  # points the user at the exact total
