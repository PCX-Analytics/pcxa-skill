"""File search, browse, download, and upload commands."""

import json
import sys
from pathlib import Path

from pcxa._config import KNOWN_FILE_TYPES
from pcxa._http import requests
from pcxa._output import fmt_size, out_json, out_table, tag_names


def _file_row(f):
    return {
        "id": str(f.get("id", "")),
        "title": str(f.get("title", ""))[:55],
        "type": f.get("file_type", ""),
        "folder": (f.get("folder_info") or {}).get("full_path", "/"),
        "size": fmt_size(f.get("file_size")),
        "created": str(f.get("created_at", ""))[:10],
        "tags": tag_names(f.get("tags"))[:25],
    }


def cmd_files_list(client, args):
    """List/filter files by metadata."""
    params = client.paginate_params(args.limit, args.offset)
    if args.ext:
        types = [t.strip().upper() for t in args.ext.split(",")]
        if len(types) == 1:
            params["file_type"] = types[0]
    if args.tags:
        params["tags"] = args.tags
        if getattr(args, "tags_mode", None):
            params["tags_mode"] = args.tags_mode
    if args.folder:
        params["folder"] = args.folder
    if args.category:
        params["category"] = args.category
    if args.search:
        params["search"] = args.search
    if args.index_status:
        params["search_status"] = args.index_status
    if args.sort:
        params["ordering"] = args.sort

    if args.count_only:
        print(json.dumps({"count": client.get_count("files/", params)}))
        return

    data = client.get("files/", params)
    if args.format == "json":
        out_json(data)
    else:
        results = data.get("results", data) if isinstance(data, dict) else data
        total = data.get("count", len(results)) if isinstance(data, dict) else len(results)
        rows = [_file_row(f) for f in results]
        print(f"Files: {len(rows)} of {total}\n")
        out_table(rows, ["id", "title", "type", "folder", "size", "created", "tags"])


def cmd_files_search(client, args):
    """Semantic vector search."""
    params = {"q": args.query, "limit": args.limit}
    if args.types:
        params["source_types"] = args.types
    if args.ext:
        params["file_types"] = args.ext

    data = client.get("semantic-search/search/", params)
    for r in data.get("results", []):
        fid = r.get("file_id")
        if fid:
            r["url"] = client.file_url(fid, highlight=args.query)

    if args.format == "json":
        out_json(data)
    else:
        results = data.get("results", [])
        print(f"Search: '{data.get('query', args.query)}' — {data.get('total_results', len(results))} results\n")
        rows = []
        for r in results:
            row = {
                "score": f"{r.get('score', 0):.3f}",
                "id": str(r.get("file_id") or r.get("object_id", "")),
                "name": (r.get("file_name") or r.get("file_title") or r.get("title", ""))[:45],
                "type": r.get("file_type", ""),
                "url": r.get("url", ""),
            }
            if args.show_content and r.get("content"):
                row["content"] = r["content"][:100]
            rows.append(row)
        cols = ["score", "id", "name", "type"]
        if args.show_content:
            cols.append("content")
        cols.append("url")
        out_table(rows, cols)


def cmd_files_content(client, args):
    """Keyword search in indexed file text."""
    try:
        params = {"q": args.query, "limit": args.limit, "offset": args.offset}
        if args.ext:
            params["file_types"] = args.ext
        if args.folder:
            params["folder"] = args.folder
        data = client.get("semantic-search/content-search/", params)
        for r in data.get("results", []):
            fid = r.get("file_id")
            if fid:
                r["url"] = client.file_url(fid, highlight=args.query)
        if args.format == "json":
            out_json(data)
        else:
            results = data.get("results", [])
            total = data.get("total_results", len(results))
            print(f"Content matches for '{args.query}': {total}\n")
            rows = []
            for r in results:
                rows.append({
                    "file_id": str(r.get("file_id", "")),
                    "name": str(r.get("file_name", ""))[:40],
                    "page": str(r.get("page_number", "-")),
                    "match": str(r.get("content", ""))[:70],
                    "url": r.get("url", ""),
                })
            out_table(rows, ["file_id", "name", "page", "match", "url"])
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            # Fallback to title search
            params = {"search": args.query, "page_size": args.limit}
            data = client.get("files/", params)
            if args.format == "json":
                out_json(data)
            else:
                results = data.get("results", [])
                rows = [_file_row(f) for f in results]
                print(f"Title matches (content search unavailable): {len(rows)}\n")
                out_table(rows, ["id", "title", "type", "folder"])
        else:
            raise


