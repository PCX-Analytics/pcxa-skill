"""Recursive directory sync: mirror a local tree into a PCXA folder.

`pcxa files sync <input_dir> --folder <output_folder_id>` walks the local
tree, creates any missing PCXA subfolders to match the directory shape,
and uploads each file. Idempotent: skips any local file whose name already
exists in the corresponding PCXA folder. An optional ``--manifest`` JSON
sidecar persists upload state across runs so repeats are fast no-ops.
"""

import fnmatch
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from pcxa._output import fmt_size, out_json
from pcxa.commands.files import (
    _multipart_presign_and_put,
    _presign_and_put,
    _with_retry,
)


MANIFEST_VERSION = 1
BULK_REGISTER_FLUSH_SIZE = 100
EXISTING_FILES_PAGE_SIZE = 200


def cmd_files_sync(client, args):
    input_root = Path(args.input_dir).resolve()
    if not input_root.exists() or not input_root.is_dir():
        print(f"Input path not found or not a directory: {args.input_dir}",
              file=sys.stderr)
        sys.exit(1)

    root_folder_id = args.folder  # may be None == project root
    includes = list(args.include or [])
    excludes = list(args.exclude or [])
    skip_hidden = not args.include_hidden
    tags = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else []
    output_format = getattr(args, "format", "json")

    entries = _collect_files(input_root, includes, excludes, skip_hidden)
    if not entries:
        print(f"No files matched in {input_root} (after filters).", file=sys.stderr)
        return

    total_files = len(entries)
    total_bytes = sum(e["size"] for e in entries)
    print(
        f"Scanned {total_files} files ({fmt_size(total_bytes)}) under {input_root}",
        file=sys.stderr,
    )

    manifest_path = Path(args.manifest).resolve() if args.manifest else None
    manifest = _load_manifest(manifest_path) if manifest_path else {
        "version": MANIFEST_VERSION, "files": {}
    }

    if args.dry_run:
        _print_dry_run(entries, root_folder_id, manifest, total_bytes)
        return

    print("Resolving target folders...", file=sys.stderr)
    rel_dirs = sorted({e["relative_dir"] for e in entries},
                      key=lambda x: (x.count("/"), x))
    rel_dir_to_folder_id = _resolve_or_create_folders(client, rel_dirs, root_folder_id)

    print("Reading existing filenames in target folders...", file=sys.stderr)
    target_folder_ids = {fid for fid in rel_dir_to_folder_id.values() if fid is not None}
    folder_to_existing = _fetch_existing_names(client, target_folder_ids)

    work_items = []
    skipped_manifest = 0
    skipped_api = 0
    for e in entries:
        rel_path = e["relative_path"]
        folder_id = rel_dir_to_folder_id[e["relative_dir"]]
        e["folder_id"] = folder_id
        mf = manifest["files"].get(rel_path)
        if mf and mf.get("size") == e["size"] and mf.get("folder_id") == folder_id:
            skipped_manifest += 1
            continue
        existing = folder_to_existing.get(folder_id, set()) if folder_id else set()
        # Backend default-titles uploads to the filename stem; existing rows
        # uploaded via this same flow will match on stem.
        candidates = {e["name"].lower(), e["stem"].lower()}
        if existing & candidates:
            skipped_api += 1
            manifest["files"][rel_path] = {
                "size": e["size"],
                "name": e["name"],
                "folder_id": folder_id,
                "skipped_reason": "already_in_target_folder",
            }
            continue
        work_items.append(e)

    pending_bytes = sum(e["size"] for e in work_items)
    print(
        f"To upload: {len(work_items)} files ({fmt_size(pending_bytes)})  "
        f"Skipped: {skipped_manifest} (manifest), {skipped_api} (name match)",
        file=sys.stderr,
    )

    summary = {
        "scanned": total_files,
        "scanned_bytes": total_bytes,
        "to_upload": len(work_items),
        "to_upload_bytes": pending_bytes,
        "skipped_manifest": skipped_manifest,
        "skipped_name_match": skipped_api,
        "created": 0,
        "duplicate": 0,
        "error": 0,
        "failures": [],
    }

    if not work_items:
        if manifest_path:
            _save_manifest(manifest_path, manifest, input_root, root_folder_id)
        if output_format == "json":
            out_json(summary)
        else:
            print("Nothing to upload.", file=sys.stderr)
        return

    multipart_threshold = args.multipart_threshold_mb * 1024 * 1024
    part_size = max(5, args.part_size_mb) * 1024 * 1024
    concurrency = max(1, min(32, args.concurrency))

    _run_uploads(
        client=client,
        work_items=work_items,
        manifest=manifest,
        manifest_path=manifest_path,
        input_root=input_root,
        root_folder_id=root_folder_id,
        tags=tags,
        concurrency=concurrency,
        multipart_threshold=multipart_threshold,
        part_size=part_size,
        pending_bytes=pending_bytes,
        summary=summary,
    )

    if manifest_path:
        _save_manifest(manifest_path, manifest, input_root, root_folder_id)

    if output_format == "json":
        out_json(summary)
    else:
        print(
            f"\nDone: {summary['created']} created, "
            f"{summary['duplicate']} duplicate, "
            f"{summary['error']} error  "
            f"(skipped: {skipped_manifest} manifest, {skipped_api} name match)",
            file=sys.stderr,
        )
        if summary["failures"]:
            print(f"\nFirst {min(10, len(summary['failures']))} failures:", file=sys.stderr)
            for f in summary["failures"][:10]:
                print(f"  - {f['name']}: {f['error']}", file=sys.stderr)


