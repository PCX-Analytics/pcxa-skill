"""Cost code and budget commands."""

import json
import sys

from pcxa._output import out_json, out_table


def _costcode_row(c):
    return {
        "id": str(c.get("id", "")),
        "code": str(c.get("code", "")),
        "name": str(c.get("name", ""))[:35],
        "parent": str(c.get("parent") or "-"),
        "active": "yes" if c.get("is_active") else "no",
        "children": str(c.get("children_count", 0)),
    }


def cmd_costcodes_list(client, args):
    """List cost codes."""
    params = client.paginate_params(args.limit, args.offset)
    if args.code:
        params["code"] = args.code
    if args.name:
        params["name"] = args.name
    if args.active is not None:
        params["is_active"] = str(args.active).lower()
    if args.parent:
        params["parent"] = args.parent
    if args.root_only:
        params["root_only"] = "true"
    data = client.get("cost-codes/", params, project_scoped=False)
    if args.format == "json":
        out_json(data)
        return
    results = data.get("results", data) if isinstance(data, dict) else data
    total = data.get("count", len(results)) if isinstance(data, dict) else len(results)
    rows = [_costcode_row(c) for c in results]
    print(f"Cost codes: {len(rows)} of {total}\n")
    out_table(rows, ["id", "code", "name", "parent", "active", "children"])


def cmd_costcodes_get(client, args):
    """Cost code detail."""
    data = client.get(f"cost-codes/{args.costcode_id}/", project_scoped=False)
    if args.format == "json":
        out_json(data)
        return
    print(f"Cost Code {data.get('id')}: {data.get('code')} — {data.get('name')}")
    print(f"  Parent:   {data.get('parent') or '-'}")
    print(f"  Active:   {data.get('is_active')}")
    if data.get("description"):
        print(f"  Desc:     {data['description'][:200]}")
    children = data.get("children") or []
    if children:
        print(f"\n  Children ({len(children)}):")
        for c in children:
            print(f"    [{c.get('id')}] {c.get('code')} — {c.get('name')}")
    print()


def cmd_costcodes_create(client, args):
    """Create cost code."""
    payload = {"code": args.code, "name": args.name}
    if args.description:
        payload["description"] = args.description
    if args.parent:
        payload["parent"] = args.parent
    if args.sort_order is not None:
        payload["sort_order"] = args.sort_order
    if args.dry_run:
        print(f"Would CREATE cost code: {json.dumps(payload, indent=2)}")
        return
    data = client.post("cost-codes/", payload, project_scoped=False)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Created cost code {data.get('id')}: {data.get('code')} — {data.get('name')}")


def cmd_costcodes_update(client, args):
    """Update cost code."""
    payload = {}
    if args.code:
        payload["code"] = args.code
    if args.name:
        payload["name"] = args.name
    if args.description is not None:
        payload["description"] = args.description
    if args.parent is not None:
        payload["parent"] = args.parent if args.parent != 0 else None
    if args.sort_order is not None:
        payload["sort_order"] = args.sort_order
    if args.active is not None:
        payload["is_active"] = args.active
    if not payload:
        print("No fields to update.", file=sys.stderr)
        sys.exit(1)
    if args.dry_run:
        print(f"Would UPDATE cost code {args.costcode_id}: {json.dumps(payload, indent=2)}")
        return
    data = client.patch(f"cost-codes/{args.costcode_id}/", payload, project_scoped=False)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Updated cost code {data.get('id')}: {data.get('code')}")


def cmd_costcodes_delete(client, args):
    """Delete cost code."""
    if args.dry_run:
        print(f"Would DELETE cost code {args.costcode_id}")
        return
    client.delete(f"cost-codes/{args.costcode_id}/", project_scoped=False)
    print(f"Deleted cost code {args.costcode_id}")


# ═══════════════════════════════════════════════════════════════════════════════
# BUDGETS — Project cost code budgets
# ═══════════════════════════════════════════════════════════════════════════════


def _budget_row(b):
    return {
        "id": str(b.get("id", "")),
        "code": str(b.get("cost_code_code") or "")[:15],
        "name": str(b.get("cost_code_name") or "")[:30],
        "amount": str(b.get("budgeted_amount", 0)),
        "units": str(b.get("budgeted_units", 0)),
    }


def cmd_budgets_list(client, args):
    """List cost code budgets."""
    params = client.paginate_params(args.limit, args.offset)
    data = client.get("cost-code-budgets/", params)
    if args.format == "json":
        out_json(data)
        return
    results = data.get("results", data) if isinstance(data, dict) else data
    total = data.get("count", len(results)) if isinstance(data, dict) else len(results)
    rows = [_budget_row(b) for b in results]
    print(f"Budgets: {len(rows)} of {total}\n")
    out_table(rows, ["id", "code", "name", "amount", "units"])


def cmd_budgets_create(client, args):
    """Create cost code budget."""
    payload = {"cost_code": args.cost_code}
    if args.amount is not None:
        payload["budgeted_amount"] = str(args.amount)
    if args.units is not None:
        payload["budgeted_units"] = str(args.units)
    if args.dry_run:
        print(f"Would CREATE budget: {json.dumps(payload, indent=2)}")
        return
    data = client.post("cost-code-budgets/", payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Created budget {data.get('id')}: cost_code={data.get('cost_code')} amount={data.get('budgeted_amount')}")


def cmd_budgets_update(client, args):
    """Update cost code budget."""
    payload = {}
    if args.amount is not None:
        payload["budgeted_amount"] = str(args.amount)
    if args.units is not None:
        payload["budgeted_units"] = str(args.units)
    if not payload:
        print("No fields to update.", file=sys.stderr)
        sys.exit(1)
    if args.dry_run:
        print(f"Would UPDATE budget {args.budget_id}: {json.dumps(payload, indent=2)}")
        return
    data = client.patch(f"cost-code-budgets/{args.budget_id}/", payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Updated budget {data.get('id')}")


def cmd_budgets_delete(client, args):
    """Delete cost code budget."""
    if args.dry_run:
        print(f"Would DELETE budget {args.budget_id}")
        return
    client.delete(f"cost-code-budgets/{args.budget_id}/")
    print(f"Deleted budget {args.budget_id}")
