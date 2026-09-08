"""`Activity.outcome` and `activities related` — the CLI surface for backend #2797.

Two features that arrived together and fail in opposite directions:

* **outcome** is the activity's answer, and it is revisable. The risk is a
  truthiness guard silently dropping ``--outcome ""``, which would make a wrong
  outcome permanent, and the mirror risk of sending the key when it was never
  passed, which would wipe one on an unrelated update.
* **related** answers "what is attached to this activity". The risks are
  reaching for the flat ``/api/generic-links/`` route, where the company and
  project permission gates do not fire, and printing a table that implies
  completeness when the server told us it omitted or truncated something.
"""

from types import SimpleNamespace

import pytest

from pcxa._parser import build_parser
from pcxa._resolve import LINK_OBJECT_TYPES, NEIGHBOR_OBJECT_TYPES, parse_object_ref
from pcxa.commands.activities import (
    cmd_activities_create,
    cmd_activities_get,
    cmd_activities_related,
    cmd_activities_update,
)
from tests.conftest import FakeResponse, RecordingSession


def _create_args(**kw):
    d = {
        "title": "T", "description": None, "outcome": None, "status": None,
        "priority": None, "due_date": None, "planned_start": None,
        "planned_finish": None, "owner": None, "assignees": None, "type": None,
        "parent": None, "tags": None, "wbs": None, "custom_fields": None,
        "no_fuzzy": True, "dry_run": False, "format": "json",
    }
    d.update(kw)
    return SimpleNamespace(**d)


def _update_args(**kw):
    d = {
        "activity_id": 7, "title": None, "description": None, "outcome": None,
        "status": None, "priority": None, "percent": None, "due_date": None,
        "planned_start": None, "planned_finish": None, "actual_start": None,
        "actual_finish": None, "owner": None, "assignees": None, "parent": None,
        "tags": None, "custom_fields": None, "no_fuzzy": True,
        "dry_run": False, "format": "json",
    }
    d.update(kw)
    return SimpleNamespace(**d)


def _related_args(**kw):
    d = {
        "activity_id": 7, "types": None, "limit": None, "all": False,
        "cursor": None, "format": "table", "dry_run": False,
    }
    d.update(kw)
    return SimpleNamespace(**d)


class _RecClient:
    """Records write payloads; returns a canned activity on GET."""

    company_id = 1
    project_id = 2

    def __init__(self, detail=None):
        self._detail = detail or {"id": 7, "title": "T"}
        self.posted = None
        self.patched = None

    def get(self, path, **kw):
        return self._detail

    def post(self, path, json_data=None, **kw):
        self.posted = json_data
        return {"id": 7, **(json_data or {})}

    def patch(self, path, json_data=None, **kw):
        self.patched = json_data
        return {"id": 7, **(json_data or {})}


# ── outcome ────────────────────────────────────────────────────────────────


class TestOutcomeWrites:
    def test_create_sends_outcome(self):
        c = _RecClient()
        cmd_activities_create(c, _create_args(outcome="Decided: option B."))
        assert c.posted["outcome"] == "Decided: option B."

    def test_update_sends_outcome(self):
        c = _RecClient()
        cmd_activities_update(c, _update_args(outcome="Grain is one row per charge."))
        assert c.patched["outcome"] == "Grain is one row per charge."

    def test_empty_outcome_clears_rather_than_being_dropped(self):
        """``--outcome ""`` must reach the server as an explicit empty string.

        This is the whole reason the guard is ``is not None`` and not a
        truthiness check. Dropped, the CLI would offer no way to retract an
        outcome that turned out to be wrong.
        """
        c = _RecClient()
        cmd_activities_update(c, _update_args(outcome=""))
        assert "outcome" in c.patched
        assert c.patched["outcome"] == ""

    def test_unpassed_outcome_is_omitted_entirely(self):
        """An update about something else must not touch the outcome."""
        c = _RecClient()
        cmd_activities_update(c, _update_args(status="completed"))
        assert "outcome" not in c.patched

    def test_create_omits_outcome_when_unset(self):
        c = _RecClient()
        cmd_activities_create(c, _create_args())
        assert "outcome" not in c.posted


