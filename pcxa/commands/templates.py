"""Activity templates and custom-field definition commands.

What the web UI calls Settings > Templates > Activity Types (company) and
Project Settings > Activities > Templates (project):

- ``activity-types``        the type itself (``activity-types/``), company or project scope.
- ``type-steps``            its template steps (``activity-types/{type}/steps/``).
- ``type-fields``           its custom fields (``activity-types/{type}/fields/``). Each one
                            is a ``FieldDefinition`` linked to the type; its ``id`` is the
                            FieldDefinition id, which is also the key of an activity's
                            ``custom_field_data``.
- ``type-extensions``       a project's extension of a company type (project-only fields).
- ``type-extension-fields`` the fields an extension adds.
- ``field-definitions``     the raw FieldDefinition pool shared by forms and activity types.
- ``field-sections``        the headings fields are grouped under.
- ``field-rules``           validation rules on one field definition.

``resolve_field_labels`` turns ``Label=value`` pairs into a ``custom_field_data``
dict for ``activities create|update --field``.
"""

import json
import sys

from pcxa._output import out_json, out_table

RULE_TYPES = [
    "min_length", "max_length", "pattern", "min_value", "max_value", "decimal_places",
    "min_date", "max_date", "date_relative", "min_selections", "max_selections", "custom",
]
FIELD_TYPES = [
    "text", "textarea", "number", "date", "datetime", "checkbox", "select", "radio",
    "choice", "table", "photo", "file", "location",
]


def _bool(x):
    return x.lower() in ("true", "1", "yes")


def _scoped(args):
    """True if the request should be project-scoped (default), False for company."""
    return getattr(args, "scope", "project") != "company"


def _json_arg(raw, flag):
    """Parse a JSON flag value, or exit 1 with a clean message."""
    try:
        return json.loads(raw)
    except ValueError as e:
        print(f"Invalid JSON for {flag}: {e}", file=sys.stderr)
        sys.exit(1)


def _merge_data(payload, args):
    """Merge ``--data`` (a JSON object) over the typed flags."""
    raw = getattr(args, "data", None)
    if raw:
        extra = _json_arg(raw, "--data")
        if not isinstance(extra, dict):
            print("--data must be a JSON object.", file=sys.stderr)
            sys.exit(1)
        payload.update(extra)
    return payload


def _dropdown(raw):
    """``"A,B"`` -> ``[{"value": "A", "label": "A"}, ...]`` (the web UI's options.dropdown)."""
    return [{"value": v.strip(), "label": v.strip()} for v in raw.split(",") if v.strip()]


def _results(data):
    return data.get("results", data) if isinstance(data, dict) else data


def _require(payload, what):
    if not payload:
        print(f"No {what} to update.", file=sys.stderr)
        sys.exit(1)


def _done(args, data, verb, noun):
    if args.format == "json":
        out_json(data)
    else:
        label = data.get("name") or data.get("label") or data.get("base_type_name") or ""
        print(f"{verb} {noun} {data.get('id')}" + (f": '{label}'" if label else ""))


def _show(data, title):
    print(title)
    for k, v in data.items():
        if isinstance(v, (dict, list)):
            v = json.dumps(v, default=str)[:200]
        print(f"  {k}: {v}")
    print()


# ═══════════════════════════════════════════════════════════════════════════════
# ACTIVITY TYPES
# ═══════════════════════════════════════════════════════════════════════════════


def cmd_activity_types_list(client, args):
    """List activity types (project scope also returns company-wide types)."""
    params = client.paginate_params(args.limit, args.offset)
    if args.search:
        params["search"] = args.search
    if args.category:
        params["category"] = args.category
    data = client.get("activity-types/", params, project_scoped=_scoped(args))
    if args.format == "json":
        out_json(data)
        return
    rows = [{
        "id": str(t.get("id", "")),
        "name": str(t.get("name", ""))[:35],
        "scope": "company" if t.get("project") in (None, "") else "project",
        "category": str(t.get("category") or "")[:20],
        "steps": str(len(t.get("template_steps") or [])),
        "activities": str(t.get("activities_count", "")),
        "default": "yes" if t.get("is_default") else "",
        "ext": str(t.get("extension_id") or ""),
    } for t in _results(data)]
    total = data.get("count", len(rows)) if isinstance(data, dict) else len(rows)
    print(f"Activity types: {len(rows)} of {total}\n")
    out_table(rows, ["id", "name", "scope", "category", "steps", "activities", "default", "ext"])


def cmd_activity_types_get(client, args):
    """Activity type detail with its template steps."""
    data = client.get(f"activity-types/{args.type_id}/", project_scoped=_scoped(args))
    if args.format == "json":
        out_json(data)
        return
    print(f"Activity type {data.get('id')}: {data.get('name')}")
    print(f"  Scope:      {'company' if data.get('project') in (None, '') else 'project'}")
    print(f"  Category:   {data.get('category') or '-'}")
    print(f"  Default:    {data.get('is_default', False)}")
    print(f"  Activities: {data.get('activities_count', '-')}")
    if data.get("extension_id"):
        print(f"  Extension:  {data['extension_id']} (pcxa type-extension-fields list {data['extension_id']})")
    if data.get("description"):
        print(f"  Desc:       {data['description'][:200]}")
    steps = data.get("template_steps") or []
    if steps:
        print(f"\n  Steps ({len(steps)}):")
        for s in sorted(steps, key=lambda s: s.get("order", 0)):
            print(f"    [{s.get('id')}] {s.get('order')}. {s.get('name')} ({s.get('progress_weight')}%)")
    print()