def cmd_files_read(client, args):
    """Read file content from indexed chunks."""
    params = {}
    if args.outline:
        params["outline"] = "true"
    else:
        if args.all:
            params["window"] = 50
            params["start"] = 0
        else:
            params["window"] = args.window
            if args.start is not None:
                params["start"] = args.start
            if args.end is not None:
                params["end"] = args.end

    data = client.get(f"semantic-search/file-content/{args.file_id}/", params)
    file_id = data.get("file_id", args.file_id)
    for m in data.get("chunks_meta", []):
        m["url"] = client.file_url(file_id, chunk=m.get("index"))

    if args.format == "json":
        out_json(data)
        return

    print(f"File: {data.get('file_title')} (id={file_id})")
    print(f"Type: {data.get('file_type')}  Chunks: {data.get('total_chunks')}  Chars: {data.get('total_chars', '?'):,}")
    summary = data.get("document_summary")
    if summary:
        print(f"\nSummary: {summary[:300]}")

    if args.outline:
        sections = data.get("sections", [])
        print(f"\nSections ({len(sections)}):")
        for s in sections:
            path = " > ".join(s.get("path", [])) or s.get("title", "?")
            chunks = s.get("chunks", [])
            chunk_range = f"chunks {chunks[0]}-{chunks[-1]}" if chunks else ""
            print(f"  {path}")
            print(f"    {chunk_range}  ({s.get('chars', 0):,} chars)")
    else:
        w = data.get("window", {})
        print(f"\n--- Chunks {w.get('start', 0)}-{w.get('end', '?')} of {data.get('total_chunks', '?')} ---\n")
        print(data.get("content", ""))
        if w.get("has_more"):
            print(f"\n--- More available. Next: --start {w.get('next_start')} ---")
        meta = data.get("chunks_meta", [])
        if meta and not args.all:
            print(f"\nChunk details:")
            rows = [{"idx": str(m["index"]), "page": str(m.get("page") or "-"),
                      "section": (m.get("section") or "")[:35], "chars": str(m.get("chars", 0)),
                      "url": m.get("url", "")} for m in meta]
            out_table(rows, ["idx", "page", "section", "chars", "url"])


def cmd_files_info(client, args):
    """Detailed file metadata."""
    data = client.get(f"files/{args.file_id}/")
    if args.format == "json":
        out_json(data)
        return
    print(f"File {data.get('id')}: {data.get('title')}")
    print(f"  Type:     {data.get('file_type')}")
    print(f"  Size:     {fmt_size(data.get('file_size'))}")
    print(f"  Category: {data.get('category') or '-'}")
    fi = data.get("folder_info") or {}
    print(f"  Folder:   {fi.get('full_path') or '/'} (id={data.get('folder') or 'root'})")
    print(f"  Tags:     {tag_names(data.get('tags')) or '-'}")
    cb = data.get("created_by") or {}
    print(f"  Created:  {str(data.get('created_at', ''))[:10]} by {cb.get('username', '-')}")
    print(f"  Index:    {data.get('search_status', '-')}")
    desc = data.get("description") or ""
    if desc:
        print(f"  Desc:     {desc[:200]}")
    versions = data.get("versions") or []
    if versions:
        print(f"\n  Versions ({len(versions)}):")
        for v in versions:
            meta = v.get("file_metadata") or {}
            print(f"    v{v.get('version_number')}: {meta.get('original_filename', '-')} ({fmt_size(meta.get('size'))}) - {str(v.get('created_at', ''))[:10]}")


