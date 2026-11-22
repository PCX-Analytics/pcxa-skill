"""`pcxa baselines|calendars|work-log` and the schedule extras on `activities`.

Each test parses a real argv, dispatches through the module's handler tables,
and asserts on the HTTP request the real APIClient sent (method, URL — which
carries the project/company scope — params and JSON body).
"""

import argparse
import json

import pytest

from pcxa.commands import schedule
from pcxa.commands.schedule import (
    ACTIVITIES_HANDLERS,
    BULK_CHUNK,
    SUB_COMMAND_KEYS,
    SUB_HANDLERS,
    register_activities_parser,
    register_parser,
)
from tests.conftest import FakeResponse

PROJ = "https://api.example.com/api/companies/1/projects/2/"
COMP = "https://api.example.com/api/companies/1/"

# Taken by activities.py / _parser.py; the extras must not shadow them.
EXISTING_ACTIVITIES_SUBS = {"list", "get", "create", "update", "delete", "bulk-update",
                            "types", "related"}


def build():
    p = argparse.ArgumentParser()
    p.add_argument("--format", default="json")
    p.add_argument("--dry-run", action="store_true")
    sub = p.add_subparsers(dest="command")
    register_parser(sub)
    act_sub = sub.add_parser("activities").add_subparsers(dest="activities_command")
    register_activities_parser(act_sub)
    return p


def run(client, argv, responses=()):
    client.session.responses = [FakeResponse(200, r) for r in responses]
    args = build().parse_args(argv)
    if args.command == "activities":
        return ACTIVITIES_HANDLERS[args.activities_command](client, args)
    return SUB_HANDLERS[args.command][getattr(args, SUB_COMMAND_KEYS[args.command])](client, args)


def only_call(client):
    assert len(client.session.calls) == 1, client.session.calls
    return client.session.calls[0]


# ── baselines ────────────────────────────────────────────────────────────────


def test_baselines_list_filters_and_reads_every_page(client, capsys):
    run(client, ["baselines", "list", "--status", "approved", "--version", "3"],
        [{"results": [{"id": 9}], "next": "http://x/?page=2"}, {"results": [{"id": 8}]}])
    calls = client.session.calls
    assert [c["method"] for c in calls] == ["GET", "GET"]
    assert calls[0]["url"] == PROJ + "baselines/"
    # get_all_pages reuses one params dict, so both records show the final page.
    assert {"status": "approved", "version": 3}.items() <= calls[0]["params"].items()
    assert calls[1]["params"]["page"] == "2"
    assert [b["id"] for b in json.loads(capsys.readouterr().out)] == [9, 8]


def test_baselines_get_update_delete(client):
    run(client, ["baselines", "get", "7"])
    assert only_call(client)["url"] == PROJ + "baselines/7/"
    client.session.calls.clear()
    run(client, ["baselines", "update", "7", "--description", "re-plan",
                 "--data", '{"description": "wins", "x": 1}'])
    c = only_call(client)
    assert (c["method"], c["url"], c["json"]) == ("PATCH", PROJ + "baselines/7/",
                                                  {"description": "wins", "x": 1})
    client.session.calls.clear()
    run(client, ["baselines", "delete", "7"])
    c = only_call(client)
    assert (c["method"], c["url"]) == ("DELETE", PROJ + "baselines/7/")


def test_baselines_create_uses_initialize_not_the_model_create(client):
    run(client, ["baselines", "create"])
    c = only_call(client)
    assert (c["method"], c["url"], c["json"]) == ("POST", PROJ + "baselines/initialize/", None)


def test_baselines_uncommitted_and_changes_reads(client):
    run(client, ["baselines", "uncommitted"])
    assert only_call(client)["url"] == PROJ + "baselines/uncommitted/"
    client.session.calls.clear()
    run(client, ["baselines", "uncommitted-changes"], [[]])
    assert only_call(client)["url"] == PROJ + "baselines/uncommitted_changes/"


