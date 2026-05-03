"""Resource, rate, and assignment commands."""

import json
import sys

from pcxa._output import out_json, out_table


def _resource_row(r):
    return {
        "id": str(r.get("id", "")),
        "name": str(r.get("name", ""))[:35],
        "type": r.get("resource_type", ""),
        "unit": r.get("unit_of_measure", ""),
        "cap/day": str(r.get("default_capacity_per_day", "")),
        "user": str(r.get("user") or "-"),
        "active": "yes" if r.get("is_active") else "no",
        "assigns": str(r.get("assignments_count", 0)),
    }


def cmd_resources_list(client, args):
    """List resources."""
    params = client.paginate_params(args.limit, args.offset)
    if args.resource_type:
        params["resource_type"] = args.resource_type
    if args.active is not None:
        params["is_active"] = str(args.active).lower()
    if args.user:
        params["user"] = args.user
    if args.name:
        params["name"] = args.name
    if args.search:
        params["search"] = args.search
    if args.sort:
        params["ordering"] = args.sort
    data = client.get("resources/", params)
    if args.format == "json":
        out_json(data)
        return
    results = data.get("results", data) if isinstance(data, dict) else data
    total = data.get("count", len(results)) if isinstance(data, dict) else len(results)
    rows = [_resource_row(r) for r in results]
    print(f"Resources: {len(rows)} of {total}\n")
    out_table(rows, ["id", "name", "type", "unit", "cap/day", "user", "active", "assigns"])


def cmd_resources_get(client, args):
    """Resource detail with rates."""
    data = client.get(f"resources/{args.resource_id}/")
    if args.format == "json":
        out_json(data)
        return
    print(f"Resource {data.get('id')}: {data.get('name')}")
    print(f"  Type:     {data.get('resource_type')}")
    print(f"  Unit:     {data.get('unit_of_measure')}")
    print(f"  Cap/Day:  {data.get('default_capacity_per_day')}")
    print(f"  Active:   {data.get('is_active')}")
    print(f"  User:     {data.get('user') or '-'}")
    if data.get("description"):
        print(f"  Desc:     {data['description'][:200]}")
    rates = data.get("rates") or []
    if rates:
        print(f"\n  Rates ({len(rates)}):")
        for r in rates:
            print(f"    [{r.get('id')}] {r.get('effective_date')}: std={r.get('standard_rate')} ot={r.get('overtime_rate', '-')} cost={r.get('cost_rate')} bill={r.get('bill_rate')} {r.get('currency', 'USD')}")
    print()


