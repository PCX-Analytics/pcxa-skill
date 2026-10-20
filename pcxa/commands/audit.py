"""Company audit log: who opened, downloaded or was shown which files.

Reads ``/api/companies/{c}/audit-logs/``, which is company-scoped (no project)
and readable by company admins only — anyone else gets a 403, not an empty list.

Four behaviours of that API shape this module, each of which would otherwise
produce an export that looks complete and isn't:

* **No server-side date filter.** ``list`` pages newest-first and stops at
  ``--since`` on its own. It orders by ``-timestamp,-sequence_number`` rather
  than the default ``-sequence_number``: sequence is insert order, and a row
  whose timestamp was taken a moment before a concurrent writer's would sit on
  the wrong side of an early stop. The sequence tiebreaker keeps page
  boundaries deterministic, and rows repeated when new events push the pages
  down are dropped by ``sequence_number``.
* **``username`` is matched exactly against an internal handle**, which is
  derived from the email prefix but gets a numeric suffix on collision. So a
  guessed handle can return nothing, or a different person with the same
  prefix. An email passed to ``--user`` is resolved against company members.
* **``resource-history`` answers "0 rows" when the company has audit
  disabled**, and marks that only with ``audit_enabled: false``.
* **``resource-history`` is throttled to 10 requests/hour per user**, shared
  with ``audit-logs/verify/``.
"""

import csv
import json
import sys
from datetime import datetime, timedelta, timezone

from pcxa._http import requests
from pcxa._output import out_json, out_table

# Page size is the server cap (StandardPagination.max_page_size). Every history
# page spends one request of a 10/hour budget, so it must be the largest.
PAGE_SIZE = 1000

# Audit pages at 1000 rows can outlast the 30s default read timeout. A floor,
# not a ceiling: a longer `pcxa --timeout` still wins.
AUDIT_TIMEOUT = 120


def _timeout(client):
    return max(getattr(client, "timeout", None) or 0, AUDIT_TIMEOUT)

DEFAULT_SCREEN_LIMIT = 500

LIST_ORDERING = "-timestamp,-sequence_number"

# Serializer fields, in export order, followed by the two `details` keys worth
# a column of their own. `details` itself is kept whole as JSON: the CSV is an
# export of a signed ledger, and dropping keys from it loses evidence.
EXPORT_FIELDS = [
    "sequence_number", "timestamp", "username", "user_display_name", "ip_address",
    "event_category", "event_type", "resource_type", "resource_id", "resource_label",
    "is_ai_agent", "conversation_id", "details",
]

FILE_EVENT_TYPES_HELP = """\
File event types:
  files.get                 a file's record opened in the app or API
  file.presign.issued       file bytes handed out: a download or in-app preview
  file.bulk_download.entry  a file included in a bulk download
  file.search_excerpt       file text shown in a search result or to the assistant
  file.ai_read              the assistant read the file
  file.ai_viewed_pages      the assistant looked at page images
  file.ai_quoted            the assistant quoted the file in an answer
  file.email_preview        an email previewed
  file.container_entry      a file opened inside a zip or mailbox

The list is not exhaustive: event_type is free text on the server, which also
records folder, photo and drawing events, and a `.denied` variant of some.
"""


def parse_when(value, end_of_day=False):
    """Parse ``YYYY-MM-DD`` or an ISO-8601 time into an aware UTC datetime.

    A bare date means midnight UTC. With ``end_of_day`` it means the midnight
    *after* it, so ``--until 2026-09-13`` includes the whole of the 13th.
    """
    text = value.strip()
    try:
        if len(text) == 10:
            day = datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            return day + timedelta(days=1) if end_of_day else day
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"not a date or ISO time: {value!r} (use YYYY-MM-DD or 2026-09-13T14:00:00Z)")
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _row_time(row):
    return datetime.fromisoformat(str(row["timestamp"]).replace("Z", "+00:00"))


def export_row(row):
    details = row.get("details") or {}
    flat = {k: row.get(k, "") for k in EXPORT_FIELDS}
    flat["is_ai_agent"] = bool(details.get("is_ai_agent"))
    flat["conversation_id"] = details.get("conversation_id") or ""
    flat["details"] = json.dumps(details, sort_keys=True, default=str)
    return flat


def resolve_username(client, user):
    """Turn ``--user`` into the exact handle the audit log stores.

    A value without ``@`` is taken as the handle itself. An email is looked up
    among the company's members; there is deliberately no prefix fallback,
    because a collided prefix names a different person.
    """
    if "@" not in user:
        return user
    data = client.get("memberships/", {"search": user, "page_size": 100},
                      project_scoped=False)
    members = data.get("results", data) if isinstance(data, dict) else data
    hits = [m for m in members if str(m.get("user_email", "")).casefold() == user.casefold()]
    if len(hits) != 1 or not hits[0].get("user_username"):
        print(
            f"No company {client.company_id} member has the email {user}. Only current "
            "company members resolve; for anyone else pass their exact username — "
            f"find it with: pcxa audit list --search {user.split('@')[0]} -f table",
            file=sys.stderr,
        )
        sys.exit(1)
    username = hits[0]["user_username"]
    print(f"{user} -> username {username}", file=sys.stderr)
    return username


