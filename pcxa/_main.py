"""CLI entrypoint and command dispatch.

`main()` builds the parser, parses args, and dispatches via HANDLERS /
SUB_HANDLERS to the appropriate `cmd_*` function. Auth-free commands run
without an APIClient; all others get a configured client with auto-resolved
company/project IDs.
"""

import json
import sys

from pcxa._api import APIClient
from pcxa._config import get_profile, load_config
from pcxa._http import requests
from pcxa._parser import build_parser
from pcxa._resolve import resolve_ids
from pcxa._update import _check_for_update, _print_update_notice

from pcxa.commands.auth import (
    cmd_login,
    cmd_projects,
    cmd_set_project,
    cmd_setup,
    cmd_whoami,
)
from pcxa.commands.project import (
    cmd_project_get,
    cmd_project_members,
    cmd_project_update,
)
from pcxa.commands.files import (
    cmd_files_aggregate,
    cmd_files_batch_read,
    cmd_files_content,
    cmd_files_download,
    cmd_files_info,
    cmd_files_list,
    cmd_files_read,
    cmd_files_recent,
    cmd_files_search,
    cmd_files_stats,
    cmd_files_upload,
    cmd_files_upload_version,
)
from pcxa.commands.tags_folders import (
    cmd_categorize,
    cmd_file_update,
    cmd_files_delete,
    cmd_files_restore,
    cmd_folders_contents,
    cmd_folders_create,
    cmd_folders_delete,
    cmd_folders_move,
    cmd_folders_rename,
    cmd_folders_subfolders,
    cmd_folders_tree,
    cmd_move,
    cmd_tags_add,
    cmd_tags_list,
    cmd_tags_remove,
    cmd_tags_set,
)
from pcxa.commands.activities import (
    cmd_activities_bulk_update,
    cmd_activities_create,
    cmd_activities_delete,
    cmd_activities_get,
    cmd_activities_list,
    cmd_activities_types,
    cmd_activities_update,
    cmd_comments_add,
    cmd_comments_bulk,
    cmd_comments_delete,
    cmd_comments_list,
    cmd_deps_create,
    cmd_deps_delete,
    cmd_deps_list,
    cmd_gantt,
    cmd_progress_add,
    cmd_progress_delete,
    cmd_progress_list,
    cmd_steps_create,
    cmd_steps_delete,
    cmd_steps_from_template,
    cmd_steps_list,
    cmd_steps_update,
    cmd_tree,
)
from pcxa.commands.chat import (
    cmd_chat_delete,
    cmd_chat_get,
    cmd_chat_ls,
    cmd_chat_models,
    cmd_chat_new,
    cmd_chat_send,
)
from pcxa.commands.forms import (
    cmd_fields_create,
    cmd_fields_delete,
    cmd_fields_list,
    cmd_fields_update,
    cmd_forms_create,
    cmd_forms_delete,
    cmd_forms_get,
    cmd_forms_list,
    cmd_forms_update,
    cmd_submissions_create,
    cmd_submissions_delete,
    cmd_submissions_get,
    cmd_submissions_list,
    cmd_submissions_update,
)
from pcxa.commands.resources import (
    cmd_assignments_create,
    cmd_assignments_delete,
    cmd_assignments_list,
    cmd_assignments_update,
    cmd_rates_create,
    cmd_rates_list,
    cmd_resources_create,
    cmd_resources_delete,
    cmd_resources_get,
    cmd_resources_list,
    cmd_resources_update,
)
from pcxa.commands.costing import (
    cmd_budgets_create,
    cmd_budgets_delete,
    cmd_budgets_list,
    cmd_budgets_update,
    cmd_costcodes_create,
    cmd_costcodes_delete,
    cmd_costcodes_get,
    cmd_costcodes_list,
    cmd_costcodes_update,
)
from pcxa.commands.timesheets import (
    cmd_costentries_create,
    cmd_costentries_delete,
    cmd_costentries_list,
    cmd_costentries_update,
    cmd_entries_create,
    cmd_entries_delete,
    cmd_entries_list,
    cmd_entries_update,
    cmd_locks_create,
    cmd_locks_delete,
    cmd_locks_list,
    cmd_timesheets_approve,
    cmd_timesheets_create,
    cmd_timesheets_delete,
    cmd_timesheets_get,
    cmd_timesheets_list,
    cmd_timesheets_reject,
    cmd_timesheets_reopen,
    cmd_timesheets_submit,
    cmd_timesheets_update,
)
from pcxa.commands.links import (
    cmd_links_bulk,
    cmd_links_create,
    cmd_links_delete,
    cmd_links_list,
)
from pcxa.commands.tags_folders import DELETION_TAG  # noqa: F401  (re-export for legacy callers)
from pcxa._update import cmd_update


