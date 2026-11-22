"""Photos, drawings and comment threads on files/photos/submissions/drawings.

Web UI pages ``project/photos`` and ``project/drawings``, plus the comment
panel shown on a file, photo, form submission or drawing. All project-scoped:

- ``photos``    -> ``photos/`` (+ nested ``layers/`` and ``markups/``)
- ``drawings``  -> ``drawings/`` (+ nested ``revisions/``) and the
                   ``source-documents/`` a drawing is extracted from
- ``threads``   -> ``{files|photos|form-submissions|drawings}/{id}/comments/``

``threads`` is one generic group rather than four: the backend serves all four
targets from the same ``BaseCommentViewSet`` and only the parent path differs.
(``comments`` is already the activity-comment group.)
"""

import json
import sys
from pathlib import Path

from pcxa._output import out_json, out_table, tag_names
from pcxa.commands.files import _upload_via_presign


def _bool(value):
    return value.lower() in ("true", "1", "yes")


def _json_flag(value, flag):
    """Parse a JSON-valued flag; clean stderr error + exit 1 on bad JSON."""
    try:
        return json.loads(value)
    except json.JSONDecodeError as e:
        print(f"{flag} is not valid JSON: {e}", file=sys.stderr)
        sys.exit(1)


def _merge_data(payload, args):
    """Merge the ``--data`` escape hatch last, over the typed flags."""
    if getattr(args, "data", None):
        extra = _json_flag(args.data, "--data")
        if not isinstance(extra, dict):
            print("--data must be a JSON object.", file=sys.stderr)
            sys.exit(1)
        payload.update(extra)
    return payload


def _results(data):
    return data.get("results", data) if isinstance(data, dict) else data


def _csv(value):
    return [t.strip() for t in value.split(",") if t.strip()]


def _print_or_json(args, data, message):
    if args.format == "json":
        out_json(data)
    else:
        print(message)


# ═══════════════════════════════════════════════════════════════════════════════
# PHOTOS
# ═══════════════════════════════════════════════════════════════════════════════


def _photo_row(p):
    return {
        "id": str(p.get("id", "")),
        "date": str(p.get("photo_date") or "-"),
        "category": str(p.get("category") or "")[:20],
        "description": str(p.get("description") or "")[:40],
        "location": str(p.get("location_name") or "")[:25],
        "tags": tag_names(p.get("tags"))[:30],
        "status": str(p.get("processing_status") or ""),
    }


def cmd_photos_list(client, args):
    """List photos with the photo grid's filters."""
    params = client.paginate_params(args.limit, args.offset)
    for key, value in (
        ("search", args.search),
        ("category", args.category),
        ("tags", args.tags),
        ("tags_mode", args.tags_mode),
        ("photo_date", args.date),
        ("uploaded_at", args.uploaded),
        ("processing_status", args.status),
        ("camera_make", args.camera_make),
        ("ordering", args.sort),
    ):
        if value:
            params[key] = value
    if args.archived is not None:
        params["is_archived"] = str(args.archived).lower()

    data = client.get("photos/", params)
    if args.format == "json":
        out_json(data)
        return
    rows = [_photo_row(p) for p in _results(data)]
    # The photo list is paginated count-less (count: null); the exact total is
    # a separate photos/exact-count/ call, so only print it when we have it.
    total = data.get("count") if isinstance(data, dict) else None
    print(f"Photos: {len(rows)}" + (f" of {total}" if total is not None else "") + "\n")
    out_table(rows, ["id", "date", "category", "description", "location", "tags", "status"])


def cmd_photos_get(client, args):
    """Photo detail."""
    data = client.get(f"photos/{args.photo_id}/")
    if args.format == "json":
        out_json(data)
        return
    print(f"Photo {data.get('id')}: {data.get('description') or '-'}")
    print(f"  Date:      {data.get('photo_date') or '-'} ({data.get('photo_date_source') or '-'})")
    print(f"  Category:  {data.get('category') or '-'}")
    print(f"  Location:  {data.get('location_name') or '-'} "
          f"({data.get('latitude')}, {data.get('longitude')})")
    print(f"  Tags:      {tag_names(data.get('tags')) or '-'}")
    print(f"  Status:    {data.get('processing_status') or '-'}")
    print(f"  Comments:  {data.get('comments_count', 0)}")
    print()