def _type_payload(args):
    payload = {}
    if args.name is not None:
        payload["name"] = args.name
    if args.description is not None:
        payload["description"] = args.description
    if args.category is not None:
        payload["category"] = args.category or None
    if args.default is not None:
        payload["is_default"] = args.default
    return _merge_data(payload, args)


def cmd_activity_types_create(client, args):
    """Create an activity type; company/project come from the URL scope."""
    payload = _type_payload(args)
    if args.dry_run:
        print(f"Would CREATE activity type: {json.dumps(payload, indent=2)}")
        return
    data = client.post("activity-types/", payload, project_scoped=_scoped(args))
    _done(args, data, "Created", "activity type")


def cmd_activity_types_update(client, args):
    """Update an activity type (``--default true`` is company-scope only)."""
    payload = _type_payload(args)
    _require(payload, "fields")
    if args.dry_run:
        print(f"Would UPDATE activity type {args.type_id}: {json.dumps(payload, indent=2)}")
        return
    data = client.patch(f"activity-types/{args.type_id}/", payload, project_scoped=_scoped(args))
    _done(args, data, "Updated", "activity type")


def cmd_activity_types_delete(client, args):
    """Soft-delete an activity type."""
    if args.dry_run:
        print(f"Would DELETE activity type {args.type_id}")
        return
    client.delete(f"activity-types/{args.type_id}/", project_scoped=_scoped(args))
    print(f"Deleted activity type {args.type_id}")


def cmd_activity_types_categories(client, args):
    """Distinct categories in scope."""
    data = client.get("activity-types/categories/", project_scoped=_scoped(args))
    if args.format == "json":
        out_json(data)
    else:
        for c in data.get("results", []):
            print(c)


def cmd_activity_types_schema(client, args):
    """Effective schema: sections + fields, merged with the project's extension."""
    data = client.get(f"activity-types/{args.type_id}/schema/", project_scoped=_scoped(args))
    if args.format == "json":
        out_json(data)
        return
    rows = [{
        "id": str(f.get("id", "")),
        "label": str(f.get("label", ""))[:35],
        "type": str(f.get("field_type", "")),
        "req": "yes" if f.get("is_required") else "",
        "section": str(f.get("section_id") or ""),
        "source": str(f.get("source", "")),
    } for f in data.get("fields", [])]
    print(f"Schema of activity type {args.type_id}: {len(rows)} fields, "
          f"{len(data.get('sections', []))} sections\n")
    out_table(rows, ["id", "label", "type", "req", "section", "source"])


def cmd_activity_types_default(client, args):
    """The company's default activity type."""
    data = client.get("activity-types/get_default_for_company/",
                      {"company": client.company_id}, project_scoped=False)
    if args.format == "json":
        out_json(data)
    elif data.get("id"):
        print(f"Default activity type {data['id']}: {data.get('name')}")
    else:
        print("No default activity type set.")


# ═══════════════════════════════════════════════════════════════════════════════
# TYPE STEPS — template steps of an activity type
# ═══════════════════════════════════════════════════════════════════════════════


def _steps_path(args, suffix=""):
    return f"activity-types/{args.type_id}/steps/{suffix}"


def cmd_type_steps_list(client, args):
    """List an activity type's template steps."""
    data = client.get_all_pages(_steps_path(args), project_scoped=_scoped(args))
    if args.format == "json":
        out_json(data)
        return
    rows = [{
        "id": str(s.get("id", "")),
        "order": str(s.get("order", "")),
        "name": str(s.get("name", ""))[:40],
        "weight": str(s.get("progress_weight", "")),
        "form": str(s.get("required_form_template") or ""),
    } for s in data]
    print(f"Steps of activity type {args.type_id}: {len(rows)}\n")
    out_table(rows, ["id", "order", "name", "weight", "form"])


def _step_payload(args):
    payload = {}
    if args.name is not None:
        payload["name"] = args.name
    if args.description is not None:
        payload["description"] = args.description
    if args.weight is not None:
        payload["progress_weight"] = args.weight
    if args.order is not None:
        payload["order"] = args.order
    if args.form is not None:
        payload["required_form_template"] = args.form or None
    return _merge_data(payload, args)


def cmd_type_steps_create(client, args):
    """Create a template step. Weights rebalance to 100% server-side."""
    payload = dict(_step_payload(args), activity_type=args.type_id)
    if args.dry_run:
        print(f"Would CREATE step on activity type {args.type_id}: {json.dumps(payload, indent=2)}")
        return
    data = client.post(_steps_path(args), payload, project_scoped=_scoped(args))
    _done(args, data, "Created", "step")


def cmd_type_steps_update(client, args):
    """Update a template step."""
    payload = _step_payload(args)
    _require(payload, "fields")
    if args.dry_run:
        print(f"Would UPDATE step {args.step_id}: {json.dumps(payload, indent=2)}")
        return
    data = client.patch(_steps_path(args, f"{args.step_id}/"), payload, project_scoped=_scoped(args))
    _done(args, data, "Updated", "step")


