"""Transport externally-produced OCR — page text plus geometry — into PCXA.

For a caller who scanned the documents themselves and wants PCXA to treat *their*
OCR as the file's authoritative text. We chunk it, extract identifiers, index it
and embed it, exactly as we would text from our own OCR provider.

**This is not ``upload-chunks``, and picking the wrong one is the mistake this
command exists to prevent** — both "succeed", so nothing tells you afterwards::

    upload-ocr     you have page TEXT       -> the server chunks it
    upload-chunks  you have finished CHUNKS -> the server stores them verbatim

Sending OCR through ``upload-chunks`` returns a 200 and a searchable file, and
has silently thrown away every bounding box and opted those files out of every
future chunker improvement — the rows are marked ``chunk_source=external`` and
are never re-derived. If you OCR'd a scan, you want this command.

Input is JSON-Lines, one record per file, streamed, so a six-figure corpus never
has to fit in memory::

    {"file_id": 123, "envelope": {...}}
    {"path": "Sources/RFI-142.pdf", "overwrite": false, "envelope": {...}}

``path``/``name`` resolve through ``--manifest`` against what a prior
``files sync`` wrote, so the two commands compose without the caller keeping an
id table of its own.

Three things about this command are load-bearing:

**``--validate-only`` is the feature, not a nicety.** The server dry run writes
nothing and reports both halves — envelope shape errors *and* the state-machine
refusals (parked, already indexed, batch in flight, ...). For a six-figure run
that is the difference between finding 8,000 unusable targets before scanning
and after, so it prints a refusal histogram by ``error_code`` rather than a wall
of per-file lines.

**``202`` does not mean searchable.** It means the text is durably stored.
Chunking and embedding run afterwards on a background queue, so a caller who
treats acceptance as "indexed" will report the feature broken. Poll
``pcxa files info`` / the index status instead.

**Retries are safe and need no idempotency key.** Dedup is content-addressed and
computed server-side: re-posting byte-identical content for the same file and
version returns ``"deduplicated": true`` and writes nothing new. A client-side
key would be wrong in both directions — the client cannot reproduce the server's
canonical JSON (key order, separators, float repr) — so we deliberately send
none.
"""

import json
import random
import sys
import time

from pcxa._output import out_json

# The streaming reader, manifest index, resume state and rate governor are
# identical to `upload-chunks`' and are imported rather than re-implemented —
# the two commands must behave the same way on the parts a caller drives them
# with (a directory of *.jsonl, a sync manifest, Ctrl-C, --state).
from pcxa.commands.chunks import (
    ChunkInputError as OcrInputError,
    Pacer,
    _iter_jsonl,
    _load_manifest_index,
    _load_state,
    _save_state,
)

# --- Server contract, mirrored so a bad payload fails fast with a useful
# message instead of as a 400 (or, for the body cap, a 413 raised before the
# body is even parsed). Tracks docs/integrations/external-ocr-upload.md.
MAX_FILES_PER_REQUEST = 50
MAX_PAGES_PER_FILE = 1_000
MAX_REQUEST_BYTES = 10 * 1024 * 1024

# Headroom under the 10 MB cap. The server measures the raw body; we measure the
# items we are about to put in it, so the difference — the `validate_only` key,
# the `items` wrapper, transfer framing — has to be slack we left behind. A
# typical envelope is 50-300 KB, so this still packs ~30 files per request.
DEFAULT_MAX_BYTES = 9 * 1024 * 1024

# Client-side pacing, in pages/hour. The endpoint allows 120 requests/minute —
# its own throttle scope, not the 30/min that `upload-chunks` gets — which at
# realistic batch sizes is far more headroom than a backfill needs. This is a
# smoother, not a durability requirement: raise it, or pass 0 to disable it.
DEFAULT_PAGES_PER_HOUR = 250_000

SCHEMA_LAYOUT = "pcxa.ocr_layout.v1"
SCHEMA_TEXT = "pcxa.text.v1"
# A closed union. A third schema string is a typo, not an extension point.
SCHEMAS = (SCHEMA_LAYOUT, SCHEMA_TEXT)