def test_baselines_approve_and_bulk_approve(client):
    run(client, ["baselines", "approve", "5", "--description", "v2"])
    c = only_call(client)
    assert (c["method"], c["url"], c["json"]) == ("POST", PROJ + "baselines/5/approve/",
                                                  {"description": "v2"})
    client.session.calls.clear()
    run(client, ["baselines", "bulk-approve", "--activities", "1,2"])
    c = only_call(client)
    assert (c["url"], c["json"]) == (PROJ + "baselines/bulk_approve/",
                                     {"description": "", "activity_ids": [1, 2]})


def test_baselines_changes_page_and_all(client):
    run(client, ["baselines", "changes", "5", "--change-type", "update", "--activity", "11",
                 "--limit", "10"])
    c = only_call(client)
    assert c["url"] == PROJ + "baselines/5/change-entries/"
    assert c["params"] == {"change_type": "update", "activity": 11, "page_size": 10}
    client.session.calls.clear()
    run(client, ["baselines", "changes", "5", "--all"],
        [{"results": [], "next": "http://x/?page=2"}, {"results": []}])
    assert len(client.session.calls) == 2


def test_baselines_comparison(client):
    run(client, ["baselines", "comparison", "5", "44"])
    assert only_call(client)["url"] == PROJ + "baselines/5/change-entries/44/comparison/"


# ── calendars ────────────────────────────────────────────────────────────────


def test_calendars_list_company_scope_hits_the_company_url(client):
    run(client, ["calendars", "list", "--scope", "company", "--default", "company"])
    c = only_call(client)
    assert (c["url"], c["params"]) == (COMP + "calendars/", {"is_company_default": "true"})


def test_calendars_create_expands_working_days_to_all_seven(client):
    run(client, ["calendars", "create", "--name", "5x8", "--working-days", "mon,tue,wed,thu,FRI",
                 "--hours-per-day", "8", "--project-default"])
    c = only_call(client)
    assert (c["method"], c["url"]) == ("POST", PROJ + "calendars/")
    assert c["json"] == {
        "name": "5x8",
        "working_days": {"monday": True, "tuesday": True, "wednesday": True, "thursday": True,
                         "friday": True, "saturday": False, "sunday": False},
        "hours_per_day": "8.0",
        "is_project_default": True,
    }


def test_calendars_unknown_weekday_is_refused_before_any_request(client, capsys):
    with pytest.raises(SystemExit) as exc:
        run(client, ["calendars", "create", "--name", "x", "--working-days", "mon,funday"])
    assert exc.value.code == 1
    assert client.session.calls == []
    assert "funday" in capsys.readouterr().err


def test_calendars_get_update_delete_default_effective(client):
    run(client, ["calendars", "get", "3"])
    assert only_call(client)["url"] == PROJ + "calendars/3/"
    client.session.calls.clear()
    run(client, ["calendars", "update", "3", "--no-company-default", "--scope", "company"])
    c = only_call(client)
    assert (c["method"], c["url"], c["json"]) == ("PATCH", COMP + "calendars/3/",
                                                  {"is_company_default": False})
    client.session.calls.clear()
    run(client, ["calendars", "delete", "3"])
    assert only_call(client)["method"] == "DELETE"
    client.session.calls.clear()
    run(client, ["calendars", "set-default", "3", "--scope", "company"])
    c = only_call(client)
    assert (c["method"], c["url"]) == ("POST", COMP + "calendars/3/set_as_default/")
    client.session.calls.clear()
    run(client, ["calendars", "effective"])
    assert only_call(client)["url"] == PROJ + "calendars/effective/"


def test_calendars_bad_data_json_is_a_clean_exit(client, capsys):
    with pytest.raises(SystemExit) as exc:
        run(client, ["calendars", "update", "3", "--data", "{bad"])
    assert exc.value.code == 1
    assert client.session.calls == []
    assert "--data: invalid JSON" in capsys.readouterr().err