def cmd_type_steps_delete(client, args):
    """Delete a template step (remaining weights redistribute equally)."""
    if args.dry_run:
        print(f"Would DELETE step {args.step_id} from activity type {args.type_id}")
        return
    client.delete(_steps_path(args, f"{args.step_id}/"), project_scoped=_scoped(args))
    print(f"Deleted step {args.step_id}")


def cmd_type_steps_reorder(client, args):
    """Reorder steps: step ids in the desired order (1-based, as the web UI sends)."""
    ids = [int(x) for x in args.order.split(",") if x.strip()]
    payload = {"step_orders": [{"id": sid, "order": i + 1} for i, sid in enumerate(ids)]}
    if args.dry_run:
        print(f"Would REORDER steps of activity type {args.type_id}: {json.dumps(payload)}")
        return
    data = client.post(_steps_path(args, "reorder/"), payload, project_scoped=_scoped(args))
    if args.format == "json":
        out_json(data)
    else:
        print(f"Reordered {len(ids)} steps of activity type {args.type_id}")


def cmd_type_steps_weights(client, args):
    """Set step weights (``STEP=WEIGHT,...``); the server normalizes them to 100%."""
    weights = []
    for item in args.set.split(","):
        sid, sep, w = item.partition("=")
        try:
            weights.append({"step_id": int(sid), "weight": float(w)})
        except ValueError:
            print(f"Bad --set entry {item!r}: expected STEP_ID=WEIGHT", file=sys.stderr)
            sys.exit(1)
    payload = {"step_weights": weights}
    if args.dry_run:
        print(f"Would SET step weights on activity type {args.type_id}: {json.dumps(payload)}")
        return
    data = client.post(f"activity-types/{args.type_id}/adjust_template_weights/", payload,
                       project_scoped=_scoped(args))
    if args.format == "json":
        out_json(data)
    else:
        print(f"Weights set; total {data.get('total_weight')}")


# ═══════════════════════════════════════════════════════════════════════════════
# TYPE FIELDS — custom fields of an activity type (or of a project extension)
# ═══════════════════════════════════════════════════════════════════════════════


def _is_extension(args):
    return args.command == "type-extension-fields"


def _fields_path(args, suffix=""):
    if _is_extension(args):
        return f"activity-type-extensions/{args.parent_id}/fields/{suffix}"
    return f"activity-types/{args.parent_id}/fields/{suffix}"


def _fields_scoped(args):
    return True if _is_extension(args) else _scoped(args)


def _field_row(f):
    return {
        "id": str(f.get("id", "")),
        "order": str(f.get("order", "")),
        "label": str(f.get("label", f.get("field_label", "")))[:35],
        "type": str(f.get("field_type", f.get("field_field_type", ""))),
        "req": "yes" if f.get("is_required", f.get("field_is_required")) else "",
        "choice": str(f.get("choice_id", f.get("field_choice_id")) or ""),
        "options": ",".join(str(o.get("value")) for o in f.get("dropdown_options") or [])[:30],
    }


def cmd_type_fields_list(client, args):
    """List custom fields; ``id`` is the FieldDefinition id used in custom_field_data."""
    data = client.get_all_pages(_fields_path(args), project_scoped=_fields_scoped(args))
    if args.format == "json":
        out_json(data)
        return
    rows = [_field_row(f) for f in data]
    owner = "extension" if _is_extension(args) else "activity type"
    print(f"Fields of {owner} {args.parent_id}: {len(rows)}\n")
    out_table(rows, ["id", "order", "label", "type", "req", "choice", "options"])


def cmd_type_fields_get(client, args):
    """One custom field."""
    data = client.get(_fields_path(args, f"{args.field_id}/"), project_scoped=_fields_scoped(args))
    if args.format == "json":
        out_json(data)
    else:
        _show(data, f"Field {data.get('id')}: {data.get('label', data.get('field_label'))}")


def _field_payload(args, section_key, choice_key="choice_id"):
    payload = {}
    if args.label is not None:
        payload["label"] = args.label
    if args.type is not None:
        payload["field_type"] = args.type
    if args.required is not None:
        payload["is_required"] = args.required
    if args.options is not None:
        payload["options"] = {"dropdown": _dropdown(args.options)}
    if args.choice_id is not None:
        payload[choice_key] = args.choice_id or None
    if args.order is not None:
        payload["order"] = args.order
    if getattr(args, "help_text", None) is not None:
        payload["help_text"] = args.help_text
    if args.section_id is not None:
        payload[section_key] = args.section_id or None
    return _merge_data(payload, args)


def _check_field_type(payload, choice_key):
    """The web UI's rules: select/radio need options or a list; choice needs a list."""
    ftype = payload.get("field_type")
    has_options = bool((payload.get("options") or {}).get("dropdown"))
    if ftype in ("select", "radio") and not has_options and not payload.get(choice_key):
        print(f"A {ftype} field needs --options A,B,... (or --choice-id).", file=sys.stderr)
        sys.exit(1)
    if ftype == "choice" and not payload.get(choice_key):
        print("A choice field needs --choice-id (a custom object id).", file=sys.stderr)
        sys.exit(1)