def _collect_files(input_root, includes, excludes, skip_hidden):
    entries = []
    for dirpath, dirnames, filenames in os.walk(input_root):
        if skip_hidden:
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in sorted(filenames):
            if skip_hidden and name.startswith("."):
                continue
            if includes and not any(fnmatch.fnmatch(name, p) for p in includes):
                continue
            if excludes and any(fnmatch.fnmatch(name, p) for p in excludes):
                continue
            abs_path = Path(dirpath) / name
            try:
                size = abs_path.stat().st_size
            except OSError as exc:
                print(f"  skip (stat failed): {abs_path} — {exc}", file=sys.stderr)
                continue
            rel_dir_parts = Path(dirpath).resolve().relative_to(input_root).parts
            rel_dir = "/".join(rel_dir_parts)  # "" for root
            rel_path = f"{rel_dir}/{name}" if rel_dir else name
            entries.append({
                "abs_path": abs_path,
                "name": name,
                "stem": Path(name).stem,
                "size": size,
                "relative_dir": rel_dir,
                "relative_path": rel_path,
            })
    return entries


def _resolve_or_create_folders(client, rel_dirs, root_folder_id):
    """Map each relative dir (POSIX-style) to a PCXA folder id.

    Walks the folder tree segment by segment, reusing existing folders by
    name and creating any that don't exist. Builds a cache so each segment
    is only resolved once per run.
    """
    cache = {"": root_folder_id}

    # Pre-populate subfolder maps lazily; ``children_cache[parent_id]`` is a
    # ``{lowercased_name: id}`` dict, fetched the first time a parent is touched.
    children_cache = {}

    def _children_of(parent_id):
        if parent_id in children_cache:
            return children_cache[parent_id]
        m = {}
        if parent_id is None:
            tree = client.get("folders/folder_tree/")
            roots = tree if isinstance(tree, list) else tree.get("results", [])
            for node in roots:
                m[node["name"].lower()] = node["id"]
        else:
            page = 1
            while True:
                resp = client._request(
                    "GET",
                    client._url(f"folders/{parent_id}/subfolders/"),
                    params={"page": page, "page_size": 1000},
                    timeout=180,
                )
                data = resp.json()
                results = data.get("results") if isinstance(data, dict) else data
                for s in results or []:
                    m[s["name"].lower()] = int(s["id"])
                if not (isinstance(data, dict) and data.get("next")):
                    break
                page += 1
        children_cache[parent_id] = m
        return m

    for rel_dir in rel_dirs:
        if rel_dir in cache:
            continue
        segments = rel_dir.split("/")
        parent_id = root_folder_id
        running = []
        for seg in segments:
            running.append(seg)
            key = "/".join(running)
            if key in cache:
                parent_id = cache[key]
                continue
            siblings = _children_of(parent_id)
            existing = siblings.get(seg.lower())
            if existing is not None:
                parent_id = existing
            else:
                payload = {"name": seg}
                if parent_id is not None:
                    payload["parent"] = parent_id
                created = client.post("folders/", payload)
                parent_id = int(created["id"])
                siblings[seg.lower()] = parent_id
            cache[key] = parent_id
    return cache