class TestOutcomeSurface:
    def test_get_prints_outcome(self, capsys):
        c = _RecClient({"id": 7, "title": "T", "outcome": "Option B, after review."})
        cmd_activities_get(c, SimpleNamespace(activity_id=7, format="table"))
        assert "Option B, after review." in capsys.readouterr().out

    def test_get_omits_outcome_line_when_empty(self, capsys):
        c = _RecClient({"id": 7, "title": "T", "outcome": None})
        cmd_activities_get(c, SimpleNamespace(activity_id=7, format="table"))
        assert "Outcome:" not in capsys.readouterr().out

    def test_bulk_update_does_not_offer_outcome(self):
        """The server's bulk allow-list has no `outcome`, so the flag must not exist.

        Offering it would parse locally and then 400 for every id in the
        selection. If the backend adds the field, delete this test with the
        change that adds the flag.
        """
        args = build_parser().parse_args(["activities", "bulk-update", "1", "2", "--status", "x"])
        assert not hasattr(args, "outcome")

    def test_create_and_update_do_offer_outcome(self):
        p = build_parser()
        assert p.parse_args(["activities", "create", "--title", "T", "--outcome", "x"]).outcome == "x"
        assert p.parse_args(["activities", "update", "7", "--outcome", "x"]).outcome == "x"


# ── related ────────────────────────────────────────────────────────────────


def _neighbors_body(results, next_cursor=None, **meta):
    base = {"scanned": len(results), "returned": len(results),
            "truncated_scan": False, "omitted_types": []}
    base.update(meta)
    return {"count": None, "count_state": "deferred",
            "next_cursor": next_cursor, "results": results, "meta": base}


def _row(link_id, ntype, nid, name, direction="outgoing", description=""):
    return {"link_id": link_id, "direction": direction, "description": description,
            "created_at": "2026-09-01T00:00:00Z", "neighbor_type": ntype,
            "neighbor": {"id": nid, "type": ntype, "name": name}}


class TestRelatedRoute:
    def test_uses_project_nested_route_not_the_flat_one(self, client):
        """The permission gate is the reason this endpoint is addressed nested.

        ``CompanyProjectsPermissions`` needs company_pk/project_pk in the path;
        on ``/api/generic-links/`` it degrades to IsAuthenticated. A refactor
        that "simplifies" this back to the flat route silently drops the
        company and project checks, so the URL shape is asserted directly.
        """
        client.session = RecordingSession(
            responses=[FakeResponse(200, _neighbors_body([_row(1, "file", 9, "plan.pdf")]))]
        )
        cmd_activities_related(client, _related_args())
        url = client.session.calls[0]["url"]
        assert url == "https://api.example.com/api/companies/1/projects/2/generic-links/neighbors/"
        assert "/api/generic-links/" not in url

    def test_anchors_on_the_activity(self, client):
        client.session = RecordingSession(responses=[FakeResponse(200, _neighbors_body([]))])
        cmd_activities_related(client, _related_args(activity_id=41))
        params = client.session.calls[0]["params"]
        assert params["type"] == "activity"
        assert params["id"] == 41

    def test_passes_type_filter_and_page_size(self, client):
        client.session = RecordingSession(responses=[FakeResponse(200, _neighbors_body([]))])
        cmd_activities_related(client, _related_args(types="file,folder", limit=5))
        params = client.session.calls[0]["params"]
        assert params["neighbor_type"] == "file,folder"
        assert params["page_size"] == 5