def _photo_payload(args):
    payload = {}
    if args.description is not None:
        payload["description"] = args.description
    if args.category is not None:
        payload["category"] = args.category
    if args.location is not None:
        payload["location_name"] = args.location
    if args.tags is not None:
        payload["tags"] = _csv(args.tags)
    return payload


def cmd_photos_update(client, args):
    """Update a photo's metadata (PATCH; --tags replaces the tag set)."""
    payload = _photo_payload(args)
    if args.lat is not None:
        payload["latitude"] = args.lat
    if args.lng is not None:
        payload["longitude"] = args.lng
    if args.altitude is not None:
        payload["altitude"] = args.altitude
    if args.date is not None:
        payload["photo_date"] = args.date or None
    _merge_data(payload, args)
    if not payload:
        print("No fields to update.", file=sys.stderr)
        sys.exit(1)
    if args.dry_run:
        print(f"Would UPDATE photo {args.photo_id}: {json.dumps(payload, indent=2)}")
        return
    data = client.patch(f"photos/{args.photo_id}/", payload)
    _print_or_json(args, data, f"Updated photo {data.get('id')}")


def cmd_photos_delete(client, args):
    """Delete a photo (soft delete)."""
    if args.dry_run:
        print(f"Would DELETE photo {args.photo_id}")
        return
    client.delete(f"photos/{args.photo_id}/")
    print(f"Deleted photo {args.photo_id}")


def cmd_photos_upload(client, args):
    """Upload a local image as a photo: presign -> PUT to storage -> POST photos/.

    Reuses the files presign helper (same three steps, bytes never transit the
    API origin). The create POST cannot carry tags (PhotoSerializer.create
    builds the row without popping them), so any metadata is applied with one
    PATCH after the photo exists.
    """
    fp = Path(args.path)
    if not fp.is_file():
        print(f"File not found: {fp}", file=sys.stderr)
        sys.exit(1)
    meta = _photo_payload(args)
    if args.dry_run:
        print(f"Would UPLOAD photo {fp.name} ({fp.stat().st_size:,} bytes)"
              + (f" then UPDATE: {json.dumps(meta, indent=2)}" if meta else ""))
        return
    data = _upload_via_presign(
        client, fp, None, None, None,
        client._url("photos/presign-upload/"), client._url("photos/"),
    )
    if meta:
        data = client.patch(f"photos/{data['id']}/", meta)
    _print_or_json(args, data, f"Uploaded photo {data.get('id')}: {fp.name}")


# ── photo layers / markups (photos/{photo}/layers|markups/) ──


def cmd_photo_layers_list(client, args):
    data = client.get(f"photos/{args.photo_id}/layers/")
    if args.format == "json":
        out_json(data)
        return
    rows = [{
        "id": str(lyr.get("id", "")),
        "order": str(lyr.get("order", "")),
        "name": str(lyr.get("name", ""))[:35],
        "default": "yes" if lyr.get("is_default") else "",
        "visible": "yes" if lyr.get("is_visible") else "",
        "markups": str(lyr.get("markups_count", "")),
    } for lyr in _results(data)]
    print(f"Layers on photo {args.photo_id}: {len(rows)}\n")
    out_table(rows, ["id", "order", "name", "default", "visible", "markups"])


def _layer_payload(args):
    payload = {}
    if args.name is not None:
        payload["name"] = args.name
    if args.order is not None:
        payload["order"] = args.order
    if args.visible is not None:
        payload["is_visible"] = args.visible
    return payload


def cmd_photo_layers_create(client, args):
    payload = _layer_payload(args)
    if args.dry_run:
        print(f"Would CREATE layer on photo {args.photo_id}: {json.dumps(payload, indent=2)}")
        return
    data = client.post(f"photos/{args.photo_id}/layers/", payload)
    _print_or_json(args, data, f"Created layer {data.get('id')}: '{data.get('name')}'")