def _fetch_existing_names(client, folder_ids):
    """For each folder id, return the set of names already there.

    Pulls ``files/?folder=X`` paginated. We store both ``title`` and any
    ``original_filename`` we find (lowercased) so the name collision check
    in :func:`cmd_files_sync` is forgiving across upload sources.
    """
    result = {}
    for fid in folder_ids:
        names = set()
        page = 1
        while True:
            data = client.get("files/", {
                "folder": fid,
                "page": page,
                "page_size": EXISTING_FILES_PAGE_SIZE,
            })
            results = data.get("results", []) if isinstance(data, dict) else data
            for f in results:
                title = (f.get("title") or "").strip().lower()
                if title:
                    names.add(title)
                cv = f.get("current_version") or {}
                meta = cv.get("file_metadata") or {}
                orig = (meta.get("original_filename") or "").strip().lower()
                if orig:
                    names.add(orig)
                    names.add(Path(orig).stem.lower())
            if not (isinstance(data, dict) and data.get("next")):
                break
            page += 1
        result[fid] = names
    return result


def _load_manifest(path):
    if not path or not path.exists():
        return {"version": MANIFEST_VERSION, "files": {}}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict) or "files" not in data:
            return {"version": MANIFEST_VERSION, "files": {}}
        return data
    except Exception as exc:
        print(f"  manifest read failed ({path}): {exc} — starting fresh",
              file=sys.stderr)
        return {"version": MANIFEST_VERSION, "files": {}}


def _save_manifest(path, manifest, input_root, root_folder_id):
    manifest.setdefault("version", MANIFEST_VERSION)
    manifest["input_root"] = str(input_root)
    manifest["output_folder_id"] = root_folder_id
    manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
    try:
        tmp = path.with_suffix(path.suffix + ".tmp")
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except Exception as exc:
        print(f"  manifest write failed ({path}): {exc}", file=sys.stderr)


def _print_dry_run(entries, root_folder_id, manifest, total_bytes):
    by_dir = {}
    for e in entries:
        by_dir.setdefault(e["relative_dir"], []).append(e)
    in_manifest = 0
    for e in entries:
        mf = manifest["files"].get(e["relative_path"])
        if mf and mf.get("size") == e["size"]:
            in_manifest += 1
    print(
        f"Dry run — would mirror {len(entries)} files ({fmt_size(total_bytes)}) "
        f"under PCXA folder id={root_folder_id or 'root'}.",
        file=sys.stderr,
    )
    if in_manifest:
        print(f"  {in_manifest} already in manifest (would skip).", file=sys.stderr)
    for rel_dir, files in sorted(by_dir.items()):
        print(f"  {rel_dir or '.'}/  ({len(files)} files)", file=sys.stderr)
        for e in files[:5]:
            print(f"    {e['name']} ({fmt_size(e['size'])})", file=sys.stderr)
        if len(files) > 5:
            print(f"    ... and {len(files) - 5} more", file=sys.stderr)


