"""Form template, field, and submission commands."""

import json
import sys

from pcxa._output import out_json, out_table, tag_names


def _form_row(f):
    return {
        "id": str(f.get("id", "")),
        "name": str(f.get("name", ""))[:40],
        "scope": f.get("scope", ""),
        "category": str(f.get("category") or "")[:15],
        "type": str(f.get("form_type") or "")[:15],
        "prefix": str(f.get("code_prefix") or ""),
        "subs": str(f.get("submissions_count", 0)),
        "created": str(f.get("created_at", ""))[:10],
    }


def cmd_forms_list(client, args):
    """List form templates."""
    params = client.paginate_params(args.limit, args.offset)
    if args.category:
        params["category"] = args.category
    if args.scope:
        params["scope"] = args.scope
    if args.search:
        params["search"] = args.search
    if args.sort:
        params["ordering"] = args.sort

    if args.count_only:
        print(json.dumps({"count": client.get_count("forms/", params)}))
        return

    data = client.get("forms/", params)
    if args.format == "json":
        out_json(data)
        return
    results = data.get("results", data) if isinstance(data, dict) else data
    total = data.get("count", len(results)) if isinstance(data, dict) else len(results)
    rows = [_form_row(f) for f in results]
    print(f"Forms: {len(rows)} of {total}\n")
    out_table(rows, ["id", "name", "scope", "category", "type", "prefix", "subs", "created"])


def cmd_forms_get(client, args):
    """Show form detail with fields."""
    data = client.get(f"forms/{args.form_id}/")
    if args.format == "json":
        out_json(data)
        return

    print(f"Form {data.get('id')}: {data.get('name')}")
    print(f"  Scope:       {data.get('scope')}")
    print(f"  Category:    {data.get('category') or '-'}")
    print(f"  Type:        {data.get('form_type') or '-'}")
    print(f"  Code prefix: {data.get('code_prefix') or '-'} (pad={data.get('code_padding')}, sep='{data.get('code_separator', '-')}')")
    print(f"  Code scope:  {data.get('code_scope', '-')}")
    print(f"  Private def: {data.get('is_private_default', False)}")
    print(f"  Submissions: {data.get('submissions_count', 0)}")
    if data.get("description"):
        print(f"  Description: {data['description'][:200]}")
    wf = data.get("workflow_template_detail")
    if wf:
        print(f"  Workflow:    {wf.get('name', wf)}")
    reviewers = data.get("default_reviewers_details") or []
    if reviewers:
        names = ", ".join(r.get("username", str(r)) for r in reviewers)
        print(f"  Reviewers:   {names}")

    # Fetch and display fields
    try:
        fields_data = client.get(f"forms/{args.form_id}/fields/")
        fields = fields_data.get("results", fields_data) if isinstance(fields_data, dict) else fields_data
        if fields:
            print(f"\n  Fields ({len(fields)}):")
            for f in fields:
                req = "*" if f.get("is_required") else " "
                opts = ""
                if f.get("options"):
                    choices = f["options"].get("choices", [])
                    if choices:
                        opts = f" [{', '.join(str(c) for c in choices[:5])}]"
                print(f"    {req} {f.get('order', '-'):>2}. [{f.get('id')}] {f.get('label')} ({f.get('field_type')}){opts}")
    except Exception:
        pass
    print()