def test_calendar_exceptions_crud(client):
    run(client, ["calendars", "exceptions", "list", "3", "--type", "working"])
    c = only_call(client)
    assert (c["url"], c["params"]) == (PROJ + "calendars/3/exceptions/",
                                       {"exception_type": "working"})
    client.session.calls.clear()
    run(client, ["calendars", "exceptions", "create", "3", "--name", "Xmas",
                 "--start", "2026-12-25", "--scope", "company"])
    c = only_call(client)
    assert (c["method"], c["url"], c["json"]) == (
        "POST", COMP + "calendars/3/exceptions/", {"name": "Xmas", "start_date": "2026-12-25"})
    client.session.calls.clear()
    run(client, ["calendars", "exceptions", "update", "3", "8", "--end", ""])
    c = only_call(client)
    assert (c["method"], c["url"], c["json"]) == ("PATCH", PROJ + "calendars/3/exceptions/8/",
                                                  {"end_date": None})
    client.session.calls.clear()
    run(client, ["calendars", "exceptions", "delete", "3", "8"])
    c = only_call(client)
    assert (c["method"], c["url"]) == ("DELETE", PROJ + "calendars/3/exceptions/8/")


def test_calendar_exceptions_bulk_create_exits_1_on_partial_failure(client, tmp_path):
    f = tmp_path / "hol.json"
    f.write_text(json.dumps([{"name": "A", "start_date": "2026-01-01"}, {"name": "B"}]))
    rc = run(client, ["calendars", "exceptions", "bulk-create", "3", str(f)],
             [{"created_count": 1, "error_count": 1}])
    c = only_call(client)
    assert c["url"] == PROJ + "calendars/3/exceptions/bulk_create/"
    assert len(c["json"]["exceptions"]) == 2
    assert rc == 1


# ── work-log ─────────────────────────────────────────────────────────────────


def test_work_log_list_sends_the_viewset_filters(client, capsys):
    run(client, ["work-log", "list", "--from", "2026-10-01", "--to", "2026-10-25",
                 "--group-by", "week", "--user", "4,5", "--event-type", "progress,photo",
                 "--page", "2"],
        [{"periods": [], "total_events": 120, "page": 2, "page_size": 50, "has_next": True}])
    c = only_call(client)
    assert c["url"] == PROJ + "work-log/"
    assert c["params"] == {"date_from": "2026-10-01", "date_to": "2026-10-25", "group_by": "week",
                           "user": "4,5", "event_type": "progress,photo", "page": 2}
    assert "--page 3" in capsys.readouterr().err


# ── activities: read-only ────────────────────────────────────────────────────


@pytest.mark.parametrize("argv,path,params", [
    (["field-changes", "12"], "activities/12/field_changes/", None),
    (["critical-path"], "activities/critical_path/", None),
    (["schedule-summary"], "activities/schedule_summary/", None),
    (["progress-summary", "--period-days", "14"], "activities/progress-summary/",
     {"period_days": 14}),
    (["progress-curve", "--from", "2026-01-01", "--interval", "monthly",
      "--rollup-method", "duration"], "activities/progress-curve/",
     {"date_from": "2026-01-01", "interval": "monthly", "rollup_method": "duration"}),
    (["baseline-curve", "--status", "completed"], "activities/baseline-curve/",
     {"status": "completed"}),
    (["trend", "--days", "all", "--params", '{"tags": "7"}'], "activities/trend/",
     {"days": "all", "tags": "7"}),
    (["wbs-tree", "--parent", "40", "--page-size", "200", "--search", "pump"],
     "activities/wbs-tree/", {"search": "pump", "parent": 40, "page_size": 200}),
])
def test_activity_reads(client, argv, path, params):
    run(client, ["activities"] + argv)
    c = only_call(client)
    assert (c["method"], c["url"]) == ("GET", PROJ + path)
    assert (c["params"] or None) == params


def test_bad_params_json_is_a_clean_exit(client, capsys):
    with pytest.raises(SystemExit) as exc:
        run(client, ["activities", "trend", "--params", "[1"])
    assert exc.value.code == 1
    assert client.session.calls == []
    assert "--params: invalid JSON" in capsys.readouterr().err


# ── activities: writes ───────────────────────────────────────────────────────


@pytest.mark.parametrize("verb,method", [("archive", "PATCH"), ("unarchive", "PATCH"),
                                         ("restore", "POST")])