# Per-item refusals. None of these become true by asking again; retrying them
# only burns the rate limit, so they are reported and skipped.
NON_RETRYABLE_CODES = frozenset({
    "index_parked",
    "already_indexed",
    "external_chunk_source",
    "project_external_chunks_only",
    "index_excluded_by_policy",
    "file_version_mismatch",
    "incomplete_page_coverage",
    "has_server_text",
    "has_ocr_text",
    "derivation_pending",
    "not_found",
    "invalid_envelope",
})

# Transient states. Not retried inside this run — the file is simply left out of
# --state so the next run picks it up, which is the resume path the caller
# already has, rather than a second pacing policy hidden inside a batch loop.
RETRYABLE_CODES = frozenset({"ocr_batch_in_flight", "chunking_in_progress"})

# Refusals a caller will misread without help. Printed once per run, only when
# the code actually occurs.
CODE_NOTES = {
    "index_parked": (
        "a deliberate exclusion someone chose, not an error — this endpoint clears "
        "no park reasons. A whole folder coming back parked means that folder is "
        "excluded from indexing on purpose; ask before scanning it."
    ),
    "incomplete_page_coverage": (
        "only happens on a partially-OCR'd file, and names the pages still missing. "
        "The upload must cover every page the server still needs; it then stores "
        "only those, so text already extracted from the good pages survives."
    ),
    "already_indexed": (
        "the file is searchable already and is refused even with --overwrite. "
        "Replacing a searchable file's text needs a deliberate demote — ask us."
    ),
    "external_chunk_source": (
        "this file's chunks came from `files upload-chunks`, so the server never "
        "re-derives its text. Nothing to do here."
    ),
    "project_external_chunks_only": (
        "a bring-your-own-chunks project, which has been promised we never derive "
        "text server-side. Use `pcxa files upload-chunks` instead."
    ),
    "derivation_pending": (
        "text is stored but not yet chunked. Pass --overwrite to displace it."
    ),
    "has_server_text": "the file already has text. Pass --overwrite to displace it.",
    "has_ocr_text": "the file already has OCR text. Pass --overwrite to displace it.",
}

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
RETRIES = 4
UPLOAD_TIMEOUT = 300

# Envelope shape errors are capped at 50 per file server-side; match that when
# echoing them, for the same reason (a wall of them helps nobody).
MAX_REPORTED_ERRORS = 50

# A refusal is per-file, and a parked folder produces one per file in it. Echoing
# 8,000 of them buries the histogram that actually answers the question, so only
# the first few are printed; the rest are counted, and --error-log still gets
# every one.
MAX_ECHOED_REFUSALS = 20

# The summary keeps a sample of messages, not all of them. At corpus scale the
# full list is both a memory footprint and an unreadable JSON blob — `error_count`
# is the number that matters and is always exact.
MAX_STORED_ERRORS = 200


# --- validation --------------------------------------------------------------


def _num(value, where, field):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OcrInputError(f"{where}.{field}: expected a number, got {value!r}")
    return float(value)


def _unit(value, where, field):
    """A coordinate: a number in [0, 1]. Normalized, origin top-left."""
    v = _num(value, where, field)
    if not (0.0 <= v <= 1.0):
        raise OcrInputError(
            f"{where}.{field}: coordinates must be in [0, 1] (normalized, origin "
            f"top-left), got {v!r}"
        )
    return v


def _bbox(box, where, field):
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        raise OcrInputError(f"{where}.{field}: expected [x0, y0, x1, y1]")
    x0, y0, x1, y1 = (_unit(v, where, f"{field}[{i}]") for i, v in enumerate(box))
    if x1 <= x0:
        raise OcrInputError(f"{where}.{field}: x1 <= x0 (zero or negative width)")
    if y1 <= y0:
        raise OcrInputError(f"{where}.{field}: y1 <= y0 (zero or negative height)")
    return [x0, y0, x1, y1]


def _confidence(value, where, field):
    """Confidence is optional, and 0 is not how you say "unknown".

    ``0`` asserts the text is *certainly wrong*, and it is a common exporter
    default — a corpus uploaded with it is quietly telling the ranker to
    distrust every line. Rejected here so it is fixed in the producer, where it
    is one line, rather than discovered after 136,000 files have landed.
    """
    v = _num(value, where, field)
    if not (0.0 <= v <= 1.0):
        raise OcrInputError(f"{where}.{field}: confidence must be in [0, 1], got {v!r}")
    if v == 0.0:
        raise OcrInputError(
            f"{where}.{field}: confidence 0 asserts the text is certainly wrong. "
            f"Omit the field instead — it is optional, and an exporter that "
            f"defaults it to 0 should leave it out."
        )
    return v


