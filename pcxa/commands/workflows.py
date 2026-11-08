"""Form workflows, submission statuses and revisions, automations, exports,
AI extraction and saved views.

These back the web UI's form-template workflow tab, the submission page
(status badge + revision history), Automations, the "Recent exports" dialogs,
AI jobs and every grid's saved-view picker. Scope is fixed per resource:

* company: ``workflow-templates/`` (+ ``stages/``), ``form-submission-statuses/``,
  ``automation-rules/``, ``automation-executions/``, ``report-presets/``
* project: ``workflow-instances/`` (+ ``stage-instances/``, ``history/``),
  ``form-submissions/{id}/revisions/``, ``exports/``, ``file-exports/``,
  ``folder-downloads/``, ``extraction-profiles/``, ``extraction-jobs/``,
  ``location-fields/``
* both: ``generic-views/`` (``views --scope company|project``)

Exports are async: ``create`` returns a pending job; poll ``get`` until
``status`` is ``completed``, then ``download`` asks the API for a short-lived
signed URL and streams the bytes to ``--out``. The URL is never printed.
"""

import json
import sys
from pathlib import Path

from pcxa._http import requests
from pcxa._output import out_json, out_table


def _json_arg(value, flag):
    """Parse a JSON-valued flag, or exit with a clean error naming the flag."""
    try:
        return json.loads(value)
    except json.JSONDecodeError as e:
        print(f"Invalid JSON for {flag}: {e}", file=sys.stderr)
        sys.exit(1)


def _int_list(value, flag):
    try:
        return [int(x) for x in value.split(",") if x.strip()]
    except ValueError:
        print(f"{flag} must be comma-separated integer ids, got {value!r}", file=sys.stderr)
        sys.exit(1)


def _with_data(payload, args):
    """Merge ``--data JSON`` over the typed flags (it wins on conflicts)."""
    if getattr(args, "data", None):
        extra = _json_arg(args.data, "--data")
        if not isinstance(extra, dict):
            print("--data must be a JSON object", file=sys.stderr)
            sys.exit(1)
        payload.update(extra)
    return payload


def _put(payload, key, value):
    if value is not None:
        payload[key] = value


def _send(client, args, method, path, payload, what, project_scoped=True):
    """POST/PATCH/DELETE honouring ``--dry-run``; prints the result."""
    verb = {"post": "CREATE", "patch": "UPDATE", "delete": "DELETE"}[method]
    if verb == "UPDATE" and payload is not None and not payload:
        print("No fields to update.", file=sys.stderr)
        sys.exit(1)
    if args.dry_run:
        body = f": {json.dumps(payload, indent=2)}" if payload else ""
        print(f"Would {verb} {what}{body}")
        return None
    data = getattr(client, method)(path, payload, project_scoped=project_scoped)
    if method == "delete":
        print(f"Deleted {what}")
        return data
    if args.format == "json":
        out_json(data)
    else:
        ident = data.get("id", "") if isinstance(data, dict) else ""
        print(f"{verb.title()}d {what} {ident}".rstrip())
    return data


def _action(client, args, path, payload, what, project_scoped=True):
    """POST to a detail @action (activate, approve, cancel...) with dry-run."""
    if args.dry_run:
        body = f": {json.dumps(payload)}" if payload else ""
        print(f"Would {what}{body}")
        return
    data = client.post(path, payload or {}, project_scoped=project_scoped)
    if args.format == "json":
        out_json(data)
    else:
        status = data.get("status", "") if isinstance(data, dict) else ""
        print(f"{what}: {status or 'ok'}")


def _show(client, args, path, project_scoped=True):
    data = client.get(path, project_scoped=project_scoped)
    if args.format == "json":
        out_json(data)
    else:
        for k, v in data.items():
            if isinstance(v, (dict, list)):
                v = json.dumps(v, default=str)[:120]
            print(f"  {k}: {v}")


def _list(client, args, path, params, title, cols, project_scoped=True):
    """GET a (possibly paginated) list; table mode prints ``cols``."""
    data = client.get(path, params, project_scoped=project_scoped)
    if args.format == "json":
        out_json(data)
        return
    results = data.get("results", data) if isinstance(data, dict) else data
    total = data.get("count") if isinstance(data, dict) else None
    print(f"{title}: {len(results)}" + (f" of {total}" if total is not None else "") + "\n")
    rows = [{c: str(r.get(c, "") if r.get(c) is not None else "")[:40] for c in cols} for r in results]
    out_table(rows, cols)


def _page(client, args, **filters):
    params = client.paginate_params(args.limit, args.offset)
    params.update({k: v for k, v in filters.items() if v is not None})
    return params


def _bool_param(value):
    return None if value is None else str(value).lower()


def _download(client, args, path, project_scoped=True):
    """Ask the API for a signed URL, then stream its bytes to ``--out``.

    The download endpoints all answer ``{download_url, filename, expires_in}``;
    a not-ready job is a 400/409 from the API, not an empty file here.
    """
    info = client.get(path, project_scoped=project_scoped)
    url = info.get("download_url")
    if not url:
        print(f"API returned no download URL: {json.dumps(info)}", file=sys.stderr)
        sys.exit(1)
    out_path = Path(args.out or info.get("filename") or "download")
    resp = requests.get(url, stream=True, timeout=120)
    resp.raise_for_status()
    written = 0
    with open(out_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)
            written += len(chunk)
    result = {"path": str(out_path), "bytes": written, "filename": info.get("filename")}
    if info.get("sha256"):  # the folder-download manifest carries its own hash
        result["sha256"] = info["sha256"]
    if args.format == "json":
        out_json(result)
    else:
        print(f"Saved {out_path} ({written:,} bytes)")