def test_one_id_uses_the_per_activity_action(client, verb, method):
    run(client, ["activities", verb, "12"])
    c = only_call(client)
    assert (c["method"], c["url"]) == (method, PROJ + f"activities/12/{verb}/")


@pytest.mark.parametrize("verb", ["archive", "unarchive", "restore"])
def test_many_ids_use_the_bulk_action_in_chunks_under_the_server_cap(client, verb):
    ids = [str(i) for i in range(1, 1201)]
    rc = run(client, ["activities", verb] + ids,
             [{"success_count": 500}, {"success_count": 499, "error_count": 1,
                                       "errors": ["Activity 7 not found"]},
              {"success_count": 200}])
    calls = client.session.calls
    assert {(c["method"], c["url"]) for c in calls} == {("PATCH", PROJ + f"activities/bulk_{verb}/")}
    sizes = [len(c["json"]["activity_ids"]) for c in calls]
    assert sizes == [BULK_CHUNK, BULK_CHUNK, 200] and max(sizes) <= 10_000
    assert rc == 1, "a partial failure must not exit 0"


def test_reparent_to_root_sends_null_and_sums_updated_rows(client, capsys):
    rc = run(client, ["activities", "reparent", "1", "2", "--parent", "none"],
             [{"updated_count": 2, "updated_activities": [{"id": 1}, {"id": 2}], "errors": []}])
    c = only_call(client)
    assert (c["method"], c["url"], c["json"]) == (
        "PATCH", PROJ + "activities/bulk_reparent/", {"new_parent": None, "activity_ids": [1, 2]})
    assert json.loads(capsys.readouterr().out)["updated_count"] == 2
    assert rc is None


def test_reparent_rejects_a_non_id_parent_at_parse_time(client):
    with pytest.raises(SystemExit) as exc:
        run(client, ["activities", "reparent", "1", "--parent", "top"])
    assert exc.value.code == 2
    assert client.session.calls == []


def test_toggle_closed_and_assign_users(client):
    run(client, ["activities", "toggle-closed", "12"])
    c = only_call(client)
    assert (c["method"], c["url"], c["json"]) == ("POST", PROJ + "activities/12/toggle_closed/", {})
    client.session.calls.clear()
    run(client, ["activities", "assign-users", "12", "--users", "4,5", "--distribution", "6"])
    c = only_call(client)
    assert (c["url"], c["json"]) == (PROJ + "activities/12/assign_users/",
                                     {"assignees": [4, 5], "distribution_list": [6]})


def test_distribution_without_users_is_refused_before_the_server_500s(client, capsys):
    args = build().parse_args(["activities", "assign-users", "12", "--users", "4"])
    args.users = ""
    args.distribution = "6"
    with pytest.raises(SystemExit) as exc:
        schedule.cmd_activities_assign_users(client, args)
    assert exc.value.code == 1
    assert client.session.calls == []


def test_dry_run_sends_nothing(client, capsys):
    run(client, ["--dry-run", "calendars", "create", "--name", "x"])
    run(client, ["--dry-run", "activities", "archive", "1", "2"])
    run(client, ["--dry-run", "baselines", "create"])
    assert client.session.calls == []
    out = capsys.readouterr().out
    assert "Would CREATE calendar" in out and "Would ARCHIVE" in out


# ── wiring ───────────────────────────────────────────────────────────────────


def test_every_registered_subcommand_has_a_handler():
    parser = build()
    sub_action = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    for group, handlers in SUB_HANDLERS.items():
        group_parser = sub_action.choices[group]
        nested = next(a for a in group_parser._actions
                      if isinstance(a, argparse._SubParsersAction))
        assert nested.dest == SUB_COMMAND_KEYS[group]
        assert set(nested.choices) == set(handlers)
    acts = sub_action.choices["activities"]
    nested = next(a for a in acts._actions if isinstance(a, argparse._SubParsersAction))
    assert set(nested.choices) == set(ACTIVITIES_HANDLERS)
    assert not set(ACTIVITIES_HANDLERS) & EXISTING_ACTIVITIES_SUBS
    assert schedule.COMPANY_ONLY == set()
