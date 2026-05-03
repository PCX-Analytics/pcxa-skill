"""Timesheet, time entry, cost entry, and time period lock commands."""

import json
import sys

from pcxa._output import out_json, out_table


def _timesheet_row(t):
    return {
        "id": str(t.get("id", "")),
        "resource": str(t.get("resource_name") or t.get("resource", ""))[:20],
        "period": f"{str(t.get('period_start', ''))[:10]}..{str(t.get('period_end', ''))[:10]}",
        "type": t.get("period_type", ""),
        "status": t.get("status", ""),
        "hours": str(t.get("total_hours", 0)),
        "cost": str(t.get("total_cost", 0)),
        "entries": str(t.get("entries_count", 0)),
    }


def cmd_timesheets_list(client, args):
    """List timesheets."""
    params = client.paginate_params(args.limit, args.offset)
    if args.status:
        params["status"] = args.status
    if args.resource:
        params["resource"] = args.resource
    if args.period_type:
        params["period_type"] = args.period_type
    if args.after:
        params["period_start_after"] = args.after
    if args.before:
        params["period_start_before"] = args.before
    if args.sort:
        params["ordering"] = args.sort
    data = client.get("timesheets/", params)
    if args.format == "json":
        out_json(data)
        return
    results = data.get("results", data) if isinstance(data, dict) else data
    total = data.get("count", len(results)) if isinstance(data, dict) else len(results)
    rows = [_timesheet_row(t) for t in results]
    print(f"Timesheets: {len(rows)} of {total}\n")
    out_table(rows, ["id", "resource", "period", "type", "status", "hours", "cost", "entries"])


def cmd_timesheets_get(client, args):
    """Timesheet detail with entries."""
    data = client.get(f"timesheets/{args.timesheet_id}/")
    if args.format == "json":
        out_json(data)
        return
    print(f"Timesheet {data.get('id')}")
    print(f"  Resource:  {data.get('resource_name') or data.get('resource')}")
    print(f"  Period:    {data.get('period_start')} to {data.get('period_end')} ({data.get('period_type')})")
    print(f"  Status:    {data.get('status')} {'(editable)' if data.get('is_editable') else '(locked)'}")
    print(f"  Hours:     {data.get('total_regular_hours', 0)} regular + {data.get('total_overtime_hours', 0)} OT = {data.get('total_hours', 0)} total")
    print(f"  Cost:      {data.get('total_cost', 0)}  Billable: {data.get('total_billable', 0)}")
    if data.get("submitted_by"):
        print(f"  Submitted: {str(data.get('submitted_at', ''))[:19]} by {data.get('submitted_by')}")
    if data.get("approved_by"):
        print(f"  Approved:  {str(data.get('approved_at', ''))[:19]} by {data.get('approved_by')}")
    if data.get("rejection_reason"):
        print(f"  Rejected:  {data['rejection_reason'][:200]}")

    entries = data.get("entries") or []
    if entries:
        print(f"\n  Time Entries ({len(entries)}):")
        for e in entries:
            desc = f" ({e['description']})" if e.get("description") else ""
            print(f"    [{e.get('id')}] {e.get('date')} {e.get('hours')}h {e.get('entry_type')} — {e.get('activity_title') or e.get('activity')}{desc}")

    cost_entries = data.get("cost_entries") or []
    if cost_entries:
        print(f"\n  Cost Entries ({len(cost_entries)}):")
        for e in cost_entries:
            print(f"    [{e.get('id')}] {e.get('date')} qty={e.get('quantity')} @{e.get('unit_cost')} = {e.get('total_cost')} — {e.get('activity_title') or e.get('activity')}")
    print()


