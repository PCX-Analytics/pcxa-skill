"""Tag management, folder navigation, and bulk file operation commands."""

import sys

from pcxa._http import requests
from pcxa._output import out_json, out_table
from pcxa.commands.files import _file_row


def cmd_tags_list(client, args):
    """List all tags with file counts."""
    data = client.get("files/value-counts/", {"column": "tags", "page_size": 200})
    if args.format == "json":
        out_json(data)
        return
    # Handle both formats: {value_counts: [...]} and {results: [...]}
    if isinstance(data, dict):
        results = data.get("value_counts") or data.get("results") or []
    else:
        results = data
    rows = []
    for item in results:
        if isinstance(item, dict):
            rows.append({"tag": item.get("value", ""), "files": str(item.get("count", 0))})
        else:
            rows.append({"tag": str(item), "files": "-"})
    rows.sort(key=lambda r: int(r["files"]) if r["files"] != "-" else 0, reverse=True)
    print(f"Tags: {len(rows)} unique\n")
    out_table(rows, ["tag", "files"])


def cmd_tags_add(client, args):
    """Add tags to files."""
    tags = [t.strip() for t in args.tags.split(",") if t.strip()]
    if args.dry_run:
        print(f"Would ADD tags {tags} to files {args.file_ids}")
        return
    data = client.bulk_call("files/bulk_update/", "file_ids", args.file_ids,
                            {"tags": tags, "tag_mode": "add"})
    if args.format == "json":
        out_json(data)
    else:
        print(f"Added tags {tags} to {data.get('success_count', 0)} files")


def cmd_tags_remove(client, args):
    """Remove tags from files."""
    tags = [t.strip() for t in args.tags.split(",") if t.strip()]
    if args.dry_run:
        print(f"Would REMOVE tags {tags} from files {args.file_ids}")
        return
    data = client.bulk_call("files/bulk_update/", "file_ids", args.file_ids,
                            {"tags": tags, "tag_mode": "remove"})
    if args.format == "json":
        out_json(data)
    else:
        print(f"Removed tags {tags} from {data.get('success_count', 0)} files")


def cmd_tags_set(client, args):
    """Replace all tags on files."""
    tags = [t.strip() for t in args.tags.split(",") if t.strip()]
    if args.dry_run:
        print(f"Would SET tags to {tags} on files {args.file_ids}")
        return
    data = client.bulk_call("files/bulk_update/", "file_ids", args.file_ids,
                            {"tags": tags, "tag_mode": "set"})
    if args.format == "json":
        out_json(data)
    else:
        print(f"Set tags {tags} on {data.get('success_count', 0)} files")


def cmd_folders_tree(client, args):
    """Folder hierarchy."""
    try:
        data = client.get("folders/folder_tree/")
    except requests.HTTPError:
        data = client.get_all_pages("folders/")

    if args.format == "json":
        out_json(data)
        return

    folders = data if isinstance(data, list) else data.get("results", [])
    if folders and "subfolders" in folders[0]:
        _print_tree_nested(folders, args.depth)
    else:
        _print_tree_flat(folders, args.depth)


def _print_tree_nested(nodes, max_depth, depth=0):
    for f in sorted(nodes, key=lambda x: x.get("name", "")):
        if max_depth is not None and depth > max_depth:
            return
        indent = "  " * depth
        prefix = "|- " if depth > 0 else ""
        fc = f.get("file_count", 0)
        rfc = f.get("recursive_file_count", "")
        count_str = f"({fc} files"
        if rfc and rfc != fc:
            count_str += f", {rfc} recursive"
        count_str += ")"
        print(f"{indent}{prefix}{f['name']}/  [id={f['id']}]  {count_str}")
        for sub in f.get("subfolders", []):
            _print_tree_nested([sub], max_depth, depth + 1)


def _print_tree_flat(folders, max_depth):
    by_parent = {}
    by_id = {}
    for f in folders:
        by_id[f["id"]] = f
        by_parent.setdefault(f.get("parent"), []).append(f)

    def print_node(fid, depth=0):
        if max_depth is not None and depth > max_depth:
            return
        f = by_id[fid]
        indent = "  " * depth
        prefix = "|- " if depth > 0 else ""
        fc = f.get("file_count", 0)
        print(f"{indent}{prefix}{f['name']}/  [id={f['id']}]  ({fc} files)")
        for child in sorted(by_parent.get(fid, []), key=lambda x: x["name"]):
            print_node(child["id"], depth + 1)

    roots = sorted(by_parent.get(None, []), key=lambda x: x["name"])
    print(f"Folders: {len(folders)} total\n")
    for r in roots:
        print_node(r["id"])