# ═══════════════════════════════════════════════════════════════════════════════
# WORKFLOWS — company templates + stages, project instances + stage decisions
# ═══════════════════════════════════════════════════════════════════════════════


def _tpl(args, suffix=""):
    return f"workflow-templates/{args.template_id}/{suffix}"


def cmd_workflows_templates(client, args):
    """List company workflow templates."""
    params = _page(client, args, is_active=_bool_param(args.active))
    _list(client, args, "workflow-templates/", params, "Workflow templates",
          ["id", "name", "version", "is_active", "created_at"], project_scoped=False)


def cmd_workflows_template(client, args):
    """Workflow template detail, including its stages."""
    _show(client, args, _tpl(args), project_scoped=False)


def _template_payload(args):
    payload = {}
    _put(payload, "name", args.name)
    _put(payload, "description", args.description)
    if args.auto_apply_forms is not None:
        payload["auto_apply_to_forms"] = _int_list(args.auto_apply_forms, "--auto-apply-forms")
    return payload


def cmd_workflows_template_create(client, args):
    """Create a template with its stages in one call (the API requires >= 1 stage)."""
    payload = _template_payload(args)
    stages = _json_arg(args.stages, "--stages")
    if not isinstance(stages, list) or not stages:
        print("--stages must be a non-empty JSON list of stage objects", file=sys.stderr)
        sys.exit(1)
    payload["stages"] = stages
    _send(client, args, "post", "workflow-templates/", _with_data(payload, args),
          "workflow template", project_scoped=False)


def cmd_workflows_template_update(client, args):
    """Update a template's name, description or auto-apply forms."""
    _send(client, args, "patch", _tpl(args), _with_data(_template_payload(args), args),
          f"workflow template {args.template_id}", project_scoped=False)


def cmd_workflows_template_delete(client, args):
    _send(client, args, "delete", _tpl(args), None,
          f"workflow template {args.template_id}", project_scoped=False)


def cmd_workflows_template_activate(client, args):
    _action(client, args, _tpl(args, "activate/"), None,
            f"ACTIVATE workflow template {args.template_id}", project_scoped=False)


def cmd_workflows_template_deactivate(client, args):
    _action(client, args, _tpl(args, "deactivate/"), None,
            f"DEACTIVATE workflow template {args.template_id}", project_scoped=False)


def cmd_workflows_template_version(client, args):
    """Copy a template in use into a new version (running instances keep the old)."""
    _action(client, args, _tpl(args, "create_version/"), None,
            f"VERSION workflow template {args.template_id}", project_scoped=False)


def cmd_workflows_stages(client, args):
    """List a template's stages in order."""
    _list(client, args, _tpl(args, "stages/"), {}, f"Stages of template {args.template_id}",
          ["id", "order", "name", "assignment_type", "assigned_user", "approval_type"],
          project_scoped=False)


def _stage_payload(args):
    payload = {}
    for key in ("name", "order", "description", "assignment_type", "assigned_user",
                "approval_type", "required_approvers_count", "rejection_behavior", "sla_hours"):
        _put(payload, key, getattr(args, key, None))
    return payload


def cmd_workflows_stage_create(client, args):
    _send(client, args, "post", _tpl(args, "stages/"), _with_data(_stage_payload(args), args),
          f"stage on template {args.template_id}", project_scoped=False)


def cmd_workflows_stage_update(client, args):
    _send(client, args, "patch", _tpl(args, f"stages/{args.stage_id}/"),
          _with_data(_stage_payload(args), args), f"stage {args.stage_id}", project_scoped=False)


def cmd_workflows_stage_delete(client, args):
    _send(client, args, "delete", _tpl(args, f"stages/{args.stage_id}/"), None,
          f"stage {args.stage_id}", project_scoped=False)


def cmd_workflows_stage_reorder(client, args):
    ids = _int_list(args.order, "--order")
    _action(client, args, _tpl(args, "stages/reorder/"), {"stage_ids": ids},
            f"REORDER stages of template {args.template_id}", project_scoped=False)


def _inst(args, suffix=""):
    return f"workflow-instances/{args.instance_id}/{suffix}"


def cmd_workflows_list(client, args):
    """List workflow instances (one per submission under a workflow) in the project."""
    params = _page(client, args, status=args.status, workflow_template=args.template,
                   form_submission=args.submission)
    _list(client, args, "workflow-instances/", params, "Workflow instances",
          ["id", "workflow_template_name", "form_submission", "status", "current_stage_name",
           "started_at"])


def cmd_workflows_get(client, args):
    _show(client, args, _inst(args))


def cmd_workflows_start(client, args):
    _action(client, args, _inst(args, "start/"), None, f"START workflow {args.instance_id}")


def cmd_workflows_cancel(client, args):
    payload = {"reason": args.reason} if args.reason else {}
    _action(client, args, _inst(args, "cancel/"), payload, f"CANCEL workflow {args.instance_id}")


def cmd_workflows_restart(client, args):
    """Restart a completed or cancelled workflow from stage 1."""
    _action(client, args, _inst(args, "restart/"), None, f"RESTART workflow {args.instance_id}")


def cmd_workflows_stage_instances(client, args):
    """Progress through each stage: status, assignees, decisions."""
    params = {"status": args.status} if args.status else {}
    _list(client, args, _inst(args, "stage-instances/"), params,
          f"Stage instances of workflow {args.instance_id}",
          ["id", "stage_order", "stage_name", "status", "approvals_received",
           "rejections_received", "entered_at"])


