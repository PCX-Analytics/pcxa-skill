"""Activity, step, progress, comment, dependency, Gantt, and tree commands."""

import datetime
import json
import sys
from pathlib import Path

from pcxa._http import requests
from pcxa._output import out_json, out_table, tag_names
from pcxa._resolve import resolve_member_by_name, validate_choice_field_values

PRIORITY_MAP = {0: "-", 1: "Low", 2: "Med", 3: "High", 4: "Critical"}

# Payload key carrying custom-field values on an activity (keyed by custom-field
# id). Activity custom fields are defined at ``activities/custom-fields/`` — the
# activity analogue of a form's fields. The exact attribute couldn't be confirmed
# on the (permission-gated) test account; it's centralized here so confirming the
# real name is a one-line change.
_CUSTOM_FIELDS_KEY = "custom_field_values"


def _validate_activity_choice_values(client, values, args):
    """Fuzzy-validate activity custom-field values against custom objects.

    Mirrors form-submission validation: for each value targeting a
    custom-object-backed activity custom field, confirm the value matches an
    option, else block with a "Did you mean Y?" suggestion (unless ``--no-fuzzy``).
    A read error loading the custom-field definitions degrades to a warning.
    """
    if getattr(args, "no_fuzzy", False) or not values:
        return
    try:
        fdata = client.get("activities/custom-fields/")
        fields = fdata.get("results", fdata) if isinstance(fdata, dict) else fdata
    except Exception as e:
        print(f"Note: skipped custom-object validation (could not load activity custom fields: {e})",
              file=sys.stderr)
        return

    problems, notes = validate_choice_field_values(client, fields, values)
    for n in notes:
        print(f"Note: skipped validation for {n}", file=sys.stderr)
    if problems:
        print("Custom-object value validation failed:", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        print("Fix the value(s), or pass --no-fuzzy to write as-is.", file=sys.stderr)
        sys.exit(1)


def _activity_row(a):
    assignees = a.get("assignees_details") or a.get("assignees") or []
    if isinstance(assignees, list) and assignees:
        if isinstance(assignees[0], dict):
            astr = ",".join(x.get("username", "") for x in assignees[:3])
        else:
            astr = ",".join(str(x) for x in assignees[:3])
    else:
        astr = ""
    return {
        "id": str(a.get("id", "")),
        "title": str(a.get("title", ""))[:45],
        "status": a.get("status", ""),
        "pct": f"{a.get('percent_complete', 0)}%",
        "priority": PRIORITY_MAP.get(a.get("priority", 0), "?"),
        "owner": str(a.get("owner_name", a.get("owner") or ""))[:12],
        "due": str(a.get("due_date") or "")[:10],
    }


def cmd_activities_list(client, args):
    """List activities."""
    params = client.paginate_params(args.limit, args.offset)
    if args.status:
        params["status"] = args.status
    if args.priority:
        params["priority"] = args.priority
    if args.owner:
        owner = args.owner
        if not owner.isdigit():
            uid, msg = resolve_member_by_name(client, owner)
            print(msg, file=sys.stderr)
            if uid is None:
                sys.exit(1)
            owner = str(uid)
        params["owner"] = owner
    if args.assignee:
        # Accept user ID (integer) or name (fuzzy resolved)
        assignee = args.assignee
        if not assignee.isdigit():
            uid, msg = resolve_member_by_name(client, assignee)
            print(msg, file=sys.stderr)
            if uid is None:
                sys.exit(1)
            assignee = str(uid)
        params["assigned_to"] = assignee
    if args.type:
        params["activity_type"] = args.type
    if args.parent:
        params["parent"] = args.parent
    if args.root_only:
        params["parent__isnull"] = "true"
    if args.search:
        params["search"] = args.search
        # Default to fuzzy: exact substring matches surface first, then
        # typo-tolerant matches ranked by similarity. `--exact` opts back
        # into the tighter 0.8-threshold mode.
        if not getattr(args, "exact", False):
            params["search_mode"] = "fuzzy"
    if args.tags:
        params["tags"] = args.tags
        if getattr(args, "tags_mode", None):
            params["tags_mode"] = args.tags_mode
    if getattr(args, "after", None):
        params["updated_at__gte"] = args.after
    if getattr(args, "before", None):
        params["updated_at__lte"] = args.before
    if getattr(args, "created_after", None):
        params["created_at__gte"] = args.created_after
    if getattr(args, "created_before", None):
        params["created_at__lte"] = args.created_before
    # When --sort is unspecified and --search is supplied, omit `ordering`
    # so TrigramSearchFilter can rank by similarity DESC (exact matches
    # surface first). Otherwise preserve the prior default.
    sort = args.sort if args.sort is not None else (None if args.search else "-created_at")
    if sort:
        params["ordering"] = sort

    if args.count_only:
        print(json.dumps({"count": client.get_count("activities/", params)}))
        return

    data = client.get("activities/", params)
    if args.format == "json":
        out_json(data)
        return
    results = data.get("results", data) if isinstance(data, dict) else data
    total = data.get("count", len(results)) if isinstance(data, dict) else len(results)
    rows = [_activity_row(a) for a in results]
    print(f"Activities: {len(rows)} of {total}\n")
    out_table(rows, ["id", "title", "status", "pct", "priority", "owner", "due"])


def cmd_activities_get(client, args):
    """Activity detail."""
    data = client.get(f"activities/{args.activity_id}/")
    if args.format == "json":
        out_json(data)
        return

    print(f"Activity {data.get('id')}: {data.get('title')}")
    print(f"  Status:    {data.get('status')} ({data.get('percent_complete', 0)}%)")
    print(f"  Priority:  {PRIORITY_MAP.get(data.get('priority', 0), '?')}")
    print(f"  Type:      {data.get('activity_type_name', data.get('activity_type') or '-')}")
    print(f"  Owner:     {data.get('owner_name', data.get('owner') or '-')}")
    print(f"  Due:       {data.get('due_date') or '-'}")
    ps, pf = str(data.get("planned_start") or "-")[:10], str(data.get("planned_finish") or "-")[:10]
    print(f"  Planned:   {ps} -> {pf}")
    acs, acf = str(data.get("actual_start") or "-")[:10], str(data.get("actual_finish") or "-")[:10]
    print(f"  Actual:    {acs} -> {acf}")
    if data.get("description"):
        print(f"  Desc:      {data['description'][:200]}")
    if data.get("wbs_code"):
        print(f"  WBS:       {data['wbs_code']}")
    tags = data.get("tags") or []
    if tags:
        print(f"  Tags:      {tag_names(tags)}")

    steps = data.get("steps") or []
    if steps:
        print(f"\n  Steps ({len(steps)}):")
        for s in steps:
            check = "x" if s.get("percent_complete", 0) == 100 else " "
            print(f"    [{check}] {s.get('name', '?')} ({s.get('percent_complete', 0)}%, w={s.get('progress_weight', '?')})")

    deps = data.get("activity_dependencies") or {}
    for label, key in [("Predecessors", "predecessors"), ("Successors", "successors")]:
        items = deps.get(key) or []
        if items:
            print(f"\n  {label} ({len(items)}):")
            for d in items:
                ref = d.get("predecessor") if key == "predecessors" else d.get("successor")
                print(f"    {d.get('dependency_type', '?')} #{ref} (lag={d.get('lag_days', 0)}d)")
    print()


def cmd_activities_create(client, args):
    """Create activity."""
    payload = {"title": args.title, "project": client.project_id}
    if args.description:
        payload["description"] = args.description
    if args.status:
        payload["status"] = args.status
    if args.priority is not None:
        payload["priority"] = args.priority
    if args.due_date:
        payload["due_date"] = args.due_date
    if args.planned_start:
        payload["planned_start"] = args.planned_start
    if args.planned_finish:
        payload["planned_finish"] = args.planned_finish
    if args.owner:
        payload["owner"] = args.owner
    if args.assignees:
        payload["assignees"] = [int(x) for x in args.assignees.split(",")]
    if args.type:
        payload["activity_type"] = args.type
    if args.parent:
        payload["parent"] = args.parent
    if args.tags:
        payload["tags"] = [t.strip() for t in args.tags.split(",")]
    if args.wbs:
        payload["wbs_code"] = args.wbs
    cf = json.loads(args.custom_fields) if getattr(args, "custom_fields", None) else None
    if cf:
        payload[_CUSTOM_FIELDS_KEY] = cf

    if args.dry_run:
        print(f"Would CREATE activity: {json.dumps(payload, indent=2)}")
        return
    _validate_activity_choice_values(client, cf, args)
    data = client.post("activities/", payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Created activity {data.get('id')}: '{data.get('title')}'")


def cmd_activities_update(client, args):
    """Update activity."""
    payload = {}
    if args.title:
        payload["title"] = args.title
    if args.description is not None:
        payload["description"] = args.description
    if args.status:
        payload["status"] = args.status
    if args.priority is not None:
        payload["priority"] = args.priority
    if args.percent is not None:
        payload["percent_complete"] = args.percent
    if args.due_date:
        payload["due_date"] = args.due_date
    if args.planned_start:
        payload["planned_start"] = args.planned_start
    if args.planned_finish:
        payload["planned_finish"] = args.planned_finish
    if args.actual_start:
        payload["actual_start"] = args.actual_start
    if args.actual_finish:
        payload["actual_finish"] = args.actual_finish
    if args.owner:
        payload["owner"] = args.owner
    if args.assignees:
        payload["assignees"] = [int(x) for x in args.assignees.split(",")]
    if args.parent is not None:
        payload["parent"] = args.parent if args.parent != 0 else None
    if args.tags:
        payload["tags"] = [t.strip() for t in args.tags.split(",")]
    cf = json.loads(args.custom_fields) if getattr(args, "custom_fields", None) else None
    if cf:
        payload[_CUSTOM_FIELDS_KEY] = cf

    if not payload:
        print("No fields to update.", file=sys.stderr)
        sys.exit(1)
    if args.dry_run:
        print(f"Would UPDATE activity {args.activity_id}: {json.dumps(payload, indent=2)}")
        return
    _validate_activity_choice_values(client, cf, args)
    data = client.patch(f"activities/{args.activity_id}/", payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Updated activity {data.get('id')}: '{data.get('title')}'")


def cmd_activities_delete(client, args):
    """Delete activities."""
    if args.dry_run:
        print(f"Would DELETE activities {args.activity_ids}")
        return
    if len(args.activity_ids) == 1:
        # The per-id soft_delete action is exposed as DELETE on the server
        # (not POST) — POST returns 405 Method Not Allowed.
        client.delete(f"activities/{args.activity_ids[0]}/soft_delete/")
        print(f"Deleted activity {args.activity_ids[0]}")
    else:
        data = client.bulk_call("activities/bulk_delete/", "activity_ids", args.activity_ids)
        if args.format == "json":
            out_json(data)
        else:
            print(f"Deleted {data.get('success_count', len(args.activity_ids))} activities")


def cmd_activities_bulk_update(client, args):
    """Bulk update activities."""
    updates = {}
    if args.status:
        updates["status"] = args.status
    if args.priority is not None:
        updates["priority"] = args.priority
    if args.owner:
        updates["owner"] = args.owner
    if not updates:
        print("No updates specified.", file=sys.stderr)
        sys.exit(1)
    if args.dry_run:
        print(f"Would BULK UPDATE {len(args.activity_ids)} activities: {updates}")
        return
    # activities/bulk_update/ is exposed as PATCH (unlike files/bulk_update/,
    # which takes POST) — POST returns 405 Method Not Allowed.
    data = client.bulk_call("activities/bulk_update/", "activity_ids", args.activity_ids,
                            {"updates": updates}, method="PATCH")
    if args.format == "json":
        out_json(data)
    else:
        print(f"Updated {data.get('success_count', '?')} activities")


def cmd_activities_types(client, args):
    """List activity types."""
    data = client.get_all_pages("activity-types/")
    if args.format == "json":
        out_json(data)
        return
    rows = []
    for t in data:
        steps = t.get("template_steps") or t.get("steps") or []
        rows.append({
            "id": str(t.get("id", "")),
            "name": str(t.get("name", ""))[:35],
            "category": str(t.get("category", ""))[:20],
            "steps": str(len(steps)),
            "default": "yes" if t.get("is_default") else "",
        })
    print(f"Activity types: {len(rows)}\n")
    out_table(rows, ["id", "name", "category", "steps", "default"])


# ═══════════════════════════════════════════════════════════════════════════════
# STEPS
# ═══════════════════════════════════════════════════════════════════════════════


def cmd_steps_list(client, args):
    data = client.get(f"activities/{args.activity_id}/steps/")
    if args.format == "json":
        out_json(data)
        return
    results = data.get("results", data) if isinstance(data, dict) else data
    rows = []
    for s in results:
        check = "x" if s.get("percent_complete", 0) == 100 else " "
        rows.append({
            "id": str(s.get("id", "")),
            "order": str(s.get("order", "")),
            "done": f"[{check}]",
            "name": str(s.get("name", ""))[:40],
            "pct": f"{s.get('percent_complete', 0)}%",
            "weight": f"{s.get('progress_weight', 0)}%",
        })
    print(f"Steps for activity {args.activity_id}:\n")
    out_table(rows, ["id", "order", "done", "name", "pct", "weight"])


def cmd_steps_create(client, args):
    payload = {"name": args.name, "activity": args.activity_id}
    if args.description:
        payload["description"] = args.description
    if args.weight is not None:
        payload["progress_weight"] = args.weight
    if args.order is not None:
        payload["order"] = args.order
    if args.dry_run:
        print(f"Would CREATE step: {payload}")
        return
    data = client.post(f"activities/{args.activity_id}/steps/", payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Created step {data.get('id')}: '{data.get('name')}' (weight={data.get('progress_weight')}%)")


def cmd_steps_update(client, args):
    payload = {}
    if args.name:
        payload["name"] = args.name
    if args.percent is not None:
        payload["percent_complete"] = args.percent
    if args.weight is not None:
        payload["progress_weight"] = args.weight
    if args.order is not None:
        payload["order"] = args.order
    if not payload:
        print("No fields to update.", file=sys.stderr)
        sys.exit(1)
    if args.dry_run:
        print(f"Would UPDATE step {args.step_id}: {payload}")
        return
    data = client.patch(f"activities/{args.activity_id}/steps/{args.step_id}/", payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Updated step {data.get('id')}: '{data.get('name')}' ({data.get('percent_complete')}%)")


def cmd_steps_delete(client, args):
    if args.dry_run:
        print(f"Would DELETE step {args.step_id}")
        return
    client.delete(f"activities/{args.activity_id}/steps/{args.step_id}/")
    print(f"Deleted step {args.step_id}")


def cmd_steps_from_template(client, args):
    if args.dry_run:
        print(f"Would CREATE steps from template on activity {args.activity_id}")
        return
    data = client.post(f"activities/{args.activity_id}/create_steps_from_template/")
    if args.format == "json":
        out_json(data)
    else:
        steps = data if isinstance(data, list) else data.get("steps", [data])
        print(f"Created {len(steps)} steps from template")


# ═══════════════════════════════════════════════════════════════════════════════
# PROGRESS
# ═══════════════════════════════════════════════════════════════════════════════


def cmd_progress_list(client, args):
    params = {}
    if args.source:
        params["source"] = args.source
    data = client.get(f"activities/{args.activity_id}/progress-entries/", params)
    if args.format == "json":
        out_json(data)
        return
    results = data.get("results", data) if isinstance(data, dict) else data
    rows = []
    for e in results:
        rows.append({
            "id": str(e.get("id", "")),
            "date": str(e.get("effective_date", ""))[:16],
            "pct": f"{e.get('percent_complete', 0)}%",
            "source": e.get("source", ""),
            "notes": str(e.get("notes", ""))[:40],
        })
    print(f"Progress for activity {args.activity_id}:\n")
    out_table(rows, ["id", "date", "pct", "source", "notes"])


def cmd_progress_add(client, args):
    payload = {"percent_complete": args.percent}
    if args.notes:
        payload["notes"] = args.notes
    payload["effective_date"] = args.date or datetime.date.today().isoformat()
    if args.dry_run:
        print(f"Would ADD progress: {payload}")
        return
    data = client.post(f"activities/{args.activity_id}/progress-entries/", payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Added progress: {data.get('percent_complete', 0)}% at {str(data.get('effective_date', ''))[:16]}")


def cmd_progress_delete(client, args):
    if args.dry_run:
        print(f"Would DELETE progress entry {args.entry_id}")
        return
    client.delete(f"activities/{args.activity_id}/progress-entries/{args.entry_id}/")
    print(f"Deleted progress entry {args.entry_id}")


# ═══════════════════════════════════════════════════════════════════════════════
# COMMENTS
# ═══════════════════════════════════════════════════════════════════════════════


def cmd_comments_list(client, args):
    data = client.get(f"activities/{args.activity_id}/comments/")
    results = data.get("results", data) if isinstance(data, dict) else data
    if args.format == "json":
        out_json(data)
        return
    rows = []
    for c in results:
        user = c.get("user") or {}
        user_name = user.get("first_name", "") or user.get("username", "") or str(user.get("id", ""))
        rows.append({
            "id": str(c.get("id", "")),
            "user": user_name,
            "content": str(c.get("content", ""))[:60],
            "type": c.get("comment_type", ""),
            "created": str(c.get("created_at", ""))[:16],
        })
    print(f"Comments on activity {args.activity_id}: {len(rows)}\n")
    out_table(rows, ["id", "user", "content", "type", "created"])


def cmd_comments_add(client, args):
    payload = {"content": args.content}
    if args.dry_run:
        print(f"Would ADD comment on activity {args.activity_id}: {payload}")
        return
    data = client.post(f"activities/{args.activity_id}/comments/", payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Added comment {data.get('id')} on activity {args.activity_id}")


def cmd_comments_delete(client, args):
    if args.dry_run:
        print(f"Would DELETE comment {args.comment_id} on activity {args.activity_id}")
        return
    client.delete(f"activities/{args.activity_id}/comments/{args.comment_id}/")
    print(f"Deleted comment {args.comment_id}")


def cmd_comments_bulk(client, args):
    """Bulk add comments from a JSON file."""
    file_path = Path(args.file)
    if not file_path.exists():
        print(f"File not found: {file_path}", file=sys.stderr)
        sys.exit(1)
    raw = json.loads(file_path.read_text())
    if isinstance(raw, dict):
        comments = raw.get("comments")
        if not isinstance(comments, list):
            print("JSON object must contain a 'comments' key with a list.", file=sys.stderr)
            sys.exit(1)
    elif isinstance(raw, list):
        comments = raw
    else:
        print("JSON file must contain a list or an object with a 'comments' key.", file=sys.stderr)
        sys.exit(1)

    created = 0
    errors = []
    for i, item in enumerate(comments):
        content = item.get("content")
        if not content:
            errors.append(f"[{i}] Missing 'content' field")
            continue

        payload = {"content": content}

        if args.dry_run:
            preview = content[:60] + ("..." if len(content) > 60 else "")
            print(f"  [{i}] Would ADD: {preview}")
            continue

        try:
            data = client.post(f"activities/{args.activity_id}/comments/", payload)
            created += 1
            if args.format != "json":
                print(f"  [{i}] Created comment {data.get('id')}")
        except Exception as e:
            errors.append(f"[{i}] {e}")

    if args.dry_run:
        print(f"\nDry run: {len(comments)} comments would be created on activity {args.activity_id}")
    else:
        print(f"\nBulk complete: {created} created, {len(errors)} errors")
    if errors:
        print("\nErrors:")
        for err in errors:
            print(f"  {err}")


# ═══════════════════════════════════════════════════════════════════════════════
# DEPENDENCIES
# ═══════════════════════════════════════════════════════════════════════════════


def cmd_deps_list(client, args):
    params = {}
    if args.predecessor:
        params["predecessor"] = args.predecessor
    if args.successor:
        params["successor"] = args.successor
    if args.dep_type:
        params["dependency_type"] = args.dep_type
    data = client.get("dependencies/", params)
    if args.format == "json":
        out_json(data)
        return
    results = data.get("results", data) if isinstance(data, dict) else data
    rows = [{"id": str(d.get("id", "")), "pred": str(d.get("predecessor", "")),
             "type": d.get("dependency_type", ""), "succ": str(d.get("successor", "")),
             "lag": str(d.get("lag_days", 0)), "notes": str(d.get("notes", ""))[:35]}
            for d in results]
    print(f"Dependencies: {len(rows)}\n")
    out_table(rows, ["id", "pred", "type", "succ", "lag", "notes"])


def cmd_deps_create(client, args):
    payload = {"predecessor": args.predecessor, "successor": args.successor, "dependency_type": args.dep_type}
    if args.lag is not None:
        payload["lag_days"] = args.lag
    if args.notes:
        payload["notes"] = args.notes
    if args.dry_run:
        print(f"Would CREATE dependency: {payload}")
        return
    data = client.post("dependencies/", payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Created dependency {data.get('id')}: #{data.get('predecessor')} {data.get('dependency_type')} -> #{data.get('successor')}")


def cmd_deps_delete(client, args):
    if args.dry_run:
        print(f"Would DELETE dependency {args.dep_id}")
        return
    client.delete(f"dependencies/{args.dep_id}/")
    print(f"Deleted dependency {args.dep_id}")


# ═══════════════════════════════════════════════════════════════════════════════
# TAG-FILTER LINKS (saved tag queries attached to an activity)
# ═══════════════════════════════════════════════════════════════════════════════
# A single link that stands for a *set* of files matched by tags — the AND/OR
# combination the plain generic-link can't express (issue #1481). "Pay apps for
# Yates" = tags [pay_app, yates] in `all` mode. The set is dynamic: it resolves
# live to whatever files carry the tags (see `files list --tags <...> --tags-mode
# <...>`), and the web app renders each link as a chip that deep-links into the
# Files tab with the filter pre-applied. Backed by the nested endpoint
# `activities/{id}/tag-filter-links/` (ActivityTagFilterLink).

# Server caps a link at 20 tags (ActivityTagFilterLinkSerializer.validate_tags);
# mirror it so we fail fast with a clear message instead of a 400.
MAX_TAG_FILTER_TAGS = 20
# any = files with at least one of the tags (OR); all = files carrying every tag (AND).
TAG_FILTER_MODES = ("any", "all")


def _tag_filter_join(tags, mode):
    """Human label for a tag set: ``a + b`` for AND, ``a, b`` for OR — matching
    the server's ``display_label`` so CLI output reads like the web chip."""
    return (" + " if mode == "all" else ", ").join(tags)


def cmd_tag_filters_list(client, args):
    """List the tag-filter links (saved tag queries) on an activity."""
    data = client.get(f"activities/{args.activity_id}/tag-filter-links/")
    if args.format == "json":
        out_json(data)
        return
    results = data.get("results", data) if isinstance(data, dict) else data
    rows = []
    for link in results:
        tags = link.get("tags") or []
        mode = link.get("tags_mode", "any")
        rows.append({
            "id": str(link.get("id", "")),
            "mode": mode,
            "tags": _tag_filter_join([str(t) for t in tags], mode)[:50],
            "label": str(link.get("label") or link.get("display_label") or "")[:30],
            "created": str(link.get("created_at", ""))[:10],
        })
    print(f"Tag-filter links for activity {args.activity_id}: {len(rows)}\n")
    out_table(rows, ["id", "mode", "tags", "label", "created"])


def cmd_tag_filters_add(client, args):
    """Attach a saved tag query (evidence set) to an activity.

    ``--mode all`` requires every tag (AND) — e.g. pay_app AND yates; ``--mode
    any`` (default) matches any of them (OR).
    """
    tags = [t.strip() for t in args.tags.split(",") if t.strip()]
    if not tags:
        print("Provide at least one tag via --tags (comma-separated).", file=sys.stderr)
        sys.exit(1)
    if len(tags) > MAX_TAG_FILTER_TAGS:
        print(f"Too many tags ({len(tags)}); a tag-filter link allows at most "
              f"{MAX_TAG_FILTER_TAGS}.", file=sys.stderr)
        sys.exit(1)
    payload = {"tags": tags, "tags_mode": args.mode}
    if args.label:
        payload["label"] = args.label
    if args.dry_run:
        print(f"Would CREATE tag-filter link on activity {args.activity_id}: {payload}")
        return
    data = client.post(f"activities/{args.activity_id}/tag-filter-links/", payload)
    if args.format == "json":
        out_json(data)
        return
    mode = data.get("tags_mode", args.mode)
    saved_tags = [str(t) for t in (data.get("tags") or tags)]
    op = "AND" if mode == "all" else "OR"
    print(f"Created tag-filter link {data.get('id')} on activity {args.activity_id}: "
          f"{_tag_filter_join(saved_tags, mode)} [{op}]")
    # A directly runnable command that lists the set this link resolves to
    # (real --tags-mode value, not the AND/OR label).
    print(f"  -> reproduce: pcxa files list --tags {','.join(saved_tags)} --tags-mode {mode}")


def cmd_tag_filters_delete(client, args):
    """Remove a tag-filter link from an activity."""
    if args.dry_run:
        print(f"Would DELETE tag-filter link {args.link_id} from activity {args.activity_id}")
        return
    client.delete(f"activities/{args.activity_id}/tag-filter-links/{args.link_id}/")
    print(f"Deleted tag-filter link {args.link_id} from activity {args.activity_id}")


# ═══════════════════════════════════════════════════════════════════════════════
# GANTT / TREE
# ═══════════════════════════════════════════════════════════════════════════════


def cmd_gantt(client, args):
    params = {}
    if args.status:
        params["status"] = args.status
    data = client.get("activities/gantt_data/", params)
    if args.format == "json":
        out_json(data)
        return
    results = data.get("results", data) if isinstance(data, dict) else data
    rows = [{"id": str(a.get("id", "")), "title": str(a.get("title", ""))[:35],
             "start": str(a.get("planned_start") or "")[:10], "finish": str(a.get("planned_finish") or "")[:10],
             "pct": f"{a.get('percent_complete', a.get('progress', 0))}%",
             "critical": "YES" if a.get("is_critical") else "", "float": str(a.get("total_float", ""))[:5]}
            for a in results]
    print(f"Gantt: {len(rows)} activities\n")
    out_table(rows, ["id", "title", "start", "finish", "pct", "critical", "float"])


def cmd_tree(client, args):
    params = {"view": "tree"}
    if args.max_depth:
        params["max_depth"] = args.max_depth
    if args.status:
        params["status"] = args.status
    data = client.get("activities/", params)
    if args.format == "json":
        out_json(data)
        return
    results = data.get("results", data) if isinstance(data, dict) else data

    def print_node(node, depth=0):
        indent = "  " * depth
        prefix = "|- " if depth > 0 else ""
        print(f"{indent}{prefix}[{node.get('id')}] {node.get('title', '?')} ({node.get('status', '')}, {node.get('percent_complete', 0)}%)")
        for child in node.get("children", []):
            print_node(child, depth + 1)

    for node in results:
        print_node(node)