def _section_key(args):
    return "target_section_id" if _is_extension(args) else "section_id"


def cmd_type_fields_create(client, args):
    """Create a custom field (a FieldDefinition linked to the type or extension)."""
    payload = _field_payload(args, _section_key(args))
    _check_field_type(payload, "choice_id")
    if args.dry_run:
        print(f"Would CREATE field on {_fields_path(args)}: {json.dumps(payload, indent=2)}")
        return
    data = client.post(_fields_path(args), payload, project_scoped=_fields_scoped(args))
    _done(args, data, "Created", "field")


def cmd_type_fields_update(client, args):
    """Update a custom field. ``--options`` keeps the field's other option keys."""
    payload = _field_payload(args, _section_key(args))
    _require(payload, "fields")
    path = _fields_path(args, f"{args.field_id}/")
    if args.options is not None:
        # options replaces the whole dict server-side; the web UI merges into the current one.
        current = client.get(path, project_scoped=_fields_scoped(args)).get("options") or {}
        payload["options"] = dict(current, **payload["options"])
    if args.dry_run:
        print(f"Would UPDATE field {args.field_id}: {json.dumps(payload, indent=2)}")
        return
    data = client.patch(path, payload, project_scoped=_fields_scoped(args))
    _done(args, data, "Updated", "field")


def cmd_type_fields_delete(client, args):
    """Unlink a custom field; the FieldDefinition is deleted too when nothing else uses it."""
    if args.dry_run:
        print(f"Would DELETE field {args.field_id} from {_fields_path(args)}")
        return
    client.delete(_fields_path(args, f"{args.field_id}/"), project_scoped=_fields_scoped(args))
    print(f"Deleted field {args.field_id}")


def cmd_type_fields_reorder(client, args):
    """Reorder fields: field ids in the desired order."""
    ids = [int(x) for x in args.order.split(",") if x.strip()]
    if args.dry_run:
        print(f"Would REORDER fields on {_fields_path(args)}: {ids}")
        return
    data = client.post(_fields_path(args, "reorder/"), {"field_ids": ids},
                       project_scoped=_fields_scoped(args))
    if args.format == "json":
        out_json(data)
    else:
        print(f"Reordered {len(ids)} fields")


# ═══════════════════════════════════════════════════════════════════════════════
# TYPE EXTENSIONS — a project's extension of a company activity type
# ═══════════════════════════════════════════════════════════════════════════════


def cmd_type_extensions_list(client, args):
    """List the project's activity-type extensions."""
    data = client.get_all_pages("activity-type-extensions/")
    if args.format == "json":
        out_json(data)
        return
    rows = [{
        "id": str(e.get("id", "")),
        "base_type": str(e.get("base_type", "")),
        "name": str(e.get("base_type_name", ""))[:35],
        "fields": str(len(e.get("field_configs") or [])),
    } for e in data]
    print(f"Activity-type extensions: {len(rows)}\n")
    out_table(rows, ["id", "base_type", "name", "fields"])


def cmd_type_extensions_get(client, args):
    """One extension with its effective (company + project) fields."""
    data = client.get(f"activity-type-extensions/{args.extension_id}/")
    if args.format == "json":
        out_json(data)
        return
    print(f"Extension {data.get('id')} of activity type {data.get('base_type')}: "
          f"{data.get('base_type_name')}\n")
    out_table([_field_row(f) for f in data.get("effective_fields") or []],
              ["id", "order", "label", "type", "req", "choice"])


def cmd_type_extensions_create(client, args):
    """Extend a company activity type into the current project."""
    payload = {"project": client.project_id, "base_type": args.base_type}
    if args.dry_run:
        print(f"Would CREATE activity-type extension: {json.dumps(payload)}")
        return
    data = client.post("activity-type-extensions/", payload)
    _done(args, data, "Created", "extension")


def cmd_type_extensions_delete(client, args):
    """Delete an extension (and its project-only field links)."""
    if args.dry_run:
        print(f"Would DELETE activity-type extension {args.extension_id}")
        return
    client.delete(f"activity-type-extensions/{args.extension_id}/")
    print(f"Deleted extension {args.extension_id}")


# ═══════════════════════════════════════════════════════════════════════════════
# FIELD DEFINITIONS / SECTIONS / RULES
# ═══════════════════════════════════════════════════════════════════════════════


def cmd_field_definitions_list(client, args):
    """List field definitions (project scope also returns company ones)."""
    params = client.paginate_params(args.limit, args.offset)
    for key, val in (("field_type", args.type), ("section", args.section),
                     ("choice_set", args.choice_set)):
        if val is not None:
            params[key] = val
    if args.lean:
        params["lean"] = "true"
    data = client.get("field-definitions/", params, project_scoped=_scoped(args))
    if args.format == "json":
        out_json(data)
        return
    rows = [{
        "id": str(f.get("id", "")),
        "label": str(f.get("label", ""))[:35],
        "type": str(f.get("field_type", "")),
        "req": "yes" if f.get("is_required") else "",
        "section": str((f.get("section") or {}).get("name") or "")[:20],
        "choice": str((f.get("choice_set") or {}).get("id") or ""),
    } for f in _results(data)]
    total = data.get("count", len(rows)) if isinstance(data, dict) else len(rows)
    print(f"Field definitions: {len(rows)} of {total}\n")
    out_table(rows, ["id", "label", "type", "req", "section", "choice"])