def _decide(client, args, url_action, verb):
    payload = {"comments": args.comments} if args.comments else {}
    _action(client, args, _inst(args, f"stage-instances/{args.stage_instance_id}/{url_action}/"),
            payload, f"{verb} stage instance {args.stage_instance_id}")


def cmd_workflows_approve(client, args):
    _decide(client, args, "approve", "APPROVE")


def cmd_workflows_reject(client, args):
    _decide(client, args, "reject", "REJECT")


def cmd_workflows_request_changes(client, args):
    _decide(client, args, "request-changes", "REQUEST CHANGES on")


def cmd_workflows_history(client, args):
    params = _page(client, args, event_type=args.event_type)
    _list(client, args, _inst(args, "history/"), params, f"History of workflow {args.instance_id}",
          ["id", "timestamp", "event_type", "actor_name", "stage_name", "comments"])


# ═══════════════════════════════════════════════════════════════════════════════
# SUBMISSION STATUSES (company) and REVISIONS (project, read-only)
# ═══════════════════════════════════════════════════════════════════════════════


def cmd_statuses_list(client, args):
    params = _page(client, args, phase=args.phase)
    _list(client, args, "form-submission-statuses/", params, "Submission statuses",
          ["id", "order", "key", "name", "phase", "color", "is_default", "is_system"],
          project_scoped=False)


def cmd_statuses_get(client, args):
    _show(client, args, f"form-submission-statuses/{args.status_id}/", project_scoped=False)


def _status_payload(args):
    payload = {}
    for key in ("name", "key", "phase", "description", "color", "icon", "order"):
        _put(payload, key, getattr(args, key, None))
    _put(payload, "is_default", args.default)
    return payload


def cmd_statuses_create(client, args):
    _send(client, args, "post", "form-submission-statuses/", _with_data(_status_payload(args), args),
          "submission status", project_scoped=False)


def cmd_statuses_update(client, args):
    _send(client, args, "patch", f"form-submission-statuses/{args.status_id}/",
          _with_data(_status_payload(args), args), f"submission status {args.status_id}",
          project_scoped=False)


def cmd_statuses_delete(client, args):
    """Delete a status (system statuses answer 403)."""
    _send(client, args, "delete", f"form-submission-statuses/{args.status_id}/", None,
          f"submission status {args.status_id}", project_scoped=False)


def cmd_revisions_list(client, args):
    _list(client, args, f"form-submissions/{args.submission_id}/revisions/", {},
          f"Revisions of submission {args.submission_id}",
          ["id", "revision_number", "source", "change_summary", "created_by_name", "created_at",
           "is_current"])


def cmd_revisions_get(client, args):
    _show(client, args, f"form-submissions/{args.submission_id}/revisions/{args.revision_id}/")


def cmd_revisions_compare(client, args):
    """Field-level diff between two revision NUMBERS (not ids)."""
    data = client.get(f"form-submissions/{args.submission_id}/revisions/compare/",
                      {"a": args.a, "b": args.b})
    out_json(data)


# ═══════════════════════════════════════════════════════════════════════════════
# AUTOMATIONS (company)
# ═══════════════════════════════════════════════════════════════════════════════


def cmd_automations_list(client, args):
    params = _page(client, args, project=args.project_filter, target_model=args.target_model,
                   trigger_type=args.trigger, is_active=_bool_param(args.active),
                   source_form=args.source_form)
    _list(client, args, "automation-rules/", params, "Automation rules",
          ["id", "name", "trigger_type", "project", "is_active", "execution_order"],
          project_scoped=False)


def cmd_automations_get(client, args):
    _show(client, args, f"automation-rules/{args.rule_id}/", project_scoped=False)


def _rule_payload(args):
    payload = {}
    for key in ("name", "description", "target_model", "trigger_type", "execution_order"):
        _put(payload, key, getattr(args, key, None))
    _put(payload, "project", args.rule_project)
    _put(payload, "source_form", args.source_form)
    if args.actions is not None:
        payload["actions_if_pass"] = _json_arg(args.actions, "--actions")
    if args.condition is not None:
        payload["condition_definition"] = _json_arg(args.condition, "--condition")
    return payload


def cmd_automations_create(client, args):
    _send(client, args, "post", "automation-rules/", _with_data(_rule_payload(args), args),
          "automation rule", project_scoped=False)


def cmd_automations_update(client, args):
    _send(client, args, "patch", f"automation-rules/{args.rule_id}/",
          _with_data(_rule_payload(args), args), f"automation rule {args.rule_id}",
          project_scoped=False)


def cmd_automations_delete(client, args):
    _send(client, args, "delete", f"automation-rules/{args.rule_id}/", None,
          f"automation rule {args.rule_id}", project_scoped=False)


def cmd_automations_enable(client, args):
    _action(client, args, f"automation-rules/{args.rule_id}/enable/", None,
            f"ENABLE automation rule {args.rule_id}", project_scoped=False)


def cmd_automations_disable(client, args):
    _action(client, args, f"automation-rules/{args.rule_id}/disable/", None,
            f"DISABLE automation rule {args.rule_id}", project_scoped=False)


def cmd_automations_executions(client, args):
    params = _page(client, args, rule=args.rule, status=args.status,
                   trigger_event=args.trigger_event)
    _list(client, args, "automation-executions/", params, "Automation executions",
          ["id", "rule", "rule_name", "trigger_model_name", "trigger_object_id", "trigger_event",
           "status", "created_at"], project_scoped=False)


def cmd_automations_execution(client, args):
    _show(client, args, f"automation-executions/{args.execution_id}/", project_scoped=False)


