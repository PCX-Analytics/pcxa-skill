"""Bring-your-own-chunks: push pre-computed chunks + embeddings into PCXA.

For a caller that runs its own extraction/chunking/embedding pipeline and wants
PCXA to serve *its* index rather than re-derive one. Files must already exist
(``pcxa files sync`` / ``files upload``); chunks attach to them.

Input is JSON-Lines — one record per file, streamed, so a multi-million-chunk
corpus never has to fit in memory::

    {"file_id": 123, "chunks": [
        {"chunk_index": 0, "content": "...", "embedding": [768 floats]},
        {"chunk_index": 1, "content": "...", "embedding": [768 floats]}
    ]}

``path`` or ``name`` may be used instead of ``file_id`` when ``--manifest``
points at the manifest a prior ``files sync`` wrote — that file already maps
local path → file_id, so the two commands compose without the caller
maintaining its own id table.

Two things about this command are load-bearing and easy to get wrong:

**Pacing defaults to the vector-lake drain rate, not the API rate limit.**
Every upserted vector is mirrored from Pinecone (the rebuildable serving index)
into R2/Lance (the system-of-record) through a DB outbox drained by a single
writer at ~60k chunks/hour. The endpoint will happily accept ~150x that. Past
the outbox's depth ceiling the mirror *sheds*: vectors serve fine but the
durable copy is dropped, recoverable only by an operator running
``manage.py reconcile_vector_lake --apply`` afterwards. So the default is to
pace to the drain rate. ``--chunks-per-hour 0`` opts out; do that only if
someone has agreed to run the reconcile.

**Embeddings are all-or-nothing per file.** The server marks a file INDEXED
only when *every* chunk in it carries a vector; one missing embedding demotes
the whole file to CHUNKED and hands it to our embedder, which costs money the
caller was trying to avoid. Partial files are therefore rejected client-side
rather than silently re-embedded.
"""

import json
import random
import sys
import time
from pathlib import Path

from pcxa._output import out_json

# ── Server contract, mirrored so we fail fast with a useful message instead of
# posting a payload the serializer will reject. Keep in sync with
# api/semantic_search/serializers.py (STAFF_UPLOAD_MAX_*).
MAX_FILES_PER_REQUEST = 50
MAX_CHUNKS_PER_REQUEST = 5_000
MAX_CHUNKS_PER_FILE = 2_000
MAX_CONTENT_CHARS = 16_000
EMBEDDING_DIMENSIONS = 768
DEFAULT_EMBEDDING_MODEL = "gemini-embedding-001"

# Single-writer vector-lake drain: 5,000 rows per */5min tick (see the module
# docstring). This is the real throughput ceiling for a durable load.
LAKE_DRAIN_CHUNKS_PER_HOUR = 60_000

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
RETRIES = 4
UPLOAD_TIMEOUT = 300

STATE_VERSION = 1


class ChunkInputError(ValueError):
    """A record is unusable. Message is shown to the user verbatim."""


# ── input ────────────────────────────────────────────────────────────────────


