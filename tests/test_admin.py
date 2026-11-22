"""`pcxa members|groups|team|permission-templates|company|programs|notifications|needs-you`.

Each command is parsed from a real argv and dispatched through the module's
SUB_HANDLERS / SUB_COMMAND_KEYS, and the recorded request is compared to the
route the backend serves (api/backend/nested_urls.py, notifications/urls.py
@95addac48). The failures worth catching: a company resource sent to the
project URL (or the reverse), a membership id confused with a user id, a write
that runs under --dry-run, an invitation email sent without an explicit
address, and notifications built on the company tree they are not part of.
"""

import argparse
import json

import pytest

from pcxa._api import APIClient
from pcxa.commands.admin import (
    COMPANY_ONLY,
    SUB_COMMAND_KEYS,
    SUB_HANDLERS,
    register_parser,
)

BASE = "https://api.example.com"


class _Resp:
    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body


class FakeClient:
    """Records every request; returns a canned body."""

    company_id = 3
    project_id = 7
    base_url = BASE
    paginate_params = staticmethod(APIClient.paginate_params)

    def __init__(self, body=None):
        self.body = {"results": [], "count": 0} if body is None else body
        self.calls = []

    def _record(self, method, path, params=None, payload=None, project_scoped=True):
        self.calls.append({"method": method, "path": path, "params": params,
                           "payload": payload, "project_scoped": project_scoped})
        return self.body

    def get(self, path, params=None, project_scoped=True, timeout=None):
        return self._record("GET", path, params=params, project_scoped=project_scoped)

    def post(self, path, json_data=None, project_scoped=True, timeout=None):
        return self._record("POST", path, payload=json_data, project_scoped=project_scoped)

    def patch(self, path, json_data=None, project_scoped=True, timeout=None):
        return self._record("PATCH", path, payload=json_data, project_scoped=project_scoped)

    def delete(self, path, json_data=None, project_scoped=True, timeout=None):
        return self._record("DELETE", path, project_scoped=project_scoped)

    def _request(self, method, url, params=None, json=None, timeout=None):
        self._record(method, url, params=params, payload=json, project_scoped=None)
        return _Resp(self.body)


def parser():
    p = argparse.ArgumentParser()
    p.add_argument("--format", default="json")
    p.add_argument("--dry-run", action="store_true")
    sub = p.add_subparsers(dest="command")
    register_parser(sub)
    return p


def run(argv, client=None):
    client = client or FakeClient()
    args = parser().parse_args(argv)
    sub = getattr(args, SUB_COMMAND_KEYS[args.command])
    SUB_HANDLERS[args.command][sub](client, args)
    return client


def only_call(client):
    assert len(client.calls) == 1, client.calls
    return client.calls[0]


P, C, N = True, False, None  # project-scoped, company-scoped, full URL (notifications)