def cmd_field_definitions_get(client, args):
    data = client.get(f"field-definitions/{args.field_id}/", project_scoped=_scoped(args))
    if args.format == "json":
        out_json(data)
    else:
        _show(data, f"Field definition {data.get('id')}: {data.get('label')}")


def _definition_payload(args):
    return _field_payload(args, "section_id", choice_key="choice_set_id")


def cmd_field_definitions_create(client, args):
    """Create a bare field definition (not linked to any type or form)."""
    payload = _definition_payload(args)
    _check_field_type(payload, "choice_set_id")
    if args.dry_run:
        print(f"Would CREATE field definition: {json.dumps(payload, indent=2)}")
        return
    data = client.post("field-definitions/", payload, project_scoped=_scoped(args))
    _done(args, data, "Created", "field definition")


def cmd_field_definitions_update(client, args):
    payload = _definition_payload(args)
    _require(payload, "fields")
    if args.dry_run:
        print(f"Would UPDATE field definition {args.field_id}: {json.dumps(payload, indent=2)}")
        return
    data = client.patch(f"field-definitions/{args.field_id}/", payload, project_scoped=_scoped(args))
    _done(args, data, "Updated", "field definition")


def cmd_field_definitions_delete(client, args):
    if args.dry_run:
        print(f"Would DELETE field definition {args.field_id}")
        return
    client.delete(f"field-definitions/{args.field_id}/", project_scoped=_scoped(args))
    print(f"Deleted field definition {args.field_id}")


def cmd_field_sections_list(client, args):
    params = client.paginate_params(args.limit, args.offset)
    data = client.get("field-sections/", params, project_scoped=_scoped(args))
    if args.format == "json":
        out_json(data)
        return
    rows = [{
        "id": str(s.get("id", "")),
        "order": str(s.get("order", "")),
        "name": str(s.get("name", ""))[:40],
        "forms": str(s.get("form_count", "")),
    } for s in _results(data)]
    print(f"Field sections: {len(rows)}\n")
    out_table(rows, ["id", "order", "name", "forms"])


def cmd_field_sections_get(client, args):
    data = client.get(f"field-sections/{args.section_id}/", project_scoped=_scoped(args))
    if args.format == "json":
        out_json(data)
    else:
        _show(data, f"Field section {data.get('id')}: {data.get('name')}")


def _section_payload(args):
    payload = {}
    if args.name is not None:
        payload["name"] = args.name
    if args.description is not None:
        payload["description"] = args.description
    if args.order is not None:
        payload["order"] = args.order
    return _merge_data(payload, args)


def cmd_field_sections_create(client, args):
    payload = _section_payload(args)
    if args.dry_run:
        print(f"Would CREATE field section: {json.dumps(payload, indent=2)}")
        return
    data = client.post("field-sections/", payload, project_scoped=_scoped(args))
    _done(args, data, "Created", "field section")


def cmd_field_sections_update(client, args):
    payload = _section_payload(args)
    _require(payload, "fields")
    if args.dry_run:
        print(f"Would UPDATE field section {args.section_id}: {json.dumps(payload, indent=2)}")
        return
    data = client.patch(f"field-sections/{args.section_id}/", payload, project_scoped=_scoped(args))
    _done(args, data, "Updated", "field section")


def cmd_field_sections_delete(client, args):
    if args.dry_run:
        print(f"Would DELETE field section {args.section_id}")
        return
    client.delete(f"field-sections/{args.section_id}/", project_scoped=_scoped(args))
    print(f"Deleted field section {args.section_id}")


def _rules_path(args, suffix=""):
    return f"field-definitions/{args.field_id}/validation-rules/{suffix}"


def cmd_field_rules_list(client, args):
    data = client.get_all_pages(_rules_path(args), project_scoped=_scoped(args))
    if args.format == "json":
        out_json(data)
        return
    rows = [{
        "id": str(r.get("id", "")),
        "order": str(r.get("order", "")),
        "rule": str(r.get("rule_type", "")),
        "value": json.dumps(r.get("value"), default=str)[:30],
        "active": "yes" if r.get("is_active", True) else "",
        "message": str(r.get("error_message") or "")[:30],
    } for r in data]
    print(f"Validation rules of field {args.field_id}: {len(rows)}\n")
    out_table(rows, ["id", "order", "rule", "value", "active", "message"])


def cmd_field_rules_get(client, args):
    data = client.get(_rules_path(args, f"{args.rule_id}/"), project_scoped=_scoped(args))
    if args.format == "json":
        out_json(data)
    else:
        _show(data, f"Rule {data.get('id')}: {data.get('rule_type')}")


def _rule_payload(args):
    payload = {}
    if args.rule_type is not None:
        payload["rule_type"] = args.rule_type
    if args.value is not None:
        payload["value"] = _json_arg(args.value, "--value")
    if args.message is not None:
        payload["error_message"] = args.message
    if args.active is not None:
        payload["is_active"] = args.active
    if args.order is not None:
        payload["order"] = args.order
    return _merge_data(payload, args)