def _validate_word(word, where):
    """``[text, x0, y0, x1, y1]`` with an optional 6th confidence."""
    if not isinstance(word, (list, tuple)) or len(word) not in (5, 6):
        got = len(word) if isinstance(word, (list, tuple)) else type(word).__name__
        raise OcrInputError(
            f"{where}: expected [text, x0, y0, x1, y1] with an optional 6th "
            f"confidence, got {got}"
        )
    if not isinstance(word[0], str):
        raise OcrInputError(f"{where}[0]: word text must be a string")
    _bbox(list(word[1:5]), where, "box")
    if len(word) == 6 and word[5] is not None:
        _confidence(word[5], where, "[5]")


def _validate_layout_page(page, where):
    for field in ("w", "h"):
        if field not in page:
            raise OcrInputError(f"{where}.{field}: required (page dimensions)")
        if _num(page[field], where, field) <= 0:
            raise OcrInputError(f"{where}.{field}: must be positive")

    lines = page.get("lines")
    # `[]` is a legal blank page and is how you say "nothing here". An omitted
    # `lines` is a different statement, so it is rejected.
    if not isinstance(lines, list):
        raise OcrInputError(
            f"{where}.lines: required — use [] for a blank page (an omitted "
            f"'lines' and a blank page mean different things on a partially "
            f"OCR'd file)"
        )

    for li, line in enumerate(lines):
        lw = f"{where}.lines[{li}]"
        if not isinstance(line, dict):
            raise OcrInputError(f"{lw}: expected an object")
        if not isinstance(line.get("t"), str):
            raise OcrInputError(f"{lw}.t: line text must be a string")
        if "b" not in line:
            raise OcrInputError(f"{lw}.b: required — [x0, y0, x1, y1]")
        _bbox(line["b"], lw, "b")
        if line.get("c") is not None:
            _confidence(line["c"], lw, "c")

        words = line.get("words")
        if not isinstance(words, list):
            raise OcrInputError(f"{lw}.words: required — use [] if you have no word boxes")
        for wi, word in enumerate(words):
            _validate_word(word, f"{lw}.words[{wi}]")

        font = line.get("f")
        if font is not None and (not isinstance(font, (list, tuple)) or len(font) != 3):
            raise OcrInputError(f"{lw}.f: expected [font_name, font_size, is_bold]")


def _validate_envelope(envelope, where):
    """Reject an envelope the server would reject, before it costs a request.

    Returns the page count.
    """
    if not isinstance(envelope, dict):
        raise OcrInputError(f"{where}.envelope: expected an object")

    schema = envelope.get("schema")
    if schema not in SCHEMAS:
        raise OcrInputError(
            f"{where}.envelope.schema: must be one of {' / '.join(SCHEMAS)} "
            f"(got {schema!r}). This is a closed union — use {SCHEMA_TEXT} when you "
            f"have no geometry rather than {SCHEMA_LAYOUT} with fabricated boxes."
        )

    prov = envelope.get("provenance")
    if prov is not None and not isinstance(prov, dict):
        raise OcrInputError(f"{where}.envelope.provenance: expected an object")
    # Required on the layout arm per the API contract; on the text arm the server
    # does not demand it, so neither do we — being stricter than the server here
    # would reject payloads it accepts.
    if schema == SCHEMA_LAYOUT and (not isinstance(prov, dict) or not prov.get("producer")):
        raise OcrInputError(
            f"{where}.envelope.provenance.producer: required — name the tool that "
            f'produced this OCR (e.g. "mxi-scanner/1.4")'
        )

    pages = envelope.get("pages")
    # `"lines": []` is a legal blank page; an empty `pages` is not.
    if not isinstance(pages, list) or not pages:
        raise OcrInputError(
            f"{where}.envelope.pages: must be a non-empty list (a blank page is a "
            f'page with "lines": [], not an empty envelope)'
        )
    if len(pages) > MAX_PAGES_PER_FILE:
        raise OcrInputError(
            f"{where}.envelope.pages: {len(pages)} pages exceeds the "
            f"{MAX_PAGES_PER_FILE}-per-file server limit"
        )

    seen = set()
    for pi, page in enumerate(pages):
        pw = f"{where}.envelope.pages[{pi}]"
        if not isinstance(page, dict):
            raise OcrInputError(f"{pw}: expected an object")

        n = page.get("n")
        if isinstance(n, bool) or not isinstance(n, int):
            raise OcrInputError(f"{pw}.n: page number must be an integer")
        if n < 1:
            raise OcrInputError(f"{pw}.n: page numbers are 1-indexed, got {n}")
        if n in seen:
            raise OcrInputError(f"{pw}.n: duplicate page number {n} within this envelope")
        seen.add(n)

        if schema == SCHEMA_LAYOUT:
            _validate_layout_page(page, pw)
        elif not isinstance(page.get("text"), str):
            raise OcrInputError(f"{pw}.text: required for {SCHEMA_TEXT} — must be a string")

    return len(pages)