def cmd_forms_create(client, args):
    """Create form template."""
    payload = {
        "name": args.name,
        "scope": args.scope or "project",
        "company": client.company_id,
    }
    if (args.scope or "project") == "project":
        payload["project"] = client.project_id
    if args.description:
        payload["description"] = args.description
    if args.category:
        payload["category"] = args.category
    if args.form_type:
        payload["form_type"] = args.form_type
    if args.code_prefix:
        payload["code_prefix"] = args.code_prefix
    if args.code_scope:
        payload["code_scope"] = args.code_scope
    if args.code_separator is not None:
        payload["code_separator"] = args.code_separator
    if args.code_padding is not None:
        payload["code_padding"] = args.code_padding
    if args.private_default:
        payload["is_private_default"] = True
    if args.reviewers:
        payload["default_reviewers"] = [int(x) for x in args.reviewers.split(",")]

    if args.dry_run:
        print(f"Would CREATE form: {json.dumps(payload, indent=2)}")
        return
    data = client.post("forms/", payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Created form {data.get('id')}: '{data.get('name')}'")


def cmd_forms_update(client, args):
    """Update form template."""
    payload = {}
    if args.name:
        payload["name"] = args.name
    if args.description is not None:
        payload["description"] = args.description
    if args.category is not None:
        payload["category"] = args.category or None
    if args.form_type is not None:
        payload["form_type"] = args.form_type or None
    if args.code_prefix is not None:
        payload["code_prefix"] = args.code_prefix or None
    if args.code_scope:
        payload["code_scope"] = args.code_scope
    if args.code_separator is not None:
        payload["code_separator"] = args.code_separator
    if args.code_padding is not None:
        payload["code_padding"] = args.code_padding
    if args.private_default is not None:
        payload["is_private_default"] = args.private_default
    if args.reviewers is not None:
        payload["default_reviewers"] = [int(x) for x in args.reviewers.split(",")] if args.reviewers else []
    if not payload:
        print("No fields to update.", file=sys.stderr)
        sys.exit(1)
    if args.dry_run:
        print(f"Would UPDATE form {args.form_id}: {json.dumps(payload, indent=2)}")
        return
    data = client.patch(f"forms/{args.form_id}/", payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Updated form {data.get('id')}: '{data.get('name')}'")


def cmd_forms_delete(client, args):
    """Delete form template."""
    if args.dry_run:
        print(f"Would DELETE form {args.form_id}")
        return
    client.delete(f"forms/{args.form_id}/")
    print(f"Deleted form {args.form_id}")


# ═══════════════════════════════════════════════════════════════════════════════
# FIELDS — Form field management
# ═══════════════════════════════════════════════════════════════════════════════


def cmd_fields_list(client, args):
    """List fields for a form."""
    data = client.get(f"forms/{args.form_id}/fields/")
    if args.format == "json":
        out_json(data)
        return
    results = data.get("results", data) if isinstance(data, dict) else data
    rows = []
    for f in results:
        opts = ""
        if f.get("options"):
            choices = f["options"].get("choices", [])
            if choices:
                opts = ", ".join(str(c) for c in choices[:4])
                if len(choices) > 4:
                    opts += f" (+{len(choices)-4})"
        rows.append({
            "id": str(f.get("id", "")),
            "order": str(f.get("order", "")),
            "label": str(f.get("label", ""))[:35],
            "type": f.get("field_type", ""),
            "req": "yes" if f.get("is_required") else "",
            "options": opts[:30],
        })
    print(f"Fields for form {args.form_id}: {len(rows)}\n")
    out_table(rows, ["id", "order", "label", "type", "req", "options"])


def cmd_fields_create(client, args):
    """Create a field on a form."""
    payload = {"label": args.label, "field_type": args.field_type}
    if args.order is not None:
        payload["order"] = args.order
    if args.required:
        payload["is_required"] = True
    if args.placeholder:
        payload["placeholder"] = args.placeholder
    if args.help_text:
        payload["help_text"] = args.help_text
    if args.options:
        payload["options"] = json.loads(args.options)
    if args.column_span is not None:
        payload["column_span"] = args.column_span
    if args.section is not None:
        payload["section_id"] = args.section

    if args.dry_run:
        print(f"Would CREATE field on form {args.form_id}: {json.dumps(payload, indent=2)}")
        return
    data = client.post(f"forms/{args.form_id}/fields/", payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Created field {data.get('id')}: '{data.get('label')}' ({data.get('field_type')})")


def cmd_fields_update(client, args):
    """Update a form field."""
    payload = {}
    if args.label:
        payload["label"] = args.label
    if args.field_type:
        payload["field_type"] = args.field_type
    if args.order is not None:
        payload["order"] = args.order
    if args.required is not None:
        payload["is_required"] = args.required
    if args.placeholder is not None:
        payload["placeholder"] = args.placeholder
    if args.help_text is not None:
        payload["help_text"] = args.help_text
    if args.options:
        payload["options"] = json.loads(args.options)
    if args.column_span is not None:
        payload["column_span"] = args.column_span
    if not payload:
        print("No fields to update.", file=sys.stderr)
        sys.exit(1)
    if args.dry_run:
        print(f"Would UPDATE field {args.field_id} on form {args.form_id}: {json.dumps(payload, indent=2)}")
        return
    data = client.patch(f"forms/{args.form_id}/fields/{args.field_id}/", payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Updated field {data.get('id')}: '{data.get('label')}' ({data.get('field_type')})")


def cmd_fields_delete(client, args):
    """Delete a form field."""
    if args.dry_run:
        print(f"Would DELETE field {args.field_id} from form {args.form_id}")
        return
    client.delete(f"forms/{args.form_id}/fields/{args.field_id}/")
    print(f"Deleted field {args.field_id}")


# ═══════════════════════════════════════════════════════════════════════════════
# SUBMISSIONS — Form submission management
# ═══════════════════════════════════════════════════════════════════════════════


def _submission_row(s):
    return {
        "id": str(s.get("id", "")),
        "code": str(s.get("code", ""))[:15],
        "form": str(s.get("form_name") or s.get("form", ""))[:25],
        "status": s.get("status", ""),
        "by": str(s.get("submitted_by_username") or s.get("submitted_by") or "")[:12],
        "owner": str(s.get("owner_name") or s.get("owner") or "")[:12],
        "submitted": str(s.get("submitted_at") or "")[:10],
        "tags": tag_names(s.get("tags"))[:20],
    }


def cmd_submissions_list(client, args):
    """List form submissions."""
    params = client.paginate_params(args.limit, args.offset)
    if args.form:
        params["form"] = args.form
    if args.status:
        params["status"] = args.status
    if args.search:
        params["search"] = args.search
    if args.owner:
        params["owner"] = args.owner
    if args.sort:
        params["ordering"] = args.sort

    if args.count_only:
        print(json.dumps({"count": client.get_count("form-submissions/", params)}))
        return

    data = client.get("form-submissions/", params)
    if args.format == "json":
        out_json(data)
        return
    results = data.get("results", data) if isinstance(data, dict) else data
    total = data.get("count", len(results)) if isinstance(data, dict) else len(results)
    rows = [_submission_row(s) for s in results]
    print(f"Submissions: {len(rows)} of {total}\n")
    out_table(rows, ["id", "code", "form", "status", "by", "owner", "submitted", "tags"])


def cmd_submissions_get(client, args):
    """Show submission detail with values."""
    data = client.get(f"forms/{args.form_id}/submissions/{args.submission_id}/")
    if args.format == "json":
        out_json(data)
        return

    print(f"Submission {data.get('id')}: {data.get('code')}")
    print(f"  Form:      {data.get('form_name')} (id={data.get('form')})")
    print(f"  Status:    {data.get('status')}")
    print(f"  Owner:     {data.get('owner_name') or data.get('owner') or '-'}")
    print(f"  By:        {data.get('submitted_by_username') or '-'}")
    print(f"  Submitted: {str(data.get('submitted_at') or '-')[:19]}")
    if data.get("first_submitted_at"):
        print(f"  First sub: {str(data['first_submitted_at'])[:19]}")
    if data.get("closed_at"):
        print(f"  Closed:    {str(data['closed_at'])[:19]}")
    print(f"  Private:   {data.get('is_private', False)}")
    print(f"  Revision:  {data.get('current_revision_number') or '-'} ({data.get('revision_count', 0)} total)")

    assignees = data.get("assignees_details") or data.get("assignees") or []
    if assignees:
        names = ", ".join(
            a.get("username", str(a)) if isinstance(a, dict) else str(a)
            for a in assignees
        )
        print(f"  Assignees: {names}")

    tags = data.get("tags") or []
    if tags:
        print(f"  Tags:      {tag_names(tags)}")

    loc = data.get("location_name")
    if loc:
        print(f"  Location:  {loc}")

    values = data.get("values") or {}
    if values:
        print(f"\n  Values ({len(values)}):")
        # Try to fetch field labels for display
        field_labels = {}
        try:
            fields_data = client.get(f"forms/{data.get('form', args.form_id)}/fields/")
            fields = fields_data.get("results", fields_data) if isinstance(fields_data, dict) else fields_data
            field_labels = {str(f["id"]): f.get("label", f"field_{f['id']}") for f in fields}
        except Exception:
            pass
        for fid, val in values.items():
            label = field_labels.get(str(fid), f"field_{fid}")
            print(f"    {label}: {val}")
    print()


def cmd_submissions_create(client, args):
    """Create a form submission."""
    payload = {
        "form": args.form_id,
        "company": client.company_id,
        "project": client.project_id,
        "code": args.code,
    }
    if args.values:
        payload["values"] = json.loads(args.values)
    if args.owner:
        payload["owner"] = args.owner
    if args.assignees:
        payload["assignees"] = [int(x) for x in args.assignees.split(",")]
    if args.distribution:
        payload["distribution_list"] = [int(x) for x in args.distribution.split(",")]
    if args.private:
        payload["is_private"] = True
    if args.tags:
        payload["tags"] = [t.strip() for t in args.tags.split(",")]
    if args.location_name:
        payload["location_name"] = args.location_name

    if args.dry_run:
        print(f"Would CREATE submission on form {args.form_id}: {json.dumps(payload, indent=2)}")
        return
    data = client.post(f"forms/{args.form_id}/submissions/", payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Created submission {data.get('id')}: code='{data.get('code')}' status={data.get('status')}")


def cmd_submissions_update(client, args):
    """Update a form submission."""
    payload = {}
    if args.values:
        payload["values"] = json.loads(args.values)
    if args.code:
        payload["code"] = args.code
    if args.owner is not None:
        payload["owner"] = args.owner if args.owner != 0 else None
    if args.assignees is not None:
        payload["assignees"] = [int(x) for x in args.assignees.split(",")] if args.assignees else []
    if args.distribution is not None:
        payload["distribution_list"] = [int(x) for x in args.distribution.split(",")] if args.distribution else []
    if args.private is not None:
        payload["is_private"] = args.private
    if args.tags is not None:
        payload["tags"] = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else []
    if args.location_name is not None:
        payload["location_name"] = args.location_name
    if not payload:
        print("No fields to update.", file=sys.stderr)
        sys.exit(1)
    if args.dry_run:
        print(f"Would UPDATE submission {args.submission_id}: {json.dumps(payload, indent=2)}")
        return
    data = client.patch(f"forms/{args.form_id}/submissions/{args.submission_id}/", payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Updated submission {data.get('id')}: code='{data.get('code')}' status={data.get('status')}")


def cmd_submissions_delete(client, args):
    """Delete a form submission."""
    if args.dry_run:
        print(f"Would DELETE submission {args.submission_id} from form {args.form_id}")
        return
    client.delete(f"forms/{args.form_id}/submissions/{args.submission_id}/")
    print(f"Deleted submission {args.submission_id}")