# ═══════════════════════════════════════════════════════════════════════════════
# EXPORTS — PDF report jobs, file-list exports, folder downloads
# ═══════════════════════════════════════════════════════════════════════════════

_JOB_COLS = ["id", "status", "created_at", "completed_at", "expires_at"]


def cmd_exports_create(client, args):
    """Queue a PDF report over record ids (e.g. activities or form submissions)."""
    payload = {"report_type": args.report_type, "report_name": args.name,
               "record_ids": _int_list(args.ids, "--ids")}
    if args.options is not None:
        payload["options"] = _json_arg(args.options, "--options")
    _put(payload, "preset_id", args.preset)
    _send(client, args, "post", "exports/", _with_data(payload, args), "export job")


def cmd_exports_list(client, args):
    _list(client, args, "exports/", _page(client, args), "Exports",
          ["id", "report_type", "report_name", "record_count"] + _JOB_COLS[1:])


def cmd_exports_get(client, args):
    _show(client, args, f"exports/{args.job_id}/")


def cmd_exports_download(client, args):
    _download(client, args, f"exports/{args.job_id}/download/")


def cmd_exports_presets(client, args):
    params = {"report_type": args.report_type} if args.report_type else {}
    _list(client, args, "report-presets/", params, "Report presets",
          ["id", "name", "report_type", "is_default", "is_shared"], project_scoped=False)


def cmd_file_exports_create(client, args):
    """Snapshot the file list (same filters as `files list`) to CSV or Parquet."""
    payload = {"format": args.export_format}
    if args.filters is not None:
        payload["filter_params"] = _json_arg(args.filters, "--filters")
    _send(client, args, "post", "file-exports/", payload, "file export")


def cmd_file_exports_list(client, args):
    _list(client, args, "file-exports/", {}, "File exports",
          ["id", "format", "record_count", "file_size"] + _JOB_COLS[1:])


def cmd_file_exports_get(client, args):
    _show(client, args, f"file-exports/{args.job_id}/")


def cmd_file_exports_download(client, args):
    _download(client, args, f"file-exports/{args.job_id}/download/")


def _folder_request(args):
    payload = {"folder": args.folder}
    if args.filters is not None:
        payload["filters"] = _json_arg(args.filters, "--filters")
    return payload


def cmd_folder_downloads_preflight(client, args):
    """Size, file count and caps for a folder ZIP, without starting one."""
    data = client.post("folder-downloads/preflight/", _folder_request(args))
    out_json(data)


def cmd_folder_downloads_create(client, args):
    payload = _folder_request(args)
    _put(payload, "reason", args.reason)
    _send(client, args, "post", "folder-downloads/", payload, f"folder download of {args.folder}")


def cmd_folder_downloads_list(client, args):
    _list(client, args, "folder-downloads/", {}, "Folder downloads",
          ["id", "folder", "folder_name", "total_files", "total_bytes"] + _JOB_COLS[1:])


def cmd_folder_downloads_get(client, args):
    """Job status plus its volumes (parts); each part id feeds `download --part`."""
    _show(client, args, f"folder-downloads/{args.job_id}/")


def cmd_folder_downloads_download(client, args):
    """Fetch ONE volume, or the manifest. One part per call: each is re-authorized
    and audited server-side, so there is deliberately no download-all."""
    if args.manifest:
        _download(client, args, f"folder-downloads/{args.job_id}/manifest/")
    elif args.part is not None:
        _download(client, args, f"folder-downloads/{args.job_id}/parts/{args.part}/download/")
    else:
        print("Pass --part PART_ID (from `folder-downloads get`) or --manifest", file=sys.stderr)
        sys.exit(1)


# ═══════════════════════════════════════════════════════════════════════════════
# EXTRACTION — AI extraction profiles and jobs (project)
# ═══════════════════════════════════════════════════════════════════════════════


def cmd_extraction_profiles(client, args):
    _list(client, args, "extraction-profiles/", _page(client, args), "Extraction profiles",
          ["id", "name", "output_mode", "applies_to", "auto_apply_on_upload", "is_active"])


def cmd_extraction_profile_get(client, args):
    _show(client, args, f"extraction-profiles/{args.profile_id}/")


def _profile_payload(args):
    payload = {}
    for key in ("name", "description", "prompt_text", "output_mode"):
        _put(payload, key, getattr(args, key, None))
    _put(payload, "form_schema", args.form)
    _put(payload, "auto_apply_on_upload", args.auto_apply)
    return payload


def cmd_extraction_profile_create(client, args):
    _send(client, args, "post", "extraction-profiles/", _with_data(_profile_payload(args), args),
          "extraction profile")


def cmd_extraction_profile_update(client, args):
    _send(client, args, "patch", f"extraction-profiles/{args.profile_id}/",
          _with_data(_profile_payload(args), args), f"extraction profile {args.profile_id}")


def cmd_extraction_profile_delete(client, args):
    _send(client, args, "delete", f"extraction-profiles/{args.profile_id}/", None,
          f"extraction profile {args.profile_id}")


def cmd_extraction_jobs(client, args):
    params = _page(client, args, status=args.status)
    _list(client, args, "extraction-jobs/", params, "Extraction jobs",
          ["id", "name", "status", "output_mode", "total_items", "completed_items", "created_at"])


def cmd_extraction_job_get(client, args):
    _show(client, args, f"extraction-jobs/{args.job_id}/")