def _validate_record(record, *, source, line_no, by_path, by_name, overwrite_default):
    """Normalize one JSONL record into an API ``items[]`` entry.

    Returns ``(item, page_count)``.
    """
    where = f"{source}:{line_no}"
    if not isinstance(record, dict):
        raise OcrInputError(f"{where}: expected a JSON object")

    file_id = record.get("file_id")
    if file_id is None:
        key = record.get("path") or record.get("name")
        if not key:
            raise OcrInputError(f"{where}: record needs one of file_id / path / name")
        file_id = by_path.get(key) or by_name.get(key)
        if file_id is None:
            raise OcrInputError(
                f"{where}: {key!r} is not in the manifest. Pass --manifest from the "
                f"`files sync` run that uploaded it, or use an explicit file_id."
            )
    try:
        file_id = int(file_id)
    except (TypeError, ValueError):
        raise OcrInputError(f"{where}: file_id must be an integer (got {file_id!r})") from None

    if "envelope" not in record:
        raise OcrInputError(
            f"{where}: record needs an 'envelope'. If you have finished chunks "
            f"rather than page text, you want `pcxa files upload-chunks`."
        )
    pages = _validate_envelope(record["envelope"], where)

    item = {"file_id": file_id, "envelope": record["envelope"]}

    # Per-record `overwrite` wins over the flag: a corpus is usually uniform, but
    # the exceptions are per file and belong with the file.
    overwrite = record.get("overwrite")
    if overwrite is None:
        overwrite = overwrite_default
    if overwrite:
        item["overwrite"] = True

    if record.get("file_version_id") is not None:
        try:
            item["file_version_id"] = int(record["file_version_id"])
        except (TypeError, ValueError):
            raise OcrInputError(f"{where}: file_version_id must be an integer") from None

    prov = record.get("provenance")
    if prov is not None:
        if not isinstance(prov, dict):
            raise OcrInputError(f"{where}: record-level provenance must be an object")
        item["provenance"] = prov

    return item, pages


# --- byte budgeting ----------------------------------------------------------


def _item_bytes(item):
    """Serialized size of one item, measured the way the transport measures it.

    ``_http`` sends ``json.dumps(payload).encode("utf-8")`` — **default**
    separators, with their spaces. Measuring compactly here would under-count by
    several percent and produce exactly the failure this budget exists to
    prevent: a ``413`` the server raises before it parses the body, so nothing in
    ``results`` says which file was at fault.
    """
    return len(json.dumps(item).encode("utf-8"))


# --- transport ---------------------------------------------------------------


def _post_batch(client, payload, *, timeout):
    """POST one batch, retrying 429/5xx and honouring ``Retry-After``."""
    from pcxa._http import HTTPError
    from pcxa._http import requests as _rq

    last_exc = None
    for attempt in range(RETRIES):
        try:
            return client.post(
                "semantic-search/upload-ocr/", json_data=payload, timeout=timeout
            )
        except HTTPError as exc:
            last_exc = exc
            resp = getattr(exc, "response", None)
            code = getattr(resp, "status_code", 0)
            if code == 413:
                # Raised before the body is parsed, so no per-item result exists
                # to explain it. Say what actually fixes it.
                raise OcrInputError(
                    f"413: request body over the server's "
                    f"{MAX_REQUEST_BYTES // (1024 * 1024)} MB cap — lower --max-bytes "
                    f"(this request carried {len(payload.get('items') or [])} file(s))."
                ) from exc
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


