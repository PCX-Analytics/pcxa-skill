---
name: pcxa
description: PCXA construction intelligence platform CLI. Search/read files, manage tags and folders, manage activities/steps/progress/dependencies, manage form templates/fields/submissions, manage custom objects (field choices) with fuzzy "did you mean?" value matching, manage resources/timesheets/cost-codes/budgets, manage entity links between objects, and chat with the project's AI assistant. Use when the user asks about project files, documents, tasks, activities, forms, custom objects, resources, timesheets, entity links, project management, or wants to send messages to the AI chatbot.
argument-hint: <command> [options]
user-invocable: true
disable-model-invocation: false
---

# PCXA CLI

**Tool:** `pcxa`

All commands output JSON by default. Use `-f table` for human-readable. Use `--dry-run` on write operations. Run `pcxa <command> --help` for full options.

When installed as a Claude Code plugin, `pcxa` is provided by the plugin `bin/` directory. For direct terminal use outside Claude Code, ask the user to install it once with:
```
pipx install git+https://github.com/PCX-Analytics/pcxa-skill.git
```
Then `pcxa update` self-upgrades from GitHub. The CLI prints a one-line notice to stderr (max once per 24h) when a newer release is available.


## Setup & Authentication

Always start by running `pcxa whoami` to see the current state. The output tells you whether the user is authenticated and whether a project is set. Only run setup steps that are actually missing.

### Step 1 — Authentication (drive it yourself, don't punt to a terminal)

If `whoami` says "No profiles configured" or a command fails with "Profile not found":

```bash
pcxa login --no-setup
```

The `--no-setup` flag is required when you (the agent) drive login: it skips the interactive company/project picker that reads stdin, which would otherwise hang. The CLI prints two lines immediately:

```
Opening browser to authenticate...
  If your browser does not open automatically, visit:
  https://www.pcxa.app/auth/cli-auth?port=PORT&state=STATE
```

**Read those lines from stdout, then surface the URL to the user as a clickable markdown link.** The CLI tries `webbrowser.open()` but that's usually a no-op in WSL/headless — the user opening the link manually is the normal path. They sign in (MFA/SSO supported), the page redirects to a localhost callback, the CLI captures tokens, the command exits.

Run with a generous bash timeout (e.g., 180s) since the user may take a moment to sign in. The CLI's own `--timeout` defaults to 120s; pass `--timeout 300` if you want longer.

If browser login isn't viable (rare — only if the host can't reach `pcxa.app`), fall back to password login: `pcxa setup -u USER_EMAIL` (prompts for password — only works if the user can run it themselves).

### Step 2 — Pick a project

After login (or if `whoami` shows `Project: not set`), drive the project picker yourself:

```bash
pcxa projects -f table       # list all (company_id, project_id) the user has access to
```

Show that list to the user, ask which project they want, then:

```bash
# When the conversation/CWD is inside a repo that maps 1:1 to a PCXA project,
# pin it locally so future runs in this repo are auto-scoped:
pcxa set-project PROJECT_ID --company COMPANY_ID --local

# Otherwise, set the global default for this user:
pcxa set-project PROJECT_ID --company COMPANY_ID
```

Always pass `--company` when picking — `--local` writes a `.pcxa` file in CWD; without `--local`, the choice is saved in the global profile.

### How resolution works

Project scope resolves in this order: **repo `.pcxa` file** > **active profile default**. `.pcxa` is committed (no secrets — just `{ "company": 4, "project": 10, "user": "alice@example.com" }`). Different repos can pin different accounts via the `user` field; the CLI matches it against profile usernames in the active credentials file.

Credentials resolve **folder-first**: a `.pcxa-credentials.json` found by walking up from CWD is used for both reads and writes (token refresh included); otherwise the global `~/.pcxa/credentials.json` is used. So `pcxa login` from inside a repo writes that repo's own `.pcxa-credentials.json` (at the git root, gitignored) and can't clobber another repo's tokens — pass `--global` to write the shared global file instead. Pre-0.3 global creds at `~/.file_explorer/config.json` are auto-migrated to `~/.pcxa/credentials.json` on first run.

State to surface to the user on the first turn: `whoami` shows `Active profile`, `User`, `Company`, `Project` (with `(from .pcxa)` annotation when applicable), `Creds:`, and `Repo pin:`. If you ran setup steps, echo the resulting scope back ("Operating on project Acme Tower (4)") so the user can correct you before any writes.

## Project Metadata

```bash
pcxa project get                                              # view project details
pcxa project members                                          # list members (name → user ID)
pcxa project members --search "John"                          # search by name/username
pcxa project update --name "New Name"                         # update name
pcxa project update --description "..." --scope-statement "..." # set description & scope
pcxa project update --code "T-FAB1" --industry "Construction" # set code & industry
pcxa project update --start-date 2025-12-03 --end-date 2026-08-10
pcxa project update --rollup-method equal                     # equal|duration|cost|labor
pcxa project update --progress-input-method percentage        # status|percentage
```

Fields: `name`, `code` (max 20), `description`, `scope-statement`, `industry`, `life-cycle`, `start-date`, `end-date`, `progress-input-method`, `rollup-method`.

## File Search & Reading

```bash
pcxa files list --ext PDF --search "keyword" --limit 50  # title trigram (fuzzy by default; exact first)
pcxa files list --search "concret" --exact               # tighter title match (still AND-matches words, NOT a phrase)
pcxa files query 'title:schedule AND precast AND delay'  # BOOLEAN: AND/OR/NOT + grouping (⚠ multi-term AND under-reports on projects without the fix — see below)
pcxa files query 'title:report AND (delay OR "change order")'   # grouping + quoted phrase
pcxa files query 'contract NOT draft' --ext PDF          # exclusion (impossible with --search/--content)
pcxa files query 'title:"Case Memo"' --count-only        # true LITERAL phrase on the filename
pcxa files list --content "IOCC-387"                     # literal substring in file BODY — every match, paginated (stable id order)
pcxa files list --content "IOCC-387" --count-only        # exact total of content matches (count:0 = genuinely not located)
pcxa files list --tags "urgent,review" --tags-mode all   # AND: files with ALL tags
pcxa files search "natural language query"                    # hybrid keyword + semantic (same endpoint as the web UI)
pcxa files search "query" --scope file,activity               # restrict source types (csv: file,activity,drawing,photo)
pcxa files search "query" --ext PDF --limit 25                # narrow to file type, page-size up to 50
pcxa files content "BRG report" --ext PDF                     # alias of `files search` scoped to files (same hybrid endpoint)
pcxa files read FILE_ID --outline                             # section map
pcxa files read FILE_ID                                       # first 5 chunks
pcxa files read FILE_ID --start 5                             # next window
pcxa files batch-read 423 511 612 --window 3                  # multi-file in one call
pcxa files batch-read --chunk 423:7 --chunk 511:12            # excerpts at specific chunks
pcxa files batch-read 423 511 --outline                       # outlines, multi-file
pcxa files info FILE_ID                                       # metadata + versions
pcxa files stats                                              # project-wide counts
pcxa files aggregate file_type                                # group by dimension
pcxa files recent --limit 30
pcxa files download FILE_ID                              # download to current dir
pcxa files download FILE_ID -o report.pdf                # custom output path
pcxa files upload /path/to/file.pdf --folder 5 --title "Report" --tags "final,2026"
pcxa files upload /path/to/dir/ --folder 5               # bulk upload all files in dir (flat)
pcxa files sync /path/to/tree --folder 5                 # recursive mirror; creates subfolders; idempotent
pcxa files sync /path/to/tree --folder 5 --manifest .pcxa-sync.json   # persist upload log for fast re-runs
pcxa files sync /path/to/tree --folder 5 --include "*.pdf" --exclude "draft_*" --concurrency 16
pcxa files delete 123 124 --yes                          # mark for deletion (adds 'to_delete' tag)
pcxa files restore 123 124                               # remove 'to_delete' tag (undo)
pcxa files list --tags to_delete                         # list everything pending deletion
pcxa files set-index-mode 1 2 3 --mode none              # stop server-side indexing (BYOC prep)
pcxa files upload-chunks corpus.jsonl --manifest .pcxa-sync.json   # supply your own chunks + vectors
pcxa files upload-ocr ocr.jsonl --manifest .pcxa-sync.json --validate-only  # dry-run your own OCR
pcxa files upload-ocr ocr.jsonl --manifest .pcxa-sync.json         # supply your own OCR (text + boxes)
```