def _iter_jsonl(paths):
    """Yield ``(source, line_no, record)`` across files/dirs, streaming.

    Directories are walked for ``*.jsonl`` in sorted order so a resumed run
    sees the same sequence as the first one.
    """
    for raw in paths:
        p = Path(raw).expanduser()
        if p.is_dir():
            files = sorted(p.rglob("*.jsonl"))
            if not files:
                print(f"  {p}: no .jsonl files found", file=sys.stderr)
        elif p.exists():
            files = [p]
        else:
            raise ChunkInputError(f"input not found: {p}")

        for fp in files:
            with open(fp, "r", encoding="utf-8") as fh:
                for line_no, line in enumerate(fh, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield str(fp), line_no, json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise ChunkInputError(f"{fp}:{line_no}: invalid JSON — {exc}") from exc


def _load_manifest_index(manifest_path):
    """Build {path: file_id} and {name: file_id} from a `files sync` manifest.

    Names can collide across folders, so a name that maps to more than one id is
    dropped rather than guessed — resolving it to the wrong file would overwrite
    the wrong document's index.
    """
    if not manifest_path:
        return {}, {}
    p = Path(manifest_path).expanduser()
    if not p.exists():
        raise ChunkInputError(f"--manifest not found: {p}")
    try:
        with open(p, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as exc:
        raise ChunkInputError(f"--manifest unreadable ({p}): {exc}") from exc

    by_path, name_hits = {}, {}
    for rel_path, entry in (data.get("files") or {}).items():
        if not isinstance(entry, dict):
            continue
        fid = entry.get("file_id")
        if not fid:
            continue  # skipped/duplicate entries carry no id
        by_path[rel_path] = fid
        nm = entry.get("name")
        if nm:
            name_hits.setdefault(nm, set()).add(fid)

    by_name = {n: next(iter(ids)) for n, ids in name_hits.items() if len(ids) == 1}
    ambiguous = len(name_hits) - len(by_name)
    if ambiguous:
        print(
            f"  manifest: {ambiguous} filename(s) map to multiple files — "
            f"those must be addressed by 'path' or 'file_id'",
            file=sys.stderr,
        )
    return by_path, by_name


# ── validation ───────────────────────────────────────────────────────────────


def _validate_embedding(vec, where):
    if not isinstance(vec, (list, tuple)):
        raise ChunkInputError(f"{where}: embedding must be a list of numbers")
    if len(vec) != EMBEDDING_DIMENSIONS:
        raise ChunkInputError(
            f"{where}: embedding has {len(vec)} dimensions, expected "
            f"{EMBEDDING_DIMENSIONS}. Vectors from a different model are not "
            f"interoperable with this index."
        )
    for v in vec:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ChunkInputError(f"{where}: embedding contains a non-numeric value ({v!r})")
    return [float(v) for v in vec]


def _validate_record(record, *, source, line_no, by_path, by_name):
    """Normalize one JSONL record into an API ``items[]`` entry.

    Every rejection here is one the server would also make, surfaced before the
    request so a 5,000-chunk batch isn't discarded for one bad row.
    """
    where = f"{source}:{line_no}"
    if not isinstance(record, dict):
        raise ChunkInputError(f"{where}: expected a JSON object")

    file_id = record.get("file_id")
    if file_id is None:
        key = record.get("path") or record.get("name")
        if not key:
            raise ChunkInputError(f"{where}: record needs one of file_id / path / name")
        file_id = by_path.get(key) or by_name.get(key)
        if file_id is None:
            raise ChunkInputError(
                f"{where}: {key!r} is not in the manifest. Pass --manifest from the "
                f"`files sync` run that uploaded it, or use an explicit file_id."
            )
    try:
        file_id = int(file_id)
    except (TypeError, ValueError):
        raise ChunkInputError(f"{where}: file_id must be an integer (got {file_id!r})") from None

    chunks_in = record.get("chunks")
    if not isinstance(chunks_in, list) or not chunks_in:
        raise ChunkInputError(f"{where}: 'chunks' must be a non-empty list")
    if len(chunks_in) > MAX_CHUNKS_PER_FILE:
        raise ChunkInputError(
            f"{where}: {len(chunks_in)} chunks exceeds the {MAX_CHUNKS_PER_FILE}-per-file "
            f"server limit. Split the document."
        )

    seen_idx = set()
    out_chunks = []
    embedded = 0
    for i, c in enumerate(chunks_in):
        cw = f"{where} chunk[{i}]"
        if not isinstance(c, dict):
            raise ChunkInputError(f"{cw}: expected an object")

        idx = c.get("chunk_index", i)
        try:
            idx = int(idx)
        except (TypeError, ValueError):
            raise ChunkInputError(f"{cw}: chunk_index must be an integer") from None
        if idx < 0:
            raise ChunkInputError(f"{cw}: chunk_index must be >= 0")
        if idx in seen_idx:
            raise ChunkInputError(f"{cw}: duplicate chunk_index {idx} within this file")
        seen_idx.add(idx)

        content = c.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ChunkInputError(f"{cw}: 'content' must be a non-empty string")
        if len(content) > MAX_CONTENT_CHARS:
            raise ChunkInputError(
                f"{cw}: content is {len(content)} chars, over the {MAX_CONTENT_CHARS} limit. "
                f"Chunk smaller."
            )

        item = {"chunk_index": idx, "content": content}

        chash = c.get("content_hash")
        if chash:
            if not (isinstance(chash, str) and len(chash) == 64):
                raise ChunkInputError(f"{cw}: content_hash must be a 64-char sha256 hex digest")
            item["content_hash"] = chash

        page = c.get("page_number")
        if page is not None:
            try:
                page = int(page)
            except (TypeError, ValueError):
                raise ChunkInputError(f"{cw}: page_number must be an integer") from None
            if page < 1:
                raise ChunkInputError(f"{cw}: page_number is 1-based, got {page}")
            item["page_number"] = page

        md = c.get("metadata")
        if md is not None:
            if not isinstance(md, dict):
                raise ChunkInputError(f"{cw}: metadata must be an object")
            item["metadata"] = md

        emb = c.get("embedding")
        if emb is not None:
            item["embedding"] = _validate_embedding(emb, cw)
            embedded += 1

        out_chunks.append(item)

    # All-or-nothing: see module docstring. A partial file silently costs money.
    if embedded and embedded != len(out_chunks):
        raise ChunkInputError(
            f"{where}: {embedded}/{len(out_chunks)} chunks carry an embedding. The server "
            f"marks a file INDEXED only when every chunk has one — a partial file is demoted "
            f"to CHUNKED and re-embedded at our cost. Supply all embeddings, or none "
            f"(and let our embedder do the whole file)."
        )

    item = {"file_id": file_id, "chunks": out_chunks}
    for opt in ("file_version_id", "indexed_content_hash", "document_summary",
                "document_context_strategy"):
        if record.get(opt) is not None:
            item[opt] = record[opt]

    return item, bool(embedded)


# ── pacing ───────────────────────────────────────────────────────────────────


class Pacer:
    """Sleep so the cumulative chunk rate stays under ``chunks_per_hour``.

    Deliberately a cumulative-average governor rather than a token bucket: the
    thing being protected is a *backlog* (the lake outbox), so what matters is
    total chunks over total elapsed time, not smoothness. 0 disables.
    """

    def __init__(self, chunks_per_hour):
        self.limit = max(0, int(chunks_per_hour or 0))
        self.sent = 0
        self.started = time.monotonic()
        self.slept = 0.0

    def record_and_wait(self, n_chunks):
        self.sent += n_chunks
        if not self.limit:
            return 0.0
        target_elapsed = self.sent / self.limit * 3600.0
        actual_elapsed = time.monotonic() - self.started
        delay = target_elapsed - actual_elapsed
        if delay <= 0:
            return 0.0
        time.sleep(delay)
        self.slept += delay
        return delay


# ── transport ────────────────────────────────────────────────────────────────


def _post_batch(client, payload, *, timeout):
    """POST one batch, retrying 429/5xx with Retry-After when offered."""
    from pcxa._http import HTTPError
    from pcxa._http import requests as _rq

    last_exc = None
    for attempt in range(RETRIES):
        try:
            return client.post(
                "semantic-search/upload-chunks/", json_data=payload, timeout=timeout
            )
        except HTTPError as exc:
            last_exc = exc
            resp = getattr(exc, "response", None)
            code = getattr(resp, "status_code", 0)
            if code not in RETRY_STATUSES:
                raise
            wait = None
            headers = getattr(resp, "headers", None) or {}
            for key in ("Retry-After", "retry-after"):
                if key in headers:
                    try:
                        wait = float(headers[key])
                    except (TypeError, ValueError):
                        wait = None
                    break
            if wait is None:
                wait = 2.0 * (2 ** attempt) + random.uniform(0, 0.5)
            if code == 429:
                print(f"  rate limited (429) — waiting {wait:.0f}s", file=sys.stderr)
            time.sleep(wait)
        except (_rq.ConnectionError, OSError) as exc:
            last_exc = exc
            time.sleep(2.0 * (2 ** attempt) + random.uniform(0, 0.5))
    raise last_exc


# ── resume state ─────────────────────────────────────────────────────────────


def _load_state(path):
    if not path:
        return {"version": STATE_VERSION, "applied": []}
    p = Path(path).expanduser()
    if not p.exists():
        return {"version": STATE_VERSION, "applied": []}
    try:
        with open(p, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and isinstance(data.get("applied"), list):
            return data
    except Exception as exc:
        print(f"  state read failed ({p}): {exc} — starting fresh", file=sys.stderr)
    return {"version": STATE_VERSION, "applied": []}


def _save_state(path, applied):
    if not path:
        return
    p = Path(path).expanduser()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"version": STATE_VERSION, "applied": sorted(applied)}, fh)
        tmp.replace(p)
    except Exception as exc:
        print(f"  state write failed ({p}): {exc}", file=sys.stderr)


# ── command ──────────────────────────────────────────────────────────────────


def cmd_files_upload_chunks(client, args):
    """Upload pre-computed chunks (and optional embeddings) for existing files."""
    as_json = getattr(args, "format", "table") == "json"
    model = args.embedding_model or DEFAULT_EMBEDDING_MODEL

    # In --format json, stdout must be nothing but the summary object — progress
    # on stdout would make the output unparseable for the caller piping it.
    def _progress(msg):
        print(msg, file=sys.stderr if as_json else sys.stdout)

    files_per_req = min(max(1, args.files_per_request), MAX_FILES_PER_REQUEST)
    chunks_per_req = min(max(1, args.chunks_per_request), MAX_CHUNKS_PER_REQUEST)

    try:
        by_path, by_name = _load_manifest_index(args.manifest)
    except ChunkInputError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    state = _load_state(args.state)
    applied = set(state.get("applied") or [])
    if applied and not args.dry_run:
        _progress(f"Resuming: {len(applied)} file(s) already applied — skipping them.")

    pacer = Pacer(0 if args.dry_run else args.chunks_per_hour)
    if pacer.limit and not args.dry_run:
        _progress(
            f"Pacing at {pacer.limit:,} chunks/hour to match the vector-lake drain rate. "
            f"Use --chunks-per-hour 0 to disable (then an operator must run "
            f"`reconcile_vector_lake --apply` afterwards)."
        )

    summary = {
        "files_applied": 0, "files_skipped_resume": 0, "files_rejected": 0,
        "chunks_sent": 0, "requests": 0, "with_embeddings": 0, "without_embeddings": 0,
        "errors": [],
    }
    errors_log = open(args.error_log, "a", encoding="utf-8") if args.error_log else None

    batch, batch_chunks, batch_ids = [], 0, []
    queued = 0
    aborted = False

    def _record_error(msg, *, file_id=None):
        summary["errors"].append(msg)
        print(f"  ! {msg}", file=sys.stderr)
        if errors_log:
            errors_log.write(json.dumps({"file_id": file_id, "error": msg}) + "\n")
            errors_log.flush()

    def _flush():
        nonlocal batch, batch_chunks, batch_ids, aborted
        if not batch:
            return
        payload = {"embedding_model": model, "items": batch}
        n_files, n_chunks = len(batch), batch_chunks
        if args.dry_run:
            _progress(f"  [dry-run] would POST {n_files} file(s) / {n_chunks} chunk(s)")
            summary["files_applied"] += n_files
            summary["chunks_sent"] += n_chunks
            summary["requests"] += 1
        else:
            try:
                data = _post_batch(
                    client, payload,
                    timeout=getattr(args, "http_timeout", None) or UPLOAD_TIMEOUT,
                )
            except Exception as exc:
                _record_error(f"batch of {n_files} file(s) failed: {exc}")
                batch, batch_chunks, batch_ids = [], 0, []
                if args.max_failures and len(summary["errors"]) >= args.max_failures:
                    aborted = True
                return
            summary["requests"] += 1
            ok_ids = []
            for row in data.get("results", []):
                fid = row.get("file_id")
                if row.get("error"):
                    _record_error(f"file {fid}: {row['error']}", file_id=fid)
                else:
                    ok_ids.append(fid)
                    summary["files_applied"] += 1
            summary["chunks_sent"] += n_chunks
            applied.update(ok_ids)
            _save_state(args.state, applied)
            slept = pacer.record_and_wait(n_chunks)
            rate = summary["chunks_sent"] / max(1e-9, time.monotonic() - pacer.started) * 3600
            _progress(
                f"  {summary['files_applied']:,} file(s), {summary['chunks_sent']:,} chunk(s) "
                f"— {rate:,.0f}/hr"
                + (f" (paced +{slept:.0f}s)" if slept else "")
            )
            if args.max_failures and len(summary["errors"]) >= args.max_failures:
                aborted = True
        batch, batch_chunks, batch_ids = [], 0, []

    try:
        for source, line_no, record in _iter_jsonl(args.paths):
            if aborted:
                break
            try:
                item, has_emb = _validate_record(
                    record, source=source, line_no=line_no,
                    by_path=by_path, by_name=by_name,
                )
            except ChunkInputError as exc:
                summary["files_rejected"] += 1
                _record_error(str(exc))
                if args.max_failures and len(summary["errors"]) >= args.max_failures:
                    print(
                        f"error: aborting after {len(summary['errors'])} failures "
                        f"(--max-failures)", file=sys.stderr,
                    )
                    aborted = True
                    break
                continue

            if item["file_id"] in applied:
                summary["files_skipped_resume"] += 1
                continue

            n = len(item["chunks"])
            if n > chunks_per_req:
                summary["files_rejected"] += 1
                _record_error(
                    f"{source}:{line_no}: file {item['file_id']} has {n} chunks, over the "
                    f"{chunks_per_req} per-request budget — raise --chunks-per-request "
                    f"(server max {MAX_CHUNKS_PER_REQUEST}) or split the document."
                )
                continue

            if batch and (len(batch) >= files_per_req or batch_chunks + n > chunks_per_req):
                _flush()
                if aborted:
                    break

            batch.append(item)
            batch_chunks += n
            batch_ids.append(item["file_id"])
            summary["with_embeddings" if has_emb else "without_embeddings"] += 1
            queued += 1
            if args.limit and queued >= args.limit:
                break

        if not aborted:
            _flush()
    except ChunkInputError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted — state saved; re-run to resume", file=sys.stderr)
        _save_state(args.state, applied)
        return 130
    finally:
        if errors_log:
            errors_log.close()

    if summary["without_embeddings"] and not args.dry_run:
        print(
            f"\nNote: {summary['without_embeddings']} file(s) had no embeddings and will be "
            f"embedded by our pipeline (billable). Files with embeddings skip that step.",
            file=sys.stderr,
        )

    if as_json:
        out_json(summary)
    else:
        print()
        print(f"Done: {summary['files_applied']:,} file(s) applied, "
              f"{summary['chunks_sent']:,} chunk(s), {summary['requests']:,} request(s)")
        if summary["files_skipped_resume"]:
            print(f"  skipped (already applied): {summary['files_skipped_resume']:,}")
        if summary["files_rejected"]:
            print(f"  rejected before send:      {summary['files_rejected']:,}")
        if summary["errors"]:
            print(f"  errors:                    {len(summary['errors']):,}")
        if pacer.slept:
            print(f"  paced (slept):             {pacer.slept / 60:.1f} min")

    return 1 if (summary["errors"] or aborted) else 0


def cmd_files_set_index_mode(client, args):
    """Bulk-set File.index_mode — the prep step before a BYOC corpus load.

    ``none`` is what stops our own chunker from processing files whose chunks
    are about to be supplied. Without it every file is chunked server-side
    first: harmless (the upload replaces it) but pure waste at corpus scale,
    and XLSX in particular is a known CPU hog on the ingest workers.
    """
    as_json = getattr(args, "format", "table") == "json"

    raw_tokens = list(args.file_ids or [])

    # --ids-file / stdin, mirroring `files purge` — a corpus's worth of ids does
    # not fit in argv.
    ids_file = getattr(args, "ids_file", None)
    if ids_file:
        try:
            text = sys.stdin.read() if ids_file == "-" else Path(ids_file).expanduser().read_text()
        except OSError as exc:
            print(f"error: --ids-file unreadable: {exc}", file=sys.stderr)
            return 1
        raw_tokens.extend(text.replace(",", " ").split())

    # --from-manifest: the corpus you just uploaded, without a jq incantation.
    from_manifest = getattr(args, "from_manifest", None)
    if from_manifest:
        try:
            by_path, _ = _load_manifest_index(from_manifest)
        except ChunkInputError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        if not by_path:
            print(f"error: no file ids in manifest {from_manifest}", file=sys.stderr)
            return 1
        raw_tokens.extend(str(v) for v in by_path.values())

    file_ids = []
    for raw in raw_tokens:
        for part in str(raw).split(","):
            part = part.strip()
            if part:
                try:
                    file_ids.append(int(part))
                except ValueError:
                    print(f"error: not an integer file id: {part!r}", file=sys.stderr)
                    return 1
    # Dedupe, preserving order (mirrors `files purge`).
    seen = set()
    file_ids = [i for i in file_ids if not (i in seen or seen.add(i))]
    if not file_ids:
        print(
            "error: no file ids given — use positional args, --ids-file, or --from-manifest",
            file=sys.stderr,
        )
        return 1

    # Server cap is 10k ids per call; chunk so a corpus-sized list just works.
    # A FRESH payload per batch — reusing one dict and reassigning "file_ids"
    # hands every request a reference to the same object, so anything that keeps
    # the payload (a recorder, a retry wrapper, a log line) sees only the last
    # slice.
    updated = skipped = 0
    warnings = []
    for start in range(0, len(file_ids), 10_000):
        payload = {"file_ids": file_ids[start:start + 10_000], "index_mode": args.mode}
        if args.index_text_source is not None:
            payload["index_text_source"] = args.index_text_source
        data = client.post("files/bulk_set_index_mode/", json_data=payload)
        updated += data.get("updated") or 0
        skipped += len(data.get("skipped") or [])
        warnings.extend(data.get("warnings") or [])

    result = {"updated": updated, "skipped": skipped, "warnings": warnings}
    if as_json:
        out_json(result)
    else:
        print(f"index_mode={args.mode}: {updated:,} updated, {skipped:,} skipped")
        for w in warnings[:20]:
            print(f"  warning: {w}")
    return 0