AUTH_FREE = {"login", "setup", "whoami", "set-project", "projects", "update"}


HANDLERS = {
    "whoami": lambda c, a: cmd_whoami(c, a),
    "move": cmd_move,
    "categorize": cmd_categorize,
    "gantt": cmd_gantt,
    "tree": cmd_tree,
}


SUB_HANDLERS = {
    "project": {"get": cmd_project_get, "update": cmd_project_update, "members": cmd_project_members},
    "files": {
        "list": cmd_files_list, "search": cmd_files_search, "content": cmd_files_content,
        "read": cmd_files_read, "batch-read": cmd_files_batch_read,
        "info": cmd_files_info, "stats": cmd_files_stats,
        "aggregate": cmd_files_aggregate, "recent": cmd_files_recent, "download": cmd_files_download,
        "upload": cmd_files_upload, "upload-version": cmd_files_upload_version,
        "update": cmd_file_update,
        "delete": cmd_files_delete, "restore": cmd_files_restore,
    },
    "tags": {"list": cmd_tags_list, "add": cmd_tags_add, "remove": cmd_tags_remove, "set": cmd_tags_set},
    "folders": {
        "tree": cmd_folders_tree, "create": cmd_folders_create, "rename": cmd_folders_rename,
        "move": cmd_folders_move, "delete": cmd_folders_delete, "contents": cmd_folders_contents,
        "subfolders": cmd_folders_subfolders,
    },
    "activities": {
        "list": cmd_activities_list, "get": cmd_activities_get, "create": cmd_activities_create,
        "update": cmd_activities_update, "delete": cmd_activities_delete,
        "bulk-update": cmd_activities_bulk_update, "types": cmd_activities_types,
    },
    "steps": {
        "list": cmd_steps_list, "create": cmd_steps_create, "update": cmd_steps_update,
        "delete": cmd_steps_delete, "from-template": cmd_steps_from_template,
    },
    "progress": {"list": cmd_progress_list, "add": cmd_progress_add, "delete": cmd_progress_delete},
    "deps": {"list": cmd_deps_list, "create": cmd_deps_create, "delete": cmd_deps_delete},
    "forms": {
        "list": cmd_forms_list, "get": cmd_forms_get, "create": cmd_forms_create,
        "update": cmd_forms_update, "delete": cmd_forms_delete,
    },
    "fields": {
        "list": cmd_fields_list, "create": cmd_fields_create,
        "update": cmd_fields_update, "delete": cmd_fields_delete,
    },
    "submissions": {
        "list": cmd_submissions_list, "get": cmd_submissions_get, "create": cmd_submissions_create,
        "update": cmd_submissions_update, "delete": cmd_submissions_delete,
    },
    "resources": {
        "list": cmd_resources_list, "get": cmd_resources_get, "create": cmd_resources_create,
        "update": cmd_resources_update, "delete": cmd_resources_delete,
    },
    "rates": {"list": cmd_rates_list, "create": cmd_rates_create},
    "assignments": {
        "list": cmd_assignments_list, "create": cmd_assignments_create,
        "update": cmd_assignments_update, "delete": cmd_assignments_delete,
    },
    "cost-codes": {
        "list": cmd_costcodes_list, "get": cmd_costcodes_get, "create": cmd_costcodes_create,
        "update": cmd_costcodes_update, "delete": cmd_costcodes_delete,
    },
    "budgets": {
        "list": cmd_budgets_list, "create": cmd_budgets_create,
        "update": cmd_budgets_update, "delete": cmd_budgets_delete,
    },
    "timesheets": {
        "list": cmd_timesheets_list, "get": cmd_timesheets_get, "create": cmd_timesheets_create,
        "update": cmd_timesheets_update, "delete": cmd_timesheets_delete,
        "submit": cmd_timesheets_submit, "approve": cmd_timesheets_approve,
        "reject": cmd_timesheets_reject, "reopen": cmd_timesheets_reopen,
    },
    "entries": {
        "list": cmd_entries_list, "create": cmd_entries_create,
        "update": cmd_entries_update, "delete": cmd_entries_delete,
    },
    "cost-entries": {
        "list": cmd_costentries_list, "create": cmd_costentries_create,
        "update": cmd_costentries_update, "delete": cmd_costentries_delete,
    },
    "locks": {"list": cmd_locks_list, "create": cmd_locks_create, "delete": cmd_locks_delete},
    "links": {
        "list": cmd_links_list, "create": cmd_links_create,
        "delete": cmd_links_delete, "bulk": cmd_links_bulk,
    },
    "comments": {
        "list": cmd_comments_list, "add": cmd_comments_add,
        "delete": cmd_comments_delete, "bulk": cmd_comments_bulk,
    },
    "chat": {
        "send": cmd_chat_send, "ls": cmd_chat_ls, "get": cmd_chat_get,
        "new": cmd_chat_new, "delete": cmd_chat_delete, "models": cmd_chat_models,
    },
}


