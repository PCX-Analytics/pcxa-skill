"""`pcxa workflows|submission-statuses|revisions|automations|exports|file-exports|
folder-downloads|extraction|views|location-fields`.

Each command is parsed from a real argv and dispatched through the module's own
SUB_HANDLERS, so a test fails if the URL, the scope (company vs project), the
payload or the dispatch key drifts from what the backend registers.
"""

import argparse
import json

import pytest

from pcxa.commands import workflows
from pcxa.commands.workflows import COMPANY_ONLY, SUB_COMMAND_KEYS, SUB_HANDLERS, register_parser


class FakeClient:
    company_id = 3
    project_id = 7

    def __init__(self, response=None):
        self.calls = []
        self.response = response if response is not None else {"id": 1, "results": []}

    def _record(self, method, path, body=None, params=None, project_scoped=True):
        self.calls.append({"method": method, "path": path, "body": body, "params": params,
                           "project_scoped": project_scoped})
        return self.response

    def get(self, path, params=None, project_scoped=True, timeout=None):
        return self._record("GET", path, params=params, project_scoped=project_scoped)

    def post(self, path, json_data=None, project_scoped=True, timeout=None):
        return self._record("POST", path, body=json_data, project_scoped=project_scoped)

    def patch(self, path, json_data=None, project_scoped=True, timeout=None):
        return self._record("PATCH", path, body=json_data, project_scoped=project_scoped)

    def delete(self, path, json_data=None, project_scoped=True, timeout=None):
        return self._record("DELETE", path, body=json_data, project_scoped=project_scoped)

    @staticmethod
    def paginate_params(limit, offset=0):
        params = {"page_size": limit}
        if offset > 0:
            params["page"] = offset // limit + 1
        return params


def parse(argv):
    p = argparse.ArgumentParser()
    p.add_argument("--format", default="json")
    p.add_argument("--dry-run", action="store_true")
    sub = p.add_subparsers(dest="command")
    register_parser(sub)
    return p.parse_args(argv)


def run(argv, client=None):
    args = parse(argv)
    client = client or FakeClient()
    SUB_HANDLERS[args.command][getattr(args, SUB_COMMAND_KEYS[args.command])](client, args)
    return client


C, P = False, True  # project_scoped
PAGE = {"page_size": 25}