def cmd_photo_layers_update(client, args):
    payload = _layer_payload(args)
    if not payload:
        print("No fields to update.", file=sys.stderr)
        sys.exit(1)
    if args.dry_run:
        print(f"Would UPDATE layer {args.layer_id}: {json.dumps(payload, indent=2)}")
        return
    data = client.patch(f"photos/{args.photo_id}/layers/{args.layer_id}/", payload)
    _print_or_json(args, data, f"Updated layer {data.get('id')}")


def cmd_photo_layers_delete(client, args):
    if args.dry_run:
        print(f"Would DELETE layer {args.layer_id} from photo {args.photo_id}")
        return
    client.delete(f"photos/{args.photo_id}/layers/{args.layer_id}/")
    print(f"Deleted layer {args.layer_id}")


def cmd_photo_markups_list(client, args):
    params = {}
    if args.layer:
        params["layer"] = args.layer
    if args.shape:
        params["shape_type"] = args.shape
    data = client.get(f"photos/{args.photo_id}/markups/", params)
    if args.format == "json":
        out_json(data)
        return
    rows = [{
        "id": str(m.get("id", "")),
        "layer": str(m.get("layer_name") or m.get("layer") or ""),
        "shape": str(m.get("shape_type", "")),
        "text": str(m.get("text") or "")[:40],
        "created": str(m.get("created_at", ""))[:16],
    } for m in _results(data)]
    print(f"Markups on photo {args.photo_id}: {len(rows)}\n")
    out_table(rows, ["id", "layer", "shape", "text", "created"])


def _markup_payload(args):
    payload = {}
    if args.layer is not None:
        payload["layer"] = args.layer
    if args.shape is not None:
        payload["shape_type"] = args.shape
    if args.coords is not None:
        payload["coords"] = _json_flag(args.coords, "--coords")
    if args.properties is not None:
        payload["properties"] = _json_flag(args.properties, "--properties")
    if args.text is not None:
        payload["text"] = args.text
    return _merge_data(payload, args)


def cmd_photo_markups_create(client, args):
    payload = _markup_payload(args)
    if args.dry_run:
        print(f"Would CREATE markup on photo {args.photo_id}: {json.dumps(payload, indent=2)}")
        return
    data = client.post(f"photos/{args.photo_id}/markups/", payload)
    _print_or_json(args, data, f"Created markup {data.get('id')} ({data.get('shape_type')})")


def cmd_photo_markups_update(client, args):
    payload = _markup_payload(args)
    if not payload:
        print("No fields to update.", file=sys.stderr)
        sys.exit(1)
    if args.dry_run:
        print(f"Would UPDATE markup {args.markup_id}: {json.dumps(payload, indent=2)}")
        return
    data = client.patch(f"photos/{args.photo_id}/markups/{args.markup_id}/", payload)
    _print_or_json(args, data, f"Updated markup {data.get('id')}")


def cmd_photo_markups_delete(client, args):
    if args.dry_run:
        print(f"Would DELETE markup {args.markup_id} from photo {args.photo_id}")
        return
    client.delete(f"photos/{args.photo_id}/markups/{args.markup_id}/")
    print(f"Deleted markup {args.markup_id}")


# ═══════════════════════════════════════════════════════════════════════════════
# DRAWINGS (drawing_v2)
# ═══════════════════════════════════════════════════════════════════════════════


def _drawing_row(d):
    return {
        "id": str(d.get("id", "")),
        "identifier": str(d.get("identifier") or "")[:20],
        "title": str(d.get("title") or "")[:35],
        "discipline": str(d.get("discipline") or ""),
        "type": str(d.get("drawing_type") or ""),
        "status": str(d.get("processing_status") or ""),
        "revs": str(d.get("revisions_count", "")),
    }


