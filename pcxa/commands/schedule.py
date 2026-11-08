"""Schedule commands: baselines, CPM calendars, the work log, and activity extras.

These back the web UI's schedule pages: Activities > Baselines, Activities >
CPM > Calendars, Activities > Work Log and Company Settings > Templates >
Calendars. All live in the backend ``activity`` app.

- ``baselines`` (project only): ``baselines/`` + nested ``change-entries/``.
  A new baseline is made by the ``initialize`` action — plain POST to
  ``baselines/`` runs the full model serializer first and 400s without
  ``version``/``status``/``project``, which the web UI never sends.
- ``calendars``: ``calendars/`` (+ nested ``exceptions/``) at company and
  project scope. Project scope lists the project's calendars AND the
  company's; company scope lists company calendars only, so a project
  calendar is unreachable (404) through ``--scope company``.
- ``work-log`` (project only): a read-only aggregation (``viewsets.ViewSet``
  with ``list`` only) of progress, comments, files and photos.

``register_activities_parser`` adds read-only analytics and lifecycle writes
to the existing ``activities`` group (see ``ACTIVITIES_HANDLERS``).
"""

import argparse
import json
import sys

from pcxa._output import out_json, out_table

# The archive/unarchive/restore bulk endpoints cap a request at
# BULK_FILTER_LIMIT = 10_000 ids (413 above it; activity_bulk.py). 500 per
# request keeps each call well under the cap and short enough for the default
# HTTP timeout. bulk_reparent has no cap but runs one transaction per request.
BULK_CHUNK = 500

_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def _scoped(args):
    """True if the request should be project-scoped (default), False for company."""
    return getattr(args, "scope", "project") != "company"


def _json_arg(value, flag):
    """Parse a JSON-valued flag, or exit 1 with a clean message."""
    try:
        return json.loads(value)
    except ValueError as e:
        print(f"{flag}: invalid JSON ({e})", file=sys.stderr)
        sys.exit(1)


def _merge_data(payload, args):
    """Merge the ``--data`` escape hatch last, so it wins over typed flags."""
    if getattr(args, "data", None):
        extra = _json_arg(args.data, "--data")
        if not isinstance(extra, dict):
            print("--data must be a JSON object", file=sys.stderr)
            sys.exit(1)
        payload.update(extra)
    return payload


def _id_list(value):
    return [int(x) for x in value.split(",") if x.strip()]


def _show(args, data, rows=None, columns=None):
    """JSON as-is; for table, a row table when given, else ``key: value`` lines."""
    if args.format == "json":
        out_json(data)
    elif rows is not None:
        out_table(rows, columns)
    elif isinstance(data, dict):
        for k, v in data.items():
            print(f"  {k}: {json.dumps(v, default=str) if isinstance(v, (dict, list)) else v}")
    else:
        out_json(data)


# ═══════════════════════════════════════════════════════════════════════════════
# BASELINES — project baselines and their change entries
# ═══════════════════════════════════════════════════════════════════════════════


def _baseline_row(b):
    return {
        "id": str(b.get("id", "")),
        "version": str(b.get("version", "")),
        "status": str(b.get("status", "")),
        "approved_at": str(b.get("approved_at") or "-")[:19],
        "description": str(b.get("description") or "")[:40],
    }


def cmd_baselines_list(client, args):
    """List project baselines, newest version first."""
    params = {}
    if args.status:
        params["status"] = args.status
    if args.version is not None:
        params["version"] = args.version
    data = client.get_all_pages("baselines/", params)
    _show(args, data, [_baseline_row(b) for b in data],
          ["id", "version", "status", "approved_at", "description"])


def cmd_baselines_get(client, args):
    """Baseline detail with its change summary."""
    _show(args, client.get(f"baselines/{args.baseline_id}/"))


def cmd_baselines_create(client, args):
    """Create the project's initial baseline (the ``initialize`` action)."""
    if args.dry_run:
        print("Would CREATE initial baseline: POST baselines/initialize/")
        return
    data = client.post("baselines/initialize/")
    if args.format == "json":
        out_json(data)
    else:
        print(f"Created baseline {data.get('id')} (version {data.get('version')})")


