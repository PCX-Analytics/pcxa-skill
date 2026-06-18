"""Custom object (FieldChoice) and option (FieldChoiceOption) commands.

"Custom Objects" is the PCXA product name for the backend ``FieldChoice`` model
(a typed object whose columns are defined by ``property_schema``) and its rows,
``FieldChoiceOption`` (``label`` + ``properties`` JSON). They are served at
``field-choices/`` (+ nested ``options/``) at both company and project scope.

Project scope is the default; pass ``--scope company`` for company-level objects.
A company-level object can be surfaced into the current project with
``extend`` (the backend ``extend_to_project`` action).
"""

import json
import sys

from pcxa._output import out_json, out_table
from pcxa._resolve import resolve_field_choice_option


def _scoped(args):
    """True if the request should be project-scoped (default), False for company."""
    return getattr(args, "scope", "project") != "company"


def _object_row(o):
    schema = o.get("property_schema") or {}
    props = schema.get("properties", schema) if isinstance(schema, dict) else {}
    return {
        "id": str(o.get("id", "")),
        "name": str(o.get("name", ""))[:35],
        "scope": "company" if o.get("project") in (None, "") else "project",
        "props": str(len(props) if isinstance(props, (dict, list)) else 0),
        "options": str(o.get("options_count", o.get("option_count", ""))),
        "extends": str(o.get("extends_field_choice") or ""),
        "ext": "yes" if o.get("is_extensible") else "",
    }


def cmd_custom_objects_list(client, args):
    """List custom objects (field-choices)."""
    params = client.paginate_params(args.limit, args.offset)
    if args.search:
        params["search"] = args.search
    if args.sort:
        params["ordering"] = args.sort

    data = client.get("field-choices/", params, project_scoped=_scoped(args))
    if args.format == "json":
        out_json(data)
        return
    results = data.get("results", data) if isinstance(data, dict) else data
    total = data.get("count", len(results)) if isinstance(data, dict) else len(results)
    rows = [_object_row(o) for o in results]
    print(f"Custom objects: {len(rows)} of {total}\n")
    out_table(rows, ["id", "name", "scope", "props", "options", "extends", "ext"])


def cmd_custom_objects_get(client, args):
    """Show a custom object's schema and option count."""
    data = client.get(f"field-choices/{args.object_id}/", project_scoped=_scoped(args))
    if args.format == "json":
        out_json(data)
        return

    print(f"Custom object {data.get('id')}: {data.get('name')}")
    print(f"  Description: {data.get('description') or '-'}")
    print(f"  Extensible:  {data.get('is_extensible', False)}")
    if data.get("extends_field_choice"):
        print(f"  Extends:     field-choice {data['extends_field_choice']}")
    schema = data.get("property_schema") or {}
    props = schema.get("properties", schema) if isinstance(schema, dict) else schema
    if props:
        print(f"\n  Property schema ({len(props)}):")
        if isinstance(props, dict):
            for name, spec in props.items():
                typ = spec.get("type", spec) if isinstance(spec, dict) else spec
                print(f"    - {name}: {typ}")
        else:
            print(f"    {json.dumps(props)[:300]}")

    try:
        opts = client.get(
            f"field-choices/{args.object_id}/options/",
            {"page_size": 10},
            project_scoped=_scoped(args),
        )
        results = opts.get("results", opts) if isinstance(opts, dict) else opts
        total = opts.get("count", len(results)) if isinstance(opts, dict) else len(results)
        if results:
            print(f"\n  Options ({total}):")
            for o in results:
                flag = "" if o.get("is_active", True) else " (inactive)"
                print(f"    [{o.get('id')}] {o.get('label')}{flag}")
            if total > len(results):
                print(f"    ... +{total - len(results)} more (pcxa custom-objects options list {args.object_id})")
    except Exception:
        pass
    print()


def cmd_custom_objects_create(client, args):
    """Create a custom object (field-choice)."""
    project_scoped = _scoped(args)
    payload = {"name": args.name, "company": client.company_id}
    if project_scoped:
        payload["project"] = client.project_id
    if args.description:
        payload["description"] = args.description
    if args.schema:
        payload["property_schema"] = json.loads(args.schema)
    if args.extensible is not None:
        payload["is_extensible"] = args.extensible

    if args.dry_run:
        print(f"Would CREATE custom object: {json.dumps(payload, indent=2)}")
        return
    data = client.post("field-choices/", payload, project_scoped=project_scoped)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Created custom object {data.get('id')}: '{data.get('name')}'")