def cmd_field_rules_create(client, args):
    payload = _rule_payload(args)
    if args.dry_run:
        print(f"Would CREATE rule on field {args.field_id}: {json.dumps(payload, indent=2)}")
        return
    data = client.post(_rules_path(args), payload, project_scoped=_scoped(args))
    _done(args, data, "Created", "rule")


def cmd_field_rules_update(client, args):
    payload = _rule_payload(args)
    _require(payload, "fields")
    if args.dry_run:
        print(f"Would UPDATE rule {args.rule_id}: {json.dumps(payload, indent=2)}")
        return
    data = client.patch(_rules_path(args, f"{args.rule_id}/"), payload, project_scoped=_scoped(args))
    _done(args, data, "Updated", "rule")


def cmd_field_rules_delete(client, args):
    if args.dry_run:
        print(f"Would DELETE rule {args.rule_id} from field {args.field_id}")
        return
    client.delete(_rules_path(args, f"{args.rule_id}/"), project_scoped=_scoped(args))
    print(f"Deleted rule {args.rule_id}")


# ═══════════════════════════════════════════════════════════════════════════════
# LABEL RESOLUTION — `activities create|update --field "Label=value"`
# ═══════════════════════════════════════════════════════════════════════════════

_TRUE = ("true", "yes", "y", "1", "on")
_FALSE = ("false", "no", "n", "0", "off")


def _fail(msg):
    print(msg, file=sys.stderr)
    sys.exit(1)


def _coerce(field, raw):
    """The value as the field stores it: a number, a bool, a select's option value."""
    label, ftype = field.get("label"), field.get("field_type")
    if ftype == "number":
        try:
            number = float(raw)
        except ValueError:
            _fail(f"Field '{label}' is a number, not {raw!r}.")
        return int(number) if number.is_integer() else number
    if ftype == "checkbox":
        if raw.lower() in _TRUE:
            return True
        if raw.lower() in _FALSE:
            return False
        _fail(f"Field '{label}' is a checkbox: use true or false, not {raw!r}.")
    if ftype in ("select", "radio"):
        options = field.get("dropdown_options") or (field.get("options") or {}).get("dropdown") or []
        if options:
            for o in options:
                if raw == str(o.get("value")):
                    return o.get("value")
            for o in options:
                if raw.lower() in (str(o.get("value")).lower(), str(o.get("label")).lower()):
                    return o.get("value")
            choices = ", ".join(str(o.get("value")) for o in options)
            _fail(f"Field '{label}' is one of: {choices} (not {raw!r}).")
    return raw


def resolve_field_labels(client, activity_type_id, pairs):
    """pairs: list of 'Label=value' strings -> {field_id_str: value}; 'Label=' -> None (clear).

    Reads the type's fields (``activity-types/{id}/fields/``, project route, as the
    fleet's plan_fields tool does) and matches labels case-insensitively. A miss
    or an ambiguous label exits 1 listing the type's labels. Number, checkbox and
    select/radio values are coerced to what the field stores.
    """
    fields = client.get_all_pages(f"activity-types/{activity_type_id}/fields/")
    by_label = {}
    for f in fields:
        by_label.setdefault(str(f.get("label", "")).strip().lower(), []).append(f)
    available = ", ".join(sorted(str(f.get("label")) for f in fields)) or "(none)"

    out = {}
    for pair in pairs:
        label, sep, raw = pair.partition("=")
        if not sep:
            _fail(f"--field expects Label=value, got {pair!r}.")
        matches = by_label.get(label.strip().lower(), [])
        if not matches:
            _fail(f"Activity type {activity_type_id} has no field '{label.strip()}'. "
                  f"Available: {available}")
        if len(matches) > 1:
            ids = ", ".join(str(m.get("id")) for m in matches)
            _fail(f"Field label '{label.strip()}' is ambiguous on activity type "
                  f"{activity_type_id} (field ids {ids}). Available: {available}")
        field = matches[0]
        out[str(field["id"])] = None if raw == "" else _coerce(field, raw)
    return out


# ═══════════════════════════════════════════════════════════════════════════════
# REGISTRATION
# ═══════════════════════════════════════════════════════════════════════════════


def _add_scope(p):
    p.add_argument("--scope", choices=["project", "company"], default="project",
                   help="Template scope (default: project)")


def _add_data(p):
    p.add_argument("--data", help="Extra JSON object merged into the payload last")


def _add_field_flags(p, create, choice_help="Custom object (field-choice) id for a choice field"):
    p.add_argument("--label", required=create)
    p.add_argument("--type", required=create, choices=FIELD_TYPES, help="field_type")
    p.add_argument("--required", type=_bool, metavar="BOOL", help="is_required")
    p.add_argument("--options", help="Dropdown options for select/radio, comma-separated")
    p.add_argument("--choice-id", type=int, help=choice_help + " (0 clears)")
    p.add_argument("--order", type=int)
    p.add_argument("--section-id", type=int, help="Field section id (0 clears)")
    _add_data(p)