def _run_uploads(*, client, work_items, manifest, manifest_path, input_root,
                 root_folder_id, tags, concurrency, multipart_threshold, part_size,
                 pending_bytes, summary):
    register_url = client._url("files/bulk-register/")
    pending = []
    pending_lock = threading.Lock()
    state_lock = threading.Lock()
    manifest_lock = threading.Lock()
    state = {
        "files_done": 0,
        "bytes_done": 0,
        "last_render": 0.0,
        "started": time.monotonic(),
        "since_manifest_save": 0,
    }
    interrupted = threading.Event()

    def _flush_locked(items):
        if not items:
            return
        try:
            resp = client._request("POST", register_url, json={"items": items})
            data = resp.json()
            s = data.get("summary", {})
            summary["created"] += s.get("created", 0)
            summary["duplicate"] += s.get("duplicate", 0)
            summary["error"] += s.get("error", 0)
            # Map register results back so the manifest gets the file_id.
            results = data.get("results") or []
            for row in results:
                idx = row.get("index")
                if idx is None or idx >= len(items):
                    continue
                item = items[idx]
                rel_path = item.get("_sync_rel_path")
                if row.get("status") == "error":
                    summary["failures"].append({
                        "name": item.get("original_filename", "?"),
                        "error": row.get("error", "unknown"),
                    })
                    continue
                if not rel_path:
                    continue
                with manifest_lock:
                    manifest["files"][rel_path] = {
                        "size": item.get("file_size"),
                        "name": item.get("original_filename"),
                        "folder_id": item.get("folder"),
                        "file_id": row.get("file_id"),
                        "uploaded_at": datetime.now(timezone.utc).isoformat(),
                    }
        except Exception as exc:
            summary["error"] += len(items)
            for it in items:
                summary["failures"].append({
                    "name": it.get("original_filename", "?"),
                    "error": f"bulk-register: {exc}",
                })

    def _maybe_flush():
        with pending_lock:
            if len(pending) < BULK_REGISTER_FLUSH_SIZE:
                return
            batch = pending[:BULK_REGISTER_FLUSH_SIZE]
            del pending[:BULK_REGISTER_FLUSH_SIZE]
        _flush_locked(batch)

    def _render(force=False):
        now = time.monotonic()
        with state_lock:
            if not force and now - state["last_render"] < 0.2:
                return
            state["last_render"] = now
            done = state["files_done"]
            bytes_done = state["bytes_done"]
        elapsed = max(0.001, now - state["started"])
        rate = bytes_done / elapsed
        total = len(work_items)
        pct = (done / total * 100) if total else 100.0
        err_rate = (summary["error"] / done * 100) if done else 0.0
        eta = ((pending_bytes - bytes_done) / rate) if rate > 0 and pending_bytes else 0
        bar = _bar(pct, width=20)
        line = (
            f"\r  {bar} {done:>5}/{total:<5} {pct:5.1f}%  "
            f"{fmt_size(bytes_done)}/{fmt_size(pending_bytes)}  "
            f"{fmt_size(rate)}/s  "
            f"elapsed={_fmt_elapsed(elapsed)}  "
            f"eta={_fmt_elapsed(eta) if eta else '--'}  "
            f"err={summary['error']} ({err_rate:.1f}%)"
        )
        sys.stderr.write(line + "   ")
        sys.stderr.flush()

    def _upload_one(entry):
        fp = entry["abs_path"]
        size = entry["size"]
        if size > multipart_threshold:
            return _multipart_presign_and_put(
                client, fp, folder=entry["folder_id"],
                part_size=part_size, concurrency=concurrency,
            )
        return _presign_and_put(client, fp, folder=entry["folder_id"])

    _render(force=True)

    try:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = {pool.submit(_with_retry, _upload_one, e): e for e in work_items}
            for future in as_completed(futures):
                entry = futures[future]
                try:
                    item = future.result()
                    item["_sync_rel_path"] = entry["relative_path"]
                    if tags:
                        item["tags"] = list(tags)
                    with pending_lock:
                        pending.append(item)
                    with state_lock:
                        state["files_done"] += 1
                        state["bytes_done"] += entry["size"]
                        state["since_manifest_save"] += 1
                except Exception as exc:
                    summary["error"] += 1
                    summary["failures"].append({
                        "name": entry["name"], "error": str(exc),
                    })
                    with state_lock:
                        state["files_done"] += 1
                _maybe_flush()
                _render()
                # Periodic manifest snapshot so a crash doesn't lose progress.
                if manifest_path and state["since_manifest_save"] >= 50:
                    # Drain pending so manifest reflects registered rows only.
                    with pending_lock:
                        batch = list(pending)
                        pending[:] = []
                    _flush_locked(batch)
                    _save_manifest(manifest_path, manifest, input_root, root_folder_id)
                    with state_lock:
                        state["since_manifest_save"] = 0
    except KeyboardInterrupt:
        interrupted.set()
        sys.stderr.write("\n  interrupted — flushing partial progress...\n")

    # Drain remaining items.
    with pending_lock:
        leftover, pending[:] = list(pending), []
    _flush_locked(leftover)
    _render(force=True)
    sys.stderr.write("\n")
    sys.stderr.flush()

    if interrupted.is_set():
        sys.exit(130)


def _bar(pct, width=20):
    filled = int(round(pct / 100 * width))
    filled = max(0, min(width, filled))
    return "[" + "#" * filled + "-" * (width - filled) + "]"


def _fmt_elapsed(seconds):
    seconds = int(max(0, seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:d}h{m:02d}m"
    if m:
        return f"{m:d}m{s:02d}s"
    return f"{s:d}s"


__all__ = ["cmd_files_sync"]