def cmd_custom_objects_update(client, args):
    """Update a custom object (field-choice)."""
    payload = {}
    if args.name:
        payload["name"] = args.name
    if args.description is not None:
        payload["description"] = args.description or None
    if args.schema is not None:
        payload["property_schema"] = json.loads(args.schema) if args.schema else {}
    if args.extensible is not None:
        payload["is_extensible"] = args.extensible
    if not payload:
        print("No fields to update.", file=sys.stderr)
        sys.exit(1)
    if args.dry_run:
        print(f"Would UPDATE custom object {args.object_id}: {json.dumps(payload, indent=2)}")
        return
    data = client.patch(f"field-choices/{args.object_id}/", payload, project_scoped=_scoped(args))
    if args.format == "json":
        out_json(data)
    else:
        print(f"Updated custom object {data.get('id')}: '{data.get('name')}'")


def cmd_custom_objects_delete(client, args):
    """Delete a custom object (field-choice)."""
    if args.dry_run:
        print(f"Would DELETE custom object {args.object_id}")
        return
    client.delete(f"field-choices/{args.object_id}/", project_scoped=_scoped(args))
    print(f"Deleted custom object {args.object_id}")


def cmd_custom_objects_extend(client, args):
    """Surface a company-level custom object into the current project.

    Wraps the backend ``extend_to_project`` action on a company-scoped
    field-choice; the originating object always lives at company scope.
    """
    payload = {"project": client.project_id}
    if args.dry_run:
        print(f"Would EXTEND custom object {args.object_id} to project {client.project_id}")
        return
    data = client.post(
        f"field-choices/{args.object_id}/extend_to_project/",
        payload,
        project_scoped=False,
    )
    if args.format == "json":
        out_json(data)
    else:
        print(f"Extended custom object {args.object_id} to project {client.project_id} "
              f"(new field-choice {data.get('id')})" if data.get("id") else
              f"Extended custom object {args.object_id} to project {client.project_id}")


def cmd_custom_objects_resolve(client, args):
    """Fuzzy-resolve a value against a custom object's options.

    Prints the matched option, or a "No match for 'X'. Did you mean 'Y'?"
    suggestion. Exits non-zero when there is no confident match — the same
    contract the form-submission validator relies on.
    """
    scope = getattr(args, "scope", "project")
    option, msg = resolve_field_choice_option(client, args.object_id, args.query, scope=scope)
    if args.format == "json":
        out_json({"matched": option is not None, "message": msg, "option": option})
    else:
        print(msg)
    if option is None:
        sys.exit(1)


# ═══════════════════════════════════════════════════════════════════════════════
# OPTIONS (FieldChoiceOption) — rows of a custom object
# ═══════════════════════════════════════════════════════════════════════════════


def _options_path(args, suffix=""):
    return f"field-choices/{args.object_id}/options/{suffix}"


def cmd_co_options_list(client, args):
    """List options (rows) of a custom object."""
    params = {}
    if getattr(args, "search", None):
        params["search"] = args.search
    if getattr(args, "active", None) is not None:
        params["is_active"] = str(args.active).lower()
    data = client.get(_options_path(args), params, project_scoped=_scoped(args))
    if args.format == "json":
        out_json(data)
        return
    results = data.get("results", data) if isinstance(data, dict) else data
    rows = []
    for o in results:
        rows.append({
            "id": str(o.get("id", "")),
            "order": str(o.get("order", "")),
            "label": str(o.get("label", ""))[:40],
            "active": "yes" if o.get("is_active", True) else "",
            "properties": json.dumps(o.get("properties") or {}, default=str)[:40],
        })
    print(f"Options for custom object {args.object_id}: {len(rows)}\n")
    out_table(rows, ["id", "order", "label", "active", "properties"])


def cmd_co_options_get(client, args):
    """Show one option (row) with its property values."""
    data = client.get(_options_path(args, f"{args.option_id}/"), project_scoped=_scoped(args))
    if args.format == "json":
        out_json(data)
        return
    print(f"Option {data.get('id')}: {data.get('label')}")
    print(f"  Object:   field-choice {args.object_id}")
    print(f"  Order:    {data.get('order', '-')}")
    print(f"  Active:   {data.get('is_active', True)}")
    if data.get("description"):
        print(f"  Desc:     {data['description'][:200]}")
    props = data.get("properties") or {}
    if props:
        print(f"\n  Properties ({len(props)}):")
        for k, v in props.items():
            print(f"    {k}: {v}")
    print()


def _option_payload(args):
    payload = {}
    if getattr(args, "label", None) is not None:
        payload["label"] = args.label
    if getattr(args, "description", None) is not None:
        payload["description"] = args.description
    if getattr(args, "order", None) is not None:
        payload["order"] = args.order
    if getattr(args, "properties", None) is not None:
        payload["properties"] = json.loads(args.properties) if args.properties else {}
    if getattr(args, "active", None) is not None:
        payload["is_active"] = args.active
    return payload


