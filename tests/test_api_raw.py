"""`pcxa api METHOD PATH`: one authenticated request to any route.

The point is that nobody needs a private HTTP client again, so these pin the
URL scoping rules, the body and params, and that --dry-run sends nothing.
"""

import json

import pytest

from pcxa._parser import build_parser
from pcxa.commands.api_raw import cmd_api
from tests.conftest import FakeResponse


class Resp(FakeResponse):
    @property
    def content(self):
        return b"" if self.status_code == 204 else json.dumps(self._body).encode()


def _run(client, *argv):
    cmd_api(client, build_parser().parse_args(["api", *argv]))
    return client.session.calls


def test_relative_path_is_project_scoped(client, capsys):
    client.session.default = Resp(200, {"id": 1})
    calls = _run(client, "get", "baselines/", "-q", "page_size=5", "-q", "status=a", "-q", "status=b")
    assert calls[-1]["method"] == "GET"
    assert calls[-1]["url"] == "https://api.example.com/api/companies/1/projects/2/baselines/"
    assert calls[-1]["params"] == {"page_size": "5", "status": ["a", "b"]}
    assert json.loads(capsys.readouterr().out) == {"id": 1}


def test_company_level_and_absolute_paths(client):
    client.session.default = Resp(200, {})
    _run(client, "GET", "cost-codes/", "--company-level")
    assert client.session.calls[-1]["url"] == "https://api.example.com/api/companies/1/cost-codes/"
    _run(client, "GET", "/api/notifications/")
    assert client.session.calls[-1]["url"] == "https://api.example.com/api/notifications/"


def test_body_is_sent_as_json_and_204_prints_status(client, capsys):
    client.session.default = Resp(204, {})
    _run(client, "PATCH", "activities/5/archive/", "-d", '{"x": 1}')
    assert client.session.calls[-1]["json"] == {"x": 1}
    assert json.loads(capsys.readouterr().out) == {"status": 204}


def test_dry_run_sends_nothing(client, capsys):
    cmd_api(client, build_parser().parse_args(["api", "DELETE", "activities/5/", "--dry-run"]))
    assert client.session.calls == []
    assert "Would DELETE" in capsys.readouterr().out


def test_bad_json_body_exits_1(client):
    with pytest.raises(SystemExit) as e:
        _run(client, "POST", "activities/", "-d", "{nope")
    assert e.value.code == 1
