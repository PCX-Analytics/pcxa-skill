"""Tests for `files list --content` — literal substring search of file BODY on
the paginated + countable list (the gap: title-only `--search` can't find files
by their contents, and `files search` is a ranked top-50 with no count), plus
the `files search` clamp notice.
"""

import json
from types import SimpleNamespace

from pcxa.commands.files import SEMANTIC_SEARCH_CAP, cmd_files_list, cmd_files_search
from tests.conftest import FakeResponse

LIST_URL = "https://api.example.com/api/companies/1/projects/2/files/"
TOTAL_URL = "https://api.example.com/api/companies/1/projects/2/files/total-size/"
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


def test_content_sends_content_contains_and_stable_order(client):
    client.session.default = FakeResponse(200, {"count": 2, "count_state": "exact", "results": []})
    cmd_files_list(client, _args(content="IOCC-387"))
    call = client.session.calls[-1]
    assert call["method"] == "GET"
    assert call["url"] == LIST_URL
    # Literal-substring backend filter param.
    assert call["params"]["content_contains"] == "IOCC-387"
    # Exhaustive paging needs a unique, deterministic order.
    assert call["params"]["ordering"] == "id"
    # A title search must NOT be sent when only --content is given.
    assert "search" not in call["params"]


def test_content_count_only_uses_total_size(client, capsys):
    client.session.default = FakeResponse(200, {"total_size": 123, "count": 217})
    cmd_files_list(client, _args(content="IOCC-387", count_only=True))
    call = client.session.calls[-1]
    assert call["url"] == TOTAL_URL
    assert call["params"]["content_contains"] == "IOCC-387"
    assert json.loads(capsys.readouterr().out) == {"count": 217}


def test_content_composes_with_title_search(client):
    client.session.default = FakeResponse(200, {"count": 1, "results": []})
    cmd_files_list(client, _args(content="IOCC-387", search="report"))
    params = client.session.calls[-1]["params"]
    assert params["content_contains"] == "IOCC-387"
    assert params["search"] == "report"  # both ride along (title AND body)
    # An explicit --search means the user opted into title matching; --content
    # still pins a stable order for enumeration.
    assert params["ordering"] == "id"


def test_explicit_sort_overrides_content_default(client):
    client.session.default = FakeResponse(200, {"count": 0, "results": []})
    cmd_files_list(client, _args(content="IOCC-387", sort="-created_at"))
    assert client.session.calls[-1]["params"]["ordering"] == "-created_at"


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
    assert "files list --content" in err  # points at the exhaustive/countable path


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