def cmd_extraction_job_create(client, args):
    """Queue AI extraction over exactly one kind of document (files, photos or drawings)."""
    payload = {"name": args.name, "output_mode": args.output_mode}
    _put(payload, "form_id", args.form)
    _put(payload, "profile_id", args.profile)
    _put(payload, "prompt_override", args.prompt)
    for flag, key in (("files", "file_ids"), ("photos", "photo_ids"), ("drawings", "drawing_ids")):
        value = getattr(args, flag)
        if value:
            payload[key] = _int_list(value, f"--{flag}")
    _with_data(payload, args)
    kinds = [k for k in ("file_ids", "photo_ids", "drawing_ids") if payload.get(k)]
    if len(kinds) != 1:
        print("Pass exactly one of --files, --photos or --drawings", file=sys.stderr)
        sys.exit(1)
    if payload.get("output_mode") == "form_submission" and not payload.get("form_id"):
        print("--form is required with --output-mode form_submission", file=sys.stderr)
        sys.exit(1)
    _send(client, args, "post", "extraction-jobs/", payload, "extraction job")


def cmd_extraction_job_cancel(client, args):
    _action(client, args, f"extraction-jobs/{args.job_id}/cancel/", None,
            f"CANCEL extraction job {args.job_id}")


# ═══════════════════════════════════════════════════════════════════════════════
# VIEWS — saved grid views (generic-views), company or project scope
# ═══════════════════════════════════════════════════════════════════════════════


def _scoped(args):
    return getattr(args, "scope", "project") != "company"


def cmd_views_list(client, args):
    params = _page(client, args, target_component=args.target_component,
                   target_context=args.target_context, search=args.search)
    _list(client, args, "generic-views/", params, "Saved views",
          ["id", "name", "target_component", "target_context", "project", "is_default",
           "is_shared"], project_scoped=_scoped(args))


def cmd_views_get(client, args):
    _show(client, args, f"generic-views/{args.view_id}/", project_scoped=_scoped(args))


def _view_payload(args):
    payload = {}
    for key in ("name", "description", "target_component", "target_context"):
        _put(payload, key, getattr(args, key, None))
    if args.view_state is not None:
        payload["view_state"] = _json_arg(args.view_state, "--view-state")
    _put(payload, "is_shared", args.shared)
    _put(payload, "is_default", args.default)
    _put(payload, "is_favorite", args.favorite)
    return payload


def cmd_views_create(client, args):
    _send(client, args, "post", "generic-views/", _with_data(_view_payload(args), args),
          "saved view", project_scoped=_scoped(args))


def cmd_views_update(client, args):
    _send(client, args, "patch", f"generic-views/{args.view_id}/",
          _with_data(_view_payload(args), args), f"saved view {args.view_id}",
          project_scoped=_scoped(args))


def cmd_views_delete(client, args):
    _send(client, args, "delete", f"generic-views/{args.view_id}/", None,
          f"saved view {args.view_id}", project_scoped=_scoped(args))


def cmd_location_fields_list(client, args):
    """Fields flagged as location fields, in location order (read-only endpoint)."""
    _list(client, args, "location-fields/", None, "Location fields",
          ["id", "key", "label", "field_type", "location_order"])


# ═══════════════════════════════════════════════════════════════════════════════
# PARSER
# ═══════════════════════════════════════════════════════════════════════════════


def _bool(x):
    return x.lower() in ("true", "1", "yes")


def _paging(p, limit=25):
    p.add_argument("--limit", type=int, default=limit)
    p.add_argument("--offset", type=int, default=0)


def _company(p):
    p.add_argument("--company", type=int, help="Company ID (default: the profile/.pcxa company)")


def _data(p):
    p.add_argument("--data", help="Extra fields as a JSON object, merged last (wins)")


def _out(p):
    p.add_argument("--out", "-o", help="Output path (default: the server's filename, in cwd)")