SUB_COMMAND_KEYS = {
    "project": "project_command",
    "files": "files_command", "tags": "tags_command", "folders": "folders_command",
    "activities": "activities_command", "steps": "steps_command",
    "progress": "progress_command", "deps": "deps_command",
    "forms": "forms_command", "fields": "fields_command",
    "submissions": "submissions_command",
    "resources": "resources_command", "rates": "rates_command",
    "assignments": "assignments_command", "cost-codes": "costcodes_command",
    "budgets": "budgets_command", "timesheets": "timesheets_command",
    "entries": "entries_command", "costentries_command": "costentries_command",
    "cost-entries": "costentries_command",
    "locks": "locks_command",
    "links": "links_command",
    "comments": "comments_command",
    "chat": "chat_command",
}


def main():
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    if args.command in AUTH_FREE:
        try:
            if args.command == "login":
                cmd_login(args)
            elif args.command == "setup":
                cmd_setup(args)
            elif args.command == "whoami":
                cmd_whoami(None, args)
            elif args.command == "set-project":
                cmd_set_project(args)
            elif args.command == "projects":
                cmd_projects(args)
            elif args.command == "update":
                cmd_update(args)
        finally:
            if args.command != "update":
                _print_update_notice(_check_for_update())
        return

    config = load_config()
    profile_name = args.profile or config.get("default_profile")
    _, profile = get_profile(config, profile_name)
    client = APIClient(profile, profile_name, config)
    resolve_ids(client)

    try:
        if args.command in HANDLERS:
            HANDLERS[args.command](client, args)
        elif args.command in SUB_HANDLERS:
            sub_key = SUB_COMMAND_KEYS[args.command]
            sub_cmd = getattr(args, sub_key, None)
            if not sub_cmd:
                avail = ", ".join(SUB_HANDLERS[args.command].keys())
                print(f"Usage: pcxa {args.command} {{{avail}}}", file=sys.stderr)
                sys.exit(1)
            SUB_HANDLERS[args.command][sub_cmd](client, args)
        else:
            parser.print_help()
    except requests.HTTPError as e:
        print(f"API error: {e}", file=sys.stderr)
        if e.response is not None:
            try:
                print(json.dumps(e.response.json(), indent=2), file=sys.stderr)
            except Exception:
                print(e.response.text[:500], file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
    finally:
        _print_update_notice(_check_for_update())


__all__ = [
    "main",
    "AUTH_FREE",
    "HANDLERS",
    "SUB_HANDLERS",
    "SUB_COMMAND_KEYS",
]