def cmd_drawings_list(client, args):
    """List drawings with the drawing register's filters."""
    params = client.paginate_params(args.limit, args.offset)
    for key, value in (
        ("search", args.search),
        ("discipline", args.discipline),
        ("building", args.building),
        ("floor", args.floor),
        ("drawing_type", args.type),
        ("processing_status", args.status),
        ("tags", args.tags),
        ("ordering", args.sort),
    ):
        if value:
            params[key] = value
    if args.archived is not None:
        params["is_archived"] = str(args.archived).lower()
    if args.plot_plan is not None:
        params["is_plot_plan"] = str(args.plot_plan).lower()

    data = client.get("drawings/", params)
    if args.format == "json":
        out_json(data)
        return
    results = _results(data)
    total = data.get("count", len(results)) if isinstance(data, dict) else len(results)
    rows = [_drawing_row(d) for d in results]
    print(f"Drawings: {len(rows)} of {total}\n")
    out_table(rows, ["id", "identifier", "title", "discipline", "type", "status", "revs"])


def cmd_drawings_get(client, args):
    """Drawing detail."""
    data = client.get(f"drawings/{args.drawing_id}/")
    if args.format == "json":
        out_json(data)
        return
    rev = data.get("current_revision") or {}
    print(f"Drawing {data.get('id')}: {data.get('identifier')} — {data.get('title')}")
    print(f"  Discipline: {data.get('discipline') or '-'}   Type: {data.get('drawing_type') or '-'}")
    print(f"  Building:   {data.get('building') or '-'}   Floor: {data.get('floor') or '-'}")
    print(f"  Status:     {data.get('processing_status') or '-'}")
    print(f"  Current:    revision {rev.get('revision_number', '-')} (id {rev.get('id', '-')}) "
          f"of {data.get('revisions_count', 0)}")
    print(f"  Tags:       {tag_names(data.get('tags')) or '-'}")
    print()


def cmd_drawings_update(client, args):
    """Update a drawing's register fields (PATCH; --tags replaces the tag set)."""
    payload = {}
    for key, value in (
        ("identifier", args.identifier),
        ("title", args.title),
        ("description", args.description),
        ("building", args.building),
        ("floor", args.floor),
        ("discipline", args.discipline),
        ("drawing_type", args.type),
    ):
        if value is not None:
            payload[key] = value
    if args.tags is not None:
        payload["tags"] = _csv(args.tags)
    _merge_data(payload, args)
    if not payload:
        print("No fields to update.", file=sys.stderr)
        sys.exit(1)
    if args.dry_run:
        print(f"Would UPDATE drawing {args.drawing_id}: {json.dumps(payload, indent=2)}")
        return
    data = client.patch(f"drawings/{args.drawing_id}/", payload)
    _print_or_json(args, data, f"Updated drawing {data.get('id')}: {data.get('identifier')}")


def cmd_drawings_delete(client, args):
    """Delete a drawing (soft delete)."""
    if args.dry_run:
        print(f"Would DELETE drawing {args.drawing_id}")
        return
    client.delete(f"drawings/{args.drawing_id}/")
    print(f"Deleted drawing {args.drawing_id}")


def cmd_drawings_status(client, args):
    """Processing status of a drawing (page extraction)."""
    out_json(client.get(f"drawings/{args.drawing_id}/processing-status/"))


def cmd_drawings_set_current(client, args):
    """Make a revision the drawing's current one."""
    payload = {"revision_id": args.revision}
    if args.dry_run:
        print(f"Would SET current revision of drawing {args.drawing_id}: {json.dumps(payload)}")
        return
    data = client.post(f"drawings/{args.drawing_id}/set_current_revision/", payload)
    _print_or_json(args, data, f"Drawing {args.drawing_id}: current revision is now {args.revision}")


# ── revisions (drawings/{drawing}/revisions/) ──


