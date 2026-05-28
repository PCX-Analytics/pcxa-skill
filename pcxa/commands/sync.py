"""Recursive directory sync: mirror a local tree into a PCXA folder.

`pcxa files sync <input_dir> --folder <output_folder_id>` walks the local
tree, creates any missing PCXA subfolders to match the directory shape,
and uploads each file. Idempotent: skips any local file whose name already
exists in the corresponding PCXA folder. An optional ``--manifest`` JSON
sidecar persists upload state across runs so repeats are fast no-ops.

Designed for terabyte-scale workloads: concurrency is gated by a runtime
resizable semaphore, an AIMD controller continuously adjusts the active
worker count from observed throughput + error rate (bounded by
``--min-concurrency`` / ``--max-concurrency``), the manifest is flushed by
both count and wall time so a crash loses at most ~30s of work, and
``--max-failures`` short-circuits a misconfigured run before it burns
hours of bandwidth.
"""

import fnmatch
import json
import math
import os
import sys
import threading
import time
import queue as _queue
from concurrent.futures import (
    FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait,
)
from datetime import datetime, timezone
from pathlib import Path

from pcxa._output import fmt_size, out_json
from pcxa._http import requests as _requests
from pcxa._api import AuthExpiredError
from pcxa.commands.files import (
    _multipart_presign_and_put,
    _presign_and_put,
    _with_retry,
)


MANIFEST_VERSION = 1
BULK_REGISTER_FLUSH_SIZE = 100
EXISTING_FILES_PAGE_SIZE = 200
EXISTING_FILES_TIMEOUT = 180
EXISTING_FILES_RETRIES = 3
EXISTING_FILES_WORKERS = 6
BULK_REGISTER_TIMEOUT = 180
BULK_REGISTER_RETRIES = 3
BULK_REGISTER_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
MANIFEST_CHECKPOINT_SECONDS = 30
MANIFEST_CHECKPOINT_FILES = 50
R2_MAX_PARTS = 10000
R2_PART_HEADROOM = 9500  # leave room so we never round up to 10001