# (argv, method, path, project_scoped, params-subset, payload)
CASES = [
    # members — project memberships / access summary / resource access
    (["members", "list", "--search", "ann", "--user", "12", "--limit", "50"],
     "GET", "memberships/", P, {"search": "ann", "user": 12, "page_size": 50}, None),
    (["members", "get", "41"], "GET", "memberships/41/", P, None, None),
    (["members", "add", "--user", "12", "--role", "Foreman", "--groups", "4,5"],
     "POST", "memberships/", P, None,
     {"user": 12, "project": 7, "role": "Foreman", "is_admin": False,
      "authorization_level": "standard", "permission_group_ids": [4, 5]}),
    (["members", "add", "--user", "12", "--role", "PM", "--admin"],
     "POST", "memberships/", P, None,
     {"user": 12, "project": 7, "role": "PM", "is_admin": True, "authorization_level": "admin"}),
    (["members", "update", "41", "--role", "Lead", "--admin", "false"],
     "PATCH", "memberships/41/", P, None, {"role": "Lead", "is_admin": False}),
    (["members", "remove", "41"], "DELETE", "memberships/41/", P, None, None),
    (["members", "access"], "GET", "access-summary/", P, None, None),
    (["members", "access", "12"], "GET", "access-summary/12/", P, None, None),
    (["members", "resource-access", "--enabled", "true"],
     "GET", "resource-access/", P, {"is_enabled": "true"}, None),
    # groups — project permission groups + nested members / rules
    (["groups", "list", "-s", "field"], "GET", "groups/", P, {"search": "field"}, None),
    (["groups", "get", "9"], "GET", "groups/9/", P, None, None),
    (["groups", "create", "--name", "Field", "--template", "2"],
     "POST", "groups/", P, None, {"name": "Field", "linked_template": 2}),
    (["groups", "update", "9", "--description", "x", "--data", '{"name": "F2"}'],
     "PATCH", "groups/9/", P, None, {"description": "x", "name": "F2"}),
    (["groups", "delete", "9"], "DELETE", "groups/9/", P, None, None),
    (["groups", "members", "list", "9"], "GET", "groups/9/members/", P, {"page_size": 25}, None),
    (["groups", "members", "add", "9", "--user", "12"],
     "POST", "groups/9/members/", P, None, {"user": 12}),
    (["groups", "members", "remove", "9", "300"], "DELETE", "groups/9/members/300/", P, None, None),
    (["groups", "rules", "list", "9"], "GET", "groups/9/rules/", P, None, None),
    (["groups", "rules", "create", "9", "--type", "photo", "--value", "all", "--level", "1"],
     "POST", "groups/9/rules/", P, None,
     {"resource_type": "photo", "resource_value": "all", "permission_level": 1}),
    (["groups", "rules", "update", "9", "5", "--level", "2"],
     "PATCH", "groups/9/rules/5/", P, None, {"permission_level": 2}),
    (["groups", "rules", "delete", "9", "5"], "DELETE", "groups/9/rules/5/", P, None, None),
    # team — company memberships + invitations
    (["team", "list", "--admin", "true", "--role", "Company Admin"],
     "GET", "memberships/", C, {"is_admin": "true", "role": "Company Admin"}, None),
    (["team", "get", "15"], "GET", "memberships/15/", C, None, None),
    (["team", "update", "15", "--ai-access", "true", "--role", "Estimator"],
     "PATCH", "memberships/15/", C, None, {"ai_access_enabled": True, "role": "Estimator"}),
    (["team", "remove", "15"], "DELETE", "memberships/15/", C, None, None),
    (["team", "invite", "--email", "new@x.com", "--project-id", "7", "--project-role", "QA",
      "--groups", "4"],
     "POST", "invitations/", C, None,
     {"email": "new@x.com", "role": "Company Member", "is_admin": False, "ai_access_enabled": False,
      "project_id": 7, "project_is_admin": False, "project_role": "QA", "permission_group_ids": [4]}),
    (["team", "invitations", "list", "--status", "pending"],
     "GET", "invitations/", C, {"status": "pending"}, None),
    (["team", "invitations", "revoke", "8"], "DELETE", "invitations/8/", C, None, None),
    (["team", "invitations", "resend", "8"], "POST", "invitations/8/resend/", C, None, {}),
    # permission-templates — company group templates + rules
    (["permission-templates", "list", "--active", "false"],
     "GET", "group-templates/", C, {"is_active": "false"}, None),
    (["permission-templates", "get", "2"], "GET", "group-templates/2/", C, None, None),
    (["permission-templates", "create", "--name", "Viewer"],
     "POST", "group-templates/", C, None, {"name": "Viewer"}),
    (["permission-templates", "update", "2", "--active", "true"],
     "PATCH", "group-templates/2/", C, None, {"is_active": True}),
    (["permission-templates", "delete", "2"], "DELETE", "group-templates/2/", C, None, None),
    (["permission-templates", "rules", "list", "2"], "GET", "group-templates/2/rules/", C, None, None),
    (["permission-templates", "rules", "create", "2", "--type", "activity_type",
      "--resource-id", "33", "--level", "2"],
     "POST", "group-templates/2/rules/", C, None,
     {"resource_type": "activity_type", "resource_id": 33, "permission_level": 2}),
    (["permission-templates", "rules", "update", "2", "6", "--level", "0"],
     "PATCH", "group-templates/2/rules/6/", C, None, {"permission_level": 0}),
    (["permission-templates", "rules", "delete", "2", "6"],
     "DELETE", "group-templates/2/rules/6/", C, None, None),
    # company
    (["company", "get"], "GET", "", C, None, None),
    (["company", "update", "--website", "https://x.io", "--data", '{"ai_settings": {}}'],
     "PATCH", "", C, None, {"website": "https://x.io", "ai_settings": {}}),
    (["company", "security-policy"], "GET", "security-policy/", C, None, None),
    (["company", "sso"], "GET", "sso-config/", C, None, None),
    # programs
    (["programs", "list", "--archived", "false", "--owner", "12"],
     "GET", "programs/", C, {"is_archived": "false", "owner": 12}, None),
    (["programs", "get", "4"], "GET", "programs/4/", C, None, None),
    (["programs", "create", "--name", "North", "--code", "PGM01"],
     "POST", "programs/", C, None, {"name": "North", "code": "PGM01"}),
    (["programs", "update", "4", "--description", "d"],
     "PATCH", "programs/4/", C, None, {"description": "d"}),
    (["programs", "delete", "4"], "DELETE", "programs/4/", C, None, None),
    # notifications / needs-you — /api/notifications/, company as a parameter
    (["notifications", "list", "--unread", "--project-id", "7"],
     "GET", f"{BASE}/api/notifications/", N, {"company": 3, "read": "false", "project": 7}, None),
    (["notifications", "unread-count"],
     "GET", f"{BASE}/api/notifications/unread_count/", N, {"company": 3}, None),
    (["notifications", "mark-read", "55"],
     "POST", f"{BASE}/api/notifications/55/mark_read/", N, None, {}),
    (["notifications", "mark-all-read", "--project-id", "7"],
     "POST", f"{BASE}/api/notifications/mark_all_read/", N, None, {"company": 3, "project": 7}),
    (["needs-you", "list"], "GET", f"{BASE}/api/notifications/needs-you/", N, {"company": 3}, None),
]