def cmd_drawing_revisions_list(client, args):
    # drawings/{id}/revisions/ resolves to the DrawingViewSet ``revisions``
    # action (registered first), which returns a bare list, not a page.
    data = client.get(f"drawings/{args.drawing_id}/revisions/")
    if args.format == "json":
        out_json(data)
        return
    rows = [{
        "id": str(r.get("id", "")),
        "rev": str(r.get("revision_number", "")),
        "mark": str(r.get("revision_mark") or ""),
        "current": "yes" if r.get("is_current") else "",
        "source": str(r.get("source_document_title") or "")[:30],
        "page": str(r.get("source_page_number", "")),
        "uploaded": str(r.get("uploaded_at", ""))[:16],
    } for r in _results(data)]
    print(f"Revisions of drawing {args.drawing_id}: {len(rows)}\n")
    out_table(rows, ["id", "rev", "mark", "current", "source", "page", "uploaded"])


def cmd_drawing_revisions_get(client, args):
    out_json(client.get(f"drawings/{args.drawing_id}/revisions/{args.revision_id}/"))


# ── source documents (source-documents/) ──


def cmd_drawing_sources_list(client, args):
    data = client.get("source-documents/", client.paginate_params(args.limit, args.offset))
    if args.format == "json":
        out_json(data)
        return
    results = _results(data)
    total = data.get("count", len(results)) if isinstance(data, dict) else len(results)
    rows = [{
        "id": str(s.get("id", "")),
        "title": str(s.get("title") or "")[:35],
        "file": str(s.get("file_name") or "")[:30],
        "pages": str(s.get("total_pages", "")),
        "drawings": str(s.get("extracted_drawings_count", "")),
        "status": str(s.get("processing_status") or ""),
    } for s in results]
    print(f"Source documents: {len(rows)} of {total}\n")
    out_table(rows, ["id", "title", "file", "pages", "drawings", "status"])


def cmd_drawing_sources_get(client, args):
    out_json(client.get(f"source-documents/{args.source_id}/"))


def cmd_drawing_sources_create(client, args):
    """Turn existing project files into source documents (files/convert_to_drawings/).

    The backend references each file's stored object (no copy) and queues
    page extraction; idempotent per file version (``already_imported``).
    """
    payload = {"file_ids": args.file_ids}
    if args.dry_run:
        print(f"Would CREATE source documents from files: {json.dumps(payload)}")
        return
    data = client.post("files/convert_to_drawings/", payload)
    if args.format == "json":
        out_json(data)
        return
    for c in data.get("conversions", []):
        note = " (already imported)" if c.get("already_imported") else ""
        print(f"  file {c.get('file_id')} -> source document {c.get('source_document_id')}{note}")
    for e in data.get("errors", []):
        print(f"  file {e.get('file_id')}: {e.get('error')}", file=sys.stderr)
    print(f"Queued {data.get('success_count', 0)}, failed {data.get('error_count', 0)}")


def cmd_drawing_sources_delete(client, args):
    if args.dry_run:
        print(f"Would DELETE source document {args.source_id}")
        return
    client.delete(f"source-documents/{args.source_id}/")
    print(f"Deleted source document {args.source_id}")


def cmd_drawing_sources_status(client, args):
    out_json(client.get(f"source-documents/{args.source_id}/processing-status/"))


def cmd_drawing_sources_retry(client, args):
    if args.dry_run:
        print(f"Would RETRY processing of source document {args.source_id}")
        return
    data = client.post(f"source-documents/{args.source_id}/retry-processing/", {})
    _print_or_json(args, data, f"Retry queued for source document {args.source_id}")


def cmd_drawing_sources_drawings(client, args):
    data = client.get(f"source-documents/{args.source_id}/extracted_drawings/")
    if args.format == "json":
        out_json(data)
        return
    rows = [_drawing_row(d) for d in _results(data)]
    print(f"Drawings from source document {args.source_id}: {len(rows)}\n")
    out_table(rows, ["id", "identifier", "title", "discipline", "type", "status", "revs"])


# ═══════════════════════════════════════════════════════════════════════════════
# THREADS — comments on a file, photo, form submission or drawing
# ═══════════════════════════════════════════════════════════════════════════════

_THREAD_PARENTS = {
    "file": "files",
    "photo": "photos",
    "submission": "form-submissions",
    "drawing": "drawings",
}


def _thread_path(args, suffix=""):
    return f"{_THREAD_PARENTS[args.target]}/{args.parent_id}/comments/{suffix}"