def cmd_timesheets_create(client, args):
    """Create timesheet."""
    payload = {
        "resource": args.resource,
        "period_start": args.period_start,
        "period_end": args.period_end,
    }
    if args.period_type:
        payload["period_type"] = args.period_type
    if args.dry_run:
        print(f"Would CREATE timesheet: {json.dumps(payload, indent=2)}")
        return
    data = client.post("timesheets/", payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Created timesheet {data.get('id')}: {data.get('period_start')} to {data.get('period_end')} ({data.get('status')})")


def cmd_timesheets_update(client, args):
    """Update timesheet."""
    payload = {}
    if args.period_start:
        payload["period_start"] = args.period_start
    if args.period_end:
        payload["period_end"] = args.period_end
    if args.period_type:
        payload["period_type"] = args.period_type
    if not payload:
        print("No fields to update.", file=sys.stderr)
        sys.exit(1)
    if args.dry_run:
        print(f"Would UPDATE timesheet {args.timesheet_id}: {json.dumps(payload, indent=2)}")
        return
    data = client.patch(f"timesheets/{args.timesheet_id}/", payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Updated timesheet {data.get('id')}")


def cmd_timesheets_delete(client, args):
    """Delete timesheet."""
    if args.dry_run:
        print(f"Would DELETE timesheet {args.timesheet_id}")
        return
    client.delete(f"timesheets/{args.timesheet_id}/")
    print(f"Deleted timesheet {args.timesheet_id}")


def cmd_timesheets_submit(client, args):
    """Submit timesheet for approval."""
    if args.dry_run:
        print(f"Would SUBMIT timesheet {args.timesheet_id}")
        return
    data = client.post(f"timesheets/{args.timesheet_id}/submit/")
    if args.format == "json":
        out_json(data)
    else:
        print(f"Submitted timesheet {data.get('id')} (status={data.get('status')})")


def cmd_timesheets_approve(client, args):
    """Approve timesheet."""
    if args.dry_run:
        print(f"Would APPROVE timesheet {args.timesheet_id}")
        return
    data = client.post(f"timesheets/{args.timesheet_id}/approve/")
    if args.format == "json":
        out_json(data)
    else:
        print(f"Approved timesheet {data.get('id')} (status={data.get('status')})")


def cmd_timesheets_reject(client, args):
    """Reject timesheet."""
    payload = {}
    if args.reason:
        payload["rejection_reason"] = args.reason
    if args.dry_run:
        print(f"Would REJECT timesheet {args.timesheet_id}")
        return
    data = client.post(f"timesheets/{args.timesheet_id}/reject/", payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Rejected timesheet {data.get('id')} (status={data.get('status')})")


def cmd_timesheets_reopen(client, args):
    """Reopen rejected timesheet."""
    if args.dry_run:
        print(f"Would REOPEN timesheet {args.timesheet_id}")
        return
    data = client.post(f"timesheets/{args.timesheet_id}/reopen/")
    if args.format == "json":
        out_json(data)
    else:
        print(f"Reopened timesheet {data.get('id')} (status={data.get('status')})")


# ═══════════════════════════════════════════════════════════════════════════════
# TIME ENTRIES — Hours logged against activities
# ═══════════════════════════════════════════════════════════════════════════════


def cmd_entries_list(client, args):
    """List time entries for a timesheet."""
    params = {}
    if args.date:
        params["date"] = args.date
    if args.after:
        params["date_after"] = args.after
    if args.before:
        params["date_before"] = args.before
    if args.activity:
        params["activity"] = args.activity
    if args.entry_type:
        params["entry_type"] = args.entry_type
    data = client.get(f"timesheets/{args.timesheet_id}/entries/", params)
    if args.format == "json":
        out_json(data)
        return
    results = data.get("results", data) if isinstance(data, dict) else data
    rows = []
    for e in results:
        rows.append({
            "id": str(e.get("id", "")),
            "date": str(e.get("date", "")),
            "hours": str(e.get("hours", "")),
            "type": e.get("entry_type", ""),
            "activity": str(e.get("activity_title") or e.get("activity", ""))[:25],
            "cost_code": str(e.get("cost_code") or "-"),
            "desc": str(e.get("description", ""))[:30],
        })
    print(f"Time entries for timesheet {args.timesheet_id}: {len(rows)}\n")
    out_table(rows, ["id", "date", "hours", "type", "activity", "cost_code", "desc"])


def cmd_entries_create(client, args):
    """Create time entry."""
    payload = {
        "activity": args.activity,
        "date": args.date,
        "hours": str(args.hours),
    }
    if args.entry_type:
        payload["entry_type"] = args.entry_type
    if args.cost_code:
        payload["cost_code"] = args.cost_code
    if args.description:
        payload["description"] = args.description
    if args.dry_run:
        print(f"Would CREATE time entry: {json.dumps(payload, indent=2)}")
        return
    data = client.post(f"timesheets/{args.timesheet_id}/entries/", payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Created entry {data.get('id')}: {data.get('date')} {data.get('hours')}h on activity {data.get('activity')}")


def cmd_entries_update(client, args):
    """Update time entry."""
    payload = {}
    if args.date:
        payload["date"] = args.date
    if args.hours is not None:
        payload["hours"] = str(args.hours)
    if args.entry_type:
        payload["entry_type"] = args.entry_type
    if args.cost_code is not None:
        payload["cost_code"] = args.cost_code if args.cost_code != 0 else None
    if args.description is not None:
        payload["description"] = args.description
    if not payload:
        print("No fields to update.", file=sys.stderr)
        sys.exit(1)
    if args.dry_run:
        print(f"Would UPDATE entry {args.entry_id}: {json.dumps(payload, indent=2)}")
        return
    data = client.patch(f"timesheets/{args.timesheet_id}/entries/{args.entry_id}/", payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Updated entry {data.get('id')}")


def cmd_entries_delete(client, args):
    """Delete time entry."""
    if args.dry_run:
        print(f"Would DELETE entry {args.entry_id}")
        return
    client.delete(f"timesheets/{args.timesheet_id}/entries/{args.entry_id}/")
    print(f"Deleted entry {args.entry_id}")


# ═══════════════════════════════════════════════════════════════════════════════
# COST ENTRIES — Non-labor costs logged against activities
# ═══════════════════════════════════════════════════════════════════════════════


def cmd_costentries_list(client, args):
    """List cost entries for a timesheet."""
    params = {}
    if args.date:
        params["date"] = args.date
    if args.activity:
        params["activity"] = args.activity
    if args.resource:
        params["resource"] = args.resource
    data = client.get(f"timesheets/{args.timesheet_id}/cost-entries/", params)
    if args.format == "json":
        out_json(data)
        return
    results = data.get("results", data) if isinstance(data, dict) else data
    rows = []
    for e in results:
        rows.append({
            "id": str(e.get("id", "")),
            "date": str(e.get("date", "")),
            "resource": str(e.get("resource_name") or e.get("resource", ""))[:15],
            "activity": str(e.get("activity_title") or e.get("activity", ""))[:20],
            "qty": str(e.get("quantity", "")),
            "unit_cost": str(e.get("unit_cost", "")),
            "total": str(e.get("total_cost", "")),
            "desc": str(e.get("description", ""))[:25],
        })
    print(f"Cost entries for timesheet {args.timesheet_id}: {len(rows)}\n")
    out_table(rows, ["id", "date", "resource", "activity", "qty", "unit_cost", "total", "desc"])


def cmd_costentries_create(client, args):
    """Create cost entry."""
    payload = {
        "resource": args.resource,
        "activity": args.activity,
        "date": args.date,
        "quantity": str(args.quantity),
        "unit_cost": str(args.unit_cost),
    }
    if args.cost_code:
        payload["cost_code"] = args.cost_code
    if args.description:
        payload["description"] = args.description
    if args.dry_run:
        print(f"Would CREATE cost entry: {json.dumps(payload, indent=2)}")
        return
    data = client.post(f"timesheets/{args.timesheet_id}/cost-entries/", payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Created cost entry {data.get('id')}: qty={data.get('quantity')} @{data.get('unit_cost')} = {data.get('total_cost')}")


def cmd_costentries_update(client, args):
    """Update cost entry."""
    payload = {}
    if args.date:
        payload["date"] = args.date
    if args.quantity is not None:
        payload["quantity"] = str(args.quantity)
    if args.unit_cost is not None:
        payload["unit_cost"] = str(args.unit_cost)
    if args.cost_code is not None:
        payload["cost_code"] = args.cost_code if args.cost_code != 0 else None
    if args.description is not None:
        payload["description"] = args.description
    if not payload:
        print("No fields to update.", file=sys.stderr)
        sys.exit(1)
    if args.dry_run:
        print(f"Would UPDATE cost entry {args.entry_id}: {json.dumps(payload, indent=2)}")
        return
    data = client.patch(f"timesheets/{args.timesheet_id}/cost-entries/{args.entry_id}/", payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Updated cost entry {data.get('id')}")


def cmd_costentries_delete(client, args):
    """Delete cost entry."""
    if args.dry_run:
        print(f"Would DELETE cost entry {args.entry_id}")
        return
    client.delete(f"timesheets/{args.timesheet_id}/cost-entries/{args.entry_id}/")
    print(f"Deleted cost entry {args.entry_id}")


# ═══════════════════════════════════════════════════════════════════════════════
# TIME PERIOD LOCKS — Prevent edits to specific date ranges
# ═══════════════════════════════════════════════════════════════════════════════


def cmd_locks_list(client, args):
    """List time period locks."""
    data = client.get("time-period-locks/")
    if args.format == "json":
        out_json(data)
        return
    results = data.get("results", data) if isinstance(data, dict) else data
    rows = []
    for lk in results:
        rows.append({
            "id": str(lk.get("id", "")),
            "start": str(lk.get("period_start", "")),
            "end": str(lk.get("period_end", "")),
            "locked_by": str(lk.get("locked_by") or "-"),
            "reason": str(lk.get("reason", ""))[:40],
            "created": str(lk.get("created_at", ""))[:10],
        })
    print(f"Time period locks: {len(rows)}\n")
    out_table(rows, ["id", "start", "end", "locked_by", "reason", "created"])


def cmd_locks_create(client, args):
    """Create time period lock."""
    payload = {
        "period_start": args.period_start,
        "period_end": args.period_end,
    }
    if args.reason:
        payload["reason"] = args.reason
    if args.dry_run:
        print(f"Would CREATE lock: {json.dumps(payload, indent=2)}")
        return
    data = client.post("time-period-locks/", payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Created lock {data.get('id')}: {data.get('period_start')} to {data.get('period_end')}")


def cmd_locks_delete(client, args):
    """Delete time period lock."""
    if args.dry_run:
        print(f"Would DELETE lock {args.lock_id}")
        return
    client.delete(f"time-period-locks/{args.lock_id}/")
    print(f"Deleted lock {args.lock_id}")