def cmd_files_stats(client, args):
    """Project file statistics."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    stats = {"total_files": client.get_count("files/")}

    try:
        stats["indexing"] = client.get("semantic-search/status/")
    except requests.HTTPError:
        stats["indexing"] = None

    type_counts = {}
    def _count_type(ft):
        try:
            return ft, client.get_count("files/", {"file_type": ft})
        except Exception:
            return ft, None
    with ThreadPoolExecutor(max_workers=10) as pool:
        futs = [pool.submit(_count_type, ft) for ft in KNOWN_FILE_TYPES]
        for fut in as_completed(futs):
            ft, c = fut.result()
            if c and c > 0:
                type_counts[ft] = c
    stats["by_type"] = dict(sorted(type_counts.items(), key=lambda x: -x[1]))

    if args.format == "json":
        out_json(stats)
        return

    print(f"Total files: {stats['total_files']:,}\n")
    idx = stats.get("indexing")
    if isinstance(idx, dict):
        print("Indexing:")
        for k, v in idx.items():
            if isinstance(v, (int, float)):
                print(f"  {k}: {v:,}")
        print()
    by_type = stats.get("by_type")
    if by_type:
        print(f"File types ({len(by_type)}):")
        for ft, count in by_type.items():
            bar = "#" * min(50, max(1, int(count / max(stats["total_files"], 1) * 50)))
            print(f"  {ft:8s} {count:>8,}  {bar}")


def cmd_files_aggregate(client, args):
    """Aggregate file counts by dimension."""
    params = {"group_by": args.group_by}
    if args.ext:
        params["file_type"] = args.ext.upper()
    if args.tags:
        params["tags"] = args.tags
    if args.folder:
        params["folder"] = args.folder
    if args.search:
        params["search"] = args.search
    if args.top:
        params["top"] = args.top

    data = client.get("files/aggregate/", params)
    if args.format == "json":
        out_json(data)
        return

    total = data.get("total_matching", 0)
    groups = data.get("groups", [])
    print(f"Total: {total:,} — grouped by: {data.get('group_by')}\n")
    rows = []
    for g in groups:
        row = {"value": str(g.get("value", ""))[:45], "count": str(g.get("count", 0))}
        if "id" in g:
            row["id"] = str(g["id"] or "")
        pct = (g["count"] / total * 100) if total else 0
        row["pct"] = f"{pct:.1f}%"
        rows.append(row)
    cols = ["value"]
    if any("id" in r for r in rows):
        cols.append("id")
    cols.extend(["count", "pct"])
    out_table(rows, cols)


def cmd_files_recent(client, args):
    """Recently uploaded files."""
    params = {"page_size": args.limit, "ordering": "-created_at"}
    if args.ext:
        params["file_type"] = args.ext.upper()
    if args.folder:
        params["folder"] = args.folder
    data = client.get("files/", params)
    if args.format == "json":
        out_json(data)
    else:
        results = data.get("results", data) if isinstance(data, dict) else data
        rows = [_file_row(f) for f in results]
        print(f"Recent files ({len(rows)}):\n")
        out_table(rows, ["id", "title", "type", "folder", "size", "created"])


def cmd_files_download(client, args):
    """Download a file to disk via presigned URL."""
    file_data = client.get(f"files/{args.file_id}/")
    current_version = file_data.get("current_version")
    if not current_version:
        print(f"File {args.file_id} has no versions to download.", file=sys.stderr)
        sys.exit(1)

    version_id = current_version["id"]
    dl = client.get(f"files/{args.file_id}/versions/{version_id}/presign-download/")
    presigned_url = dl.get("url")
    if not presigned_url:
        print("Could not get download URL from API.", file=sys.stderr)
        sys.exit(1)

    if args.output:
        out_path = Path(args.output)
    else:
        meta = current_version.get("file_metadata") or {}
        filename = meta.get("original_filename") or file_data.get("title", f"file_{args.file_id}")
        out_path = Path(filename)

    print(f"Downloading: {file_data.get('title')} (v{current_version.get('version_number', '?')})")
    print(f"  Saving to: {out_path}")

    resp = requests.get(presigned_url, stream=True, timeout=120)
    resp.raise_for_status()

    total = int(resp.headers.get("content-length", 0))
    downloaded = 0
    with open(out_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)
            downloaded += len(chunk)
            if total:
                pct = downloaded / total * 100
                print(f"\r  Progress: {pct:.1f}%", end="", flush=True)

    print(f"\n  Done: {out_path} ({out_path.stat().st_size:,} bytes)")


def cmd_files_upload(client, args):
    """Upload one or more files (or all files in a directory)."""
    # Resolve paths — expand directories to their file contents
    file_paths = []
    for p in args.paths:
        path = Path(p)
        if not path.exists():
            print(f"Path not found: {p}", file=sys.stderr)
            sys.exit(1)
        if path.is_dir():
            children = sorted(f for f in path.iterdir() if f.is_file() and not f.name.startswith("."))
            if not children:
                print(f"No files in directory: {p}", file=sys.stderr)
                sys.exit(1)
            file_paths.extend(children)
        else:
            file_paths.append(path)

    tags = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else []

    if args.dry_run:
        for fp in file_paths:
            title = args.title if (args.title and len(file_paths) == 1) else fp.stem
            parts = [f"title='{title}'"]
            if args.folder:
                parts.append(f"folder={args.folder}")
            if tags:
                parts.append(f"tags={tags}")
            print(f"Would UPLOAD {fp.name} ({fp.stat().st_size:,} bytes) — {', '.join(parts)}")
        return

    # Files > 10 MB use the 3-step presign flow (upload directly to storage
    # provider, no bytes through server). Smaller files use multipart POST.
    large_file_threshold = 10 * 1024 * 1024

    url = client._url("files/")
    presign_url = client._url("files/presign-upload/")
    uploaded = 0
    for fp in file_paths:
        title = args.title if (args.title and len(file_paths) == 1) else fp.stem
        file_size = fp.stat().st_size

        print(f"Uploading: {fp.name} ({file_size:,} bytes) ...", end=" ", flush=True)

        if file_size > large_file_threshold:
            result = _upload_via_presign(client, fp, title, args.folder, tags, presign_url, url)
        else:
            result = _upload_via_multipart(client, fp, title, args.folder, tags, url)

        if args.format == "json":
            out_json(result)
        else:
            print(f"OK — id={result.get('id')} title='{result.get('title')}'")
        uploaded += 1

    if len(file_paths) > 1 and args.format != "json":
        print(f"\nUploaded {uploaded} file(s)")


def _upload_via_multipart(client, fp, title, folder, tags, url):
    """Small files: single multipart POST (bytes through server)."""
    import mimetypes

    data_fields = {"title": title}
    if folder:
        data_fields["folder"] = str(folder)
    for i, tag in enumerate(tags):
        data_fields[f"tags[{i}]"] = tag

    content_type = mimetypes.guess_type(fp.name)[0] or "application/octet-stream"
    with open(fp, "rb") as fh:
        files = {"file_upload": (fp.name, fh, content_type)}
        resp = client._request("POST", url, data=data_fields, files=files)
    return resp.json()


def _upload_via_presign(client, fp, title, folder, tags, presign_url, create_url):
    """Large files: 3-step presign flow (upload directly to storage provider)."""
    import mimetypes

    content_type = mimetypes.guess_type(fp.name)[0] or "application/octet-stream"
    file_size = fp.stat().st_size

    # Step 1: Get presigned upload URL from backend
    presign = client._request("POST", presign_url, json={
        "filename": fp.name,
        "content_type": content_type,
        "file_size": file_size,
    }).json()

    upload_url = presign["upload_url"]
    upload_type = presign.get("upload_type", "presigned_put")

    # Step 2: Upload directly to storage provider
    if upload_type == "sharepoint_session":
        drive_item = _upload_sharepoint_chunked(upload_url, fp, file_size)
        drive_item_id = drive_item["id"]
        drive_id = drive_item.get("parentReference", {}).get("driveId", "")
    else:
        with open(fp, "rb") as fh:
            resp = requests.put(upload_url, data=fh, headers={
                "Content-Type": content_type,
            }, timeout=300)
            resp.raise_for_status()
        drive_item_id = None
        drive_id = None

    # Step 3: Create file record in the app
    payload = {
        "title": title,
        "original_filename": fp.name,
        "content_type": content_type,
        "file_size_input": file_size,
    }
    if folder:
        payload["folder"] = folder
    if tags:
        payload["tags"] = tags

    # Include storage reference data
    storage_key = presign.get("storage_key", "")
    if storage_key:
        payload["storage_key"] = storage_key
    if drive_item_id:
        payload["drive_item_id"] = drive_item_id
        payload["drive_id"] = drive_id
        payload["provider_type"] = presign.get("provider_type", "")
        storage_ref_data = presign.get("storage_ref_data", {})
        if storage_ref_data.get("provider_id"):
            payload["provider_id"] = storage_ref_data["provider_id"]

    resp = client._request("POST", create_url, json=payload)
    return resp.json()


def cmd_files_upload_version(client, args):
    """Upload a new version of an existing file.

    Hits POST /files/{id}/upload_new_version/ on the backend. Small files
    (≤ 10 MB) go through multipart POST; larger ones use the presign flow
    (identical to `files upload`) then reference the resulting storage_key
    in the version create payload.
    """
    fp = Path(args.path)
    if not fp.exists() or not fp.is_file():
        print(f"Path not found or not a file: {args.path}", file=sys.stderr)
        sys.exit(1)

    file_size = fp.stat().st_size
    large_file_threshold = 10 * 1024 * 1024

    version_url = client._url(f"files/{args.file_id}/upload_new_version/")

    if args.dry_run:
        mode = "presign" if file_size > large_file_threshold else "multipart"
        print(f"Would UPLOAD new version of file id={args.file_id} "
              f"from {fp.name} ({file_size:,} bytes, {mode})")
        return

    print(f"Uploading new version of file {args.file_id}: {fp.name} "
          f"({file_size:,} bytes) ...", end=" ", flush=True)

    if file_size > large_file_threshold:
        result = _upload_version_via_presign(client, fp, args.file_id, args.notes, version_url)
    else:
        result = _upload_version_via_multipart(client, fp, args.notes, version_url)

    if args.format == "json":
        out_json(result)
    else:
        print(f"OK — version_id={result.get('id')} v{result.get('version_number')}")


def _upload_version_via_multipart(client, fp, notes, version_url):
    """Small-file path: direct multipart POST to upload_new_version."""
    import mimetypes

    content_type = mimetypes.guess_type(fp.name)[0] or "application/octet-stream"
    data_fields = {}
    if notes:
        data_fields["version_notes"] = notes
    with open(fp, "rb") as fh:
        files = {"file_upload": (fp.name, fh, content_type)}
        resp = client._request("POST", version_url, data=data_fields, files=files)
    return resp.json()


def _upload_version_via_presign(client, fp, file_id, notes, version_url):
    """Large-file path: presign → PUT-to-storage → POST version with storage_key.

    Reuses the same `files/presign-upload/` endpoint the CLI already uses for
    new uploads. The returned storage_key is passed to
    `files/{id}/upload_new_version/` instead of the `files/` create URL.
    """
    import mimetypes

    content_type = mimetypes.guess_type(fp.name)[0] or "application/octet-stream"
    file_size = fp.stat().st_size

    presign_url = client._url("files/presign-upload/")

    # Step 1: presigned upload URL. Deliberately do NOT pass a `folder`
    # because the version inherits its file's folder — the backend ignores
    # folder here but the caller would be confusing.
    presign = client._request("POST", presign_url, json={
        "filename": fp.name,
        "content_type": content_type,
        "file_size": file_size,
    }).json()

    upload_url = presign["upload_url"]
    upload_type = presign.get("upload_type", "presigned_put")

    # Step 2: PUT bytes to storage.
    if upload_type == "sharepoint_session":
        drive_item = _upload_sharepoint_chunked(upload_url, fp, file_size)
        drive_item_id = drive_item["id"]
        drive_id = drive_item.get("parentReference", {}).get("driveId", "")
    else:
        with open(fp, "rb") as fh:
            resp = requests.put(upload_url, data=fh, headers={
                "Content-Type": content_type,
            }, timeout=300)
            resp.raise_for_status()
        drive_item_id = None
        drive_id = None

    # Step 3: create the FileVersion record pointing at the uploaded bytes.
    payload = {
        "original_filename": fp.name,
        "content_type": content_type,
        "file_size_input": file_size,
    }
    if notes:
        payload["version_notes"] = notes
    storage_key = presign.get("storage_key", "")
    if storage_key:
        payload["storage_key"] = storage_key
    if drive_item_id:
        payload["drive_item_id"] = drive_item_id
        payload["drive_id"] = drive_id
        payload["provider_type"] = presign.get("provider_type", "")
        storage_ref_data = presign.get("storage_ref_data", {})
        if storage_ref_data.get("provider_id"):
            payload["provider_id"] = storage_ref_data["provider_id"]

    resp = client._request("POST", version_url, json=payload)
    return resp.json()


def _upload_sharepoint_chunked(upload_url, fp, total_size):
    """Upload through a chunked provider session."""
    chunk_size = 5 * 320 * 1024  # 1.6 MB aligned to 320 KiB
    offset = 0
    item = None

    with open(fp, "rb") as fh:
        while offset < total_size:
            end = min(offset + chunk_size, total_size)
            chunk = fh.read(end - offset)

            resp = requests.put(upload_url, data=chunk, headers={
                "Content-Length": str(end - offset),
                "Content-Range": f"bytes {offset}-{end - 1}/{total_size}",
            }, timeout=120)

            if resp.status_code in (200, 201):
                item = resp.json()
                break
            elif resp.status_code == 202:
                offset = end
                pct = offset / total_size * 100
                print(f"\r  Progress: {pct:.0f}%", end="", flush=True)
            else:
                resp.raise_for_status()

    if not item:
        raise RuntimeError("Chunked upload completed but no provider item was returned")

    print(f"\r  Progress: 100%", flush=True)
    return item
