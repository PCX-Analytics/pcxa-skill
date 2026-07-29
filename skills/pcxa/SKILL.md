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
pcxa files query 'title:schedule AND precast AND delay'  # BOOLEAN: AND/OR/NOT + grouping, exhaustive + exact count
pcxa files query 'title:report AND (delay OR "change order")'   # grouping + quoted phrase
pcxa files query 'contract NOT draft' --ext PDF          # exclusion (impossible with --search/--content)
pcxa files query 'title:"Case Memo"' --count-only        # true LITERAL phrase on the filename
pcxa files list --content "IOCC-387"                     # literal substring in file BODY — every match, paginated (stable id order)
pcxa files list --content "IOCC-387" --count-only        # exact total of content matches (count:0 = genuinely not located)
pcxa files list --tags "urgent,review" --tags-mode all   # AND: files with ALL tags
pcxa files search "natural language query"                    # hybrid BM25 + semantic (same endpoint as the web UI)
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
```

**Upload storage:** Small files are uploaded through the API. Larger files use a presigned upload flow handled by the CLI and API.

**Bulk tree sync (`files sync`):** Mirrors a local directory tree under a PCXA folder. Walks the tree, creates any missing subfolders to match, and uploads files in parallel via the same presign+PUT/multipart path as `files upload`. Idempotent two ways: it lists each target folder once and skips local files whose name already exists there, and an optional `--manifest <path>` persists `{relative_path → {size, file_id}}` so re-runs skip without hitting the API. Failures (network, register errors) are listed at the end and counted toward `error` rate. Progress is rendered live on stderr: a bar plus files-done, bytes-done/total, throughput, current concurrency (`c=N`), elapsed, ETA, and error count. Filters: `--include` and `--exclude` accept repeatable globs against filenames; dotfiles/dot-dirs are skipped by default (`--include-hidden` opts in).

**Designed for TB-scale runs:**
- **Auto-tuning concurrency** (default ON): an AIMD controller samples throughput and error rate every 10s and adjusts active workers via a runtime-resizable semaphore. Errors halve concurrency with a 30s cooldown; clean windows with rising throughput step it up. Bound by `--min-concurrency` / `--max-concurrency` (default 1 / 32). Pass `--no-auto-tune` to pin concurrency at `--concurrency` for the whole run. Tuner decisions print above the progress line so you can see what's happening.
- **Failure-budget circuit breaker** (`--max-failures`, default 100): aborts the run cleanly if cumulative errors hit the budget, so a misconfigured target folder doesn't burn hours of bandwidth. Set `0` to disable.
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

**Search response shape:** `pcxa files search` returns `{query, total_results, results, hybrid_enabled}` — a top-N reranked list (server-capped at 50). Each row carries `score`, `file_id`/`activity_id`, `file_name`/`title`, `folder_path`, `page_number`, `chunk_position`, and a `url`. Hybrid means the result is the union of Pinecone semantic similarity and BM25 over the project's chunk text, RRF-fused and Cohere-reranked — the same path the web UI's search bar uses.

**Four ways to find files — pick by what you need:**
- **`files list --search <term>`** — matches the **title/filename** only (trigram). Paginated + countable. Use when you know part of the name.
- **`files list --content <term>`** — matches the indexed **body/contents** as a **literal substring** (not ranked, not semantic). Paginated + **countable and exhaustive**: `--content <term> --count-only` gives the *exact* total and you can page through *every* match in a stable order (by `id`). `count: 0` means the term is genuinely in no in-scope file — so this is the path for a "not located" / completeness finding. Use it to find files by what's inside them — e.g. an eDiscovery estate where emails are named by bare Bates numbers (`YATES002119058`) and the term only appears in the body. Only indexed files match; scope with `--folder`/`--ext`/`--index-status`.
- **`files search <term>` / `files content <term>`** — hybrid semantic + keyword **ranking** (relevance-ordered), a **top-50 reranked sample, not a total**. Best for natural-language / "most relevant" lookups. Do **not** use it to count or enumerate — it can't page past 50, and asking for more (`--limit 200`) is clamped to 50 with a notice pointing you at `--content`. For "how many / list them all", use `--content`.

- **`files query '<expr>'`** — **boolean** search: `AND` / `OR` / `NOT`, parentheses for grouping, `title:` / `content:` field scoping, and `"quoted phrases"` matched adjacently. Exhaustive with an **exact count**, like `--content`, but structurally expressive. Use it whenever the question has more than one condition — *"schedule in the title AND both precast and delay in the body"* is one call: `pcxa files query 'title:schedule AND precast AND delay'`. Bare terms search content; operators must be UPPERCASE (lowercase `and`/`or` are ordinary words). Every response echoes `parsed` — **read it** to confirm the query was understood before trusting the results.

`--search` and `--content` compose (title AND body) and combine with `--tags`, `--folder`, `--ext`, dates, etc. For anything beyond a plain AND of one title term and one body term, reach for `files query` instead.

**⚠️ `AND` currently means "same passage", not "same file".** Two terms joined by `AND` must appear within the same ~3,000-character chunk of a document, not merely somewhere in the same file. Measured on a real corpus this under-reports by **60-70%**: `rebar AND delay` returns 1,042 files where 3,496 actually contain both words. It never returns a wrong file — it silently omits correct ones.

Until this is fixed (blocked on a backend index change), treat an `AND` result as a **floor, not a complete set**, and do not use it alone to support a "not found" conclusion. To widen recall, search each term separately with `files list --content <term> --count-only` and intersect the ids yourself.

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

`tags bulk --file plan.json` is the tag-only view of the same endpoint — rows are `{file_id, tags, tag_mode}` and scalar fields (title/category/description) are rejected with a pointer to `files bulk-patch`. `folder` is not patchable via either command (folder moves recompute privacy/aggregates — use `pcxa move`). Empty `tags` in `set` mode is refused client- and server-side to prevent an accidental mass tag-wipe; use `tag_mode: remove` to clear specific tags. Add `--dry-run` to preview the plan without sending. Both are `bulk_patch`-backed; the older `tags add/remove/set` and `bulk_update` path is now set-based server-side (pmapp2 #1265) but unchanged for callers.

## Activities

```bash
pcxa activities list --status in_progress --priority 3,4
pcxa activities list --search "foundation" --assignee 5 --sort -due_date  # fuzzy by default; add --exact for tight
pcxa activities list --tags "structural,review" --tags-mode all  # AND mode
pcxa activities list --after 2026-03-01 --before 2026-03-31       # updated in date range
pcxa activities list --after last_month                          # relative dates supported
pcxa activities list --created-after 2026-01-01 --created-before 2026-03-31
pcxa activities list --assignee 5 --after 2026-03-01 --before 2026-03-31  # user's work in period
pcxa activities get 123                                       # detail + steps + deps
pcxa activities create --title "Review" --priority 3 --type 5 --assignees 1,2
pcxa activities create --title "Pour slab" --custom-fields '{"3":"Acme Corp"}'  # custom-object value, fuzzy-validated
pcxa activities update 123 --status completed --percent 100
pcxa activities update 123 --custom-fields '{"3":"Acme Corp"}' --no-fuzzy        # write value as-is
pcxa activities delete 123 456                                # bulk delete
pcxa activities bulk-update 1 2 3 --status in_progress
pcxa activities types                                         # list templates
```

**Statuses:** `not_started`, `in_progress`, `completed` | **Priority:** 0=none, 1=low, 2=med, 3=high, 4=critical

**Descriptions support Markdown** (headings, tables, bold, lists). Keep descriptions focused on:
- **Scope/objective** — what the activity is and what it produces
- **Requestor** — who asked for it, with reference to source communication (e.g., `file:247764`)
- **Business justification** — why this work exists

Do NOT put in descriptions: processing details, scripts, output file lists, status updates, or progress notes. Those belong in **comments** (`pcxa comments add`) as dated narrative entries.

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

**Types:** `file`, `activity`, `photo`, `drawing`, `source_document`, `project`

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