CASES = [
    # workflows: company templates + stages
    (["workflows", "templates", "--active", "true"], "GET", "workflow-templates/", C,
     None, {"page_size": 25, "is_active": "true"}),
    (["workflows", "template", "4"], "GET", "workflow-templates/4/", C, None, None),
    (["workflows", "template-create", "--name", "QA", "--auto-apply-forms", "8,9",
      "--stages", '[{"name":"PM","assignment_type":"submitter"}]'],
     "POST", "workflow-templates/", C,
     {"name": "QA", "auto_apply_to_forms": [8, 9],
      "stages": [{"name": "PM", "assignment_type": "submitter"}]}, None),
    (["workflows", "template-update", "4", "--description", "x", "--data", '{"is_active":false}'],
     "PATCH", "workflow-templates/4/", C, {"description": "x", "is_active": False}, None),
    (["workflows", "template-delete", "4"], "DELETE", "workflow-templates/4/", C, None, None),
    (["workflows", "template-activate", "4"], "POST", "workflow-templates/4/activate/", C, {}, None),
    (["workflows", "template-deactivate", "4"], "POST", "workflow-templates/4/deactivate/", C, {}, None),
    (["workflows", "template-version", "4"], "POST", "workflow-templates/4/create_version/", C, {}, None),
    (["workflows", "stages", "4"], "GET", "workflow-templates/4/stages/", C, None, {}),
    (["workflows", "stage-create", "4", "--name", "GC", "--order", "1",
      "--assignment-type", "specific_user", "--assigned-user", "12", "--approval-type", "all"],
     "POST", "workflow-templates/4/stages/", C,
     {"name": "GC", "order": 1, "assignment_type": "specific_user", "assigned_user": 12,
      "approval_type": "all"}, None),
    (["workflows", "stage-update", "4", "5", "--sla-hours", "48"], "PATCH",
     "workflow-templates/4/stages/5/", C, {"sla_hours": 48}, None),
    (["workflows", "stage-delete", "4", "5"], "DELETE", "workflow-templates/4/stages/5/", C, None, None),
    (["workflows", "stage-reorder", "4", "--order", "6,5"], "POST",
     "workflow-templates/4/stages/reorder/", C, {"stage_ids": [6, 5]}, None),
    # workflows: project instances
    (["workflows", "list", "--status", "in_progress", "--submission", "33"], "GET",
     "workflow-instances/", P, None, {"page_size": 25, "status": "in_progress", "form_submission": 33}),
    (["workflows", "get", "9"], "GET", "workflow-instances/9/", P, None, None),
    (["workflows", "start", "9"], "POST", "workflow-instances/9/start/", P, {}, None),
    (["workflows", "cancel", "9", "--reason", "dup"], "POST", "workflow-instances/9/cancel/", P,
     {"reason": "dup"}, None),
    (["workflows", "restart", "9"], "POST", "workflow-instances/9/restart/", P, {}, None),
    (["workflows", "stage-instances", "9"], "GET", "workflow-instances/9/stage-instances/", P, None, {}),
    (["workflows", "approve", "9", "2", "--comments", "ok"], "POST",
     "workflow-instances/9/stage-instances/2/approve/", P, {"comments": "ok"}, None),
    (["workflows", "reject", "9", "2"], "POST", "workflow-instances/9/stage-instances/2/reject/", P,
     {}, None),
    (["workflows", "request-changes", "9", "2", "--comments", "fix"], "POST",
     "workflow-instances/9/stage-instances/2/request-changes/", P, {"comments": "fix"}, None),
    (["workflows", "history", "9"], "GET", "workflow-instances/9/history/", P, None, {"page_size": 50}),
    # submission statuses (company) + revisions (project)
    (["submission-statuses", "list", "--phase", "draft"], "GET", "form-submission-statuses/", C,
     None, {"page_size": 100, "phase": "draft"}),
    (["submission-statuses", "get", "2"], "GET", "form-submission-statuses/2/", C, None, None),
    (["submission-statuses", "create", "--name", "In Review", "--key", "in_review",
      "--phase", "submitted", "--color", "blue"], "POST", "form-submission-statuses/", C,
     {"name": "In Review", "key": "in_review", "phase": "submitted", "color": "blue"}, None),
    (["submission-statuses", "update", "2", "--default", "true"], "PATCH",
     "form-submission-statuses/2/", C, {"is_default": True}, None),
    (["submission-statuses", "delete", "2"], "DELETE", "form-submission-statuses/2/", C, None, None),
    (["revisions", "list", "33"], "GET", "form-submissions/33/revisions/", P, None, {}),
    (["revisions", "get", "33", "71"], "GET", "form-submissions/33/revisions/71/", P, None, None),
    (["revisions", "compare", "33", "--a", "0", "--b", "2"], "GET",
     "form-submissions/33/revisions/compare/", P, None, {"a": 0, "b": 2}),
    # automations (company)
    (["automations", "list", "--project", "7", "--active", "false"], "GET", "automation-rules/", C,
     None, {"page_size": 25, "project": 7, "is_active": "false"}),
    (["automations", "get", "5"], "GET", "automation-rules/5/", C, None, None),
    (["automations", "create", "--name", "Notify", "--target-model", "Activity",
      "--trigger", "on_create", "--actions", '[{"type":"notify"}]', "--rule-project", "7"],
     "POST", "automation-rules/", C,
     {"name": "Notify", "target_model": "Activity", "trigger_type": "on_create",
      "project": 7, "actions_if_pass": [{"type": "notify"}]}, None),
    (["automations", "update", "5", "--execution-order", "2"], "PATCH", "automation-rules/5/", C,
     {"execution_order": 2}, None),
    (["automations", "delete", "5"], "DELETE", "automation-rules/5/", C, None, None),
    (["automations", "enable", "5"], "POST", "automation-rules/5/enable/", C, {}, None),
    (["automations", "disable", "5"], "POST", "automation-rules/5/disable/", C, {}, None),
    (["automations", "executions", "--rule", "5", "--status", "failed"], "GET",
     "automation-executions/", C, None, {"page_size": 25, "rule": 5, "status": "failed"}),
    (["automations", "execution", "88"], "GET", "automation-executions/88/", C, None, None),
    # exports
    (["exports", "create", "--type", "activities_table", "--name", "Wk 43", "--ids", "1,2,3"],
     "POST", "exports/", P,
     {"report_type": "activities_table", "report_name": "Wk 43", "record_ids": [1, 2, 3]}, None),
    (["exports", "list"], "GET", "exports/", P, None, PAGE),
    (["exports", "get", "11"], "GET", "exports/11/", P, None, None),
    (["exports", "presets", "--report-type", "activities_table"], "GET", "report-presets/", C,
     None, {"report_type": "activities_table"}),
    (["file-exports", "create", "--type", "parquet", "--filters", '{"folder":12}'], "POST",
     "file-exports/", P, {"format": "parquet", "filter_params": {"folder": 12}}, None),
    (["file-exports", "list"], "GET", "file-exports/", P, None, {}),
    (["file-exports", "get", "6"], "GET", "file-exports/6/", P, None, None),
    (["folder-downloads", "preflight", "--folder", "12"], "POST", "folder-downloads/preflight/", P,
     {"folder": 12}, None),
    (["folder-downloads", "create", "--folder", "12", "--reason", "handover"], "POST",
     "folder-downloads/", P, {"folder": 12, "reason": "handover"}, None),
    (["folder-downloads", "list"], "GET", "folder-downloads/", P, None, {}),
    (["folder-downloads", "get", "4"], "GET", "folder-downloads/4/", P, None, None),
    # extraction
    (["extraction", "profiles"], "GET", "extraction-profiles/", P, None, PAGE),
    (["extraction", "profile-get", "3"], "GET", "extraction-profiles/3/", P, None, None),
    (["extraction", "profile-create", "--name", "Invoices", "--prompt", "Pull totals",
      "--auto-apply", "yes"], "POST", "extraction-profiles/", P,
     {"name": "Invoices", "prompt_text": "Pull totals", "auto_apply_on_upload": True}, None),
    (["extraction", "profile-update", "3", "--form", "8"], "PATCH", "extraction-profiles/3/", P,
     {"form_schema": 8}, None),
    (["extraction", "profile-delete", "3"], "DELETE", "extraction-profiles/3/", P, None, None),
    (["extraction", "jobs", "--status", "pending,processing"], "GET", "extraction-jobs/", P, None,
     {"page_size": 25, "status": "pending,processing"}),
    (["extraction", "job-get", "21"], "GET", "extraction-jobs/21/", P, None, None),
    (["extraction", "job-create", "--name", "Batch", "--form", "8", "--files", "1,2"], "POST",
     "extraction-jobs/", P,
     {"name": "Batch", "output_mode": "form_submission", "form_id": 8, "file_ids": [1, 2]}, None),
    (["extraction", "job-cancel", "21"], "POST", "extraction-jobs/21/cancel/", P, {}, None),
    # views (both scopes) + location fields
    (["views", "list", "--target-context", "activities"], "GET", "generic-views/", P, None,
     {"page_size": 25, "target_context": "activities"}),
    (["views", "list", "--scope", "company"], "GET", "generic-views/", C, None, PAGE),
    (["views", "get", "5", "--scope", "company"], "GET", "generic-views/5/", C, None, None),
    (["views", "create", "--name", "Late", "--target-component", "data-grid",
      "--target-context", "activities", "--view-state", '{"filters":[]}', "--shared", "true"],
     "POST", "generic-views/", P,
     {"name": "Late", "target_component": "data-grid", "target_context": "activities",
      "view_state": {"filters": []}, "is_shared": True}, None),
    (["views", "update", "5", "--favorite", "true"], "PATCH", "generic-views/5/", P,
     {"is_favorite": True}, None),
    (["views", "delete", "5", "--scope", "company"], "DELETE", "generic-views/5/", C, None, None),
    (["location-fields", "list"], "GET", "location-fields/", P, None, None),
]


