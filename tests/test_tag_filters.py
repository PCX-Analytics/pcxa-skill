"""Tests for `tag-filters` — saved AND/OR tag-query links on an activity
(issue #1481), backed by the nested ActivityTagFilterLink endpoint
`activities/{id}/tag-filter-links/`.
"""

from types import SimpleNamespace

import pytest

from pcxa.commands.activities import (
    cmd_tag_filters_add,
    cmd_tag_filters_delete,
    cmd_tag_filters_list,
)
from tests.conftest import FakeResponse

BASE = "https://api.example.com/api/companies/1/projects/2/activities/5080/tag-filter-links/"


def _args(**kwargs):
    defaults = {"dry_run": False, "format": "text"}
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_add_all_mode_sends_payload_and_prints(client, capsys):
    client.session.default = FakeResponse(201, {
        "id": 12, "tags": ["pay_app", "yates"], "tags_mode": "all",
        "label": "Yates pay apps", "display_label": "pay_app + yates",
    })
    cmd_tag_filters_add(client, _args(
        activity_id=5080, tags="pay_app, yates", mode="all", label="Yates pay apps"))

    call = client.session.calls[-1]
    assert call["method"] == "POST"
    assert call["url"] == BASE
    assert call["json"] == {"tags": ["pay_app", "yates"], "tags_mode": "all", "label": "Yates pay apps"}
    out = capsys.readouterr().out
    assert "tag-filter link 12" in out
    assert "pay_app + yates [AND]" in out
    # The reproduce hint must be directly runnable — real --tags-mode, not "AND".
    assert "files list --tags pay_app,yates --tags-mode all" in out


def test_add_any_mode_is_default_and_omits_empty_label(client, capsys):
    client.session.default = FakeResponse(201, {
        "id": 3, "tags": ["draft"], "tags_mode": "any", "label": "",
    })
    cmd_tag_filters_add(client, _args(activity_id=5080, tags="draft", mode="any", label=None))
    call = client.session.calls[-1]
    assert call["json"] == {"tags": ["draft"], "tags_mode": "any"}  # no label key
    assert "[OR]" in capsys.readouterr().out


def test_add_empty_tags_exits_without_request(client, capsys):
    with pytest.raises(SystemExit) as exc:
        cmd_tag_filters_add(client, _args(activity_id=5080, tags="  , ,", mode="all", label=None))
    assert exc.value.code == 1
    assert client.session.calls == []
    assert "at least one tag" in capsys.readouterr().err.lower()


def test_add_too_many_tags_exits(client, capsys):
    many = ",".join(f"t{i}" for i in range(21))
    with pytest.raises(SystemExit):
        cmd_tag_filters_add(client, _args(activity_id=5080, tags=many, mode="any", label=None))
    assert client.session.calls == []
    assert "at most 20" in capsys.readouterr().err


def test_add_dry_run_makes_no_request(client, capsys):
    cmd_tag_filters_add(client, _args(
        activity_id=5080, tags="pay_app,yates", mode="all", label=None, dry_run=True))
    assert client.session.calls == []
    assert "Would CREATE" in capsys.readouterr().out


def test_add_json_format_emits_response(client, capsys):
    import json
    body = {"id": 9, "tags": ["a", "b"], "tags_mode": "all", "label": ""}
    client.session.default = FakeResponse(201, body)
    cmd_tag_filters_add(client, _args(
        activity_id=5080, tags="a,b", mode="all", label=None, format="json"))
    assert json.loads(capsys.readouterr().out) == body


def test_list_json_and_bare_list_response(client, capsys):
    import json
    # A bare list (unpaginated) response must render too, not just {"results": [...]}.
    body = [{"id": 7, "tags": ["a", "b"], "tags_mode": "all", "label": "",
             "created_at": "2026-07-21"}]
    client.session.default = FakeResponse(200, body)
    cmd_tag_filters_list(client, _args(activity_id=5080, format="json"))
    assert json.loads(capsys.readouterr().out) == body


def test_delete_dry_run_makes_no_request(client, capsys):
    cmd_tag_filters_delete(client, _args(activity_id=5080, link_id=12, dry_run=True))
    assert client.session.calls == []
    assert "Would DELETE tag-filter link 12" in capsys.readouterr().out


def test_list_renders_table(client, capsys):
    client.session.default = FakeResponse(200, {"results": [
        {"id": 12, "tags": ["pay_app", "yates"], "tags_mode": "all",
         "label": "Yates pay apps", "created_at": "2026-07-21T10:00:00Z"},
        {"id": 13, "tags": ["rfi"], "tags_mode": "any", "label": "", "created_at": "2026-07-20"},
    ]})
    cmd_tag_filters_list(client, _args(activity_id=5080))
    assert client.session.calls[-1]["url"] == BASE
    out = capsys.readouterr().out
    assert "12" in out and "all" in out and "pay_app + yates" in out
    assert "13" in out and "rfi" in out


def test_delete_hits_nested_url(client, capsys):
    client.session.default = FakeResponse(204, {})
    cmd_tag_filters_delete(client, _args(activity_id=5080, link_id=12))
    call = client.session.calls[-1]
    assert call["method"] == "DELETE"
    assert call["url"] == BASE + "12/"
    assert "Deleted tag-filter link 12" in capsys.readouterr().out