def cmd_folders_create(client, args):
    """Create a folder."""
    payload = {"name": args.name}
    if args.parent:
        payload["parent"] = args.parent
    if args.description:
        payload["description"] = args.description
    if args.dry_run:
        print(f"Would CREATE folder: {payload}")
        return
    data = client.post("folders/", payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Created folder '{data.get('name')}' (id={data.get('id')})")


def cmd_folders_rename(client, args):
    """Rename a folder."""
    if args.dry_run:
        print(f"Would RENAME folder {args.folder_id} to '{args.name}'")
        return
    data = client.patch(f"folders/{args.folder_id}/", {"name": args.name})
    if args.format == "json":
        out_json(data)
    else:
        print(f"Renamed folder {args.folder_id} to '{data.get('name')}'")


def cmd_folders_move(client, args):
    """Move a folder."""
    if args.dry_run:
        target = f"folder {args.parent}" if args.parent else "root"
        print(f"Would MOVE folder {args.folder_id} to {target}")
        return
    data = client.post(f"folders/{args.folder_id}/move/", {"parent_id": args.parent})
    if args.format == "json":
        out_json(data)
    else:
        print(f"Moved folder {args.folder_id}")


def cmd_folders_delete(client, args):
    """Delete folder and all contents."""
    if args.dry_run:
        print(f"Would DELETE folder {args.folder_id} and all contents")
        return
    data = client.delete(f"folders/{args.folder_id}/delete_with_contents/")
    if args.format == "json":
        out_json(data)
    else:
        print(f"Deleted folder {args.folder_id} and all contents")


def cmd_folders_subfolders(client, args):
    """Lightweight subfolder listing for path resolution.

    Hits POST /folders/{id}/subfolders/ which returns only [{id, name}] and
    is paginated. Walks all pages and returns a single JSON array. This is
    much faster than `folders contents` on large folders because the backend
    skips the file_count / subfolder_count Cartesian-product COUNT joins.
    """
    timeout = getattr(args, "timeout", None) or 180
    page_size = getattr(args, "page_size", None) or 1000
    page = 1
    out = []
    while True:
        resp = client._request(
            "GET",
            client._url(f"folders/{args.folder_id}/subfolders/"),
            params={"page": page, "page_size": page_size},
            timeout=timeout,
        )
        data = resp.json()
        results = data.get("results") if isinstance(data, dict) else data
        if results is None:
            results = []
        for s in results:
            sid = s.get("id")
            name = s.get("name")
            if sid is not None and name is not None:
                out.append({"id": int(sid), "name": str(name)})
        # DRF paginators expose `next` as the next URL or null.
        next_url = data.get("next") if isinstance(data, dict) else None
        if not next_url:
            break
        page += 1
    out_json({"subfolders": out, "count": len(out)})


def cmd_folders_contents(client, args):
    """Show folder contents."""
    # Large folders (e.g. project-4 ProjectSight Documents/QA-QC, RFI, Submittal)
    # can take 30-90s server-side. Override the 30s default to give the backend
    # room to respond for legitimately large enumerations.
    timeout = getattr(args, "timeout", None) or 180
    resp = client._request(
        "GET",
        client._url(f"folders/{args.folder_id}/contents/"),
        timeout=timeout,
    )
    data = resp.json()
    if args.format == "json":
        out_json(data)
        return
    folder = data.get("folder", data)
    print(f"Folder: {folder.get('name', '?')} (id={folder.get('id', args.folder_id)})\n")
    subs = data.get("subfolders", [])
    if subs:
        print(f"Subfolders ({len(subs)}):")
        for s in subs:
            print(f"  {s['name']}/  [id={s['id']}]  ({s.get('file_count', 0)} files)")
        print()
    files = data.get("files", [])
    if files:
        rows = [_file_row(f) for f in files]
        print(f"Files ({len(files)}):")
        out_table(rows, ["id", "title", "type", "size", "tags"])


def cmd_move(client, args):
    """Move files to a folder."""
    if args.dry_run:
        target = f"folder {args.folder}" if args.folder else "root"
        print(f"Would MOVE {len(args.file_ids)} files to {target}")
        return
    data = client.bulk_call("files/bulk_move/", "file_ids", args.file_ids,
                            {"folder_id": args.folder})
    if args.format == "json":
        out_json(data)
    else:
        print(f"Moved {data.get('success_count', len(args.file_ids))} files")


def cmd_categorize(client, args):
    """Set category on files."""
    if args.dry_run:
        print(f"Would SET category '{args.category}' on {len(args.file_ids)} files")
        return
    data = client.bulk_call("files/bulk_update/", "file_ids", args.file_ids,
                            {"category": args.category})
    if args.format == "json":
        out_json(data)
    else:
        print(f"Set category '{args.category}' on {data.get('success_count', 0)} files")


def cmd_file_update(client, args):
    """Update single file metadata."""
    payload = {}
    if args.title:
        payload["title"] = args.title
    if args.description is not None:
        payload["description"] = args.description
    if args.category is not None:
        payload["category"] = args.category
    if args.tags is not None:
        payload["tags"] = [t.strip() for t in args.tags.split(",") if t.strip()]
    if args.folder is not None:
        payload["folder"] = args.folder if args.folder != 0 else None
    if not payload:
        print("No fields to update.", file=sys.stderr)
        sys.exit(1)
    if args.dry_run:
        print(f"Would UPDATE file {args.file_id}: {payload}")
        return
    data = client.patch(f"files/{args.file_id}/", payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Updated file {data.get('id')}: '{data.get('title')}'")


# Tag used to mark files for deletion.
DELETION_TAG = "to_delete"


def cmd_files_delete(client, args):
    """Tag files for deletion (soft-delete via the 'to_delete' tag).

    Direct deletion is intentionally not exposed. This command adds the
    DELETION_TAG to the listed files so they can be discovered and removed
    by the platform. Use `pcxa files restore` to undo.
    """
    file_ids = args.file_ids
    if args.dry_run:
        print(f"Would tag {len(file_ids)} files with '{DELETION_TAG}': {file_ids}")
        return
    if not args.yes:
        print(f"Tag {len(file_ids)} files with '{DELETION_TAG}' (mark for deletion)? "
              f"[y/N] ", end="", flush=True)
        if input().strip().lower() != "y":
            print("Aborted.")
            return
    data = client.bulk_call("files/bulk_update/", "file_ids", file_ids,
                            {"tags": [DELETION_TAG], "tag_mode": "add"})
    if args.format == "json":
        out_json(data)
    else:
        n = data.get("success_count", 0)
        print(f"Tagged {n} files with '{DELETION_TAG}'.")
        print(f"Actual removal is handled by a separate cleanup process.")
        print(f"List pending deletions:  pcxa files list --tags {DELETION_TAG}")


def cmd_files_restore(client, args):
    """Remove the 'to_delete' tag from files (undo a deletion mark)."""
    file_ids = args.file_ids
    if args.dry_run:
        print(f"Would remove '{DELETION_TAG}' tag from {len(file_ids)} files")
        return
    data = client.bulk_call("files/bulk_update/", "file_ids", file_ids,
                            {"tags": [DELETION_TAG], "tag_mode": "remove"})
    if args.format == "json":
        out_json(data)
    else:
        n = data.get("success_count", 0)
        print(f"Removed '{DELETION_TAG}' tag from {n} files.")


def cmd_files_purge(client, args):
    """Hard-delete files via ``files/bulk_delete/``, chunked.

    Distinct from ``files delete``, which is a soft-delete (adds the
    ``to_delete`` tag). ``purge`` calls the real DELETE endpoint and is
    irreversible. Routes through ``APIClient.bulk_call`` so JWT auto-refresh
    works across long-running jobs (issue #562).
    """
    ids = list(args.file_ids or [])
    if args.ids_file:
        text = sys.stdin.read() if args.ids_file == "-" else open(args.ids_file).read()
        for tok in text.replace(",", " ").split():
            tok = tok.strip()
            if tok:
                ids.append(int(tok))
    # Dedupe preserving order.
    seen = set()
    ids = [i for i in ids if not (i in seen or seen.add(i))]
    if not ids:
        print("No file ids provided. Use positional args or --ids-file.", file=sys.stderr)
        sys.exit(1)
    chunk = max(1, args.chunk)
    total = len(ids)
    if args.dry_run:
        print(f"Would PURGE {total} files in {(total + chunk - 1) // chunk} chunks of {chunk}")
        print(f"First 5: {ids[:5]}")
        return
    if not args.yes:
        prompt = (f"Type {total} to confirm PURGE of {total} files: "
                  if total >= 1000 else f"PURGE {total} files (irreversible)? [y/N] ")
        print(prompt, end="", flush=True)
        ans = input().strip()
        ok = ans == str(total) if total >= 1000 else ans.lower() == "y"
        if not ok:
            print("Aborted.")
            return

    def _progress(start, size, tot, data):
        ok = data.get("success_count", 0)
        err = data.get("error_count", 0)
        print(f"  {start + size}/{tot}  ok={ok} err={err}", file=sys.stderr)

    data = client.bulk_call(
        "files/bulk_delete/", "file_ids", ids,
        chunk=chunk, method="DELETE",
        on_chunk=None if args.format == "json" else _progress,
    )
    if args.format == "json":
        out_json(data)
    else:
        print(f"\nPurged {data['success_count']} files "
              f"({data['error_count']} errors) in {data['chunks']} chunks.")
        if data["errors"]:
            print(f"First 3 errors: {data['errors'][:3]}", file=sys.stderr)
