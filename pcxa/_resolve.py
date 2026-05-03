"""Cross-cutting helpers: ID/name resolution, generic object refs.

These utilities are shared by multiple command modules and depend only on the
APIClient.
"""

import difflib
import sys

from pcxa._http import requests


def resolve_member_by_name(client, query):
    """Resolve a name query to a user ID via fuzzy matching on project members.

    Returns (user_id, message) where message describes the match outcome.
    On ambiguity or no match, user_id is None and message explains next steps.
    """
    data = client.get("memberships/", {"limit": 200})
    results = data.get("results", data) if isinstance(data, dict) else data
    members = [m for m in results if not m.get("is_ai_agent")]
    if not members:
        return None, "No project members found."

    query_lower = query.lower().strip()

    candidates = []
    for m in members:
        uid = m.get("user")
        name = m.get("user_name", "")
        username = m.get("user_username", "")
        email = m.get("user_email", "")
        candidates.append((uid, name, username, email))

    for uid, name, username, email in candidates:
        if query_lower in (name.lower(), username.lower(), email.lower()):
            return uid, f"Exact match: {name} (user {uid})"

    substring_hits = []
    for uid, name, username, email in candidates:
        combined = f"{name} {username} {email}".lower()
        if query_lower in combined:
            substring_hits.append((uid, name, username))

    if len(substring_hits) == 1:
        uid, name, username = substring_hits[0]
        return uid, f"Matched: {name} [{username}] (user {uid})"

    if len(substring_hits) >= 2:
        lines = [f"Multiple matches for '{query}':"]
        for uid, name, username in substring_hits:
            lines.append(f"  - user {uid}: {name} [{username}]")
        lines.append("Pass --assignee <user_id> with the correct ID.")
        return None, "\n".join(lines)

    scored = []
    for uid, name, username, email in candidates:
        s1 = difflib.SequenceMatcher(None, query_lower, name.lower()).ratio()
        s2 = difflib.SequenceMatcher(None, query_lower, username.lower()).ratio()
        best = max(s1, s2)
        scored.append((best, uid, name, username))

    scored.sort(key=lambda x: x[0], reverse=True)

    if scored[0][0] >= 0.6:
        top = scored[0]
        if len(scored) > 1 and scored[1][0] >= top[0] - 0.1:
            close = [s for s in scored if s[0] >= top[0] - 0.1][:5]
            lines = [f"No exact match for '{query}'. Close matches:"]
            for score, uid, name, username in close:
                lines.append(f"  - user {uid}: {name} [{username}] ({score:.0%})")
            lines.append("Pass --assignee <user_id> with the correct ID.")
            return None, "\n".join(lines)
        _, uid, name, username = top
        return uid, f"Fuzzy match: {name} [{username}] (user {uid})"

    return None, f"No match found for '{query}'. Use `pcxa project members` to list all members."


def resolve_ids(client):
    """Auto-resolve company_id / project_id when only one is available."""
    if client.company_id and client.project_id:
        return
    try:
        if not client.company_id:
            data = client.get_raw(f"{client.base_url}/api/companies/")
            companies = data.get("results", data) if isinstance(data, dict) else data
            if len(companies) == 1:
                client.company_id = companies[0]["id"]
            else:
                print("Multiple companies. Use --company or set in profile.", file=sys.stderr)
                sys.exit(1)
        if not client.project_id:
            data = client.get_raw(f"{client.base_url}/api/companies/{client.company_id}/projects/")
            projects = data.get("results", data) if isinstance(data, dict) else data
            if len(projects) == 1:
                client.project_id = projects[0]["id"]
            else:
                print("Multiple projects. Use --project or set in profile.", file=sys.stderr)
                sys.exit(1)
    except requests.ConnectionError:
        print(f"Cannot connect to {client.base_url}", file=sys.stderr)
        sys.exit(1)


def parse_object_ref(ref):
    """Parse 'type:id' reference into (type_string, object_id).

    Example: 'file:170106' -> ('file', 170106)
    """
    parts = ref.split(":", 1)
    if len(parts) != 2:
        print(f"Invalid object reference '{ref}'. Use format type:id (e.g. file:123, activity:456)", file=sys.stderr)
        sys.exit(1)
    obj_type, obj_id = parts
    try:
        return obj_type.strip(), int(obj_id.strip())
    except ValueError:
        print(f"Invalid ID in reference '{ref}'. ID must be an integer.", file=sys.stderr)
        sys.exit(1)


def links_url(client, path=""):
    """Build URL for generic-links endpoint (top-level, not project-scoped)."""
    return f"{client.base_url}/api/generic-links/{path}"


__all__ = [
    "resolve_member_by_name",
    "resolve_ids",
    "parse_object_ref",
    "links_url",
]