@pytest.mark.parametrize("argv,method,path,scoped,params,payload", CASES,
                         ids=[" ".join(c[0][:3]) for c in CASES])
def test_command_hits_the_backend_route(argv, method, path, scoped, params, payload, capsys):
    body = [] if argv[:2] == ["members", "access"] and len(argv) == 2 else None
    call = only_call(run(argv, FakeClient(body)))
    assert (call["method"], call["path"], call["project_scoped"]) == (method, path, scoped)
    if params is not None:
        assert params.items() <= (call["params"] or {}).items()
    if payload is not None:
        assert call["payload"] == payload


def test_every_registered_subcommand_is_covered():
    covered = set()
    for argv, *_ in CASES:
        group, sub = argv[0], argv[1]
        covered.add((group, sub, argv[2] if sub in ("members", "rules", "invitations") else None))
    for group, subs in SUB_HANDLERS.items():
        for sub in subs:
            assert any(g == group and s == sub for g, s, _ in covered), f"{group} {sub} untested"


def test_table_format_renders_without_error(capsys):
    rows = {"results": [{"id": 1, "user": 2, "user_name": "Ann", "groups": ["Field"]}], "count": 1}
    run(["--format", "table", "members", "list"], FakeClient(rows))
    out = capsys.readouterr().out
    assert "Project members: 1 of 1" in out and "Field" in out


# ── dry-run: writes never leave the machine ──────────────────────────────────


@pytest.mark.parametrize("argv", [
    ["--dry-run", "team", "invite", "--email", "new@x.com"],
    ["--dry-run", "members", "remove", "41"],
    ["--dry-run", "groups", "rules", "create", "9", "--type", "file", "--value", "all", "--level", "1"],
    ["--dry-run", "notifications", "mark-all-read"],
    ["--dry-run", "team", "invitations", "resend", "8"],
])
def test_dry_run_sends_nothing(argv, capsys):
    client = run(argv)
    assert client.calls == []
    assert "Would" in capsys.readouterr().out


def test_dry_run_invite_names_who_gets_the_email(capsys):
    run(["--dry-run", "team", "invite", "--email", "new@x.com"])
    out = capsys.readouterr().out
    assert "emails new@x.com" in out
    assert json.loads(out.split(":", 1)[1])["email"] == "new@x.com"


def test_invite_without_an_email_is_refused_by_the_parser(capsys):
    with pytest.raises(SystemExit):
        parser().parse_args(["team", "invite"])


# ── input validation ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("raw", ["{not json", "[1, 2]"])
def test_bad_data_json_exits_cleanly_before_any_request(raw, capsys):
    client = FakeClient()
    with pytest.raises(SystemExit) as exc:
        run(["programs", "update", "4", "--data", raw], client)
    assert exc.value.code == 1
    assert client.calls == []
    assert "--data" in capsys.readouterr().err


def test_update_with_no_fields_exits_without_a_request(capsys):
    client = FakeClient()
    with pytest.raises(SystemExit):
        run(["team", "update", "15"], client)
    assert client.calls == []


def test_unknown_nested_subcommand_prints_usage(capsys):
    client = FakeClient()
    args = parser().parse_args(["groups", "rules"])
    with pytest.raises(SystemExit):
        SUB_HANDLERS["groups"]["rules"](client, args)
    assert "groups rules" in capsys.readouterr().err


# ── wiring ───────────────────────────────────────────────────────────────────


def test_company_only_groups_take_company_and_need_no_project():
    assert COMPANY_ONLY == {"team", "permission-templates", "company", "programs",
                            "notifications", "needs-you"}
    assert "members" not in COMPANY_ONLY and "groups" not in COMPANY_ONLY
    a = parser().parse_args(["team", "invitations", "list", "--company", "9"])
    assert a.company == 9
    a = parser().parse_args(["needs-you", "list", "--company", "9"])
    assert a.company == 9


def test_company_only_commands_never_touch_the_project_url():
    for argv, _, _, scoped, _, _ in CASES:
        if argv[0] in COMPANY_ONLY:
            assert scoped is not True, argv


def test_group_names_do_not_collide_with_existing_commands():
    existing = set(
        "login projects setup whoami set-project update project files tags folders move categorize "
        "activities steps progress deps tag-filters gantt tree forms fields submissions custom-objects "
        "resources rates assignments cost-codes budgets timesheets entries cost-entries locks links "
        "comments chat audit api".split()
    )
    assert not existing & set(SUB_HANDLERS)
    assert set(SUB_HANDLERS) == set(SUB_COMMAND_KEYS)