def cmd_co_options_create(client, args):
    """Create an option (row) on a custom object."""
    payload = _option_payload(args)
    payload.setdefault("label", args.label)
    if args.dry_run:
        print(f"Would CREATE option on object {args.object_id}: {json.dumps(payload, indent=2)}")
        return
    data = client.post(_options_path(args), payload, project_scoped=_scoped(args))
    if args.format == "json":
        out_json(data)
    else:
        print(f"Created option {data.get('id')}: '{data.get('label')}'")


def cmd_co_options_update(client, args):
    """Update an option (row)."""
    payload = _option_payload(args)
    if not payload:
        print("No fields to update.", file=sys.stderr)
        sys.exit(1)
    if args.dry_run:
        print(f"Would UPDATE option {args.option_id}: {json.dumps(payload, indent=2)}")
        return
    data = client.patch(_options_path(args, f"{args.option_id}/"), payload, project_scoped=_scoped(args))
    if args.format == "json":
        out_json(data)
    else:
        print(f"Updated option {data.get('id')}: '{data.get('label')}'")


def cmd_co_options_delete(client, args):
    """Delete an option (row)."""
    if args.dry_run:
        print(f"Would DELETE option {args.option_id} from object {args.object_id}")
        return
    client.delete(_options_path(args, f"{args.option_id}/"), project_scoped=_scoped(args))
    print(f"Deleted option {args.option_id}")


def cmd_co_options_bulk_create(client, args):
    """Bulk-create options from a JSON file (list of option objects).

    Wraps the backend ``options/bulk_create/`` action. The file may be a bare
    list or an object with an ``options`` key. Each entry needs at least
    ``label``; ``properties``/``order``/``description`` are optional.
    """
    from pathlib import Path
    path = Path(args.file)
    if not path.exists():
        print(f"File not found: {path}", file=sys.stderr)
        sys.exit(1)
    raw = json.loads(path.read_text())
    options = raw.get("options") if isinstance(raw, dict) else raw
    if not isinstance(options, list):
        print("JSON must be a list of option objects or an object with an 'options' key.", file=sys.stderr)
        sys.exit(1)
    bad = [i for i, o in enumerate(options) if not isinstance(o, dict) or not o.get("label")]
    if bad:
        print(f"Entries missing 'label': indices {bad}", file=sys.stderr)
        sys.exit(1)

    if args.dry_run:
        print(f"Would BULK-CREATE {len(options)} options on object {args.object_id}")
        return
    data = client.post(_options_path(args, "bulk_create/"), {"options": options}, project_scoped=_scoped(args))
    if args.format == "json":
        out_json(data)
    else:
        created = data.get("success_count", data.get("created", len(options))) if isinstance(data, dict) else len(options)
        print(f"Bulk-created {created} options on object {args.object_id}")


def cmd_co_options_reorder(client, args):
    """Reorder options by passing option ids in the desired order (comma-sep).

    Wraps the backend ``options/reorder/`` action.
    """
    ids = [int(x) for x in args.order.split(",") if x.strip()]
    if args.dry_run:
        print(f"Would REORDER options on object {args.object_id}: {ids}")
        return
    data = client.post(_options_path(args, "reorder/"), {"option_ids": ids}, project_scoped=_scoped(args))
    if args.format == "json":
        out_json(data)
    else:
        print(f"Reordered {len(ids)} options on object {args.object_id}")


_OPTION_HANDLERS = {
    "list": cmd_co_options_list,
    "get": cmd_co_options_get,
    "create": cmd_co_options_create,
    "update": cmd_co_options_update,
    "delete": cmd_co_options_delete,
    "bulk-create": cmd_co_options_bulk_create,
    "reorder": cmd_co_options_reorder,
}


def cmd_custom_objects_options(client, args):
    """Dispatch ``custom-objects options <subcommand>`` (nested third level)."""
    sub = getattr(args, "co_options_command", None)
    fn = _OPTION_HANDLERS.get(sub)
    if fn is None:
        avail = ", ".join(_OPTION_HANDLERS)
        print(f"Usage: pcxa custom-objects options {{{avail}}} <object_id>", file=sys.stderr)
        sys.exit(1)
    fn(client, args)


__all__ = [
    "cmd_custom_objects_list",
    "cmd_custom_objects_get",
    "cmd_custom_objects_create",
    "cmd_custom_objects_update",
    "cmd_custom_objects_delete",
    "cmd_custom_objects_extend",
    "cmd_custom_objects_resolve",
    "cmd_custom_objects_options",
]