def cmd_files_sync(client, args):
    input_root = Path(args.input_dir).resolve()
    if not input_root.exists() or not input_root.is_dir():
        print(f"Input path not found or not a directory: {args.input_dir}",
              file=sys.stderr)
        sys.exit(1)

    root_folder_id = args.folder
    includes = list(args.include or [])
    excludes = list(args.exclude or [])
    skip_hidden = not args.include_hidden
    tags = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else []
    output_format = getattr(args, "format", "json")

    # Concurrency knobs. --concurrency is the *starting* value; --max-
    # concurrency caps the auto-tuner; --min-concurrency is the floor.
    max_concurrency = max(1, min(64, args.max_concurrency))
    min_concurrency = max(1, min(max_concurrency, args.min_concurrency))
    initial_concurrency = max(min_concurrency,
                              min(max_concurrency, args.concurrency))
    part_concurrency = max(1, min(16, args.part_concurrency))
    auto_tune = not args.no_auto_tune
    max_failures = max(0, args.max_failures)
    # Bulk-presign batches multiple files into one /files/bulk-presign-
    # upload/ call. Server cap is 500; default 200 mirrors the new
    # `files upload` flag. If the endpoint isn't deployed yet, the run
    # auto-falls back to per-file presign on the first 404.
    batch_size = max(1, min(500, args.batch_size))
    use_bulk_presign = not args.no_bulk_presign

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
    if manifest_path and manifest.get("files"):
        print(
            f"Resuming from manifest: {len(manifest['files'])} files already recorded.",
            file=sys.stderr,
        )

    limit = max(0, getattr(args, "limit", 0))

    if args.dry_run:
        _print_dry_run(entries, root_folder_id, manifest, total_bytes,
                       initial_concurrency, max_concurrency, auto_tune, limit)
        return

    # Pre-flight: confirm the target folder actually exists so we don't
    # discover the typo 4 hours into a TB upload.
    if root_folder_id is not None:
        try:
            client.get(f"folders/{root_folder_id}/")
        except AuthExpiredError as exc:
            print(f"Token expired: {exc}", file=sys.stderr)
            sys.exit(2)
        except _requests.HTTPError as exc:
            print(f"Target folder id={root_folder_id} not accessible: {exc}",
                  file=sys.stderr)
            sys.exit(1)

    print("Resolving target folders...", file=sys.stderr)
    rel_dirs = sorted({e["relative_dir"] for e in entries},
                      key=lambda x: (x.count("/"), x))
    rel_dir_to_folder_id = _resolve_or_create_folders(client, rel_dirs, root_folder_id)

    target_folder_ids = {fid for fid in rel_dir_to_folder_id.values() if fid is not None}
    if getattr(args, "trust_manifest", False):
        print(
            "Skipping existing-filename check (--trust-manifest): "
            "dedup will rely on the manifest only.",
            file=sys.stderr,
        )
        folder_to_existing = {fid: set() for fid in target_folder_ids}
    else:
        print("Reading existing filenames in target folders...", file=sys.stderr)
        try:
            folder_to_existing = _fetch_existing_names(client, target_folder_ids)
        except Exception as exc:
            print(
                f"Warning: pre-flight name check failed ({exc}). "
                f"Proceeding with manifest-only dedup. Duplicate uploads "
                f"may occur for files added via other means.",
                file=sys.stderr,
            )
            folder_to_existing = {fid: set() for fid in target_folder_ids}

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

    capped_by_limit = 0
    if limit and len(work_items) > limit:
        capped_by_limit = len(work_items) - limit
        work_items = work_items[:limit]

    pending_bytes = sum(e["size"] for e in work_items)
    limit_note = f"  (--limit kept first {limit}, deferred {capped_by_limit})" \
        if capped_by_limit else ""
    print(
        f"To upload: {len(work_items)} files ({fmt_size(pending_bytes)})  "
        f"Skipped: {skipped_manifest} (manifest), {skipped_api} (name match)"
        f"{limit_note}",
        file=sys.stderr,
    )

    _warn_if_token_short(client)
    if auto_tune:
        print(
            f"Auto-tune ON: starting at {initial_concurrency} workers, "
            f"range [{min_concurrency}, {max_concurrency}], "
            f"max-failures={max_failures}.",
            file=sys.stderr,
        )
    else:
        print(
            f"Auto-tune OFF: fixed at {initial_concurrency} workers, "
            f"max-failures={max_failures}.",
            file=sys.stderr,
        )
    if use_bulk_presign:
        print(
            f"Bulk-presign ON: batch={batch_size}. "
            f"(Falls back to per-file presign automatically on 404.)",
            file=sys.stderr,
        )
    else:
        print("Bulk-presign OFF: using per-file presign.", file=sys.stderr)

    summary = {
        "scanned": total_files,
        "scanned_bytes": total_bytes,
        "to_upload": len(work_items),
        "to_upload_bytes": pending_bytes,
        "skipped_manifest": skipped_manifest,
        "skipped_name_match": skipped_api,
        "deferred_by_limit": capped_by_limit,
        "created": 0,
        "duplicate": 0,
        "error": 0,
        "failures": [],
        "aborted_max_failures": False,
        "concurrency_final": initial_concurrency,
    }

    if not work_items:
        if manifest_path:
            _save_manifest(manifest_path, manifest, input_root, root_folder_id)
        if output_format == "json":
            out_json(summary)
        else:
            print("Nothing to upload.", file=sys.stderr)
        return

    _run_uploads(
        client=client,
        work_items=work_items,
        manifest=manifest,
        manifest_path=manifest_path,
        input_root=input_root,
        root_folder_id=root_folder_id,
        tags=tags,
        initial_concurrency=initial_concurrency,
        min_concurrency=min_concurrency,
        max_concurrency=max_concurrency,
        part_concurrency=part_concurrency,
        auto_tune=auto_tune,
        max_failures=max_failures,
        multipart_threshold=args.multipart_threshold_mb * 1024 * 1024,
        part_size=max(5, args.part_size_mb) * 1024 * 1024,
        pending_bytes=pending_bytes,
        summary=summary,
        batch_size=batch_size,
        use_bulk_presign=use_bulk_presign,
        error_log_path=getattr(args, "error_log", None),
        stats_interval=float(getattr(args, "stats_interval", 0.0) or 0.0),
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
            f"(skipped: {skipped_manifest} manifest, {skipped_api} name match)  "
            f"final concurrency: {summary['concurrency_final']}",
            file=sys.stderr,
        )
        if summary["aborted_max_failures"]:
            print(
                f"  Aborted: failure budget exceeded ({max_failures}).",
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
            rel_dir = "/".join(rel_dir_parts)
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
    name and creating any that don't exist. Serial intentionally: parent
    must exist before children, and parallel POSTs on the same name race
    into duplicate folders.
    """
    cache = {"": root_folder_id}
    children_cache = {}

    def _children_of(parent_id):
        if parent_id in children_cache:
            return children_cache[parent_id]
        m = {}
        if parent_id is None:
            # `folders/folder_tree/` 404s on projects with no folders yet.
            # Fall back to the flat `folders/` listing in that case (same
            # pattern `cmd_folders_tree` uses).
            try:
                tree = client.get("folders/folder_tree/")
                roots = tree if isinstance(tree, list) else tree.get("results", [])
            except _requests.HTTPError:
                all_folders = client.get_all_pages("folders/")
                roots = [f for f in all_folders if f.get("parent") is None]
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


def _fetch_one_folder_names(client, fid):
    """Fetch the name-set for a single folder, paginating internally.

    Uses ``client._request`` directly so we can pass a longer timeout than
    the 30s default — folder listings on the `files/` endpoint can take
    well over that under load.
    """
    names = set()
    page = 1
    while True:
        resp = client._request(
            "GET",
            client._url("files/"),
            params={
                "folder": fid,
                "page": page,
                "page_size": EXISTING_FILES_PAGE_SIZE,
            },
            timeout=EXISTING_FILES_TIMEOUT,
        )
        data = resp.json()
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
    return names


def _fetch_existing_names(client, folder_ids):
    """For each folder id, return the set of names already there.

    Resilience: per-folder retry with exponential backoff on transient
    ConnectionError/HTTPError, parallelized across a small worker pool
    (server load on `files/` is the limiting factor — keep this well
    below upload concurrency). Folders that exhaust their retry budget
    are dropped from the result with a warning; the manifest still
    protects against duplicates for files the caller has seen before.
    """
    result = {}
    failures = []
    folder_list = list(folder_ids)
    if not folder_list:
        return result

    def _fetch_with_retry(fid):
        for attempt in range(EXISTING_FILES_RETRIES):
            try:
                return fid, _fetch_one_folder_names(client, fid), None
            except (_requests.ConnectionError, _requests.HTTPError) as exc:
                if attempt == EXISTING_FILES_RETRIES - 1:
                    return fid, None, exc
                time.sleep(2 ** attempt)
        return fid, None, RuntimeError("unreachable")

    workers = min(EXISTING_FILES_WORKERS, max(1, len(folder_list)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_fetch_with_retry, fid) for fid in folder_list]
        for fut in as_completed(futures):
            fid, names, err = fut.result()
            if err is not None:
                failures.append((fid, err))
            else:
                result[fid] = names

    if failures:
        sample = ", ".join(str(fid) for fid, _ in failures[:5])
        more = "" if len(failures) <= 5 else f" (+{len(failures) - 5} more)"
        print(
            f"Warning: {len(failures)} folders' existing names couldn't be "
            f"fetched after {EXISTING_FILES_RETRIES} attempts: {sample}{more}. "
            f"Their files will be matched against the manifest only — "
            f"duplicate uploads may occur for files added via other means.",
            file=sys.stderr,
        )
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


def _print_dry_run(entries, root_folder_id, manifest, total_bytes,
                   initial_concurrency, max_concurrency, auto_tune, limit):
    # Apply manifest filter so the preview matches the real run. Folder
    # IDs aren't known in dry-run, so we match on (relative_path, size)
    # only — the real run additionally requires folder_id agreement.
    in_manifest = 0
    would_upload = []
    for e in entries:
        mf = manifest["files"].get(e["relative_path"])
        if mf and mf.get("size") == e["size"]:
            in_manifest += 1
            continue
        would_upload.append(e)

    deferred = 0
    if limit and len(would_upload) > limit:
        deferred = len(would_upload) - limit
        would_upload = would_upload[:limit]

    upload_bytes = sum(e["size"] for e in would_upload)

    print(
        f"Dry run — scanned {len(entries)} files ({fmt_size(total_bytes)}) "
        f"under PCXA folder id={root_folder_id or 'root'}.",
        file=sys.stderr,
    )
    print(
        f"  would upload: {len(would_upload)} files ({fmt_size(upload_bytes)}).",
        file=sys.stderr,
    )
    print(
        f"  concurrency: starting {initial_concurrency}, cap {max_concurrency}, "
        f"auto-tune {'ON' if auto_tune else 'OFF'}.",
        file=sys.stderr,
    )
    if in_manifest:
        print(f"  {in_manifest} already in manifest (would skip).", file=sys.stderr)
    if deferred:
        print(f"  --limit deferred {deferred} files to a later run.", file=sys.stderr)

    by_dir = {}
    for e in would_upload:
        by_dir.setdefault(e["relative_dir"], []).append(e)
    for rel_dir, files in sorted(by_dir.items()):
        print(f"  {rel_dir or '.'}/  ({len(files)} files)", file=sys.stderr)
        for e in files[:5]:
            print(f"    {e['name']} ({fmt_size(e['size'])})", file=sys.stderr)
        if len(files) > 5:
            print(f"    ... and {len(files) - 5} more", file=sys.stderr)


# ───────────────────────── runtime concurrency ─────────────────────────


class AdjustableSemaphore:
    """Semaphore whose permit count can grow or shrink at runtime.

    Workers ``acquire()`` before doing chargeable work and ``release()``
    after. ``resize(new_target)`` adjusts available permits up or down;
    shrinking is lazy — existing holders keep their permit and finish.
    """

    def __init__(self, initial, minimum=1, maximum=None):
        self._cond = threading.Condition()
        self._target = initial
        self._available = initial
        self._min = minimum
        self._max = maximum if maximum is not None else initial * 4

    def acquire(self):
        with self._cond:
            while self._available <= 0:
                self._cond.wait()
            self._available -= 1

    def release(self):
        with self._cond:
            self._available += 1
            self._cond.notify()

    def resize(self, new_target):
        with self._cond:
            new_target = max(self._min, min(self._max, int(new_target)))
            delta = new_target - self._target
            self._target = new_target
            self._available += delta
            if delta > 0:
                self._cond.notify(delta)
            return new_target

    @property
    def target(self):
        with self._cond:
            return self._target


def _adaptive_part_size(file_size, requested_part_size):
    """Bump part_size if needed to stay under R2's 10000-part cap."""
    if file_size <= 0:
        return requested_part_size
    parts = math.ceil(file_size / requested_part_size)
    if parts <= R2_PART_HEADROOM:
        return requested_part_size
    # Round up to next 4 MB so we don't end up with awkward sub-MB sizes.
    needed = math.ceil(file_size / R2_PART_HEADROOM)
    rounded = math.ceil(needed / (4 * 1024 * 1024)) * (4 * 1024 * 1024)
    return rounded


# ───────────────────────── bulk-presign helpers ─────────────────────────


def _content_type_for(name):
    import mimetypes
    return mimetypes.guess_type(name)[0] or "application/octet-stream"


def _bulk_presign(client, batch_entries):
    """POST /files/bulk-presign-upload/ for a batch of work-item entries.

    Returns a list aligned with ``batch_entries`` of
    ``(entry, upload_url, storage_key, error)`` tuples. On success the
    entry has the URL and key set; on a per-row failure ``error`` is the
    backend message. Raises ``HTTPError`` for batch-level failures
    (including 404 when the endpoint isn't deployed) so the caller can
    decide whether to fall back to legacy per-file presign.
    """
    payload_items = []
    for entry in batch_entries:
        item = {
            "filename": entry["name"],
            "content_type": _content_type_for(entry["name"]),
            "file_size": entry["size"],
        }
        if entry.get("folder_id") is not None:
            item["folder"] = entry["folder_id"]
        payload_items.append(item)

    resp = client._request(
        "POST",
        client._url("files/bulk-presign-upload/"),
        json={"items": payload_items},
        timeout=60,
    )
    data = resp.json()

    # Default to per-row "no_response" so a malformed/incomplete reply is
    # treated as a per-row error rather than silently skipping uploads.
    out = [(e, None, None, "no_response") for e in batch_entries]
    for row in data.get("results", []):
        idx = row.get("index")
        if idx is None or idx < 0 or idx >= len(batch_entries):
            continue
        entry = batch_entries[idx]
        if row.get("status") == "ok":
            out[idx] = (
                entry,
                row.get("upload_url"),
                row.get("storage_key", ""),
                None,
            )
        else:
            out[idx] = (entry, None, None, row.get("error", "unknown"))
    return out


def _upload_one_presigned(entry, upload_url, storage_key):
    """PUT bytes to a pre-issued presigned URL; return bulk-register item.

    Mirrors the metadata shape returned by :func:`_presign_and_put` so
    the rest of the pipeline (manifest, bulk-register) doesn't need to
    care which presign path produced this row.
    """
    fp = entry["abs_path"]
    content_type = _content_type_for(entry["name"])
    file_size = entry["size"]
    with open(fp, "rb") as fh:
        resp = _requests.put(
            upload_url,
            data=fh,
            headers={
                "Content-Type": content_type,
                "Content-Length": str(file_size),
            },
            timeout=600,
        )
        resp.raise_for_status()
    item = {
        "storage_key": storage_key,
        "original_filename": entry["name"],
        "content_type": content_type,
        "file_size": file_size,
    }
    if entry.get("folder_id") is not None:
        item["folder"] = entry["folder_id"]
    return item


# ───────────────────────── upload loop ─────────────────────────


def _run_uploads(*, client, work_items, manifest, manifest_path, input_root,
                 root_folder_id, tags, initial_concurrency, min_concurrency,
                 max_concurrency, part_concurrency, auto_tune, max_failures,
                 multipart_threshold, part_size, pending_bytes, summary,
                 batch_size, use_bulk_presign,
                 error_log_path=None, stats_interval=0.0):
    register_url = client._url("files/bulk-register/")

    # ── Live error log + periodic stats (issue: troubleshooting low-
    # throughput runs needed mid-run per-file errors, not just
    # end-of-run summary.failures). ──
    #
    # error_log_path: JSON-Lines file. One line per failure with phase
    # (upload/bulk_presign/bulk_register), HTTP status, body excerpt,
    # filename, timing. Append-mode so re-runs accumulate.
    #
    # stats_interval: seconds between periodic JSON stats lines to
    # stderr. 0 disables. Lets operators eyeball real-time throughput
    # / error rate / pool reuse without scraping the progress bar.
    error_log_lock = threading.Lock()
    error_log_fh = None
    if error_log_path:
        try:
            error_log_fh = open(error_log_path, "a", buffering=1)  # line-buffered
        except OSError as exc:
            print(f"warn: --error-log {error_log_path!r}: {exc} (continuing without)",
                  file=sys.stderr)

    def _log_error(*, phase, name, status_code=None, error=None,
                   detail=None, batch_size_=None, **extra):
        if error_log_fh is None:
            return
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "phase": phase,
            "name": name,
        }
        if status_code is not None:
            record["status"] = status_code
        if error is not None:
            record["error"] = str(error)[:500]
        if detail is not None:
            record["detail"] = str(detail)[:500]
        if batch_size_ is not None:
            record["batch_size"] = batch_size_
        record.update({k: v for k, v in extra.items() if v is not None})
        line = json.dumps(record, default=str) + "\n"
        with error_log_lock:
            try:
                error_log_fh.write(line)
            except Exception:
                pass
    pending = []
    pending_lock = threading.Lock()
    state_lock = threading.Lock()
    manifest_lock = threading.Lock()
    log_lock = threading.Lock()
    state = {
        "files_done": 0,
        "bytes_done": 0,
        "last_render": 0.0,
        "started": time.monotonic(),
        "last_manifest_save": time.monotonic(),
        "since_manifest_save": 0,
        "autotune_messages": [],
    }
    interrupted = threading.Event()
    fatal_error: list[BaseException] = []  # at most one element; written by bg thread
    slots = AdjustableSemaphore(
        initial=initial_concurrency,
        minimum=min_concurrency,
        maximum=max_concurrency,
    )

    def _log_autotune(msg):
        with log_lock:
            state["autotune_messages"].append(msg)

    def _flush_locked(items):
        if not items:
            return
        # bulk-register is a heavy multi-row DB write — the default 30s
        # timeout is far too short for batches of 50+ files, and a
        # ConnectionError here means the R2 PUTs already succeeded but
        # the DB rows never landed. Retry on transient errors before
        # giving up and marking the whole batch failed (issue #554).
        resp = None
        for attempt in range(BULK_REGISTER_RETRIES):
            try:
                resp = client._request(
                    "POST", register_url,
                    json={"items": items},
                    timeout=BULK_REGISTER_TIMEOUT,
                )
                break
            except AuthExpiredError as exc:
                # Token expired mid-run and refresh failed. Signal the main
                # loop to stop immediately — retrying this batch won't help.
                if not fatal_error:
                    fatal_error.append(exc)
                interrupted.set()
                return
            except _requests.ConnectionError as exc:
                if attempt == BULK_REGISTER_RETRIES - 1:
                    summary["error"] += len(items)
                    for it in items:
                        summary["failures"].append({
                            "name": it.get("original_filename", "?"),
                            "error": f"bulk-register: {exc}",
                        })
                    _log_error(
                        phase="bulk_register",
                        name=f"<batch of {len(items)}>",
                        error=str(exc),
                        batch_size_=len(items),
                        attempt=attempt + 1,
                    )
                    return
                time.sleep(2 ** attempt)
            except Exception as exc:
                # Capture response body when available — a 400 from
                # bulk-register tells us *why* the row didn't land
                # (validation, missing storage_key, etc.). Stringifying
                # the exception alone loses that.
                detail = ""
                status_code = None
                resp_obj = getattr(exc, "response", None)
                if resp_obj is not None:
                    try:
                        detail = resp_obj.text[:400]
                        status_code = getattr(resp_obj, "status_code", None)
                    except Exception:
                        pass
                # Retry transient server errors (5xx, 429) — a single
                # bulk-register 5xx was counting 80-100 small files as
                # errors at once when a large-file PUT unblocked the
                # bg_flush_worker queue. Client errors (4xx exc. 429)
                # are not retryable.
                if status_code in BULK_REGISTER_RETRY_STATUSES \
                        and attempt < BULK_REGISTER_RETRIES - 1:
                    time.sleep(2 ** attempt)
                    continue
                summary["error"] += len(items)
                for it in items:
                    summary["failures"].append({
                        "name": it.get("original_filename", "?"),
                        "error": f"bulk-register: {exc}"
                                 + (f" detail={detail}" if detail else ""),
                    })
                _log_error(
                    phase="bulk_register",
                    name=f"<batch of {len(items)}>",
                    status_code=status_code,
                    error=str(exc),
                    detail=detail or None,
                    batch_size_=len(items),
                )
                return
        if resp is None:
            return
        try:
            data = resp.json()
            s = data.get("summary", {})
            summary["created"] += s.get("created", 0)
            summary["duplicate"] += s.get("duplicate", 0)
            summary["error"] += s.get("error", 0)
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
                    _log_error(
                        phase="bulk_register_row",
                        name=item.get("original_filename", "?"),
                        error=row.get("error", "unknown"),
                        storage_key=item.get("storage_key"),
                    )
                    continue
                if not rel_path:
                    continue
                with manifest_lock:
                    # Backend's bulk-register response shape is
                    # ``{"status": "created", "id": <pk>, ...}`` — the
                    # field is ``id``, not ``file_id``. The original
                    # read silently wrote ``file_id: null`` into every
                    # manifest entry across the entire upload campaign;
                    # fall back through ``id`` first and accept
                    # ``file_id`` for forward-compat if the backend ever
                    # renames.
                    manifest["files"][rel_path] = {
                        "size": item.get("file_size"),
                        "name": item.get("original_filename"),
                        "folder_id": item.get("folder"),
                        "file_id": row.get("id") or row.get("file_id"),
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

    def _drain_pending_messages():
        """Emit auto-tune messages above the next progress line."""
        with log_lock:
            if not state["autotune_messages"]:
                return False
            msgs = list(state["autotune_messages"])
            state["autotune_messages"].clear()
        # Clear current progress line, then print messages.
        sys.stderr.write("\r" + " " * 140 + "\r")
        for m in msgs:
            sys.stderr.write(m + "\n")
        sys.stderr.flush()
        return True

    def _render(force=False):
        had_msgs = _drain_pending_messages()
        now = time.monotonic()
        with state_lock:
            if not force and not had_msgs and now - state["last_render"] < 0.2:
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
            f"{fmt_size(rate)}/s  c={slots.target:<2}  "
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
            effective_part_size = _adaptive_part_size(size, part_size)
            return _multipart_presign_and_put(
                client, fp, folder=entry["folder_id"],
                part_size=effective_part_size, concurrency=part_concurrency,
            )
        return _presign_and_put(client, fp, folder=entry["folder_id"])

    def _gated_upload(entry):
        if interrupted.is_set():
            raise RuntimeError("interrupted")
        slots.acquire()
        try:
            if interrupted.is_set():
                raise RuntimeError("interrupted")
            return _upload_one(entry)
        finally:
            slots.release()

    def _gated_upload_presigned(entry, upload_url, storage_key):
        if interrupted.is_set():
            raise RuntimeError("interrupted")
        slots.acquire()
        try:
            if interrupted.is_set():
                raise RuntimeError("interrupted")
            return _upload_one_presigned(entry, upload_url, storage_key)
        finally:
            slots.release()

    def _maybe_checkpoint_manifest():
        if not manifest_path:
            return
        with state_lock:
            should = (
                state["since_manifest_save"] >= MANIFEST_CHECKPOINT_FILES
                or time.monotonic() - state["last_manifest_save"]
                >= MANIFEST_CHECKPOINT_SECONDS
            )
        if not should:
            return
        with pending_lock:
            batch = list(pending)
            pending[:] = []
        _flush_locked(batch)
        _save_manifest(manifest_path, manifest, input_root, root_folder_id)
        with state_lock:
            state["since_manifest_save"] = 0
            state["last_manifest_save"] = time.monotonic()

    # Background flush worker. Pulled off the as_completed loop so the
    # synchronous bulk-register POST + manifest save (~1-7s each) no
    # longer stalls upload-completion processing. See issue #661 — the
    # every-50-files dip in the throughput timeline was this stall.
    flush_done = threading.Event()

    def _drain_pending_in_chunks():
        """Drain `pending` and bulk-register in chunks of <= FLUSH_SIZE.

        Server caps bulk-register at 200 items. We use FLUSH_SIZE (100)
        as the chunk size to leave headroom and match the legacy
        behavior. Returns True if any items were flushed.
        """
        any_flushed = False
        while True:
            with pending_lock:
                if not pending:
                    break
                batch = pending[:BULK_REGISTER_FLUSH_SIZE]
                del pending[:BULK_REGISTER_FLUSH_SIZE]
            _flush_locked(batch)
            any_flushed = True
        return any_flushed

    def _flush_worker():
        while not flush_done.is_set():
            did_work = False
            # 1) Threshold-based bulk-register.
            with pending_lock:
                need = len(pending) >= BULK_REGISTER_FLUSH_SIZE
            if need:
                with pending_lock:
                    batch = pending[:BULK_REGISTER_FLUSH_SIZE]
                    del pending[:BULK_REGISTER_FLUSH_SIZE]
                if batch:
                    _flush_locked(batch)
                    did_work = True
            # 2) Periodic manifest checkpoint.
            if manifest_path:
                with state_lock:
                    since = state["since_manifest_save"]
                    elapsed = time.monotonic() - state["last_manifest_save"]
                if since >= MANIFEST_CHECKPOINT_FILES \
                        or elapsed >= MANIFEST_CHECKPOINT_SECONDS:
                    # Drain in chunks so we never exceed the server cap.
                    _drain_pending_in_chunks()
                    _save_manifest(
                        manifest_path, manifest, input_root, root_folder_id,
                    )
                    with state_lock:
                        state["since_manifest_save"] = 0
                        state["last_manifest_save"] = time.monotonic()
                    did_work = True
            if not did_work:
                # No work — short nap. ~10 polls/sec is plenty granular
                # and the CPU cost is negligible.
                time.sleep(0.1)

    flush_thread = threading.Thread(target=_flush_worker, daemon=True)
    flush_thread.start()

    # Periodic stats emitter. JSON line per interval to stderr so the
    # operator can eyeball real-time throughput / error rate / pool
    # reuse without scraping the progress bar. Off by default (0 sec).
    stats_thread = None
    if stats_interval and stats_interval > 0:
        from pcxa._http import _STATS as _http_stats, _STATS_LOCK as _http_stats_lock

        def _stats_worker():
            last_done = 0
            last_bytes = 0
            last_t = time.monotonic()
            while not flush_done.is_set() and not interrupted.is_set():
                # Sleep in 0.5s slices so shutdown is responsive.
                slept = 0.0
                while slept < stats_interval:
                    if flush_done.is_set() or interrupted.is_set():
                        return
                    time.sleep(0.5)
                    slept += 0.5
                now = time.monotonic()
                with state_lock:
                    done = state["files_done"]
                    bdone = state["bytes_done"]
                d_done = done - last_done
                d_bytes = bdone - last_bytes
                window = max(0.001, now - last_t)
                with _http_stats_lock:
                    http_total = (_http_stats["new_conns"]
                                  + _http_stats["reused_conns"])
                    reuse_rate = (100.0 * _http_stats["reused_conns"]
                                  / http_total) if http_total else 0.0
                    new_conns = _http_stats["new_conns"]
                    retries = _http_stats["retries_on_disconnect"]
                rec = {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "kind": "stats",
                    "files_done": done,
                    "bytes_done": bdone,
                    "window_files_per_s": round(d_done / window, 2),
                    "window_MB_per_s": round(d_bytes / window / 1e6, 2),
                    "concurrency": slots.target,
                    "errors": summary["error"],
                    "http_reuse_pct": round(reuse_rate, 1),
                    "http_new_conns": new_conns,
                    "http_retries_on_disconnect": retries,
                }
                try:
                    sys.stderr.write("\r" + " " * 140 + "\r")
                    sys.stderr.write(json.dumps(rec) + "\n")
                    sys.stderr.flush()
                except Exception:
                    pass
                last_done, last_bytes, last_t = done, bdone, now

        stats_thread = threading.Thread(target=_stats_worker, daemon=True)
        stats_thread.start()

    # AIMD controller thread.
    controller_thread = None
    if auto_tune:
        controller_thread = threading.Thread(
            target=_run_controller,
            args=(slots, state, summary, interrupted,
                  min_concurrency, max_concurrency, _log_autotune),
            daemon=True,
        )
        controller_thread.start()

    _render(force=True)

    # Mutable container so the feeder thread can flip it off after a 404
    # and the change persists for the rest of the run.
    bulk_flag = [bool(use_bulk_presign)]

    # ── Rolling pipeline: presign feeder + continuous submit/drain ──
    #
    # Replaces the prior per-batch barrier (where `_process_batch` would
    # bulk-presign N files, submit N futures, then wait for ALL N to
    # complete before moving on). That barrier wasted concurrency at the
    # tail of every batch — slowest 1-2 files held back the next batch's
    # entire submission.
    #
    # Now: a feeder thread pre-presigns the next chunk while the upload
    # pool keeps eating from a bounded ready_queue. The queue cap
    # (batch_size * 2) caps memory and keeps presigned URLs fresh —
    # 15-min TTL means we don't want a 60K-item feeder lead.
    ready_queue = _queue.Queue(maxsize=max(2, batch_size * 2))
    feeder_done = threading.Event()
    # Sentinel for per-item bulk-presign errors that need to surface as
    # immediate failures in the main loop without going through the pool.
    _PRESIGN_ERR = object()

    def _presign_feeder():
        try:
            for batch_start in range(0, len(work_items), batch_size):
                if interrupted.is_set():
                    break
                batch = work_items[batch_start:batch_start + batch_size]
                small = [e for e in batch
                         if e["size"] <= multipart_threshold]
                # Multipart files use their own per-file presign path
                # inside _upload_one — feeder just forwards them.
                large = [e for e in batch
                         if e["size"] > multipart_threshold]
                for e in large:
                    if interrupted.is_set():
                        return
                    ready_queue.put((e, None, None))

                presigned = {}
                if bulk_flag[0] and small:
                    try:
                        results = _bulk_presign(client, small)
                    except _requests.HTTPError as exc:
                        code = getattr(exc.response, "status_code", 0)
                        if code == 404:
                            bulk_flag[0] = False
                            _log_autotune(
                                "[bulk-presign] endpoint not deployed "
                                "(404) — falling back to per-file "
                                "presign for the rest of this run"
                            )
                        else:
                            _log_autotune(
                                f"[bulk-presign] HTTP {code}: {exc} — "
                                "using legacy presign for this batch"
                            )
                        results = []
                    except Exception as exc:
                        _log_autotune(
                            f"[bulk-presign] {exc} — using legacy "
                            "presign for this batch"
                        )
                        results = []
                    for entry, url, key, err in results:
                        if err:
                            ready_queue.put((entry, _PRESIGN_ERR, err))
                        elif url:
                            presigned[id(entry)] = (url, key)
                for e in small:
                    if interrupted.is_set():
                        return
                    pair = presigned.get(id(e))
                    if pair is not None:
                        ready_queue.put((e, pair[0], pair[1]))
                    else:
                        # No bulk URL → fall through to _gated_upload's
                        # per-file presign-and-put path.
                        ready_queue.put((e, None, None))
        finally:
            feeder_done.set()

    feeder_thread = threading.Thread(target=_presign_feeder, daemon=True)
    feeder_thread.start()

    def _handle_completion(future, entry):
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
            # Pull HTTP detail off the exception if present so the live
            # log distinguishes R2 PUT 403 from backend presign 5xx from
            # plain ConnectionError. The CLI's HTTPError carries
            # ``.response.text`` and ``.response.status_code``; bare
            # ConnectionError doesn't.
            status_code = None
            detail = None
            resp_obj = getattr(exc, "response", None)
            if resp_obj is not None:
                try:
                    status_code = getattr(resp_obj, "status_code", None)
                    detail = resp_obj.text[:400]
                except Exception:
                    pass
            _log_error(
                phase="upload",
                name=entry["name"],
                status_code=status_code,
                error=str(exc),
                detail=detail,
                rel_path=entry.get("relative_path"),
                size=entry.get("size"),
            )
        _render()
        if max_failures and summary["error"] >= max_failures \
                and not interrupted.is_set():
            interrupted.set()
            summary["aborted_max_failures"] = True
            _log_autotune(
                f"[abort] failure budget exceeded "
                f"({summary['error']}/{max_failures}) — stopping."
            )

    # Cap on submitted-but-not-completed work. The slots semaphore is
    # still the real concurrency gate; this just keeps the executor's
    # internal queue (and memory of presigned URLs in flight) bounded.
    in_flight_cap = max(max_concurrency * 4, 16)

    try:
        with ThreadPoolExecutor(max_workers=max_concurrency) as pool:
            in_flight = {}  # future -> entry
            while True:
                if interrupted.is_set():
                    # Drain remaining futures so they don't get cancelled
                    # mid-PUT and leak partial uploads. _gated_upload
                    # checks interrupted internally.
                    for fut in list(in_flight.keys()):
                        entry = in_flight.pop(fut)
                        try:
                            _handle_completion(fut, entry)
                        except Exception:
                            pass
                    break

                # Submit ready work while we have headroom.
                submitted_any = False
                while len(in_flight) < in_flight_cap:
                    try:
                        work = ready_queue.get_nowait()
                    except _queue.Empty:
                        break
                    entry, url_or_marker, key_or_err = work
                    if url_or_marker is _PRESIGN_ERR:
                        # Per-item bulk-presign failure — surface
                        # immediately, no pool submit.
                        summary["error"] += 1
                        summary["failures"].append({
                            "name": entry["name"],
                            "error": f"bulk-presign: {key_or_err}",
                        })
                        with state_lock:
                            state["files_done"] += 1
                        _log_error(
                            phase="bulk_presign",
                            name=entry["name"],
                            error=str(key_or_err),
                            rel_path=entry.get("relative_path"),
                            size=entry.get("size"),
                        )
                        _render()
                        submitted_any = True
                        continue
                    if url_or_marker is not None:
                        fut = pool.submit(
                            _with_retry, _gated_upload_presigned,
                            entry, url_or_marker, key_or_err,
                        )
                    else:
                        fut = pool.submit(
                            _with_retry, _gated_upload, entry,
                        )
                    in_flight[fut] = entry
                    submitted_any = True

                # Termination: feeder done, queue empty, nothing in flight.
                if not in_flight:
                    if feeder_done.is_set() and ready_queue.empty():
                        break
                    if not submitted_any:
                        # Feeder is producing but pipeline is idle —
                        # don't spin.
                        time.sleep(0.02)
                    continue

                # Wait for at least one completion before looping back to
                # the submit phase. Short timeout so we can poll the
                # interrupt flag and re-fill the pipeline promptly.
                done_futs, _ = wait(
                    list(in_flight.keys()),
                    timeout=0.1,
                    return_when=FIRST_COMPLETED,
                )
                for fut in done_futs:
                    entry = in_flight.pop(fut)
                    _handle_completion(fut, entry)
    except KeyboardInterrupt:
        interrupted.set()
        _log_autotune("[interrupted] flushing partial progress...")

    interrupted.set()  # signal controller to stop
    if controller_thread:
        controller_thread.join(timeout=2.0)

    # Stop the background flush worker before we do the final drain, so
    # we don't race it for the last `pending` items.
    flush_done.set()
    flush_thread.join(timeout=10.0)
    if stats_thread is not None:
        stats_thread.join(timeout=2.0)

    if fatal_error:
        print(
            f"\nFatal: {fatal_error[0]}\n"
            "Run `pcxa login` then restart from this chunk.",
            file=sys.stderr,
        )
        sys.exit(2)

    # Final drain — chunked so we never exceed the server's 200-item cap.
    _drain_pending_in_chunks()
    if manifest_path:
        _save_manifest(manifest_path, manifest, input_root, root_folder_id)
    summary["concurrency_final"] = slots.target
    _render(force=True)
    sys.stderr.write("\n")
    sys.stderr.flush()
    if error_log_fh is not None:
        try:
            error_log_fh.close()
        except Exception:
            pass


# ───────────────────────── auto-tune controller ─────────────────────────


def _run_controller(slots, state, summary, interrupted, min_c, max_c, log,
                    window_seconds=10.0, cooldown_seconds=30.0):
    """AIMD controller. Samples throughput + error count every window.

    Decisions per window:
      • errors >= 3 OR err_rate > 10%  → multiplicative decrease (halve),
        followed by a cooldown to avoid thrashing.
      • throughput growing AND below cap → additive increase (+max(1, c/4)).
      • throughput falling materially AND above floor → step down by 1.
      • otherwise hold.
    """
    last = {
        "bytes": 0,
        "files": 0,
        "errors": 0,
        "throughput": 0.0,
    }
    cooldown_until = 0.0
    # Wait one window before the first decision so we have a baseline.
    interrupted.wait(window_seconds)

    while not interrupted.is_set():
        bytes_now = state["bytes_done"]
        files_now = state["files_done"]
        errors_now = summary["error"]
        win_bytes = bytes_now - last["bytes"]
        win_files = files_now - last["files"]
        win_errors = errors_now - last["errors"]
        win_throughput = win_bytes / window_seconds
        win_err_rate = (win_errors / win_files) if win_files else 0.0
        now = time.monotonic()
        current = slots.target

        prev_throughput = last["throughput"]

        if win_errors >= 3 or win_err_rate > 0.10:
            new_c = max(min_c, current // 2)
            if new_c < current:
                slots.resize(new_c)
                cooldown_until = now + cooldown_seconds
                log(
                    f"[auto-tune] {win_errors} errors in {int(window_seconds)}s "
                    f"({win_err_rate*100:.0f}%) — concurrency {current} -> {new_c}"
                )
        elif now < cooldown_until:
            pass
        elif win_files == 0:
            # No completions this window: maybe huge files in flight.
            # Hold steady so we don't keep nudging while waiting.
            pass
        elif current < max_c and (
            prev_throughput == 0
            or win_throughput >= prev_throughput * 1.05
        ):
            step = max(1, current // 4)
            new_c = min(max_c, current + step)
            if new_c > current:
                slots.resize(new_c)
                log(
                    f"[auto-tune] throughput {_fmt_mbps(prev_throughput)} -> "
                    f"{_fmt_mbps(win_throughput)} — concurrency {current} -> {new_c}"
                )
        elif current > min_c and prev_throughput > 0 \
                and win_throughput < prev_throughput * 0.85:
            new_c = max(min_c, current - 1)
            slots.resize(new_c)
            log(
                f"[auto-tune] throughput {_fmt_mbps(prev_throughput)} -> "
                f"{_fmt_mbps(win_throughput)} — concurrency {current} -> {new_c}"
            )

        last["bytes"] = bytes_now
        last["files"] = files_now
        last["errors"] = errors_now
        last["throughput"] = win_throughput
        interrupted.wait(window_seconds)


def _warn_if_token_short(client, threshold_seconds=600):
    """Surface a one-line warning if the access_token won't outlive a typical
    sync. The client refreshes proactively now, but a heads-up here lets the
    user run ``pcxa login`` *before* a TB-scale run instead of relying on
    mid-run refresh to keep working.
    """
    remaining = getattr(client, "access_token_expires_in", lambda: None)()
    if remaining is None or remaining > threshold_seconds:
        return
    if remaining <= 0:
        print(
            "Warning: access_token already expired — will refresh on first request. "
            "If refresh fails, run `pcxa login` and re-run.",
            file=sys.stderr,
        )
    else:
        mins = max(1, int(remaining // 60))
        print(
            f"Warning: access_token expires in ~{mins} min. Auto-refresh will "
            f"kick in, but for long syncs consider running `pcxa login` first.",
            file=sys.stderr,
        )


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


def _fmt_mbps(bps):
    return f"{bps / (1024 * 1024):.1f}MB/s"


__all__ = ["cmd_files_sync"]