def cmd_threads_list(client, args):
    params = client.paginate_params(args.limit, args.offset)
    data = client.get(_thread_path(args), params)
    if args.format == "json":
        out_json(data)
        return
    rows = []
    for c in _results(data):
        user = c.get("user") or {}
        rows.append({
            "id": str(c.get("id", "")),
            "user": user.get("display_name") or user.get("username") or str(user.get("id", "")),
            "reply_to": str(c.get("parent_comment") or ""),
            "content": str(c.get("content", ""))[:60],
            "type": c.get("comment_type", ""),
            "created": str(c.get("created_at", ""))[:16],
        })
    print(f"Comments on {args.target} {args.parent_id}: {len(rows)}\n")
    out_table(rows, ["id", "user", "reply_to", "content", "type", "created"])


def cmd_threads_add(client, args):
    payload = {"content": args.content}
    if args.reply_to:
        payload["parent_comment"] = args.reply_to
    if args.mention:
        payload["mentioned_users"] = args.mention
    if args.dry_run:
        print(f"Would ADD comment on {args.target} {args.parent_id}: {json.dumps(payload)}")
        return
    data = client.post(_thread_path(args), payload)
    _print_or_json(args, data, f"Added comment {data.get('id')} on {args.target} {args.parent_id}")


def cmd_threads_update(client, args):
    payload = {"content": args.content}
    if args.dry_run:
        print(f"Would UPDATE comment {args.comment_id}: {json.dumps(payload)}")
        return
    data = client.patch(_thread_path(args, f"{args.comment_id}/"), payload)
    _print_or_json(args, data, f"Updated comment {data.get('id')}")


def cmd_threads_delete(client, args):
    if args.dry_run:
        print(f"Would DELETE comment {args.comment_id} on {args.target} {args.parent_id}")
        return
    client.delete(_thread_path(args, f"{args.comment_id}/"))
    print(f"Deleted comment {args.comment_id}")


# ═══════════════════════════════════════════════════════════════════════════════
# NESTED DISPATCH + PARSER
# ═══════════════════════════════════════════════════════════════════════════════

_NESTED = {
    "photos_layers_command": {
        "list": cmd_photo_layers_list,
        "create": cmd_photo_layers_create,
        "update": cmd_photo_layers_update,
        "delete": cmd_photo_layers_delete,
    },
    "photos_markups_command": {
        "list": cmd_photo_markups_list,
        "create": cmd_photo_markups_create,
        "update": cmd_photo_markups_update,
        "delete": cmd_photo_markups_delete,
    },
    "drawings_revisions_command": {
        "list": cmd_drawing_revisions_list,
        "get": cmd_drawing_revisions_get,
    },
    "drawings_sources_command": {
        "list": cmd_drawing_sources_list,
        "get": cmd_drawing_sources_get,
        "create": cmd_drawing_sources_create,
        "delete": cmd_drawing_sources_delete,
        "status": cmd_drawing_sources_status,
        "retry": cmd_drawing_sources_retry,
        "drawings": cmd_drawing_sources_drawings,
    },
}


def _nested(key, usage):
    """Build a dispatcher for a third-level group (e.g. ``photos layers list``)."""
    def dispatch(client, args):
        fn = _NESTED[key].get(getattr(args, key, None))
        if fn is None:
            print(f"Usage: pcxa {usage} {{{', '.join(_NESTED[key])}}}", file=sys.stderr)
            sys.exit(1)
        return fn(client, args)
    return dispatch


def _add_page(p, limit=25):
    p.add_argument("--limit", type=int, default=limit)
    p.add_argument("--offset", type=int, default=0)


def _add_photo_meta(p):
    p.add_argument("--description")
    p.add_argument("--category")
    p.add_argument("--location", help="location_name")
    p.add_argument("--tags", help="Comma-separated tag names (replaces the photo's tags)")