def register_parser(sub):
    # ── workflows ──
    wf = sub.add_parser(
        "workflows",
        help="Form approval workflows: company templates/stages, project instances + decisions",
    ).add_subparsers(dest="workflows_command")

    p = wf.add_parser("templates", help="List workflow templates (company)")
    p.add_argument("--active", type=_bool, metavar="BOOL")
    _paging(p)
    p = wf.add_parser("template", help="Workflow template detail (company)")
    p.add_argument("template_id", type=int)

    def _tpl_fields(p, create):
        p.add_argument("--name", required=create)
        p.add_argument("--description")
        p.add_argument("--auto-apply-forms", dest="auto_apply_forms",
                       help="Form ids the template auto-applies to (comma-sep, empty=clear)")
        _data(p)

    p = wf.add_parser("template-create", help="Create template + stages")
    _tpl_fields(p, True)
    p.add_argument("--stages", required=True,
                   help='JSON list, >= 1: [{"name":"PM review","assignment_type":"specific_user",'
                        '"assigned_user_id":12}] (assignment_type: specific_user|form_field|submitter)')
    p = wf.add_parser("template-update", help="Update template")
    p.add_argument("template_id", type=int)
    _tpl_fields(p, False)
    for name, text in (("template-delete", "Delete template"),
                       ("template-activate", "Activate template"),
                       ("template-deactivate", "Deactivate template"),
                       ("template-version", "Create a new version of a template")):
        wf.add_parser(name, help=text).add_argument("template_id", type=int)

    p = wf.add_parser("stages", help="List a template's stages")
    p.add_argument("template_id", type=int)

    def _stage_fields(p, create):
        p.add_argument("--name", required=create)
        p.add_argument("--order", type=int, required=create, help="0-based position")
        p.add_argument("--description")
        p.add_argument("--assignment-type", dest="assignment_type",
                       choices=["specific_user", "form_field", "submitter"])
        p.add_argument("--assigned-user", dest="assigned_user", type=int, help="User id")
        p.add_argument("--approval-type", dest="approval_type", choices=["single", "all", "count"])
        p.add_argument("--required-approvers", dest="required_approvers_count", type=int)
        p.add_argument("--rejection-behavior", dest="rejection_behavior",
                       choices=["return_to_submitter", "return_to_previous", "return_to_specific",
                                "cancel_workflow"])
        p.add_argument("--sla-hours", dest="sla_hours", type=int)
        _data(p)

    p = wf.add_parser("stage-create", help="Add a stage to a template")
    p.add_argument("template_id", type=int)
    _stage_fields(p, True)
    p = wf.add_parser("stage-update", help="Update a template stage")
    p.add_argument("template_id", type=int)
    p.add_argument("stage_id", type=int)
    _stage_fields(p, False)
    p = wf.add_parser("stage-delete", help="Delete a template stage")
    p.add_argument("template_id", type=int)
    p.add_argument("stage_id", type=int)
    p = wf.add_parser("stage-reorder", help="Reorder stages (stage ids, comma-sep, in order)")
    p.add_argument("template_id", type=int)
    p.add_argument("--order", required=True)

    p = wf.add_parser("list", help="List workflow instances (project)")
    p.add_argument("--status")
    p.add_argument("--template", type=int, help="Workflow template id")
    p.add_argument("--submission", type=int, help="Form submission id")
    _paging(p)
    for name, text in (("get", "Workflow instance detail"),
                       ("start", "Start a pending workflow"),
                       ("restart", "Restart a completed/cancelled workflow")):
        wf.add_parser(name, help=text).add_argument("instance_id", type=int)
    p = wf.add_parser("cancel", help="Cancel a workflow")
    p.add_argument("instance_id", type=int)
    p.add_argument("--reason")
    p = wf.add_parser("stage-instances", help="Per-stage progress of a workflow instance")
    p.add_argument("instance_id", type=int)
    p.add_argument("--status")
    for name, text in (("approve", "Approve the stage"), ("reject", "Reject the stage"),
                       ("request-changes", "Send the stage back for changes")):
        p = wf.add_parser(name, help=text)
        p.add_argument("instance_id", type=int)
        p.add_argument("stage_instance_id", type=int, help="From `workflows stage-instances`")
        p.add_argument("--comments")
    p = wf.add_parser("history", help="Workflow instance event history")
    p.add_argument("instance_id", type=int)
    p.add_argument("--event-type", dest="event_type")
    _paging(p, 50)

    # ── submission-statuses (company) ──
    st = sub.add_parser(
        "submission-statuses", help="Company form-submission status definitions (draft/submitted/closed)",
    ).add_subparsers(dest="submission_statuses_command")
    p = st.add_parser("list", help="List statuses")
    p.add_argument("--phase", choices=["draft", "submitted", "closed"])
    _paging(p, 100)
    _company(p)
    p = st.add_parser("get", help="Status detail")
    p.add_argument("status_id", type=int)
    _company(p)

    def _status_fields(p, create):
        p.add_argument("--name", required=create)
        p.add_argument("--key", required=create, help="Slug, e.g. in_review")
        p.add_argument("--phase", required=create, choices=["draft", "submitted", "closed"])
        p.add_argument("--description")
        p.add_argument("--color", help="gray|blue|yellow|orange|green|red")
        p.add_argument("--icon")
        p.add_argument("--order", type=int)
        p.add_argument("--default", type=_bool, metavar="BOOL")
        _data(p)
        _company(p)

    _status_fields(st.add_parser("create", help="Create status"), True)
    p = st.add_parser("update", help="Update status")
    p.add_argument("status_id", type=int)
    _status_fields(p, False)
    p = st.add_parser("delete", help="Delete status (system statuses refuse)")
    p.add_argument("status_id", type=int)
    _company(p)

    # ── revisions (project, read-only) ──
    rv = sub.add_parser("revisions", help="Form submission revision history (read-only)") \
        .add_subparsers(dest="revisions_command")
    rv.add_parser("list", help="List revisions of a submission").add_argument("submission_id", type=int)
    p = rv.add_parser("get", help="One revision snapshot")
    p.add_argument("submission_id", type=int)
    p.add_argument("revision_id", type=int)
    p = rv.add_parser("compare", help="Diff two revision numbers")
    p.add_argument("submission_id", type=int)
    p.add_argument("--a", type=int, required=True, help="Revision number")
    p.add_argument("--b", type=int, required=True, help="Revision number")

    # ── automations (company) ──
    au = sub.add_parser("automations", help="Company automation rules + executions") \
        .add_subparsers(dest="automations_command")
    p = au.add_parser("list", help="List automation rules")
    p.add_argument("--project", dest="project_filter", type=int, help="Only rules on this project")
    p.add_argument("--target-model", dest="target_model", help="e.g. Activity, FormSubmission")
    p.add_argument("--trigger", help="Trigger type, e.g. on_create, on_field_change")
    p.add_argument("--active", type=_bool, metavar="BOOL")
    p.add_argument("--source-form", dest="source_form", type=int)
    _paging(p)
    _company(p)
    for name, text in (("get", "Rule detail"), ("delete", "Delete rule"),
                       ("enable", "Enable rule"), ("disable", "Disable rule")):
        p = au.add_parser(name, help=text)
        p.add_argument("rule_id", type=int)
        _company(p)

    def _rule_fields(p, create):
        p.add_argument("--name", required=create)
        p.add_argument("--description")
        p.add_argument("--target-model", dest="target_model", required=create,
                       help="Model name, e.g. Activity or form_builder.FormSubmission")
        p.add_argument("--trigger", dest="trigger_type", required=create,
                       help="on_create|on_update|on_field_change|on_delete|...")
        p.add_argument("--rule-project", dest="rule_project", type=int,
                       help="Limit the rule to one project id")
        p.add_argument("--source-form", dest="source_form", type=int)
        p.add_argument("--execution-order", dest="execution_order", type=int)
        p.add_argument("--actions", help="actions_if_pass JSON list")
        p.add_argument("--condition", help="condition_definition JSON")
        _data(p)
        _company(p)

    _rule_fields(au.add_parser("create", help="Create rule"), True)
    p = au.add_parser("update", help="Update rule")
    p.add_argument("rule_id", type=int)
    _rule_fields(p, False)
    p = au.add_parser("executions", help="List executions (all rules, or --rule)")
    p.add_argument("--rule", type=int)
    p.add_argument("--status")
    p.add_argument("--trigger-event", dest="trigger_event")
    _paging(p)
    _company(p)
    p = au.add_parser("execution", help="Execution detail (condition + action results)")
    p.add_argument("execution_id", type=int)
    _company(p)

    # ── exports (PDF reports) ──
    ex = sub.add_parser("exports", help="PDF report export jobs + report presets") \
        .add_subparsers(dest="exports_command")
    p = ex.add_parser("create", help="Queue a PDF export")
    p.add_argument("--type", dest="report_type", required=True,
                   choices=["form_submission_detail", "form_submissions_table",
                            "activity_detail", "activities_table"])
    p.add_argument("--name", required=True, help="Report name")
    p.add_argument("--ids", required=True, help="Record ids, comma-sep")
    p.add_argument("--options", help="Report options JSON")
    p.add_argument("--preset", type=int, help="Report preset id instead of --options")
    _data(p)
    p = ex.add_parser("list", help="Your recent exports")
    _paging(p)
    ex.add_parser("get", help="Export job status").add_argument("job_id", type=int)
    p = ex.add_parser("download", help="Download a completed export")
    p.add_argument("job_id", type=int)
    _out(p)
    p = ex.add_parser("presets", help="List report presets (company)")
    p.add_argument("--report-type", dest="report_type")

    # ── file-exports (CSV/Parquet of the file list) ──
    fe = sub.add_parser("file-exports", help="Async CSV/Parquet export of the file list") \
        .add_subparsers(dest="file_exports_command")
    p = fe.add_parser("create", help="Start a file-list export")
    p.add_argument("--type", dest="export_format", choices=["csv", "parquet"], default="csv")
    p.add_argument("--filters", help='File-list filter params JSON, e.g. \'{"folder":12}\'')
    fe.add_parser("list", help="Your last 50 file exports")
    fe.add_parser("get", help="File export status").add_argument("job_id", type=int)
    p = fe.add_parser("download", help="Download a completed file export")
    p.add_argument("job_id", type=int)
    _out(p)

    # ── folder-downloads (recursive ZIP) ──
    fd = sub.add_parser("folder-downloads", help="Recursive folder ZIP downloads") \
        .add_subparsers(dest="folder_downloads_command")
    for name, text in (("preflight", "Size/caps check, starts nothing"),
                       ("create", "Start a folder ZIP job")):
        p = fd.add_parser(name, help=text)
        p.add_argument("--folder", type=int, required=True)
        p.add_argument("--filters", help="File filter params JSON")
        if name == "create":
            p.add_argument("--reason", help="Required when the company policy says so")
    fd.add_parser("list", help="Your folder download jobs")
    fd.add_parser("get", help="Job status + parts").add_argument("job_id", type=int)
    p = fd.add_parser("download", help="Download one part (--part) or the manifest")
    p.add_argument("job_id", type=int)
    p.add_argument("--part", type=int, help="Part id from `folder-downloads get`")
    p.add_argument("--manifest", action="store_true", help="Download the manifest instead")
    _out(p)

    # ── extraction (AI) ──
    ai = sub.add_parser("extraction", help="AI extraction profiles and jobs") \
        .add_subparsers(dest="extraction_command")
    p = ai.add_parser("profiles", help="List extraction profiles")
    _paging(p)
    ai.add_parser("profile-get", help="Profile detail").add_argument("profile_id", type=int)

    def _profile_fields(p, create):
        p.add_argument("--name", required=create)
        p.add_argument("--description")
        p.add_argument("--prompt", dest="prompt_text")
        p.add_argument("--output-mode", dest="output_mode",
                       choices=["field_extraction", "form_submission"])
        p.add_argument("--form", type=int, help="Form id (form_schema)")
        p.add_argument("--auto-apply", dest="auto_apply", type=_bool, metavar="BOOL",
                       help="Run on every upload")
        _data(p)

    _profile_fields(ai.add_parser("profile-create", help="Create profile"), True)
    p = ai.add_parser("profile-update", help="Update profile")
    p.add_argument("profile_id", type=int)
    _profile_fields(p, False)
    ai.add_parser("profile-delete", help="Delete profile").add_argument("profile_id", type=int)
    p = ai.add_parser("jobs", help="List extraction jobs")
    p.add_argument("--status", help="Comma-sep, e.g. pending,processing")
    _paging(p)
    ai.add_parser("job-get", help="Job detail").add_argument("job_id", type=int)
    p = ai.add_parser("job-create", help="Queue an extraction job")
    p.add_argument("--name", required=True)
    p.add_argument("--output-mode", dest="output_mode", default="form_submission",
                   choices=["form_submission", "field_extraction"])
    p.add_argument("--form", type=int, help="Target form id (required for form_submission)")
    p.add_argument("--profile", type=int)
    p.add_argument("--prompt", help="prompt_override")
    p.add_argument("--files", help="File ids, comma-sep")
    p.add_argument("--photos", help="Photo ids, comma-sep")
    p.add_argument("--drawings", help="Drawing ids, comma-sep")
    _data(p)
    ai.add_parser("job-cancel", help="Cancel a pending/processing job").add_argument("job_id", type=int)

    # ── views (saved grid views) ──
    vw = sub.add_parser("views", help="Saved grid views (generic-views), company or project") \
        .add_subparsers(dest="views_command")

    def _scope(p):
        p.add_argument("--scope", choices=["project", "company"], default="project",
                       help="View scope (default: project; project lists include company views)")

    p = vw.add_parser("list", help="List saved views")
    p.add_argument("--target-component", dest="target_component", help="e.g. data-grid")
    p.add_argument("--target-context", dest="target_context", help="e.g. activities, files")
    p.add_argument("--search", "-s")
    _paging(p)
    _scope(p)

    def _view_fields(p, create):
        p.add_argument("--name", required=create)
        p.add_argument("--description")
        p.add_argument("--target-component", dest="target_component")
        p.add_argument("--target-context", dest="target_context")
        p.add_argument("--view-state", dest="view_state", help="Grid state JSON")
        p.add_argument("--shared", type=_bool, metavar="BOOL")
        p.add_argument("--default", type=_bool, metavar="BOOL")
        p.add_argument("--favorite", type=_bool, metavar="BOOL")
        _data(p)
        _scope(p)

    for name, text in (("get", "View detail"), ("delete", "Delete view")):
        p = vw.add_parser(name, help=text)
        p.add_argument("view_id", type=int)
        _scope(p)
    _view_fields(vw.add_parser("create", help="Create view"), True)
    p = vw.add_parser("update", help="Update view")
    p.add_argument("view_id", type=int)
    _view_fields(p, False)

    # ── location-fields (read-only) ──
    lf = sub.add_parser("location-fields", help="Fields flagged as location fields (read-only)") \
        .add_subparsers(dest="location_fields_command")
    lf.add_parser("list", help="List location fields")