def iter_events(client, params, since=None, until=None, limit=0):
    """Yield audit rows newest first, within [since, until), up to ``limit``.

    Returns (via StopIteration.value) True when it stopped at ``limit`` with
    matching rows possibly left unread.
    """
    params = {**params, "ordering": LIST_ORDERING,
              "page_size": min(limit, PAGE_SIZE) if limit else PAGE_SIZE, "page": 1}
    seen = set()
    got = 0
    while True:
        page = client.get("audit-logs/", params, project_scoped=False, timeout=_timeout(client))
        rows = page.get("results", [])
        for i, row in enumerate(rows):
            if row.get("sequence_number") in seen:
                continue
            seen.add(row.get("sequence_number"))
            when = _row_time(row)
            if until and when >= until:
                continue
            if since and when < since:
                return False
            if limit and got >= limit:
                return True
            yield row
            got += 1
        if not page.get("next"):
            return False
        params["page"] += 1


def _collect(gen):
    rows = []
    while True:
        try:
            rows.append(next(gen))
        except StopIteration as stop:
            return rows, bool(stop.value)


def _write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=EXPORT_FIELDS)
        writer.writeheader()
        writer.writerows(export_row(r) for r in rows)
    print(f"Wrote {len(rows):,} rows to {path}", file=sys.stderr)


def _print_table(rows):
    table = []
    for r in rows:
        flat = export_row(r)
        table.append({
            "seq": flat["sequence_number"],
            "time (UTC)": str(flat["timestamp"])[:19].replace("T", " "),
            "user": flat["username"],
            "event": flat["event_type"],
            "resource": f"{flat['resource_type']}:{flat['resource_id']}",
            "label": str(flat["resource_label"] or "")[:40],
            "ai": "yes" if flat["is_ai_agent"] else "",
        })
    out_table(table, ["seq", "time (UTC)", "user", "event", "resource", "label", "ai"])


def cmd_audit_list(client, args):
    """List audit events, newest first."""
    try:
        since = parse_when(args.since) if args.since else None
        until = parse_when(args.until, end_of_day=True) if args.until else None
    except ValueError as e:
        print(f"Invalid date: {e}", file=sys.stderr)
        return 2

    params = {}
    if args.user:
        params["username"] = resolve_username(client, args.user)
    for key, value in (("event_type", args.type), ("event_category", args.category),
                       ("resource_type", args.resource_type), ("search", args.search)):
        if value:
            params[key] = value

    limit = args.limit
    if limit is None:
        limit = 0 if args.csv else DEFAULT_SCREEN_LIMIT
    rows, truncated = _collect(iter_events(client, params, since, until, limit))

    if args.csv:
        _write_csv(args.csv, rows)
    elif args.format == "json":
        out_json({"count": len(rows), "truncated": truncated, "results": rows})
    else:
        print(f"Audit events: {len(rows):,}\n")
        _print_table(rows)

    if truncated:
        print(f"Stopped at --limit {limit}; more matching events exist. Pass --limit 0 for all.",
              file=sys.stderr)
    if not rows and "username" in params:
        print(
            f"No events for username {params['username']!r}. The filter is an exact match on an "
            "internal handle: pass an email to resolve it, or find the handle with --search.",
            file=sys.stderr,
        )
    return None


def cmd_audit_history(client, args):
    """Every recorded access to one resource, oldest first."""
    params = {"resource_type": args.resource_type, "resource_id": str(args.resource_id),
              "page_size": PAGE_SIZE, "page": 1}
    rows = []
    audit_enabled = True
    while True:
        try:
            page = client.get("audit-logs/resource-history/", params,
                              project_scoped=False, timeout=_timeout(client))
        except requests.HTTPError as e:
            if e.response is None or e.response.status_code != 429:
                raise
            # The transport lower-cases header names.
            wait = str((getattr(e.response, "headers", None) or {}).get("retry-after", ""))
            msg = ("Rate limited: resource history allows 10 requests/hour per user, "
                   "shared with audit verification")
            if wait.isdigit():
                msg += f"; retry in {int(wait) // 60 + 1} min"
            if rows:
                msg += (f". {len(rows):,} rows were read before the limit and NOT written: "
                        "a partial history would read as complete")
            print(msg + ".", file=sys.stderr)
            return 1
        audit_enabled = audit_enabled and page.get("audit_enabled", True)
        rows.extend(page.get("results", []))
        if not page.get("next"):
            break
        params["page"] += 1

    if args.csv:
        _write_csv(args.csv, rows)
    elif args.format == "json":
        out_json({"audit_enabled": audit_enabled, "count": len(rows), "results": rows})
    else:
        print(f"History of {args.resource_type} {args.resource_id}: {len(rows):,} events\n")
        _print_table(rows)

    if not audit_enabled:
        print(
            f"WARNING: company {client.company_id} does not have chain-of-custody auditing "
            "enabled. An empty or short history here does NOT mean nobody accessed it.",
            file=sys.stderr,
        )
    return None


__all__ = ["cmd_audit_list", "cmd_audit_history", "FILE_EVENT_TYPES_HELP"]