def register_parser(sub):
    # ── photos ──
    ph = sub.add_parser("photos", help="Project photos: list/get/update/delete/upload, layers, markups")
    ph_sub = ph.add_subparsers(dest="photos_command")

    p = ph_sub.add_parser("list", help="List photos")
    p.add_argument("--search", "-s", help="Description, location, camera")
    p.add_argument("--category", help="Comma-separated categories")
    p.add_argument("--tags", help="Comma-separated tag names (OR; --tags-mode all for AND)")
    p.add_argument("--tags-mode", choices=["any", "all"])
    p.add_argument("--date", help="photo_date: YYYY, YYYY-MM, YYYY-MM-DD, today, this_week, "
                                  "this_month or unknown")
    p.add_argument("--uploaded", help="uploaded_at: same forms as --date")
    p.add_argument("--status", help="processing_status")
    p.add_argument("--camera-make")
    p.add_argument("--archived", type=_bool, metavar="BOOL")
    p.add_argument("--sort", help="photo_date, uploaded_at, capture_time, category (prefix - for desc)")
    _add_page(p)

    p = ph_sub.add_parser("get", help="Photo detail")
    p.add_argument("photo_id", type=int)

    p = ph_sub.add_parser("update", help="Update photo metadata")
    p.add_argument("photo_id", type=int)
    _add_photo_meta(p)
    p.add_argument("--lat", type=float)
    p.add_argument("--lng", type=float)
    p.add_argument("--altitude", type=float)
    p.add_argument("--date", help="photo_date YYYY-MM-DD (empty string clears)")
    p.add_argument("--data", help="Extra fields as a JSON object, merged last")

    p = ph_sub.add_parser("delete", help="Delete photo")
    p.add_argument("photo_id", type=int)

    p = ph_sub.add_parser("upload", help="Upload a local image as a photo")
    p.add_argument("path")
    _add_photo_meta(p)

    for name, key in (("layers", "photos_layers_command"), ("markups", "photos_markups_command")):
        g = ph_sub.add_parser(name, help=f"Photo {name}")
        g_sub = g.add_subparsers(dest=key)
        p = g_sub.add_parser("list")
        p.add_argument("photo_id", type=int)
        if name == "markups":
            p.add_argument("--layer", type=int)
            p.add_argument("--shape", help="shape_type")
        p = g_sub.add_parser("create")
        p.add_argument("photo_id", type=int)
        _add_layer_or_markup_fields(p, name, create=True)
        p = g_sub.add_parser("update")
        p.add_argument("photo_id", type=int)
        p.add_argument("layer_id" if name == "layers" else "markup_id", type=int)
        _add_layer_or_markup_fields(p, name, create=False)
        p = g_sub.add_parser("delete")
        p.add_argument("photo_id", type=int)
        p.add_argument("layer_id" if name == "layers" else "markup_id", type=int)

    # ── drawings ──
    dr = sub.add_parser("drawings", help="Drawings (v2): register, revisions, source documents")
    dr_sub = dr.add_subparsers(dest="drawings_command")

    p = dr_sub.add_parser("list", help="List drawings")
    p.add_argument("--search", "-s", help="Identifier, title, description")
    p.add_argument("--discipline")
    p.add_argument("--building")
    p.add_argument("--floor")
    p.add_argument("--type", help="drawing_type")
    p.add_argument("--status", help="processing_status")
    p.add_argument("--tags", help="Comma-separated tag names")
    p.add_argument("--archived", type=_bool, metavar="BOOL")
    p.add_argument("--plot-plan", type=_bool, metavar="BOOL")
    p.add_argument("--sort", help="identifier, title, created_at, updated_at (prefix - for desc)")
    _add_page(p)

    p = dr_sub.add_parser("get", help="Drawing detail")
    p.add_argument("drawing_id", type=int)

    p = dr_sub.add_parser("update", help="Update drawing fields")
    p.add_argument("drawing_id", type=int)
    for flag in ("--identifier", "--title", "--description", "--building", "--floor",
                 "--discipline"):
        p.add_argument(flag)
    p.add_argument("--type", help="drawing_type")
    p.add_argument("--tags", help="Comma-separated tag names (replaces the drawing's tags)")
    p.add_argument("--data", help="Extra fields as a JSON object, merged last")

    p = dr_sub.add_parser("delete", help="Delete drawing")
    p.add_argument("drawing_id", type=int)

    p = dr_sub.add_parser("status", help="Drawing processing status")
    p.add_argument("drawing_id", type=int)

    p = dr_sub.add_parser("set-current", help="Make a revision the drawing's current one")
    p.add_argument("drawing_id", type=int)
    p.add_argument("--revision", type=int, required=True, help="Revision id")

    g = dr_sub.add_parser("revisions", help="Drawing revisions (read-only)")
    g_sub = g.add_subparsers(dest="drawings_revisions_command")
    p = g_sub.add_parser("list")
    p.add_argument("drawing_id", type=int)
    p = g_sub.add_parser("get")
    p.add_argument("drawing_id", type=int)
    p.add_argument("revision_id", type=int)

    g = dr_sub.add_parser("sources", help="Source documents drawings are extracted from")
    g_sub = g.add_subparsers(dest="drawings_sources_command")
    p = g_sub.add_parser("list")
    _add_page(p)
    p = g_sub.add_parser("create", help="Convert existing project files into source documents")
    p.add_argument("--file-id", dest="file_ids", type=int, nargs="+", required=True)
    for name in ("get", "delete", "status", "retry", "drawings"):
        p = g_sub.add_parser(name)
        p.add_argument("source_id", type=int)

    # ── threads ──
    th = sub.add_parser("threads",
                        help="Comments on a file, photo, form submission or drawing")
    th_sub = th.add_subparsers(dest="threads_command")
    for name in ("list", "add", "update", "delete"):
        p = th_sub.add_parser(name)
        p.add_argument("--target", "-t", required=True, choices=sorted(_THREAD_PARENTS))
        p.add_argument("parent_id", type=int, help="Id of the file/photo/submission/drawing")
        if name in ("update", "delete"):
            p.add_argument("comment_id", type=int)
        if name in ("add", "update"):
            p.add_argument("--content", "-c", required=True)
        if name == "add":
            p.add_argument("--reply-to", type=int, help="Parent comment id")
            p.add_argument("--mention", type=int, nargs="+", help="User ids to mention")
        if name == "list":
            _add_page(p, limit=50)


