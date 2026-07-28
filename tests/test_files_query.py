"""Tests for `files query` — boolean file search (AND/OR/NOT, grouping,
field scoping, phrases) over ``semantic-search/boolean-search/``.

This is the precise channel: exhaustive with an exact count, unlike the
ranked 50-capped ``files search``, and structurally expressive, unlike
``files list --search/--content``.

The parse explain-back and the exactness of the count are asserted
deliberately — a result set whose interpretation you cannot verify is not one
you can cite in work product (the complaint behind `--exact`).
"""

import json
from types import SimpleNamespace

from pcxa.commands.files import cmd_files_query
from tests.conftest import FakeResponse

QUERY_URL = "https://api.example.com/api/companies/1/projects/2/semantic-search/boolean-search/"


def _args(**kwargs):
    defaults = {
        "query": "title:report AND delay",
        "limit": 25,
        "offset": 0,
        "ext": None,
        "folder": None,
        "doc_date_from": None,
        "doc_date_to": None,
        "created_from": None,
        "created_to": None,
        "count_only": False,
        "format": "json",
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _body(**kwargs):
    base = {
        "query": "title:report AND delay",
        "parsed": "(title:report AND content:delay)",
        "results": [{"file_id": 7, "file_name": "Monthly Delay Report", "file_type": "PDF", "folder_path": "/a"}],
        "total_files": 1,
        "count_exact": True,
        "limit": 25,
        "offset": 0,
    }
    base.update(kwargs)
    return base


def test_sends_query_verbatim_to_the_boolean_endpoint(client):
    """The expression must reach the server untouched — the CLI does not
    reinterpret operators, the backend parser owns the grammar."""
    client.session.default = FakeResponse(200, _body())
    cmd_files_query(client, _args(query='title:report AND (delay OR "change order")'))
    call = client.session.calls[-1]
    assert call["method"] == "GET"
    assert call["url"] == QUERY_URL
    assert call["params"]["q"] == 'title:report AND (delay OR "change order")'


def test_paging_params_are_forwarded(client):
    client.session.default = FakeResponse(200, _body())
    cmd_files_query(client, _args(limit=50, offset=100))
    params = client.session.calls[-1]["params"]
    assert params["limit"] == 50
    assert params["offset"] == 100


def test_metadata_filters_are_forwarded_with_api_names(client):
    """CLI uses --doc-date-*, the API expects document_date_*."""
    client.session.default = FakeResponse(200, _body())
    cmd_files_query(
        client,
        _args(ext="PDF,DOCX", folder=42, doc_date_from="2024-01-01", doc_date_to="2024-12-31",
              created_from="2023-01-01", created_to="2023-12-31"),
    )
    params = client.session.calls[-1]["params"]
    assert params["file_types"] == "PDF,DOCX"
    assert params["folder_id"] == 42
    assert params["document_date_from"] == "2024-01-01"
    assert params["document_date_to"] == "2024-12-31"
    assert params["created_from"] == "2023-01-01"
    assert params["created_to"] == "2023-12-31"


def test_absent_filters_are_not_sent(client):
    client.session.default = FakeResponse(200, _body())
    cmd_files_query(client, _args())
    params = client.session.calls[-1]["params"]
    for key in ("file_types", "folder_id", "document_date_from", "created_from"):
        assert key not in params


def test_count_only_reports_exactness(client, capsys):
    client.session.default = FakeResponse(200, _body(total_files=512, count_exact=True))
    cmd_files_query(client, _args(count_only=True))
    assert json.loads(capsys.readouterr().out) == {"count": 512, "exact": True}


def test_count_only_flags_a_ceiling_hit(client, capsys):
    """A capped count must not be reported as if it were the real total."""
    client.session.default = FakeResponse(200, _body(total_files=10000, count_exact=False))
    cmd_files_query(client, _args(count_only=True))
    assert json.loads(capsys.readouterr().out) == {"count": 10000, "exact": False}


def test_table_output_shows_the_parse(client, capsys):
    """Explain-back is the point: the user must be able to confirm the query
    was understood the way they meant before trusting the results."""
    client.session.default = FakeResponse(200, _body())
    cmd_files_query(client, _args(format="table"))
    out = capsys.readouterr().out
    assert "Parsed as: (title:report AND content:delay)" in out
    assert "Matches:   1" in out


def test_table_output_marks_an_inexact_count(client, capsys):
    client.session.default = FakeResponse(200, _body(total_files=10000, count_exact=False))
    cmd_files_query(client, _args(format="table"))
    out = capsys.readouterr().out
    assert "10000+" in out
    assert "narrow the query" in out


def test_empty_result_is_stated_as_a_real_zero(client, capsys):
    """count:0 here means genuinely not located — not truncated. Saying so is
    what makes a negative finding usable."""
    client.session.default = FakeResponse(200, _body(results=[], total_files=0))
    cmd_files_query(client, _args(format="table"))
    out = capsys.readouterr().out
    assert "genuinely not located" in out


def test_results_get_clickable_urls(client):
    client.session.default = FakeResponse(200, _body())
    cmd_files_query(client, _args())
    call = client.session.calls[-1]
    assert call["url"] == QUERY_URL
