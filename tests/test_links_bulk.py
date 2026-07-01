"""Tests for `links bulk`, which routes through the server-side
POST /api/generic-links/create-attachment/bulk/ endpoint (PR #1245)
instead of one create-attachment request per link.
"""

import json
from types import SimpleNamespace

import pytest

from pcxa.commands.links import cmd_links_bulk, MAX_BULK_LINKS
from tests.conftest import FakeResponse


def _args(**kwargs):
    defaults = {"dry_run": False, "format": "text", "file": None}
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _write_links(tmp_path, links):
    p = tmp_path / "links.json"
    p.write_text(json.dumps(links))
    return str(p)


def test_bulk_sends_single_request_with_bare_array(client, tmp_path):
    links = [
        {"source": "file:170106", "target": "file:170107", "type": "attachment"},
        {"source_type": "activity", "source_id": 3710, "target_type": "file", "target_id": 170106},
    ]
    client.session.default = FakeResponse(201, {"created": 2, "exists": 0, "failed": []})
    cmd_links_bulk(client, _args(file=_write_links(tmp_path, links)))

    assert len(client.session.calls) == 1
    call = client.session.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == "https://api.example.com/api/generic-links/create-attachment/bulk/"
    assert call["json"] == [
        {"source_type": "file", "source_id": 170106, "target_type": "file", "target_id": 170107,
         "description": "attachment"},
        {"source_type": "activity", "source_id": 3710, "target_type": "file", "target_id": 170106},
    ]
    assert call["timeout"] == 180


def test_bulk_reports_created_exists_and_failed(client, tmp_path, capsys):
    links = [
        {"source": "file:1", "target": "file:2"},
        {"source": "file:1", "target": "file:2"},
        {"source": "file:1", "target": "file:1"},
    ]
    client.session.default = FakeResponse(
        201, {"created": 1, "exists": 1, "failed": [{"index": 2, "error": "An object cannot be linked to itself"}]}
    )
    cmd_links_bulk(client, _args(file=_write_links(tmp_path, links)))
    out = capsys.readouterr().out
    assert "1 created, 1 already existed, 1 failed" in out
    assert "[2] file:1 -> file:1: An object cannot be linked to itself" in out


def test_bulk_json_format_emits_aggregate(client, tmp_path, capsys):
    links = [{"source": "file:1", "target": "file:2"}]
    client.session.default = FakeResponse(201, {"created": 1, "exists": 0, "failed": []})
    cmd_links_bulk(client, _args(file=_write_links(tmp_path, links), format="json"))
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data == {"created": 1, "exists": 0, "failed": []}


def test_bulk_chunks_at_max_bulk_links(client, tmp_path):
    links = [{"source_type": "file", "source_id": i, "target_type": "file", "target_id": i + 1}
             for i in range(1, MAX_BULK_LINKS + 11)]
    client.session.default = FakeResponse(201, {"created": 0, "exists": 0, "failed": []})
    cmd_links_bulk(client, _args(file=_write_links(tmp_path, links)))

    assert len(client.session.calls) == 2
    assert len(client.session.calls[0]["json"]) == MAX_BULK_LINKS
    assert len(client.session.calls[1]["json"]) == 10


def test_bulk_dry_run_makes_no_request(client, tmp_path, capsys):
    links = [{"source": "file:1", "target": "file:2"}]
    cmd_links_bulk(client, _args(file=_write_links(tmp_path, links), dry_run=True))
    assert client.session.calls == []
    out = capsys.readouterr().out
    assert "Dry run: 1 links would be created" in out


def test_bulk_parse_errors_isolated_without_api_call(client, tmp_path, capsys):
    links = [{"source": "file:1"}]  # missing target
    cmd_links_bulk(client, _args(file=_write_links(tmp_path, links)))
    assert client.session.calls == []
    out = capsys.readouterr().out
    assert "0 created" in out
    assert "Missing source/target fields" in out


def test_bulk_missing_file_exits(client, tmp_path):
    with pytest.raises(SystemExit):
        cmd_links_bulk(client, _args(file=str(tmp_path / "nope.json")))


def test_bulk_empty_list_exits(client, tmp_path):
    with pytest.raises(SystemExit):
        cmd_links_bulk(client, _args(file=_write_links(tmp_path, [])))