def _register_type_fields(sub, group, parent_help, scoped):
    g = sub.add_parser(group, help=parent_help)
    gs = g.add_subparsers(dest=group.replace("-", "_") + "_command")
    owner = "Extension id" if not scoped else "Activity type id"

    def base(name, help_text):
        p = gs.add_parser(name, help=help_text)
        p.add_argument("parent_id", type=int, help=owner)
        if scoped:
            _add_scope(p)
        return p

    base("list", "List custom fields")
    p = base("get", "One custom field")
    p.add_argument("field_id", type=int, help="Field (FieldDefinition) id")
    p = base("create", "Create a custom field")
    _add_field_flags(p, True)
    p.add_argument("--help-text")
    p = base("update", "Update a custom field")
    p.add_argument("field_id", type=int, help="Field (FieldDefinition) id")
    _add_field_flags(p, False)
    p.add_argument("--help-text")
    p = base("delete", "Delete a custom field")
    p.add_argument("field_id", type=int, help="Field (FieldDefinition) id")
    p = base("reorder", "Reorder fields (comma-sep field ids, in order)")
    p.add_argument("--order", required=True, help="Field ids in desired order, comma-separated")


def register_parser(sub):
    # ── activity-types ──
    g = sub.add_parser("activity-types", help="Activity type templates (company or project)")
    gs = g.add_subparsers(dest="activity_types_command")

    p = gs.add_parser("list", help="List activity types")
    p.add_argument("--search", "-s")
    p.add_argument("--category")
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--offset", type=int, default=0)
    _add_scope(p)
    p = gs.add_parser("get", help="Activity type detail (with steps)")
    p.add_argument("type_id", type=int)
    _add_scope(p)
    for name, create in (("create", True), ("update", False)):
        p = gs.add_parser(name, help=f"{name.capitalize()} an activity type")
        if not create:
            p.add_argument("type_id", type=int)
        p.add_argument("--name", required=create)
        p.add_argument("--description")
        p.add_argument("--category", help="Category ('' clears)")
        p.add_argument("--default", type=_bool, metavar="BOOL",
                       help="Company default (company scope only)")
        _add_data(p)
        _add_scope(p)
    p = gs.add_parser("delete", help="Delete an activity type")
    p.add_argument("type_id", type=int)
    _add_scope(p)
    p = gs.add_parser("categories", help="Distinct categories")
    _add_scope(p)
    p = gs.add_parser("schema", help="Effective field schema (sections + fields, incl. extension)")
    p.add_argument("type_id", type=int)
    _add_scope(p)
    gs.add_parser("default", help="The company's default activity type")

    # ── type-steps ──
    g = sub.add_parser("type-steps", help="Template steps of an activity type")
    gs = g.add_subparsers(dest="type_steps_command")
    p = gs.add_parser("list", help="List template steps")
    p.add_argument("type_id", type=int)
    _add_scope(p)
    for name, create in (("create", True), ("update", False)):
        p = gs.add_parser(name, help=f"{name.capitalize()} a template step")
        p.add_argument("type_id", type=int)
        if not create:
            p.add_argument("step_id", type=int)
        p.add_argument("--name", required=create)
        p.add_argument("--description")
        p.add_argument("--weight", type=float, help="progress_weight (0-100)")
        p.add_argument("--order", type=int)
        p.add_argument("--form", type=int, help="Required form template id (0 clears)")
        _add_data(p)
        _add_scope(p)
    p = gs.add_parser("delete", help="Delete a template step")
    p.add_argument("type_id", type=int)
    p.add_argument("step_id", type=int)
    _add_scope(p)
    p = gs.add_parser("reorder", help="Reorder steps (comma-sep step ids, in order)")
    p.add_argument("type_id", type=int)
    p.add_argument("--order", required=True, help="Step ids in desired order, comma-separated")
    _add_scope(p)
    p = gs.add_parser("weights", help="Set step weights (normalized to 100%%)")
    p.add_argument("type_id", type=int)
    p.add_argument("--set", required=True, help="STEP_ID=WEIGHT,... e.g. 12=30,13=70")
    _add_scope(p)

    # ── type-fields / type-extension-fields ──
    _register_type_fields(sub, "type-fields", "Custom fields of an activity type", scoped=True)
    _register_type_fields(sub, "type-extension-fields",
                          "Project-only fields added by an activity-type extension", scoped=False)

    # ── type-extensions ──
    g = sub.add_parser("type-extensions", help="Project extensions of company activity types")
    gs = g.add_subparsers(dest="type_extensions_command")
    gs.add_parser("list", help="List extensions in the project")
    p = gs.add_parser("get", help="Extension detail (effective fields)")
    p.add_argument("extension_id", type=int)
    p = gs.add_parser("create", help="Extend a company activity type into the project")
    p.add_argument("--base-type", type=int, required=True, help="Company activity type id")
    p = gs.add_parser("delete", help="Delete an extension")
    p.add_argument("extension_id", type=int)

    # ── field-definitions ──
    g = sub.add_parser("field-definitions", help="Field definitions (shared by forms and activity types)")
    gs = g.add_subparsers(dest="field_definitions_command")
    p = gs.add_parser("list", help="List field definitions")
    p.add_argument("--type", choices=FIELD_TYPES)
    p.add_argument("--section", type=int)
    p.add_argument("--choice-set", type=int)
    p.add_argument("--lean", action="store_true")
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--offset", type=int, default=0)
    _add_scope(p)
    p = gs.add_parser("get", help="Field definition detail")
    p.add_argument("field_id", type=int)
    _add_scope(p)
    p = gs.add_parser("create", help="Create a field definition")
    _add_field_flags(p, True, choice_help="Custom object id (choice_set_id)")
    _add_scope(p)
    p = gs.add_parser("update", help="Update a field definition")
    p.add_argument("field_id", type=int)
    _add_field_flags(p, False, choice_help="Custom object id (choice_set_id)")
    _add_scope(p)
    p = gs.add_parser("delete", help="Delete a field definition")
    p.add_argument("field_id", type=int)
    _add_scope(p)

    # ── field-sections ──
    g = sub.add_parser("field-sections", help="Field sections (headings fields group under)")
    gs = g.add_subparsers(dest="field_sections_command")
    p = gs.add_parser("list", help="List field sections")
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--offset", type=int, default=0)
    _add_scope(p)
    p = gs.add_parser("get", help="Field section detail")
    p.add_argument("section_id", type=int)
    _add_scope(p)
    for name, create in (("create", True), ("update", False)):
        p = gs.add_parser(name, help=f"{name.capitalize()} a field section")
        if not create:
            p.add_argument("section_id", type=int)
        p.add_argument("--name", required=create)
        p.add_argument("--description")
        p.add_argument("--order", type=int)
        _add_data(p)
        _add_scope(p)
    p = gs.add_parser("delete", help="Delete a field section")
    p.add_argument("section_id", type=int)
    _add_scope(p)

    # ── field-rules ──
    g = sub.add_parser("field-rules", help="Validation rules of a field definition")
    gs = g.add_subparsers(dest="field_rules_command")
    p = gs.add_parser("list", help="List validation rules")
    p.add_argument("field_id", type=int)
    _add_scope(p)
    p = gs.add_parser("get", help="Validation rule detail")
    p.add_argument("field_id", type=int)
    p.add_argument("rule_id", type=int)
    _add_scope(p)
    for name, create in (("create", True), ("update", False)):
        p = gs.add_parser(name, help=f"{name.capitalize()} a validation rule")
        p.add_argument("field_id", type=int)
        if not create:
            p.add_argument("rule_id", type=int)
        p.add_argument("--rule-type", required=create, choices=RULE_TYPES)
        p.add_argument("--value", required=create, help='Rule config JSON, e.g. \'{"min": 5}\'')
        p.add_argument("--message", help="error_message")
        p.add_argument("--active", type=_bool, metavar="BOOL")
        p.add_argument("--order", type=int)
        _add_data(p)
        _add_scope(p)
    p = gs.add_parser("delete", help="Delete a validation rule")
    p.add_argument("field_id", type=int)
    p.add_argument("rule_id", type=int)
    _add_scope(p)


