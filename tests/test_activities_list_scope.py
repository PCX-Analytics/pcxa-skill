"""`activities list` must see every activity the caller asks for.

Measured 2026-10-25 on project 58 (987 activities: 729 Issues, 218 Work
Packages, 40 Releases): `--type 170` (Releases) and `--root-only` returned 0
and an unfiltered list 743, because the flat list endpoint hides summary
activities (any with children) unless `include_summary=true` is sent, and
hides archived ones unless `is_archived` is present. With this change the same
reads return 40, 40 and 987.
"""

import pytest

from pcxa._api import APIClient

from pcxa._parser import build_parser
from pcxa.commands.activities import MAX_PAGE_SIZE, cmd_activities_list, cmd_activities_update


class Pages:
    """Serves `n` rows in pages of the requested size; records every GET."""

    project_id = 58
    paginate_params = staticmethod(APIClient.paginate_params)

    def __init__(self, n=0):
        self.n = n
        self.calls = []
        self.patches = []

    def get(self, path, params=None, project_scoped=True):
        self.calls.append(dict(params or {}))
        size, page = params.get("page_size", 25), params.get("page", 1)
        start = (page - 1) * size
        rows = [{"id": i} for i in range(start, min(start + size, self.n))]
        return {"count": self.n, "results": rows,
                "next": "x" if start + size < self.n else None}

    def patch(self, path, payload=None, project_scoped=True):
        self.patches.append((path, payload))
        return {"id": 1, "title": "t"}


def _list(argv, client):
    args = build_parser().parse_args(["activities", "list", *argv])
    cmd_activities_list(client, args)
    return client.calls[0]


def test_type_filter_includes_summary_activities():
    params = _list(["--type", "170"], Pages())
    assert params["activity_type"] == "170"
    assert params["include_summary"] == "true"


def test_leaves_only_restores_the_web_grid_view():
    params = _list(["--leaves-only"], Pages())
    assert "include_summary" not in params


@pytest.mark.parametrize("argv,expected", [
    ([], None), (["--include-archived"], ""), (["--archived-only"], "true"),
])
def test_archived_scope(argv, expected):
    params = _list(argv, Pages())
    assert params.get("is_archived") == expected


def test_limit_above_the_server_cap_pages_through_every_row(capsys):
    c = Pages(n=MAX_PAGE_SIZE * 2 + 7)
    args = build_parser().parse_args(["activities", "list", "--limit", "5000"])
    cmd_activities_list(c, args)
    assert [p["page"] for p in c.calls] == [1, 2, 3]
    assert all(p["page_size"] == MAX_PAGE_SIZE for p in c.calls)
    import json
    assert len(json.loads(capsys.readouterr().out)["results"]) == MAX_PAGE_SIZE * 2 + 7


def test_update_sets_and_clears_code():
    c = Pages()
    cmd_activities_update(c, build_parser().parse_args(["activities", "update", "5", "--code", "R3"]))
    cmd_activities_update(c, build_parser().parse_args(["activities", "update", "5", "--code", ""]))
    assert c.patches == [("activities/5/", {"code": "R3"}), ("activities/5/", {"code": ""})]
