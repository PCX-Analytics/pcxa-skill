"""Generic link commands (cross-object linking, not project-scoped)."""

import json
import sys
from pathlib import Path

from pcxa._output import out_json, out_table
from pcxa._resolve import parse_object_ref, links_url


def cmd_links_list(client, args):
    """List links filtered by source or target object."""
    params = {}
    if args.source:
        src_type, src_id = parse_object_ref(args.source)
        params["source_type"] = src_type
        params["source_object_id"] = src_id
    if args.target:
        tgt_type, tgt_id = parse_object_ref(args.target)
        params["target_type"] = tgt_type
        params["target_object_id"] = tgt_id
    if args.project_id:
        params["project_id"] = args.project_id
    else:
        # Default to current project
        params["project_id"] = client.project_id

    data = client.get_raw(links_url(client), params=params)
    if args.format == "json":
        out_json(data)
        return
    results = data.get("results", data) if isinstance(data, dict) else data
    rows = []
    for lnk in results:
        src = lnk.get("source_object") or {}
        tgt = lnk.get("target_object") or {}
        rows.append({
            "id": str(lnk.get("id", "")),
            "source": f"{src.get('type', '?')}:{src.get('id', '?')}",
            "source_name": str(src.get("title") or src.get("name", ""))[:30],
            "target": f"{tgt.get('type', '?')}:{tgt.get('id', '?')}",
            "target_name": str(tgt.get("title") or tgt.get("name", ""))[:30],
            "description": str(lnk.get("description") or "")[:35],
        })
    print(f"Links: {len(rows)}\n")
    out_table(rows, ["id", "source", "source_name", "target", "target_name", "description"])


def cmd_links_create(client, args):
    """Create a single link between two objects."""
    src_type, src_id = parse_object_ref(args.source)
    tgt_type, tgt_id = parse_object_ref(args.target)
    description = args.description or args.link_type or ""
    payload = {
        "source_type": src_type,
        "source_id": src_id,
        "target_type": tgt_type,
        "target_id": tgt_id,
    }
    if description:
        payload["description"] = description
    if args.dry_run:
        print(f"Would CREATE link: {payload}")
        return
    resp = client._request("POST", links_url(client, "create-attachment/"), json=payload)
    data = resp.json()
    if args.format == "json":
        out_json(data)
    else:
        print(f"Created link {data.get('id')}: {src_type}:{src_id} -> {tgt_type}:{tgt_id} ({description})")


def cmd_links_delete(client, args):
    """Delete a link by ID."""
    if args.dry_run:
        print(f"Would DELETE link {args.link_id}")
        return
    client._request("DELETE", links_url(client, f"{args.link_id}/"))
    print(f"Deleted link {args.link_id}")


def cmd_links_bulk(client, args):
    """Bulk create links from a JSON file."""
    file_path = Path(args.file)
    if not file_path.exists():
        print(f"File not found: {file_path}", file=sys.stderr)
        sys.exit(1)
    raw = json.loads(file_path.read_text())
    # Support both bare list and wrapper object with "links" key
    if isinstance(raw, dict):
        links = raw.get("links")
        if not isinstance(links, list):
            print("JSON object must contain a 'links' key with a list of link objects.", file=sys.stderr)
            sys.exit(1)
    elif isinstance(raw, list):
        links = raw
    else:
        print("JSON file must contain a list or an object with a 'links' key.", file=sys.stderr)
        sys.exit(1)

    created = 0
    errors = []
    for i, link in enumerate(links):
        # Support both "source": "file:123" shorthand and explicit "source_type"/"source_id" fields
        if "source" in link and isinstance(link["source"], str):
            src_type, src_id = parse_object_ref(link["source"])
        else:
            src_type = link.get("source_type")
            src_id = link.get("source_id")

        if "target" in link and isinstance(link["target"], str):
            tgt_type, tgt_id = parse_object_ref(link["target"])
        else:
            tgt_type = link.get("target_type")
            tgt_id = link.get("target_id")

        if not all([src_type, src_id, tgt_type, tgt_id]):
            errors.append(f"[{i}] Missing source/target fields")
            continue

        description = link.get("description") or link.get("type") or ""
        payload = {
            "source_type": src_type,
            "source_id": src_id,
            "target_type": tgt_type,
            "target_id": tgt_id,
        }
        if description:
            payload["description"] = description

        if args.dry_run:
            print(f"  [{i}] Would CREATE: {src_type}:{src_id} -> {tgt_type}:{tgt_id} ({description})")
            continue

        try:
            resp = client._request("POST", links_url(client, "create-attachment/"), json=payload)
            data = resp.json()
            created += 1
            if args.format != "json":
                print(f"  [{i}] Created link {data.get('id')}: {src_type}:{src_id} -> {tgt_type}:{tgt_id}")
        except Exception as e:
            errors.append(f"[{i}] {src_type}:{src_id} -> {tgt_type}:{tgt_id}: {e}")

    if args.dry_run:
        print(f"\nDry run: {len(links)} links would be created")
    else:
        print(f"\nBulk complete: {created} created, {len(errors)} errors")
    if errors:
        print("\nErrors:")
        for err in errors:
            print(f"  {err}")
