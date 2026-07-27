"""argparse setup for the pcxa CLI.

`build_parser()` returns a fully-configured ArgumentParser. Dispatch from parsed
args to command functions happens in `pcxa._main` via the HANDLERS / SUB_HANDLERS
tables — `set_defaults(func=...)` is intentionally not used.
"""

import argparse

from pcxa import __version__
from pcxa.commands.activities import MAX_TAG_FILTER_TAGS, TAG_FILTER_MODES
from pcxa.commands.tags_folders import DELETION_TAG


def build_parser():
    parser = argparse.ArgumentParser(
        prog="pcxa",
        description="PCXA construction intelligence platform CLI.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--profile", "-p", default=None, help="Config profile")
    parser.add_argument("--format", "-f", choices=["json", "table"], default="json", help="Output format")
    parser.add_argument("--dry-run", action="store_true", help="Preview without changes")
    parser.add_argument("--version", "-V", action="version", version=f"pcxa {__version__}")

    sub = parser.add_subparsers(dest="command", help="Commands")

    # ── login (browser-based) ──
    p = sub.add_parser("login", help="Browser-based login (opens pcxa.app, no password needed)")
    p.add_argument("--url", default="https://api.pcxa.app", help="API URL")
    p.add_argument("--frontend-url", default="https://www.pcxa.app", help="Frontend URL")
    p.add_argument("--profile", help="Profile name (default: prod)")
    p.add_argument("--timeout", type=int, default=120, help="Seconds to wait for browser (default: 120)")
    p.add_argument("--no-setup", action="store_true",
                   help="Skip the post-login interactive company/project picker. Use when an agent (e.g. Claude Code) drives login non-interactively; the agent should follow up with `pcxa projects` and `pcxa set-project`.")
    p.add_argument("--global", dest="use_global", action="store_true",
                   help="Store credentials in the global ~/.pcxa/credentials.json instead of a per-repo .pcxa-credentials.json.")

    # ── projects (list accessible projects across companies) ──
    p = sub.add_parser("projects", help="List all projects you have access to (across companies)")
    p.add_argument("-f", "--format", choices=("json", "table"), default="json", help="Output format")
    p.add_argument("--profile", help="Profile name (default: active)")

    # ── setup ──
    p = sub.add_parser("setup", help="Configure profile and login (password-based)")
    p.add_argument("--url", default="https://api.pcxa.app", help="API URL (default: https://api.pcxa.app)")
    p.add_argument("--username", "-u", required=True, help="Username/email")
    p.add_argument("--password", default=None, help="Password (omit to be prompted)")
    p.add_argument("--company", type=int, help="Company ID")
    p.add_argument("--project", type=int, help="Project ID")
    p.add_argument("--frontend-url", help="Frontend URL (defaults to --url)")
    p.add_argument("--global", dest="use_global", action="store_true",
                   help="Store credentials in the global ~/.pcxa/credentials.json instead of a per-repo .pcxa-credentials.json.")

    # ── whoami ──
    sub.add_parser("whoami", help="Show current profile")

    # ── set-project ──
    p = sub.add_parser("set-project", help="Set default project (globally or per-repo)")
    p.add_argument("project_id", type=int, nargs="?", help="Project ID (omit to choose from menu if multiple exist)")
    p.add_argument("--company", type=int, help="Company ID (only used with --local)")
    p.add_argument("--user", help="Pin auth account by email for this repo (only used with --local)")
    p.add_argument("--local", action="store_true", help="Write to .pcxa in CWD instead of global config")

    # ── update ──
    p = sub.add_parser("update", help="Update pcxa to the latest release from GitHub")
    p.add_argument("--dry-run", action="store_true", help="Print what would be run without executing")

    # ── project ──
    proj_p = sub.add_parser("project", help="View/update project metadata")
    proj_sub = proj_p.add_subparsers(dest="project_command")

    proj_sub.add_parser("get", help="Show project details")

    p = proj_sub.add_parser("members", help="List project members (name → user ID)")
    p.add_argument("--search", "-s", help="Search by name or username")

    p = proj_sub.add_parser("update", help="Update project metadata")
    p.add_argument("--name", help="Project name")
    p.add_argument("--code", help="Short project code (max 20 chars)")
    p.add_argument("--description", help="Project description")
    p.add_argument("--scope-statement", dest="scope_statement", help="Scope statement (objectives, deliverables, boundaries)")
    p.add_argument("--industry", help="Industry")
    p.add_argument("--life-cycle", dest="life_cycle", help="Project lifecycle stage")
    p.add_argument("--start-date", dest="start_date", help="Start date (YYYY-MM-DD)")
    p.add_argument("--end-date", dest="end_date", help="End date (YYYY-MM-DD)")
    p.add_argument("--progress-input-method", dest="progress_input_method", choices=["status", "percentage"], help="How activities report progress")
    p.add_argument("--rollup-method", dest="rollup_method", choices=["equal", "duration", "cost", "labor"], help="Summary rollup weighting")

    # ── files ──
    files_p = sub.add_parser("files", help="File search & reading")
    files_sub = files_p.add_subparsers(dest="files_command")

    p = files_sub.add_parser("list", help="Filter files by metadata")
    p.add_argument("--ext", help="File types (comma-sep: PDF,DOCX)")
    p.add_argument("--tags", help="Tags filter (comma-sep)")
    p.add_argument("--tags-mode", dest="tags_mode", choices=["any", "all"], help="any=OR (default), all=AND")
    p.add_argument("--folder", type=int, help="Folder ID")
    p.add_argument("--category", help="Category")
    p.add_argument("--search", "-s", help="Title search (trigram fuzzy by default; exact matches rank first)")
    p.add_argument("--content", help="Full-text search of file BODY/contents (paginated + countable, unlike `files search`). "
                                     "Finds files by what's inside them even when the filename doesn't match.")
    p.add_argument("--exact", action="store_true",
                   help="Tight substring matching (rejects typos). Default is fuzzy with exact-first ranking.")
    p.add_argument("--index-status", choices=["indexed", "pending", "processing", "failed"])
    # Default is None (not "-created_at") so fuzzy search can rank by
    # similarity DESC. When --search is absent we fall back to -created_at
    # in cmd_files_list. An explicit --sort always wins.
    p.add_argument("--sort", default=None, help="Sort field (default: -created_at; similarity when --search)")
    p.add_argument("--count-only", action="store_true")
    p.add_argument("--limit", type=int, default=25)
    p.add_argument("--offset", type=int, default=0)

    p = files_sub.add_parser(
        "search",
        help="Hybrid semantic + keyword search (same endpoint as the web UI)",
    )
    p.add_argument("query", help="Natural language query")
    p.add_argument(
        "--scope",
        help="Sources to search: csv subset of file,activity,drawing,photo (default: all)",
    )
    p.add_argument("--ext", help="File type filter (csv, e.g. PDF,DOCX)")
    # `--limit` is an alias for `--page-size` for symmetry with `files list`
    # / `files content` / etc., which agent wrappers expect to use uniformly.
    p.add_argument("--page-size", "--limit", dest="page_size", type=int, default=25,
                   help="Max results (server-capped at 50)")

    p = files_sub.add_parser(
        "content",
        help="Keyword search in file text (hybrid BM25 + semantic, same as web UI)",
    )
    p.add_argument("query", help="Keyword/phrase")
    p.add_argument("--ext", help="File type filter")
    p.add_argument("--limit", type=int, default=25, help="Max results (server-capped at 50)")

    p = files_sub.add_parser("read", help="Read file content (windowed)")
    p.add_argument("file_id", type=int)
    p.add_argument("--start", type=int, default=None)
    p.add_argument("--end", type=int, default=None)
    p.add_argument("--window", type=int, default=5)
    p.add_argument("--outline", action="store_true", help="Section map only")
    p.add_argument("--all", action="store_true")

    p = files_sub.add_parser(
        "batch-read",
        help="Read multiple files in one round trip (designed for post-search drill-down)",
    )
    p.add_argument(
        "file_ids", nargs="*", type=int,
        help="File IDs to read from chunk 0 with default window",
    )
    p.add_argument(
        "--chunk", dest="chunks", action="append",
        help="Read chunks centered on a specific position. Format: file_id:chunk_index. Repeatable.",
    )
    p.add_argument("--window", type=int, default=3, help="Per-file window size (default 3)")
    p.add_argument("--outline", action="store_true", help="Outline (section map) instead of content")

    p = files_sub.add_parser("info", help="File metadata & versions")
    p.add_argument("file_id", type=int)

    p = files_sub.add_parser("stats", help="Project file statistics")

    p = files_sub.add_parser("aggregate", help="Group files by dimension")
    p.add_argument("group_by", choices=["file_type", "folder", "category", "search_status"])
    p.add_argument("--ext", help="File type filter")
    p.add_argument("--tags", help="Tags filter")
    p.add_argument("--folder", type=int)
    p.add_argument("--search", "-s", help="Title search (fuzzy by default — use --exact for tight matching)")
    p.add_argument("--exact", action="store_true", help="Tight substring matching instead of fuzzy default")
    p.add_argument("--top", type=int, default=50)

    p = files_sub.add_parser("recent", help="Recently uploaded files")
    p.add_argument("--ext", help="File type filter")
    p.add_argument("--folder", type=int)
    p.add_argument("--limit", type=int, default=20)

    p = files_sub.add_parser("download", help="Download a file to disk")
    p.add_argument("file_id", type=int, help="File ID to download")
    p.add_argument("--output", "-o", help="Output file path (default: original filename)")

    p = files_sub.add_parser("upload", help="Upload file(s) or directory contents")
    p.add_argument("paths", nargs="+", help="File path(s) or directory to upload")
    p.add_argument("--folder", type=int, help="Target folder ID")
    p.add_argument("--title", help="Override title (single file only; defaults to filename stem)")
    p.add_argument("--tags", help="Tags (comma-sep)")
    p.add_argument(
        "--concurrency",
        type=int,
        default=8,
        help="Parallel uploads for batch operations (default: 8, max: 32). "
             "Each worker uploads one file end-to-end via presign+PUT; "
             "metadata is then flushed in batches to /files/bulk-register/.",
    )
    p.add_argument(
        "--multipart-threshold-mb",
        type=int,
        default=50,
        help="Files larger than this (MB) use multipart upload "
             "(default: 50). Multipart parallelizes part uploads and is "
             "required for files larger than ~75MB on R2.",
    )
    p.add_argument(
        "--part-size-mb",
        type=int,
        default=16,
        help="Part size for multipart uploads (default: 16MB).",
    )

    p = files_sub.add_parser(
        "sync",
        help="Recursively mirror a local directory into a PCXA folder "
             "(idempotent; creates subfolders to match the tree)",
    )
    p.add_argument("input_dir", help="Local directory to mirror")
    p.add_argument("--folder", type=int, default=None,
                   help="Target PCXA folder ID (omit = project root)")
    p.add_argument("--manifest",
                   help="Path to a JSON manifest file used to track uploaded "
                        "files across runs. Skipped files are recorded so "
                        "re-runs don't hit the API. Created if missing.")
    p.add_argument("--include", action="append",
                   help="Glob to include (repeatable). Default: include all.")
    p.add_argument("--exclude", action="append",
                   help="Glob to exclude (repeatable).")
    p.add_argument("--include-hidden", action="store_true",
                   help="Include dotfiles and dot-directories (default: skip).")
    p.add_argument("--tags", help="Tags to apply to every uploaded file (comma-sep)")
    p.add_argument("--concurrency", type=int, default=8,
                   help="Starting parallel uploads (default: 8). The "
                        "auto-tuner adjusts up to --max-concurrency.")
    p.add_argument("--max-concurrency", dest="max_concurrency", type=int, default=32,
                   help="Hard cap on concurrent uploads (default: 32, max: 64). "
                        "The auto-tuner will never exceed this.")
    p.add_argument("--min-concurrency", dest="min_concurrency", type=int, default=1,
                   help="Floor for concurrent uploads (default: 1). The "
                        "auto-tuner will not drop below this even on errors.")
    p.add_argument("--no-auto-tune", dest="no_auto_tune", action="store_true",
                   help="Disable the AIMD auto-tuner. Concurrency stays "
                        "fixed at --concurrency for the whole run.")
    p.add_argument("--max-failures", dest="max_failures", type=int, default=100,
                   help="Abort the run if cumulative errors reach this count "
                        "(default: 100). Set 0 to disable the circuit breaker.")
    p.add_argument("--part-concurrency", dest="part_concurrency", type=int, default=4,
                   help="Parallel parts per multipart upload (default: 4, "
                        "max: 16). Decoupled from --concurrency to keep "
                        "memory bounded when many big files are in flight.")
    p.add_argument("--multipart-threshold-mb", type=int, default=50,
                   help="Files larger than this (MB) use multipart upload "
                        "(default: 50).")
    p.add_argument("--part-size-mb", type=int, default=16,
                   help="Multipart part size in MB (default: 16, min: 5). "
                        "Auto-bumped per file if it would exceed R2's "
                        "10000-part cap.")
    p.add_argument("--batch-size", dest="batch_size", type=int, default=200,
                   help="Files per /files/bulk-presign-upload/ call "
                        "(default: 200, server cap: 500). ~Nx fewer "
                        "presign roundtrips than the legacy per-file path.")
    p.add_argument("--no-bulk-presign", dest="no_bulk_presign",
                   action="store_true",
                   help="Disable the bulk-presign optimization and use "
                        "the legacy per-file /files/presign-upload/ path. "
                        "Sync auto-falls back on 404 anyway; this flag "
                        "lets you force the legacy path explicitly.")
    p.add_argument("--limit", type=int, default=0,
                   help="Stop after queueing N files (after manifest/name "
                        "filtering). Useful for graduated smoke tests. "
                        "0 = no limit (default).")
    p.add_argument("--trust-manifest", dest="trust_manifest",
                   action="store_true",
                   help="Skip the server-side existing-filename pre-flight "
                        "check and rely entirely on the manifest for dedup. "
                        "Much faster startup on resume runs against a "
                        "trusted manifest; risks creating duplicates if "
                        "files were added to the target folder via other "
                        "means.")
    p.add_argument("--error-log", dest="error_log", default=None,
                   help="Path to a JSON-Lines file. One line per per-file "
                        "or per-batch failure with phase (presign/put/"
                        "register), HTTP status, response-body excerpt, "
                        "and timing. Use for live diagnosis of low-"
                        "throughput / high-error runs — failures normally "
                        "only surface in the end-of-run summary, which is "
                        "useless mid-run.")
    p.add_argument("--stats-interval", dest="stats_interval", type=float,
                   default=0.0,
                   help="If >0, emit a JSON stats line to stderr every N "
                        "seconds: throughput, in-flight, errors, http "
                        "reuse rate, retries. Default 0 = off. 30 is a "
                        "reasonable starting point for long runs.")

    p = files_sub.add_parser("upload-version",
                             help="Upload a new version of an existing PCXA file")
    p.add_argument("file_id", type=int, help="Existing PCXA file id")
    p.add_argument("path", help="Local file path to upload as the new version")
    p.add_argument("--notes", help="Optional version_notes annotation")

    p = files_sub.add_parser("update", help="Update single file metadata")
    p.add_argument("file_id", type=int)
    p.add_argument("--title")
    p.add_argument("--description")
    p.add_argument("--category")
    p.add_argument("--tags", help="Tags (comma-sep, replaces all)")
    p.add_argument("--folder", type=int, help="Move to folder (0=root)")

    p = files_sub.add_parser("delete",
                             help=f"Tag files for deletion (adds '{DELETION_TAG}' tag; "
                                  "actual deletion handled by the platform)")
    p.add_argument("file_ids", nargs="+", type=int)
    p.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")

    p = files_sub.add_parser("restore",
                             help=f"Remove '{DELETION_TAG}' tag from files (undo deletion mark)")
    p.add_argument("file_ids", nargs="+", type=int)

    p = files_sub.add_parser("purge",
                             help="Hard-delete files (irreversible). Chunks via bulk_delete; "
                                  "use this instead of ad-hoc c.session.delete scripts.")
    p.add_argument("file_ids", nargs="*", type=int,
                   help="File ids. May also be supplied via --ids-file.")
    p.add_argument("--ids-file",
                   help="Read ids from file (whitespace/comma-separated). Use '-' for stdin.")
    p.add_argument("--chunk", type=int, default=500,
                   help="Chunk size for bulk_delete (default 500).")
    p.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")

    p = files_sub.add_parser(
        "bulk-patch",
        help="Apply a per-file metadata/tag plan from JSON (each row sets its own "
             "title/category/description/tags via files/bulk_patch/)")
    p.add_argument("--file", required=True,
                   help='JSON plan: a list of rows or {"changes": [...]}. Row: '
                        '{"file_id": N, "tags": [...], "tag_mode": "set|add|remove", '
                        '"title": ..., "category": ..., "description": ...}')

    # ── tags ──
    tags_p = sub.add_parser("tags", help="Tag management")
    tags_sub = tags_p.add_subparsers(dest="tags_command")
    tags_sub.add_parser("list", help="List tags with counts")

    p = tags_sub.add_parser("add", help="Add tags to files")
    p.add_argument("file_ids", nargs="+", type=int)
    p.add_argument("--tags", required=True, help="Tags (comma-sep)")

    p = tags_sub.add_parser("remove", help="Remove tags from files")
    p.add_argument("file_ids", nargs="+", type=int)
    p.add_argument("--tags", required=True)

    p = tags_sub.add_parser("set", help="Replace all tags on files")
    p.add_argument("file_ids", nargs="+", type=int)
    p.add_argument("--tags", required=True)

    p = tags_sub.add_parser(
        "bulk", help="Apply a per-file tag plan from JSON (different tags per file)")
    p.add_argument("--file", required=True,
                   help='JSON plan: a list of rows or {"changes": [...]}. Row: '
                        '{"file_id": N, "tags": [...], "tag_mode": "set|add|remove"}')

    # ── folders ──
    folders_p = sub.add_parser("folders", help="Folder management")
    folders_sub = folders_p.add_subparsers(dest="folders_command")

    p = folders_sub.add_parser("tree", help="Folder hierarchy")
    p.add_argument("--depth", type=int)

    p = folders_sub.add_parser("create", help="Create folder")
    p.add_argument("name")
    p.add_argument("--parent", type=int)
    p.add_argument("--description")

    p = folders_sub.add_parser("rename", help="Rename folder")
    p.add_argument("folder_id", type=int)
    p.add_argument("name")

    p = folders_sub.add_parser("move", help="Move folder")
    p.add_argument("folder_id", type=int)
    p.add_argument("--parent", type=int, default=None)

    p = folders_sub.add_parser("delete", help="Delete folder + contents")
    p.add_argument("folder_id", type=int)

    p = folders_sub.add_parser("contents", help="Show folder contents")
    p.add_argument("folder_id", type=int)
    p.add_argument("--timeout", type=int, default=180,
                   help="HTTP read timeout in seconds (default: 180)")

    p = folders_sub.add_parser(
        "subfolders",
        help="Lightweight [{id, name}] subfolder listing for path resolution "
             "(paginated; faster than `contents` on large folders)",
    )
    p.add_argument("folder_id", type=int)
    p.add_argument("--page-size", type=int, default=1000,
                   help="Results per page (default: 1000, max enforced server-side)")
    p.add_argument("--timeout", type=int, default=180,
                   help="HTTP read timeout in seconds (default: 180)")

    # ── move / categorize (bulk file ops) ──
    p = sub.add_parser("move", help="Move files to folder")
    p.add_argument("file_ids", nargs="+", type=int)
    p.add_argument("--folder", type=int, default=None)

    p = sub.add_parser("categorize", help="Set category on files")
    p.add_argument("file_ids", nargs="+", type=int)
    p.add_argument("--category", required=True)

    # ── activities ──
    act_p = sub.add_parser("activities", help="Activity management")
    act_sub = act_p.add_subparsers(dest="activities_command")

    p = act_sub.add_parser("list", help="List activities")
    p.add_argument("--status", help="not_started,in_progress,completed")
    p.add_argument("--priority", help="0-4 (comma-sep)")
    p.add_argument("--owner", help="Owner user ID")
    p.add_argument("--assignee", help="Assignee user ID")
    p.add_argument("--type", help="Activity type ID")
    p.add_argument("--parent", type=int)
    p.add_argument("--root-only", action="store_true")
    p.add_argument("--search", "-s",
                   help="Search title/description/wbs_code (fuzzy by default; exact matches rank first)")
    p.add_argument("--exact", action="store_true",
                   help="Tight substring matching (rejects typos). Default is fuzzy with exact-first ranking.")
    p.add_argument("--tags")
    p.add_argument("--tags-mode", dest="tags_mode", choices=["any", "all"], help="any=OR (default), all=AND")
    p.add_argument("--after", help="Updated after date (YYYY-MM-DD or relative: last_7_days, this_month, etc.)")
    p.add_argument("--before", help="Updated before date (YYYY-MM-DD or relative: last_30_days, last_quarter, etc.)")
    p.add_argument("--created-after", help="Created after date (YYYY-MM-DD or relative)")
    p.add_argument("--created-before", help="Created before date (YYYY-MM-DD or relative)")
    # See cmd_activities_list — None lets --search rank by similarity DESC.
    p.add_argument("--sort", default=None,
                   help="Sort field (default: -created_at; similarity when --search)")
    p.add_argument("--count-only", action="store_true")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--offset", type=int, default=0)

    p = act_sub.add_parser("get", help="Activity detail")
    p.add_argument("activity_id", type=int)

    p = act_sub.add_parser("create", help="Create activity")
    p.add_argument("--title", required=True)
    p.add_argument("--description")
    p.add_argument("--status", choices=["not_started", "in_progress", "completed"])
    p.add_argument("--priority", type=int, choices=[0, 1, 2, 3, 4])
    p.add_argument("--due-date")
    p.add_argument("--planned-start")
    p.add_argument("--planned-finish")
    p.add_argument("--owner", type=int)
    p.add_argument("--assignees", help="User IDs (comma-sep)")
    p.add_argument("--type", type=int, help="Activity type ID")
    p.add_argument("--parent", type=int)
    p.add_argument("--tags")
    p.add_argument("--wbs")
    p.add_argument("--custom-fields", dest="custom_fields",
                   help='Custom-field values JSON keyed by custom-field id '
                        '(e.g. \'{"3":"Acme Corp"}\'); custom-object-backed values are fuzzy-validated')
    p.add_argument("--no-fuzzy", dest="no_fuzzy", action="store_true",
                   help="Skip fuzzy validation of custom-object field values (write as-is)")

    p = act_sub.add_parser("update", help="Update activity")
    p.add_argument("activity_id", type=int)
    p.add_argument("--title")
    p.add_argument("--description")
    p.add_argument("--status", choices=["not_started", "in_progress", "completed"])
    p.add_argument("--priority", type=int, choices=[0, 1, 2, 3, 4])
    p.add_argument("--percent", type=int)
    p.add_argument("--due-date")
    p.add_argument("--planned-start")
    p.add_argument("--planned-finish")
    p.add_argument("--actual-start")
    p.add_argument("--actual-finish")
    p.add_argument("--owner", type=int)
    p.add_argument("--assignees")
    p.add_argument("--parent", type=int, help="0=root")
    p.add_argument("--tags")
    p.add_argument("--custom-fields", dest="custom_fields",
                   help='Custom-field values JSON keyed by custom-field id '
                        '(custom-object-backed values are fuzzy-validated)')
    p.add_argument("--no-fuzzy", dest="no_fuzzy", action="store_true",
                   help="Skip fuzzy validation of custom-object field values (write as-is)")

    p = act_sub.add_parser("delete", help="Delete activities")
    p.add_argument("activity_ids", nargs="+", type=int)

    p = act_sub.add_parser("bulk-update", help="Bulk update activities")
    p.add_argument("activity_ids", nargs="+", type=int)
    p.add_argument("--status")
    p.add_argument("--priority", type=int)
    p.add_argument("--owner", type=int)

    act_sub.add_parser("types", help="List activity types")

    # ── steps ──
    steps_p = sub.add_parser("steps", help="Step (subtask) management")
    steps_sub = steps_p.add_subparsers(dest="steps_command")

    p = steps_sub.add_parser("list", help="List steps")
    p.add_argument("activity_id", type=int)

    p = steps_sub.add_parser("create", help="Create step")
    p.add_argument("activity_id", type=int)
    p.add_argument("--name", required=True)
    p.add_argument("--description")
    p.add_argument("--weight", type=float)
    p.add_argument("--order", type=int)

    p = steps_sub.add_parser("update", help="Update step")
    p.add_argument("activity_id", type=int)
    p.add_argument("step_id", type=int)
    p.add_argument("--name")
    p.add_argument("--percent", type=int)
    p.add_argument("--weight", type=float)
    p.add_argument("--order", type=int)

    p = steps_sub.add_parser("delete", help="Delete step")
    p.add_argument("activity_id", type=int)
    p.add_argument("step_id", type=int)

    p = steps_sub.add_parser("from-template", help="Create steps from type template")
    p.add_argument("activity_id", type=int)

    # ── progress ──
    prog_p = sub.add_parser("progress", help="Progress tracking")
    prog_sub = prog_p.add_subparsers(dest="progress_command")

    p = prog_sub.add_parser("list", help="Progress history")
    p.add_argument("activity_id", type=int)
    p.add_argument("--source", choices=["automatic", "manual"])

    p = prog_sub.add_parser("add", help="Add manual progress")
    p.add_argument("activity_id", type=int)
    p.add_argument("--percent", type=int, required=True)
    p.add_argument("--notes")
    p.add_argument("--date", help="Effective date (ISO, for backdating)")

    p = prog_sub.add_parser("delete", help="Delete manual entry")
    p.add_argument("activity_id", type=int)
    p.add_argument("entry_id", type=int)

    # ── deps ──
    deps_p = sub.add_parser("deps", help="Dependencies (CPM)")
    deps_sub = deps_p.add_subparsers(dest="deps_command")

    p = deps_sub.add_parser("list", help="List dependencies")
    p.add_argument("--predecessor", type=int)
    p.add_argument("--successor", type=int)
    p.add_argument("--type", dest="dep_type", choices=["FS", "SS", "FF", "SF"])

    p = deps_sub.add_parser("create", help="Create dependency")
    p.add_argument("--predecessor", type=int, required=True)
    p.add_argument("--successor", type=int, required=True)
    p.add_argument("--type", dest="dep_type", choices=["FS", "SS", "FF", "SF"], default="FS")
    p.add_argument("--lag", type=float)
    p.add_argument("--notes")

    p = deps_sub.add_parser("delete", help="Delete dependency")
    p.add_argument("dep_id", type=int)

    # ── tag-filters (saved tag-query links on an activity) ──
    tf_p = sub.add_parser(
        "tag-filters",
        help="Attach saved tag queries (AND/OR evidence sets) to an activity")
    tf_sub = tf_p.add_subparsers(dest="tag_filters_command")

    p = tf_sub.add_parser("list", help="List an activity's tag-filter links")
    p.add_argument("activity_id", type=int)

    p = tf_sub.add_parser(
        "add", help="Attach a tag-filter link (e.g. pay_app AND yates) to an activity")
    p.add_argument("activity_id", type=int)
    p.add_argument("--tags", required=True, help=f"Tag names (comma-sep, max {MAX_TAG_FILTER_TAGS})")
    p.add_argument("--mode", choices=list(TAG_FILTER_MODES), default=TAG_FILTER_MODES[0],
                   help="any=OR match any tag (default); all=AND require every tag")
    p.add_argument("--label", help="Optional display label (defaults to a join of the tags)")

    p = tf_sub.add_parser("delete", help="Remove a tag-filter link from an activity")
    p.add_argument("activity_id", type=int)
    p.add_argument("link_id", type=int)

    # ── gantt / tree ──
    p = sub.add_parser("gantt", help="Gantt view")
    p.add_argument("--status")

    p = sub.add_parser("tree", help="Activity WBS tree")
    p.add_argument("--max-depth", type=int)
    p.add_argument("--status")

    # ── forms ──
    forms_p = sub.add_parser("forms", help="Form template management")
    forms_sub = forms_p.add_subparsers(dest="forms_command")

    p = forms_sub.add_parser("list", help="List form templates")
    p.add_argument("--category", help="Filter by category")
    p.add_argument("--scope", choices=["company", "project"])
    p.add_argument("--search", "-s")
    p.add_argument("--sort", default="-created_at")
    p.add_argument("--count-only", action="store_true")
    p.add_argument("--limit", type=int, default=25)
    p.add_argument("--offset", type=int, default=0)

    p = forms_sub.add_parser("get", help="Form detail with fields")
    p.add_argument("form_id", type=int)

    p = forms_sub.add_parser("create", help="Create form template")
    p.add_argument("--name", required=True)
    p.add_argument("--description")
    p.add_argument("--scope", choices=["company", "project"], default="project")
    p.add_argument("--category", help="Grouping label (e.g. Safety, Quality)")
    p.add_argument("--form-type", dest="form_type", help="Type identifier (e.g. safety_incident)")
    p.add_argument("--code-prefix", dest="code_prefix", help="Submission code prefix (e.g. RFI, SI)")
    p.add_argument("--code-scope", dest="code_scope", choices=["project", "company"])
    p.add_argument("--code-separator", dest="code_separator", help="Separator (default: -)")
    p.add_argument("--code-padding", dest="code_padding", type=int, help="Zero-padding (3=001)")
    p.add_argument("--private-default", dest="private_default", action="store_true")
    p.add_argument("--reviewers", help="Default reviewer user IDs (comma-sep)")

    p = forms_sub.add_parser("update", help="Update form template")
    p.add_argument("form_id", type=int)
    p.add_argument("--name")
    p.add_argument("--description")
    p.add_argument("--category")
    p.add_argument("--form-type", dest="form_type")
    p.add_argument("--code-prefix", dest="code_prefix")
    p.add_argument("--code-scope", dest="code_scope", choices=["project", "company"])
    p.add_argument("--code-separator", dest="code_separator")
    p.add_argument("--code-padding", dest="code_padding", type=int)
    p.add_argument("--private-default", dest="private_default", type=lambda x: x.lower() in ("true", "1", "yes"), metavar="BOOL")
    p.add_argument("--reviewers", help="Default reviewer IDs (comma-sep, empty=clear)")

    p = forms_sub.add_parser("delete", help="Delete form template")
    p.add_argument("form_id", type=int)

    # ── fields ──
    fields_p = sub.add_parser("fields", help="Form field management")
    fields_sub = fields_p.add_subparsers(dest="fields_command")

    p = fields_sub.add_parser("list", help="List fields for a form")
    p.add_argument("form_id", type=int)
    p.add_argument("--limit", type=int, default=25,
                   help="Page size (max 1000). Default 25.")
    p.add_argument("--offset", type=int, default=0,
                   help="Skip this many fields (paged by --limit), matching `files list`.")
    p.add_argument("--all", action="store_true",
                   help="Fetch every field across all pages (overrides --limit/--offset).")

    p = fields_sub.add_parser("create", help="Create field on form")
    p.add_argument("form_id", type=int)
    p.add_argument("--label", required=True)
    p.add_argument("--type", dest="field_type", required=True, help="text, textarea, number, date, datetime, checkbox, select, radio, choice, table, photo, file, location")
    p.add_argument("--required", action="store_true")
    p.add_argument("--order", type=int)
    p.add_argument("--placeholder")
    p.add_argument("--help-text", dest="help_text")
    p.add_argument("--options", help='Simple choice list for select/radio/choice (e.g. \'["Low","Med","High"]\')')
    p.add_argument("--choice-id", dest="choice_id", type=int,
                   help="Bind field to a custom object (field-choice) id; use with --type choice")
    p.add_argument("--table-schema", dest="table_schema",
                   help='Column definitions for --type table (JSON array, e.g. '
                        '\'[{"name":"item","field_type":"text","label":"Item"},{"name":"qty","field_type":"number","label":"Qty"}]\')')
    p.add_argument("--min-rows", dest="min_rows", type=int, help="Minimum rows for a table field")
    p.add_argument("--max-rows", dest="max_rows", type=int, help="Maximum rows for a table field")
    p.add_argument("--column-span", dest="column_span", type=int)
    p.add_argument("--section", type=int, help="Section ID")

    p = fields_sub.add_parser("update", help="Update form field")
    p.add_argument("form_id", type=int)
    p.add_argument("field_id", type=int)
    p.add_argument("--label")
    p.add_argument("--type", dest="field_type")
    p.add_argument("--required", type=lambda x: x.lower() in ("true", "1", "yes"), metavar="BOOL")
    p.add_argument("--order", type=int)
    p.add_argument("--placeholder")
    p.add_argument("--help-text", dest="help_text")
    p.add_argument("--options", help="Simple choice list JSON for select/radio/choice")
    p.add_argument("--choice-id", dest="choice_id", type=int,
                   help="Bind field to a custom object (field-choice) id")
    p.add_argument("--table-schema", dest="table_schema",
                   help="Column definitions for a table field (JSON array)")
    p.add_argument("--min-rows", dest="min_rows", type=int, help="Minimum rows for a table field")
    p.add_argument("--max-rows", dest="max_rows", type=int, help="Maximum rows for a table field")
    p.add_argument("--column-span", dest="column_span", type=int)

    p = fields_sub.add_parser("delete", help="Delete form field")
    p.add_argument("form_id", type=int)
    p.add_argument("field_id", type=int)

    # ── submissions ──
    subs_p = sub.add_parser("submissions", help="Form submission management")
    subs_sub = subs_p.add_subparsers(dest="submissions_command")

    p = subs_sub.add_parser("list", help="List submissions")
    p.add_argument("--form", type=int, help="Filter by form ID")
    p.add_argument("--status", help="draft, submitted, closed")
    p.add_argument("--owner", type=int)
    p.add_argument("--search", "-s")
    p.add_argument("--sort", default="-submitted_at")
    p.add_argument("--count-only", action="store_true")
    p.add_argument("--limit", type=int, default=25)
    p.add_argument("--offset", type=int, default=0)

    p = subs_sub.add_parser("get", help="Submission detail with values")
    p.add_argument("form_id", type=int)
    p.add_argument("submission_id", type=int)

    p = subs_sub.add_parser("create", help="Create submission")
    p.add_argument("form_id", type=int)
    p.add_argument("--code", required=True, help="Submission code (e.g. RFI-001)")
    p.add_argument("--values", help='JSON field values (e.g. \'{"1":"text","2":"2026-01-01"}\')')
    p.add_argument("--owner", type=int)
    p.add_argument("--assignees", help="User IDs (comma-sep)")
    p.add_argument("--distribution", help="Distribution list user IDs (comma-sep)")
    p.add_argument("--private", action="store_true")
    p.add_argument("--tags")
    p.add_argument("--location-name", dest="location_name")
    p.add_argument("--no-fuzzy", dest="no_fuzzy", action="store_true",
                   help="Skip fuzzy validation of custom-object field values (submit as-is)")

    p = subs_sub.add_parser("update", help="Update submission")
    p.add_argument("form_id", type=int)
    p.add_argument("submission_id", type=int)
    p.add_argument("--code")
    p.add_argument("--values", help="JSON field values. NOTE: by default this REPLACES the "
                                    "entire values dict; pass --merge to update only the keys given")
    p.add_argument("--merge", "--patch", dest="merge", action="store_true",
                   help="Merge --values into the submission's existing field values "
                        "(only the keys you pass change) instead of replacing the whole dict")
    p.add_argument("--owner", type=int, help="Owner user ID (0=clear)")
    p.add_argument("--assignees", help="User IDs (comma-sep, empty=clear)")
    p.add_argument("--distribution", help="Distribution list IDs (comma-sep, empty=clear)")
    p.add_argument("--private", type=lambda x: x.lower() in ("true", "1", "yes"), metavar="BOOL")
    p.add_argument("--tags", help="Tags (comma-sep, empty=clear)")
    p.add_argument("--location-name", dest="location_name")
    p.add_argument("--no-fuzzy", dest="no_fuzzy", action="store_true",
                   help="Skip fuzzy validation of custom-object field values (submit as-is)")

    p = subs_sub.add_parser("delete", help="Delete submission")
    p.add_argument("form_id", type=int)
    p.add_argument("submission_id", type=int)

    # ── custom-objects (FieldChoice + FieldChoiceOption) ──
    co_p = sub.add_parser(
        "custom-objects",
        help="Custom object management (field-choices) + fuzzy option lookup",
    )
    co_sub = co_p.add_subparsers(dest="custom_objects_command")

    def _add_scope(p):
        p.add_argument("--scope", choices=["project", "company"], default="project",
                       help="Object scope (default: project)")

    p = co_sub.add_parser("list", help="List custom objects")
    p.add_argument("--search", "-s")
    p.add_argument("--sort", default="-created_at")
    p.add_argument("--limit", type=int, default=25)
    p.add_argument("--offset", type=int, default=0)
    _add_scope(p)

    p = co_sub.add_parser("get", help="Custom object detail (schema + options)")
    p.add_argument("object_id", type=int, help="Custom object (field-choice) id")
    _add_scope(p)

    p = co_sub.add_parser("create", help="Create custom object")
    p.add_argument("--name", required=True)
    p.add_argument("--description")
    p.add_argument("--schema",
                   help='property_schema JSON: a list of column defs '
                        '(e.g. \'[{"name":"code","type":"text"}]\')')
    p.add_argument("--extensible", type=lambda x: x.lower() in ("true", "1", "yes"), metavar="BOOL",
                   help="Allow projects to extend this company object")
    _add_scope(p)

    p = co_sub.add_parser("update", help="Update custom object")
    p.add_argument("object_id", type=int, help="Custom object (field-choice) id")
    p.add_argument("--name")
    p.add_argument("--description")
    p.add_argument("--schema", help="property_schema JSON list (empty string clears)")
    p.add_argument("--extensible", type=lambda x: x.lower() in ("true", "1", "yes"), metavar="BOOL")
    _add_scope(p)

    p = co_sub.add_parser("delete", help="Delete custom object")
    p.add_argument("object_id", type=int, help="Custom object (field-choice) id")
    _add_scope(p)

    p = co_sub.add_parser("extend", help="Extend a company custom object into the current project")
    p.add_argument("object_id", type=int, help="Custom object (field-choice) id")

    p = co_sub.add_parser("resolve",
                          help="Fuzzy-match a value to an option ('No match for X. Did you mean Y?')")
    p.add_argument("object_id", type=int, help="Custom object (field-choice) id")
    p.add_argument("query", help="Value to fuzzy-match against the object's options")
    _add_scope(p)

    # nested third level: `custom-objects options <subcommand>`
    opts_p = co_sub.add_parser("options", help="Manage options (rows) of a custom object")
    opts_sub = opts_p.add_subparsers(dest="co_options_command")

    p = opts_sub.add_parser("list", help="List options of a custom object")
    p.add_argument("object_id", type=int, help="Custom object (field-choice) id")
    p.add_argument("--search", "-s")
    p.add_argument("--active", type=lambda x: x.lower() in ("true", "1", "yes"), metavar="BOOL")
    _add_scope(p)

    p = opts_sub.add_parser("get", help="Option detail")
    p.add_argument("object_id", type=int, help="Custom object (field-choice) id")
    p.add_argument("option_id", type=int, help="Option (row) id")
    _add_scope(p)

    p = opts_sub.add_parser("create", help="Create an option (row)")
    p.add_argument("object_id", type=int, help="Custom object (field-choice) id")
    p.add_argument("--label", required=True)
    p.add_argument("--description")
    p.add_argument("--order", type=int)
    p.add_argument("--properties", help='Property values JSON (e.g. \'{"code":"A1"}\')')
    p.add_argument("--active", type=lambda x: x.lower() in ("true", "1", "yes"), metavar="BOOL")
    _add_scope(p)

    p = opts_sub.add_parser("update", help="Update an option (row)")
    p.add_argument("object_id", type=int, help="Custom object (field-choice) id")
    p.add_argument("option_id", type=int, help="Option (row) id")
    p.add_argument("--label")
    p.add_argument("--description")
    p.add_argument("--order", type=int)
    p.add_argument("--properties", help="Property values JSON")
    p.add_argument("--active", type=lambda x: x.lower() in ("true", "1", "yes"), metavar="BOOL")
    _add_scope(p)

    p = opts_sub.add_parser("delete", help="Delete an option (row)")
    p.add_argument("object_id", type=int, help="Custom object (field-choice) id")
    p.add_argument("option_id", type=int, help="Option (row) id")
    _add_scope(p)

    p = opts_sub.add_parser("bulk-create", help="Bulk-create options from a JSON file")
    p.add_argument("object_id", type=int, help="Custom object (field-choice) id")
    p.add_argument("--file", required=True, help="JSON list of option objects (or {\"options\":[...]})")
    _add_scope(p)

    p = opts_sub.add_parser("reorder", help="Reorder options (comma-sep option ids, in order)")
    p.add_argument("object_id", type=int, help="Custom object (field-choice) id")
    p.add_argument("--order", required=True, help="Option ids in desired order, comma-separated")
    _add_scope(p)

    # ── resources ──
    res_p = sub.add_parser("resources", help="Resource management")
    res_sub = res_p.add_subparsers(dest="resources_command")

    p = res_sub.add_parser("list", help="List resources")
    p.add_argument("--type", dest="resource_type", choices=["personnel", "equipment", "consumable", "subcontractor"])
    p.add_argument("--active", type=lambda x: x.lower() in ("true", "1", "yes"), metavar="BOOL")
    p.add_argument("--user", type=int, help="Linked user ID")
    p.add_argument("--name", help="Name filter (substring)")
    p.add_argument("--search", "-s")
    p.add_argument("--sort", default="-created_at")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--offset", type=int, default=0)

    p = res_sub.add_parser("get", help="Resource detail with rates")
    p.add_argument("resource_id", type=int)

    p = res_sub.add_parser("create", help="Create resource")
    p.add_argument("--name", required=True)
    p.add_argument("--type", dest="resource_type", required=True, choices=["personnel", "equipment", "consumable", "subcontractor"])
    p.add_argument("--description")
    p.add_argument("--user", type=int, help="Link to user ID (personnel only)")
    p.add_argument("--unit", choices=["hours", "days", "each", "lump_sum"], default="hours")
    p.add_argument("--capacity", type=float, help="Default capacity per day (default: 8)")

    p = res_sub.add_parser("update", help="Update resource")
    p.add_argument("resource_id", type=int)
    p.add_argument("--name")
    p.add_argument("--type", dest="resource_type", choices=["personnel", "equipment", "consumable", "subcontractor"])
    p.add_argument("--description")
    p.add_argument("--user", type=int, help="User ID (0=unlink)")
    p.add_argument("--unit", choices=["hours", "days", "each", "lump_sum"])
    p.add_argument("--capacity", type=float)
    p.add_argument("--active", type=lambda x: x.lower() in ("true", "1", "yes"), metavar="BOOL")

    p = res_sub.add_parser("delete", help="Delete resource")
    p.add_argument("resource_id", type=int)

    # ── rates ──
    rates_p = sub.add_parser("rates", help="Resource rate management")
    rates_sub = rates_p.add_subparsers(dest="rates_command")

    p = rates_sub.add_parser("list", help="List rates for a resource")
    p.add_argument("resource_id", type=int)

    p = rates_sub.add_parser("create", help="Create rate for a resource")
    p.add_argument("resource_id", type=int)
    p.add_argument("--effective-date", dest="effective_date", required=True, help="YYYY-MM-DD")
    p.add_argument("--standard-rate", dest="standard_rate", type=float, required=True)
    p.add_argument("--cost-rate", dest="cost_rate", type=float, required=True)
    p.add_argument("--bill-rate", dest="bill_rate", type=float, required=True)
    p.add_argument("--overtime-rate", dest="overtime_rate", type=float)
    p.add_argument("--currency", default="USD", help="3-letter code (default: USD)")
    p.add_argument("--notes")

    # ── assignments ──
    assign_p = sub.add_parser("assignments", help="Resource assignments on activities")
    assign_sub = assign_p.add_subparsers(dest="assignments_command")

    p = assign_sub.add_parser("list", help="List assignments for an activity")
    p.add_argument("activity_id", type=int)

    p = assign_sub.add_parser("create", help="Create assignment")
    p.add_argument("activity_id", type=int)
    p.add_argument("--resource", dest="resource_id", type=int, required=True)
    p.add_argument("--planned-units", dest="planned_units", type=float)
    p.add_argument("--planned-per-day", dest="planned_per_day", type=float)
    p.add_argument("--curve", choices=["uniform", "front_loaded", "back_loaded", "bell"])
    p.add_argument("--driving", action="store_true", help="Resource drives activity duration")
    p.add_argument("--role", help="Role label (e.g. 'Lead Engineer')")
    p.add_argument("--start", help="Assignment start (YYYY-MM-DD)")
    p.add_argument("--end", help="Assignment end (YYYY-MM-DD)")

    p = assign_sub.add_parser("update", help="Update assignment")
    p.add_argument("activity_id", type=int)
    p.add_argument("assignment_id", type=int)
    p.add_argument("--planned-units", dest="planned_units", type=float)
    p.add_argument("--planned-per-day", dest="planned_per_day", type=float)
    p.add_argument("--remaining", type=float)
    p.add_argument("--at-completion", dest="at_completion", type=float)
    p.add_argument("--curve", choices=["uniform", "front_loaded", "back_loaded", "bell"])
    p.add_argument("--driving", type=lambda x: x.lower() in ("true", "1", "yes"), metavar="BOOL")
    p.add_argument("--role")
    p.add_argument("--start", help="YYYY-MM-DD")
    p.add_argument("--end", help="YYYY-MM-DD")

    p = assign_sub.add_parser("delete", help="Delete assignment")
    p.add_argument("activity_id", type=int)
    p.add_argument("assignment_id", type=int)

    # ── cost-codes ──
    cc_p = sub.add_parser("cost-codes", help="Cost code management (company-wide)")
    cc_sub = cc_p.add_subparsers(dest="costcodes_command")

    p = cc_sub.add_parser("list", help="List cost codes")
    p.add_argument("--code", help="Code filter (substring)")
    p.add_argument("--name", help="Name filter (substring)")
    p.add_argument("--active", type=lambda x: x.lower() in ("true", "1", "yes"), metavar="BOOL")
    p.add_argument("--parent", type=int)
    p.add_argument("--root-only", action="store_true")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--offset", type=int, default=0)

    p = cc_sub.add_parser("get", help="Cost code detail")
    p.add_argument("costcode_id", type=int)

    p = cc_sub.add_parser("create", help="Create cost code")
    p.add_argument("--code", required=True, help="Cost code (e.g. 03.300)")
    p.add_argument("--name", required=True)
    p.add_argument("--description")
    p.add_argument("--parent", type=int)
    p.add_argument("--sort-order", dest="sort_order", type=int)

    p = cc_sub.add_parser("update", help="Update cost code")
    p.add_argument("costcode_id", type=int)
    p.add_argument("--code")
    p.add_argument("--name")
    p.add_argument("--description")
    p.add_argument("--parent", type=int, help="Parent ID (0=root)")
    p.add_argument("--sort-order", dest="sort_order", type=int)
    p.add_argument("--active", type=lambda x: x.lower() in ("true", "1", "yes"), metavar="BOOL")

    p = cc_sub.add_parser("delete", help="Delete cost code")
    p.add_argument("costcode_id", type=int)

    # ── budgets ──
    bud_p = sub.add_parser("budgets", help="Cost code budgets")
    bud_sub = bud_p.add_subparsers(dest="budgets_command")

    p = bud_sub.add_parser("list", help="List budgets")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--offset", type=int, default=0)

    p = bud_sub.add_parser("create", help="Create budget")
    p.add_argument("--cost-code", dest="cost_code", type=int, required=True)
    p.add_argument("--amount", type=float, help="Budgeted amount")
    p.add_argument("--units", type=float, help="Budgeted units")

    p = bud_sub.add_parser("update", help="Update budget")
    p.add_argument("budget_id", type=int)
    p.add_argument("--amount", type=float)
    p.add_argument("--units", type=float)

    p = bud_sub.add_parser("delete", help="Delete budget")
    p.add_argument("budget_id", type=int)

    # ── timesheets ──
    ts_p = sub.add_parser("timesheets", help="Timesheet management")
    ts_sub = ts_p.add_subparsers(dest="timesheets_command")

    p = ts_sub.add_parser("list", help="List timesheets")
    p.add_argument("--status", choices=["draft", "submitted", "approved", "rejected"])
    p.add_argument("--resource", type=int)
    p.add_argument("--period-type", dest="period_type", choices=["weekly", "biweekly", "monthly"])
    p.add_argument("--after", help="Period start >= date")
    p.add_argument("--before", help="Period start <= date")
    p.add_argument("--sort", default="-period_start")
    p.add_argument("--limit", type=int, default=25)
    p.add_argument("--offset", type=int, default=0)

    p = ts_sub.add_parser("get", help="Timesheet detail with entries")
    p.add_argument("timesheet_id", type=int)

    p = ts_sub.add_parser("create", help="Create timesheet")
    p.add_argument("--resource", type=int, required=True, help="Resource ID")
    p.add_argument("--period-start", dest="period_start", required=True, help="YYYY-MM-DD")
    p.add_argument("--period-end", dest="period_end", required=True, help="YYYY-MM-DD")
    p.add_argument("--period-type", dest="period_type", choices=["weekly", "biweekly", "monthly"])

    p = ts_sub.add_parser("update", help="Update timesheet")
    p.add_argument("timesheet_id", type=int)
    p.add_argument("--period-start", dest="period_start")
    p.add_argument("--period-end", dest="period_end")
    p.add_argument("--period-type", dest="period_type", choices=["weekly", "biweekly", "monthly"])

    p = ts_sub.add_parser("delete", help="Delete timesheet")
    p.add_argument("timesheet_id", type=int)

    p = ts_sub.add_parser("submit", help="Submit for approval")
    p.add_argument("timesheet_id", type=int)

    p = ts_sub.add_parser("approve", help="Approve timesheet")
    p.add_argument("timesheet_id", type=int)

    p = ts_sub.add_parser("reject", help="Reject timesheet")
    p.add_argument("timesheet_id", type=int)
    p.add_argument("--reason", help="Rejection reason")

    p = ts_sub.add_parser("reopen", help="Reopen rejected timesheet")
    p.add_argument("timesheet_id", type=int)

    # ── entries (time entries) ──
    ent_p = sub.add_parser("entries", help="Time entry management")
    ent_sub = ent_p.add_subparsers(dest="entries_command")

    p = ent_sub.add_parser("list", help="List time entries")
    p.add_argument("timesheet_id", type=int)
    p.add_argument("--date", help="Exact date filter")
    p.add_argument("--after", help="Date >= filter")
    p.add_argument("--before", help="Date <= filter")
    p.add_argument("--activity", type=int)
    p.add_argument("--type", dest="entry_type", choices=["regular", "overtime", "double_time"])

    p = ent_sub.add_parser("create", help="Create time entry")
    p.add_argument("timesheet_id", type=int)
    p.add_argument("--activity", type=int, required=True)
    p.add_argument("--date", required=True, help="YYYY-MM-DD")
    p.add_argument("--hours", type=float, required=True)
    p.add_argument("--type", dest="entry_type", choices=["regular", "overtime", "double_time"])
    p.add_argument("--cost-code", dest="cost_code", type=int)
    p.add_argument("--description")

    p = ent_sub.add_parser("update", help="Update time entry")
    p.add_argument("timesheet_id", type=int)
    p.add_argument("entry_id", type=int)
    p.add_argument("--date")
    p.add_argument("--hours", type=float)
    p.add_argument("--type", dest="entry_type", choices=["regular", "overtime", "double_time"])
    p.add_argument("--cost-code", dest="cost_code", type=int, help="Cost code ID (0=clear)")
    p.add_argument("--description")

    p = ent_sub.add_parser("delete", help="Delete time entry")
    p.add_argument("timesheet_id", type=int)
    p.add_argument("entry_id", type=int)

    # ── cost-entries ──
    ce_p = sub.add_parser("cost-entries", help="Cost entry management")
    ce_sub = ce_p.add_subparsers(dest="costentries_command")

    p = ce_sub.add_parser("list", help="List cost entries")
    p.add_argument("timesheet_id", type=int)
    p.add_argument("--date")
    p.add_argument("--activity", type=int)
    p.add_argument("--resource", type=int)

    p = ce_sub.add_parser("create", help="Create cost entry")
    p.add_argument("timesheet_id", type=int)
    p.add_argument("--resource", type=int, required=True)
    p.add_argument("--activity", type=int, required=True)
    p.add_argument("--date", required=True, help="YYYY-MM-DD")
    p.add_argument("--quantity", type=float, required=True)
    p.add_argument("--unit-cost", dest="unit_cost", type=float, required=True)
    p.add_argument("--cost-code", dest="cost_code", type=int)
    p.add_argument("--description")

    p = ce_sub.add_parser("update", help="Update cost entry")
    p.add_argument("timesheet_id", type=int)
    p.add_argument("entry_id", type=int)
    p.add_argument("--date")
    p.add_argument("--quantity", type=float)
    p.add_argument("--unit-cost", dest="unit_cost", type=float)
    p.add_argument("--cost-code", dest="cost_code", type=int, help="0=clear")
    p.add_argument("--description")

    p = ce_sub.add_parser("delete", help="Delete cost entry")
    p.add_argument("timesheet_id", type=int)
    p.add_argument("entry_id", type=int)

    # ── locks ──
    locks_p = sub.add_parser("locks", help="Time period locks")
    locks_sub = locks_p.add_subparsers(dest="locks_command")

    locks_sub.add_parser("list", help="List time period locks")

    p = locks_sub.add_parser("create", help="Create lock")
    p.add_argument("--period-start", dest="period_start", required=True, help="YYYY-MM-DD")
    p.add_argument("--period-end", dest="period_end", required=True, help="YYYY-MM-DD")
    p.add_argument("--reason")

    p = locks_sub.add_parser("delete", help="Delete lock")
    p.add_argument("lock_id", type=int)

    # ── comments ──
    comments_p = sub.add_parser("comments", help="Activity comment management")
    comments_sub = comments_p.add_subparsers(dest="comments_command")

    p = comments_sub.add_parser("list", help="List comments on an activity")
    p.add_argument("activity_id", type=int, help="Activity ID")

    p = comments_sub.add_parser("add", help="Add a comment to an activity")
    p.add_argument("activity_id", type=int, help="Activity ID")
    p.add_argument("--content", required=True, help="Comment text")

    p = comments_sub.add_parser("delete", help="Delete a comment")
    p.add_argument("activity_id", type=int, help="Activity ID")
    p.add_argument("comment_id", type=int, help="Comment ID")

    p = comments_sub.add_parser("bulk", help="Bulk add comments from JSON file")
    p.add_argument("activity_id", type=int, help="Activity ID")
    p.add_argument("--file", required=True, help="JSON file with list of {content} objects")

    # ── links ──
    links_p = sub.add_parser("links", help="Entity link management")
    links_sub = links_p.add_subparsers(dest="links_command")

    p = links_sub.add_parser("list", help="List links for an object")
    p.add_argument("--source", help="Source object ref (type:id, e.g. file:170106)")
    p.add_argument("--target", help="Target object ref (type:id, e.g. activity:3710)")
    p.add_argument("--project-id", type=int, help="Project ID (default: current)")

    p = links_sub.add_parser("create", help="Create a link between two objects")
    p.add_argument("--source", required=True, help="Source object ref (type:id)")
    p.add_argument("--target", required=True, help="Target object ref (type:id)")
    p.add_argument("--type", dest="link_type", help="Link type (used as description, e.g. attachment, deliverable)")
    p.add_argument("--description", help="Description (overrides --type)")

    p = links_sub.add_parser("delete", help="Delete a link")
    p.add_argument("link_id", type=int, help="Link ID")

    p = links_sub.add_parser("bulk", help="Bulk create links from JSON file")
    p.add_argument("--file", required=True, help="JSON file with list of link objects")

    # ── chat ──
    chat_p = sub.add_parser("chat", help="AI chat (project-scoped conversation)")
    chat_sub = chat_p.add_subparsers(dest="chat_command")

    p = chat_sub.add_parser("send", help="Send a message and wait for the AI response")
    p.add_argument("message", help="User message content")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--conversation", type=int, help="Use existing conversation ID (default: current)")
    g.add_argument("--new", action="store_true", help="Start a fresh conversation for this message")
    p.add_argument("--title", help="Title for new conversation (with --new)")
    p.add_argument("--model", help="Override AI model id (see `pcxa chat models`)")
    p.add_argument("--research", action="store_true", help="Enable research_mode (file search tools)")
    p.add_argument("--page-url", help="Optional context URL passed to the agent")
    p.add_argument("--no-wait", action="store_true", help="Return immediately with agent_task_id; don't poll")
    p.add_argument("--timeout", type=int, default=180, help="Polling timeout in seconds (default: 180)")

    p = chat_sub.add_parser("ls", help="List conversations")
    p.add_argument("--search", help="Filter by title or message content")
    p.add_argument("--limit", type=int, default=20)

    p = chat_sub.add_parser("get", help="Show conversation detail (default: current)")
    p.add_argument("conversation_id", type=int, nargs="?", help="Conversation ID (default: current)")
    p.add_argument("--show-tools", action="store_true", help="Include tool calls in output")

    p = chat_sub.add_parser("new", help="Create a new conversation")
    p.add_argument("--title", help="Optional title")

    p = chat_sub.add_parser("delete", help="Archive (soft-delete) a conversation")
    p.add_argument("conversation_id", type=int)

    chat_sub.add_parser("models", help="List available AI models")

    _propagate_format_to_subparsers(parser)
    _propagate_dry_run_to_subparsers(parser)
    return parser