@pytest.mark.parametrize("argv,method,path,scoped,body,params", CASES,
                         ids=[" ".join(c[0][:2]) for c in CASES])
def test_command_hits_backend_route(argv, method, path, scoped, body, params, capsys):
    client = run(argv)
    assert client.calls == [{"method": method, "path": path, "body": body, "params": params,
                             "project_scoped": scoped}]


def test_every_handler_is_covered():
    covered = {(c[0][0], c[0][1]) for c in CASES}
    covered |= {("exports", "download"), ("file-exports", "download"),
                ("folder-downloads", "download")}
    declared = {(g, s) for g, subs in SUB_HANDLERS.items() for s in subs}
    assert declared - covered == set()


def test_company_only_groups_never_touch_a_project():
    for argv, _m, _p, scoped, _b, _q in CASES:
        if argv[0] in COMPANY_ONLY:
            assert scoped is False, argv


def test_dry_run_sends_nothing(capsys):
    client = run(["--dry-run", "submission-statuses", "create", "--name", "Closed",
                  "--key", "closed", "--phase", "closed"])
    assert client.calls == []
    out = capsys.readouterr().out
    assert out.startswith("Would CREATE submission status")
    assert json.loads(out.split(":", 1)[1]) == {"name": "Closed", "key": "closed", "phase": "closed"}


@pytest.mark.parametrize("argv", [
    ["--dry-run", "workflows", "approve", "9", "2"],
    ["--dry-run", "automations", "delete", "5"],
    ["--dry-run", "views", "update", "5", "--name", "x"],
])
def test_dry_run_covers_actions_deletes_and_updates(argv, capsys):
    client = run(argv)
    assert client.calls == []
    assert capsys.readouterr().out.startswith("Would ")


