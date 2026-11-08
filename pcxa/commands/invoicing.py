"""Project invoicing commands: contracts, schedule of values, pay applications, change orders.

The web UI's Invoicing pages (AIA G702/G703 billing). Everything lives under a
project contract::

    contracts/                                         contracts
    contracts/{c}/sov-items/                           schedule of values (SOV)
    contracts/{c}/pay-applications/                    pay applications (G702)
    contracts/{c}/pay-applications/{p}/line-items/     pay application lines (G703)
    contracts/{c}/change-orders/                       change orders

Nested groups take the contract id as a required ``--contract`` flag. Money
fields are sent as the decimal strings the serializers expect ("12500.00"),
never floats, so no rounding happens on the way to the server.
"""

import json
import sys
from decimal import Decimal, InvalidOperation

from pcxa._output import out_json, out_table


def _money(value):
    """argparse type: a decimal amount, passed through as the string given."""
    try:
        Decimal(value)
    except InvalidOperation:
        raise ValueError(value)
    return value.strip()


_money.__name__ = "decimal amount"  # argparse names the type in its error message


def _data(args, payload):
    """Merge the ``--data`` JSON object into ``payload`` (last, so it wins)."""
    raw = getattr(args, "data", None)
    if not raw:
        return payload
    try:
        extra = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"--data is not valid JSON: {e}", file=sys.stderr)
        sys.exit(1)
    if not isinstance(extra, dict):
        print("--data must be a JSON object.", file=sys.stderr)
        sys.exit(1)
    payload.update(extra)
    return payload


def _results(data):
    results = data.get("results", data) if isinstance(data, dict) else data
    total = data.get("count", len(results)) if isinstance(data, dict) else len(results)
    return results, total


def _require_fields(payload):
    if not payload:
        print("No fields to update.", file=sys.stderr)
        sys.exit(1)


# ═══════════════════════════════════════════════════════════════════════════════
# CONTRACTS
# ═══════════════════════════════════════════════════════════════════════════════


def _contract_row(c):
    return {
        "id": str(c.get("id", "")),
        "number": str(c.get("contract_number") or "")[:15],
        "name": str(c.get("name", ""))[:35],
        "type": str(c.get("contract_type", "")),
        "status": str(c.get("status", "")),
        "current": str(c.get("current_amount") or c.get("original_amount") or ""),
        "sov": str(c.get("sov_items_count", "")),
        "pay_apps": str(c.get("pay_applications_count", "")),
    }


def cmd_contracts_list(client, args):
    """List project contracts."""
    params = client.paginate_params(args.limit, args.offset)
    if args.status:
        params["status"] = args.status
    if args.type:
        params["contract_type"] = args.type
    if args.name:
        params["name"] = args.name
    data = client.get("contracts/", params)
    if args.format == "json":
        out_json(data)
        return
    results, total = _results(data)
    rows = [_contract_row(c) for c in results]
    print(f"Contracts: {len(rows)} of {total}\n")
    out_table(rows, ["id", "number", "name", "type", "status", "current", "sov", "pay_apps"])


def cmd_contracts_get(client, args):
    """Contract detail, with its SOV items."""
    data = client.get(f"contracts/{args.contract_id}/")
    if args.format == "json":
        out_json(data)
        return
    print(f"Contract {data.get('id')}: {data.get('name')} [{data.get('status')}]")
    print(f"  Number:     {data.get('contract_number') or '-'}")
    print(f"  Type:       {data.get('contract_type')}")
    print(f"  Original:   {data.get('original_amount')}")
    print(f"  Current:    {data.get('current_amount') or data.get('revised_amount') or '-'}")
    print(f"  Retainage:  {data.get('retainage_percent')}%")
    print(f"  Client:     {data.get('client_name') or '-'}")
    print(f"  Dates:      {data.get('start_date') or '-'} .. {data.get('end_date') or '-'}")
    items = data.get("sov_items") or []
    if items:
        print(f"\n  SOV items ({len(items)}):")
        out_table([_sov_row(s) for s in items], ["id", "item", "description", "value", "activity", "cost_code"])
    print()