def _propagate_format_to_subparsers(parser):
    """Add -f/--format to every subparser so it works after the subcommand too.

    Argparse only parses --format on the parser scope it was registered on. The
    top-level `pcxa --format table activities list` works, but the more natural
    `pcxa activities list -f table` raises "unrecognized arguments". Rather than
    repeat add_argument on 100+ subparsers, walk the tree and inject once.

    default=SUPPRESS so an unset value on the subparser doesn't overwrite the
    top-level --format that was parsed first.
    """
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for sub in action.choices.values():
                already = any(
                    a.dest == "format" and a.option_strings
                    for a in sub._actions
                )
                if not already:
                    sub.add_argument(
                        "-f", "--format",
                        choices=["json", "table"],
                        default=argparse.SUPPRESS,
                        help="Output format (json|table)",
                    )
                _propagate_format_to_subparsers(sub)


def _propagate_dry_run_to_subparsers(parser):
    """Same trick as --format: let --dry-run sit after the subcommand."""
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for sub in action.choices.values():
                already = any(
                    a.dest == "dry_run" and a.option_strings
                    for a in sub._actions
                )
                if not already:
                    sub.add_argument(
                        "--dry-run",
                        action="store_true",
                        default=argparse.SUPPRESS,
                        help="Preview without changes",
                    )
                _propagate_dry_run_to_subparsers(sub)


__all__ = ["build_parser"]