SUB_HANDLERS = {
    "workflows": {
        "templates": cmd_workflows_templates, "template": cmd_workflows_template,
        "template-create": cmd_workflows_template_create,
        "template-update": cmd_workflows_template_update,
        "template-delete": cmd_workflows_template_delete,
        "template-activate": cmd_workflows_template_activate,
        "template-deactivate": cmd_workflows_template_deactivate,
        "template-version": cmd_workflows_template_version,
        "stages": cmd_workflows_stages, "stage-create": cmd_workflows_stage_create,
        "stage-update": cmd_workflows_stage_update, "stage-delete": cmd_workflows_stage_delete,
        "stage-reorder": cmd_workflows_stage_reorder,
        "list": cmd_workflows_list, "get": cmd_workflows_get, "start": cmd_workflows_start,
        "cancel": cmd_workflows_cancel, "restart": cmd_workflows_restart,
        "stage-instances": cmd_workflows_stage_instances, "approve": cmd_workflows_approve,
        "reject": cmd_workflows_reject, "request-changes": cmd_workflows_request_changes,
        "history": cmd_workflows_history,
    },
    "submission-statuses": {
        "list": cmd_statuses_list, "get": cmd_statuses_get, "create": cmd_statuses_create,
        "update": cmd_statuses_update, "delete": cmd_statuses_delete,
    },
    "revisions": {"list": cmd_revisions_list, "get": cmd_revisions_get,
                  "compare": cmd_revisions_compare},
    "automations": {
        "list": cmd_automations_list, "get": cmd_automations_get,
        "create": cmd_automations_create, "update": cmd_automations_update,
        "delete": cmd_automations_delete, "enable": cmd_automations_enable,
        "disable": cmd_automations_disable, "executions": cmd_automations_executions,
        "execution": cmd_automations_execution,
    },
    "exports": {
        "create": cmd_exports_create, "list": cmd_exports_list, "get": cmd_exports_get,
        "download": cmd_exports_download, "presets": cmd_exports_presets,
    },
    "file-exports": {
        "create": cmd_file_exports_create, "list": cmd_file_exports_list,
        "get": cmd_file_exports_get, "download": cmd_file_exports_download,
    },
    "folder-downloads": {
        "preflight": cmd_folder_downloads_preflight, "create": cmd_folder_downloads_create,
        "list": cmd_folder_downloads_list, "get": cmd_folder_downloads_get,
        "download": cmd_folder_downloads_download,
    },
    "extraction": {
        "profiles": cmd_extraction_profiles, "profile-get": cmd_extraction_profile_get,
        "profile-create": cmd_extraction_profile_create,
        "profile-update": cmd_extraction_profile_update,
        "profile-delete": cmd_extraction_profile_delete,
        "jobs": cmd_extraction_jobs, "job-get": cmd_extraction_job_get,
        "job-create": cmd_extraction_job_create, "job-cancel": cmd_extraction_job_cancel,
    },
    "views": {
        "list": cmd_views_list, "get": cmd_views_get, "create": cmd_views_create,
        "update": cmd_views_update, "delete": cmd_views_delete,
    },
    "location-fields": {"list": cmd_location_fields_list},
}

SUB_COMMAND_KEYS = {
    "workflows": "workflows_command",
    "submission-statuses": "submission_statuses_command",
    "revisions": "revisions_command",
    "automations": "automations_command",
    "exports": "exports_command",
    "file-exports": "file_exports_command",
    "folder-downloads": "folder_downloads_command",
    "extraction": "extraction_command",
    "views": "views_command",
    "location-fields": "location_fields_command",
}

COMPANY_ONLY = {"submission-statuses", "automations"}