def cmd_resources_create(client, args):
    """Create resource."""
    payload = {"name": args.name, "resource_type": args.resource_type}
    if args.description:
        payload["description"] = args.description
    if args.user:
        payload["user"] = args.user
    if args.unit:
        payload["unit_of_measure"] = args.unit
    if args.capacity is not None:
        payload["default_capacity_per_day"] = str(args.capacity)
    if args.dry_run:
        print(f"Would CREATE resource: {json.dumps(payload, indent=2)}")
        return
    data = client.post("resources/", payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Created resource {data.get('id')}: '{data.get('name')}' ({data.get('resource_type')})")


def cmd_resources_update(client, args):
    """Update resource."""
    payload = {}
    if args.name:
        payload["name"] = args.name
    if args.resource_type:
        payload["resource_type"] = args.resource_type
    if args.description is not None:
        payload["description"] = args.description
    if args.user is not None:
        payload["user"] = args.user if args.user != 0 else None
    if args.unit:
        payload["unit_of_measure"] = args.unit
    if args.capacity is not None:
        payload["default_capacity_per_day"] = str(args.capacity)
    if args.active is not None:
        payload["is_active"] = args.active
    if not payload:
        print("No fields to update.", file=sys.stderr)
        sys.exit(1)
    if args.dry_run:
        print(f"Would UPDATE resource {args.resource_id}: {json.dumps(payload, indent=2)}")
        return
    data = client.patch(f"resources/{args.resource_id}/", payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Updated resource {data.get('id')}: '{data.get('name')}'")


def cmd_resources_delete(client, args):
    """Delete resource."""
    if args.dry_run:
        print(f"Would DELETE resource {args.resource_id}")
        return
    client.delete(f"resources/{args.resource_id}/")
    print(f"Deleted resource {args.resource_id}")


# ═══════════════════════════════════════════════════════════════════════════════
# RATES — Resource rate history (append-only)
# ═══════════════════════════════════════════════════════════════════════════════


def cmd_rates_list(client, args):
    """List rates for a resource."""
    data = client.get(f"resources/{args.resource_id}/rates/")
    if args.format == "json":
        out_json(data)
        return
    results = data.get("results", data) if isinstance(data, dict) else data
    rows = []
    for r in results:
        rows.append({
            "id": str(r.get("id", "")),
            "effective": str(r.get("effective_date", "")),
            "standard": str(r.get("standard_rate", "")),
            "overtime": str(r.get("overtime_rate") or "-"),
            "cost": str(r.get("cost_rate", "")),
            "bill": str(r.get("bill_rate", "")),
            "currency": r.get("currency", "USD"),
            "notes": str(r.get("notes", ""))[:30],
        })
    print(f"Rates for resource {args.resource_id}: {len(rows)}\n")
    out_table(rows, ["id", "effective", "standard", "overtime", "cost", "bill", "currency", "notes"])


def cmd_rates_create(client, args):
    """Create a new rate for a resource."""
    payload = {
        "effective_date": args.effective_date,
        "standard_rate": str(args.standard_rate),
        "cost_rate": str(args.cost_rate),
        "bill_rate": str(args.bill_rate),
    }
    if args.overtime_rate is not None:
        payload["overtime_rate"] = str(args.overtime_rate)
    if args.currency:
        payload["currency"] = args.currency
    if args.notes:
        payload["notes"] = args.notes
    if args.dry_run:
        print(f"Would CREATE rate on resource {args.resource_id}: {json.dumps(payload, indent=2)}")
        return
    data = client.post(f"resources/{args.resource_id}/rates/", payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Created rate {data.get('id')}: effective={data.get('effective_date')} std={data.get('standard_rate')}")


# ═══════════════════════════════════════════════════════════════════════════════
# ASSIGNMENTS — Resource assignments on activities
# ═══════════════════════════════════════════════════════════════════════════════


def _assignment_row(a):
    return {
        "id": str(a.get("id", "")),
        "resource": str(a.get("resource_name") or a.get("resource", ""))[:25],
        "type": a.get("resource_type", ""),
        "planned": str(a.get("planned_units", "")),
        "actual": str(a.get("actual_units", 0)),
        "remaining": str(a.get("remaining_units") or "-"),
        "role": str(a.get("role_label") or "")[:20],
        "driving": "yes" if a.get("is_driving") else "",
    }


def cmd_assignments_list(client, args):
    """List resource assignments for an activity."""
    data = client.get(f"activities/{args.activity_id}/resource-assignments/")
    if args.format == "json":
        out_json(data)
        return
    results = data.get("results", data) if isinstance(data, dict) else data
    rows = [_assignment_row(a) for a in results]
    print(f"Assignments for activity {args.activity_id}: {len(rows)}\n")
    out_table(rows, ["id", "resource", "type", "planned", "actual", "remaining", "role", "driving"])


def cmd_assignments_create(client, args):
    """Create resource assignment."""
    payload = {"resource": args.resource_id}
    if args.planned_units is not None:
        payload["planned_units"] = str(args.planned_units)
    if args.planned_per_day is not None:
        payload["planned_units_per_day"] = str(args.planned_per_day)
    if args.curve:
        payload["resource_curve"] = args.curve
    if args.driving:
        payload["is_driving"] = True
    if args.role:
        payload["role_label"] = args.role
    if args.start:
        payload["assignment_start"] = args.start
    if args.end:
        payload["assignment_end"] = args.end
    if args.dry_run:
        print(f"Would CREATE assignment on activity {args.activity_id}: {json.dumps(payload, indent=2)}")
        return
    data = client.post(f"activities/{args.activity_id}/resource-assignments/", payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Created assignment {data.get('id')}: resource={data.get('resource')} planned={data.get('planned_units')}")


def cmd_assignments_update(client, args):
    """Update resource assignment."""
    payload = {}
    if args.planned_units is not None:
        payload["planned_units"] = str(args.planned_units)
    if args.planned_per_day is not None:
        payload["planned_units_per_day"] = str(args.planned_per_day)
    if args.remaining is not None:
        payload["remaining_units"] = str(args.remaining)
    if args.at_completion is not None:
        payload["at_completion_units"] = str(args.at_completion)
    if args.curve:
        payload["resource_curve"] = args.curve
    if args.driving is not None:
        payload["is_driving"] = args.driving
    if args.role is not None:
        payload["role_label"] = args.role
    if args.start:
        payload["assignment_start"] = args.start
    if args.end:
        payload["assignment_end"] = args.end
    if not payload:
        print("No fields to update.", file=sys.stderr)
        sys.exit(1)
    if args.dry_run:
        print(f"Would UPDATE assignment {args.assignment_id}: {json.dumps(payload, indent=2)}")
        return
    data = client.patch(f"activities/{args.activity_id}/resource-assignments/{args.assignment_id}/", payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Updated assignment {data.get('id')}")


def cmd_assignments_delete(client, args):
    """Delete resource assignment."""
    if args.dry_run:
        print(f"Would DELETE assignment {args.assignment_id}")
        return
    client.delete(f"activities/{args.activity_id}/resource-assignments/{args.assignment_id}/")
    print(f"Deleted assignment {args.assignment_id}")