def _add_layer_or_markup_fields(p, name, create):
    if name == "layers":
        p.add_argument("--name", required=create)
        p.add_argument("--order", type=int)
        p.add_argument("--visible", type=_bool, metavar="BOOL")
        return
    p.add_argument("--layer", type=int, required=create, help="Photo layer id")
    p.add_argument("--shape", required=create,
                   help="shape_type: rect circle ellipse line polyline polygon path text "
                        "pin freehand measurement arrow")
    p.add_argument("--coords", required=create, help="Coordinates JSON")
    p.add_argument("--properties", help="Style properties JSON")
    p.add_argument("--text")
    p.add_argument("--data", help="Extra fields as a JSON object, merged last")


SUB_HANDLERS = {
    "photos": {
        "list": cmd_photos_list,
        "get": cmd_photos_get,
        "update": cmd_photos_update,
        "delete": cmd_photos_delete,
        "upload": cmd_photos_upload,
        "layers": _nested("photos_layers_command", "photos layers"),
        "markups": _nested("photos_markups_command", "photos markups"),
    },
    "drawings": {
        "list": cmd_drawings_list,
        "get": cmd_drawings_get,
        "update": cmd_drawings_update,
        "delete": cmd_drawings_delete,
        "status": cmd_drawings_status,
        "set-current": cmd_drawings_set_current,
        "revisions": _nested("drawings_revisions_command", "drawings revisions"),
        "sources": _nested("drawings_sources_command", "drawings sources"),
    },
    "threads": {
        "list": cmd_threads_list,
        "add": cmd_threads_add,
        "update": cmd_threads_update,
        "delete": cmd_threads_delete,
    },
}

SUB_COMMAND_KEYS = {
    "photos": "photos_command",
    "drawings": "drawings_command",
    "threads": "threads_command",
}

COMPANY_ONLY = set()