**Upload storage:** Small files are uploaded through the API. Larger files use a presigned upload flow handled by the CLI and API.

## Bring your own chunks (`files upload-chunks`)

For a caller that runs its **own** extraction / chunking / embedding pipeline and
wants PCXA to serve *its* index instead of re-deriving one. Files must exist
first — chunks attach to them.

> **If what you have is OCR page text, you want [`files
> upload-ocr`](#bring-your-own-ocr-files-upload-ocr) instead.** Sending OCR
> through this command succeeds — and silently discards your bounding boxes and
> opts those files out of every future chunker improvement, because the rows are
> marked `chunk_source=external` and are never re-derived.

```bash
# 1. upload the files (idempotent, resumable, records file_ids in the manifest)
pcxa files sync ./corpus --folder 42 --manifest .pcxa-sync.json

# 2. stop our chunker touching them (optional but recommended — see below)
pcxa files set-index-mode 101 102 103 --mode none

# 3. supply chunks + embeddings
pcxa files upload-chunks ./chunks/ --manifest .pcxa-sync.json --state .pcxa-chunks.json

# validate the whole corpus without sending anything
pcxa files upload-chunks ./chunks/ --dry-run
```

**Input is JSON-Lines, one record per file, streamed** — a multi-million-chunk
corpus never has to fit in memory. Pass `.jsonl` files or directories of them.

```json
{"file_id": 123, "chunks": [
  {"chunk_index": 0, "content": "...", "embedding": [768 floats]},
  {"chunk_index": 1, "content": "...", "embedding": [768 floats]}
]}
```

Per-chunk optional keys: `content_hash` (sha256 hex), `page_number` (1-based),
`metadata` (object). Per-file optional keys: `file_version_id`,
`indexed_content_hash`, `document_summary`, `document_context_strategy`.

Instead of `file_id` a record may carry `path` or `name`, resolved through
`--manifest` against the manifest a prior `files sync` wrote — so the two
commands compose without you keeping your own id table. A filename that maps to
more than one file is **rejected, not guessed** (it would overwrite the wrong
document's index); address those by `path` or `file_id`.

### Embeddings

Supply `embedding` on every chunk of a file and that file is marked **INDEXED** —
our embedder never runs on it, and you pay nothing for embedding. Omit them
entirely and the file lands **CHUNKED** for our embedder to pick up (billable,
but valid — useful if you have good chunk boundaries but no vectors).

**Embeddings are all-or-nothing per file.** A file where only some chunks carry a
vector is rejected client-side, because the server would demote the whole file to
CHUNKED and re-embed it — silently costing you the thing you were avoiding.

Vectors must be exactly **768 dimensions** from `gemini-embedding-001`
(`--embedding-model` to override, but the server requires an exact match). This
is checked hard on purpose: every vector in the index is 768-dim, so another
768-dim model's output validates fine and then simply retrieves badly, forever,
with nothing to detect it afterwards.

You do **not** need to supply identifier metadata — the server extracts
RFI/PCO/CO/NCR-style references from the `content` you send.

### Pacing: a client-side default you can safely raise

`--chunks-per-hour` defaults to **60,000**, which keeps a long backfill
comfortably under the endpoint's limit of **30 requests/minute per user**. This
is client-side pacing only. It is **not** a durability constraint, and nothing
downstream requires the upload to be slow.

Raise it for a large corpus, or pass `--chunks-per-hour 0` to upload as fast as
the rate limit allows — at the 5,000-chunk maximum per request that is a great
deal of headroom. Either way the client retries `429`s honouring `Retry-After`,
and once a chunk is accepted the server is responsible for storing it durably;
there is no follow-up step for you to run.

### Why `set-index-mode --mode none` first

Without it, our own chunker processes every file before your chunks arrive. That
is *harmless* — your upload replaces whatever it produced, and the guards below
then protect it — but it is pure waste at corpus scale. `--mode none` skips it
entirely; your upload still creates the index row.

### After a successful upload, the server leaves your chunks alone

- Server-side reprocessing will not re-chunk the file: retries and re-runs leave
  your chunks in place.
- Index-policy changes — including a project left at the default `selective` —
  neither delete your chunks nor strip their vectors.
- Uploading a **new version** of the file keeps your chunks and marks the index
  **stale**: search keeps returning the *older* text until you re-upload for the
  new version.

### Operational notes

- **Auth: project admin or company admin.** Chunk upload replaces a file's whole
  indexed content, so it takes the same gate as every other bulk file operation.
  Plain project membership gets `403`. Give an integration a service account with
  project-admin on each target project.
- **Resume:** `--state <path>` records applied file ids; a re-run skips them.
  Ctrl-C saves state before exiting.
- **Batching** is automatic against the server caps — 50 files and 5,000 chunks
  per request, 2,000 chunks per file, 16,000 chars per chunk. Values above the
  caps are clamped rather than rejected.
- **`--dry-run` validates everything** — dimensions, partial embeddings,
  duplicate `chunk_index`, oversized content — without spending a request. Run it
  over the full corpus before the real load.
- **Failures are per-item.** One bad `file_id` doesn't abort the batch; bad
  records are counted and logged (`--error-log` for one JSON line each).
  `--max-failures` (default 100) aborts a run that is clearly misconfigured.
- **Rate limits** (`429`) are retried honouring `Retry-After`.
- **Exit code is non-zero** if anything failed, so a driving script can tell.

## Bring your own OCR (`files upload-ocr`)

For a caller who **scanned the documents themselves**. You send page text —
ideally with word- and line-level bounding boxes — and PCXA treats it as the
file's authoritative text: it chunks it, extracts identifiers, indexes it for
boolean search and embeds it, exactly as it would text from its own OCR
provider.

### Which of the two upload commands you want

**This is the most common mistake with these two endpoints, and nothing tells
you afterwards, because both succeed.**

| | `upload-ocr` | `upload-chunks` |
|---|---|---|
| You have | page **text** from a scan | finished **chunks**, usually embedded |
| Who chunks | **the server** | nobody — it stores what you send |
| Geometry | preserved at full precision | no field for it |
| Row marked | ordinary — re-derivable | `chunk_source=external`, never re-derived |
| After acceptance | chunking → embedding, async | nothing |

Send OCR through `upload-chunks` and you get a 200 and a searchable file — and
you have silently thrown away every bounding box and opted those files out of
every future chunker improvement. If you OCR'd a scan, use `upload-ocr`.

(On a bring-your-own-chunks project `upload-ocr` refuses outright with
`project_external_chunks_only`: those projects were promised the server never
derives text server-side.)

### Start with `--validate-only`

```bash
# 1. the dry run — writes nothing, reports refusals grouped by error_code
pcxa files upload-ocr ./ocr/ --manifest .pcxa-sync.json --validate-only

# 2. the real load, resumable
pcxa files upload-ocr ./ocr/ --manifest .pcxa-sync.json --state .pcxa-ocr.json
```

The dry run is **server-side**, which is the point: it reports envelope shape
errors *and* the state-machine refusals a client cannot see — whether each file
is parked, already indexed, has an OCR batch in flight, belongs to a
bring-your-own-chunks project. For a six-figure run that is the difference
between discovering 8,000 unusable targets before scanning and after.

It exits **0** when the run completed: refusals are its *output*, not its
failure, so `validate && upload` is scriptable. Only client-side rejections and
transport failures make it non-zero. `--dry-run` is an accepted alias.

### Input

JSON-Lines, one record per file, streamed — a corpus never has to fit in memory.

```json
{"file_id": 123, "envelope": {...}}
{"path": "Sources/RFI-142.pdf", "overwrite": false, "envelope": {...}}
```

Per-record optional keys: `overwrite`, `file_version_id`, `provenance`. As with
`upload-chunks`, `path`/`name` resolve through `--manifest`, and an ambiguous
filename is rejected rather than guessed.

The `envelope` is one of **two shapes, and that is the whole set** — a closed
union. Do not invent a third, and do not send a layout envelope with null or
fabricated boxes.

**`pcxa.ocr_layout.v1`** — text with geometry. All coordinates 0–1 normalized,
origin top-left. Positional arrays because this repeats per word across a corpus.

```json
{"schema": "pcxa.ocr_layout.v1",
 "provenance": {"producer": "mxi-scanner/1.4", "tool_version": "1.4.0"},
 "pages": [{"n": 1, "w": 2550, "h": 3300,
            "lines": [{"t": "REINFORCED CONCRETE SLAB",
                       "b": [0.08, 0.10, 0.92, 0.14],
                       "c": 0.97,
                       "words": [["REINFORCED", 0.08, 0.10, 0.24, 0.14, 0.99]]}]}]}
```

**`pcxa.text.v1`** — no geometry. Fully supported, and better than fabricated
boxes; you lose only future geometry-dependent features, not retrieval.

```json
{"schema": "pcxa.text.v1",
 "provenance": {"producer": "mxi-scanner/1.4"},
 "pages": [{"n": 1, "text": "..."}]}
```

**Send full precision — do not round.** The stored copy is the master and
everything downstream is re-derived from it, so rounding is irreversible loss
taken to save storage that costs about $0.015/GB-month.

### `202` does not mean searchable

**This is the one that gets reported as a bug.** Acceptance means the text is
durably stored. Chunking and embedding happen afterwards on a background queue,
so the response cannot tell you the file is searchable and this command's exit
does not mean "indexed". Read `pcxa files info <id>` / the index status for that.
`upload-chunks` returns a terminal state; this does not.

### Refusals are per-item, and most are not retryable

Refused files are reported and skipped — retrying them only burns the rate
limit. The end-of-run report is a **histogram by `error_code`**, not a wall of
per-file lines (only the first 20 are echoed; `--error-log` gets every one).

Not retryable: `index_parked` · `already_indexed` · `external_chunk_source` ·
`project_external_chunks_only` · `index_excluded_by_policy` ·
`file_version_mismatch` · `incomplete_page_coverage` · `has_server_text` ·
`has_ocr_text` · `derivation_pending` · `not_found`.

Transient, and worth retrying: `ocr_batch_in_flight` · `chunking_in_progress`.
These are deliberately **not** recorded in `--state`, so re-running with the
same state file retries exactly those.

Two will confuse a caller, so the CLI explains them inline:

- **`index_parked` is a decision, not an error.** Someone excluded that file
  from indexing on purpose, and this endpoint clears **no** park reasons. A
  whole folder coming back parked means that folder is excluded deliberately.
- **`incomplete_page_coverage`** only happens on a partially-OCR'd file, and
  names the missing pages. Your upload must cover every page the server still
  needs; it then stores only those, so text already extracted from the good
  pages survives.

`warnings` are advisory and are **not** failures. The common one — a word box
outside its line box — is legitimate for rotated text.

### Operational notes

- **Auth: project admin or company admin**, same gate as `upload-chunks`. Give
  the integration its own service account: every applied file is written to the
  audit log with the acting user.
- **Client-side validation mirrors the server** so a bad payload fails before it
  costs a request: closed schema union, 1-indexed unique page numbers, bboxes
  with `x1 > x0` / `y1 > y0` and all coords in `[0,1]`, positive `w`/`h`, and at
  most 1,000 pages per file. `"lines": []` is a **legal blank page**; an empty
  `pages` array is not.
- **Confidence is optional, and `0` is rejected.** `0` asserts the text is
  certainly wrong — it is a common exporter default, and a corpus carrying it
  tells the ranker to distrust every line. Omit the field instead.
- **Batching packs by bytes *and* file count** — 50 files/request and a 10 MB
  body cap, where the body cap is a `413` raised *before* the body is parsed, so
  nothing in the response would say which file was responsible. `--max-bytes`
  (default 9 MB) is the byte budget. A single file whose envelope exceeds it is
  rejected client-side: an envelope is that file's text master and cannot be
  split across requests.
- **Pacing** `--pages-per-hour` defaults to 250,000. The endpoint allows **120
  requests/minute** — its own throttle scope, *not* `upload-chunks`' 30/min.
  Client-side smoothing only: raise it, or pass `0` to disable. `429`s are
  retried honouring `Retry-After` either way.
- **Retries need no idempotency key.** Dedup is content-addressed and computed
  server-side; a re-post of byte-identical content for the same file and version
  returns `"deduplicated": true` and writes nothing new. Do **not** add an
  `Idempotency-Key` header — the client cannot reproduce the server's canonical
  JSON, so a client-computed key would be wrong in both directions.
- **`--overwrite`** (default off) displaces text a file already has; a
  per-record `"overwrite"` key wins over the flag. It never overrides
  `already_indexed` or `index_parked`.
- **Resume** with `--state`, exactly as `upload-chunks` does.

**Not in scope:** producing the OCR. This command transports what a scanner
emits.

**Bulk tree sync (`files sync`):** Mirrors a local directory tree under a PCXA folder. Walks the tree, creates any missing subfolders to match, and uploads files in parallel via the same presign+PUT/multipart path as `files upload`. Idempotent two ways: it lists each target folder once and skips local files whose name already exists there, and an optional `--manifest <path>` persists `{relative_path → {size, file_id}}` so re-runs skip without hitting the API. Failures (network, register errors) are listed at the end and counted toward `error` rate. Progress is rendered live on stderr: a bar plus files-done, bytes-done/total, throughput, current concurrency (`c=N`), elapsed, ETA, and error count. Filters: `--include` and `--exclude` accept repeatable globs against filenames; dotfiles/dot-dirs are skipped by default (`--include-hidden` opts in).

**Designed for TB-scale runs:**
- **Auto-tuning concurrency** (default ON): an AIMD controller samples throughput and error rate every 10s and adjusts active workers via a runtime-resizable semaphore. Errors halve concurrency with a 30s cooldown; clean windows with rising throughput step it up. Bound by `--min-concurrency` / `--max-concurrency` (default 1 / 32). Pass `--no-auto-tune` to pin concurrency at `--concurrency` for the whole run. Tuner decisions print above the progress line so you can see what's happening.
- **Failure-budget circuit breaker** (`--max-failures`, default 100): aborts the run cleanly if failure events hit the budget (one failed batch = one event), so a misconfigured target folder doesn't burn hours of bandwidth. Exits `4`; files the abort never reached are reported as `not_attempted`, not as errors. Set `0` to disable.
- **Exit status a script can trust** (0.8.0+): `0` every queued file registered · `1` bad input/startup · `2` auth expired · `3` partial (some files errored, unrecognized, or not attempted — a Ctrl-C'd run included) · `4` aborted by `--max-failures`. Before 0.8.0 a sync with failed files exited `0`. The JSON summary satisfies `created + duplicate + error + unrecognized + not_attempted == to_upload`, except for a bulk-register still in flight after the CLI's 10 s end-of-run wait (#27).
- **`--trust-manifest` limits**: only server-confirmed rows (`created`/`duplicate`) enter the manifest, so failed files are retried. The flag skips the server-side name check, so a file deleted on the server after being recorded is never re-uploaded — drop the flag when the target folder may have changed.
- **Pre-flight folder check**: `GET /folders/{id}/` runs before walking, so an inaccessible target ID fails fast instead of mid-run. Retried with backoff — a transport hiccup on this call is not evidence the folder is missing.
- **Resilient folder resolution**: folder creates and per-parent subfolder lookups run with a 180s floor (raised further by `--timeout`, never lowered) and retry on timeouts, 429s, and 5xx. The root-level listing on a project-root sync inherits the ordinary default rather than the floor, so `--timeout` is still worth setting there. Because folders are identified by (name, parent), a retry re-resolves by name first and adopts the folder if a timed-out create actually landed server-side, instead of making a duplicate. Auth/permission/validation errors still fail on the first attempt. Resolved folders are checkpointed into `--manifest` (`{relative_dir → folder_id}`) and reused on the next run, so a failure part-way through a wide tree doesn't throw away the whole run's setup.
- **Adaptive multipart part-size**: per-file `part_size` is bumped automatically if the file would exceed R2's 10000-part cap (relevant for files > ~160 GB at the 16 MB default).
- **Time-based manifest checkpoints**: manifest flushes every 50 uploads *or* every 30s, whichever comes first — a crash mid-run loses at most ~30s of progress.
- **Resume messaging**: when the manifest already has entries, the run prints "Resuming from manifest: N files already recorded." up front.
- **`--part-concurrency`** (default 4) decouples parts-per-file parallelism from files-in-flight parallelism, so 16 concurrent multipart files don't spawn 16 × 16 = 256 PUTs.
- **`--limit N`** stops after queueing N files (post-filtering). Useful for graduated smoke tests before committing to a multi-hour run; the manifest is still written so the next run resumes exactly where this one stopped.

**HTTP read timeout (`--timeout`, `$PCXA_HTTP_TIMEOUT`):** every API call defaults to 30s. That is too tight for write endpoints on large projects — folder creates and bulk mutations regularly run past it, and the client can't distinguish "slow" from "dead". `pcxa --timeout <seconds> ...` (accepted before or after the subcommand on `files sync` and `files purge`) raises it for every call that goes through the API client. It does **not** change the upload/download/presign helpers, which call the transport directly with their own longer timeouts already baked in (60–600s depending on the operation) — an explicit value there wins over the default, so `--timeout` neither raises nor lowers them. Prefer raising the timeout over shrinking `--chunk` when a bulk call times out: the server usually finished the work, so an abort leaves a silent partial success.

**Deletion convention:** `pcxa files delete <ids>` marks files for deletion by applying the `to_delete` tag. Use `pcxa files restore <ids>` to undo before cleanup runs. Without `--yes`, `delete` prompts for confirmation.

Search results include `url` fields — always show these to users for document links.

**`--search` is fuzzy by default (`files list`, `files aggregate`, `activities list`).** Backend uses PostgreSQL trigram similarity: exact substring matches surface first (similarity ~1.0), then typo-tolerant matches ranked by similarity DESC, in a single paginated response. `concret` finds `Concrete Pour`; `0314` ranks `RFI-0314` above `Document-031499`. Pass `--exact` to opt back into tight substring matching (rejects typos — useful when the query is a known-correct identifier and you don't want fuzzy noise). **`--exact` is not a phrase match:** the words are still AND-matched separately, so `--search "Case Memo" --exact` also matches `Case Assessment Memorandum`. When you need a literal adjacent phrase in the filename, use `pcxa files query 'title:"Case Memo"'`. The backend rate-limits fuzzy search to 100/min per user; not normally a concern for agent use.

**`files query` response shape:** `{query, parsed, results, total_files, count_exact, limit, offset}`. `parsed` is the canonical interpretation of your expression — surface it to the user when the result set matters. `count_exact: false` means `total_files` is a floor (ceiling reached), not a total. Rows carry `file_id`, `file_name`, `file_type`, `folder_path`, and a `url`.

**Search response shape:** `pcxa files search` returns `{query, total_results, results, hybrid_enabled}` — a top-N reranked list (server-capped at 50). Each row carries `score`, `file_id`/`activity_id`, `file_name`/`title`, `folder_path`, `page_number`, `chunk_position`, and a `url`. Hybrid means semantic similarity and keyword matching over the project's chunk text are combined and reranked into a single ordering — the same path the web UI's search bar uses.

**Four ways to find files — pick by what you need:**
- **`files list --search <term>`** — matches the **title/filename** only (trigram). Paginated + countable. Use when you know part of the name.
- **`files list --content <term>`** — matches the indexed **body/contents** as a **literal substring** (not ranked, not semantic). Paginated + **countable and exhaustive**: `--content <term> --count-only` gives the *exact* total and you can page through *every* match in a stable order (by `id`). `count: 0` means the term is genuinely in no in-scope file — so this is the path for a "not located" / completeness finding. Use it to find files by what's inside them — e.g. an eDiscovery estate where emails are named by bare Bates numbers (`YATES002119058`) and the term only appears in the body. Only indexed files match; scope with `--folder`/`--ext`/`--index-status`.
- **`files search <term>` / `files content <term>`** — hybrid semantic + keyword **ranking** (relevance-ordered), a **top-50 reranked sample, not a total**. Best for natural-language / "most relevant" lookups. Do **not** use it to count or enumerate — it can't page past 50, and asking for more (`--limit 200`) is clamped to 50 with a notice pointing you at `--content`. For "how many / list them all", use `--content`.

- **`files query '<expr>'`** — **boolean** search: `AND` / `OR` / `NOT`, parentheses for grouping, `title:` / `content:` field scoping, and `"quoted phrases"` matched adjacently. Structurally expressive where `--content` is a single literal. Use it whenever the question has more than one condition — *"schedule in the title AND both precast and delay in the body"* is one call: `pcxa files query 'title:schedule AND precast AND delay'`. Bare terms search content; operators must be UPPERCASE (lowercase `and`/`or` are ordinary words). Every response echoes `parsed` — **read it** to confirm the query was understood before trusting the results.

  > ⚠️ **Multi-term `AND` is being fixed PER PROJECT — and nothing in the response tells you which behaviour you got.**
  >
  > **Old behaviour, still live on most projects:** two or more CONTENT terms joined by `AND` are chunk-scoped and UNDER-REPORT. The match requires the terms to fall in the *same passage* (~3,000 characters), not merely in the same file, so `precast AND delay` misses files where the words appear pages apart. Measured: **1,042 returned where 3,496 match** (~70% missed); on another shape, **3,738 returned where 9,342 match** (~60% missed).
  >
  > **New behaviour, rolling out:** `AND` is file-scoped, the count is correct, and totals are more often exact rather than capped.
  >
  > **The trap: `count_exact: true` appears in BOTH cases.** The old path returns a wrong number *and* labels it exact whenever it lands under the ceiling. So `count_exact` does not distinguish them, and neither does anything else in the payload.
  >
  > **So do not rest a completeness or "not located" finding on a multi-term `AND` unless you have confirmed the fix is live for that project.** Otherwise:
  > - Run each content term as its own `files query` (or `files list --content <term>`, which IS exhaustive per term) and intersect the `file_id`s yourself. Slower, correct on every project.
  > - Or ask which projects have the file-scoped fix enabled, and say so when you rely on it.
  >
  > Using `AND` for the stricter "these words near each other" reading stays legitimate — just describe it that way rather than calling it exhaustive.
  >
  > A single content term, `title:` predicates, `OR`, and `NOT` are unaffected. When the fix reaches a project, counts on existing `AND` queries **go up** — that is the correction, not a regression.

`--search` and `--content` compose (title AND body) and combine with `--tags`, `--folder`, `--ext`, dates, etc. For anything beyond a plain AND of one title term and one body term, reach for `files query` instead.

**`files query` limits (all reported, never silent):** at most 8 OR branches, 4 levels of nesting, 16 terms. A bare `NOT` is rejected — negation needs something positive to search within (`contract NOT draft`, not `NOT draft`). An `OR` whose branches are **two or more very common** content words is rejected as too broad with an actionable message; narrow one branch or run them separately. `count_exact: false` means a ceiling was hit and the number is a floor, not a total.

**Paging through the matches (`--limit` / `--offset`).** Every `... list` subcommand — `files list`, `activities list`, `forms list`, `submissions list`, `custom-objects list`, `resources list`, `cost-codes list`, `budgets list`, `timesheets list` — returns **one page at a time**, defaulting to 25 or 50 rows. **A bare `list` call is never the complete set.** To enumerate everything, get the total first, then walk it:

```bash
pcxa files list --content "IOCC-387" --count-only              # {"count": 137} ← the real total
pcxa files list --content "IOCC-387" --limit 100 --offset 0    # rows 1–100
pcxa files list --content "IOCC-387" --limit 100 --offset 100  # rows 101–137
```

Increment `--offset` by `--limit` until you've collected `count` rows (or a page comes back short). With `--content` the order is by `id`, so paging is exhaustive — exactly `count` distinct ids, no repeats or skips. Report the number from `--count-only`, never the length of one page.

**After search → read in batch.** Once `files search` returns the rows, prefer `files batch-read --chunk file_id:chunk_position` over N single `files read` calls. Each row carries a `chunk_position` — pass `file_id:chunk_position` as `--chunk` to read just the relevant excerpt + neighbors. One round trip instead of N. Use `--outline` for section maps when files are large and you need to plan further reads.

For folder-scoped browsing, use `pcxa files list --folder <id>`.

## Tags & Folders

```bash
pcxa tags list                                                # all tags with counts
pcxa tags add 1 2 3 --tags urgent,review                      # add (preserves existing)
pcxa tags remove 1 2 --tags draft                             # remove specific tags
pcxa tags set 1 2 --tags final,approved                       # replace all tags
pcxa tags bulk --file plan.json                               # different tags per file, one request
pcxa files bulk-patch --file plan.json                        # per-file metadata plan (tags + title/category/description)
pcxa folders tree --depth 2                                   # hierarchy
pcxa folders create "Contracts" --parent 5                    # new folder
pcxa folders rename 5 "Legal"                                 # rename
pcxa folders move 5 --parent 10                               # reparent
pcxa folders contents 5                                       # subfolders + files (paginated; --timeout for slow folders)
pcxa folders subfolders 5                                     # lightweight [{id,name}] list (fast on large folders)
pcxa folders delete 5                                         # delete + all contents
pcxa move 10 11 12 --folder 5                                 # bulk move files
pcxa categorize 10 11 --category "Submittal"                  # bulk set category
pcxa files update 10 --title "New" --tags a,b --folder 5      # single file update
```

**Bulk tag/metadata plans (`files bulk-patch`, `tags bulk`):** `tags add/remove/set` apply the *same* tag set to a list of file ids. When each file needs *different* tags (or you're patching metadata exported from a spreadsheet), use the server-side `files/bulk_patch/` endpoint instead of one request per file — up to 500 rows per request, auto-chunked for larger plans, per-row validated so one bad row doesn't fail the batch. Output reports `patched`, `modified` (rows that actually changed), and `failed` (per-row errors).

`files bulk-patch --file plan.json` takes a JSON list of rows (or `{"changes": [...]}`), each row `{file_id, ...}` setting any subset of `title`, `category`, `description`, `tags` (with optional `tag_mode` ∈ `set`|`add`|`remove`, default `set`):

```json
{"changes": [
  {"file_id": 123, "tags": ["reviewed", "urgent"], "tag_mode": "add"},
  {"file_id": 124, "tags": ["legal"]},
  {"file_id": 125, "title": "ACME-0001.pdf", "category": "Contracts", "description": "Q3 amendment"}
]}
```

`tags bulk --file plan.json` is the tag-only view of the same endpoint — rows are `{file_id, tags, tag_mode}` and scalar fields (title/category/description) are rejected with a pointer to `files bulk-patch`. `folder` is not patchable via either command (folder moves recompute privacy/aggregates — use `pcxa move`). Empty `tags` in `set` mode is refused client- and server-side to prevent an accidental mass tag-wipe; use `tag_mode: remove` to clear specific tags. Add `--dry-run` to preview the plan without sending. Both are `bulk_patch`-backed; the older `tags add/remove/set` and `bulk_update` path is now set-based server-side, but unchanged for callers.

## Activities

```bash
pcxa activities list --status in_progress --priority 3,4
pcxa activities list --search "foundation" --assignee 5 --sort -due_date  # fuzzy by default; add --exact for tight
pcxa activities list --tags "structural,review" --tags-mode all  # AND mode
pcxa activities list --after 2026-03-01 --before 2026-03-31       # updated in date range
pcxa activities list --after last_month                          # relative dates supported
pcxa activities list --created-after 2026-01-01 --created-before 2026-03-31
pcxa activities list --assignee 5 --after 2026-03-01 --before 2026-03-31  # user's work in period
pcxa activities list --wbs 1.4.2                              # exact WBS path
pcxa activities list --wbs-branch 1.4                         # 1.4 and everything under it
pcxa activities list --wbs-branch 1.4 --status completed      # composes with any other filter
pcxa activities get 123                                       # detail + steps + deps
pcxa activities create --title "Review" --priority 3 --type 5 --assignees 1,2
pcxa activities create --title "Pour slab" --custom-fields '{"3":"Acme Corp"}'  # custom-object value, fuzzy-validated
pcxa activities update 123 --status completed --percent 100
pcxa activities update 123 --outcome "Grain is one row per charge per register."
pcxa activities update 123 --outcome ""                       # clear a wrong outcome
pcxa activities update 123 --custom-fields '{"3":"Acme Corp"}' --no-fuzzy        # write value as-is
pcxa activities delete 123 456                                # bulk delete
pcxa activities bulk-update 1 2 3 --status in_progress
pcxa activities types                                         # list templates
pcxa activities related 123                                   # linked files/folders/photos/forms
```

**Statuses:** `not_started`, `in_progress`, `completed` | **Priority:** 0=none, 1=low, 2=med, 3=high, 4=critical

**Descriptions support Markdown** (headings, tables, bold, lists). Keep descriptions focused on:
- **Scope/objective** — what the activity is and what it produces
- **Requestor** — who asked for it, with reference to source communication (e.g., `file:247764`)
- **Business justification** — why this work exists

Do NOT put in descriptions: processing details, scripts, output file lists, status updates, or progress notes. Those belong in **comments** (`pcxa comments add`) as dated narrative entries.

**`--outcome` is the answer; `--description` is the question.** Description is the brief — what was asked and why. Outcome is what the activity concluded or produced: the decision, the finding, the deliverable. It is *not* production or quantity (that is `--percent`), and it is deliberately not called "output", which in this codebase means measurable production. Put the conclusion here rather than burying it in the newest comment — outcome is semantically indexed, so "what did we decide" stays findable.

Every edit is kept in the activity's history, so revising an outcome loses nothing. `--outcome ""` clears it.

Two limits worth knowing before you plan around them. Both are lifted by PCX-Analytics/pcxa#2863, so check whether that has deployed before designing around them:
- **`bulk-update` has no `--outcome`.** The server's bulk allow-list rejects the field. Update them one at a time.
- **`activities list --search` does not read outcomes.** Server-side search covers title, description and WBS code only. The outcome *is* indexed for semantic search, so `pcxa chat send` can find it when `--search` cannot.

**WBS filters:** `--wbs 1.4.2` matches that one activity exactly. `--wbs-branch 1.4` returns 1.4 plus its whole subtree — 1.4.1, 1.4.2.7, at any depth — but **not** 1.40 or 1.41, which merely share a digit prefix. Both are server-side and compose with every other filter, and a `wbs` column is added to the table so you can confirm the scope rather than trust it.

Requires an API with the WBS filters (PCX-Analytics/pcxa#2863). Against an older API the CLI **aborts rather than printing**: django-filter silently ignores parameters it does not know, so an unpatched server answers `--wbs-branch 1.4` with the entire project and a 200. The CLI re-checks every returned row and refuses a result it can prove is out of scope — you get a loud error, never a project-wide list mislabelled as a branch.

**Date filters:** `--after`/`--before` filter by last updated; `--created-after`/`--created-before` filter by creation date. Accepts `YYYY-MM-DD` or relative keywords: `today`, `last_7_days`, `this_month`, `last_quarter`, etc.

**Name resolution:** `--assignee` and `--owner` accept user IDs or names. Names are fuzzy-matched against project members:
- Exact/substring match → resolves automatically with confirmation message
- Multiple close matches → lists candidates with IDs for you to pick
- No match → suggests `pcxa project members` to list all

**Custom-object fields:** `--custom-fields` takes a JSON map of `{custom_field_id: value}` (custom fields are defined at the project level). Values targeting a custom-object-backed field are fuzzy-validated the same way form submissions are — see **Custom Objects**. Pass `--no-fuzzy` to write the raw value.

**Invoicing workflow:** Query a user's activity in a billing period by name — no `--status` filter needed since `--after`/`--before` captures any work (started, progressed, or completed):
```bash
pcxa activities list --assignee "John" --after 2026-03-01 --before 2026-03-31
```

### Related items (`activities related`)

What is attached to an activity — files, folders, photos, form submissions and
records — in **one call, in both directions**. This is the CLI view of the web
app's "Related items" rail.

```bash
pcxa activities related 123                       # everything linked, either direction
pcxa activities related 123 --types file,folder   # only files and folders
pcxa activities related 123 --all                 # follow cursors to the end
pcxa activities related 123 --limit 100           # rows per page
```

The `dir` column says which way the link points: `->` the activity is the
link's source, `<-` it is the target.

Prefer this over two `links list` calls. Links point either way, so the flat
endpoint needs one query for `--source activity:123`, another for `--target
activity:123`, and a merge; this is anchored on the activity and resolves the
far end of every edge for you. It also runs on the project-scoped route, where
the company and project permission gates actually fire.

**It does not report a total, and that is deliberate** — an exact count would
mean enumerating and permission-filtering the whole neighbourhood. So the row
count is what was fetched, never what exists. Three notices tell you when the
view is partial, and all three go to stderr:

- **`... cannot be resolved by this endpoint and are NOT listed above`** — a
  link of that type exists on the activity but this endpoint cannot hydrate it.
  **Drawings are the common case**: `links create --target drawing:N` works and
  the web app makes them, but `related` cannot show them. Use
  `pcxa links list --source activity:123` to see those edges.
- **`the server stopped scanning on its own budget`** — more links may exist
  beyond what was scanned.
- **`More results. Use --all, or --cursor …`** — pagination is cursor-based;
  `--all` follows it, stopping after 50 pages and printing the cursor to resume.

## Steps (Subtasks)

When steps exist, activity progress auto-calculates from weighted step completion.

```bash
pcxa steps list 123                                           # list steps
pcxa steps create 123 --name "Draft review" --weight 40
pcxa steps update 123 45 --percent 100                        # mark step complete
pcxa steps delete 123 45                                      # weights rebalance
pcxa steps from-template 123                                  # create from type template
```

## Progress

```bash
pcxa progress list 123                                        # progress timeline
pcxa progress add 123 --percent 50 --notes "Halfway"
pcxa progress add 123 --percent 25 --date 2026-03-15          # backdate
pcxa progress delete 123 789                                  # manual entries only
```

## Comments

```bash
pcxa comments list 3712                                       # list comments on activity
pcxa comments add 3712 --content "[2026-03-16] Drafting initiated..."
pcxa comments delete 3712 2952                                # delete comment by ID
pcxa comments bulk 3712 --file comments.json                  # bulk add from JSON file
```

**Bulk JSON format** (`comments.json`):
```json
[
  {"content": "[2026-03-16] Drafting initiated per weekly report #12"},
  {"content": "[2026-03-23] Internal review completed, minor revisions needed"}
]
```

Supports both bare list and wrapper object with `"comments"` key.

## Dependencies (CPM)

Types: `FS` (finish-to-start), `SS`, `FF`, `SF`. Lag in days (negative = lead).

```bash
pcxa deps list --predecessor 10
pcxa deps create --predecessor 10 --successor 20 --type FS --lag 2
pcxa deps delete 456
```

## Tag-Filter Links (evidence sets on an activity)

Attach a **saved tag query** to an activity as one link that stands for a whole *set* of files — the AND/OR combination a plain object link can't express. "Pay apps for Yates" = tags `pay_app` **and** `yates` in `all` mode. The set is **dynamic**: it resolves live to whatever files currently carry the tags (`pcxa files list --tags pay_app,yates --tags-mode all`), and the web app renders each link as a chip that deep-links into the Files tab with the filter pre-applied.

```bash
pcxa tag-filters list 5080                                          # links on activity 5080
pcxa tag-filters add 5080 --tags pay_app,yates --mode all           # AND: files with BOTH tags
pcxa tag-filters add 5080 --tags rfi,submittal --mode any --label "Open items"  # OR (default)
pcxa tag-filters delete 5080 12                                     # remove link 12
```

`--mode all` requires **every** tag (AND); `--mode any` (default) matches **any** tag (OR) — same semantics as `files list --tags-mode`. Up to 20 tags per link; `--label` overrides the default `tag1 + tag2` display label. This is the CLI surface for the backend's `ActivityTagFilterLink` (nested under `activities/{id}/tag-filter-links/`). Use it instead of `links create --target …` when the evidence is defined by a tag combination rather than a fixed object.

## Gantt & WBS Tree

```bash
pcxa gantt --status in_progress
pcxa tree --max-depth 3
```

## Forms (Templates)

Form templates define reusable forms with typed fields. Submissions are instances of a form.

```bash
pcxa forms list --category Safety                     # list templates
pcxa forms list --scope project --search "inspection"
pcxa forms get 1                                      # detail + fields
pcxa forms create --name "Safety Inspection" --category Safety --code-prefix SI --code-padding 3
pcxa forms update 1 --description "Updated desc" --reviewers 3,5
pcxa forms delete 1
```

**Scope:** `project` (default) or `company`. **Code settings:** `code-prefix` (e.g. RFI), `code-scope` (project/company), `code-separator` (default: -), `code-padding` (3=001).

## Fields (Form Fields)

Fields define the structure of a form template. Field values are referenced by field ID in submissions.

```bash
pcxa fields list 1                                    # list fields for form 1
pcxa fields create 1 --label "Inspector" --type text --required --order 1
pcxa fields create 1 --label "Severity" --type select --options '{"choices":["Low","Med","High"]}'
pcxa fields create 1 --label "Date" --type date --required --order 2
pcxa fields create 1 --label "Vendor" --type choice --choice-id 21      # custom-object-backed
pcxa fields update 1 5 --label "New Label" --required true
pcxa fields delete 1 5
```

**Field types** (exact set): `text`, `textarea`, `number`, `date`, `datetime`, `checkbox`, `select`, `radio`, `choice`, `table`, `photo`, `file`, `location`.
- `select`/`radio`: inline options via `--options '{"choices":["A","B"]}'`.
- `choice`: backed by a **custom object** (field-choice) — bind with `--choice-id <object_id>`; submission values are fuzzy-validated (see **Custom Objects**).
- `table`: a repeating grid of typed columns — see **Table fields** below.

### Table fields

A `table` field holds a list of rows, each with the same typed columns. **The columns go in `table_schema` (a JSON array), NOT `--options`** — the CLI exposes a dedicated `--table-schema` flag for this. `--options` is silently accepted by the API but ignored by the forms UI, so a table defined via `--options` renders with no columns.

```bash
pcxa fields create 1 --label "Line Items" --type table --required \
  --table-schema '[
    {"name":"item","field_type":"text","label":"Item"},
    {"name":"qty","field_type":"number","label":"Qty"},
    {"name":"unit","field_type":"select","label":"Unit","options":["ea","lf","sf"]}
  ]' \
  --min-rows 1 --max-rows 20
```

- Each column needs a **`name`** (the key used in submission values), a **`field_type`**, and a **`label`**. Column `name` keys must be stable — submission row values are keyed by them.
- `--min-rows` / `--max-rows` bound the row count (optional).
- `pcxa fields list 1` and `pcxa forms get 1` show a table field's columns (`cols: item, qty, …`) and a choice field's binding (`custom-object 21`).

## Submissions (Form Submissions)

Submissions are instances of a form template, with field values keyed by field ID.

```bash
pcxa submissions list --form 1                        # list for a specific form
pcxa submissions list --status draft                  # filter by status
pcxa submissions get 1 42                             # detail (form_id submission_id)
pcxa submissions create 1 --code SI-001 --values '{"1":"John","2":"2026-03-24","3":"High"}'
pcxa submissions update 1 42 --values '{"3":"Critical"}' --merge --tags urgent  # change only field 3
pcxa submissions delete 1 42
```

**Statuses:** `draft`, `submitted`, `closed`. `--values` is a JSON object mapping **field ID → value**. The value shape depends on the field type:

| Field type | Value shape | Example (`"3"` = field ID) |
|---|---|---|
| text/number/date/select/radio/choice | scalar | `{"3":"High"}`, `{"3":42}` |
| checkbox | boolean | `{"3":true}` |
| table | **array of row objects keyed by column `name`** | `{"3":[{"item":"Rebar","qty":50},{"item":"Concrete","qty":12}]}` |

For a **table** field, each row is an object whose keys are the column `name`s from the field's `table_schema`. Example creating a submission with a 2-row table in field `7`:

```bash
pcxa submissions create 1 --code SI-002 \
  --values '{"7":[{"item":"Rebar","qty":50,"unit":"lf"},{"item":"Concrete","qty":12,"unit":"sf"}]}'
```

> ⚠️ **The API does NOT validate value shapes.** It accepts a wrong shape (a scalar where a table is expected, rows keyed by the wrong names, a `{"rows":[…]}` wrapper, etc.) without error — but the forms UI then renders the field broken. Match the field type and the column `name`s exactly. Use `pcxa fields list <form_id>` to confirm a table field's columns before submitting.

> ⚠️ **`update --values` replaces the entire values dict by default.** Passing a partial `--values` (e.g. just `{"3":"Critical"}`) clears every other field on the submission. Pass **`--merge`** (alias `--patch`) to update only the keys you supply, leaving the rest intact. Without `--merge` the CLI prints a one-line overwrite warning to stderr.
>
> **`--merge` works at field-ID granularity only — it cannot merge *within* a table value.** To change one cell or add one row, resend the field's **entire** row array: `pcxa submissions get` the current value, edit the array, and send it back under that field's key with `--merge`.

Values targeting a custom-object-backed (`choice`) field are fuzzy-validated on create/update — see **Custom Objects** below. Pass `--no-fuzzy` to skip. (Custom-object columns *inside* a table row are not yet fuzzy-validated — verify those values against the object's options yourself.)

## Custom Objects (Field Choices)

Custom objects are reusable, typed lookup tables (`field-choices` in the API). A custom object defines a `property_schema` (its columns — a JSON **list** of `{"name", "type"}` objects) and holds **options** (rows), each with a `label` and a `properties` JSON of column values. Form fields can be backed by a custom object so submissions pick from its options. Objects exist at **project** (default) or **company** scope; a company object can be surfaced into a project with `extend`.

```bash
pcxa custom-objects list                                    # project-scoped objects
pcxa custom-objects list --scope company --search vendor     # company-scoped, filtered
pcxa custom-objects get 5                                   # schema + first options
pcxa custom-objects create --name "Vendors" --schema '[{"name":"code","type":"text"}]'
pcxa custom-objects create --name "Trades" --scope company --extensible true
pcxa custom-objects update 5 --description "Approved vendor list"
pcxa custom-objects extend 5                                # company object → current project
pcxa custom-objects delete 5
```

### Options (rows)

```bash
pcxa custom-objects options list 5                          # rows of object 5
pcxa custom-objects options create 5 --label "Acme Corp" --properties '{"code":"A1"}'
pcxa custom-objects options update 5 42 --label "Acme Corporation" --order 1
pcxa custom-objects options delete 5 42
pcxa custom-objects options bulk-create 5 --file options.json   # [{"label":..,"properties":..}, ...]
pcxa custom-objects options reorder 5 --order "42,17,9"      # option ids in desired order
```

Pass `--scope company` to any object/option command when the object is company-scoped.

### Fuzzy matching ("did you mean?")

When a value should reference a custom object, the CLI fuzzy-matches it against that object's options:

```bash
pcxa custom-objects resolve 5 "Acme Crp"
# → No match for 'Acme Crp'. Did you mean 'Acme Corp' (option 10)?
```

`resolve` exits non-zero when there is no confident match. **Form submissions and activity custom fields run the same check automatically:** on `submissions create`/`update` with `--values` or `activities create`/`update` with `--custom-fields`, any value targeting a custom-object-backed field is validated — an exact label/id or a unique substring resolves silently; anything else blocks the write with a "Did you mean Y?" suggestion. Pass `--no-fuzzy` to write the raw value. Validation reads the field definitions (form fields, or activity custom fields at `activities/custom-fields/`) and the object's options (trying project then company scope); a permissions or network error degrades to a warning and never blocks an otherwise-valid write.

## Resources

Resources represent people, equipment, consumables, or subcontractors assigned to activities.

```bash
pcxa resources list --type personnel --active true
pcxa resources get 1                                  # detail with rates
pcxa resources create --name "John Smith" --type personnel --user 5
pcxa resources create --name "Crane #3" --type equipment --unit hours --capacity 10
pcxa resources update 1 --name "John D. Smith" --active false
pcxa resources delete 1
```

**Types:** `personnel`, `equipment`, `consumable`, `subcontractor` | **Units:** `hours`, `days`, `each`, `lump_sum`

Only `personnel` resources can link to a user (`--user`). Use `--user 0` to unlink.

## Rates (Resource Rates)

Append-only rate history. Create new rates with new effective dates — no updates/deletes.

```bash
pcxa rates list 1                                     # rates for resource 1
pcxa rates create 1 --effective-date 2026-04-01 --standard-rate 75 --cost-rate 50 --bill-rate 100
pcxa rates create 1 --effective-date 2026-04-01 --standard-rate 75 --cost-rate 50 --bill-rate 100 --overtime-rate 112.50
```

## Assignments (Resource → Activity)

```bash
pcxa assignments list 123                             # list for activity 123
pcxa assignments create 123 --resource 5 --planned-units 40 --role "Lead Engineer"
pcxa assignments create 123 --resource 8 --planned-units 80 --curve front_loaded --driving
pcxa assignments update 123 45 --remaining 20 --at-completion 50
pcxa assignments delete 123 45
```

**Curves:** `uniform`, `front_loaded`, `back_loaded`, `bell`

## Cost Codes (Company-Scoped)

Hierarchical cost tracking codes shared across all projects.

```bash
pcxa cost-codes list --root-only
pcxa cost-codes get 1                                 # detail with children
pcxa cost-codes create --code "03.300" --name "Cast-in-Place Concrete"
pcxa cost-codes create --code "03.310" --name "Structural" --parent 1
pcxa cost-codes update 1 --active false
pcxa cost-codes delete 1
```

## Budgets (Cost Code Budgets)

```bash
pcxa budgets list
pcxa budgets create --cost-code 1 --amount 50000 --units 200
pcxa budgets update 1 --amount 75000
pcxa budgets delete 1
```

## Timesheets

Time/cost tracking per resource with approval workflow: `draft` → `submitted` → `approved` | `rejected` → `draft`.

```bash
pcxa timesheets list --status draft --resource 5
pcxa timesheets list --after 2026-03-01 --before 2026-03-31
pcxa timesheets get 1                                 # detail with all entries
pcxa timesheets create --resource 5 --period-start 2026-03-24 --period-end 2026-03-30 --period-type weekly
pcxa timesheets update 1 --period-end 2026-03-31
pcxa timesheets delete 1
pcxa timesheets submit 1                              # draft → submitted
pcxa timesheets approve 1                             # submitted → approved
pcxa timesheets reject 1 --reason "Missing entries"   # submitted → rejected
pcxa timesheets reopen 1                              # rejected → draft
```

**Period types:** `weekly`, `biweekly`, `monthly`. Only draft/rejected timesheets are editable.

## Time Entries (nested under timesheets)

```bash
pcxa entries list 1                                   # entries for timesheet 1
pcxa entries list 1 --date 2026-03-25 --activity 123
pcxa entries create 1 --activity 123 --date 2026-03-25 --hours 8
pcxa entries create 1 --activity 123 --date 2026-03-25 --hours 2 --type overtime --cost-code 5
pcxa entries update 1 42 --hours 6 --description "Half day"
pcxa entries delete 1 42
```

**Types:** `regular`, `overtime`, `double_time` | Hours: 0.01–24

## Cost Entries (nested under timesheets)

```bash
pcxa cost-entries list 1
pcxa cost-entries create 1 --resource 8 --activity 123 --date 2026-03-25 --quantity 5 --unit-cost 200
pcxa cost-entries update 1 42 --quantity 10
pcxa cost-entries delete 1 42
```

`total_cost` auto-computed: `quantity × unit_cost`.

## Time Period Locks

Lock date ranges to prevent edits.

```bash
pcxa locks list
pcxa locks create --period-start 2026-03-01 --period-end 2026-03-15 --reason "Month closed"
pcxa locks delete 1
```

## Links (Entity Links)

Connect any two objects (files, activities, photos, drawings) with contextual descriptions. Object references use `type:id` format.

**Types:** `activity`, `file`, `folder`, `photo`, `drawing`, `source_document`, `markup`, `project`, `company`, `formsubmission`, `fieldchoiceoption`

An unknown type is rejected locally, with the valid set and a "did you mean?"
suggestion, instead of costing a round-trip and coming back as a bare 400.

To read an activity's links, prefer `pcxa activities related <id>` — one call,
both directions, far ends resolved. Note the two type sets differ: every type
above is *linkable*, but `related` can only resolve `activity`, `file`,
`folder`, `photo`, `formsubmission` and `fieldchoiceoption`. Drawing links are
creatable and visible to `links list`, but `related` reports them as omitted
rather than showing them.

```bash
pcxa links list --source file:170106                  # links from a file
pcxa links list --target activity:3710                # links to an activity
pcxa links list --source file:170106 --target activity:3710  # specific pair
pcxa links create --source file:170106 --target file:170107 --type attachment
pcxa links create --source activity:3710 --target file:170106 --type deliverable
pcxa links create --source file:170129 --target file:170100 --type "supersedes" --description "Corrected analysis"
pcxa links delete 42
pcxa links bulk --file links.json                     # bulk create from JSON
```

**Bulk JSON format** (`links.json`):
```json
[
  {"source": "file:170106", "target": "file:170107", "type": "attachment"},
  {"source": "activity:3710", "target": "file:170106", "description": "RFI analysis deliverable"},
  {"source_type": "file", "source_id": 170129, "target_type": "file", "target_id": 170100, "type": "supersedes"}
]
```

The `--type` flag sets the description field (the backend doesn't enforce typed relationships). Use `--description` for longer context. Both shorthand (`"source": "file:123"`) and explicit (`"source_type": "file", "source_id": 123`) formats work in bulk JSON.

`links bulk` calls the server-side bulk endpoint (up to 500 links per request, auto-chunked for larger files) instead of one request per link — rows are validated independently server-side, so one bad row doesn't fail the batch. Output reports `created`, `exists` (already-present links, not an error), and `failed` (per-row errors with the row index).

## AI Chat

Send messages to the project's AI assistant and read back its replies. Useful for ad-hoc probing and for letting an agent evaluate chatbot response quality. Project-scoped — uses the `.pcxa` company/project.

```bash
pcxa chat send "What is the status of the foundation work?"          # uses current conversation, waits for reply
pcxa chat send "..." --new --title "Eval run 1"                      # fresh conversation
pcxa chat send "..." --conversation 123                              # continue an existing thread
pcxa chat send "..." --research                                      # enable file-search tools (research_mode)
pcxa chat send "..." --model gemini-2.5-pro                          # override model (see chat models)
pcxa chat send "..." --no-wait                                       # fire-and-forget; returns agent_task_id
pcxa chat send "..." --timeout 300                                   # polling timeout (default 180s)

pcxa chat ls --search "RFI"                                          # list conversations
pcxa chat get [ID]                                                    # show full transcript (default: current)
pcxa chat get 123 --show-tools                                        # include tool calls
pcxa chat new --title "Probe"                                         # create empty conversation
pcxa chat delete 123                                                  # soft-delete (archive)
pcxa chat models                                                      # list available models
```

**Default JSON output** (from `chat send`):
```json
{
  "conversation_id": 42,
  "agent_task_id": 9001,
  "agent_task_status": "completed",
  "elapsed_seconds": 14.2,
  "timed_out": false,
  "user_message": {"id": 500, "content": "..."},
  "assistant_message": {
    "id": 501, "role": "assistant", "content": "...",
    "tool_steps": [...], "thinking_steps": [...], "action_cards": [...]
  }
}
```

**How it works:** `chat send` submits a message, waits for the platform's assistant task to finish, and returns the assistant response. Exit code 2 means the task failed. `--no-wait` skips polling and returns the task ID immediately.

## Reporting API errors

When a PCXa CLI command returns a non-2xx API response, surface the endpoint, status code, and response body to the user. Do not invent alternate data paths; ask the user how they want to proceed.

## Invocation

`/pcxa $ARGUMENTS` → `pcxa $ARGUMENTS` (if installed via pipx) or `python .claude/skills/pcxa/pcxa.py $ARGUMENTS`