class TestRelatedOutput:
    def test_shows_both_directions(self, client, capsys):
        client.session = RecordingSession(responses=[FakeResponse(200, _neighbors_body([
            _row(1, "file", 9, "plan.pdf", direction="outgoing"),
            _row(2, "activity", 8, "Pour slab", direction="incoming"),
        ]))])
        cmd_activities_related(client, _related_args())
        out = capsys.readouterr().out
        assert "plan.pdf" in out and "Pour slab" in out
        assert "->" in out and "<-" in out

    def test_omitted_types_are_reported_not_hidden(self, client, capsys):
        """A drawing link exists but neighbors cannot hydrate it.

        The table would otherwise read as the complete set of related items.
        """
        client.session = RecordingSession(responses=[FakeResponse(200, _neighbors_body(
            [_row(1, "file", 9, "plan.pdf")], omitted_types=["drawing"],
        ))])
        cmd_activities_related(client, _related_args())
        err = capsys.readouterr().err
        assert "drawing" in err
        assert "NOT listed" in err

    def test_truncated_scan_is_reported(self, client, capsys):
        client.session = RecordingSession(responses=[FakeResponse(200, _neighbors_body(
            [_row(1, "file", 9, "plan.pdf")], truncated_scan=True,
        ))])
        cmd_activities_related(client, _related_args())
        assert "stopped scanning" in capsys.readouterr().err

    def test_next_cursor_is_offered_when_not_following(self, client, capsys):
        client.session = RecordingSession(responses=[FakeResponse(200, _neighbors_body(
            [_row(1, "file", 9, "plan.pdf")], next_cursor="c2",
        ))])
        cmd_activities_related(client, _related_args())
        err = capsys.readouterr().err
        assert "--all" in err and "c2" in err


class TestRelatedPagination:
    def test_all_follows_cursors_and_aggregates(self, client, capsys):
        client.session = RecordingSession(responses=[
            FakeResponse(200, _neighbors_body([_row(1, "file", 9, "a.pdf")], next_cursor="c2")),
            FakeResponse(200, _neighbors_body([_row(2, "file", 10, "b.pdf")], next_cursor=None)),
        ])
        cmd_activities_related(client, _related_args(all=True))
        out = capsys.readouterr().out
        assert "a.pdf" in out and "b.pdf" in out
        assert "Related items for activity 7: 2" in out
        assert client.session.calls[1]["params"]["cursor"] == "c2"

    def test_all_aggregates_omitted_types_across_pages(self, client, capsys):
        client.session = RecordingSession(responses=[
            FakeResponse(200, _neighbors_body([_row(1, "file", 9, "a.pdf")], next_cursor="c2")),
            FakeResponse(200, _neighbors_body(
                [_row(2, "file", 10, "b.pdf")], omitted_types=["drawing"])),
        ])
        cmd_activities_related(client, _related_args(all=True))
        assert "drawing" in capsys.readouterr().err

    def test_single_page_makes_one_request(self, client):
        client.session = RecordingSession(responses=[FakeResponse(200, _neighbors_body(
            [_row(1, "file", 9, "a.pdf")], next_cursor="c2",
        ))])
        cmd_activities_related(client, _related_args())
        assert len(client.session.calls) == 1


class TestRelatedTypeValidation:
    def test_unsupported_neighbor_type_fails_before_any_request(self, client, capsys):
        client.session = RecordingSession()
        with pytest.raises(SystemExit):
            cmd_activities_related(client, _related_args(types="drawing"))
        assert client.session.calls == []
        assert "drawing" in capsys.readouterr().err

    def test_drawing_links_are_creatable_but_not_neighbor_resolvable(self):
        """The two sets differ on purpose; this pins the asymmetry."""
        assert "drawing" in LINK_OBJECT_TYPES
        assert "drawing" not in NEIGHBOR_OBJECT_TYPES


# ── object refs ────────────────────────────────────────────────────────────


class TestParseObjectRef:
    def test_accepts_every_server_side_type(self):
        for t in LINK_OBJECT_TYPES:
            assert parse_object_ref(f"{t}:1") == (t, 1)

    def test_rejects_unknown_type_locally(self, capsys):
        with pytest.raises(SystemExit):
            parse_object_ref("drawings:1")
        err = capsys.readouterr().err
        assert "Did you mean 'drawing'?" in err
        assert "formsubmission" in err  # the full valid set is named

    def test_still_rejects_a_non_integer_id(self, capsys):
        with pytest.raises(SystemExit):
            parse_object_ref("file:abc")
        assert "must be an integer" in capsys.readouterr().err.lower()

    def test_still_rejects_a_missing_colon(self, capsys):
        with pytest.raises(SystemExit):
            parse_object_ref("file")
        assert "type:id" in capsys.readouterr().err