@pytest.mark.parametrize("argv,flag", [
    (["workflows", "template-create", "--name", "x", "--stages", "[{bad"], "--stages"),
    (["views", "create", "--name", "x", "--view-state", "{"], "--view-state"),
    (["automations", "update", "5", "--data", "not json"], "--data"),
    (["file-exports", "create", "--filters", "{'single':1}"], "--filters"),
])
def test_bad_json_exits_cleanly_without_a_request(argv, flag, capsys):
    client = FakeClient()
    with pytest.raises(SystemExit) as exc:
        run(argv, client)
    assert exc.value.code == 1
    assert client.calls == []
    assert f"Invalid JSON for {flag}" in capsys.readouterr().err


def test_data_must_be_an_object(capsys):
    with pytest.raises(SystemExit):
        run(["views", "update", "5", "--data", "[1]"])
    assert "--data must be a JSON object" in capsys.readouterr().err


def test_template_create_needs_a_stage(capsys):
    client = FakeClient()
    with pytest.raises(SystemExit):
        run(["workflows", "template-create", "--name", "x", "--stages", "[]"], client)
    assert client.calls == []


def test_update_with_no_fields_refuses(capsys):
    client = FakeClient()
    with pytest.raises(SystemExit):
        run(["automations", "update", "5"], client)
    assert client.calls == []
    assert "No fields to update" in capsys.readouterr().err


@pytest.mark.parametrize("extra,err", [
    ([], "exactly one of"),
    (["--files", "1", "--photos", "2"], "exactly one of"),
    (["--output-mode", "form_submission", "--files", "1"], "--form is required"),
])
def test_extraction_job_validates_before_sending(extra, err, capsys):
    client = FakeClient()
    with pytest.raises(SystemExit):
        run(["extraction", "job-create", "--name", "x"] + extra, client)
    assert client.calls == []
    assert err in capsys.readouterr().err


class FakeStream:
    def __init__(self, chunks):
        self.chunks = chunks
        self.headers = {}

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size=8192):
        return iter(self.chunks)


@pytest.fixture
def fake_storage(monkeypatch):
    fetched = []

    def fake_get(url, stream=False, timeout=None):
        fetched.append(url)
        return FakeStream([b"%PDF-", b"1.7"])

    monkeypatch.setattr(workflows.requests, "get", fake_get)
    return fetched


@pytest.mark.parametrize("argv,api_path", [
    (["exports", "download", "11"], "exports/11/download/"),
    (["file-exports", "download", "6"], "file-exports/6/download/"),
    (["folder-downloads", "download", "4", "--part", "2"], "folder-downloads/4/parts/2/download/"),
    (["folder-downloads", "download", "4", "--manifest"], "folder-downloads/4/manifest/"),
])
def test_download_writes_signed_url_bytes_to_out(argv, api_path, fake_storage, tmp_path, capsys):
    out = tmp_path / "result.bin"
    client = FakeClient({"download_url": "https://r2.example/signed?x=1", "filename": "r.pdf",
                         "expires_in": 120})
    run(argv + ["--out", str(out)], client)
    assert client.calls == [{"method": "GET", "path": api_path, "body": None, "params": None,
                             "project_scoped": True}]
    assert fake_storage == ["https://r2.example/signed?x=1"]
    assert out.read_bytes() == b"%PDF-1.7"
    assert json.loads(capsys.readouterr().out)["bytes"] == 8


def test_download_without_url_fails_instead_of_writing(fake_storage, tmp_path, capsys):
    out = tmp_path / "r.pdf"
    with pytest.raises(SystemExit):
        run(["exports", "download", "11", "--out", str(out)], FakeClient({"error": "x"}))
    assert not out.exists()
    assert fake_storage == []


def test_folder_download_needs_part_or_manifest(fake_storage, capsys):
    client = FakeClient()
    with pytest.raises(SystemExit):
        run(["folder-downloads", "download", "4"], client)
    assert client.calls == []
    assert fake_storage == []


def test_parser_groups_match_dispatch_tables():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="command")
    register_parser(sub)
    for group, key in SUB_COMMAND_KEYS.items():
        group_parser = sub.choices[group]
        nested = [a for a in group_parser._actions if isinstance(a, argparse._SubParsersAction)]
        assert nested[0].dest == key
        assert set(nested[0].choices) == set(SUB_HANDLERS[group])


def test_table_mode_renders_lists_and_details(capsys):
    rows = {"count": 1, "results": [{"id": 4, "name": "QA", "version": 2, "is_active": True,
                                     "created_at": "2026-10-01", "stages": [{"id": 1}]}]}
    run(["--format", "table", "workflows", "templates"], FakeClient(rows))
    out = capsys.readouterr().out
    assert "Workflow templates: 1 of 1" in out and "QA" in out
    run(["--format", "table", "workflows", "template", "4"], FakeClient(rows["results"][0]))
    assert "name: QA" in capsys.readouterr().out
    run(["--format", "table", "workflows", "template-delete", "4"])
    assert capsys.readouterr().out.strip() == "Deleted workflow template 4"