def _contract_payload(args):
    payload = {}
    for flag, field in (
        ("name", "name"),
        ("number", "contract_number"),
        ("type", "contract_type"),
        ("original_amount", "original_amount"),
        ("retainage", "retainage_percent"),
        ("client_name", "client_name"),
        ("client_contact", "client_contact"),
        ("start", "start_date"),
        ("end", "end_date"),
    ):
        value = getattr(args, flag, None)
        if value is not None:
            payload[field] = value
    return _data(args, payload)


def cmd_contracts_create(client, args):
    """Create a contract (created as draft; status is server-managed)."""
    payload = _contract_payload(args)
    if args.dry_run:
        print(f"Would CREATE contract: {json.dumps(payload, indent=2)}")
        return
    data = client.post("contracts/", payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Created contract {data.get('id')}: '{data.get('name')}' [{data.get('status')}]")


def cmd_contracts_update(client, args):
    """Update a contract (refused by the server once completed or cancelled)."""
    payload = _contract_payload(args)
    _require_fields(payload)
    if args.dry_run:
        print(f"Would UPDATE contract {args.contract_id}: {json.dumps(payload, indent=2)}")
        return
    data = client.patch(f"contracts/{args.contract_id}/", payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Updated contract {data.get('id')}: '{data.get('name')}'")


def cmd_contracts_delete(client, args):
    """Soft-delete a contract."""
    if args.dry_run:
        print(f"Would DELETE contract {args.contract_id}")
        return
    client.delete(f"contracts/{args.contract_id}/")
    print(f"Deleted contract {args.contract_id}")


def cmd_contracts_restore(client, args):
    """Restore a soft-deleted contract (staff only)."""
    if args.dry_run:
        print(f"Would RESTORE contract {args.contract_id}")
        return
    data = client.post(f"contracts/{args.contract_id}/restore/")
    if args.format == "json":
        out_json(data)
    else:
        print(f"Restored contract {args.contract_id}")


# ═══════════════════════════════════════════════════════════════════════════════
# SOV — schedule of values items under a contract
# ═══════════════════════════════════════════════════════════════════════════════


def _sov_path(args, suffix=""):
    return f"contracts/{args.contract}/sov-items/{suffix}"


def _sov_row(s):
    return {
        "id": str(s.get("id", "")),
        "item": str(s.get("item_number", "")),
        "description": str(s.get("description", ""))[:40],
        "value": str(s.get("scheduled_value", "")),
        "activity": str(s.get("activity") or ""),
        "cost_code": str(s.get("cost_code") or ""),
    }


def cmd_sov_list(client, args):
    """List a contract's SOV items."""
    data = client.get(_sov_path(args), client.paginate_params(args.limit, args.offset))
    if args.format == "json":
        out_json(data)
        return
    results, total = _results(data)
    rows = [_sov_row(s) for s in results]
    print(f"SOV items on contract {args.contract}: {len(rows)} of {total}\n")
    out_table(rows, ["id", "item", "description", "value", "activity", "cost_code"])


def _sov_payload(args):
    payload = {}
    for flag, field in (
        ("item_number", "item_number"),
        ("description", "description"),
        ("value", "scheduled_value"),
        ("activity", "activity"),
        ("cost_code", "cost_code"),
        ("sort_order", "sort_order"),
    ):
        value = getattr(args, flag, None)
        if value is not None:
            payload[field] = value
    return _data(args, payload)


def cmd_sov_create(client, args):
    """Add an SOV item to a contract."""
    payload = _sov_payload(args)
    if args.dry_run:
        print(f"Would CREATE SOV item on contract {args.contract}: {json.dumps(payload, indent=2)}")
        return
    data = client.post(_sov_path(args), payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Created SOV item {data.get('id')}: {data.get('item_number')} {data.get('scheduled_value')}")


def cmd_sov_update(client, args):
    """Update an SOV item (refused once a non-draft pay application uses it)."""
    payload = _sov_payload(args)
    _require_fields(payload)
    if args.dry_run:
        print(f"Would UPDATE SOV item {args.item_id}: {json.dumps(payload, indent=2)}")
        return
    data = client.patch(_sov_path(args, f"{args.item_id}/"), payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Updated SOV item {data.get('id')}: {data.get('item_number')}")


def cmd_sov_delete(client, args):
    """Delete an SOV item (hard delete; SOV items are not soft-deleted)."""
    if args.dry_run:
        print(f"Would DELETE SOV item {args.item_id} from contract {args.contract}")
        return
    client.delete(_sov_path(args, f"{args.item_id}/"))
    print(f"Deleted SOV item {args.item_id}")


# ═══════════════════════════════════════════════════════════════════════════════
# PAY APPLICATIONS — G702 under a contract, G703 lines under a pay application
# ═══════════════════════════════════════════════════════════════════════════════


def _pay_app_path(args, suffix=""):
    return f"contracts/{args.contract}/pay-applications/{suffix}"


def _pay_app_row(p):
    return {
        "id": str(p.get("id", "")),
        "no": str(p.get("application_number", "")),
        "period": f"{p.get('period_start', '')}..{p.get('period_end', '')}",
        "status": str(p.get("status", "")),
        "completed": str(p.get("total_completed_to_date", "")),
        "retainage": str(p.get("retainage_held", "")),
        "due": str(p.get("current_payment_due", "")),
    }


def _line_row(line):
    return {
        "id": str(line.get("id", "")),
        "item": str(line.get("sov_item_number", "")),
        "description": str(line.get("sov_description", ""))[:30],
        "scheduled": str(line.get("scheduled_value", "")),
        "previous": str(line.get("previous_completed", "")),
        "current": str(line.get("current_completed", "")),
        "stored": str(line.get("stored_materials", "")),
        "pct": str(line.get("percent_complete", "")),
    }


_LINE_COLS = ["id", "item", "description", "scheduled", "previous", "current", "stored", "pct"]


def cmd_pay_apps_list(client, args):
    """List a contract's pay applications."""
    params = client.paginate_params(args.limit, args.offset)
    if args.status:
        params["status"] = args.status
    data = client.get(_pay_app_path(args), params)
    if args.format == "json":
        out_json(data)
        return
    results, total = _results(data)
    rows = [_pay_app_row(p) for p in results]
    print(f"Pay applications on contract {args.contract}: {len(rows)} of {total}\n")
    out_table(rows, ["id", "no", "period", "status", "completed", "retainage", "due"])


def cmd_pay_apps_get(client, args):
    """Pay application detail: G702 summary and G703 lines."""
    data = client.get(_pay_app_path(args, f"{args.pay_app_id}/"))
    if args.format == "json":
        out_json(data)
        return
    print(f"Pay application {data.get('id')} #{data.get('application_number')} [{data.get('status')}]")
    print(f"  Period:               {data.get('period_start')} .. {data.get('period_end')}")
    for field in (
        "original_contract_sum", "change_order_total", "contract_sum_to_date",
        "total_completed_to_date", "retainage_held", "total_earned_less_retainage",
        "less_previous_payments", "current_payment_due",
    ):
        print(f"  {field + ':':<22}{data.get(field)}")
    if data.get("rejection_reason"):
        print(f"  Rejected:             {data['rejection_reason'][:200]}")
    lines = data.get("line_items") or []
    if lines:
        print(f"\n  Line items ({len(lines)}):")
        out_table([_line_row(line) for line in lines], _LINE_COLS)
    print()


def cmd_pay_apps_create(client, args):
    """Create a pay application; the server numbers it and fills its lines.

    The contract must be active. Only the period is read from the request.
    """
    payload = {"period_start": args.period_start, "period_end": args.period_end}
    if args.dry_run:
        print(f"Would CREATE pay application on contract {args.contract}: {json.dumps(payload, indent=2)}")
        return
    data = client.post(_pay_app_path(args), payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Created pay application {data.get('id')} #{data.get('application_number')} [{data.get('status')}]")


def cmd_pay_apps_update(client, args):
    """Update a draft pay application (``notes`` is its only writable field)."""
    payload = {}
    if args.notes is not None:
        payload["notes"] = args.notes
    payload = _data(args, payload)
    _require_fields(payload)
    if args.dry_run:
        print(f"Would UPDATE pay application {args.pay_app_id}: {json.dumps(payload, indent=2)}")
        return
    data = client.patch(_pay_app_path(args, f"{args.pay_app_id}/"), payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Updated pay application {data.get('id')}")


def cmd_pay_apps_delete(client, args):
    """Soft-delete a draft pay application."""
    if args.dry_run:
        print(f"Would DELETE pay application {args.pay_app_id}")
        return
    client.delete(_pay_app_path(args, f"{args.pay_app_id}/"))
    print(f"Deleted pay application {args.pay_app_id}")


def _pay_app_action(client, args, url_path, verb, payload=None):
    if args.dry_run:
        extra = f": {json.dumps(payload)}" if payload else ""
        print(f"Would {verb.upper()} pay application {args.pay_app_id} on contract {args.contract}{extra}")
        return
    data = client.post(_pay_app_path(args, f"{args.pay_app_id}/{url_path}/"), payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Pay application {args.pay_app_id}: {data.get('status')}")


def cmd_pay_apps_submit(client, args):
    """draft -> submitted."""
    _pay_app_action(client, args, "submit", "submit")


def cmd_pay_apps_approve(client, args):
    """submitted -> approved (also locks the period's timesheets)."""
    _pay_app_action(client, args, "approve", "approve")


def cmd_pay_apps_reject(client, args):
    """submitted -> rejected, with an optional reason."""
    _pay_app_action(client, args, "reject", "reject", {"reason": args.reason or ""})


def cmd_pay_apps_reopen(client, args):
    """rejected -> draft."""
    _pay_app_action(client, args, "reopen", "reopen")


def cmd_pay_apps_mark_paid(client, args):
    """approved -> paid."""
    _pay_app_action(client, args, "mark-as-paid", "mark paid")


def _lines_path(args, suffix=""):
    return _pay_app_path(args, f"{args.pay_app_id}/line-items/{suffix}")


def cmd_pay_app_lines_list(client, args):
    """List a pay application's G703 line items."""
    data = client.get(_lines_path(args), client.paginate_params(args.limit, args.offset))
    if args.format == "json":
        out_json(data)
        return
    results, total = _results(data)
    rows = [_line_row(line) for line in results]
    print(f"Line items on pay application {args.pay_app_id}: {len(rows)} of {total}\n")
    out_table(rows, _LINE_COLS)


def cmd_pay_app_lines_update(client, args):
    """Set a line's work completed this period and stored materials (draft only)."""
    payload = {}
    if args.current is not None:
        payload["current_completed"] = args.current
    if args.stored is not None:
        payload["stored_materials"] = args.stored
    _require_fields(payload)
    if args.dry_run:
        print(f"Would UPDATE line item {args.line_id} on pay application {args.pay_app_id}: "
              f"{json.dumps(payload, indent=2)}")
        return
    data = client.patch(_lines_path(args, f"{args.line_id}/"), payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Updated line item {data.get('id')}: current={data.get('current_completed')} "
              f"stored={data.get('stored_materials')} pct={data.get('percent_complete')}")


_LINES_HANDLERS = {
    "list": cmd_pay_app_lines_list,
    "update": cmd_pay_app_lines_update,
}


def cmd_pay_apps_lines(client, args):
    """Dispatch ``pay-apps lines <subcommand>`` (nested third level)."""
    fn = _LINES_HANDLERS.get(getattr(args, "pay_apps_lines_command", None))
    if fn is None:
        print(f"Usage: pcxa pay-apps lines {{{', '.join(_LINES_HANDLERS)}}} <pay_app_id> --contract ID",
              file=sys.stderr)
        sys.exit(1)
    fn(client, args)


# ═══════════════════════════════════════════════════════════════════════════════
# CHANGE ORDERS — under a contract
# ═══════════════════════════════════════════════════════════════════════════════


def _co_path(args, suffix=""):
    return f"contracts/{args.contract}/change-orders/{suffix}"


def _co_row(c):
    return {
        "id": str(c.get("id", "")),
        "no": str(c.get("number", "")),
        "description": str(c.get("description", ""))[:40],
        "amount": str(c.get("amount", "")),
        "status": str(c.get("status", "")),
        "approved_at": str(c.get("approved_at") or "")[:10],
    }


def cmd_change_orders_list(client, args):
    """List a contract's change orders."""
    params = client.paginate_params(args.limit, args.offset)
    if args.status:
        params["status"] = args.status
    data = client.get(_co_path(args), params)
    if args.format == "json":
        out_json(data)
        return
    results, total = _results(data)
    rows = [_co_row(c) for c in results]
    print(f"Change orders on contract {args.contract}: {len(rows)} of {total}\n")
    out_table(rows, ["id", "no", "description", "amount", "status", "approved_at"])


def cmd_change_orders_get(client, args):
    """Change order detail."""
    data = client.get(_co_path(args, f"{args.co_id}/"))
    if args.format == "json":
        out_json(data)
        return
    print(f"Change order {data.get('id')} #{data.get('number')} [{data.get('status')}]")
    print(f"  Amount:      {data.get('amount')}")
    print(f"  Description: {str(data.get('description', ''))[:300]}")
    if data.get("approved_at"):
        print(f"  Approved:    {data['approved_at']} by user {data.get('approved_by')}")
    print()


def _co_payload(args):
    payload = {}
    if args.description is not None:
        payload["description"] = args.description
    if args.amount is not None:
        payload["amount"] = args.amount
    return _data(args, payload)


def cmd_change_orders_create(client, args):
    """Create a change order (contract must be active; the server numbers it)."""
    payload = _co_payload(args)
    if args.dry_run:
        print(f"Would CREATE change order on contract {args.contract}: {json.dumps(payload, indent=2)}")
        return
    data = client.post(_co_path(args), payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Created change order {data.get('id')} #{data.get('number')}: {data.get('amount')}")


def cmd_change_orders_update(client, args):
    """Update a proposed change order."""
    payload = _co_payload(args)
    _require_fields(payload)
    if args.dry_run:
        print(f"Would UPDATE change order {args.co_id}: {json.dumps(payload, indent=2)}")
        return
    data = client.patch(_co_path(args, f"{args.co_id}/"), payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Updated change order {data.get('id')}")


def cmd_change_orders_delete(client, args):
    """Soft-delete a change order (refused once approved)."""
    if args.dry_run:
        print(f"Would DELETE change order {args.co_id}")
        return
    client.delete(_co_path(args, f"{args.co_id}/"))
    print(f"Deleted change order {args.co_id}")


def cmd_change_orders_approve(client, args):
    """proposed -> approved; the server recomputes the contract's revised amount."""
    if args.dry_run:
        print(f"Would APPROVE change order {args.co_id} on contract {args.contract}")
        return
    data = client.post(_co_path(args, f"{args.co_id}/approve/"))
    if args.format == "json":
        out_json(data)
    else:
        print(f"Change order {args.co_id}: {data.get('status')}")


# ═══════════════════════════════════════════════════════════════════════════════
# PARSER
# ═══════════════════════════════════════════════════════════════════════════════

CONTRACT_TYPES = ["time_and_materials", "fixed_price", "unit_price", "gmp"]


def _add_contract(p):
    p.add_argument("--contract", type=int, required=True, help="Contract id")


def _add_page(p, limit=25):
    p.add_argument("--limit", type=int, default=limit)
    p.add_argument("--offset", type=int, default=0)


def _add_data(p):
    p.add_argument("--data", help="Extra fields as a JSON object, merged last")


def register_parser(sub):
    # ── contracts ──
    ct_p = sub.add_parser("contracts", help="Project contracts (invoicing)")
    ct_sub = ct_p.add_subparsers(dest="contracts_command")

    p = ct_sub.add_parser("list", help="List contracts")
    p.add_argument("--status", choices=["draft", "active", "completed", "cancelled"])
    p.add_argument("--type", choices=CONTRACT_TYPES)
    p.add_argument("--name", help="Name filter (substring)")
    _add_page(p)

    p = ct_sub.add_parser("get", help="Contract detail with SOV items")
    p.add_argument("contract_id", type=int)

    def _contract_fields(p, create):
        p.add_argument("--name", required=create)
        p.add_argument("--type", choices=CONTRACT_TYPES, required=create, help="Contract type")
        p.add_argument("--original-amount", dest="original_amount", type=_money, required=create,
                       help="Original contract sum, e.g. 125000.00")
        p.add_argument("--number", help="Contract number")
        p.add_argument("--retainage", type=_money, help="Retainage percent, e.g. 10.00")
        p.add_argument("--client-name", dest="client_name")
        p.add_argument("--client-contact", dest="client_contact")
        p.add_argument("--start", help="Start date YYYY-MM-DD")
        p.add_argument("--end", help="End date YYYY-MM-DD")
        _add_data(p)

    p = ct_sub.add_parser("create", help="Create contract (as draft)")
    _contract_fields(p, create=True)

    p = ct_sub.add_parser("update", help="Update contract")
    p.add_argument("contract_id", type=int)
    _contract_fields(p, create=False)

    p = ct_sub.add_parser("delete", help="Soft-delete contract")
    p.add_argument("contract_id", type=int)

    p = ct_sub.add_parser("restore", help="Restore a deleted contract (staff only)")
    p.add_argument("contract_id", type=int)

    # ── sov ──
    sov_p = sub.add_parser("sov", help="Schedule of values items on a contract")
    sov_sub = sov_p.add_subparsers(dest="sov_command")

    p = sov_sub.add_parser("list", help="List SOV items")
    _add_contract(p)
    _add_page(p, limit=100)

    def _sov_fields(p, create):
        p.add_argument("--item-number", dest="item_number", required=create, help="Item number, e.g. 001")
        p.add_argument("--description", required=create)
        p.add_argument("--value", type=_money, required=create, help="Scheduled value, e.g. 25000.00")
        p.add_argument("--activity", type=int, help="Activity id (same project)")
        p.add_argument("--cost-code", dest="cost_code", type=int, help="Cost code id (same company)")
        p.add_argument("--sort-order", dest="sort_order", type=int)
        _add_data(p)

    p = sov_sub.add_parser("create", help="Add SOV item")
    _add_contract(p)
    _sov_fields(p, create=True)

    p = sov_sub.add_parser("update", help="Update SOV item")
    p.add_argument("item_id", type=int)
    _add_contract(p)
    _sov_fields(p, create=False)

    p = sov_sub.add_parser("delete", help="Delete SOV item")
    p.add_argument("item_id", type=int)
    _add_contract(p)

    # ── pay-apps ──
    pa_p = sub.add_parser("pay-apps", help="Pay applications (AIA G702/G703) on a contract")
    pa_sub = pa_p.add_subparsers(dest="pay_apps_command")

    p = pa_sub.add_parser("list", help="List pay applications")
    _add_contract(p)
    p.add_argument("--status", choices=["draft", "submitted", "approved", "rejected", "paid"])
    _add_page(p)

    p = pa_sub.add_parser("get", help="Pay application detail with line items")
    p.add_argument("pay_app_id", type=int)
    _add_contract(p)

    p = pa_sub.add_parser("create", help="Create pay application (contract must be active)")
    _add_contract(p)
    p.add_argument("--period-start", dest="period_start", required=True, help="YYYY-MM-DD")
    p.add_argument("--period-end", dest="period_end", required=True, help="YYYY-MM-DD")

    p = pa_sub.add_parser("update", help="Update draft pay application notes")
    p.add_argument("pay_app_id", type=int)
    _add_contract(p)
    p.add_argument("--notes")
    _add_data(p)

    p = pa_sub.add_parser("delete", help="Soft-delete draft pay application")
    p.add_argument("pay_app_id", type=int)
    _add_contract(p)

    for name, help_text in (
        ("submit", "Submit (draft -> submitted)"),
        ("approve", "Approve (submitted -> approved)"),
        ("reject", "Reject (submitted -> rejected)"),
        ("reopen", "Reopen (rejected -> draft)"),
        ("mark-paid", "Mark as paid (approved -> paid)"),
    ):
        p = pa_sub.add_parser(name, help=help_text)
        p.add_argument("pay_app_id", type=int)
        _add_contract(p)
        if name == "reject":
            p.add_argument("--reason", help="Rejection reason")

    lines_p = pa_sub.add_parser("lines", help="Pay application line items (G703)")
    lines_sub = lines_p.add_subparsers(dest="pay_apps_lines_command")

    p = lines_sub.add_parser("list", help="List line items")
    p.add_argument("pay_app_id", type=int)
    _add_contract(p)
    _add_page(p, limit=100)

    p = lines_sub.add_parser("update", help="Update a line item (draft pay application only)")
    p.add_argument("pay_app_id", type=int)
    p.add_argument("line_id", type=int)
    _add_contract(p)
    p.add_argument("--current", type=_money, help="Work completed this period, e.g. 5000.00")
    p.add_argument("--stored", type=_money, help="Materials presently stored, e.g. 0.00")

    # ── change-orders ──
    co_p = sub.add_parser("change-orders", help="Change orders on a contract")
    co_sub = co_p.add_subparsers(dest="change_orders_command")

    p = co_sub.add_parser("list", help="List change orders")
    _add_contract(p)
    p.add_argument("--status", choices=["proposed", "approved", "rejected"])
    _add_page(p)

    p = co_sub.add_parser("get", help="Change order detail")
    p.add_argument("co_id", type=int)
    _add_contract(p)

    p = co_sub.add_parser("create", help="Create change order (contract must be active)")
    _add_contract(p)
    p.add_argument("--description", required=True)
    p.add_argument("--amount", type=_money, required=True,
                   help="Positive adds, negative deducts, e.g. -1500.00")
    _add_data(p)

    p = co_sub.add_parser("update", help="Update proposed change order")
    p.add_argument("co_id", type=int)
    _add_contract(p)
    p.add_argument("--description")
    p.add_argument("--amount", type=_money)
    _add_data(p)

    p = co_sub.add_parser("delete", help="Soft-delete change order (not once approved)")
    p.add_argument("co_id", type=int)
    _add_contract(p)

    p = co_sub.add_parser("approve", help="Approve change order (proposed -> approved)")
    p.add_argument("co_id", type=int)
    _add_contract(p)


SUB_HANDLERS = {
    "contracts": {
        "list": cmd_contracts_list,
        "get": cmd_contracts_get,
        "create": cmd_contracts_create,
        "update": cmd_contracts_update,
        "delete": cmd_contracts_delete,
        "restore": cmd_contracts_restore,
    },
    "sov": {
        "list": cmd_sov_list,
        "create": cmd_sov_create,
        "update": cmd_sov_update,
        "delete": cmd_sov_delete,
    },
    "pay-apps": {
        "list": cmd_pay_apps_list,
        "get": cmd_pay_apps_get,
        "create": cmd_pay_apps_create,
        "update": cmd_pay_apps_update,
        "delete": cmd_pay_apps_delete,
        "submit": cmd_pay_apps_submit,
        "approve": cmd_pay_apps_approve,
        "reject": cmd_pay_apps_reject,
        "reopen": cmd_pay_apps_reopen,
        "mark-paid": cmd_pay_apps_mark_paid,
        "lines": cmd_pay_apps_lines,
    },
    "change-orders": {
        "list": cmd_change_orders_list,
        "get": cmd_change_orders_get,
        "create": cmd_change_orders_create,
        "update": cmd_change_orders_update,
        "delete": cmd_change_orders_delete,
        "approve": cmd_change_orders_approve,
    },
}

SUB_COMMAND_KEYS = {
    "contracts": "contracts_command",
    "sov": "sov_command",
    "pay-apps": "pay_apps_command",
    "change-orders": "change_orders_command",
}

COMPANY_ONLY = set()