_TYPE_FIELD_HANDLERS = {
    "list": cmd_type_fields_list,
    "get": cmd_type_fields_get,
    "create": cmd_type_fields_create,
    "update": cmd_type_fields_update,
    "delete": cmd_type_fields_delete,
    "reorder": cmd_type_fields_reorder,
}

SUB_HANDLERS = {
    "activity-types": {
        "list": cmd_activity_types_list,
        "get": cmd_activity_types_get,
        "create": cmd_activity_types_create,
        "update": cmd_activity_types_update,
        "delete": cmd_activity_types_delete,
        "categories": cmd_activity_types_categories,
        "schema": cmd_activity_types_schema,
        "default": cmd_activity_types_default,
    },
    "type-steps": {
        "list": cmd_type_steps_list,
        "create": cmd_type_steps_create,
        "update": cmd_type_steps_update,
        "delete": cmd_type_steps_delete,
        "reorder": cmd_type_steps_reorder,
        "weights": cmd_type_steps_weights,
    },
    "type-fields": dict(_TYPE_FIELD_HANDLERS),
    "type-extension-fields": dict(_TYPE_FIELD_HANDLERS),
    "type-extensions": {
        "list": cmd_type_extensions_list,
        "get": cmd_type_extensions_get,
        "create": cmd_type_extensions_create,
        "delete": cmd_type_extensions_delete,
    },
    "field-definitions": {
        "list": cmd_field_definitions_list,
        "get": cmd_field_definitions_get,
        "create": cmd_field_definitions_create,
        "update": cmd_field_definitions_update,
        "delete": cmd_field_definitions_delete,
    },
    "field-sections": {
        "list": cmd_field_sections_list,
        "get": cmd_field_sections_get,
        "create": cmd_field_sections_create,
        "update": cmd_field_sections_update,
        "delete": cmd_field_sections_delete,
    },
    "field-rules": {
        "list": cmd_field_rules_list,
        "get": cmd_field_rules_get,
        "create": cmd_field_rules_create,
        "update": cmd_field_rules_update,
        "delete": cmd_field_rules_delete,
    },
}

SUB_COMMAND_KEYS = {g: g.replace("-", "_") + "_command" for g in SUB_HANDLERS}

COMPANY_ONLY = set()
