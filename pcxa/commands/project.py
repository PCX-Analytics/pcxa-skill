"""Project metadata and membership commands."""

import json
import sys

from pcxa._output import out_json, out_table


def cmd_project_get(client, args):
    """Show project details."""
    url = client._url("")
    data = client._request("GET", url).json()
    if args.format == "json":
        out_json(data)
    else:
        rows = []
        display_fields = [
            "id", "name", "code", "industry", "description", "scope_statement",
            "life_cycle", "start_date", "end_date", "percent_complete",
            "progress_input_method", "rollup_method", "owner_username",
            "company_name", "is_archived", "created_at", "updated_at",
        ]
        for key in display_fields:
            val = data.get(key, "")
            if val is None:
                val = ""
            rows.append({"field": key, "value": str(val)})
        out_table(rows, ["field", "value"])


def cmd_project_update(client, args):
    """Update project metadata."""
    payload = {}
    if args.name is not None:
        payload["name"] = args.name
    if args.code is not None:
        payload["code"] = args.code or None
    if args.description is not None:
        payload["description"] = args.description
    if args.scope_statement is not None:
        payload["scope_statement"] = args.scope_statement
    if args.industry is not None:
        payload["industry"] = args.industry
    if args.life_cycle is not None:
        payload["life_cycle"] = args.life_cycle
    if args.start_date is not None:
        payload["start_date"] = args.start_date or None
    if args.end_date is not None:
        payload["end_date"] = args.end_date or None
    if args.progress_input_method is not None:
        payload["progress_input_method"] = args.progress_input_method
    if args.rollup_method is not None:
        payload["rollup_method"] = args.rollup_method
    if not payload:
        print("Nothing to update — provide at least one field.", file=sys.stderr)
        sys.exit(1)
    if args.dry_run:
        print(f"Would UPDATE project: {json.dumps(payload, indent=2)}")
        return
    url = client._url("")
    data = client._request("PATCH", url, json=payload).json()
    if args.format == "json":
        out_json(data)
    else:
        print(f"Project updated: {data.get('name')}")


def cmd_project_members(client, args):
    """List project members with user IDs."""
    params = {"limit": 100}
    if args.search:
        params["search"] = args.search
    data = client.get("memberships/", params)
    if args.format == "json":
        out_json(data)
        return
    results = data.get("results", data) if isinstance(data, dict) else data
    rows = []
    for m in results:
        if m.get("is_ai_agent"):
            continue
        rows.append({
            "user_id": m.get("user"),
            "name": m.get("user_name", ""),
            "username": m.get("user_username", ""),
            "email": m.get("user_email", ""),
            "role": m.get("role", ""),
        })
    print(f"Project members: {len(rows)}\n")
    out_table(rows, ["user_id", "name", "username", "email", "role"])