def cmd_baselines_update(client, args):
    """Update a baseline (Company Admin only)."""
    payload = {}
    if args.description is not None:
        payload["description"] = args.description
    _merge_data(payload, args)
    if not payload:
        print("No fields to update.", file=sys.stderr)
        sys.exit(1)
    if args.dry_run:
        print(f"Would UPDATE baseline {args.baseline_id}: {json.dumps(payload, indent=2)}")
        return
    data = client.patch(f"baselines/{args.baseline_id}/", payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Updated baseline {data.get('id')}")


def cmd_baselines_delete(client, args):
    """Delete a baseline (Company Admin only)."""
    if args.dry_run:
        print(f"Would DELETE baseline {args.baseline_id}")
        return
    client.delete(f"baselines/{args.baseline_id}/")
    print(f"Deleted baseline {args.baseline_id}")


def cmd_baselines_uncommitted(client, args):
    """The project's uncommitted baseline (created on first read if missing)."""
    _show(args, client.get("baselines/uncommitted/"))


def cmd_baselines_uncommitted_changes(client, args):
    """Field-by-field uncommitted changes since the last approved baseline."""
    data = client.get("baselines/uncommitted_changes/")
    rows = [{
        "activity": str(c.get("id", "")),
        "title": str(c.get("title", ""))[:40],
        "change": str(c.get("change_type", "")),
        "fields": ",".join(c.get("changed_fields") or [])[:50],
    } for c in data]
    _show(args, data, rows, ["activity", "title", "change", "fields"])


def cmd_baselines_approve(client, args):
    """Approve an uncommitted baseline."""
    payload = {"description": args.description or ""}
    if args.dry_run:
        print(f"Would APPROVE baseline {args.baseline_id}: {json.dumps(payload)}")
        return
    data = client.post(f"baselines/{args.baseline_id}/approve/", payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Approved baseline {data.get('id')} (version {data.get('version')})")


def cmd_baselines_bulk_approve(client, args):
    """Approve the uncommitted changes of some (or all) activities."""
    payload = {"description": args.description or ""}
    if args.activities:
        payload["activity_ids"] = _id_list(args.activities)
    if args.dry_run:
        print(f"Would BULK-APPROVE baseline changes: {json.dumps(payload)}")
        return
    data = client.post("baselines/bulk_approve/", payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"{data.get('message')} (baseline {data.get('approved_baseline_id')})")


def cmd_baselines_changes(client, args):
    """List a baseline's change entries."""
    params = {}
    if args.change_type:
        params["change_type"] = args.change_type
    if args.activity is not None:
        params["activity"] = args.activity
    path = f"baselines/{args.baseline_id}/change-entries/"
    if args.all:
        data = client.get_all_pages(path, params)
        results = data
    else:
        params.update(client.paginate_params(args.limit, args.offset))
        data = client.get(path, params)
        results = data.get("results", data) if isinstance(data, dict) else data
    rows = [{
        "id": str(e.get("id", "")),
        "activity": str(e.get("activity", "")),
        "title": str(e.get("activity_title", ""))[:40],
        "change": str(e.get("change_type", "")),
        "fields": ",".join(e.get("changed_fields") or [])[:50],
    } for e in results]
    _show(args, data, rows, ["id", "activity", "title", "change", "fields"])


def cmd_baselines_comparison(client, args):
    """Current vs previous snapshot for one change entry."""
    _show(args, client.get(
        f"baselines/{args.baseline_id}/change-entries/{args.entry_id}/comparison/"))


# ═══════════════════════════════════════════════════════════════════════════════
# CALENDARS — CPM working calendars and their exceptions
# ═══════════════════════════════════════════════════════════════════════════════


def _working_days(value):
    """``mon,tue,...`` (or full names) -> the backend's 7-key dict; JSON passes through."""
    if value.lstrip().startswith("{"):
        return _json_arg(value, "--working-days")
    picked = set()
    for token in value.split(","):
        token = token.strip().lower()
        if not token:
            continue
        match = [d for d in _WEEKDAYS if d.startswith(token[:3])]
        if len(token) < 3 or not match:
            print(f"--working-days: unknown day '{token}'", file=sys.stderr)
            sys.exit(1)
        picked.add(match[0])
    return {d: d in picked for d in _WEEKDAYS}


def _calendar_payload(args):
    payload = {}
    if args.name is not None:
        payload["name"] = args.name
    if args.description is not None:
        payload["description"] = args.description
    if args.working_days is not None:
        payload["working_days"] = _working_days(args.working_days)
    if args.hours_per_day is not None:
        payload["hours_per_day"] = str(args.hours_per_day)
    if args.company_default is not None:
        payload["is_company_default"] = args.company_default
    if args.project_default is not None:
        payload["is_project_default"] = args.project_default
    return _merge_data(payload, args)


def _calendar_row(c):
    days = c.get("working_days") or {}
    return {
        "id": str(c.get("id", "")),
        "name": str(c.get("name", ""))[:35],
        "scope": "company" if c.get("project") in (None, "") else "project",
        "default": "company" if c.get("is_company_default") else (
            "project" if c.get("is_project_default") else ""),
        "days": ",".join(d[:3] for d in _WEEKDAYS if days.get(d)),
        "hours": str(c.get("hours_per_day", "")),
        "exceptions": str(c.get("exceptions_count", "")),
    }


def cmd_calendars_list(client, args):
    """List calendars (project scope also lists the company's calendars)."""
    params = {}
    if args.source_system:
        params["source_system"] = args.source_system
    if args.default == "company":
        params["is_company_default"] = "true"
    elif args.default == "project":
        params["is_project_default"] = "true"
    data = client.get_all_pages("calendars/", params, project_scoped=_scoped(args))
    _show(args, data, [_calendar_row(c) for c in data],
          ["id", "name", "scope", "default", "days", "hours", "exceptions"])


def cmd_calendars_get(client, args):
    """Calendar detail including its exceptions."""
    _show(args, client.get(f"calendars/{args.calendar_id}/", project_scoped=_scoped(args)))


def cmd_calendars_create(client, args):
    """Create a calendar at the chosen scope."""
    payload = _calendar_payload(args)
    if args.dry_run:
        print(f"Would CREATE calendar: {json.dumps(payload, indent=2)}")
        return
    data = client.post("calendars/", payload, project_scoped=_scoped(args))
    if args.format == "json":
        out_json(data)
    else:
        print(f"Created calendar {data.get('id')}: '{data.get('name')}'")


def cmd_calendars_update(client, args):
    """Update a calendar."""
    payload = _calendar_payload(args)
    if not payload:
        print("No fields to update.", file=sys.stderr)
        sys.exit(1)
    if args.dry_run:
        print(f"Would UPDATE calendar {args.calendar_id}: {json.dumps(payload, indent=2)}")
        return
    data = client.patch(f"calendars/{args.calendar_id}/", payload, project_scoped=_scoped(args))
    if args.format == "json":
        out_json(data)
    else:
        print(f"Updated calendar {data.get('id')}: '{data.get('name')}'")


def cmd_calendars_delete(client, args):
    """Soft-delete a calendar."""
    if args.dry_run:
        print(f"Would DELETE calendar {args.calendar_id}")
        return
    client.delete(f"calendars/{args.calendar_id}/", project_scoped=_scoped(args))
    print(f"Deleted calendar {args.calendar_id}")


def cmd_calendars_set_default(client, args):
    """Make a calendar the default for its own scope (company or project)."""
    if args.dry_run:
        print(f"Would SET calendar {args.calendar_id} as default")
        return
    data = client.post(f"calendars/{args.calendar_id}/set_as_default/",
                       project_scoped=_scoped(args))
    if args.format == "json":
        out_json(data)
    else:
        print(data.get("message", f"Calendar {args.calendar_id} set as default"))


def cmd_calendars_effective(client, args):
    """The calendar that applies here: project default, else company default."""
    _show(args, client.get("calendars/effective/", project_scoped=_scoped(args)))


def _exceptions_path(args, suffix=""):
    return f"calendars/{args.calendar_id}/exceptions/{suffix}"


def _exception_payload(args):
    payload = {}
    if args.name is not None:
        payload["name"] = args.name
    if args.start is not None:
        payload["start_date"] = args.start
    if args.end is not None:
        payload["end_date"] = args.end or None
    if args.type is not None:
        payload["exception_type"] = args.type
    return _merge_data(payload, args)


def cmd_cal_exceptions_list(client, args):
    """List a calendar's exceptions, by start date."""
    params = {}
    if args.type:
        params["exception_type"] = args.type
    data = client.get_all_pages(_exceptions_path(args), params, project_scoped=_scoped(args))
    rows = [{
        "id": str(e.get("id", "")),
        "name": str(e.get("name", ""))[:35],
        "type": str(e.get("exception_type", "")),
        "start": str(e.get("start_date", "")),
        "end": str(e.get("end_date") or "-"),
    } for e in data]
    _show(args, data, rows, ["id", "name", "type", "start", "end"])


def cmd_cal_exceptions_create(client, args):
    """Add an exception (holiday, shutdown or working override)."""
    payload = _exception_payload(args)
    if args.dry_run:
        print(f"Would CREATE exception on calendar {args.calendar_id}: "
              f"{json.dumps(payload, indent=2)}")
        return
    data = client.post(_exceptions_path(args), payload, project_scoped=_scoped(args))
    if args.format == "json":
        out_json(data)
    else:
        print(f"Created exception {data.get('id')}: '{data.get('name')}'")


def cmd_cal_exceptions_update(client, args):
    """Update an exception."""
    payload = _exception_payload(args)
    if not payload:
        print("No fields to update.", file=sys.stderr)
        sys.exit(1)
    if args.dry_run:
        print(f"Would UPDATE exception {args.exception_id}: {json.dumps(payload, indent=2)}")
        return
    data = client.patch(_exceptions_path(args, f"{args.exception_id}/"), payload,
                        project_scoped=_scoped(args))
    if args.format == "json":
        out_json(data)
    else:
        print(f"Updated exception {data.get('id')}: '{data.get('name')}'")


def cmd_cal_exceptions_delete(client, args):
    """Soft-delete an exception."""
    if args.dry_run:
        print(f"Would DELETE exception {args.exception_id} from calendar {args.calendar_id}")
        return
    client.delete(_exceptions_path(args, f"{args.exception_id}/"), project_scoped=_scoped(args))
    print(f"Deleted exception {args.exception_id}")


def cmd_cal_exceptions_bulk_create(client, args):
    """Create many exceptions from a JSON file (a list, or ``{"exceptions": [...]}``)."""
    from pathlib import Path
    path = Path(args.file)
    if not path.exists():
        print(f"File not found: {path}", file=sys.stderr)
        sys.exit(1)
    raw = _json_arg(path.read_text(), args.file)
    items = raw.get("exceptions") if isinstance(raw, dict) else raw
    if not isinstance(items, list) or not items:
        print("JSON must be a non-empty list of exceptions or an object with an "
              "'exceptions' key.", file=sys.stderr)
        sys.exit(1)
    if args.dry_run:
        print(f"Would BULK-CREATE {len(items)} exceptions on calendar {args.calendar_id}")
        return
    data = client.post(_exceptions_path(args, "bulk_create/"), {"exceptions": items},
                       project_scoped=_scoped(args))
    if args.format == "json":
        out_json(data)
    else:
        print(f"Created {data.get('created_count', 0)} exceptions, "
              f"{data.get('error_count', 0)} errors")
    # The server creates the valid rows and reports the rest; say so in the exit code.
    return 1 if data.get("error_count") else None


_EXCEPTION_HANDLERS = {
    "list": cmd_cal_exceptions_list,
    "create": cmd_cal_exceptions_create,
    "update": cmd_cal_exceptions_update,
    "delete": cmd_cal_exceptions_delete,
    "bulk-create": cmd_cal_exceptions_bulk_create,
}


def cmd_calendars_exceptions(client, args):
    """Dispatch ``calendars exceptions <subcommand>`` (nested third level)."""
    fn = _EXCEPTION_HANDLERS.get(getattr(args, "cal_exceptions_command", None))
    if fn is None:
        avail = ", ".join(_EXCEPTION_HANDLERS)
        print(f"Usage: pcxa calendars exceptions {{{avail}}} <calendar_id>", file=sys.stderr)
        sys.exit(1)
    return fn(client, args)


# ═══════════════════════════════════════════════════════════════════════════════
# WORK LOG — read-only event feed across a project's activities
# ═══════════════════════════════════════════════════════════════════════════════


def cmd_work_log_list(client, args):
    """Progress, comments, files and photos per period (max 90-day window)."""
    params = {"date_from": args.date_from, "date_to": args.date_to}
    if args.group_by:
        params["group_by"] = args.group_by
    if args.user:
        params["user"] = args.user
    if args.activity:
        params["activity"] = args.activity
    if args.event_type:
        params["event_type"] = args.event_type
    if args.page:
        params["page"] = args.page
    if args.page_size:
        params["page_size"] = args.page_size
    data = client.get("work-log/", params)
    if data.get("has_next"):
        print(f"More events: rerun with --page {data.get('page', 1) + 1} "
              f"({data.get('total_events')} total)", file=sys.stderr)
    rows = []
    for period in data.get("periods", []):
        for e in period.get("events", []):
            act = e.get("activity") or {}
            rows.append({
                "period": str(period.get("period_label", "")),
                "when": str(e.get("timestamp", ""))[:16],
                "type": str(e.get("event_type", "")),
                "user": str((e.get("user") or {}).get("full_name", "-"))[:20],
                "activity": f"{act.get('id', '')} {act.get('title', '')}"[:40],
            })
    _show(args, data, rows, ["period", "when", "type", "user", "activity"])


# ═══════════════════════════════════════════════════════════════════════════════
# ACTIVITIES EXTRAS — analytics and lifecycle actions on ``activities/``
# ═══════════════════════════════════════════════════════════════════════════════


def _list_filters(args):
    """The subset of list filters the analytics endpoints honour, plus ``--params``."""
    params = {}
    if getattr(args, "status", None):
        params["status"] = args.status
    if getattr(args, "search", None):
        params["search"] = args.search
    if getattr(args, "params", None):
        extra = _json_arg(args.params, "--params")
        if not isinstance(extra, dict):
            print("--params must be a JSON object", file=sys.stderr)
            sys.exit(1)
        params.update(extra)
    return params


def cmd_activities_wbs_tree(client, args):
    """One level of the filtered WBS tree (children of --parent), keyset-paged."""
    params = _list_filters(args)
    if args.parent is not None:
        params["parent"] = args.parent
    if args.cursor:
        params["cursor"] = args.cursor
    if args.page_size:
        params["page_size"] = args.page_size
    data = client.get("activities/wbs-tree/", params)
    if data.get("has_more"):
        print(f"More rows: rerun with --cursor {data.get('next_cursor')}", file=sys.stderr)
    rows = [{
        "id": str(a.get("id", "")),
        "wbs": str(a.get("wbs_code") or ""),
        "title": str(a.get("title", ""))[:40],
        "status": str(a.get("status", "")),
        "children": "yes" if a.get("has_children") else "",
        "match": "yes" if a.get("is_match") else "",
    } for a in data.get("results", [])]
    _show(args, data, rows, ["id", "wbs", "title", "status", "children", "match"])


def cmd_activities_field_changes(client, args):
    """Field-level change history of one activity."""
    data = client.get(f"activities/{args.activity_id}/field_changes/")
    rows = [{
        "when": str(c.get("datetime", ""))[:16],
        "user": str(c.get("user", ""))[:20],
        "field": str(c.get("field_display_name") or c.get("field_name", "")),
        "from": str(c.get("previous_value", ""))[:25],
        "to": str(c.get("current_value", ""))[:25],
    } for c in data.get("changes", [])]
    _show(args, data, rows, ["when", "user", "field", "from", "to"])


def cmd_activities_critical_path(client, args):
    """Activities with zero total float, by early start."""
    data = client.get("activities/critical_path/")
    rows = [{
        "id": str(a.get("id", "")),
        "title": str(a.get("title", ""))[:40],
        "early_start": str(a.get("early_start") or "")[:10],
        "early_finish": str(a.get("early_finish") or "")[:10],
        "float": str(a.get("total_float", "")),
    } for a in data.get("critical_path", [])]
    _show(args, data, rows, ["id", "title", "early_start", "early_finish", "float"])


def cmd_activities_schedule_summary(client, args):
    """Project dates, duration and status counts from the CPM fields."""
    _show(args, client.get("activities/schedule_summary/"))


def cmd_activities_progress_summary(client, args):
    """Current vs planned progress and the delta over --period-days."""
    params = {}
    if args.period_days is not None:
        params["period_days"] = args.period_days
    _show(args, client.get("activities/progress-summary/", params))


def cmd_activities_progress_curve(client, args):
    """Actual vs planned % complete over time (S-curve)."""
    params = {}
    if args.date_from:
        params["date_from"] = args.date_from
    if args.date_to:
        params["date_to"] = args.date_to
    if args.interval:
        params["interval"] = args.interval
    if args.rollup_method:
        params["rollup_method"] = args.rollup_method
    data = client.get("activities/progress-curve/", params)
    points = data if isinstance(data, list) else []
    _show(args, data, points, ["date", "actual_pct", "planned_pct", "activity_count"])


def cmd_activities_baseline_curve(client, args):
    """Planned (latest approved baseline) vs actual cumulative completions per day."""
    data = client.get("activities/baseline-curve/", _list_filters(args))
    if args.format != "json" and data.get("baseline") is None:
        print("No approved baseline (pcxa baselines list).")
        return
    _show(args, data, data.get("points", []), ["day", "planned", "actual"])


def cmd_activities_trend(client, args):
    """Daily total / in-progress / completed counts over the last --days."""
    params = _list_filters(args)
    if args.days is not None:
        params["days"] = args.days
    data = client.get("activities/trend/", params)
    _show(args, data, data, ["day", "total", "in_progress", "completed"])


def _lifecycle(client, args, verb, single_path, single_method, bulk_path):
    """One id -> the per-id action; several -> the bulk action in BULK_CHUNK chunks.

    Per-id needs only the caller's permission on that activity; bulk_archive
    and bulk_unarchive need project/company admin (``_check_bulk_action_permission``).
    """
    ids = args.activity_ids
    if args.dry_run:
        print(f"Would {verb.upper()} activities {ids}")
        return None
    if len(ids) == 1:
        call = client.patch if single_method == "PATCH" else client.post
        data = call(single_path.format(id=ids[0]), {})
        if args.format == "json":
            out_json(data)
        else:
            print(f"{verb.capitalize()}d activity {ids[0]}")
        return None
    data = client.bulk_call(bulk_path, "activity_ids", ids, chunk=BULK_CHUNK, method="PATCH")
    if args.format == "json":
        out_json(data)
    else:
        print(f"{verb.capitalize()}d {data.get('success_count', 0)} of {len(ids)} activities")
        for err in data.get("errors", []):
            print(f"  {err}", file=sys.stderr)
    return 1 if data.get("error_count") else None


def cmd_activities_archive(client, args):
    """Archive activities."""
    return _lifecycle(client, args, "archive", "activities/{id}/archive/", "PATCH",
                      "activities/bulk_archive/")


def cmd_activities_unarchive(client, args):
    """Unarchive activities."""
    return _lifecycle(client, args, "unarchive", "activities/{id}/unarchive/", "PATCH",
                      "activities/bulk_unarchive/")


def cmd_activities_restore(client, args):
    """Restore soft-deleted activities."""
    return _lifecycle(client, args, "restore", "activities/{id}/restore/", "POST",
                      "activities/bulk_restore/")


def cmd_activities_reparent(client, args):
    """Move activities under a new parent (``--parent none`` = WBS root)."""
    new_parent = args.parent
    if args.dry_run:
        print(f"Would REPARENT activities {args.activity_ids} under {new_parent}")
        return None
    updated = []

    # bulk_reparent answers with updated_count/updated_activities, not
    # success_count, so collect those per chunk; bulk_call still gathers errors.
    def on_chunk(_start, _size, _total, data):
        updated.extend(data.get("updated_activities") or [])

    agg = client.bulk_call("activities/bulk_reparent/", "activity_ids", args.activity_ids,
                           {"new_parent": new_parent}, chunk=BULK_CHUNK, method="PATCH",
                           on_chunk=on_chunk)
    result = {"updated_count": len(updated), "updated_activities": updated,
              "errors": agg["errors"], "chunks": agg["chunks"]}
    if args.format == "json":
        out_json(result)
    else:
        print(f"Reparented {len(updated)} of {len(args.activity_ids)} activities")
        out_table([{"id": str(a.get("id")), "wbs": str(a.get("wbs_code") or ""),
                    "title": str(a.get("title", ""))[:40]} for a in updated],
                  ["id", "wbs", "title"])
        for err in agg["errors"]:
            print(f"  {err}", file=sys.stderr)
    return 1 if agg["errors"] else None


def cmd_activities_toggle_closed(client, args):
    """Flip an activity between closed and open (admin-level permission)."""
    if args.dry_run:
        print(f"Would TOGGLE closed on activity {args.activity_id}")
        return
    data = client.post(f"activities/{args.activity_id}/toggle_closed/", {})
    if args.format == "json":
        out_json(data)
    else:
        print(data.get("message", f"Toggled activity {args.activity_id}"))


def cmd_activities_assign_users(client, args):
    """Replace an activity's assignees (and optionally its distribution list)."""
    if args.distribution and not args.users:
        # assign_users binds ``User`` only inside its ``if assignees:`` branch,
        # so a distribution list sent alone 500s (activity_actions.py:181-190).
        # Empty lists are ignored server-side too: this cannot clear assignees.
        print("--distribution needs --users in the same call (the server errors "
              "on a distribution list alone).", file=sys.stderr)
        sys.exit(1)
    payload = {"assignees": _id_list(args.users)}
    if args.distribution:
        payload["distribution_list"] = _id_list(args.distribution)
    if args.dry_run:
        print(f"Would ASSIGN users on activity {args.activity_id}: {json.dumps(payload)}")
        return
    data = client.post(f"activities/{args.activity_id}/assign_users/", payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Assignees: {data.get('assignees')}  Distribution: {data.get('distribution_list')}")


# ═══════════════════════════════════════════════════════════════════════════════
# PARSER REGISTRATION
# ═══════════════════════════════════════════════════════════════════════════════


def _add_scope(p):
    p.add_argument("--scope", choices=["project", "company"], default="project",
                   help="Calendar scope (default: project, which also sees company calendars)")


def _add_data(p):
    p.add_argument("--data", help="Extra fields as a JSON object, merged last")


def _add_bool(p, flag, dest, help_text):
    g = p.add_mutually_exclusive_group()
    g.add_argument(f"--{flag}", dest=dest, action="store_true", default=None, help=help_text)
    g.add_argument(f"--no-{flag}", dest=dest, action="store_false")


def register_parser(sub):
    # ── baselines ──
    bl = sub.add_parser("baselines", help="Project schedule baselines and change entries")
    bl_sub = bl.add_subparsers(dest="baselines_command")

    p = bl_sub.add_parser("list", help="List baselines (newest version first)")
    p.add_argument("--status", choices=["approved", "uncommitted", "superseded"])
    p.add_argument("--version", type=int)

    p = bl_sub.add_parser("get", help="Baseline detail")
    p.add_argument("baseline_id", type=int)

    bl_sub.add_parser("create", help="Create the project's initial baseline")

    p = bl_sub.add_parser("update", help="Update a baseline (Company Admin)")
    p.add_argument("baseline_id", type=int)
    p.add_argument("--description")
    _add_data(p)

    p = bl_sub.add_parser("delete", help="Delete a baseline (Company Admin)")
    p.add_argument("baseline_id", type=int)

    bl_sub.add_parser("uncommitted", help="The uncommitted baseline (pending changes)")
    bl_sub.add_parser("uncommitted-changes", help="Field-by-field pending changes")

    p = bl_sub.add_parser("approve", help="Approve an uncommitted baseline")
    p.add_argument("baseline_id", type=int)
    p.add_argument("--description", help="What changed since the previous baseline")

    p = bl_sub.add_parser("bulk-approve", help="Approve pending changes for some or all activities")
    p.add_argument("--activities", help="Activity IDs (comma-sep); omit for all")
    p.add_argument("--description")

    p = bl_sub.add_parser("changes", help="A baseline's change entries")
    p.add_argument("baseline_id", type=int)
    p.add_argument("--change-type", dest="change_type", choices=["create", "update", "delete"])
    p.add_argument("--activity", type=int, help="Only this activity's entry")
    p.add_argument("--limit", type=int, default=25)
    p.add_argument("--offset", type=int, default=0)
    p.add_argument("--all", action="store_true", help="Fetch every page")

    p = bl_sub.add_parser("comparison", help="Current vs previous snapshot of one change entry")
    p.add_argument("baseline_id", type=int)
    p.add_argument("entry_id", type=int)

    # ── calendars ──
    cal = sub.add_parser("calendars", help="CPM working calendars and exceptions")
    cal_sub = cal.add_subparsers(dest="calendars_command")

    p = cal_sub.add_parser("list", help="List calendars")
    p.add_argument("--default", choices=["company", "project"], help="Only the default calendar")
    p.add_argument("--source-system", dest="source_system", help="e.g. p6")
    _add_scope(p)

    p = cal_sub.add_parser("get", help="Calendar detail with exceptions")
    p.add_argument("calendar_id", type=int)
    _add_scope(p)

    for name, required in (("create", True), ("update", False)):
        p = cal_sub.add_parser(name, help=f"{name.capitalize()} a calendar")
        if not required:
            p.add_argument("calendar_id", type=int)
        p.add_argument("--name", required=required)
        p.add_argument("--description")
        p.add_argument("--working-days", dest="working_days",
                       help="Working weekdays, e.g. mon,tue,wed,thu,fri (others become "
                            "non-working), or a JSON object")
        p.add_argument("--hours-per-day", dest="hours_per_day", type=float)
        _add_bool(p, "company-default", "company_default", "Company default (company scope only)")
        _add_bool(p, "project-default", "project_default", "Project default (project scope only)")
        _add_data(p)
        _add_scope(p)

    p = cal_sub.add_parser("delete", help="Delete a calendar")
    p.add_argument("calendar_id", type=int)
    _add_scope(p)

    p = cal_sub.add_parser("set-default", help="Make a calendar the default for its scope")
    p.add_argument("calendar_id", type=int)
    _add_scope(p)

    p = cal_sub.add_parser("effective", help="Calendar in effect (project default, else company)")
    _add_scope(p)

    exc = cal_sub.add_parser("exceptions", help="Holidays, shutdowns and working overrides")
    exc_sub = exc.add_subparsers(dest="cal_exceptions_command")

    p = exc_sub.add_parser("list", help="List a calendar's exceptions")
    p.add_argument("calendar_id", type=int)
    p.add_argument("--type", choices=["non_working", "working"])
    _add_scope(p)

    for name, required in (("create", True), ("update", False)):
        p = exc_sub.add_parser(name, help=f"{name.capitalize()} an exception")
        p.add_argument("calendar_id", type=int)
        if not required:
            p.add_argument("exception_id", type=int)
        p.add_argument("--name", required=required)
        p.add_argument("--start", required=required, help="start_date YYYY-MM-DD")
        p.add_argument("--end", help="end_date YYYY-MM-DD (omit for a single day)")
        p.add_argument("--type", choices=["non_working", "working"],
                       help="non_working (default) or working override")
        _add_data(p)
        _add_scope(p)

    p = exc_sub.add_parser("delete", help="Delete an exception")
    p.add_argument("calendar_id", type=int)
    p.add_argument("exception_id", type=int)
    _add_scope(p)

    p = exc_sub.add_parser("bulk-create", help="Create exceptions from a JSON file")
    p.add_argument("calendar_id", type=int)
    p.add_argument("file", help='JSON list of {"name","start_date","end_date"?,"exception_type"?}')
    _add_scope(p)

    # ── work-log ──
    wl = sub.add_parser("work-log", help="Work events across project activities")
    wl_sub = wl.add_subparsers(dest="work_log_command")
    p = wl_sub.add_parser("list", help="Events grouped by period (window of at most 90 days)")
    p.add_argument("--from", dest="date_from", required=True, help="YYYY-MM-DD")
    p.add_argument("--to", dest="date_to", required=True, help="YYYY-MM-DD")
    p.add_argument("--group-by", dest="group_by", choices=["day", "week", "month"])
    p.add_argument("--user", help="User IDs (comma-sep)")
    p.add_argument("--activity", help="Activity IDs (comma-sep)")
    p.add_argument("--event-type", dest="event_type",
                   help="progress,comment,file,photo (comma-sep)")
    p.add_argument("--page", type=int)
    p.add_argument("--page-size", dest="page_size", type=int, help="Default 50")


def _add_list_filters(p):
    p.add_argument("--status", help="not_started,in_progress,completed")
    p.add_argument("--search", "-s")
    p.add_argument("--params", help="Any other activities-list filters as a JSON object")


def _parent_id(value):
    """``--parent``: an activity id, or none/root for the top of the WBS (sent as null)."""
    if value.strip().lower() in ("none", "null", "root"):
        return None
    try:
        return int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected an activity ID or 'none', got '{value}'")


def register_activities_parser(act_sub):
    p = act_sub.add_parser("wbs-tree", help="One level of the filtered WBS tree")
    p.add_argument("--parent", type=int, help="Node to expand (omit for the top level)")
    p.add_argument("--cursor", help="next_cursor from the previous page")
    p.add_argument("--page-size", dest="page_size", type=int, help="Default 50, max 200")
    _add_list_filters(p)

    p = act_sub.add_parser("field-changes", help="Field-level change history of an activity")
    p.add_argument("activity_id", type=int)

    act_sub.add_parser("critical-path", help="Critical activities (zero total float)")
    act_sub.add_parser("schedule-summary", help="CPM dates, duration and status counts")

    p = act_sub.add_parser("progress-summary", help="Actual vs planned progress now")
    p.add_argument("--period-days", dest="period_days", type=int, help="Delta window (default 30)")

    p = act_sub.add_parser("progress-curve", help="Actual vs planned %% complete over time (S-curve)")
    p.add_argument("--from", dest="date_from", help="ISO date")
    p.add_argument("--to", dest="date_to", help="ISO date")
    p.add_argument("--interval", choices=["daily", "weekly", "biweekly", "monthly"])
    p.add_argument("--rollup-method", dest="rollup_method",
                   help="equal, duration, cost or labor (default: the project's)")

    p = act_sub.add_parser("baseline-curve", help="Planned vs actual completions vs latest baseline")
    _add_list_filters(p)

    p = act_sub.add_parser("trend", help="Daily total/in-progress/completed counts")
    p.add_argument("--days", help="Window ending today (default 30, max 365) or 'all'")
    _add_list_filters(p)

    for name, help_text in (("archive", "Archive activities"),
                            ("unarchive", "Unarchive activities"),
                            ("restore", "Restore soft-deleted activities")):
        p = act_sub.add_parser(name, help=help_text)
        p.add_argument("activity_ids", nargs="+", type=int)

    p = act_sub.add_parser("reparent", help="Move activities under a new WBS parent")
    p.add_argument("activity_ids", nargs="+", type=int)
    p.add_argument("--parent", required=True, type=_parent_id,
                   help="New parent activity ID, or 'none' for root")

    p = act_sub.add_parser("toggle-closed", help="Close or reopen an activity")
    p.add_argument("activity_id", type=int)

    p = act_sub.add_parser("assign-users", help="Replace an activity's assignees")
    p.add_argument("activity_id", type=int)
    p.add_argument("--users", required=True, help="Assignee user IDs (comma-sep)")
    p.add_argument("--distribution", help="Distribution-list user IDs (comma-sep)")


SUB_HANDLERS = {
    "baselines": {
        "list": cmd_baselines_list,
        "get": cmd_baselines_get,
        "create": cmd_baselines_create,
        "update": cmd_baselines_update,
        "delete": cmd_baselines_delete,
        "uncommitted": cmd_baselines_uncommitted,
        "uncommitted-changes": cmd_baselines_uncommitted_changes,
        "approve": cmd_baselines_approve,
        "bulk-approve": cmd_baselines_bulk_approve,
        "changes": cmd_baselines_changes,
        "comparison": cmd_baselines_comparison,
    },
    "calendars": {
        "list": cmd_calendars_list,
        "get": cmd_calendars_get,
        "create": cmd_calendars_create,
        "update": cmd_calendars_update,
        "delete": cmd_calendars_delete,
        "set-default": cmd_calendars_set_default,
        "effective": cmd_calendars_effective,
        "exceptions": cmd_calendars_exceptions,
    },
    "work-log": {
        "list": cmd_work_log_list,
    },
}

SUB_COMMAND_KEYS = {
    "baselines": "baselines_command",
    "calendars": "calendars_command",
    "work-log": "work_log_command",
}

COMPANY_ONLY = set()

ACTIVITIES_HANDLERS = {
    "wbs-tree": cmd_activities_wbs_tree,
    "field-changes": cmd_activities_field_changes,
    "critical-path": cmd_activities_critical_path,
    "schedule-summary": cmd_activities_schedule_summary,
    "progress-summary": cmd_activities_progress_summary,
    "progress-curve": cmd_activities_progress_curve,
    "baseline-curve": cmd_activities_baseline_curve,
    "trend": cmd_activities_trend,
    "archive": cmd_activities_archive,
    "unarchive": cmd_activities_unarchive,
    "restore": cmd_activities_restore,
    "reparent": cmd_activities_reparent,
    "toggle-closed": cmd_activities_toggle_closed,
    "assign-users": cmd_activities_assign_users,
}