# --- reporting ---------------------------------------------------------------


def _print_refusal_histogram(refusals, *, stream):
    """Refusals grouped by ``error_code``, not a wall of per-file lines.

    At corpus scale the per-file list is unreadable and the histogram is the
    whole answer: "8,000 parked" is a decision to go and discuss, and no number
    of individual lines says it more clearly.
    """
    if not refusals:
        return
    print(f"\nRefused: {sum(refusals.values()):,} file(s), by error_code", file=stream)
    width = max(len(c) for c in refusals)
    for code, count in sorted(refusals.items(), key=lambda kv: (-kv[1], kv[0])):
        if code in RETRYABLE_CODES:
            tag = "retryable"
        elif code in NON_RETRYABLE_CODES:
            tag = "not retryable"
        else:
            # The server grew a code we do not know. Treated as non-retryable,
            # which is the safe default, but said out loud — silently filing it
            # under "not retryable" is how a client drifts from its contract.
            tag = "unrecognised — treated as not retryable"
        print(f"  {code:<{width}}  {count:>7,}  ({tag})", file=stream)
    for code in sorted(refusals):
        note = CODE_NOTES.get(code)
        if note:
            print(f"\n  {code}: {note}", file=stream)


# --- command -----------------------------------------------------------------


def cmd_files_upload_ocr(client, args):
    """Upload externally-produced OCR (text + geometry) for existing files."""
    as_json = getattr(args, "format", "table") == "json"
    # `--dry-run` is an alias: the parser propagates that flag onto every
    # subcommand, and a caller who types it must never get a real upload.
    validate_only = bool(
        getattr(args, "validate_only", False) or getattr(args, "dry_run", False)
    )

    # In --format json, stdout carries nothing but the summary object.
    def _progress(msg):
        print(msg, file=sys.stderr if as_json else sys.stdout)

    files_per_req = min(max(1, args.files_per_request), MAX_FILES_PER_REQUEST)
    max_bytes = min(max(1, args.max_bytes), MAX_REQUEST_BYTES)

    try:
        by_path, by_name = _load_manifest_index(args.manifest)
    except OcrInputError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    # A dry run must not consult or advance resume state: the point is to see the
    # whole target list, including files a previous run already applied.
    state = {"applied": []} if validate_only else _load_state(args.state)
    applied = set(state.get("applied") or [])
    if applied:
        _progress(f"Resuming: {len(applied)} file(s) already applied — skipping them.")

    pacer = Pacer(0 if validate_only else args.pages_per_hour)
    if pacer.limit:
        _progress(
            f"Pacing at {pacer.limit:,} pages/hour. Client-side smoothing only — the "
            f"endpoint allows 120 requests/minute. Raise it with --pages-per-hour N, "
            f"or pass 0 to go as fast as the rate limit allows."
        )

    summary = {
        "validate_only": validate_only,
        "files_applied": 0, "files_would_apply": 0, "files_deduplicated": 0,
        "files_refused": 0, "files_skipped_resume": 0, "files_rejected": 0,
        "pages_sent": 0, "requests": 0, "warnings": 0,
        "error_count": 0,
        "refusals": {}, "errors": [],
    }
    refusals = summary["refusals"]
    echoed_refusals = 0
    errors_log = open(args.error_log, "a", encoding="utf-8") if args.error_log else None

    batch, batch_bytes, batch_pages = [], 0, 0
    queued = 0
    aborted = False

    def _record_error(msg, *, file_id=None, error_code=None, details=None,
                      echo=True, counts=True):
        if counts:
            summary["error_count"] += 1
        if len(summary["errors"]) < MAX_STORED_ERRORS:
            summary["errors"].append(msg)
        if echo:
            print(f"  ! {msg}", file=sys.stderr)
        if errors_log:
            errors_log.write(json.dumps({
                "file_id": file_id, "error_code": error_code,
                "error": msg, "details": details,
            }) + "\n")
            errors_log.flush()

    def _handle_results(data, n_pages):
        """Fold one response's ``results`` into the summary.

        Per-item failures are isolated server-side, so this reads ``results``
        rather than trusting the status code — a 200 here means "nothing
        applied", which is an ordinary outcome, not a transport error.
        """
        nonlocal echoed_refusals
        ok_ids = []
        for row in data.get("results") or []:
            fid = row.get("file_id")
            code = row.get("error_code")
            if code:
                summary["files_refused"] += 1
                refusals[code] = refusals.get(code, 0) + 1
                # Shape errors come back as JSON paths and never echo document
                # text, so they are safe to log verbatim.
                details = (row.get("errors") or [])[:MAX_REPORTED_ERRORS]
                msg = f"file {fid}: {code}"
                if row.get("error"):
                    msg += f" — {row['error']}"
                if row.get("parked_reason"):
                    msg += f" [parked_reason={row['parked_reason']}]"
                echo = echoed_refusals < MAX_ECHOED_REFUSALS
                # Enumerating refusals is what a dry run is *for*, so they must
                # not spend its failure budget — otherwise --validate-only over a
                # six-figure corpus aborts at the 100th parked file, which is the
                # exact discovery it exists to make.
                _record_error(msg, file_id=fid, error_code=code,
                              details=details or None, echo=echo,
                              counts=not validate_only)
                if echo:
                    echoed_refusals += 1
                    for line in details:
                        print(f"      {line}", file=sys.stderr)
                    if echoed_refusals == MAX_ECHOED_REFUSALS:
                        print(
                            "  ... further refusals are counted in the histogram "
                            "below, not printed (use --error-log for all of them)",
                            file=sys.stderr,
                        )
                continue

            # `warnings` are advisory and must not be treated as failures. The
            # common one is a word box outside its line box, which is legitimate
            # for rotated text.
            summary["warnings"] += len(row.get("warnings") or [])
            if validate_only:
                summary["files_would_apply"] += 1
            elif row.get("applied"):
                summary["files_applied"] += 1
                if row.get("deduplicated"):
                    summary["files_deduplicated"] += 1
                ok_ids.append(fid)
        summary["pages_sent"] += n_pages
        return ok_ids

    def _flush():
        nonlocal batch, batch_bytes, batch_pages, aborted
        if not batch:
            return
        payload = {"items": batch}
        if validate_only:
            payload["validate_only"] = True
        n_files, n_pages, n_bytes = len(batch), batch_pages, batch_bytes
        try:
            data = _post_batch(
                client, payload,
                timeout=getattr(args, "http_timeout", None) or UPLOAD_TIMEOUT,
            )
        except OcrInputError as exc:
            # A 413 is a batching bug, not a bad file — carrying on would repeat
            # it on every remaining batch.
            _record_error(str(exc))
            batch, batch_bytes, batch_pages = [], 0, 0
            aborted = True
            return
        except Exception as exc:
            _record_error(f"batch of {n_files} file(s) failed: {exc}")
            batch, batch_bytes, batch_pages = [], 0, 0
            if args.max_failures and summary["error_count"] >= args.max_failures:
                aborted = True
            return

        summary["requests"] += 1
        ok_ids = _handle_results(data, n_pages)
        if not validate_only and ok_ids:
            applied.update(ok_ids)
            _save_state(args.state, applied)

        slept = pacer.record_and_wait(n_pages)
        done = summary["files_would_apply"] if validate_only else summary["files_applied"]
        verb = "validated" if validate_only else "applied"
        _progress(
            f"  {done:,} {verb}, {summary['files_refused']:,} refused "
            f"— {summary['pages_sent']:,} page(s), {n_bytes / 1e6:.1f} MB last request"
            + (f" (paced +{slept:.0f}s)" if slept else "")
        )
        if args.max_failures and summary["error_count"] >= args.max_failures:
            aborted = True
        batch, batch_bytes, batch_pages = [], 0, 0

    try:
        for source, line_no, record in _iter_jsonl(args.paths):
            if aborted:
                break
            try:
                item, pages = _validate_record(
                    record, source=source, line_no=line_no,
                    by_path=by_path, by_name=by_name,
                    overwrite_default=bool(getattr(args, "overwrite", False)),
                )
            except OcrInputError as exc:
                summary["files_rejected"] += 1
                _record_error(str(exc), error_code="invalid_envelope")
                if args.max_failures and summary["error_count"] >= args.max_failures:
                    print(
                        f"error: aborting after {summary['error_count']} failures "
                        f"(--max-failures)", file=sys.stderr,
                    )
                    aborted = True
                    break
                continue

            if item["file_id"] in applied:
                summary["files_skipped_resume"] += 1
                continue

            size = _item_bytes(item)
            if size > max_bytes:
                # There is no split: the envelope is this file's text master, and
                # a second request for the same file would replace rather than
                # extend it. So this is a producer-side fix.
                summary["files_rejected"] += 1
                _record_error(
                    f"{source}:{line_no}: file {item['file_id']} serializes to "
                    f"{size / 1e6:.1f} MB, over the {max_bytes / 1e6:.1f} MB request "
                    f"budget, and an envelope cannot be split across requests. Raise "
                    f"--max-bytes (server cap {MAX_REQUEST_BYTES / 1e6:.0f} MB), or "
                    f"emit {SCHEMA_TEXT} for this file if geometry is what makes it "
                    f"large.",
                    file_id=item["file_id"],
                )
                continue

            # Pack by bytes *and* count: the body cap is a 413 raised before the
            # body is parsed, so a batcher that packs by file count alone hits it
            # with nothing in the response to say which file was responsible.
            if batch and (len(batch) >= files_per_req or batch_bytes + size > max_bytes):
                _flush()
                if aborted:
                    break

            batch.append(item)
            batch_bytes += size
            batch_pages += pages
            queued += 1
            if args.limit and queued >= args.limit:
                break

        if not aborted:
            _flush()
    except OcrInputError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted — state saved; re-run to resume", file=sys.stderr)
        if not validate_only:
            _save_state(args.state, applied)
        return 130
    finally:
        if errors_log:
            errors_log.close()

    deferred = sum(n for c, n in refusals.items() if c in RETRYABLE_CODES)

    if as_json:
        out_json(summary)
    else:
        print()
        if validate_only:
            print(
                f"Dry run: {summary['files_would_apply']:,} file(s) would apply, "
                f"{summary['files_refused']:,} refused, "
                f"{summary['requests']:,} request(s). Nothing was written."
            )
        else:
            print(
                f"Done: {summary['files_applied']:,} file(s) applied, "
                f"{summary['pages_sent']:,} page(s), {summary['requests']:,} request(s)"
            )
            if summary["files_deduplicated"]:
                print(f"  already stored (deduplicated): {summary['files_deduplicated']:,}")
        if summary["files_skipped_resume"]:
            print(f"  skipped (already applied):     {summary['files_skipped_resume']:,}")
        if summary["files_rejected"]:
            print(f"  rejected before send:          {summary['files_rejected']:,}")
        if summary["warnings"]:
            print(f"  advisory warnings:             {summary['warnings']:,} (not failures)")
        if pacer.slept:
            print(f"  paced (slept):                 {pacer.slept / 60:.1f} min")
        _print_refusal_histogram(refusals, stream=sys.stdout)

    if deferred and not validate_only:
        print(
            f"\n{deferred} file(s) hit a transient state "
            f"({', '.join(sorted(RETRYABLE_CODES))}) and were not recorded as applied. "
            f"Re-run with the same --state to retry just those.",
            file=sys.stderr,
        )

    if not validate_only and summary["files_applied"]:
        print(
            "\nNote: acceptance means the text is durably stored, not that the files "
            "are searchable. Chunking and embedding run afterwards on a background "
            "queue — read `pcxa files info <id>` for the index status rather than "
            "treating this command's exit as 'indexed'.",
            file=sys.stderr,
        )

    # A dry run's whole purpose is to find refusals, so they are its *output*,
    # not its failure — which is why they do not reach `error_count` under
    # --validate-only. What is left there is client-side rejections and transport
    # failures, and those should still fail the run, so one expression covers
    # both modes and `validate && upload` stays scriptable.
    return 1 if (summary["error_count"] or aborted) else 0
